from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd
import pytest

from app.p1_owner.automation import (
    AutomationEngine,
    AutomationInputs,
    AutomationPolicySnapshot,
    AutomationStore,
    BotPosition,
    FixtureAutomationTransport,
    Quote,
    SignalCandidate,
)
from app.p1_owner.automation_atr import CompletedDailyBar

_KST = ZoneInfo("Asia/Seoul")
_SESSION = date(2026, 8, 26)
_NOW = datetime(2026, 8, 26, 9, 30, tzinfo=_KST)
_RUN = "auto_run_v3_fixture_0001"
_POLICY = "auto_pol_" + "a" * 32


def _policy(preset: str = "BALANCED") -> AutomationPolicySnapshot:
    return AutomationPolicySnapshot.from_v3_preset(
        policy_id=_POLICY,
        version=1,
        capital_limit_krw=10_000_000,
        preset=cast(Any, preset),
    )


def _history(symbol: str = "005930") -> tuple[tuple[CompletedDailyBar, ...], tuple[date, ...]]:
    del symbol
    calendar = xcals.get_calendar("XKRX", start="2025-01-01", end="2035-12-31")
    anchor = calendar.previous_session(pd.Timestamp(_SESSION))
    expected = tuple(item.date() for item in calendar.sessions_window(anchor, -100))
    bars = tuple(CompletedDailyBar(item, 70_000, 71_000, 69_000, 70_000) for item in expected[-23:])
    return bars, expected


def _store() -> AutomationStore:
    store = AutomationStore(
        account_id="acct_fixture_0001",
        brokerage_mode="KIS_MOCK",
        principle_id="prc_fixture_0001",
        strategy_id="strategy_fixture_0001",
        baseline_account_digest="a" * 64,
    )
    store.create_run(run_id=_RUN, session_date=_SESSION, now=_NOW)
    return store


def _buy() -> SignalCandidate:
    return SignalCandidate("005930", "BUY", "BUY", 0.05)


def _drive(
    store: AutomationStore, transport: FixtureAutomationTransport, inputs: AutomationInputs
) -> str:
    result: dict[str, object] = {}
    for index in range(1, 20):
        result = AutomationEngine(store).tick(
            run_id=_RUN,
            tick_id=f"v3_tick_{index}",
            now=_NOW + timedelta(seconds=index),
            inputs=inputs,
            transport=transport,
        )
        if result["state"] in {
            "COMPLETED",
            "SKIPPED_DATA_UNAVAILABLE",
            "SKIPPED_NO_ACTION",
            "HALTED",
        }:
            return str(result["state"])
    raise AssertionError(result)


def test_ai_off_v3_uses_no_screen_or_judge_and_unlimited_fill_snapshots_peak() -> None:
    bars, expected = _history()
    policy = _policy("AGGRESSIVE")
    inputs = AutomationInputs(
        session_date=_SESSION,
        policy=policy,
        signals=(_buy(),),
        atr_histories={"005930": bars},
        atr_expected_sessions=expected,
        ai_judgement_enabled=False,
        buyable_quantity=1,
    )
    transport = FixtureAutomationTransport(
        quotes={"005930": Quote("005930", 75_000, 52_500, 97_500)},
    )
    store = _store()
    assert _drive(store, transport, inputs) == "COMPLETED"
    position = store.positions[0]
    assert position.expiry_session is None
    assert position.max_holding_sessions == 0
    assert position.peak_price_krw == position.entry_average_fill_price_krw
    assert (transport.screen_calls, transport.judge_calls, transport.vertex_calls) == (0, 0, 0)


