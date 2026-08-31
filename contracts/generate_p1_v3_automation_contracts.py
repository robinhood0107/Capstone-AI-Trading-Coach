"""Generate additive Automation V3, evidence, and market bootstrap contracts.

The generator deliberately does not mutate the current root OpenAPI.  The
contract-only PR publishes an exact-six overlay first; the Spring/runtime PR
may merge it only after implementing the same surface.
"""

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
)

CATALOG_PATH: Final = ROOT / "contracts/catalogs/p1-automation-policy.v2.json"
ADDITIVE_OPENAPI_PATH: Final = (
    ROOT / "contracts/openapi/p1-automation-v3.v1.openapi.json"
)
SCHEMA_IDS: Final[tuple[str, ...]] = (
    "automation-policy.v2",
    "automation-status.v3",
    "automation-run.v3",
    "automation-run-detail.v3",
    "automation-position.v3",
    "automation-candidate-evidence.v1",
    "vertex-news-screen.v2",
    "strong-llm-owner-settings.v2",
    "p1-automation-market-bootstrap.v1",
)
SCHEMA_PATHS: Final = {
    schema_id: ROOT / f"contracts/schemas/{schema_id}.schema.json"
    for schema_id in SCHEMA_IDS
}
PRESETS: Final = {
    "conservative": (300, 500, 20, 22, 2_500, True),
    "balanced": (500, 1_000, 60, 22, 3_000, True),
    "aggressive": (800, 1_500, 0, 22, 3_500, True),
}
BLOCKERS: Final = (
    "ACCOUNT_NOT_CONFIGURED",
    "POLICY_NOT_CONFIGURED",
    "POLICY_VERSION_DRIFT",
    "POLICY_V3_REQUIRED",
    "PRINCIPLE_NOT_CONFIGURED",
    "REAL_TEAM_B_POINTER_INACTIVE",
    "RELEASE_BINDING_UNCLEAN",
    "CERTIFICATION_INVALID",
    "KILL_SWITCH_ACTIVE",
    "UNRESOLVED_RECONCILIATION",
    "CONTROL_HALTED",
    "BLOCKED_INCOMPLETE_RISK_BALANCE",
    "LEGACY_POSITION_PRESENT",
    "MARKET_HISTORY_EMPTY",
    "MARKET_HISTORY_INSUFFICIENT",
    "MARKET_DATA_CATCHUP_REQUIRED",
    "AI_PROVIDER_NOT_READY",
)
RUN_STATES: Final = (
    "SCHEDULED",
    "PRECHECK",
    "RECONCILING_PREVIOUS",
    "EXIT_SELECTED",
    "NEWS_SCREENING",
    "AI_JUDGING",
    "BUY_CANDIDATE_SELECTED",
    # Legacy V90/V106 checkpoint states remain readable.
    "NEWS_CHECKING",
    "NEWS_VETOED",
    "ORDER_SIZING",
    "RISK_CHECKING",
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
    "ATR_TRAILING",
    "MODEL_SELL",
    "TAKE_PROFIT",
    "MAX_HOLDING_SESSIONS",
)
HISTORICAL_BYTES: Final = {
    "contracts/catalogs/p1-automation-policy.v1.json": "03ff14572aa716c11a0fc63a75ded2483078b35c71fa12f64cb5a1fdd00ff549",
    "contracts/catalogs/p1-team-a-acceptance.v1.json": "75efdec876c2c6b3388ae08f4b478f6564a97b54cb6a1da41766757399173dd8",
    "contracts/catalogs/p1-team-a-acceptance.v2.json": "35fac477fa732e44fbe346fbe135dad95514835dca28b09c359b6aecc8daa114",
    "workspaces/experience-dashboard/src/shared/api/generated/p1-team-a-client.v1.ts": "869b5f6bfb069ca015037461d9706cd8e0a65f5dea688fe83aea022f3281c584",
    "workspaces/experience-dashboard/src/shared/api/generated/p1-team-a-client.v2.ts": "5261128f0af7b2c5853536effd7920c4c28265dcbf311317ff291751c0a2d923",
}
HISTORICAL_AUTOMATION_V2_OPENAPI_SHA256: Final = (
    "fd830d894852ab4e66acba8bb4c7d8eb743fb00c1242b5e75b5bd9fc996a1da1"
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


def _timestamp() -> dict[str, Any]:
    return {"type": "string", "format": "date-time"}


def _date() -> dict[str, Any]:
    return {"type": "string", "format": "date"}


def _sha() -> dict[str, Any]:
    return {"type": "string", "pattern": "^[0-9a-f]{64}$"}


def _symbol() -> dict[str, Any]:
    return {"type": "string", "pattern": "^[0-9]{6}$"}


def _identifier(prefix: str) -> dict[str, Any]:
    return {"type": "string", "pattern": f"^{prefix}_[A-Za-z0-9_-]{{8,96}}$"}


def _policy_identifier() -> dict[str, Any]:
    return {"type": "string", "pattern": "^auto_pol_[0-9a-f]{32}$"}


def _policy_body() -> dict[str, Any]:
    required = [
        "contractId",
        "policyId",
        "version",
        "presetId",
        "capitalLimitKrw",
        "stopLossBps",
        "takeProfitBps",
        "maxHoldingSessions",
        "atrPeriod",
        "atrMultiplierMilli",
        "modelSellEnabled",
        "maxOpenPositions",
        "maxNewOrdersPerSession",
        "evaluationTimeKst",
        "buyCutoffTimeKst",
        "cancelTimeKst",
        "createdAt",
        "updatedAt",
    ]
    return _closed(
        required,
        {
            "contractId": {"const": "automation-policy.v2"},
            "policyId": _policy_identifier(),
            "version": {"type": "integer", "minimum": 1},
            "presetId": {"enum": ["conservative", "balanced", "aggressive", "custom"]},
            "capitalLimitKrw": {
                "type": "integer",
                "minimum": 10_000,
                "maximum": 10_000_000_000,
                "multipleOf": 10_000,
            },
            "stopLossBps": {"type": "integer", "minimum": 100, "maximum": 1_500},
            "takeProfitBps": {"type": "integer", "minimum": 200, "maximum": 3_000},
            "maxHoldingSessions": {"type": "integer", "minimum": 0, "maximum": 1_260},
            "atrPeriod": {"type": "integer", "minimum": 5, "maximum": 100},
            "atrMultiplierMilli": {
                "type": "integer",
                "minimum": 1_000,
                "maximum": 10_000,
                "multipleOf": 100,
            },
            "modelSellEnabled": {"type": "boolean"},
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
        "$id": "contracts/schemas/automation-policy.v2.schema.json",
        "title": "automation-policy.v2",
        **_policy_body(),
    }


def _evidence_body() -> dict[str, Any]:
    return _closed(
        [
            "symbol",
            "citationId",
            "sourceId",
            "sourceType",
            "sourceEventDate",
            "ageWarning",
            "uriSha256",
            "boundedQuote",
            "quoteSha256",
            "verified",
        ],
        {
            "symbol": _symbol(),
            "citationId": {
                "type": "string",
                "pattern": "^cit_[A-Za-z0-9._:-]{1,96}$",
            },
            "sourceId": {
                "type": "string",
                "pattern": "^[A-Za-z0-9._:-]{1,128}$",
            },
            "sourceType": {"enum": ["OFFICIAL_PRIMARY", "REGISTERED_INDEPENDENT"]},
            "sourceEventDate": _nullable(_date()),
            "ageWarning": {"type": "boolean"},
            "uriSha256": _sha(),
            "boundedQuote": {"type": "string", "minLength": 1, "maxLength": 240},
            "quoteSha256": _sha(),
            "verified": {"const": True},
        },
    )


def _evidence_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/automation-candidate-evidence.v1.schema.json",
        "title": "automation-candidate-evidence.v1",
        **_evidence_body(),
    }


def _screening_body() -> dict[str, Any]:
    return _closed(
        ["symbol", "status", "verdict", "score", "reason", "evidence"],
        {
            "symbol": _symbol(),
            "status": {"enum": ["AVAILABLE", "ABSTAIN"]},
            "verdict": {"enum": ["VETO_BUY", "NO_VETO"]},
            "score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "reason": {"type": "string", "minLength": 1, "maxLength": 512},
            "evidence": {
                "type": "array",
                "maxItems": 5,
                "items": _evidence_body(),
            },
        },
    )


def _screen_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/vertex-news-screen.v2.schema.json",
        "title": "vertex-news-screen.v2",
        **_closed(
            [
                "contractId",
                "status",
                "failureReason",
                "candidateSetSha256",
                "inputSha256",
                "outputSha256",
                "modelId",
                "promptVersion",
                "providerCallCount",
                "groundingQueryCount",
                "orderAuthority",
                "candidates",
            ],
            {
                "contractId": {"const": "vertex-news-screen.v2"},
                "status": {"enum": ["AVAILABLE", "ABSTAIN"]},
                "failureReason": _nullable(
                    {
                        "enum": [
                            "PROVIDER_UNAVAILABLE",
                            "BUDGET_EXHAUSTED",
                            "PROMPT_INJECTION",
                            "CONTRACT_VIOLATION",
                        ]
                    }
                ),
                "candidateSetSha256": _sha(),
                "inputSha256": _sha(),
                "outputSha256": _sha(),
                "modelId": {"type": "string", "minLength": 1, "maxLength": 128},
                "promptVersion": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9._-]{1,64}$",
                },
                "providerCallCount": {"type": "integer", "minimum": 0, "maximum": 1},
                "groundingQueryCount": {"type": "integer", "minimum": 0, "maximum": 32},
                "orderAuthority": {"const": "NONE"},
                "candidates": {
                    "type": "array",
                    "maxItems": 31,
                    "items": _screening_body(),
                },
            },
        ),
    }


