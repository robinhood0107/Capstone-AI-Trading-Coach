from __future__ import annotations

import argparse
import copy
import hashlib
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

_SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from contracts.generated_artifact_io import write_generated_artifact  # noqa: E402
from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    canonical_json_bytes,
    load_json_bytes_strict,
)
from contracts.generate_s2_2_contracts import (  # noqa: E402
    CATALOG_PATH as S22_CATALOG_PATH,
    generate_outputs as generate_s22_outputs,
    load_catalog as load_s22_catalog,
    validate_risk_decision_semantics,
)

REPO_ROOT = _SCRIPT_REPO_ROOT
CATALOG_PATH = REPO_ROOT / "contracts/catalogs/s2-3-decision-contract.v1.json"
EXPECTED_CATALOG_SHA256: Final[str] = (
    "d035607af50a0f7cb9cd7170e9a6a188e6af32d5bbbdb76e5e4f7b3edc68cd18"
)
OUTPUTS: Final[frozenset[str]] = frozenset(
    {
        "contracts/schemas/s2-3-decision-contract.schema.json",
        "contracts/schemas/s2-3-evaluate-order-request.schema.json",
        "contracts/schemas/s2-3-decision-response.schema.json",
        "contracts/examples/s2-3-evaluate-order-request.valid.json",
        "contracts/examples/s2-3-decision-response.allow.valid.json",
        "contracts/examples/s2-3-decision-response.warn.valid.json",
        "contracts/examples/s2-3-decision-response.hold.valid.json",
        "contracts/examples/s2-3-decision-response.block.valid.json",
        "contracts/examples/invalid/s2-3-evaluate-order-request.mode.invalid.json",
        "contracts/examples/invalid/s2-3-evaluate-order-request.user.invalid.json",
        "contracts/examples/invalid/s2-3-evaluate-order-request.amount.invalid.json",
        "contracts/examples/invalid/s2-3-decision-response.missing-id.invalid.json",
        "contracts/proto/disclosure_observation.proto",
    }
)

PROTO = """syntax = "proto3";

package capstone.decision.v1;

option java_multiple_files = true;
option java_package = "com.capstone.decision.contract.v1";
option java_outer_classname = "DisclosureObservationContract";

service DisclosureObservationService {
  rpc GetDisclosureEvents(GetDisclosureEventsRequest) returns (GetDisclosureEventsResponse);
}

message GetDisclosureEventsRequest {
  string symbol = 1;
  string corp_code = 2;
  string as_of = 3;
  string window_from = 4;
  string window_to = 5;
}

message GetDisclosureEventsResponse {
  string symbol = 1;
  string corp_code = 2;
  string as_of = 3;
  string window_from = 4;
  string window_to = 5;
  double score = 6;
  string mapping_version = 7;
  repeated DisclosureRiskEvent events = 8;
  repeated DisclosureRiskWarning warnings = 9;
  repeated string source_refs = 10;
  string observed_at = 11;
  bool complete = 12;
}

message DisclosureRiskEvent {
  string event_code = 1;
  string receipt_no = 2;
  string occurred_on = 3;
}

message DisclosureRiskWarning {
  string code = 1;
  string event_code = 2;
  string receipt_no = 3;
  string message = 4;
}
"""


def load_catalog(path: Path = CATALOG_PATH) -> Mapping[str, Any]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_CATALOG_SHA256:
        raise ContractValidationError(
            f"S2.3 catalog hash mismatch: expected {EXPECTED_CATALOG_SHA256}, got {digest}"
        )
    catalog = load_json_bytes_strict(raw, source=path.relative_to(REPO_ROOT).as_posix())
    if not isinstance(catalog, dict):
        raise ContractValidationError("S2.3 catalog must be an object.")
    validate_catalog_semantics(catalog)
    return catalog


def _closed_catalog_shape(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            "type": "object",
            "additionalProperties": False,
            "required": list(value),
            "properties": {
                key: _closed_catalog_shape(item)
                for key, item in value.items()
            },
        }
    if isinstance(value, list):
        return {"const": value}
    return {"const": value}


