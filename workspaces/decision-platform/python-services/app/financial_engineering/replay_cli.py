from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Never

from app.data._shared.canonical_json import canonical_json_bytes
from app.financial_engineering.event_study import EventObservation, evaluate_event_study
from app.financial_engineering.lightgbm_replay import (
    ResearchCandidate,
    build_lightgbm_policy_replay,
)

MAX_DATASET_BYTES = 16 * 1024 * 1024
MAX_CANDIDATE_BYTES = 64 * 1024
MAX_OBSERVATIONS = 10_000
_HASH = set("0123456789abcdef")
_EVIDENCE_MODES = {"HISTORICAL_REPLAY", "SYNTHETIC_FIXTURE"}


def run_replay(
    *,
    dataset_path: Path,
    candidate_path: Path,
    output_root: Path,
) -> dict[str, object]:
    dataset_raw, dataset_file_hash = _read_json(dataset_path, MAX_DATASET_BYTES)
    candidate_raw, candidate_file_hash = _read_json(candidate_path, MAX_CANDIDATE_BYTES)
    dataset = _mapping(dataset_raw, "dataset_manifest_invalid")
    candidate = _mapping(candidate_raw, "candidate_manifest_invalid")
    observations, evidence_mode, created_at = _parse_dataset(dataset)
    research_candidate = _parse_candidate(candidate, evidence_mode)

    result = evaluate_event_study(
        observations,
        evidence_mode=evidence_mode,
        created_at=created_at,
    )
    empirical_claim = bool(result.event_study["performanceClaimAllowed"])
    replay = build_lightgbm_policy_replay(
        research_candidate,
        pit_dataset_available=evidence_mode == "HISTORICAL_REPLAY",
        empirical_performance_claim_allowed=empirical_claim,
    )
    sensitivity: dict[str, object] = {
        "contractId": "s6-6-cost-sensitivity.v1",
        "researchOnly": True,
        "transactionCostSensitivityBps": {
            str(cost): metrics for cost, metrics in sorted(result.sensitivity_metrics.items())
        },
    }
    sensitivity["artifactHash"] = hashlib.sha256(canonical_json_bytes(sensitivity)).hexdigest()
    artifacts = {
        "cross_market_threshold_freeze.v1.json": result.threshold_freeze,
        "cross_market_event_study.v2.json": result.event_study,
        "lightgbm_policy_replay.v1.json": replay,
        "s6-6-cost-sensitivity.v1.json": sensitivity,
    }
    receipt: dict[str, object] = {
        "contractId": "s6-6-provider-free-replay-receipt.v1",
        "status": "COMPLETE",
        "evidenceMode": evidence_mode,
        "datasetManifestFileHash": dataset_file_hash,
        "datasetArtifactHash": dataset["datasetArtifactHash"],
        "candidateManifestFileHash": candidate_file_hash,
        "candidateArtifactHash": research_candidate.artifact_hash,
        "thresholdPercentile": result.threshold_freeze["selectedPercentile"],
        "thresholdArtifactHash": _artifact_hash(result.threshold_freeze),
        "configHash": result.threshold_freeze["configHash"],
        "performanceClaimAllowed": bool(empirical_claim and replay["performanceClaimAllowed"]),
        "providerCalls": 0,
        "liveAccountCalls": 0,
        "orderCalls": 0,
        "artifacts": {
            name: hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
            for name, payload in artifacts.items()
        },
    }
    receipt["artifactHash"] = hashlib.sha256(canonical_json_bytes(receipt)).hexdigest()
    artifacts["s6-6-provider-free-replay-receipt.v1.json"] = receipt
    _publish(output_root, artifacts)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="Run bounded provider-free S6.6 PIT replay")
    parser.add_argument("--dataset-manifest", type=Path, required=True)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    receipt = run_replay(
        dataset_path=args.dataset_manifest,
        candidate_path=args.candidate_manifest,
        output_root=args.output_root,
    )
    print(canonical_json_bytes(receipt).decode("utf-8"), end="")
    return 0


def _parse_dataset(value: dict[str, Any]) -> tuple[list[EventObservation], str, datetime]:
    _exact_keys(
        value,
        {
            "contractId",
            "datasetArtifactHash",
            "immutable",
            "entitlementStatus",
            "retentionPolicy",
            "pitAvailableAt",
            "evidenceMode",
            "createdAt",
            "sourceManifestHashes",
            "observations",
        },
        "dataset_manifest_invalid",
    )
    if value["contractId"] != "s6-6-pit-dataset-manifest.v1":
        _fail("dataset_contract_invalid")
    evidence_mode = _string(value["evidenceMode"], "evidence_mode_invalid")
    if evidence_mode not in _EVIDENCE_MODES:
        _fail("evidence_mode_invalid")
    if value["immutable"] is not True:
        _fail("dataset_not_immutable")
    if value["entitlementStatus"] != "VERIFIED" or value["pitAvailableAt"] is not True:
        _fail("dataset_authority_unverified")
    if not _string(value["retentionPolicy"], "retention_policy_invalid"):
        _fail("retention_policy_invalid")
    _require_hash(value["datasetArtifactHash"], "dataset_artifact_hash_invalid")
    source_hashes = value["sourceManifestHashes"]
    if not isinstance(source_hashes, list) or not 2 <= len(source_hashes) <= 64:
        _fail("source_manifest_hashes_invalid")
    if len(
        set(_require_hash(item, "source_manifest_hash_invalid") for item in source_hashes)
    ) != len(source_hashes):
        _fail("source_manifest_hashes_invalid")
    created_at = _aware_datetime(value["createdAt"], "created_at_invalid")
    rows = value["observations"]
    if not isinstance(rows, list) or not 40 <= len(rows) <= MAX_OBSERVATIONS:
        _fail("observation_count_invalid")
    return [_parse_observation(item) for item in rows], evidence_mode, created_at


