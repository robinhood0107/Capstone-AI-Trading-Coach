from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.p1_owner.automation import AutomationRun, OrderReservation, Quote, ReconcileOutcome
from app.p1_owner.automation_runtime import RuntimeClaim
from app.p1_owner.automation_runtime_live import (
    FailClosedVertexVetoTransport,
    LiveAutomationPort,
    SpringAutomationBridgeClient,
)

_KST = ZoneInfo("Asia/Seoul")


class FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def command(
        self,
        operation: str,
        user_id: str,
        payload: dict[str, object],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        del user_id, idempotency_key
        self.calls.append((operation, payload))
        if operation == "EVALUATE":
            return {
                "decisionId": "dec_" + "1" * 32,
                "riskDecision": {"decision": "ALLOW", "canSubmitOrder": True},
            }
        if operation == "BALANCE":
            return {
                "accountId": "acct_" + "2" * 32,
                "cashKrw": 1_000_000,
                "marginRequirementKrw": 0,
                "portfolioEquityKrw": 1_000_000,
                "positions": [],
            }
        if operation == "BUYABLE":
            return {
                "accountId": "acct_" + "2" * 32,
                "buyableQuantity": 1,
                "estimatedPrice": 75_000,
                "symbol": "005930",
            }
        if operation == "SUBMIT":
            return {"orderId": "ord_mock_" + "3" * 32, "status": "ACCEPTED"}
        if operation == "CANCEL":
            return {"orderId": "ord_mock_" + "3" * 32, "status": "CANCELLED"}
        raise AssertionError(operation)

    def close(self) -> None:
        return None


class FakeQuoteSource:
    def __init__(self) -> None:
        self.calls = 0

    def quote(self, symbol: str) -> Quote:
        self.calls += 1
        return Quote(symbol, 75_000, 52_500, 97_500)

    def close(self) -> None:
        return None


class FakeExecutionSource:
    def __init__(self) -> None:
        self.read_calls = 0
        self.close_calls = 0

    def balance(self, account_id: str) -> dict[str, object]:
        return {
            "accountId": account_id,
            "cashKrw": 1_000_000,
            "portfolioEquityKrw": 1_000_000,
            "positions": [],
        }

    def read(self, order_id: str, account_id: str, session_date: date) -> ReconcileOutcome:
        del order_id, account_id, session_date
        self.read_calls += 1
        return "FILLED"

    def require_closed(self, order_id: str, account_id: str, session_date: date) -> bool:
        del order_id, account_id, session_date
        self.close_calls += 1
        return True

    def close(self) -> None:
        return None


def _claim() -> RuntimeClaim:
    return RuntimeClaim(
        user_id="usr_automation_runtime_0001",
        run_id="auto_run_" + "4" * 32,
        control_version=2,
        account_id="acct_" + "2" * 32,
        principle_id="prc_" + "5" * 32,
        strategy_id="strategy_runtime_0001",
        baseline_account_digest="6" * 64,
        replayed=False,
        session_date=date(2026, 8, 28),
        claim_token_hash="sha256:" + "7" * 64,
    )


def _state() -> dict[str, Any]:
    return {
        "accountComplete": True,
        "accountDigestMatches": True,
        "accountId": "acct_" + "2" * 32,
        "baselineAccountDigest": "6" * 64,
        "baselineAccountProjection": {
            "accountId": "acct_" + "2" * 32,
            "cashKrw": 1_000_000,
            "marginRequirementKrw": 0,
            "portfolioEquityKrw": 1_000_000,
            "positions": [],
        },
        "brokerageMode": "KIS_MOCK",
        "checkpointVersion": 4,
        "controlState": "ARMED",
        "controlVersion": 2,
        "dailyShardFreshComplete": True,
        "decisionId": None,
        "killSwitchActive": False,
        "logicalSubmitCount": 0,
        "manualPositionSymbols": [],
        "noOpenOrder": True,
        "positions": [],
        "principleActiveCurrent": True,
        "principleId": "prc_" + "5" * 32,
        "providerCallCount": 0,
        "releaseActive": True,
        "reservation": None,
        "runId": "auto_run_" + "4" * 32,
        "runStartedAt": "2026-08-28T09:10:00+09:00",
        "selectedSide": "BUY",
        "selectedSymbol": "005930",
        "sessionDate": "2026-08-28",
        "signals": [],
        "state": "RISK_CHECKING",
        "strategyId": "strategy_runtime_0001",
        "unfinishedPreviousOrder": False,
        "vertexCallCount": 0,
    }


def test_live_port_reuses_one_quote_spring_risk_brokerage_and_execution_reader() -> None:
    bridge = FakeBridge()
    quote_source = FakeQuoteSource()
    execution = FakeExecutionSource()
    state = _state()
    run = AutomationRun(
        run_id=str(state["runId"]),
        session_date=date(2026, 8, 28),
        brokerage_mode="KIS_MOCK",
        started_at=datetime(2026, 8, 28, 9, 10, tzinfo=_KST),
        updated_at=datetime(2026, 8, 28, 9, 10, tzinfo=_KST),
        state="RISK_CHECKING",
        selected_symbol="005930",
        selected_side="BUY",
    )
    port = LiveAutomationPort(
        _claim(),
        state,
        bridge,
        quote_source,
        execution,
        FailClosedVertexVetoTransport(),
    )

    risk_inputs = port.inputs(state=state, run=run, now=run.started_at)
    assert risk_inputs.risk_allow is True
    assert port.decision_id == "dec_" + "1" * 32

    run.state = "ORDER_SUBMITTING"
    order_inputs = port.inputs(state=state, run=run, now=run.started_at)
    assert order_inputs.buyable_quantity == 1
    reservation = OrderReservation("005930", "BUY", 1, 75_100)
    assert port.submit(reservation) == "UNFILLED"
    assert port.reconcile(reservation) == "FILLED"
    assert quote_source.calls == 1
    assert [operation for operation, _ in bridge.calls] == [
        "EVALUATE",
        "BUYABLE",
        "SUBMIT",
    ]
    assert port.physical_calls == 5
    assert port.physical_submit_calls == 1


def test_unconfigured_vertex_transport_abstains_before_provider_call() -> None:
    port = LiveAutomationPort(
        _claim(),
        _state(),
        FakeBridge(),
        FakeQuoteSource(),
        FakeExecutionSource(),
        FailClosedVertexVetoTransport(),
    )

    assert port.vertex("005930") == "ABSTAIN"
    assert port.vertex_calls == 1
    assert port.physical_calls == 1  # current-price quote only


def test_spring_bridge_client_is_fixed_loopback_secret_bound_and_retry_zero() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, json={"status": "OK", "data": {"accountId": "acct_test"}})

    client = SpringAutomationBridgeClient(
        "automation-runtime-bridge-test-secret-0001",
        transport=httpx.MockTransport(handler),
    )
    result = client.command(
        "BALANCE",
        "usr_automation_runtime_0001",
        {"accountId": "acct_test"},
    )
    client.close()

    assert result == {"accountId": "acct_test"}
    assert len(observed) == 1
    assert observed[0].url == httpx.URL("http://127.0.0.1:8080/internal/automation-runtime/command")
    assert observed[0].headers["x-automation-runtime-auth"] == (
        "automation-runtime-bridge-test-secret-0001"
    )
    payload = json.loads(observed[0].content)
    assert payload["operation"] == "BALANCE"