def _catalog_schema(catalog: Mapping[str, Any]) -> dict[str, Any]:
    schema = _closed_catalog_shape(dict(catalog))
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "contracts/schemas/s2-3-decision-contract.schema.json"
    return schema


def _request_schema() -> dict[str, Any]:
    order_schema = load_json_bytes_strict(
        (REPO_ROOT / "contracts/schemas/order_intent.schema.json").read_bytes(),
        source="contracts/schemas/order_intent.schema.json",
    )
    order_schema.pop("$schema", None)
    order_schema.pop("$id", None)
    order_schema.pop("title", None)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/s2-3-evaluate-order-request.schema.json",
        "title": "S2.3 evaluate-order request v1",
        "type": "object",
        "additionalProperties": False,
        "required": ["principleId", "portfolioSource", "orderIntent"],
        "properties": {
            "principleId": {
                "type": "string",
                "pattern": "^prc_[0-9a-f]{32}$",
            },
            "portfolioSource": {
                "enum": ["KIS_MOCK", "INTERNAL_PAPER"],
            },
            "orderIntent": order_schema,
        },
    }


def _rewrite_refs(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                item.replace("#/$defs/", "#/$defs/riskDecision/$defs/")
                if key == "$ref" and isinstance(item, str)
                else _rewrite_refs(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_rewrite_refs(item) for item in value]
    return value


def _response_schema(risk_schema: Mapping[str, Any]) -> dict[str, Any]:
    embedded = _rewrite_refs(copy.deepcopy(dict(risk_schema)))
    embedded.pop("$schema", None)
    embedded.pop("$id", None)
    embedded.pop("title", None)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/s2-3-decision-response.schema.json",
        "title": "S2.3 persisted decision canonical projection v1",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "decisionId",
            "createdAt",
            "validUntil",
            "principleId",
            "principleVersionId",
            "principleVersion",
            "portfolioSource",
            "mode",
            "enforcementAction",
            "riskDecision",
        ],
        "properties": {
            "decisionId": {
                "type": "string",
                "pattern": "^dec_[0-9a-f]{32}$",
            },
            "createdAt": {"type": "string", "format": "date-time"},
            "validUntil": {"type": "string", "format": "date-time"},
            "principleId": {
                "type": "string",
                "pattern": "^prc_[0-9a-f]{32}$",
            },
            "principleVersionId": {
                "type": "string",
                "pattern": "^pvr_[0-9a-f]{32}$",
            },
            "principleVersion": {
                "type": "integer",
                "minimum": 1,
                "maximum": 2147483647,
            },
            "portfolioSource": {"enum": ["KIS_MOCK", "INTERNAL_PAPER"]},
            "mode": {"enum": ["GUIDE", "STRICT"]},
            "enforcementAction": {
                "enum": [
                    "NONE",
                    "ACKNOWLEDGE_WARNING",
                    "RECONFIRM_PRINCIPLE",
                    "RE_EVALUATE",
                    "DO_NOT_SUBMIT",
                ]
            },
            "riskDecision": {"$ref": "#/$defs/riskDecision"},
        },
        "$defs": {"riskDecision": embedded},
    }


