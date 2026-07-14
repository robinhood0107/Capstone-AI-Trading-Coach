from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.data._shared.source_snapshot_models import SourceSnapshotManifest


def _ecos_manifest() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "source": "ecos",
        "providerProfile": "ecos",
        "operation": "ecos-macro-collect",
        "generatedAt": "2026-07-14T00:00:00Z",
        "asOf": "2026-07-14",
        "snapshotPath": "ecos/2026/07/14/00000000-0000-4000-8000-000000000001/snapshot.json",
        "snapshotSha256": "a" * 64,
        "recordCount": 2,
        "countBreakdown": {"seriesCount": 2, "observationCount": 2, "duplicateCount": 0},
        "partial": False,
        "coverage": "complete",
        "deferredQueries": 0,
        "physicalAttemptCount": 4,
        "quotaPolicyVersion": "ecos-v1",
        "provenance": {
            "documentationUrl": "https://ecos.bok.or.kr/api/",
            "policyUrl": "https://ecos.bok.or.kr/api/",
        },
        "sanitizationVersion": "ecos-v1",
        "retentionDays": 365,
        "deleteOwner": "decision-platform:source-snapshot-retention",
    }


def test_ecos_manifest_counts_and_retention_are_consistent() -> None:
    manifest = SourceSnapshotManifest.model_validate(_ecos_manifest())

    assert manifest.source == "ecos"
    assert manifest.record_count == manifest.count_breakdown.observation_count


@pytest.mark.parametrize("forbidden", ["credential", "requestUrl", "authorization", "rawBody"])
def test_manifest_rejects_forbidden_or_unknown_fields(forbidden: str) -> None:
    payload = _ecos_manifest()
    payload[forbidden] = "not-allowed"

    with pytest.raises(ValidationError):
        SourceSnapshotManifest.model_validate(payload)


def test_partial_and_coverage_cannot_disagree() -> None:
    payload = _ecos_manifest()
    payload["partial"] = True
    payload["coverage"] = "complete"

    with pytest.raises(ValidationError):
        SourceSnapshotManifest.model_validate(payload)
