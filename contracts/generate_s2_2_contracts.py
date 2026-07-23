from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Mapping, Sequence

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

_SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    canonical_json_bytes,
    load_catalog as load_s21_catalog,
    load_json_bytes_strict,
)


REPO_ROOT = _SCRIPT_REPO_ROOT
CATALOG_PATH = REPO_ROOT / "contracts/catalogs/s2-2-system-rule-catalog.v1.json"
EXPECTED_CATALOG_SHA256: Final[str] = (
    "57101a64421805911ddfc7d652c44e8cc2bc08d200ec2c06cc439fd82ce392a2"
)
OUTPUTS: Final[frozenset[str]] = frozenset(
    {
        "contracts/schemas/s2-2-system-rule-catalog.schema.json",
        "contracts/schemas/s2-2-hash-vector.schema.json",
        "contracts/schemas/risk_decision.schema.json",
        "contracts/examples/s2-2-hash-vector.valid.json",
        "contracts/examples/risk_decision.valid.json",
        "contracts/examples/risk_decision.allow.valid.json",
        "contracts/examples/risk_decision.warn.valid.json",
        "contracts/examples/risk_decision.hold.valid.json",
        "contracts/examples/risk_decision.block.valid.json",
        "contracts/examples/risk_decision.optional-disclosure-missing.valid.json",
        "contracts/examples/risk_decision.required-disclosure-missing.valid.json",
        "contracts/examples/risk_decision.precedence-block-hold.valid.json",
        "contracts/examples/risk_decision.precedence-block-warn.valid.json",
        "contracts/examples/risk_decision.precedence-hold-warn.valid.json",
        "contracts/examples/risk_decision.precedence-warn-na.valid.json",
        "contracts/examples/risk_decision.precedence-pass-na.valid.json",
        "contracts/examples/invalid/risk_decision.invalid.json",
        "contracts/examples/invalid/risk_decision.abstention-without-warning.invalid.json",
        "contracts/examples/invalid/risk_decision.null-violation.invalid.json",
        "contracts/examples/invalid/risk_decision.null-risk-item.invalid.json",
        "contracts/examples/invalid/risk_decision.non-threshold-violation.invalid.json",
        "contracts/examples/invalid/risk_decision.hold-without-issue.invalid.json",
        "contracts/examples/invalid/risk_decision.block-without-block-violation.invalid.json",
        "contracts/examples/invalid/risk_decision.optional-as-issue.invalid.json",
        "contracts/examples/invalid/risk_decision.too-many-warnings.invalid.json",
        "contracts/examples/invalid/risk_decision.unsorted-violations.invalid.json",
        "contracts/examples/invalid/risk_decision.warning-without-abstention.invalid.json",
    }
)

EXPECTED_BOUNDS: Final[dict[str, Any]] = {
    "abstentionMaxItems": 50,
    "codeMaxChars": 128,
    "concurrencyMax": 8,
    "disclosureEventMaxItems": 100,
    "idMaxChars": 128,
    "issueMaxItems": 14,
    "messageMaxChars": 1_024,
    "perPortLogicalCallMax": 1,
    "positionMaxItems": 1_000,
    "requestMaxBytes": 256 * 1_024,
    "responseMaxBytes": 1_024 * 1_024,
    "sourceDeadlineMillis": 500,
    "sourceRefMaxItems": 100,
    "sourceRefPattern": "^[0-9a-f]{64}$",
    "totalDeadlineMillis": 900,
    "violationMaxItems": 14,
    "warningMaxItems": 50,
}
EXPECTED_SYSTEM_RULE_IDS: Final[list[str]] = [
    "high_volatility_guard",
    "data_freshness_guard",
    "hmm_risk_off_guard",
    "mean_reversion_warning",
    "etf_etn_risk_check",
    "ad_leading_room_guard",
]
EXPECTED_EXECUTION_COUNTS: Final[dict[str, int]] = {
    "THRESHOLD": 12,
    "READINESS": 1,
    "NOT_APPLICABLE": 1,
}
EXPECTED_ACTION_PRECEDENCE: Final[list[str]] = ["BLOCK", "HOLD", "WARN", "ALLOW"]
ISSUE_CODES: Final[list[str]] = [
    "BALANCE_PARTIAL",
    "BALANCE_STALE",
    "BROKERAGE_UNAVAILABLE",
    "DISCLOSURE_PARTIAL",
    "DISCLOSURE_PROVIDER_ERROR",
    "INSTRUMENT_METADATA_UNAVAILABLE",
    "MARGIN_CONTEXT_UNAVAILABLE",
    "NEWS_EVIDENCE_UNAVAILABLE",
    "PORTFOLIO_CONTEXT_UNAVAILABLE",
    "PRICE_MISSING",
    "PRICE_STALE",
    "PRINCIPLE_CONTEXT_UNAVAILABLE",
    "RISK_SNAPSHOT_MISSING",
    "RISK_SNAPSHOT_VERSION_MISMATCH",
    "SOURCE_DEADLINE_EXCEEDED",
]
WARNING_CODES: Final[list[str]] = [
    "MODEL_ABSTAINED",
    "OPTIONAL_EVIDENCE_ERROR",
    "OPTIONAL_EVIDENCE_INCOMPLETE",
    "OPTIONAL_EVIDENCE_MISSING",
    "OPTIONAL_EVIDENCE_STALE",
]
ABSTENTION_CODES: Final[list[str]] = [
    *WARNING_CODES,
    "NOT_APPLICABLE_V1",
]


