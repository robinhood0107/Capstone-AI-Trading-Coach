from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
import unicodedata
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Iterable, Mapping, Sequence

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

_SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from contracts.openapi_env import parse_openapi_environment


REPO_ROOT = _SCRIPT_REPO_ROOT
CATALOG_PATH = REPO_ROOT / "contracts" / "catalogs" / "s2-1-principle-contract.v1.json"

SCHEMA_FILE_NAMES: Final[dict[str, str]] = {
    "PrincipleRule": "principle-rule.schema.json",
    "PrincipleCurrent": "principle.schema.json",
    "PrinciplePresetListData": "principle-preset-list.schema.json",
    "PrincipleCreateRequest": "principle-create-request.schema.json",
    "PrincipleUpdateRequest": "principle-update-request.schema.json",
    "PrincipleOwnerListData": "principle-list-response.schema.json",
    "PrincipleHistoryData": "principle-history-response.schema.json",
    "ErrorEnvelope": "principle-error.schema.json",
}
OUTPUTS: Final[frozenset[str]] = frozenset(
    {
        "contracts/schemas/s2-1-principle-catalog.schema.json",
        "contracts/schemas/principle-rule.schema.json",
        "contracts/schemas/principle.schema.json",
        "contracts/schemas/principle-preset-list.schema.json",
        "contracts/schemas/principle-create-request.schema.json",
        "contracts/schemas/principle-update-request.schema.json",
        "contracts/schemas/principle-list-response.schema.json",
        "contracts/schemas/principle-history-response.schema.json",
        "contracts/schemas/principle-error.schema.json",
        "contracts/examples/principle.valid.json",
        "contracts/examples/principle-presets.valid.json",
        "contracts/examples/principle-create.valid.json",
        "contracts/examples/principle-create-custom-rules.valid.json",
        "contracts/examples/principle-update.valid.json",
        "contracts/examples/principle-update-no-op.valid.json",
        "contracts/examples/principle-list.valid.json",
        "contracts/examples/principle-list-next-page.valid.json",
        "contracts/examples/principle-list-empty.valid.json",
        "contracts/examples/principle-history.valid.json",
        "contracts/examples/principle-history-next-page.valid.json",
        "contracts/examples/principle-history-empty.valid.json",
        "contracts/examples/principle-error-validation.valid.json",
        "contracts/examples/principle-error-cursor.valid.json",
        "contracts/examples/principle-error-unauthorized.valid.json",
        "contracts/examples/principle-error-forbidden.valid.json",
        "contracts/examples/principle-error-not-found.valid.json",
        "contracts/examples/principle-error-conflict.valid.json",
        "contracts/examples/principle-error-version-exhausted.valid.json",
        "contracts/examples/principle-error-payload-too-large.valid.json",
        "contracts/examples/invalid/principle.invalid.json",
        "contracts/examples/invalid/principle.duplicate-rule.invalid.json",
        "contracts/examples/invalid/principle.invalid-tuple.invalid.json",
        "contracts/examples/invalid/principle.threshold-range.invalid.json",
        "contracts/examples/invalid/principle.threshold-scale.invalid.json",
        "contracts/examples/invalid/principle.threshold-null.invalid.json",
        "contracts/examples/invalid/principle.threshold-string.invalid.json",
        "contracts/examples/invalid/principle.unknown-property.invalid.json",
        "contracts/examples/invalid/principle.enabled-allow.invalid.json",
        "contracts/examples/invalid/principle.disabled-block.invalid.json",
        "contracts/examples/invalid/principle.too-many-rules.invalid.json",
        "contracts/examples/invalid/principle.evidence-missing.invalid.json",
        "contracts/examples/invalid/principle.evidence-optional-hard.invalid.json",
        "contracts/examples/invalid/principle-update.empty-rules.invalid.json",
        "contracts/examples/invalid/principle-update.invalid-status.invalid.json",
        "contracts/examples/invalid/principle-update.missing-field.invalid.json",
        "contracts/examples/invalid/principle-create.missing-title.invalid.json",
    }
)

EXPECTED_TOP_LEVEL_KEYS: Final[set[str]] = {
    "$schema",
    "contractId",
    "contractVersion",
    "disclaimer",
    "limits",
    "enums",
    "legacyEvidenceInference",
    "ruleDefinitions",
    "presets",
    "schemas",
    "operations",
}
EXPECTED_DISCLAIMER: Final[dict[str, str]] = {
    "ko": "이 프리셋은 교육·시연용 기본값이며 투자 권유, 개인별 적합성 판단 또는 손실 방지를 보장하지 않습니다.",
    "en": (
        "These presets are educational demo defaults. They are not investment advice, "
        "an assessment of personal suitability, or a guarantee against loss."
    ),
}
EXPECTED_LIMITS: Final[dict[str, int]] = {
    "titleMinCodePoints": 1,
    "titleMaxCodePoints": 120,
    "rulesMinItems": 1,
    "rulesMaxItems": 8,
    "pageDefault": 50,
    "pageMin": 1,
    "pageMax": 200,
    "requestMaxBytes": 1_048_576,
    "cursorMaxChars": 2_048,
    "cursorTtlSeconds": 900,
    "maxVersion": 2_147_483_647,
}
EXPECTED_ENUMS: Final[dict[str, list[str]]] = {
    "evidenceRequirements": ["OPTIONAL", "REQUIRED"],
    "modes": ["GUIDE", "STRICT"],
    "statuses": ["ACTIVE", "ARCHIVED"],
    "ownerSorts": ["UPDATED_AT_DESC", "UPDATED_AT_ASC"],
    "historySorts": ["VERSION_DESC", "VERSION_ASC"],
    "violationReasons": [
        "REQUIRED",
        "UNKNOWN_FIELD",
        "INVALID_FORMAT",
        "INVALID_ENUM",
        "UNAVAILABLE",
        "OUT_OF_RANGE",
        "INVALID_SCALE",
        "TOO_FEW_ITEMS",
        "TOO_MANY_ITEMS",
        "DUPLICATE",
        "INVALID_COMBINATION",
        "INVALID_CURSOR",
    ],
}
EXPECTED_LEGACY_EVIDENCE_INFERENCE: Final[dict[str, Any]] = {
    "disabledMissingField": "RULE_DEFAULT",
    "enabledMissingField": "REQUIRED",
    "policyVersion": "s2-1-legacy-evidence-inference/v1",
    "rewriteHistoricalRows": False,
    "unknownTuple": "REJECT",
}

