from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path
from typing import Literal

import pytest

from app.data import source_snapshot_retention_cli
from app.data._shared.canonical_json import canonical_json_bytes

Source = Literal["ecos", "naver"]
_RUN_DATE = date(2026, 7, 14)


def _snapshot_payload(source: Source, as_of: date) -> dict[str, object]:
    if source == "ecos":
        return {
            "schemaVersion": 1,
            "source": "ecos",
            "asOf": as_of.isoformat(),
            "retrievedAt": f"{as_of.isoformat()}T00:00:00Z",
            "registryVersion": "synthetic-v1",
            "registryVerifiedAt": f"{as_of.isoformat()}T00:00:00Z",
            "series": [
                {
                    "seriesId": "synthetic-rate",
                    "statCode": "SYNTH001",
                    "itemCode1": "RATE01",
                    "cycle": "D",
                    "name": "Synthetic rate",
                    "unit": "percent",
                    "requestedFrom": as_of.strftime("%Y%m%d"),
                    "requestedTo": as_of.strftime("%Y%m%d"),
                    "status": "empty",
                    "observations": [],
                },
                {
                    "seriesId": "synthetic-fx",
                    "statCode": "SYNTH002",
                    "itemCode1": "FX01",
                    "cycle": "D",
                    "name": "Synthetic FX",
                    "unit": "KRW",
                    "requestedFrom": as_of.strftime("%Y%m%d"),
                    "requestedTo": as_of.strftime("%Y%m%d"),
                    "status": "empty",
                    "observations": [],
                },
            ],
            "partial": False,
            "coverage": "empty",
        }
    empty_queries = [
        {
            "rank": rank,
            "symbol": f"{rank:06d}",
            "query": f"Synthetic Company {rank}",
            "status": "empty",
            "providerTotal": 0,
            "requestedDisplay": 10,
            "providerDisplay": 0,
            "receivedCount": 0,
            "acceptedCount": 0,
            "filteredCount": 0,
            "redactedUrlCount": 0,
            "items": [],
        }
        for rank in range(1, 5)
    ]
    return {
        "schemaVersion": 1,
        "source": "naver",
        "providerProfile": "naver-legacy",
        "asOf": as_of.isoformat(),
        "retrievedAt": f"{as_of.isoformat()}T00:00:00Z",
        "universeManifestSha256": "a" * 64,
        "universeAsOfDate": as_of.isoformat(),
        "batchCursor": 0,
        "nextBatchCursor": 4,
        "queries": empty_queries,
        "partial": False,
        "coverage": "empty",
        "deferredQueries": [],
    }


def _manifest_payload(
    *,
    source: Source,
    as_of: date,
    snapshot_path: str,
    snapshot_sha256: str,
    retention_days: int | None = None,
) -> dict[str, object]:
    if source == "ecos":
        return {
            "schemaVersion": 1,
            "source": "ecos",
            "providerProfile": "ecos",
            "operation": "ecos-macro-collect",
            "generatedAt": f"{as_of.isoformat()}T00:00:01Z",
            "asOf": as_of.isoformat(),
            "snapshotPath": snapshot_path,
            "snapshotSha256": snapshot_sha256,
            "recordCount": 0,
            "countBreakdown": {
                "seriesCount": 2,
                "observationCount": 0,
                "duplicateCount": 0,
            },
            "partial": False,
            "coverage": "empty",
            "deferredQueries": 0,
            "physicalAttemptCount": 0,
            "quotaPolicyVersion": "synthetic-ecos-v1",
            "provenance": {
                "documentationUrl": "https://ecos.bok.or.kr/api/",
                "policyUrl": "https://ecos.bok.or.kr/api/",
            },
            "sanitizationVersion": "synthetic-v1",
            "retentionDays": 365 if retention_days is None else retention_days,
            "deleteOwner": "decision-platform:source-snapshot-retention",
        }
    return {
        "schemaVersion": 1,
        "source": "naver",
        "providerProfile": "naver-legacy",
        "operation": "naver-news-metadata-collect",
        "generatedAt": f"{as_of.isoformat()}T00:00:01Z",
        "asOf": as_of.isoformat(),
        "snapshotPath": snapshot_path,
        "snapshotSha256": snapshot_sha256,
        "recordCount": 0,
        "countBreakdown": {
            "queryCount": 4,
            "acceptedItemCount": 0,
            "filteredItemCount": 0,
            "redactedUrlCount": 0,
        },
        "partial": False,
        "coverage": "empty",
        "deferredQueries": 0,
        "physicalAttemptCount": 0,
        "quotaPolicyVersion": "synthetic-naver-v1",
        "provenance": {
            "documentationUrl": "https://developers.naver.com/docs/serviceapi/search/news/news.md",
            "policyUrl": "https://developers.naver.com/products/terms/",
        },
        "sanitizationVersion": "synthetic-v1",
        "retentionDays": 30 if retention_days is None else retention_days,
        "deleteOwner": "decision-platform:source-snapshot-retention",
    }


