from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from app.p1_owner.automation import (
    AutomationEngine,
    AutomationInputs,
    AutomationStore,
    BotPosition,
    FixtureAutomationTransport,
    OrderReservation,
    Quote,
    SignalCandidate,
)

_KST = ZoneInfo("Asia/Seoul")
_SESSION = date(2026, 8, 26)
_NOW = datetime(2026, 8, 26, 9, 0, tzinfo=_KST)
_RUN_ID = "auto_run_fixture_0001"


def _store() -> AutomationStore:
    return AutomationStore(
        account_id="acct_fixture_0001",
        brokerage_mode="KIS_MOCK",
        principle_id="prc_fixture_0001",
        strategy_id="strategy_fixture_0001",
        baseline_account_digest="a" * 64,
    )


def _buy(symbol: str = "005930", expected_return: float = 0.05) -> SignalCandidate:
    return SignalCandidate(symbol, "BUY", "BUY", expected_return, 0.8)


def _sell(symbol: str = "005930") -> SignalCandidate:
    return SignalCandidate(symbol, "SELL", "SELL", -0.03, 0.8)


def _quote(symbol: str = "005930", *, fresh: bool = True) -> Quote:
    return Quote(symbol, 75_000, 52_500, 97_500, fresh)


def _transport(
    *,
    news: str = "NO_VETO",
    submit: str = "FILLED",
    reconcile: list[str] | None = None,
    cancel_succeeds: bool = True,
    quote: Quote | None = None,
) -> FixtureAutomationTransport:
    selected_quote = quote or _quote()
    return FixtureAutomationTransport(
        quotes={selected_quote.symbol: selected_quote},
        news_verdict=cast(Any, news),
        submit_outcome=cast(Any, submit),
        reconcile_outcomes=cast(Any, reconcile or ["FILLED"]),
        cancel_succeeds=cancel_succeeds,
    )


def _inputs(*signals: SignalCandidate, **overrides: object) -> AutomationInputs:
    values: dict[str, object] = {"session_date": _SESSION, "signals": tuple(signals)}
    values.update(overrides)
    return AutomationInputs(**cast(Any, values))


def _create(store: AutomationStore, *, run_id: str = _RUN_ID, now: datetime = _NOW) -> None:
    store.create_run(run_id=run_id, session_date=now.date(), now=now)


def _tick(
    store: AutomationStore,
    transport: FixtureAutomationTransport,
    inputs: AutomationInputs,
    index: int,
    *,
    now: datetime | None = None,
    run_id: str = _RUN_ID,
    tick_id: str | None = None,
) -> dict[str, object]:
    # A new engine object on every tick proves state recovery from the shared append-only store.
    return AutomationEngine(store).tick(
        run_id=run_id,
        tick_id=tick_id or f"tick_{index:03d}",
        now=now or _NOW + timedelta(seconds=index),
        inputs=inputs,
        transport=transport,
    )


def _drive(
    store: AutomationStore,
    transport: FixtureAutomationTransport,
    inputs: AutomationInputs,
    *,
    max_ticks: int = 20,
    run_id: str = _RUN_ID,
    start: datetime = _NOW,
) -> list[str]:
    states: list[str] = []
    for index in range(1, max_ticks + 1):
        result = _tick(
            store,
            transport,
            inputs,
            index,
            now=start + timedelta(seconds=index),
            run_id=run_id,
            tick_id=f"{run_id}_tick_{index:03d}",
        )
        state = cast(str, result["state"])
        states.append(state)
        if state in {
            "NEWS_VETOED",
            "CANCELLED_UNFILLED",
            "COMPLETED",
            "SKIPPED_NO_ACTION",
            "SKIPPED_DATA_UNAVAILABLE",
            "SKIPPED_LATE_START",
            "HALTED",
        }:
            return states
    raise AssertionError(f"automation run did not terminate: {states}")