def _settings_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/strong-llm-owner-settings.v2.schema.json",
        "title": "strong-llm-owner-settings.v2",
        **_closed(
            ["contractId", "aiJudgementEnabled", "thinkingLevel"],
            {
                "contractId": {"const": "strong-llm-owner-settings.v2"},
                "aiJudgementEnabled": {"type": "boolean"},
                "thinkingLevel": {"enum": ["minimal", "low", "medium"]},
            },
        ),
    }


def _run_body() -> dict[str, Any]:
    optional_nonnegative = _nullable({"type": "integer", "minimum": 0})
    return _closed(
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
            "screeningProviderCallCount",
            "groundingQueryCount",
            "judgeCallCount",
            "evidenceCount",
            "evidenceSetSha256",
            "aiSettingsSha256",
            "startedAt",
            "updatedAt",
        ],
        {
            "contractId": {"const": "automation-run.v3"},
            "runId": _identifier("auto_run"),
            "sessionDate": _date(),
            "state": {"enum": list(RUN_STATES)},
            "brokerageMode": {"enum": ["KIS_MOCK", "INTERNAL_PAPER"]},
            "policyId": _nullable(_policy_identifier()),
            "policyVersion": _nullable({"type": "integer", "minimum": 1}),
            "selectedSymbol": _nullable(_symbol()),
            "selectedSide": _nullable({"enum": ["BUY", "SELL"]}),
            "orderQuantity": optional_nonnegative,
            "filledQuantity": optional_nonnegative,
            "leavesQuantity": optional_nonnegative,
            "limitPriceKrw": _nullable({"type": "integer", "minimum": 1}),
            "estimatedAmountKrw": _nullable({"type": "integer", "minimum": 1}),
            "exitReason": _nullable({"enum": list(EXIT_REASONS)}),
            "physicalSubmitCount": {"type": "integer", "minimum": 0, "maximum": 1},
            "providerCalls": {"type": "integer", "minimum": 0, "maximum": 64},
            "screeningProviderCallCount": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1,
            },
            "groundingQueryCount": {"type": "integer", "minimum": 0, "maximum": 32},
            "judgeCallCount": {"type": "integer", "minimum": 0, "maximum": 2},
            "evidenceCount": {"type": "integer", "minimum": 0, "maximum": 155},
            "evidenceSetSha256": _nullable(_sha()),
            "aiSettingsSha256": _nullable(_sha()),
            "startedAt": _timestamp(),
            "updatedAt": _timestamp(),
        },
    )