@pytest.mark.parametrize("holding_sessions", [1, 20, 60, 1_260])
def test_finite_holding_expiry_uses_exact_xkrx_sessions(holding_sessions: int) -> None:
    bars, expected_sessions = _history()
    policy = AutomationPolicySnapshot(
        policy_id=_POLICY,
        version=holding_sessions,
        capital_limit_krw=10_000_000,
        stop_loss_bps=500,
        take_profit_bps=1_000,
        preset="CUSTOM",
        max_holding_sessions=holding_sessions,
        atr_period=22,
        atr_multiplier_milli=3_000,
        model_sell_enabled=True,
    )
    inputs = AutomationInputs(
        session_date=_SESSION,
        policy=policy,
        signals=(_buy(),),
        atr_histories={"005930": bars},
        atr_expected_sessions=expected_sessions,
        ai_judgement_enabled=False,
        buyable_quantity=1,
    )
    transport = FixtureAutomationTransport(
        quotes={"005930": Quote("005930", 75_000, 52_500, 97_500)},
    )
    store = _store()
    assert _drive(store, transport, inputs) == "COMPLETED"
    calendar = xcals.get_calendar("XKRX", start="2025-01-01", end="2035-12-31")
    current = calendar.date_to_session(pd.Timestamp(_SESSION), direction="none")
    for _ in range(holding_sessions):
        current = calendar.next_session(current)
    assert store.positions[0].expiry_session == current.date()


def test_v3_candidate_with_insufficient_atr_is_excluded_before_quote() -> None:
    inputs = AutomationInputs(
        session_date=_SESSION,
        policy=_policy(),
        signals=(_buy(),),
        atr_histories={"005930": ()},
        ai_judgement_enabled=False,
    )
    transport = FixtureAutomationTransport(
        quotes={"005930": Quote("005930", 75_000, 52_500, 97_500)},
    )
    store = _store()
    assert _drive(store, transport, inputs) == "SKIPPED_DATA_UNAVAILABLE"
    assert transport.quote_calls == 0


def test_stop_loss_precedes_simultaneous_atr_trigger() -> None:
    bars, expected = _history()
    store = _store()
    store.positions.append(
        BotPosition(
            "auto_pos_v3_stop_0001",
            store.account_id,
            "005930",
            date(2026, 8, 24),
            date(2026, 11, 20),
            _NOW,
            entry_average_fill_price_krw=100_000,
            max_holding_sessions=60,
            atr_period=22,
            atr_multiplier_milli=3_000,
            peak_price_krw=120_000,
            trailing_stop_krw=99_000,
            atr_status="AVAILABLE",
            atr_as_of_session=bars[-1].session_date,
        )
    )
    transport = FixtureAutomationTransport(
        quotes={"005930": Quote("005930", 95_000, 50_000, 150_000)},
    )
    inputs = AutomationInputs(
        session_date=_SESSION,
        policy=_policy(),
        atr_histories={"005930": bars},
        atr_expected_sessions=expected,
    )
    AutomationEngine(store).tick(
        run_id=_RUN,
        tick_id="priority_1",
        now=_NOW,
        inputs=inputs,
        transport=transport,
    )
    result = AutomationEngine(store).tick(
        run_id=_RUN,
        tick_id="priority_2",
        now=_NOW + timedelta(seconds=1),
        inputs=inputs,
        transport=transport,
    )
    assert result["state"] == "EXIT_SELECTED"
    assert store.runs[_RUN].exit_reason == "STOP_LOSS"


@pytest.mark.parametrize(
    ("quote_price", "model", "expiry", "trailing", "expected"),
    [
        (97_000, True, False, 99_000, "ATR_TRAILING"),
        (112_000, True, False, 1, "MODEL_SELL"),
        (112_000, False, True, 1, "TAKE_PROFIT"),
        (100_000, False, True, 1, "MAX_HOLDING_SESSIONS"),
    ],
)
def test_v3_exit_priority_pairs_use_the_first_true_rule(
    quote_price: int,
    model: bool,
    expiry: bool,
    trailing: int,
    expected: str,
) -> None:
    bars, expected_sessions = _history()
    store = _store()
    store.runs[_RUN].state = "PRECHECK"
    store.positions.append(
        BotPosition(
            "auto_pos_v3_priority_0001",
            store.account_id,
            "005930",
            bars[-2].session_date,
            _SESSION if expiry else date(2026, 11, 20),
            _NOW,
            entry_average_fill_price_krw=100_000,
            max_holding_sessions=60,
            atr_period=22,
            atr_multiplier_milli=3_000,
            model_sell_enabled=True,
            peak_price_krw=100_000,
            trailing_stop_krw=trailing,
            atr_status="AVAILABLE",
            atr_as_of_session=bars[-1].session_date,
        )
    )
    inputs = AutomationInputs(
        session_date=_SESSION,
        policy=_policy(),
        signals=((SignalCandidate("005930", "SELL", "SELL", -0.1),) if model else ()),
        atr_histories={"005930": bars},
        atr_expected_sessions=expected_sessions,
    )
    AutomationEngine(store).tick(
        run_id=_RUN,
        tick_id="priority_pair",
        now=_NOW,
        inputs=inputs,
        transport=FixtureAutomationTransport(
            quotes={"005930": Quote("005930", quote_price, 50_000, 150_000)}
        ),
    )

    assert store.runs[_RUN].exit_reason == expected


