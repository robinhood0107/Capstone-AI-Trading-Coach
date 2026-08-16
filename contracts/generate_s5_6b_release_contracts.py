"""S5.6B immutable model release와 exact signal batch manifest 계약을 생성한다."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHA = "^[0-9a-f]{64}$"


def _schema() -> tuple[dict[str, object], dict[str, object]]:
    release_properties: dict[str, object] = {
        "releaseVersion": {"const": "s5-model-release-v1"},
        "modelReleaseId": {"type": "string", "pattern": "^lgr-[0-9a-f]{12}$"},
        "modelVersion": {"type": "string", "pattern": "^lgbm-v1-[0-9a-f]{12}$"},
        "modelReportId": {"type": "string", "pattern": "^mrp-[0-9a-f]{12}$"},
        "featureManifestSha256": {"type": "string", "pattern": SHA},
        "sourceBundleSetSha256": {"type": "string", "pattern": SHA},
        "sourcePolicySetSha256": {"type": "string", "pattern": SHA},
        "trainingDatasetSha256": {"type": "string", "pattern": SHA},
        "codeHead": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
        "codeTree": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
        "uvLockSha256": {"type": "string", "pattern": SHA},
        "calendarName": {"const": "XKRX"},
        "calendarVersion": {"const": "4.13.2"},
        "temporalQuality": {"const": "RECONSTRUCTED_FIXED_LAG"},
        "fixture": {"const": False},
        "provenanceClass": {"const": "PRODUCTION"},
        "status": {"const": "QUALIFIED"},
        "files": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "model.txt", "calibrator.json", "report.json", "gain-importance.json",
                "contribution-report.json", "qualification.json",
            ],
            "properties": {
                name: {"type": "string", "pattern": SHA}
                for name in (
                    "model.txt", "calibrator.json", "report.json", "gain-importance.json",
                    "contribution-report.json", "qualification.json",
                )
            },
        },
        "semanticSha256": {"type": "string", "pattern": SHA},
    }
    release = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/s5-model-release-v1.schema.json",
        "type": "object",
        "additionalProperties": False,
        "required": list(release_properties),
        "properties": release_properties,
    }
    batch_properties: dict[str, object] = {
        "batchVersion": {"const": "s5-signal-batch-v1"},
        "batchPurpose": {"enum": ["DAILY", "ROLLBACK"]},
        "signalBatchId": {"type": "string", "pattern": "^sgb-[0-9a-f]{12}$"},
        "modelReleaseId": {"type": "string", "pattern": "^lgr-[0-9a-f]{12}$"},
        "universeReleaseId": {"type": "string", "pattern": "^sur-[0-9a-f]{12}$"},
        "membershipSha256": {"type": "string", "pattern": SHA},
        "sessionDate": {"type": "string", "format": "date"},
        "asOf": {"type": "string", "format": "date-time"},
        "timeframe": {"const": "1d"},
        "rowCount": {"const": 31},
        "membersSha256": {"type": "string", "pattern": SHA},
        "parquetFile": {"const": "signals.parquet"},
        "parquetSha256": {"type": "string", "pattern": SHA},
        "fixture": {"const": False},
        "provenanceClass": {"const": "PRODUCTION"},
        "semanticSha256": {"type": "string", "pattern": SHA},
    }
    batch = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/s5-signal-batch-v1.schema.json",
        "type": "object",
        "additionalProperties": False,
        "required": list(batch_properties),
        "properties": batch_properties,
    }
    return release, batch


def artifacts() -> dict[str, bytes]:
    release_schema, batch_schema = _schema()
    release = {
        "releaseVersion": "s5-model-release-v1",
        "modelReleaseId": "lgr-111111111111",
        "modelVersion": "lgbm-v1-222222222222",
        "modelReportId": "mrp-333333333333",
        "featureManifestSha256": "a" * 64,
        "sourceBundleSetSha256": "b" * 64,
        "sourcePolicySetSha256": "c" * 64,
        "trainingDatasetSha256": "d" * 64,
        "codeHead": "e" * 40,
        "codeTree": "f" * 40,
        "uvLockSha256": "1" * 64,
        "calendarName": "XKRX",
        "calendarVersion": "4.13.2",
        "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
        "fixture": False,
        "provenanceClass": "PRODUCTION",
        "status": "QUALIFIED",
        "files": {name: "2" * 64 for name in release_schema["properties"]["files"]["required"]},  # type: ignore[index]
        "semanticSha256": "3" * 64,
    }
    batch = {
        "batchVersion": "s5-signal-batch-v1",
        "batchPurpose": "DAILY",
        "signalBatchId": "sgb-444444444444",
        "modelReleaseId": "lgr-111111111111",
        "universeReleaseId": "sur-555555555555",
        "membershipSha256": "5" * 64,
        "sessionDate": "2026-08-14",
        "asOf": "2026-08-17T23:10:00Z",
        "timeframe": "1d",
        "rowCount": 31,
        "membersSha256": "8" * 64,
        "parquetFile": "signals.parquet",
        "parquetSha256": "6" * 64,
        "fixture": False,
        "provenanceClass": "PRODUCTION",
        "semanticSha256": "7" * 64,
    }
    release_unknown = copy.deepcopy(release)
    release_unknown["modelScore"] = [0.1, 0.2, 0.7]
    release_fake = copy.deepcopy(release)
    release_fake["fixture"] = True
    batch_count = copy.deepcopy(batch)
    batch_count["rowCount"] = 30
    batch_pointer = copy.deepcopy(batch)
    batch_pointer["artifactPath"] = "/tmp/model"
    catalog = {
        "contractId": "s5-production-release-lock.v1",
        "modelReleaseFiles": [
            "release.json", "model.txt", "calibrator.json", "report.json",
            "gain-importance.json", "contribution-report.json", "qualification.json",
        ],
        "signalBatchFiles": ["batch.json", "signals.parquet"],
        "activation": "MANUAL_EXPECTED_CURRENT_CAS",
        "membership": {"policy": "top30-plus-132030-v1", "exactRows": 31},
        "sessionClock": {
            "calendar": "XKRX",
            "calendarVersion": "4.13.2",
            "asOfPolicy": "NEXT_COMPLETED_XKRX_SESSION_08_10_ASIA_SEOUL",
            "calendarDatePlusOneAllowed": False,
            "regression": {
                "sessionDate": "2026-08-14",
                "nextSession": "2026-08-18",
                "asOf": "2026-08-17T23:10:00Z",
            },
        },
        "dailyRefresh": {
            "packetVersion": "s5-daily-refresh-packet-v1",
            "stateVersion": "s5-daily-inference-state-v1",
            "singleSessionOnly": True,
            "missedSessionResume": "EXPLICIT_NEXT_XKRX_SESSION_ONLY",
            "maxPhysicalCalls": 41,
            "retry": 0,
            "failedQueryResumeMax": 1,
            "resumeDoesNotIncreaseCaps": True,
            "localFinalizationProviderCalls": 0,
        },
        "rollback": {
            "batchPurpose": "ROLLBACK",
            "freshCurrentSessionBatchRequired": True,
            "oldBatchReuseAllowed": False,
        },
        "automaticRetrain": 0,
        "automaticModelActivation": 0,
        "riskDecisionWiring": 0,
        "orderWiring": 0,
    }

    def encode(value: object) -> bytes:
        return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()

    return {
        "contracts/schemas/s5-model-release-v1.schema.json": encode(release_schema),
        "contracts/schemas/s5-signal-batch-v1.schema.json": encode(batch_schema),
        "contracts/catalogs/s5-production-release-lock.v1.json": encode(catalog),
        "contracts/examples/s5-model-release-v1.valid.json": encode(release),
        "contracts/examples/s5-signal-batch-v1.valid.json": encode(batch),
        "contracts/examples/invalid/s5-model-release-v1.unknown-field.invalid.json": encode(release_unknown),
        "contracts/examples/invalid/s5-model-release-v1.fake.invalid.json": encode(release_fake),
        "contracts/examples/invalid/s5-signal-batch-v1.row-count.invalid.json": encode(batch_count),
        "contracts/examples/invalid/s5-signal-batch-v1.artifact-path.invalid.json": encode(batch_pointer),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    drift: list[str] = []
    for relative, content in artifacts().items():
        path = ROOT / relative
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        elif not path.is_file() or path.read_bytes() != content:
            drift.append(relative)
    if drift:
        raise SystemExit("generated S5.6B artifacts drifted:\n" + "\n".join(drift))
    print("S5_6B_RELEASE_CONTRACTS_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
