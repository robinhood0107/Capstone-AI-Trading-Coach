"""S5.7A model-neutral market-data seed, daily shard, and health contracts."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Final, Mapping

from jsonschema import Draft202012Validator

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from contracts.generated_artifact_io import write_generated_artifact  # noqa: E402
from contracts.generate_principle_contracts import ContractValidationError  # noqa: E402


ROOT = _REPO_ROOT
SHA_PATTERN: Final[str] = "^[0-9a-f]{64}$"
SYMBOL_PATTERN: Final[str] = "^[0-9]{6}$"
SCHEMA_IDS: Final[tuple[str, ...]] = (
    "market-data-seed.v1",
    "market-data-daily-shard.v1",
    "market-data-health.v1",
)
SCHEMA_PATHS: Final[dict[str, str]] = {
    schema_id: f"contracts/schemas/{schema_id}.schema.json" for schema_id in SCHEMA_IDS
}


def _closed(required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def _schema(contract_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_PATHS[contract_id],
        "title": contract_id,
        **copy.deepcopy(dict(body)),
    }


def _sha() -> dict[str, Any]:
    return {"type": "string", "pattern": SHA_PATTERN}


def _timestamp() -> dict[str, Any]:
    return {"type": "string", "format": "date-time"}


def _date() -> dict[str, Any]:
    return {"type": "string", "format": "date"}


def _positive_number() -> dict[str, Any]:
    return {"type": "number", "exclusiveMinimum": 0}


def _temporal_quality() -> dict[str, Any]:
    return {
        "enum": [
            "PROVIDER_VINTAGE",
            "PROVIDER_AS_OF_NO_VINTAGE",
            "RECONSTRUCTED_FIXED_LAG",
            "COLLECTION_ONLY",
        ]
    }


def _artifact_descriptor() -> dict[str, Any]:
    return _closed(
        [
            "kind",
            "relativePath",
            "sha256",
            "rowCount",
            "firstSessionDate",
            "lastSessionDate",
            "temporalQuality",
        ],
        {
            "kind": {"enum": ["BARS", "INDICES", "MACRO", "UNIVERSES"]},
            "relativePath": {
                "type": "string",
                "pattern": "^(bars|indices|macro|universes)/[a-z0-9._/-]+\\.parquet$",
                "not": {"pattern": "(^|/)\\.\\.(/|$)"},
            },
            "sha256": _sha(),
            "rowCount": {"type": "integer", "minimum": 1},
            "firstSessionDate": _date(),
            "lastSessionDate": _date(),
            "temporalQuality": _temporal_quality(),
        },
    )


def _seed_schema() -> dict[str, Any]:
    body = _closed(
        [
            "contractId",
            "createdAt",
            "sourceManifestSha256",
            "sourceChunkCount",
            "historicalProviderIntentCount",
            "providerCallsDuringAdoption",
            "sourceSessionCount",
            "operationalHistoryMaxSessions",
            "researchHistoryMaxSessions",
            "historicalUniverseUnionCount",
            "temporalQuality",
            "strictPitPerformanceClaimAllowed",
            "rawChunkCopied",
            "hardlinkUsed",
            "sourcePathPersisted",
            "artifacts",
            "archiveSha256",
        ],
        {
            "contractId": {"const": "market-data-seed.v1"},
            "createdAt": _timestamp(),
            "sourceManifestSha256": _sha(),
            "sourceChunkCount": {"const": 7218},
            "historicalProviderIntentCount": {"const": 7230},
            "providerCallsDuringAdoption": {"const": 0},
            "sourceSessionCount": {"const": 1072},
            "operationalHistoryMaxSessions": {"const": 253},
            "researchHistoryMaxSessions": {"const": 1260},
            "historicalUniverseUnionCount": {"const": 270},
            "temporalQuality": {"const": "RECONSTRUCTED_FIXED_LAG"},
            "strictPitPerformanceClaimAllowed": {"const": False},
            "rawChunkCopied": {"const": False},
            "hardlinkUsed": {"const": False},
            "sourcePathPersisted": {"const": False},
            "artifacts": {
                "type": "array",
                "minItems": 4,
                "maxItems": 4,
                "items": _artifact_descriptor(),
            },
            "archiveSha256": _sha(),
        },
    )
    return _schema("market-data-seed.v1", body)


def _calendar() -> dict[str, Any]:
    return _closed(
        ["name", "version", "revision", "attestationSha256"],
        {
            "name": {"const": "XKRX"},
            "version": {"const": "4.13.2"},
            "revision": {"type": "string", "minLength": 1, "maxLength": 128},
            "attestationSha256": _sha(),
        },
    )


def _bar() -> dict[str, Any]:
    return _closed(
        [
            "symbol",
            "sessionDate",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "currency",
            "temporalQuality",
            "sourceReceiptSha256",
        ],
        {
            "symbol": {"type": "string", "pattern": SYMBOL_PATTERN},
            "sessionDate": _date(),
            "open": _positive_number(),
            "high": _positive_number(),
            "low": _positive_number(),
            "close": _positive_number(),
            "volume": {"type": "integer", "minimum": 0},
            "currency": {"const": "KRW"},
            "temporalQuality": {"const": "RECONSTRUCTED_FIXED_LAG"},
            "sourceReceiptSha256": _sha(),
        },
    )


def _index_observation() -> dict[str, Any]:
    return _closed(
        [
            "indexId",
            "sessionDate",
            "close",
            "temporalQuality",
            "sourceReceiptSha256",
        ],
        {
            "indexId": {"enum": ["KOSPI", "KOSDAQ"]},
            "sessionDate": _date(),
            "close": _positive_number(),
            "temporalQuality": {"const": "PROVIDER_AS_OF_NO_VINTAGE"},
            "sourceReceiptSha256": _sha(),
        },
    )


def _macro_observation() -> dict[str, Any]:
    return _closed(
        [
            "seriesId",
            "observationDate",
            "availableAt",
            "value",
            "temporalQuality",
            "sourceReceiptSha256",
        ],
        {
            "seriesId": {"enum": ["722Y001/0101000/D", "731Y001/0000001/D"]},
            "observationDate": _date(),
            "availableAt": _timestamp(),
            "value": {"type": "number"},
            "temporalQuality": {"const": "RECONSTRUCTED_FIXED_LAG"},
            "sourceReceiptSha256": _sha(),
        },
    )


def _source_receipt() -> dict[str, Any]:
    return _closed(
        [
            "sourceId",
            "operationId",
            "querySha256",
            "contentSha256",
            "retrievedAt",
        ],
        {
            "sourceId": {"enum": ["KRX", "KIS", "ECOS"]},
            "operationId": {"type": "string", "minLength": 1, "maxLength": 80},
            "querySha256": _sha(),
            "contentSha256": _sha(),
            "retrievedAt": _timestamp(),
        },
    )


def _daily_shard_schema() -> dict[str, Any]:
    body = _closed(
        [
            "contractId",
            "accepted",
            "generation",
            "previousAcceptedManifestSha256",
            "sessionDate",
            "asOf",
            "calendar",
            "membershipMonth",
            "membershipSha256",
            "membership",
            "bars",
            "indices",
            "macro",
            "sourceReceipts",
            "providerPhysicalCalls",
            "forwardFillUsed",
            "providerCallsOnRead",
            "decisionAuthority",
            "manifestSha256",
        ],
        {
            "contractId": {"const": "market-data-daily-shard.v1"},
            "accepted": {"const": True},
            "generation": {"type": "integer", "minimum": 1},
            "supersedesSha256": _sha(),
            "previousAcceptedManifestSha256": _sha(),
            "sessionDate": _date(),
            "asOf": _timestamp(),
            "calendar": _calendar(),
            "membershipMonth": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}$"},
            "membershipSha256": _sha(),
            "membership": {
                "type": "array",
                "minItems": 31,
                "maxItems": 31,
                "uniqueItems": True,
                "items": {"type": "string", "pattern": SYMBOL_PATTERN},
            },
            "bars": {
                "type": "array",
                "minItems": 31,
                "maxItems": 31,
                "items": _bar(),
            },
            "indices": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "items": _index_observation(),
            },
            "macro": {
                "type": "array",
                "minItems": 1,
                "maxItems": 2,
                "items": _macro_observation(),
            },
            "sourceReceipts": {
                "type": "array",
                "minItems": 34,
                "maxItems": 41,
                "items": _source_receipt(),
            },
            "providerPhysicalCalls": {"const": 0},
            "forwardFillUsed": {"const": False},
            "providerCallsOnRead": {"const": 0},
            "decisionAuthority": {"const": "NONE"},
            "manifestSha256": _sha(),
        },
    )
    body["allOf"] = [
        {
            "if": {"properties": {"generation": {"const": 1}}},
            "then": {"not": {"required": ["supersedesSha256"]}},
            "else": {"required": ["supersedesSha256"]},
        }
    ]
    return _schema("market-data-daily-shard.v1", body)


def _health_schema() -> dict[str, Any]:
    body = _closed(
        [
            "contractId",
            "status",
            "checkedAt",
            "expectedSessionDate",
            "providerPhysicalCalls",
            "collectorInvocationAllowed",
            "retryAllowed",
            "details",
        ],
        {
            "contractId": {"const": "market-data-health.v1"},
            "status": {
                "enum": [
                    "ACCEPTED",
                    "PARTIAL",
                    "NO_NEW_SESSION",
                    "WAITING_FOR_EVIDENCE_CLOCK",
                    "CALENDAR_DIVERGENCE_SUSPECTED",
                    "EVIDENCE_GAP",
                    "NEEDS_HUMAN",
                    "NOT_ESTIMABLE",
                ]
            },
            "checkedAt": _timestamp(),
            "expectedSessionDate": _date(),
            "lastAcceptedManifestSha256": _sha(),
            "lastAcceptedAsOf": _timestamp(),
            "providerPhysicalCalls": {"const": 0},
            "collectorInvocationAllowed": {"type": "boolean"},
            "retryAllowed": {"type": "boolean"},
            "details": {
                "type": "array",
                "maxItems": 32,
                "uniqueItems": True,
                "items": {
                    "type": "string",
                    "pattern": "^[A-Z][A-Z0-9_]{2,63}$",
                },
            },
        },
    )
    body["allOf"] = [
        {
            "if": {
                "properties": {
                    "status": {"enum": ["NO_NEW_SESSION", "WAITING_FOR_EVIDENCE_CLOCK"]}
                }
            },
            "then": {
                "properties": {
                    "providerPhysicalCalls": {"const": 0},
                    "collectorInvocationAllowed": {"const": False},
                    "retryAllowed": {"const": False},
                }
            },
        },
        {
            "if": {"properties": {"status": {"const": "ACCEPTED"}}},
            "then": {
                "required": ["lastAcceptedManifestSha256", "lastAcceptedAsOf"],
                "properties": {
                    "collectorInvocationAllowed": {"const": False},
                    "retryAllowed": {"const": False},
                },
            },
        },
        {
            "if": {
                "required": ["lastAcceptedManifestSha256"],
            },
            "then": {"required": ["lastAcceptedAsOf"]},
        },
        {
            "if": {"required": ["lastAcceptedAsOf"]},
            "then": {"required": ["lastAcceptedManifestSha256"]},
        },
    ]
    return _schema("market-data-health.v1", body)


def _symbols() -> list[str]:
    return [f"{index:06d}" for index in range(1, 31)] + ["132030"]


def _valid_seed() -> dict[str, Any]:
    qualities = {
        "BARS": "RECONSTRUCTED_FIXED_LAG",
        "INDICES": "PROVIDER_AS_OF_NO_VINTAGE",
        "MACRO": "RECONSTRUCTED_FIXED_LAG",
        "UNIVERSES": "PROVIDER_AS_OF_NO_VINTAGE",
    }
    artifacts = [
        {
            "kind": kind,
            "relativePath": f"{kind.lower()}/{kind.lower()}-v1.parquet",
            "sha256": character * 64,
            "rowCount": 1072 if kind != "BARS" else 25000,
            "firstSessionDate": "2022-03-30",
            "lastSessionDate": "2026-08-14",
            "temporalQuality": qualities[kind],
        }
        for kind, character in zip(
            ("BARS", "INDICES", "MACRO", "UNIVERSES"), "abcd", strict=True
        )
    ]
    return {
        "contractId": "market-data-seed.v1",
        "createdAt": "2026-08-20T12:00:00Z",
        "sourceManifestSha256": "1" * 64,
        "sourceChunkCount": 7218,
        "historicalProviderIntentCount": 7230,
        "providerCallsDuringAdoption": 0,
        "sourceSessionCount": 1072,
        "operationalHistoryMaxSessions": 253,
        "researchHistoryMaxSessions": 1260,
        "historicalUniverseUnionCount": 270,
        "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
        "strictPitPerformanceClaimAllowed": False,
        "rawChunkCopied": False,
        "hardlinkUsed": False,
        "sourcePathPersisted": False,
        "artifacts": artifacts,
        "archiveSha256": "2" * 64,
    }


def _valid_daily() -> dict[str, Any]:
    symbols = _symbols()
    receipts = [
        {
            "sourceId": source,
            "operationId": operation,
            "querySha256": f"{index:064x}",
            "contentSha256": f"{index + 100:064x}",
            "retrievedAt": "2026-08-18T23:11:00Z",
        }
        for index, (source, operation) in enumerate(
            [("KRX", f"DAILY_MARKET_{index}") for index in range(1, 6)]
            + [("KIS", "FHKST03010100")] * 31
            + [
                ("ECOS", "722Y001/0101000/D"),
                ("ECOS", "731Y001/0000001/D"),
            ],
            start=1,
        )
    ]
    krx_receipts = [row for row in receipts if row["sourceId"] == "KRX"]
    kis_receipts = [row for row in receipts if row["sourceId"] == "KIS"]
    ecos_receipts = [row for row in receipts if row["sourceId"] == "ECOS"]
    bars = [
        {
            "symbol": symbol,
            "sessionDate": "2026-08-18",
            "open": 10000 + index,
            "high": 10100 + index,
            "low": 9900 + index,
            "close": 10050 + index,
            "volume": 100000 + index,
            "currency": "KRW",
            "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
            "sourceReceiptSha256": kis_receipts[index]["contentSha256"],
        }
        for index, symbol in enumerate(symbols)
    ]
    return {
        "contractId": "market-data-daily-shard.v1",
        "accepted": True,
        "generation": 1,
        "previousAcceptedManifestSha256": "a" * 64,
        "sessionDate": "2026-08-18",
        "asOf": "2026-08-18T23:10:00Z",
        "calendar": {
            "name": "XKRX",
            "version": "4.13.2",
            "revision": "kis-ctca0903r-attested-v1",
            "attestationSha256": "b" * 64,
        },
        "membershipMonth": "2026-08",
        "membershipSha256": "c" * 64,
        "membership": symbols,
        "bars": bars,
        "indices": [
            {
                "indexId": index_id,
                "sessionDate": "2026-08-18",
                "close": close,
                "temporalQuality": "PROVIDER_AS_OF_NO_VINTAGE",
                "sourceReceiptSha256": receipt["contentSha256"],
            }
            for index_id, close, receipt in (
                ("KOSPI", 3200.5, krx_receipts[0]),
                ("KOSDAQ", 900.25, krx_receipts[1]),
            )
        ],
        "macro": [
            {
                "seriesId": series_id,
                "observationDate": "2026-08-18",
                "availableAt": "2026-08-18T23:10:00Z",
                "value": value,
                "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
                "sourceReceiptSha256": receipt["contentSha256"],
            }
            for series_id, value, receipt in (
                ("722Y001/0101000/D", 2.5, ecos_receipts[0]),
                ("731Y001/0000001/D", 1380.25, ecos_receipts[1]),
            )
        ],
        "sourceReceipts": receipts,
        "providerPhysicalCalls": 0,
        "forwardFillUsed": False,
        "providerCallsOnRead": 0,
        "decisionAuthority": "NONE",
        "manifestSha256": "7" * 64,
    }


def _valid_health() -> dict[str, Any]:
    return {
        "contractId": "market-data-health.v1",
        "status": "WAITING_FOR_EVIDENCE_CLOCK",
        "checkedAt": "2026-08-18T22:00:00Z",
        "expectedSessionDate": "2026-08-18",
        "providerPhysicalCalls": 0,
        "collectorInvocationAllowed": False,
        "retryAllowed": False,
        "details": ["BEFORE_0810_KST"],
    }


def _catalog() -> dict[str, Any]:
    return {
        "contractId": "s5-7a-market-data-lock.v1",
        "contracts": list(SCHEMA_IDS),
        "scope": "INTERNAL_PYTHON_DATA_PLANE_ONLY",
        "sourceAdoption": {
            "sourceChunkCount": 7218,
            "historicalProviderIntentCount": 7230,
            "providerCalls": 0,
            "rawChunkCopyAllowed": False,
            "hardlinkAllowed": False,
            "sourcePathPersistenceAllowed": False,
            "featureReadAllowed": False,
            "labelReadAllowed": False,
            "finalTestReadAllowed": False,
            "releaseReadAllowed": False,
            "signalBatchReadAllowed": False,
            "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
            "strictPitPerformanceClaimAllowed": False,
        },
        "dailyShard": {
            "sessionsPerShard": 1,
            "membershipPolicy": "MONTHLY_FIXED_TOP30_PLUS_132030",
            "exactSymbols": 31,
            "indexIds": ["KOSPI", "KOSDAQ"],
            "macroSeriesMax": 2,
            "normalReplayOperationCount": 38,
            "monthBoundaryReplayOperationCount": 41,
            "providerPhysicalCallMax": 0,
            "accountCalls": 0,
            "balanceCalls": 0,
            "orderCalls": 0,
            "manifestPublication": "LAST_AFTER_COMPLETE_REQUIRED_SET",
            "partialRunAcceptedRows": 0,
            "forwardFillAllowed": False,
        },
        "normalizedRows": {
            "bars": {
                "key": ["symbol", "sessionDate", "generation"],
                "fields": [
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "currency",
                    "temporalQuality",
                    "sourceReceiptSha256",
                ],
            },
            "indices": {
                "key": ["indexId", "sessionDate", "generation"],
                "indexIds": ["KOSPI", "KOSDAQ"],
                "fields": ["close", "temporalQuality", "sourceReceiptSha256"],
            },
            "macro": {
                "key": ["seriesId", "observationDate", "availableAt", "generation"],
                "fields": ["value", "temporalQuality", "sourceReceiptSha256"],
            },
            "universe": {
                "key": ["membershipMonth", "symbol", "generation"],
                "fields": ["rank", "isFixedMember", "sourceReceiptSha256"],
                "historicalUnionIsDailyScope": False,
            },
        },
        "operationalReader": {
            "port": "MarketDataOperationalReader",
            "deliveryScope": "INTERNAL_PYTHON_PORT_ONLY",
            "symbolScope": "CURRENT_EXACT_31",
            "maxCloseSessions": 253,
            "providerCallsOnRead": 0,
            "publicApi": False,
            "scheduler": False,
        },
        "researchReader": {
            "port": "ResearchMarketHistoryReader",
            "deliveryScope": "OFFLINE_RESEARCH_ONLY",
            "maxXkrxSessions": 1260,
            "providerCallsOnRead": 0,
            "springDecisionAccess": False,
            "springRiskAccess": False,
            "publicApi": False,
        },
        "consumerWindows": {
            "monteCarloCloseCount": 61,
            "ouObservationCount": 60,
            "operationalRiskCloseMax": 253,
            "hmmExistingIndexSessionCount": 1072,
            "researchSessionMax": 1260,
        },
        "retention": {
            "ecosActiveDaysMax": 365,
            "entitlementExpiryWins": True,
            "generalWriterUpdateAllowed": False,
            "generalWriterDeleteAllowed": False,
            "generalWriterTruncateAllowed": False,
            "pruneToolDefault": "DRY_RUN",
        },
        "correction": {
            "sameSessionSameSha": "NO_OP",
            "sameSessionDifferentSha": "NEEDS_HUMAN",
            "overwriteAllowed": False,
            "approvedGeneration": "APPEND_WITH_SUPERSEDES_SHA256",
        },
        "calendar": {
            "name": "XKRX",
            "version": "4.13.2",
            "correctionAuthority": "KIS_CTCA0903R_ATTESTED_TRADING_SESSIONS",
            "evidenceClock": "NEXT_COMPLETED_XKRX_SESSION_08_10_ASIA_SEOUL",
            "regression": {"lastSession": "2026-08-14", "nextSession": "2026-08-18"},
        },
        "publicMarketDataApi": False,
        "dashboard": False,
        "runtimeImplemented": False,
        "providerAuthorityGranted": False,
        "lightgbmSignalAuthority": "ABSTAIN",
        "riskDecisionAuthority": "NONE",
        "orderAuthority": "NONE",
    }


def validate_semantics(contract_id: str, payload: object) -> None:
    if contract_id not in SCHEMA_IDS:
        raise ContractValidationError(f"unknown S5.7A contract: {contract_id}")
    if not isinstance(payload, dict):
        raise ContractValidationError("S5.7A payload must be an object")

    if contract_id == "market-data-seed.v1":
        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, list) or {
            item.get("kind") for item in artifacts if isinstance(item, dict)
        } != {"BARS", "INDICES", "MACRO", "UNIVERSES"}:
            raise ContractValidationError(
                "seed must contain each normalized artifact kind exactly once"
            )
        for item in artifacts:
            if not isinstance(item, dict) or item.get(
                "firstSessionDate", ""
            ) > item.get("lastSessionDate", ""):
                raise ContractValidationError("seed artifact session range is invalid")
        return

    if contract_id == "market-data-daily-shard.v1":
        session_date = payload.get("sessionDate")
        membership = payload.get("membership")
        bars = payload.get("bars")
        indices = payload.get("indices")
        macro = payload.get("macro")
        if not isinstance(membership, list) or len(set(membership)) != len(membership):
            raise ContractValidationError(
                "daily membership must contain unique symbols"
            )
        if not isinstance(bars, list) or any(
            not isinstance(bar, dict) or bar.get("sessionDate") != session_date
            for bar in bars
        ):
            raise ContractValidationError(
                "daily bars must contain exactly the shard session"
            )
        if {bar.get("symbol") for bar in bars if isinstance(bar, dict)} != set(
            membership
        ):
            raise ContractValidationError(
                "daily bars must match the exact monthly membership"
            )
        if not isinstance(indices, list) or {
            item.get("indexId") for item in indices if isinstance(item, dict)
        } != {"KOSPI", "KOSDAQ"}:
            raise ContractValidationError(
                "daily shard requires exact KOSPI and KOSDAQ indices"
            )
        if any(
            not isinstance(item, dict) or item.get("sessionDate") != session_date
            for item in indices
        ):
            raise ContractValidationError(
                "daily indices must contain exactly the shard session"
            )
        if not isinstance(macro, list) or len(
            {item.get("seriesId") for item in macro if isinstance(item, dict)}
        ) != len(macro):
            raise ContractValidationError("daily macro series must be unique")
        receipts = payload.get("sourceReceipts")
        if not isinstance(receipts, list):
            raise ContractValidationError("daily source receipts are required")
        receipt_sources = {
            item.get("contentSha256"): item.get("sourceId")
            for item in receipts
            if isinstance(item, dict)
        }
        if len(receipt_sources) != len(receipts):
            raise ContractValidationError(
                "daily source receipt content hashes must be unique"
            )
        referenced = [
            *(bar.get("sourceReceiptSha256") for bar in bars if isinstance(bar, dict)),
            *(
                item.get("sourceReceiptSha256")
                for item in indices
                if isinstance(item, dict)
            ),
            *(
                item.get("sourceReceiptSha256")
                for item in macro
                if isinstance(item, dict)
            ),
        ]
        if any(reference not in receipt_sources for reference in referenced):
            raise ContractValidationError(
                "daily observation must reference a sealed source receipt"
            )
        if any(
            receipt_sources.get(bar.get("sourceReceiptSha256")) != "KIS"
            for bar in bars
            if isinstance(bar, dict)
        ):
            raise ContractValidationError("daily bars must reference KIS receipts")
        if any(
            receipt_sources.get(item.get("sourceReceiptSha256")) != "KRX"
            for item in indices
            if isinstance(item, dict)
        ):
            raise ContractValidationError("daily indices must reference KRX receipts")
        if any(
            receipt_sources.get(item.get("sourceReceiptSha256")) != "ECOS"
            for item in macro
            if isinstance(item, dict)
        ):
            raise ContractValidationError("daily macro must reference ECOS receipts")
        return

    if bool(payload.get("lastAcceptedManifestSha256")) != bool(
        payload.get("lastAcceptedAsOf")
    ):
        raise ContractValidationError(
            "health last-good manifest and asOf must appear together"
        )


def artifacts() -> dict[str, bytes]:
    schemas = {
        "market-data-seed.v1": _seed_schema(),
        "market-data-daily-shard.v1": _daily_shard_schema(),
        "market-data-health.v1": _health_schema(),
    }
    valid_seed = _valid_seed()
    valid_daily = _valid_daily()
    valid_health = _valid_health()

    invalid_seed_pit = copy.deepcopy(valid_seed)
    invalid_seed_pit["strictPitPerformanceClaimAllowed"] = True
    invalid_daily_count = copy.deepcopy(valid_daily)
    invalid_daily_count["bars"] = invalid_daily_count["bars"][:-1]
    invalid_daily_duplicate = copy.deepcopy(valid_daily)
    invalid_daily_duplicate["bars"][1]["symbol"] = invalid_daily_duplicate["bars"][0][
        "symbol"
    ]
    invalid_daily_mixed_session = copy.deepcopy(valid_daily)
    invalid_daily_mixed_session["bars"][0]["sessionDate"] = "2026-08-19"
    invalid_daily_unsealed_receipt = copy.deepcopy(valid_daily)
    invalid_daily_unsealed_receipt["bars"][0]["sourceReceiptSha256"] = "f" * 64
    invalid_daily_correction = copy.deepcopy(valid_daily)
    invalid_daily_correction["generation"] = 2
    invalid_health_provider = copy.deepcopy(valid_health)
    invalid_health_provider["providerPhysicalCalls"] = 1
    invalid_health_accepted = copy.deepcopy(valid_health)
    invalid_health_accepted["status"] = "ACCEPTED"

    values: dict[str, object] = {
        **{SCHEMA_PATHS[schema_id]: schema for schema_id, schema in schemas.items()},
        "contracts/catalogs/s5-7a-market-data-lock.v1.json": _catalog(),
        "contracts/examples/market-data-seed.v1.valid.json": valid_seed,
        "contracts/examples/market-data-daily-shard.v1.valid.json": valid_daily,
        "contracts/examples/market-data-health.v1.valid.json": valid_health,
        "contracts/examples/invalid/market-data-seed.v1.strict-pit-claim.invalid.json": invalid_seed_pit,
        "contracts/examples/invalid/market-data-daily-shard.v1.row-count.invalid.json": invalid_daily_count,
        "contracts/examples/invalid/market-data-daily-shard.v1.duplicate-symbol.invalid.json": invalid_daily_duplicate,
        "contracts/examples/invalid/market-data-daily-shard.v1.mixed-session.invalid.json": invalid_daily_mixed_session,
        "contracts/examples/invalid/market-data-daily-shard.v1.unsealed-receipt.invalid.json": invalid_daily_unsealed_receipt,
        "contracts/examples/invalid/market-data-daily-shard.v1.correction-without-supersedes.invalid.json": invalid_daily_correction,
        "contracts/examples/invalid/market-data-health.v1.waiting-provider-call.invalid.json": invalid_health_provider,
        "contracts/examples/invalid/market-data-health.v1.accepted-without-manifest.invalid.json": invalid_health_accepted,
    }
    return {
        relative: (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        for relative, value in values.items()
    }


def validate_outputs(outputs: Mapping[str, bytes]) -> None:
    schemas = {
        schema_id: json.loads(outputs[SCHEMA_PATHS[schema_id]])
        for schema_id in SCHEMA_IDS
    }
    validators = {
        schema_id: Draft202012Validator(schema) for schema_id, schema in schemas.items()
    }
    for relative, content in outputs.items():
        if "/examples/" not in relative or "/invalid/" in relative:
            continue
        payload = json.loads(content)
        contract_id = payload["contractId"]
        errors = list(validators[contract_id].iter_errors(payload))
        if errors:
            raise ContractValidationError(f"generated valid fixture failed: {relative}")
        validate_semantics(contract_id, payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.write and args.check:
        parser.error("--write and --check are mutually exclusive")
    generated = artifacts()
    validate_outputs(generated)
    drift: list[str] = []
    for relative, content in generated.items():
        path = ROOT / relative
        if args.write:
            write_generated_artifact(ROOT, relative, content)
        elif path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            drift.append(relative)
    if drift:
        raise SystemExit("generated S5.7A artifacts drifted:\n" + "\n".join(drift))
    print("S5_7A_MARKET_DATA_CONTRACT_LOCK_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