def _catalog_meta_schema() -> dict[str, Any]:
    nullable_number = {"type": ["number", "integer", "null"]}
    rule = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "applicability",
            "defaultSeverity",
            "defaultThreshold",
            "evidenceCriticality",
            "executionKind",
            "freshnessPolicy",
            "maximum",
            "metric",
            "minimum",
            "operator",
            "order",
            "ownership",
            "ruleId",
            "scale",
            "severitySource",
            "thresholdSource",
            "unit",
        ],
        "properties": {
            "applicability": {"type": "string", "minLength": 1, "maxLength": 128},
            "defaultSeverity": {"type": ["string", "null"], "enum": [None, "WARN", "BLOCK"]},
            "defaultThreshold": nullable_number,
            "evidenceCriticality": {
                "enum": ["HARD", "CONFIGURABLE", "MIXED", "OPTIONAL"]
            },
            "executionKind": {"enum": list(EXPECTED_EXECUTION_COUNTS)},
            "freshnessPolicy": {"type": "string", "minLength": 1, "maxLength": 128},
            "maximum": nullable_number,
            "metric": {"type": "string", "minLength": 1, "maxLength": 128},
            "minimum": nullable_number,
            "operator": {"type": ["string", "null"], "enum": [None, "<=", ">="]},
            "order": {"type": "integer", "minimum": 1, "maximum": 14},
            "ownership": {"enum": ["PUBLIC_PRINCIPLE", "SYSTEM_MANAGED"]},
            "ruleId": {"type": "string", "minLength": 1, "maxLength": 128},
            "scale": {"type": ["integer", "null"], "minimum": 0, "maximum": 4},
            "severitySource": {"type": "string", "minLength": 1, "maxLength": 128},
            "thresholdSource": {"type": "string", "minLength": 1, "maxLength": 128},
            "unit": {"type": "string", "minLength": 1, "maxLength": 128},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/s2-2-system-rule-catalog.schema.json",
        "title": "S2.2 system-managed rule catalog v1",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "$schema",
            "bounds",
            "canonicalization",
            "catalogId",
            "catalogVersion",
            "portfolioPolicy",
            "readinessPolicyVersion",
            "resultPolicy",
            "rules",
            "systemManagedRuleIds",
        ],
        "properties": {
            "$schema": {"const": "https://json-schema.org/draft/2020-12/schema"},
            "bounds": {
                "type": "object",
                "additionalProperties": False,
                "required": sorted(EXPECTED_BOUNDS),
                "properties": {
                    key: {"const": value} for key, value in EXPECTED_BOUNDS.items()
                },
            },
            "canonicalization": {"type": "object"},
            "catalogId": {"const": "s2-2-system-rule-catalog"},
            "catalogVersion": {"const": 1},
            "portfolioPolicy": {"type": "object"},
            "readinessPolicyVersion": {"const": "s2-2-readiness-v1"},
            "resultPolicy": {"type": "object"},
            "rules": {
                "type": "array",
                "minItems": 14,
                "maxItems": 14,
                "items": rule,
            },
            "systemManagedRuleIds": {
                "type": "array",
                "minItems": 6,
                "maxItems": 6,
                "items": {"type": "string"},
                "uniqueItems": True,
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


def _normalized_decimal(value: Any, *, location: str) -> Decimal:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        raise ContractValidationError(f"{location}: finite JSON number required.")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Decimal) and value.is_finite():
        return value
    if isinstance(value, float) and math.isfinite(value):
        return Decimal(str(value))
    raise ContractValidationError(f"{location}: finite JSON number required.")


def _validate_public_alignment(rules: Sequence[Mapping[str, Any]]) -> None:
    s21 = load_s21_catalog()
    definitions = s21["ruleDefinitions"]
    if [rule["ruleId"] for rule in rules[:8]] != [
        definition["ruleId"] for definition in definitions
    ]:
        raise ContractValidationError("S2.2 public rules do not match S2.1 canonical order.")
    for index, (rule, definition) in enumerate(zip(rules[:8], definitions, strict=True)):
        threshold = definition["thresholdSchema"]
        expected = {
            "metric": definition["metric"],
            "operator": definition["operator"],
            "scale": threshold["maxNormalizedScale"],
            "minimum": threshold["minimum"],
            "maximum": threshold["maximum"],
        }
        for field, value in expected.items():
            if rule[field] != value:
                raise ContractValidationError(
                    f"/rules/{index}/{field}: public S2.1 tuple drift."
                )
        if (
            rule["ownership"] != "PUBLIC_PRINCIPLE"
            or rule["executionKind"] != "THRESHOLD"
            or rule["thresholdSource"] != "PRINCIPLE_VERSION"
            or rule["severitySource"] != "PRINCIPLE_VERSION"
        ):
            raise ContractValidationError(
                f"/rules/{index}: public Principle ownership/source drift."
            )


def validate_catalog_semantics(catalog: Any) -> None:
    if not isinstance(catalog, dict):
        raise ContractValidationError("S2.2 catalog root must be an object.")
    meta_error = _first_validation_error(Draft202012Validator(_catalog_meta_schema()), catalog)
    if meta_error is not None:
        raise ContractValidationError(f"S2.2 catalog meta-schema violation: {meta_error}")
    if catalog["bounds"] != EXPECTED_BOUNDS:
        raise ContractValidationError("BOUNDS-CONTRACT-S22-V1 drift.")

    canonicalization = catalog["canonicalization"]
    expected_canonicalization = {
        "arrayOrder": "EXPLICIT_STABLE_SORT_KEYS",
        "charset": "UTF-8",
        "decimalFormat": "PLAIN_NO_EXPONENT",
        "id": "HASH-CANONICALIZATION-S22-V1",
        "jsonWhitespace": "NONE",
        "negativeZero": "ZERO",
        "objectKeyOrder": "LEXICOGRAPHIC",
        "scaleStorage": "EXPLICIT_CATALOG_FIELD",
        "semanticInputExcludedFields": [
            "requestId",
            "evaluationId",
            "retrievedAt",
            "traceId",
            "inputArrayOrder",
        ],
        "semanticInputIncludedFields": [
            "snapshotSchemaVersion",
            "actorUserId",
            "evaluationAsOf",
            "principle",
            "systemRuleCatalogVersion",
            "readinessPolicyVersion",
            "portfolio",
            "orderIntent",
            "metrics",
            "requestedOptionalComponents",
            "observedOptionalComponentEvidence",
            "disclosureEvidence",
            "provenanceRefs",
        ],
        "snapshotArtifactIncludedFields": ["*"],
        "trailingZeros": "REMOVE",
    }
    if canonicalization != expected_canonicalization:
        raise ContractValidationError("HASH-CANONICALIZATION-S22-V1 drift.")

    portfolio = catalog["portfolioPolicy"]
    if portfolio != {
        "contextUnavailableDecision": "HOLD",
        "invalidSelectorHttpStatus": 400,
        "invalidSelectorPublicCode": "VALIDATION_ERROR",
        "modes": ["KIS_MOCK", "INTERNAL_PAPER"],
        "noAutomaticFallback": True,
        "selectionAuthority": "SERVER_OWNER_SCOPED",
        "storageMapping": {"INTERNAL_PAPER": "PAPER", "KIS_MOCK": "KIS_MOCK"},
    }:
        raise ContractValidationError("Portfolio selection/no-fallback policy drift.")

    result = catalog["resultPolicy"]
    if (
        result.get("actions") != ["ALLOW", "WARN", "HOLD", "BLOCK"]
        or result.get("precedence") != EXPECTED_ACTION_PRECEDENCE
        or result.get("thresholdViolationContainer") != "violations"
        or result.get("requiredSourceFailureContainer") != "issues"
        or result.get("unavailableOptionalEvidenceContainers")
        != ["warnings", "abstentions"]
    ):
        raise ContractValidationError("S2.2 result wire policy drift.")

    rules = catalog["rules"]
    if [rule["order"] for rule in rules] != list(range(1, 15)):
        raise ContractValidationError("S2.2 rule order must be exactly 1..14.")
    if len({rule["ruleId"] for rule in rules}) != 14:
        raise ContractValidationError("S2.2 rule IDs must be unique.")
    if [rule["ruleId"] for rule in rules[8:]] != EXPECTED_SYSTEM_RULE_IDS:
        raise ContractValidationError("S2.2 system-managed six drift.")
    if catalog["systemManagedRuleIds"] != EXPECTED_SYSTEM_RULE_IDS:
        raise ContractValidationError("S2.2 system rule manifest drift.")
    if sum(rule["ownership"] == "PUBLIC_PRINCIPLE" for rule in rules) != 8:
        raise ContractValidationError("S2.2 must keep exactly eight public Principle rules.")
    if sum(rule["ownership"] == "SYSTEM_MANAGED" for rule in rules) != 6:
        raise ContractValidationError("S2.2 must keep exactly six system rules.")
    for kind, count in EXPECTED_EXECUTION_COUNTS.items():
        if sum(rule["executionKind"] == kind for rule in rules) != count:
            raise ContractValidationError(f"S2.2 execution disposition drift for {kind}.")
    _validate_public_alignment(rules)

    for index, rule in enumerate(rules):
        if rule["executionKind"] == "THRESHOLD":
            if any(
                rule[field] is None
                for field in ("defaultSeverity", "defaultThreshold")
            ) and rule["ownership"] == "SYSTEM_MANAGED":
                raise ContractValidationError(
                    f"/rules/{index}: system threshold default is missing."
                )
            for field in ("minimum", "maximum"):
                _normalized_decimal(rule[field], location=f"/rules/{index}/{field}")
            if rule["operator"] not in {"<=", ">="} or rule["scale"] is None:
                raise ContractValidationError(
                    f"/rules/{index}: threshold operator/scale is invalid."
                )
        elif rule["ruleId"] == "data_freshness_guard":
            if rule["executionKind"] != "READINESS":
                raise ContractValidationError("data_freshness_guard must be READINESS.")
        elif rule["ruleId"] == "ad_leading_room_guard":
            if (
                rule["executionKind"] != "NOT_APPLICABLE"
                or rule["applicability"] != "NOT_APPLICABLE_V1"
            ):
                raise ContractValidationError(
                    "ad_leading_room_guard must be deterministic N/A in v1."
                )
    if hashlib.sha256(canonical_json_bytes(catalog)).hexdigest() != (
        EXPECTED_CATALOG_SHA256
    ):
        raise ContractValidationError("S2.2 v1 canonical catalog content drift.")


def load_catalog(
    path: Path = CATALOG_PATH, *, require_canonical: bool = True
) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ContractValidationError(f"S2.2 catalog could not be read: {path}") from error
    catalog = load_json_bytes_strict(raw, source=path.as_posix())
    validate_catalog_semantics(catalog)
    if require_canonical and raw != canonical_json_bytes(catalog):
        raise ContractValidationError(
            "S2.2 catalog bytes are not canonical UTF-8/LF/sorted JSON."
        )
    return catalog


def _bounded_string(max_length: int, *, minimum: int = 1) -> dict[str, Any]:
    return {"type": "string", "minLength": minimum, "maxLength": max_length}


def _risk_decision_schema(catalog: Mapping[str, Any]) -> dict[str, Any]:
    bounds = catalog["bounds"]
    id_schema = _bounded_string(bounds["idMaxChars"])
    code_schema = _bounded_string(bounds["codeMaxChars"])
    message_schema = _bounded_string(bounds["messageMaxChars"])
    hash_schema = {
        "type": "string",
        "pattern": "^[0-9a-f]{64}$",
        "minLength": 64,
        "maxLength": 64,
    }
    source_ref_array = {
        "type": "array",
        "maxItems": bounds["sourceRefMaxItems"],
        "uniqueItems": True,
        "items": {
            "type": "string",
            "pattern": bounds["sourceRefPattern"],
            "minLength": 64,
            "maxLength": 64,
        },
    }
    optional_rule_id = {
        "type": "string",
        "enum": [rule["ruleId"] for rule in catalog["rules"]],
        "maxLength": bounds["idMaxChars"],
    }
    violation_rule_id = {
        "type": "string",
        "enum": [
            rule["ruleId"]
            for rule in catalog["rules"]
            if rule["executionKind"] == "THRESHOLD"
        ],
        "maxLength": bounds["idMaxChars"],
    }
    system_threshold_severity_rules = [
        {
            "if": {
                "properties": {"ruleId": {"const": rule["ruleId"]}},
                "required": ["ruleId"],
            },
            "then": {
                "properties": {
                    "severity": {"const": rule["defaultSeverity"]},
                },
            },
        }
        for rule in catalog["rules"]
        if rule["ownership"] == "SYSTEM_MANAGED"
        and rule["executionKind"] == "THRESHOLD"
    ]
    violation = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "ruleId",
            "severity",
            "message",
            "metricValue",
            "threshold",
        ],
        "properties": {
            "ruleId": violation_rule_id,
            "severity": {"enum": ["WARN", "BLOCK"]},
            "message": message_schema,
            "metricValue": {"type": "number"},
            "threshold": {"type": "number"},
        },
        "allOf": system_threshold_severity_rules,
    }
    issue = {
        "type": "object",
        "additionalProperties": False,
        "required": ["code", "message", "source"],
        "properties": {
            "code": {**code_schema, "enum": ISSUE_CODES},
            "message": message_schema,
            "source": {**code_schema},
            "ruleId": optional_rule_id,
        },
    }
    warning = {
        "type": "object",
        "additionalProperties": False,
        "required": ["code", "message", "source"],
        "properties": {
            "code": {**code_schema, "enum": WARNING_CODES},
            "message": message_schema,
            "source": {**code_schema},
            "ruleId": optional_rule_id,
        },
    }
    abstention = {
        "type": "object",
        "additionalProperties": False,
        "required": ["code", "component", "disposition", "message"],
        "properties": {
            "code": {**code_schema, "enum": ABSTENTION_CODES},
            "component": code_schema,
            "disposition": {"enum": ["ABSTAIN", "NOT_APPLICABLE"]},
            "message": message_schema,
            "ruleId": optional_rule_id,
        },
        "allOf": [
            {
                "if": {
                    "properties": {"disposition": {"const": "NOT_APPLICABLE"}},
                    "required": ["disposition"],
                },
                "then": {"properties": {"code": {"const": "NOT_APPLICABLE_V1"}}},
                "else": {"properties": {"code": {"enum": WARNING_CODES}}},
            }
        ],
    }
    risk_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["metric", "value", "severity", "source"],
        "properties": {
            "metric": code_schema,
            "value": {"type": "number"},
            "severity": {"enum": ["ALLOW", "WARN", "BLOCK"]},
            "source": {"enum": ["OPENDART", "KIS", "NEWS", "MACRO", "INTERNAL"]},
            "eventCodes": {
                "type": "array",
                "maxItems": bounds["disclosureEventMaxItems"],
                "uniqueItems": True,
                "items": code_schema,
            },
            "mappingVersion": id_schema,
            "sourceRefs": source_ref_array,
        },
    }
    not_applicable_only = {
        "type": "array",
        "maxItems": bounds["abstentionMaxItems"],
        "items": {
            "allOf": [
                {"$ref": "#/$defs/abstention"},
                {"properties": {"disposition": {"const": "NOT_APPLICABLE"}}},
            ]
        },
    }
    block_contains = {
        "contains": {
            "type": "object",
            "properties": {"severity": {"const": "BLOCK"}},
            "required": ["severity"],
        },
        "minContains": 1,
    }
    abstain_contains = {
        "contains": {
            "type": "object",
            "properties": {"disposition": {"const": "ABSTAIN"}},
            "required": ["disposition"],
        },
        "minContains": 1,
    }
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/risk_decision.schema.json",
        "title": "S2.2 offline risk evaluation result",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schemaVersion",
            "evaluationId",
            "catalogVersion",
            "readinessPolicyVersion",
            "decision",
            "mode",
            "canSubmitOrder",
            "principleVersionId",
            "principleVersion",
            "portfolioSource",
            "semanticInputHash",
            "snapshotArtifactHash",
            "violations",
            "issues",
            "warnings",
            "abstentions",
            "riskItems",
        ],
        "properties": {
            "schemaVersion": {"const": "s2-2-risk-decision/v1"},
            "evaluationId": id_schema,
            "decisionId": id_schema,
            "catalogVersion": {"const": 1},
            "readinessPolicyVersion": {"const": "s2-2-readiness-v1"},
            "decision": {"enum": ["ALLOW", "WARN", "HOLD", "BLOCK"]},
            "mode": {"enum": ["GUIDE", "STRICT"]},
            "canSubmitOrder": {"type": "boolean"},
            "validUntil": {"type": "string", "format": "date-time"},
            "principleVersionId": id_schema,
            "principleVersion": {
                "type": "integer",
                "minimum": 1,
                "maximum": 2_147_483_647,
            },
            "portfolioSource": {"enum": catalog["portfolioPolicy"]["modes"]},
            "semanticInputHash": hash_schema,
            "snapshotArtifactHash": hash_schema,
            "violations": {
                "type": "array",
                "maxItems": bounds["violationMaxItems"],
                "items": {"$ref": "#/$defs/violation"},
            },
            "issues": {
                "type": "array",
                "maxItems": bounds["issueMaxItems"],
                "items": {"$ref": "#/$defs/issue"},
            },
            "warnings": {
                "type": "array",
                "maxItems": bounds["warningMaxItems"],
                "items": {"$ref": "#/$defs/warning"},
            },
            "abstentions": {
                "type": "array",
                "maxItems": bounds["abstentionMaxItems"],
                "items": {"$ref": "#/$defs/abstention"},
            },
            "riskItems": {
                "type": "array",
                "maxItems": bounds["warningMaxItems"],
                "items": {"$ref": "#/$defs/riskItem"},
            },
            "riskSummary": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "mdd": {"type": "number"},
                    "var95": {"type": "number"},
                    "cvar95": {"type": "number"},
                    "hmmRegime": {
                        "enum": [
                            "NORMAL",
                            "SIDEWAYS",
                            "HIGH_VOLATILITY",
                            "RISK_OFF",
                            "RISK_ON",
                        ]
                    },
                    "regimeProbability": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                },
            },
            "signalSummary": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "finalSignal": {
                        "enum": [
                            "BUY_STRONG",
                            "BUY_WEAK",
                            "BUY",
                            "HOLD",
                            "SELL",
                            "SELL_WEAK",
                            "SELL_STRONG",
                        ]
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "lstmSignal": {"enum": ["BUY", "SELL", "HOLD"]},
                    "lightgbmSignal": {"enum": ["BUY", "SELL", "HOLD"]},
                    "newsSentiment": {
                        "type": "number",
                        "minimum": -1,
                        "maximum": 1,
                    },
                },
            },
            "explanation": {
                "type": "object",
                "additionalProperties": False,
                "required": ["shortText", "citationIds"],
                "properties": {
                    "shortText": message_schema,
                    "citationIds": {
                        "type": "array",
                        "maxItems": bounds["sourceRefMaxItems"],
                        "uniqueItems": True,
                        "items": id_schema,
                    },
                },
            },
        },
        "$defs": {
            "abstention": abstention,
            "issue": issue,
            "riskItem": risk_item,
            "violation": violation,
            "warning": warning,
        },
        "allOf": [
            {
                "if": {
                    "properties": {"decision": {"const": "ALLOW"}},
                    "required": ["decision"],
                },
                "then": {
                    "properties": {
                        "canSubmitOrder": {"const": True},
                        "violations": {"maxItems": 0},
                        "issues": {"maxItems": 0},
                        "warnings": {"maxItems": 0},
                        "abstentions": not_applicable_only,
                    }
                },
            },
            {
                "if": {
                    "properties": {"decision": {"const": "WARN"}},
                    "required": ["decision"],
                },
                "then": {
                    "properties": {
                        "canSubmitOrder": {"const": True},
                        "issues": {"maxItems": 0},
                    },
                    "not": {
                        "properties": {"violations": block_contains},
                        "required": ["violations"],
                    },
                    "anyOf": [
                        {"properties": {"violations": {"minItems": 1}}},
                        {"properties": {"warnings": {"minItems": 1}}},
                        {"properties": {"abstentions": abstain_contains}},
                    ],
                },
            },
            {
                "if": {
                    "properties": {"decision": {"const": "HOLD"}},
                    "required": ["decision"],
                },
                "then": {
                    "properties": {
                        "canSubmitOrder": {"const": False},
                        "issues": {"minItems": 1},
                    },
                    "not": {"properties": {"violations": block_contains}},
                },
            },
            {
                "if": {
                    "properties": {"decision": {"const": "BLOCK"}},
                    "required": ["decision"],
                },
                "then": {
                    "properties": {
                        "canSubmitOrder": {"const": False},
                        "violations": block_contains,
                    }
                },
            },
        ],
    }
    Draft202012Validator.check_schema(schema)
    return schema


