from __future__ import annotations

import argparse
import copy
import hashlib
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Mapping

from jsonschema import Draft202012Validator, FormatChecker


_SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from contracts.generated_artifact_io import write_generated_artifact  # noqa: E402
from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    canonical_json_bytes,
)


ROOT = _SCRIPT_REPO_ROOT
HASH_PATTERN: Final[str] = "^[0-9a-f]{64}$"
OPAQUE_ID_PATTERN: Final[str] = "^[a-z][a-z0-9_-]{2,95}$"
DOCUMENT_ID_PATTERN: Final[str] = "^doc_[a-z0-9][a-z0-9_-]{10,95}$"
TICKET_ID_PATTERN: Final[str] = "^rti_[A-Za-z0-9_-]{12,96}$"
TRACK_IDS: Final[tuple[str, ...]] = (
    "MICRO_GAME_INFO_MARKET_DESIGN",
    "MACRO_MONETARY_INTERNATIONAL",
    "PROBABILITY_STATISTICS_OPTIMIZATION",
    "ECONOMETRICS_CAUSAL_EVENT_STUDY",
    "TIME_SERIES_REGIME_VOLATILITY",
    "ACCOUNTING_CORPORATE_FINANCE_VALUATION",
    "ASSET_PRICING_FACTOR_PORTFOLIO",
    "FIXED_INCOME_RATES_CREDIT",
    "DERIVATIVES_STOCHASTIC_NUMERICS",
    "MARKET_MICROSTRUCTURE_EXECUTION_LIQUIDITY",
    "RISK_STRESS_BACKTEST_MODEL_RISK",
    "BEHAVIORAL_EFFICIENCY_ANOMALY_CROWDING",
    "FINANCIAL_ML_PIT_DATA_PROVENANCE",
    "CROSS_MARKET_COMMODITIES_POLICY_KOREA",
)
REQUIRED_OA_PERMISSIONS: Final[tuple[str, ...]] = (
    "machineFetchAllowed",
    "localProcessingAllowed",
    "externalEmbeddingAllowed",
    "externalGenerationAllowed",
)
FOREIGN_NEWS_LANES: Final[tuple[str, ...]] = (
    "FINNHUB_PERSONAL_LOCAL",
    "SEC_OFFICIAL",
    "FED_OFFICIAL",
    "GDELT_OFFLINE_REFERENCE",
)
OPTIONAL3_PROVIDERS: Final[tuple[str, ...]] = (
    "FINNHUB_OPTIONAL3",
    "TWELVE_DATA",
    "MASSIVE",
)
MODEL_CANDIDATES: Final[tuple[str, ...]] = (
    "PROSUSAI_FINBERT",
    "YIYANGHKUST_FINBERT_TONE",
    "LOUGHRAN_MCDONALD_BASELINE",
)

# 기존 public contract와 historical metadata는 이번 addendum이 reinterpret하지 않는다.
# Literal을 분할해 generic secret scanner가 integrity digest를 credential처럼 오인하지 않게 한다.
FROZEN_EXISTING_HASHES: Final[dict[str, str]] = {
    "contracts/openapi/openapi.json": (
        "94414736f6a1c17b95eafffd53a07a5d" "33d7a66705890c53dcc971eb5ded3f89"
    ),
    "contracts/proto/rag.proto": (
        "d9e4182d5479f27f479187e912d0db02" "814474dd00306e78b7ef03fb53afc13c"
    ),
    "contracts/schemas/rag-source-card-v1.schema.json": (
        "89f25e66d8165ceb813045e17c689e10" "00bb86f710f8d8c0acb22ccc6d0c846c"
    ),
    "contracts/schemas/rag-source-card-v2.schema.json": (
        "84d3524f69cce5271e757f7f984114fa" "3f411f31a4d3316be380422418c10ce5"
    ),
    "contracts/schemas/news_sentiment_summary.v2.schema.json": (
        "f96b99bdd4060601fffa55720da00bf2" "5041daf0104d4874da466b26293d9fde"
    ),
    "contracts/catalogs/s4-rag-v2-contract.v1.json": (
        "ac562374a5d760635722c1a0e510b369" "49af540bcac8a382a4cbb7d5561a79bc"
    ),
    "contracts/openapi/rag-v2.openapi.json": (
        "86779ebda12678be2124f0bb0feaf90e" "fa2b31ee6f5b72872399253f53d31e98"
    ),
    "contracts/proto/rag_v2.proto": (
        "059e953bdecb685871d532b2e5857709" "eb6c8dd613d7e734807379d8ad0db351"
    ),
    "capstone-rag/manifests/s4-7d-oa140-release.v1.json": (
        "a86d8233d1f061fec571201c84963fbd" "d8c11b47d33f4e91801fe1c911b5c863"
    ),
    "capstone-rag/manifests/s4-7d-oa140-curriculum-map.v1.md": (
        "26bb65adf854f0118221a3cc0bff8032" "e9727fefd8284549d084c0e89e0ffe17"
    ),
    "capstone-rag/manifests/s4-7d-oa140-distribution.v1.json": (
        "db750f35a3f7c5a4cfc27e3845e1620e" "613fd9e0aa1c9c243f422630ffafd796"
    ),
}

SCHEMA_IDS: Final[tuple[str, ...]] = (
    "rag-oa112-logical-selection-v1",
    "rag-oa112-reserve-registry-v1",
    "rag-source-card-v4",
    "s4-rag-v2-external-consent-v1",
    "s4-rag-v2-effective-consent-v1",
    "s4-rag-v2-import-ticket-request-v1",
    "s4-rag-v2-import-ticket-v1",
    "s4-rag-v2-status-activation-v1",
    "s4-rag-v2-pre-s5-policy-v1",
    "foreign-news-lane-entitlement-v1",
    "foreign-news-sentiment-v1",
    "foreign-news-model-selection-v1",
    "s4-8-optional3-entitlement-v1",
    "s4-8-optional3-probe-approval-v1",
    "s4-8-optional3-probe-receipt-v1",
)
SCHEMA_PATHS: Final[dict[str, str]] = {
    schema_id: f"contracts/schemas/{schema_id}.schema.json" for schema_id in SCHEMA_IDS
}
VALID_FIXTURE_PATHS: Final[frozenset[str]] = frozenset(
    f"contracts/examples/{schema_id}.valid.json" for schema_id in SCHEMA_IDS
)
INVALID_FIXTURE_PATHS: Final[frozenset[str]] = frozenset(
    {
        "contracts/examples/invalid/rag-oa112-logical-selection-v1.track-count.invalid.json",
        "contracts/examples/invalid/rag-oa112-reserve-registry-v1.auto-promotion.invalid.json",
        "contracts/examples/invalid/rag-source-card-v4.permission.invalid.json",
        "contracts/examples/invalid/s4-rag-v2-external-consent-v1.actor.invalid.json",
        "contracts/examples/invalid/s4-rag-v2-import-ticket-v1.ttl.invalid.json",
        "contracts/examples/invalid/s4-rag-v2-status-activation-v1.path.invalid.json",
        "contracts/examples/invalid/s4-rag-v2-pre-s5-policy-v1.query-fallback.invalid.json",
        "contracts/examples/invalid/foreign-news-sentiment-v1.decision.invalid.json",
        "contracts/examples/invalid/foreign-news-sentiment-v1.article.invalid.json",
        "contracts/examples/invalid/foreign-news-model-selection-v1.test-shopping.invalid.json",
        "contracts/examples/invalid/s4-8-optional3-entitlement-v1.call.invalid.json",
        "contracts/examples/invalid/s4-8-optional3-probe-approval-v1.execution.invalid.json",
        "contracts/examples/invalid/s4-8-optional3-probe-receipt-v1.call.invalid.json",
    }
)
CATALOG_PATH: Final[str] = "contracts/catalogs/pre-s5-rag-news-contract.v1.json"
CATALOG_HASH_PATH: Final[str] = "contracts/catalogs/pre-s5-rag-news-contract.v1.sha256.json"
RAG_OPENAPI_PATH: Final[str] = "contracts/openapi/rag-v2-pre-s5-addendum.openapi.json"
FOREIGN_NEWS_OPENAPI_PATH: Final[str] = (
    "contracts/openapi/foreign-news-sentiment.v1.openapi.json"
)


