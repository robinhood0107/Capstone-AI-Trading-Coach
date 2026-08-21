"""Generate the internal P1 verification packet, report, and profile catalog."""

from __future__ import annotations

import argparse
import copy
from datetime import date, datetime
import json
from pathlib import Path
import sys
from typing import Any, Final, Mapping

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.generated_artifact_io import write_generated_artifact  # noqa: E402
from contracts.generate_principle_contracts import ContractValidationError  # noqa: E402
SCHEMA_IDS: Final[tuple[str, ...]] = (
    "p1-verification-packet.v1",
    "p1-verification-report.v1",
)
SCHEMA_PATHS: Final[dict[str, str]] = {
    schema_id: f"contracts/schemas/{schema_id}.schema.json" for schema_id in SCHEMA_IDS
}
PROFILE_IDS: Final[tuple[str, ...]] = (
    "S0_S5_CURRENT",
    "PROVIDER_READ_SMOKE",
    "S6_OFFLINE",
    "S7_RUNTIME",
    "P1_DEMO",
    "P1_LIVE_READINESS",
    "P1_FULL",
)
IMPLEMENTATION_STATES: Final[tuple[str, ...]] = (
    "IMPLEMENTED",
    "INTENTIONALLY_DISABLED",
    "NOT_IMPLEMENTED",
    "EXTERNAL_PLACEHOLDER",
)
EXECUTION_STATES: Final[tuple[str, ...]] = (
    "PASS",
    "FAIL",
    "BLOCKED",
    "NOT_RUN",
    "NOT_APPLICABLE",
)
AGGREGATE_OUTCOMES: Final[tuple[str, ...]] = ("PASS", "FAIL", "BLOCKED", "INCOMPLETE")
LIVE_OPERATIONS: Final[tuple[str, ...]] = (
    "KRX_KOSPI_DAILY",
    "KRX_KOSDAQ_DAILY",
    "KIS_CURRENT_PRICE",
    "KIS_DAILY_BAR",
    "ECOS_POLICY_RATE_DAILY",
    "ECOS_KRW_USD_DAILY",
)
S0_S5_REQUIRED_GATES: Final[tuple[str, ...]] = (
    "MARKET_DATA_OFFLINE_STATE_CHAIN",
    "DECISION_INTERNAL_PAPER_STATE_CHAIN",
    "LIGHTGBM_RESEARCH_ONLY_BOUNDARY",
    "MARKET_DATA_CHAIN_GUARD",
)


