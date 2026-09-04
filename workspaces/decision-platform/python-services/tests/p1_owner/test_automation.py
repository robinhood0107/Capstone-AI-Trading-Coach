from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from app.p1_owner.automation import (
    AccountLineageSnapshot,
    AutomationEngine,
    AutomationInputs,
    AutomationPolicySnapshot,
    AutomationStore,
    BotPosition,
    ExactOrderIntent,
    FixtureAutomationTransport,
    OrderReservation,
    Quote,
    ReconcileSnapshot,
    SignalCandidate,
    _limit_price,
    _estimated_net_return_bps,
    _variable_buy_quantity,
    _tick_size,
)

_KST = ZoneInfo("Asia/Seoul")
_SESSION = date(2026, 8, 26)
_NOW = datetime(2026, 8, 26, 9, 30, tzinfo=_KST)
_RUN_ID = "auto_run_fixture_0001"
_POLICY_ID = "auto_pol_" + "f" * 32


def _store() -> AutomationStore:
    return AutomationStore(
        account_id="acct_fixture_0001",
        brokerage_mode="KIS_MOCK",
        principle_id="prc_fixture_0001",
        strategy_id="strategy_fixture_0001",
        baseline_account_digest="a" * 64,
    )


def _buy(symbol: str = "005930", expected_return: float = 0.05) -> SignalCandidate:
    return SignalCandidate(symbol, "BUY", "BUY", expected_return)


