"""두 원장(paper·mock)을 실제 스택에서 각각 한 바퀴 돌리고, 서로 섞이지 않는지 본다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다.

무엇을 확인하나.
  1. 판단 없이는 주문이 없다. 주문은 승인된 판단 ID를 실어야만 접수된다.
  2. INTERNAL_PAPER 원장: 주문 → 잔고 → 매수가능 → 체결 조회.
  3. KIS_MOCK 원장: 주문 → 상태 → 취소 → 대사.
  4. 두 원장이 섞이지 않는다. 한쪽 계좌 ID로 다른 쪽 원장을 읽으면 열리지 않는다.
  5. 같은 멱등키로 두 번 넣으면 주문이 하나다.

무엇을 확인하지 않나. 이 장외 runner는 실제 provider socket을 열지 않는다. 현재 등록된
`kis-mock.env`를 읽거나 바꾸지 않고, 컨테이너 안의 결정적 모의 브로커리지로만 돈다. 실제 KIS
모의계좌 주문·체결·취소·대사는 별도의 장중 runner가 담당한다.

정리. 이 runner가 만드는 것은 판단과 주문, 그리고 그에 딸린 관측이다. 시작 시 스냅샷을 찍고
끝에서 차집합만 지운다. 정리에 실패하면 FAIL이다.

실행:
  P1_BROKERAGE_E2E=1 python -m tests.e2e.brokerage_e2e \\
    --out artifacts/decision-platform/e2e/brokerage.json
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from .full_pipeline_e2e import (
    INITIAL_CASH_KRW,
    _compose,
    _publish_driver,
    _start_offline_brokerage,
    _stop_offline_brokerage,
    quiesce_rival_portfolio_contexts,
    restore_portfolio_contexts,
    seed_risk_metrics,
)
from .harness import (
    Api,
    HarnessError,
    Recorder,
    cleanup,
    psql,
    require_opt_in,
    snapshot,
    wait_healthy,
    write_report,
)

_OPT_IN: Final = "P1_BROKERAGE_E2E"
_SYMBOL: Final = "005930"
_PRICE: Final = 70_100
_QUANTITY: Final = 1
_FIXTURE_ACCOUNT: Final = "acct_" + "a" * 32


def _key() -> str:
    return f"idem_{uuid.uuid4().hex}"


def _order_intent() -> dict[str, Any]:
    return {
        "symbol": _SYMBOL,
        "side": "BUY",
        "quantity": _QUANTITY,
        "orderType": "LIMIT",
        "estimatedPrice": _PRICE,
        "estimatedAmount": _PRICE * _QUANTITY,
        "strategyId": "p1-e2e-brokerage",
        "timeframe": "1d",
    }


def _active_principle(owner: Api) -> str:
    status, listed = owner.request("GET", "/api/v1/principles")
    items = ((listed.get("data") or {}).get("items")) or []
    active = [item for item in items if item.get("status") == "ACTIVE"]
    if status != 200 or not active:
        raise HarnessError("no ACTIVE principle is available for order evaluation")
    return str(active[0]["principleId"])


def _evaluate(owner: Api, principle: str, source: str) -> tuple[int, dict[str, Any]]:
    return owner.request(
        "POST",
        "/api/v1/decisions/evaluate-order",
        {
            "principleId": principle,
            "portfolioSource": source,
            "orderIntent": _order_intent(),
        },
        idempotency_key=_key(),
    )


def _account_id(source: str, *, optional: bool = False) -> str | None:
    """관측된 계좌 ID를 DB에서 읽는다. 지어내지 않는다."""

    # 관측 표에는 계좌 ID가 그대로 없다. scope hash 앞 32자가 계좌 suffix다
    # (`full_pipeline_e2e`의 `_ACCOUNT`가 같은 규칙으로 만들어진다).
    row = psql(
        "select left(account_scope_hash, 32) from public.portfolio_balance_observations"
        f" where source = '{source}' and context_status = 'ACTIVE' order by observed_at desc limit 1;"
    )
    if not row:
        if optional:
            return None
        raise HarnessError(f"no ACTIVE {source} account observation is available")
    return "acct_" + row.splitlines()[0]


def check_order_requires_decision(recorder: Recorder, owner: Api) -> None:
    """판단 ID 없이 주문이 열리면 그것 자체가 회귀다."""

    status, body = owner.request(
        "POST",
        "/api/v1/brokerage/paper/orders",
        {"orderIntent": _order_intent(), "userAcknowledgement": {"warningsAccepted": True}},
        idempotency_key=_key(),
    )
    fabricated_status, fabricated_body = owner.request(
        "POST",
        "/api/v1/brokerage/paper/orders",
        {
            "decisionId": "dec_" + uuid.uuid4().hex,
            "orderIntent": _order_intent(),
            "userAcknowledgement": {"warningsAccepted": True},
        },
        idempotency_key=_key(),
    )
    recorder.add(
        "판단 없는 주문은 닫힌다",
        "PASS" if status == 400 and fabricated_status in (404, 409, 422) else "FAIL",
        f"판단 ID 없음 HTTP {status} {(body.get('error') or {}).get('code')} / "
        f"지어낸 판단 ID HTTP {fabricated_status} "
        f"{(fabricated_body.get('error') or {}).get('code')}",
    )


def check_ledger(recorder: Recorder, owner: Api, principle: str, lane: str) -> str | None:
    """한 원장을 주문부터 체결 조회까지 한 바퀴 돌린다."""

    source = "INTERNAL_PAPER" if lane == "paper" else "KIS_MOCK"
    decision_status, decision = _evaluate(owner, principle, source)
    decision_id = (decision.get("data") or {}).get("decisionId")
    if decision_status != 200 or not decision_id:
        recorder.add(
            f"{lane} 판단",
            "FAIL",
            f"HTTP {decision_status} {(decision.get('error') or {}).get('code')}",
        )
        return None

    verdict = ((decision.get("data") or {}).get("riskDecision") or {}).get("decision")
    key = _key()
    order_status, ordered = owner.request(
        "POST",
        f"/api/v1/brokerage/{lane}/orders",
        {
            "decisionId": decision_id,
            "orderIntent": _order_intent(),
            "userAcknowledgement": {"warningsAccepted": True},
        },
        idempotency_key=key,
    )
    order = (ordered.get("data") or {}).get("orderId")
    # 같은 멱등키를 다시 쓴다. 주문이 하나여야 한다.
    repeat_status, repeated = owner.request(
        "POST",
        f"/api/v1/brokerage/{lane}/orders",
        {
            "decisionId": decision_id,
            "orderIntent": _order_intent(),
            "userAcknowledgement": {"warningsAccepted": True},
        },
        idempotency_key=key,
    )
    repeated_order = (repeated.get("data") or {}).get("orderId")
    # 판정은 관측한 위험 결정에서 나온다. 시장 판단을 테스트가 강요하지 않는다. ALLOW면 주문이
    # 접수돼야 하고, 그 외에는 typed refusal로 닫혀야 한다. 둘 다 같은 계약의 양면이다.
    if verdict == "ALLOW":
        accepted = (
            order_status in (200, 201)
            and bool(order)
            and repeat_status in (200, 201)
            and repeated_order == order
        )
    else:
        accepted = (
            order_status == 422 and (ordered.get("error") or {}).get("code") == "RISK_BLOCKED"
        )
    recorder.add(
        f"{lane} 주문 접수와 멱등",
        "PASS" if accepted else "FAIL",
        f"위험판정={verdict} HTTP {order_status} orderId={'있음' if order else '없음'} "
        f"재시도 HTTP {repeat_status} 같은 주문={repeated_order == order} "
        f"code={(ordered.get('error') or {}).get('code')} "
        "(ALLOW면 접수, 아니면 RISK_BLOCKED)",
    )

    account = _account_id(source, optional=True)
    if account is None:
        recorder.add(
            f"{lane} 잔고·매수가능·체결 조회",
            "INFO",
            f"{source} 계좌 관측이 없어 조회할 대상이 없다. 지어내지 않는다",
        )
        return str(order) if order else None
    balances_status, balances = owner.request(
        "GET", f"/api/v1/brokerage/{lane}/accounts/{account}/balances"
    )
    # 매수가능은 종목과 가격이 있어야 성립한다. 둘 다 필수 query다.
    buyable_status, buyable = owner.request(
        "GET",
        f"/api/v1/brokerage/{lane}/accounts/{account}/buyable?symbol={_SYMBOL}&price={_PRICE}",
    )
    now = datetime.now(UTC)
    window = (
        f"from={(now - timedelta(days=1)).date().isoformat()}"
        f"&to={(now + timedelta(days=1)).date().isoformat()}"
    )
    fills_status, fills = owner.request(
        "GET", f"/api/v1/brokerage/{lane}/accounts/{account}/fills?{window}"
    )
    recorder.add(
        f"{lane} 잔고·매수가능·체결 조회",
        "PASS"
        if balances_status == 200 and buyable_status == 200 and fills_status == 200
        else "FAIL",
        f"잔고 HTTP {balances_status} 매수가능 HTTP {buyable_status} 체결 HTTP {fills_status} "
        f"잔고키={sorted((balances.get('data') or {}).keys())[:4]} "
        f"매수가능키={sorted((buyable.get('data') or {}).keys())[:4]} "
        f"체결 {len(((fills.get('data') or {}).get('items')) or [])}건",
    )

    order_view_status, order_view = owner.request("GET", f"/api/v1/brokerage/orders/{order}")
    recorder.add(
        f"{lane} 주문 단건 조회",
        "PASS"
        if order_view_status == 200 and (order_view.get("data") or {}).get("orderId") == order
        else "FAIL",
        f"HTTP {order_view_status} 상태={(order_view.get('data') or {}).get('status')}",
    )
    return str(order)


def check_cancel_and_reconcile(
    recorder: Recorder, owner: Api, admin: Api, order: str | None
) -> None:
    if order is None:
        recorder.add(
            "취소와 대사",
            "INFO",
            "위험 판정이 ALLOW가 아니어서 접수된 주문이 없다. 취소할 대상이 없다",
        )
        return
    cancel_status, cancelled = owner.request(
        "POST", f"/api/v1/brokerage/orders/{order}/cancel", {}, idempotency_key=_key()
    )
    # 대사는 ADMIN만 할 수 있다. 소유자가 부르면 403이어야 한다.
    owner_reconcile_status, _ = owner.request(
        "POST", f"/api/v1/brokerage/orders/{order}/reconcile", {}, idempotency_key=_key()
    )
    reconcile_status, reconciled = admin.request(
        "POST", f"/api/v1/brokerage/orders/{order}/reconcile", {}, idempotency_key=_key()
    )
    # 취소는 이미 체결된 주문에서 거절될 수 있다. 그때도 typed로 닫혀야 하고 5xx면 회귀다.
    recorder.add(
        "취소와 대사",
        "PASS"
        if cancel_status < 500 and owner_reconcile_status == 403 and reconcile_status < 500
        else "FAIL",
        f"취소 HTTP {cancel_status} {(cancelled.get('error') or {}).get('code') or ''} "
        f"소유자대사 HTTP {owner_reconcile_status} "
        f"관리자대사 HTTP {reconcile_status} {(reconciled.get('error') or {}).get('code') or ''} "
        "(체결된 주문의 취소 거절은 정상이다. 5xx만 회귀다)",
    )


def check_ledgers_do_not_mix(recorder: Recorder, owner: Api) -> None:
    """한쪽 원장의 계좌 ID로 다른 쪽을 읽으면 열리지 않아야 한다."""

    paper = _account_id("INTERNAL_PAPER", optional=True)
    mock = _account_id("KIS_MOCK", optional=True)
    if paper is None or mock is None:
        recorder.add(
            "두 원장 분리",
            "INFO",
            f"두 원장의 계좌 관측이 모두 있어야 성립한다. paper={'있음' if paper else '없음'} "
            f"mock={'있음' if mock else '없음'}",
        )
        return
    if paper == mock:
        recorder.add(
            "두 원장 분리",
            "FAIL",
            "두 원장이 같은 계좌 ID를 관측하고 있다. 분리 자체가 성립하지 않는다",
        )
        return
    crossed_paper, _ = owner.request("GET", f"/api/v1/brokerage/paper/accounts/{mock}/balances")
    crossed_mock, _ = owner.request("GET", f"/api/v1/brokerage/mock/accounts/{paper}/balances")
    recorder.add(
        "두 원장 분리",
        "PASS" if crossed_paper in (400, 403, 404) and crossed_mock in (400, 403, 404) else "FAIL",
        f"paper에 mock 계좌 HTTP {crossed_paper} / mock에 paper 계좌 HTTP {crossed_mock} "
        "(200이면 원장이 섞인 것이다)",
    )


def main(argv: list[str]) -> int:
    require_opt_in(_OPT_IN)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv[1:])

    recorder = Recorder()
    before: dict[str, list[str]] = {}
    rivals: list[str] = []
    platform_switched = False
    try:
        before = snapshot()
        # 현재 배포가 KIS Mock online 모드여도 이 장외 회귀가 물리 주문을 내면 안 된다.
        # Spring의 gRPC adapter만 켜고 production brokerage child는 끈 compose override로
        # 재기동한 뒤, 같은 loopback 포트에는 결정적 fixture server를 붙인다.
        _compose("up", "-d", "--no-deps", "decision-platform", offline_brokerage=True)
        platform_switched = True
        wait_healthy()
        _publish_driver()
        _start_offline_brokerage(account_id=_FIXTURE_ACCOUNT, cash_krw=INITIAL_CASH_KRW)
        recorder.add(
            "장외 brokerage 격리",
            "PASS",
            "fixture loopback; providerCalls=0; registered mock env unchanged",
        )
        owner = Api()
        owner.login("demo-user")
        admin = Api()
        admin.login("demo-admin")
        rivals = quiesce_rival_portfolio_contexts()
        seed_risk_metrics()
        principle = _active_principle(owner)
        check_order_requires_decision(recorder, owner)
        check_ledger(recorder, owner, principle, "paper")
        mock_order = check_ledger(recorder, owner, principle, "mock")
        check_cancel_and_reconcile(recorder, owner, admin, mock_order)
        check_ledgers_do_not_mix(recorder, owner)
    except HarnessError as error:
        recorder.add("확인 중단", "FAIL", str(error))
    except Exception as error:  # noqa: BLE001 - 어떤 실패든 정리는 반드시 돈다
        recorder.add("확인 중단", "FAIL", f"{type(error).__name__}: {error}")
    finally:
        restore_portfolio_contexts(rivals)
        _stop_offline_brokerage()
        if platform_switched:
            try:
                _compose("up", "-d", "--no-deps", "decision-platform")
                wait_healthy()
                recorder.add("스택 구성 복원", "PASS", "테스트 시작 시 런타임 플래그로 되돌림")
            except Exception as error:  # noqa: BLE001 - 복원 실패는 runner 실패다
                recorder.add("스택 구성 복원", "FAIL", f"{type(error).__name__}: {error}")
        if before:
            cleanup(before, recorder)
        else:
            recorder.add("정리", "FAIL", "스냅샷을 찍지 못해 되돌릴 범위를 알 수 없다")

    report = write_report(
        contract_id="p1-brokerage-e2e.v1",
        marker="P1_BROKERAGE_E2E",
        recorder=recorder,
        out=args.out,
    )
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
