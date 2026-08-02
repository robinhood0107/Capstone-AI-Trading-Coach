from __future__ import annotations

import argparse
import copy
import hashlib
import os
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Collection, Final, Mapping

from jsonschema import Draft202012Validator

_SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_REPO_ROOT))

from contracts.generated_artifact_io import write_generated_artifact  # noqa: E402
from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    canonical_json_bytes,
    load_json_bytes_strict,
)


ROOT = _SCRIPT_REPO_ROOT
HASH_PATTERN: Final[str] = "^[0-9a-f]{64}$"
SHA1_PATTERN: Final[str] = "^[0-9a-f]{40}$"
CORE6_SOURCE_FAMILIES: Final[tuple[str, ...]] = (
    "KIS",
    "OPENDART",
    "SEC_EDGAR",
    "KRX",
    "KOFIA",
    "ECOS",
)
SOURCE_ID_BY_FAMILY: Final[dict[str, str]] = {
    "KIS": "S48_CORE6_KIS",
    "OPENDART": "S48_CORE6_OPENDART",
    "SEC_EDGAR": "S48_CORE6_SEC_EDGAR",
    "KRX": "S48_CORE6_KRX",
    "KOFIA": "S48_CORE6_KOFIA",
    "ECOS": "S48_CORE6_ECOS",
}
ENDPOINT_SET_COUNT_BY_FAMILY: Final[dict[str, int]] = {
    "KIS": 18,
    "OPENDART": 0,
    "SEC_EDGAR": 2,
    "KRX": 2,
    "KOFIA": 1,
    "ECOS": 0,
}
DIRECT_READ_FAMILIES: Final[frozenset[str]] = frozenset(
    {"KIS", "SEC_EDGAR", "KRX", "KOFIA"}
)
PROJECTION_ONLY_FAMILIES: Final[frozenset[str]] = frozenset({"OPENDART", "ECOS"})
# KOFIA는 v2 registry에서 credential/approval evidence가 없어 BLOCKED다. 이 contract는
# 추후 entitlement amendment 전까지 실행 packet을 만들 수 있는 direct-read family를 좁힌다.
PROBE_ELIGIBLE_FAMILIES: Final[frozenset[str]] = frozenset(
    {"KIS", "SEC_EDGAR", "KRX"}
)
SCHEMA_IDS: Final[tuple[str, ...]] = (
    "market_source_entitlement.v2",
    "cross_market_provider_probe_approval.v1",
    "cross_market_provider_probe_receipt.v1",
)
SCHEMA_PATHS: Final[dict[str, Path]] = {
    schema_id: ROOT / f"contracts/schemas/{schema_id}.schema.json"
    for schema_id in SCHEMA_IDS
}

# v1/V23은 이미 배포된 fixture-only 경계다. Core 6 contract는 이를 수정하지 못한다.
FROZEN_V1_HASHES: Final[dict[str, str]] = {
    "contracts/schemas/market_source_entitlement.v1.schema.json": (
        "9448f3c93453c42be561230de915f7c1dcd4bbe064f365308b2cbba1b5f5a2c1"
    ),
    "contracts/examples/market_source_entitlement.v1.valid.json": (
        "38b3fb3ded793b1fa913837da85dd9037889a787c9ebe4912aeff91755eb3383"
    ),
    "contracts/generate_s4_8a_cross_market_contracts.py": (
        "7730bb4f47445b4ce73be0d2440073607ffc2de16684646cb4681df45a19545a"
    ),
    "contracts/catalogs/s4-8a-cross-market-get.v1.json": (
        "78275982d48b001db22d3482f6bb9aed874fb158addfcf08c0cf3352cde99c39"
    ),
    "workspaces/decision-platform/spring-api/src/main/resources/db/migration/"
    "V23__s4_8b_cross_market_evidence.sql": (
        "5430d482e556c755b528cbaa77cf0ba1e3dbc7302ad25da6961fbc0d76cf6772"
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


def _source_entry_schema() -> dict[str, Any]:
    return _closed_object(
        required=[
            "accessEvidenceDigest",
            "accountCallsAllowed",
            "activationBlocker",
            "activationStatus",
            "attributionRequired",
            "contractExpiry",
            "decisionAuthority",
            "deletionOwner",
            "derivedDataAllowed",
            "embeddingAllowed",
            "endpointSetCount",
            "endpointSetIdentityHash",
            "entitlementVersion",
            "externalLlmAllowed",
            "ingestionMode",
            "logicalIdentityHash",
            "machineFetchAllowed",
            "nonDisplayAllowed",
            "orderAuthority",
            "orderCallsAllowed",
            "projectionRetentionMaxDays",
            "providerCallsAllowed",
            "rawRetentionMaxHours",
            "rawStoreAllowed",
            "region",
            "riskEngineAuthority",
            "riskSignalOrderAuthority",
            "signalAuthority",
            "sourceFamily",
            "sourceId",
            "termsEvidenceDigest",
        ],
        properties={
            "accessEvidenceDigest": _hash_schema(),
            "accountCallsAllowed": {"const": False},
            "activationBlocker": {
                "enum": [
                    "APPROVAL_PACKET_REQUIRED",
                    "BLOCKED_NO_CREDENTIAL_OR_APPROVAL",
                    "REUSE_AUTHORIZED_PROJECTION_ONLY",
                ]
            },
            "activationStatus": {"enum": ["CANDIDATE_DISABLED", "BLOCKED"]},
            "attributionRequired": {"type": "boolean"},
            "contractExpiry": _timestamp_schema(),
            "decisionAuthority": {"const": "NONE"},
            "deletionOwner": {
                "type": "string",
                "pattern": "^[a-z0-9][a-z0-9:_-]{2,127}$",
            },
            "derivedDataAllowed": {"const": True},
            "embeddingAllowed": {"const": False},
            "endpointSetCount": {"type": "integer", "minimum": 0, "maximum": 18},
            "endpointSetIdentityHash": _hash_schema(),
            "entitlementVersion": {
                "type": "string",
                "pattern": "^[a-z0-9][a-z0-9._-]{0,63}$",
            },
            "ingestionMode": {
                "enum": ["DIRECT_READ_PROBE", "REUSE_AUTHORIZED_PROJECTION"]
            },
            "externalLlmAllowed": {"const": False},
            "logicalIdentityHash": _hash_schema(),
            "machineFetchAllowed": {"const": False},
            "nonDisplayAllowed": {"const": True},
            "orderAuthority": {"const": "NONE"},
            "orderCallsAllowed": {"const": False},
            "projectionRetentionMaxDays": {
                "type": "integer",
                "minimum": 1,
                "maximum": 3650,
            },
            "providerCallsAllowed": {"const": False},
            "rawRetentionMaxHours": {"const": 0},
            "rawStoreAllowed": {"const": False},
            "region": {"enum": ["KR", "US", "GLOBAL"]},
            "riskEngineAuthority": {"const": "NONE"},
            "riskSignalOrderAuthority": {"const": "NONE"},
            "signalAuthority": {"const": "NONE"},
            "sourceFamily": {"enum": list(CORE6_SOURCE_FAMILIES)},
            "sourceId": {"enum": list(SOURCE_ID_BY_FAMILY.values())},
            "termsEvidenceDigest": _hash_schema(),
        },
    )


def _entitlement_schema() -> dict[str, Any]:
    return _schema_document(
        "market_source_entitlement.v2",
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
                "contractId": {"const": "market_source_entitlement.v2"},
                "entitlements": {
                    "type": "array",
                    "minItems": 6,
                    "maxItems": 6,
                    "uniqueItems": True,
                    "items": _source_entry_schema(),
                },
                "evaluatedAt": _timestamp_schema(),
                "inventoryAuthority": {"const": "LOCAL_PRIVATE_REGISTRY"},
                "payloadHash": _hash_schema(),
                "publicIdentityMode": {"const": "OPAQUE_SHA256_ONLY"},
                "registryId": {"const": "s4-8-core6-source-entitlements-20260802"},
                "schemaVersion": {"const": "2"},
            },
        ),
    )


