from datetime import date
from pathlib import Path

from app.data.kis.parsers import DailyBar
from app.data.kis.storage import upsert_daily_bars


def test_parquet_upsert_is_idempotent_by_symbol_and_date(tmp_path: Path) -> None:
    first_rows = [
        DailyBar("005930", date(2026, 7, 8), 72800, 73900, 72400, 73500, 12123456, 889000000000),
        DailyBar("005930", date(2026, 7, 7), 72000, 73000, 71800, 72700, 10101010, 735000000000),
    ]
    second_rows = [
        DailyBar("005930", date(2026, 7, 8), 72800, 73900, 72400, 73500, 12123456, 889000000000),
        DailyBar("005930", date(2026, 7, 6), 73100, 73300, 72500, 72900, 9090909, 662000000000),
    ]

    first = upsert_daily_bars(tmp_path, "005930", first_rows)
    second = upsert_daily_bars(tmp_path, "005930", second_rows)

    assert first.total_rows == 2
    assert second.total_rows == 3
    assert second.inserted_rows == 1
    assert (tmp_path / "daily" / "005930.parquet").exists()