def _write_artifact(
    root: Path,
    *,
    source: Source,
    as_of: date,
    sequence: int,
    invalid_hash: bool = False,
    retention_days: int | None = None,
    invalid_snapshot_contract: bool = False,
    noncanonical_snapshot: bool = False,
) -> Path:
    run_id = f"00000000-0000-4000-8000-{sequence:012x}"
    relative_snapshot = f"{source}/{as_of:%Y/%m/%d}/{run_id}/snapshot.json"
    leaf = root / Path(relative_snapshot).parent
    leaf.mkdir(parents=True)
    snapshot_payload = _snapshot_payload(source, as_of)
    if invalid_snapshot_contract:
        snapshot_payload.pop("series" if source == "ecos" else "queries")
    snapshot_bytes = canonical_json_bytes(snapshot_payload)
    if noncanonical_snapshot:
        snapshot_bytes = (
            json.dumps(snapshot_payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        )
    digest = hashlib.sha256(snapshot_bytes).hexdigest()
    manifest = _manifest_payload(
        source=source,
        as_of=as_of,
        snapshot_path=relative_snapshot,
        snapshot_sha256="0" * 64 if invalid_hash else digest,
        retention_days=retention_days,
    )
    (leaf / "snapshot.json").write_bytes(snapshot_bytes)
    (leaf / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    return leaf


def _run(root: Path, *, apply: bool) -> int:
    argv = ["--root", str(root), "--as-of", _RUN_DATE.isoformat()]
    if apply:
        argv.append("--apply")
    return source_snapshot_retention_cli.main(argv)


def test_cli_defaults_to_dry_run_and_apply_uses_source_retention_windows(tmp_path: Path) -> None:
    root = tmp_path / "source_snapshots"
    ecos_expired = _write_artifact(root, source="ecos", as_of=date(2025, 7, 13), sequence=1)
    ecos_retained = _write_artifact(root, source="ecos", as_of=date(2025, 7, 15), sequence=2)
    naver_expired = _write_artifact(root, source="naver", as_of=date(2026, 6, 13), sequence=3)
    naver_retained = _write_artifact(root, source="naver", as_of=date(2026, 6, 15), sequence=4)

    assert _run(root, apply=False) == 0
    assert all(
        (leaf / filename).exists()
        for leaf in (ecos_expired, ecos_retained, naver_expired, naver_retained)
        for filename in ("manifest.json", "snapshot.json")
    )

    assert _run(root, apply=True) == 0
    assert not (ecos_expired / "manifest.json").exists()
    assert not (ecos_expired / "snapshot.json").exists()
    assert not (naver_expired / "manifest.json").exists()
    assert not (naver_expired / "snapshot.json").exists()
    assert (ecos_retained / "manifest.json").exists()
    assert (ecos_retained / "snapshot.json").exists()
    assert (naver_retained / "manifest.json").exists()
    assert (naver_retained / "snapshot.json").exists()


def test_apply_deletes_manifest_first_and_never_recursively_deletes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source_snapshots"
    leaf = _write_artifact(root, source="ecos", as_of=date(2025, 7, 13), sequence=10)
    sentinel = leaf / "operator-note.keep"
    sentinel.write_text("do-not-delete", encoding="utf-8")
    unlink_calls: list[str] = []
    real_unlink = os.unlink

    def recording_unlink(path: object, *args: object, **kwargs: object) -> None:
        unlink_calls.append(Path(os.fsdecode(path)).name)
        real_unlink(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "unlink", recording_unlink)

    assert _run(root, apply=True) == 0
    assert unlink_calls.index("manifest.json") < unlink_calls.index("snapshot.json")
    assert sentinel.read_text(encoding="utf-8") == "do-not-delete"
    assert leaf.exists()
    assert not (leaf / "manifest.json").exists()
    assert not (leaf / "snapshot.json").exists()


def test_invalid_and_incomplete_artifacts_are_skipped_without_partial_deletion(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source_snapshots"
    invalid_hash = _write_artifact(
        root,
        source="ecos",
        as_of=date(2025, 7, 13),
        sequence=20,
        invalid_hash=True,
    )
    invalid_retention = _write_artifact(
        root,
        source="naver",
        as_of=date(2026, 6, 13),
        sequence=21,
        retention_days=365,
    )
    invalid_snapshot_contract = _write_artifact(
        root,
        source="ecos",
        as_of=date(2025, 7, 13),
        sequence=24,
        invalid_snapshot_contract=True,
    )
    invalid_naver_snapshot_contract = _write_artifact(
        root,
        source="naver",
        as_of=date(2026, 6, 13),
        sequence=26,
        invalid_snapshot_contract=True,
    )
    noncanonical_snapshot = _write_artifact(
        root,
        source="naver",
        as_of=date(2026, 6, 13),
        sequence=25,
        noncanonical_snapshot=True,
    )
    noncanonical_ecos_snapshot = _write_artifact(
        root,
        source="ecos",
        as_of=date(2025, 7, 13),
        sequence=27,
        noncanonical_snapshot=True,
    )
    orphan_snapshot = _write_artifact(root, source="ecos", as_of=date(2025, 7, 13), sequence=22)
    (orphan_snapshot / "manifest.json").unlink()
    manifest_only = _write_artifact(root, source="naver", as_of=date(2026, 6, 13), sequence=23)
    (manifest_only / "snapshot.json").unlink()

    assert _run(root, apply=True) == 0
    assert (invalid_hash / "manifest.json").exists()
    assert (invalid_hash / "snapshot.json").exists()
    assert (invalid_retention / "manifest.json").exists()
    assert (invalid_retention / "snapshot.json").exists()
    assert (invalid_snapshot_contract / "manifest.json").exists()
    assert (invalid_snapshot_contract / "snapshot.json").exists()
    assert (invalid_naver_snapshot_contract / "manifest.json").exists()
    assert (invalid_naver_snapshot_contract / "snapshot.json").exists()
    assert (noncanonical_snapshot / "manifest.json").exists()
    assert (noncanonical_snapshot / "snapshot.json").exists()
    assert (noncanonical_ecos_snapshot / "manifest.json").exists()
    assert (noncanonical_ecos_snapshot / "snapshot.json").exists()
    assert not (orphan_snapshot / "manifest.json").exists()
    assert (orphan_snapshot / "snapshot.json").exists()
    assert (manifest_only / "manifest.json").exists()
    assert not (manifest_only / "snapshot.json").exists()


def test_apply_deletes_at_most_one_thousand_artifacts_per_run(tmp_path: Path) -> None:
    root = tmp_path / "source_snapshots"
    for sequence in range(1, 1_002):
        _write_artifact(root, source="ecos", as_of=date(2025, 7, 13), sequence=sequence)

    assert _run(root, apply=True) == 0
    assert len(list(root.rglob("manifest.json"))) == 1
    assert len(list(root.rglob("snapshot.json"))) == 1
