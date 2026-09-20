from __future__ import annotations

import pathlib

from datetime import date
from typing import Any

from app.p1_owner.automation import Quote, ReconcileSnapshot
from app.p1_owner.automation_portfolio_runtime import PortfolioContinuationRunner
from app.p1_owner.automation_runtime import RuntimeClaim


class Repository:
    def __init__(self) -> None:
        self.execution: dict[str, object] | None = None
        self.staged_strategy = ""

    def load_sources(self, *, claim):
        return {
            "automationPolicyId": "auto_pol_" + "d" * 32,
            "automationPolicyVersion": 1,
            "balanceObservationId": "pbo_" + "b" * 32,
            "botPositionMarketValueKrw": 0,
            "capitalPolicyVersion": 1,
            "configuredCapitalKrw": 1_000_000,
            "maxOpenPositions": 5,
            "maxOrdersPerSession": 3,
            "ordersAlreadySubmitted": 1,
            "positions": [],
            "principleVersion": 1,
            "principleVersionId": "pvr_" + "c" * 32,
            "realizedPnlSinceTransitionKrw": 0,
            "reinvestRealizedPnl": True,
            "reservedBuyCashKrw": 0,
        }

    def current_execution(self, *, claim):
        return self.execution

    def record_buyable(self, *, claim, projection):
        return "auto_buyable_" + "a" * 32

    def stage(self, *, claim, binding, plan):
        order = plan.orders[0]
        intent = order.intent(claim.strategy_id)
        self.staged_strategy = str(intent["strategyId"])
        self.execution = {
            "exactIntent": intent,
            "idempotencyKeyHash": "sha256:" + "1" * 64,
            "ordinal": 1,
            "quantity": order.quantity,
            "state": "PLANNED",
        }
        return "INSERTED"

    def begin(self, **kwargs):
        assert self.execution is not None
        self.execution["state"] = "SUBMITTING"
        return True

    def finish(self, *, state, order_id=None, provider_order_ref_hash=None, **kwargs):
        assert self.execution is not None
        if state == "PENDING_RECONCILIATION":
            self.execution.update(
                state=state,
                orderId=order_id,
                providerOrderRefHash=provider_order_ref_hash,
            )
        else:
            self.execution = None
        return True


class Port:
    def portfolio_quote(self, symbol):
        return Quote(symbol, 50_000, 30_000, 70_000)

    def portfolio_buyable(self, symbol, estimated_price):
        return {
            "accountId": "acct_" + "b" * 32,
            "brokerageMode": "KIS_MOCK",
            "buyableAmountKrw": 1_000_000,
            "buyableQuantity": 20,
            "cashKrw": 1_000_000,
            "estimatedPrice": estimated_price,
            "observedAt": "2026-09-09T00:31:00Z",
            "sourceVersion": "kis-mock-buyable-v1",
            "symbol": symbol,
        }

    def portfolio_evaluate(self, intent, ordinal):
        return "dec_" + "d" * 32

    def portfolio_submit(self, intent, *, decision_id, ordinal):
        return {
            "orderId": "ord_mock_" + "e" * 32,
            "providerOrderRefHash": "f" * 64,
        }

    def portfolio_reconcile(self, order_id):
        return ReconcileSnapshot(True, 3, 0, 50_010, provider_exec_ref_hash="a" * 64)


def test_claim_strategy_flows_through_stage_risk_submit_and_fill() -> None:
    repository = Repository()
    claim = RuntimeClaim(
        "usr_demo_user",
        "auto_run_" + "a" * 32,
        14,
        "acct_" + "b" * 32,
        "prc_" + "c" * 32,
        "strategy_rule_lstm_v1",
        "d" * 64,
        False,
        date(2026, 9, 9),
        "sha256:" + "e" * 64,
    )
    state: dict[str, Any] = {
        "signals": [
            {
                "baselineSignal": "BUY",
                "expectedReturn": 0.05,
                "forecastClose": 55_000,
                "lstmSignal": "BUY",
                "symbol": "005930",
            }
        ]
    }
    result = PortfolioContinuationRunner(repository).continue_session(
        claim=claim,
        state=state,
        port=Port(),
    )
    assert result.status == "COMPLETE"
    assert result.completed_orders == result.planned_orders == 1
    assert repository.staged_strategy == claim.strategy_id