EXPECTED_RULE_DEFINITIONS: Final[list[dict[str, Any]]] = [
    {
        "order": 1,
        "ruleId": "max_position_per_asset",
        "ruleType": "POSITION_LIMIT",
        "metric": "asset_weight",
        "operator": "<=",
        "thresholdSchema": {
            "jsonType": "number",
            "minimum": Decimal("0"),
            "maximum": Decimal("1"),
            "minimumInclusive": True,
            "maximumInclusive": True,
            "maxNormalizedScale": 4,
        },
        "enabledSeverities": ["BLOCK"],
        "disabledSeverity": "ALLOW",
        "evidenceRequirements": ["REQUIRED"],
        "defaultEvidenceRequirement": "REQUIRED",
    },
    {
        "order": 2,
        "ruleId": "max_gold_etf_etn_weight",
        "ruleType": "POSITION_LIMIT",
        "metric": "gold_etf_etn_weight",
        "operator": "<=",
        "thresholdSchema": {
            "jsonType": "number",
            "minimum": Decimal("0"),
            "maximum": Decimal("1"),
            "minimumInclusive": True,
            "maximumInclusive": True,
            "maxNormalizedScale": 4,
        },
        "enabledSeverities": ["BLOCK"],
        "disabledSeverity": "ALLOW",
        "evidenceRequirements": ["REQUIRED"],
        "defaultEvidenceRequirement": "REQUIRED",
    },
    {
        "order": 3,
        "ruleId": "max_single_order_amount",
        "ruleType": "ORDER_SIZE",
        "metric": "order_amount_krw",
        "operator": "<=",
        "thresholdSchema": {
            "jsonType": "integer",
            "minimum": 0,
            "maximum": 10_000_000_000,
            "minimumInclusive": True,
            "maximumInclusive": True,
            "maxNormalizedScale": 0,
        },
        "enabledSeverities": ["BLOCK"],
        "disabledSeverity": "ALLOW",
        "evidenceRequirements": ["REQUIRED"],
        "defaultEvidenceRequirement": "REQUIRED",
    },
    {
        "order": 4,
        "ruleId": "daily_loss_guard",
        "ruleType": "LOSS_LIMIT",
        "metric": "daily_loss_rate",
        "operator": ">=",
        "thresholdSchema": {
            "jsonType": "number",
            "minimum": Decimal("-1"),
            "maximum": Decimal("0"),
            "minimumInclusive": True,
            "maximumInclusive": True,
            "maxNormalizedScale": 4,
        },
        "enabledSeverities": ["BLOCK"],
        "disabledSeverity": "ALLOW",
        "evidenceRequirements": ["REQUIRED"],
        "defaultEvidenceRequirement": "REQUIRED",
    },
    {
        "order": 5,
        "ruleId": "mdd_guard",
        "ruleType": "DRAWDOWN_LIMIT",
        "metric": "mdd",
        "operator": ">=",
        "thresholdSchema": {
            "jsonType": "number",
            "minimum": Decimal("-1"),
            "maximum": Decimal("0"),
            "minimumInclusive": True,
            "maximumInclusive": True,
            "maxNormalizedScale": 4,
        },
        "enabledSeverities": ["BLOCK"],
        "disabledSeverity": "ALLOW",
        "evidenceRequirements": ["REQUIRED"],
        "defaultEvidenceRequirement": "REQUIRED",
    },
    {
        "order": 6,
        "ruleId": "max_daily_orders",
        "ruleType": "TRADING_FREQUENCY",
        "metric": "daily_order_count",
        "operator": "<=",
        "thresholdSchema": {
            "jsonType": "integer",
            "minimum": 0,
            "maximum": 1_000,
            "minimumInclusive": True,
            "maximumInclusive": True,
            "maxNormalizedScale": 0,
        },
        "enabledSeverities": ["WARN", "BLOCK"],
        "disabledSeverity": "ALLOW",
        "evidenceRequirements": ["REQUIRED"],
        "defaultEvidenceRequirement": "REQUIRED",
    },
    {
        "order": 7,
        "ruleId": "negative_news_guard",
        "ruleType": "NEWS_GUARD",
        "metric": "negative_news_score",
        "operator": "<=",
        "thresholdSchema": {
            "jsonType": "number",
            "minimum": Decimal("0"),
            "maximum": Decimal("1"),
            "minimumInclusive": True,
            "maximumInclusive": True,
            "maxNormalizedScale": 4,
        },
        "enabledSeverities": ["WARN", "BLOCK"],
        "disabledSeverity": "ALLOW",
        "evidenceRequirements": ["OPTIONAL", "REQUIRED"],
        "defaultEvidenceRequirement": "OPTIONAL",
    },
    {
        "order": 8,
        "ruleId": "disclosure_risk_guard",
        "ruleType": "DISCLOSURE_GUARD",
        "metric": "disclosure_risk_score",
        "operator": "<=",
        "thresholdSchema": {
            "jsonType": "number",
            "minimum": Decimal("0"),
            "maximum": Decimal("1"),
            "minimumInclusive": True,
            "maximumInclusive": True,
            "maxNormalizedScale": 4,
        },
        "enabledSeverities": ["WARN", "BLOCK"],
        "disabledSeverity": "ALLOW",
        "evidenceRequirements": ["OPTIONAL", "REQUIRED"],
        "defaultEvidenceRequirement": "OPTIONAL",
    },
]