def _run_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/automation-run.v3.schema.json",
        "title": "automation-run.v3",
        **_run_body(),
    }


def _run_detail_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/automation-run-detail.v3.schema.json",
        "title": "automation-run-detail.v3",
        **_closed(
            ["contractId", "run", "candidateScreenings"],
            {
                "contractId": {"const": "automation-run-detail.v3"},
                "run": _run_body(),
                "candidateScreenings": {
                    "type": "array",
                    "maxItems": 31,
                    "items": _screening_body(),
                },
            },
        ),
    }


def _position_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/automation-position.v3.schema.json",
        "title": "automation-position.v3",
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
                "maxHoldingSessions",
                "atrPeriod",
                "atrMultiplierMilli",
                "modelSellEnabled",
                "peakPriceKrw",
                "atrAsOfSession",
                "trailingStopKrw",
                "status",
                "exitReason",
                "botOwned",
                "shortAllowed",
                "createdAt",
                "closedAt",
            ],
            {
                "contractId": {"const": "automation-position.v3"},
                "positionId": _identifier("auto_pos"),
                "accountId": _identifier("acct"),
                "symbol": _symbol(),
                "quantity": {"type": "integer", "minimum": 1},
                "entryAverageFillPriceKrw": {"type": "integer", "minimum": 1},
                "entrySession": _date(),
                "expirySession": _nullable(_date()),
                "policyId": _policy_identifier(),
                "policyVersion": {"type": "integer", "minimum": 1},
                "stopLossBps": {"type": "integer", "minimum": 100, "maximum": 1_500},
                "takeProfitBps": {"type": "integer", "minimum": 200, "maximum": 3_000},
                "maxHoldingSessions": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1_260,
                },
                "atrPeriod": {"type": "integer", "minimum": 5, "maximum": 100},
                "atrMultiplierMilli": {
                    "type": "integer",
                    "minimum": 1_000,
                    "maximum": 10_000,
                    "multipleOf": 100,
                },
                "modelSellEnabled": {"type": "boolean"},
                "peakPriceKrw": {"type": "integer", "minimum": 1},
                "atrAsOfSession": _nullable(_date()),
                "trailingStopKrw": _nullable({"type": "integer", "minimum": 1}),
                "status": {
                    "enum": ["OPEN", "EXIT_PENDING", "CLOSED", "HALTED_MISMATCH"]
                },
                "exitReason": _nullable({"enum": list(EXIT_REASONS)}),
                "botOwned": {"const": True},
                "shortAllowed": {"const": False},
                "createdAt": _timestamp(),
                "closedAt": _nullable(_timestamp()),
            },
        ),
    }