def _caps_schema() -> dict[str, Any]:
    return _closed_object(
        required=["artifactCap", "logicalCap", "physicalCallCap", "retryCap"],
        properties={
            "artifactCap": {"const": 0},
            "logicalCap": {"type": "integer", "minimum": 0, "maximum": 1},
            "physicalCallCap": {"type": "integer", "minimum": 0, "maximum": 1},
            "retryCap": {"const": 0},
        },
    )


def _probe_approval_schema() -> dict[str, Any]:
    body = _closed_object(
        required=[
            "approvalIdHash",
            "approvalPayloadHash",
            "approvalStatus",
            "artifactPersistence",
            "caps",
            "ciEvidenceDigest",
            "contractId",
            "coverageDigest",
            "decisionAuthority",
            "endpointSetIdentityHash",
            "entitlementPayloadHash",
            "executionAllowed",
            "executionMode",
            "expiresAt",
            "findingsDigest",
            "fixtureOnly",
            "headSha",
            "issuedAt",
            "nonceHash",
            "persistRaw",
            "providerCallsBeforeApproval",
            "registryPayloadHash",
            "requestPlanDigest",
            "riskSignalOrderAuthority",
            "schemaVersion",
            "securityScanManifestDigest",
            "sourceFamily",
            "sourceId",
            "treeDigest",
        ],
        properties={
            "approvalIdHash": _hash_schema(),
            "approvalPayloadHash": _hash_schema(),
            "approvalStatus": {"enum": ["TEMPLATE", "APPROVED", "CONSUMED", "EXPIRED"]},
            "artifactPersistence": {"const": False},
            "caps": _caps_schema(),
            "ciEvidenceDigest": _hash_schema(),
            "contractId": {"const": "cross_market_provider_probe_approval.v1"},
            "coverageDigest": _hash_schema(),
            "decisionAuthority": {"const": "NONE"},
            "endpointSetIdentityHash": _hash_schema(),
            "entitlementPayloadHash": _hash_schema(),
            "executionAllowed": {"type": "boolean"},
            "executionMode": {
                "enum": ["DIRECT_READ_PROBE", "REUSE_AUTHORIZED_PROJECTION"]
            },
            "expiresAt": _timestamp_schema(),
            "findingsDigest": _hash_schema(),
            "fixtureOnly": {"type": "boolean"},
            "headSha": {"type": "string", "pattern": SHA1_PATTERN},
            "issuedAt": _timestamp_schema(),
            "nonceHash": _hash_schema(),
            "persistRaw": {"const": False},
            "providerCallsBeforeApproval": {"const": 0},
            "registryPayloadHash": _hash_schema(),
            "requestPlanDigest": _hash_schema(),
            "riskSignalOrderAuthority": {"const": "NONE"},
            "schemaVersion": {"const": "1"},
            "securityScanManifestDigest": _hash_schema(),
            "sourceFamily": {"enum": list(CORE6_SOURCE_FAMILIES)},
            "sourceId": {"enum": list(SOURCE_ID_BY_FAMILY.values())},
            "treeDigest": _hash_schema(),
        },
    )
    body["allOf"] = [
        {
            "if": {"properties": {"approvalStatus": {"const": "TEMPLATE"}}},
            "then": {
                "properties": {
                    "executionAllowed": {"const": False},
                    "fixtureOnly": {"const": True},
                    "caps": {
                        "properties": {
                            "logicalCap": {"const": 0},
                            "physicalCallCap": {"const": 0},
                        }
                    },
                }
            },
        },
        {
            "if": {"properties": {"approvalStatus": {"const": "APPROVED"}}},
            "then": {
                "properties": {
                    "executionAllowed": {"const": True},
                    "fixtureOnly": {"const": False},
                    "caps": {
                        "properties": {
                            "logicalCap": {"const": 1},
                            "physicalCallCap": {"const": 1},
                        }
                    },
                }
            },
        },
        {
            "if": {
                "properties": {"approvalStatus": {"enum": ["CONSUMED", "EXPIRED"]}}
            },
            "then": {
                "properties": {
                    "executionAllowed": {"const": False},
                    "fixtureOnly": {"const": False},
                    "caps": {
                        "properties": {
                            "logicalCap": {"const": 0},
                            "physicalCallCap": {"const": 0},
                        }
                    },
                }
            },
        },
    ]
    return _schema_document("cross_market_provider_probe_approval.v1", body)