def _hash_vector_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "contracts/schemas/s2-2-hash-vector.schema.json",
        "title": "S2.2 canonical hash parity vector",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "canonicalizationId",
            "semanticInput",
            "semanticInputCanonicalJson",
            "semanticInputHash",
            "snapshotArtifact",
            "snapshotArtifactCanonicalJson",
            "snapshotArtifactHash",
        ],
        "properties": {
            "canonicalizationId": {"const": "HASH-CANONICALIZATION-S22-V1"},
            "semanticInput": {"type": "object"},
            "semanticInputCanonicalJson": {"type": "string", "minLength": 2},
            "semanticInputHash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "snapshotArtifact": {"type": "object"},
            "snapshotArtifactCanonicalJson": {"type": "string", "minLength": 2},
            "snapshotArtifactHash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        },
    }


def hash_canonical_bytes(value: Any) -> bytes:
    def validate(node: Any, location: str) -> None:
        if node is None or isinstance(node, (str, bool)):
            return
        if isinstance(node, int) and not isinstance(node, bool):
            return
        if isinstance(node, list):
            for index, item in enumerate(node):
                validate(item, f"{location}/{index}")
            return
        if isinstance(node, dict) and all(isinstance(key, str) for key in node):
            for key, item in node.items():
                validate(item, f"{location}/{key}")
            return
        raise ContractValidationError(
            f"{location}: hash input must use integer or canonical decimal strings."
        )

    validate(value, "")
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _hash_vector() -> dict[str, Any]:
    source_ref = "1" * 64
    observed_at = "2026-07-23T00:00:00Z"
    retrieved_at = "2026-07-23T00:00:01Z"
    metric_names = [
        "annualized_volatility",
        "asset_weight",
        "current_price_krw",
        "daily_loss_rate",
        "daily_order_count",
        "disclosure_risk_score",
        "etf_etn_product_risk_score",
        "gold_etf_etn_weight",
        "hmm_risk_off_probability",
        "margin_requirement_krw",
        "mdd",
        "mean_reversion_abs_z_score",
        "negative_news_score",
        "order_amount_krw",
        "owner_position_quantity",
        "portfolio_equity_krw",
    ]

    def metric_artifact(metric: str) -> dict[str, Any]:
        if metric == "asset_weight":
            return {
                "availability": "AVAILABLE",
                "declaredScale": 4,
                "freshUntil": "2026-07-23T00:05:00Z",
                "metric": metric,
                "observedAt": observed_at,
                "retrievedAt": retrieved_at,
                "source": "INTERNAL_PAPER",
                "sourceRef": source_ref,
                "sourceVersion": "paper-v1",
                "unit": "RATIO",
                "value": "0.2",
            }
        if metric == "order_amount_krw":
            return {
                "availability": "AVAILABLE",
                "declaredScale": 0,
                "freshUntil": "2026-07-23T00:05:00Z",
                "metric": metric,
                "observedAt": observed_at,
                "retrievedAt": retrieved_at,
                "source": "INTERNAL_PAPER",
                "sourceRef": source_ref,
                "sourceVersion": "paper-v1",
                "unit": "KRW",
                "value": "500000",
            }
        return {
            "availability": "MISSING",
            "metric": metric,
            "reason": "SOURCE_MISSING",
        }

    snapshot_artifact = {
        "actorUserId": "usr_hash_fixture",
        "disclosureEvidence": {
            "completeness": "COMPLETE",
            "mappingVersion": "s1.2-v1",
            "sourceRefs": [source_ref],
        },
        "evaluationAsOf": observed_at,
        "evaluationId": "evl_0123456789abcdef",
        "metrics": [metric_artifact(metric) for metric in metric_names],
        "observedOptionalComponentEvidence": [
            {
                "available": True,
                "completeness": "COMPLETE",
                "componentId": "DISCLOSURE",
                "evidenceVersion": "s1.2-v1",
                "reasonCode": None,
                "sourceRefs": [source_ref],
            }
        ],
        "orderIntent": {
            "limitPrice": "50000",
            "orderType": "LIMIT",
            "quantity": "10",
            "side": "BUY",
            "symbol": "005930",
        },
        "portfolio": {
            "ownerScopeHash": "3" * 64,
            "positionCount": 1,
            "revision": "portfolio-revision-7",
            "source": "INTERNAL_PAPER",
        },
        "principle": {
            "mode": "GUIDE",
            "principleId": "prc_0123456789abcdef0123456789abcdef",
            "principleVersion": 3,
            "principleVersionId": "pvr_0123456789abcdef0123456789abcdef",
            "rulesHash": "2" * 64,
        },
        "provenanceRefs": [source_ref],
        "readinessPolicyVersion": "s2-2-readiness-v1",
        "requestedOptionalComponents": ["DISCLOSURE"],
        "retrievedAt": retrieved_at,
        "snapshotSchemaVersion": "s2.2-metric-snapshot-v1",
        "systemRuleCatalogVersion": 1,
    }
    semantic_input = copy.deepcopy(snapshot_artifact)
    semantic_input.pop("evaluationId")
    semantic_input.pop("retrievedAt")
    for metric in semantic_input["metrics"]:
        metric.pop("retrievedAt", None)

    semantic_bytes = hash_canonical_bytes(semantic_input)
    artifact_bytes = hash_canonical_bytes(snapshot_artifact)
    return {
        "canonicalizationId": "HASH-CANONICALIZATION-S22-V1",
        "semanticInput": semantic_input,
        "semanticInputCanonicalJson": semantic_bytes.decode("utf-8"),
        "semanticInputHash": hashlib.sha256(semantic_bytes).hexdigest(),
        "snapshotArtifact": snapshot_artifact,
        "snapshotArtifactCanonicalJson": artifact_bytes.decode("utf-8"),
        "snapshotArtifactHash": hashlib.sha256(artifact_bytes).hexdigest(),
    }


