from __future__ import annotations

import argparse
import copy
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Final, Mapping

from jsonschema import Draft202012Validator

_SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    canonical_json_bytes,
    load_json_bytes_strict,
)
from contracts.generated_artifact_io import write_generated_artifact  # noqa: E402


REPO_ROOT = _SCRIPT_REPO_ROOT
GDELT_SCHEMA_PATH = (
    REPO_ROOT / "contracts/schemas/gdelt_news_tone_observation.v1.schema.json"
)
NEWS_SUMMARY_SCHEMA_PATH = (
    REPO_ROOT / "contracts/schemas/news_sentiment_summary.v2.schema.json"
)
CONTRACT_CHANGE_PATH = (
    REPO_ROOT
    / "contracts/changes/20260731-s1-3g-naver-retirement-gdelt-aggregate-lock.md"
)

GDELT_PROJECT_URL: Final[str] = "https://www.gdeltproject.org/"
GDELT_TERMS_URL: Final[str] = "https://www.gdeltproject.org/about.html"
SHA256_PATTERN: Final[str] = "^[0-9a-f]{64}$"
UTC_TIMESTAMP_PATTERN: Final[str] = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)

VALID_FIXTURE_PATHS: Final[frozenset[str]] = frozenset(
    {
        "contracts/examples/gdelt_news_tone_observation.v1.available.valid.json",
        "contracts/examples/gdelt_news_tone_observation.v1.abstain.valid.json",
        "contracts/examples/news_sentiment_summary.v2.available.valid.json",
        "contracts/examples/news_sentiment_summary.v2.abstain.valid.json",
    }
)
INVALID_FIXTURE_PATHS: Final[frozenset[str]] = frozenset(
    {
        "contracts/examples/invalid/gdelt_news_tone_observation.v1.article-title.invalid.json",
        "contracts/examples/invalid/gdelt_news_tone_observation.v1.article-url.invalid.json",
        "contracts/examples/invalid/gdelt_news_tone_observation.v1.available-at-inversion.invalid.json",
        "contracts/examples/invalid/gdelt_news_tone_observation.v1.future-observed-at.invalid.json",
        "contracts/examples/invalid/gdelt_news_tone_observation.v1.missing-attribution.invalid.json",
        "contracts/examples/invalid/gdelt_news_tone_observation.v1.nan.invalid.json",
        "contracts/examples/invalid/gdelt_news_tone_observation.v1.partial-available.invalid.json",
        "contracts/examples/invalid/gdelt_news_tone_observation.v1.raw-query.invalid.json",
        "contracts/examples/invalid/news_sentiment_summary.v2.fake-zero.invalid.json",
        "contracts/examples/invalid/news_sentiment_summary.v2.unknown-field.invalid.json",
    }
)
OUTPUTS: Final[frozenset[str]] = frozenset(
    {
        "contracts/schemas/gdelt_news_tone_observation.v1.schema.json",
        "contracts/schemas/news_sentiment_summary.v2.schema.json",
        *VALID_FIXTURE_PATHS,
        *INVALID_FIXTURE_PATHS,
    }
)


def _closed_object(
    properties: Mapping[str, object],
    required: tuple[str, ...] | list[str],
) -> dict[str, object]:
    return {
        "additionalProperties": False,
        "properties": dict(properties),
        "required": list(required),
        "type": "object",
    }


def _document(schema_id: str, body: Mapping[str, object]) -> dict[str, object]:
    return {
        "$id": f"contracts/schemas/{schema_id}.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **body,
    }


def _utc_timestamp() -> dict[str, object]:
    return {
        "format": "date-time",
        "pattern": UTC_TIMESTAMP_PATTERN,
        "type": "string",
    }


def _attribution_schema() -> dict[str, object]:
    return _closed_object(
        {
            "citation": {"const": "The GDELT Project"},
            "projectUrl": {"const": GDELT_PROJECT_URL},
            "provider": {"const": "GDELT"},
            "termsUrl": {"const": GDELT_TERMS_URL},
        },
        ["provider", "citation", "projectUrl", "termsUrl"],
    )


