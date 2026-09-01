from __future__ import annotations

import json

import pytest
from datetime import date, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import httpx

from app.p1_owner.automation import (
    AutomationRun,
    ExactOrderIntent,
    OrderReservation,
    Quote,
    ReconcileOutcome,
    SignalCandidate,
)
from app.p1_owner.automation_runtime import AutomationRuntimeError, RuntimeClaim
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
                "buyableAmountKrw": 751_000,
                "buyableQuantity": 10,
                "estimatedPrice": 75_100,
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
            "marginRequirementKrw": 0,
            "portfolioEquityKrw": 1_000_000,
            "positions": [],
            "positionsComplete": True,
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
        "instrumentCatalogSymbols": ["000660", "005930"],
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
        "policy": {
            "capitalLimitKrw": 10_000_000,
            "maxOpenPositions": 5,
            "policyId": "auto_pol_" + "f" * 32,
            "preset": "BALANCED",
            "stopLossBps": 500,
            "takeProfitBps": 1_000,
            "version": 1,
        },
        "principleActiveCurrent": True,
        "principleId": "prc_" + "5" * 32,
        "providerCallCount": 0,
        "releaseActive": True,
        "reservation": None,
        "runId": "auto_run_" + "4" * 32,
        "runStartedAt": "2026-08-28T09:30:00+09:00",
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
        started_at=datetime(2026, 8, 28, 9, 30, tzinfo=_KST),
        updated_at=datetime(2026, 8, 28, 9, 30, tzinfo=_KST),
        state="ORDER_SIZING",
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

    order_inputs = port.inputs(state=state, run=run, now=run.started_at)
    assert (order_inputs.buyable_quantity, order_inputs.buyable_amount_krw) == (10, 751_000)
    intent = ExactOrderIntent(
        "005930", "BUY", "LIMIT", 2, 75_100, 150_200, "1d", _claim().strategy_id
    )
    reservation = OrderReservation("005930", "BUY", 2, 75_100, intent=intent)
    run.reservation = reservation
    run.state = "RISK_CHECKING"
    risk_inputs = port.inputs(state=state, run=run, now=run.started_at)
    assert risk_inputs.risk_allow is True
    assert port.decision_id == "dec_" + "1" * 32
    assert port.submit(reservation) == "UNFILLED"
    assert port.reconcile(reservation) == "FILLED"
    assert quote_source.calls == 1
    assert [operation for operation, _ in bridge.calls] == [
        "BUYABLE",
        "EVALUATE",
        "SUBMIT",
    ]
    evaluate_intent = cast(dict[str, object], bridge.calls[1][1]["orderIntent"])
    submit_intent = cast(dict[str, object], bridge.calls[2][1]["orderIntent"])
    assert evaluate_intent == submit_intent == intent.projection()
    assert port.physical_calls == 5
    assert port.physical_submit_calls == 1


def test_kis_runtime_sizing_fails_closed_when_risk_balance_is_incomplete() -> None:
    class IncompleteExecution(FakeExecutionSource):
        def balance(self, account_id: str) -> dict[str, object]:
            # 카탈로그가 모르는 종목을 보유하면 분류가 확인되지 않은 것이므로 risk-complete가 아니다.
            value = super().balance(account_id)
            value["positions"] = [{"marketValueKrw": 100_000, "quantity": 1, "symbol": "999999"}]
            return value

    bridge = FakeBridge()
    state = _state()
    run = AutomationRun(
        run_id=str(state["runId"]),
        session_date=date(2026, 8, 28),
        brokerage_mode="KIS_MOCK",
        started_at=datetime(2026, 8, 28, 9, 30, tzinfo=_KST),
        updated_at=datetime(2026, 8, 28, 9, 30, tzinfo=_KST),
        state="ORDER_SIZING",
        selected_symbol="005930",
        selected_side="BUY",
    )
    port = LiveAutomationPort(
        _claim(),
        state,
        bridge,
        FakeQuoteSource(),
        IncompleteExecution(),
        FailClosedVertexVetoTransport(),
    )

    inputs = port.inputs(state=state, run=run, now=run.started_at)

    assert inputs.account_complete is False
    assert bridge.calls == []
    assert port.physical_calls == 2  # one current-price quote and one complete-page balance read


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


