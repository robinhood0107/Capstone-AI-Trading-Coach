"""세계 뉴스 v2 조회/RAG additive OpenAPI와 fixture를 결정론적으로 생성한다."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from contracts.generate_principle_contracts import ContractValidationError

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "contracts/openapi/openapi.json"
PREVIOUS = ROOT / "contracts/openapi/p1-world-news-v2.previous.openapi.json"
OVERLAY = ROOT / "contracts/openapi/p1-world-news-v2.v1.openapi.json"
SCHEMA = ROOT / "contracts/schemas/world-news-v2.schema.json"
POSITIVE = ROOT / "contracts/fixtures/world-news-v2/positive/page.valid.json"
PERFORMANCE_POSITIVE = (
    ROOT / "contracts/fixtures/owner-performance-report/positive/report.valid.json"
)
NEGATIVE = ROOT / "contracts/fixtures/world-news-v2/negative"


def item_schema() -> dict[str, object]:
    properties: dict[str, object] = {
        "documentId": {"type": "string", "pattern": "^news_doc_[0-9a-f]{32}$"},
        "documentVersionId": {"type": "string", "pattern": "^news_ver_[0-9a-f]{32}$"},
        "sourceId": {"type": "string", "pattern": "^src_[a-z0-9][a-z0-9_-]{2,95}$"},
        "provider": {"type": "string", "enum": ["GDELT_GQG", "GDELT_GEMG", "FINNHUB_MARKET_NEWS"]},
        "providerDocumentId": {"type": ["string", "null"], "minLength": 1, "maxLength": 256},
        "canonicalUrl": {"type": "string", "format": "uri", "pattern": "^https://"},
        "republicationOfDocumentId": {
            "type": ["string", "null"],
            "pattern": "^news_doc_[0-9a-f]{32}$",
        },
        "identityStatus": {
            "type": "string",
            "enum": ["VERIFIED", "PROVIDER_ID_CONFLICT", "URL_HASH_CONFLICT"],
        },
        "title": {"type": ["string", "null"], "minLength": 1, "maxLength": 300},
        "boundedQuote": {"type": ["string", "null"], "minLength": 1, "maxLength": 600},
        "boundedPassage": {"type": ["string", "null"], "minLength": 1, "maxLength": 1200},
        "language": {"type": "string", "pattern": "^[a-z]{2,3}(-[A-Z]{2})?$"},
        "publishedAt": {"type": ["string", "null"], "format": "date-time"},
        "publicationStatus": {"type": "string", "enum": ["VERIFIED", "MISSING", "CONFLICT"]},
        "providerObservedAt": {"type": "string", "format": "date-time"},
        "firstSeenAt": {"type": "string", "format": "date-time"},
        "availableAt": {"type": "string", "format": "date-time"},
        "rightsProfile": {
            "type": "string",
            "enum": ["GDELT_METADATA_QUOTE", "FINNHUB_PERSONAL_LOCAL"],
        },
        "externalLlmAllowed": {"type": "boolean"},
        "lookupAllowed": {"const": True},
        "ragRetrievalAllowed": {"const": True},
        "promptUntrusted": {"const": True},
        "collectionStatus": {
            "type": "string",
            "enum": ["COMPLETE", "PARTIAL", "COLLECTION_FAILED", "NOT_COLLECTED"],
        },
        "contentSha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "versionSha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    }
    return {
        "$id": "contracts/schemas/world-news-v2.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": sorted(properties),
        "allOf": [
            {
                "if": {"properties": {"publicationStatus": {"const": "MISSING"}}},
                "then": {"properties": {"publishedAt": {"type": "null"}}},
            },
            {
                "if": {"properties": {"publicationStatus": {"const": "VERIFIED"}}},
                "then": {"properties": {"publishedAt": {"type": "string", "format": "date-time"}}},
            },
            {
                "if": {"properties": {"rightsProfile": {"const": "FINNHUB_PERSONAL_LOCAL"}}},
                "then": {"properties": {"externalLlmAllowed": {"const": False}}},
            },
        ],
    }


def collection_schema() -> dict[str, object]:
    properties: dict[str, object] = {
        "provider": {"type": "string", "enum": ["GDELT_GQG", "GDELT_GEMG", "FINNHUB_MARKET_NEWS"]},
        "collectionStatus": {
            "type": "string",
            "enum": ["COMPLETE", "PARTIAL", "COLLECTION_FAILED", "NOT_COLLECTED"],
        },
        "startedAt": {"type": "string", "format": "date-time"},
        "completedAt": {"type": ["string", "null"], "format": "date-time"},
        "observedThrough": {"type": ["string", "null"], "format": "date-time"},
        "itemCount": {"type": "integer", "minimum": 0, "maximum": 100000},
        "errorCode": {"type": ["string", "null"], "pattern": "^[A-Z0-9_]{1,96}$"},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": sorted(properties),
    }


def page_schema() -> dict[str, object]:
    properties: dict[str, object] = {
        "items": {
            "type": "array",
            "items": {"$ref": "#/components/schemas/WorldNewsV2Item"},
            "maxItems": 50,
        },
        "collections": {
            "type": "array",
            "items": {"$ref": "#/components/schemas/WorldNewsV2CollectionStatus"},
            "maxItems": 3,
        },
        "asOf": {"type": "string", "format": "date-time"},
        "decisionAuthority": {"const": "NONE"},
        "signalAuthority": {"const": "NONE"},
        "orderAuthority": {"const": "NONE"},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": sorted(properties),
    }


def error_schema() -> dict[str, object]:
    properties: dict[str, object] = {
        "code": {
            "type": "string",
            "enum": ["WORLD_NEWS_VALIDATION_FAILED", "WORLD_NEWS_UNAVAILABLE", "UNAUTHORIZED"],
        },
        "message": {"type": "string", "minLength": 1, "maxLength": 256},
        "requestId": {"type": "string", "minLength": 1, "maxLength": 128},
    }


def capital_policy_schema() -> dict[str, object]:
    properties: dict[str, object] = {
        "contractId": {"const": "automation-capital-policy.v1"},
        "version": {"type": "integer", "minimum": 1},
        "reinvestRealizedPnl": {"type": "boolean", "default": True},
        "cashBufferBps": {"const": 100},
        "rebalanceDeviationBps": {"const": 200},
        "minimumAdjustmentKrw": {"const": 10000},
        "maxOrdersPerSession": {"type": "integer", "minimum": 1, "maximum": 3},
        "effectiveFromSession": {"type": "string", "format": "date"},
        "transitionStartedAt": {"type": "string", "format": "date-time"},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": sorted(properties),
    }


def capital_policy_request_schema() -> dict[str, object]:
    properties: dict[str, object] = {
        "reinvestRealizedPnl": {"type": "boolean"},
        "expectedVersion": {"type": "integer", "minimum": 0},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": sorted(properties),
    }


def capital_policy_envelope_schema() -> dict[str, object]:
    previous = json.loads(PREVIOUS.read_text(encoding="utf-8"))
    envelope = copy.deepcopy(previous["components"]["schemas"]["ApiResponseAutomationPolicyV3"])
    envelope["properties"]["data"] = {
        "oneOf": [
            {"$ref": "#/components/schemas/AutomationCapitalPolicyV1"},
            {"type": "null"},
        ]
    }
    return envelope


def capital_status_schema() -> dict[str, object]:
    position = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "symbol": {"type": "string", "pattern": "^[0-9]{6}$"},
            "currentQuantity": {"type": "integer", "minimum": 1},
            "targetQuantity": {"type": ["integer", "null"], "minimum": 0},
            "currentMarketValueKrw": {"type": ["integer", "null"], "minimum": 0},
            "targetMarketValueKrw": {"type": "integer", "minimum": 0},
            "currentWeightBps": {"type": ["integer", "null"], "minimum": 0},
            "targetWeightBps": {"type": "integer", "minimum": 0},
            "valuationStatus": {"enum": ["COMPLETE", "MISSING"]},
        },
    }
    position["required"] = sorted(position["properties"])
    properties: dict[str, object] = {
        "contractId": {"const": "automation-capital-status.v1"},
        "policyVersion": {"type": "integer", "minimum": 1},
        "reinvestRealizedPnl": {"type": "boolean"},
        "configuredCapitalKrw": {"type": "integer", "minimum": 0},
        "realizedPnlSinceTransitionKrw": {"type": "integer"},
        "brokerBuyableCashKrw": {"type": "integer", "minimum": 0},
        "botPositionMarketValueKrw": {"type": "integer", "minimum": 0},
        "reservedBuyCashKrw": {"type": "integer", "minimum": 0},
        "allocationCapKrw": {"type": "integer", "minimum": 0},
        "investableCapKrw": {"type": "integer", "minimum": 0},
        "availableBuyCashKrw": {"type": "integer", "minimum": 0},
        "targetPerPositionKrw": {"type": "integer", "minimum": 0},
        "existingBotPositionsAdopted": {"type": "integer", "minimum": 0, "maximum": 5},
        "valuationMissingCount": {"type": "integer", "minimum": 0, "maximum": 5},
        "unusedCashReason": {"type": ["string", "null"]},
        "positions": {"type": "array", "items": position, "maxItems": 5},
        "asOf": {"type": "string", "format": "date-time"},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": sorted(properties),
    }


def capital_status_envelope_schema() -> dict[str, object]:
    envelope = capital_policy_envelope_schema()
    envelope["properties"]["data"] = {
        "oneOf": [
            {"$ref": "#/components/schemas/AutomationCapitalStatusV1"},
            {"type": "null"},
        ]
    }
    return envelope
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": sorted(properties),
    }


def _sort_required(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: (
                sorted(item) if key == "required" and isinstance(item, list) else _sort_required(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sort_required(item) for item in value]
    return value


def spring_world_news_item_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "availableAt": {"type": "string", "format": "date-time"},
            "boundedPassage": {"type": ["string", "null"]},
            "boundedQuote": {"type": ["string", "null"]},
            "canonicalUrl": {"type": "string"},
            "collectionStatus": {"type": "string"},
            "contentSha256": {"type": "string"},
            "documentId": {"type": "string"},
            "documentVersionId": {"type": "string"},
            "externalLlmAllowed": {"type": "boolean"},
            "firstSeenAt": {"type": "string", "format": "date-time"},
            "identityStatus": {"type": "string"},
            "language": {"type": "string"},
            "lookupAllowed": {"type": "boolean"},
            "promptUntrusted": {"type": "boolean"},
            "provider": {"type": "string"},
            "providerDocumentId": {"type": ["string", "null"]},
            "providerObservedAt": {"type": "string", "format": "date-time"},
            "publicationStatus": {"type": "string"},
            "publishedAt": {"type": ["string", "null"], "format": "date-time"},
            "ragRetrievalAllowed": {"type": "boolean"},
            "republicationOfDocumentId": {"type": ["string", "null"]},
            "rightsProfile": {"type": "string"},
            "sourceId": {"type": "string"},
            "title": {"type": ["string", "null"]},
            "versionSha256": {"type": "string"},
        },
    }


def spring_world_news_collection_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "collectionStatus": {"type": "string"},
            "completedAt": {"type": ["string", "null"], "format": "date-time"},
            "errorCode": {"type": ["string", "null"]},
            "itemCount": {"type": "integer", "format": "int32"},
            "observedThrough": {"type": ["string", "null"], "format": "date-time"},
            "provider": {"type": "string"},
            "startedAt": {"type": "string", "format": "date-time"},
        },
    }


def spring_world_news_page_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "asOf": {"type": "string", "format": "date-time"},
            "collections": {"type": "array", "items": {"$ref": "#/components/schemas/WorldNewsCollectionStatus"}},
            "decisionAuthority": {"type": "string"},
            "items": {"type": "array", "items": {"$ref": "#/components/schemas/WorldNewsItem"}},
            "orderAuthority": {"type": "string"},
            "signalAuthority": {"type": "string"},
        },
    }


def performance_report_schema() -> dict[str, object]:
    ratio = {"type": ["number", "null"]}
    report_properties: dict[str, object] = {
        "contractId": {"const": "owner-performance-report.v1"},
        "reportId": {"type": "string", "pattern": "^perf_report_[0-9a-f]{24}$"},
        "reportVersion": {"type": "integer", "minimum": 1},
        "supersedesReportId": {"type": ["string", "null"], "pattern": "^perf_report_[0-9a-f]{24}$"},
        "correctionOfReportId": {
            "type": ["string", "null"],
            "pattern": "^perf_report_[0-9a-f]{24}$",
        },
        "generatedAt": {"type": "string", "format": "date-time"},
        "sourceStart": {"const": "2026-08-18"},
        "sourceEnd": {"type": "string", "format": "date"},
        "sourceGenerationSha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "modelSha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "principleVersionId": {"type": "string", "pattern": "^pvr_[A-Za-z0-9_-]{8,96}$"},
        "principleVersion": {"type": "integer", "minimum": 1},
        "costBps": {"type": "integer", "minimum": 0, "maximum": 10000},
        "modelAdoption": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "state": {
                    "enum": [
                        "RESEARCH_EVALUATED",
                        "SHADOW_DAILY",
                        "ACCEPTANCE_REVIEWED",
                        "CURRENT_MODEL",
                    ]
                },
                "candidateId": {"type": ["string", "null"]},
                "currentModel": {"type": "string", "minLength": 1, "maxLength": 128},
                "predictionAccepted": {"type": "boolean"},
                "performanceAccepted": {"type": "boolean"},
                "automaticActivation": {"const": False},
                "blockers": {
                    "type": "array",
                    "items": {"type": "string", "pattern": "^[A-Z0-9_]{1,96}$"},
                    "maxItems": 8,
                },
            },
            "required": [
                "state",
                "candidateId",
                "currentModel",
                "predictionAccepted",
                "performanceAccepted",
                "automaticActivation",
                "blockers",
            ],
        },
        "sections": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "recalculatedBacktest": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "status": {"const": "RECALCULATED"},
                        "baselineNetReturn": ratio,
                        "guideNetReturn": ratio,
                        "strictNetReturn": ratio,
                    },
                    "required": [
                        "status",
                        "baselineNetReturn",
                        "guideNetReturn",
                        "strictNetReturn",
                    ],
                },
                "fixedDailyForecast": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "status": {"enum": ["NOT_AVAILABLE", "PARTIAL", "REALIZED"]},
                        "totalCount": {"type": "integer", "minimum": 0},
                        "realizedCount": {"type": "integer", "minimum": 0},
                        "pendingCount": {"type": "integer", "minimum": 0},
                        "mae": ratio,
                        "rmse": ratio,
                        "bias": ratio,
                    },
                    "required": [
                        "status",
                        "totalCount",
                        "realizedCount",
                        "pendingCount",
                        "mae",
                        "rmse",
                        "bias",
                    ],
                },
                "actualTrading": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "status": {"enum": ["NO_REALIZED_TRADES", "REALIZED"]},
                        "closedPositionCount": {"type": "integer", "minimum": 0},
                        "openPositionCount": {"type": "integer", "minimum": 0},
                        "realizedPnlKrw": {"type": "integer"},
                        "unrealizedStatus": {"enum": ["NONE", "OPEN"]},
                    },
                    "required": [
                        "status",
                        "closedPositionCount",
                        "openPositionCount",
                        "realizedPnlKrw",
                        "unrealizedStatus",
                    ],
                },
            },
            "required": ["recalculatedBacktest", "fixedDailyForecast", "actualTrading"],
        },
    }
    payload_properties: dict[str, object] = {
        "report": {
            "type": "object",
            "additionalProperties": False,
            "properties": report_properties,
            "required": sorted(report_properties),
        },
        "lastRefreshStatus": {"enum": ["SUCCESS", "FAILED_LAST_SUCCESS_PRESERVED"]},
        "lastFailureCode": {"type": ["string", "null"], "pattern": "^[A-Z0-9_]{1,96}$"},
        "lastFailureAt": {"type": ["string", "null"], "format": "date-time"},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": payload_properties,
        "required": sorted(payload_properties),
    }


def build_overlay() -> dict[str, object]:
    document = {
        "openapi": "3.1.1",
        "info": {"title": "World news v2 lookup and RAG overlay", "version": "1.0.0"},
        "paths": {
            "/api/v2/rag/world-news": {
                "get": {
                    "operationId": "ragV2WorldNews",
                    "summary": "세계 뉴스 조회",
                    "description": "발행일이 없어도 최초 관측 시각으로 조회한다. Decision, Signal, Risk, Order 권한은 없다.",
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string", "maxLength": 200, "default": ""},
                        },
                        {
                            "name": "limit",
                            "in": "query",
                            "required": False,
                            "schema": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 50,
                                "default": 20,
                            },
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "World-news page",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/WorldNewsV2Page"}
                                }
                            },
                        },
                        "400": {
                            "description": "Invalid query",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/WorldNewsV2Error"}
                                }
                            },
                        },
                        "401": {
                            "description": "Authentication required",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/WorldNewsV2Error"}
                                }
                            },
                        },
                        "503": {
                            "description": "Lookup unavailable",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/WorldNewsV2Error"}
                                }
                            },
                        },
                    },
                    "security": [{"bearerAuth": []}],
                    "tags": ["world-news-controller"],
                }
            },
            "/api/v1/dashboard/performance-reports/latest": {
                "get": {
                    "operationId": "latestOwnerPerformanceReport",
                    "summary": "Latest immutable owner performance report generation.",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ApiResponseOwnerPerformanceReport"
                                    }
                                }
                            },
                        }
                    },
                    "security": [{"bearerAuth": []}],
                    "tags": ["dashboard-controller"],
                }
            },
            "/api/v4/automation/capital-policy": {
                "get": {
                    "operationId": "readAutomationCapitalPolicyV1",
                    "summary": "다음 세션 자본·재투자 정책 조회",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ApiResponseAutomationCapitalPolicyV1"
                                    }
                                }
                            },
                        }
                    },
                    "security": [{"bearerAuth": []}],
                    "tags": ["automation-v4-controller"],
                },
                "put": {
                    "operationId": "putAutomationCapitalPolicyV1",
                    "summary": "재투자 토글을 다음 XKRX 세션부터 변경",
                    "parameters": [
                        {
                            "name": "X-Idempotency-Key",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string", "minLength": 8, "maxLength": 128},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/PutAutomationCapitalPolicyV1Request"
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ApiResponseAutomationCapitalPolicyV1"
                                    }
                                }
                            },
                        }
                    },
                    "security": [{"bearerAuth": []}],
                    "tags": ["automation-v4-controller"],
                },
            },
            "/api/v4/automation/capital-status": {
                "get": {
                    "operationId": "readAutomationCapitalStatusV1",
                    "summary": "자본 원장과 목표·현재 비중 조회",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/ApiResponseAutomationCapitalStatusV1"
                                    }
                                }
                            },
                        }
                    },
                    "security": [{"bearerAuth": []}],
                    "tags": ["automation-v4-controller"],
                }
            },
        },
        "components": {
            "schemas": {
                "WorldNewsV2Item": item_schema(),
                "WorldNewsV2CollectionStatus": collection_schema(),
                "WorldNewsV2Page": page_schema(),
                "WorldNewsV2Error": error_schema(),
                "OwnerPerformanceReport": performance_report_schema(),
                "ApiResponseOwnerPerformanceReport": {
                    "type": "object",
                    "properties": {
                        "data": {
                            "oneOf": [
                                {"$ref": "#/components/schemas/OwnerPerformanceReport"},
                                {"type": "null"},
                            ]
                        },
                        "error": {
                            "oneOf": [{"$ref": "#/components/schemas/ApiError"}, {"type": "null"}]
                        },
                        "requestId": {"type": "string"},
                        "success": {"type": "boolean"},
                        "warnings": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/ApiWarning"},
                        },
                    },
                },
                "WorldNewsItem": spring_world_news_item_schema(),
                "WorldNewsCollectionStatus": spring_world_news_collection_schema(),
                "WorldNewsPage": spring_world_news_page_schema(),
                "AutomationCapitalPolicyV1": capital_policy_schema(),
                "PutAutomationCapitalPolicyV1Request": capital_policy_request_schema(),
                "ApiResponseAutomationCapitalPolicyV1": capital_policy_envelope_schema(),
                "AutomationCapitalStatusV1": capital_status_schema(),
                "ApiResponseAutomationCapitalStatusV1": capital_status_envelope_schema(),
            }
        },
    }
    return _sort_required(document)  # type: ignore[return-value]


def project_previous(document: dict[str, object]) -> dict[str, object]:
    overlay = build_overlay()
    current_paths = document.get("paths", {})
    current_schemas = document.get("components", {}).get("schemas", {})  # type: ignore[union-attr]
    for path, value in overlay["paths"].items():  # type: ignore[union-attr]
        if current_paths.get(path) != value:  # type: ignore[union-attr]
            raise ContractValidationError(f"world-news path drift: {path}")
    for name, value in overlay["components"]["schemas"].items():  # type: ignore[index,union-attr]
        if current_schemas.get(name) != value:  # type: ignore[union-attr]
            raise ContractValidationError(f"world-news schema drift: {name}")
    result = copy.deepcopy(document)
    for path in overlay["paths"]:  # type: ignore[union-attr]
        result["paths"].pop(path, None)  # type: ignore[union-attr]
    for name in overlay["components"]["schemas"]:  # type: ignore[index,union-attr]
        result["components"]["schemas"].pop(name, None)  # type: ignore[index,union-attr]
    return result


def positive_fixture() -> dict[str, object]:
    return {
        "items": [
            {
                "documentId": "news_doc_" + "1" * 32,
                "documentVersionId": "news_ver_" + "2" * 32,
                "sourceId": "src_gdelt_world_news",
                "provider": "GDELT_GQG",
                "providerDocumentId": None,
                "canonicalUrl": "https://example.com/world/supply-chain",
                "republicationOfDocumentId": None,
                "identityStatus": "VERIFIED",
                "title": "세계 공급망 동향",
                "boundedQuote": "공급망 병목이 완화되고 있다고 설명했다.",
                "boundedPassage": None,
                "language": "ko",
                "publishedAt": None,
                "publicationStatus": "MISSING",
                "providerObservedAt": "2026-09-08T03:00:00Z",
                "firstSeenAt": "2026-09-08T03:01:00Z",
                "availableAt": "2026-09-08T03:02:00Z",
                "rightsProfile": "GDELT_METADATA_QUOTE",
                "externalLlmAllowed": False,
                "lookupAllowed": True,
                "ragRetrievalAllowed": True,
                "promptUntrusted": True,
                "collectionStatus": "COMPLETE",
                "contentSha256": "a" * 64,
                "versionSha256": "b" * 64,
            }
        ],
        "collections": [],
        "asOf": "2026-09-08T04:00:00Z",
        "decisionAuthority": "NONE",
        "signalAuthority": "NONE",
        "orderAuthority": "NONE",
    }


def performance_positive_fixture() -> dict[str, object]:
    return {
        "report": {
            "contractId": "owner-performance-report.v1",
            "reportId": "perf_report_" + "1" * 24,
            "reportVersion": 2,
            "supersedesReportId": "perf_report_" + "0" * 24,
            "correctionOfReportId": None,
            "generatedAt": "2026-09-08T06:00:00Z",
            "sourceStart": "2026-08-18",
            "sourceEnd": "2026-09-08",
            "sourceGenerationSha256": "a" * 64,
            "modelSha256": "b" * 64,
            "principleVersionId": "pvr_" + "c" * 32,
            "principleVersion": 3,
            "costBps": 35,
            "modelAdoption": {
                "state": "RESEARCH_EVALUATED",
                "candidateId": None,
                "currentModel": "EQUAL_WEIGHT_50_50",
                "predictionAccepted": False,
                "performanceAccepted": False,
                "automaticActivation": False,
                "blockers": ["NO_DUAL_ACCEPTANCE_CANDIDATE"],
            },
            "sections": {
                "recalculatedBacktest": {
                    "status": "RECALCULATED",
                    "baselineNetReturn": 0.01,
                    "guideNetReturn": 0.02,
                    "strictNetReturn": 0.005,
                },
                "fixedDailyForecast": {
                    "status": "PARTIAL",
                    "totalCount": 62,
                    "realizedCount": 31,
                    "pendingCount": 31,
                    "mae": 0.01,
                    "rmse": 0.02,
                    "bias": -0.001,
                },
                "actualTrading": {
                    "status": "NO_REALIZED_TRADES",
                    "closedPositionCount": 0,
                    "openPositionCount": 1,
                    "realizedPnlKrw": 0,
                    "unrealizedStatus": "OPEN",
                },
            },
        },
        "lastRefreshStatus": "SUCCESS",
        "lastFailureCode": None,
        "lastFailureAt": None,
    }


def write() -> None:
    if not PREVIOUS.exists():
        PREVIOUS.write_bytes(CURRENT.read_bytes())
    overlay = build_overlay()
    previous = json.loads(PREVIOUS.read_text(encoding="utf-8"))
    current = copy.deepcopy(previous)
    current["paths"].update(overlay["paths"])
    current["components"]["schemas"].update(overlay["components"]["schemas"])
    for path, value in (
        (OVERLAY, overlay),
        (SCHEMA, item_schema()),
        (POSITIVE, positive_fixture()),
        (PERFORMANCE_POSITIVE, performance_positive_fixture()),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
    negative = {
        "missing-publication-with-date.invalid.json": {
            **positive_fixture()["items"][0],
            "publishedAt": "2026-09-08T01:00:00Z",
        },  # type: ignore[index]
        "finnhub-external.invalid.json": {
            **positive_fixture()["items"][0],
            "provider": "FINNHUB_MARKET_NEWS",
            "rightsProfile": "FINNHUB_PERSONAL_LOCAL",
            "externalLlmAllowed": True,
        },  # type: ignore[index]
        "raw-body.invalid.json": {**positive_fixture()["items"][0], "rawProviderBody": "forbidden"},  # type: ignore[index]
    }
    NEGATIVE.mkdir(parents=True, exist_ok=True)
    for name, value in negative.items():
        (NEGATIVE / name).write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
    CURRENT.write_text(
        json.dumps(current, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    write()