def _base_result(decision: str, can_submit: bool) -> dict[str, Any]:
    return {
        "schemaVersion": "s2-2-risk-decision/v1",
        "evaluationId": "evl_0123456789abcdef",
        "catalogVersion": 1,
        "readinessPolicyVersion": "s2-2-readiness-v1",
        "decision": decision,
        "mode": "GUIDE",
        "canSubmitOrder": can_submit,
        "principleVersionId": "pvr_0123456789abcdef0123456789abcdef",
        "principleVersion": 3,
        "portfolioSource": "INTERNAL_PAPER",
        "semanticInputHash": "a" * 64,
        "snapshotArtifactHash": "b" * 64,
        "violations": [],
        "issues": [],
        "warnings": [],
        "abstentions": [],
        "riskItems": [],
    }


def _violation(rule_id: str, severity: str, value: Any, threshold: Any) -> dict[str, Any]:
    return {
        "ruleId": rule_id,
        "severity": severity,
        "message": "The ready metric exceeded the configured threshold.",
        "metricValue": value,
        "threshold": threshold,
    }


def _issue(code: str, source: str, rule_id: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "code": code,
        "message": "A required evaluation input is unavailable.",
        "source": source,
    }
    if rule_id is not None:
        value["ruleId"] = rule_id
    return value