def _validator(name: str) -> Draft202012Validator:
    root = Path(__file__).parents[5]
    schema = json.loads((root / f"contracts/schemas/{name}.schema.json").read_text())
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_buy_full_fill_is_restart_safe_exact_one_and_contract_valid() -> None:
    store = _store()
    _create(store)
    transport = _transport()
    states = _drive(store, transport, _inputs(_buy()))

    assert states == [
        "PRECHECK",
        "BUY_CANDIDATE_SELECTED",
        "NEWS_CHECKING",
        "RISK_CHECKING",
        "ORDER_SUBMITTING",
        "ORDER_SUBMITTING",
        "ORDER_SUBMITTED",
        "COMPLETED",
    ]
    run = store.runs[_RUN_ID]
    assert run.selected_symbol == "005930"
    assert run.reservation == OrderReservation("005930", "BUY", 1, 75_100)
    assert (transport.vertex_calls, transport.quote_calls, transport.submit_calls) == (1, 1, 1)
    assert transport.reconcile_calls == 1
    assert transport.cancel_calls == transport.physical_calls == 0
    assert run.logical_submit_count == 1
    assert len(store.positions) == 1
    position = store.positions[0]
    assert position.status == "OPEN"
    assert position.expiry_session == date(2026, 9, 2)
    assert store.events[0]["eventType"] == "BASELINE_CAPTURED"
    assert [event["sequence"] for event in store.events] == list(range(1, len(store.events) + 1))

    assert list(_validator("automation-run.v1").iter_errors(run.projection())) == []
    assert list(_validator("automation-position.v1").iter_errors(position.projection())) == []
    assert all(
        list(_validator("automation-event.v1").iter_errors(event)) == [] for event in store.events
    )
    control = store.control_projection(kill_switch_active=False)
    assert control["projectionState"] == "ARMED"
    assert list(_validator("automation-control.v1").iter_errors(control)) == []


@pytest.mark.parametrize("verdict", ["VETO_BUY", "ABSTAIN"])
def test_vertex_veto_and_abstain_stop_buy_without_second_candidate(verdict: str) -> None:
    store = _store()
    _create(store)
    transport = _transport(news=verdict)
    candidates = (_buy("000002", 0.04), _buy("000001", 0.05))
    states = _drive(store, transport, _inputs(*candidates))

    assert states[-1] == "NEWS_VETOED"
    assert store.runs[_RUN_ID].selected_symbol == "000001"
    assert transport.vertex_calls == 1
    assert transport.quote_calls == transport.submit_calls == 0
    assert store.positions == []
    assert transport.physical_calls == 0


def test_model_sell_and_expiry_sell_never_call_vertex_and_only_bot_lot_closes() -> None:
    for expiry, signal in ((date(2026, 9, 2), _sell()), (_SESSION, _buy())):
        store = _store()
        store.positions.append(
            BotPosition(
                "auto_pos_fixture_0001",
                store.account_id,
                "005930",
                date(2026, 8, 25),
                expiry,
                _NOW - timedelta(days=1),
            )
        )
        _create(store)
        transport = _transport()
        states = _drive(store, transport, _inputs(signal))
        assert states[-1] == "COMPLETED"
        run = store.runs[_RUN_ID]
        assert run.selected_side == "SELL"
        assert cast(OrderReservation, run.reservation).limit_price_krw == 74_900
        assert store.positions[0].status == "CLOSED"
        assert transport.vertex_calls == 0
        assert transport.submit_calls == 1
        assert transport.physical_calls == 0


def test_buy_then_fifth_xkrx_session_sell_closes_the_same_one_share_lot() -> None:
    store = _store()
    _create(store)
    buy_transport = _transport()
    assert _drive(store, buy_transport, _inputs(_buy()))[-1] == "COMPLETED"
    position = store.positions[0]
    assert position.entry_session == _SESSION
    assert position.expiry_session == date(2026, 9, 2)

    exit_now = datetime(2026, 9, 2, 9, 0, tzinfo=_KST)
    exit_run = "auto_run_fixture_0002"
    _create(store, run_id=exit_run, now=exit_now)
    sell_transport = _transport()
    exit_inputs = AutomationInputs(session_date=exit_now.date())
    states = _drive(
        store,
        sell_transport,
        exit_inputs,
        run_id=exit_run,
        start=exit_now,
    )
    assert states[-1] == "COMPLETED"
    assert store.runs[exit_run].selected_side == "SELL"
    assert position.status == "CLOSED"
    assert position.closed_at is not None
    assert buy_transport.vertex_calls == 1
    assert sell_transport.vertex_calls == 0
    assert buy_transport.physical_calls == sell_transport.physical_calls == 0