def _source_observation_ref_schema() -> dict[str, object]:
    return _closed_object(
        {
            "artifactHash": {"pattern": SHA256_PATTERN, "type": "string"},
            "observationId": {
                "maxLength": 96,
                "minLength": 8,
                "pattern": "^[a-z0-9][a-z0-9._:-]+$",
                "type": "string",
            },
        },
        ["observationId", "artifactHash"],
    )


def gdelt_observation_schema() -> dict[str, object]:
    point = _closed_object(
        {
            "articleCount": {
                "maximum": 1_000_000_000,
                "minimum": 0,
                "type": "integer",
            },
            "averageTone": {
                "maximum": 100,
                "minimum": -100,
                "type": "number",
            },
            "coverageRatio": {
                "maximum": 1,
                "minimum": 0,
                "type": "number",
            },
            "norm": {
                "maximum": 10_000_000_000,
                "minimum": 1,
                "type": "integer",
            },
            "timestamp": _utc_timestamp(),
        },
        ["timestamp", "averageTone", "articleCount", "norm", "coverageRatio"],
    )
    properties: dict[str, object] = {
        "abstainReason": {
            "enum": [
                "EMPTY_WINDOW",
                "INCOMPLETE_SOURCE",
                "INVALID_RESPONSE",
                "MAPPING_AMBIGUOUS",
                "NORM_ZERO",
                "PROVIDER_DISABLED",
            ]
        },
        "approvalPacketHash": {
            "oneOf": [
                {"pattern": SHA256_PATTERN, "type": "string"},
                {"type": "null"},
            ]
        },
        "artifactHash": {"pattern": SHA256_PATTERN, "type": "string"},
        "attribution": _attribution_schema(),
        "availableAt": _utc_timestamp(),
        "collectionMode": {"enum": ["APPROVED_ONLINE", "OFFLINE_FIXTURE"]},
        "decisionAuthority": {"const": "NONE"},
        "articleMetadataStored": {"const": False},
        "modes": {
            "maxItems": 2,
            "minItems": 2,
            "prefixItems": [
                {"const": "TIMELINE_TONE"},
                {"const": "TIMELINE_VOL_RAW"},
            ],
            "type": "array",
        },
        "observationId": {
            "maxLength": 96,
            "minLength": 8,
            "pattern": "^gdelt_obs_[a-z0-9][a-z0-9_-]+$",
            "type": "string",
        },
        "observedAt": _utc_timestamp(),
        "physicalAttemptCount": {"maximum": 1, "minimum": 0, "type": "integer"},
        "points": {
            "items": point,
            "maxItems": 512,
            "minItems": 1,
            "type": "array",
        },
        "queryRegistryId": {
            "maxLength": 64,
            "minLength": 3,
            "pattern": "^[a-z][a-z0-9_]+_v[0-9]+$",
            "type": "string",
        },
        "rawProviderDataStored": {"const": False},
        "receivedAt": _utc_timestamp(),
        "schemaVersion": {"const": "1"},
        "sourceCompleteness": {
            "enum": ["COMPLETE", "EMPTY", "MALFORMED", "PARTIAL", "UNAVAILABLE"]
        },
        "status": {"enum": ["ABSTAIN", "AVAILABLE"]},
        "windowEnd": _utc_timestamp(),
        "windowStart": _utc_timestamp(),
    }
    body = _closed_object(
        properties,
        [
            "schemaVersion",
            "observationId",
            "queryRegistryId",
            "status",
            "decisionAuthority",
            "collectionMode",
            "physicalAttemptCount",
            "approvalPacketHash",
            "windowStart",
            "windowEnd",
            "observedAt",
            "receivedAt",
            "availableAt",
            "sourceCompleteness",
            "modes",
            "rawProviderDataStored",
            "articleMetadataStored",
            "attribution",
            "artifactHash",
        ],
    )
    body["allOf"] = [
        {
            "if": {
                "properties": {"status": {"const": "AVAILABLE"}},
                "required": ["status"],
            },
            "then": {
                "not": {"required": ["abstainReason"]},
                "properties": {"sourceCompleteness": {"const": "COMPLETE"}},
                "required": ["points"],
            },
            "else": {
                "not": {"required": ["points"]},
                "properties": {
                    "sourceCompleteness": {
                        "enum": ["EMPTY", "MALFORMED", "PARTIAL", "UNAVAILABLE"]
                    }
                },
                "required": ["abstainReason"],
            },
        },
        {
            "if": {
                "properties": {"collectionMode": {"const": "OFFLINE_FIXTURE"}},
                "required": ["collectionMode"],
            },
            "then": {
                "properties": {
                    "approvalPacketHash": {"type": "null"},
                    "physicalAttemptCount": {"const": 0},
                }
            },
            "else": {
                "properties": {
                    "approvalPacketHash": {
                        "pattern": SHA256_PATTERN,
                        "type": "string",
                    },
                    "physicalAttemptCount": {"const": 1},
                }
            },
        },
    ]
    return _document("gdelt_news_tone_observation.v1", body)


