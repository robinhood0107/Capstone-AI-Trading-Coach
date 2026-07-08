from datetime import date

from app.data.kis.calendar import is_xkrx_trading_day


def test_xkrx_calendar_marks_weekend_as_non_trading_day() -> None:
    assert is_xkrx_trading_day(date(2026, 7, 11)) is False


def test_xkrx_calendar_marks_regular_weekday_as_trading_day() -> None:
    assert is_xkrx_trading_day(date(2026, 7, 8)) is True