def test_multiple_exits_choose_most_overdue_then_entry_then_symbol() -> None:
    store = _store()
    store.positions.extend(
        [
            BotPosition(
                "auto_pos_fixture_0001",
                store.account_id,
                "000002",
                date(2026, 8, 10),
                date(2026, 8, 24),
                _NOW - timedelta(days=10),
            ),
            BotPosition(
                "auto_pos_fixture_0002",
                store.account_id,
                "000001",
                date(2026, 8, 9),
                date(2026, 8, 24),
                _NOW - timedelta(days=11),
            ),
            BotPosition(
                "auto_pos_fixture_0003",
                store.account_id,
                "000003",
                date(2026, 8, 1),
                date(2026, 8, 25),
                _NOW - timedelta(days=20),
            ),
        ]
    )
    _create(store)
    transport = _transport(quote=_quote("000001"))
    _tick(store, transport, _inputs(), 1)
    selected = _tick(store, transport, _inputs(), 2)
    assert selected["state"] == "EXIT_SELECTED"
    assert selected["selectedSymbol"] == "000001"
    assert sum(position.status == "EXIT_PENDING" for position in store.positions) == 1


def test_unfilled_cancel_and_cancel_failure_are_terminal_without_resubmit() -> None:
    for cancel_succeeds, expected in ((True, "CANCELLED_UNFILLED"), (False, "HALTED")):
        store = _store()
        _create(store)
        transport = _transport(
            submit="UNFILLED",
            reconcile=["UNFILLED", "UNFILLED"],
            cancel_succeeds=cancel_succeeds,
        )
        inputs = _inputs(_buy())
        for index in range(1, 9):
            _tick(store, transport, inputs, index)
        assert store.runs[_RUN_ID].state == "PENDING_RECONCILIATION"
        result = _tick(
            store,
            transport,
            inputs,
            9,
            now=datetime(2026, 8, 26, 15, 20, tzinfo=_KST),
        )
        assert result["state"] == expected
        assert transport.submit_calls == 1
        assert transport.cancel_calls == 1
        assert transport.physical_calls == 0
        assert store.positions == []


def test_ambiguous_submit_reconciles_without_second_submit() -> None:
    store = _store()
    _create(store)
    transport = _transport(submit="AMBIGUOUS", reconcile=["FILLED"])
    states = _drive(store, transport, _inputs(_buy()))
    assert "PENDING_RECONCILIATION" in states
    assert states[-1] == "COMPLETED"
    assert transport.submit_calls == 1
    assert transport.reconcile_calls == 1
    assert len(store.positions) == 1


def test_duplicate_tick_is_exact_noop_and_session_submit_cap_halts_second_run() -> None:
    store = _store()
    _create(store)
    transport = _transport()
    inputs = _inputs(_buy())
    first = _tick(store, transport, inputs, 1, tick_id="same_tick")
    counts = (transport.vertex_calls, transport.quote_calls, transport.submit_calls)
    duplicate = _tick(store, transport, inputs, 99, tick_id="same_tick")
    assert duplicate == first
    assert (transport.vertex_calls, transport.quote_calls, transport.submit_calls) == counts
    assert store.runs[_RUN_ID].state == "PRECHECK"

    _drive(store, transport, inputs)
    second_id = "auto_run_fixture_0002"
    _create(store, run_id=second_id, now=_NOW + timedelta(minutes=1))
    second_transport = _transport(quote=_quote("000002"))
    second_inputs = _inputs(_buy("000002"))
    for index in range(1, 8):
        result = _tick(
            store,
            second_transport,
            second_inputs,
            index,
            run_id=second_id,
            tick_id=f"second_{index}",
        )
    assert result["state"] == "HALTED"
    assert second_transport.submit_calls == 0


@pytest.mark.parametrize(
    ("now", "overrides", "expected"),
    [
        (datetime(2026, 8, 17, 9, 0, tzinfo=_KST), {}, "SKIPPED_NO_ACTION"),
        (datetime(2026, 8, 26, 9, 21, tzinfo=_KST), {}, "SKIPPED_LATE_START"),
        (_NOW, {"daily_shard_fresh_complete": False}, "SKIPPED_DATA_UNAVAILABLE"),
        (_NOW, {"account_digest_matches": False}, "HALTED"),
        (_NOW, {"kill_switch_active": True}, "HALTED"),
    ],
)
def test_holiday_late_stale_drift_and_kill_switch_fail_before_transports(
    now: datetime, overrides: dict[str, object], expected: str
) -> None:
    store = _store()
    _create(store, now=now)
    transport = _transport()
    inputs = AutomationInputs(
        **cast(Any, {"session_date": now.date(), "signals": (_buy(),), **overrides})
    )
    result = _tick(store, transport, inputs, 1, now=now)
    assert result["state"] == expected
    assert (
        transport.vertex_calls
        + transport.quote_calls
        + transport.submit_calls
        + transport.reconcile_calls
        + transport.cancel_calls
    ) == 0
    assert transport.physical_calls == 0


