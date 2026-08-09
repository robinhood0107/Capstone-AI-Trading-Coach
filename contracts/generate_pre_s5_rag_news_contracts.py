from __future__ import annotations

import argparse
import copy
import hashlib
import os
import re
import stat
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Collection, Final, Mapping

from jsonschema import Draft202012Validator, FormatChecker


_SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from contracts.generated_artifact_io import write_generated_artifact  # noqa: E402
from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    canonical_json_bytes,
    load_json_bytes_strict,
)
from contracts.generate_rag_source_card_v2_contracts import (  # noqa: E402
    _validate_canonical_url,
)


ROOT = _SCRIPT_REPO_ROOT
HASH_PATTERN: Final[str] = "^[0-9a-f]{64}$"
OPAQUE_ID_PATTERN: Final[str] = "^[a-z][a-z0-9_-]{2,95}$"
DOCUMENT_ID_PATTERN: Final[str] = "^doc_[a-z0-9][a-z0-9_-]{10,95}$"
TICKET_ID_PATTERN: Final[str] = "^rti_[A-Za-z0-9_-]{12,96}$"
DELETE_TICKET_ID_PATTERN: Final[str] = "^rtd_[A-Za-z0-9_-]{12,96}$"
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
FOREIGN_NEWS_LANE_CONTRACTS: Final[dict[str, dict[str, Any]]] = {
    "FINNHUB_PERSONAL_LOCAL": {
        "allowedRetainedFields": ["OWNER_LOCAL_DERIVED_AGGREGATE"],
        "attachmentAllowed": False,
        "boundedTransientParseOnly": False,
        "credentialMode": "OWNER_PERSONAL_LOCAL_ONLY",
        "derivedCacheDeletionOnExpiryRequired": True,
        "endpointAllowance": "MARKET_AND_COMPANY_NEWS_ONLY",
        "externalEntityAllowed": False,
        "fixedOriginBinding": "FINNHUB_FIXED_API_HOST",
        "mode": "CONTRACT_ONLY",
        "ownerLocalDerivedOnly": True,
    },
    "SEC_OFFICIAL": {
        "allowedRetainedFields": [
            "CONTENT_HASH",
            "DERIVED_AGGREGATE",
            "OFFICIAL_RELEASE_LOCATOR",
        ],
        "attachmentAllowed": False,
        "boundedTransientParseOnly": True,
        "credentialMode": "OFFICIAL_ORIGIN_NO_KEY",
        "derivedCacheDeletionOnExpiryRequired": False,
        "endpointAllowance": "OFFICIAL_RELEASES_ONLY",
        "externalEntityAllowed": False,
        "fixedOriginBinding": "SEC_GOV_OFFICIAL",
        "mode": "CONTRACT_ONLY",
        "ownerLocalDerivedOnly": False,
    },
    "FED_OFFICIAL": {
        "allowedRetainedFields": [
            "CONTENT_HASH",
            "DERIVED_AGGREGATE",
            "OFFICIAL_RELEASE_LOCATOR",
        ],
        "attachmentAllowed": False,
        "boundedTransientParseOnly": True,
        "credentialMode": "OFFICIAL_ORIGIN_NO_KEY",
        "derivedCacheDeletionOnExpiryRequired": False,
        "endpointAllowance": "OFFICIAL_RELEASES_ONLY",
        "externalEntityAllowed": False,
        "fixedOriginBinding": "FEDERAL_RESERVE_GOV_OFFICIAL",
        "mode": "CONTRACT_ONLY",
        "ownerLocalDerivedOnly": False,
    },
    "GDELT_OFFLINE_REFERENCE": {
        "allowedRetainedFields": ["OFFLINE_AGGREGATE_REFERENCE"],
        "attachmentAllowed": False,
        "boundedTransientParseOnly": False,
        "credentialMode": "NONE",
        "derivedCacheDeletionOnExpiryRequired": False,
        "endpointAllowance": "NONE",
        "externalEntityAllowed": False,
        "fixedOriginBinding": "NONE",
        "mode": "DECISION_PLATFORM_OFFLINE_REFERENCE_ONLY",
        "ownerLocalDerivedOnly": False,
    },
}

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
    "contracts/proto/rag_v2.descriptor.pb": (
        "0fee0d5a7f44dab752f750a17697c474" "cfc6992756f0dc00dad6c3779bb413ba"
    ),
    "contracts/proto/rag_v2.descriptor.sha256": (
        "193076c57ed42d28a24e3aa29df064e8" "957a55454774d087f13720a0eeaa2dd6"
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
    "capstone-rag/manifests/s4-7d-oa140-checksums.sha256": (
        "698e455069593816fed05bfce7da74a5" "4a3ffd4579c8eb4931a9fbd3051e1abc"
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
    "s4-rag-v2-delete-ticket-request-v1",
    "s4-rag-v2-delete-ticket-v1",
    "s4-rag-v2-vertex-preparation-v1",
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
    [
        *(f"contracts/examples/{schema_id}.valid.json" for schema_id in SCHEMA_IDS),
        "contracts/examples/s4-rag-v2-status-activation-v1.ready.valid.json",
    ]
)
INVALID_FIXTURE_PATHS: Final[frozenset[str]] = frozenset(
    {
        "contracts/examples/invalid/rag-oa112-logical-selection-v1.track-count.invalid.json",
        "contracts/examples/invalid/rag-oa112-reserve-registry-v1.auto-promotion.invalid.json",
        "contracts/examples/invalid/rag-source-card-v4.permission.invalid.json",
        "contracts/examples/invalid/rag-source-card-v4.url.invalid.json",
        "contracts/examples/invalid/s4-rag-v2-external-consent-v1.actor.invalid.json",
        "contracts/examples/invalid/s4-rag-v2-import-ticket-v1.ttl.invalid.json",
        "contracts/examples/invalid/s4-rag-v2-delete-ticket-v1.ttl.invalid.json",
        "contracts/examples/invalid/s4-rag-v2-vertex-preparation-v1.raw-question.invalid.json",
        "contracts/examples/invalid/s4-rag-v2-status-activation-v1.deletion.invalid.json",
        "contracts/examples/invalid/s4-rag-v2-status-activation-v1.path.invalid.json",
        "contracts/examples/invalid/s4-rag-v2-pre-s5-policy-v1.byte-approximation.invalid.json",
        "contracts/examples/invalid/s4-rag-v2-pre-s5-policy-v1.query-fallback.invalid.json",
        "contracts/examples/invalid/foreign-news-lane-entitlement-v1.gdelt.invalid.json",
        "contracts/examples/invalid/foreign-news-sentiment-v1.activation.invalid.json",
        "contracts/examples/invalid/foreign-news-sentiment-v1.status-lane.invalid.json",
        "contracts/examples/invalid/foreign-news-sentiment-v1.decision.invalid.json",
        "contracts/examples/invalid/foreign-news-sentiment-v1.article.invalid.json",
        "contracts/examples/invalid/foreign-news-model-selection-v1.test-state.invalid.json",
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
    properties = value.get("properties")
    required = value.get("required")
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise ContractValidationError("Pre-S5 schema body must be a closed object.")
    existing_contract_id = properties.get("contractId")
    expected_contract_id = {"const": schema_id}
    if existing_contract_id is not None and existing_contract_id != expected_contract_id:
        raise ContractValidationError(
            f"Pre-S5 schema {schema_id} has a conflicting contractId field."
        )
    properties["contractId"] = expected_contract_id
    if "contractId" not in required:
        required.insert(0, "contractId")
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


def _exact_object_membership(
    item_schema: Mapping[str, Any], *, discriminator: str, values: tuple[str, ...]
) -> dict[str, Any]:
    """ID별 중복·누락을 schema 단계에서 막고 순서는 semantic validator가 고정한다."""

    return {
        "allOf": [
            {
                "contains": {
                    "properties": {discriminator: {"const": value}},
                    "required": [discriminator],
                    "type": "object",
                },
                "maxContains": 1,
                "minContains": 1,
            }
            for value in values
        ],
        "items": dict(item_schema),
        "maxItems": len(values),
        "minItems": len(values),
        "type": "array",
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
                "historicalManifestReference",
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
                "historicalManifestReference": {
                    "const": "S4_7D_OA140_RELEASE_V1_FROZEN"
                },
                "materializationAllowed": {"const": False},
                "physicalActivation": {"const": "NOT_MATERIALIZED"},
                "schemaVersion": {"const": 1},
                "sourceCount": {"const": 112},
                "sourceMetadataIncluded": {"const": False},
                "sourcesPerTrack": {"const": 8},
                "tracks": _exact_object_membership(
                    track, discriminator="trackId", values=TRACK_IDS
                ),
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
            "accessEvidence",
            "activeOa112Eligible",
            "authors",
            "canonicalUrl",
            "canonicalUrlSha256",
            "contractId",
            "identifier",
            "licenseEvidenceDigest",
            "mimeType",
            "permissions",
            "rawContentSha256",
            "revision",
            "revisionDate",
            "schemaVersion",
            "sourceId",
            "sourceKind",
            "title",
        ],
        properties={
            "accessEvidence": _closed(
                required=[
                    "accessCheckedAt",
                    "accessEvidenceDigest",
                    "verificationState",
                ],
                properties={
                    "accessCheckedAt": _timestamp(),
                    "accessEvidenceDigest": _digest(),
                    "verificationState": {"const": "VERIFIED"},
                },
            ),
            "activeOa112Eligible": {"type": "boolean"},
            "authors": {
                "items": _text(maximum=300),
                "maxItems": 50,
                "minItems": 1,
                "type": "array",
                "uniqueItems": True,
            },
            "canonicalUrl": {"format": "uri", "pattern": "^https://", "type": "string"},
            "canonicalUrlSha256": _digest(),
            "contractId": {"const": "rag-source-card-v4"},
            "identifier": _closed(
                required=["scheme", "value"],
                properties={
                    "scheme": {"enum": ["DOI", "ISBN", "ARXIV"]},
                    "value": _text(maximum=256),
                },
            ),
            "licenseEvidenceDigest": _digest(),
            "mimeType": {"enum": ["application/pdf", "text/html", "text/plain"]},
            "permissions": permissions,
            "rawContentSha256": _digest(),
            "revision": _text(maximum=128),
            "revisionDate": {"format": "date", "type": "string"},
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


def _delete_ticket_request_schema() -> dict[str, Any]:
    """owner-local delete capability에는 document selector만 받고 actor/path는 server-owned로 둔다."""

    return _schema(
        "s4-rag-v2-delete-ticket-request-v1",
        _closed(
            required=["documentId", "schemaVersion", "sourceScope"],
            properties={
                "documentId": _opaque_id(DOCUMENT_ID_PATTERN),
                "schemaVersion": {"const": 1},
                "sourceScope": {"const": "OWNER_PRIVATE"},
            },
        ),
    )


def _delete_ticket_schema() -> dict[str, Any]:
    """삭제 ticket은 owner·document bind와 five-minute single-use를 response에도 고정한다."""

    return _schema(
        "s4-rag-v2-delete-ticket-v1",
        _closed(
            required=[
                "documentBound",
                "documentId",
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
                "documentBound": {"const": True},
                "documentId": _opaque_id(DOCUMENT_ID_PATTERN),
                "expiresAt": _timestamp(),
                "issuedAt": _timestamp(),
                "ownerBound": {"const": True},
                "ownerRawCopyAllowed": {"const": False},
                "schemaVersion": {"const": 1},
                "singleUse": {"const": True},
                "sourceScope": {"const": "OWNER_PRIVATE"},
                "ticketId": _opaque_id(DELETE_TICKET_ID_PATTERN),
                "ttlSeconds": {"const": 300},
            },
        ),
    )


def _vertex_preparation_schema() -> dict[str, Any]:
    """Vertex packet preparation은 raw question/evidence 없이 stable two-minute scope만 반환한다."""

    return _schema(
        "s4-rag-v2-vertex-preparation-v1",
        _closed(
            required=[
                "answerMode",
                "consentEventId",
                "embeddingProfileId",
                "expiresAt",
                "policyDigest",
                "processorSetDigest",
                "questionFingerprintHmac",
                "rawEvidenceStored",
                "rawQuestionStored",
                "requestId",
                "schemaVersion",
                "scopeClaimId",
                "scopeTtlSeconds",
            ],
            properties={
                "answerMode": {"enum": ["CONCISE", "DETAILED"]},
                "consentEventId": _opaque_id("^rce_[A-Za-z0-9_-]{12,96}$"),
                "embeddingProfileId": {
                    "enum": ["bge_m3_local_1024_v1", "voyage_context_4_1024_v1"]
                },
                "expiresAt": _timestamp(),
                "policyDigest": _digest(),
                "processorSetDigest": _digest(),
                "questionFingerprintHmac": _digest(),
                "rawEvidenceStored": {"const": False},
                "rawQuestionStored": {"const": False},
                "requestId": _opaque_id("^req_[A-Za-z0-9_-]{12,96}$"),
                "schemaVersion": {"const": 1},
                "scopeClaimId": _opaque_id("^rvs_[0-9a-f]{32}$"),
                "scopeTtlSeconds": {"const": 120},
            },
        ),
    )


def _status_activation_schema() -> dict[str, Any]:
    hard_deleted_artifacts = _exact_string_array(
        ("DOCUMENT_IR", "CANONICAL_TEXT", "CHUNK", "VECTOR")
    )
    body = _closed(
        required=[
            "documentId",
            "generationDisposition",
            "hardDeletedArtifactClasses",
            "ownerDeleteHardDeleteVerified",
            "ownerRawCopies",
            "replacementGenerationActivated",
            "replacementGenerationReceiptId",
            "schemaVersion",
            "sourceScope",
            "state",
        ],
        properties={
            "documentId": _opaque_id(DOCUMENT_ID_PATTERN),
            "generationDisposition": {
                "enum": [
                    "NOT_ACTIVE",
                    "GENERATION_ACTIVE",
                    "GENERATION_ACTIVATED_THEN_HARD_DELETED",
                ]
            },
            "hardDeletedArtifactClasses": {
                "items": {
                    "enum": ["DOCUMENT_IR", "CANONICAL_TEXT", "CHUNK", "VECTOR"]
                },
                "maxItems": 4,
                "type": "array",
            },
            "ownerDeleteHardDeleteVerified": {"type": "boolean"},
            "ownerRawCopies": {"const": 0},
            "replacementGenerationActivated": {"type": "boolean"},
            "replacementGenerationReceiptId": {
                "oneOf": [
                    {"pattern": "^rgr_[A-Za-z0-9_-]{12,96}$", "type": "string"},
                    {"type": "null"},
                ]
            },
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
    )
    # 삭제 receipt는 실제 owner hard-delete 때만 존재한다. READY 상태가 삭제된
    # IR/text/chunk/vector를 주장하지 않도록 API shape는 유지하되 빈/false/null로 고정한다.
    body["allOf"] = [
        {
            "if": {"properties": {"state": {"const": "DELETED"}}, "required": ["state"]},
            "then": {
                "properties": {
                    "generationDisposition": {
                        "const": "GENERATION_ACTIVATED_THEN_HARD_DELETED"
                    },
                    "hardDeletedArtifactClasses": hard_deleted_artifacts,
                    "ownerDeleteHardDeleteVerified": {"const": True},
                    "replacementGenerationActivated": {"const": True},
                    "replacementGenerationReceiptId": {
                        "pattern": "^rgr_[A-Za-z0-9_-]{12,96}$",
                        "type": "string",
                    },
                }
            },
            "else": {
                "properties": {
                    "hardDeletedArtifactClasses": {"maxItems": 0},
                    "ownerDeleteHardDeleteVerified": {"const": False},
                    "replacementGenerationActivated": {"const": False},
                    "replacementGenerationReceiptId": {"const": None},
                }
            },
        },
        {
            "if": {"properties": {"state": {"const": "READY"}}, "required": ["state"]},
            "then": {
                "properties": {"generationDisposition": {"const": "GENERATION_ACTIVE"}}
            },
        },
        {
            "if": {
                "properties": {
                    "state": {"enum": ["QUEUED", "PROCESSING", "FAILED"]}
                },
                "required": ["state"],
            },
            "then": {"properties": {"generationDisposition": {"const": "NOT_ACTIVE"}}},
        },
    ]
    return _schema("s4-rag-v2-status-activation-v1", body)


def _rag_policy_schema() -> dict[str, Any]:
    voyage = _closed(
        required=[
            "activationEvidenceRequired",
            "batchApiAllowed",
            "dimension",
            "filesApiAllowed",
            "fullBundleScope",
            "generationFallback",
            "modelId",
            "officialTokenizer",
            "ownerPrivateSentinel",
            "outboundCallsAllowed",
            "orderedPrechunkedDocumentGroupsRequired",
            "partialProfileMixAllowed",
            "queryUnitFallbackAllowed",
            "retryCount",
            "runtimeEnvironmentVariable",
        ],
        properties={
            "activationEvidenceRequired": {
                "prefixItems": [
                    {"const": "ORGANIZATION_TRAINING_OPT_OUT"},
                    {"const": "PAYMENT_METHOD_PRIVACY_EVIDENCE"},
                ],
                "items": False,
                "maxItems": 2,
                "minItems": 2,
                "type": "array",
            },
            "batchApiAllowed": {"const": False},
            "dimension": {"const": 1024},
            "filesApiAllowed": {"const": False},
            "fullBundleScope": {
                "prefixItems": [
                    {"const": "EXACT30"},
                    {"const": "OA112"},
                    {"const": "OWNER_PRIVATE"},
                ],
                "items": False,
                "maxItems": 3,
                "minItems": 3,
                "type": "array",
            },
            "ownerPrivateSentinel": _closed(
                required=[
                    "allowed",
                    "orderedGroupCount",
                    "ownerScopeSha256",
                    "publicBaseOnly",
                    "sourceScope",
                ],
                properties={
                    "allowed": {"const": True},
                    "orderedGroupCount": {"const": 0},
                    "ownerScopeSha256": {"const": None},
                    "publicBaseOnly": {"const": True},
                    "sourceScope": {"const": "OWNER_PRIVATE"},
                },
            ),
            "generationFallback": {"const": "FULL_BUNDLE_REBUILD_EVALUATE_CAS"},
            "modelId": {"const": "voyage-context-4"},
            "officialTokenizer": _closed(
                required=[
                    "artifactAutoDownloadAllowed",
                    "localArtifactOnly",
                    "packetHashBindingRequired",
                    "preflightExpectedInputTokenLedgerRequired",
                    "providerTokenCountCallAllowed",
                    "utf8ByteApproximationAllowed",
                ],
                properties={
                    "artifactAutoDownloadAllowed": {"const": False},
                    "localArtifactOnly": {"const": True},
                    "packetHashBindingRequired": {"const": True},
                    "preflightExpectedInputTokenLedgerRequired": {"const": True},
                    "providerTokenCountCallAllowed": {"const": False},
                    "utf8ByteApproximationAllowed": {"const": False},
                },
            ),
            "outboundCallsAllowed": {"const": False},
            "orderedPrechunkedDocumentGroupsRequired": {"const": True},
            "partialProfileMixAllowed": {"const": False},
            "queryUnitFallbackAllowed": {"const": False},
            "retryCount": {"const": 0},
            "runtimeEnvironmentVariable": {"const": "VOYAGE_API_KEY"},
        },
    )
    vertex = _closed(
        required=[
            "activationEvidenceRequired",
            "authentication",
            "boundedJsonCredentialRequired",
            "contextCacheAllowed",
            "credentialFileRequirements",
            "developerApiAllowed",
            "fallbackAllowed",
            "fileUploadAllowed",
            "generationMethod",
            "generationUnavailableStatus",
            "maximumEvidenceCount",
            "maximumGenerateContentCallsPerQuestion",
            "modelId",
            "openAiCallsAllowed",
            "preparedScopeControlPlane",
            "rawResponseStored",
            "rerankerAllowed",
            "retryCount",
            "sanitizedUsageLedgerOnly",
            "searchMapsGroundingAllowed",
            "sessionResumptionAllowed",
            "toolsFunctionsAllowed",
            "verifierAllowed",
        ],
        properties={
            "activationEvidenceRequired": {
                "prefixItems": [
                    {"const": "CREDENTIAL_FILE_SECURITY"},
                    {"const": "PROJECT_CACHE_STATE"},
                    {"const": "ABUSE_MONITORING_STATE"},
                    {"const": "MODEL_AVAILABILITY"},
                ],
                "items": False,
                "maxItems": 4,
                "minItems": 4,
                "type": "array",
            },
            "authentication": {
                "prefixItems": [{"const": "ADC"}, {"const": "SERVICE_ACCOUNT"}],
                "items": False,
                "maxItems": 2,
                "minItems": 2,
                "type": "array",
            },
            "boundedJsonCredentialRequired": {"const": True},
            "contextCacheAllowed": {"const": False},
            "credentialFileRequirements": _closed(
                required=[
                    "credentialFileMode",
                    "credentialRootMode",
                    "linkCount",
                    "ownerMatchRequired",
                    "regularFileRequired",
                ],
                properties={
                    "credentialFileMode": {"const": "0600"},
                    "credentialRootMode": {"const": "0700"},
                    "linkCount": {"const": 1},
                    "ownerMatchRequired": {"const": True},
                    "regularFileRequired": {"const": True},
                },
            ),
            "developerApiAllowed": {"const": False},
            "fallbackAllowed": {"const": False},
            "fileUploadAllowed": {"const": False},
            "generationMethod": {"const": "generateContent"},
            "generationUnavailableStatus": {"const": "GENERATION_UNAVAILABLE"},
            "maximumEvidenceCount": {"const": 5},
            "maximumGenerateContentCallsPerQuestion": {"const": 1},
            "modelId": {"const": "gemini-3.5-flash"},
            "openAiCallsAllowed": {"const": False},
            "preparedScopeControlPlane": _closed(
                required=[
                    "headerName",
                    "packetOwnerIdentityStored",
                    "rawEvidenceStored",
                    "rawQuestionStored",
                    "sameParsedAskCommandFingerprintBound",
                    "sameRequestIdRequired",
                    "scopeTtlSeconds",
                ],
                properties={
                    "headerName": {"const": "X-Rag-V2-Vertex-Scope-Claim"},
                    "packetOwnerIdentityStored": {"const": False},
                    "rawEvidenceStored": {"const": False},
                    "rawQuestionStored": {"const": False},
                    "sameParsedAskCommandFingerprintBound": {"const": True},
                    "sameRequestIdRequired": {"const": True},
                    "scopeTtlSeconds": {"const": 120},
                },
            ),
            "rawResponseStored": {"const": False},
            "rerankerAllowed": {"const": False},
            "retryCount": {"const": 0},
            "sanitizedUsageLedgerOnly": {"const": True},
            "searchMapsGroundingAllowed": {"const": False},
            "sessionResumptionAllowed": {"const": False},
            "toolsFunctionsAllowed": {"const": False},
            "verifierAllowed": {"const": False},
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
    def lane_shape(lane_id: str, policy: Mapping[str, Any]) -> dict[str, Any]:
        required = [
            "allowedRetainedFields",
            "articleMetadataStored",
            "attachmentAllowed",
            "boundedTransientParseOnly",
            "credentialMode",
            "credentialStored",
            "derivedCacheDeletionOnExpiryRequired",
            "endpointAllowance",
            "externalEntityAllowed",
            "fixedOriginBinding",
            "headlineSummaryBodyStored",
            "laneId",
            "mode",
            "ownerLocalDerivedOnly",
            "providerCallsAllowed",
            "queryOrHeaderStored",
            "rawForwardedToVertex",
            "rawProviderDataStored",
            "redirectAllowed",
            "sharedHostedKeyAllowed",
        ]
        properties: dict[str, Any] = {
            "allowedRetainedFields": {
                "prefixItems": [
                    {"const": value} for value in policy["allowedRetainedFields"]
                ],
                "items": False,
                "maxItems": len(policy["allowedRetainedFields"]),
                "minItems": len(policy["allowedRetainedFields"]),
                "type": "array",
            },
            "articleMetadataStored": {"const": False},
            "attachmentAllowed": {"const": policy["attachmentAllowed"]},
            "boundedTransientParseOnly": {
                "const": policy["boundedTransientParseOnly"]
            },
            "credentialMode": {"const": policy["credentialMode"]},
            "credentialStored": {"const": False},
            "derivedCacheDeletionOnExpiryRequired": {
                "const": policy["derivedCacheDeletionOnExpiryRequired"]
            },
            "endpointAllowance": {"const": policy["endpointAllowance"]},
            "externalEntityAllowed": {"const": policy["externalEntityAllowed"]},
            "fixedOriginBinding": {"const": policy["fixedOriginBinding"]},
            "headlineSummaryBodyStored": {"const": False},
            "laneId": {"const": lane_id},
            "mode": {"const": policy["mode"]},
            "ownerLocalDerivedOnly": {"const": policy["ownerLocalDerivedOnly"]},
            "providerCallsAllowed": {"const": False},
            "queryOrHeaderStored": {"const": False},
            "rawForwardedToVertex": {"const": False},
            "rawProviderDataStored": {"const": False},
            "redirectAllowed": {"const": False},
            "sharedHostedKeyAllowed": {"const": False},
        }
        if lane_id == "GDELT_OFFLINE_REFERENCE":
            required.extend(
                ["gdeltAdapterAdded", "gdeltExecutorAdded", "gdeltOutboundCalls"]
            )
            properties.update(
                {
                    "gdeltAdapterAdded": {"const": False},
                    "gdeltExecutorAdded": {"const": False},
                    "gdeltOutboundCalls": {"const": 0},
                }
            )
        return _closed(required=required, properties=properties)

    lane = {
        "oneOf": [
            lane_shape(lane_id, FOREIGN_NEWS_LANE_CONTRACTS[lane_id])
            for lane_id in FOREIGN_NEWS_LANES
        ]
    }
    return _schema(
        "foreign-news-lane-entitlement-v1",
        _closed(
            required=["lanes", "schemaVersion"],
            properties={
                "lanes": _exact_object_membership(
                    lane, discriminator="laneId", values=FOREIGN_NEWS_LANES
                ),
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
    body = _closed(
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
                "lanes": _exact_object_membership(
                    lane, discriminator="laneId", values=FOREIGN_NEWS_LANES
                ),
                "rawProviderDataStored": {"const": False},
                "riskDecisionHashIncluded": {"const": False},
                "s5FeatureEligible": {"const": False},
                "schemaVersion": {"const": 1},
                "status": {"enum": ["AVAILABLE", "ABSTAIN"]},
                "symbol": {"pattern": "^[0-9A-Z._:-]{1,20}$", "type": "string"},
            },
        )
    body["allOf"] = [
        {
            "if": {"properties": {"status": {"const": "AVAILABLE"}}},
            "then": {
                "properties": {
                    "lanes": {
                        "contains": {
                            "properties": {"state": {"const": "AVAILABLE"}},
                            "required": ["state"],
                            "type": "object",
                        }
                    }
                }
            },
            "else": {
                "properties": {
                    "lanes": {
                        "not": {
                            "contains": {
                                "properties": {"state": {"const": "AVAILABLE"}},
                                "required": ["state"],
                                "type": "object",
                            }
                        }
                    }
                }
            },
        }
    ]
    return _schema("foreign-news-sentiment-v1", body)


def _foreign_news_model_selection_schema() -> dict[str, Any]:
    class_recalls = _closed(
        required=["NEGATIVE", "NEUTRAL", "POSITIVE"],
        properties={
            "NEGATIVE": {"maximum": 1, "minimum": 0, "type": "number"},
            "NEUTRAL": {"maximum": 1, "minimum": 0, "type": "number"},
            "POSITIVE": {"maximum": 1, "minimum": 0, "type": "number"},
        },
    )
    metrics = _closed(
        required=[
            "classRecalls",
            "cpuP95Millis",
            "criticalNegationNumberUnitErrors",
            "ece",
            "footprintBytes",
            "macroF1",
            "neutralF1",
        ],
        properties={
            "classRecalls": class_recalls,
            "cpuP95Millis": {"minimum": 0, "type": "number"},
            "criticalNegationNumberUnitErrors": {"minimum": 0, "type": "integer"},
            "ece": {"maximum": 1, "minimum": 0, "type": "number"},
            "footprintBytes": {"minimum": 0, "type": "integer"},
            "macroF1": {"maximum": 1, "minimum": 0, "type": "number"},
            "neutralF1": {"maximum": 1, "minimum": 0, "type": "number"},
        },
    )
    validation_result = _closed(
        required=["candidateModel", "metrics"],
        properties={
            "candidateModel": {"enum": list(MODEL_CANDIDATES)},
            "metrics": metrics,
        },
    )
    return _schema(
        "foreign-news-model-selection-v1",
        _closed(
            required=[
                "abstainReason",
                "candidateModels",
                "schemaVersion",
                "selectionGeneration",
                "selectionId",
                "selectedModel",
                "selectionStatus",
                "testEvaluationCount",
                "testOutcome",
                "testTargetModel",
                "validationCompleted",
                "validationResults",
            ],
            properties={
                "abstainReason": {
                    "oneOf": [
                        {
                            "enum": [
                                "NO_MODEL_MEETS_VALIDATION_GATE",
                                "TIE_AFTER_FOOTPRINT",
                                "TEST_FAILED",
                            ]
                        },
                        {"type": "null"},
                    ]
                },
                "candidateModels": {
                    "prefixItems": [{"const": value} for value in MODEL_CANDIDATES],
                    "items": False,
                    "maxItems": 3,
                    "minItems": 3,
                    "type": "array",
                },
                "schemaVersion": {"const": 1},
                "selectionGeneration": {"minimum": 1, "type": "integer"},
                "selectionId": _opaque_id("^fns_[A-Za-z0-9_-]{12,96}$"),
                "selectedModel": {
                    "oneOf": [{"enum": list(MODEL_CANDIDATES)}, {"type": "null"}]
                },
                "selectionStatus": {
                    "enum": ["NOT_SELECTED", "SELECTED_PENDING_TEST", "TEST_EVALUATED", "ABSTAIN"]
                },
                "testEvaluationCount": {"maximum": 1, "minimum": 0, "type": "integer"},
                "testOutcome": {"enum": ["NOT_RUN", "PASSED", "FAILED"]},
                "testTargetModel": {
                    "oneOf": [{"enum": list(MODEL_CANDIDATES)}, {"type": "null"}]
                },
                "validationCompleted": {"type": "boolean"},
                "validationResults": _exact_object_membership(
                    validation_result,
                    discriminator="candidateModel",
                    values=MODEL_CANDIDATES,
                ),
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
                "entitlements": _exact_object_membership(
                    entry,
                    discriminator="providerFamily",
                    values=OPTIONAL3_PROVIDERS,
                ),
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
        "s4-rag-v2-delete-ticket-request-v1": _delete_ticket_request_schema(),
        "s4-rag-v2-delete-ticket-v1": _delete_ticket_schema(),
        "s4-rag-v2-vertex-preparation-v1": _vertex_preparation_schema(),
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
        "historicalManifestReference": "S4_7D_OA140_RELEASE_V1_FROZEN",
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
        "accessEvidence": {
            "accessCheckedAt": "2026-08-03T00:00:00Z",
            "accessEvidenceDigest": _hash("f"),
            "verificationState": "VERIFIED",
        },
        "activeOa112Eligible": True,
        "authors": ["Contract fixture author"],
        "canonicalUrl": "https://example.invalid/oa-contract-fixture",
        "canonicalUrlSha256": hashlib.sha256(
            b"https://example.invalid/oa-contract-fixture"
        ).hexdigest(),
        "contractId": "rag-source-card-v4",
        "identifier": {"scheme": "DOI", "value": "10.0000/contract-fixture"},
        "licenseEvidenceDigest": _hash("b"),
        "mimeType": "application/pdf",
        "permissions": {name: True for name in REQUIRED_OA_PERMISSIONS},
        "rawContentSha256": _hash("a"),
        "revision": "contract-fixture-r1",
        "revisionDate": "2026-08-03",
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


def _delete_ticket_request_fixture() -> dict[str, Any]:
    return {
        "documentId": "doc_01contractfixture",
        "schemaVersion": 1,
        "sourceScope": "OWNER_PRIVATE",
    }


def _delete_ticket_fixture() -> dict[str, Any]:
    return {
        "documentBound": True,
        "documentId": "doc_01contractfixture",
        "expiresAt": "2026-08-03T00:05:00Z",
        "issuedAt": "2026-08-03T00:00:00Z",
        "ownerBound": True,
        "ownerRawCopyAllowed": False,
        "schemaVersion": 1,
        "singleUse": True,
        "sourceScope": "OWNER_PRIVATE",
        "ticketId": "rtd_01CONTRACTFIXTURE",
        "ttlSeconds": 300,
    }


def _vertex_preparation_fixture() -> dict[str, Any]:
    consent = _effective_consent_fixture()
    return {
        "answerMode": "CONCISE",
        "consentEventId": consent["consentEventId"],
        "embeddingProfileId": "voyage_context_4_1024_v1",
        "expiresAt": "2026-08-03T00:02:00Z",
        "policyDigest": consent["policyDigest"],
        "processorSetDigest": consent["processorSetDigest"],
        "questionFingerprintHmac": _hash("a"),
        "rawEvidenceStored": False,
        "rawQuestionStored": False,
        "requestId": "req_vertexcontractfixture001",
        "schemaVersion": 1,
        "scopeClaimId": "rvs_0123456789abcdef0123456789abcdef",
        "scopeTtlSeconds": 120,
    }


def _status_activation_fixture() -> dict[str, Any]:
    return {
        "documentId": "doc_01contractfixture",
        "generationDisposition": "GENERATION_ACTIVATED_THEN_HARD_DELETED",
        "hardDeletedArtifactClasses": [
            "DOCUMENT_IR",
            "CANONICAL_TEXT",
            "CHUNK",
            "VECTOR",
        ],
        "ownerDeleteHardDeleteVerified": True,
        "ownerRawCopies": 0,
        "replacementGenerationActivated": True,
        "replacementGenerationReceiptId": "rgr_01CONTRACTFIXTURE",
        "schemaVersion": 1,
        "sourceScope": "OWNER_PRIVATE",
        "state": "DELETED",
    }


def _status_activation_ready_fixture() -> dict[str, Any]:
    return {
        "documentId": "doc_01contractfixture",
        "generationDisposition": "GENERATION_ACTIVE",
        "hardDeletedArtifactClasses": [],
        "ownerDeleteHardDeleteVerified": False,
        "ownerRawCopies": 0,
        "replacementGenerationActivated": False,
        "replacementGenerationReceiptId": None,
        "schemaVersion": 1,
        "sourceScope": "OWNER_PRIVATE",
        "state": "READY",
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
            "activationEvidenceRequired": [
                "CREDENTIAL_FILE_SECURITY",
                "PROJECT_CACHE_STATE",
                "ABUSE_MONITORING_STATE",
                "MODEL_AVAILABILITY",
            ],
            "authentication": ["ADC", "SERVICE_ACCOUNT"],
            "boundedJsonCredentialRequired": True,
            "contextCacheAllowed": False,
            "credentialFileRequirements": {
                "credentialFileMode": "0600",
                "credentialRootMode": "0700",
                "linkCount": 1,
                "ownerMatchRequired": True,
                "regularFileRequired": True,
            },
            "developerApiAllowed": False,
            "fallbackAllowed": False,
            "fileUploadAllowed": False,
            "generationMethod": "generateContent",
            "generationUnavailableStatus": "GENERATION_UNAVAILABLE",
            "maximumEvidenceCount": 5,
            "maximumGenerateContentCallsPerQuestion": 1,
            "modelId": "gemini-3.5-flash",
            "openAiCallsAllowed": False,
            "preparedScopeControlPlane": {
                "headerName": "X-Rag-V2-Vertex-Scope-Claim",
                "packetOwnerIdentityStored": False,
                "rawEvidenceStored": False,
                "rawQuestionStored": False,
                "sameParsedAskCommandFingerprintBound": True,
                "sameRequestIdRequired": True,
                "scopeTtlSeconds": 120,
            },
            "rawResponseStored": False,
            "rerankerAllowed": False,
            "retryCount": 0,
            "sanitizedUsageLedgerOnly": True,
            "searchMapsGroundingAllowed": False,
            "sessionResumptionAllowed": False,
            "toolsFunctionsAllowed": False,
            "verifierAllowed": False,
        },
        "voyage": {
            "activationEvidenceRequired": [
                "ORGANIZATION_TRAINING_OPT_OUT",
                "PAYMENT_METHOD_PRIVACY_EVIDENCE",
            ],
            "batchApiAllowed": False,
            "dimension": 1024,
            "filesApiAllowed": False,
            "fullBundleScope": ["EXACT30", "OA112", "OWNER_PRIVATE"],
            "ownerPrivateSentinel": {
                "allowed": True,
                "orderedGroupCount": 0,
                "ownerScopeSha256": None,
                "publicBaseOnly": True,
                "sourceScope": "OWNER_PRIVATE",
            },
            "generationFallback": "FULL_BUNDLE_REBUILD_EVALUATE_CAS",
            "modelId": "voyage-context-4",
            "officialTokenizer": {
                "artifactAutoDownloadAllowed": False,
                "localArtifactOnly": True,
                "packetHashBindingRequired": True,
                "preflightExpectedInputTokenLedgerRequired": True,
                "providerTokenCountCallAllowed": False,
                "utf8ByteApproximationAllowed": False,
            },
            "outboundCallsAllowed": False,
            "orderedPrechunkedDocumentGroupsRequired": True,
            "partialProfileMixAllowed": False,
            "queryUnitFallbackAllowed": False,
            "retryCount": 0,
            "runtimeEnvironmentVariable": "VOYAGE_API_KEY",
        },
    }


def _foreign_lanes() -> list[dict[str, Any]]:
    lanes: list[dict[str, Any]] = []
    for lane_id in FOREIGN_NEWS_LANES:
        policy = FOREIGN_NEWS_LANE_CONTRACTS[lane_id]
        lane: dict[str, Any] = {
            "allowedRetainedFields": policy["allowedRetainedFields"],
            "articleMetadataStored": False,
            "attachmentAllowed": policy["attachmentAllowed"],
            "boundedTransientParseOnly": policy["boundedTransientParseOnly"],
            "credentialMode": policy["credentialMode"],
            "credentialStored": False,
            "derivedCacheDeletionOnExpiryRequired": policy[
                "derivedCacheDeletionOnExpiryRequired"
            ],
            "endpointAllowance": policy["endpointAllowance"],
            "externalEntityAllowed": policy["externalEntityAllowed"],
            "fixedOriginBinding": policy["fixedOriginBinding"],
            "headlineSummaryBodyStored": False,
            "laneId": lane_id,
            "mode": policy["mode"],
            "ownerLocalDerivedOnly": policy["ownerLocalDerivedOnly"],
            "providerCallsAllowed": False,
            "queryOrHeaderStored": False,
            "rawForwardedToVertex": False,
            "rawProviderDataStored": False,
            "redirectAllowed": False,
            "sharedHostedKeyAllowed": False,
        }
        if lane_id == "GDELT_OFFLINE_REFERENCE":
            lane.update(
                {
                    "gdeltAdapterAdded": False,
                    "gdeltExecutorAdded": False,
                    "gdeltOutboundCalls": 0,
                }
            )
        lanes.append(lane)
    return lanes


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
        "abstainReason": None,
        "candidateModels": list(MODEL_CANDIDATES),
        "schemaVersion": 1,
        "selectionGeneration": 1,
        "selectionId": "fns_01CONTRACTFIXTURE",
        "selectedModel": None,
        "selectionStatus": "NOT_SELECTED",
        "testEvaluationCount": 0,
        "testOutcome": "NOT_RUN",
        "testTargetModel": None,
        "validationCompleted": False,
        "validationResults": [
            {
                "candidateModel": candidate,
                "metrics": {
                    "classRecalls": {"NEGATIVE": 0, "NEUTRAL": 0, "POSITIVE": 0},
                    "cpuP95Millis": 0,
                    "criticalNegationNumberUnitErrors": 0,
                    "ece": 0,
                    "footprintBytes": 0,
                    "macroF1": 0,
                    "neutralF1": 0,
                },
            }
            for candidate in MODEL_CANDIDATES
        ],
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
            "generatorVerifiedInputs": list(FROZEN_EXISTING_HASHES),
            "verificationState": "EXACT_BYTES_VERIFIED_BY_GENERATOR",
        },
        "foreignNews": {
            "endpoint": "/api/v2/market-evidence/{symbol}/foreign-news-sentiment",
            "lanes": _foreign_lanes(),
            "modelSelection": {
                "candidates": list(MODEL_CANDIDATES),
                "criticalNegationNumberUnitErrorsMaximum": 0,
                "eceMaximum": 0.10,
                "macroF1Minimum": 0.80,
                "perClassRecallMinimum": 0.75,
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
            "ownerDeleteTicket": {
                "documentBound": True,
                "ownerBound": True,
                "ownerRawCopyAllowed": False,
                "singleUse": True,
                "sourceScope": "OWNER_PRIVATE",
                "ttlSeconds": 300,
            },
            "inheritedSurface": {
                "legacyBytesBinding": "RAG_V2_OPENAPI_FROZEN",
                "paths": [
                    "/api/v2/rag/ask",
                    "/api/v2/rag/corpus-status",
                    "/api/v2/rag/history",
                    "/api/v2/rag/history/{answerId}",
                ],
            },
            "vertex": {
                "activationEvidenceRequired": [
                    "CREDENTIAL_FILE_SECURITY",
                    "PROJECT_CACHE_STATE",
                    "ABUSE_MONITORING_STATE",
                    "MODEL_AVAILABILITY",
                ],
                "authentication": ["ADC", "SERVICE_ACCOUNT"],
                "boundedJsonCredentialRequired": True,
                "contextCacheAllowed": False,
                "credentialFileRequirements": {
                    "credentialFileMode": "0600",
                    "credentialRootMode": "0700",
                    "linkCount": 1,
                    "ownerMatchRequired": True,
                    "regularFileRequired": True,
                },
                "developerApiAllowed": False,
                "fallbackAllowed": False,
                "fileUploadAllowed": False,
                "generationMethod": "generateContent",
                "generationUnavailableStatus": "GENERATION_UNAVAILABLE",
                "maximumEvidenceCount": 5,
                "maximumGenerateContentCallsPerQuestion": 1,
                "modelId": "gemini-3.5-flash",
                "openAiCallsAllowed": False,
                "preparedScopeControlPlane": {
                    "headerName": "X-Rag-V2-Vertex-Scope-Claim",
                    "packetOwnerIdentityStored": False,
                    "rawEvidenceStored": False,
                    "rawQuestionStored": False,
                    "sameParsedAskCommandFingerprintBound": True,
                    "sameRequestIdRequired": True,
                    "scopeTtlSeconds": 120,
                },
                "rawResponseStored": False,
                "rerankerAllowed": False,
                "retryCount": 0,
                "sanitizedUsageLedgerOnly": True,
                "searchMapsGroundingAllowed": False,
                "sessionResumptionAllowed": False,
                "toolsFunctionsAllowed": False,
                "verifierAllowed": False,
            },
            "voyage": {
                "activationEvidenceRequired": [
                    "ORGANIZATION_TRAINING_OPT_OUT",
                    "PAYMENT_METHOD_PRIVACY_EVIDENCE",
                ],
                "batchApiAllowed": False,
                "dimension": 1024,
                "generationFallback": "FULL_BUNDLE_REBUILD_EVALUATE_CAS",
                "filesApiAllowed": False,
                "fullBundleScope": ["EXACT30", "OA112", "OWNER_PRIVATE"],
                "ownerPrivateSentinel": {
                    "allowed": True,
                    "orderedGroupCount": 0,
                    "ownerScopeSha256": None,
                    "publicBaseOnly": True,
                    "sourceScope": "OWNER_PRIVATE",
                },
                "modelId": "voyage-context-4",
                "officialTokenizer": {
                    "artifactAutoDownloadAllowed": False,
                    "localArtifactOnly": True,
                    "packetHashBindingRequired": True,
                    "preflightExpectedInputTokenLedgerRequired": True,
                    "providerTokenCountCallAllowed": False,
                    "utf8ByteApproximationAllowed": False,
                },
                "outboundCallsAllowed": False,
                "orderedPrechunkedDocumentGroupsRequired": True,
                "partialProfileMixAllowed": False,
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
                "RagV2DeleteTicket": schemas["s4-rag-v2-delete-ticket-v1"],
                "RagV2DeleteTicketRequest": schemas[
                    "s4-rag-v2-delete-ticket-request-v1"
                ],
                "RagV2VertexPreparation": schemas[
                    "s4-rag-v2-vertex-preparation-v1"
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
            "/api/v2/rag/delete-tickets": {
                "post": {
                    "operationId": "issueRagV2DeleteTicket",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/RagV2DeleteTicketRequest"
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/RagV2DeleteTicket"}
                                }
                            },
                            "description": "Five-minute single-use owner-and-document-bound local delete ticket",
                        }
                    },
                    "security": [bearer],
                }
            },
            "/api/v2/rag/vertex-preparations": {
                "post": {
                    "operationId": "prepareRagV2VertexGeneration",
                    "parameters": [
                        {
                            "description": "Stable request ID reused verbatim for the subsequent Vertex ask.",
                            "in": "header",
                            "name": "X-Request-Id",
                            "required": True,
                            "schema": {"pattern": "^req_[A-Za-z0-9_-]{12,96}$", "type": "string"},
                        }
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "rag-v2.openapi.json#/components/schemas/RagV2AskRequest"
                                }
                            }
                        },
                        "required": True,
                    },
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/RagV2VertexPreparation"}
                                }
                            },
                            "description": "Content-free two-minute scope preparation for one Vertex packet",
                        },
                        "409": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "rag-v2.openapi.json#/components/schemas/RagV2Error"
                                    }
                                }
                            },
                            "description": "RAG corpus or consent is not currently usable",
                        },
                        "503": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "rag-v2.openapi.json#/components/schemas/RagV2Error"
                                    }
                                }
                            },
                            "description": "Vertex activation is unavailable before any provider call",
                        },
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
        "contracts/examples/s4-rag-v2-delete-ticket-request-v1.valid.json": _delete_ticket_request_fixture(),
        "contracts/examples/s4-rag-v2-delete-ticket-v1.valid.json": _delete_ticket_fixture(),
        "contracts/examples/s4-rag-v2-vertex-preparation-v1.valid.json": _vertex_preparation_fixture(),
        "contracts/examples/s4-rag-v2-status-activation-v1.valid.json": _status_activation_fixture(),
        "contracts/examples/s4-rag-v2-status-activation-v1.ready.valid.json": _status_activation_ready_fixture(),
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

    unsafe_source_url = copy.deepcopy(source)
    unsafe_source_url["permissions"]["externalGenerationAllowed"] = True
    unsafe_source_url["canonicalUrl"] = "https://127.0.0.1/internal.pdf"

    consent = copy.deepcopy(valid["contracts/examples/s4-rag-v2-external-consent-v1.valid.json"])
    consent["actor"] = "client-controlled"

    ticket = copy.deepcopy(valid["contracts/examples/s4-rag-v2-import-ticket-v1.valid.json"])
    ticket["ttlSeconds"] = 301

    delete_ticket = copy.deepcopy(valid["contracts/examples/s4-rag-v2-delete-ticket-v1.valid.json"])
    delete_ticket["ttlSeconds"] = 301

    vertex_preparation = copy.deepcopy(
        valid["contracts/examples/s4-rag-v2-vertex-preparation-v1.valid.json"]
    )
    vertex_preparation["rawQuestionStored"] = True

    status = copy.deepcopy(valid["contracts/examples/s4-rag-v2-status-activation-v1.valid.json"])
    status["rawPath"] = "/home/example/private.pdf"

    status_state = copy.deepcopy(valid["contracts/examples/s4-rag-v2-status-activation-v1.valid.json"])
    status_state["state"] = "READY"

    policy = copy.deepcopy(valid["contracts/examples/s4-rag-v2-pre-s5-policy-v1.valid.json"])
    policy["voyage"]["queryUnitFallbackAllowed"] = True

    tokenizer_policy = copy.deepcopy(
        valid["contracts/examples/s4-rag-v2-pre-s5-policy-v1.valid.json"]
    )
    tokenizer_policy["voyage"]["officialTokenizer"]["utf8ByteApproximationAllowed"] = True

    decision = copy.deepcopy(valid["contracts/examples/foreign-news-sentiment-v1.valid.json"])
    decision["decisionAuthority"] = "RISK"

    unavailable = copy.deepcopy(valid["contracts/examples/foreign-news-sentiment-v1.valid.json"])
    unavailable["status"] = "AVAILABLE"

    contradictory_status = copy.deepcopy(
        valid["contracts/examples/foreign-news-sentiment-v1.valid.json"]
    )
    contradictory_status["lanes"][0]["state"] = "AVAILABLE"

    article = copy.deepcopy(valid["contracts/examples/foreign-news-sentiment-v1.valid.json"])
    article["articleTitle"] = "must never be stored"

    test_shopping = copy.deepcopy(
        valid["contracts/examples/foreign-news-model-selection-v1.valid.json"]
    )
    test_shopping["testEvaluationCount"] = 1

    invalid_test_state = copy.deepcopy(
        valid["contracts/examples/foreign-news-model-selection-v1.valid.json"]
    )
    invalid_test_state["selectionStatus"] = "TEST_EVALUATED"
    invalid_test_state["testOutcome"] = "PASSED"

    gdelt_lane = copy.deepcopy(
        valid["contracts/examples/foreign-news-lane-entitlement-v1.valid.json"]
    )
    gdelt_lane["lanes"][-1]["mode"] = "CONTRACT_ONLY"

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
        "contracts/examples/invalid/rag-source-card-v4.url.invalid.json": unsafe_source_url,
        "contracts/examples/invalid/s4-rag-v2-external-consent-v1.actor.invalid.json": consent,
        "contracts/examples/invalid/s4-rag-v2-import-ticket-v1.ttl.invalid.json": ticket,
        "contracts/examples/invalid/s4-rag-v2-delete-ticket-v1.ttl.invalid.json": delete_ticket,
        "contracts/examples/invalid/s4-rag-v2-vertex-preparation-v1.raw-question.invalid.json": vertex_preparation,
        "contracts/examples/invalid/s4-rag-v2-status-activation-v1.path.invalid.json": status,
        "contracts/examples/invalid/s4-rag-v2-status-activation-v1.deletion.invalid.json": status_state,
        "contracts/examples/invalid/s4-rag-v2-pre-s5-policy-v1.query-fallback.invalid.json": policy,
        "contracts/examples/invalid/s4-rag-v2-pre-s5-policy-v1.byte-approximation.invalid.json": tokenizer_policy,
        "contracts/examples/invalid/foreign-news-sentiment-v1.decision.invalid.json": decision,
        "contracts/examples/invalid/foreign-news-sentiment-v1.activation.invalid.json": unavailable,
        "contracts/examples/invalid/foreign-news-sentiment-v1.status-lane.invalid.json": contradictory_status,
        "contracts/examples/invalid/foreign-news-sentiment-v1.article.invalid.json": article,
        "contracts/examples/invalid/foreign-news-lane-entitlement-v1.gdelt.invalid.json": gdelt_lane,
        "contracts/examples/invalid/foreign-news-model-selection-v1.test-state.invalid.json": invalid_test_state,
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
        canonical_url = value.get("canonicalUrl")
        if not isinstance(canonical_url, str):
            raise ContractValidationError("source card v4 public HTTPS URL is required")
        try:
            _validate_canonical_url(canonical_url)
        except ContractValidationError as error:
            raise ContractValidationError(
                "source card v4 public HTTPS URL is unsafe"
            ) from error
        expected_url_digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
        if value.get("canonicalUrlSha256") != expected_url_digest:
            raise ContractValidationError("source card v4 canonical URL digest mismatched")
        identifier = value.get("identifier")
        if not isinstance(identifier, Mapping):
            raise ContractValidationError("source card v4 identifier is required")
        identifier_scheme = identifier.get("scheme")
        identifier_value = identifier.get("value")
        identifier_patterns = {
            "DOI": r"10\.[0-9]{4,9}/[-._;()/:A-Za-z0-9]+",
            "ISBN": r"(?:[0-9]{9}[0-9X]|[0-9]{13})",
            "ARXIV": r"(?:[a-z-]+/)?[0-9]{4}\.[0-9]{4,5}(?:v[0-9]+)?",
        }
        compact_identifier = (
            identifier_value.replace("-", "").replace(" ", "")
            if identifier_scheme == "ISBN" and isinstance(identifier_value, str)
            else identifier_value
        )
        if (
            identifier_scheme not in identifier_patterns
            or not isinstance(compact_identifier, str)
            or re.fullmatch(identifier_patterns[identifier_scheme], compact_identifier) is None
        ):
            raise ContractValidationError("source card v4 identifier is invalid")
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
    if schema_id == "s4-rag-v2-delete-ticket-v1":
        issued_at = _parse_timestamp(value.get("issuedAt"), field="issuedAt")
        expires_at = _parse_timestamp(value.get("expiresAt"), field="expiresAt")
        if (expires_at - issued_at).total_seconds() != 300:
            raise ContractValidationError("delete ticket lifetime must be exactly five minutes")
        if value.get("documentBound") is not True:
            raise ContractValidationError("delete ticket must be document-bound")
        return
    if schema_id == "s4-rag-v2-vertex-preparation-v1":
        if (
            value.get("scopeTtlSeconds") != 120
            or value.get("rawQuestionStored") is not False
            or value.get("rawEvidenceStored") is not False
        ):
            raise ContractValidationError(
                "Vertex preparation must be content-free and bound to a two-minute scope"
            )
        return
    if schema_id == "s4-rag-v2-status-activation-v1":
        deleted = value.get("state") == "DELETED"
        ready = value.get("state") == "READY"
        hard_delete_disposition = value.get("generationDisposition") == (
            "GENERATION_ACTIVATED_THEN_HARD_DELETED"
        )
        required_artifacts = ["DOCUMENT_IR", "CANONICAL_TEXT", "CHUNK", "VECTOR"]
        if deleted != hard_delete_disposition:
            raise ContractValidationError(
                "owner hard-delete disposition must match the DELETED state"
            )
        if hard_delete_disposition and (
            value.get("replacementGenerationActivated") is not True
            or not isinstance(value.get("replacementGenerationReceiptId"), str)
            or value.get("ownerDeleteHardDeleteVerified") is not True
            or value.get("hardDeletedArtifactClasses") != required_artifacts
        ):
            raise ContractValidationError(
                "owner hard-delete requires replacement generation activation receipt"
            )
        if not hard_delete_disposition and (
            value.get("replacementGenerationActivated") is not False
            or value.get("replacementGenerationReceiptId") is not None
            or value.get("ownerDeleteHardDeleteVerified") is not False
            or value.get("hardDeletedArtifactClasses") != []
        ):
            raise ContractValidationError(
                "non-deleted owner status cannot claim hard-delete activation"
            )
        if ready and value.get("generationDisposition") != "GENERATION_ACTIVE":
            raise ContractValidationError("READY owner status requires an active generation")
        if not deleted and not ready and value.get("generationDisposition") != "NOT_ACTIVE":
            raise ContractValidationError("non-ready owner status cannot claim an active generation")
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
        official_tokenizer = voyage.get("officialTokenizer")
        if not isinstance(official_tokenizer, Mapping) or any(
            (
                official_tokenizer.get("artifactAutoDownloadAllowed"),
                not official_tokenizer.get("localArtifactOnly"),
                not official_tokenizer.get("packetHashBindingRequired"),
                not official_tokenizer.get("preflightExpectedInputTokenLedgerRequired"),
                official_tokenizer.get("providerTokenCountCallAllowed"),
                official_tokenizer.get("utf8ByteApproximationAllowed"),
            )
        ):
            raise ContractValidationError(
                "Voyage must use a packet-bound local official tokenizer without approximation"
            )
        if vertex.get("maximumGenerateContentCallsPerQuestion") != 1:
            raise ContractValidationError("Vertex may generate at most once per question")
        return
    if schema_id == "foreign-news-lane-entitlement-v1":
        lanes = value.get("lanes")
        if not isinstance(lanes, list) or tuple(
            lane.get("laneId") for lane in lanes if isinstance(lane, Mapping)
        ) != FOREIGN_NEWS_LANES:
            raise ContractValidationError("foreign-news lane set or order drifted")
        for lane in lanes:
            assert isinstance(lane, Mapping)
            lane_id = lane["laneId"]
            policy = FOREIGN_NEWS_LANE_CONTRACTS[lane_id]
            expected: dict[str, Any] = {
                "allowedRetainedFields": policy["allowedRetainedFields"],
                "articleMetadataStored": False,
                "attachmentAllowed": policy["attachmentAllowed"],
                "boundedTransientParseOnly": policy["boundedTransientParseOnly"],
                "credentialMode": policy["credentialMode"],
                "credentialStored": False,
                "derivedCacheDeletionOnExpiryRequired": policy[
                    "derivedCacheDeletionOnExpiryRequired"
                ],
                "endpointAllowance": policy["endpointAllowance"],
                "externalEntityAllowed": policy["externalEntityAllowed"],
                "fixedOriginBinding": policy["fixedOriginBinding"],
                "headlineSummaryBodyStored": False,
                "laneId": lane_id,
                "mode": policy["mode"],
                "ownerLocalDerivedOnly": policy["ownerLocalDerivedOnly"],
                "providerCallsAllowed": False,
                "queryOrHeaderStored": False,
                "rawForwardedToVertex": False,
                "rawProviderDataStored": False,
                "redirectAllowed": False,
                "sharedHostedKeyAllowed": False,
            }
            if lane_id == "GDELT_OFFLINE_REFERENCE":
                expected.update(
                    {
                        "gdeltAdapterAdded": False,
                        "gdeltExecutorAdded": False,
                        "gdeltOutboundCalls": 0,
                    }
                )
            if dict(lane) != expected:
                raise ContractValidationError(
                    f"foreign-news lane policy drifted for {lane_id}"
                )
        return
    if schema_id == "foreign-news-sentiment-v1":
        lanes = value.get("lanes")
        if not isinstance(lanes, list) or tuple(
            lane.get("laneId") for lane in lanes if isinstance(lane, Mapping)
        ) != FOREIGN_NEWS_LANES:
            raise ContractValidationError("foreign-news response must cover exactly four lanes")
        has_available_lane = any(
            lane.get("state") == "AVAILABLE"
            for lane in lanes
            if isinstance(lane, Mapping)
        )
        if value.get("status") == "AVAILABLE" and not has_available_lane:
            raise ContractValidationError(
                "AVAILABLE foreign-news response requires an available lane"
            )
        if value.get("status") == "ABSTAIN" and has_available_lane:
            raise ContractValidationError(
                "ABSTAIN foreign-news response cannot contain an available lane"
            )
        return
    if schema_id == "foreign-news-model-selection-v1":
        status = value.get("selectionStatus")
        selected = value.get("selectedModel")
        test_count = value.get("testEvaluationCount")
        test_outcome = value.get("testOutcome")
        test_target = value.get("testTargetModel")
        abstain_reason = value.get("abstainReason")
        completed = value.get("validationCompleted")
        results = value.get("validationResults")
        if not isinstance(results, list) or tuple(
            result.get("candidateModel")
            for result in results
            if isinstance(result, Mapping)
        ) != MODEL_CANDIDATES:
            raise ContractValidationError("foreign-news validation candidate set or order drifted")
        if not completed:
            if (
                status != "NOT_SELECTED"
                or selected is not None
                or test_count != 0
                or test_outcome != "NOT_RUN"
                or test_target is not None
                or abstain_reason is not None
            ):
                raise ContractValidationError(
                    "incomplete validation cannot select or test a foreign-news model"
                )
            return

        def passes_validation(result: Mapping[str, Any]) -> bool:
            metrics = result.get("metrics")
            if not isinstance(metrics, Mapping):
                return False
            recalls = metrics.get("classRecalls")
            if not isinstance(recalls, Mapping):
                return False
            return (
                metrics.get("macroF1", 0) >= 0.80
                and all(recalls.get(label, 0) >= 0.75 for label in ("NEGATIVE", "NEUTRAL", "POSITIVE"))
                and metrics.get("neutralF1", 0) >= 0.75
                and metrics.get("ece", 1) <= 0.10
                and metrics.get("criticalNegationNumberUnitErrors") == 0
            )

        eligible = [
            result for result in results if isinstance(result, Mapping) and passes_validation(result)
        ]
        if not eligible:
            if (
                status != "ABSTAIN"
                or selected is not None
                or test_count != 0
                or test_outcome != "NOT_RUN"
                or test_target is not None
                or abstain_reason != "NO_MODEL_MEETS_VALIDATION_GATE"
            ):
                raise ContractValidationError(
                    "no validation-qualified model must ABSTAIN without a test call"
                )
            return

        def ranking_key(result: Mapping[str, Any]) -> tuple[float, float, float, int]:
            metrics = result["metrics"]
            assert isinstance(metrics, Mapping)
            return (
                -float(metrics["macroF1"]),
                float(metrics["ece"]),
                float(metrics["cpuP95Millis"]),
                int(metrics["footprintBytes"]),
            )

        ranked = sorted(eligible, key=ranking_key)
        best_key = ranking_key(ranked[0])
        tied = [result for result in ranked if ranking_key(result) == best_key]
        if len(tied) != 1:
            if (
                status != "ABSTAIN"
                or selected is not None
                or test_count != 0
                or test_outcome != "NOT_RUN"
                or test_target is not None
                or abstain_reason != "TIE_AFTER_FOOTPRINT"
            ):
                raise ContractValidationError(
                    "validation tie after footprint must ABSTAIN without test shopping"
                )
            return

        winner = ranked[0]["candidateModel"]
        if selected != winner:
            raise ContractValidationError(
                "selected foreign-news model must be the deterministic validation winner"
            )
        if status == "SELECTED_PENDING_TEST":
            if (
                test_count != 0
                or test_outcome != "NOT_RUN"
                or test_target is not None
                or abstain_reason is not None
            ):
                raise ContractValidationError(
                    "pending model selection cannot contain a test evaluation"
                )
            return
        if status == "TEST_EVALUATED":
            if (
                test_count != 1
                or test_outcome != "PASSED"
                or test_target != winner
                or abstain_reason is not None
            ):
                raise ContractValidationError(
                    "test evaluation must run exactly once for the selected model"
                )
            return
        if status == "ABSTAIN":
            if (
                test_count != 1
                or test_outcome != "FAILED"
                or test_target != winner
                or abstain_reason != "TEST_FAILED"
            ):
                raise ContractValidationError(
                    "failed selected-model test must ABSTAIN without next-candidate shopping"
                )
            return
        raise ContractValidationError("completed validation requires a selection state")
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
    valid = {
        relative_path: {
            **payload,
            "contractId": _fixture_schema_id(relative_path),
        }
        for relative_path, payload in _valid_fixtures().items()
    }
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
PRE_S5_SCHEMA_DOCUMENT_IDS: Final[frozenset[str]] = frozenset(
    f"contracts/schemas/{schema_id}.schema.json" for schema_id in SCHEMA_IDS
)
PRE_S5_FILENAME_PREFIXES: Final[tuple[str, ...]] = (
    "foreign-news-",
    "pre-s5-rag-news-contract",
    "rag-oa112-",
    "rag-source-card-v4",
    "rag-v2-pre-s5-addendum",
    "s4-8-optional3-",
    "s4-rag-v2-effective-consent-v1",
    "s4-rag-v2-external-consent-v1",
    "s4-rag-v2-import-ticket-request-v1",
    "s4-rag-v2-import-ticket-v1",
    "s4-rag-v2-vertex-preparation-v1",
    "s4-rag-v2-pre-s5-policy-v1",
    "s4-rag-v2-status-activation-v1",
)
PRE_S5_OPENAPI_TITLES: Final[frozenset[str]] = frozenset(
    {
        "Capstone RAG v2 Pre-S5 addendum",
        "Capstone foreign-news explanation-only contract",
    }
)


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