def news_summary_schema() -> dict[str, object]:
    properties: dict[str, object] = {
        "abstainReason": {
            "enum": [
                "CONFLICT_UNRESOLVED",
                "INPUT_INCOMPLETE",
                "INPUT_STALE",
                "NO_OBSERVATIONS",
                "PRODUCER_FAILURE",
            ]
        },
        "allowedUses": {
            "const": ["EXPLANATION_ONLY"],
            "items": {"const": "EXPLANATION_ONLY"},
            "maxItems": 1,
            "minItems": 1,
            "type": "array",
        },
        "articleCount": {
            "maximum": 1_000_000_000,
            "minimum": 1,
            "type": "integer",
        },
        "articleMetadataStored": {"const": False},
        "artifactHash": {"pattern": SHA256_PATTERN, "type": "string"},
        "artifactId": {
            "maxLength": 96,
            "minLength": 8,
            "pattern": "^news_sum_[a-z0-9][a-z0-9_-]+$",
            "type": "string",
        },
        "asOf": _utc_timestamp(),
        "attentionScore": {"maximum": 1, "minimum": 0, "type": "number"},
        "attribution": _attribution_schema(),
        "availableAt": _utc_timestamp(),
        "conflictFlag": {"type": "boolean"},
        "decisionAuthority": {"const": "NONE"},
        "producer": {"const": "NEWS_SENTIMENT_AGGREGATOR"},
        "qualityStatus": {
            "enum": ["COMPLETE", "CONFLICTED", "INCOMPLETE", "STALE", "UNAVAILABLE"]
        },
        "rawProviderDataStored": {"const": False},
        "riskDecisionHashIncluded": {"const": False},
        "s5FeatureEligible": {"const": False},
        "schemaVersion": {"const": "2"},
        "sentimentScore": {"maximum": 1, "minimum": -1, "type": "number"},
        "sourceObservationRefs": {
            "items": _source_observation_ref_schema(),
            "maxItems": 32,
            "minItems": 1,
            "type": "array",
            "uniqueItems": True,
        },
        "sourceWorkspace": {"const": "decision-platform"},
        "status": {"enum": ["ABSTAIN", "AVAILABLE"]},
        "summary": {"maxLength": 1000, "minLength": 1, "type": "string"},
        "symbol": {"pattern": "^[0-9]{6}$", "type": "string"},
    }
    body = _closed_object(
        properties,
        [
            "schemaVersion",
            "artifactId",
            "symbol",
            "asOf",
            "availableAt",
            "status",
            "producer",
            "sourceWorkspace",
            "decisionAuthority",
            "allowedUses",
            "riskDecisionHashIncluded",
            "s5FeatureEligible",
            "rawProviderDataStored",
            "articleMetadataStored",
            "sourceObservationRefs",
            "attribution",
            "artifactHash",
            "qualityStatus",
        ],
    )
    available_fields = [
        "sentimentScore",
        "attentionScore",
        "articleCount",
        "conflictFlag",
        "summary",
    ]
    body["allOf"] = [
        {
            "if": {
                "properties": {"status": {"const": "AVAILABLE"}},
                "required": ["status"],
            },
            "then": {
                "not": {"required": ["abstainReason"]},
                "properties": {"qualityStatus": {"const": "COMPLETE"}},
                "required": available_fields,
            },
            "else": {
                "allOf": [
                    {"not": {"required": [field]}}
                    for field in available_fields
                ],
                "properties": {
                    "qualityStatus": {
                        "enum": [
                            "CONFLICTED",
                            "INCOMPLETE",
                            "STALE",
                            "UNAVAILABLE",
                        ]
                    }
                },
                "required": ["abstainReason"],
            },
        }
    ]
    return _document("news_sentiment_summary.v2", body)