def _sell(symbol: str = "005930") -> SignalCandidate:
    return SignalCandidate(symbol, "SELL", "SELL", -0.03)


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
    quotes = {
        symbol: Quote(
            symbol,
            selected_quote.price_krw,
            selected_quote.lower_limit_krw,
            selected_quote.upper_limit_krw,
            selected_quote.fresh,
            selected_quote.is_etf_etn,
        )
        for symbol in {"005930", "000001", "000002", "000003", selected_quote.symbol}
    }
    return FixtureAutomationTransport(
        quotes=quotes,
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


@pytest.mark.parametrize(
    ("lstm_signal", "eligible"),
    [("BUY", True), ("HOLD", True), ("SELL", False)],
)
def test_buy_needs_a_rule_buy_and_only_a_model_sell_vetoes_it(
    lstm_signal: str, eligible: bool
) -> None:
    """매수 후보는 RULE BUY 가 만들고 LSTM 은 SELL 일 때만 거부한다.

    2-of-2 교집합에서 RULE 주도 + LSTM 거부권으로 바꾼 판정을 고정한다. 교집합 시절에는
    LSTM HOLD 가 매수를 막았고, 그래서 후보가 0인 날이 51.8%(최장 191세션 연속)였다.
    근거는 `research/p1-return-profit-verification/consensus_eval.py` 의 22 fold PIT 측정.
    """

    store = _store()
    _create(store)
    engine = AutomationEngine(store)
    candidate = SignalCandidate("005930", cast(Any, lstm_signal), "BUY", 0.05)
    assert bool(engine._buy_candidates(_inputs(candidate))) is eligible


def test_a_rule_sell_is_never_a_buy_candidate_whatever_the_model_says() -> None:
    """거부권은 한 방향이다 - LSTM 이 BUY 여도 RULE 이 BUY 가 아니면 후보가 아니다."""

    store = _store()
    _create(store)
    engine = AutomationEngine(store)
    for baseline in ("HOLD", "SELL"):
        candidate = SignalCandidate("005930", "BUY", cast(Any, baseline), 0.05)
        assert engine._buy_candidates(_inputs(candidate)) == ()


def test_buy_full_fill_is_restart_safe_exact_one_and_contract_valid() -> None:
    store = _store()
    _create(store)
    transport = _transport()
    states = _drive(store, transport, _inputs(_buy()))

    assert states == [
        "PRECHECK",
        "AI_JUDGING",
        "BUY_CANDIDATE_SELECTED",
        "NEWS_CHECKING",
        "ORDER_SIZING",
        "RISK_CHECKING",
        "ORDER_SUBMITTING",
        "ORDER_SUBMITTED",
        "COMPLETED",
    ]
    run = store.runs[_RUN_ID]
    assert run.selected_symbol == "005930"
    assert run.reservation is not None
    assert (run.reservation.symbol, run.reservation.side) == ("005930", "BUY")
    assert (run.reservation.quantity, run.reservation.limit_price_krw) == (1, 75_100)
    assert run.reservation.intent is not None
    assert run.reservation.intent.estimated_amount == 75_100
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

    assert list(_validator("automation-run.v2").iter_errors(run.projection())) == []
    assert list(_validator("automation-position.v2").iter_errors(position.projection())) == []
    assert all(
        list(_validator("automation-event.v1").iter_errors(event)) == [] for event in store.events
    )
    control = store.control_projection(kill_switch_active=False)
    assert control["projectionState"] == "ARMED"
    assert list(_validator("automation-control.v1").iter_errors(control)) == []


@pytest.mark.parametrize("verdict", ["VETO_BUY", "ABSTAIN", "NO_VETO"])
@pytest.mark.parametrize("provider_bound", [True, False])
def test_news_verdict_is_advisory_and_never_stops_the_buy(
    verdict: str, provider_bound: bool
) -> None:
    """뉴스 판정은 호출·집계되지만 run 을 닫지 않는다.

    이전 계약은 `VETO_BUY` 와 (provider 가 붙어 있을 때의) `ABSTAIN` 을 차단으로 봤다.
    실측으로 그것이 매수를 영구히 막았다 - 2026-09-04 세션이 `ABSTAIN /
    VERTEX_NO_REGISTERED_EVIDENCE` 로 닫혔고, 등록 근거가 0 건인 한 어떤 세션도 통과할 수
    없었다. 근거는 `contracts/changes/20260904-p1-news-advisory-and-intraday-buy-window.md`.
    """

    store = _store()
    _create(store)
    transport = _transport(news=verdict)
    candidates = (_buy("000002", 0.04), _buy("000001", 0.05))
    inputs = replace(_inputs(*candidates), news_veto_provider_bound=provider_bound)
    states = _drive(store, transport, inputs)

    assert "NEWS_VETOED" not in states
    assert states[-1] == "COMPLETED"
    # 판정은 계속 물어본다. 자문을 없애는 것이 아니라 차단만 없앤다.
    assert transport.vertex_calls == 1
    # 후보 순위는 그대로다 - 뉴스가 종목을 바꾸지 않는다.
    assert store.runs[_RUN_ID].selected_symbol == "000001"
    assert transport.submit_calls == 1


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

    exit_now = datetime(2026, 9, 2, 9, 30, tzinfo=_KST)
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
        # AI_JUDGING이 하나 늘어 같은 지점에 닿는 데 tick이 하나 더 든다.
        for index in range(1, 10):
            _tick(store, transport, inputs, index)
        assert store.runs[_RUN_ID].state == "PENDING_RECONCILIATION"
        result = _tick(
            store,
            transport,
            inputs,
            10,
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
    for index in range(1, 9):
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
        (datetime(2026, 8, 17, 9, 30, tzinfo=_KST), {}, "SKIPPED_NO_ACTION"),
        (datetime(2026, 8, 26, 15, 21, tzinfo=_KST), {}, "SKIPPED_LATE_START"),
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
    assert risk_transport.quote_calls == 1
    assert risk_transport.submit_calls == 0


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
    intent = ExactOrderIntent("005930", "BUY", "LIMIT", 1, 75_100, 75_100, "1d", store.strategy_id)
    run.reservation = OrderReservation("005930", "BUY", 1, 75_100, intent=intent)
    run.policy_snapshot = _inputs().policy
    transport = _transport(reconcile=["FILLED"])
    result = _tick(store, transport, _inputs(_buy()), 1)
    assert result["state"] == "COMPLETED"
    assert len(store.positions) == 1
    assert transport.reconcile_calls == 1
    assert transport.physical_calls == 0


def test_policy_presets_and_custom_thresholds_are_exact() -> None:
    expected = {
        "CONSERVATIVE": (300, 500),
        "BALANCED": (500, 1_000),
        "AGGRESSIVE": (800, 1_500),
    }
    for preset, thresholds in expected.items():
        policy = AutomationPolicySnapshot.from_preset(
            policy_id=_POLICY_ID,
            version=1,
            capital_limit_krw=10_000_000,
            preset=cast(Any, preset),
        )
        assert (policy.stop_loss_bps, policy.take_profit_bps) == thresholds

    custom = AutomationPolicySnapshot(
        _POLICY_ID,
        2,
        20_000_000,
        650,
        1_200,
        "CUSTOM",
    )
    assert (custom.stop_loss_bps, custom.take_profit_bps) == (650, 1_200)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, 2),
        ({"open_position_market_value_krw": 900_000}, 1),
        ({"principle_max_single_order_krw": 150_000}, 1),
        ({"principle_asset_remaining_krw": 150_000}, 1),
        ({"buyable_amount_krw": 150_000}, 1),
        ({"buyable_quantity": 1}, 1),
    ],
)
def test_variable_buy_quantity_uses_the_minimum_exact_budget(
    overrides: dict[str, int], expected: int
) -> None:
    policy = AutomationPolicySnapshot.from_preset(
        policy_id=_POLICY_ID,
        version=1,
        capital_limit_krw=1_000_000,
        preset="BALANCED",
    )
    values: dict[str, object] = {
        "session_date": _SESSION,
        "policy": policy,
        "buyable_quantity": 99,
        "buyable_amount_krw": 10_000_000,
    }
    values.update(overrides)
    assert _variable_buy_quantity(AutomationInputs(**cast(Any, values)), 75_100) == expected