def _probe_receipt_schema() -> dict[str, Any]:
    body = _closed_object(
        required=[
            "approvalIdHash",
            "approvalPayloadHash",
            "approvedLogicalCap",
            "approvedPhysicalCallCap",
            "artifactsWritten",
            "completedAt",
            "contractId",
            "decisionAuthority",
            "endpointSetIdentityHash",
            "headSha",
            "logicalCalls",
            "outcome",
            "physicalCalls",
            "projectionHash",
            "providerStatusClass",
            "rawBodyStored",
            "rawHeaderStored",
            "rawQueryStored",
            "requestPlanDigest",
            "retries",
            "riskSignalOrderAuthority",
            "schemaVersion",
            "sensitiveMaterialStored",
            "sourceFamily",
            "sourceId",
            "startedAt",
            "steps",
        ],
        properties={
            "approvalIdHash": _hash_schema(),
            "approvalPayloadHash": _hash_schema(),
            "approvedLogicalCap": {"type": "integer", "minimum": 0, "maximum": 1},
            "approvedPhysicalCallCap": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1,
            },
            "artifactsWritten": {"const": 0},
            "completedAt": _timestamp_schema(),
            "contractId": {"const": "cross_market_provider_probe_receipt.v1"},
            "decisionAuthority": {"const": "NONE"},
            "endpointSetIdentityHash": _hash_schema(),
            "headSha": {"type": "string", "pattern": SHA1_PATTERN},
            "logicalCalls": {"type": "integer", "minimum": 0, "maximum": 1},
            "outcome": {"enum": ["NOT_EXECUTED", "SUCCESS", "FAILED"]},
            "physicalCalls": {"type": "integer", "minimum": 0, "maximum": 1},
            "projectionHash": {
                "type": ["string", "null"],
                "pattern": HASH_PATTERN,
            },
            "providerStatusClass": {
                "enum": ["NOT_ATTEMPTED", "HTTP_2XX", "HTTP_4XX", "HTTP_5XX", "TRANSPORT"]
            },
            "rawBodyStored": {"const": False},
            "rawHeaderStored": {"const": False},
            "rawQueryStored": {"const": False},
            "requestPlanDigest": _hash_schema(),
            "retries": {"const": 0},
            "riskSignalOrderAuthority": {"const": "NONE"},
            "schemaVersion": {"const": "1"},
            "sensitiveMaterialStored": {"const": False},
            "sourceFamily": {"enum": list(CORE6_SOURCE_FAMILIES)},
            "sourceId": {"enum": list(SOURCE_ID_BY_FAMILY.values())},
            "startedAt": _timestamp_schema(),
            "steps": {
                "type": "array",
                "maxItems": 2,
                "items": _closed_object(
                    required=["outcome", "physicalCalls", "step"],
                    properties={
                        "outcome": {"enum": ["SUCCESS", "FAIL_CLOSED"]},
                        "physicalCalls": {"type": "integer", "minimum": 0, "maximum": 1},
                        "step": {
                            "enum": ["AUTH_TOKEN", "DATA_REQUEST", "PROJECTION_READ"]
                        },
                    },
                ),
            },
        },
    )
    body["allOf"] = [
        {
            "if": {"properties": {"outcome": {"const": "NOT_EXECUTED"}}},
            "then": {
                "properties": {
                    "logicalCalls": {"const": 0},
                    "physicalCalls": {"const": 0},
                    "projectionHash": {"const": None},
                    "providerStatusClass": {"const": "NOT_ATTEMPTED"},
                    "steps": {"const": []},
                }
            },
        },
        {
            "if": {"properties": {"outcome": {"const": "SUCCESS"}}},
            "then": {
                "properties": {
                    "approvedLogicalCap": {"const": 1},
                    "approvedPhysicalCallCap": {"const": 1},
                    "logicalCalls": {"const": 1},
                    "physicalCalls": {"const": 1},
                    "projectionHash": _hash_schema(),
                    "providerStatusClass": {"const": "HTTP_2XX"},
                    "steps": {
                        "minItems": 1,
                        "maxItems": 1,
                        "items": {
                            "properties": {
                                "outcome": {"const": "SUCCESS"},
                                "physicalCalls": {"const": 1},
                                "step": {"const": "DATA_REQUEST"},
                            }
                        },
                    },
                }
            },
        },
        {
            "if": {"properties": {"outcome": {"const": "FAILED"}}},
            "then": {
                "properties": {
                    "approvedLogicalCap": {"const": 1},
                    "approvedPhysicalCallCap": {"const": 1},
                    "logicalCalls": {"const": 1},
                    "physicalCalls": {"const": 1},
                    "projectionHash": {"const": None},
                    "providerStatusClass": {
                        "enum": ["HTTP_4XX", "HTTP_5XX", "TRANSPORT"]
                    },
                    "steps": {
                        "minItems": 1,
                        "maxItems": 1,
                        "items": {
                            "properties": {
                                "outcome": {"const": "FAIL_CLOSED"},
                                "physicalCalls": {"const": 1},
                            }
                        },
                    },
                }
            },
        },
    ]
    return _schema_document("cross_market_provider_probe_receipt.v1", body)


