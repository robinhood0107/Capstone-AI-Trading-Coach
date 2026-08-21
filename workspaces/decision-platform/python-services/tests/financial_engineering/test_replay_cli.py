from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from app.data._shared.canonical_json import canonical_json_bytes
from app.financial_engineering.replay_cli import run_replay


def _dataset(*, evidence_mode: str = "SYNTHETIC_FIXTURE") -> dict[str, object]:
    start = date(2022, 1, 3)
    base = datetime(2022, 1, 3, 22, tzinfo=UTC)
    observations = []
    for index in range(240):
        source_at = base + timedelta(days=index * 7)
        observations.append(
            {
                "eventDate": (start + timedelta(days=index * 7)).isoformat(),
                "scorePercentile": float(index % 100),
                "forwardReturnBps": float(-200 if index % 23 == 0 else 80 - index % 50),
                "snapshotAvailableAt": (source_at + timedelta(minutes=5)).isoformat(),
                "requiredSourceAvailableAts": [source_at.isoformat()],
                "xkrxOpenAt": (source_at + timedelta(hours=1)).isoformat(),
                "causeSupported": index % 3 == 0,
                "causeConflict": index % 17 == 0,
            }
        )
    return {
        "contractId": "s6-6-pit-dataset-manifest.v1",
        "datasetArtifactHash": "a" * 64,
        "immutable": True,
        "entitlementStatus": "VERIFIED",
        "retentionPolicy": "PROJECT_POLICY_BOUND",
        "pitAvailableAt": True,
        "evidenceMode": evidence_mode,
        "createdAt": "2026-08-21T08:15:00Z",
        "sourceManifestHashes": ["b" * 64, "c" * 64],
        "observations": observations,
    }


def _candidate(*, evidence_label: str = "SYNTHETIC_FIXTURE") -> dict[str, object]:
    return {
        "contractId": "s6-6-research-candidate-manifest.v1",
        "artifactHash": "d" * 64,
        "qualificationStatus": "AVAILABLE",
        "immutable": True,
        "side": "BUY",
        "evidenceLabel": evidence_label,
    }


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_bytes(canonical_json_bytes(payload))


def test_provider_free_runner_publishes_deterministic_bounded_research_artifacts(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset.json"
    candidate = tmp_path / "candidate.json"
    _write(dataset, _dataset())
    _write(candidate, _candidate())

    first = run_replay(
        dataset_path=dataset, candidate_path=candidate, output_root=tmp_path / "first"
    )
    second = run_replay(
        dataset_path=dataset, candidate_path=candidate, output_root=tmp_path / "second"
    )

    assert first == second
    assert first["status"] == "COMPLETE"
    assert first["evidenceMode"] == "SYNTHETIC_FIXTURE"
    assert first["providerCalls"] == first["liveAccountCalls"] == first["orderCalls"] == 0
    assert first["performanceClaimAllowed"] is False
    assert first["thresholdPercentile"] in {95.0, 97.5, 99.0}
    assert (tmp_path / "first").is_dir()
    assert sorted(path.name for path in (tmp_path / "first").iterdir()) == sorted(
        [*first["artifacts"], "s6-6-provider-free-replay-receipt.v1.json"]
    )
    sensitivity = json.loads((tmp_path / "first/s6-6-cost-sensitivity.v1.json").read_text())
    assert set(sensitivity["transactionCostSensitivityBps"]) == {"25", "30", "35"}


def test_historical_replay_requires_real_pit_available_buy_candidate(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.json"
    candidate = tmp_path / "candidate.json"
    _write(dataset, _dataset(evidence_mode="HISTORICAL_REPLAY"))
    invalid = _candidate(evidence_label="REAL_PIT")
    invalid["qualificationStatus"] = "FAILED"
    _write(candidate, invalid)

    with pytest.raises(ValueError, match="candidate_not_eligible"):
        run_replay(dataset_path=dataset, candidate_path=candidate, output_root=tmp_path / "output")
    assert not (tmp_path / "output").exists()

    _write(candidate, _candidate(evidence_label="REAL_PIT"))
    receipt = run_replay(
        dataset_path=dataset,
        candidate_path=candidate,
        output_root=tmp_path / "valid-output",
    )
    study = json.loads((tmp_path / "valid-output/cross_market_event_study.v2.json").read_text())
    interval = study["bootstrap"]["interval"]
    expected_claim = interval is not None and not (interval[0] <= 0 <= interval[1])
    assert receipt["performanceClaimAllowed"] is expected_claim


def test_runner_rejects_symlink_duplicate_keys_and_unverified_dataset(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.json"
    candidate = tmp_path / "candidate.json"
    _write(dataset, _dataset())
    _write(candidate, _candidate())
    linked = tmp_path / "linked.json"
    linked.symlink_to(dataset)

    with pytest.raises(ValueError, match="input_file_invalid"):
        run_replay(
            dataset_path=linked, candidate_path=candidate, output_root=tmp_path / "linked-output"
        )

    dataset.write_text('{"contractId":"a","contractId":"b"}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate_json_key"):
        run_replay(
            dataset_path=dataset,
            candidate_path=candidate,
            output_root=tmp_path / "duplicate-output",
        )

    unverified = _dataset()
    unverified["entitlementStatus"] = "UNKNOWN"
    _write(dataset, unverified)
    with pytest.raises(ValueError, match="dataset_authority_unverified"):
        run_replay(
            dataset_path=dataset,
            candidate_path=candidate,
            output_root=tmp_path / "unverified-output",
        )


def test_numeric_or_chronology_mutation_fails_before_publication(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.json"
    _write(candidate, _candidate())
    dataset_value = _dataset()
    observations = dataset_value["observations"]
    assert isinstance(observations, list)
    first = observations[0]
    assert isinstance(first, dict)
    first["snapshotAvailableAt"] = "2022-01-03T21:59:59+00:00"
    dataset = tmp_path / "dataset.json"
    _write(dataset, dataset_value)

    with pytest.raises(ValueError, match="INVALID_CHRONOLOGY"):
        run_replay(dataset_path=dataset, candidate_path=candidate, output_root=tmp_path / "output")
    assert not (tmp_path / "output").exists()
