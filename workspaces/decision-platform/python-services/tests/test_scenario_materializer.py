from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.p1_owner.scenario_materializer import (
    ReplayResult,
    ScenarioMaterializationError,
    Trade,
    _baseline_replay,
    _bars_by_symbol,
    _metrics,
    _strict_replay,
)


def _sessions(count: int = 104) -> list[date]:
    start = date(2026, 1, 2)
    return [start + timedelta(days=index) for index in range(count)]


def _bars(symbols: int = 31) -> dict[str, list[dict[str, object]]]:
    sessions = _sessions()
    return {
        f"{index:06d}": [
            {
                "sessionDate": session.isoformat(),
                "open": 10_000 + day,
                "high": 10_100 + day,
                "low": 9_900 + day,
                "close": 10_000 + day,
                "volume": 1_000_000,
            }
            for day, session in enumerate(sessions)
        ]
        for index in range(1, symbols + 1)
    }


def test_baseline_replay_covers_exact_31_by_104_without_placeholder_values() -> None:
    result = _baseline_replay(_sessions(), _bars())

    assert len(result.curve) == 104
    assert result.curve[0][1] == 10_000_000.0
    assert all(value > 0 for _, value in result.curve)


def test_replay_is_deterministic_for_the_same_ordered_input() -> None:
    first = _baseline_replay(_sessions(), _bars())
    second = _baseline_replay(_sessions(), _bars())

    assert first == second


def test_database_input_rejects_out_of_order_sessions() -> None:
    sessions = _sessions()
    value = {
        "sessions": [session.isoformat() for session in reversed(sessions)],
        "bars": [{"symbol": symbol, **row} for symbol, rows in _bars().items() for row in rows],
    }

    with pytest.raises(ScenarioMaterializationError, match="SCENARIO_BARS_NOT_EXACT_31_BY_104"):
        _bars_by_symbol(value)


def test_metrics_keep_undefined_ratios_null_instead_of_zero() -> None:
    result = ReplayResult([(session, 10_000_000.0) for session in _sessions()[:4]], [])

    metrics = _metrics(result)

    assert metrics["mdd"] == 0.0
    assert metrics["sharpe"] is None
    assert metrics["sortino"] is None
    assert metrics["winRate"] is None


def test_strict_replay_counts_and_blocks_an_order_above_the_saved_limit() -> None:
    sessions = _sessions()
    bars = _bars(symbols=1)
    symbol = next(iter(bars))
    trade = Trade(symbol, sessions[40], sessions[50], 100, 10_040, 10_050)
    rules = [
        {"ruleId": "max_position_per_asset", "enabled": True, "threshold": 1.0},
        {"ruleId": "max_gold_etf_etn_weight", "enabled": True, "threshold": 1.0},
        {"ruleId": "max_single_order_amount", "enabled": True, "threshold": 500_000},
        {"ruleId": "daily_loss_guard", "enabled": True, "threshold": -0.03},
        {"ruleId": "mdd_guard", "enabled": True, "threshold": -0.15},
        {"ruleId": "max_daily_orders", "enabled": True, "threshold": 3},
    ]

    result = _strict_replay(sessions, bars, [trade], rules)

    assert result.violation_count == 1
    assert result.trades == []
    assert result.curve[-1][1] == 10_000_000.0


def test_strict_replay_charges_exact_round_trip_35_bps() -> None:
    sessions = _sessions()
    bars = _bars(symbols=1)
    symbol = next(iter(bars))
    entry, exit = sessions[40], sessions[41]
    trade = Trade(
        symbol, entry, exit, 10, int(bars[symbol][40]["close"]), int(bars[symbol][40]["close"])
    )
    rules = [
        {"ruleId": "max_position_per_asset", "enabled": True, "threshold": 1.0},
        {"ruleId": "max_gold_etf_etn_weight", "enabled": True, "threshold": 1.0},
        {"ruleId": "max_single_order_amount", "enabled": True, "threshold": 10_000_000},
        {"ruleId": "daily_loss_guard", "enabled": True, "threshold": -1.0},
        {"ruleId": "mdd_guard", "enabled": True, "threshold": -1.0},
        {"ruleId": "max_daily_orders", "enabled": True, "threshold": 3},
    ]

    result = _strict_replay(sessions, bars, [trade], rules)
    notional = trade.quantity * trade.entry_price

    assert result.curve[-1][1] == pytest.approx(10_000_000 - notional * 0.0035)