def _schemas() -> dict[str, dict[str, Any]]:
    return {
        "market_source_entitlement.v2": _entitlement_schema(),
        "cross_market_provider_probe_approval.v1": _probe_approval_schema(),
        "cross_market_provider_probe_receipt.v1": _probe_receipt_schema(),
    }


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _endpoint_set_identity_hash(family: str) -> str:
    """Public contract에는 endpoint 문자열 대신 source-family 결속 hash만 남긴다."""

    return _sha(f"s4-8-core6:{family}:opaque-endpoint-set-v1")


def _source_spec(family: str) -> dict[str, str]:
    source_id = SOURCE_ID_BY_FAMILY[family]
    if family in PROJECTION_ONLY_FAMILIES:
        return {
            "activationBlocker": "REUSE_AUTHORIZED_PROJECTION_ONLY",
            "activationStatus": "CANDIDATE_DISABLED",
            "ingestionMode": "REUSE_AUTHORIZED_PROJECTION",
            "region": "KR",
            "sourceId": source_id,
        }
    if family == "KOFIA":
        return {
            "activationBlocker": "BLOCKED_NO_CREDENTIAL_OR_APPROVAL",
            "activationStatus": "BLOCKED",
            "ingestionMode": "DIRECT_READ_PROBE",
            "region": "KR",
            "sourceId": source_id,
        }
    return {
        "activationBlocker": "APPROVAL_PACKET_REQUIRED",
        "activationStatus": "CANDIDATE_DISABLED",
        "ingestionMode": "DIRECT_READ_PROBE",
        "region": "US" if family == "SEC_EDGAR" else "KR",
        "sourceId": source_id,
    }


def _entitlement_entry(family: str) -> dict[str, Any]:
    spec = _source_spec(family)
    endpoint_set_hash = _endpoint_set_identity_hash(family)
    return {
        # 이 public fixture는 실제 entitlement 증빙을 포함하지 않는다. 실제 digest는
        # local-private registry와 실행 직전 approval packet에만 넣어 scanner 노출을 막는다.
        "accessEvidenceDigest": f"{CORE6_SOURCE_FAMILIES.index(family):064x}",
        "accountCallsAllowed": False,
        "activationBlocker": spec["activationBlocker"],
        "activationStatus": spec["activationStatus"],
        "attributionRequired": family in {"SEC_EDGAR", "KRX", "KOFIA"},
        "contractExpiry": "2027-08-02T00:00:00Z",
        "decisionAuthority": "NONE",
        "deletionOwner": "decision-platform:cross-market-retention",
        "derivedDataAllowed": True,
        "embeddingAllowed": False,
        "endpointSetCount": ENDPOINT_SET_COUNT_BY_FAMILY[family],
        "endpointSetIdentityHash": endpoint_set_hash,
        "entitlementVersion": "core6-contract-v2",
        "ingestionMode": spec["ingestionMode"],
        "externalLlmAllowed": False,
        "logicalIdentityHash": _sha(f"s4-8-core6:{family}:logical-v1"),
        "machineFetchAllowed": False,
        "nonDisplayAllowed": True,
        "orderAuthority": "NONE",
        "orderCallsAllowed": False,
        "projectionRetentionMaxDays": 365,
        "providerCallsAllowed": False,
        "rawRetentionMaxHours": 0,
        "rawStoreAllowed": False,
        "region": spec["region"],
        "riskEngineAuthority": "NONE",
        "riskSignalOrderAuthority": "NONE",
        "signalAuthority": "NONE",
        "sourceFamily": family,
        "sourceId": spec["sourceId"],
        "termsEvidenceDigest": _sha(f"s4-8-core6:{family}:terms-evidence-v1"),
    }


def _entitlement_fixture() -> dict[str, Any]:
    return {
        "artifactHash": _sha("s4-8-core6:entitlement-artifact-v1"),
        "contractId": "market_source_entitlement.v2",
        "entitlements": [_entitlement_entry(family) for family in CORE6_SOURCE_FAMILIES],
        "evaluatedAt": "2026-08-02T00:00:00Z",
        "inventoryAuthority": "LOCAL_PRIVATE_REGISTRY",
        "payloadHash": _sha("s4-8-core6:entitlement-payload-v1"),
        "publicIdentityMode": "OPAQUE_SHA256_ONLY",
        "registryId": "s4-8-core6-source-entitlements-20260802",
        "schemaVersion": "2",
    }


