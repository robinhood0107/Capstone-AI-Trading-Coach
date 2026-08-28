"""Generate the additive 1.1.0 automation policy contracts without changing v1 bytes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Final

from jsonschema import Draft202012Validator

ROOT: Final = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.generated_artifact_io import write_generated_path  # noqa: E402
from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    canonical_json_bytes,
    load_json_bytes_strict,
)

CATALOG_PATH: Final = ROOT / "contracts/catalogs/p1-automation-policy.v1.json"
ADDITIVE_OPENAPI_PATH: Final = ROOT / "contracts/openapi/p1-automation-v2.v1.openapi.json"
SCHEMA_IDS: Final[tuple[str, ...]] = (
    "automation-policy.v1",
    "automation-status.v2",
    "automation-run.v2",
    "automation-position.v2",
)
SCHEMA_PATHS: Final = {
    schema_id: ROOT / f"contracts/schemas/{schema_id}.schema.json"
    for schema_id in SCHEMA_IDS
}
PRESETS: Final = {
    "conservative": (300, 500),
    "balanced": (500, 1000),
    "aggressive": (800, 1500),
}
BLOCKERS: Final = (
    "ACCOUNT_NOT_CONFIGURED",
    "POLICY_NOT_CONFIGURED",
    "POLICY_VERSION_DRIFT",
    "PRINCIPLE_NOT_CONFIGURED",
    "REAL_TEAM_B_POINTER_INACTIVE",
    "RELEASE_BINDING_UNCLEAN",
    "CERTIFICATION_INVALID",
    "KILL_SWITCH_ACTIVE",
    "UNRESOLVED_RECONCILIATION",
    "CONTROL_HALTED",
    "BLOCKED_INCOMPLETE_RISK_BALANCE",
)
RUN_STATES: Final = (
    "SCHEDULED",
    "PRECHECK",
    "RECONCILING_PREVIOUS",
    "EXIT_SELECTED",
    "BUY_CANDIDATE_SELECTED",
    "NEWS_CHECKING",
    "NEWS_VETOED",
    "RISK_CHECKING",
    "ORDER_SIZING",
    "ORDER_SUBMITTING",
    "ORDER_SUBMITTED",
    "PENDING_RECONCILIATION",
    "CANCELLED_UNFILLED",
    "COMPLETED",
    "SKIPPED_NO_ACTION",
    "SKIPPED_DATA_UNAVAILABLE",
    "SKIPPED_LATE_START",
    "HALTED",
)
EXIT_REASONS: Final = (
    "STOP_LOSS",
    "MAX_HOLDING_SESSIONS",
    "MODEL_SELL",
    "TAKE_PROFIT",
)


def canonical_json(value: object) -> bytes:
    return canonical_json_bytes(value)


def _closed(required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def _nullable(value: dict[str, Any]) -> dict[str, Any]:
    return {"oneOf": [value, {"type": "null"}]}


def _identifier(prefix: str) -> dict[str, Any]:
    return {"type": "string", "pattern": f"^{prefix}_[A-Za-z0-9_-]{{8,96}}$"}


def _policy_identifier() -> dict[str, Any]:
    return {"type": "string", "pattern": "^auto_pol_[0-9a-f]{32}$"}


def _timestamp() -> dict[str, Any]:
    return {"type": "string", "format": "date-time"}


def _money(nullable: bool = False) -> dict[str, Any]:
    value = {"type": "integer", "minimum": 1}
    return _nullable(value) if nullable else value


def _policy_body() -> dict[str, Any]:
    return _closed(
        [
            "contractId",
            "policyId",
            "version",
            "presetId",
            "capitalLimitKrw",
            "stopLossBps",
            "takeProfitBps",
            "maxOpenPositions",
            "maxNewOrdersPerSession",
            "evaluationTimeKst",
            "buyCutoffTimeKst",
            "cancelTimeKst",
            "createdAt",
            "updatedAt",
        ],
        {
            "contractId": {"const": "automation-policy.v1"},
            "policyId": _policy_identifier(),
            "version": {"type": "integer", "minimum": 1},
            "presetId": {
                "enum": ["conservative", "balanced", "aggressive", "custom"]
            },
            "capitalLimitKrw": {
                "type": "integer",
                "minimum": 10_000,
                "maximum": 10_000_000_000,
                "multipleOf": 10_000,
            },
            "stopLossBps": {"type": "integer", "minimum": 100, "maximum": 1500},
            "takeProfitBps": {"type": "integer", "minimum": 200, "maximum": 3000},
            "maxOpenPositions": {"const": 5},
            "maxNewOrdersPerSession": {"const": 1},
            "evaluationTimeKst": {"const": "09:30"},
            "buyCutoffTimeKst": {"const": "09:40"},
            "cancelTimeKst": {"const": "15:20"},
            "createdAt": _timestamp(),
            "updatedAt": _timestamp(),
        },
    )


def _policy_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/automation-policy.v1.schema.json",
        "title": "automation-policy.v1",
        **_policy_body(),
    }


def _status_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/automation-status.v2.schema.json",
        "title": "automation-status.v2",
        **_closed(
            [
                "contractId",
                "controlState",
                "projectionState",
                "controlVersion",
                "brokerageMode",
                "accountId",
                "policy",
                "killSwitchActive",
                "certificationStatus",
                "openPositionCount",
                "unresolvedReconciliation",
                "canArm",
                "blockers",
            ],
            {
                "contractId": {"const": "automation-status.v2"},
                "controlState": {"enum": ["DISARMED", "ARMED", "HALTED"]},
                "projectionState": {
                    "enum": ["DISARMED", "ARMED", "RUNNING", "HALTED"]
                },
                "controlVersion": {"type": "integer", "minimum": 1},
                "brokerageMode": {"const": "KIS_MOCK"},
                "accountId": _nullable(_identifier("acct")),
                "policy": _nullable(_policy_body()),
                "killSwitchActive": {"type": "boolean"},
                "certificationStatus": {
                    "enum": [
                        "NOT_REQUIRED_INTERNAL_PAPER",
                        "REQUIRED",
                        "VALID",
                        "EXPIRED",
                        "INVALID",
                    ]
                },
                "openPositionCount": {"type": "integer", "minimum": 0, "maximum": 5},
                "unresolvedReconciliation": {"type": "boolean"},
                "canArm": {"type": "boolean"},
                "blockers": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": {"enum": list(BLOCKERS)},
                },
            },
        ),
    }


def _run_schema() -> dict[str, Any]:
    optional_integer = _nullable({"type": "integer", "minimum": 0})
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/automation-run.v2.schema.json",
        "title": "automation-run.v2",
        **_closed(
            [
                "contractId",
                "runId",
                "sessionDate",
                "state",
                "brokerageMode",
                "policyId",
                "policyVersion",
                "selectedSymbol",
                "selectedSide",
                "orderQuantity",
                "filledQuantity",
                "leavesQuantity",
                "limitPriceKrw",
                "estimatedAmountKrw",
                "exitReason",
                "physicalSubmitCount",
                "providerCalls",
                "startedAt",
                "updatedAt",
            ],
            {
                "contractId": {"const": "automation-run.v2"},
                "runId": _identifier("auto_run"),
                "sessionDate": {"type": "string", "format": "date"},
                "state": {"enum": list(RUN_STATES)},
                "brokerageMode": {"enum": ["KIS_MOCK", "INTERNAL_PAPER"]},
                "policyId": _nullable(_policy_identifier()),
                "policyVersion": _nullable({"type": "integer", "minimum": 1}),
                "selectedSymbol": _nullable(
                    {"type": "string", "pattern": "^[0-9]{6}$"}
                ),
                "selectedSide": _nullable({"enum": ["BUY", "SELL"]}),
                "orderQuantity": optional_integer,
                "filledQuantity": optional_integer,
                "leavesQuantity": optional_integer,
                "limitPriceKrw": _money(nullable=True),
                "estimatedAmountKrw": _money(nullable=True),
                "exitReason": _nullable({"enum": list(EXIT_REASONS)}),
                "physicalSubmitCount": {"type": "integer", "minimum": 0, "maximum": 1},
                "providerCalls": {"type": "integer", "minimum": 0, "maximum": 16},
                "startedAt": _timestamp(),
                "updatedAt": _timestamp(),
            },
        ),
    }


def _position_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/automation-position.v2.schema.json",
        "title": "automation-position.v2",
        **_closed(
            [
                "contractId",
                "positionId",
                "accountId",
                "symbol",
                "quantity",
                "entryAverageFillPriceKrw",
                "entrySession",
                "expirySession",
                "policyId",
                "policyVersion",
                "stopLossBps",
                "takeProfitBps",
                "status",
                "exitReason",
                "exitAverageFillPriceKrw",
                "realizedPnlKrw",
                "botOwned",
                "shortAllowed",
                "createdAt",
                "closedAt",
            ],
            {
                "contractId": {"const": "automation-position.v2"},
                "positionId": _identifier("auto_pos"),
                "accountId": _identifier("acct"),
                "symbol": {"type": "string", "pattern": "^[0-9]{6}$"},
                "quantity": {"type": "integer", "minimum": 1},
                "entryAverageFillPriceKrw": _money(),
                "entrySession": {"type": "string", "format": "date"},
                "expirySession": {"type": "string", "format": "date"},
                "policyId": _policy_identifier(),
                "policyVersion": {"type": "integer", "minimum": 1},
                "stopLossBps": {"type": "integer", "minimum": 100, "maximum": 1500},
                "takeProfitBps": {"type": "integer", "minimum": 200, "maximum": 3000},
                "status": {"enum": ["OPEN", "EXIT_PENDING", "CLOSED", "HALTED_MISMATCH"]},
                "exitReason": _nullable({"enum": list(EXIT_REASONS)}),
                "exitAverageFillPriceKrw": _nullable(_money()),
                "realizedPnlKrw": _nullable({"type": "integer"}),
                "botOwned": {"const": True},
                "shortAllowed": {"const": False},
                "createdAt": _timestamp(),
                "closedAt": _nullable(_timestamp()),
            },
        ),
    }


def build_schemas() -> dict[str, dict[str, Any]]:
    return {
        "automation-policy.v1": _policy_schema(),
        "automation-status.v2": _status_schema(),
        "automation-run.v2": _run_schema(),
        "automation-position.v2": _position_schema(),
    }


def _request_header() -> dict[str, Any]:
    return {
        "in": "header",
        "name": "X-Idempotency-Key",
        "required": True,
        "schema": {
            "type": "string",
            "minLength": 16,
            "maxLength": 128,
            "pattern": "^[A-Za-z0-9._~-]+$",
        },
    }


def _page_parameters() -> list[dict[str, Any]]:
    return [
        {
            "in": "query",
            "name": "size",
            "required": False,
            "schema": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        {
            "in": "query",
            "name": "cursor",
            "required": False,
            "schema": {"type": "string", "maxLength": 512},
        },
    ]


def _success(schema_name: str) -> dict[str, Any]:
    return {
        "200": {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": {"$ref": f"#/components/schemas/{schema_name}"}
                }
            },
        }
    }


def _envelope(data_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "requestId": {"type": "string"},
            "data": {"oneOf": [data_schema, {"type": "null"}]},
            "warnings": {"type": "array", "items": {"type": "object"}},
            "error": {"oneOf": [{"type": "object"}, {"type": "null"}]},
        },
    }


def build_additive_openapi(schemas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    policy_request = _closed(
        ["expectedVersion", "capitalLimitKrw", "stopLossBps", "takeProfitBps"],
        {
            "expectedVersion": {"type": "integer", "minimum": 0},
            "capitalLimitKrw": schemas["automation-policy.v1"]["properties"]["capitalLimitKrw"],
            "stopLossBps": schemas["automation-policy.v1"]["properties"]["stopLossBps"],
            "takeProfitBps": schemas["automation-policy.v1"]["properties"]["takeProfitBps"],
        },
    )
    arm_request = _closed(
        ["accountId", "policyId", "expectedPolicyVersion", "expectedControlVersion"],
        {
            "accountId": _identifier("acct"),
            "policyId": _policy_identifier(),
            "expectedPolicyVersion": {"type": "integer", "minimum": 1},
            "expectedControlVersion": {"type": "integer", "minimum": 1},
        },
    )
    run_page = _closed(
        ["items", "nextCursor"],
        {
            "items": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/AutomationRunV2"},
            },
            "nextCursor": _nullable({"type": "string", "maxLength": 512}),
        },
    )
    # 실현 성과는 새 operation을 만들지 않고 positions 응답에 요약으로 얹는다. 수익을 주장하지
    # 않으므로 performanceClaimAllowed는 항상 false이고 연율화·Sharpe는 넣지 않는다.
    realized_summary = _closed(
        [
            "closedPositionCount",
            "realizedPnlKrw",
            "realizedGrossKrw",
            "winningPositionCount",
            "losingPositionCount",
            "evidenceMode",
            "performanceClaimAllowed",
        ],
        {
            "closedPositionCount": {"type": "integer", "minimum": 0},
            "realizedPnlKrw": {"type": "integer"},
            "realizedGrossKrw": {"type": "integer"},
            "winningPositionCount": {"type": "integer", "minimum": 0},
            "losingPositionCount": {"type": "integer", "minimum": 0},
            "evidenceMode": {"const": "KIS_MOCK"},
            "performanceClaimAllowed": {"const": False},
        },
    )
    position_page = _closed(
        ["items", "nextCursor", "realizedSummary"],
        {
            "items": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/AutomationPositionV2"},
            },
            "nextCursor": _nullable({"type": "string", "maxLength": 512}),
            "realizedSummary": {
                "$ref": "#/components/schemas/AutomationRealizedSummaryV2"
            },
        },
    )
    components = {
        "AutomationPolicyV2": schemas["automation-policy.v1"],
        "AutomationStatusV2": schemas["automation-status.v2"],
        "AutomationRunV2": schemas["automation-run.v2"],
        "AutomationPositionV2": schemas["automation-position.v2"],
        "PutAutomationPolicyV2Request": policy_request,
        "ArmAutomationV2Request": arm_request,
        "AutomationRunPageV2": run_page,
        "AutomationPositionPageV2": position_page,
        "AutomationRealizedSummaryV2": realized_summary,
        "ApiResponseAutomationStatusV2": _envelope(
            {"$ref": "#/components/schemas/AutomationStatusV2"}
        ),
        "ApiResponseAutomationPolicyV2": _envelope(
            {"$ref": "#/components/schemas/AutomationPolicyV2"}
        ),
        "ApiResponseAutomationRunPageV2": _envelope(
            {"$ref": "#/components/schemas/AutomationRunPageV2"}
        ),
        "ApiResponseAutomationPositionPageV2": _envelope(
            {"$ref": "#/components/schemas/AutomationPositionPageV2"}
        ),
        "P1AutomationV2ErrorResponse": {
            "type": "object",
            "additionalProperties": False,
            "required": ["success", "requestId", "data", "warnings", "error"],
            "properties": {
                "success": {"const": False},
                "requestId": {"type": "string"},
                "data": {"type": "null"},
                "warnings": {"type": "array", "items": {"type": "object"}},
                "error": {
                    "type": "object",
                    "required": ["code", "message"],
                    "properties": {
                        "code": {"const": "CONFLICT"},
                        "message": {"type": "string"},
                        "details": {"type": "object"},
                    },
                },
            },
        },
    }
    return {
        "openapi": "3.1.0",
        "info": {"title": "P1 Automation Policy v2 additive contract", "version": "1.1.0"},
        "paths": {
            "/api/v2/automation/status": {
                "get": {
                    "operationId": "getAutomationStatusV2",
                    "responses": _success("ApiResponseAutomationStatusV2"),
                }
            },
            "/api/v2/automation/policy": {
                "put": {
                    "operationId": "putAutomationPolicyV2",
                    "parameters": [_request_header()],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/PutAutomationPolicyV2Request"}
                            }
                        },
                    },
                    "responses": _success("ApiResponseAutomationPolicyV2"),
                }
            },
            "/api/v2/automation/arm": {
                "post": {
                    "operationId": "armAutomationV2",
                    "parameters": [_request_header()],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ArmAutomationV2Request"}
                            }
                        },
                    },
                    "responses": {
                        **_success("ApiResponseAutomationStatusV2"),
                        "409": {
                            "description": "Blocked by incomplete qualified risk balance",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/P1AutomationV2ErrorResponse"
                                    }
                                }
                            },
                        },
                    },
                }
            },
            "/api/v2/automation/runs": {
                "get": {
                    "operationId": "listAutomationRunsV2",
                    "parameters": _page_parameters(),
                    "responses": _success("ApiResponseAutomationRunPageV2"),
                }
            },
            "/api/v2/automation/positions": {
                "get": {
                    "operationId": "listAutomationPositionsV2",
                    "responses": _success("ApiResponseAutomationPositionPageV2"),
                }
            },
        },
        "components": {"schemas": components},
    }


def load_catalog() -> dict[str, Any]:
    value = load_json_bytes_strict(CATALOG_PATH.read_bytes(), source=str(CATALOG_PATH))
    if not isinstance(value, dict):
        raise ContractValidationError("P1 automation policy catalog must be an object.")
    if value.get("contractId") != "p1-automation-policy.v1":
        raise ContractValidationError("P1 automation policy catalog id drifted.")
    actual_presets = {
        item.get("presetId"): (item.get("stopLossBps"), item.get("takeProfitBps"))
        for item in value.get("presets", [])
        if isinstance(item, dict)
    }
    if actual_presets != PRESETS:
        raise ContractValidationError("P1 automation policy presets drifted.")
    if tuple(value.get("blockers", [])) != BLOCKERS:
        raise ContractValidationError("P1 automation blocker inventory drifted.")
    operations = value.get("operations")
    if not isinstance(operations, list) or len(operations) != 5:
        raise ContractValidationError("P1 automation policy must publish exact five operations.")
    return value


def validate_policy_semantics(value: dict[str, Any]) -> None:
    if value["takeProfitBps"] <= value["stopLossBps"]:
        raise ContractValidationError("takeProfitBps must exceed stopLossBps.")
    preset = value["presetId"]
    if preset in PRESETS and (
        value["stopLossBps"], value["takeProfitBps"]
    ) != PRESETS[preset]:
        raise ContractValidationError("named preset values drifted; use custom.")


def build_outputs() -> dict[Path, bytes]:
    load_catalog()
    schemas = build_schemas()
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
    additive = build_additive_openapi(schemas)
    return {
        **{SCHEMA_PATHS[name]: canonical_json(schema) for name, schema in schemas.items()},
        ADDITIVE_OPENAPI_PATH: canonical_json(additive),
    }


def generate(*, check: bool) -> None:
    for path, payload in build_outputs().items():
        if check:
            if not path.is_file() or path.read_bytes() != payload:
                raise ContractValidationError(
                    f"generated P1 automation artifact drifted: {path.relative_to(ROOT)}"
                )
        else:
            write_generated_path(ROOT, path, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        generate(check=arguments.check)
    except (ContractValidationError, OSError, json.JSONDecodeError) as error:
        print(f"P1_V91_AUTOMATION_CONTRACTS=FAIL: {error}", file=sys.stderr)
        return 1
    print("P1_V91_AUTOMATION_CONTRACTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