def _status_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/automation-status.v3.schema.json",
        "title": "automation-status.v3",
        **_closed(
            [
                "contractId",
                "controlState",
                "projectionState",
                "controlVersion",
                "brokerageMode",
                "accountId",
                "policy",
                "aiJudgementEnabled",
                "thinkingLevel",
                "marketHistoryStatus",
                "killSwitchActive",
                "certificationStatus",
                "openPositionCount",
                "legacyOpenPositionCount",
                "unresolvedReconciliation",
                "canArm",
                "blockers",
            ],
            {
                "contractId": {"const": "automation-status.v3"},
                "controlState": {"enum": ["DISARMED", "ARMED", "HALTED"]},
                "projectionState": {"enum": ["DISARMED", "ARMED", "RUNNING", "HALTED"]},
                "controlVersion": {"type": "integer", "minimum": 1},
                "brokerageMode": {"const": "KIS_MOCK"},
                "accountId": _nullable(_identifier("acct")),
                "policy": _nullable(_policy_body()),
                "aiJudgementEnabled": {"type": "boolean"},
                "thinkingLevel": {"enum": ["minimal", "low", "medium"]},
                "marketHistoryStatus": {
                    "enum": ["EMPTY", "PARTIAL", "READY", "CATCHUP_REQUIRED"]
                },
                "killSwitchActive": {"type": "boolean"},
                "certificationStatus": {
                    "enum": ["REQUIRED", "VALID", "EXPIRED", "INVALID"]
                },
                "openPositionCount": {"type": "integer", "minimum": 0, "maximum": 5},
                "legacyOpenPositionCount": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 5,
                },
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


def _bootstrap_schema() -> dict[str, Any]:
    caps = _closed(
        ["kisDaily", "kisToken", "krxMembership", "retry"],
        {
            "kisDaily": {"type": "integer", "minimum": 0, "maximum": 403},
            "kisToken": {"type": "integer", "minimum": 0, "maximum": 1},
            "krxMembership": {"type": "integer", "minimum": 0, "maximum": 5},
            "retry": {"const": 0},
        },
    )
    actual = _closed(
        ["kisDaily", "kisToken", "krxMembership"],
        {
            "kisDaily": {"type": "integer", "minimum": 0, "maximum": 403},
            "kisToken": {"type": "integer", "minimum": 0, "maximum": 1},
            "krxMembership": {"type": "integer", "minimum": 0, "maximum": 5},
        },
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/p1-automation-market-bootstrap.v1.schema.json",
        "title": "p1-automation-market-bootstrap.v1",
        **_closed(
            [
                "contractId",
                "complete",
                "createdAt",
                "membershipMonth",
                "membership",
                "requestedSessionCount",
                "firstSessionDate",
                "lastSessionDate",
                "adjustmentMode",
                "bars",
                "providerCaps",
                "providerPhysicalCalls",
                "rawProviderResponseStored",
                "sourcePathPersisted",
                "performanceClaimAllowed",
                "accountCalls",
                "orderCalls",
                "manifestSha256",
            ],
            {
                "contractId": {"const": "p1-automation-market-bootstrap.v1"},
                "complete": {"const": True},
                "createdAt": _timestamp(),
                "membershipMonth": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}$"},
                "membership": {
                    "type": "array",
                    "minItems": 31,
                    "maxItems": 31,
                    "uniqueItems": True,
                    "items": _symbol(),
                    "contains": {"const": "132030"},
                },
                "requestedSessionCount": {"const": 1_260},
                "firstSessionDate": _date(),
                "lastSessionDate": _date(),
                "adjustmentMode": {"const": "ADJUSTED"},
                "bars": _closed(
                    ["relativePath", "sha256", "rowCount"],
                    {
                        "relativePath": {
                            "type": "string",
                            "pattern": "^bars/[a-z0-9._/-]+\\.parquet$",
                            "not": {"pattern": "(^|/)\\.\\.(/|$)"},
                        },
                        "sha256": _sha(),
                        "rowCount": {"type": "integer", "minimum": 1},
                    },
                ),
                "providerCaps": caps,
                "providerPhysicalCalls": actual,
                "rawProviderResponseStored": {"const": False},
                "sourcePathPersisted": {"const": False},
                "performanceClaimAllowed": {"const": False},
                "accountCalls": {"const": 0},
                "orderCalls": {"const": 0},
                "manifestSha256": _sha(),
            },
        ),
    }


