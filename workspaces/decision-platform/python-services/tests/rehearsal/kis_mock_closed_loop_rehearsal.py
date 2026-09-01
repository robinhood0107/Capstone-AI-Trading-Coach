"""자동운용 폐루프를 실제 KIS 모의계좌로 끝까지 구동하는 리허설.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며
pytest 수집 대상도 아니다(`test_` 접두사가 아니다).

`app.p1_owner.automation` 엔진을 그대로 쓰되 transport만 실제 KIS 모의 brokerage로 바꾼다.
DB gate(REAL_TEAM_B_POINTER, activation gate)는 production arming 경로를 지키는 장치이고 여기서는
건드리지 않는다. 위조 없이 엔진 상태기계와 실제 주문을 함께 검증하는 것이 목적이다.

덮는 구간:
  세션 1  SCHEDULED → PRECHECK → BUY_CANDIDATE_SELECTED → NEWS_CHECKING → ORDER_SIZING
          → RISK_CHECKING → ORDER_SUBMITTING → ORDER_SUBMITTED → COMPLETED (실제 매수 체결)
  세션 5  만기 도달 → EXIT_SELECTED → ORDER_SIZING → RISK_CHECKING → ORDER_SUBMITTING
          → ORDER_SUBMITTED → COMPLETED (실제 매도 체결, 같은 lot close)
  그리고 tick 재시작 안전성, 중복 tick no-op, 잔고 원복.

실행:
  /usr/bin/docker compose --project-name capstone-p1 \
    --env-file deploy/p1/.state-app/runtime.env -f deploy/p1/compose.yml \
    run --rm --no-deps \
    -v "$PWD/workspaces/decision-platform/python-services/tests/rehearsal:/rehearsal:ro" \
    -e P1_KIS_MOCK_CLOSED_LOOP_REHEARSAL=1 -e PYTHONPATH=/app \
    kis-mock-certification-runner python /rehearsal/kis_mock_closed_loop_rehearsal.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import UTC, date, datetime, time as clock_time
from decimal import ROUND_CEILING, Decimal
from typing import Any, Final
from zoneinfo import ZoneInfo

import pandas as pd

from app.brokerage.kis_mock_certification_gate import (
    CertificationWindowClosed,
    require_certification_window,
)
from app.brokerage.kis_mock_online_client import (
    BALANCE_PATH,
    EXECUTIONS_PATH,
    MOCK_BALANCE_TR_ID,
    MOCK_BUY_TR_ID,
    MOCK_EXECUTIONS_RECENT_TR_ID,
    MOCK_SELL_TR_ID,
    ORDER_CASH_PATH,
    KISBrokerageCallBudget,
    KISMockBrokerageHttpClient,
)
from app.data.kis.http_client import CURRENT_PRICE_PATH, KISHttpClient
from app.data.kis.settings import KISSettings
from app.data.calendar.xkrx_policy import corrected_calendar
from app.generated import strong_llm_agent_pb2
from app.p1_owner.automation import (
    _nth_next_session,
    AiCandidateVerdict,
    AiJudgement,
    AutomationEngine,
    AutomationInputs,
    AutomationPolicySnapshot,
    AutomationStore,
    NewsVerdict,
    OrderReservation,
    Quote,
    ReconcileSnapshot,
    SignalCandidate,
    SubmitOutcome,
)
from app.p1_owner.automation_atr import CompletedDailyBar, wilder_atr
from app.strong_llm.judge_client import (
    JudgeClientSettings,
    StrongLlmJudgeClient,
    StrongLlmJudgeUnavailableError,
)

_KST: Final = ZoneInfo("Asia/Seoul")
_SYMBOL: Final = os.environ.get("P1_KIS_MOCK_REHEARSAL_SYMBOL", "005930")
_OPT_IN: Final = "P1_KIS_MOCK_CLOSED_LOOP_REHEARSAL"
_SANITIZED_OUTPUT: Final = "P1_KIS_MOCK_REHEARSAL_SANITIZED_OUTPUT"
_ACCOUNT_ID: Final = "acct_" + "c" * 32
_STRATEGY_ID: Final = "strategy_rehearsal_real_kis"
_PRINCIPLE_ID: Final = "prc_rehearsal_real_kis"
_LIMIT_DIVISION: Final = "00"
_FILL_POLL_ATTEMPTS: Final = 12
_FILL_POLL_SECONDS: Final = 2.5
_BROKERAGE_CAP: Final = 60
_TOKEN_CAP: Final = 1
_EVALUATION_TIME: Final = clock_time(9, 30)
# 청산 사유별 경로를 실거래로 나누어 찍기 위한 시나리오 선택.
#   MAX_HOLDING_SESSIONS  만기 도달로 청산(기본)
#   MODEL_SELL            보유 종목에 SELL 신호가 들어와 만기 전에 청산
#   AI_RERANK             후보를 둘 주고 Strong LLM 판단이 1등을 바꾸는지 본다
#   STOP_LOSS             손절선을 넘긴 상태를 만들어 그 청산 경로를 태운다
#   TAKE_PROFIT           익절선을 넘긴 상태를 만들어 그 청산 경로를 태운다
#   ATR_TRAILING          합성 ATR/peak로 트레일링을 발화하고 실제 시세·매도로 닫는다
#   MAX_HOLDING_UNLIMITED 무제한 정책으로 6세션 뒤에도 보유함을 확인한 뒤 MODEL_SELL로 원복한다
#   THREE_SESSION_SOAK    연속 세 XKRX 세션에 걸쳐 tick을 돌린다. 가운데 두 세션은 신호가
#                         없어 무행동으로 닫혀야 하고, 그 사이 포지션이 그대로 살아 있어야 한다
#
# STOP_LOSS/TAKE_PROFIT은 실제 등락을 기다릴 수 없다. 그래서 시세가 아니라 **진입가**를
# 옮겨 손익률만 만든다. 시세 조회도 매도 주문도 전부 실제다. 시세를 조작하면 매도 지정가가
# 함께 움직여(_limit_price는 quote에서 나온다) 익절은 시장가보다 높은 값으로 나가 체결되지
# 않는다. 진입가를 옮기면 판정만 바뀌고 주문은 시장 근처에 남아 실제로 체결된다.
# 그 대신 이 두 시나리오의 realizedPnl은 실제 손익이 아니다. 판정표가 그렇게 적는다.
_SCENARIO: Final = os.environ.get("P1_KIS_MOCK_REHEARSAL_SCENARIO", "MAX_HOLDING_SESSIONS")
# AI_RERANK에서 규칙상 2등이 되는 종목. 규칙은 기대수익만 보고 AI는 확신도까지 본다.
_RIVAL_SYMBOL: Final = os.environ.get("P1_KIS_MOCK_REHEARSAL_RIVAL_SYMBOL", "000660")
_JUDGE_QUESTION: Final = (
    "주어진 후보 각각에 0과 1 사이 점수를 매기고 매수를 막아야 하면 veto를 표시하라."
)


class RehearsalFailed(RuntimeError):
    """리허설을 중단시킨 사유. 실패를 성공으로 축소하지 않는다."""


def _sanitized_output(value: Any) -> Any:
    """계좌 현금과 raw 주문번호를 사용자 출력에서 제거한다."""

    if isinstance(value, list):
        return [_sanitized_output(item) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key == "cashKrw":
            continue
        if key == "orderNo":
            result["orderRefSha256"] = hashlib.sha256(
                ("kis-mock-order-ref\0" + str(item)).encode()
            ).hexdigest()
            continue
        result[key] = _sanitized_output(item)
    return result


def _print_report(report: dict[str, Any], *, stream: Any = None) -> None:
    projected = _sanitized_output(report) if os.environ.get(_SANITIZED_OUTPUT) == "1" else report
    print(json.dumps(projected, ensure_ascii=False, indent=2), file=stream)


def _require_opt_in() -> None:
    if os.environ.get(_OPT_IN) != "1":
        raise RehearsalFailed(f"{_OPT_IN}=1 이 없으면 실주문 리허설을 실행하지 않는다")
    if os.environ.get("CI"):
        raise RehearsalFailed("CI 환경에서는 실주문 리허설을 실행하지 않는다")


def _nonnegative(value: Any, label: str) -> int:
    text = str(value).strip()
    if not text.lstrip("-").isdigit():
        raise RehearsalFailed(f"{label} 응답이 정수가 아니다")
    number = int(text)
    if number < 0:
        raise RehearsalFailed(f"{label} 응답이 음수다")
    return number


def _synthetic_atr_history(
    *, as_of_session: date, anchor_price_krw: int, period: int = 22
) -> tuple[tuple[CompletedDailyBar, ...], tuple[date, ...]]:
    """실제 가격 규모를 anchor로 쓰는 연속 완료봉을 만든다.

    세션 축과 현재가는 실제지만 OHLC는 분기 발화용 합성값이다. 실제 손익이나 장중 변동성
    근거로 사용하지 않고 리허설 JSON에도 그 경계를 명시한다.
    """

    calendar = corrected_calendar()
    previous = calendar.previous_session(pd.Timestamp(as_of_session))
    sessions = tuple(item.date() for item in calendar.sessions_window(previous, -(period + 1)))
    if len(sessions) != period + 1:
        raise RehearsalFailed("합성 ATR 세션 축이 불완전하다")
    spread = max(100, anchor_price_krw // 100)
    low = max(1, anchor_price_krw - spread)
    bars = tuple(
        CompletedDailyBar(
            session,
            anchor_price_krw,
            anchor_price_krw + spread,
            low,
            anchor_price_krw,
        )
        for session in sessions
    )
    return bars, sessions


def _require_success(payload: dict[str, Any], label: str) -> dict[str, Any]:
    if str(payload.get("rt_cd", "")).strip() != "0":
        raise RehearsalFailed(
            f"{label} 실패: rt_cd={payload.get('rt_cd')} msg_cd={payload.get('msg_cd')}"
        )
    return payload


class RealKisAutomationTransport:
    """엔진이 요구하는 transport port를 실제 KIS 모의 brokerage로 구현한다."""

    judge_calls = 0
    last_judgement: dict[str, object] | None = None

    def judge(
        self,
        candidates: tuple[SignalCandidate, ...],
        candidate_set_sha256: str,
    ) -> AiJudgement | None:
        """Strong LLM에게 후보를 보이고 점수를 받는다.

        설정이 없으면 None이다. 엔진은 그때 AI_NOT_PARTICIPATED로 적고 규칙만으로 계속한다.
        판단을 못 받았다고 리허설을 실패로 만들지 않는다. 그것이 운영 계약이다.
        """

        del candidate_set_sha256
        settings = JudgeClientSettings.from_env()
        if settings is None or not candidates:
            return None
        self.judge_calls += 1
        try:
            judgement = StrongLlmJudgeClient(settings).judge(
                run_id="s49_run_" + "r" * 0 + f"{self.judge_calls:032d}",
                model_id=os.environ.get("VERTEX_MODEL_ID", "gemini-3.5-flash"),
                question=_JUDGE_QUESTION,
                language="ko",
                candidates=tuple(
                    strong_llm_agent_pb2.JudgementCandidate(
                        symbol=item.symbol,
                        expected_return=item.expected_return,
                        model_confidence=item.confidence,
                        lstm_signal=item.lstm_signal,
                        baseline_signal=item.baseline_signal,
                    )
                    for item in candidates
                ),
            )
        except StrongLlmJudgeUnavailableError as error:
            self.last_judgement = {"unavailable": str(error)}
            return None
        allowed = {item.symbol for item in candidates}
        verdicts = tuple(
            AiCandidateVerdict(item.symbol, item.score, item.veto, item.reason)
            for item in judgement.candidates
            if item.symbol in allowed
        )
        if not verdicts:
            return None
        self.last_judgement = {
            "confidence": judgement.confidence,
            "summary": judgement.summary,
            "verdicts": [
                {"symbol": v.symbol, "score": v.score, "veto": v.veto, "reason": v.reason}
                for v in verdicts
            ],
        }
        print(
            "  AI 판단: "
            + " ".join(f"{v.symbol}={v.score:.4f}{'(veto)' if v.veto else ''}" for v in verdicts)
            + f" confidence={judgement.confidence:.4f}",
            file=sys.stderr,
        )
        return AiJudgement(verdicts, judgement.confidence, judgement.summary)

    def __init__(self, market: KISHttpClient, client: KISMockBrokerageHttpClient) -> None:
        self._market = market
        self._client = client
        self._orders: dict[tuple[str, str], dict[str, str]] = {}
        self._session = datetime.now(tz=_KST).date()
        self.physical_calls = 0
        self.physical_submit_calls = 0
        self.quote_calls = 0
        self.vertex_calls = 0
        self.submit_calls = 0
        self.reconcile_calls = 0
        self.cancel_calls = 0
        self.steps: list[dict[str, Any]] = []

    def quote(self, symbol: str) -> Quote:
        self.quote_calls += 1
        self.physical_calls += 1
        payload = self._market.request(
            "GET",
            CURRENT_PRICE_PATH,
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
        )
        _require_success(payload, "시세 조회")
        output = payload.get("output")
        if not isinstance(output, dict):
            raise RehearsalFailed("시세 응답 형식이 유효하지 않다")
        price = _nonnegative(output.get("stck_prpr"), "현재가")
        upper = _nonnegative(output.get("stck_mxpr"), "상한가")
        lower = _nonnegative(output.get("stck_llam"), "하한가")
        if price <= 0 or upper <= 0 or lower <= 0:
            raise RehearsalFailed("시세 응답에 가격 또는 상·하한가가 없다")
        self.steps.append(
            {
                "step": "quote",
                "symbol": symbol,
                "priceKrw": price,
                "upperKrw": upper,
                "lowerKrw": lower,
            }
        )
        return Quote(symbol=symbol, price_krw=price, lower_limit_krw=lower, upper_limit_krw=upper)

    def vertex(self, symbol: str) -> NewsVerdict:
        # Vertex는 이 리허설 범위가 아니다. provider 호출 없이 통과시키고 그 사실을 기록한다.
        self.vertex_calls += 1
        self.steps.append(
            {"step": "vertex", "symbol": symbol, "verdict": "NO_VETO", "providerCalls": 0}
        )
        return "NO_VETO"

    def submit(self, reservation: OrderReservation) -> SubmitOutcome:
        self.submit_calls += 1
        self.physical_submit_calls = 1
        self.physical_calls += 1
        tr_id = MOCK_BUY_TR_ID if reservation.side == "BUY" else MOCK_SELL_TR_ID
        payload = self._client.request(
            "POST",
            ORDER_CASH_PATH,
            tr_id,
            json_body={
                "PDNO": reservation.symbol,
                "ORD_DVSN": _LIMIT_DIVISION,
                "ORD_QTY": str(reservation.quantity),
                "ORD_UNPR": str(reservation.limit_price_krw),
            },
        )
        _require_success(payload, f"{reservation.side} 제출")
        output = payload.get("output")
        if not isinstance(output, dict):
            raise RehearsalFailed("주문 제출 응답 형식이 유효하지 않다")
        order_no = str(output.get("ODNO", "")).strip()
        branch = str(output.get("KRX_FWDG_ORD_ORGNO", "")).strip()
        if not order_no or not branch:
            raise RehearsalFailed("주문 제출 응답에 주문번호가 없다")
        self._orders[(reservation.symbol, reservation.side)] = {
            "orderNo": order_no,
            "branchNo": branch,
        }
        self.steps.append(
            {
                "step": f"submit{reservation.side.title()}",
                "symbol": reservation.symbol,
                "quantity": reservation.quantity,
                "limitPriceKrw": reservation.limit_price_krw,
                "orderNo": order_no,
            }
        )
        return "UNFILLED"

    def reconcile(self, reservation: OrderReservation | None) -> ReconcileSnapshot:
        self.reconcile_calls += 1
        if reservation is None:
            return ReconcileSnapshot(True, 0, 0, None)
        reference = self._orders.get((reservation.symbol, reservation.side))
        if reference is None:
            raise RehearsalFailed("대사할 주문 참조가 없다")
        snapshot = self._await_fill(reference, reservation)
        self.steps.append(
            {
                "step": f"{reservation.side.lower()}Fill",
                "cumulativeQuantity": snapshot.cumulative_quantity,
                "leavesQuantity": snapshot.leaves_quantity,
                "averageFillPriceKrw": snapshot.average_fill_price_krw,
            }
        )
        return snapshot

    def cancel(self, reservation: OrderReservation) -> bool:
        # 이 리허설은 체결까지 가는 marketable 주문만 낸다. 취소 경로는 왕복 리허설이 덮는다.
        self.cancel_calls += 1
        raise RehearsalFailed("체결을 기대한 주문에서 취소 경로로 들어왔다")

    def _await_fill(
        self, reference: dict[str, str], reservation: OrderReservation
    ) -> ReconcileSnapshot:
        snapshot = ReconcileSnapshot(False, 0, reservation.quantity, None)
        for _ in range(_FILL_POLL_ATTEMPTS):
            snapshot = self._read_execution(reference, reservation)
            if snapshot.cumulative_quantity >= reservation.quantity:
                return snapshot
            time.sleep(_FILL_POLL_SECONDS)
        raise RehearsalFailed(
            f"{reservation.side} 체결이 관측되지 않았다: 누적={snapshot.cumulative_quantity} "
            f"잔여={snapshot.leaves_quantity}. 미체결을 체결로 추정하지 않는다"
        )

    def _read_execution(
        self, reference: dict[str, str], reservation: OrderReservation
    ) -> ReconcileSnapshot:
        self.physical_calls += 1
        compact = self._session.isoformat().replace("-", "")
        payload = self._client.request(
            "GET",
            EXECUTIONS_PATH,
            MOCK_EXECUTIONS_RECENT_TR_ID,
            params={
                "INQR_STRT_DT": compact,
                "INQR_END_DT": compact,
                "SLL_BUY_DVSN_CD": "00",
                "INQR_DVSN": "00",
                "PDNO": reservation.symbol,
                "CCLD_DVSN": "00",
                "ORD_GNO_BRNO": reference["branchNo"],
                "ODNO": reference["orderNo"],
                "INQR_DVSN_3": "00",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
                "EXCG_ID_DVSN_CD": "KRX",
            },
        )
        _require_success(payload, "체결 조회")
        rows = payload.get("output1")
        if not isinstance(rows, list):
            raise RehearsalFailed("체결 조회 응답 형식이 유효하지 않다")
        matches = [
            row
            for row in rows
            if isinstance(row, dict) and str(row.get("odno", "")).strip() == reference["orderNo"]
        ]
        if len(matches) != 1:
            raise RehearsalFailed(f"체결 row가 정확히 하나가 아니다: {len(matches)}건")
        row = matches[0]
        cumulative = _nonnegative(row.get("tot_ccld_qty"), "누적체결수량")
        leaves = _nonnegative(row.get("rmn_qty"), "미체결수량")
        if cumulative + leaves > reservation.quantity:
            raise RehearsalFailed("체결 수량 불변식 위반: 누적+잔여 > 주문수량")
        average_text = str(row.get("avg_prvs", "")).strip()
        average = int(float(average_text)) if average_text and average_text != "0" else None
        if cumulative > 0 and (average is None or average <= 0):
            raise RehearsalFailed("체결이 있는데 평균단가가 유효하지 않다")
        return ReconcileSnapshot(
            resolved=cumulative + leaves == reservation.quantity or cumulative > 0,
            cumulative_quantity=cumulative,
            leaves_quantity=leaves,
            average_fill_price_krw=average,
        )


def _balance(client: KISMockBrokerageHttpClient) -> dict[str, Any]:
    payload = client.request(
        "GET",
        BALANCE_PATH,
        MOCK_BALANCE_TR_ID,
        params={
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        },
    )
    _require_success(payload, "잔고 조회")
    summary = payload.get("output2")
    rows = summary if isinstance(summary, list) else []
    cash = rows[0].get("dnca_tot_amt") if rows and isinstance(rows[0], dict) else None
    holdings = payload.get("output1")
    position = 0
    if isinstance(holdings, list):
        for row in holdings:
            if isinstance(row, dict) and str(row.get("pdno", "")).strip() == _SYMBOL:
                position = _nonnegative(row.get("hldg_qty"), "보유수량")
    return {
        "cashKrw": _nonnegative(cash, "예수금") if cash is not None else None,
        "symbolQuantity": position,
    }


def _drive(
    engine_store: AutomationStore,
    transport: RealKisAutomationTransport,
    *,
    run_id: str,
    session: date,
    inputs: AutomationInputs,
    terminal: set[str],
    label: str,
) -> dict[str, object]:
    """엔진을 종료 상태까지 tick한다. 매 tick마다 engine을 새로 만들어 재시작 안전성도 본다."""

    now = datetime.combine(session, _EVALUATION_TIME, tzinfo=_KST)
    engine_store.create_run(run_id=run_id, session_date=session, now=now)
    projection: dict[str, object] = {}
    for index in range(1, 25):
        engine = AutomationEngine(engine_store)
        projection = engine.tick(
            run_id=run_id,
            tick_id=f"tick_{index:03d}",
            now=now,
            inputs=inputs,
            transport=transport,
        )
        state = str(projection.get("state"))
        print(f"  {label} tick {index:02d} -> {state}", file=sys.stderr)
        if state in terminal:
            # 같은 tick identity를 다시 넣어도 순수 no-op이어야 한다.
            replay = AutomationEngine(engine_store).tick(
                run_id=run_id,
                tick_id=f"tick_{index:03d}",
                now=now,
                inputs=inputs,
                transport=transport,
            )
            if replay != projection:
                raise RehearsalFailed(f"{label} 중복 tick이 no-op이 아니다")
            return projection
    raise RehearsalFailed(f"{label} 이 종료 상태에 도달하지 못했다: {projection.get('state')}")


def main() -> int:
    steps: list[dict[str, Any]] = []
    try:
        _require_opt_in()
        require_certification_window(datetime.now(tz=UTC))
        settings = KISSettings()
        if settings.kis_mode != "mock" or settings.kis_offline:
            raise RehearsalFailed("KIS_MODE=mock, KIS_OFFLINE=0 이 아니면 실행하지 않는다")

        budget = KISBrokerageCallBudget(token_p_cap=_TOKEN_CAP, brokerage_cap=_BROKERAGE_CAP)
        market = KISHttpClient(settings=settings)
        client = KISMockBrokerageHttpClient(settings=settings, budget=budget)
        transport = RealKisAutomationTransport(market, client)
        try:
            pre = _balance(client)
            steps.append({"step": "preBalance", **pre})
            if pre["symbolQuantity"] != 0:
                raise RehearsalFailed("리허설은 보유수량 0에서 시작해야 한다")

            v3_scenario = _SCENARIO in {"ATR_TRAILING", "MAX_HOLDING_UNLIMITED"}
            preset = "AGGRESSIVE" if _SCENARIO == "MAX_HOLDING_UNLIMITED" else "BALANCED"
            policy = (
                AutomationPolicySnapshot.from_v3_preset(
                    policy_id="auto_pol_" + "a" * 32,
                    version=1,
                    capital_limit_krw=10_000_000,
                    preset=preset,
                )
                if v3_scenario
                else AutomationPolicySnapshot.from_preset(
                    policy_id="auto_pol_" + "a" * 32,
                    version=1,
                    capital_limit_krw=10_000_000,
                    preset="BALANCED",
                )
            )
            store = AutomationStore(
                account_id=_ACCOUNT_ID,
                brokerage_mode="KIS_MOCK",
                principle_id=_PRINCIPLE_ID,
                strategy_id=_STRATEGY_ID,
                baseline_account_digest=hashlib.sha256(b"rehearsal-baseline").hexdigest(),
            )

            buy_session = datetime.now(tz=_KST).date()
            buy_atr_histories: dict[str, tuple[CompletedDailyBar, ...]] = {}
            buy_atr_sessions: tuple[date, ...] = ()
            if v3_scenario:
                atr_anchor = transport.quote(_SYMBOL)
                history, expected_sessions = _synthetic_atr_history(
                    as_of_session=buy_session,
                    anchor_price_krw=atr_anchor.price_krw,
                    period=policy.atr_period or 22,
                )
                buy_atr_histories[_SYMBOL] = history
                buy_atr_sessions = expected_sessions
                steps.append(
                    {
                        "step": "syntheticAtrHistory",
                        "asOfSession": buy_session.isoformat(),
                        "barCount": len(history),
                        "note": "실제 현재가 규모와 XKRX 세션 축을 사용한 합성 adjusted OHLC",
                    }
                )
            buy_inputs = AutomationInputs(
                session_date=buy_session,
                policy=policy,
                buyable_quantity=1,
                buyable_amount_krw=policy.capital_limit_krw,
                atr_histories=buy_atr_histories,
                atr_expected_sessions=buy_atr_sessions,
                ai_judgement_provider_bound=_SCENARIO == "AI_RERANK",
                signals=(
                    SignalCandidate(
                        symbol=_SYMBOL,
                        lstm_signal="BUY",
                        baseline_signal="BUY",
                        expected_return=0.05,
                        confidence=0.9,
                    ),
                )
                if _SCENARIO != "AI_RERANK"
                else (
                    # 둘 다 2-of-2 합의를 통과한다. 규칙 정렬은 기대수익이 먼저이므로
                    # 규칙만이면 1등은 _SYMBOL이다.
                    SignalCandidate(
                        symbol=_SYMBOL,
                        lstm_signal="BUY",
                        baseline_signal="BUY",
                        expected_return=0.0400,
                        confidence=0.51,
                    ),
                    # 기대수익은 근소하게 낮지만 확신도가 훨씬 높다. AI가 순위를 바꾼다면 여기다.
                    SignalCandidate(
                        symbol=_RIVAL_SYMBOL,
                        lstm_signal="BUY",
                        baseline_signal="BUY",
                        expected_return=0.0399,
                        confidence=0.93,
                    ),
                ),
            )
            buy_projection = _drive(
                store,
                transport,
                run_id="auto_run_rehearsal_buy_0001",
                session=buy_session,
                inputs=buy_inputs,
                terminal={"COMPLETED", "CANCELLED_UNFILLED", "HALTED", "SKIPPED_NO_ACTION"},
                label="BUY",
            )
            if buy_projection.get("state") != "COMPLETED":
                raise RehearsalFailed(
                    f"매수 세션이 COMPLETED가 아니다: {buy_projection.get('state')}"
                )
            open_positions = [item for item in store.positions if item.status == "OPEN"]
            if len(open_positions) != 1:
                raise RehearsalFailed("매수 뒤 OPEN 포지션이 정확히 하나가 아니다")
            position = open_positions[0]
            if _SCENARIO == "AI_RERANK":
                rule_top = min(
                    buy_inputs.signals,
                    key=lambda item: (-item.expected_return, -item.confidence, item.symbol),
                ).symbol
                steps.append(
                    {
                        "step": "aiJudgement",
                        "ruleTopSymbol": rule_top,
                        "selectedSymbol": position.symbol,
                        "aiChangedTop": rule_top != position.symbol,
                        "judgeCalls": transport.judge_calls,
                        "judgement": transport.last_judgement,
                    }
                )
            steps.append(
                {
                    "step": "positionOpened",
                    "symbol": position.symbol,
                    "quantity": position.quantity,
                    "entryAverageFillPriceKrw": position.entry_average_fill_price_krw,
                    "entrySession": position.entry_session.isoformat(),
                    "expirySession": (
                        position.expiry_session.isoformat() if position.expiry_session else None
                    ),
                }
            )

            # 같은 lot을 실제 매도로 닫는다. 시나리오에 따라 청산 사유가 달라진다.
            sell_atr_histories: dict[str, tuple[CompletedDailyBar, ...]] = {}
            sell_atr_sessions: tuple[date, ...] = ()
            if _SCENARIO == "MODEL_SELL":
                # 만기 이전 세션으로 옮기고 같은 종목에 SELL 신호를 준다. 시세를 만지지 않고
                # 신호만으로 MODEL_SELL 분기를 태운다.
                sell_session = _nth_next_session(position.entry_session, 1)
                sell_signals = (
                    SignalCandidate(
                        symbol=_SYMBOL,
                        lstm_signal="SELL",
                        baseline_signal="SELL",
                        expected_return=-0.03,
                        confidence=0.8,
                    ),
                )
            elif _SCENARIO == "THREE_SESSION_SOAK":
                # 매수 세션 다음 두 세션에서 신호 없이 tick만 돌린다. 자동운용이 아무 일도
                # 없는 날에 무엇을 하는지, 그리고 그 사이 포지션이 살아 있는지를 본다.
                for offset in (1, 2):
                    mid_session = _nth_next_session(position.entry_session, offset)
                    mid_projection = _drive(
                        store,
                        transport,
                        run_id=f"auto_run_rehearsal_soak_mid{offset:02d}",
                        session=mid_session,
                        inputs=AutomationInputs(
                            session_date=mid_session,
                            policy=policy,
                            buyable_quantity=0,
                            buyable_amount_krw=0,
                            signals=(),
                        ),
                        terminal={
                            "COMPLETED",
                            "SKIPPED_NO_ACTION",
                            "CANCELLED_UNFILLED",
                            "HALTED",
                        },
                        label=f"MID{offset}",
                    )
                    open_now = [item for item in store.positions if item.status == "OPEN"]
                    steps.append(
                        {
                            "step": "soakSession",
                            "sessionDate": mid_session.isoformat(),
                            "state": mid_projection.get("state"),
                            "openPositions": len(open_now),
                        }
                    )
                    if len(open_now) != 1:
                        raise RehearsalFailed(f"{mid_session} 세션 뒤 OPEN 포지션이 하나가 아니다")
                sell_session = position.expiry_session
                sell_signals = ()
            elif _SCENARIO in {"MAX_HOLDING_SESSIONS", "AI_RERANK"}:
                sell_session = position.expiry_session
                sell_signals = ()
            elif _SCENARIO == "ATR_TRAILING":
                sell_session = _nth_next_session(position.entry_session, 1)
                sell_signals = ()
                trigger_quote = transport.quote(position.symbol)
                history, expected_sessions = _synthetic_atr_history(
                    as_of_session=sell_session,
                    anchor_price_krw=trigger_quote.price_krw,
                    period=position.atr_period or 22,
                )
                atr = wilder_atr(
                    history,
                    period=position.atr_period or 22,
                    as_of_session=sell_session,
                    expected_sessions=expected_sessions,
                )
                multiplier = position.atr_multiplier_milli or 3_000
                distance = int(
                    (atr.value_krw * Decimal(multiplier) / Decimal(1_000)).to_integral_value(
                        rounding=ROUND_CEILING
                    )
                )
                cushion = max(1_000, trigger_quote.price_krw // 20)
                position.peak_price_krw = trigger_quote.price_krw + distance + cushion
                position.trailing_stop_krw = 1
                sell_atr_histories[position.symbol] = history
                sell_atr_sessions = expected_sessions
                steps.append(
                    {
                        "step": "syntheticAtrTrigger",
                        "atrValueKrw": str(atr.value_krw),
                        "quotePriceKrw": trigger_quote.price_krw,
                        "syntheticPeakPriceKrw": position.peak_price_krw,
                        "note": "ATR/peak만 합성, KIS 현재가·SELL·체결·대사는 실제 모의계좌",
                    }
                )
            elif _SCENARIO == "MAX_HOLDING_UNLIMITED":
                if position.expiry_session is not None or position.max_holding_sessions != 0:
                    raise RehearsalFailed("무제한 정책 snapshot이 position에 보존되지 않았다")
                hold_session = _nth_next_session(position.entry_session, 6)
                hold_quote = transport.quote(position.symbol)
                hold_history, hold_expected = _synthetic_atr_history(
                    as_of_session=hold_session,
                    anchor_price_krw=hold_quote.price_krw,
                    period=position.atr_period or 22,
                )
                hold_projection = _drive(
                    store,
                    transport,
                    run_id="auto_run_rehearsal_unlimited_hold_0001",
                    session=hold_session,
                    inputs=AutomationInputs(
                        session_date=hold_session,
                        policy=policy,
                        buyable_quantity=0,
                        buyable_amount_krw=0,
                        atr_histories={position.symbol: hold_history},
                        atr_expected_sessions=hold_expected,
                        signals=(),
                    ),
                    terminal={"SKIPPED_NO_ACTION", "HALTED", "COMPLETED"},
                    label="UNLIMITED_HOLD",
                )
                open_now = [item for item in store.positions if item.status == "OPEN"]
                if hold_projection.get("state") != "SKIPPED_NO_ACTION" or len(open_now) != 1:
                    raise RehearsalFailed("무제한 정책이 6세션 뒤 포지션을 유지하지 못했다")
                steps.append(
                    {
                        "step": "unlimitedHolding",
                        "sessionDate": hold_session.isoformat(),
                        "sessionsAfterEntry": 6,
                        "state": hold_projection.get("state"),
                        "expirySession": position.expiry_session,
                        "openPositions": len(open_now),
                    }
                )
                sell_session = _nth_next_session(position.entry_session, 7)
                sell_signals = (
                    SignalCandidate(
                        symbol=position.symbol,
                        lstm_signal="SELL",
                        baseline_signal="SELL",
                        expected_return=-0.03,
                        confidence=0.8,
                    ),
                )
                close_quote = transport.quote(position.symbol)
                close_history, close_expected = _synthetic_atr_history(
                    as_of_session=sell_session,
                    anchor_price_krw=close_quote.price_krw,
                    period=position.atr_period or 22,
                )
                sell_atr_histories[position.symbol] = close_history
                sell_atr_sessions = close_expected
            elif _SCENARIO in {"STOP_LOSS", "TAKE_PROFIT"}:
                # 만기 전 세션이라 MAX_HOLDING_SESSIONS 분기는 열리지 않는다. 신호도 주지
                # 않으므로 MODEL_SELL도 아니다. 남는 것은 손절선과 익절선뿐이다.
                sell_session = _nth_next_session(position.entry_session, 1)
                sell_signals = ()
                actual_entry = position.entry_average_fill_price_krw
                if actual_entry is None:
                    raise RehearsalFailed("진입 체결가가 없어 손익률을 만들 수 없다")
                # 손절은 진입가를 올려 손실을, 익절은 내려 이익을 만든다. 기본 정책은
                # stop 500bp / take 1000bp이고 15%면 두 선을 넉넉히 넘는다.
                position.entry_average_fill_price_krw = (
                    int(actual_entry * 115 // 100)
                    if _SCENARIO == "STOP_LOSS"
                    else max(1, int(actual_entry * 85 // 100))
                )
                steps.append(
                    {
                        "step": "syntheticEntryPrice",
                        "scenario": _SCENARIO,
                        "actualEntryPriceKrw": actual_entry,
                        "syntheticEntryPriceKrw": position.entry_average_fill_price_krw,
                        "note": (
                            "실제 등락을 기다릴 수 없어 진입가만 옮겼다. 시세와 주문은 실제이고 "
                            "이 run의 realizedPnl은 실제 손익이 아니다"
                        ),
                    }
                )
            else:
                raise RehearsalFailed(f"지원하지 않는 시나리오다: {_SCENARIO}")
            sell_inputs = AutomationInputs(
                session_date=sell_session,
                policy=policy,
                buyable_quantity=0,
                buyable_amount_krw=0,
                open_position_market_value_krw=position.quantity
                * (position.entry_average_fill_price_krw or 0),
                atr_histories=sell_atr_histories,
                atr_expected_sessions=sell_atr_sessions,
                signals=sell_signals,
            )
            sell_projection = _drive(
                store,
                transport,
                run_id="auto_run_rehearsal_sell_0001",
                session=sell_session,
                inputs=sell_inputs,
                terminal={"COMPLETED", "CANCELLED_UNFILLED", "HALTED", "SKIPPED_NO_ACTION"},
                label="SELL",
            )
            if sell_projection.get("state") != "COMPLETED":
                raise RehearsalFailed(
                    f"매도 세션이 COMPLETED가 아니다: {sell_projection.get('state')}"
                )
            expected_new_exit = {
                "ATR_TRAILING": "ATR_TRAILING",
                "MAX_HOLDING_UNLIMITED": "MODEL_SELL",
            }.get(_SCENARIO)
            if expected_new_exit and sell_projection.get("exitReason") != expected_new_exit:
                raise RehearsalFailed(
                    f"{_SCENARIO} 청산 사유가 다르다: {sell_projection.get('exitReason')}"
                )
            if any(item.status == "OPEN" for item in store.positions):
                raise RehearsalFailed("매도 뒤에도 OPEN 포지션이 남아 있다")
            closed = [item for item in store.positions if item.status == "CLOSED"]
            if len(closed) != 1:
                raise RehearsalFailed("CLOSED 포지션이 정확히 하나가 아니다")
            closed_position = closed[0]
            if closed_position.realized_pnl_krw is None:
                raise RehearsalFailed("청산했는데 실현손익이 기록되지 않았다")
            steps.append(
                {
                    "step": "positionClosed",
                    "exitReason": sell_projection.get("exitReason"),
                    "entryAverageFillPriceKrw": closed_position.entry_average_fill_price_krw,
                    "exitAverageFillPriceKrw": closed_position.exit_average_fill_price_krw,
                    "exitFilledQuantity": closed_position.exit_filled_quantity,
                    "realizedPnlKrw": closed_position.realized_pnl_krw,
                    "closedPositions": len(store.positions),
                }
            )

            post = _balance(client)
            steps.append({"step": "postBalance", **post})
            if post["symbolQuantity"] != pre["symbolQuantity"]:
                raise RehearsalFailed(
                    "왕복 뒤 보유수량이 원래대로 돌아오지 않았다: "
                    f"{pre['symbolQuantity']} -> {post['symbolQuantity']}"
                )
        finally:
            client.close()
            market.close()
    except (RehearsalFailed, CertificationWindowClosed) as error:
        _print_report(
            {
                "status": "FAILED",
                "reason": str(error),
                "engineSteps": steps,
                "sessionKst": datetime.now(tz=_KST).isoformat(),
            },
            stream=sys.stderr,
        )
        return 1
    _print_report(
        {
            "status": "SUCCESS",
            "scenario": _SCENARIO,
            "symbol": _SYMBOL,
            "engineSteps": steps,
            "transportSteps": transport.steps,
            "physicalCalls": transport.physical_calls,
            "vertexProviderCalls": 0,
            "sessionKst": datetime.now(tz=_KST).isoformat(),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