def _parse_candidate(value: dict[str, Any], evidence_mode: str) -> ResearchCandidate:
    _exact_keys(
        value,
        {
            "contractId",
            "artifactHash",
            "qualificationStatus",
            "immutable",
            "side",
            "evidenceLabel",
        },
        "candidate_manifest_invalid",
    )
    if value["contractId"] != "s6-6-research-candidate-manifest.v1":
        _fail("candidate_contract_invalid")
    candidate = ResearchCandidate(
        artifact_hash=_require_hash(value["artifactHash"], "candidate_artifact_hash_invalid"),
        qualification_status=_string(value["qualificationStatus"], "candidate_status_invalid"),
        immutable=value["immutable"] is True,
        side=_string(value["side"], "candidate_side_invalid"),
        evidence_label=_string(value["evidenceLabel"], "candidate_evidence_invalid"),
    )
    expected_label = "REAL_PIT" if evidence_mode == "HISTORICAL_REPLAY" else "SYNTHETIC_FIXTURE"
    if (
        candidate.qualification_status != "AVAILABLE"
        or not candidate.immutable
        or candidate.side != "BUY"
        or candidate.evidence_label != expected_label
    ):
        _fail("candidate_not_eligible")
    return candidate


def _parse_observation(raw: object) -> EventObservation:
    value = _mapping(raw, "observation_invalid")
    _exact_keys(
        value,
        {
            "eventDate",
            "scorePercentile",
            "forwardReturnBps",
            "snapshotAvailableAt",
            "requiredSourceAvailableAts",
            "xkrxOpenAt",
            "causeSupported",
            "causeConflict",
        },
        "observation_invalid",
    )
    required = value["requiredSourceAvailableAts"]
    if not isinstance(required, list) or not 1 <= len(required) <= 64:
        _fail("required_source_timestamps_invalid")
    return EventObservation(
        event_date=_date(value["eventDate"], "event_date_invalid"),
        score_percentile=_finite_number(value["scorePercentile"], "score_invalid"),
        forward_return_bps=_finite_number(value["forwardReturnBps"], "return_invalid"),
        snapshot_available_at=_optional_datetime(value["snapshotAvailableAt"]),
        required_source_available_ats=tuple(
            _aware_datetime(item, "required_source_timestamp_invalid") for item in required
        ),
        xkrx_open_at=_optional_datetime(value["xkrxOpenAt"]),
        cause_supported=_optional_bool(value["causeSupported"], "cause_supported_invalid"),
        cause_conflict=_optional_bool(value["causeConflict"], "cause_conflict_invalid"),
    )


def _read_json(path: Path, maximum: int) -> tuple[object, str]:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or not 1 <= metadata.st_size <= maximum:
        _fail("input_file_invalid")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            metadata.st_dev,
            metadata.st_ino,
        ):
            _fail("input_file_changed")
        payload = os.read(descriptor, maximum + 1)
        if len(payload) != metadata.st_size or len(payload) > maximum:
            _fail("input_file_invalid")
    finally:
        os.close(descriptor)
    try:
        decoded = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("input_json_invalid")
    return decoded, hashlib.sha256(payload).hexdigest()


def _publish(output_root: Path, artifacts: dict[str, dict[str, object]]) -> None:
    if output_root.exists() or output_root.is_symlink():
        _fail("output_root_exists")
    parent = output_root.parent
    parent_metadata = parent.lstat()
    if not stat.S_ISDIR(parent_metadata.st_mode):
        _fail("output_parent_invalid")
    staging = Path(tempfile.mkdtemp(prefix=f".{output_root.name}.staging-", dir=parent))
    try:
        for name, payload in artifacts.items():
            content = canonical_json_bytes(payload)
            if len(content) > 1_048_576:
                _fail("output_artifact_too_large")
            (staging / name).write_bytes(content)
        os.replace(staging, output_root)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _unique_object(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            _fail("duplicate_json_key")
        result[key] = value
    return result


def _mapping(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(code)
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], code: str) -> None:
    if set(value) != expected:
        _fail(code)


def _string(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        _fail(code)
    return value


def _require_hash(value: object, code: str) -> str:
    text = _string(value, code)
    if len(text) != 64 or any(character not in _HASH for character in text):
        _fail(code)
    return text


def _aware_datetime(value: object, code: str) -> datetime:
    text = _string(value, code)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None:
        _fail(code)
    return parsed


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _aware_datetime(value, "timestamp_invalid")


def _date(value: object, code: str) -> date:
    text = _string(value, code)
    try:
        return date.fromisoformat(text)
    except ValueError:
        _fail(code)


def _finite_number(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(code)
    number = float(value)
    if not (-1e12 < number < 1e12):
        _fail(code)
    return number


def _optional_bool(value: object, code: str) -> bool | None:
    if value is not None and not isinstance(value, bool):
        _fail(code)
    return value


def _artifact_hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _fail(code: str) -> Never:
    raise ValueError(code)


if __name__ == "__main__":
    raise SystemExit(main())
