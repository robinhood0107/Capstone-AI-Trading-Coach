from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from app.brokerage.kis_mock_online_client import KISBrokerageCallBudget
from app.brokerage.kis_mock_online_runtime import KISMockBalanceSourceProbe
from app.brokerage.kis_mock_owner_certification import (
    OwnerMockCredentialCertifier,
    build_quote_accounting,
)
from app.brokerage.mock_order_reference_store import MockProviderOrderReference
from app.data.kis.accounting import PhysicalChannel
from app.generated import brokerage_pb2

_ACCOUNT = "acct_" + "a" * 32
_CERTIFICATION = "cert_" + "b" * 32
_OPEN_KRX = datetime(2026, 8, 26, 0, 30, tzinfo=UTC)


class FakeQuoteClient:
    def __init__(self, recorder: Any) -> None:
        self.recorder = recorder

    def request(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        self.recorder.record_physical_attempt(PhysicalChannel.MARKET_DATA)
        self.recorder.record_physical_success(PhysicalChannel.MARKET_DATA)
        return {"output": {"stck_llam": "5000"}}


class FakeBalanceReader:
    def __init__(self, budget: KISBrokerageCallBudget, *, buyable_quantity: int = 1) -> None:
        self.budget = budget
        self.buyable_quantity = buyable_quantity

    def probe_balance_source(self, account_id: str) -> KISMockBalanceSourceProbe:
        self.budget.reserve_brokerage()
        return KISMockBalanceSourceProbe(
            account_id=account_id,
            cash_krw=100_000,
            portfolio_equity_krw=100_000,
            positions=(),
            positions_complete=True,
        )

    def buyable(self, account_id: str, symbol: str, price: int, order_division: str) -> Any:
        self.budget.reserve_brokerage()
        return brokerage_pb2.GetMockBuyableResponse(
            account_id=account_id,
            symbol=symbol,
            estimated_price_krw=price,
            buyable_quantity=self.buyable_quantity,
            buyable_amount_krw=100_000,
            source_version="kis-mock-buyable-v1",
        )


class FakeExecutionReader:
    def __init__(self, budget: KISBrokerageCallBudget, *, recovery_open: bool = False) -> None:
        self.budget = budget
        self.recovery_open = recovery_open

    def verify_cancelled_unfilled(self, **_kwargs: object) -> None:
        self.budget.reserve_brokerage()

    def require_no_open_order(self, **_kwargs: object) -> None:
        self.budget.reserve_brokerage()

    def read_optional(self, **_kwargs: object) -> Any:
        self.budget.reserve_brokerage()
        return SimpleNamespace(
            cumulative_quantity=0,
            leaves_quantity=1 if self.recovery_open else 0,
            cancelled=not self.recovery_open,
            rejected=False,
        )


class FakeGateway:
    def __init__(
        self, budget: KISBrokerageCallBudget, reference_store: "FakeReferenceStore"
    ) -> None:
        self.budget = budget
        self.reference_store = reference_store
        self.submit_intents: list[object] = []
        self.cancellations: list[str] = []
        self.account_id = _ACCOUNT

    def submit_cash_order(self, intent: object, *, order_id: str, account_id: str) -> Any:
        self.budget.reserve_brokerage()
        self.submit_intents.append(intent)
        self.order_id = order_id
        self.account_id = account_id
        self.reference_store.current_state = "COMMITTED"
        return SimpleNamespace(accepted=True)

    def cancel_cash_order(self, *, order_id: str, account_id: str) -> Any:
        self.budget.reserve_brokerage()
        self.cancellations.append(order_id)
        assert account_id == self.account_id
        return SimpleNamespace(status="CANCELLED")


class FakeReferenceStore:
    def __init__(self, state: str | None) -> None:
        self.current_state = state

    def state(self, _order_id: str, _account_id: str) -> str | None:
        return self.current_state

    def get(self, _order_id: str, _account_id: str) -> MockProviderOrderReference | None:
        if self.current_state != "COMMITTED":
            return None
        return MockProviderOrderReference(
            provider_order_no="12345",
            provider_org_no="001",
            order_division="00",
            quantity=1,
        )


def _certifier(
    *,
    reference_state: str | None = None,
    recovery: bool = False,
    buyable_quantity: int = 1,
    recovery_open: bool = False,
) -> tuple[OwnerMockCredentialCertifier, FakeGateway, KISBrokerageCallBudget]:
    budget = KISBrokerageCallBudget(token_p_cap=1, brokerage_cap=7)
    accounting = build_quote_accounting()
    reference_store = FakeReferenceStore(reference_state)
    gateway = FakeGateway(budget, reference_store)
    certifier = OwnerMockCredentialCertifier(
        account_id=_ACCOUNT,
        certification_id=_CERTIFICATION,
        session_date="2026-08-26",
        recovery=recovery,
        quote_client=FakeQuoteClient(accounting),  # type: ignore[arg-type]
        quote_accounting=accounting,
        broker_client=SimpleNamespace(),  # type: ignore[arg-type]
        brokerage_budget=budget,
        gateway=gateway,  # type: ignore[arg-type]
        balance_reader=FakeBalanceReader(budget, buyable_quantity=buyable_quantity),  # type: ignore[arg-type]
        execution_reader=FakeExecutionReader(budget, recovery_open=recovery_open),  # type: ignore[arg-type]
        reference_store=reference_store,  # type: ignore[arg-type]
        now=lambda: _OPEN_KRX,
        sleep=lambda _seconds: None,
    )
    return certifier, gateway, budget


def test_owner_certification_only_submits_one_fixed_share_and_requires_full_reconciliation() -> (
    None
):
    certifier, gateway, budget = _certifier()

    result = certifier.run()

    assert result.state == "PASS", result
    assert result.receipt_sha256 and len(result.receipt_sha256) == 64
    assert (result.quote_calls, result.brokerage_calls, result.token_calls) == (1, 7, 0)
    assert len(gateway.submit_intents) == 1
    intent = gateway.submit_intents[0]
    assert intent.symbol == "005930"  # type: ignore[attr-defined]
    assert intent.side == "BUY"  # type: ignore[attr-defined]
    assert intent.quantity == 1  # type: ignore[attr-defined]
    assert intent.estimated_price == 5_000  # type: ignore[attr-defined]
    assert intent.order_division == "00"  # type: ignore[attr-defined]
    assert intent.exchange_division == "KRX"  # type: ignore[attr-defined]
    assert gateway.cancellations == ["ord_mock_" + "b" * 32]
    assert budget.counts["brokerage"] == 7


def test_unfunded_test_order_stops_before_submit() -> None:
    certifier, gateway, budget = _certifier(buyable_quantity=0)

    result = certifier.run()

    assert result.state == "FAILED"
    assert result.failure_code == "BUYABLE_UNAVAILABLE"
    assert gateway.submit_intents == []
    assert budget.counts["brokerage"] == 2


def test_pending_provider_reference_never_submits_a_duplicate_recovery_order() -> None:
    certifier, gateway, budget = _certifier(reference_state="PENDING", recovery=True)

    result = certifier.run()

    assert result.state == "RECOVERY_REQUIRED"
    assert result.failure_code == "TEST_ORDER_UNCERTAIN"
    assert gateway.submit_intents == []
    assert budget.counts["brokerage"] == 0


def test_committed_open_test_order_recovery_cancels_exact_order_once_without_recertifying() -> None:
    certifier, gateway, budget = _certifier(
        reference_state="COMMITTED",
        recovery=True,
        recovery_open=True,
    )

    result = certifier.run()

    assert result.state == "FAILED"
    assert result.failure_code == "TEST_ORDER_RECOVERED"
    assert gateway.submit_intents == []
    assert gateway.cancellations == ["ord_mock_" + "b" * 32]
    assert result.quote_calls == 0
    assert budget.counts["brokerage"] == 6