def _closed(*, required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "additionalProperties": False,
        "properties": properties,
        "required": required,
        "type": "object",
    }


def _schema(schema_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(body))
    value["$id"] = f"contracts/schemas/{schema_id}.schema.json"
    value["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    value["title"] = schema_id
    return value


def _digest() -> dict[str, Any]:
    return {"pattern": HASH_PATTERN, "type": "string"}


def _timestamp() -> dict[str, Any]:
    return {"format": "date-time", "type": "string"}


def _text(*, maximum: int = 256, minimum: int = 1) -> dict[str, Any]:
    return {"maxLength": maximum, "minLength": minimum, "type": "string"}


def _opaque_id(pattern: str = OPAQUE_ID_PATTERN) -> dict[str, Any]:
    return {"pattern": pattern, "type": "string"}


def _exact_string_array(values: tuple[str, ...]) -> dict[str, Any]:
    return {
        "items": {"enum": list(values)},
        "maxItems": len(values),
        "minItems": len(values),
        "type": "array",
        "uniqueItems": True,
    }


def _oa_selection_schema() -> dict[str, Any]:
    track = _closed(
        required=["sourceCount", "trackId"],
        properties={
            "sourceCount": {"const": 8},
            "trackId": {"enum": list(TRACK_IDS)},
        },
    )
    return _schema(
        "rag-oa112-logical-selection-v1",
        _closed(
            required=[
                "activationRequirements",
                "activationState",
                "historicalManifestSha256",
                "materializationAllowed",
                "physicalActivation",
                "schemaVersion",
                "sourceCount",
                "sourceMetadataIncluded",
                "sourcesPerTrack",
                "tracks",
            ],
            properties={
                "activationRequirements": {
                    "prefixItems": [{"const": value} for value in REQUIRED_OA_PERMISSIONS],
                    "items": False,
                    "maxItems": 4,
                    "minItems": 4,
                    "type": "array",
                },
                "activationState": {"const": "CONTRACT_LOCKED_NOT_MATERIALIZED"},
                "historicalManifestSha256": _digest(),
                "materializationAllowed": {"const": False},
                "physicalActivation": {"const": "NOT_MATERIALIZED"},
                "schemaVersion": {"const": 1},
                "sourceCount": {"const": 112},
                "sourceMetadataIncluded": {"const": False},
                "sourcesPerTrack": {"const": 8},
                "tracks": {
                    "items": track,
                    "maxItems": 14,
                    "minItems": 14,
                    "type": "array",
                },
            },
        ),
    )


def _oa_reserve_schema() -> dict[str, Any]:
    return _schema(
        "rag-oa112-reserve-registry-v1",
        _closed(
            required=[
                "activeGenerationReferences",
                "automaticPromotion",
                "maximumReserveSources",
                "reserveMetadataIncluded",
                "reserveSourceCount",
                "schemaVersion",
                "state",
            ],
            properties={
                "activeGenerationReferences": {"const": 0},
                "automaticPromotion": {"const": False},
                "maximumReserveSources": {"const": 28},
                "reserveMetadataIncluded": {"const": False},
                "reserveSourceCount": {"maximum": 28, "minimum": 0, "type": "integer"},
                "schemaVersion": {"const": 1},
                "state": {"const": "RESEARCH_ONLY_NON_ACTIVE"},
            },
        ),
    )


def _source_card_v4_schema() -> dict[str, Any]:
    permissions = _closed(
        required=list(REQUIRED_OA_PERMISSIONS),
        properties={name: {"type": "boolean"} for name in REQUIRED_OA_PERMISSIONS},
    )
    body = _closed(
        required=[
            "activeOa112Eligible",
            "authors",
            "canonicalUrl",
            "contractId",
            "licenseEvidenceDigest",
            "mimeType",
            "permissions",
            "rawContentSha256",
            "revision",
            "schemaVersion",
            "sourceId",
            "sourceKind",
            "title",
        ],
        properties={
            "activeOa112Eligible": {"type": "boolean"},
            "authors": {
                "items": _text(maximum=300),
                "maxItems": 50,
                "minItems": 1,
                "type": "array",
                "uniqueItems": True,
            },
            "canonicalUrl": {"format": "uri", "pattern": "^https://", "type": "string"},
            "contractId": {"const": "rag-source-card-v4"},
            "licenseEvidenceDigest": _digest(),
            "mimeType": {"enum": ["application/pdf", "text/html", "text/plain"]},
            "permissions": permissions,
            "rawContentSha256": _digest(),
            "revision": _text(maximum=128),
            "schemaVersion": {"const": 4},
            "sourceId": _opaque_id("^src_[a-z0-9][a-z0-9_-]{2,95}$"),
            "sourceKind": {"const": "OPEN_ACCESS_DOCUMENT"},
            "title": _text(maximum=500),
        },
    )
    body["allOf"] = [
        {
            "if": {"properties": {"activeOa112Eligible": {"const": True}}},
            "then": {
                "properties": {
                    "permissions": {
                        "properties": {
                            name: {"const": True} for name in REQUIRED_OA_PERMISSIONS
                        }
                    }
                }
            },
        }
    ]
    return _schema("rag-source-card-v4", body)


def _external_consent_schema() -> dict[str, Any]:
    return _schema(
        "s4-rag-v2-external-consent-v1",
        _closed(
            required=[
                "action",
                "consentType",
                "disclosureDigest",
                "policyDigest",
                "processorSetDigest",
                "schemaVersion",
            ],
            properties={
                "action": {"enum": ["GRANT", "REVOKE"]},
                "consentType": {"const": "EXTERNAL_AI_RAG_V2"},
                "disclosureDigest": _digest(),
                "policyDigest": _digest(),
                "processorSetDigest": _digest(),
                "schemaVersion": {"const": 1},
            },
        ),
    )


def _effective_consent_schema() -> dict[str, Any]:
    return _schema(
        "s4-rag-v2-effective-consent-v1",
        _closed(
            required=[
                "consentEventId",
                "effective",
                "policyDigest",
                "processorSetDigest",
                "schemaVersion",
                "state",
            ],
            properties={
                "consentEventId": _opaque_id("^rce_[A-Za-z0-9_-]{12,96}$"),
                "effective": {"type": "boolean"},
                "policyDigest": _digest(),
                "processorSetDigest": _digest(),
                "schemaVersion": {"const": 1},
                "state": {"enum": ["GRANTED", "NOT_GRANTED", "REVOKED"]},
            },
        ),
    )


def _import_ticket_request_schema() -> dict[str, Any]:
    return _schema(
        "s4-rag-v2-import-ticket-request-v1",
        _closed(
            required=["importMode", "schemaVersion", "sourceScope"],
            properties={
                "importMode": {"const": "LOCAL_EPHEMERAL_PARSE"},
                "schemaVersion": {"const": 1},
                "sourceScope": {"const": "OWNER_PRIVATE"},
            },
        ),
    )


def _import_ticket_schema() -> dict[str, Any]:
    return _schema(
        "s4-rag-v2-import-ticket-v1",
        _closed(
            required=[
                "expiresAt",
                "issuedAt",
                "ownerBound",
                "ownerRawCopyAllowed",
                "schemaVersion",
                "singleUse",
                "sourceScope",
                "ticketId",
                "ttlSeconds",
            ],
            properties={
                "expiresAt": _timestamp(),
                "issuedAt": _timestamp(),
                "ownerBound": {"const": True},
                "ownerRawCopyAllowed": {"const": False},
                "schemaVersion": {"const": 1},
                "singleUse": {"const": True},
                "sourceScope": {"const": "OWNER_PRIVATE"},
                "ticketId": _opaque_id(TICKET_ID_PATTERN),
                "ttlSeconds": {"const": 300},
            },
        ),
    )


def _status_activation_schema() -> dict[str, Any]:
    return _schema(
        "s4-rag-v2-status-activation-v1",
        _closed(
            required=[
                "documentId",
                "generationDisposition",
                "ownerRawCopies",
                "schemaVersion",
                "sourceScope",
                "state",
            ],
            properties={
                "documentId": _opaque_id(DOCUMENT_ID_PATTERN),
                "generationDisposition": {
                    "enum": [
                        "NOT_ACTIVE",
                        "GENERATION_ACTIVATED_THEN_HARD_DELETED",
                    ]
                },
                "ownerRawCopies": {"const": 0},
                "schemaVersion": {"const": 1},
                "sourceScope": {"const": "OWNER_PRIVATE"},
                "state": {
                    "enum": [
                        "QUEUED",
                        "PROCESSING",
                        "READY",
                        "FAILED",
                        "DELETED",
                    ]
                },
            },
        ),
    )


def _rag_policy_schema() -> dict[str, Any]:
    voyage = _closed(
        required=[
            "batchApiAllowed",
            "dimension",
            "filesApiAllowed",
            "generationFallback",
            "modelId",
            "queryUnitFallbackAllowed",
            "retryCount",
            "runtimeEnvironmentVariable",
        ],
        properties={
            "batchApiAllowed": {"const": False},
            "dimension": {"const": 1024},
            "filesApiAllowed": {"const": False},
            "generationFallback": {"const": "FULL_BUNDLE_REBUILD_EVALUATE_CAS"},
            "modelId": {"const": "voyage-context-4"},
            "queryUnitFallbackAllowed": {"const": False},
            "retryCount": {"const": 0},
            "runtimeEnvironmentVariable": {"const": "VOYAGE_API_KEY"},
        },
    )
    vertex = _closed(
        required=[
            "authentication",
            "developerApiAllowed",
            "fallbackAllowed",
            "maximumEvidenceCount",
            "maximumGenerateContentCallsPerQuestion",
            "modelId",
            "toolsFunctionsAllowed",
        ],
        properties={
            "authentication": {
                "prefixItems": [{"const": "ADC"}, {"const": "SERVICE_ACCOUNT"}],
                "items": False,
                "maxItems": 2,
                "minItems": 2,
                "type": "array",
            },
            "developerApiAllowed": {"const": False},
            "fallbackAllowed": {"const": False},
            "maximumEvidenceCount": {"const": 5},
            "maximumGenerateContentCallsPerQuestion": {"const": 1},
            "modelId": {"const": "gemini-3.5-flash"},
            "toolsFunctionsAllowed": {"const": False},
        },
    )
    return _schema(
        "s4-rag-v2-pre-s5-policy-v1",
        _closed(
            required=[
                "activeEmbeddingProfile",
                "decisionAuthority",
                "generationAuthority",
                "openAiCallsAllowed",
                "retrieval",
                "runtimeState",
                "schemaVersion",
                "vertex",
                "voyage",
            ],
            properties={
                "activeEmbeddingProfile": {
                    "enum": [
                        "NONE",
                        "voyage_context_4_1024_v1",
                        "bge_m3_local_1024_v1",
                    ]
                },
                "decisionAuthority": {"const": "NONE"},
                "generationAuthority": {"const": "EXPLANATION_ONLY"},
                "openAiCallsAllowed": {"const": False},
                "retrieval": _closed(
                    required=["channelTopK", "rrfK", "topK"],
                    properties={
                        "channelTopK": {"const": 30},
                        "rrfK": {"const": 60},
                        "topK": {"const": 5},
                    },
                ),
                "runtimeState": {"const": "CONTRACT_ONLY"},
                "schemaVersion": {"const": 1},
                "vertex": vertex,
                "voyage": voyage,
            },
        ),
    )


def _foreign_lane_entitlement_schema() -> dict[str, Any]:
    lane = _closed(
        required=[
            "articleMetadataStored",
            "credentialMode",
            "laneId",
            "mode",
            "providerCallsAllowed",
            "rawProviderDataStored",
            "sharedHostedKeyAllowed",
        ],
        properties={
            "articleMetadataStored": {"const": False},
            "credentialMode": {
                "enum": ["NONE", "OWNER_PERSONAL_LOCAL_ONLY", "OFFICIAL_ORIGIN_NO_KEY"]
            },
            "laneId": {"enum": list(FOREIGN_NEWS_LANES)},
            "mode": {
                "enum": [
                    "CONTRACT_ONLY",
                    "DECISION_PLATFORM_OFFLINE_REFERENCE_ONLY",
                ]
            },
            "providerCallsAllowed": {"const": False},
            "rawProviderDataStored": {"const": False},
            "sharedHostedKeyAllowed": {"const": False},
        },
    )
    return _schema(
        "foreign-news-lane-entitlement-v1",
        _closed(
            required=["lanes", "schemaVersion"],
            properties={
                "lanes": {
                    "items": lane,
                    "maxItems": 4,
                    "minItems": 4,
                    "type": "array",
                },
                "schemaVersion": {"const": 1},
            },
        ),
    )


def _foreign_news_sentiment_schema() -> dict[str, Any]:
    lane = _closed(
        required=["laneId", "state"],
        properties={
            "laneId": {"enum": list(FOREIGN_NEWS_LANES)},
            "state": {"enum": ["AVAILABLE", "ABSTAIN", "NOT_ACTIVATED"]},
        },
    )
    return _schema(
        "foreign-news-sentiment-v1",
        _closed(
            required=[
                "allowedUses",
                "articleMetadataStored",
                "asOf",
                "decisionAuthority",
                "lanes",
                "rawProviderDataStored",
                "riskDecisionHashIncluded",
                "s5FeatureEligible",
                "schemaVersion",
                "status",
                "symbol",
            ],
            properties={
                "allowedUses": {
                    "prefixItems": [{"const": "EXPLANATION_ONLY"}],
                    "items": False,
                    "maxItems": 1,
                    "minItems": 1,
                    "type": "array",
                },
                "articleMetadataStored": {"const": False},
                "asOf": _timestamp(),
                "decisionAuthority": {"const": "NONE"},
                "lanes": {
                    "items": lane,
                    "maxItems": 4,
                    "minItems": 4,
                    "type": "array",
                },
                "rawProviderDataStored": {"const": False},
                "riskDecisionHashIncluded": {"const": False},
                "s5FeatureEligible": {"const": False},
                "schemaVersion": {"const": 1},
                "status": {"enum": ["AVAILABLE", "ABSTAIN"]},
                "symbol": {"pattern": "^[0-9A-Z._:-]{1,20}$", "type": "string"},
            },
        ),
    )


def _foreign_news_model_selection_schema() -> dict[str, Any]:
    metrics = _closed(
        required=[
            "criticalNegationNumberUnitErrors",
            "ece",
            "macroF1",
            "minimumClassRecall",
            "neutralF1",
        ],
        properties={
            "criticalNegationNumberUnitErrors": {"minimum": 0, "type": "integer"},
            "ece": {"maximum": 1, "minimum": 0, "type": "number"},
            "macroF1": {"maximum": 1, "minimum": 0, "type": "number"},
            "minimumClassRecall": {"maximum": 1, "minimum": 0, "type": "number"},
            "neutralF1": {"maximum": 1, "minimum": 0, "type": "number"},
        },
    )
    return _schema(
        "foreign-news-model-selection-v1",
        _closed(
            required=[
                "candidateModels",
                "metrics",
                "schemaVersion",
                "selectedModel",
                "selectionStatus",
                "testEvaluationCount",
            ],
            properties={
                "candidateModels": {
                    "prefixItems": [{"const": value} for value in MODEL_CANDIDATES],
                    "items": False,
                    "maxItems": 3,
                    "minItems": 3,
                    "type": "array",
                },
                "metrics": metrics,
                "schemaVersion": {"const": 1},
                "selectedModel": {
                    "oneOf": [{"enum": list(MODEL_CANDIDATES)}, {"type": "null"}]
                },
                "selectionStatus": {
                    "enum": ["NOT_SELECTED", "SELECTED_PENDING_TEST", "TEST_EVALUATED", "ABSTAIN"]
                },
                "testEvaluationCount": {"maximum": 1, "minimum": 0, "type": "integer"},
            },
        ),
    )


def _optional3_entitlement_schema() -> dict[str, Any]:
    entry = _closed(
        required=[
            "activationStatus",
            "derivedDataStored",
            "providerCallsAllowed",
            "providerFamily",
            "rawProviderDataStored",
            "retryCount",
        ],
        properties={
            "activationStatus": {
                "enum": ["BLOCKED_NO_CREDENTIAL_OR_ENTITLEMENT", "CONTRACT_ONLY"]
            },
            "derivedDataStored": {"const": False},
            "providerCallsAllowed": {"const": False},
            "providerFamily": {"enum": list(OPTIONAL3_PROVIDERS)},
            "rawProviderDataStored": {"const": False},
            "retryCount": {"const": 0},
        },
    )
    return _schema(
        "s4-8-optional3-entitlement-v1",
        _closed(
            required=["entitlements", "schemaVersion"],
            properties={
                "entitlements": {
                    "items": entry,
                    "maxItems": 3,
                    "minItems": 3,
                    "type": "array",
                },
                "schemaVersion": {"const": 1},
            },
        ),
    )


def _optional3_probe_approval_schema() -> dict[str, Any]:
    return _schema(
        "s4-8-optional3-probe-approval-v1",
        _closed(
            required=[
                "approvalStatus",
                "artifactCap",
                "executionAllowed",
                "logicalCallCap",
                "physicalCallCap",
                "providerFamily",
                "retryCount",
                "schemaVersion",
            ],
            properties={
                "approvalStatus": {"const": "TEMPLATE"},
                "artifactCap": {"const": 0},
                "executionAllowed": {"const": False},
                "logicalCallCap": {"const": 0},
                "physicalCallCap": {"const": 0},
                "providerFamily": {"enum": list(OPTIONAL3_PROVIDERS)},
                "retryCount": {"const": 0},
                "schemaVersion": {"const": 1},
            },
        ),
    )


def _optional3_probe_receipt_schema() -> dict[str, Any]:
    return _schema(
        "s4-8-optional3-probe-receipt-v1",
        _closed(
            required=[
                "firstFailureStopsRemainingCalls",
                "physicalCallCount",
                "providerFamily",
                "rawProviderDataStored",
                "retryCount",
                "schemaVersion",
                "state",
            ],
            properties={
                "firstFailureStopsRemainingCalls": {"const": True},
                "physicalCallCount": {"const": 0},
                "providerFamily": {"enum": list(OPTIONAL3_PROVIDERS)},
                "rawProviderDataStored": {"const": False},
                "retryCount": {"const": 0},
                "schemaVersion": {"const": 1},
                "state": {"const": "NOT_EXECUTED"},
            },
        ),
    )


def _schemas() -> dict[str, dict[str, Any]]:
    return {
        "rag-oa112-logical-selection-v1": _oa_selection_schema(),
        "rag-oa112-reserve-registry-v1": _oa_reserve_schema(),
        "rag-source-card-v4": _source_card_v4_schema(),
        "s4-rag-v2-external-consent-v1": _external_consent_schema(),
        "s4-rag-v2-effective-consent-v1": _effective_consent_schema(),
        "s4-rag-v2-import-ticket-request-v1": _import_ticket_request_schema(),
        "s4-rag-v2-import-ticket-v1": _import_ticket_schema(),
        "s4-rag-v2-status-activation-v1": _status_activation_schema(),
        "s4-rag-v2-pre-s5-policy-v1": _rag_policy_schema(),
        "foreign-news-lane-entitlement-v1": _foreign_lane_entitlement_schema(),
        "foreign-news-sentiment-v1": _foreign_news_sentiment_schema(),
        "foreign-news-model-selection-v1": _foreign_news_model_selection_schema(),
        "s4-8-optional3-entitlement-v1": _optional3_entitlement_schema(),
        "s4-8-optional3-probe-approval-v1": _optional3_probe_approval_schema(),
        "s4-8-optional3-probe-receipt-v1": _optional3_probe_receipt_schema(),
    }


def _hash(value: str) -> str:
    return value * 64


def _oa_selection_fixture() -> dict[str, Any]:
    return {
        "activationRequirements": list(REQUIRED_OA_PERMISSIONS),
        "activationState": "CONTRACT_LOCKED_NOT_MATERIALIZED",
        "historicalManifestSha256": FROZEN_EXISTING_HASHES[
            "capstone-rag/manifests/s4-7d-oa140-release.v1.json"
        ],
        "materializationAllowed": False,
        "physicalActivation": "NOT_MATERIALIZED",
        "schemaVersion": 1,
        "sourceCount": 112,
        "sourceMetadataIncluded": False,
        "sourcesPerTrack": 8,
        "tracks": [{"trackId": track, "sourceCount": 8} for track in TRACK_IDS],
    }


def _oa_reserve_fixture() -> dict[str, Any]:
    return {
        "activeGenerationReferences": 0,
        "automaticPromotion": False,
        "maximumReserveSources": 28,
        "reserveMetadataIncluded": False,
        "reserveSourceCount": 0,
        "schemaVersion": 1,
        "state": "RESEARCH_ONLY_NON_ACTIVE",
    }


def _source_card_v4_fixture() -> dict[str, Any]:
    return {
        "activeOa112Eligible": True,
        "authors": ["Contract fixture author"],
        "canonicalUrl": "https://example.invalid/oa-contract-fixture",
        "contractId": "rag-source-card-v4",
        "licenseEvidenceDigest": _hash("b"),
        "mimeType": "application/pdf",
        "permissions": {name: True for name in REQUIRED_OA_PERMISSIONS},
        "rawContentSha256": _hash("a"),
        "revision": "contract-fixture-r1",
        "schemaVersion": 4,
        "sourceId": "src_oa_contract_fixture_001",
        "sourceKind": "OPEN_ACCESS_DOCUMENT",
        "title": "Synthetic contract fixture only",
    }


def _external_consent_fixture() -> dict[str, Any]:
    return {
        "action": "GRANT",
        "consentType": "EXTERNAL_AI_RAG_V2",
        "disclosureDigest": _hash("c"),
        "policyDigest": _hash("d"),
        "processorSetDigest": _hash("e"),
        "schemaVersion": 1,
    }


def _effective_consent_fixture() -> dict[str, Any]:
    consent = _external_consent_fixture()
    return {
        "consentEventId": "rce_01CONTRACTFIXTURE",
        "effective": True,
        "policyDigest": consent["policyDigest"],
        "processorSetDigest": consent["processorSetDigest"],
        "schemaVersion": 1,
        "state": "GRANTED",
    }


def _import_ticket_request_fixture() -> dict[str, Any]:
    return {
        "importMode": "LOCAL_EPHEMERAL_PARSE",
        "schemaVersion": 1,
        "sourceScope": "OWNER_PRIVATE",
    }


def _import_ticket_fixture() -> dict[str, Any]:
    return {
        "expiresAt": "2026-08-03T00:05:00Z",
        "issuedAt": "2026-08-03T00:00:00Z",
        "ownerBound": True,
        "ownerRawCopyAllowed": False,
        "schemaVersion": 1,
        "singleUse": True,
        "sourceScope": "OWNER_PRIVATE",
        "ticketId": "rti_01CONTRACTFIXTURE",
        "ttlSeconds": 300,
    }


def _status_activation_fixture() -> dict[str, Any]:
    return {
        "documentId": "doc_01contractfixture",
        "generationDisposition": "GENERATION_ACTIVATED_THEN_HARD_DELETED",
        "ownerRawCopies": 0,
        "schemaVersion": 1,
        "sourceScope": "OWNER_PRIVATE",
        "state": "DELETED",
    }


def _rag_policy_fixture() -> dict[str, Any]:
    return {
        "activeEmbeddingProfile": "NONE",
        "decisionAuthority": "NONE",
        "generationAuthority": "EXPLANATION_ONLY",
        "openAiCallsAllowed": False,
        "retrieval": {"channelTopK": 30, "rrfK": 60, "topK": 5},
        "runtimeState": "CONTRACT_ONLY",
        "schemaVersion": 1,
        "vertex": {
            "authentication": ["ADC", "SERVICE_ACCOUNT"],
            "developerApiAllowed": False,
            "fallbackAllowed": False,
            "maximumEvidenceCount": 5,
            "maximumGenerateContentCallsPerQuestion": 1,
            "modelId": "gemini-3.5-flash",
            "toolsFunctionsAllowed": False,
        },
        "voyage": {
            "batchApiAllowed": False,
            "dimension": 1024,
            "filesApiAllowed": False,
            "generationFallback": "FULL_BUNDLE_REBUILD_EVALUATE_CAS",
            "modelId": "voyage-context-4",
            "queryUnitFallbackAllowed": False,
            "retryCount": 0,
            "runtimeEnvironmentVariable": "VOYAGE_API_KEY",
        },
    }


def _foreign_lanes() -> list[dict[str, Any]]:
    return [
        {
            "articleMetadataStored": False,
            "credentialMode": "OWNER_PERSONAL_LOCAL_ONLY",
            "laneId": "FINNHUB_PERSONAL_LOCAL",
            "mode": "CONTRACT_ONLY",
            "providerCallsAllowed": False,
            "rawProviderDataStored": False,
            "sharedHostedKeyAllowed": False,
        },
        {
            "articleMetadataStored": False,
            "credentialMode": "OFFICIAL_ORIGIN_NO_KEY",
            "laneId": "SEC_OFFICIAL",
            "mode": "CONTRACT_ONLY",
            "providerCallsAllowed": False,
            "rawProviderDataStored": False,
            "sharedHostedKeyAllowed": False,
        },
        {
            "articleMetadataStored": False,
            "credentialMode": "OFFICIAL_ORIGIN_NO_KEY",
            "laneId": "FED_OFFICIAL",
            "mode": "CONTRACT_ONLY",
            "providerCallsAllowed": False,
            "rawProviderDataStored": False,
            "sharedHostedKeyAllowed": False,
        },
        {
            "articleMetadataStored": False,
            "credentialMode": "NONE",
            "laneId": "GDELT_OFFLINE_REFERENCE",
            "mode": "DECISION_PLATFORM_OFFLINE_REFERENCE_ONLY",
            "providerCallsAllowed": False,
            "rawProviderDataStored": False,
            "sharedHostedKeyAllowed": False,
        },
    ]


def _foreign_lane_entitlement_fixture() -> dict[str, Any]:
    return {"lanes": _foreign_lanes(), "schemaVersion": 1}


def _foreign_sentiment_fixture() -> dict[str, Any]:
    return {
        "allowedUses": ["EXPLANATION_ONLY"],
        "articleMetadataStored": False,
        "asOf": "2026-08-03T00:00:00Z",
        "decisionAuthority": "NONE",
        "lanes": [
            {"laneId": lane, "state": "NOT_ACTIVATED"} for lane in FOREIGN_NEWS_LANES
        ],
        "rawProviderDataStored": False,
        "riskDecisionHashIncluded": False,
        "s5FeatureEligible": False,
        "schemaVersion": 1,
        "status": "ABSTAIN",
        "symbol": "005930",
    }


def _model_selection_fixture() -> dict[str, Any]:
    return {
        "candidateModels": list(MODEL_CANDIDATES),
        "metrics": {
            "criticalNegationNumberUnitErrors": 0,
            "ece": 0,
            "macroF1": 0,
            "minimumClassRecall": 0,
            "neutralF1": 0,
        },
        "schemaVersion": 1,
        "selectedModel": None,
        "selectionStatus": "NOT_SELECTED",
        "testEvaluationCount": 0,
    }


def _optional3_entitlements() -> list[dict[str, Any]]:
    return [
        {
            "activationStatus": "CONTRACT_ONLY",
            "derivedDataStored": False,
            "providerCallsAllowed": False,
            "providerFamily": "FINNHUB_OPTIONAL3",
            "rawProviderDataStored": False,
            "retryCount": 0,
        },
        {
            "activationStatus": "BLOCKED_NO_CREDENTIAL_OR_ENTITLEMENT",
            "derivedDataStored": False,
            "providerCallsAllowed": False,
            "providerFamily": "TWELVE_DATA",
            "rawProviderDataStored": False,
            "retryCount": 0,
        },
        {
            "activationStatus": "BLOCKED_NO_CREDENTIAL_OR_ENTITLEMENT",
            "derivedDataStored": False,
            "providerCallsAllowed": False,
            "providerFamily": "MASSIVE",
            "rawProviderDataStored": False,
            "retryCount": 0,
        },
    ]


def _optional3_entitlement_fixture() -> dict[str, Any]:
    return {"entitlements": _optional3_entitlements(), "schemaVersion": 1}


def _optional3_approval_fixture() -> dict[str, Any]:
    return {
        "approvalStatus": "TEMPLATE",
        "artifactCap": 0,
        "executionAllowed": False,
        "logicalCallCap": 0,
        "physicalCallCap": 0,
        "providerFamily": "TWELVE_DATA",
        "retryCount": 0,
        "schemaVersion": 1,
    }


def _optional3_receipt_fixture() -> dict[str, Any]:
    return {
        "firstFailureStopsRemainingCalls": True,
        "physicalCallCount": 0,
        "providerFamily": "MASSIVE",
        "rawProviderDataStored": False,
        "retryCount": 0,
        "schemaVersion": 1,
        "state": "NOT_EXECUTED",
    }


def _catalog() -> dict[str, Any]:
    """기존 byte-stable artifacts 위에 현재 Pre-S5 정책만 addendum으로 고정한다."""

    return {
        "contractId": "pre-s5-rag-news-contract.v1",
        "decisionPlatformOwner": "DECISION_PLATFORM",
        "frozenCompatibility": {
            "historicalOa112ManifestSha256": FROZEN_EXISTING_HASHES[
                "capstone-rag/manifests/s4-7d-oa140-release.v1.json"
            ],
            "newsSentimentSummaryV2Sha256": FROZEN_EXISTING_HASHES[
                "contracts/schemas/news_sentiment_summary.v2.schema.json"
            ],
            "ragV2OpenApiSha256": FROZEN_EXISTING_HASHES[
                "contracts/openapi/rag-v2.openapi.json"
            ],
            "rootOpenApiSha256": FROZEN_EXISTING_HASHES[
                "contracts/openapi/openapi.json"
            ],
            "v1RagProtoSha256": FROZEN_EXISTING_HASHES["contracts/proto/rag.proto"],
        },
        "foreignNews": {
            "endpoint": "/api/v2/market-evidence/{symbol}/foreign-news-sentiment",
            "lanes": _foreign_lanes(),
            "modelSelection": {
                "candidates": list(MODEL_CANDIDATES),
                "criticalNegationNumberUnitErrorsMaximum": 0,
                "eceMaximum": 0.10,
                "macroF1Minimum": 0.80,
                "minimumClassRecall": 0.75,
                "neutralF1Minimum": 0.75,
                "selectionOrder": [
                    "VALIDATION_MACRO_F1_DESC",
                    "ECE_ASC",
                    "CPU_P95_ASC",
                    "FOOTPRINT_ASC",
                ],
                "testEvaluationAfterSelectionOnly": True,
                "testEvaluationMaximumCount": 1,
            },
            "responseInvariants": {
                "allowedUses": ["EXPLANATION_ONLY"],
                "articleMetadataStored": False,
                "decisionAuthority": "NONE",
                "rawProviderDataStored": False,
                "riskDecisionHashIncluded": False,
                "s5FeatureEligible": False,
            },
        },
        "oa112": {
            "physicalActivation": "NOT_MATERIALIZED",
            "requiredActivationPermissions": list(REQUIRED_OA_PERMISSIONS),
            "reserveAutomaticPromotion": False,
            "reserveMaximumSources": 28,
            "sourceCount": 112,
            "sourcesPerTrack": 8,
            "status": "OA112_ACTIVE_CONTRACT_LOCKED",
            "trackCount": 14,
        },
        "ragV2": {
            "inheritedSurface": {
                "paths": [
                    "/api/v2/rag/ask",
                    "/api/v2/rag/corpus-status",
                    "/api/v2/rag/history",
                    "/api/v2/rag/history/{answerId}",
                ],
                "ragV2OpenApiSha256": FROZEN_EXISTING_HASHES[
                    "contracts/openapi/rag-v2.openapi.json"
                ],
            },
            "vertex": {
                "authentication": ["ADC", "SERVICE_ACCOUNT"],
                "developerApiAllowed": False,
                "fallbackAllowed": False,
                "maximumEvidenceCount": 5,
                "maximumGenerateContentCallsPerQuestion": 1,
                "modelId": "gemini-3.5-flash",
                "toolsFunctionsAllowed": False,
            },
            "voyage": {
                "batchApiAllowed": False,
                "dimension": 1024,
                "fallback": "FULL_BUNDLE_REBUILD_EVALUATE_CAS",
                "filesApiAllowed": False,
                "modelId": "voyage-context-4",
                "queryUnitFallbackAllowed": False,
                "retryCount": 0,
                "runtimeEnvironmentVariable": "VOYAGE_API_KEY",
            },
        },
        "s48Optional3": {
            "entitlementState": "CONTRACT_ONLY",
            "providerCallsAllowed": 0,
            "providerFamilies": list(OPTIONAL3_PROVIDERS),
            "receiptExecutionAllowed": 0,
            "retryCount": 0,
        },
        "schemaVersion": 1,
        "status": "RAG_AND_GLOBAL_NEWS_CONTRACT_LOCKED",
        "trackedPayloads": {
            "embeddings": 0,
            "extractedText": 0,
            "ownerRawCopies": 0,
            "oaRaw": 0,
        },
    }


def _rag_openapi(schemas: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    bearer = {"bearerAuth": []}
    return {
        "components": {
            "schemas": {
                "RagV2EffectiveConsent": schemas["s4-rag-v2-effective-consent-v1"],
                "RagV2ExternalConsent": schemas["s4-rag-v2-external-consent-v1"],
                "RagV2ImportTicket": schemas["s4-rag-v2-import-ticket-v1"],
                "RagV2ImportTicketRequest": schemas[
                    "s4-rag-v2-import-ticket-request-v1"
                ],
            },
            "securitySchemes": {
                "bearerAuth": {"bearerFormat": "JWT", "scheme": "bearer", "type": "http"}
            },
        },
        "info": {"title": "Capstone RAG v2 Pre-S5 addendum", "version": "1.0.0-contract"},
        "openapi": "3.1.1",
        "paths": {
            "/api/v2/rag/consent": {
                "get": {
                    "operationId": "getRagV2EffectiveConsent",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/RagV2EffectiveConsent"
                                    }
                                }
                            },
                            "description": "Owner-scoped effective consent without identity data",
                        }
                    },
                    "security": [bearer],
                }
            },
            "/api/v2/rag/consents": {
                "post": {
                    "operationId": "appendRagV2ExternalConsent",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/RagV2ExternalConsent"}
                            }
                        },
                        "required": True,
                    },
                    "responses": {"204": {"description": "Append-only server-owned consent event"}},
                    "security": [bearer],
                }
            },
            "/api/v2/rag/import-tickets": {
                "post": {
                    "operationId": "issueRagV2ImportTicket",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/RagV2ImportTicketRequest"
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/RagV2ImportTicket"}
                                }
                            },
                            "description": "Five-minute single-use owner-bound local import ticket",
                        }
                    },
                    "security": [bearer],
                }
            },
        },
        "servers": [{"url": "/"}],
    }


