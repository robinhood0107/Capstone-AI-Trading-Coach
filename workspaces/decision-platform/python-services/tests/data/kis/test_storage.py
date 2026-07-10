from datetime import date
import os
from pathlib import Path

import pytest

from app.data.kis.parsers import DailyBar
from app.data.kis.storage import missing_daily_ranges, upsert_daily_bars


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
    assert (tmp_path / "daily" / "005930.parquet").stat().st_mode & 0o777 == 0o600


def test_parquet_storage_rejects_symbol_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="six digits"):
        upsert_daily_bars(tmp_path / "data", "../../escaped", [])

    assert not (tmp_path / "escaped.parquet").exists()


def test_existing_parquet_limits_backfill_to_missing_edges(tmp_path: Path) -> None:
    upsert_daily_bars(
        tmp_path,
        "005930",
        [
            DailyBar("005930", date(2026, 7, 7), 1, 1, 1, 1, 1),
            DailyBar("005930", date(2026, 7, 8), 1, 1, 1, 1, 1),
        ],
    )

    assert missing_daily_ranges(tmp_path, "005930", date(2026, 7, 6), date(2026, 7, 9)) == [
        (date(2026, 7, 6), date(2026, 7, 6)),
        (date(2026, 7, 9), date(2026, 7, 9)),
    ]
    assert missing_daily_ranges(tmp_path, "005930", date(2026, 7, 7), date(2026, 7, 8)) == []


def test_parquet_storage_rejects_final_symlink_without_touching_target(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    daily_dir = data_dir / "daily"
    daily_dir.mkdir(parents=True)
    target = tmp_path / "outside.parquet"
    target.write_bytes(b"do-not-touch")
    (daily_dir / "005930.parquet").symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        upsert_daily_bars(
            data_dir,
            "005930",
            [DailyBar("005930", date(2026, 7, 8), 1, 1, 1, 1, 1)],
        )
    with pytest.raises(ValueError, match="symlink"):
        missing_daily_ranges(data_dir, "005930", date(2026, 7, 8), date(2026, 7, 8))

    assert target.read_bytes() == b"do-not-touch"


def test_parquet_storage_rejects_symlink_root(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        upsert_daily_bars(linked_root, "005930", [])

    assert not os.path.lexists(real_root / "daily" / "005930.parquet")