def _warning(code: str, source: str, rule_id: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "code": code,
        "message": "Optional evidence was not used for this evaluation.",
        "source": source,
    }
    if rule_id is not None:
        value["ruleId"] = rule_id
    return value


def _abstention(
    code: str,
    component: str,
    disposition: str,
    rule_id: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "code": code,
        "component": component,
        "disposition": disposition,
        "message": "The component did not produce threshold evidence.",
    }
    if rule_id is not None:
        value["ruleId"] = rule_id
    return value


def _result_fixtures() -> dict[str, dict[str, Any]]:
    allow = _base_result("ALLOW", True)
    warn = _base_result("WARN", True)
    warn["violations"] = [
        _violation("mean_reversion_warning", "WARN", Decimal("2.0001"), 2)
    ]
    warn["riskItems"] = [
        {
            "metric": "disclosure_risk_score",
            "value": Decimal("0.6"),
            "severity": "WARN",
            "source": "OPENDART",
            "eventCodes": ["OPENDART:piicDecsn"],
            "mappingVersion": "s1.2-v1",
            "sourceRefs": ["c" * 64],
        }
    ]
    hold = _base_result("HOLD", False)
    hold["issues"] = [_issue("PORTFOLIO_CONTEXT_UNAVAILABLE", "PORTFOLIO")]
    block = _base_result("BLOCK", False)
    block["violations"] = [
        _violation("max_single_order_amount", "BLOCK", 500001, 500000)
    ]
    optional_disclosure_missing = _base_result("WARN", True)
    optional_disclosure_missing["warnings"] = [
        _warning(
            "OPTIONAL_EVIDENCE_MISSING",
            "DISCLOSURE",
            "disclosure_risk_guard",
        )
    ]
    optional_disclosure_missing["abstentions"] = [
        _abstention(
            "OPTIONAL_EVIDENCE_MISSING",
            "DISCLOSURE",
            "ABSTAIN",
            "disclosure_risk_guard",
        )
    ]
    required_disclosure_missing = _base_result("HOLD", False)
    required_disclosure_missing["issues"] = [
        _issue(
            "DISCLOSURE_PROVIDER_ERROR",
            "DISCLOSURE",
            "disclosure_risk_guard",
        )
    ]

    block_hold = copy.deepcopy(block)
    block_hold["issues"] = [_issue("PRICE_STALE", "PRICE", "max_single_order_amount")]
    block_warn = copy.deepcopy(block)
    block_warn["warnings"] = [_warning("MODEL_ABSTAINED", "LIGHTGBM")]
    block_warn["abstentions"] = [
        _abstention("MODEL_ABSTAINED", "LIGHTGBM", "ABSTAIN")
    ]
    hold_warn = copy.deepcopy(hold)
    hold_warn["warnings"] = [
        _warning("MODEL_ABSTAINED", "HMM", "hmm_risk_off_guard")
    ]
    hold_warn["abstentions"] = [
        _abstention("MODEL_ABSTAINED", "HMM", "ABSTAIN", "hmm_risk_off_guard")
    ]
    warn_na = copy.deepcopy(warn)
    warn_na["abstentions"] = [
        _abstention(
            "NOT_APPLICABLE_V1",
            "AD_LEADING_ROOM",
            "NOT_APPLICABLE",
            "ad_leading_room_guard",
        )
    ]
    pass_na = copy.deepcopy(allow)
    pass_na["abstentions"] = copy.deepcopy(warn_na["abstentions"])

    invalid_decision = copy.deepcopy(allow)
    invalid_decision["decision"] = "APPROVE"
    abstention_without_warning = copy.deepcopy(optional_disclosure_missing)
    abstention_without_warning["warnings"] = []
    null_violation = copy.deepcopy(warn)
    null_violation["violations"][0]["metricValue"] = None
    null_risk_item = copy.deepcopy(warn)
    null_risk_item["riskItems"][0]["value"] = None
    non_threshold_violation = copy.deepcopy(warn)
    non_threshold_violation["violations"] = [
        _violation("data_freshness_guard", "WARN", 501, 500)
    ]
    hold_without_issue = copy.deepcopy(hold)
    hold_without_issue["issues"] = []
    block_without_block = copy.deepcopy(block)
    block_without_block["violations"][0]["severity"] = "WARN"
    optional_as_issue = copy.deepcopy(hold)
    optional_as_issue["issues"][0]["code"] = "MODEL_ABSTAINED"
    too_many_warnings = copy.deepcopy(warn)
    too_many_warnings["violations"] = []
    too_many_warnings["warnings"] = [
        _warning("MODEL_ABSTAINED", f"MODEL_{index:02d}") for index in range(51)
    ]
    too_many_warnings["abstentions"] = [
        _abstention("MODEL_ABSTAINED", "MODEL_00", "ABSTAIN")
    ]
    unsorted = copy.deepcopy(warn)
    unsorted["violations"] = [
        _violation("high_volatility_guard", "WARN", Decimal("0.42"), Decimal("0.35")),
        _violation("max_position_per_asset", "WARN", Decimal("0.21"), Decimal("0.2")),
    ]
    warning_without_abstention = copy.deepcopy(optional_disclosure_missing)
    warning_without_abstention["abstentions"] = []

    return {
        "contracts/examples/risk_decision.valid.json": warn,
        "contracts/examples/risk_decision.allow.valid.json": allow,
        "contracts/examples/risk_decision.warn.valid.json": warn,
        "contracts/examples/risk_decision.hold.valid.json": hold,
        "contracts/examples/risk_decision.block.valid.json": block,
        "contracts/examples/risk_decision.optional-disclosure-missing.valid.json": (
            optional_disclosure_missing
        ),
        "contracts/examples/risk_decision.required-disclosure-missing.valid.json": (
            required_disclosure_missing
        ),
        "contracts/examples/risk_decision.precedence-block-hold.valid.json": block_hold,
        "contracts/examples/risk_decision.precedence-block-warn.valid.json": block_warn,
        "contracts/examples/risk_decision.precedence-hold-warn.valid.json": hold_warn,
        "contracts/examples/risk_decision.precedence-warn-na.valid.json": warn_na,
        "contracts/examples/risk_decision.precedence-pass-na.valid.json": pass_na,
        "contracts/examples/invalid/risk_decision.invalid.json": invalid_decision,
        "contracts/examples/invalid/risk_decision.abstention-without-warning.invalid.json": (
            abstention_without_warning
        ),
        "contracts/examples/invalid/risk_decision.null-violation.invalid.json": null_violation,
        "contracts/examples/invalid/risk_decision.null-risk-item.invalid.json": (
            null_risk_item
        ),
        "contracts/examples/invalid/risk_decision.non-threshold-violation.invalid.json": (
            non_threshold_violation
        ),
        "contracts/examples/invalid/risk_decision.hold-without-issue.invalid.json": (
            hold_without_issue
        ),
        "contracts/examples/invalid/risk_decision.block-without-block-violation.invalid.json": (
            block_without_block
        ),
        "contracts/examples/invalid/risk_decision.optional-as-issue.invalid.json": (
            optional_as_issue
        ),
        "contracts/examples/invalid/risk_decision.too-many-warnings.invalid.json": (
            too_many_warnings
        ),
        "contracts/examples/invalid/risk_decision.unsorted-violations.invalid.json": unsorted,
        "contracts/examples/invalid/risk_decision.warning-without-abstention.invalid.json": (
            warning_without_abstention
        ),
    }


