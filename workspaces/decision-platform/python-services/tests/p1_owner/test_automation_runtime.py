from __future__ import annotations

from datetime import date, datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

from app.p1_owner.automation import AutomationInputs, FixtureAutomationTransport, Quote
from app.p1_owner.automation_runtime import (
    AdvanceCommand,
    AutomationRuntimeError,
    PersistentAutomationRunner,
    PostgresAutomationRuntimeRepository,
    RuntimeClaim,
    XkrxBoundaryPlanner,
    inputs_from_state,
)

_KST = ZoneInfo("Asia/Seoul")


def _claim() -> RuntimeClaim:
    return RuntimeClaim(
        user_id="usr_automation_runtime_0001",
        run_id="auto_run_" + "a" * 32,
        control_version=2,
        account_id="acct_" + "b" * 32,
        principle_id="prc_automation_runtime_0001",
        strategy_id="strategy_automation_runtime_0001",
        baseline_account_digest="c" * 64,
        replayed=False,
        session_date=date(2026, 8, 28),
        claim_token_hash="sha256:" + "d" * 64,
    )


def _state(state: str = "SCHEDULED", version: int = 1) -> dict[str, Any]:
    return {
        "accountComplete": True,
        "accountDigestMatches": True,
        "accountId": "acct_" + "b" * 32,
        "baselineAccountDigest": "c" * 64,
        "brokerageMode": "KIS_MOCK",
        "checkpointVersion": version,
        "controlState": "ARMED",
        "controlVersion": 2,
        "dailyShardFreshComplete": True,
        "killSwitchActive": False,
        "manualPositionSymbols": [],
        "noOpenOrder": True,
        "positions": [],
        "principleId": "prc_automation_runtime_0001",
        "principleActiveCurrent": True,
        "providerCallCount": 0,
        "logicalSubmitCount": 0,
        "releaseActive": True,
        "reservation": None,
        "runId": "auto_run_" + "a" * 32,
        "runStartedAt": "2026-08-28T09:10:00+09:00",
        "selectedSide": None,
        "selectedSymbol": None,
        "sessionDate": "2026-08-28",
        "signals": [
            {
                "symbol": "005930",
                "lstmSignal": "BUY",
                "baselineSignal": "BUY",
                "expectedReturn": 0.03,
                "confidence": 0.8,
            }
        ],
        "state": state,
        "strategyId": "strategy_automation_runtime_0001",
        "unfinishedPreviousOrder": False,
        "vertexCallCount": 0,
    }


class FakeRepository:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.commands: list[AdvanceCommand] = []

    def read_state(self, claim: RuntimeClaim) -> dict[str, Any]:
        assert claim.run_id == self.state["runId"]
        return dict(self.state)

    def advance(self, command: AdvanceCommand) -> tuple[int, bool]:
        self.commands.append(command)
        self.state["state"] = command.next_state
        self.state["checkpointVersion"] = command.expected_version + 1
        self.state["selectedSymbol"] = command.selected_symbol
        self.state["selectedSide"] = command.selected_side
        self.state["vertexCallCount"] = command.vertex_call_count
        self.state["providerCallCount"] = command.provider_call_count
        self.state["logicalSubmitCount"] = command.logical_submit_count
        return command.expected_version + 1, False


class FakeRuntimePort(FixtureAutomationTransport):
    order_id: str | None = None
    provider_order_ref_hash: str | None = None
    decision_id: str | None = None

    def inputs(
        self,
        *,
        state: dict[str, Any],
        run: object,
        now: datetime,
    ) -> AutomationInputs:
        del run, now
        return inputs_from_state(state, risk_allow=True, buyable_quantity=1)

    def close(self) -> None:
        return None


def test_persistent_runner_reloads_state_and_cas_persists_each_boundary() -> None:
    repository = FakeRepository(_state())
    port = FakeRuntimePort(quotes={"005930": Quote("005930", 75_000, 52_500, 97_500)})
    first = PersistentAutomationRunner(cast(Any, repository)).run_tick(
        claim=_claim(),
        tick_id="boundary-001",
        now=datetime(2026, 8, 28, 9, 10, tzinfo=_KST),
        port=port,
    )
    second = PersistentAutomationRunner(cast(Any, repository)).run_tick(
        claim=_claim(),
        tick_id="boundary-002",
        now=datetime(2026, 8, 28, 9, 10, 1, tzinfo=_KST),
        port=port,
    )

    assert first["state"] == "PRECHECK"
    assert second["state"] == "BUY_CANDIDATE_SELECTED"
    assert [item.expected_version for item in repository.commands] == [1, 2]
    assert all(item.tick_identity_hash.startswith("sha256:") for item in repository.commands)
    assert all(item.result_hash.startswith("sha256:") for item in repository.commands)
    assert port.physical_calls == port.physical_submit_calls == 0


def test_inputs_from_state_preserve_rule_lstm_and_fail_closed_flags() -> None:
    state = _state()
    state["killSwitchActive"] = True
    state["accountDigestMatches"] = False
    inputs = inputs_from_state(state, risk_allow=False, buyable_quantity=0)

    assert inputs.signals[0].lstm_signal == inputs.signals[0].baseline_signal == "BUY"
    assert inputs.kill_switch_active is True
    assert inputs.account_digest_matches is False
    assert inputs.risk_allow is False
    assert inputs.buyable_quantity == 0


def test_xkrx_boundary_skips_substitute_holiday_and_uses_exact_times() -> None:
    planner = XkrxBoundaryPlanner()

    after_close = datetime(2026, 8, 14, 15, 21, tzinfo=_KST)
    assert planner.current_or_next_session(after_close) == date(2026, 8, 18)
    assert planner.next_wakeup(after_close) == datetime(2026, 8, 18, 9, 10, tzinfo=_KST)
    pending = datetime(2026, 8, 18, 10, 0, tzinfo=_KST)
    assert planner.next_wakeup(pending, "PENDING_RECONCILIATION") == datetime(
        2026, 8, 18, 15, 20, tzinfo=_KST
    )


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://decision_worker:secret@postgres:5432/capstone_p1",
        "postgresql://decision_automation_runtime:secret@example.com:5432/capstone_p1",
        "",
    ],
)
def test_repository_rejects_non_runtime_or_non_internal_dsn(dsn: str) -> None:
    with pytest.raises(AutomationRuntimeError):
        PostgresAutomationRuntimeRepository(dsn)