def test_invalid_ai_usage_counts_never_move_the_physical_ledger_backwards() -> None:
    class InvalidUsageBridge(FakeBridge):
        def command(
            self,
            operation: str,
            user_id: str,
            payload: dict[str, object],
            *,
            idempotency_key: str | None = None,
        ) -> dict[str, Any]:
            del user_id, payload, idempotency_key
            if operation == "NEWS_SCREEN":
                return {"providerCallCount": -1, "groundingQueryCount": 0, "screenings": []}
            if operation == "JUDGE":
                return {
                    "providerCallCount": 3,
                    "summary": "invalid usage",
                    "candidates": [],
                }
            raise AssertionError(operation)

    port = LiveAutomationPort(
        _claim(),
        _state(),
        InvalidUsageBridge(),
        FakeQuoteSource(),
        FakeExecutionSource(),
        FailClosedVertexVetoTransport(),
    )
    candidate = SignalCandidate("005930", "BUY", "BUY", 0.03)
    quote = port.quote(candidate.symbol)
    before = port.physical_calls

    with pytest.raises(AutomationRuntimeError, match="SCREENING_USAGE_INVALID"):
        port.screen((candidate,), {candidate.symbol: quote}, "a" * 64)
    assert port.physical_calls == before
    assert port.judge((candidate,), "a" * 64) is None
    assert port.physical_calls == before


def test_spring_bridge_client_is_fixed_loopback_secret_bound_and_retry_zero() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        if request.url.path == "/api/v1/auth/login":
            return httpx.Response(200, json={"data": {"accessToken": "owner-access-token"}})
        return httpx.Response(200, json={"status": "OK", "data": {"accountId": "acct_test"}})

    client = SpringAutomationBridgeClient(
        "automation-runtime-bridge-test-secret-0001",
        transport=httpx.MockTransport(handler),
        owner_username="demo-user",
        owner_password="owner-password-0001",
    )
    result = client.command(
        "BALANCE",
        "usr_automation_runtime_0001",
        {"accountId": "acct_test"},
    )
    client.close()

    assert result == {"accountId": "acct_test"}
    # 소유자 세션을 먼저 열고 그 토큰으로 명령을 보낸다. shared secret만으로는 bridge 뒤의
    # actor capability가 발급되지 않는다.
    assert len(observed) == 2
    assert observed[0].url == httpx.URL("http://127.0.0.1:8080/api/v1/auth/login")
    assert observed[1].url == httpx.URL("http://127.0.0.1:8080/internal/automation-runtime/command")
    assert observed[1].headers["x-automation-runtime-auth"] == (
        "automation-runtime-bridge-test-secret-0001"
    )
    assert observed[1].headers["authorization"] == "Bearer owner-access-token"
    payload = json.loads(observed[1].content)
    assert payload["operation"] == "BALANCE"


def test_spring_bridge_client_refuses_to_command_without_an_owner_session() -> None:
    """자격이 없으면 명령을 아예 시작하지 않는다. bridge를 인증 없이 두드리지 않는다."""

    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, json={"status": "OK", "data": {}})

    client = SpringAutomationBridgeClient(
        "automation-runtime-bridge-test-secret-0001",
        transport=httpx.MockTransport(handler),
        owner_username="",
        owner_password="",
    )
    with pytest.raises(AutomationRuntimeError, match="AUTOMATION_BRIDGE_OWNER_CREDENTIAL_MISSING"):
        client.command("BALANCE", "usr_automation_runtime_0001", {"accountId": "acct_test"})
    client.close()

    assert observed == []