EXPECTED_PRESET_METADATA: Final[list[dict[str, Any]]] = [
    {
        "order": 1,
        "presetId": "conservative",
        "nameKo": "보수형",
        "nameEn": "Conservative",
        "descriptionKo": "손실 제한과 분산투자를 우선하는 데모 원칙",
        "descriptionEn": "Demo principle prioritizing loss limits and diversification",
        "mode": "GUIDE",
        "thresholds": [
            Decimal("0.15"),
            Decimal("0.20"),
            300_000,
            Decimal("-0.02"),
            Decimal("-0.10"),
            2,
            Decimal("0.50"),
            Decimal("0.50"),
        ],
        "severities": ["BLOCK", "BLOCK", "BLOCK", "BLOCK", "BLOCK", "BLOCK", "ALLOW", "ALLOW"],
        "enabled": [True, True, True, True, True, True, False, False],
        "evidenceRequirements": [
            "REQUIRED",
            "REQUIRED",
            "REQUIRED",
            "REQUIRED",
            "REQUIRED",
            "REQUIRED",
            "OPTIONAL",
            "OPTIONAL",
        ],
    },
    {
        "order": 2,
        "presetId": "balanced",
        "nameKo": "균형형",
        "nameEn": "Balanced",
        "descriptionKo": "위험 제한과 거래 기회를 균형 있게 적용하는 데모 원칙",
        "descriptionEn": "Demo principle balancing risk limits and trading flexibility",
        "mode": "GUIDE",
        "thresholds": [
            Decimal("0.20"),
            Decimal("0.30"),
            500_000,
            Decimal("-0.03"),
            Decimal("-0.15"),
            3,
            Decimal("0.70"),
            Decimal("0.70"),
        ],
        "severities": ["BLOCK", "BLOCK", "BLOCK", "BLOCK", "BLOCK", "WARN", "ALLOW", "ALLOW"],
        "enabled": [True, True, True, True, True, True, False, False],
        "evidenceRequirements": [
            "REQUIRED",
            "REQUIRED",
            "REQUIRED",
            "REQUIRED",
            "REQUIRED",
            "REQUIRED",
            "OPTIONAL",
            "OPTIONAL",
        ],
    },
    {
        "order": 3,
        "presetId": "aggressive",
        "nameKo": "공격형",
        "nameEn": "Aggressive",
        "descriptionKo": "더 넓은 위험 한도 안에서도 핵심 손실 제한을 유지하는 데모 원칙",
        "descriptionEn": "Demo principle retaining core loss controls within wider risk limits",
        "mode": "GUIDE",
        "thresholds": [
            Decimal("0.30"),
            Decimal("0.40"),
            1_000_000,
            Decimal("-0.05"),
            Decimal("-0.25"),
            5,
            Decimal("0.85"),
            Decimal("0.85"),
        ],
        "severities": ["BLOCK", "BLOCK", "BLOCK", "BLOCK", "BLOCK", "WARN", "ALLOW", "ALLOW"],
        "enabled": [True, True, True, True, True, True, False, False],
        "evidenceRequirements": [
            "REQUIRED",
            "REQUIRED",
            "REQUIRED",
            "REQUIRED",
            "REQUIRED",
            "REQUIRED",
            "OPTIONAL",
            "OPTIONAL",
        ],
    },
]

EXPECTED_OPERATIONS: Final[list[dict[str, Any]]] = [
    {
        "operationId": "listPrinciplePresets",
        "method": "GET",
        "path": "/api/v1/principle-presets",
        "successStatus": 200,
        "requestSchema": None,
        "queryFields": [],
        "responseSchema": "PrinciplePresetListData",
        "errorCodes": ["UNAUTHORIZED", "FORBIDDEN"],
    },
    {
        "operationId": "createPrinciple",
        "method": "POST",
        "path": "/api/v1/principles",
        "successStatus": 201,
        "requestSchema": "PrincipleCreateRequest",
        "queryFields": [],
        "responseSchema": "PrincipleCurrent",
        "errorCodes": [
            "VALIDATION_ERROR",
            "UNAUTHORIZED",
            "FORBIDDEN",
            "PAYLOAD_TOO_LARGE",
        ],
    },
    {
        "operationId": "listPrinciples",
        "method": "GET",
        "path": "/api/v1/principles",
        "successStatus": 200,
        "requestSchema": None,
        "queryFields": ["cursor", "size", "sort"],
        "responseSchema": "PrincipleOwnerListData",
        "errorCodes": ["VALIDATION_ERROR", "UNAUTHORIZED", "FORBIDDEN"],
    },
    {
        "operationId": "getPrinciple",
        "method": "GET",
        "path": "/api/v1/principles/{principleId}",
        "successStatus": 200,
        "requestSchema": None,
        "queryFields": [],
        "responseSchema": "PrincipleCurrent",
        "errorCodes": ["VALIDATION_ERROR", "UNAUTHORIZED", "FORBIDDEN", "NOT_FOUND"],
    },
    {
        "operationId": "updatePrinciple",
        "method": "PUT",
        "path": "/api/v1/principles/{principleId}",
        "successStatus": 200,
        "requestSchema": "PrincipleUpdateRequest",
        "queryFields": [],
        "responseSchema": "PrincipleCurrent",
        "errorCodes": [
            "VALIDATION_ERROR",
            "UNAUTHORIZED",
            "FORBIDDEN",
            "NOT_FOUND",
            "CONFLICT",
            "VERSION_EXHAUSTED",
            "PAYLOAD_TOO_LARGE",
        ],
    },
    {
        "operationId": "listPrincipleVersions",
        "method": "GET",
        "path": "/api/v1/principles/{principleId}/versions",
        "successStatus": 200,
        "requestSchema": None,
        "queryFields": ["cursor", "size", "sort"],
        "responseSchema": "PrincipleHistoryData",
        "errorCodes": ["VALIDATION_ERROR", "UNAUTHORIZED", "FORBIDDEN", "NOT_FOUND"],
    },
]


class ContractValidationError(ValueError):
    """S2.1 catalog, schema 또는 fixture가 잠긴 v1 계약을 위반할 때 발생한다."""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractValidationError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_constant(token: str) -> None:
    raise ContractValidationError(f"Non-finite JSON number is forbidden: {token}")


def load_json_bytes_strict(raw: bytes, *, source: str) -> Any:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ContractValidationError(f"{source}: UTF-8 BOM is forbidden.")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ContractValidationError(f"{source}: invalid UTF-8.") from error
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ContractValidationError) as error:
        if isinstance(error, ContractValidationError):
            raise
        raise ContractValidationError(f"{source}: invalid JSON: {error.msg}") from error


def _decimal_token(value: Decimal) -> str:
    if not value.is_finite():
        raise ContractValidationError("Non-finite decimal cannot be canonicalized.")
    if len(value.as_tuple().digits) > 10_000 or abs(value.adjusted()) > 10_000:
        raise ContractValidationError("Decimal exceeds the canonicalization resource bound.")
    if value == 0:
        return "0"
    token = format(value.normalize(), "f")
    if "." in token:
        token = token.rstrip("0").rstrip(".")
    return token


