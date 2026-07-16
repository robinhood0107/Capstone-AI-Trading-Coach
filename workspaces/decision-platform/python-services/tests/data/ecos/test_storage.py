from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.data._shared.source_snapshot_models import SourceSnapshotManifest
from app.data.ecos.models import ECOSCollectionResult, ECOSMacroSnapshot
from app.data.ecos.quota import ECOS_QUOTA_POLICY_VERSION
from app.data.ecos.storage import (
    ECOS_SNAPSHOT_MAX_BYTES,
    ECOSSnapshotStorageError,
    publish_ecos_collection,
    serialize_ecos_snapshot,
)


def _snapshot() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "source": "ecos",
        "asOf": "2026-07-14",
        "retrievedAt": "2026-07-14T00:00:00Z",
        "registryVersion": "ecos-v1",
        "registryVerifiedAt": "2026-07-14T00:00:00Z",
        "series": [
            {
                "seriesId": "policy-rate",
                "statCode": "722Y001",
                "itemCode1": "0101000",
                "cycle": "D",
                "name": "synthetic policy rate",
                "unit": "%",
                "requestedFrom": "20260701",
                "requestedTo": "20260714",
                "status": "complete",
                "observations": [{"time": "20260714", "value": "2.5"}],
            },
            {
                "seriesId": "krw-usd-rate",
                "statCode": "731Y001",
                "itemCode1": "0000001",
                "cycle": "D",
                "name": "synthetic KRW/USD rate",
                "unit": "KRW",
                "requestedFrom": "20260701",
                "requestedTo": "20260714",
                "status": "empty",
                "observations": [],
            },
        ],
        "partial": False,
        "coverage": "complete",
    }


def test_snapshot_serialization_is_deterministic_sanitized_and_newline_terminated() -> None:
    first = _snapshot()
    second = dict(reversed(list(first.items())))

    encoded = serialize_ecos_snapshot(first)

    assert encoded == serialize_ecos_snapshot(second)
    assert encoded.endswith(b"\n")
    assert len(encoded) <= ECOS_SNAPSHOT_MAX_BYTES == 2 * 1024 * 1024
    lowered = encoded.lower()
    assert b"credential" not in lowered
    assert b"authorization" not in lowered
    assert b"rawbody" not in lowered


@pytest.mark.parametrize("forbidden", ["credential", "requestUrl", "authorization", "rawBody"])
def test_forbidden_fields_are_rejected_recursively(forbidden: str) -> None:
    payload = deepcopy(_snapshot())
    payload["series"][0][forbidden] = "synthetic-secret"  # type: ignore[index]

    with pytest.raises(ECOSSnapshotStorageError, match="forbidden"):
        serialize_ecos_snapshot(payload)


def test_snapshot_over_two_mib_is_rejected_before_publish() -> None:
    payload = _snapshot()
    payload["padding"] = "x" * ECOS_SNAPSHOT_MAX_BYTES

    with pytest.raises(ECOSSnapshotStorageError, match="size"):
        serialize_ecos_snapshot(payload)


def test_snapshot_contract_is_validated_before_serialization() -> None:
    payload = _snapshot()
    payload["series"] = payload["series"][:1]  # type: ignore[index]

    with pytest.raises(ECOSSnapshotStorageError, match="contract"):
        serialize_ecos_snapshot(payload)


def test_snapshot_rejects_invalid_calendar_observation_date() -> None:
    payload = _snapshot()
    first = payload["series"][0]  # type: ignore[index]
    first["requestedFrom"] = "20260228"  # type: ignore[index]
    first["requestedTo"] = "20260301"  # type: ignore[index]
    first["observations"][0]["time"] = "20260229"  # type: ignore[index]

    with pytest.raises(ECOSSnapshotStorageError, match="contract"):
        serialize_ecos_snapshot(payload)


def test_publish_writes_contract_valid_manifest_and_commit_marker(tmp_path) -> None:
    snapshot = ECOSMacroSnapshot.model_validate(_snapshot())
    result = ECOSCollectionResult(
        snapshot=snapshot,
        series_results=snapshot.series,
        partial=False,
        coverage="complete",
        physical_attempt_count=4,
        duplicate_count=0,
    )

    published = publish_ecos_collection(
        result,
        root=tmp_path,
        generated_at=datetime(2026, 7, 14, 1, 0, tzinfo=UTC),
        snapshot_id=UUID("12345678-1234-4abc-8def-1234567890ab"),
    )

    manifest = SourceSnapshotManifest.model_validate(
        json.loads(published.manifest_path.read_text(encoding="utf-8"))
    )
    snapshot_bytes = published.snapshot_path.read_bytes()
    assert manifest.operation == "ecos-macro-collect"
    assert manifest.source == "ecos"
    assert manifest.provider_profile == "ecos"
    assert manifest.retention_days == 365
    assert manifest.physical_attempt_count == 4
    assert manifest.deferred_queries == 0
    assert manifest.quota_policy_version == ECOS_QUOTA_POLICY_VERSION
    assert manifest.sanitization_version == "s1.3-sanitization-v1"
    assert manifest.delete_owner == "decision-platform:source-snapshot-retention"
    assert str(manifest.provenance.documentation_url) == (
        "https://ecos.bok.or.kr/api/#/DevGuide/DevSpeciflcation"
    )
    assert manifest.provenance.policy_url == manifest.provenance.documentation_url
    assert manifest.snapshot_sha256 == hashlib.sha256(snapshot_bytes).hexdigest()
    assert published.snapshot_path.exists()
    assert published.manifest_path.exists()
