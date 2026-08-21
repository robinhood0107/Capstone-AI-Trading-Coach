from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from app.financial_engineering.event_study import (
    EventObservation,
    evaluate_event_study,
    unavailable_event_study,
)
from app.financial_engineering.lightgbm_replay import (
    ResearchCandidate,
    build_lightgbm_policy_replay,
    data_requirements_packet,
)

REPO = Path(__file__).resolve().parents[5]


def _rows() -> list[EventObservation]:
    start = date(2022, 1, 3)
    result = []
    for index in range(240):
        source_at = datetime(2022, 1, 3, 22, tzinfo=UTC) + timedelta(days=index * 7)
        result.append(
            EventObservation(
                event_date=start + timedelta(days=index * 7),
                score_percentile=float(index % 100),
                forward_return_bps=float(-200 if index % 23 == 0 else 80 - index % 50),
                snapshot_available_at=source_at + timedelta(minutes=5),
                required_source_available_ats=(source_at, source_at - timedelta(minutes=1)),
                xkrx_open_at=source_at + timedelta(hours=1),
                cause_supported=index % 3 == 0,
            )
        )
    return result


def _validate(schema_name: str, payload: dict[str, object]) -> None:
    schema = json.loads((REPO / f"contracts/schemas/{schema_name}.schema.json").read_text())
    assert list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload)) == []


def test_validation_only_threshold_freeze_and_untouched_test_are_deterministic() -> None:
    rows = _rows()
    first = evaluate_event_study(
        rows,
        evidence_mode="SYNTHETIC_FIXTURE",
        created_at=datetime(2026, 8, 21, tzinfo=UTC),
    )
    mutated = [*rows]
    for index in range(202, len(mutated)):
        mutated[index] = replace(mutated[index], forward_return_bps=9999.0)
    second = evaluate_event_study(
        mutated,
        evidence_mode="SYNTHETIC_FIXTURE",
        created_at=datetime(2026, 8, 21, tzinfo=UTC),
    )

    assert first.threshold_freeze == second.threshold_freeze
    assert first.event_study["split"] == [0.6, 0.2, 0.2]
    assert first.event_study["purgeEmbargoSessions"] == 5
    assert first.event_study["thresholdCandidates"] == [95.0, 97.5, 99.0]
    assert first.event_study["transactionCostSensitivityBps"] == [25, 30, 35]
    assert first.event_study["performanceClaimAllowed"] is False
    assert first.event_study["bootstrap"]["replications"] == 2000
    assert first.event_study["bootstrap"]["unit"] == "EVENT_DATE"
    assert set(first.sensitivity_metrics) == {25, 30, 35}
    _validate("cross_market_threshold_freeze.v1", first.threshold_freeze)
    _validate("cross_market_event_study.v2", first.event_study)


def test_missing_timing_is_not_estimated_and_negative_latency_is_invalid() -> None:
    missing = _rows()
    missing[-1] = replace(missing[-1], snapshot_available_at=None)
    result = evaluate_event_study(
        missing,
        evidence_mode="PROSPECTIVE_SHADOW",
        created_at=datetime(2026, 8, 21, tzinfo=UTC),
    )
    assert result.event_study["timing"] == {
        "detectionLatencyMillis": None,
        "preOpenLeadTimeMillis": None,
        "preOpenStatus": "NOT_ESTIMABLE",
        "estimationStatus": "NOT_ESTIMABLE",
    }
    invalid = _rows()
    invalid[-1] = replace(
        invalid[-1],
        snapshot_available_at=invalid[-1].required_source_available_ats[0] - timedelta(milliseconds=1),
    )
    with pytest.raises(ValueError, match="INVALID_CHRONOLOGY"):
        evaluate_event_study(
            invalid,
            evidence_mode="HISTORICAL_REPLAY",
            created_at=datetime(2026, 8, 21, tzinfo=UTC),
        )


def test_unavailable_dataset_and_zero_denominators_remain_honest() -> None:
    report = unavailable_event_study()
    assert report["datasetStatus"] == "DATASET_UNAVAILABLE"
    assert report["metrics"]["triggerCount"] == 0
    for name in ("falseBlockRate", "downsideAvoidedBps", "missedUpsideBps", "netProtectionBps"):
        assert report["metrics"][name] == {"value": None, "estimationStatus": "NOT_ESTIMABLE"}
    assert report["performanceClaimAllowed"] is False
    _validate("cross_market_event_study.v2", report)


def test_lightgbm_replay_accepts_only_immutable_available_buy_candidate() -> None:
    failed = build_lightgbm_policy_replay(
        ResearchCandidate("a" * 64, "FAILED", True, "BUY", "REAL_PIT"),
        pit_dataset_available=True,
    )
    synthetic = build_lightgbm_policy_replay(
        ResearchCandidate("b" * 64, "AVAILABLE", True, "BUY", "SYNTHETIC_FIXTURE"),
        pit_dataset_available=True,
    )
    real = build_lightgbm_policy_replay(
        ResearchCandidate("c" * 64, "AVAILABLE", True, "BUY", "REAL_PIT"),
        pit_dataset_available=True,
    )
    assert failed["candidateQualificationStatus"] == "FAILED"
    assert failed["candidateArtifactHash"] is None
    assert failed["performanceClaimAllowed"] is False
    assert synthetic["evidenceLabel"] == "SYNTHETIC_FIXTURE"
    assert synthetic["performanceClaimAllowed"] is False
    assert real["performanceClaimAllowed"] is True
    requirements = data_requirements_packet()
    assert requirements["expectedCalls"] == 0
    assert json.loads(
        (REPO / "contracts/examples/s6-6-data-requirements.v1.json").read_text()
    ) == requirements
    receipt = json.loads(
        (REPO / "contracts/examples/s6-6-dataset-candidate-receipt.v1.json").read_text()
    )
    assert receipt["datasetStatus"] == "DATASET_UNAVAILABLE"
    assert receipt["performanceClaimAllowed"] is False
    assert receipt["providerCalls"] == 0
    for payload in (failed, synthetic, real):
        _validate("lightgbm_policy_replay.v1", payload)


def test_minimum_three_year_coverage_and_event_date_unit_fail_closed() -> None:
    with pytest.raises(ValueError, match="minimum_three_year"):
        evaluate_event_study(
            _rows()[:100],
            evidence_mode="SYNTHETIC_FIXTURE",
            created_at=datetime(2026, 8, 21, tzinfo=UTC),
        )
    duplicate = _rows()
    duplicate[1] = replace(duplicate[1], event_date=duplicate[0].event_date)
    with pytest.raises(ValueError, match="event_date_unit"):
        evaluate_event_study(
            duplicate,
            evidence_mode="SYNTHETIC_FIXTURE",
            created_at=datetime(2026, 8, 21, tzinfo=UTC),
        )
