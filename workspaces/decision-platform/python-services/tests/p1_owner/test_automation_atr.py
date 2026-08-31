from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.p1_owner.automation_atr import (
    AtrHistoryError,
    CompletedDailyBar,
    advance_trailing_stop,
    true_range,
    validate_completed_history,
    wilder_atr,
)


def _bar(
    day: int, *, high: int, low: int, close: int, open_: int | None = None
) -> CompletedDailyBar:
    return CompletedDailyBar(date(2026, 8, day), open_ or close, high, low, close)


def test_wilder_initial_average_gap_true_range_and_recursive_update_are_exact() -> None:
    bars = (
        _bar(3, high=100, low=95, close=98),
        _bar(4, high=110, low=103, close=108, open_=105),
        _bar(5, high=112, low=107, close=111, open_=108),
        _bar(6, high=115, low=109, close=110, open_=111),
        _bar(7, high=118, low=112, close=116, open_=113),
        _bar(10, high=121, low=114, close=120, open_=117),
        _bar(11, high=125, low=119, close=124, open_=120),
    )
    assert true_range(bars[1], bars[0].close_price_krw) == 12
    result = wilder_atr(bars, period=5, as_of_session=date(2026, 8, 12))
    ranges = [12, 5, 6, 8, 7, 6]
    expected = (Decimal(sum(ranges[:5])) / 5 * 4 + ranges[5]) / 5
    assert result.value_krw == expected
    assert result.as_of_session == date(2026, 8, 11)


def test_history_rejects_duplicate_future_mid_gap_and_bad_ohlc() -> None:
    valid = _bar(3, high=105, low=95, close=100)
    with pytest.raises(AtrHistoryError, match="DUPLICATE_OR_UNORDERED"):
        validate_completed_history((valid, valid), as_of_session=date(2026, 8, 4))
    with pytest.raises(AtrHistoryError, match="FUTURE_OR_INCOMPLETE"):
        validate_completed_history((valid,), as_of_session=date(2026, 8, 3))
    with pytest.raises(AtrHistoryError, match="MIDDLE_GAP"):
        validate_completed_history(
            (valid, _bar(5, high=106, low=96, close=101)),
            as_of_session=date(2026, 8, 6),
            expected_sessions=(date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)),
        )
    with pytest.raises(AtrHistoryError, match="OHLC_INVARIANT"):
        CompletedDailyBar(date(2026, 8, 3), 100, 99, 95, 100)


def test_history_allows_contiguous_listing_and_delisting_edge_trims() -> None:
    expected = tuple(date(2026, 8, day) for day in range(3, 8))
    left_trimmed = tuple(_bar(day, high=105, low=95, close=100) for day in range(5, 8))
    right_trimmed = tuple(_bar(day, high=105, low=95, close=100) for day in range(3, 6))

    assert (
        validate_completed_history(
            left_trimmed,
            as_of_session=date(2026, 8, 8),
            expected_sessions=expected,
        )
        == left_trimmed
    )
    assert (
        validate_completed_history(
            right_trimmed,
            as_of_session=date(2026, 8, 8),
            expected_sessions=expected,
        )
        == right_trimmed
    )


def test_trailing_peak_and_stop_never_decrease_and_floor_decimal_threshold() -> None:
    first = advance_trailing_stop(
        previous_peak_price_krw=100,
        completed_high_price_krw=120,
        current_quote_price_krw=115,
        atr_value_krw=Decimal("7.25"),
        atr_multiplier_milli=3_000,
        previous_trailing_stop_krw=None,
    )
    assert first.peak_price_krw == 120
    assert first.trailing_stop_krw == 98
    second = advance_trailing_stop(
        previous_peak_price_krw=first.peak_price_krw,
        completed_high_price_krw=118,
        current_quote_price_krw=110,
        atr_value_krw=Decimal("10"),
        atr_multiplier_milli=3_000,
        previous_trailing_stop_krw=first.trailing_stop_krw,
    )
    assert second.peak_price_krw == 120
    assert second.trailing_stop_krw == first.trailing_stop_krw