def build_schemas() -> dict[str, dict[str, Any]]:
    return {
        "automation-policy.v2": _policy_schema(),
        "automation-status.v3": _status_schema(),
        "automation-run.v3": _run_schema(),
        "automation-run-detail.v3": _run_detail_schema(),
        "automation-position.v3": _position_schema(),
        "automation-candidate-evidence.v1": _evidence_schema(),
        "vertex-news-screen.v2": _screen_schema(),
        "strong-llm-owner-settings.v2": _settings_schema(),
        "p1-automation-market-bootstrap.v1": _bootstrap_schema(),
    }


def _catalog() -> dict[str, Any]:
    operations = [
        ("GET", "/api/v3/automation/status", "getAutomationStatusV3"),
        ("PUT", "/api/v3/automation/policy", "putAutomationPolicyV3"),
        ("POST", "/api/v3/automation/arm", "armAutomationV3"),
        ("GET", "/api/v3/automation/runs", "listAutomationRunsV3"),
        ("GET", "/api/v3/automation/runs/{runId}", "getAutomationRunV3"),
        ("GET", "/api/v3/automation/positions", "listAutomationPositionsV3"),
    ]
    return {
        "apiVersion": "1.2.0",
        "contractId": "p1-automation-policy.v2",
        "blockers": list(BLOCKERS),
        "exitPriority": [
            "UNRESOLVED_RECONCILIATION",
            *EXIT_REASONS,
            "NEW_BUY",
        ],
        "operations": [
            {"method": method, "path": path, "operationId": operation_id}
            for method, path, operation_id in operations
        ],
        "presets": [
            {
                "presetId": preset,
                "stopLossBps": values[0],
                "takeProfitBps": values[1],
                "maxHoldingSessions": values[2],
                "atrPeriod": values[3],
                "atrMultiplierMilli": values[4],
                "modelSellEnabled": values[5],
            }
            for preset, values in PRESETS.items()
        ],
        "execution": {
            "evaluationTimeKst": "09:30",
            "buyCutoffTimeKst": "09:40",
            "cancelTimeKst": "15:20",
            "maxOpenPositions": 5,
            "maxNewOrdersPerSession": 1,
        },
        "evidencePolicy": {
            "candidateUniverseMax": 31,
            "groundedProviderCallsPerRunMax": 1,
            "groundingQueriesPerRunMax": 32,
            "judgeProviderCallsPerRunMax": 2,
            "zeroEvidenceScore": 0.5,
            "zeroEvidenceVeto": False,
            "freshnessRequired": False,
            "unregisteredDomainIsEvidence": False,
            "orderAuthority": "NONE",
        },
        "marketBootstrap": {
            "currentUniverseSize": 31,
            "fixedMember": "132030",
            "researchSessionMax": 1_260,
            "kisDailyPhysicalMax": 403,
            "kisTokenPhysicalMax": 1,
            "krxMembershipPhysicalMax": 5,
            "retryMax": 0,
            "accountCalls": 0,
            "orderCalls": 0,
        },
        "safety": {
            "defaultControlState": "DISARMED",
            "defaultAiJudgementEnabled": False,
            "automaticInternalPaperFallback": False,
            "kisLiveOrderCalls": 0,
            "gdeltOutboundCalls": 0,
        },
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


def _envelope(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "requestId": {"type": "string"},
            "data": {"oneOf": [data, {"type": "null"}]},
            "warnings": {"type": "array", "items": {"type": "object"}},
            "error": {"oneOf": [{"type": "object"}, {"type": "null"}]},
        },
    }