def _stable_result_key(
    item: Mapping[str, Any],
    order: Mapping[str, int],
) -> tuple[int, str, str, str, str, str, str, str, str]:
    return (
        order.get(str(item.get("ruleId", "")), 10_000),
        str(item.get("ruleId", "")),
        str(item.get("code", item.get("severity", ""))),
        str(item.get("source", "")),
        str(item.get("component", "")),
        str(item.get("disposition", "")),
        str(item.get("message", "")),
        str(item.get("metricValue", "")),
        str(item.get("threshold", "")),
    )


def validate_risk_decision_semantics(
    payload: Any, catalog: Mapping[str, Any]
) -> None:
    if not isinstance(payload, dict):
        raise ContractValidationError("Risk decision must be an object.")
    order = {rule["ruleId"]: rule["order"] for rule in catalog["rules"]}
    for container in ("violations", "issues", "warnings", "abstentions"):
        values = payload.get(container)
        if not isinstance(values, list):
            raise ContractValidationError(f"/{container}: array required.")
        if values != sorted(values, key=lambda item: _stable_result_key(item, order)):
            raise ContractValidationError(f"/{container}: stable order is invalid.")

    violations = payload["violations"]
    issues = payload["issues"]
    warnings = payload["warnings"]
    abstentions = payload["abstentions"]
    warning_pairs = Counter(
        (item.get("ruleId"), item.get("code")) for item in warnings
    )
    abstention_pairs = Counter(
        (item.get("ruleId"), item.get("code"))
        for item in abstentions
        if item.get("disposition") == "ABSTAIN"
    )
    if warning_pairs != abstention_pairs:
        raise ContractValidationError(
            "/warnings,/abstentions: optional evidence must be paired "
            "bidirectionally by (ruleId, code)."
        )
    rules_by_id = {rule["ruleId"]: rule for rule in catalog["rules"]}
    for index, violation in enumerate(violations):
        rule_id = violation.get("ruleId")
        rule = rules_by_id.get(rule_id)
        if rule is None:
            raise ContractValidationError(
                f"/violations/{index}/ruleId: unknown catalog rule."
            )
        if rule["executionKind"] != "THRESHOLD":
            raise ContractValidationError(
                f"/violations/{index}/ruleId: only THRESHOLD rules may violate."
            )
        if rule["ownership"] == "SYSTEM_MANAGED":
            expected_severity = rule["defaultSeverity"]
            if violation.get("severity") != expected_severity:
                raise ContractValidationError(
                    f"/violations/{index}/severity: expected {expected_severity} "
                    "for the immutable system rule."
                )
    has_block = any(item.get("severity") == "BLOCK" for item in violations)
    has_abstain = any(
        item.get("disposition") == "ABSTAIN" for item in abstentions
    )
    expected = (
        "BLOCK"
        if has_block
        else "HOLD"
        if issues
        else "WARN"
        if violations or warnings or has_abstain
        else "ALLOW"
    )
    if payload.get("decision") != expected:
        raise ContractValidationError(
            f"/decision: expected {expected} from BLOCK > HOLD > WARN > ALLOW."
        )
    if payload.get("canSubmitOrder") is not (expected in {"ALLOW", "WARN"}):
        raise ContractValidationError("/canSubmitOrder: action mapping drift.")