def _canonical_lines(value: Any, level: int) -> list[str]:
    indentation = "  " * level
    child_indentation = "  " * (level + 1)
    if value is None:
        return [indentation + "null"]
    if value is True:
        return [indentation + "true"]
    if value is False:
        return [indentation + "false"]
    if isinstance(value, int) and not isinstance(value, bool):
        return [indentation + str(value)]
    if isinstance(value, Decimal):
        return [indentation + _decimal_token(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractValidationError("Non-finite float cannot be canonicalized.")
        return [indentation + _decimal_token(Decimal(str(value)))]
    if isinstance(value, str):
        return [indentation + json.dumps(value, ensure_ascii=False)]
    if isinstance(value, list):
        if not value:
            return [indentation + "[]"]
        lines = [indentation + "["]
        for index, item in enumerate(value):
            item_lines = _canonical_lines(item, level + 1)
            if index < len(value) - 1:
                item_lines[-1] += ","
            lines.extend(item_lines)
        lines.append(indentation + "]")
        return lines
    if isinstance(value, dict):
        if not value:
            return [indentation + "{}"]
        if any(not isinstance(key, str) for key in value):
            raise ContractValidationError("Canonical JSON object keys must be strings.")
        lines = [indentation + "{"]
        keys = sorted(value)
        for index, key in enumerate(keys):
            key_token = json.dumps(key, ensure_ascii=False)
            value_lines = _canonical_lines(value[key], level + 1)
            first_value = value_lines[0][len(child_indentation) :]
            lines.append(f"{child_indentation}{key_token}: {first_value}")
            lines.extend(value_lines[1:])
            if index < len(keys) - 1:
                lines[-1] += ","
        lines.append(indentation + "}")
        return lines
    raise ContractValidationError(f"Unsupported canonical JSON value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    return ("\n".join(_canonical_lines(value, 0)) + "\n").encode("utf-8")


def _catalog_meta_schema() -> dict[str, Any]:
    threshold_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "jsonType",
            "minimum",
            "maximum",
            "minimumInclusive",
            "maximumInclusive",
            "maxNormalizedScale",
        ],
        "properties": {
            "jsonType": {"enum": ["number", "integer"]},
            "minimum": {"type": ["number", "integer"]},
            "maximum": {"type": ["number", "integer"]},
            "minimumInclusive": {"const": True},
            "maximumInclusive": {"const": True},
            "maxNormalizedScale": {"type": "integer", "minimum": 0, "maximum": 4},
        },
    }
    rule_definition = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "order",
            "ruleId",
            "ruleType",
            "metric",
            "operator",
            "thresholdSchema",
            "enabledSeverities",
            "disabledSeverity",
            "evidenceRequirements",
            "defaultEvidenceRequirement",
        ],
        "properties": {
            "order": {"type": "integer", "minimum": 1, "maximum": 8},
            "ruleId": {"type": "string", "minLength": 1},
            "ruleType": {"type": "string", "minLength": 1},
            "metric": {"type": "string", "minLength": 1},
            "operator": {"enum": ["<=", ">="]},
            "thresholdSchema": threshold_schema,
            "enabledSeverities": {
                "type": "array",
                "minItems": 1,
                "maxItems": 2,
                "items": {"enum": ["WARN", "BLOCK"]},
                "uniqueItems": True,
            },
            "disabledSeverity": {"const": "ALLOW"},
            "evidenceRequirements": {
                "type": "array",
                "minItems": 1,
                "maxItems": 2,
                "items": {"enum": EXPECTED_ENUMS["evidenceRequirements"]},
                "uniqueItems": True,
            },
            "defaultEvidenceRequirement": {
                "enum": EXPECTED_ENUMS["evidenceRequirements"],
            },
        },
    }
    default_rule = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "ruleId",
            "threshold",
            "severity",
            "enabled",
            "evidenceRequirement",
        ],
        "properties": {
            "ruleId": {"type": "string", "minLength": 1},
            "threshold": {"type": ["number", "integer"]},
            "severity": {"enum": ["ALLOW", "WARN", "BLOCK"]},
            "enabled": {"type": "boolean"},
            "evidenceRequirement": {
                "enum": EXPECTED_ENUMS["evidenceRequirements"],
            },
        },
    }
    preset = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "order",
            "presetId",
            "nameKo",
            "nameEn",
            "descriptionKo",
            "descriptionEn",
            "mode",
            "defaultRules",
        ],
        "properties": {
            "order": {"type": "integer", "minimum": 1, "maximum": 3},
            "presetId": {"enum": ["conservative", "balanced", "aggressive"]},
            "nameKo": {"type": "string", "minLength": 1},
            "nameEn": {"type": "string", "minLength": 1},
            "descriptionKo": {"type": "string", "minLength": 1},
            "descriptionEn": {"type": "string", "minLength": 1},
            "mode": {"const": "GUIDE"},
            "defaultRules": {
                "type": "array",
                "minItems": 8,
                "maxItems": 8,
                "items": default_rule,
            },
        },
    }
    operation = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "operationId",
            "method",
            "path",
            "successStatus",
            "requestSchema",
            "queryFields",
            "responseSchema",
            "errorCodes",
        ],
        "properties": {
            "operationId": {"type": "string", "minLength": 1},
            "method": {"enum": ["GET", "POST", "PUT"]},
            "path": {"type": "string", "pattern": "^/api/v1/"},
            "successStatus": {"enum": [200, 201]},
            "requestSchema": {
                "type": ["string", "null"],
                "enum": [None, "PrincipleCreateRequest", "PrincipleUpdateRequest"],
            },
            "queryFields": {
                "type": "array",
                "items": {"enum": ["cursor", "size", "sort"]},
                "uniqueItems": True,
            },
            "responseSchema": {"enum": list(SCHEMA_FILE_NAMES)},
            "errorCodes": {
                "type": "array",
                "items": {
                    "enum": [
                        "VALIDATION_ERROR",
                        "UNAUTHORIZED",
                        "FORBIDDEN",
                        "NOT_FOUND",
                        "CONFLICT",
                        "VERSION_EXHAUSTED",
                        "PAYLOAD_TOO_LARGE",
                    ]
                },
                "uniqueItems": True,
            },
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/s2-1-principle-catalog.schema.json",
        "title": "S2.1 Principle contract catalog v1",
        "type": "object",
        "additionalProperties": False,
        "required": sorted(EXPECTED_TOP_LEVEL_KEYS),
        "properties": {
            "$schema": {"const": "https://json-schema.org/draft/2020-12/schema"},
            "contractId": {"const": "s2-1-principle-contract"},
            "contractVersion": {"const": 1},
            "disclaimer": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ko", "en"],
                "properties": {
                    "ko": {"const": EXPECTED_DISCLAIMER["ko"]},
                    "en": {"const": EXPECTED_DISCLAIMER["en"]},
                },
            },
            "limits": {
                "type": "object",
                "additionalProperties": False,
                "required": sorted(EXPECTED_LIMITS),
                "properties": {
                    key: {"const": value} for key, value in EXPECTED_LIMITS.items()
                },
            },
            "enums": {
                "type": "object",
                "additionalProperties": False,
                "required": sorted(EXPECTED_ENUMS),
                "properties": {
                    key: {"const": value} for key, value in EXPECTED_ENUMS.items()
                },
            },
            "legacyEvidenceInference": {
                "type": "object",
                "additionalProperties": False,
                "required": sorted(EXPECTED_LEGACY_EVIDENCE_INFERENCE),
                "properties": {
                    key: {"const": value}
                    for key, value in EXPECTED_LEGACY_EVIDENCE_INFERENCE.items()
                },
            },
            "ruleDefinitions": {
                "type": "array",
                "minItems": 8,
                "maxItems": 8,
                "items": rule_definition,
            },
            "presets": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": preset,
            },
            "schemas": {
                "type": "object",
                "additionalProperties": False,
                "required": list(SCHEMA_FILE_NAMES),
                "properties": {
                    name: {"type": "object"} for name in SCHEMA_FILE_NAMES
                },
            },
            "operations": {
                "type": "array",
                "minItems": 6,
                "maxItems": 6,
                "items": operation,
            },
        },
    }