def test_baseline_manual_position_is_excluded_without_false_drift_halt() -> None:
    store = _store()
    _create(store)
    transport = _transport()
    inputs = _inputs(_buy(), manual_position_symbols=frozenset({"005930"}))
    states = _drive(store, transport, inputs)
    assert states[-1] == "SKIPPED_NO_ACTION"
    assert store.control_state == "ARMED"
    assert transport.vertex_calls == transport.submit_calls == 0


def test_stale_quote_balance_gap_and_risk_deny_make_zero_submits() -> None:
    stale_store = _store()
    _create(stale_store)
    stale_transport = _transport(quote=_quote(fresh=False))
    stale_states = _drive(stale_store, stale_transport, _inputs(_buy()))
    assert stale_states[-1] == "SKIPPED_DATA_UNAVAILABLE"
    assert stale_transport.quote_calls == 1
    assert stale_transport.submit_calls == 0

    balance_store = _store()
    _create(balance_store)
    balance_transport = _transport()
    balance_states = _drive(
        balance_store,
        balance_transport,
        _inputs(_buy(), account_complete=False),
    )
    assert balance_states[-1] == "SKIPPED_DATA_UNAVAILABLE"
    assert balance_transport.quote_calls == balance_transport.submit_calls == 0

    risk_store = _store()
    risk_store.positions.append(
        BotPosition(
            "auto_pos_fixture_0001",
            risk_store.account_id,
            "005930",
            date(2026, 8, 20),
            _SESSION,
            _NOW - timedelta(days=6),
        )
    )
    _create(risk_store)
    risk_transport = _transport()
    risk_states = _drive(risk_store, risk_transport, _inputs(risk_allow=False))
    assert risk_states[-1] == "SKIPPED_NO_ACTION"
    assert risk_store.positions[0].status == "OPEN"
    assert risk_transport.quote_calls == risk_transport.submit_calls == 0


def test_pending_previous_reconciliation_precedes_exit_and_buy_selection() -> None:
    store = _store()
    _create(store)
    transport = _transport(reconcile=["UNRESOLVED"])
    inputs = _inputs(_buy(), unfinished_previous_order=True)
    assert _tick(store, transport, inputs, 1)["state"] == "PRECHECK"
    assert _tick(store, transport, inputs, 2)["state"] == "RECONCILING_PREVIOUS"
    assert _tick(store, transport, inputs, 3)["state"] == "PENDING_RECONCILIATION"
    assert transport.reconcile_calls == 1
    assert transport.vertex_calls == transport.submit_calls == 0


def test_disarm_still_allows_outstanding_reconciliation() -> None:
    store = _store()
    store.control_state = "DISARMED"
    _create(store)
    run = store.runs[_RUN_ID]
    run.state = "PENDING_RECONCILIATION"
    run.selected_symbol = "005930"
    run.selected_side = "BUY"
    run.reservation = OrderReservation("005930", "BUY", 1, 75_100)
    transport = _transport(reconcile=["FILLED"])
    result = _tick(store, transport, _inputs(_buy()), 1)
    assert result["state"] == "COMPLETED"
    assert len(store.positions) == 1
    assert transport.reconcile_calls == 1
    assert transport.physical_calls == 0


def test_module_has_no_live_provider_network_or_database_transport() -> None:
    source = (Path(__file__).parents[2] / "app/p1_owner/automation.py").read_text()
    for forbidden in (
        "app.brokerage",
        "app.data.kis",
        "app.data.gdelt",
        "psycopg",
        "httpx",
        "requests",
        "urllib",
        "socket",
    ):
        assert forbidden not in source
    assert "physical_calls: int = 0" in source
    assert "quantity: int\n    limit_price_krw" in source
