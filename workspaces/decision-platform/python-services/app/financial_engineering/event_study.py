from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Sequence

import numpy as np

from app.data._shared.canonical_json import canonical_json_bytes

THRESHOLD_CANDIDATES = (95.0, 97.5, 99.0)
COST_SENSITIVITY_BPS = (25, 30, 35)
PURGE_EMBARGO_SESSIONS = 5
BOOTSTRAP_REPLICATIONS = 2_000
BOOTSTRAP_BLOCK_LENGTH = 5
BOOTSTRAP_SEED = 20_260_821


@dataclass(frozen=True)
class EventObservation:
    event_date: date
    score_percentile: float
    forward_return_bps: float
    snapshot_available_at: datetime | None
    required_source_available_ats: tuple[datetime, ...]
    xkrx_open_at: datetime | None
    cause_supported: bool | None = None


@dataclass(frozen=True)
class EventStudyResult:
    threshold_freeze: dict[str, object]
    event_study: dict[str, object]
    sensitivity_metrics: dict[int, dict[str, object]]


def evaluate_event_study(
    observations: Sequence[EventObservation],
    *,
    evidence_mode: str,
    created_at: datetime,
) -> EventStudyResult:
    if evidence_mode not in {"SYNTHETIC_FIXTURE", "HISTORICAL_REPLAY", "PROSPECTIVE_SHADOW"}:
        raise ValueError("evidence_mode_invalid")
    ordered = sorted(observations, key=lambda item: item.event_date)
    _validate_observations(ordered)
    coverage_years = (ordered[-1].event_date - ordered[0].event_date).days / 365.25
    if coverage_years < 3.0:
        raise ValueError("minimum_three_year_pit_coverage_required")
    train, validation, test = _chronological_split(ordered)
    severe_cutoff = float(np.quantile([row.forward_return_bps - 30 for row in train], 0.05, method="linear"))
    validation_metrics = {
        percentile: _selection_metrics(validation, percentile, severe_cutoff, 30)
        for percentile in THRESHOLD_CANDIDATES
    }
    selected = max(
        THRESHOLD_CANDIDATES,
        key=lambda percentile: (
            validation_metrics[percentile]["netProtectionBps"],
            validation_metrics[percentile]["severeLossRecall"],
            validation_metrics[percentile]["severeLossPrecision"],
            -validation_metrics[percentile]["falseBlockRate"],
            percentile,
        ),
    )
    validation_hash = hashlib.sha256(
        canonical_json_bytes({str(key): value for key, value in validation_metrics.items()})
    ).hexdigest()
    config = {
        "bootstrapBlockLength": BOOTSTRAP_BLOCK_LENGTH,
        "bootstrapReplications": BOOTSTRAP_REPLICATIONS,
        "bootstrapSeed": BOOTSTRAP_SEED,
        "costSensitivityBps": list(COST_SENSITIVITY_BPS),
        "purgeEmbargoSessions": PURGE_EMBARGO_SESSIONS,
        "split": [0.6, 0.2, 0.2],
        "thresholdCandidates": list(THRESHOLD_CANDIDATES),
    }
    freeze = {
        "contractId": "cross_market_threshold_freeze.v1",
        "selectedOn": "VALIDATION_ONLY",
        "selectedPercentile": selected,
        "candidatePercentiles": list(THRESHOLD_CANDIDATES),
        "selectionMetricOrder": [
            "MAX_NET_PROTECTION_BPS",
            "MAX_SEVERE_LOSS_RECALL",
            "MAX_SEVERE_LOSS_PRECISION",
            "MIN_FALSE_BLOCK_RATE",
            "HIGHER_PERCENTILE",
        ],
        "validationArtifactHash": validation_hash,
        "configHash": hashlib.sha256(canonical_json_bytes(config)).hexdigest(),
        "immutable": True,
        "createdAt": _timestamp(created_at),
    }
    sensitivities = {
        cost: _contract_metrics(test, selected, severe_cutoff, cost) for cost in COST_SENSITIVITY_BPS
    }
    test_metrics = sensitivities[30]
    interval = _block_bootstrap_interval(test, selected, severe_cutoff, 30)
    timing = _timing(test)
    study: dict[str, object] = {
        "contractId": "cross_market_event_study.v2",
        "decisionAuthority": "NONE",
        "runtimeRiskEngineSource": False,
        "productionSignalAuthority": False,
        "researchOnly": True,
        "evidenceMode": evidence_mode,
        "datasetStatus": "AVAILABLE",
        "coverageYears": min(10.0, coverage_years),
        "split": [0.6, 0.2, 0.2],
        "purgeEmbargoSessions": PURGE_EMBARGO_SESSIONS,
        "thresholdCandidates": list(THRESHOLD_CANDIDATES),
        "severeLossCutoff": severe_cutoff,
        "transactionCostSensitivityBps": list(COST_SENSITIVITY_BPS),
        "timing": timing,
        "metrics": test_metrics,
        "bootstrap": {
            "unit": "EVENT_DATE",
            "blockLengthSessions": BOOTSTRAP_BLOCK_LENGTH,
            "replications": BOOTSTRAP_REPLICATIONS,
            "seed": BOOTSTRAP_SEED,
            "interval": interval,
            "superiorityClaimAllowed": interval is not None and not (interval[0] <= 0 <= interval[1]),
        },
        "performanceClaimAllowed": (
            evidence_mode == "HISTORICAL_REPLAY"
            and interval is not None
            and not (interval[0] <= 0 <= interval[1])
        ),
    }
    study["artifactHash"] = hashlib.sha256(canonical_json_bytes(study)).hexdigest()
    return EventStudyResult(freeze, study, sensitivities)


