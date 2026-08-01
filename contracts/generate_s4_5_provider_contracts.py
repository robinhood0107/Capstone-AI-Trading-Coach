from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from jsonschema import Draft202012Validator

_SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))

from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    canonical_json_bytes,
)
from contracts.generated_artifact_io import write_generated_artifact  # noqa: E402


ROOT = _SCRIPT_ROOT
VOYAGE_SCHEMA_PATH = ROOT / "contracts/schemas/s4-2c-voyage-approval.schema.json"
GEMINI_SCHEMA_PATH = ROOT / "contracts/schemas/s4-4g-gemini-approval.schema.json"
OUTPUTS: Final[frozenset[str]] = frozenset(
    {
        "contracts/schemas/s4-2c-voyage-approval.schema.json",
        "contracts/schemas/s4-4g-gemini-approval.schema.json",
        "contracts/examples/s4-2c-voyage-approval.valid.json",
        "contracts/examples/s4-4g-gemini-approval.valid.json",
        "contracts/examples/invalid/s4-2c-voyage-approval.paid.invalid.json",
        "contracts/examples/invalid/s4-4g-gemini-approval.store.invalid.json",
    }
)
_HASH: Final[dict[str, object]] = {
    "pattern": "^[0-9a-f]{64}$",
    "type": "string",
}


def _closed(properties: Mapping[str, object]) -> dict[str, object]:
    return {
        "additionalProperties": False,
        "properties": dict(properties),
        "required": list(properties),
        "type": "object",
    }


def _document(schema_id: str, properties: Mapping[str, object]) -> dict[str, object]:
    return {
        "$id": f"contracts/schemas/{schema_id}.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": schema_id,
        **_closed(properties),
    }


def voyage_schema() -> dict[str, object]:
    """Voyage one-shot generation approval packet의 exact zero-paid 계약이다."""

    return _document(
        "s4-2c-voyage-approval",
        {
            "balanceSnapshotSha256": _HASH,
            "contextSetHash": _HASH,
            "corpusManifestSha256": _HASH,
            "generationPlanSha256": _HASH,
            "paidHardCapUsd": {"const": 0},
            "physicalBatchCap": {"const": 1},
            "projectFingerprintSha256": _HASH,
            "provider": {"const": "VOYAGE"},
            "purpose": {
                "enum": ["EVALUATION_ONLY", "SLA_FALLBACK_CANDIDATE"]
            },
            "retryCount": {"const": 0},
            "schemaVersion": {"const": "s4-2c-voyage-approval/v1"},
            "state": {"const": "APPROVED"},
            "zdrOptOutEvidenceSha256": _HASH,
        },
    )


def gemini_schema() -> dict[str, object]:
    """Gemini preflight/evaluation/activation approval packet의 목적 분리 계약이다."""

    return _document(
        "s4-4g-gemini-approval",
        {
            "evaluationManifestSha256": _HASH,
            "loggingPolicyEvidenceSha256": _HASH,
            "logicalCallCap": {"const": 60},
            "model": {"const": "gemini-3.5-flash-lite"},
            "paidProject": {"const": True},
            "physicalCallCap": {"const": 60},
            "projectFingerprintSha256": _HASH,
            "promptSha256": _HASH,
            "provider": {"const": "GEMINI"},
            "purpose": {
                "enum": ["PREFLIGHT", "EVALUATION", "PRODUCTION_ACTIVATION"]
            },
            "responseSchemaSha256": _HASH,
            "retryCount": {"const": 0},
            "schemaVersion": {"const": "s4-4g-gemini-approval/v1"},
            "state": {"const": "APPROVED"},
            "store": {"const": False},
            "zdrEvidenceSha256": _HASH,
        },
    )