def test_spring_bridge_client_reopens_the_owner_session_once_on_expiry() -> None:
    """만료된 세션은 한 번만 다시 연다. 같은 idempotency key로 재시도하므로 중복 주문이 없다."""

    observed: list[httpx.Request] = []
    commands = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal commands
        observed.append(request)
        if request.url.path == "/api/v1/auth/login":
            return httpx.Response(200, json={"data": {"accessToken": f"token-{len(observed)}"}})
        commands += 1
        if commands == 1:
            return httpx.Response(401, json={"error": {"code": "UNAUTHORIZED"}})
        return httpx.Response(200, json={"status": "OK", "data": {"accountId": "acct_test"}})

    client = SpringAutomationBridgeClient(
        "automation-runtime-bridge-test-secret-0001",
        transport=httpx.MockTransport(handler),
        owner_username="demo-user",
        owner_password="owner-password-0001",
    )
    result = client.command("BALANCE", "usr_automation_runtime_0001", {"accountId": "acct_test"})
    client.close()

    assert result == {"accountId": "acct_test"}
    # login, command(401), login, command(200) 정확히 넷이다. 그 이상 재시도하지 않는다.
    assert [request.url.path for request in observed] == [
        "/api/v1/auth/login",
        "/internal/automation-runtime/command",
        "/api/v1/auth/login",
        "/internal/automation-runtime/command",
    ]


class FilledExecutionSource(FakeExecutionSource):
    """매수 체결 뒤의 잔고를 돌려준다. 현금이 줄고 그 종목이 생긴다."""

    def __init__(self, *, cash_krw: int, positions: list[dict[str, object]]) -> None:
        super().__init__()
        self._cash_krw = cash_krw
        self._positions = positions

    def balance(self, account_id: str) -> dict[str, object]:
        return {
            "accountId": account_id,
            "cashKrw": self._cash_krw,
            "marginRequirementKrw": 0,
            "portfolioEquityKrw": 1_000_000,
            "positions": self._positions,
            "positionsComplete": True,
        }


def _lineage_port(execution: FakeExecutionSource) -> LiveAutomationPort:
    state = _state()
    state["reservation"] = {"orderId": "ord_mock_" + "3" * 32}
    return LiveAutomationPort(
        _claim(),
        state,
        FakeBridge(),
        FakeQuoteSource(),
        execution,
        FailClosedVertexVetoTransport(),
    )


def test_confirmed_buy_fill_advances_the_expected_account_projection() -> None:
    # 75,000 한 주를 샀으니 현금은 그만큼(수수료 여유 포함) 줄고 포지션이 하나 생긴다.
    execution = FilledExecutionSource(
        cash_krw=1_000_000 - 75_030,
        positions=[{"quantity": 1, "symbol": "005930"}],
    )
    advance = _lineage_port(execution).account_lineage_advance(
        symbol="005930", side="BUY", filled_quantity=1, average_fill_price_krw=75_000
    )

    assert advance is not None
    assert advance.reason == "BUY_FILL"
    assert advance.order_id == "ord_mock_" + "3" * 32
    assert advance.projection["schemaVersion"] == "2"
    assert advance.projection["positions"] == [{"quantity": 1, "symbol": "005930"}]
    assert len(advance.digest) == 64


def test_account_movement_the_bot_cannot_explain_never_advances_the_expectation() -> None:
    # 외부에서 다른 종목이 들어왔다면 자기 체결로 설명되지 않는다. 전진시키지 않고
    # 다음 tick이 드리프트로 HALT하게 둔다.
    execution = FilledExecutionSource(
        cash_krw=1_000_000 - 75_030,
        positions=[{"quantity": 1, "symbol": "005930"}, {"quantity": 9, "symbol": "000660"}],
    )
    assert (
        _lineage_port(execution).account_lineage_advance(
            symbol="005930", side="BUY", filled_quantity=1, average_fill_price_krw=75_000
        )
        is None
    )