def validate_catalog_semantics(catalog: Mapping[str, Any]) -> None:
    schema = _catalog_schema(catalog)
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(catalog))
    if errors:
        raise ContractValidationError(f"S2.3 catalog schema error: {errors[0].message}")
    if catalog["request"]["fields"] != [
        "principleId",
        "portfolioSource",
        "orderIntent",
    ]:
        raise ContractValidationError("S2.3 request fields drifted.")
    if catalog["request"]["orderIntentFields"] != [
        "symbol",
        "side",
        "orderType",
        "quantity",
        "estimatedPrice",
        "estimatedAmount",
        "timeframe",
        "strategyId",
    ]:
        raise ContractValidationError("S2.3 OrderIntent fields drifted.")
    if catalog["outcomePolicy"]["precedence"] != [
        "BLOCK",
        "HOLD",
        "WARN",
        "ALLOW",
    ]:
        raise ContractValidationError("S2.3 outcome precedence drifted.")
    if catalog["grpc"] != {
        "businessRpc": "GetDisclosureEvents",
        "concurrencyMax": 8,
        "corpCodeRequestPolicy": "OPTIONAL_EMPTY_RESOLVES_FROM_REGISTRY",
        "corpRegistryResolution": "SYMBOL_EXACTLY_ONE",
        "databaseErrorMapping": {
            "authentication": "UNAUTHENTICATED",
            "connectionOrNetwork": "UNAVAILABLE",
            "insufficientPrivilege": "PERMISSION_DENIED",
            "malformedRow": "DATA_LOSS",
            "queryCancellationOrTimeout": "DEADLINE_EXCEEDED_OR_UNAVAILABLE",
            "unexpectedInvariant": "INTERNAL",
        },
        "effectiveSourceDeadlineMillis": 500,
        "eventLookbackDays": 365,
        "hardDeadlineMillis": 2000,
        "nonLoopbackStartup": "FAIL",
        "physicalAttemptsMax": 1,
        "reflection": False,
        "requestMaxBytes": 262144,
        "responseMaxBytes": 1048576,
        "retryEnabled": False,
        "totalEvaluationDeadlineMillis": 900,
        "transparentRetryEnabled": False,
        "transport": "NUMERIC_LOOPBACK_PLAINTEXT",
    }:
        raise ContractValidationError("S2.3 gRPC safety tuple drifted.")
    if catalog["decisionFlow"] != [
        "ORDER_INTENT_VALIDATION",
        "OWNER_ACTIVE_CURRENT_PRINCIPLE_PIN",
        "SOURCE_FRESHNESS_APPLICABILITY",
        "ALL_APPLICABLE_RULES",
        "FINDINGS_AND_ABSTENTIONS",
        "PINNED_MODE_OUTCOME",
        "ATOMIC_PERSISTENCE",
    ]:
        raise ContractValidationError("S2.3 seven-step decision flow drifted.")
    if catalog["persistence"] != {
        "atomicInsertOrder": [
            "decision",
            "violations",
            "trace",
            "artifact",
            "audit",
            "outbox",
            "idempotencyResult",
        ],
        "brokerPublish": False,
        "transactionReadPolicy": "SOURCE_READ_AND_EVALUATION_OUTSIDE",
        "transactionWritePolicy": "FINAL_PERSISTENCE_ONLY",
    }:
        raise ContractValidationError("S2.3 persistence boundary drifted.")
    if catalog["principleConcurrency"] != {
        "decisionFirst": "UPDATER_WAITS_FOR_DECISION_COMMIT",
        "lockOrder": [
            "IDEMPOTENCY_ADVISORY_LOCK",
            "PRINCIPLE_FOR_SHARE",
            "DECISION_GRAPH_INSERT",
        ],
        "principleLock": "FOR SHARE OF principle",
        "updaterFirst": "HTTP_409_ALL_WRITES_ZERO",
    }:
        raise ContractValidationError("S2.3 Principle serialization drifted.")
    if catalog["databaseSecurity"]["broadSelectGrant"]:
        raise ContractValidationError("S2.3 must not grant broad table SELECT.")
    if catalog["databaseSecurity"]["futureTableDefaultSelectGrant"]:
        raise ContractValidationError("S2.3 must not grant future-table SELECT.")
    if catalog["databaseSecurity"]["idempotencyReplayRead"] != (
        "BOUNDED_SECURITY_DEFINER_FUNCTION"
    ):
        raise ContractValidationError("S2.3 idempotency replay read drifted.")
    if catalog["sourceOwnership"]["productionSeed"]:
        raise ContractValidationError("S2.3 must not generate production source rows.")
    if catalog["sourceOwnership"]["providerHttpFallback"]:
        raise ContractValidationError("S2.3 must not call provider HTTP.")
    if catalog["sourceOwnership"]["instrumentFields"] != [
        "symbol",
        "isEtfEtn",
        "isGoldEtfEtn",
        "productRiskScore",
        "catalogVersion",
        "observedAt",
        "receivedAt",
        "sourceRef",
        "artifactHash",
    ]:
        raise ContractValidationError("S2.3 instrument catalog fields drifted.")
    if {
        "instrumentProducer": catalog["sourceOwnership"]["instrumentProducer"],
        "instrumentProductRiskScoreNullable": catalog["sourceOwnership"][
            "instrumentProductRiskScoreNullable"
        ],
        "instrumentProjection": catalog["sourceOwnership"]["instrumentProjection"],
        "instrumentReaderMaxRows": catalog["sourceOwnership"]["instrumentReaderMaxRows"],
        "instrumentTable": catalog["sourceOwnership"]["instrumentTable"],
        "instrumentTimePolicy": catalog["sourceOwnership"]["instrumentTimePolicy"],
        "instrumentVersionPolicy": catalog["sourceOwnership"]["instrumentVersionPolicy"],
        "instrumentWriterRole": catalog["sourceOwnership"]["instrumentWriterRole"],
    } != {
        "instrumentProducer": "S1.1",
        "instrumentProductRiskScoreNullable": True,
        "instrumentProjection": "latest_instrument_catalog_observations",
        "instrumentReaderMaxRows": 1,
        "instrumentTable": "instrument_catalog_observations",
        "instrumentTimePolicy": "FUTURE_TIMESTAMP_IS_STALE",
        "instrumentVersionPolicy": "LATEST_OBSERVED_VERSION_NO_FALLBACK",
        "instrumentWriterRole": "decision_market_writer",
    }:
        raise ContractValidationError("S2.3 instrument source ownership drifted.")
    instrument_contracts = [
        row
        for row in catalog["sourceOwnership"]["observationContracts"]
        if row["table"] == "instrument_catalog_observations"
    ]
    if instrument_contracts != [
        {
            "producerOwner": "S1.1",
            "projection": "latest_instrument_catalog_observations",
            "table": "instrument_catalog_observations",
            "writerRole": "decision_market_writer",
        }
    ]:
        raise ContractValidationError("S2.3 instrument observation contract drifted.")
    if catalog["sourceOwnership"]["structuralMissingPolicy"] != (
        "S23_RUNTIME_SOURCE_BLOCKED"
    ):
        raise ContractValidationError("S2.3 structural source policy drifted.")
    if catalog["sourceOwnership"]["transientUnavailablePolicy"] != "PERSISTED_HOLD":
        raise ContractValidationError("S2.3 transient source policy drifted.")