def _voyage_fixture() -> dict[str, object]:
    return {
        "balanceSnapshotSha256": "b" * 64,
        "contextSetHash": "c" * 64,
        "corpusManifestSha256": (
            "bdc42bfb735b411156ec2f79626d6fd2cf56662c57d83e2cdb960fb74e7b0e04"
        ),
        "generationPlanSha256": "d" * 64,
        "paidHardCapUsd": 0,
        "physicalBatchCap": 1,
        "projectFingerprintSha256": "a" * 64,
        "provider": "VOYAGE",
        "purpose": "EVALUATION_ONLY",
        "retryCount": 0,
        "schemaVersion": "s4-2c-voyage-approval/v1",
        "state": "APPROVED",
        "zdrOptOutEvidenceSha256": "e" * 64,
    }


def _gemini_fixture() -> dict[str, object]:
    return {
        "evaluationManifestSha256": "1" * 64,
        "loggingPolicyEvidenceSha256": "2" * 64,
        "logicalCallCap": 60,
        "model": "gemini-3.5-flash-lite",
        "paidProject": True,
        "physicalCallCap": 60,
        "projectFingerprintSha256": "3" * 64,
        "promptSha256": "4" * 64,
        "provider": "GEMINI",
        "purpose": "EVALUATION",
        "responseSchemaSha256": "5" * 64,
        "retryCount": 0,
        "schemaVersion": "s4-4g-gemini-approval/v1",
        "state": "APPROVED",
        "store": False,
        "zdrEvidenceSha256": "6" * 64,
    }


def generate_outputs() -> dict[str, bytes]:
    voyage = voyage_schema()
    gemini = gemini_schema()
    Draft202012Validator.check_schema(voyage)
    Draft202012Validator.check_schema(gemini)
    voyage_valid = _voyage_fixture()
    gemini_valid = _gemini_fixture()
    voyage_invalid = {**voyage_valid, "paidHardCapUsd": 1}
    gemini_invalid = {**gemini_valid, "store": True}
    if list(Draft202012Validator(voyage).iter_errors(voyage_valid)):
        raise ContractValidationError("generated Voyage positive fixture is invalid")
    if list(Draft202012Validator(gemini).iter_errors(gemini_valid)):
        raise ContractValidationError("generated Gemini positive fixture is invalid")
    if not list(Draft202012Validator(voyage).iter_errors(voyage_invalid)):
        raise ContractValidationError("generated Voyage negative fixture passed")
    if not list(Draft202012Validator(gemini).iter_errors(gemini_invalid)):
        raise ContractValidationError("generated Gemini negative fixture passed")
    outputs = {
        "contracts/schemas/s4-2c-voyage-approval.schema.json": canonical_json_bytes(
            voyage
        ),
        "contracts/schemas/s4-4g-gemini-approval.schema.json": canonical_json_bytes(
            gemini
        ),
        "contracts/examples/s4-2c-voyage-approval.valid.json": canonical_json_bytes(
            voyage_valid
        ),
        "contracts/examples/s4-4g-gemini-approval.valid.json": canonical_json_bytes(
            gemini_valid
        ),
        "contracts/examples/invalid/s4-2c-voyage-approval.paid.invalid.json": canonical_json_bytes(
            voyage_invalid
        ),
        "contracts/examples/invalid/s4-4g-gemini-approval.store.invalid.json": canonical_json_bytes(
            gemini_invalid
        ),
    }
    if frozenset(outputs) != OUTPUTS:
        raise ContractValidationError("S4.5 provider output manifest drifted")
    return dict(sorted(outputs.items()))


def _check(outputs: Mapping[str, bytes]) -> int:
    drifted = [
        relative
        for relative, expected in outputs.items()
        if not (ROOT / relative).is_file() or (ROOT / relative).read_bytes() != expected
    ]
    if drifted:
        for relative in drifted:
            print(f"DRIFT {relative}")
        return 1
    print("S4_5_PROVIDER_CONTROL_CONTRACTS_VERIFIED")
    return 0


def _write(outputs: Mapping[str, bytes]) -> int:
    for relative, payload in outputs.items():
        write_generated_artifact(ROOT, relative, payload)
    print("S4_5_PROVIDER_CONTROL_CONTRACTS_WRITTEN")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the S4.5 provider approval packet contracts."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    outputs = generate_outputs()
    return _check(outputs) if arguments.check else _write(outputs)


if __name__ == "__main__":
    raise SystemExit(main())