def test_the_portfolio_path_shares_one_round_trip_cost_constant() -> None:
    """비용 상수가 두 경로에서 갈라지면 같은 후보를 한쪽만 사는 상태가 된다.

    예전에는 포트폴리오 계획기만 0.0035 를 글자로 박고 있었다.
    """

    from app.p1_owner import automation, automation_portfolio_runtime

    source = pathlib.Path(automation_portfolio_runtime.__file__).read_text(encoding="utf-8")
    assert "_ROUND_TRIP_COST_BPS / 10_000" in source
    assert "- 1 - 0.0035" not in source
    assert automation_portfolio_runtime._ROUND_TRIP_COST_BPS is automation._ROUND_TRIP_COST_BPS


def test_a_candidate_dropped_for_no_remaining_return_leaves_a_funnel_row() -> None:
    """후보가 비용 문턱에서 떨어지면 그 사유가 화면까지 가야 한다.

    2026-09-15 에 두 결정 시점이 모두 무주문으로 끝났는데, 이 경로는 맨 `continue` 라
    사유가 로그에만 있었다. 단일 주문 엔진 경로에는 같은 기록이 이미 붙어 있다.
    """

    from app.p1_owner.automation_portfolio_runtime import PortfolioContinuationRunner

    recorded: list[tuple[str, str]] = []

    class _Repository:
        def record_stage_outcomes(self, claim: object, symbol: str, reason: str) -> int:
            del claim
            recorded.append((symbol, reason))
            return 1

    runner = PortfolioContinuationRunner(_Repository())
    runner._record_candidate_drop(object(), "066570", "NO_REMAINING_RETURN")

    assert recorded == [("066570", "NO_REMAINING_RETURN")]


def test_recording_a_candidate_drop_never_breaks_the_session() -> None:
    """진단 기록 실패가 거래를 멈추면 안 된다."""

    from app.p1_owner.automation_portfolio_runtime import PortfolioContinuationRunner

    class _Repository:
        def record_stage_outcomes(self, claim: object, symbol: str, reason: str) -> int:
            raise RuntimeError("stage outcome store unavailable")

    runner = PortfolioContinuationRunner(_Repository())
    runner._record_candidate_drop(object(), "066570", "NO_REMAINING_RETURN")


def _snapshot(cumulative: int, leaves: int, **kwargs: object):
    """부분체결 영수증. 평균가는 체결이 있을 때만 있어야 한다(ReconcileSnapshot 불변식)."""

    from app.p1_owner.automation import ReconcileSnapshot as _Snapshot

    price = 50_010 if cumulative > 0 else None
    return _Snapshot(True, cumulative, leaves, price, provider_exec_ref_hash="a" * 64, **kwargs)


def test_a_partial_fill_books_the_filled_shares_and_stops_the_tick() -> None:
    """부분체결이 세션을 중단시키면 이미 체결된 주식이 장부에 들어가지 않는다.

    예전에는 `_terminal_state` 가 예외를 던져 `continue_session` 전체가 죽었다. 이제는
    체결된 만큼을 적재하고 tick 을 끝낸다. **다시 읽지 않는 것이 spin 방지의 핵심이다.**
    """

    from app.p1_owner.automation_portfolio_runtime import _receipt_state

    receipt = _receipt_state(_snapshot(3, 7), 10)

    assert receipt == "PENDING_RECONCILIATION"


def test_a_live_order_with_nothing_filled_is_not_an_error() -> None:
    """아직 아무것도 붙지 않은 살아 있는 주문도 예전에는 예외였다 - 조용한 두 번째 버그."""

    from app.p1_owner.automation_portfolio_runtime import _receipt_state

    assert _receipt_state(_snapshot(0, 10), 10) == "PENDING_RECONCILIATION"


def test_a_full_fill_and_terminal_outcomes_are_unchanged() -> None:
    """기존 동작이 바뀌지 않는다."""

    from app.p1_owner.automation_portfolio_runtime import _receipt_state

    assert _receipt_state(_snapshot(10, 0), 10) == "FILLED"
    assert _receipt_state(_snapshot(0, 0, cancelled=True), 10) == "CANCELLED"
    assert _receipt_state(_snapshot(0, 0, rejected=True), 10) == "REJECTED"


def test_a_receipt_with_lost_shares_is_still_rejected() -> None:
    """잔량 0 인데 terminal 표식도 없고 수량도 안 맞는 영수증은 계속 거절한다.

    사유 코드는 유지하고 적용 범위만 좁아진다.
    """

    import pytest as _pytest

    from app.p1_owner.automation_portfolio_runtime import _receipt_state

    with _pytest.raises(ValueError, match="AUTOMATION_PORTFOLIO_TERMINAL_RECEIPT_INVALID"):
        _receipt_state(_snapshot(3, 0), 10)