def _success(component: str) -> dict[str, Any]:
    return {
        "200": {
            "description": "Success",
            "content": {
                "application/json": {
                    "schema": {"$ref": f"#/components/schemas/{component}"}
                }
            },
        }
    }


def build_additive_openapi(schemas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    policy_request = _closed(
        [
            "capitalLimitKrw",
            "stopLossBps",
            "takeProfitBps",
            "maxHoldingSessions",
            "atrPeriod",
            "atrMultiplierMilli",
            "modelSellEnabled",
            "expectedVersion",
        ],
        {
            "capitalLimitKrw": schemas["automation-policy.v2"]["properties"][
                "capitalLimitKrw"
            ],
            "stopLossBps": schemas["automation-policy.v2"]["properties"]["stopLossBps"],
            "takeProfitBps": schemas["automation-policy.v2"]["properties"][
                "takeProfitBps"
            ],
            "maxHoldingSessions": schemas["automation-policy.v2"]["properties"][
                "maxHoldingSessions"
            ],
            "atrPeriod": schemas["automation-policy.v2"]["properties"]["atrPeriod"],
            "atrMultiplierMilli": schemas["automation-policy.v2"]["properties"][
                "atrMultiplierMilli"
            ],
            "modelSellEnabled": {"type": "boolean"},
            "expectedVersion": {"type": "integer", "minimum": 0},
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
                "items": {"$ref": "#/components/schemas/AutomationRunV3"},
            },
            "nextCursor": _nullable({"type": "string", "maxLength": 512}),
        },
    )
    position_page = _closed(
        ["items"],
        {
            "items": {
                "type": "array",
                "maxItems": 5,
                "items": {"$ref": "#/components/schemas/AutomationPositionV3"},
            }
        },
    )
    components: dict[str, Any] = {
        "AutomationPolicyV3": schemas["automation-policy.v2"],
        "AutomationStatusV3": schemas["automation-status.v3"],
        "AutomationRunV3": schemas["automation-run.v3"],
        "AutomationRunDetailV3": schemas["automation-run-detail.v3"],
        "AutomationPositionV3": schemas["automation-position.v3"],
        "AutomationCandidateEvidenceV1": schemas["automation-candidate-evidence.v1"],
        "PutAutomationPolicyV3Request": policy_request,
        "ArmAutomationV3Request": arm_request,
        "AutomationRunPageV3": run_page,
        "AutomationPositionPageV3": position_page,
        "ApiResponseAutomationStatusV3": _envelope(
            {"$ref": "#/components/schemas/AutomationStatusV3"}
        ),
        "ApiResponseAutomationPolicyV3": _envelope(
            {"$ref": "#/components/schemas/AutomationPolicyV3"}
        ),
        "ApiResponseAutomationRunPageV3": _envelope(
            {"$ref": "#/components/schemas/AutomationRunPageV3"}
        ),
        "ApiResponseAutomationRunDetailV3": _envelope(
            {"$ref": "#/components/schemas/AutomationRunDetailV3"}
        ),
        "ApiResponseAutomationPositionPageV3": _envelope(
            {"$ref": "#/components/schemas/AutomationPositionPageV3"}
        ),
    }
    return {
        "openapi": "3.1.0",
        "info": {"title": "P1 Automation V3 additive contract", "version": "1.2.0"},
        "paths": {
            "/api/v3/automation/status": {
                "get": {
                    "operationId": "getAutomationStatusV3",
                    "responses": _success("ApiResponseAutomationStatusV3"),
                }
            },
            "/api/v3/automation/policy": {
                "put": {
                    "operationId": "putAutomationPolicyV3",
                    "parameters": [_request_header()],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/PutAutomationPolicyV3Request"
                                }
                            }
                        },
                    },
                    "responses": _success("ApiResponseAutomationPolicyV3"),
                }
            },
            "/api/v3/automation/arm": {
                "post": {
                    "operationId": "armAutomationV3",
                    "parameters": [_request_header()],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ArmAutomationV3Request"
                                }
                            }
                        },
                    },
                    "responses": {
                        **_success("ApiResponseAutomationStatusV3"),
                        "409": {"description": "Automation V3 readiness blocker"},
                    },
                }
            },
            "/api/v3/automation/runs": {
                "get": {
                    "operationId": "listAutomationRunsV3",
                    "parameters": _page_parameters(),
                    "responses": _success("ApiResponseAutomationRunPageV3"),
                }
            },
            "/api/v3/automation/runs/{runId}": {
                "get": {
                    "operationId": "getAutomationRunV3",
                    "parameters": [
                        {
                            "in": "path",
                            "name": "runId",
                            "required": True,
                            "schema": _identifier("auto_run"),
                        }
                    ],
                    "responses": _success("ApiResponseAutomationRunDetailV3"),
                }
            },
            "/api/v3/automation/positions": {
                "get": {
                    "operationId": "listAutomationPositionsV3",
                    "responses": _success("ApiResponseAutomationPositionPageV3"),
                }
            },
        },
        "components": {"schemas": components},
    }


