from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "contracts" / "schemas"
EXAMPLE_DIR = ROOT / "contracts" / "examples"
INVALID_DIR = EXAMPLE_DIR / "invalid"
CATALOG_DIR = ROOT / "contracts" / "catalogs"

SCHEMA_IDS = ("s6-capability-disposition.v1",)
ACTIVE_SESSIONS = ("S6.1", "S6.2", "S6.3", "S6.4", "S6.5")
RETIRED_SESSIONS = ("S6.6", "S6.7")
ACTIVE_SCHEMA_IDS = (
    "hmm_regime_report.v1",
    "gbm_monte_carlo_report.v1",
    "mean_reversion_report.v1",
    "financial_engineering_snapshot.v1",
    "financial_engineering_report_manifest.v1",
    "option_contract_terms.v1",
)
HISTORICAL_ONLY_CONTRACTS = (
    "cross_market_event_study.v2",
    "lightgbm_policy_replay.v1",
    "cross_market_threshold_freeze.v1",
    "cross_market_risk_snapshot.v2",
)
HISTORICAL_ONLY_CATALOGS = (
    "s2-2-system-rule-catalog.v2",
    "s2-2-system-rule-catalog.v3",
)
HISTORICAL_HASHES = {
    "contracts/catalogs/s6-contract-lock.v1.json": "07b9405f224661343127056a020a37ec9c7453dd1b923db35c37e5371964c646",
    "contracts/catalogs/s2-2-system-rule-catalog.v1.json": "a4714ee9ce3031199b9067919b15931fb42e106857da5f8d8ad7a95bafa8ad7b",
    "contracts/catalogs/s2-2-system-rule-catalog.v2.json": "bd812439694cc55aa8eca61f7e8aebe371ef0a55040f2e2134f103449b18da70",
    "contracts/catalogs/s2-2-system-rule-catalog.v3.json": "3361e9ec2f4a572573592a20f3da360c35fcd5123a84657acb06fa2730ed55e5",
    "contracts/schemas/cross_market_event_study.v2.schema.json": "388802c074031d5f55b03c678cf490bf646c3b492408be42cc731b1647a8aca7",
    "contracts/schemas/lightgbm_policy_replay.v1.schema.json": "048d2a3f05579dbe69c77daacc0213634c61d61c112412295d281ee895c6b663",
    "contracts/schemas/cross_market_threshold_freeze.v1.schema.json": "4f3c25f8e39f04dba4db3095a4ec90beb98d3be25577e7aa4c826db2204cc76c",
    "contracts/schemas/cross_market_risk_snapshot.v2.schema.json": "78934ca6cce44a0ea1cc79c9b271c1f967275c49437d4422c36bec9364e32a1e",
}


class ContractValidationError(ValueError):
    pass


def _fixed_array(values: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": "array",
        "prefixItems": [{"const": value} for value in values],
        "items": False,
        "minItems": len(values),
        "maxItems": len(values),
    }


SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://capstone.local/contracts/s6-capability-disposition.v1",
    "title": "s6-capability-disposition.v1",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "contractId",
        "decidedOn",
        "activeSessions",
        "retiredSessions",
        "retirementReason",
        "strictPitStatus",
        "s4_8Status",
        "runtimeCatalog",
        "historicalOnlyCatalogs",
        "historicalOnlyContracts",
        "crossMarketAuthorities",
        "feasibilityProbe",
        "priorArtifactHashes",
    ],
    "properties": {
        "contractId": {"const": "s6-capability-disposition.v1"},
        "decidedOn": {"const": "2026-08-21"},
        "activeSessions": _fixed_array(ACTIVE_SESSIONS),
        "retiredSessions": _fixed_array(RETIRED_SESSIONS),
        "retirementReason": {"const": "STRICT_REAL_PIT_UNAVAILABLE_FREE_NOW"},
        "strictPitStatus": {"const": "RETIRED_NOT_APPLICABLE"},
        "s4_8Status": {"const": "VERIFIED_OFFLINE_STORED"},
        "runtimeCatalog": {"const": "s2-2-system-rule-catalog.v1"},
        "historicalOnlyCatalogs": _fixed_array(HISTORICAL_ONLY_CATALOGS),
        "historicalOnlyContracts": _fixed_array(HISTORICAL_ONLY_CONTRACTS),
        "crossMarketAuthorities": {
            "type": "object",
            "additionalProperties": False,
            "required": ["decision", "runtime", "writer", "reader", "signal", "order"],
            "properties": {
                name: {"const": "NONE"}
                for name in ("decision", "runtime", "writer", "reader", "signal", "order")
            },
        },
        "feasibilityProbe": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "physicalCalls",
                "retryCount",
                "costKrw",
                "runtimeProviderCalls",
                "liveAccountCalls",
                "orderCalls",
                "rawProviderMaterialStored",
            ],
            "properties": {
                "physicalCalls": {"const": 14},
                "retryCount": {"const": 0},
                "costKrw": {"const": 0},
                "runtimeProviderCalls": {"const": 0},
                "liveAccountCalls": {"const": 0},
                "orderCalls": {"const": 0},
                "rawProviderMaterialStored": {"const": False},
            },
        },
        "priorArtifactHashes": {
            "type": "array",
            "minItems": len(HISTORICAL_HASHES),
            "maxItems": len(HISTORICAL_HASHES),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "sha256"],
                "properties": {
                    "path": {"type": "string", "minLength": 1, "maxLength": 256},
                    "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                },
            },
        },
    },
}


