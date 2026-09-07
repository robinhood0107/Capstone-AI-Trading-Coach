from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.p1_owner.scenario_materializer import (
    ReplayResult,
    ScenarioMaterializationError,
    Trade,
    _baseline_replay,
    _bars_by_symbol,
    _curve_rows,
    _evaluation_sessions,
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

    with pytest.raises(
        ScenarioMaterializationError, match="SCENARIO_BARS_NOT_EXACT_31_BY_EVERY_SESSION"
    ):
        _bars_by_symbol(value)


def test_context_may_grow_beyond_the_demo_day_but_never_shrink() -> None:
    """세션 수는 하한으로만 본다.

    104 는 이 코드를 쓸 때 실제로 있던 세션 수였을 뿐 계약이 아니다. 등식으로 두면 거래일이
    하나 늘 때마다 백테스트 적재가 거부되고, 그것이 리포트가 자라지 않던 이유다. 반대로
    줄어드는 것은 자란 것이 아니라 잃은 것이므로 계속 막는다.
    """

    def payload(count: int) -> dict[str, object]:
        sessions = _sessions(count)
        return {
            "sessions": [session.isoformat() for session in sessions],
            "bars": [
                {
                    "symbol": f"{index:06d}",
                    "sessionDate": session.isoformat(),
                    "open": 10_000,
                    "high": 10_100,
                    "low": 9_900,
                    "close": 10_000,
                    "volume": 1_000_000,
                }
                for index in range(1, 32)
                for session in sessions
            ],
        }

    grown, _ = _bars_by_symbol(payload(140))
    assert len(grown) == 140

    with pytest.raises(
        ScenarioMaterializationError, match="SCENARIO_BARS_NOT_EXACT_31_BY_EVERY_SESSION"
    ):
        _bars_by_symbol(payload(103))


def test_evaluation_end_must_be_the_newest_context_session() -> None:
    """상한이 문맥의 마지막 세션이 아니면 거부한다.

    이것이 곡선을 거래일마다 하나씩 자라게 하는 성질이다. 상한을 리터럴로 두면 새 세션이
    적재돼도 평가 구간이 그대로여서 리포트가 멈춘다.
    """

    context = [date(2026, 5, 19) + timedelta(days=index) for index in range(91)] + [
        date(2026, 8, 18) + timedelta(days=index) for index in range(20)
    ]

    with pytest.raises(ScenarioMaterializationError, match="SCENARIO_EVALUATION_WINDOW_INVALID"):
        _evaluation_sessions(
            context,
            {"evaluationStart": "2026-08-18", "evaluationEnd": "2026-09-03"},
        )

    selected = _evaluation_sessions(
        context,
        {"evaluationStart": "2026-08-18", "evaluationEnd": context[-1].isoformat()},
    )
    assert selected[0] == date(2026, 8, 18)
    assert selected[-1] == context[-1]
    assert len(selected) == 20


def test_metrics_keep_undefined_ratios_null_instead_of_zero() -> None:
    result = ReplayResult([(session, 10_000_000.0) for session in _sessions()[:4]], [])

    metrics = _metrics(result)

    assert metrics["mdd"] == 0.0
    assert metrics["sharpe"] is None
    assert metrics["sortino"] is None
    assert metrics["winRate"] is None


def test_metrics_use_initial_capital_before_the_first_visible_session() -> None:
    metrics = _metrics(ReplayResult([(date(2026, 8, 18), 9_000_000.0)], []))

    assert metrics["netReturn"] == pytest.approx(-0.1)
    assert metrics["mdd"] == pytest.approx(-0.1)


def test_demo_window_starts_on_the_first_session_after_the_substitute_holiday() -> None:
    evaluation = [
        date(2026, 8, 18),
        date(2026, 8, 19),
        date(2026, 8, 20),
        date(2026, 8, 21),
        date(2026, 8, 24),
        date(2026, 8, 25),
        date(2026, 8, 26),
        date(2026, 8, 27),
        date(2026, 8, 28),
        date(2026, 8, 31),
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
    ]
    context = [date(2026, 5, 19) + timedelta(days=index) for index in range(91)] + evaluation

    selected = _evaluation_sessions(
        context,
        {"evaluationStart": "2026-08-18", "evaluationEnd": "2026-09-03"},
    )

    assert selected == evaluation
    assert _curve_rows(ReplayResult([(selected[0], 10_000_000.0)], []))[0]["at"] == (
        "2026-08-18T06:30:00Z"
    )


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
