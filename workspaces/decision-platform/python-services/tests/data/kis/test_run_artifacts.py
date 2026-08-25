import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.kis.accounting import CollectionRunRecorder, CollectionRunStatus
from app.data.kis.parsers import DailyBar
from app.data.kis.run_artifacts import (
    ArtifactReference,
    KISRunArtifactError,
    build_dataset_manifest,
    inventory_daily_dataset,
    publish_collection_summary,
    publish_successful_dataset_manifest,
    reference_input_artifact,
)
from app.data.kis.storage import upsert_daily_bars

RUN_ID = UUID("123e4567-e89b-42d3-a456-426614174000")
STARTED_AT = datetime(2026, 7, 21, 1, 0, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 7, 21, 1, 1, tzinfo=UTC)


def _summary():
    return CollectionRunRecorder(run_id=RUN_ID, started_at=STARTED_AT).snapshot(
        completed_at=COMPLETED_AT,
        status=CollectionRunStatus.SUCCESS,
    )


def _prepare_inputs(root: Path):
    universe_path = root / "universe_manifest.json"
    universe_path.parent.mkdir(parents=True, exist_ok=True)
    universe_path.write_bytes(canonical_json_bytes({"schemaVersion": 1, "symbols": ["005930"]}))
    os.chmod(universe_path, 0o600)
    upsert_daily_bars(
        root,
        "005930",
        [DailyBar("005930", date(2026, 7, 21), 10, 12, 9, 11, 100)],
    )
    return universe_path


def test_collection_summary_is_immutable_canonical_private_and_path_sanitized(
    tmp_path: Path,
) -> None:
    published = publish_collection_summary(tmp_path, _summary())

    assert published.identifier == (
        "collection-runs/2026/07/21/123e4567-e89b-42d3-a456-426614174000/summary.json"
    )
    assert published.path.read_bytes() == canonical_json_bytes(
        _summary().model_dump(mode="json", by_alias=True)
    )
    assert published.path.stat().st_mode & 0o777 == 0o600
    assert str(tmp_path).encode() not in published.path.read_bytes()
    with pytest.raises(KISRunArtifactError, match="exists"):
        publish_collection_summary(tmp_path, _summary())


def test_dataset_manifest_owns_exact_inventory_and_is_published_before_latest(
    tmp_path: Path,
) -> None:
    universe_path = _prepare_inputs(tmp_path)
    summary = publish_collection_summary(tmp_path, _summary())
    universe = reference_input_artifact(tmp_path, "universe_manifest.json")
    files = inventory_daily_dataset(tmp_path, ("005930",))
    manifest = build_dataset_manifest(
        dataset_manifest_id=RUN_ID,
        created_at=COMPLETED_AT,
        adjustment_mode="ADJUSTED",
        universe_manifest=universe,
        collection_run=summary.reference,
        files=files,
    )

    published = publish_successful_dataset_manifest(tmp_path, manifest)

    assert universe_path.exists()
    assert published.identifier.endswith("/manifest.json")
    assert published.path.stat().st_mode & 0o777 == 0o600
    payload = json.loads(published.path.read_text(encoding="utf-8"))
    assert payload["files"][0]["path"] == "daily/005930.parquet"
    assert payload["files"][0]["sha256"] == files[0].sha256
    latest = json.loads((tmp_path / "datasets" / "latest-success-manifest.json").read_text())
    assert latest["datasetManifest"]["identifier"] == published.identifier
    assert latest["datasetManifest"]["sha256"] == published.sha256


def test_manifest_hash_mismatch_does_not_replace_last_good_pointer(tmp_path: Path) -> None:
    _prepare_inputs(tmp_path)
    summary = publish_collection_summary(tmp_path, _summary())
    universe = reference_input_artifact(tmp_path, "universe_manifest.json")
    files = inventory_daily_dataset(tmp_path, ("005930",))
    first = build_dataset_manifest(
        dataset_manifest_id=RUN_ID,
        created_at=COMPLETED_AT,
        adjustment_mode="ADJUSTED",
        universe_manifest=universe,
        collection_run=summary.reference,
        files=files,
    )
    publish_successful_dataset_manifest(tmp_path, first)
    latest_path = tmp_path / "datasets" / "latest-success-manifest.json"
    last_good = latest_path.read_bytes()
    upsert_daily_bars(
        tmp_path,
        "005930",
        [DailyBar("005930", date(2026, 7, 22), 11, 13, 10, 12, 110)],
    )
    second = build_dataset_manifest(
        dataset_manifest_id=UUID("123e4567-e89b-42d3-a456-426614174001"),
        created_at=datetime(2026, 7, 22, 1, 1, tzinfo=UTC),
        adjustment_mode="ADJUSTED",
        universe_manifest=universe,
        collection_run=summary.reference,
        files=files,
    )

    with pytest.raises(KISRunArtifactError, match="hash"):
        publish_successful_dataset_manifest(tmp_path, second)

    assert latest_path.read_bytes() == last_good
    assert not (
        tmp_path
        / "datasets"
        / "2026"
        / "07"
        / "22"
        / "123e4567-e89b-42d3-a456-426614174001"
        / "manifest.json"
    ).exists()


def test_inventory_rejects_hardlinked_parquet(tmp_path: Path) -> None:
    _prepare_inputs(tmp_path)
    os.link(
        tmp_path / "daily" / "005930.parquet",
        tmp_path / "daily" / "alias.parquet",
    )

    with pytest.raises(KISRunArtifactError, match="link"):
        inventory_daily_dataset(tmp_path, ("005930",))


def test_artifact_reference_rejects_dot_segment_alias() -> None:
    with pytest.raises(ValidationError):
        ArtifactReference(identifier="collection-runs/./summary.json", sha256="a" * 64)


def test_dataset_manifest_rejects_duplicate_symbol_inventory(tmp_path: Path) -> None:
    _prepare_inputs(tmp_path)
    summary = publish_collection_summary(tmp_path, _summary())
    universe = reference_input_artifact(tmp_path, "universe_manifest.json")
    files = inventory_daily_dataset(tmp_path, ("005930",))

    with pytest.raises(ValidationError, match="unique"):
        build_dataset_manifest(
            dataset_manifest_id=RUN_ID,
            created_at=COMPLETED_AT,
            adjustment_mode="ADJUSTED",
            universe_manifest=universe,
            collection_run=summary.reference,
            files=(files[0], files[0]),
        )
