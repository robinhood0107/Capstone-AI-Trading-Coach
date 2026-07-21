from datetime import UTC, date, datetime
import os
from pathlib import Path
from threading import Event, Thread

import pytest

from app.data.kis.parsers import DailyBar
from app.data.kis.storage import dataset_lock, upsert_daily_bars
from app.data.quality.kis_daily import (
    KISQualityInputError,
    QualityReadLimits,
    load_quality_snapshot,
)
from tests.data.quality.helpers import prepare_snapshot


EVALUATED_AT = datetime(2026, 7, 21, 7, tzinfo=UTC)


def _load(root: Path, *, limits: QualityReadLimits | None = None, collection: bool = True):
    identifiers = prepare_snapshot(root)
    return load_quality_snapshot(
        root=root,
        universe_identifier=identifiers.universe,
        dataset_identifier=identifiers.dataset,
        collection_identifier=identifiers.collection if collection else None,
        window_start=date(2026, 7, 21),
        window_end=date(2026, 7, 21),
        evaluated_at=EVALUATED_AT,
        software_revision="7131f695293472ea16ee05322ed9b05f7b69d129",
        limits=limits or QualityReadLimits(),
    )


def test_reader_verifies_manifest_hash_inventory_and_projects_symbol_batches(
    posix_tmp_path: Path,
) -> None:
    snapshot = _load(posix_tmp_path)

    assert snapshot.context.universe_symbols == ("005930",)
    assert snapshot.context.collection_summary is not None
    assert snapshot.context.dataset_file_count == 1
    assert snapshot.context.sessions == (date(2026, 7, 21),)
    assert len(snapshot.datasets) == 1
    assert snapshot.datasets[0].symbol == "005930"
    assert len(snapshot.datasets[0].rows) == 1


def test_reader_fails_closed_when_parquet_changes_after_success_manifest(
    posix_tmp_path: Path,
) -> None:
    identifiers = prepare_snapshot(posix_tmp_path)
    upsert_daily_bars(
        posix_tmp_path,
        "005930",
        [DailyBar("005930", date(2026, 7, 20), 90, 95, 85, 91, 900, 90_000)],
    )

    with pytest.raises(KISQualityInputError, match="dataset inventory"):
        load_quality_snapshot(
            root=posix_tmp_path,
            universe_identifier=identifiers.universe,
            dataset_identifier=identifiers.dataset,
            collection_identifier=identifiers.collection,
            window_start=date(2026, 7, 21),
            window_end=date(2026, 7, 21),
            evaluated_at=EVALUATED_AT,
            software_revision="7131f695293472ea16ee05322ed9b05f7b69d129",
        )


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_reader_rejects_non_regular_or_linked_manifest(
    posix_tmp_path: Path,
    kind: str,
) -> None:
    identifiers = prepare_snapshot(posix_tmp_path)
    source = posix_tmp_path / identifiers.universe
    unsafe = posix_tmp_path / "unsafe-universe.json"
    if kind == "symlink":
        unsafe.symlink_to(source)
    elif kind == "hardlink":
        os.link(source, unsafe)
    else:
        os.mkfifo(unsafe)

    with pytest.raises(KISQualityInputError, match="input artifact"):
        load_quality_snapshot(
            root=posix_tmp_path,
            universe_identifier="unsafe-universe.json",
            dataset_identifier=identifiers.dataset,
            collection_identifier=identifiers.collection,
            window_start=date(2026, 7, 21),
            window_end=date(2026, 7, 21),
            evaluated_at=EVALUATED_AT,
            software_revision="7131f695293472ea16ee05322ed9b05f7b69d129",
        )


def test_reader_enforces_lower_only_file_row_and_byte_caps(posix_tmp_path: Path) -> None:
    identifiers = prepare_snapshot(posix_tmp_path)
    common = dict(
        root=posix_tmp_path,
        universe_identifier=identifiers.universe,
        dataset_identifier=identifiers.dataset,
        collection_identifier=identifiers.collection,
        window_start=date(2026, 7, 21),
        window_end=date(2026, 7, 21),
        evaluated_at=EVALUATED_AT,
        software_revision="7131f695293472ea16ee05322ed9b05f7b69d129",
    )

    with pytest.raises(KISQualityInputError, match="byte limit"):
        load_quality_snapshot(**common, limits=QualityReadLimits(max_file_bytes=64))
    with pytest.raises(ValueError, match="hard cap"):
        QualityReadLimits(max_rows=2_000_001)


def test_reader_holds_shared_lock_until_snapshot_bytes_are_consumed(
    posix_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifiers = prepare_snapshot(posix_tmp_path)
    entered = Event()
    release = Event()
    completed = Event()

    from app.data.quality import kis_daily

    original = kis_daily._read_parquet_dataset

    def blocking_reader(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=2)
        return original(*args, **kwargs)

    monkeypatch.setattr(kis_daily, "_read_parquet_dataset", blocking_reader)

    def consume() -> None:
        load_quality_snapshot(
            root=posix_tmp_path,
            universe_identifier=identifiers.universe,
            dataset_identifier=identifiers.dataset,
            collection_identifier=identifiers.collection,
            window_start=date(2026, 7, 21),
            window_end=date(2026, 7, 21),
            evaluated_at=EVALUATED_AT,
            software_revision="7131f695293472ea16ee05322ed9b05f7b69d129",
        )
        completed.set()

    thread = Thread(target=consume)
    thread.start()
    assert entered.wait(timeout=1)
    writer_acquired = Event()

    def writer() -> None:
        with dataset_lock(posix_tmp_path, exclusive=True):
            writer_acquired.set()

    writer_thread = Thread(target=writer)
    writer_thread.start()
    assert not writer_acquired.wait(timeout=0.05)
    release.set()
    thread.join(timeout=2)
    writer_thread.join(timeout=2)
    assert completed.is_set() and writer_acquired.is_set()