def _is_pre_s5_public_artifact_payload(path: Path, root: Path) -> bool:
    """filename과 무관하게 public namespace 안의 Pre-S5 payload를 식별한다."""

    try:
        payload = load_json_bytes_strict(
            path.read_bytes(), source=path.relative_to(root).as_posix()
        )
    except (ContractValidationError, OSError):
        return False
    if not isinstance(payload, Mapping):
        return False
    if payload.get("contractId") in {*SCHEMA_IDS, "pre-s5-rag-news-contract.v1"}:
        return True
    if payload.get("$id") in PRE_S5_SCHEMA_DOCUMENT_IDS:
        return True
    info = payload.get("info")
    return isinstance(info, Mapping) and info.get("title") in PRE_S5_OPENAPI_TITLES


def _is_pre_s5_filename_family(path: Path) -> bool:
    """declared schema/fixture/OpenAPI name 계열의 숨은 추가 파일도 fail-closed한다."""

    return any(path.name.startswith(prefix) for prefix in PRE_S5_FILENAME_PREFIXES)


def _public_pre_s5_artifact_candidates(namespace_root: Path, root: Path) -> set[Path]:
    """no-follow recursive scan으로 hidden/nested packet과 link를 public tree에서 찾는다."""

    candidates: set[Path] = set()
    try:
        metadata = namespace_root.lstat()
    except OSError:
        return candidates
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        return {namespace_root}
    for directory, directory_names, file_names in os.walk(namespace_root, followlinks=False):
        current_directory = Path(directory)
        for directory_name in tuple(directory_names):
            child = current_directory / directory_name
            try:
                metadata = child.lstat()
            except OSError:
                candidates.add(child)
                directory_names.remove(directory_name)
                continue
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                candidates.add(child)
                directory_names.remove(directory_name)
        for file_name in file_names:
            candidate = current_directory / file_name
            try:
                metadata = candidate.lstat()
            except OSError:
                candidates.add(candidate)
                continue
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                candidates.add(candidate)
                continue
            if _is_pre_s5_filename_family(candidate) or _is_pre_s5_public_artifact_payload(
                candidate, root
            ):
                candidates.add(candidate)
    return candidates