def test_model_sell_off_skips_only_model_branch() -> None:
    bars, expected_sessions = _history()
    store = _store()
    store.runs[_RUN].state = "PRECHECK"
    store.positions.append(
        BotPosition(
            "auto_pos_v3_model_off_0001",
            store.account_id,
            "005930",
            bars[-2].session_date,
            date(2026, 11, 20),
            _NOW,
            entry_average_fill_price_krw=100_000,
            max_holding_sessions=60,
            atr_period=22,
            atr_multiplier_milli=3_000,
            model_sell_enabled=False,
            peak_price_krw=100_000,
            trailing_stop_krw=1,
            atr_status="AVAILABLE",
            atr_as_of_session=bars[-1].session_date,
        )
    )
    inputs = AutomationInputs(
        session_date=_SESSION,
        policy=_policy(),
        signals=(SignalCandidate("005930", "SELL", "SELL", -0.1),),
        atr_histories={"005930": bars},
        atr_expected_sessions=expected_sessions,
    )
    result = AutomationEngine(store).tick(
        run_id=_RUN,
        tick_id="model_off",
        now=_NOW,
        inputs=inputs,
        transport=FixtureAutomationTransport(
            quotes={"005930": Quote("005930", 100_000, 50_000, 150_000)}
        ),
    )

    assert result["state"] == "SKIPPED_NO_ACTION"
    assert store.runs[_RUN].exit_reason is None


def test_pre_entry_high_does_not_poison_position_peak_and_sell_keeps_v3_snapshot() -> None:
    bars, expected_sessions = _history()
    poisoned = (
        CompletedDailyBar(bars[0].session_date, 70_000, 200_000, 60_000, 70_000),
        *bars[1:],
    )
    store = _store()
    store.runs[_RUN].state = "PRECHECK"
    position = BotPosition(
        "auto_pos_v3_peak_0001",
        store.account_id,
        "005930",
        bars[-2].session_date,
        date(2026, 11, 20),
        _NOW,
        entry_average_fill_price_krw=100_000,
        max_holding_sessions=60,
        atr_period=22,
        atr_multiplier_milli=3_000,
        model_sell_enabled=True,
        peak_price_krw=100_000,
        trailing_stop_krw=1,
        atr_status="AVAILABLE",
        atr_as_of_session=bars[-1].session_date,
    )
    store.positions.append(position)
    inputs = AutomationInputs(
        session_date=_SESSION,
        policy=_policy(),
        signals=(SignalCandidate("005930", "SELL", "SELL", -0.1),),
        atr_histories={"005930": poisoned},
        atr_expected_sessions=expected_sessions,
    )
    transport = FixtureAutomationTransport(
        quotes={"005930": Quote("005930", 100_000, 50_000, 150_000)}
    )
    engine = AutomationEngine(store)
    engine.tick(run_id=_RUN, tick_id="peak_then_sell", now=_NOW, inputs=inputs, transport=transport)
    assert position.peak_price_krw == 100_000
    engine.tick(
        run_id=_RUN,
        tick_id="sell_sizing",
        now=_NOW + timedelta(seconds=1),
        inputs=inputs,
        transport=transport,
    )
    engine.tick(
        run_id=_RUN,
        tick_id="sell_sizing_2",
        now=_NOW + timedelta(seconds=2),
        inputs=inputs,
        transport=transport,
    )
    snapshot = store.runs[_RUN].policy_snapshot
    assert snapshot is not None and snapshot.is_v3
    assert snapshot.max_holding_sessions == 60
    assert snapshot.atr_multiplier_milli == 3_000