def _approval_template_fixture() -> dict[str, Any]:
    family = "KIS"
    entry = _entitlement_entry(family)
    return {
        "approvalIdHash": _sha("s4-8-core6:template-approval-id-v1"),
        "approvalPayloadHash": _sha("s4-8-core6:template-approval-payload-v1"),
        "approvalStatus": "TEMPLATE",
        "artifactPersistence": False,
        "caps": {
            "artifactCap": 0,
            "logicalCap": 0,
            "physicalCallCap": 0,
            "retryCap": 0,
        },
        "ciEvidenceDigest": _sha("s4-8-core6:template-ci-v1"),
        "contractId": "cross_market_provider_probe_approval.v1",
        "coverageDigest": _sha("s4-8-core6:template-coverage-v1"),
        "decisionAuthority": "NONE",
        "endpointSetIdentityHash": entry["endpointSetIdentityHash"],
        "entitlementPayloadHash": _sha("s4-8-core6:entitlement-payload-v1"),
        "executionAllowed": False,
        "executionMode": "DIRECT_READ_PROBE",
        "expiresAt": "2026-08-02T00:05:00Z",
        "findingsDigest": _sha("s4-8-core6:template-findings-v1"),
        "fixtureOnly": True,
        "headSha": "0" * 40,
        "issuedAt": "2026-08-02T00:00:00Z",
        "nonceHash": _sha("s4-8-core6:template-nonce-v1"),
        "persistRaw": False,
        "providerCallsBeforeApproval": 0,
        "registryPayloadHash": _sha("s4-8-core6:entitlement-payload-v1"),
        "requestPlanDigest": _sha("s4-8-core6:template-request-plan-v1"),
        "riskSignalOrderAuthority": "NONE",
        "schemaVersion": "1",
        "securityScanManifestDigest": _sha("s4-8-core6:template-security-manifest-v1"),
        "sourceFamily": family,
        "sourceId": SOURCE_ID_BY_FAMILY[family],
        "treeDigest": _sha("s4-8-core6:template-tree-v1"),
    }


def _receipt_fixture() -> dict[str, Any]:
    template = _approval_template_fixture()
    return {
        "approvalIdHash": template["approvalIdHash"],
        "approvalPayloadHash": template["approvalPayloadHash"],
        "approvedLogicalCap": 0,
        "approvedPhysicalCallCap": 0,
        "artifactsWritten": 0,
        "completedAt": "2026-08-02T00:00:00Z",
        "contractId": "cross_market_provider_probe_receipt.v1",
        "decisionAuthority": "NONE",
        "endpointSetIdentityHash": template["endpointSetIdentityHash"],
        "headSha": template["headSha"],
        "logicalCalls": 0,
        "outcome": "NOT_EXECUTED",
        "physicalCalls": 0,
        "projectionHash": None,
        "providerStatusClass": "NOT_ATTEMPTED",
        "rawBodyStored": False,
        "rawHeaderStored": False,
        "rawQueryStored": False,
        "requestPlanDigest": template["requestPlanDigest"],
        "retries": 0,
        "riskSignalOrderAuthority": "NONE",
        "schemaVersion": "1",
        "sensitiveMaterialStored": False,
        "sourceFamily": template["sourceFamily"],
        "sourceId": template["sourceId"],
        "startedAt": "2026-08-02T00:00:00Z",
        "steps": [],
    }


def _positive_fixtures() -> dict[str, dict[str, Any]]:
    return {
        "contracts/examples/market_source_entitlement.v2.valid.json": _entitlement_fixture(),
        "contracts/examples/cross_market_provider_probe_approval.v1.template.valid.json": _approval_template_fixture(),
        "contracts/examples/cross_market_provider_probe_receipt.v1.not-executed.valid.json": _receipt_fixture(),
    }