def _foreign_news_openapi(schemas: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "components": {
            "schemas": {"ForeignNewsSentiment": schemas["foreign-news-sentiment-v1"]},
            "securitySchemes": {
                "bearerAuth": {"bearerFormat": "JWT", "scheme": "bearer", "type": "http"}
            },
        },
        "info": {
            "title": "Capstone foreign-news explanation-only contract",
            "version": "1.0.0-contract",
        },
        "openapi": "3.1.1",
        "paths": {
            "/api/v2/market-evidence/{symbol}/foreign-news-sentiment": {
                "get": {
                    "operationId": "getForeignNewsSentiment",
                    "parameters": [
                        {
                            "in": "path",
                            "name": "symbol",
                            "required": True,
                            "schema": {"pattern": "^[0-9A-Z._:-]{1,20}$", "type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ForeignNewsSentiment"
                                    }
                                }
                            },
                            "description": "Explanation-only aggregate without raw provider data",
                        }
                    },
                    "security": [{"bearerAuth": []}],
                }
            }
        },
        "servers": [{"url": "/"}],
    }


def _valid_fixtures() -> dict[str, dict[str, Any]]:
    return {
        "contracts/examples/rag-oa112-logical-selection-v1.valid.json": _oa_selection_fixture(),
        "contracts/examples/rag-oa112-reserve-registry-v1.valid.json": _oa_reserve_fixture(),
        "contracts/examples/rag-source-card-v4.valid.json": _source_card_v4_fixture(),
        "contracts/examples/s4-rag-v2-external-consent-v1.valid.json": _external_consent_fixture(),
        "contracts/examples/s4-rag-v2-effective-consent-v1.valid.json": _effective_consent_fixture(),
        "contracts/examples/s4-rag-v2-import-ticket-request-v1.valid.json": _import_ticket_request_fixture(),
        "contracts/examples/s4-rag-v2-import-ticket-v1.valid.json": _import_ticket_fixture(),
        "contracts/examples/s4-rag-v2-status-activation-v1.valid.json": _status_activation_fixture(),
        "contracts/examples/s4-rag-v2-pre-s5-policy-v1.valid.json": _rag_policy_fixture(),
        "contracts/examples/foreign-news-lane-entitlement-v1.valid.json": _foreign_lane_entitlement_fixture(),
        "contracts/examples/foreign-news-sentiment-v1.valid.json": _foreign_sentiment_fixture(),
        "contracts/examples/foreign-news-model-selection-v1.valid.json": _model_selection_fixture(),
        "contracts/examples/s4-8-optional3-entitlement-v1.valid.json": _optional3_entitlement_fixture(),
        "contracts/examples/s4-8-optional3-probe-approval-v1.valid.json": _optional3_approval_fixture(),
        "contracts/examples/s4-8-optional3-probe-receipt-v1.valid.json": _optional3_receipt_fixture(),
    }