def unavailable_event_study(*, evidence_mode: str = "PROSPECTIVE_SHADOW") -> dict[str, object]:
    payload: dict[str, object] = {
        "contractId": "cross_market_event_study.v2",
        "decisionAuthority": "NONE",
        "runtimeRiskEngineSource": False,
        "productionSignalAuthority": False,
        "researchOnly": True,
        "evidenceMode": evidence_mode,
        "datasetStatus": "DATASET_UNAVAILABLE",
        "coverageYears": 0,
        "split": [0.6, 0.2, 0.2],
        "purgeEmbargoSessions": 5,
        "thresholdCandidates": [95, 97.5, 99],
        "severeLossCutoff": None,
        "transactionCostSensitivityBps": [25, 30, 35],
        "timing": _not_estimable_timing(),
        "metrics": _not_estimable_metrics(),
        "bootstrap": {
            "unit": "EVENT_DATE",
            "blockLengthSessions": 5,
            "replications": 2000,
            "seed": BOOTSTRAP_SEED,
            "interval": None,
            "superiorityClaimAllowed": False,
        },
        "performanceClaimAllowed": False,
    }
    payload["artifactHash"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return payload


def _validate_observations(rows: list[EventObservation]) -> None:
    if len(rows) < 40 or len({row.event_date for row in rows}) != len(rows):
        raise ValueError("event_date_unit_invalid")
    for row in rows:
        if not math.isfinite(row.score_percentile) or not 0 <= row.score_percentile <= 100:
            raise ValueError("score_invalid")
        if not math.isfinite(row.forward_return_bps):
            raise ValueError("return_invalid")
        if row.snapshot_available_at is not None and row.snapshot_available_at.tzinfo is None:
            raise ValueError("timestamp_must_be_aware")
        if any(value.tzinfo is None for value in row.required_source_available_ats):
            raise ValueError("timestamp_must_be_aware")
        if row.snapshot_available_at is not None and row.required_source_available_ats:
            if row.snapshot_available_at < max(row.required_source_available_ats):
                raise ValueError("INVALID_CHRONOLOGY")


def _chronological_split(rows: list[EventObservation]) -> tuple[list[EventObservation], list[EventObservation], list[EventObservation]]:
    count = len(rows)
    train_end = int(count * 0.6)
    validation_start = train_end + PURGE_EMBARGO_SESSIONS
    validation_end = validation_start + int(count * 0.2)
    test_start = validation_end + PURGE_EMBARGO_SESSIONS
    train, validation, test = rows[:train_end], rows[validation_start:validation_end], rows[test_start:]
    if min(len(train), len(validation), len(test)) < 5:
        raise ValueError("split_not_estimable")
    return train, validation, test


def _selection_metrics(rows: list[EventObservation], threshold: float, severe: float, cost: int) -> dict[str, float]:
    triggered = [row for row in rows if row.score_percentile >= threshold]
    severe_rows = [row for row in rows if row.forward_return_bps - cost <= severe]
    true_positive = [row for row in triggered if row.forward_return_bps - cost <= severe]
    net = sum(max(0.0, -(row.forward_return_bps - cost)) for row in triggered) - sum(
        max(0.0, row.forward_return_bps - cost) for row in triggered
    )
    return {
        "netProtectionBps": net / len(triggered) if triggered else -1e300,
        "severeLossRecall": len(true_positive) / len(severe_rows) if severe_rows else 0.0,
        "severeLossPrecision": len(true_positive) / len(triggered) if triggered else 0.0,
        "falseBlockRate": (len(triggered) - len(true_positive)) / len(triggered) if triggered else 1.0,
    }


def _contract_metrics(rows: list[EventObservation], threshold: float, severe: float, cost: int) -> dict[str, object]:
    triggered = [row for row in rows if row.score_percentile >= threshold]
    if not triggered:
        return _not_estimable_metrics()
    false_blocks = [row for row in triggered if row.forward_return_bps - cost > severe]
    downside = [max(0.0, -(row.forward_return_bps - cost)) for row in triggered]
    missed = [max(0.0, row.forward_return_bps - cost) for row in triggered]
    net = [left - right for left, right in zip(downside, missed)]
    return {
        "triggerCount": len(triggered),
        "falseBlockRate": _estimated(len(false_blocks) / len(triggered)),
        "downsideAvoidedBps": _estimated(float(np.mean(downside))),
        "missedUpsideBps": _estimated(float(np.mean(missed))),
        "netProtectionBps": _estimated(float(np.mean(net))),
    }


def _block_bootstrap_interval(rows: list[EventObservation], threshold: float, severe: float, cost: int) -> list[float] | None:
    values = [
        max(0.0, -(row.forward_return_bps - cost)) - max(0.0, row.forward_return_bps - cost)
        for row in rows
        if row.score_percentile >= threshold
    ]
    if not values:
        return None
    rng = np.random.Generator(np.random.PCG64(BOOTSTRAP_SEED))
    array = np.asarray(values, dtype=np.float64)
    estimates = np.empty(BOOTSTRAP_REPLICATIONS, dtype=np.float64)
    for replication in range(BOOTSTRAP_REPLICATIONS):
        sampled: list[float] = []
        while len(sampled) < len(array):
            start = int(rng.integers(0, len(array)))
            sampled.extend(array.take(np.arange(start, start + BOOTSTRAP_BLOCK_LENGTH) % len(array)).tolist())
        estimates[replication] = float(np.mean(sampled[: len(array)]))
    low, high = np.quantile(estimates, [0.025, 0.975], method="linear")
    return [float(low), float(high)]


def _timing(rows: list[EventObservation]) -> dict[str, object]:
    if any(row.snapshot_available_at is None or not row.required_source_available_ats or row.xkrx_open_at is None for row in rows):
        return _not_estimable_timing()
    latencies = [
        int((row.snapshot_available_at - max(row.required_source_available_ats)).total_seconds() * 1000)  # type: ignore[operator]
        for row in rows
    ]
    if any(value < 0 for value in latencies):
        raise ValueError("INVALID_CHRONOLOGY")
    leads = [int((row.xkrx_open_at - row.snapshot_available_at).total_seconds() * 1000) for row in rows]  # type: ignore[operator]
    latency = int(np.median(latencies))
    lead = int(np.median(leads))
    return {
        "detectionLatencyMillis": latency,
        "preOpenLeadTimeMillis": lead,
        "preOpenStatus": "EARLY" if lead > 0 else ("AT_OPEN" if lead == 0 else "LATE"),
        "estimationStatus": "ESTIMATED",
    }


def _estimated(value: float) -> dict[str, object]:
    return {"value": value, "estimationStatus": "ESTIMATED"}


def _not_estimable_metrics() -> dict[str, object]:
    missing = {"value": None, "estimationStatus": "NOT_ESTIMABLE"}
    return {
        "triggerCount": 0,
        "falseBlockRate": dict(missing),
        "downsideAvoidedBps": dict(missing),
        "missedUpsideBps": dict(missing),
        "netProtectionBps": dict(missing),
    }


def _not_estimable_timing() -> dict[str, object]:
    return {
        "detectionLatencyMillis": None,
        "preOpenLeadTimeMillis": None,
        "preOpenStatus": "NOT_ESTIMABLE",
        "estimationStatus": "NOT_ESTIMABLE",
    }


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("created_at_must_be_aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
