from datetime import date
import os
from pathlib import Path
from threading import Event, Thread

import pytest

from app.data.kis.parsers import DailyBar
from app.data.kis.storage import (
    KISConflictingDuplicateError,
    dataset_lock,
    missing_daily_ranges,
    upsert_daily_bars,
)


def test_parquet_upsert_is_idempotent_by_symbol_and_date(tmp_path: Path) -> None:
    mode_probe = tmp_path / "mode-probe"
    probe_fd = os.open(mode_probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(probe_fd)
    supports_posix_modes = mode_probe.stat().st_mode & 0o777 == 0o600
    mode_probe.unlink()
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
    assert second.exact_duplicate_rows == 1
    assert second.conflicting_duplicate_groups == 0
    assert (tmp_path / "daily" / "005930.parquet").exists()
    if supports_posix_modes:
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


def test_parquet_storage_rejects_symlink_in_ancestor_path(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(outside, target_is_directory=True)
    nested_data = alias / "nested-data"

    with pytest.raises(ValueError, match="symlink"):
        upsert_daily_bars(nested_data, "005930", [])

    assert not (outside / "nested-data" / "daily" / "005930.parquet").exists()


def test_conflicting_duplicate_fails_without_overwriting_existing_parquet(tmp_path: Path) -> None:
    first = DailyBar("005930", date(2026, 7, 8), 10, 12, 9, 11, 100)
    upsert_daily_bars(tmp_path, "005930", [first])
    path = tmp_path / "daily" / "005930.parquet"
    before = path.read_bytes()

    with pytest.raises(KISConflictingDuplicateError, match="conflicting"):
        upsert_daily_bars(
            tmp_path,
            "005930",
            [DailyBar("005930", date(2026, 7, 8), 10, 12, 9, 12, 100)],
        )

    assert path.read_bytes() == before


def test_incoming_symbol_mismatch_is_rejected_before_write(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="symbol"):
        upsert_daily_bars(
            tmp_path,
            "005930",
            [DailyBar("000660", date(2026, 7, 8), 10, 12, 9, 11, 100)],
        )

    assert not (tmp_path / "daily" / "005930.parquet").exists()


def test_exclusive_writer_blocks_shared_reader_until_release(tmp_path: Path) -> None:
    attempted = Event()
    acquired = Event()

    def reader() -> None:
        attempted.set()
        with dataset_lock(tmp_path, exclusive=False):
            acquired.set()

    with dataset_lock(tmp_path, exclusive=True):
        thread = Thread(target=reader)
        thread.start()
        assert attempted.wait(timeout=1)
        assert not acquired.wait(timeout=0.05)

    thread.join(timeout=1)
    assert acquired.is_set()
    assert (tmp_path / ".dataset.lock").stat().st_mode & 0o777 == 0o600