def test_variable_quantity_exact_intent_and_five_position_cap() -> None:
    policy = AutomationPolicySnapshot.from_preset(
        policy_id=_POLICY_ID,
        version=1,
        capital_limit_krw=2_000_000,
        preset="BALANCED",
    )
    store = _store()
    _create(store)
    transport = _transport()
    inputs = _inputs(
        _buy(),
        policy=policy,
        buyable_quantity=3,
        buyable_amount_krw=1_000_000,
        principle_max_single_order_krw=1_000_000,
        principle_asset_remaining_krw=1_000_000,
    )
    assert _drive(store, transport, inputs)[-1] == "COMPLETED"
    reservation = cast(OrderReservation, store.runs[_RUN_ID].reservation)
    assert reservation.quantity == 3
    assert reservation.intent is not None
    assert reservation.intent.estimated_amount == 3 * reservation.limit_price_krw
    assert store.positions[0].quantity == 3

    capped = _store()
    capped.positions.extend(
        BotPosition(
            f"auto_pos_cap_{index:04d}",
            capped.account_id,
            f"{index:06d}",
            date(2026, 8, 20),
            date(2026, 9, 20),
            _NOW,
        )
        for index in range(1, 6)
    )
    _create(capped)
    capped_transport = _transport()
    assert _drive(capped, capped_transport, _inputs(_buy("000006"), policy=policy))[-1] == (
        "SKIPPED_NO_ACTION"
    )
    assert capped_transport.vertex_calls == capped_transport.submit_calls == 0


def test_internal_paper_fixture_runs_full_variable_quantity_loop() -> None:
    policy = AutomationPolicySnapshot.from_preset(
        policy_id=_POLICY_ID,
        version=1,
        capital_limit_krw=1_000_000,
        preset="CONSERVATIVE",
    )
    store = AutomationStore(
        account_id="acct_fixture_0001",
        brokerage_mode="INTERNAL_PAPER",
        principle_id="prc_fixture_0001",
        strategy_id="strategy_fixture_0001",
        baseline_account_digest="a" * 64,
        certification_status="NOT_REQUIRED_INTERNAL_PAPER",
    )
    _create(store)
    transport = _transport()
    inputs = _inputs(
        _buy(),
        policy=policy,
        buyable_quantity=10,
        buyable_amount_krw=1_000_000,
    )

    assert _drive(store, transport, inputs)[-1] == "COMPLETED"
    assert store.positions[0].quantity == 2
    assert transport.physical_calls == 0


@pytest.mark.parametrize(
    ("entry", "sell", "expected"),
    [
        (100_000, 97_350, -300),
        (100_000, 95_350, -500),
        (100_000, 92_350, -800),
        (100_000, 105_350, 500),
        (100_000, 110_350, 1_000),
        (100_000, 115_350, 1_500),
    ],
)
def test_exit_trigger_uses_exact_integer_35bp_cost(entry: int, sell: int, expected: int) -> None:
    assert _estimated_net_return_bps(entry, sell) == expected


