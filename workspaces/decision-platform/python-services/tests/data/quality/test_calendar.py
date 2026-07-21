from datetime import UTC, date, datetime

from app.data.quality.kis_daily import completed_xkrx_sessions


def test_weekend_and_intraday_current_session_are_excluded_until_close() -> None:
    before_close = completed_xkrx_sessions(
        window_start=date(2026, 7, 17),
        window_end=date(2026, 7, 20),
        evaluated_at=datetime(2026, 7, 20, 1, 0, tzinfo=UTC),
    )
    after_close = completed_xkrx_sessions(
        window_start=date(2026, 7, 17),
        window_end=date(2026, 7, 20),
        evaluated_at=datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
    )

    assert before_close.sessions == (date(2026, 7, 17),)
    assert before_close.expected_last_completed_session == date(2026, 7, 17)
    assert after_close.sessions == (date(2026, 7, 17), date(2026, 7, 20))
    assert after_close.expected_last_completed_session == date(2026, 7, 20)