def test_lineage_advance_needs_a_bound_order_id() -> None:
    execution = FilledExecutionSource(
        cash_krw=1_000_000 - 75_030, positions=[{"quantity": 1, "symbol": "005930"}]
    )
    port = LiveAutomationPort(
        _claim(),
        _state(),
        FakeBridge(),
        FakeQuoteSource(),
        execution,
        FailClosedVertexVetoTransport(),
    )
    assert (
        port.account_lineage_advance(
            symbol="005930", side="BUY", filled_quantity=1, average_fill_price_krw=75_000
        )
        is None
    )


def test_the_three_gates_that_used_to_close_every_order_are_open() -> None:
    """오늘 연 세 곳이 실제로 열려 production 경로가 주문 산정까지 간다.

    이전에는 (1) 뉴스 거부권이 항상 ABSTAIN이라 매수가 NEWS_CHECKING에서 끝났고,
    (2) riskComplete가 False로 고정돼 매수·매도 양쪽이 ORDER_SIZING에서 끝났으며,
    (3) 원칙 한도가 MAX_BIGINT로 들어와 수량이 사용자 원칙을 넘었다.
    """
    state = _state()
    # V95가 실제 값을 내보낸다. 원칙은 1회 최대주문 30만원, 보유 평가액은 20만원이다.
    state["principleMaxSingleOrderKrw"] = 300_000
    state["principleAssetRemainingKrw"] = 500_000
    # 보유 평가액은 durable state가 아니라 live 잔고가 진실이다. 아래 잔고가 그걸 준다.
    run = AutomationRun(
        run_id=str(state["runId"]),
        session_date=date(2026, 8, 28),
        brokerage_mode="KIS_MOCK",
        started_at=datetime(2026, 8, 28, 9, 30, tzinfo=_KST),
        updated_at=datetime(2026, 8, 28, 9, 30, tzinfo=_KST),
        state="ORDER_SIZING",
        selected_symbol="005930",
        selected_side="BUY",
    )
    port = LiveAutomationPort(
        _claim(),
        state,
        FakeBridge(),
        FakeQuoteSource(),
        FilledExecutionSource(
            cash_krw=1_000_000,
            positions=[{"marketValueKrw": 200_000, "quantity": 2, "symbol": "005930"}],
        ),
        FailClosedVertexVetoTransport(),
    )

    inputs = port.inputs(state=state, run=run, now=run.started_at)

    # (2) 증거금 0과 카탈로그 전수 분류가 확인되므로 주문 산정이 열린다.
    assert inputs.account_complete is True
    # (3) 원칙 한도가 그대로 사이저에 도달한다.
    assert inputs.principle_max_single_order_krw == 300_000
    assert inputs.principle_asset_remaining_krw == 500_000
    assert inputs.open_position_market_value_krw == 200_000
    # (1) provider가 없으니 ABSTAIN을 차단으로 보지 않는다.
    assert inputs.news_veto_provider_bound is False
    assert port.vertex("005930") == "ABSTAIN"


def test_a_bound_provider_makes_abstain_block_again() -> None:
    state = _state()
    run = AutomationRun(
        run_id=str(state["runId"]),
        session_date=date(2026, 8, 28),
        brokerage_mode="KIS_MOCK",
        started_at=datetime(2026, 8, 28, 9, 30, tzinfo=_KST),
        updated_at=datetime(2026, 8, 28, 9, 30, tzinfo=_KST),
        state="ORDER_SIZING",
        selected_symbol="005930",
        selected_side="BUY",
    )

    class BoundTransport:
        physical_calls = 0

        def invoke(self, *, system_prompt: str, request_bytes: bytes) -> object:
            raise AssertionError("이 테스트는 배선 여부만 본다")

    port = LiveAutomationPort(
        _claim(),
        state,
        FakeBridge(),
        FakeQuoteSource(),
        FakeExecutionSource(),
        cast(Any, BoundTransport()),
    )

    inputs = port.inputs(state=state, run=run, now=run.started_at)

    assert inputs.news_veto_provider_bound is True
