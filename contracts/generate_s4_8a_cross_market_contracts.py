from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Mapping

from jsonschema import Draft202012Validator

_SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    canonical_json_bytes,
    load_json_bytes_strict,
)


ROOT = _SCRIPT_REPO_ROOT
HASH_PATTERN: Final[str] = "^[0-9a-f]{64}$"
SCHEMA_IDS: Final[tuple[str, ...]] = (
    "market_source_entitlement.v1",
    "cross_market_exposure_catalog.v1",
    "cross_market_observation.v1",
    "analyst_revision_evidence.v1",
    "market_cause_evidence.v1",
    "cross_market_risk_snapshot.v1",
    "cross_market_policy_evaluation.v1",
)
SCHEMA_PATHS: Final[dict[str, Path]] = {
    schema_id: ROOT / f"contracts/schemas/{schema_id}.schema.json"
    for schema_id in SCHEMA_IDS
}

# endpoint 이름은 local-only registry에 남기고 Git 계약에는 exact opaque identity만 고정한다.
KIS_ENDPOINT_IDENTITY_HASHES: Final[tuple[str, ...]] = (
    "322a49da546ecc80bc9ed2828cf87f4a32c27614e930bc084b2da416b495e4cd",
    "7043afa0e404d963fb0c4fa32e0933626dda7df028e58a5e0e02d397627e1dbc",
    "1643af4b4b4bef65780dd26a4eeb2511fabe45ac3dc7b4cbc760652cebe643c5",
    "f06c062c8c8b5a303a7cd647661a2ac54326a1e3241f3d427742aebcae2cfc5d",
    "3e4d633656df2fe6b8b8171500c9f6156545dff56a034436ef9e141b75f01527",
    "ae00425f78a52af0755c884bf689dd37c4950e4c30f74fee0e8c175dc6730869",
    "5b4660b5e127a17d467e0175767264ba98b4312284d02110b62d698a7ac8210b",
    "609848efbc0812f845168ea9315f13dd8d28f6c6297998f2b44de3e5274fbcc1",
    "f192e646987197ba81f52806f0117b343309011f6700615713d0363a41271e11",
    "03f678f7310d800669026306087b75eb2641be3d8ce31061e99d0267591fc085",
    "c479298d73dee14cc3e2aee60a20b9b5f0498ce047c211d6a8dd464a2826a5e6",
    "87033bbed15777e2f5ec9332353119d4d3247dd1c17b878331ade3277c79281d",
    "d1bad1dd2d9bc8ee69b9f094f2a49a66bebe55aea89738dd7bf1990e8fb03158",
    "c1f6abd3a3cf7804b86777c3e5f77a8efc1f19b126961ac786d3ebfafe5809c4",
    "5ca1e527b1a5d952bed2ccdd5a9c69f38011b8c24495c8d3dffc03ba82f757b6",
    "5bc0704e58565ff1a5e5416585e6085e4c9d20a515818cd0ecee63cc67e03963",
    "c256ae16a5c9a26cf3cc174d12d44a82d13a5ca9578d4c0d3e87a3e408752592",
    "3bfe4796b3639a8649510f46a6cc8d4e0cbdb714a6ca6b98d413aa41b5761197",
)

FROZEN_EXISTING_PAYLOAD_HASHES: Final[dict[str, str]] = {
    "contracts/catalogs/s2-2-system-rule-catalog.v1.json": (
        "a4714ee9ce3031199b9067919b15931fb42e106857da5f8d8ad7a95bafa8ad7b"
    ),
    "contracts/examples/s2-2-hash-vector.valid.json": (
        "d4c56df918cdd15a9143ab46efb498e75d1145a338c817d0119b7519029895da"
    ),
    "contracts/schemas/s2-3-evaluate-order-request.schema.json": (
        "8b7ea2c1a1bbf6e756d5b6cd526d0793e330edc26d95e5033ba98fdd34a940bf"
    ),
    "contracts/schemas/s2-3-decision-response.schema.json": (
        "3c0e8449a51e0c48c1c5bd9141ecfd977a158bea45a6a341e6705b188da66304"
    ),
    "contracts/schemas/s4-rag-ask-request.schema.json": (
        "b0af6447c0234de050f0277ca52a61ce430ef80c78ffa4c1d58a6bcffc2a0096"
    ),
    "contracts/schemas/s4-rag-history-detail.schema.json": (
        "78b495d57d27d181adbde51c102a0d87a99678af6db52e8ed6e02a7a6c72b879"
    ),
    "contracts/schemas/s4-rag-history-page.schema.json": (
        "3862656a31198654848a92d464efb7b96f82fcb1fd5dbd0460837478de112cdb"
    ),
    "contracts/schemas/signal.schema.json": (
        "ae6b2285d1df7ce608778cd59c332332e8c44ce38a861d23d57dc8f0f9b912c2"
    ),
}


def _hash_schema() -> dict[str, Any]:
    return {"type": "string", "pattern": HASH_PATTERN}


def _timestamp_schema() -> dict[str, Any]:
    return {"type": "string", "format": "date-time"}


def _closed_object(
    *, required: list[str], properties: dict[str, Any], title: str | None = None
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }
    if title is not None:
        value["title"] = title
    return value