def _prior_hash_fixture() -> list[dict[str, str]]:
    return [
        {"path": path, "sha256": digest}
        for path, digest in sorted(HISTORICAL_HASHES.items())
    ]


VALID_FIXTURE: dict[str, Any] = {
    "contractId": "s6-capability-disposition.v1",
    "decidedOn": "2026-08-21",
    "activeSessions": list(ACTIVE_SESSIONS),
    "retiredSessions": list(RETIRED_SESSIONS),
    "retirementReason": "STRICT_REAL_PIT_UNAVAILABLE_FREE_NOW",
    "strictPitStatus": "RETIRED_NOT_APPLICABLE",
    "s4_8Status": "VERIFIED_OFFLINE_STORED",
    "runtimeCatalog": "s2-2-system-rule-catalog.v1",
    "historicalOnlyCatalogs": list(HISTORICAL_ONLY_CATALOGS),
    "historicalOnlyContracts": list(HISTORICAL_ONLY_CONTRACTS),
    "crossMarketAuthorities": {
        "decision": "NONE",
        "runtime": "NONE",
        "writer": "NONE",
        "reader": "NONE",
        "signal": "NONE",
        "order": "NONE",
    },
    "feasibilityProbe": {
        "physicalCalls": 14,
        "retryCount": 0,
        "costKrw": 0,
        "runtimeProviderCalls": 0,
        "liveAccountCalls": 0,
        "orderCalls": 0,
        "rawProviderMaterialStored": False,
    },
    "priorArtifactHashes": _prior_hash_fixture(),
}


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _assert_historical_authority() -> None:
    drift = []
    for relative, expected in HISTORICAL_HASHES.items():
        path = ROOT / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"
        if actual != expected:
            drift.append(relative)
    if drift:
        raise ContractValidationError("historical S6 authority drift: " + ", ".join(drift))


def validate_semantics(schema_id: str, payload: dict[str, Any]) -> None:
    if schema_id != SCHEMA_IDS[0]:
        raise ContractValidationError(f"unknown retirement schema: {schema_id}")
    if tuple(payload.get("activeSessions", ())) != ACTIVE_SESSIONS:
        raise ContractValidationError("active S6 sessions must remain S6.1 through S6.5")
    if tuple(payload.get("retiredSessions", ())) != RETIRED_SESSIONS:
        raise ContractValidationError("retired S6 sessions must be S6.6 and S6.7")
    authorities = payload.get("crossMarketAuthorities")
    if not isinstance(authorities, dict) or set(authorities.values()) != {"NONE"}:
        raise ContractValidationError("retired cross-market authorities must all be NONE")
    if payload.get("priorArtifactHashes") != _prior_hash_fixture():
        raise ContractValidationError("historical authority hash receipt mismatch")


def build_outputs() -> dict[Path, bytes]:
    _assert_historical_authority()
    invalid = json.loads(json.dumps(VALID_FIXTURE))
    invalid["crossMarketAuthorities"]["runtime"] = "WARN_ONLY"
    outputs = {
        SCHEMA_DIR / "s6-capability-disposition.v1.schema.json": _canonical_bytes(SCHEMA),
        EXAMPLE_DIR / "s6-capability-disposition.v1.valid.json": _canonical_bytes(VALID_FIXTURE),
        INVALID_DIR / "s6-capability-disposition.v1.contract.invalid.json": _canonical_bytes(invalid),
    }
    generated_set_hash = hashlib.sha256(
        b"".join(outputs[path] for path in sorted(outputs))
    ).hexdigest()
    lock = {
        "contractId": "s6-contract-lock.v2",
        "supersedesCurrentAuthority": "s6-contract-lock.v1",
        "priorLockHash": HISTORICAL_HASHES["contracts/catalogs/s6-contract-lock.v1.json"],
        "dispositionContract": "s6-capability-disposition.v1",
        "activeSessions": list(ACTIVE_SESSIONS),
        "activeSchemaIds": list(ACTIVE_SCHEMA_IDS),
        "retiredSessions": list(RETIRED_SESSIONS),
        "retiredStatus": "RETIRED_NOT_APPLICABLE",
        "runtimeCatalog": "s2-2-system-rule-catalog.v1",
        "historicalOnlyCatalogs": list(HISTORICAL_ONLY_CATALOGS),
        "historicalOnlyContracts": list(HISTORICAL_ONLY_CONTRACTS),
        "crossMarketRuntimeAuthority": "NONE",
        "providerRuntimeCallsAllowed": False,
        "generatedSetHash": generated_set_hash,
    }
    outputs[CATALOG_DIR / "s6-contract-lock.v2.json"] = _canonical_bytes(lock)
    return outputs


def write_or_check(*, check: bool) -> None:
    mismatches: list[str] = []
    for path, expected in build_outputs().items():
        if check:
            if not path.is_file() or path.read_bytes() != expected:
                mismatches.append(path.relative_to(ROOT).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)
    if mismatches:
        raise SystemExit("S6 retirement contract drift: " + ", ".join(mismatches))
    print("S6_RETIREMENT_CONTRACT_VERIFIED" if check else "S6_RETIREMENT_CONTRACT_GENERATED")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    write_or_check(check=arguments.check)


if __name__ == "__main__":
    main()
