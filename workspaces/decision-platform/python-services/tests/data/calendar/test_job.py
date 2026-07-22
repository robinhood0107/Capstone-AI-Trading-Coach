from datetime import UTC, datetime

from app.data.calendar.job import kst_usage_date


def test_quota_usage_date_rolls_over_at_kst_midnight() -> None:
    assert kst_usage_date(datetime(2026, 7, 21, 14, 59, 59, tzinfo=UTC)).isoformat() == "2026-07-21"
    assert kst_usage_date(datetime(2026, 7, 21, 15, 0, 0, tzinfo=UTC)).isoformat() == "2026-07-22"