def _invalid_fixtures() -> dict[str, dict[str, Any]]:
    entitlement = _entitlement_fixture()
    unknown_source = copy.deepcopy(entitlement)
    unknown_source["entitlements"][0]["sourceFamily"] = "NAVER"

    direct_projection_fanout = copy.deepcopy(entitlement)
    projection_entry = next(
        entry
        for entry in direct_projection_fanout["entitlements"]
        if entry["sourceFamily"] == "OPENDART"
    )
    projection_entry["ingestionMode"] = "DIRECT_READ_PROBE"

    active_without_rights = copy.deepcopy(entitlement)
    active_without_rights["entitlements"][0]["activationStatus"] = "ACTIVE"
    active_without_rights["entitlements"][0]["providerCallsAllowed"] = True
    active_without_rights["entitlements"][0]["machineFetchAllowed"] = True

    raw_storage = copy.deepcopy(entitlement)
    raw_storage["entitlements"][0]["rawStoreAllowed"] = True

    endpoint_count = copy.deepcopy(entitlement)
    endpoint_count["entitlements"][0]["endpointSetCount"] = 17

    authority_escalation = copy.deepcopy(entitlement)
    authority_escalation["entitlements"][0]["orderAuthority"] = "ALLOW"

    approval = _approval_template_fixture()
    approval_retry = copy.deepcopy(approval)
    approval_retry["caps"]["retryCap"] = 1

    approval_expiry = copy.deepcopy(approval)
    approval_expiry["expiresAt"] = approval_expiry["issuedAt"]

    approval_request_query = copy.deepcopy(approval)
    approval_request_query["requestQuery"] = "symbol=005930"

    approved_probe = copy.deepcopy(approval)
    approved_probe["approvalStatus"] = "APPROVED"
    approved_probe["executionAllowed"] = True
    approved_probe["fixtureOnly"] = False
    approved_probe["caps"]["logicalCap"] = 1
    approved_probe["caps"]["physicalCallCap"] = 1

    approval_consumed_executable = copy.deepcopy(approved_probe)
    approval_consumed_executable["approvalStatus"] = "CONSUMED"

    approval_expired_executable = copy.deepcopy(approved_probe)
    approval_expired_executable["approvalStatus"] = "EXPIRED"

    approval_kofia_executable = copy.deepcopy(approved_probe)
    approval_kofia_executable["sourceFamily"] = "KOFIA"
    approval_kofia_executable["sourceId"] = SOURCE_ID_BY_FAMILY["KOFIA"]
    approval_kofia_executable["endpointSetIdentityHash"] = _endpoint_set_identity_hash(
        "KOFIA"
    )

    approval_endpoint_identity = copy.deepcopy(approved_probe)
    approval_endpoint_identity["endpointSetIdentityHash"] = "0" * 64

    receipt = _receipt_fixture()
    receipt_over_cap = copy.deepcopy(receipt)
    receipt_over_cap["physicalCalls"] = 1

    receipt_raw_storage = copy.deepcopy(receipt)
    receipt_raw_storage["rawBodyStored"] = True

    receipt_provider_body = copy.deepcopy(receipt)
    receipt_provider_body["providerBody"] = "must-never-be-persisted"

    receipt_success_zero_calls = copy.deepcopy(receipt)
    receipt_success_zero_calls["outcome"] = "SUCCESS"

    receipt_failed_zero_calls = copy.deepcopy(receipt)
    receipt_failed_zero_calls.update(
        {
            "approvedLogicalCap": 1,
            "approvedPhysicalCallCap": 1,
            "logicalCalls": 1,
            "outcome": "FAILED",
            "providerStatusClass": "HTTP_4XX",
            "steps": [
                {
                    "outcome": "FAIL_CLOSED",
                    "physicalCalls": 0,
                    "step": "DATA_REQUEST",
                }
            ],
        }
    )

    receipt_projection_provider_call = copy.deepcopy(receipt)
    receipt_projection_provider_call.update(
        {
            "approvedLogicalCap": 1,
            "approvedPhysicalCallCap": 1,
            "endpointSetIdentityHash": _endpoint_set_identity_hash("OPENDART"),
            "logicalCalls": 1,
            "outcome": "SUCCESS",
            "physicalCalls": 1,
            "projectionHash": _sha("s4-8-core6:projection-only-receipt-v1"),
            "providerStatusClass": "HTTP_2XX",
            "sourceFamily": "OPENDART",
            "sourceId": SOURCE_ID_BY_FAMILY["OPENDART"],
            "steps": [
                {
                    "outcome": "SUCCESS",
                    "physicalCalls": 1,
                    "step": "DATA_REQUEST",
                }
            ],
        }
    )

    return {
        "contracts/examples/invalid/market_source_entitlement.v2.unknown-source.invalid.json": unknown_source,
        "contracts/examples/invalid/market_source_entitlement.v2.direct-projection-fanout.invalid.json": direct_projection_fanout,
        "contracts/examples/invalid/market_source_entitlement.v2.active-without-rights.invalid.json": active_without_rights,
        "contracts/examples/invalid/market_source_entitlement.v2.raw-storage.invalid.json": raw_storage,
        "contracts/examples/invalid/market_source_entitlement.v2.endpoint-count.invalid.json": endpoint_count,
        "contracts/examples/invalid/market_source_entitlement.v2.authority-escalation.invalid.json": authority_escalation,
        "contracts/examples/invalid/cross_market_provider_probe_approval.v1.approval-retry.invalid.json": approval_retry,
        "contracts/examples/invalid/cross_market_provider_probe_approval.v1.approval-expiry.invalid.json": approval_expiry,
        "contracts/examples/invalid/cross_market_provider_probe_approval.v1.approval-request-query.invalid.json": approval_request_query,
        "contracts/examples/invalid/cross_market_provider_probe_approval.v1.approval-consumed-executable.invalid.json": approval_consumed_executable,
        "contracts/examples/invalid/cross_market_provider_probe_approval.v1.approval-expired-executable.invalid.json": approval_expired_executable,
        "contracts/examples/invalid/cross_market_provider_probe_approval.v1.approval-kofia-executable.invalid.json": approval_kofia_executable,
        "contracts/examples/invalid/cross_market_provider_probe_approval.v1.approval-endpoint-identity.invalid.json": approval_endpoint_identity,
        "contracts/examples/invalid/cross_market_provider_probe_receipt.v1.receipt-over-cap.invalid.json": receipt_over_cap,
        "contracts/examples/invalid/cross_market_provider_probe_receipt.v1.receipt-raw-storage.invalid.json": receipt_raw_storage,
        "contracts/examples/invalid/cross_market_provider_probe_receipt.v1.receipt-provider-body.invalid.json": receipt_provider_body,
        "contracts/examples/invalid/cross_market_provider_probe_receipt.v1.receipt-success-zero-calls.invalid.json": receipt_success_zero_calls,
        "contracts/examples/invalid/cross_market_provider_probe_receipt.v1.receipt-failed-zero-calls.invalid.json": receipt_failed_zero_calls,
        "contracts/examples/invalid/cross_market_provider_probe_receipt.v1.receipt-projection-provider-call.invalid.json": receipt_projection_provider_call,
    }


VALID_FIXTURE_PATHS: Final[frozenset[str]] = frozenset(_positive_fixtures())
INVALID_FIXTURE_PATHS: Final[frozenset[str]] = frozenset(_invalid_fixtures())


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
    families = {entry["sourceFamily"] for entry in entries}
    if families != set(CORE6_SOURCE_FAMILIES):
        raise ContractValidationError("Core 6 source-family set drift.")
    if len({entry["sourceId"] for entry in entries}) != len(entries):
        raise ContractValidationError("Core 6 source IDs must be unique.")
    for index, entry in enumerate(entries):
        family = entry["sourceFamily"]
        if entry["sourceId"] != SOURCE_ID_BY_FAMILY[family]:
            raise ContractValidationError("source ID does not match Core 6 family.")
        if entry["endpointSetCount"] != ENDPOINT_SET_COUNT_BY_FAMILY[family]:
            raise ContractValidationError("Core 6 endpoint-set cardinality drift.")
        expiry = _parse_timestamp(
            entry["contractExpiry"], field=f"entitlements[{index}].contractExpiry"
        )
        if expiry <= evaluated_at:
            raise ContractValidationError("expired Core 6 entitlement is unusable.")
        if entry["activationStatus"] not in {"CANDIDATE_DISABLED", "BLOCKED"}:
            raise ContractValidationError("Core 6 activation must remain disabled.")
        if entry["providerCallsAllowed"] or entry["machineFetchAllowed"]:
            raise ContractValidationError("Core 6 provider calls must remain zero.")
        if family in PROJECTION_ONLY_FAMILIES:
            if (
                entry["ingestionMode"] != "REUSE_AUTHORIZED_PROJECTION"
                or entry["activationBlocker"]
                != "REUSE_AUTHORIZED_PROJECTION_ONLY"
            ):
                raise ContractValidationError(
                    "OpenDART and ECOS must reuse existing authorized projections."
                )
        elif family == "KOFIA":
            if (
                entry["activationStatus"] != "BLOCKED"
                or entry["activationBlocker"]
                != "BLOCKED_NO_CREDENTIAL_OR_APPROVAL"
            ):
                raise ContractValidationError("KOFIA must remain credential/approval blocked.")
        elif (
            entry["ingestionMode"] != "DIRECT_READ_PROBE"
            or entry["activationBlocker"] != "APPROVAL_PACKET_REQUIRED"
        ):
            raise ContractValidationError("direct Core 6 sources require a later approval packet.")