def validate_request_semantics(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ContractValidationError("S2.3 request must be an object.")
    order = payload.get("orderIntent")
    if not isinstance(order, dict):
        raise ContractValidationError("/orderIntent: object required.")
    quantity = order.get("quantity")
    price = order.get("estimatedPrice")
    amount = order.get("estimatedAmount")
    if (
        not isinstance(quantity, int)
        or isinstance(quantity, bool)
        or not isinstance(price, int)
        or isinstance(price, bool)
        or not isinstance(amount, int)
        or isinstance(amount, bool)
    ):
        raise ContractValidationError("/orderIntent: integer amount fields required.")
    expected = quantity * price
    if expected > 9223372036854775807 or amount != expected:
        raise ContractValidationError(
            "/orderIntent/estimatedAmount: exact int64 multiplication required."
        )


def validate_decision_response_semantics(
    payload: Any,
    s22_catalog: Mapping[str, Any],
) -> None:
    if not isinstance(payload, dict) or not isinstance(payload.get("riskDecision"), dict):
        raise ContractValidationError("S2.3 decision response must contain riskDecision.")
    risk = payload["riskDecision"]
    validate_risk_decision_semantics(risk, s22_catalog)
    for field in ("decisionId", "validUntil", "mode", "portfolioSource", "principleVersionId", "principleVersion"):
        if payload.get(field) != risk.get(field):
            raise ContractValidationError(f"/{field}: persisted projection and riskDecision drifted.")
    action = risk.get("decision")
    mode = payload.get("mode")
    expected = {
        ("ALLOW", "GUIDE"): "NONE",
        ("ALLOW", "STRICT"): "NONE",
        ("WARN", "GUIDE"): "ACKNOWLEDGE_WARNING",
        ("WARN", "STRICT"): "RECONFIRM_PRINCIPLE",
        ("HOLD", "GUIDE"): "RE_EVALUATE",
        ("HOLD", "STRICT"): "RE_EVALUATE",
        ("BLOCK", "GUIDE"): "DO_NOT_SUBMIT",
        ("BLOCK", "STRICT"): "DO_NOT_SUBMIT",
    }.get((action, mode))
    if payload.get("enforcementAction") != expected:
        raise ContractValidationError("/enforcementAction: mode/outcome mapping drifted.")
    created_at = datetime.fromisoformat(str(payload["createdAt"]).replace("Z", "+00:00"))
    valid_until = datetime.fromisoformat(str(payload["validUntil"]).replace("Z", "+00:00"))
    if valid_until <= created_at:
        raise ContractValidationError("/validUntil: must be after createdAt.")


def _request_fixtures() -> dict[str, Any]:
    valid = {
        "principleId": "prc_0123456789abcdef0123456789abcdef",
        "portfolioSource": "INTERNAL_PAPER",
        "orderIntent": {
            "symbol": "005930",
            "side": "BUY",
            "orderType": "MARKET",
            "quantity": 2,
            "estimatedPrice": 70000,
            "estimatedAmount": 140000,
            "timeframe": "1d",
            "strategyId": "cash-equity-v1",
        },
    }
    mode = copy.deepcopy(valid)
    mode["mode"] = "STRICT"
    user = copy.deepcopy(valid)
    user["userId"] = "usr_forbidden"
    amount = copy.deepcopy(valid)
    amount["orderIntent"]["estimatedAmount"] = 139999
    return {
        "contracts/examples/s2-3-evaluate-order-request.valid.json": valid,
        "contracts/examples/invalid/s2-3-evaluate-order-request.mode.invalid.json": mode,
        "contracts/examples/invalid/s2-3-evaluate-order-request.user.invalid.json": user,
        "contracts/examples/invalid/s2-3-evaluate-order-request.amount.invalid.json": amount,
    }


def _decision_fixtures(
    s22_outputs: Mapping[str, bytes],
) -> dict[str, Any]:
    sources = {
        "allow": ("allow", "GUIDE", "NONE"),
        "warn": ("warn", "STRICT", "RECONFIRM_PRINCIPLE"),
        "hold": ("hold", "GUIDE", "RE_EVALUATE"),
        "block": ("block", "STRICT", "DO_NOT_SUBMIT"),
    }
    fixtures: dict[str, Any] = {}
    decision_hex = {"allow": "a", "warn": "b", "hold": "c", "block": "d"}
    for name, (s22_name, mode, enforcement) in sources.items():
        risk = load_json_bytes_strict(
            s22_outputs[f"contracts/examples/risk_decision.{s22_name}.valid.json"],
            source=f"generated S2.2 {s22_name} fixture",
        )
        decision_id = f"dec_{decision_hex[name] * 32}"
        risk["decisionId"] = decision_id
        risk["validUntil"] = "2030-01-02T03:14:05Z"
        risk["mode"] = mode
        fixture = {
            "decisionId": decision_id,
            "createdAt": "2030-01-02T03:04:05Z",
            "validUntil": "2030-01-02T03:14:05Z",
            "principleId": "prc_0123456789abcdef0123456789abcdef",
            "principleVersionId": risk["principleVersionId"],
            "principleVersion": risk["principleVersion"],
            "portfolioSource": risk["portfolioSource"],
            "mode": mode,
            "enforcementAction": enforcement,
            "riskDecision": risk,
        }
        fixtures[
            f"contracts/examples/s2-3-decision-response.{name}.valid.json"
        ] = fixture
    invalid = copy.deepcopy(fixtures["contracts/examples/s2-3-decision-response.allow.valid.json"])
    invalid.pop("decisionId")
    fixtures["contracts/examples/invalid/s2-3-decision-response.missing-id.invalid.json"] = invalid
    return fixtures


def generate_outputs(catalog: Mapping[str, Any]) -> dict[str, bytes]:
    validate_catalog_semantics(catalog)
    s22_catalog = load_s22_catalog(S22_CATALOG_PATH)
    s22_outputs = generate_s22_outputs(s22_catalog)
    risk_schema = load_json_bytes_strict(
        s22_outputs["contracts/schemas/risk_decision.schema.json"],
        source="generated S2.2 risk schema",
    )
    request_schema = _request_schema()
    response_schema = _response_schema(risk_schema)
    outputs: dict[str, bytes] = {
        "contracts/schemas/s2-3-decision-contract.schema.json": canonical_json_bytes(
            _catalog_schema(catalog)
        ),
        "contracts/schemas/s2-3-evaluate-order-request.schema.json": canonical_json_bytes(
            request_schema
        ),
        "contracts/schemas/s2-3-decision-response.schema.json": canonical_json_bytes(
            response_schema
        ),
        "contracts/proto/disclosure_observation.proto": PROTO.encode("utf-8"),
    }
    fixtures = {**_request_fixtures(), **_decision_fixtures(s22_outputs)}
    outputs.update(
        {path: canonical_json_bytes(value) for path, value in fixtures.items()}
    )
    if frozenset(outputs) != OUTPUTS:
        raise ContractValidationError("S2.3 generated output manifest drifted.")

    request_validator = Draft202012Validator(request_schema)
    response_validator = Draft202012Validator(response_schema)
    for path, payload in _request_fixtures().items():
        errors = list(request_validator.iter_errors(payload))
        semantic_error: ContractValidationError | None = None
        if not errors:
            try:
                validate_request_semantics(payload)
            except ContractValidationError as caught:
                semantic_error = caught
        if path.endswith(".valid.json") and (errors or semantic_error):
            raise ContractValidationError(f"{path}: generated positive request is invalid.")
        if path.endswith(".invalid.json") and not errors and semantic_error is None:
            raise ContractValidationError(f"{path}: generated negative request passed.")
    for path, payload in _decision_fixtures(s22_outputs).items():
        errors = list(response_validator.iter_errors(payload))
        semantic_error: ContractValidationError | None = None
        if not errors:
            try:
                validate_decision_response_semantics(payload, s22_catalog)
            except ContractValidationError as caught:
                semantic_error = caught
        if path.endswith(".valid.json") and (errors or semantic_error):
            detail = errors[0].message if errors else str(semantic_error)
            raise ContractValidationError(f"{path}: generated positive response invalid: {detail}")
        if path.endswith(".invalid.json") and not errors and semantic_error is None:
            raise ContractValidationError(f"{path}: generated negative response passed.")
    return dict(sorted(outputs.items()))


def _check_outputs(outputs: Mapping[str, bytes]) -> int:
    failures = 0
    for relative_path, expected in outputs.items():
        path = REPO_ROOT / relative_path
        try:
            actual = path.read_bytes()
        except OSError:
            failures += 1
            print(f"FAIL missing generated artifact {relative_path}", file=sys.stderr)
            continue
        if actual != expected:
            failures += 1
            print(f"FAIL generated artifact drift {relative_path}", file=sys.stderr)
        else:
            print(f"PASS generated artifact {relative_path}")
    return failures


def _write_outputs(outputs: Mapping[str, bytes]) -> None:
    for relative_path, payload in outputs.items():
        write_generated_artifact(REPO_ROOT, relative_path, payload)
        print(f"WROTE {relative_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile and verify the canonical S2.3 Decision contracts."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    try:
        catalog = load_catalog()
        outputs = generate_outputs(catalog)
        if arguments.write:
            _write_outputs(outputs)
            print(f"S23_CONTRACT_LOCK_VERIFIED {EXPECTED_CATALOG_SHA256}")
            return 0
        failures = _check_outputs(outputs)
    except (OSError, ContractValidationError, SchemaError, KeyError, TypeError) as error:
        print(f"S2.3 contract generation failed: {error}", file=sys.stderr)
        return 1
    if failures:
        print(f"S2.3 contract generation failed: {failures} drift(s)", file=sys.stderr)
        return 1
    print(f"S23_CONTRACT_LOCK_VERIFIED {EXPECTED_CATALOG_SHA256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