def test_stop_loss_precedes_expiry_model_sell_and_take_profit() -> None:
    store = _store()
    store.positions.extend(
        [
            BotPosition(
                "auto_pos_stop_0001",
                store.account_id,
                "000001",
                date(2026, 8, 24),
                date(2026, 9, 10),
                _NOW,
                quantity=2,
                entry_average_fill_price_krw=100_000,
                stop_loss_bps=300,
                take_profit_bps=500,
            ),
            BotPosition(
                "auto_pos_expiry_0002",
                store.account_id,
                "000002",
                date(2026, 8, 20),
                _SESSION,
                _NOW,
            ),
            BotPosition(
                "auto_pos_model_0003",
                store.account_id,
                "000003",
                date(2026, 8, 22),
                date(2026, 9, 10),
                _NOW,
            ),
            BotPosition(
                "auto_pos_profit_0004",
                store.account_id,
                "000004",
                date(2026, 8, 23),
                date(2026, 9, 10),
                _NOW,
                entry_average_fill_price_krw=100_000,
                stop_loss_bps=300,
                take_profit_bps=500,
            ),
        ]
    )
    _create(store)
    transport = FixtureAutomationTransport(
        quotes={
            "000001": Quote("000001", 97_100, 70_000, 130_000),
            "000004": Quote("000004", 105_500, 70_000, 130_000),
        }
    )
    inputs = _inputs(_sell("000003"))
    assert _tick(store, transport, inputs, 1)["state"] == "PRECHECK"
    selected = _tick(store, transport, inputs, 2)
    assert (selected["state"], selected["selectedSymbol"]) == ("EXIT_SELECTED", "000001")
    assert store.runs[_RUN_ID].exit_reason == "STOP_LOSS"


def test_partial_buy_and_sell_cancel_apply_only_confirmed_quantity() -> None:
    policy = AutomationPolicySnapshot.from_preset(
        policy_id=_POLICY_ID,
        version=1,
        capital_limit_krw=2_000_000,
        preset="BALANCED",
    )
    buy_store = _store()
    _create(buy_store)
    buy_snapshot = ReconcileSnapshot(True, 1, 2, 75_050)
    buy_transport = _transport(submit="UNFILLED")
    buy_transport.reconcile_snapshots = [buy_snapshot, buy_snapshot]
    buy_inputs = _inputs(_buy(), policy=policy, buyable_quantity=3, buyable_amount_krw=1_000_000)
    for index in range(1, 9):
        _tick(buy_store, buy_transport, buy_inputs, index)
    result = _tick(
        buy_store,
        buy_transport,
        buy_inputs,
        9,
        now=datetime(2026, 8, 26, 15, 20, tzinfo=_KST),
    )
    assert result["state"] == "COMPLETED"
    assert (
        buy_store.positions[0].quantity,
        buy_store.positions[0].entry_average_fill_price_krw,
    ) == (
        1,
        75_050,
    )

    sell_store = _store()
    sell_store.positions.append(
        BotPosition(
            "auto_pos_partial_sell_0001",
            sell_store.account_id,
            "005930",
            date(2026, 8, 20),
            _SESSION,
            _NOW,
            quantity=4,
            entry_average_fill_price_krw=70_000,
        )
    )
    _create(sell_store)
    sell_snapshot = ReconcileSnapshot(True, 2, 2, 74_900)
    sell_transport = _transport(submit="UNFILLED")
    sell_transport.reconcile_snapshots = [sell_snapshot, sell_snapshot]
    sell_inputs = _inputs(policy=policy)
    for index in range(1, 8):
        _tick(sell_store, sell_transport, sell_inputs, index)
    sell_result = _tick(
        sell_store,
        sell_transport,
        sell_inputs,
        8,
        now=datetime(2026, 8, 26, 15, 20, tzinfo=_KST),
    )
    assert sell_result["state"] == "COMPLETED"
    assert sell_store.positions[0].quantity == 2
    assert sell_store.positions[0].status == "OPEN"