def _validate_approval(payload: Mapping[str, Any]) -> None:
    issued_at = _parse_timestamp(payload["issuedAt"], field="issuedAt")
    expires_at = _parse_timestamp(payload["expiresAt"], field="expiresAt")
    elapsed_seconds = (expires_at - issued_at).total_seconds()
    if elapsed_seconds <= 0 or elapsed_seconds > 60 * 60:
        raise ContractValidationError("approval TTL must be greater than zero and at most 60 minutes.")
    family = payload["sourceFamily"]
    if payload["sourceId"] != SOURCE_ID_BY_FAMILY[family]:
        raise ContractValidationError("approval source identity drift.")
    if payload["endpointSetIdentityHash"] != _endpoint_set_identity_hash(family):
        raise ContractValidationError("approval endpoint-set identity drift.")
    if payload["entitlementPayloadHash"] != payload["registryPayloadHash"]:
        raise ContractValidationError("approval entitlement registry binding drift.")
    if payload["executionMode"] != _source_spec(family)["ingestionMode"]:
        raise ContractValidationError("approval execution mode does not match source family.")
    status = payload["approvalStatus"]
    caps = payload["caps"]
    if status == "TEMPLATE":
        if (
            payload["executionAllowed"]
            or not payload["fixtureOnly"]
            or caps["logicalCap"]
            or caps["physicalCallCap"]
        ):
            raise ContractValidationError("template must not authorize physical calls.")
    elif status == "APPROVED":
        if not payload["executionAllowed"]:
            raise ContractValidationError("approved packet must explicitly allow execution.")
        if caps["logicalCap"] != 1 or caps["physicalCallCap"] != 1:
            raise ContractValidationError("approved probe caps must be exactly one.")
        if payload["fixtureOnly"]:
            raise ContractValidationError("approved packet cannot be a fixture.")
        if family not in PROBE_ELIGIBLE_FAMILIES:
            raise ContractValidationError("source is not eligible for a direct Core 6 probe.")
    elif status in {"CONSUMED", "EXPIRED"}:
        if (
            payload["executionAllowed"]
            or payload["fixtureOnly"]
            or caps["logicalCap"]
            or caps["physicalCallCap"]
        ):
            raise ContractValidationError("consumed or expired packet must revoke all execution capacity.")


def _validate_receipt(payload: Mapping[str, Any]) -> None:
    started_at = _parse_timestamp(payload["startedAt"], field="startedAt")
    completed_at = _parse_timestamp(payload["completedAt"], field="completedAt")
    if completed_at < started_at:
        raise ContractValidationError("receipt completion cannot precede start.")
    family = payload["sourceFamily"]
    if payload["sourceId"] != SOURCE_ID_BY_FAMILY[family]:
        raise ContractValidationError("receipt source identity drift.")
    if payload["endpointSetIdentityHash"] != _endpoint_set_identity_hash(family):
        raise ContractValidationError("receipt endpoint-set identity drift.")
    if payload["logicalCalls"] > payload["approvedLogicalCap"]:
        raise ContractValidationError("logical calls exceed approved cap.")
    if payload["physicalCalls"] > payload["approvedPhysicalCallCap"]:
        raise ContractValidationError("physical calls exceed approved cap.")
    if payload["physicalCalls"] > payload["logicalCalls"]:
        raise ContractValidationError("physical calls cannot exceed logical calls.")
    steps = payload["steps"]
    if sum(step["physicalCalls"] for step in steps) != payload["physicalCalls"]:
        raise ContractValidationError("receipt step count must equal physical calls.")
    first_failure = next(
        (
            index
            for index, step in enumerate(steps)
            if step["outcome"] == "FAIL_CLOSED"
        ),
        None,
    )
    if first_failure is not None and first_failure != len(steps) - 1:
        raise ContractValidationError("no receipt step may follow the first failure.")
    outcome = payload["outcome"]
    if outcome == "NOT_EXECUTED":
        if any(value != 0 for value in (payload["logicalCalls"], payload["physicalCalls"])):
            raise ContractValidationError("not-executed receipt cannot report calls.")
        return
    if family not in PROBE_ELIGIBLE_FAMILIES:
        raise ContractValidationError("non-executed Core 6 source cannot report a direct probe.")
    if outcome == "SUCCESS":
        if (
            payload["approvedLogicalCap"] != 1
            or payload["approvedPhysicalCallCap"] != 1
            or payload["logicalCalls"] != 1
            or payload["physicalCalls"] != 1
            or payload["providerStatusClass"] != "HTTP_2XX"
            or not isinstance(payload["projectionHash"], str)
        ):
            raise ContractValidationError("successful receipt must prove exactly one data request.")
        if len(steps) != 1 or steps[0] != {
            "outcome": "SUCCESS",
            "physicalCalls": 1,
            "step": "DATA_REQUEST",
        }:
            raise ContractValidationError("successful receipt must contain one successful data request.")
    elif outcome == "FAILED":
        if (
            payload["approvedLogicalCap"] != 1
            or payload["approvedPhysicalCallCap"] != 1
            or payload["logicalCalls"] != 1
            or payload["physicalCalls"] != 1
            or payload["providerStatusClass"] not in {"HTTP_4XX", "HTTP_5XX", "TRANSPORT"}
            or payload["projectionHash"] is not None
            or len(steps) != 1
            or steps[0]["outcome"] != "FAIL_CLOSED"
            or steps[0]["physicalCalls"] != 1
        ):
            raise ContractValidationError("failed receipt must terminate after one fail-closed step.")