def _parse_utc(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ContractValidationError(f"{field} must be a canonical UTC timestamp.")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise ContractValidationError(f"{field} is invalid.") from error
    return parsed


def _decimal(value: object, *, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ContractValidationError(f"{field} must be numeric.")
    converted = Decimal(str(value))
    if not converted.is_finite():
        raise ContractValidationError(f"{field} must be finite.")
    return converted


def validate_gdelt_observation_semantics(value: object) -> None:
    """GDELT 집계 관측의 시각·비율·offline 권한 계약을 wall clock 없이 검증한다."""
    if not isinstance(value, dict):
        raise ContractValidationError("GDELT observation must be an object.")
    window_start = _parse_utc(value.get("windowStart"), field="windowStart")
    window_end = _parse_utc(value.get("windowEnd"), field="windowEnd")
    observed_at = _parse_utc(value.get("observedAt"), field="observedAt")
    received_at = _parse_utc(value.get("receivedAt"), field="receivedAt")
    available_at = _parse_utc(value.get("availableAt"), field="availableAt")
    if not window_start < window_end:
        raise ContractValidationError("windowStart must be before windowEnd.")
    if window_end > observed_at:
        raise ContractValidationError("observedAt must not precede windowEnd.")
    if observed_at > received_at:
        raise ContractValidationError("observedAt must not be in the future of receivedAt.")
    if received_at > available_at:
        raise ContractValidationError("availableAt must not precede receivedAt.")

    status = value.get("status")
    points = value.get("points")
    if status == "AVAILABLE":
        if value.get("sourceCompleteness") != "COMPLETE" or not isinstance(points, list):
            raise ContractValidationError("AVAILABLE requires complete aggregate points.")
        previous: datetime | None = None
        for index, point in enumerate(points):
            if not isinstance(point, dict):
                raise ContractValidationError(f"points[{index}] must be an object.")
            timestamp = _parse_utc(point.get("timestamp"), field=f"points[{index}].timestamp")
            if not window_start <= timestamp < window_end:
                raise ContractValidationError(f"points[{index}] is outside the observation window.")
            if previous is not None and timestamp <= previous:
                raise ContractValidationError("GDELT points must be strictly time ordered.")
            previous = timestamp
            count = point.get("articleCount")
            norm = point.get("norm")
            if (
                not isinstance(count, int)
                or isinstance(count, bool)
                or not isinstance(norm, int)
                or isinstance(norm, bool)
                or norm <= 0
                or count < 0
                or count > norm
            ):
                raise ContractValidationError("articleCount/norm bounds are invalid.")
            expected_ratio = (Decimal(count) / Decimal(norm)).quantize(
                Decimal("0.00000001")
            )
            actual_ratio = _decimal(
                point.get("coverageRatio"), field=f"points[{index}].coverageRatio"
            ).quantize(Decimal("0.00000001"))
            if actual_ratio != expected_ratio:
                raise ContractValidationError("coverageRatio must equal articleCount / norm.")
            tone = _decimal(point.get("averageTone"), field=f"points[{index}].averageTone")
            if not Decimal("-100") <= tone <= Decimal("100"):
                raise ContractValidationError("averageTone is outside the contract range.")
    elif status == "ABSTAIN":
        if points is not None:
            raise ContractValidationError("ABSTAIN must not carry aggregate points.")
    else:
        raise ContractValidationError("GDELT status is invalid.")

    if value.get("collectionMode") == "OFFLINE_FIXTURE":
        if value.get("physicalAttemptCount") != 0 or value.get("approvalPacketHash") is not None:
            raise ContractValidationError("Offline fixtures must keep provider calls at zero.")


def validate_news_summary_semantics(value: object) -> None:
    """뉴스 요약 tagged union과 설명 전용 권한을 aggregate field만으로 검증한다."""
    if not isinstance(value, dict):
        raise ContractValidationError("News sentiment summary must be an object.")
    as_of = _parse_utc(value.get("asOf"), field="asOf")
    available_at = _parse_utc(value.get("availableAt"), field="availableAt")
    if as_of > available_at:
        raise ContractValidationError("availableAt must not precede asOf.")
    if value.get("decisionAuthority") != "NONE":
        raise ContractValidationError("News summaries have no decision authority.")
    if value.get("allowedUses") != ["EXPLANATION_ONLY"]:
        raise ContractValidationError("News summaries are explanation-only.")
    if value.get("riskDecisionHashIncluded") is not False or value.get("s5FeatureEligible") is not False:
        raise ContractValidationError("News summaries cannot affect RiskDecision or S5 features.")
    available_fields = {
        "sentimentScore",
        "attentionScore",
        "articleCount",
        "conflictFlag",
        "summary",
    }
    if value.get("status") == "AVAILABLE":
        if value.get("qualityStatus") != "COMPLETE" or not available_fields <= value.keys():
            raise ContractValidationError("AVAILABLE requires complete aggregate summary fields.")
        _decimal(value.get("sentimentScore"), field="sentimentScore")
        _decimal(value.get("attentionScore"), field="attentionScore")
    elif value.get("status") == "ABSTAIN":
        leaked = available_fields & value.keys()
        if leaked:
            raise ContractValidationError(
                f"ABSTAIN must not carry fake aggregate values: {sorted(leaked)}"
            )
    else:
        raise ContractValidationError("News summary status is invalid.")


def _attribution() -> dict[str, object]:
    return {
        "citation": "The GDELT Project",
        "projectUrl": GDELT_PROJECT_URL,
        "provider": "GDELT",
        "termsUrl": GDELT_TERMS_URL,
    }


def _gdelt_available_fixture() -> dict[str, object]:
    return {
        "approvalPacketHash": None,
        "articleMetadataStored": False,
        "artifactHash": "1" * 64,
        "attribution": _attribution(),
        "availableAt": "2026-07-31T00:00:02Z",
        "collectionMode": "OFFLINE_FIXTURE",
        "decisionAuthority": "NONE",
        "modes": ["TIMELINE_TONE", "TIMELINE_VOL_RAW"],
        "observationId": "gdelt_obs_semiconductor_20260731",
        "observedAt": "2026-07-31T00:00:00Z",
        "physicalAttemptCount": 0,
        "points": [
            {
                "articleCount": 24,
                "averageTone": -2.5,
                "coverageRatio": 0.0002,
                "norm": 120000,
                "timestamp": "2026-07-30T00:00:00Z",
            },
            {
                "articleCount": 15,
                "averageTone": -1.25,
                "coverageRatio": 0.000125,
                "norm": 120000,
                "timestamp": "2026-07-30T12:00:00Z",
            },
        ],
        "queryRegistryId": "global_semiconductor_stress_v1",
        "rawProviderDataStored": False,
        "receivedAt": "2026-07-31T00:00:01Z",
        "schemaVersion": "1",
        "sourceCompleteness": "COMPLETE",
        "status": "AVAILABLE",
        "windowEnd": "2026-07-31T00:00:00Z",
        "windowStart": "2026-07-30T00:00:00Z",
    }


def _gdelt_abstain_fixture() -> dict[str, object]:
    value = _gdelt_available_fixture()
    value.pop("points")
    value["abstainReason"] = "PROVIDER_DISABLED"
    value["artifactHash"] = "2" * 64
    value["sourceCompleteness"] = "UNAVAILABLE"
    value["status"] = "ABSTAIN"
    return value


def _summary_available_fixture() -> dict[str, object]:
    return {
        "allowedUses": ["EXPLANATION_ONLY"],
        "articleCount": 39,
        "articleMetadataStored": False,
        "artifactHash": "3" * 64,
        "artifactId": "news_sum_005930_20260731",
        "asOf": "2026-07-31T00:00:02Z",
        "attentionScore": 0.0001625,
        "attribution": _attribution(),
        "availableAt": "2026-07-31T00:00:03Z",
        "conflictFlag": True,
        "decisionAuthority": "NONE",
        "producer": "NEWS_SENTIMENT_AGGREGATOR",
        "qualityStatus": "COMPLETE",
        "rawProviderDataStored": False,
        "riskDecisionHashIncluded": False,
        "s5FeatureEligible": False,
        "schemaVersion": "2",
        "sentimentScore": -0.1875,
        "sourceObservationRefs": [
            {
                "artifactHash": "1" * 64,
                "observationId": "gdelt_obs_semiconductor_20260731",
            }
        ],
        "sourceWorkspace": "decision-platform",
        "status": "AVAILABLE",
        "summary": "합성 fixture의 뉴스 톤과 관심도 집계가 상반되어 설명 근거만 제공한다.",
        "symbol": "005930",
    }


def _summary_abstain_fixture() -> dict[str, object]:
    value = _summary_available_fixture()
    for field in (
        "articleCount",
        "attentionScore",
        "conflictFlag",
        "sentimentScore",
        "summary",
    ):
        value.pop(field)
    value["abstainReason"] = "INPUT_INCOMPLETE"
    value["artifactHash"] = "4" * 64
    value["qualityStatus"] = "INCOMPLETE"
    value["status"] = "ABSTAIN"
    return value


def fixtures() -> dict[str, object]:
    gdelt_available = _gdelt_available_fixture()
    gdelt_abstain = _gdelt_abstain_fixture()
    summary_available = _summary_available_fixture()
    summary_abstain = _summary_abstain_fixture()
    generated: dict[str, object] = {
        "contracts/examples/gdelt_news_tone_observation.v1.available.valid.json": gdelt_available,
        "contracts/examples/gdelt_news_tone_observation.v1.abstain.valid.json": gdelt_abstain,
        "contracts/examples/news_sentiment_summary.v2.available.valid.json": summary_available,
        "contracts/examples/news_sentiment_summary.v2.abstain.valid.json": summary_abstain,
    }

    for suffix, field, value in (
        ("article-title", "articleTitle", "Synthetic article title"),
        ("article-url", "articleUrl", "https://example.test/article/1"),
        ("raw-query", "rawQuery", "semiconductor stocks"),
    ):
        changed = copy.deepcopy(gdelt_available)
        changed[field] = value
        generated[
            f"contracts/examples/invalid/gdelt_news_tone_observation.v1.{suffix}.invalid.json"
        ] = changed

    partial = copy.deepcopy(gdelt_available)
    partial["sourceCompleteness"] = "PARTIAL"
    generated[
        "contracts/examples/invalid/gdelt_news_tone_observation.v1.partial-available.invalid.json"
    ] = partial

    missing_attribution = copy.deepcopy(gdelt_available)
    missing_attribution.pop("attribution")
    generated[
        "contracts/examples/invalid/gdelt_news_tone_observation.v1.missing-attribution.invalid.json"
    ] = missing_attribution

    future_observed = copy.deepcopy(gdelt_available)
    future_observed["observedAt"] = "2026-07-31T00:00:04Z"
    generated[
        "contracts/examples/invalid/gdelt_news_tone_observation.v1.future-observed-at.invalid.json"
    ] = future_observed

    inversion = copy.deepcopy(gdelt_available)
    inversion["availableAt"] = "2026-07-30T23:59:59Z"
    generated[
        "contracts/examples/invalid/gdelt_news_tone_observation.v1.available-at-inversion.invalid.json"
    ] = inversion

    fake_zero = copy.deepcopy(summary_abstain)
    fake_zero.update(
        {
            "articleCount": 0,
            "attentionScore": 0,
            "conflictFlag": False,
            "sentimentScore": 0,
            "summary": "0",
        }
    )
    generated[
        "contracts/examples/invalid/news_sentiment_summary.v2.fake-zero.invalid.json"
    ] = fake_zero

    unknown = copy.deepcopy(summary_available)
    unknown["modelRecommendation"] = "BUY"
    generated[
        "contracts/examples/invalid/news_sentiment_summary.v2.unknown-field.invalid.json"
    ] = unknown
    return generated


def _validate_generated_fixture(
    path: str,
    payload: object,
    validators: Mapping[str, Draft202012Validator],
) -> None:
    schema_id = ".".join(Path(path).name.split(".")[:2])
    validator = validators[schema_id]
    schema_errors = list(validator.iter_errors(payload))
    semantic_error: ContractValidationError | None = None
    if not schema_errors:
        try:
            if schema_id == "gdelt_news_tone_observation.v1":
                validate_gdelt_observation_semantics(payload)
            else:
                validate_news_summary_semantics(payload)
        except ContractValidationError as caught:
            semantic_error = caught
    if path in VALID_FIXTURE_PATHS and (schema_errors or semantic_error):
        detail = schema_errors[0].message if schema_errors else str(semantic_error)
        raise ContractValidationError(f"{path}: generated positive fixture invalid: {detail}")
    if path in INVALID_FIXTURE_PATHS and not schema_errors and semantic_error is None:
        raise ContractValidationError(f"{path}: generated negative fixture passed.")


def generate_outputs() -> dict[str, bytes]:
    contract_change = CONTRACT_CHANGE_PATH.read_text(encoding="utf-8")
    for marker in (
        "AUTH_NAVER_RETIREMENT_GDELT_AGGREGATE_CONTRACT=APPROVED",
        "GDELT_PROVIDER_PHYSICAL_CALLS=0",
        "NAVER_ACTIVE_PROVIDER_RUNTIME_STORAGE=RETIRED",
        "SECURITY_SCAN_TIMING=FINAL_CONSOLIDATED_CAMPAIGN",
    ):
        if marker not in contract_change:
            raise ContractValidationError("S1.3G contract-change authority drifted.")

    gdelt_schema = gdelt_observation_schema()
    summary_schema = news_summary_schema()
    Draft202012Validator.check_schema(gdelt_schema)
    Draft202012Validator.check_schema(summary_schema)
    validators = {
        "gdelt_news_tone_observation.v1": Draft202012Validator(gdelt_schema),
        "news_sentiment_summary.v2": Draft202012Validator(summary_schema),
    }
    generated_fixtures = fixtures()
    expected_json_fixture_paths = VALID_FIXTURE_PATHS | (
        INVALID_FIXTURE_PATHS
        - {
            "contracts/examples/invalid/gdelt_news_tone_observation.v1.nan.invalid.json"
        }
    )
    if frozenset(generated_fixtures) != expected_json_fixture_paths:
        raise ContractValidationError("S1.3G fixture manifest drifted.")
    for path, payload in generated_fixtures.items():
        _validate_generated_fixture(path, payload, validators)

    finite_payload = canonical_json_bytes(_gdelt_available_fixture())
    nan_payload = finite_payload.replace(b"-2.5", b"NaN", 1)
    if nan_payload == finite_payload:
        raise ContractValidationError("GDELT NaN fixture mutation did not apply.")
    try:
        load_json_bytes_strict(nan_payload, source="generated GDELT NaN fixture")
    except ContractValidationError:
        pass
    else:
        raise ContractValidationError("GDELT NaN fixture unexpectedly parsed.")

    outputs = {
        "contracts/schemas/gdelt_news_tone_observation.v1.schema.json": canonical_json_bytes(
            gdelt_schema
        ),
        "contracts/schemas/news_sentiment_summary.v2.schema.json": canonical_json_bytes(
            summary_schema
        ),
        **{
            path: canonical_json_bytes(payload)
            for path, payload in generated_fixtures.items()
        },
        "contracts/examples/invalid/gdelt_news_tone_observation.v1.nan.invalid.json": nan_payload,
    }
    if frozenset(outputs) != OUTPUTS:
        raise ContractValidationError("S1.3G generated output manifest drifted.")
    return dict(sorted(outputs.items()))


def _check_outputs(outputs: Mapping[str, bytes]) -> int:
    failures: list[str] = []
    for relative_path, expected in outputs.items():
        path = REPO_ROOT / relative_path
        if not path.is_file() or path.read_bytes() != expected:
            failures.append(relative_path)
    if failures:
        for relative_path in failures:
            print(f"DRIFT {relative_path}")
        return 1
    print("S1_3G_NEWS_CONTRACT_LOCK_VERIFIED")
    return 0


def _write_outputs(outputs: Mapping[str, bytes]) -> int:
    for relative_path, payload in outputs.items():
        write_generated_artifact(REPO_ROOT, relative_path, payload)
    print("S1_3G_NEWS_CONTRACTS_WRITTEN")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the S1.3G aggregate-only news contracts."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    outputs = generate_outputs()
    if arguments.check:
        return _check_outputs(outputs)
    return _write_outputs(outputs)


if __name__ == "__main__":
    raise SystemExit(main())