def _first_validation_error(
    validator: Draft202012Validator, value: Any
) -> str | None:
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.absolute_path))
    if not errors:
        return None
    error = errors[0]
    location = "/" + "/".join(str(part) for part in error.absolute_path)
    return f"{location or '/'}: {error.message}"


def _normalized_scale(value: Decimal) -> int:
    normalized = value.normalize()
    if normalized == 0:
        return 0
    return max(0, -normalized.as_tuple().exponent)


def _as_decimal(value: Any, *, location: str) -> Decimal:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        raise ContractValidationError(f"{location}: threshold must be a JSON number.")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ContractValidationError(f"{location}: threshold must be finite.")
        return value
    if isinstance(value, float) and math.isfinite(value):
        return Decimal(str(value))
    raise ContractValidationError(f"{location}: threshold must be a finite JSON number.")


def _validate_threshold(value: Any, definition: Mapping[str, Any], *, location: str) -> None:
    schema = definition["thresholdSchema"]
    decimal_value = _as_decimal(value, location=location)
    if schema["jsonType"] == "integer" and decimal_value != decimal_value.to_integral_value():
        raise ContractValidationError(f"{location}: integer threshold required.")
    minimum = _as_decimal(schema["minimum"], location=f"{location}/minimum")
    maximum = _as_decimal(schema["maximum"], location=f"{location}/maximum")
    if decimal_value < minimum or decimal_value > maximum:
        raise ContractValidationError(f"{location}: threshold is outside the inclusive range.")
    if _normalized_scale(decimal_value) > schema["maxNormalizedScale"]:
        raise ContractValidationError(f"{location}: threshold normalized scale is too large.")


def _validate_default_rules(
    rules: Any,
    definitions: Sequence[Mapping[str, Any]],
    *,
    location: str,
) -> None:
    if not isinstance(rules, list) or len(rules) != 8:
        raise ContractValidationError(f"{location}: preset must contain exactly eight rules.")
    for index, (rule, definition) in enumerate(zip(rules, definitions, strict=True)):
        if not isinstance(rule, dict) or set(rule) != {
            "ruleId",
            "threshold",
            "severity",
            "enabled",
            "evidenceRequirement",
        }:
            raise ContractValidationError(f"{location}/{index}: invalid default rule shape.")
        if rule["ruleId"] != definition["ruleId"]:
            raise ContractValidationError(f"{location}/{index}: canonical rule order changed.")
        _validate_threshold(rule["threshold"], definition, location=f"{location}/{index}/threshold")
        if rule["enabled"] is True:
            if rule["severity"] not in definition["enabledSeverities"]:
                raise ContractValidationError(
                    f"{location}/{index}: enabled severity is invalid."
                )
        elif rule["enabled"] is False:
            if rule["severity"] != definition["disabledSeverity"]:
                raise ContractValidationError(
                    f"{location}/{index}: disabled severity must be ALLOW."
                )
        else:
            raise ContractValidationError(f"{location}/{index}: enabled must be boolean.")
        if rule["evidenceRequirement"] not in definition["evidenceRequirements"]:
            raise ContractValidationError(
                f"{location}/{index}: evidence requirement is invalid."
            )


def validate_catalog_semantics(catalog: Any) -> None:
    if not isinstance(catalog, dict) or set(catalog) != EXPECTED_TOP_LEVEL_KEYS:
        raise ContractValidationError("Catalog top-level keys do not match v1.")

    meta_error = _first_validation_error(Draft202012Validator(_catalog_meta_schema()), catalog)
    if meta_error is not None:
        raise ContractValidationError(f"Catalog meta-schema violation: {meta_error}")

    if catalog["legacyEvidenceInference"] != EXPECTED_LEGACY_EVIDENCE_INFERENCE:
        raise ContractValidationError(
            "Legacy evidence inference does not match the approved v1 policy."
        )

    definitions = catalog["ruleDefinitions"]
    if definitions != EXPECTED_RULE_DEFINITIONS:
        raise ContractValidationError("Rule definitions do not match the approved v1 matrix.")
    if len({definition["ruleId"] for definition in definitions}) != 8:
        raise ContractValidationError("Rule definitions contain duplicate rule IDs.")

    presets = catalog["presets"]
    for index, (preset, expected) in enumerate(
        zip(presets, EXPECTED_PRESET_METADATA, strict=True)
    ):
        for field in (
            "order",
            "presetId",
            "nameKo",
            "nameEn",
            "descriptionKo",
            "descriptionEn",
            "mode",
        ):
            if preset[field] != expected[field]:
                raise ContractValidationError(
                    f"Preset {index} metadata field {field} does not match v1."
                )
        _validate_default_rules(
            preset["defaultRules"], definitions, location=f"/presets/{index}/defaultRules"
        )
        if [rule["threshold"] for rule in preset["defaultRules"]] != expected["thresholds"]:
            raise ContractValidationError(f"Preset {index} thresholds do not match v1.")
        if [rule["severity"] for rule in preset["defaultRules"]] != expected["severities"]:
            raise ContractValidationError(f"Preset {index} severities do not match v1.")
        if [rule["enabled"] for rule in preset["defaultRules"]] != expected["enabled"]:
            raise ContractValidationError(f"Preset {index} enabled flags do not match v1.")
        if [
            rule["evidenceRequirement"] for rule in preset["defaultRules"]
        ] != expected["evidenceRequirements"]:
            raise ContractValidationError(
                f"Preset {index} evidence requirements do not match v1."
            )

    if catalog["operations"] != EXPECTED_OPERATIONS:
        raise ContractValidationError("Operation routing data does not match v1.")

    schemas = catalog["schemas"]
    if set(schemas) != set(SCHEMA_FILE_NAMES):
        raise ContractValidationError("Catalog schema registry does not match v1.")
    for schema_name in SCHEMA_FILE_NAMES:
        try:
            Draft202012Validator.check_schema(_standalone_schema(catalog, schema_name))
        except SchemaError as error:
            raise ContractValidationError(
                f"{schema_name} is not a valid Draft 2020-12 schema: {error.message}"
            ) from error