def validate_semantics(contract_id: str, payload: Mapping[str, Any]) -> None:
    """Core 6 contract의 권한·packet·receipt 교차 필드를 provider 호출 없이 검증한다."""

    if contract_id == "market_source_entitlement.v2":
        _validate_entitlement(payload)
    elif contract_id == "cross_market_provider_probe_approval.v1":
        _validate_approval(payload)
    elif contract_id == "cross_market_provider_probe_receipt.v1":
        _validate_receipt(payload)
    else:
        raise ContractValidationError(f"unknown S4.8 Core 6 contract: {contract_id}")


def generate_outputs() -> dict[str, bytes]:
    """Core 6 v2 SSOT와 contract-only fixture를 canonical bytes로 생성한다."""

    outputs: dict[str, bytes] = {}
    for contract_id, schema in _schemas().items():
        Draft202012Validator.check_schema(schema)
        outputs[f"contracts/schemas/{contract_id}.schema.json"] = canonical_json_bytes(
            schema
        )
    for path, payload in {**_positive_fixtures(), **_invalid_fixtures()}.items():
        outputs[path] = canonical_json_bytes(payload)
    return outputs


OUTPUTS: Final[frozenset[str]] = frozenset(generate_outputs())


def _is_core6_fixture_payload(path: Path, root: Path) -> bool:
    """파일명과 무관하게 public fixture 안의 Core 6 contract payload를 식별한다."""

    try:
        payload = load_json_bytes_strict(
            path.read_bytes(), source=path.relative_to(root).as_posix()
        )
    except (ContractValidationError, OSError):
        return False
    return isinstance(payload, Mapping) and payload.get("contractId") in SCHEMA_IDS


def _unexpected_core6_artifact_paths(
    root: Path, expected_output_paths: Collection[str]
) -> list[str]:
    """Core 6 generated namespace의 추가 tracked-like artifact를 fail-closed로 찾는다.

    실제 APPROVED packet은 local-only runner가 public fixture directory 밖에서 관리한다.
    따라서 public fixture tree 전체를 재귀적으로 읽어 파일명과 무관한 Core 6 payload까지
    확인하고, 선언된 generated output 밖의 artifact는 허용하지 않아야 한다.
    """

    expected_paths = set(expected_output_paths)
    candidate_paths: set[Path] = set()
    for contract_id in SCHEMA_IDS:
        candidate_paths.update(
            path
            for path in (root / "contracts/schemas").glob(f"{contract_id}*.schema.json")
            if path.is_file() or path.is_symlink()
        )

    fixture_root = root / "contracts/examples"
    if fixture_root.is_dir() and not fixture_root.is_symlink():
        for directory, directory_names, file_names in os.walk(
            fixture_root, followlinks=False
        ):
            current_directory = Path(directory)
            for directory_name in tuple(directory_names):
                child = current_directory / directory_name
                if stat.S_ISLNK(child.lstat().st_mode):
                    # public fixture tree의 link directory는 local-only packet을 숨길 수 있어
                    # 허용하지 않는다. os.walk가 target을 follow하지 않도록 목록에서도 제거한다.
                    candidate_paths.add(child)
                    directory_names.remove(directory_name)
            for file_name in file_names:
                candidate = current_directory / file_name
                if candidate.is_symlink() or _is_core6_fixture_payload(candidate, root):
                    candidate_paths.add(candidate)
                elif any(
                    candidate.name.startswith(f"{contract_id}.")
                    for contract_id in SCHEMA_IDS
                ):
                    candidate_paths.add(candidate)

    return sorted(
        relative_path
        for path in candidate_paths
        if (relative_path := path.relative_to(root).as_posix()) not in expected_paths
    )


def _write_outputs(outputs: Mapping[str, bytes]) -> None:
    for relative_path, payload in sorted(outputs.items()):
        write_generated_artifact(ROOT, relative_path, payload)


def _is_regular_generated_output(root: Path, relative_path: str) -> bool:
    """expected output의 모든 상위 경로가 link 없이 checkout 안에 있는지 확인한다."""

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
    mismatches: list[str] = []
    for relative_path, expected in sorted(outputs.items()):
        path = root / relative_path
        if (
            not _is_regular_generated_output(root, relative_path)
            or path.read_bytes() != expected
        ):
            mismatches.append(relative_path)
    unexpected = _unexpected_core6_artifact_paths(root, outputs)
    if mismatches or unexpected:
        messages: list[str] = []
        if mismatches:
            messages.append(
                "generated S4.8 Core 6 artifacts drifted:\n"
                + "\n".join(f"- {path}" for path in mismatches)
            )
        if unexpected:
            messages.append(
                "unexpected Core 6 generated namespace artifacts:\n"
                + "\n".join(f"- {path}" for path in unexpected)
            )
        raise ContractValidationError("\n".join(messages))


def main(argv: list[str] | None = None) -> int:
    """CLI는 local byte generation/check만 수행하고 provider/network를 사용하지 않는다."""

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
    print("S4_8_CORE6_CONTRACT_LOCK_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