def _closed(required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def _sha256() -> dict[str, Any]:
    return {"type": "string", "pattern": "^[0-9a-f]{64}$"}


def _timestamp() -> dict[str, Any]:
    return {"type": "string", "format": "date-time"}


def _packet_schema() -> dict[str, Any]:
    body = _closed(
        [
            "contractId",
            "approvalId",
            "profile",
            "issuedAt",
            "expiresAt",
            "headSha",
            "treeSha256",
            "uvLockSha256",
            "contractCatalogSha256",
            "target",
            "operations",
            "providerDataPhysicalCallCap",
            "kisTokenPhysicalCallCap",
            "totalPhysicalCallCap",
            "retransmissionAllowed",
            "accountCallCap",
            "balanceCallCap",
            "orderCallCap",
            "productDbWriteAllowed",
        ],
        {
            "contractId": {"const": "p1-verification-packet.v1"},
            "approvalId": {"type": "string", "pattern": "^[A-Z0-9][A-Z0-9._-]{7,95}$"},
            "profile": {"const": "PROVIDER_READ_SMOKE"},
            "issuedAt": _timestamp(),
            "expiresAt": _timestamp(),
            "headSha": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
            "treeSha256": _sha256(),
            "uvLockSha256": _sha256(),
            "contractCatalogSha256": _sha256(),
            "target": _closed(
                ["sessionDate", "symbol", "ecosFrom", "ecosTo"],
                {
                    "sessionDate": {"type": "string", "format": "date"},
                    "symbol": {"const": "005930"},
                    "ecosFrom": {"type": "string", "format": "date"},
                    "ecosTo": {"type": "string", "format": "date"},
                },
            ),
            "operations": {
                "type": "array",
                "prefixItems": [{"const": operation} for operation in LIVE_OPERATIONS],
                "items": False,
                "minItems": len(LIVE_OPERATIONS),
                "maxItems": len(LIVE_OPERATIONS),
            },
            "providerDataPhysicalCallCap": {"const": 6},
            "kisTokenPhysicalCallCap": {"enum": [0, 1]},
            "totalPhysicalCallCap": {"enum": [6, 7]},
            "retransmissionAllowed": {"const": False},
            "accountCallCap": {"const": 0},
            "balanceCallCap": {"const": 0},
            "orderCallCap": {"const": 0},
            "productDbWriteAllowed": {"const": False},
        },
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_PATHS["p1-verification-packet.v1"],
        "title": "p1-verification-packet.v1",
        **body,
    }


def _gate_result() -> dict[str, Any]:
    nullable_sha = {"oneOf": [_sha256(), {"type": "null"}]}
    nullable_failure = {
        "oneOf": [
            {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{2,63}$"},
            {"type": "null"},
        ]
    }
    return _closed(
        [
            "gateId",
            "required",
            "implementationState",
            "executionState",
            "physicalCallCount",
            "evidenceSha256",
            "failureCode",
        ],
        {
            "gateId": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{2,63}$"},
            "required": {"type": "boolean"},
            "implementationState": {"enum": list(IMPLEMENTATION_STATES)},
            "executionState": {"enum": list(EXECUTION_STATES)},
            "physicalCallCount": {"type": "integer", "minimum": 0, "maximum": 7},
            "evidenceSha256": nullable_sha,
            "failureCode": nullable_failure,
        },
    )


def _report_schema() -> dict[str, Any]:
    body = _closed(
        [
            "contractId",
            "runId",
            "profile",
            "headSha",
            "startedAt",
            "completedAt",
            "implementationState",
            "executionState",
            "aggregateOutcome",
            "providerDataPhysicalCalls",
            "kisTokenPhysicalCalls",
            "accountCalls",
            "balanceCalls",
            "orderCalls",
            "liveOrderCalls",
            "productDbWrites",
            "gates",
            "evidenceSha256",
        ],
        {
            "contractId": {"const": "p1-verification-report.v1"},
            "runId": {"type": "string", "pattern": "^[a-z0-9][a-z0-9._-]{7,95}$"},
            "profile": {"enum": list(PROFILE_IDS)},
            "packetSha256": _sha256(),
            "headSha": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
            "startedAt": _timestamp(),
            "completedAt": _timestamp(),
            "implementationState": {"enum": list(IMPLEMENTATION_STATES)},
            "executionState": {"enum": list(EXECUTION_STATES)},
            "aggregateOutcome": {"enum": list(AGGREGATE_OUTCOMES)},
            "providerDataPhysicalCalls": {"type": "integer", "minimum": 0, "maximum": 6},
            "kisTokenPhysicalCalls": {"type": "integer", "minimum": 0, "maximum": 1},
            "accountCalls": {"const": 0},
            "balanceCalls": {"const": 0},
            "orderCalls": {"const": 0},
            "liveOrderCalls": {"const": 0},
            "productDbWrites": {"type": "integer", "minimum": 0},
            "gates": {
                "type": "array",
                "minItems": 1,
                "maxItems": 32,
                "items": _gate_result(),
            },
            "evidenceSha256": _sha256(),
        },
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_PATHS["p1-verification-report.v1"],
        "title": "p1-verification-report.v1",
        **body,
    }


def _catalog() -> dict[str, object]:
    return {
        "contractId": "p1-verification-catalog.v1",
        "aggregateOutcomes": list(AGGREGATE_OUTCOMES),
        "executionStates": list(EXECUTION_STATES),
        "implementationStates": list(IMPLEMENTATION_STATES),
        "profiles": [
            {
                "profile": "S0_S5_CURRENT",
                "providerAuthority": False,
                "currentImplementationState": "IMPLEMENTED",
                "requiredGates": list(S0_S5_REQUIRED_GATES),
            },
            {
                "profile": "PROVIDER_READ_SMOKE",
                "providerAuthority": True,
                "currentImplementationState": "NOT_IMPLEMENTED",
                "requiredGates": list(LIVE_OPERATIONS),
            },
            {
                "profile": "S6_OFFLINE",
                "providerAuthority": False,
                "currentImplementationState": "NOT_IMPLEMENTED",
            },
            {
                "profile": "S7_RUNTIME",
                "providerAuthority": False,
                "currentImplementationState": "NOT_IMPLEMENTED",
            },
            {
                "profile": "P1_DEMO",
                "providerAuthority": False,
                "currentImplementationState": "EXTERNAL_PLACEHOLDER",
            },
            {
                "profile": "P1_LIVE_READINESS",
                "providerAuthority": False,
                "currentImplementationState": "NOT_IMPLEMENTED",
            },
            {
                "profile": "P1_FULL",
                "providerAuthority": False,
                "currentImplementationState": "EXTERNAL_PLACEHOLDER",
            },
        ],
        "providerReadSmoke": {
            "accountCallCap": 0,
            "balanceCallCap": 0,
            "dataPhysicalCallCap": 6,
            "kisTokenPhysicalCallCap": 1,
            "liveOrderCallCap": 0,
            "operations": list(LIVE_OPERATIONS),
            "productDbWriteAllowed": False,
            "retransmissionAllowed": False,
            "ttlSecondsMax": 3600,
        },
        "offlineReplay": {
            "monthBoundaryOperationCount": 41,
            "normalOperationCount": 38,
            "providerPhysicalCalls": 0,
        },
        "lightgbmProduction": "INTENTIONALLY_DISABLED",
        "publicApi": False,
        "scheduler": False,
    }


def _valid_packet() -> dict[str, object]:
    return {
        "contractId": "p1-verification-packet.v1",
        "approvalId": "P1.V1-20260821-READ-SMOKE",
        "profile": "PROVIDER_READ_SMOKE",
        "issuedAt": "2026-08-21T09:00:00Z",
        "expiresAt": "2026-08-21T10:00:00Z",
        "headSha": "a" * 40,
        "treeSha256": "b" * 64,
        "uvLockSha256": "c" * 64,
        "contractCatalogSha256": "d" * 64,
        "target": {
            "sessionDate": "2026-08-20",
            "symbol": "005930",
            "ecosFrom": "2026-07-22",
            "ecosTo": "2026-08-20",
        },
        "operations": list(LIVE_OPERATIONS),
        "providerDataPhysicalCallCap": 6,
        "kisTokenPhysicalCallCap": 1,
        "totalPhysicalCallCap": 7,
        "retransmissionAllowed": False,
        "accountCallCap": 0,
        "balanceCallCap": 0,
        "orderCallCap": 0,
        "productDbWriteAllowed": False,
    }


def _valid_report() -> dict[str, object]:
    gates = [
        {
            "gateId": operation,
            "required": True,
            "implementationState": "IMPLEMENTED",
            "executionState": "PASS",
            "physicalCallCount": 1,
            "evidenceSha256": f"{index:x}" * 64,
            "failureCode": None,
        }
        for index, operation in enumerate(LIVE_OPERATIONS, start=1)
    ]
    return {
        "contractId": "p1-verification-report.v1",
        "runId": "p1v1-20260821-read-smoke",
        "profile": "PROVIDER_READ_SMOKE",
        "packetSha256": "e" * 64,
        "headSha": "a" * 40,
        "startedAt": "2026-08-21T09:01:00Z",
        "completedAt": "2026-08-21T09:02:00Z",
        "implementationState": "IMPLEMENTED",
        "executionState": "PASS",
        "aggregateOutcome": "PASS",
        "providerDataPhysicalCalls": 6,
        "kisTokenPhysicalCalls": 0,
        "accountCalls": 0,
        "balanceCalls": 0,
        "orderCalls": 0,
        "liveOrderCalls": 0,
        "productDbWrites": 0,
        "gates": gates,
        "evidenceSha256": "f" * 64,
    }


def validate_semantics(contract_id: str, payload: object) -> None:
    if not isinstance(payload, dict):
        raise ContractValidationError("P1 verification payload must be an object")
    if contract_id == "p1-verification-packet.v1":
        issued = datetime.fromisoformat(str(payload["issuedAt"]).replace("Z", "+00:00"))
        expires = datetime.fromisoformat(str(payload["expiresAt"]).replace("Z", "+00:00"))
        if issued.tzinfo is None or expires.tzinfo is None or not issued < expires:
            raise ContractValidationError("P1 verification packet lifetime is invalid")
        if (expires - issued).total_seconds() > 3600:
            raise ContractValidationError("P1 verification packet TTL exceeds 60 minutes")
        target = payload["target"]
        if not isinstance(target, dict):
            raise ContractValidationError("P1 verification target is invalid")
        session = date.fromisoformat(str(target["sessionDate"]))
        if date.fromisoformat(str(target["ecosTo"])) != session:
            raise ContractValidationError("P1 ECOS end date must equal the target session")
        if (session - date.fromisoformat(str(target["ecosFrom"]))).days != 29:
            raise ContractValidationError("P1 ECOS window must be exact D-29 through D")
        if tuple(payload["operations"]) != LIVE_OPERATIONS:
            raise ContractValidationError("P1 provider operation order drifted")
        token_cap = payload["kisTokenPhysicalCallCap"]
        if payload["totalPhysicalCallCap"] != 6 + token_cap:
            raise ContractValidationError("P1 total physical cap is not derived from data plus token")
        return
    if contract_id != "p1-verification-report.v1":
        raise ContractValidationError("unknown P1 verification contract")
    started = datetime.fromisoformat(str(payload["startedAt"]).replace("Z", "+00:00"))
    completed = datetime.fromisoformat(str(payload["completedAt"]).replace("Z", "+00:00"))
    if started.tzinfo is None or completed.tzinfo is None or completed < started:
        raise ContractValidationError("P1 verification report time order is invalid")
    gates = payload["gates"]
    if not isinstance(gates, list) or len({gate["gateId"] for gate in gates}) != len(gates):
        raise ContractValidationError("P1 verification gate ids must be unique")
    if any(
        gate["implementationState"] in {"NOT_IMPLEMENTED", "EXTERNAL_PLACEHOLDER"}
        and gate["executionState"] == "PASS"
        for gate in gates
    ):
        raise ContractValidationError("unimplemented P1 gate cannot pass")
    if payload["executionState"] == "PASS" and payload["aggregateOutcome"] != "PASS":
        raise ContractValidationError("passing P1 execution must have PASS aggregate")
    if payload["profile"] == "S0_S5_CURRENT" and payload["executionState"] == "PASS":
        by_id = {gate["gateId"]: gate for gate in gates}
        if set(by_id) != set(S0_S5_REQUIRED_GATES):
            raise ContractValidationError("S0-S5 PASS requires the exact offline gates")
        if any(
            by_id[gate_id]["implementationState"] != "IMPLEMENTED"
            or by_id[gate_id]["executionState"] != "PASS"
            or by_id[gate_id]["physicalCallCount"] != 0
            or by_id[gate_id]["failureCode"] is not None
            for gate_id in S0_S5_REQUIRED_GATES
        ):
            raise ContractValidationError("S0-S5 PASS requires provider-free gate success")
    if payload["profile"] == "PROVIDER_READ_SMOKE" and payload["executionState"] == "PASS":
        by_id = {gate["gateId"]: gate for gate in gates}
        if set(by_id) != set(LIVE_OPERATIONS):
            raise ContractValidationError("provider smoke PASS requires the exact six gates")
        if any(
            by_id[operation]["executionState"] != "PASS"
            or by_id[operation]["physicalCallCount"] != 1
            or by_id[operation]["failureCode"] is not None
            for operation in LIVE_OPERATIONS
        ):
            raise ContractValidationError("provider smoke PASS requires six single-attempt successes")
        if payload["providerDataPhysicalCalls"] != 6 or payload["productDbWrites"] != 0:
            raise ContractValidationError("provider smoke PASS accounting is invalid")


def artifacts() -> dict[str, bytes]:
    packet = _valid_packet()
    report = _valid_report()
    invalid_ttl = copy.deepcopy(packet)
    invalid_ttl["expiresAt"] = "2026-08-21T10:00:01Z"
    invalid_cap = copy.deepcopy(packet)
    invalid_cap["totalPhysicalCallCap"] = 6
    invalid_raw = copy.deepcopy(report)
    invalid_raw["providerBody"] = {"secret": "forbidden"}
    invalid_pass = copy.deepcopy(report)
    invalid_pass["providerDataPhysicalCalls"] = 5
    values: dict[str, object] = {
        SCHEMA_PATHS["p1-verification-packet.v1"]: _packet_schema(),
        SCHEMA_PATHS["p1-verification-report.v1"]: _report_schema(),
        "contracts/catalogs/p1-verification-catalog.v1.json": _catalog(),
        "contracts/examples/p1-verification-packet.v1.valid.json": packet,
        "contracts/examples/p1-verification-report.v1.valid.json": report,
        "contracts/examples/invalid/p1-verification-packet.v1.ttl.invalid.json": invalid_ttl,
        "contracts/examples/invalid/p1-verification-packet.v1.cap.invalid.json": invalid_cap,
        "contracts/examples/invalid/p1-verification-report.v1.raw.invalid.json": invalid_raw,
        "contracts/examples/invalid/p1-verification-report.v1.false-pass.invalid.json": invalid_pass,
    }
    return {
        relative: (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        for relative, value in values.items()
    }


def _validate_generated(outputs: Mapping[str, bytes]) -> None:
    validators = {
        schema_id: Draft202012Validator(
            json.loads(outputs[SCHEMA_PATHS[schema_id]]), format_checker=FormatChecker()
        )
        for schema_id in SCHEMA_IDS
    }
    for relative, content in outputs.items():
        if "/examples/" not in relative or "/invalid/" in relative:
            continue
        payload = json.loads(content)
        contract_id = payload["contractId"]
        errors = list(validators[contract_id].iter_errors(payload))
        if errors:
            raise ContractValidationError(f"generated P1 fixture failed schema: {relative}")
        validate_semantics(contract_id, payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.write and args.check:
        parser.error("--write and --check are mutually exclusive")
    generated = artifacts()
    _validate_generated(generated)
    drift: list[str] = []
    for relative, content in generated.items():
        path = ROOT / relative
        if args.write:
            write_generated_artifact(ROOT, relative, content)
        elif path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            drift.append(relative)
    if drift:
        raise SystemExit("generated P1 verification artifacts drifted:\n" + "\n".join(drift))
    print("P1_VERIFICATION_CONTRACTS_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