def generate_outputs(catalog: Mapping[str, Any]) -> dict[str, bytes]:
    validate_catalog_semantics(catalog)
    risk_schema = _risk_decision_schema(catalog)
    hash_schema = _hash_vector_schema()
    Draft202012Validator.check_schema(hash_schema)
    outputs: dict[str, bytes] = {
        "contracts/schemas/s2-2-system-rule-catalog.schema.json": canonical_json_bytes(
            _catalog_meta_schema()
        ),
        "contracts/schemas/s2-2-hash-vector.schema.json": canonical_json_bytes(
            hash_schema
        ),
        "contracts/schemas/risk_decision.schema.json": canonical_json_bytes(
            risk_schema
        ),
        "contracts/examples/s2-2-hash-vector.valid.json": canonical_json_bytes(
            _hash_vector()
        ),
    }
    outputs.update(
        {
            path: canonical_json_bytes(value)
            for path, value in _result_fixtures().items()
        }
    )
    if frozenset(outputs) != OUTPUTS:
        missing = sorted(OUTPUTS - frozenset(outputs))
        unexpected = sorted(frozenset(outputs) - OUTPUTS)
        raise ContractValidationError(
            f"S2.2 OUTPUTS manifest mismatch: missing={missing}, unexpected={unexpected}"
        )

    validator = Draft202012Validator(risk_schema)
    for path, payload in _result_fixtures().items():
        errors = list(validator.iter_errors(payload))
        semantic_error = None
        if not errors:
            try:
                validate_risk_decision_semantics(payload, catalog)
            except ContractValidationError as caught:
                semantic_error = caught
        if path.endswith(".valid.json") and (errors or semantic_error is not None):
            detail = errors[0].message if errors else str(semantic_error)
            raise ContractValidationError(f"{path}: generated positive fixture invalid: {detail}")
        if path.endswith(".invalid.json") and not errors and semantic_error is None:
            raise ContractValidationError(f"{path}: generated negative fixture passed.")

    try:
        Draft202012Validator(hash_schema).validate(_hash_vector())
    except ValidationError as error:
        raise ContractValidationError("S2.2 hash vector fixture is invalid.") from error
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
        path = REPO_ROOT / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        print(f"WROTE {relative_path}")


def _format_catalog() -> None:
    catalog = load_catalog(require_canonical=False)
    CATALOG_PATH.write_bytes(canonical_json_bytes(catalog))
    print(f"FORMATTED {CATALOG_PATH.relative_to(REPO_ROOT).as_posix()}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile and verify the canonical S2.2 offline rule/result contracts."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    action.add_argument("--format-catalog", action="store_true")
    arguments = parser.parse_args()

    try:
        if arguments.format_catalog:
            _format_catalog()
            return 0
        catalog = load_catalog()
        outputs = generate_outputs(catalog)
        if arguments.write:
            _write_outputs(outputs)
            print(
                "S2.2 contract generation succeeded: "
                + hashlib.sha256(canonical_json_bytes(catalog)).hexdigest()
            )
            return 0
        failures = _check_outputs(outputs)
    except (OSError, ContractValidationError, SchemaError) as error:
        print(f"S2.2 contract generation failed: {error}", file=sys.stderr)
        return 1

    if failures:
        print(f"S2.2 contract generation failed: {failures} drift(s)", file=sys.stderr)
        return 1
    print(
        "S2.2 contract generation check succeeded: "
        + hashlib.sha256(canonical_json_bytes(catalog)).hexdigest()
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
