"""Pure integer/Decimal Wilder ATR and monotonic trailing-stop kernel.

The module owns no database, provider, clock, or brokerage dependency.  Callers
must pass only adjusted, completed bars strictly before the evaluation session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_FLOOR, localcontext
from typing import Sequence


class AtrHistoryError(ValueError):
    """The supplied history cannot be used as an Automation V3 ATR input."""


@dataclass(frozen=True, slots=True)
class CompletedDailyBar:
    session_date: date
    open_price_krw: int
    high_price_krw: int
    low_price_krw: int
    close_price_krw: int
    adjusted: bool = True

    def __post_init__(self) -> None:
        if not self.adjusted:
            raise AtrHistoryError("ATR_REQUIRES_ADJUSTED_OHLC")
        if (
            min(
                self.open_price_krw,
                self.high_price_krw,
                self.low_price_krw,
                self.close_price_krw,
            )
            <= 0
        ):
            raise AtrHistoryError("ATR_OHLC_NONPOSITIVE")
        if not (
            self.low_price_krw
            <= min(self.open_price_krw, self.close_price_krw)
            <= max(self.open_price_krw, self.close_price_krw)
            <= self.high_price_krw
        ):
            raise AtrHistoryError("ATR_OHLC_INVARIANT")


@dataclass(frozen=True, slots=True)
class WilderAtr:
    as_of_session: date
    period: int
    value_krw: Decimal


@dataclass(frozen=True, slots=True)
class TrailingStopSnapshot:
    peak_price_krw: int
    trailing_stop_krw: int


def validate_completed_history(
    bars: Sequence[CompletedDailyBar],
    *,
    as_of_session: date,
    expected_sessions: Sequence[date] | None = None,
) -> tuple[CompletedDailyBar, ...]:
    """Validate ordering, completion boundary, and optional XKRX continuity.

    A missing left edge (new listing) or right edge (delisting) is allowed.
    Missing sessions between the first and last supplied bar are rejected.
    """

    normalized = tuple(bars)
    sessions = tuple(item.session_date for item in normalized)
    if sessions != tuple(sorted(sessions)) or len(sessions) != len(set(sessions)):
        raise AtrHistoryError("ATR_HISTORY_DUPLICATE_OR_UNORDERED")
    if any(item.session_date >= as_of_session for item in normalized):
        raise AtrHistoryError("ATR_HISTORY_FUTURE_OR_INCOMPLETE")
    if expected_sessions is not None:
        expected = tuple(item for item in expected_sessions if item < as_of_session)
        if len(normalized) > len(expected):
            raise AtrHistoryError("ATR_HISTORY_MIDDLE_GAP")
        if normalized:
            try:
                start = expected.index(sessions[0])
            except ValueError as error:
                raise AtrHistoryError("ATR_HISTORY_MIDDLE_GAP") from error
            if sessions != expected[start : start + len(sessions)]:
                raise AtrHistoryError("ATR_HISTORY_MIDDLE_GAP")
    return normalized


def true_range(current: CompletedDailyBar, previous_close_krw: int) -> int:
    if previous_close_krw <= 0:
        raise AtrHistoryError("ATR_PREVIOUS_CLOSE_INVALID")
    return max(
        current.high_price_krw - current.low_price_krw,
        abs(current.high_price_krw - previous_close_krw),
        abs(current.low_price_krw - previous_close_krw),
    )


def wilder_atr(
    bars: Sequence[CompletedDailyBar],
    *,
    period: int,
    as_of_session: date,
    expected_sessions: Sequence[date] | None = None,
) -> WilderAtr:
    if period not in range(5, 101):
        raise AtrHistoryError("ATR_PERIOD_INVALID")
    normalized = validate_completed_history(
        bars,
        as_of_session=as_of_session,
        expected_sessions=expected_sessions,
    )
    if len(normalized) < period + 1:
        raise AtrHistoryError("ATR_HISTORY_INSUFFICIENT")
    ranges = tuple(
        true_range(normalized[index], normalized[index - 1].close_price_krw)
        for index in range(1, len(normalized))
    )
    with localcontext() as context:
        context.prec = 50
        value = Decimal(sum(ranges[:period])) / Decimal(period)
        for item in ranges[period:]:
            value = (value * Decimal(period - 1) + Decimal(item)) / Decimal(period)
    return WilderAtr(normalized[-1].session_date, period, value)


def advance_trailing_stop(
    *,
    previous_peak_price_krw: int,
    completed_high_price_krw: int,
    current_quote_price_krw: int,
    atr_value_krw: Decimal,
    atr_multiplier_milli: int,
    previous_trailing_stop_krw: int | None,
) -> TrailingStopSnapshot:
    if min(previous_peak_price_krw, completed_high_price_krw, current_quote_price_krw) <= 0:
        raise AtrHistoryError("ATR_PEAK_INPUT_INVALID")
    if atr_value_krw < 0 or not atr_value_krw.is_finite():
        raise AtrHistoryError("ATR_VALUE_INVALID")
    if atr_multiplier_milli not in range(1_000, 10_001, 100):
        raise AtrHistoryError("ATR_MULTIPLIER_INVALID")
    if previous_trailing_stop_krw is not None and previous_trailing_stop_krw <= 0:
        raise AtrHistoryError("ATR_TRAILING_STOP_INVALID")
    peak = max(
        previous_peak_price_krw,
        completed_high_price_krw,
        current_quote_price_krw,
    )
    with localcontext() as context:
        context.prec = 50
        raw = Decimal(peak) - (atr_value_krw * Decimal(atr_multiplier_milli) / Decimal(1_000))
        calculated = max(1, int(raw.to_integral_value(rounding=ROUND_FLOOR)))
    return TrailingStopSnapshot(
        peak_price_krw=peak,
        trailing_stop_krw=max(previous_trailing_stop_krw or 1, calculated),
    )
