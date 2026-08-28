"""KIS_MOCK 매수 체결 → 매도 체결 왕복 리허설.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며
pytest 수집 대상도 아니다(`test_` 접두사가 아니다). 목적은 자동운용 폐루프가 실제 운용에
들어가기 전에 아직 한 번도 실호출되지 않은 구간을 실제 KIS 모의계좌로 찍어보는 것이다.

지금까지 실호출 0회였던 구간:
  - 매도 주문 제출(VTTC0011U)
  - 실제 체결
  - 실응답 기반 평균단가·부분체결 파싱
  - 매수/매도 전후 잔고 대사

Decision/RiskEngine 파이프라인은 의도적으로 우회한다. `kis_mock_certification_cli`가
이미 같은 경계에서 실주문을 넣는 것과 같은 패턴이며, 여기서 얻은 실응답을 근거로
`riskComplete` 구현 범위를 확정하는 것이 목적이다.

실행:
  /usr/bin/docker compose --project-name capstone-p1 \
    --env-file deploy/p1/.state-app/runtime.env -f deploy/p1/compose.yml \
    run --rm --no-deps \
    -v "$PWD/workspaces/decision-platform/python-services/tests/rehearsal:/rehearsal:ro" \
    -e P1_KIS_MOCK_ROUNDTRIP_REHEARSAL=1 \
    kis-mock-certification-runner python /rehearsal/kis_mock_roundtrip_rehearsal.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

from app.brokerage.kis_mock_online_client import (
    BALANCE_PATH,
    BUYABLE_PATH,
    EXECUTIONS_PATH,
    MOCK_BALANCE_TR_ID,
    MOCK_BUY_TR_ID,
    MOCK_BUYABLE_TR_ID,
    MOCK_EXECUTIONS_RECENT_TR_ID,
    MOCK_SELL_TR_ID,
    ORDER_CASH_PATH,
    KISBrokerageCallBudget,
    KISMockBrokerageHttpClient,
)
from app.brokerage.kis_mock_certification_gate import (
    CertificationWindowClosed,
    require_certification_window,
)
from app.data.kis.http_client import CURRENT_PRICE_PATH, KISHttpClient
from app.data.kis.settings import KISSettings

_KST: Final = ZoneInfo("Asia/Seoul")
_SYMBOL: Final = "005930"
_QUANTITY: Final = 1
_LIMIT_ORDER_DIVISION: Final = "00"
_OPT_IN: Final = "P1_KIS_MOCK_ROUNDTRIP_REHEARSAL"
_FILL_POLL_ATTEMPTS: Final = 10
_FILL_POLL_SECONDS: Final = 3.0
# 시세 1 + 사전잔고 1 + 매수가능 1 + 매수 1 + 매도 1 + 사후잔고 1 + 체결조회 폴링 여유.
_BROKERAGE_CAP: Final = 30
_TOKEN_CAP: Final = 1


class RehearsalFailed(RuntimeError):
    """리허설을 중단시킨 사유. 실패를 성공으로 축소하지 않는다."""


def _require_opt_in() -> None:
    if os.environ.get(_OPT_IN) != "1":
        raise RehearsalFailed(f"{_OPT_IN}=1 이 없으면 실주문 리허설을 실행하지 않는다")
    if os.environ.get("CI"):
        raise RehearsalFailed("CI 환경에서는 실주문 리허설을 실행하지 않는다")


def _require_mock_online(settings: KISSettings) -> None:
    if settings.kis_mode != "mock":
        raise RehearsalFailed("KIS_MODE=mock 이 아니면 실행하지 않는다")
    if settings.kis_offline:
        raise RehearsalFailed("KIS_OFFLINE=0 이 아니면 실호출이 나가지 않는다")


def _tick_size(price: int) -> int:
    """KRX 일반 주식 호가단위. ETF/ETN은 이 리허설 대상이 아니다."""
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def _positive_int(value: Any, label: str) -> int:
    text = str(value).strip()
    if not text.lstrip("-").isdigit():
        raise RehearsalFailed(f"{label} 응답이 정수가 아니다")
    number = int(text)
    if number <= 0:
        raise RehearsalFailed(f"{label} 응답이 양수가 아니다")
    return number


def _nonnegative_int(value: Any, label: str) -> int:
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


def _current_price(client: KISHttpClient) -> int:
    payload = client.request(
        "GET",
        CURRENT_PRICE_PATH,
        "FHKST01010100",
        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": _SYMBOL},
    )
    _require_success(payload, "시세 조회")
    output = payload.get("output")
    if not isinstance(output, dict):
        raise RehearsalFailed("시세 응답 형식이 유효하지 않다")
    return _positive_int(output.get("stck_prpr"), "현재가")


def _balance(client: KISMockBrokerageHttpClient, label: str) -> dict[str, Any]:
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
    _require_success(payload, f"{label} 잔고 조회")
    summary = payload.get("output2")
    rows = summary if isinstance(summary, list) else []
    cash = rows[0].get("dnca_tot_amt") if rows and isinstance(rows[0], dict) else None
    holdings = payload.get("output1")
    position = 0
    if isinstance(holdings, list):
        for row in holdings:
            if isinstance(row, dict) and str(row.get("pdno", "")).strip() == _SYMBOL:
                position = _nonnegative_int(row.get("hldg_qty"), "보유수량")
    return {
        "cashKrw": _nonnegative_int(cash, f"{label} 예수금") if cash is not None else None,
        "symbolQuantity": position,
    }


def _buyable(client: KISMockBrokerageHttpClient, price: int) -> dict[str, int]:
    payload = client.request(
        "GET",
        BUYABLE_PATH,
        MOCK_BUYABLE_TR_ID,
        params={
            "PDNO": _SYMBOL,
            "ORD_UNPR": str(price),
            "ORD_DVSN": _LIMIT_ORDER_DIVISION,
            "CMA_EVLU_AMT_ICLD_YN": "N",
            "OVRS_ICLD_YN": "N",
        },
    )
    _require_success(payload, "매수가능 조회")
    output = payload.get("output")
    if not isinstance(output, dict):
        raise RehearsalFailed("매수가능 응답 형식이 유효하지 않다")
    return {
        "buyableAmountKrw": _nonnegative_int(output.get("ord_psbl_cash"), "주문가능금액"),
        "buyableQuantity": _nonnegative_int(output.get("nrcvb_buy_qty"), "매수가능수량"),
    }


def _submit(
    client: KISMockBrokerageHttpClient,
    *,
    tr_id: str,
    price: int,
    label: str,
) -> dict[str, str]:
    payload = client.request(
        "POST",
        ORDER_CASH_PATH,
        tr_id,
        json_body={
            "PDNO": _SYMBOL,
            "ORD_DVSN": _LIMIT_ORDER_DIVISION,
            "ORD_QTY": str(_QUANTITY),
            "ORD_UNPR": str(price),
        },
    )
    _require_success(payload, f"{label} 제출")
    output = payload.get("output")
    if not isinstance(output, dict):
        raise RehearsalFailed(f"{label} 제출 응답 형식이 유효하지 않다")
    order_no = str(output.get("ODNO", "")).strip()
    branch = str(output.get("KRX_FWDG_ORD_ORGNO", "")).strip()
    if not order_no or not branch:
        raise RehearsalFailed(f"{label} 제출 응답에 주문번호가 없다")
    return {"orderNo": order_no, "branchNo": branch}


def _executions(
    client: KISMockBrokerageHttpClient,
    reference: dict[str, str],
    session: str,
) -> dict[str, Any]:
    compact = session.replace("-", "")
    payload = client.request(
        "GET",
        EXECUTIONS_PATH,
        MOCK_EXECUTIONS_RECENT_TR_ID,
        params={
            "INQR_STRT_DT": compact,
            "INQR_END_DT": compact,
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": _SYMBOL,
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
    cumulative = _nonnegative_int(row.get("tot_ccld_qty"), "누적체결수량")
    leaves = _nonnegative_int(row.get("rmn_qty"), "미체결수량")
    if cumulative + leaves > _QUANTITY:
        raise RehearsalFailed("체결 수량 불변식 위반: 누적+잔여 > 주문수량")
    average_text = str(row.get("avg_prvs", "")).strip()
    average = int(float(average_text)) if average_text and average_text != "0" else None
    if cumulative > 0 and (average is None or average <= 0):
        raise RehearsalFailed("체결이 있는데 평균단가가 유효하지 않다")
    return {
        "cumulativeQuantity": cumulative,
        "leavesQuantity": leaves,
        "averageFillPriceKrw": average,
    }


def _await_fill(
    client: KISMockBrokerageHttpClient,
    reference: dict[str, str],
    session: str,
    label: str,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for attempt in range(1, _FILL_POLL_ATTEMPTS + 1):
        snapshot = _executions(client, reference, session)
        if snapshot["cumulativeQuantity"] >= _QUANTITY:
            print(f"  {label} 체결 확인 (시도 {attempt}회)", file=sys.stderr)
            return snapshot
        time.sleep(_FILL_POLL_SECONDS)
    raise RehearsalFailed(
        f"{label} 체결이 관측되지 않았다: 누적={snapshot.get('cumulativeQuantity')} "
        f"잔여={snapshot.get('leavesQuantity')}. 미체결을 체결로 추정하지 않는다"
    )


def main() -> int:
    steps: list[dict[str, Any]] = []
    try:
        _require_opt_in()
        session = require_certification_window(datetime.now(tz=UTC))
        settings = KISSettings()
        _require_mock_online(settings)

        budget = KISBrokerageCallBudget(token_p_cap=_TOKEN_CAP, brokerage_cap=_BROKERAGE_CAP)
        market = KISHttpClient(settings=settings)
        client = KISMockBrokerageHttpClient(settings=settings, budget=budget)
        try:
            price = _current_price(market)
            tick = _tick_size(price)
            buy_price = price + tick
            sell_price = max(tick, price - tick)
            steps.append({"step": "price", "currentPriceKrw": price, "tickKrw": tick})

            pre = _balance(client, "사전")
            steps.append({"step": "preBalance", **pre})

            allowance = _buyable(client, buy_price)
            steps.append({"step": "buyable", "limitPriceKrw": buy_price, **allowance})
            if allowance["buyableQuantity"] < _QUANTITY:
                raise RehearsalFailed("매수가능수량이 1주 미만이다")

            buy_ref = _submit(client, tr_id=MOCK_BUY_TR_ID, price=buy_price, label="매수")
            steps.append({"step": "submitBuy", "limitPriceKrw": buy_price, **buy_ref})
            buy_fill = _await_fill(client, buy_ref, session, "매수")
            steps.append({"step": "buyFill", **buy_fill})

            sell_ref = _submit(client, tr_id=MOCK_SELL_TR_ID, price=sell_price, label="매도")
            steps.append({"step": "submitSell", "limitPriceKrw": sell_price, **sell_ref})
            sell_fill = _await_fill(client, sell_ref, session, "매도")
            steps.append({"step": "sellFill", **sell_fill})

            post = _balance(client, "사후")
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
                    "completedSteps": steps,
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
                "symbol": _SYMBOL,
                "quantity": _QUANTITY,
                "completedSteps": steps,
                "sessionKst": datetime.now(tz=_KST).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
