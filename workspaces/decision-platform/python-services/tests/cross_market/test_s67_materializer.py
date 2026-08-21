from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.cross_market.s67_materializer import (
    CrossMarketRiskMaterializer,
    EvidenceMode,
    ExposureClassification,
    RuntimeMode,
    StorageMode,
    ThresholdFreeze,
)


NOW = datetime(2026, 8, 21, 8, 10, tzinfo=UTC)
FREEZE = ThresholdFreeze(Decimal("97.5"), "1" * 64, "2" * 64)


def _snapshot(**overrides: object):
    arguments: dict[str, object] = {
        "symbol": "005930",
        "available_at": NOW,
        "stale_at": NOW + timedelta(hours=24),
        "score": Decimal("98.125000"),
        "freeze": FREEZE,
        "exposure": ExposureClassification.NEW_BUY,
        "exposure_catalog_hash": "3" * 64,
        "evidence_mode": EvidenceMode.SYNTHETIC_FIXTURE,
        "storage_mode": StorageMode.STORED_SNAPSHOT,
        "runtime_mode": RuntimeMode.WARN_ONLY,
        "explanation": {"analyst": "fixture", "rag": "none", "llm": "none"},
    }
    arguments.update(overrides)
    return CrossMarketRiskMaterializer().materialize(**arguments)  # type: ignore[arg-type]


def test_numeric_threshold_config_and_exposure_mutation_change_semantic_hash() -> None:
    baseline = _snapshot().semantic_input_hash
    assert _snapshot(score=Decimal("98.125001")).semantic_input_hash != baseline
    assert _snapshot(freeze=ThresholdFreeze(Decimal("99"), "4" * 64, "2" * 64)).semantic_input_hash != baseline
    assert _snapshot(freeze=ThresholdFreeze(Decimal("97.5"), "1" * 64, "5" * 64)).semantic_input_hash != baseline
    assert _snapshot(exposure=ExposureClassification.EXISTING_POSITION).semantic_input_hash != baseline


def test_explanation_analyst_rag_llm_only_changes_artifact_hash() -> None:
    baseline = _snapshot()
    changed = _snapshot(explanation={"analyst": "changed", "rag": "changed", "llm": "changed"})
    assert changed.semantic_input_hash == baseline.semantic_input_hash
    assert changed.artifact_hash != baseline.artifact_hash


def test_missing_freeze_is_unavailable_and_never_falls_back_to_80_or_zero() -> None:
    snapshot = _snapshot(freeze=None)
    assert snapshot.payload["availability"] == "UNAVAILABLE"
    assert snapshot.payload["thresholdPercentile"] is None
    assert snapshot.payload["thresholdArtifactHash"] is None
    assert b'"thresholdPercentile":80' not in snapshot.canonical_bytes()
    assert snapshot.payload["providerFanoutAllowed"] is False


def test_enforced_mode_and_invalid_timing_are_rejected_before_storage() -> None:
    with pytest.raises(ValueError, match="MODE_NOT_APPROVED"):
        _snapshot(runtime_mode=RuntimeMode.ENFORCED)
    with pytest.raises(ValueError, match="chronology"):
        _snapshot(stale_at=NOW - timedelta(seconds=1))


def test_equivalent_offset_timestamps_have_one_utc_semantic_identity() -> None:
    seoul = timezone(timedelta(hours=9))
    offset_snapshot = _snapshot(
        available_at=NOW.astimezone(seoul),
        stale_at=(NOW + timedelta(hours=24)).astimezone(seoul),
    )
    utc_snapshot = _snapshot()
    assert offset_snapshot.payload["availableAt"] == "2026-08-21T08:10:00.000000Z"
    assert offset_snapshot.semantic_input_hash == utc_snapshot.semantic_input_hash
    assert offset_snapshot.payload["snapshotId"] == utc_snapshot.payload["snapshotId"]


def test_subsecond_timestamp_mutation_changes_semantic_identity() -> None:
    changed = _snapshot(available_at=NOW + timedelta(microseconds=1))
    assert changed.semantic_input_hash != _snapshot().semantic_input_hash