def validate_policy_semantics(value: dict[str, Any]) -> None:
    if value.get("takeProfitBps", 0) <= value.get("stopLossBps", 0):
        raise ContractValidationError("takeProfitBps must exceed stopLossBps.")
    multiplier = value.get("atrMultiplierMilli")
    if (
        not isinstance(multiplier, int)
        or isinstance(multiplier, bool)
        or multiplier % 100
    ):
        raise ContractValidationError(
            "atrMultiplierMilli must use exact 0.1 increments."
        )
    holding = value.get("maxHoldingSessions")
    if (
        not isinstance(holding, int)
        or isinstance(holding, bool)
        or not 0 <= holding <= 1_260
    ):
        raise ContractValidationError("maxHoldingSessions is outside 0..1260.")
    preset = value.get("presetId")
    actual = (
        value.get("stopLossBps"),
        value.get("takeProfitBps"),
        holding,
        value.get("atrPeriod"),
        multiplier,
        value.get("modelSellEnabled"),
    )
    if preset in PRESETS and actual != PRESETS[preset]:
        raise ContractValidationError("named V3 preset values drifted; use custom.")


def validate_screen_semantics(value: dict[str, Any]) -> None:
    candidates = value.get("candidates")
    if not isinstance(candidates, list):
        raise ContractValidationError("screen candidates must be an array.")
    if value.get("status") == "ABSTAIN":
        if value.get("failureReason") is None or candidates:
            raise ContractValidationError(
                "batch ABSTAIN must have one reason and zero candidates."
            )
        return
    if value.get("status") != "AVAILABLE" or value.get("failureReason") is not None:
        raise ContractValidationError("available screen status is inconsistent.")
    symbols: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ContractValidationError("screen candidate must be an object.")
        symbol = candidate.get("symbol")
        if not isinstance(symbol, str) or symbol in symbols:
            raise ContractValidationError(
                "screen candidate symbol is invalid or duplicated."
            )
        symbols.add(symbol)
        evidence = candidate.get("evidence")
        if not isinstance(evidence, list):
            raise ContractValidationError("screen evidence must be an array.")
        if not evidence and (
            candidate.get("score") != 0.5 or candidate.get("verdict") != "NO_VETO"
        ):
            raise ContractValidationError(
                "unsupported score or veto must be neutralized."
            )
        if candidate.get("verdict") == "VETO_BUY" and not evidence:
            raise ContractValidationError("VETO_BUY requires verified evidence.")
        for item in evidence:
            if (
                not isinstance(item, dict)
                or item.get("symbol") != symbol
                or item.get("verified") is not True
            ):
                raise ContractValidationError(
                    "candidate evidence is not host verified or symbol bound."
                )