def test_the_partial_fill_branch_returns_without_rereading_the_execution() -> None:
    """실행 하나당 tick 하나당 finish 한 번. 다시 읽으면 while 루프가 돈다.

    `PortfolioExecutionJournal.current()` 는 비terminal 실행을 계속 돌려주므로, 루프 안에서
    재조회하면 같은 행을 영원히 본다. 그래서 반환 직후에 나가야 한다.
    """

    import pathlib as _pathlib

    from app.p1_owner import automation_portfolio_runtime

    source = _pathlib.Path(automation_portfolio_runtime.__file__).read_text(encoding="utf-8")
    start = source.index("receipt = _receipt_state(snapshot,")
    tail = source[start : source.index('return PortfolioContinuationResult("COMPLETE"', start)]

    assert 'if receipt == "PENDING_RECONCILIATION":' in tail
    guard = tail.index('if receipt == "PENDING_RECONCILIATION":')
    increment = tail.index("completed += 1")
    assert guard < increment, "부분체결이 completed 를 올리면 안 된다"
    assert tail.index("return PortfolioContinuationResult(", guard) < increment


def test_the_order_book_ledger_never_blocks_a_trade() -> None:
    """원장은 진단이다. 조회가 실패하거나 없어도 주문은 그대로 나가야 한다.

    체결 품질을 바꾸려면 근거가 있어야 하는데, 그 근거를 모으다가 거래를 막으면 본말전도다.
    """

    from app.p1_owner.automation_portfolio_runtime import PortfolioContinuationRunner

    recorded: list[object] = []

    class _Repository:
        def record_book(self, *, claim, ordinal, book):
            recorded.append((ordinal, book))
            return 1

    class _ExplodingPort:
        def portfolio_order_book(self, symbol):
            raise RuntimeError("order book unavailable")

    class _SilentPort:
        def portfolio_order_book(self, symbol):
            return None

    class _WorkingPort:
        def portfolio_order_book(self, symbol):
            return {"bestAskKrw": 50_100, "bestBidKrw": 50_000, "trId": "FHKST01010200"}

    runner = PortfolioContinuationRunner(_Repository())

    # 조회가 터져도, None 을 돌려줘도 예외가 새지 않는다.
    runner._record_order_book(object(), 1, _ExplodingPort(), "066570")
    runner._record_order_book(object(), 1, _SilentPort(), "066570")
    assert recorded == []

    # 포트에 그 메서드가 아예 없어도 조용히 건너뛴다.
    runner._record_order_book(object(), 1, object(), "066570")
    assert recorded == []

    runner._record_order_book(object(), 2, _WorkingPort(), "066570")
    assert len(recorded) == 1
    assert recorded[0][0] == 2


def test_recording_the_book_failure_does_not_stop_the_session() -> None:
    """저장소 쪽이 터져도 마찬가지다."""

    from app.p1_owner.automation_portfolio_runtime import PortfolioContinuationRunner

    class _Repository:
        def record_book(self, *, claim, ordinal, book):
            raise RuntimeError("ledger unavailable")

    class _Port:
        def portfolio_order_book(self, symbol):
            return {"bestAskKrw": 50_100, "bestBidKrw": 50_000, "trId": "FHKST01010200"}

    runner = PortfolioContinuationRunner(_Repository())
    try:
        runner._record_order_book(object(), 1, _Port(), "066570")
    except Exception as error:  # pragma: no cover - 실패하면 이 테스트의 의미가 사라진다
        raise AssertionError(f"ledger failure escaped: {error!r}") from error


def test_the_asking_price_endpoint_is_approved_and_mock_supported() -> None:
    """호가 TR 은 모의·실전 동일하고 승인 목록에 있어야 부를 수 있다.

    이 레포는 KIS XLSX API 목록을 모의 지원 경계의 단일 진실 소스로 삼는다.
    """

    from app.data.kis.http_client import ASKING_PRICE_PATH, _APPROVED_ENDPOINTS
    from app.data.kis.settings import KISSettings

    assert (ASKING_PRICE_PATH, "FHKST01010200") in _APPROVED_ENDPOINTS
    assert ASKING_PRICE_PATH.endswith("/inquire-asking-price-exp-ccn")
    assert KISSettings(kis_mode="mock").asking_price_tr_id == "FHKST01010200"
    assert KISSettings(kis_mode="live").asking_price_tr_id == "FHKST01010200"