def test_account_lineage_excludes_valuation_and_allows_only_bounded_bot_fill() -> None:
    expected = AccountLineageSnapshot("acct_fixture_0001", 1_000_000, (("005930", 2),))
    same = AccountLineageSnapshot.from_projection(
        {
            "accountId": "acct_fixture_0001",
            "cashKrw": 1_000_000,
            "portfolioEquityKrw": 99_000_000,
            "positions": [{"marketValueKrw": 1, "quantity": 2, "symbol": "005930"}],
        }
    )
    assert expected.exact_match(same)
    buy_fill = AccountLineageSnapshot("acct_fixture_0001", 899_965, (("000660", 1), ("005930", 2)))
    assert expected.permits_fill(
        buy_fill,
        symbol="000660",
        side="BUY",
        filled_quantity=1,
        average_fill_price_krw=100_000,
    )
    unrelated = AccountLineageSnapshot(
        "acct_fixture_0001", 899_965, (("000660", 1), ("005380", 1), ("005930", 2))
    )
    assert not expected.permits_fill(
        unrelated,
        symbol="000660",
        side="BUY",
        filled_quantity=1,
        average_fill_price_krw=100_000,
    )


def test_provider_cap_is_checked_before_quote_and_pending_noop_is_durable() -> None:
    class CapExhaustedTransport:
        physical_calls = 16
        physical_submit_calls = 0
        quote_calls = 0
        vertex_calls = 0
        submit_calls = 0
        reconcile_calls = 0
        cancel_calls = 0

        def quote(self, symbol: str) -> Quote:
            self.quote_calls += 1
            return _quote(symbol)

        def vertex(self, symbol: str) -> str:
            del symbol
            self.vertex_calls += 1
            return "NO_VETO"

        def submit(self, reservation: OrderReservation) -> str:
            del reservation
            self.submit_calls += 1
            return "UNFILLED"

        def reconcile(self, reservation: OrderReservation | None) -> str:
            del reservation
            self.reconcile_calls += 1
            return "UNRESOLVED"

        def cancel(self, reservation: OrderReservation) -> bool:
            del reservation
            self.cancel_calls += 1
            return False

    store = _store()
    _create(store)
    transport = CapExhaustedTransport()
    inputs = _inputs(_buy())
    for index in range(1, 6):
        result = AutomationEngine(store).tick(
            run_id=_RUN_ID,
            tick_id=f"cap_tick_{index}",
            now=_NOW + timedelta(seconds=index),
            inputs=inputs,
            transport=cast(Any, transport),
        )
        if result["state"] == "HALTED":
            break
    assert result["state"] == "HALTED"
    assert transport.quote_calls == 0

    pending_store = _store()
    _create(pending_store)
    pending_run = pending_store.runs[_RUN_ID]
    pending_run.state = "PENDING_RECONCILIATION"
    pending_run.selected_symbol = "005930"
    pending_run.selected_side = "BUY"
    intent = ExactOrderIntent(
        "005930", "BUY", "LIMIT", 1, 75_100, 75_100, "1d", pending_store.strategy_id
    )
    pending_run.reservation = OrderReservation("005930", "BUY", 1, 75_100, intent=intent)
    pending_run.policy_snapshot = _inputs().policy
    pending_transport = _transport(reconcile=["UNRESOLVED", "UNRESOLVED"])
    before = len(pending_store.events)
    assert _tick(pending_store, pending_transport, _inputs(), 1)["state"] == (
        "PENDING_RECONCILIATION"
    )
    assert _tick(pending_store, pending_transport, _inputs(), 2)["state"] == (
        "PENDING_RECONCILIATION"
    )
    assert len(pending_store.events) == before + 2


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


@pytest.mark.parametrize(
    ("price", "upper", "lower", "side", "expected"),
    [
        # 정상 밴드에서는 한 틱만 움직인다.
        (258_000, 335_000, 181_000, "BUY", 258_500),
        (1_679_000, 2_182_000, 1_176_000, "BUY", 1_680_000),
        (258_000, 335_000, 181_000, "SELL", 257_500),
        # 상한가가 격자 밖이면 매수는 내림으로 스냅한다.
        # 상한가로 clamp된 값이 다음 밴드 격자에 맞으면 그대로 쓴다.
        (199_900, 200_050, 140_000, "BUY", 200_000),
        # 하한가가 격자 밖이면 매도는 올림으로 스냅해 주문 가능 범위 안에 남는다.
        (49_950, 64_900, 49_930, "SELL", 49_950),
    ],
)
def test_limit_price_never_leaves_the_krx_tick_grid(
    price: int, upper: int, lower: int, side: str, expected: int
) -> None:
    quote = Quote("005930", price, lower, upper)
    result = _limit_price(quote, cast(Any, side))

    assert result == expected
    assert result % _tick_size(result, False) == 0
    assert lower <= result <= upper