def validate_bootstrap_semantics(value: dict[str, Any]) -> None:
    membership = value.get("membership")
    if (
        not isinstance(membership, list)
        or len(membership) != 31
        or len(set(membership)) != 31
        or "132030" not in membership
    ):
        raise ContractValidationError(
            "automation bootstrap membership must be exact 31 with 132030."
        )
    caps = value.get("providerCaps")
    actual = value.get("providerPhysicalCalls")
    if not isinstance(caps, dict) or not isinstance(actual, dict):
        raise ContractValidationError(
            "automation bootstrap call accounting is missing."
        )
    for key in ("kisDaily", "kisToken", "krxMembership"):
        cap = caps.get(key)
        count = actual.get(key)
        if (
            not isinstance(cap, int)
            or isinstance(cap, bool)
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
            or count > cap
        ):
            raise ContractValidationError(
                "automation bootstrap physical calls exceed the cap."
            )
    if (
        caps.get("retry") != 0
        or value.get("accountCalls") != 0
        or value.get("orderCalls") != 0
    ):
        raise ContractValidationError(
            "automation bootstrap must be retry/account/order free."
        )


def build_outputs() -> dict[Path, bytes]:
    schemas = build_schemas()
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
    catalog = _catalog()
    additive = build_additive_openapi(schemas)
    return {
        CATALOG_PATH: canonical_json(catalog),
        **{
            SCHEMA_PATHS[schema_id]: canonical_json(schema)
            for schema_id, schema in schemas.items()
        },
        ADDITIVE_OPENAPI_PATH: canonical_json(additive),
    }


def generate(*, check: bool) -> None:
    for path, payload in build_outputs().items():
        if check:
            if not path.is_file() or path.read_bytes() != payload:
                raise ContractValidationError(
                    f"generated P1 V3 artifact drifted: {path.relative_to(ROOT)}"
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
        print(f"P1_V3_AUTOMATION_CONTRACTS=FAIL: {error}", file=sys.stderr)
        return 1
    print("P1_V3_AUTOMATION_CONTRACTS=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