def _unexpected_pre_s5_artifact_paths(
    root: Path, expected_output_paths: Collection[str]
) -> list[str]:
    """Pre-S5 generated namespace 외의 extra artifact와 local-only packet 은닉을 막는다."""

    expected_paths = set(expected_output_paths)
    candidates: set[Path] = set()
    for relative_namespace in (
        "contracts/catalogs",
        "contracts/examples",
        "contracts/openapi",
        "contracts/schemas",
    ):
        candidates.update(_public_pre_s5_artifact_candidates(root / relative_namespace, root))
    return sorted(
        relative_path
        for path in candidates
        if (relative_path := path.relative_to(root).as_posix()) not in expected_paths
    )


def _is_regular_generated_output(root: Path, relative_path: str) -> bool:
    """expected output은 모든 parent가 link 없는 directory이고 leaf가 단일 regular file여야 한다."""

    try:
        metadata = root.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            return False
        path = root
        for component in PurePosixPath(relative_path).parts:
            path = path / component
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                return False
        return stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1
    except OSError:
        return False


def _check_outputs(outputs: Mapping[str, bytes], *, root: Path = ROOT) -> None:
    drifted: list[str] = []
    for relative_path, expected in sorted(outputs.items()):
        path = root / relative_path
        if not _is_regular_generated_output(root, relative_path) or path.read_bytes() != expected:
            drifted.append(relative_path)
    unexpected = _unexpected_pre_s5_artifact_paths(root, outputs)
    if drifted or unexpected:
        messages: list[str] = []
        if drifted:
            messages.append(
                "generated Pre-S5 RAG/news artifacts drifted:\n"
                + "\n".join(f"- {path}" for path in drifted)
            )
        if unexpected:
            messages.append(
                "unexpected Pre-S5 generated namespace artifacts:\n"
                + "\n".join(f"- {path}" for path in unexpected)
            )
        raise ContractValidationError("\n".join(messages))


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