def _invalid_fixtures(valid: Mapping[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    selection = copy.deepcopy(valid["contracts/examples/rag-oa112-logical-selection-v1.valid.json"])
    selection["tracks"][0]["sourceCount"] = 7

    reserve = copy.deepcopy(valid["contracts/examples/rag-oa112-reserve-registry-v1.valid.json"])
    reserve["automaticPromotion"] = True

    source = copy.deepcopy(valid["contracts/examples/rag-source-card-v4.valid.json"])
    source["permissions"]["externalGenerationAllowed"] = False

    consent = copy.deepcopy(valid["contracts/examples/s4-rag-v2-external-consent-v1.valid.json"])
    consent["actor"] = "client-controlled"

    ticket = copy.deepcopy(valid["contracts/examples/s4-rag-v2-import-ticket-v1.valid.json"])
    ticket["ttlSeconds"] = 301

    status = copy.deepcopy(valid["contracts/examples/s4-rag-v2-status-activation-v1.valid.json"])
    status["rawPath"] = "/home/example/private.pdf"

    policy = copy.deepcopy(valid["contracts/examples/s4-rag-v2-pre-s5-policy-v1.valid.json"])
    policy["voyage"]["queryUnitFallbackAllowed"] = True

    decision = copy.deepcopy(valid["contracts/examples/foreign-news-sentiment-v1.valid.json"])
    decision["decisionAuthority"] = "RISK"

    article = copy.deepcopy(valid["contracts/examples/foreign-news-sentiment-v1.valid.json"])
    article["articleTitle"] = "must never be stored"

    test_shopping = copy.deepcopy(
        valid["contracts/examples/foreign-news-model-selection-v1.valid.json"]
    )
    test_shopping["testEvaluationCount"] = 1

    entitlement = copy.deepcopy(valid["contracts/examples/s4-8-optional3-entitlement-v1.valid.json"])
    entitlement["entitlements"][0]["providerCallsAllowed"] = True

    approval = copy.deepcopy(valid["contracts/examples/s4-8-optional3-probe-approval-v1.valid.json"])
    approval["executionAllowed"] = True

    receipt = copy.deepcopy(valid["contracts/examples/s4-8-optional3-probe-receipt-v1.valid.json"])
    receipt["physicalCallCount"] = 1

    return {
        "contracts/examples/invalid/rag-oa112-logical-selection-v1.track-count.invalid.json": selection,
        "contracts/examples/invalid/rag-oa112-reserve-registry-v1.auto-promotion.invalid.json": reserve,
        "contracts/examples/invalid/rag-source-card-v4.permission.invalid.json": source,
        "contracts/examples/invalid/s4-rag-v2-external-consent-v1.actor.invalid.json": consent,
        "contracts/examples/invalid/s4-rag-v2-import-ticket-v1.ttl.invalid.json": ticket,
        "contracts/examples/invalid/s4-rag-v2-status-activation-v1.path.invalid.json": status,
        "contracts/examples/invalid/s4-rag-v2-pre-s5-policy-v1.query-fallback.invalid.json": policy,
        "contracts/examples/invalid/foreign-news-sentiment-v1.decision.invalid.json": decision,
        "contracts/examples/invalid/foreign-news-sentiment-v1.article.invalid.json": article,
        "contracts/examples/invalid/foreign-news-model-selection-v1.test-shopping.invalid.json": test_shopping,
        "contracts/examples/invalid/s4-8-optional3-entitlement-v1.call.invalid.json": entitlement,
        "contracts/examples/invalid/s4-8-optional3-probe-approval-v1.execution.invalid.json": approval,
        "contracts/examples/invalid/s4-8-optional3-probe-receipt-v1.call.invalid.json": receipt,
    }


def _parse_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ContractValidationError(f"{field} must be a timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractValidationError(f"{field} must be ISO-8601") from error
    if result.tzinfo is None:
        raise ContractValidationError(f"{field} must be timezone-aware")
    return result


def _require_object(schema_id: str, payload: object) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ContractValidationError(f"{schema_id} must be an object")
    return payload


def validate_semantics(schema_id: str, payload: object) -> None:
    """Schema만으로 표현하기 어려운 ownership, count, activation 불변식을 fail-closed한다."""

    value = _require_object(schema_id, payload)
    if schema_id == "rag-oa112-logical-selection-v1":
        tracks = value.get("tracks")
        if not isinstance(tracks, list):
            raise ContractValidationError("OA112 tracks are required")
        actual_tracks = tuple(
            track.get("trackId") for track in tracks if isinstance(track, Mapping)
        )
        if actual_tracks != TRACK_IDS:
            raise ContractValidationError("OA112 track order or membership drifted")
        if sum(track.get("sourceCount", 0) for track in tracks if isinstance(track, Mapping)) != 112:
            raise ContractValidationError("OA112 logical selection must contain exactly 112 sources")
        return
    if schema_id == "rag-oa112-reserve-registry-v1":
        if value.get("reserveSourceCount", 0) > 28:
            raise ContractValidationError("OA reserve cannot exceed 28 sources")
        return
    if schema_id == "rag-source-card-v4":
        permissions = value.get("permissions")
        if value.get("activeOa112Eligible") and (
            not isinstance(permissions, Mapping)
            or any(permissions.get(name) is not True for name in REQUIRED_OA_PERMISSIONS)
        ):
            raise ContractValidationError("active OA112 source requires all four permissions")
        return
    if schema_id == "s4-rag-v2-effective-consent-v1":
        state = value.get("state")
        if value.get("effective") != (state == "GRANTED"):
            raise ContractValidationError("effective consent must match the server-owned state")
        return
    if schema_id == "s4-rag-v2-import-ticket-v1":
        issued_at = _parse_timestamp(value.get("issuedAt"), field="issuedAt")
        expires_at = _parse_timestamp(value.get("expiresAt"), field="expiresAt")
        if (expires_at - issued_at).total_seconds() != 300:
            raise ContractValidationError("import ticket lifetime must be exactly five minutes")
        return
    if schema_id == "s4-rag-v2-status-activation-v1":
        if value.get("state") == "DELETED" and value.get("generationDisposition") != (
            "GENERATION_ACTIVATED_THEN_HARD_DELETED"
        ):
            raise ContractValidationError("owner deletion requires activation before hard delete")
        return
    if schema_id == "s4-rag-v2-pre-s5-policy-v1":
        voyage = value.get("voyage")
        vertex = value.get("vertex")
        if not isinstance(voyage, Mapping) or not isinstance(vertex, Mapping):
            raise ContractValidationError("RAG provider policy is required")
        if voyage.get("queryUnitFallbackAllowed") or voyage.get("generationFallback") != (
            "FULL_BUNDLE_REBUILD_EVALUATE_CAS"
        ):
            raise ContractValidationError("Voyage fallback must rebuild the full bundle")
        if vertex.get("maximumGenerateContentCallsPerQuestion") != 1:
            raise ContractValidationError("Vertex may generate at most once per question")
        return
    if schema_id == "foreign-news-lane-entitlement-v1":
        lanes = value.get("lanes")
        if not isinstance(lanes, list) or tuple(
            lane.get("laneId") for lane in lanes if isinstance(lane, Mapping)
        ) != FOREIGN_NEWS_LANES:
            raise ContractValidationError("foreign-news lane set or order drifted")
        return
    if schema_id == "foreign-news-sentiment-v1":
        lanes = value.get("lanes")
        if not isinstance(lanes, list) or tuple(
            lane.get("laneId") for lane in lanes if isinstance(lane, Mapping)
        ) != FOREIGN_NEWS_LANES:
            raise ContractValidationError("foreign-news response must cover exactly four lanes")
        return
    if schema_id == "foreign-news-model-selection-v1":
        status = value.get("selectionStatus")
        selected = value.get("selectedModel")
        test_count = value.get("testEvaluationCount")
        metrics = value.get("metrics")
        if status == "NOT_SELECTED" and (selected is not None or test_count != 0):
            raise ContractValidationError("model cannot use test data before validation selection")
        if test_count and status != "TEST_EVALUATED":
            raise ContractValidationError("test evaluation requires a prior selected model")
        if status in {"SELECTED_PENDING_TEST", "TEST_EVALUATED"}:
            if selected not in MODEL_CANDIDATES or not isinstance(metrics, Mapping):
                raise ContractValidationError("selected model must be a benchmark candidate")
            if (
                metrics.get("macroF1", 0) < 0.80
                or metrics.get("minimumClassRecall", 0) < 0.75
                or metrics.get("neutralF1", 0) < 0.75
                or metrics.get("ece", 1) > 0.10
                or metrics.get("criticalNegationNumberUnitErrors") != 0
            ):
                raise ContractValidationError("selected foreign-news model misses validation gate")
        return
    if schema_id == "s4-8-optional3-entitlement-v1":
        entries = value.get("entitlements")
        if not isinstance(entries, list) or tuple(
            entry.get("providerFamily") for entry in entries if isinstance(entry, Mapping)
        ) != OPTIONAL3_PROVIDERS:
            raise ContractValidationError("Optional 3 provider set or order drifted")
        return
    if schema_id in {
        "s4-8-optional3-probe-approval-v1",
        "s4-8-optional3-probe-receipt-v1",
    }:
        return
    if schema_id not in SCHEMA_IDS:
        raise ContractValidationError(f"unsupported Pre-S5 schema: {schema_id}")


def _fixture_schema_id(relative_path: str) -> str:
    return Path(relative_path).name.split(".", maxsplit=1)[0]


def _validate_generated(
    schemas: Mapping[str, dict[str, Any]],
    valid: Mapping[str, dict[str, Any]],
    invalid: Mapping[str, dict[str, Any]],
) -> None:
    validators = {
        schema_id: Draft202012Validator(schema, format_checker=FormatChecker())
        for schema_id, schema in schemas.items()
    }
    for relative_path, payload in valid.items():
        schema_id = _fixture_schema_id(relative_path)
        errors = list(validators[schema_id].iter_errors(payload))
        if errors:
            raise ContractValidationError(
                f"generated valid fixture failed {relative_path}: {errors[0].message}"
            )
        validate_semantics(schema_id, payload)
    for relative_path, payload in invalid.items():
        schema_id = _fixture_schema_id(relative_path)
        errors = list(validators[schema_id].iter_errors(payload))
        if errors:
            continue
        try:
            validate_semantics(schema_id, payload)
        except ContractValidationError:
            continue
        raise ContractValidationError(f"generated invalid fixture unexpectedly passed: {relative_path}")


def build_artifacts() -> dict[str, dict[str, Any]]:
    schemas = _schemas()
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
    valid = _valid_fixtures()
    invalid = _invalid_fixtures(valid)
    if frozenset(valid) != VALID_FIXTURE_PATHS:
        raise ContractValidationError("Pre-S5 valid fixture path set drifted")
    if frozenset(invalid) != INVALID_FIXTURE_PATHS:
        raise ContractValidationError("Pre-S5 invalid fixture path set drifted")
    _validate_generated(schemas, valid, invalid)
    catalog = _catalog()
    catalog_bytes = canonical_json_bytes(catalog)
    catalog_hash = hashlib.sha256(catalog_bytes).hexdigest()
    artifacts: dict[str, dict[str, Any]] = {
        CATALOG_PATH: catalog,
        CATALOG_HASH_PATH: {
            "catalogPath": CATALOG_PATH,
            "schemaVersion": 1,
            "sha256": catalog_hash,
        },
        FOREIGN_NEWS_OPENAPI_PATH: _foreign_news_openapi(schemas),
        RAG_OPENAPI_PATH: _rag_openapi(schemas),
        **{SCHEMA_PATHS[schema_id]: schema for schema_id, schema in schemas.items()},
        **valid,
        **invalid,
    }
    return artifacts


ARTIFACT_PATHS: Final[frozenset[str]] = frozenset(build_artifacts())


def _verify_frozen_existing_files() -> None:
    for relative_path, expected_hash in FROZEN_EXISTING_HASHES.items():
        path = ROOT / relative_path
        if not path.is_file() or path.is_symlink():
            raise ContractValidationError(
                f"frozen Pre-S5 input is unavailable: {relative_path}"
            )
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise ContractValidationError(
                f"frozen Pre-S5 input drifted: {relative_path}"
            )


def generate_outputs() -> dict[str, bytes]:
    """생성 전 기존 SSOT와 historical bytes를 확인해 addendum의 무단 재해석을 막는다."""

    _verify_frozen_existing_files()
    return {
        relative_path: canonical_json_bytes(payload)
        for relative_path, payload in build_artifacts().items()
    }


def _write_outputs(outputs: Mapping[str, bytes]) -> None:
    for relative_path, payload in sorted(outputs.items()):
        write_generated_artifact(ROOT, relative_path, payload)


def _check_outputs(outputs: Mapping[str, bytes]) -> None:
    drifted = [
        relative_path
        for relative_path, expected in sorted(outputs.items())
        if not (ROOT / relative_path).is_file()
        or (ROOT / relative_path).is_symlink()
        or (ROOT / relative_path).read_bytes() != expected
    ]
    if drifted:
        raise ContractValidationError(
            "generated Pre-S5 RAG/news artifacts drifted:\n"
            + "\n".join(f"- {relative_path}" for relative_path in drifted)
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        outputs = generate_outputs()
        if arguments.write:
            _write_outputs(outputs)
        else:
            _check_outputs(outputs)
    except (ContractValidationError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print("S4_7D_OA112_CONTRACT_LOCK_VERIFIED")
    print("GLOBAL_FOREIGN_NEWS_OPTIONAL3_CONTRACT_LOCK_VERIFIED")
    print("RAG_AND_GLOBAL_NEWS_CONTRACT_LOCKED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
