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
from typing import Any, Final
from zoneinfo import ZoneInfo

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
from app.strong_llm.judge_client import (
    JudgeClientSettings,
    StrongLlmJudgeClient,
    StrongLlmJudgeUnavailableError,
)

_KST: Final = ZoneInfo("Asia/Seoul")
_SYMBOL: Final = os.environ.get("P1_KIS_MOCK_REHEARSAL_SYMBOL", "005930")
_OPT_IN: Final = "P1_KIS_MOCK_CLOSED_LOOP_REHEARSAL"
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
# STOP_LOSS/TAKE_PROFIT은 실제 등락에 의존하므로 여기서 강제하지 않는다.
_SCENARIO: Final = os.environ.get("P1_KIS_MOCK_REHEARSAL_SCENARIO", "MAX_HOLDING_SESSIONS")
# AI_RERANK에서 규칙상 2등이 되는 종목. 규칙은 기대수익만 보고 AI는 확신도까지 본다.
_RIVAL_SYMBOL: Final = os.environ.get("P1_KIS_MOCK_REHEARSAL_RIVAL_SYMBOL", "000660")
_JUDGE_QUESTION: Final = (
    "주어진 후보 각각에 0과 1 사이 점수를 매기고 매수를 막아야 하면 veto를 표시하라."
)


class RehearsalFailed(RuntimeError):
    """리허설을 중단시킨 사유. 실패를 성공으로 축소하지 않는다."""


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

    def judge(self, candidates: tuple[SignalCandidate, ...]) -> AiJudgement | None:
        """Strong LLM에게 후보를 보이고 점수를 받는다.

        설정이 없으면 None이다. 엔진은 그때 AI_NOT_PARTICIPATED로 적고 규칙만으로 계속한다.
        판단을 못 받았다고 리허설을 실패로 만들지 않는다. 그것이 운영 계약이다.
        """

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

            policy = AutomationPolicySnapshot.from_preset(
                policy_id="auto_pol_" + "a" * 32,
                version=1,
                capital_limit_krw=10_000_000,
                preset="BALANCED",
            )
            store = AutomationStore(
                account_id=_ACCOUNT_ID,
                brokerage_mode="KIS_MOCK",
                principle_id=_PRINCIPLE_ID,
                strategy_id=_STRATEGY_ID,
                baseline_account_digest=hashlib.sha256(b"rehearsal-baseline").hexdigest(),
            )

            buy_session = datetime.now(tz=_KST).date()
            buy_inputs = AutomationInputs(
                session_date=buy_session,
                policy=policy,
                buyable_quantity=1,
                buyable_amount_krw=policy.capital_limit_krw,
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
                    "expirySession": position.expiry_session.isoformat(),
                }
            )

            # 같은 lot을 실제 매도로 닫는다. 시나리오에 따라 청산 사유가 달라진다.
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
            elif _SCENARIO in {"MAX_HOLDING_SESSIONS", "AI_RERANK"}:
                sell_session = position.expiry_session
                sell_signals = ()
            else:
                raise RehearsalFailed(f"지원하지 않는 시나리오다: {_SCENARIO}")
            sell_inputs = AutomationInputs(
                session_date=sell_session,
                policy=policy,
                buyable_quantity=0,
                buyable_amount_krw=0,
                open_position_market_value_krw=position.quantity
                * (position.entry_average_fill_price_krw or 0),
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
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "reason": str(error),
                    "engineSteps": steps,
                    "sessionKst": datetime.now(tz=_KST).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "status": "SUCCESS",
                "scenario": _SCENARIO,
                "symbol": _SYMBOL,
                "engineSteps": steps,
                "transportSteps": transport.steps,
                "physicalCalls": transport.physical_calls,
                "vertexProviderCalls": 0,
                "sessionKst": datetime.now(tz=_KST).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