def load_catalog(path: Path = CATALOG_PATH, *, require_canonical: bool = True) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ContractValidationError(f"Catalog could not be read: {path}") from error
    catalog = load_json_bytes_strict(raw, source=path.as_posix())
    if not isinstance(catalog, dict):
        raise ContractValidationError("Catalog root must be an object.")
    validate_catalog_semantics(catalog)
    if require_canonical and raw != canonical_json_bytes(catalog):
        raise ContractValidationError("Catalog bytes are not canonical UTF-8/LF/sorted JSON.")
    return catalog


def _rewrite_registry_refs(value: Any) -> Any:
    if isinstance(value, list):
        return [_rewrite_registry_refs(item) for item in value]
    if isinstance(value, dict):
        rewritten: dict[str, Any] = {}
        for key, item in value.items():
            if key == "$ref" and isinstance(item, str) and item.startswith("#/schemas/"):
                rewritten[key] = "#/$defs/" + item.removeprefix("#/schemas/")
            else:
                rewritten[key] = _rewrite_registry_refs(item)
        return rewritten
    return value


def _registry_dependencies(value: Any) -> set[str]:
    dependencies: set[str] = set()
    if isinstance(value, list):
        for item in value:
            dependencies.update(_registry_dependencies(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            if key == "$ref" and isinstance(item, str) and item.startswith("#/schemas/"):
                dependencies.add(item.removeprefix("#/schemas/"))
            else:
                dependencies.update(_registry_dependencies(item))
    return dependencies


def _standalone_schema(catalog: Mapping[str, Any], schema_name: str) -> dict[str, Any]:
    registry = catalog["schemas"]
    schema = _rewrite_registry_refs(copy.deepcopy(registry[schema_name]))
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"contracts/schemas/{SCHEMA_FILE_NAMES[schema_name]}"

    pending = list(_registry_dependencies(registry[schema_name]))
    dependencies: dict[str, Any] = {}
    while pending:
        dependency_name = pending.pop(0)
        if dependency_name == schema_name or dependency_name in dependencies:
            continue
        if dependency_name not in registry:
            raise ContractValidationError(
                f"{schema_name} references unknown schema {dependency_name}."
            )
        dependency = copy.deepcopy(registry[dependency_name])
        pending.extend(sorted(_registry_dependencies(dependency)))
        dependency.pop("$schema", None)
        dependency.pop("$id", None)
        dependencies[dependency_name] = _rewrite_registry_refs(dependency)
    if dependencies:
        schema["$defs"] = dependencies
    return schema


def _full_rule(
    definition: Mapping[str, Any], default_rule: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "ruleId": definition["ruleId"],
        "ruleType": definition["ruleType"],
        "metric": definition["metric"],
        "operator": definition["operator"],
        "threshold": default_rule["threshold"],
        "severity": default_rule["severity"],
        "enabled": default_rule["enabled"],
        "evidenceRequirement": default_rule["evidenceRequirement"],
    }


def _full_preset(
    preset: Mapping[str, Any], definitions: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    return {
        "order": preset["order"],
        "presetId": preset["presetId"],
        "nameKo": preset["nameKo"],
        "nameEn": preset["nameEn"],
        "descriptionKo": preset["descriptionKo"],
        "descriptionEn": preset["descriptionEn"],
        "mode": preset["mode"],
        "defaultRules": [
            _full_rule(definition, default_rule)
            for definition, default_rule in zip(
                definitions, preset["defaultRules"], strict=True
            )
        ],
    }


def _current_fixture(rule: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "principleId": "prc_0123456789abcdef0123456789abcdef",
        "presetId": "balanced",
        "title": "단일 규칙 원칙",
        "mode": "GUIDE",
        "status": "ACTIVE",
        "version": 1,
        "rules": [copy.deepcopy(rule)],
        "createdAt": "2026-07-22T18:00:00+09:00",
        "updatedAt": "2026-07-22T18:00:00+09:00",
    }


def _error_envelope(
    code: str, message: str, details: Mapping[str, Any], request_id: str
) -> dict[str, Any]:
    return {
        "success": False,
        "requestId": request_id,
        "data": None,
        "warnings": [],
        "error": {"code": code, "message": message, "details": dict(details)},
    }


def _fixtures(catalog: Mapping[str, Any]) -> dict[str, Any]:
    definitions = catalog["ruleDefinitions"]
    presets = catalog["presets"]
    full_presets = [_full_preset(preset, definitions) for preset in presets]
    balanced_rules = full_presets[1]["defaultRules"]
    one_rule = copy.deepcopy(balanced_rules[0])
    current = _current_fixture(one_rule)
    list_item = {key: value for key, value in current.items() if key != "rules"}
    history_item = {
        "principleId": current["principleId"],
        "version": 2,
        "presetId": current["presetId"],
        "title": "수정된 원칙",
        "mode": "STRICT",
        "status": "ACTIVE",
        "rules": [copy.deepcopy(one_rule)],
        "changedFields": ["title", "mode", "rules"],
        "createdAt": "2026-07-22T18:05:00+09:00",
    }
    opaque_cursor = "ZXhhbXBsZS1vcGFxdWUtY3Vyc29y.c2lnbmF0dXJl"

    duplicate_rules = copy.deepcopy(current)
    duplicate_rules["rules"] = [
        copy.deepcopy(one_rule),
        {**copy.deepcopy(one_rule), "threshold": Decimal("0.19")},
    ]
    invalid_tuple = copy.deepcopy(current)
    invalid_tuple["rules"][0]["metric"] = "order_amount_krw"
    out_of_range = copy.deepcopy(current)
    out_of_range["rules"][0]["threshold"] = Decimal("1.0001")
    over_scale = copy.deepcopy(current)
    over_scale["rules"][0]["threshold"] = Decimal("0.12345")
    null_threshold = copy.deepcopy(current)
    null_threshold["rules"][0]["threshold"] = None
    string_threshold = copy.deepcopy(current)
    string_threshold["rules"][0]["threshold"] = "0.2"
    unknown_property = copy.deepcopy(current)
    unknown_property["ownerUserId"] = "usr_demo_admin"
    enabled_allow = copy.deepcopy(current)
    enabled_allow["rules"][0]["severity"] = "ALLOW"
    disabled_block = copy.deepcopy(current)
    disabled_block["rules"][0]["enabled"] = False
    too_many = copy.deepcopy(current)
    too_many["rules"] = [copy.deepcopy(one_rule) for _ in range(9)]
    missing_evidence = copy.deepcopy(current)
    missing_evidence["rules"][0].pop("evidenceRequirement")
    optional_hard_evidence = copy.deepcopy(current)
    optional_hard_evidence["rules"][0]["evidenceRequirement"] = "OPTIONAL"

    update_request = {
        "expectedVersion": 1,
        "title": "수정된 원칙",
        "mode": "STRICT",
        "status": "ACTIVE",
        "rules": [copy.deepcopy(one_rule)],
    }
    return {
        "contracts/examples/principle.valid.json": current,
        "contracts/examples/principle-presets.valid.json": {
            "disclaimer": copy.deepcopy(catalog["disclaimer"]),
            "items": full_presets,
        },
        "contracts/examples/principle-create.valid.json": {
            "presetId": "balanced",
            "title": "균형형 국내주식+금 ETF 원칙",
        },
        "contracts/examples/principle-create-custom-rules.valid.json": {
            "presetId": "balanced",
            "title": "단일 규칙 원칙",
            "mode": "GUIDE",
            "rules": [copy.deepcopy(one_rule)],
        },
        "contracts/examples/principle-update.valid.json": update_request,
        "contracts/examples/principle-update-no-op.valid.json": {
            "expectedVersion": 1,
            "title": current["title"],
            "mode": current["mode"],
            "status": current["status"],
            "rules": [copy.deepcopy(one_rule)],
        },
        "contracts/examples/principle-list.valid.json": {
            "items": [list_item],
            "nextCursor": None,
        },
        "contracts/examples/principle-list-next-page.valid.json": {
            "items": [list_item],
            "nextCursor": opaque_cursor,
        },
        "contracts/examples/principle-list-empty.valid.json": {
            "items": [],
            "nextCursor": None,
        },
        "contracts/examples/principle-history.valid.json": {
            "items": [history_item],
            "nextCursor": None,
        },
        "contracts/examples/principle-history-next-page.valid.json": {
            "items": [history_item],
            "nextCursor": opaque_cursor,
        },
        "contracts/examples/principle-history-empty.valid.json": {
            "items": [],
            "nextCursor": None,
        },
        "contracts/examples/principle-error-validation.valid.json": _error_envelope(
            "VALIDATION_ERROR",
            "Request validation failed.",
            {"violations": [{"field": "/ownerUserId", "reason": "UNKNOWN_FIELD"}]},
            "req_20260722_000003",
        ),
        "contracts/examples/principle-error-cursor.valid.json": _error_envelope(
            "VALIDATION_ERROR",
            "Request validation failed.",
            {"violations": [{"field": "/query/cursor", "reason": "INVALID_CURSOR"}]},
            "req_20260722_000004",
        ),
        "contracts/examples/principle-error-unauthorized.valid.json": _error_envelope(
            "UNAUTHORIZED",
            "Authentication is required.",
            {},
            "req_20260722_000005",
        ),
        "contracts/examples/principle-error-forbidden.valid.json": _error_envelope(
            "FORBIDDEN",
            "Access is denied.",
            {},
            "req_20260722_000006",
        ),
        "contracts/examples/principle-error-not-found.valid.json": _error_envelope(
            "NOT_FOUND",
            "Resource was not found.",
            {},
            "req_20260722_000007",
        ),
        "contracts/examples/principle-error-conflict.valid.json": _error_envelope(
            "CONFLICT",
            "Resource conflict.",
            {"expectedVersion": 1, "currentVersion": 2},
            "req_20260722_000008",
        ),
        "contracts/examples/principle-error-version-exhausted.valid.json": _error_envelope(
            "VERSION_EXHAUSTED",
            "Principle version limit was reached.",
            {"currentVersion": 2_147_483_647},
            "req_20260722_000009",
        ),
        "contracts/examples/principle-error-payload-too-large.valid.json": _error_envelope(
            "PAYLOAD_TOO_LARGE",
            "Request payload exceeded the configured safety limit.",
            {"maxBytes": 1_048_576},
            "req_20260722_000010",
        ),
        "contracts/examples/invalid/principle.invalid.json": {
            **copy.deepcopy(current),
            "rules": [{**copy.deepcopy(one_rule), "ruleId": "unknown_guard"}],
        },
        "contracts/examples/invalid/principle.duplicate-rule.invalid.json": duplicate_rules,
        "contracts/examples/invalid/principle.invalid-tuple.invalid.json": invalid_tuple,
        "contracts/examples/invalid/principle.threshold-range.invalid.json": out_of_range,
        "contracts/examples/invalid/principle.threshold-scale.invalid.json": over_scale,
        "contracts/examples/invalid/principle.threshold-null.invalid.json": null_threshold,
        "contracts/examples/invalid/principle.threshold-string.invalid.json": string_threshold,
        "contracts/examples/invalid/principle.unknown-property.invalid.json": unknown_property,
        "contracts/examples/invalid/principle.enabled-allow.invalid.json": enabled_allow,
        "contracts/examples/invalid/principle.disabled-block.invalid.json": disabled_block,
        "contracts/examples/invalid/principle.too-many-rules.invalid.json": too_many,
        "contracts/examples/invalid/principle.evidence-missing.invalid.json": missing_evidence,
        "contracts/examples/invalid/principle.evidence-optional-hard.invalid.json": (
            optional_hard_evidence
        ),
        "contracts/examples/invalid/principle-update.empty-rules.invalid.json": {
            **copy.deepcopy(update_request),
            "rules": [],
        },
        "contracts/examples/invalid/principle-update.invalid-status.invalid.json": {
            **copy.deepcopy(update_request),
            "status": "DRAFT",
        },
        "contracts/examples/invalid/principle-update.missing-field.invalid.json": {
            key: value for key, value in update_request.items() if key != "rules"
        },
        "contracts/examples/invalid/principle-create.missing-title.invalid.json": {
            "presetId": "balanced"
        },
    }


def generate_outputs(catalog: Mapping[str, Any]) -> dict[str, bytes]:
    validate_catalog_semantics(catalog)
    outputs: dict[str, bytes] = {
        "contracts/schemas/s2-1-principle-catalog.schema.json": canonical_json_bytes(
            _catalog_meta_schema()
        )
    }
    for schema_name, file_name in SCHEMA_FILE_NAMES.items():
        schema = _standalone_schema(catalog, schema_name)
        Draft202012Validator.check_schema(schema)
        outputs[f"contracts/schemas/{file_name}"] = canonical_json_bytes(schema)
    outputs.update(
        {
            path: canonical_json_bytes(value)
            for path, value in _fixtures(catalog).items()
        }
    )
    if frozenset(outputs) != OUTPUTS:
        missing = sorted(OUTPUTS - frozenset(outputs))
        unexpected = sorted(frozenset(outputs) - OUTPUTS)
        raise ContractValidationError(
            f"S2.1 OUTPUTS manifest mismatch: missing={missing}, unexpected={unexpected}"
        )
    return dict(sorted(outputs.items()))


def validate_principle_payload_semantics(
    schema_name: str, payload: Any, catalog: Mapping[str, Any]
) -> None:
    definitions = catalog["ruleDefinitions"]

    def validate_rules(rules: Any, location: str) -> None:
        if not isinstance(rules, list):
            raise ContractValidationError(f"{location}: rules must be an array.")
        if not EXPECTED_LIMITS["rulesMinItems"] <= len(rules) <= EXPECTED_LIMITS["rulesMaxItems"]:
            raise ContractValidationError(f"{location}: rules cardinality is invalid.")
        seen: set[str] = set()
        by_id = {definition["ruleId"]: definition for definition in definitions}
        canonical_orders = {definition["ruleId"]: definition["order"] for definition in definitions}
        previous_order = 0
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                raise ContractValidationError(f"{location}/{index}: rule must be an object.")
            rule_id = rule.get("ruleId")
            if not isinstance(rule_id, str) or rule_id not in by_id:
                raise ContractValidationError(f"{location}/{index}/ruleId: unknown rule.")
            if rule_id in seen:
                raise ContractValidationError(f"{location}/{index}/ruleId: duplicate rule.")
            seen.add(rule_id)
            order = canonical_orders[rule_id]
            if order <= previous_order:
                raise ContractValidationError(f"{location}: rules are not in canonical order.")
            previous_order = order
            definition = by_id[rule_id]
            for field in ("ruleType", "metric", "operator"):
                if rule.get(field) != definition[field]:
                    raise ContractValidationError(
                        f"{location}/{index}/{field}: fixed tuple mismatch."
                    )
            _validate_threshold(
                rule.get("threshold"), definition, location=f"{location}/{index}/threshold"
            )
            if rule.get("evidenceRequirement") not in definition["evidenceRequirements"]:
                raise ContractValidationError(
                    f"{location}/{index}/evidenceRequirement: invalid evidence requirement."
                )
            if rule.get("enabled") is True:
                if rule.get("severity") not in definition["enabledSeverities"]:
                    raise ContractValidationError(
                        f"{location}/{index}/severity: enabled severity mismatch."
                    )
            elif rule.get("enabled") is False:
                if rule.get("severity") != definition["disabledSeverity"]:
                    raise ContractValidationError(
                        f"{location}/{index}/severity: disabled severity mismatch."
                    )
            else:
                raise ContractValidationError(
                    f"{location}/{index}/enabled: boolean required."
                )

    def validate_title(title: Any, location: str) -> None:
        if not isinstance(title, str):
            return
        if title != unicodedata.normalize("NFC", title) or title != title.strip():
            raise ContractValidationError(f"{location}: title is not canonical.")
        if any(
            character in "\r\n\x00"
            or unicodedata.category(character) in {"Cc", "Cf"}
            for character in title
        ):
            raise ContractValidationError(f"{location}: title contains a forbidden character.")
        if not 1 <= len(title) <= 120:
            raise ContractValidationError(f"{location}: title code-point length is invalid.")

    if schema_name in {"principle", "principle-create-request", "principle-update-request"}:
        if isinstance(payload, dict) and "title" in payload:
            validate_title(payload["title"], "/title")
        if isinstance(payload, dict) and "rules" in payload:
            validate_rules(payload["rules"], "/rules")
    elif schema_name == "principle-preset-list":
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            for index, preset in enumerate(payload["items"]):
                if isinstance(preset, dict):
                    validate_rules(preset.get("defaultRules"), f"/items/{index}/defaultRules")
    elif schema_name == "principle-list-response":
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            for index, item in enumerate(payload["items"]):
                if isinstance(item, dict):
                    validate_title(item.get("title"), f"/items/{index}/title")
    elif schema_name == "principle-history-response":
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            for index, item in enumerate(payload["items"]):
                if isinstance(item, dict):
                    validate_title(item.get("title"), f"/items/{index}/title")
                    validate_rules(item.get("rules"), f"/items/{index}/rules")


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
        path = REPO_ROOT / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        print(f"WROTE {relative_path}")


def _format_catalog() -> None:
    raw = CATALOG_PATH.read_bytes()
    catalog = load_json_bytes_strict(raw, source=CATALOG_PATH.as_posix())
    validate_catalog_semantics(catalog)
    CATALOG_PATH.write_bytes(canonical_json_bytes(catalog))
    print(f"FORMATTED {CATALOG_PATH.relative_to(REPO_ROOT).as_posix()}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile and verify the canonical S2.1 Principle contract catalog."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    action.add_argument("--format-catalog", action="store_true")
    action.add_argument("--check-openapi-env", type=Path, metavar="PATH")
    arguments = parser.parse_args()

    if arguments.check_openapi_env is not None:
        parse_openapi_environment(arguments.check_openapi_env)
        print("OpenAPI fixture environment validation succeeded.")
        return 0
    if arguments.format_catalog:
        _format_catalog()
        return 0

    catalog = load_catalog()
    outputs = generate_outputs(catalog)
    if arguments.write:
        _write_outputs(outputs)
        print(
            "S2.1 Principle contract generation succeeded: "
            + hashlib.sha256(canonical_json_bytes(catalog)).hexdigest()
        )
        return 0

    failures = _check_outputs(outputs)
    if failures:
        print(f"S2.1 Principle contract generation failed: {failures} drift(s)", file=sys.stderr)
        return 1
    print(
        "S2.1 Principle contract generation check succeeded: "
        + hashlib.sha256(canonical_json_bytes(catalog)).hexdigest()
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