def _schema_document(contract_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
    schema = copy.deepcopy(dict(body))
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"contracts/schemas/{contract_id}.schema.json"
    schema["title"] = contract_id
    return schema


def _entitlement_schema() -> dict[str, Any]:
    declarations = _closed_object(
        required=[
            "derivedPayloadProduced",
            "embedded",
            "externalLlmProcessed",
            "nonDisplayUsed",
            "rawStored",
        ],
        properties={
            "derivedPayloadProduced": {"type": "boolean"},
            "embedded": {"type": "boolean"},
            "externalLlmProcessed": {"type": "boolean"},
            "nonDisplayUsed": {"type": "boolean"},
            "rawStored": {"type": "boolean"},
        },
    )
    entry = _closed_object(
        required=[
            "activationStatus",
            "attributionRequired",
            "category",
            "contractExpiry",
            "decisionAuthority",
            "deletionOwner",
            "derivedDataAllowed",
            "embeddingAllowed",
            "endpointIdentityHash",
            "entitlementVersion",
            "externalLlmAllowed",
            "logicalIdentityHash",
            "machineFetchAllowed",
            "materializationDeclaration",
            "nonDisplayAllowed",
            "projectionRetentionMaxDays",
            "providerCallsAllowed",
            "rawRetentionMaxHours",
            "rawStoreAllowed",
            "region",
            "sourceFamily",
            "sourceId",
        ],
        properties={
            "activationStatus": {
                "enum": ["CANDIDATE_DISABLED", "ACTIVE", "BLOCKED", "EXPIRED"]
            },
            "attributionRequired": {"type": "boolean"},
            "category": {
                "enum": [
                    "ANALYST",
                    "OVERSEAS_LEAD",
                    "DOMESTIC_AMPLIFICATION",
                    "NEWS_AGGREGATE",
                ]
            },
            "contractExpiry": _timestamp_schema(),
            "decisionAuthority": {"const": "NONE"},
            "deletionOwner": {
                "type": "string",
                "pattern": "^[a-z0-9][a-z0-9:_-]{2,127}$",
            },
            "derivedDataAllowed": {"type": "boolean"},
            "embeddingAllowed": {"type": "boolean"},
            "endpointIdentityHash": _hash_schema(),
            "entitlementVersion": {
                "type": "string",
                "pattern": "^[a-z0-9][a-z0-9._-]{0,63}$",
            },
            "externalLlmAllowed": {"type": "boolean"},
            "logicalIdentityHash": _hash_schema(),
            "machineFetchAllowed": {"type": "boolean"},
            "materializationDeclaration": declarations,
            "nonDisplayAllowed": {"type": "boolean"},
            "projectionRetentionMaxDays": {
                "type": "integer",
                "minimum": 0,
                "maximum": 3650,
            },
            "providerCallsAllowed": {"type": "boolean"},
            "rawRetentionMaxHours": {
                "type": "integer",
                "minimum": 0,
                "maximum": 8760,
            },
            "rawStoreAllowed": {"type": "boolean"},
            "region": {"enum": ["KR", "US", "GLOBAL"]},
            "sourceFamily": {"enum": ["KIS", "GDELT_AGGREGATE"]},
            "sourceId": {
                "type": "string",
                "pattern": "^[A-Z][A-Z0-9_]{2,63}$",
            },
        },
    )
    entry["allOf"] = [
        {
            "if": {
                "properties": {
                    "materializationDeclaration": {
                        "properties": {declaration: {"const": True}}
                    }
                }
            },
            "then": {"properties": {right: {"const": True}}},
        }
        for declaration, right in (
            ("rawStored", "rawStoreAllowed"),
            ("derivedPayloadProduced", "derivedDataAllowed"),
            ("embedded", "embeddingAllowed"),
            ("externalLlmProcessed", "externalLlmAllowed"),
            ("nonDisplayUsed", "nonDisplayAllowed"),
        )
    ]
    entry["allOf"].append(
        {
            "if": {"properties": {"providerCallsAllowed": {"const": True}}},
            "then": {
                "properties": {
                    "activationStatus": {"const": "ACTIVE"},
                    "machineFetchAllowed": {"const": True},
                }
            },
        }
    )
    return _schema_document(
        "market_source_entitlement.v1",
        _closed_object(
            required=[
                "artifactHash",
                "contractId",
                "entitlements",
                "evaluatedAt",
                "inventoryAuthority",
                "payloadHash",
                "publicIdentityMode",
                "registryId",
                "schemaVersion",
            ],
            properties={
                "artifactHash": _hash_schema(),
                "contractId": {"const": "market_source_entitlement.v1"},
                "entitlements": {
                    "type": "array",
                    "minItems": 19,
                    "maxItems": 19,
                    "uniqueItems": True,
                    "items": entry,
                },
                "evaluatedAt": _timestamp_schema(),
                "inventoryAuthority": {"const": "LOCAL_PRIVATE_REGISTRY"},
                "payloadHash": _hash_schema(),
                "publicIdentityMode": {"const": "OPAQUE_SHA256_ONLY"},
                "registryId": {"const": "s4-8a-source-entitlements-20260731"},
                "schemaVersion": {"const": "1"},
            },
        ),
    )


def _exposure_schema() -> dict[str, Any]:
    body = _closed_object(
        required=[
            "artifactHash",
            "availableAt",
            "classification",
            "configVersion",
            "contractId",
            "effectiveAt",
            "inScope",
            "logicalIdentityHash",
            "payloadHash",
            "schemaVersion",
            "sourceLineage",
            "symbol",
            "validationState",
        ],
        properties={
            "artifactHash": _hash_schema(),
            "availableAt": _timestamp_schema(),
            "classification": {
                "type": ["string", "null"],
                "enum": [
                    None,
                    "SEMICONDUCTOR",
                    "BROAD_MARKET",
                    "FX",
                    "DOMESTIC_AMPLIFICATION",
                ],
            },
            "configVersion": {"const": "cross-market-exposure.v1"},
            "contractId": {"const": "cross_market_exposure_catalog.v1"},
            "effectiveAt": _timestamp_schema(),
            "inScope": {"type": "boolean"},
            "logicalIdentityHash": _hash_schema(),
            "payloadHash": _hash_schema(),
            "schemaVersion": {"const": "1"},
            "sourceLineage": {
                "type": "array",
                "minItems": 1,
                "maxItems": 10,
                "uniqueItems": True,
                "items": _hash_schema(),
            },
            "symbol": {"type": "string", "pattern": "^[0-9A-Z._:-]{1,20}$"},
            "validationState": {"enum": ["AVAILABLE", "UNCLASSIFIED", "REJECTED"]},
        },
    )
    body["allOf"] = [
        {
            "if": {"properties": {"validationState": {"const": "AVAILABLE"}}},
            "then": {
                "properties": {
                    "classification": {"type": "string"},
                    "inScope": {"const": True},
                }
            },
            "else": {
                "properties": {
                    "classification": {"const": None},
                    "inScope": {"const": False},
                }
            },
        }
    ]
    return _schema_document("cross_market_exposure_catalog.v1", body)


def _observation_schema() -> dict[str, Any]:
    body = _closed_object(
        required=[
            "artifactHash",
            "availableAt",
            "completeness",
            "contractId",
            "decisionAuthority",
            "evaluatedAt",
            "instrument",
            "logicalIdentityHash",
            "market",
            "observedAt",
            "payloadHash",
            "receivedAt",
            "schemaVersion",
            "sessionDate",
            "sourceRef",
            "status",
            "timeframe",
            "value",
            "valueType",
        ],
        properties={
            "abstainReason": {
                "enum": [
                    "SOURCE_MISSING",
                    "SOURCE_INCOMPLETE",
                    "SOURCE_STALE",
                    "ENTITLEMENT_DISABLED",
                ]
            },
            "artifactHash": _hash_schema(),
            "availableAt": _timestamp_schema(),
            "completeness": {"enum": ["COMPLETE", "INCOMPLETE", "MISSING"]},
            "contractId": {"const": "cross_market_observation.v1"},
            "decisionAuthority": {"const": "NONE"},
            "evaluatedAt": _timestamp_schema(),
            "instrument": {"type": "string", "pattern": "^[0-9A-Z._:-]{1,32}$"},
            "logicalIdentityHash": _hash_schema(),
            "market": {"enum": ["XNAS", "XNYS", "XKRX", "FX", "INDEX"]},
            "observedAt": _timestamp_schema(),
            "payloadHash": _hash_schema(),
            "receivedAt": _timestamp_schema(),
            "schemaVersion": {"const": "1"},
            "sessionDate": {"type": "string", "format": "date"},
            "sourceRef": _hash_schema(),
            "status": {"enum": ["AVAILABLE", "ABSTAIN"]},
            "timeframe": {"enum": ["EOD", "DAILY", "SESSION"]},
            "value": {
                "type": ["number", "null"],
                "minimum": -1000000000,
                "maximum": 1000000000,
            },
            "valueType": {"enum": ["PRICE", "SESSION_RETURN", "STRESS_LEVEL"]},
        },
    )
    body["oneOf"] = [
        {
            "properties": {
                "completeness": {"const": "COMPLETE"},
                "status": {"const": "AVAILABLE"},
                "value": {"type": "number"},
            },
            "not": {"required": ["abstainReason"]},
        },
        {
            "properties": {
                "completeness": {"enum": ["INCOMPLETE", "MISSING"]},
                "status": {"const": "ABSTAIN"},
                "value": {"const": None},
            },
            "required": ["abstainReason"],
        },
    ]
    return _schema_document("cross_market_observation.v1", body)


def _analyst_schema() -> dict[str, Any]:
    estimate = _closed_object(
        required=["eps", "rating", "revenue", "targetPrice"],
        properties={
            "eps": {"type": ["number", "null"]},
            "rating": {
                "type": ["string", "null"],
                "enum": [None, "BUY", "HOLD", "SELL"],
            },
            "revenue": {"type": ["number", "null"], "minimum": 0},
            "targetPrice": {"type": ["number", "null"], "minimum": 0},
        },
    )
    revision = _closed_object(
        required=["epsDelta", "ratingChanged", "revenueDelta", "targetPriceDelta"],
        properties={
            "epsDelta": {"type": ["number", "null"]},
            "ratingChanged": {"type": "boolean"},
            "revenueDelta": {"type": ["number", "null"]},
            "targetPriceDelta": {"type": ["number", "null"]},
        },
    )
    body = _closed_object(
        required=[
            "artifactHash",
            "availableAt",
            "brokerId",
            "buyOpinionWeight",
            "contractId",
            "contributorCount",
            "current",
            "decisionAuthority",
            "dedupeKeyHash",
            "dispersion",
            "estimatePeriod",
            "logicalIdentityHash",
            "originalEvidenceId",
            "payloadHash",
            "previous",
            "publishedAt",
            "rawTextStored",
            "receivedAt",
            "retracted",
            "revision",
            "schemaVersion",
            "sourceLicense",
            "supersedesEvidenceId",
            "symbol",
            "userConfirmedTags",
        ],
        properties={
            "artifactHash": _hash_schema(),
            "availableAt": _timestamp_schema(),
            "brokerId": {"type": "string", "pattern": "^broker_[0-9a-f]{16}$"},
            "buyOpinionWeight": {"const": 0},
            "contractId": {"const": "analyst_revision_evidence.v1"},
            "contributorCount": {"type": "integer", "minimum": 1, "maximum": 1000},
            "current": estimate,
            "decisionAuthority": {"const": "NONE"},
            "dedupeKeyHash": _hash_schema(),
            "dispersion": {"type": ["number", "null"], "minimum": 0},
            "estimatePeriod": {"type": "string", "pattern": "^[0-9]{4}(?:Q[1-4]|FY)$"},
            "logicalIdentityHash": _hash_schema(),
            "originalEvidenceId": {
                "type": "string",
                "pattern": "^analyst_[a-z0-9_-]{8,64}$",
            },
            "payloadHash": _hash_schema(),
            "previous": estimate,
            "publishedAt": _timestamp_schema(),
            "rawTextStored": {"const": False},
            "receivedAt": _timestamp_schema(),
            "retracted": {"type": "boolean"},
            "revision": revision,
            "schemaVersion": {"const": "1"},
            "sourceLicense": {
                "enum": [
                    "STRUCTURED_FIXTURE",
                    "MANUAL_LINK_ONLY",
                    "LICENSED_EPHEMERAL_LOCAL",
                ]
            },
            "supersedesEvidenceId": {
                "type": ["string", "null"],
                "pattern": "^analyst_[a-z0-9_-]{8,64}$",
            },
            "symbol": {"type": "string", "pattern": "^[0-9A-Z._:-]{1,20}$"},
            "userConfirmedTags": {
                "type": "array",
                "maxItems": 20,
                "uniqueItems": True,
                "items": {"enum": ["CHANGE", "RISK"]},
            },
        },
    )
    return _schema_document("analyst_revision_evidence.v1", body)


def _cause_schema() -> dict[str, Any]:
    body = _closed_object(
        required=[
            "artifactHash",
            "availableAt",
            "classification",
            "contractId",
            "contradictionEvidenceIds",
            "counterargument",
            "decisionAuthority",
            "dedupeKeyHash",
            "logicalIdentityHash",
            "occurredAt",
            "payloadHash",
            "publishedAt",
            "receivedAt",
            "relatedEvidenceIds",
            "relation",
            "retracted",
            "sanitizedSummary",
            "schemaVersion",
            "sourceFamily",
            "sourceLineageHash",
            "supersedesEvidenceId",
        ],
        properties={
            "artifactHash": _hash_schema(),
            "availableAt": _timestamp_schema(),
            "classification": {
                "enum": [
                    "CONFIRMED_FACT",
                    "REPORTED_CLAIM",
                    "MARKET_INTERPRETATION",
                    "HYPOTHESIS",
                ]
            },
            "contractId": {"const": "market_cause_evidence.v1"},
            "contradictionEvidenceIds": {
                "type": "array",
                "maxItems": 10,
                "uniqueItems": True,
                "items": {"type": "string", "pattern": "^cause_[a-z0-9_-]{8,64}$"},
            },
            "counterargument": {"type": "boolean"},
            "decisionAuthority": {"const": "NONE"},
            "dedupeKeyHash": _hash_schema(),
            "logicalIdentityHash": _hash_schema(),
            "occurredAt": _timestamp_schema(),
            "payloadHash": _hash_schema(),
            "publishedAt": _timestamp_schema(),
            "receivedAt": _timestamp_schema(),
            "relatedEvidenceIds": {
                "type": "array",
                "maxItems": 10,
                "uniqueItems": True,
                "items": {"type": "string", "pattern": "^cause_[a-z0-9_-]{8,64}$"},
            },
            "relation": {
                "enum": [
                    "PRECEDES",
                    "CO_MOVES_WITH",
                    "REPORTED_AS_CAUSE",
                    "CORROBORATES",
                    "CONTRADICTS",
                ]
            },
            "retracted": {"type": "boolean"},
            "sanitizedSummary": {
                "type": "string",
                "minLength": 1,
                "maxLength": 512,
                "pattern": "^[^\\u0000-\\u001F\\u007F<>]*$",
            },
            "schemaVersion": {"const": "1"},
            "sourceFamily": {
                "enum": [
                    "KIS_ANALYST",
                    "GDELT_AGGREGATE",
                    "OPENDART",
                    "SEC",
                    "CORPORATE_IR",
                ]
            },
            "sourceLineageHash": _hash_schema(),
            "supersedesEvidenceId": {
                "type": ["string", "null"],
                "pattern": "^cause_[a-z0-9_-]{8,64}$",
            },
        },
    )
    return _schema_document("market_cause_evidence.v1", body)


def _risk_snapshot_schema() -> dict[str, Any]:
    freshness = _closed_object(
        required=["asOf", "component", "freshUntil", "sourceRefs", "state"],
        properties={
            "asOf": {"type": ["string", "null"], "format": "date-time"},
            "component": {
                "enum": [
                    "SEMICONDUCTOR",
                    "BROAD_MARKET",
                    "FX",
                    "DOMESTIC_AMPLIFICATION",
                ]
            },
            "freshUntil": {"type": ["string", "null"], "format": "date-time"},
            "sourceRefs": {
                "type": "array",
                "maxItems": 20,
                "uniqueItems": True,
                "items": _hash_schema(),
            },
            "state": {
                "enum": ["DISABLED", "AVAILABLE", "UNAVAILABLE", "STALE", "INCOMPLETE"]
            },
        },
    )
    timing = _closed_object(
        required=[
            "detectionLatencyMillis",
            "preOpenLeadTimeMillis",
            "preOpenState",
            "snapshotAvailableAt",
            "sourceAvailableAt",
            "xkrxOpenAt",
        ],
        properties={
            "detectionLatencyMillis": {"type": "integer", "minimum": 0},
            "preOpenLeadTimeMillis": {"type": ["integer", "null"]},
            "preOpenState": {"enum": ["EARLY", "AT_OPEN", "LATE", "NOT_APPLICABLE"]},
            "snapshotAvailableAt": _timestamp_schema(),
            "sourceAvailableAt": _timestamp_schema(),
            "xkrxOpenAt": {"type": ["string", "null"], "format": "date-time"},
        },
    )
    body = _closed_object(
        required=[
            "artifactHash",
            "availability",
            "broadRiskOffScore",
            "configVersion",
            "contractId",
            "decisionAuthority",
            "domesticLeverageStressScore",
            "evidenceMode",
            "evidenceRefs",
            "freshness",
            "fxStressScore",
            "logicalIdentityHash",
            "mode",
            "orderAuthority",
            "owner",
            "payloadHash",
            "performanceClaimAllowed",
            "schemaVersion",
            "semiconductorShockScore",
            "timing",
            "upstreamArtifactHashes",
            "validationStatus",
        ],
        properties={
            "artifactHash": _hash_schema(),
            "availability": {
                "enum": ["DISABLED", "AVAILABLE", "UNAVAILABLE", "STALE", "INCOMPLETE"]
            },
            "broadRiskOffScore": {
                "type": ["number", "null"],
                "minimum": 0,
                "maximum": 100,
            },
            "configVersion": {"const": "cross-market-risk-config.v1"},
            "contractId": {"const": "cross_market_risk_snapshot.v1"},
            "decisionAuthority": {"const": "NEW_BUY_ALLOW_TO_WARN_ONLY"},
            "domesticLeverageStressScore": {
                "type": ["number", "null"],
                "minimum": 0,
                "maximum": 100,
            },
            "evidenceMode": {
                "enum": ["SYNTHETIC_FIXTURE", "HISTORICAL_REPLAY", "PROSPECTIVE_SHADOW"]
            },
            "evidenceRefs": {
                "type": "array",
                "maxItems": 10,
                "uniqueItems": True,
                "items": _hash_schema(),
            },
            "freshness": {
                "type": "array",
                "minItems": 4,
                "maxItems": 4,
                "items": freshness,
            },
            "fxStressScore": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
            "logicalIdentityHash": _hash_schema(),
            "mode": {"enum": ["OFF", "SHADOW", "WARN_ONLY"]},
            "orderAuthority": {"const": "NONE"},
            "owner": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "payloadHash": _hash_schema(),
            "performanceClaimAllowed": {"type": "boolean"},
            "schemaVersion": {"const": "1"},
            "semiconductorShockScore": {
                "type": ["number", "null"],
                "minimum": 0,
                "maximum": 100,
            },
            "timing": timing,
            "upstreamArtifactHashes": {
                "type": "array",
                "minItems": 4,
                "maxItems": 64,
                "uniqueItems": True,
                "items": _hash_schema(),
            },
            "validationStatus": {"enum": ["UNVALIDATED", "VALIDATED"]},
        },
    )
    score_names = [
        "broadRiskOffScore",
        "domesticLeverageStressScore",
        "fxStressScore",
        "semiconductorShockScore",
    ]
    body["allOf"] = [
        {
            "if": {"properties": {"availability": {"const": "AVAILABLE"}}},
            "then": {"properties": {name: {"type": "number"} for name in score_names}},
            "else": {"properties": {name: {"const": None} for name in score_names}},
        },
        {
            "if": {"properties": {"evidenceMode": {"const": "SYNTHETIC_FIXTURE"}}},
            "then": {
                "properties": {
                    "performanceClaimAllowed": {"const": False},
                    "validationStatus": {"const": "UNVALIDATED"},
                }
            },
        },
    ]
    return _schema_document("cross_market_risk_snapshot.v1", body)


def _policy_evaluation_schema() -> dict[str, Any]:
    metrics = _closed_object(
        required=[
            "conflictRate",
            "coverageRate",
            "downsideAvoidedBps",
            "falseBlockRate",
            "latencyP95Millis",
            "missedUpsideBps",
            "netProtectionBps",
            "staleRate",
            "unsupportedRate",
        ],
        properties={
            "conflictRate": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "coverageRate": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "downsideAvoidedBps": {"type": ["number", "null"]},
            "falseBlockRate": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "latencyP95Millis": {"type": ["integer", "null"], "minimum": 0},
            "missedUpsideBps": {"type": ["number", "null"]},
            "netProtectionBps": {"type": ["number", "null"]},
            "staleRate": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "unsupportedRate": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        },
    )
    split = _closed_object(
        required=[
            "embargoSessions",
            "purgeSessions",
            "testPercent",
            "trainPercent",
            "validationPercent",
        ],
        properties={
            "embargoSessions": {"const": 5},
            "purgeSessions": {"const": 5},
            "testPercent": {"const": 20},
            "trainPercent": {"const": 60},
            "validationPercent": {"const": 20},
        },
    )
    body = _closed_object(
        required=[
            "artifactHash",
            "contractId",
            "decisionAuthority",
            "estimationStatus",
            "evaluationKind",
            "frozenThreshold",
            "logicalIdentityHash",
            "metrics",
            "payloadHash",
            "runtimeRiskEngineSource",
            "schemaVersion",
            "severeLossCutoffBps",
            "split",
            "thresholdFrozen",
            "triggerCount",
        ],
        properties={
            "artifactHash": _hash_schema(),
            "contractId": {"const": "cross_market_policy_evaluation.v1"},
            "decisionAuthority": {"const": "NONE"},
            "estimationStatus": {"enum": ["ESTIMABLE", "NOT_ESTIMABLE"]},
            "evaluationKind": {
                "enum": ["EVENT_STUDY", "LIGHTGBM_POLICY_REPLAY", "RUNTIME_SHADOW"]
            },
            "frozenThreshold": {"type": "number", "minimum": 0, "maximum": 100},
            "logicalIdentityHash": _hash_schema(),
            "metrics": metrics,
            "payloadHash": _hash_schema(),
            "runtimeRiskEngineSource": {"const": False},
            "schemaVersion": {"const": "1"},
            "severeLossCutoffBps": {"type": "number", "maximum": 0},
            "split": split,
            "thresholdFrozen": {"const": True},
            "triggerCount": {"type": "integer", "minimum": 0},
        },
    )
    nullable_metrics = [
        "conflictRate",
        "coverageRate",
        "downsideAvoidedBps",
        "falseBlockRate",
        "latencyP95Millis",
        "missedUpsideBps",
        "netProtectionBps",
        "staleRate",
        "unsupportedRate",
    ]
    body["oneOf"] = [
        {
            "properties": {
                "estimationStatus": {"const": "NOT_ESTIMABLE"},
                "metrics": {
                    "properties": {name: {"const": None} for name in nullable_metrics}
                },
                "triggerCount": {"const": 0},
            }
        },
        {
            "properties": {
                "estimationStatus": {"const": "ESTIMABLE"},
                "triggerCount": {"minimum": 1},
            }
        },
    ]
    return _schema_document("cross_market_policy_evaluation.v1", body)


def _hash_vector_v3_schema() -> dict[str, Any]:
    return _schema_document(
        "s2-2-hash-vector.v3",
        _closed_object(
            required=[
                "canonicalizationId",
                "catalogVersion",
                "compatibility",
                "excludedFields",
                "metricSnapshotVersion",
                "semanticInput",
                "semanticInputHash",
            ],
            properties={
                "canonicalizationId": {"const": "HASH-CANONICALIZATION-S22-V3"},
                "catalogVersion": {"const": 2},
                "compatibility": _closed_object(
                    required=["catalogV1Sha256", "hashVectorV2Sha256", "v1Unchanged"],
                    properties={
                        "catalogV1Sha256": _hash_schema(),
                        "hashVectorV2Sha256": _hash_schema(),
                        "v1Unchanged": {"const": True},
                    },
                ),
                "excludedFields": {
                    "type": "array",
                    "minItems": 9,
                    "maxItems": 9,
                    "uniqueItems": True,
                    "items": {"type": "string"},
                },
                "metricSnapshotVersion": {"const": "s2.2-metric-snapshot-v3"},
                "semanticInput": {"type": "object"},
                "semanticInputHash": _hash_schema(),
            },
        ),
    )


def _schemas() -> dict[str, dict[str, Any]]:
    return {
        "market_source_entitlement.v1": _entitlement_schema(),
        "cross_market_exposure_catalog.v1": _exposure_schema(),
        "cross_market_observation.v1": _observation_schema(),
        "analyst_revision_evidence.v1": _analyst_schema(),
        "market_cause_evidence.v1": _cause_schema(),
        "cross_market_risk_snapshot.v1": _risk_snapshot_schema(),
        "cross_market_policy_evaluation.v1": _policy_evaluation_schema(),
    }


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _entitlement_entry(
    *, index: int, identity_hash: str, category: str, source_family: str = "KIS"
) -> dict[str, Any]:
    return {
        "activationStatus": "CANDIDATE_DISABLED",
        "attributionRequired": True,
        "category": category,
        "contractExpiry": "2027-07-31T00:00:00Z",
        "decisionAuthority": "NONE",
        "deletionOwner": "decision-platform:cross-market-retention",
        "derivedDataAllowed": False,
        "embeddingAllowed": False,
        "endpointIdentityHash": identity_hash,
        "entitlementVersion": "candidate-disabled-v1",
        "externalLlmAllowed": False,
        "logicalIdentityHash": _sha(f"{source_family}:{identity_hash}"),
        "machineFetchAllowed": False,
        "materializationDeclaration": {
            "derivedPayloadProduced": False,
            "embedded": False,
            "externalLlmProcessed": False,
            "nonDisplayUsed": False,
            "rawStored": False,
        },
        "nonDisplayAllowed": False,
        "projectionRetentionMaxDays": 0,
        "providerCallsAllowed": False,
        "rawRetentionMaxHours": 0,
        "rawStoreAllowed": False,
        "region": "GLOBAL" if source_family == "GDELT_AGGREGATE" else "KR",
        "sourceFamily": source_family,
        "sourceId": (
            "GDELT_AGGREGATE"
            if source_family == "GDELT_AGGREGATE"
            else f"KIS_DISABLED_{index:02d}"
        ),
    }


def _entitlement_fixture() -> dict[str, Any]:
    categories = (
        ["ANALYST"] * 3 + ["OVERSEAS_LEAD"] * 4 + ["DOMESTIC_AMPLIFICATION"] * 11
    )
    entries = [
        _entitlement_entry(index=index, identity_hash=identity_hash, category=category)
        for index, (identity_hash, category) in enumerate(
            zip(KIS_ENDPOINT_IDENTITY_HASHES, categories, strict=True), start=1
        )
    ]
    entries.append(
        _entitlement_entry(
            index=19,
            identity_hash=_sha("gdelt-aggregate:timeline-tone+timeline-volraw"),
            category="NEWS_AGGREGATE",
            source_family="GDELT_AGGREGATE",
        )
    )
    return {
        "artifactHash": "a" * 64,
        "contractId": "market_source_entitlement.v1",
        "entitlements": entries,
        "evaluatedAt": "2026-07-31T00:00:00Z",
        "inventoryAuthority": "LOCAL_PRIVATE_REGISTRY",
        "payloadHash": "b" * 64,
        "publicIdentityMode": "OPAQUE_SHA256_ONLY",
        "registryId": "s4-8a-source-entitlements-20260731",
        "schemaVersion": "1",
    }


def _exposure_fixture() -> dict[str, Any]:
    return {
        "artifactHash": "c" * 64,
        "availableAt": "2026-07-31T00:00:01Z",
        "classification": "SEMICONDUCTOR",
        "configVersion": "cross-market-exposure.v1",
        "contractId": "cross_market_exposure_catalog.v1",
        "effectiveAt": "2026-07-31T00:00:00Z",
        "inScope": True,
        "logicalIdentityHash": "d" * 64,
        "payloadHash": "e" * 64,
        "schemaVersion": "1",
        "sourceLineage": ["f" * 64],
        "symbol": "NVDA",
        "validationState": "AVAILABLE",
    }


def _observation_fixture() -> dict[str, Any]:
    return {
        "artifactHash": "1" * 64,
        "availableAt": "2026-07-31T00:00:03Z",
        "completeness": "COMPLETE",
        "contractId": "cross_market_observation.v1",
        "decisionAuthority": "NONE",
        "evaluatedAt": "2026-07-31T00:00:04Z",
        "instrument": "NVDA",
        "logicalIdentityHash": "2" * 64,
        "market": "XNAS",
        "observedAt": "2026-07-31T00:00:00Z",
        "payloadHash": "3" * 64,
        "receivedAt": "2026-07-31T00:00:02Z",
        "schemaVersion": "1",
        "sessionDate": "2026-07-30",
        "sourceRef": "4" * 64,
        "status": "AVAILABLE",
        "timeframe": "EOD",
        "value": -0.03125,
        "valueType": "SESSION_RETURN",
    }


def _analyst_fixture() -> dict[str, Any]:
    return {
        "artifactHash": "5" * 64,
        "availableAt": "2026-07-31T00:00:03Z",
        "brokerId": "broker_0123456789abcdef",
        "buyOpinionWeight": 0,
        "contractId": "analyst_revision_evidence.v1",
        "contributorCount": 3,
        "current": {
            "eps": 8.0,
            "rating": "BUY",
            "revenue": 980.0,
            "targetPrice": 110.0,
        },
        "decisionAuthority": "NONE",
        "dedupeKeyHash": "6" * 64,
        "dispersion": 0.12,
        "estimatePeriod": "2026Q3",
        "logicalIdentityHash": "7" * 64,
        "originalEvidenceId": "analyst_revision_0001",
        "payloadHash": "8" * 64,
        "previous": {
            "eps": 10.0,
            "rating": "BUY",
            "revenue": 1000.0,
            "targetPrice": 120.0,
        },
        "publishedAt": "2026-07-31T00:00:00Z",
        "rawTextStored": False,
        "receivedAt": "2026-07-31T00:00:02Z",
        "retracted": False,
        "revision": {
            "epsDelta": -2.0,
            "ratingChanged": False,
            "revenueDelta": -20.0,
            "targetPriceDelta": -10.0,
        },
        "schemaVersion": "1",
        "sourceLicense": "STRUCTURED_FIXTURE",
        "supersedesEvidenceId": None,
        "symbol": "NVDA",
        "userConfirmedTags": ["CHANGE", "RISK"],
    }


def _cause_fixture() -> dict[str, Any]:
    return {
        "artifactHash": "9" * 64,
        "availableAt": "2026-07-31T00:00:03Z",
        "classification": "MARKET_INTERPRETATION",
        "contractId": "market_cause_evidence.v1",
        "contradictionEvidenceIds": ["cause_counter_0001"],
        "counterargument": False,
        "decisionAuthority": "NONE",
        "dedupeKeyHash": "a" * 64,
        "logicalIdentityHash": "b" * 64,
        "occurredAt": "2026-07-31T00:00:00Z",
        "payloadHash": "c" * 64,
        "publishedAt": "2026-07-31T00:00:01Z",
        "receivedAt": "2026-07-31T00:00:02Z",
        "relatedEvidenceIds": [],
        "relation": "REPORTED_AS_CAUSE",
        "retracted": False,
        "sanitizedSummary": "합성 aggregate의 관심도와 톤 변화가 같은 기간에 관측되었다.",
        "schemaVersion": "1",
        "sourceFamily": "GDELT_AGGREGATE",
        "sourceLineageHash": "d" * 64,
        "supersedesEvidenceId": None,
    }


def _risk_snapshot_fixture() -> dict[str, Any]:
    components = ["SEMICONDUCTOR", "BROAD_MARKET", "FX", "DOMESTIC_AMPLIFICATION"]
    return {
        "artifactHash": "e" * 64,
        "availability": "AVAILABLE",
        "broadRiskOffScore": 72.5,
        "configVersion": "cross-market-risk-config.v1",
        "contractId": "cross_market_risk_snapshot.v1",
        "decisionAuthority": "NEW_BUY_ALLOW_TO_WARN_ONLY",
        "domesticLeverageStressScore": 81.25,
        "evidenceMode": "SYNTHETIC_FIXTURE",
        "evidenceRefs": ["f" * 64],
        "freshness": [
            {
                "asOf": "2026-07-31T00:00:00Z",
                "component": component,
                "freshUntil": "2026-08-01T00:00:00Z",
                "sourceRefs": [f"{index:x}" * 64],
                "state": "AVAILABLE",
            }
            for index, component in enumerate(components, start=1)
        ],
        "fxStressScore": 63.0,
        "logicalIdentityHash": "0" * 64,
        "mode": "WARN_ONLY",
        "orderAuthority": "NONE",
        "owner": "1" * 64,
        "payloadHash": "2" * 64,
        "performanceClaimAllowed": False,
        "schemaVersion": "1",
        "semiconductorShockScore": 88.75,
        "timing": {
            "detectionLatencyMillis": 900000,
            "preOpenLeadTimeMillis": 13500000,
            "preOpenState": "EARLY",
            "snapshotAvailableAt": "2026-07-31T00:15:00Z",
            "sourceAvailableAt": "2026-07-31T00:00:00Z",
            "xkrxOpenAt": "2026-07-31T04:00:00Z",
        },
        "upstreamArtifactHashes": ["3" * 64, "4" * 64, "5" * 64, "6" * 64],
        "validationStatus": "UNVALIDATED",
    }


def _policy_fixture() -> dict[str, Any]:
    return {
        "artifactHash": "7" * 64,
        "contractId": "cross_market_policy_evaluation.v1",
        "decisionAuthority": "NONE",
        "estimationStatus": "ESTIMABLE",
        "evaluationKind": "EVENT_STUDY",
        "frozenThreshold": 80.0,
        "logicalIdentityHash": "8" * 64,
        "metrics": {
            "conflictRate": 0.05,
            "coverageRate": 0.95,
            "downsideAvoidedBps": 42.0,
            "falseBlockRate": 0.0,
            "latencyP95Millis": 900000,
            "missedUpsideBps": 12.0,
            "netProtectionBps": 30.0,
            "staleRate": 0.02,
            "unsupportedRate": 0.01,
        },
        "payloadHash": "9" * 64,
        "runtimeRiskEngineSource": False,
        "schemaVersion": "1",
        "severeLossCutoffBps": -300.0,
        "split": {
            "embargoSessions": 5,
            "purgeSessions": 5,
            "testPercent": 20,
            "trainPercent": 60,
            "validationPercent": 20,
        },
        "thresholdFrozen": True,
        "triggerCount": 12,
    }


def _positive_fixtures() -> dict[str, dict[str, Any]]:
    return {
        "contracts/examples/market_source_entitlement.v1.valid.json": _entitlement_fixture(),
        "contracts/examples/cross_market_exposure_catalog.v1.valid.json": _exposure_fixture(),
        "contracts/examples/cross_market_observation.v1.valid.json": _observation_fixture(),
        "contracts/examples/analyst_revision_evidence.v1.valid.json": _analyst_fixture(),
        "contracts/examples/market_cause_evidence.v1.valid.json": _cause_fixture(),
        "contracts/examples/cross_market_risk_snapshot.v1.valid.json": _risk_snapshot_fixture(),
        "contracts/examples/cross_market_policy_evaluation.v1.valid.json": _policy_fixture(),
    }


def _invalid_fixtures() -> dict[str, dict[str, Any]]:
    entitlement = _entitlement_fixture()
    expired = copy.deepcopy(entitlement)
    expired["entitlements"][0]["contractExpiry"] = "2026-07-30T23:59:59Z"

    raw_right = copy.deepcopy(entitlement)
    raw_right["entitlements"][0]["materializationDeclaration"]["rawStored"] = True
    embedding_right = copy.deepcopy(entitlement)
    embedding_right["entitlements"][0]["materializationDeclaration"]["embedded"] = True
    external_right = copy.deepcopy(entitlement)
    external_right["entitlements"][0]["materializationDeclaration"][
        "externalLlmProcessed"
    ] = True
    derived_right = copy.deepcopy(entitlement)
    derived_right["entitlements"][0]["materializationDeclaration"][
        "derivedPayloadProduced"
    ] = True
    unknown_endpoint = copy.deepcopy(entitlement)
    unknown_endpoint["entitlements"][0]["endpointIdentityHash"] = "f" * 64

    future = _observation_fixture()
    future["availableAt"] = "2026-07-31T00:00:05Z"
    fake_zero = _observation_fixture()
    fake_zero["status"] = "ABSTAIN"
    fake_zero["completeness"] = "MISSING"
    fake_zero["abstainReason"] = "SOURCE_MISSING"
    fake_zero["value"] = 0
    incomplete_available = _observation_fixture()
    incomplete_available["completeness"] = "INCOMPLETE"

    escalation = _risk_snapshot_fixture()
    escalation["decisionAuthority"] = "NEW_BUY_ALLOW_TO_BLOCK"

    article_metadata = _cause_fixture()
    article_metadata["articleUrl"] = "https://example.test/article/1"
    article_metadata["articleText"] = "synthetic article text"

    return {
        "contracts/examples/invalid/market_source_entitlement.v1.expired-entitlement.invalid.json": expired,
        "contracts/examples/invalid/market_source_entitlement.v1.raw-right-missing.invalid.json": raw_right,
        "contracts/examples/invalid/market_source_entitlement.v1.embedding-right-missing.invalid.json": embedding_right,
        "contracts/examples/invalid/market_source_entitlement.v1.external-llm-right-missing.invalid.json": external_right,
        "contracts/examples/invalid/market_source_entitlement.v1.derived-right-missing.invalid.json": derived_right,
        "contracts/examples/invalid/market_source_entitlement.v1.unknown-endpoint.invalid.json": unknown_endpoint,
        "contracts/examples/invalid/cross_market_observation.v1.future-available-at.invalid.json": future,
        "contracts/examples/invalid/cross_market_observation.v1.fake-zero.invalid.json": fake_zero,
        "contracts/examples/invalid/cross_market_observation.v1.incomplete-available.invalid.json": incomplete_available,
        "contracts/examples/invalid/cross_market_risk_snapshot.v1.risk-authority-escalation.invalid.json": escalation,
        "contracts/examples/invalid/market_cause_evidence.v1.gdelt-article-metadata.invalid.json": article_metadata,
    }


VALID_FIXTURE_PATHS: Final[frozenset[str]] = frozenset(_positive_fixtures())
INVALID_FIXTURE_PATHS: Final[frozenset[str]] = frozenset(_invalid_fixtures())


def _get_contract() -> dict[str, Any]:
    return {
        "authenticated": True,
        "contractId": "s4-8a-cross-market-get.v1",
        "dataFieldAllowlist": [
            "artifactHash",
            "availability",
            "broadRiskOffScore",
            "configVersion",
            "domesticLeverageStressScore",
            "evidence",
            "evidenceMode",
            "freshness",
            "fxStressScore",
            "mode",
            "performanceClaimAllowed",
            "semiconductorShockScore",
            "timing",
            "validationStatus",
        ],
        "evidenceFieldAllowlist": [
            "classification",
            "counterargument",
            "evidenceId",
            "relation",
            "summary",
        ],
        "existingPayloadFieldAdditions": {
            "decisionRequest": 0,
            "decisionResponse": 0,
            "ragAsk": 0,
            "ragHistory": 0,
            "signalV1": 0,
            "signalV2": 0,
        },
        "maxEvidenceItems": 10,
        "method": "GET",
        "path": "/api/v1/risk/cross-market",
        "projection": "LATEST_OWNER_ONLY",
        "providerFanoutAllowed": False,
        "queryParameters": [],
        "runtimeStatus": "NOT_IMPLEMENTED",
    }


def _catalog_v2() -> dict[str, Any]:
    v1_path = ROOT / "contracts/catalogs/s2-2-system-rule-catalog.v1.json"
    v1 = load_json_bytes_strict(v1_path.read_bytes(), source=v1_path.as_posix())
    if not isinstance(v1, dict):
        raise ContractValidationError("S2.2 catalog v1 must be an object.")
    v2 = copy.deepcopy(v1)
    v2["catalogVersion"] = 2
    v2["readinessPolicyVersion"] = "s2-2-readiness-v2"
    v2["canonicalization"]["id"] = "HASH-CANONICALIZATION-S22-V3"
    v2["canonicalization"]["semanticMetricSnapshotVersion"] = "s2.2-metric-snapshot-v3"
    v2["bounds"]["issueMaxItems"] = 15
    v2["bounds"]["violationMaxItems"] = 15
    v2["rules"].append(
        {
            "applicability": "in-scope new BUY with stored cross-market snapshot and exposure",
            "defaultSeverity": "WARN",
            "defaultThreshold": 80,
            "evidenceCriticality": "OPTIONAL",
            "executionKind": "THRESHOLD",
            "freshnessPolicy": "cross-market-stored-snapshot-v1",
            "maximum": 100,
            "metric": "cross_market_new_buy_risk_score",
            "minimum": 0,
            "operator": ">=",
            "order": 15,
            "ownership": "SYSTEM_MANAGED",
            "ruleId": "cross_market_new_buy_guard",
            "scale": 2,
            "severitySource": "P1_WARN_ONLY_FIXED",
            "thresholdSource": "S6_6_FROZEN_THRESHOLD",
            "unit": "score_0_100",
        }
    )
    v2["systemManagedRuleIds"].append("cross_market_new_buy_guard")
    return v2


def _hash_vector_v3() -> dict[str, Any]:
    semantic_input = {
        "crossMarketDecisionInput": {
            "availability": "AVAILABLE",
            "broadRiskOffScore": 72.5,
            "configVersion": "cross-market-risk-config.v1",
            "domesticLeverageStressScore": 81.25,
            "exposure": {
                "classification": "SEMICONDUCTOR",
                "configVersion": "cross-market-exposure.v1",
                "inScope": True,
                "symbol": "005930",
                "validationState": "AVAILABLE",
            },
            "freshness": [
                {"component": "BROAD_MARKET", "state": "AVAILABLE"},
                {"component": "DOMESTIC_AMPLIFICATION", "state": "AVAILABLE"},
                {"component": "FX", "state": "AVAILABLE"},
                {"component": "SEMICONDUCTOR", "state": "AVAILABLE"},
            ],
            "fxStressScore": 63,
            "mode": "WARN_ONLY",
            "semiconductorShockScore": 88.75,
        },
        "metricSnapshotVersion": "s2.2-metric-snapshot-v3",
        "orderIntent": {
            "estimatedAmount": 70000,
            "estimatedPrice": 70000,
            "orderType": "MARKET",
            "quantity": 1,
            "side": "BUY",
            "strategyId": "lightgbm-v1",
            "symbol": "005930",
            "timeframe": "1d",
        },
        "snapshotV2SemanticInputHash": load_json_bytes_strict(
            (ROOT / "contracts/examples/s2-2-hash-vector.valid.json").read_bytes(),
            source="contracts/examples/s2-2-hash-vector.valid.json",
        )["semanticInputHash"],
    }
    canonical = json.dumps(
        semantic_input,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "canonicalizationId": "HASH-CANONICALIZATION-S22-V3",
        "catalogVersion": 2,
        "compatibility": {
            "catalogV1Sha256": FROZEN_EXISTING_PAYLOAD_HASHES[
                "contracts/catalogs/s2-2-system-rule-catalog.v1.json"
            ],
            "hashVectorV2Sha256": FROZEN_EXISTING_PAYLOAD_HASHES[
                "contracts/examples/s2-2-hash-vector.valid.json"
            ],
            "v1Unchanged": True,
        },
        "excludedFields": [
            "analystEvidence",
            "artifactHash",
            "causeEvidence",
            "evidenceMode",
            "newsEvidence",
            "performanceClaimAllowed",
            "ragOutput",
            "snapshotAsOf",
            "snapshotId",
        ],
        "metricSnapshotVersion": "s2.2-metric-snapshot-v3",
        "semanticInput": semantic_input,
        "semanticInputHash": hashlib.sha256(canonical).hexdigest(),
    }


def _parse_timestamp(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ContractValidationError(f"{field} must be canonical UTC.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ContractValidationError(f"{field} must be a date-time.") from error
    if parsed.tzinfo != UTC:
        raise ContractValidationError(f"{field} must be canonical UTC.")
    return parsed


def _validate_entitlement(payload: Mapping[str, Any]) -> None:
    evaluated_at = _parse_timestamp(payload["evaluatedAt"], field="evaluatedAt")
    entries = payload["entitlements"]
    if not isinstance(entries, list):
        raise ContractValidationError("entitlements must be an array.")
    kis = [entry for entry in entries if entry["sourceFamily"] == "KIS"]
    gdelt = [entry for entry in entries if entry["sourceFamily"] == "GDELT_AGGREGATE"]
    if len(kis) != 18 or len(gdelt) != 1:
        raise ContractValidationError(
            "exact KIS 18 plus one GDELT aggregate row required."
        )
    if {entry["endpointIdentityHash"] for entry in kis} != set(
        KIS_ENDPOINT_IDENTITY_HASHES
    ):
        raise ContractValidationError("KIS opaque endpoint identity set drift.")
    category_counts = {
        category: sum(entry["category"] == category for entry in kis)
        for category in ("ANALYST", "OVERSEAS_LEAD", "DOMESTIC_AMPLIFICATION")
    }
    if category_counts != {
        "ANALYST": 3,
        "OVERSEAS_LEAD": 4,
        "DOMESTIC_AMPLIFICATION": 11,
    }:
        raise ContractValidationError("KIS category cardinality drift.")
    for index, entry in enumerate(entries):
        expiry = _parse_timestamp(
            entry["contractExpiry"], field=f"entitlements[{index}].contractExpiry"
        )
        if expiry <= evaluated_at:
            raise ContractValidationError("expired entitlement is not usable.")
        if entry["activationStatus"] != "CANDIDATE_DISABLED":
            raise ContractValidationError("S4.8A candidates must remain disabled.")
        if entry["providerCallsAllowed"]:
            raise ContractValidationError("S4.8A provider calls must remain zero.")
        if (
            entry["sourceFamily"] == "GDELT_AGGREGATE"
            and entry["category"] != "NEWS_AGGREGATE"
        ):
            raise ContractValidationError(
                "GDELT aggregate must remain a separate news source."
            )


def _validate_chronology(payload: Mapping[str, Any], fields: tuple[str, ...]) -> None:
    values = [_parse_timestamp(payload[field], field=field) for field in fields]
    if values != sorted(values):
        raise ContractValidationError(f"timestamp order must be {' <= '.join(fields)}.")


def _validate_risk_snapshot(payload: Mapping[str, Any]) -> None:
    freshness = payload["freshness"]
    components = [item["component"] for item in freshness]
    if sorted(components) != sorted(
        ["SEMICONDUCTOR", "BROAD_MARKET", "FX", "DOMESTIC_AMPLIFICATION"]
    ):
        raise ContractValidationError(
            "freshness must contain the exact four components."
        )
    if (
        payload["mode"] == "WARN_ONLY"
        and payload["decisionAuthority"] != "NEW_BUY_ALLOW_TO_WARN_ONLY"
    ):
        raise ContractValidationError("P1 authority escalation is forbidden.")
    timing = payload["timing"]
    source_at = _parse_timestamp(timing["sourceAvailableAt"], field="sourceAvailableAt")
    snapshot_at = _parse_timestamp(
        timing["snapshotAvailableAt"], field="snapshotAvailableAt"
    )
    expected_detection = int((snapshot_at - source_at).total_seconds() * 1000)
    if timing["detectionLatencyMillis"] != expected_detection or expected_detection < 0:
        raise ContractValidationError("detection latency drift.")
    xkrx = timing["xkrxOpenAt"]
    if xkrx is None:
        if (
            timing["preOpenLeadTimeMillis"] is not None
            or timing["preOpenState"] != "NOT_APPLICABLE"
        ):
            raise ContractValidationError("not-applicable XKRX timing drift.")
        return
    open_at = _parse_timestamp(xkrx, field="xkrxOpenAt")
    expected_lead = int((open_at - snapshot_at).total_seconds() * 1000)
    expected_state = (
        "EARLY" if expected_lead > 0 else "AT_OPEN" if expected_lead == 0 else "LATE"
    )
    if (
        timing["preOpenLeadTimeMillis"] != expected_lead
        or timing["preOpenState"] != expected_state
    ):
        raise ContractValidationError("pre-open timing drift.")


def validate_semantics(contract_id: str, payload: Mapping[str, Any]) -> None:
    """입력 contract의 cross-field 시간·권한·집계 불변식을 fail-closed로 검증한다."""

    if contract_id == "market_source_entitlement.v1":
        _validate_entitlement(payload)
    elif contract_id == "cross_market_exposure_catalog.v1":
        _validate_chronology(payload, ("effectiveAt", "availableAt"))
    elif contract_id == "cross_market_observation.v1":
        _validate_chronology(
            payload, ("observedAt", "receivedAt", "availableAt", "evaluatedAt")
        )
    elif contract_id == "analyst_revision_evidence.v1":
        _validate_chronology(payload, ("publishedAt", "receivedAt", "availableAt"))
    elif contract_id == "market_cause_evidence.v1":
        _validate_chronology(
            payload, ("occurredAt", "publishedAt", "receivedAt", "availableAt")
        )
    elif contract_id == "cross_market_risk_snapshot.v1":
        _validate_risk_snapshot(payload)
    elif contract_id == "cross_market_policy_evaluation.v1":
        metrics = payload["metrics"]
        if payload["estimationStatus"] == "ESTIMABLE":
            if any(value is None for value in metrics.values()):
                raise ContractValidationError("estimable metrics must be complete.")
            if (
                metrics["netProtectionBps"]
                != metrics["downsideAvoidedBps"] - metrics["missedUpsideBps"]
            ):
                raise ContractValidationError("net protection BPS formula drift.")
    else:
        raise ContractValidationError(f"unknown S4.8A contract: {contract_id}")


def generate_outputs() -> dict[str, bytes]:
    """일곱 SSOT와 fixture/catalog/hash 계약을 결정적인 canonical bytes로 생성한다."""

    outputs: dict[str, bytes] = {}
    schemas = _schemas()
    for contract_id, schema in schemas.items():
        Draft202012Validator.check_schema(schema)
        outputs[f"contracts/schemas/{contract_id}.schema.json"] = canonical_json_bytes(
            schema
        )
    outputs["contracts/schemas/s2-2-hash-vector.v3.schema.json"] = canonical_json_bytes(
        _hash_vector_v3_schema()
    )
    for path, payload in {**_positive_fixtures(), **_invalid_fixtures()}.items():
        outputs[path] = canonical_json_bytes(payload)
    outputs["contracts/catalogs/s4-8a-cross-market-get.v1.json"] = canonical_json_bytes(
        _get_contract()
    )
    outputs["contracts/catalogs/s2-2-system-rule-catalog.v2.json"] = (
        canonical_json_bytes(_catalog_v2())
    )
    outputs["contracts/examples/s2-2-hash-vector.v3.valid.json"] = canonical_json_bytes(
        _hash_vector_v3()
    )
    return outputs


OUTPUTS: Final[frozenset[str]] = frozenset(generate_outputs())


def _write_atomic(path: Path, payload: bytes) -> None:
    # 계약 파일을 symlink/partial write로 바꾸지 않도록 sibling exclusive file 뒤 원자 교체한다.
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ContractValidationError(f"refusing symlink output: {path}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o644)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_outputs(outputs: Mapping[str, bytes]) -> None:
    for relative_path, payload in sorted(outputs.items()):
        _write_atomic(ROOT / relative_path, payload)


def _check_outputs(outputs: Mapping[str, bytes]) -> None:
    mismatches: list[str] = []
    for relative_path, expected in sorted(outputs.items()):
        path = ROOT / relative_path
        if not path.is_file() or path.is_symlink() or path.read_bytes() != expected:
            mismatches.append(relative_path)
    if mismatches:
        joined = "\n".join(f"- {path}" for path in mismatches)
        raise ContractValidationError(f"generated S4.8A artifacts drifted:\n{joined}")


def main(argv: list[str] | None = None) -> int:
    """CLI는 write 또는 byte-identical check만 수행하며 provider/network를 사용하지 않는다."""

    parser = argparse.ArgumentParser()
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
    print("S4_8A_CROSS_MARKET_CONTRACT_LOCK_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
