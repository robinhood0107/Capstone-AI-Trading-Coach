"""Generate the closed P1 Owner-First Phase A contracts and fixtures."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Final

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.generated_artifact_io import write_generated_path  # noqa: E402
from contracts.generate_principle_contracts import ContractValidationError  # noqa: E402

FEATURE_ORDER: Final[tuple[str, ...]] = (
    "open",
    "high",
    "low",
    "raw_close",
    "volume",
    "return_1d",
    "ma5",
    "ma20",
    "rsi14",
)
ARTIFACT_NAMES: Final[tuple[str, ...]] = (
    "model.safetensors",
    "scaler.json",
    "config.json",
    "lstm_signals.parquet",
    "rule_baseline_signals.parquet",
    "backtest_result.json",
    "trade_log.parquet",
    "equity_log.parquet",
    "golden_output.json",
    "model_report.md",
)
ARTIFACT_SCHEMA_IDS: Final[tuple[str, ...]] = (
    "p1-return-model-safetensors.v2",
    "p1-return-scaler.v2",
    "p1-return-config.v2",
    "p1-return-lstm-signals.v2",
    "p1-return-rule-baseline-signals.v2",
    "p1-return-backtest-result.v2",
    "p1-return-trade-log.v2",
    "p1-return-equity-log.v2",
    "p1-return-golden-output.v2",
    "p1-return-model-report.v2",
)
SCHEMA_IDS: Final[tuple[str, ...]] = (
    "p1-return-engine-input-pack.v1",
    "p1-return-engine-artifact-manifest.v2",
    *ARTIFACT_SCHEMA_IDS,
    "p1-scenario-replay-policy.v1",
    "vertex-news-veto.v1",
    "automation-control.v1",
    "automation-run.v1",
    "automation-position.v1",
    "automation-event.v1",
    "journal.v1",
    "p1-lightgbm-research-evaluation.v1",
)
SCHEMA_PATHS: Final[dict[str, str]] = {
    schema_id: f"contracts/schemas/{schema_id}.schema.json" for schema_id in SCHEMA_IDS
}
NEW_PUBLIC_PATHS: Final[tuple[str, ...]] = (
    "/api/v1/automation/status",
    "/api/v1/automation/arm",
    "/api/v1/automation/disarm",
    "/api/v1/automation/runs",
    "/api/v1/journals",
    "/api/v1/journals/{journalId}",
)
RELEASE_V3_HARD_GATES: Final[tuple[str, ...]] = (
    "P1_CORE",
    "PUBLIC_RAG_SEED",
    "OWNER_RAG_BACKEND",
    "BGE_OCR_CPU_INTEL",
    "MARKET_DATA_DAILY",
    "TEAM_B_REAL_ARTIFACT_V2",
    "TEAM_A_REAL_UI_33",
    "VERTEX_NEWS_VETO",
    "JOURNAL",
    "AUTOMATION_CLOSED_LOOP",
    "LIGHTGBM_RESEARCH_DISCLOSURE",
    "SECURITY_RELEASE",
    "SUPPLY_CHAIN_RELEASE",
    "OCI_REPRODUCIBILITY",
    "COMPOSE_E2E",
    "THREE_XKRX_SESSION_SOAK",
)
FROZEN_SHA256: Final[dict[str, str]] = {
    "contracts/catalogs/p1-full-app-release-contract.v2.json": "5e01d701cc3b50b9398a6d1acc1109e86e0418cf27f7b6cdcf385b80f736eda1",
    "deploy/p1/full-app-release-manifest.v2.schema.json": "32de13384c374b6b338c885d0e497cb98ba7d4edbb3150374fa08511b8107f1e",
    "contracts/schemas/p1-return-engine-artifact-manifest.v1.schema.json": "c88d695ac2466ad74a153aeb1fb3e96508912b27b93e293d0d27c6eea2fa1945",
    "contracts/schemas/news_sentiment_summary.v2.schema.json": "f96b99bdd4060601fffa55720da00bf25041daf0104d4874da466b26293d9fde",
    "LICENSE": "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0",
}


def _closed(required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def _schema(schema_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_PATHS[schema_id],
        "title": schema_id,
        **body,
    }


def _sha256() -> dict[str, Any]:
    return {"type": "string", "pattern": "^(?!0{64}$)[0-9a-f]{64}$"}


def _identifier(prefix: str) -> dict[str, Any]:
    return {"type": "string", "pattern": f"^{prefix}_[A-Za-z0-9_-]{{8,96}}$"}


def _timestamp(nullable: bool = False) -> dict[str, Any]:
    value: dict[str, Any] = {"type": "string", "format": "date-time"}
    return {"oneOf": [value, {"type": "null"}]} if nullable else value


def _symbol_array() -> dict[str, Any]:
    return {
        "type": "array",
        "minItems": 31,
        "maxItems": 31,
        "uniqueItems": True,
        "items": {"type": "string", "pattern": "^[0-9]{6}$"},
        "contains": {"const": "132030"},
        "minContains": 1,
        "maxContains": 1,
    }


def _feature_order() -> dict[str, Any]:
    return {
        "type": "array",
        "minItems": len(FEATURE_ORDER),
        "maxItems": len(FEATURE_ORDER),
        "prefixItems": [{"const": item} for item in FEATURE_ORDER],
        "items": False,
    }


def _input_pack_schema() -> dict[str, Any]:
    coverage = _closed(
        ["symbol", "firstSession", "lastSession", "status", "missingMiddleSessions"],
        {
            "symbol": {"type": "string", "pattern": "^[0-9]{6}$"},
            "firstSession": {"type": "string", "format": "date"},
            "lastSession": {"type": "string", "format": "date"},
            "status": {"enum": ["COMPLETE", "EDGE_TRUNCATED"]},
            "missingMiddleSessions": {"const": 0},
        },
    )
    file_item = _closed(
        ["path", "sizeBytes", "sha256", "contentType"],
        {
            "path": {
                "type": "string",
                "pattern": "^[A-Za-z0-9][A-Za-z0-9._/-]{0,239}$",
            },
            "sizeBytes": {"type": "integer", "minimum": 1},
            "sha256": _sha256(),
            "contentType": {"enum": ["PARQUET", "JSON", "JSONL", "YAML"]},
        },
    )
    return _schema(
        "p1-return-engine-input-pack.v1",
        _closed(
            [
                "contractId",
                "universe",
                "calendar",
                "period",
                "coverage",
                "dataPolicy",
                "macroSnapshot",
                "featureOrder",
                "modelConfig",
                "costModel",
                "ownerRiskEvaluator",
                "files",
                "canonicalManifestSha256",
            ],
            {
                "contractId": {"const": "p1-return-engine-input-pack.v1"},
                "universe": _closed(
                    ["universeId", "symbols", "domesticStockCount", "goldEtfSymbol"],
                    {
                        "universeId": {"const": "P1_EXACT_31_V1"},
                        "symbols": _symbol_array(),
                        "domesticStockCount": {"const": 30},
                        "goldEtfSymbol": {"const": "132030"},
                    },
                ),
                "calendar": _closed(
                    [
                        "mic",
                        "timezone",
                        "calendarVersion",
                        "correctionGenerationSha256",
                        "sessionsSha256",
                    ],
                    {
                        "mic": {"const": "XKRX"},
                        "timezone": {"const": "Asia/Seoul"},
                        "calendarVersion": {"const": "exchange-calendars-4.13.2"},
                        "correctionGenerationSha256": _sha256(),
                        "sessionsSha256": _sha256(),
                    },
                ),
                "period": _closed(
                    [
                        "firstSession",
                        "lastSession",
                        "minimumYears",
                        "trainEnd",
                        "validationEnd",
                        "testStart",
                    ],
                    {
                        "firstSession": {"type": "string", "format": "date"},
                        "lastSession": {"type": "string", "format": "date"},
                        "minimumYears": {"type": "number", "minimum": 3},
                        "trainEnd": {"type": "string", "format": "date"},
                        "validationEnd": {"type": "string", "format": "date"},
                        "testStart": {"type": "string", "format": "date"},
                    },
                ),
                "coverage": {
                    "type": "array",
                    "minItems": 31,
                    "maxItems": 31,
                    "items": coverage,
                },
                "dataPolicy": _closed(
                    [
                        "priceBasis",
                        "minimumDailyYears",
                        "corporateActionExclusionsSha256",
                        "globalSplitSha256",
                        "newsFeatures",
                        "gdeltInputs",
                        "intradayFeatures",
                        "providerCredentialsIncluded",
                        "accountOrderDataIncluded",
                    ],
                    {
                        "priceBasis": {"const": "RAW_CLOSE"},
                        "minimumDailyYears": {"type": "number", "minimum": 3},
                        "corporateActionExclusionsSha256": _sha256(),
                        "globalSplitSha256": _sha256(),
                        "newsFeatures": {"const": 0},
                        "gdeltInputs": {"const": 0},
                        "intradayFeatures": {"const": 0},
                        "providerCredentialsIncluded": {"const": False},
                        "accountOrderDataIncluded": {"const": False},
                    },
                ),
                "macroSnapshot": _closed(
                    ["contractId", "seriesCount", "availableAtBound", "manifestSha256"],
                    {
                        "contractId": {"const": "ecos_macro_snapshot"},
                        "seriesCount": {"type": "integer", "minimum": 0, "maximum": 2},
                        "availableAtBound": {"const": True},
                        "manifestSha256": _sha256(),
                    },
                ),
                "featureOrder": _feature_order(),
                "modelConfig": _closed(
                    [
                        "perSymbolIndependent",
                        "windowSize",
                        "hiddenSize",
                        "layerCount",
                        "dropout",
                        "outputSize",
                        "loss",
                        "optimizer",
                        "learningRate",
                        "seed",
                        "cpuDeterministic",
                        "threadCount",
                        "hyperparameterSearchCount",
                        "finalTestReviewCount",
                    ],
                    {
                        "perSymbolIndependent": {"const": True},
                        "windowSize": {"const": 20},
                        "hiddenSize": {"const": 128},
                        "layerCount": {"const": 3},
                        "dropout": {"const": 0.2},
                        "outputSize": {"const": 1},
                        "loss": {"const": "SmoothL1"},
                        "optimizer": {"const": "Adam"},
                        "learningRate": {"const": 0.0005},
                        "seed": {"const": 0},
                        "cpuDeterministic": {"const": True},
                        "threadCount": {"const": 1},
                        "hyperparameterSearchCount": {"const": 0},
                        "finalTestReviewCount": {"const": 0},
                    },
                ),
                "costModel": _closed(
                    [
                        "costModelId",
                        "roundTripCostBps",
                        "appliesIdenticallyToScenarios",
                        "actualKisFeeClaim",
                    ],
                    {
                        "costModelId": {"const": "CONSERVATIVE_FIXED_35BPS_V1"},
                        "roundTripCostBps": {"const": 35},
                        "appliesIdenticallyToScenarios": {"const": True},
                        "actualKisFeeClaim": {"const": False},
                    },
                ),
                "ownerRiskEvaluator": _closed(
                    ["contractId", "providerCalls", "orderAuthority"],
                    {
                        "contractId": {"const": "s2-2-system-rule-catalog/v1"},
                        "providerCalls": {"const": 0},
                        "orderAuthority": {"const": "NONE"},
                    },
                ),
                "files": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 64,
                    "uniqueItems": True,
                    "items": file_item,
                },
                "canonicalManifestSha256": _sha256(),
            },
        ),
    )


def _artifact_manifest_schema() -> dict[str, Any]:
    artifact = _closed(
        ["path", "semanticSchema", "sizeBytes", "sha256"],
        {
            "path": {"enum": list(ARTIFACT_NAMES)},
            "semanticSchema": {
                "enum": [SCHEMA_PATHS[item] for item in ARTIFACT_SCHEMA_IDS]
            },
            "sizeBytes": {"type": "integer", "minimum": 1},
            "sha256": _sha256(),
        },
    )
    return _schema(
        "p1-return-engine-artifact-manifest.v2",
        _closed(
            [
                "contractId",
                "runId",
                "evidenceMode",
                "realTeamB",
                "performanceClaimAllowed",
                "orderAuthority",
                "modelQuality",
                "mockRuntimeEligible",
                "furtherTuningRequired",
                "inputPackSha256",
                "producer",
                "artifacts",
            ],
            {
                "contractId": {"const": "p1-return-engine-artifact-manifest.v2"},
                "runId": _identifier("run"),
                "evidenceMode": {"enum": ["REAL_TEAM_B", "SYNTHETIC_GOLDEN"]},
                "realTeamB": {"type": "boolean"},
                "performanceClaimAllowed": {"const": False},
                "orderAuthority": {"const": "NONE"},
                "modelQuality": {
                    "enum": ["PASS", "BELOW_BASELINE", "NOT_EVALUATED_SYNTHETIC"]
                },
                "mockRuntimeEligible": {"type": "boolean"},
                "furtherTuningRequired": {"const": False},
                "inputPackSha256": _sha256(),
                "producer": _closed(
                    [
                        "commitSha256",
                        "dependencyLockSha256",
                        "dockerfileSha256",
                        "trainingCodeSha256",
                        "featureOrderSha256",
                        "splitSha256",
                        "configSha256",
                        "goldenOutputSha256",
                        "seed",
                        "networkCalls",
                        "springCalls",
                        "accountCalls",
                        "orderCalls",
                    ],
                    {
                        "commitSha256": _sha256(),
                        "dependencyLockSha256": _sha256(),
                        "dockerfileSha256": _sha256(),
                        "trainingCodeSha256": _sha256(),
                        "featureOrderSha256": _sha256(),
                        "splitSha256": _sha256(),
                        "configSha256": _sha256(),
                        "goldenOutputSha256": _sha256(),
                        "seed": {"const": 0},
                        "networkCalls": {"const": 0},
                        "springCalls": {"const": 0},
                        "accountCalls": {"const": 0},
                        "orderCalls": {"const": 0},
                    },
                ),
                "artifacts": {
                    "type": "array",
                    "minItems": 10,
                    "maxItems": 10,
                    "items": artifact,
                },
            },
        ),
    )


def _artifact_semantic_schemas() -> dict[str, dict[str, Any]]:
    signal_row = _closed(
        [
            "symbol",
            "sessionDate",
            "signal",
            "confidence",
            "currentClose",
            "forecastClose",
            "expectedReturn",
        ],
        {
            "symbol": {"type": "string", "pattern": "^[0-9]{6}$"},
            "sessionDate": {"type": "string", "format": "date"},
            "signal": {"enum": ["BUY", "HOLD", "SELL"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "currentClose": {"type": "number", "exclusiveMinimum": 0},
            "forecastClose": {"type": "number", "minimum": 0},
            "expectedReturn": {"type": "number"},
        },
    )
    base: dict[str, dict[str, Any]] = {}

    def add(schema_id: str, file_name: str, semantic: dict[str, Any]) -> None:
        base[schema_id] = _schema(
            schema_id,
            _closed(
                ["contractId", "fileName", "semantic"],
                {
                    "contractId": {"const": schema_id},
                    "fileName": {"const": file_name},
                    "semantic": semantic,
                },
            ),
        )

    add(
        ARTIFACT_SCHEMA_IDS[0],
        ARTIFACT_NAMES[0],
        _closed(
            [
                "format",
                "pickleFree",
                "tensorCount",
                "symbolNamespaces",
                "dtype",
                "finite",
            ],
            {
                "format": {"const": "SAFETENSORS"},
                "pickleFree": {"const": True},
                "tensorCount": {"type": "integer", "minimum": 31},
                "symbolNamespaces": _symbol_array(),
                "dtype": {"const": "FLOAT32"},
                "finite": {"const": True},
            },
        ),
    )
    add(
        ARTIFACT_SCHEMA_IDS[1],
        ARTIFACT_NAMES[1],
        _closed(
            ["symbols", "featureOrder", "fitScope", "finite"],
            {
                "symbols": _symbol_array(),
                "featureOrder": _feature_order(),
                "fitScope": {"const": "TRAIN_ONLY"},
                "finite": {"const": True},
            },
        ),
    )
    add(
        ARTIFACT_SCHEMA_IDS[2],
        ARTIFACT_NAMES[2],
        _closed(
            [
                "windowSize",
                "hiddenSize",
                "layerCount",
                "dropout",
                "outputSize",
                "loss",
                "optimizer",
                "learningRate",
                "seed",
                "threadCount",
                "deterministicAlgorithms",
            ],
            {
                "windowSize": {"const": 20},
                "hiddenSize": {"const": 128},
                "layerCount": {"const": 3},
                "dropout": {"const": 0.2},
                "outputSize": {"const": 1},
                "loss": {"const": "SmoothL1"},
                "optimizer": {"const": "Adam"},
                "learningRate": {"const": 0.0005},
                "seed": {"const": 0},
                "threadCount": {"const": 1},
                "deterministicAlgorithms": {"const": True},
            },
        ),
    )
    add(
        ARTIFACT_SCHEMA_IDS[3],
        ARTIFACT_NAMES[3],
        _closed(
            ["rowSchema", "symbols", "rowCount", "finite"],
            {
                "rowSchema": signal_row,
                "symbols": _symbol_array(),
                "rowCount": {"type": "integer", "minimum": 31},
                "finite": {"const": True},
            },
        ),
    )
    add(
        ARTIFACT_SCHEMA_IDS[4],
        ARTIFACT_NAMES[4],
        _closed(
            ["rowSchema", "symbols", "rowCount", "finite"],
            {
                "rowSchema": signal_row,
                "symbols": _symbol_array(),
                "rowCount": {"type": "integer", "minimum": 31},
                "finite": {"const": True},
            },
        ),
    )
    metric = _closed(
        ["scenario", "netReturn", "mdd", "sharpe", "tradeCount", "costModelId"],
        {
            "scenario": {"enum": ["BASELINE", "GUIDE", "STRICT"]},
            "netReturn": {"type": "number"},
            "mdd": {"type": "number", "maximum": 0},
            "sharpe": {"type": "number"},
            "tradeCount": {"type": "integer", "minimum": 0},
            "costModelId": {"const": "CONSERVATIVE_FIXED_35BPS_V1"},
        },
    )
    add(
        ARTIFACT_SCHEMA_IDS[5],
        ARTIFACT_NAMES[5],
        _closed(
            ["scenarios", "independentlyRecomputed", "finite"],
            {
                "scenarios": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": metric,
                },
                "independentlyRecomputed": {"const": True},
                "finite": {"const": True},
            },
        ),
    )
    add(
        ARTIFACT_SCHEMA_IDS[6],
        ARTIFACT_NAMES[6],
        _closed(
            ["columns", "rowCount", "longOnly", "finite"],
            {
                "columns": {
                    "const": [
                        "scenario",
                        "symbol",
                        "entrySession",
                        "exitSession",
                        "side",
                        "quantity",
                        "entryPrice",
                        "exitPrice",
                        "grossReturn",
                        "costBps",
                        "netReturn",
                    ]
                },
                "rowCount": {"type": "integer", "minimum": 0},
                "longOnly": {"const": True},
                "finite": {"const": True},
            },
        ),
    )
    add(
        ARTIFACT_SCHEMA_IDS[7],
        ARTIFACT_NAMES[7],
        _closed(
            ["columns", "rowCount", "initialCapitalKrw", "finite"],
            {
                "columns": {
                    "const": ["scenario", "sessionDate", "equityKrw", "drawdown"]
                },
                "rowCount": {"type": "integer", "minimum": 3},
                "initialCapitalKrw": {"type": "integer", "minimum": 1},
                "finite": {"const": True},
            },
        ),
    )
    add(
        ARTIFACT_SCHEMA_IDS[8],
        ARTIFACT_NAMES[8],
        _closed(
            ["symbols", "forecastFormula", "costModelId", "goldenHash", "finite"],
            {
                "symbols": _symbol_array(),
                "forecastFormula": {"const": "forecastClose/currentClose-1"},
                "costModelId": {"const": "CONSERVATIVE_FIXED_35BPS_V1"},
                "goldenHash": _sha256(),
                "finite": {"const": True},
            },
        ),
    )
    add(
        ARTIFACT_SCHEMA_IDS[9],
        ARTIFACT_NAMES[9],
        _closed(
            [
                "encoding",
                "requiredSections",
                "performanceClaimAllowed",
                "orderAuthority",
            ],
            {
                "encoding": {"const": "UTF-8"},
                "requiredSections": {
                    "const": [
                        "Data",
                        "Model ABI",
                        "Split",
                        "Reproducibility",
                        "Model quality",
                        "Limitations",
                    ]
                },
                "performanceClaimAllowed": {"const": False},
                "orderAuthority": {"const": "NONE"},
            },
        ),
    )
    return base


def _scenario_schema() -> dict[str, Any]:
    scenario = _closed(
        [
            "scenario",
            "signalPolicy",
            "ownerRiskEvaluator",
            "costModelId",
            "roundTripCostBps",
        ],
        {
            "scenario": {"enum": ["BASELINE", "GUIDE", "STRICT"]},
            "signalPolicy": {
                "enum": ["MODEL_ONLY", "OWNER_GUIDE_REPLAY", "OWNER_STRICT_REPLAY"]
            },
            "ownerRiskEvaluator": {"const": "s2-2-system-rule-catalog/v1"},
            "costModelId": {"const": "CONSERVATIVE_FIXED_35BPS_V1"},
            "roundTripCostBps": {"const": 35},
        },
    )
    return _schema(
        "p1-scenario-replay-policy.v1",
        _closed(
            [
                "contractId",
                "scenarios",
                "teamBRiskEngineImplementation",
                "providerCalls",
                "orderAuthority",
            ],
            {
                "contractId": {"const": "p1-scenario-replay-policy.v1"},
                "scenarios": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": scenario,
                },
                "teamBRiskEngineImplementation": {"const": False},
                "providerCalls": {"const": 0},
                "orderAuthority": {"const": "NONE"},
            },
        ),
    )


def _vertex_schema() -> dict[str, Any]:
    common = {
        "inputSha256": _sha256(),
        "modelId": {"type": "string", "minLength": 1, "maxLength": 128},
        "promptVersion": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,64}$"},
        "orderAuthority": {"const": "NONE"},
    }
    evidence = _closed(
        [
            "sourceId",
            "sourceType",
            "sourceEventDate",
            "boundedQuote",
            "exactGroundingSupport",
        ],
        {
            "sourceId": {"type": "string", "pattern": "^[A-Za-z0-9._:-]{1,128}$"},
            "sourceType": {"enum": ["OFFICIAL_PRIMARY", "REGISTERED_INDEPENDENT"]},
            "sourceEventDate": {"type": "string", "format": "date"},
            "boundedQuote": {"type": "string", "minLength": 1, "maxLength": 240},
            "exactGroundingSupport": {"const": True},
        },
    )
    available = _closed(
        [
            "status",
            "verdict",
            "tone",
            "eventTypes",
            "evidence",
            "freshnessWindowSatisfied",
            "mutuallyConsistent",
            "inputSha256",
            "outputSha256",
            "modelId",
            "promptVersion",
            "providerCallCount",
            "googleGroundingQueryCount",
            "orderAuthority",
        ],
        {
            "status": {"const": "AVAILABLE"},
            "verdict": {"enum": ["VETO_BUY", "NO_VETO"]},
            "tone": {"enum": ["NEGATIVE", "NEUTRAL", "POSITIVE"]},
            "eventTypes": {
                "type": "array",
                "maxItems": 8,
                "uniqueItems": True,
                "items": {
                    "enum": [
                        "REGULATORY_ACTION",
                        "TRADING_SUSPENSION",
                        "FRAUD_OR_ACCOUNTING",
                        "BANKRUPTCY_OR_RESTRUCTURING",
                        "MAJOR_LITIGATION",
                        "MATERIAL_GUIDANCE_CUT",
                        "MAJOR_OPERATIONAL_INCIDENT",
                        "OTHER_MATERIAL_NEGATIVE",
                    ]
                },
            },
            "evidence": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": evidence,
            },
            "freshnessWindowSatisfied": {"const": True},
            "mutuallyConsistent": {"const": True},
            "outputSha256": _sha256(),
            "providerCallCount": {"const": 1},
            "googleGroundingQueryCount": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1,
            },
            **common,
        },
    )
    abstain = _closed(
        [
            "status",
            "reason",
            "inputSha256",
            "modelId",
            "promptVersion",
            "orderAuthority",
        ],
        {
            "status": {"const": "ABSTAIN"},
            "reason": {
                "enum": [
                    "NO_GROUNDING",
                    "INSUFFICIENT_SOURCES",
                    "UNKNOWN_EVENT_DATE",
                    "STALE_EVIDENCE",
                    "CONFLICTING_SOURCES",
                    "PROMPT_INJECTION",
                    "UNKNOWN_FIELD",
                    "SCHEMA_ERROR",
                    "PROVIDER_TIMEOUT",
                    "BUDGET_EXHAUSTED",
                    "MODEL_PACKET_DRIFT",
                    "PROVIDER_COUNT_MISMATCH",
                ]
            },
            **common,
        },
    )
    return _schema("vertex-news-veto.v1", {"oneOf": [available, abstain]})


def _automation_schemas() -> dict[str, dict[str, Any]]:
    control = _schema(
        "automation-control.v1",
        _closed(
            [
                "contractId",
                "controlState",
                "projectionState",
                "version",
                "brokerageMode",
                "principleId",
                "strategyId",
                "killSwitchActive",
                "certificationStatus",
            ],
            {
                "contractId": {"const": "automation-control.v1"},
                "controlState": {"enum": ["DISARMED", "ARMED", "HALTED"]},
                "projectionState": {"enum": ["DISARMED", "ARMED", "RUNNING", "HALTED"]},
                "version": {"type": "integer", "minimum": 1},
                "brokerageMode": {"enum": ["KIS_MOCK", "INTERNAL_PAPER"]},
                "principleId": _identifier("prc"),
                "strategyId": _identifier("strategy"),
                "killSwitchActive": {"type": "boolean"},
                "certificationStatus": {
                    "enum": [
                        "NOT_REQUIRED_INTERNAL_PAPER",
                        "REQUIRED",
                        "VALID",
                        "EXPIRED",
                        "INVALID",
                    ]
                },
            },
        ),
    )
    run_states = [
        "SCHEDULED",
        "PRECHECK",
        "RECONCILING_PREVIOUS",
        "EXIT_SELECTED",
        "BUY_CANDIDATE_SELECTED",
        "NEWS_CHECKING",
        "NEWS_VETOED",
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
    ]
    run = _schema(
        "automation-run.v1",
        _closed(
            [
                "contractId",
                "runId",
                "sessionDate",
                "state",
                "brokerageMode",
                "selectedSymbol",
                "selectedSide",
                "physicalSubmitCount",
                "vertexCallCount",
                "providerCalls",
                "startedAt",
                "updatedAt",
            ],
            {
                "contractId": {"const": "automation-run.v1"},
                "runId": _identifier("auto_run"),
                "sessionDate": {"type": "string", "format": "date"},
                "state": {"enum": run_states},
                "brokerageMode": {"enum": ["KIS_MOCK", "INTERNAL_PAPER"]},
                "selectedSymbol": {
                    "oneOf": [
                        {"type": "string", "pattern": "^[0-9]{6}$"},
                        {"type": "null"},
                    ]
                },
                "selectedSide": {
                    "oneOf": [{"enum": ["BUY", "SELL"]}, {"type": "null"}]
                },
                "physicalSubmitCount": {"type": "integer", "minimum": 0, "maximum": 1},
                "vertexCallCount": {"type": "integer", "minimum": 0, "maximum": 1},
                "providerCalls": {"type": "integer", "minimum": 0, "maximum": 16},
                "startedAt": _timestamp(),
                "updatedAt": _timestamp(),
            },
        ),
    )
    position = _schema(
        "automation-position.v1",
        _closed(
            [
                "contractId",
                "positionId",
                "accountId",
                "symbol",
                "quantity",
                "entrySession",
                "expirySession",
                "status",
                "botOwned",
                "shortAllowed",
                "createdAt",
                "closedAt",
            ],
            {
                "contractId": {"const": "automation-position.v1"},
                "positionId": _identifier("auto_pos"),
                "accountId": _identifier("acct"),
                "symbol": {"type": "string", "pattern": "^[0-9]{6}$"},
                "quantity": {"const": 1},
                "entrySession": {"type": "string", "format": "date"},
                "expirySession": {"type": "string", "format": "date"},
                "status": {
                    "enum": ["OPEN", "EXIT_PENDING", "CLOSED", "HALTED_MISMATCH"]
                },
                "botOwned": {"const": True},
                "shortAllowed": {"const": False},
                "createdAt": _timestamp(),
                "closedAt": _timestamp(nullable=True),
            },
        ),
    )
    event = _schema(
        "automation-event.v1",
        _closed(
            [
                "contractId",
                "eventId",
                "runId",
                "sequence",
                "eventType",
                "occurredAt",
                "payloadHash",
                "providerCalls",
                "orderSubmits",
                "sanitized",
            ],
            {
                "contractId": {"const": "automation-event.v1"},
                "eventId": _identifier("auto_evt"),
                "runId": _identifier("auto_run"),
                "sequence": {"type": "integer", "minimum": 1},
                "eventType": {
                    "enum": [
                        "CONTROL_CHANGED",
                        "RUN_TRANSITIONED",
                        "BASELINE_CAPTURED",
                        "ACCOUNT_RECONCILED",
                        "EXIT_SELECTED",
                        "BUY_SELECTED",
                        "NEWS_RESULT_RECORDED",
                        "RISK_RESULT_RECORDED",
                        "ORDER_RESERVED",
                        "ORDER_OUTCOME_RECORDED",
                        "CANCEL_RECORDED",
                        "DRIFT_DETECTED",
                        "RUN_HALTED",
                    ]
                },
                "occurredAt": _timestamp(),
                "payloadHash": _sha256(),
                "providerCalls": {"type": "integer", "minimum": 0, "maximum": 16},
                "orderSubmits": {"type": "integer", "minimum": 0, "maximum": 1},
                "sanitized": {"const": True},
            },
        ),
    )
    return {
        "automation-control.v1": control,
        "automation-run.v1": run,
        "automation-position.v1": position,
        "automation-event.v1": event,
    }


def _journal_schema() -> dict[str, Any]:
    return _schema(
        "journal.v1",
        _closed(
            [
                "contractId",
                "journalId",
                "ownerScope",
                "title",
                "content",
                "tags",
                "links",
                "version",
                "createdAt",
                "updatedAt",
                "deletedAt",
            ],
            {
                "contractId": {"const": "journal.v1"},
                "journalId": _identifier("jnl"),
                "ownerScope": _sha256(),
                "title": {"type": "string", "minLength": 1, "maxLength": 120},
                "content": {"type": "string", "minLength": 1, "maxLength": 8192},
                "tags": {
                    "type": "array",
                    "maxItems": 20,
                    "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1, "maxLength": 32},
                },
                "links": _closed(
                    [],
                    {
                        "decisionId": {"oneOf": [_identifier("dec"), {"type": "null"}]},
                        "backtestRunId": {
                            "oneOf": [_identifier("run"), {"type": "null"}]
                        },
                        "ragAnswerId": {
                            "oneOf": [_identifier("rag"), {"type": "null"}]
                        },
                        "orderId": {"oneOf": [_identifier("ord"), {"type": "null"}]},
                        "automationRunId": {
                            "oneOf": [_identifier("auto_run"), {"type": "null"}]
                        },
                    },
                ),
                "version": {"type": "integer", "minimum": 1},
                "createdAt": _timestamp(),
                "updatedAt": _timestamp(),
                "deletedAt": _timestamp(nullable=True),
            },
        ),
    )


def _lightgbm_schema() -> dict[str, Any]:
    return _schema(
        "p1-lightgbm-research-evaluation.v1",
        _closed(
            [
                "contractId",
                "mode",
                "evidenceMode",
                "productionSignalAuthority",
                "riskDecisionAuthority",
                "orderAuthority",
                "providerCalls",
                "finalTestAccessCount",
                "releaseCount",
                "batchPublicationCount",
                "status",
                "limitations",
            ],
            {
                "contractId": {"const": "p1-lightgbm-research-evaluation.v1"},
                "mode": {"const": "RESEARCH_ONLY"},
                "evidenceMode": {
                    "enum": ["HISTORICAL_REPRODUCTION", "OFFLINE_RESEARCH"]
                },
                "productionSignalAuthority": {"const": "NONE"},
                "riskDecisionAuthority": {"const": "NONE"},
                "orderAuthority": {"const": "NONE"},
                "providerCalls": {"const": 0},
                "finalTestAccessCount": {"type": "integer", "minimum": 0},
                "releaseCount": {"const": 0},
                "batchPublicationCount": {"const": 0},
                "status": {"enum": ["REPRODUCED", "BELOW_GATE", "ABSTAIN"]},
                "limitations": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": {"type": "string", "minLength": 1, "maxLength": 256},
                },
            },
        ),
    )


def build_schemas() -> dict[str, dict[str, Any]]:
    schemas = {
        "p1-return-engine-input-pack.v1": _input_pack_schema(),
        "p1-return-engine-artifact-manifest.v2": _artifact_manifest_schema(),
        **_artifact_semantic_schemas(),
        "p1-scenario-replay-policy.v1": _scenario_schema(),
        "vertex-news-veto.v1": _vertex_schema(),
        **_automation_schemas(),
        "journal.v1": _journal_schema(),
        "p1-lightgbm-research-evaluation.v1": _lightgbm_schema(),
    }
    if tuple(schemas) != SCHEMA_IDS:
        raise ContractValidationError("schema output order drifted")
    return schemas


def _symbols() -> list[str]:
    return [f"{index:06d}" for index in range(1, 31)] + ["132030"]


def _fixtures() -> dict[str, dict[str, Any]]:
    sha = "a" * 64
    symbols = _symbols()
    input_pack = {
        "contractId": "p1-return-engine-input-pack.v1",
        "universe": {
            "universeId": "P1_EXACT_31_V1",
            "symbols": symbols,
            "domesticStockCount": 30,
            "goldEtfSymbol": "132030",
        },
        "calendar": {
            "mic": "XKRX",
            "timezone": "Asia/Seoul",
            "calendarVersion": "exchange-calendars-4.13.2",
            "correctionGenerationSha256": sha,
            "sessionsSha256": sha,
        },
        "period": {
            "firstSession": "2023-01-02",
            "lastSession": "2026-08-26",
            "minimumYears": 3,
            "trainEnd": "2025-06-30",
            "validationEnd": "2025-12-30",
            "testStart": "2026-01-02",
        },
        "coverage": [
            {
                "symbol": symbol,
                "firstSession": "2023-01-02",
                "lastSession": "2026-08-26",
                "status": "COMPLETE",
                "missingMiddleSessions": 0,
            }
            for symbol in symbols
        ],
        "dataPolicy": {
            "priceBasis": "RAW_CLOSE",
            "minimumDailyYears": 3,
            "corporateActionExclusionsSha256": sha,
            "globalSplitSha256": sha,
            "newsFeatures": 0,
            "gdeltInputs": 0,
            "intradayFeatures": 0,
            "providerCredentialsIncluded": False,
            "accountOrderDataIncluded": False,
        },
        "macroSnapshot": {
            "contractId": "ecos_macro_snapshot",
            "seriesCount": 2,
            "availableAtBound": True,
            "manifestSha256": sha,
        },
        "featureOrder": list(FEATURE_ORDER),
        "modelConfig": {
            "perSymbolIndependent": True,
            "windowSize": 20,
            "hiddenSize": 128,
            "layerCount": 3,
            "dropout": 0.2,
            "outputSize": 1,
            "loss": "SmoothL1",
            "optimizer": "Adam",
            "learningRate": 0.0005,
            "seed": 0,
            "cpuDeterministic": True,
            "threadCount": 1,
            "hyperparameterSearchCount": 0,
            "finalTestReviewCount": 0,
        },
        "costModel": {
            "costModelId": "CONSERVATIVE_FIXED_35BPS_V1",
            "roundTripCostBps": 35,
            "appliesIdenticallyToScenarios": True,
            "actualKisFeeClaim": False,
        },
        "ownerRiskEvaluator": {
            "contractId": "s2-2-system-rule-catalog/v1",
            "providerCalls": 0,
            "orderAuthority": "NONE",
        },
        "files": [
            {
                "path": "data/ohlcv.parquet",
                "sizeBytes": 1024,
                "sha256": sha,
                "contentType": "PARQUET",
            }
        ],
        "canonicalManifestSha256": sha,
    }
    manifest = {
        "contractId": "p1-return-engine-artifact-manifest.v2",
        "runId": "run_team_b_exact31_v1",
        "evidenceMode": "REAL_TEAM_B",
        "realTeamB": True,
        "performanceClaimAllowed": False,
        "orderAuthority": "NONE",
        "modelQuality": "BELOW_BASELINE",
        "mockRuntimeEligible": True,
        "furtherTuningRequired": False,
        "inputPackSha256": sha,
        "producer": {
            "commitSha256": sha,
            "dependencyLockSha256": sha,
            "dockerfileSha256": sha,
            "trainingCodeSha256": sha,
            "featureOrderSha256": sha,
            "splitSha256": sha,
            "configSha256": sha,
            "goldenOutputSha256": sha,
            "seed": 0,
            "networkCalls": 0,
            "springCalls": 0,
            "accountCalls": 0,
            "orderCalls": 0,
        },
        "artifacts": [
            {
                "path": name,
                "semanticSchema": SCHEMA_PATHS[schema_id],
                "sizeBytes": 1024,
                "sha256": sha,
            }
            for name, schema_id in zip(ARTIFACT_NAMES, ARTIFACT_SCHEMA_IDS, strict=True)
        ],
    }
    fixtures: dict[str, dict[str, Any]] = {
        "p1-return-engine-input-pack.v1": input_pack,
        "p1-return-engine-artifact-manifest.v2": manifest,
    }
    artifact_semantics: list[dict[str, Any]] = [
        {
            "format": "SAFETENSORS",
            "pickleFree": True,
            "tensorCount": 248,
            "symbolNamespaces": symbols,
            "dtype": "FLOAT32",
            "finite": True,
        },
        {
            "symbols": symbols,
            "featureOrder": list(FEATURE_ORDER),
            "fitScope": "TRAIN_ONLY",
            "finite": True,
        },
        {
            "windowSize": 20,
            "hiddenSize": 128,
            "layerCount": 3,
            "dropout": 0.2,
            "outputSize": 1,
            "loss": "SmoothL1",
            "optimizer": "Adam",
            "learningRate": 0.0005,
            "seed": 0,
            "threadCount": 1,
            "deterministicAlgorithms": True,
        },
        {
            "rowSchema": {
                "symbol": "000001",
                "sessionDate": "2026-08-26",
                "signal": "BUY",
                "confidence": 0.6,
                "currentClose": 100,
                "forecastClose": 101,
                "expectedReturn": 0.01,
            },
            "symbols": symbols,
            "rowCount": 31,
            "finite": True,
        },
        {
            "rowSchema": {
                "symbol": "000001",
                "sessionDate": "2026-08-26",
                "signal": "HOLD",
                "confidence": 0.5,
                "currentClose": 100,
                "forecastClose": 100,
                "expectedReturn": 0,
            },
            "symbols": symbols,
            "rowCount": 31,
            "finite": True,
        },
        {
            "scenarios": [
                {
                    "scenario": scenario,
                    "netReturn": 0,
                    "mdd": 0,
                    "sharpe": 0,
                    "tradeCount": 0,
                    "costModelId": "CONSERVATIVE_FIXED_35BPS_V1",
                }
                for scenario in ("BASELINE", "GUIDE", "STRICT")
            ],
            "independentlyRecomputed": True,
            "finite": True,
        },
        {
            "columns": [
                "scenario",
                "symbol",
                "entrySession",
                "exitSession",
                "side",
                "quantity",
                "entryPrice",
                "exitPrice",
                "grossReturn",
                "costBps",
                "netReturn",
            ],
            "rowCount": 0,
            "longOnly": True,
            "finite": True,
        },
        {
            "columns": ["scenario", "sessionDate", "equityKrw", "drawdown"],
            "rowCount": 3,
            "initialCapitalKrw": 10_000_000,
            "finite": True,
        },
        {
            "symbols": symbols,
            "forecastFormula": "forecastClose/currentClose-1",
            "costModelId": "CONSERVATIVE_FIXED_35BPS_V1",
            "goldenHash": sha,
            "finite": True,
        },
        {
            "encoding": "UTF-8",
            "requiredSections": [
                "Data",
                "Model ABI",
                "Split",
                "Reproducibility",
                "Model quality",
                "Limitations",
            ],
            "performanceClaimAllowed": False,
            "orderAuthority": "NONE",
        },
    ]
    for schema_id, name, semantic in zip(
        ARTIFACT_SCHEMA_IDS, ARTIFACT_NAMES, artifact_semantics, strict=True
    ):
        fixtures[schema_id] = {
            "contractId": schema_id,
            "fileName": name,
            "semantic": semantic,
        }
    fixtures["p1-scenario-replay-policy.v1"] = {
        "contractId": "p1-scenario-replay-policy.v1",
        "scenarios": [
            {
                "scenario": "BASELINE",
                "signalPolicy": "MODEL_ONLY",
                "ownerRiskEvaluator": "s2-2-system-rule-catalog/v1",
                "costModelId": "CONSERVATIVE_FIXED_35BPS_V1",
                "roundTripCostBps": 35,
            },
            {
                "scenario": "GUIDE",
                "signalPolicy": "OWNER_GUIDE_REPLAY",
                "ownerRiskEvaluator": "s2-2-system-rule-catalog/v1",
                "costModelId": "CONSERVATIVE_FIXED_35BPS_V1",
                "roundTripCostBps": 35,
            },
            {
                "scenario": "STRICT",
                "signalPolicy": "OWNER_STRICT_REPLAY",
                "ownerRiskEvaluator": "s2-2-system-rule-catalog/v1",
                "costModelId": "CONSERVATIVE_FIXED_35BPS_V1",
                "roundTripCostBps": 35,
            },
        ],
        "teamBRiskEngineImplementation": False,
        "providerCalls": 0,
        "orderAuthority": "NONE",
    }
    fixtures["vertex-news-veto.v1"] = {
        "status": "AVAILABLE",
        "verdict": "VETO_BUY",
        "tone": "NEGATIVE",
        "eventTypes": ["REGULATORY_ACTION"],
        "evidence": [
            {
                "sourceId": "official-primary-1",
                "sourceType": "OFFICIAL_PRIMARY",
                "sourceEventDate": "2026-08-26",
                "boundedQuote": "공식 발표가 규제 조치를 확인했다.",
                "exactGroundingSupport": True,
            }
        ],
        "freshnessWindowSatisfied": True,
        "mutuallyConsistent": True,
        "inputSha256": sha,
        "outputSha256": sha,
        "modelId": "gemini-3.5-flash",
        "promptVersion": "vertex-news-veto-v1",
        "providerCallCount": 1,
        "googleGroundingQueryCount": 1,
        "orderAuthority": "NONE",
    }
    fixtures["automation-control.v1"] = {
        "contractId": "automation-control.v1",
        "controlState": "DISARMED",
        "projectionState": "DISARMED",
        "version": 1,
        "brokerageMode": "KIS_MOCK",
        "principleId": "prc_12345678",
        "strategyId": "strategy_12345678",
        "killSwitchActive": False,
        "certificationStatus": "REQUIRED",
    }
    fixtures["automation-run.v1"] = {
        "contractId": "automation-run.v1",
        "runId": "auto_run_12345678",
        "sessionDate": "2026-08-26",
        "state": "SCHEDULED",
        "brokerageMode": "KIS_MOCK",
        "selectedSymbol": None,
        "selectedSide": None,
        "physicalSubmitCount": 0,
        "vertexCallCount": 0,
        "providerCalls": 0,
        "startedAt": "2026-08-26T08:10:00+09:00",
        "updatedAt": "2026-08-26T08:10:00+09:00",
    }
    fixtures["automation-position.v1"] = {
        "contractId": "automation-position.v1",
        "positionId": "auto_pos_12345678",
        "accountId": "acct_12345678",
        "symbol": "005930",
        "quantity": 1,
        "entrySession": "2026-08-26",
        "expirySession": "2026-09-02",
        "status": "OPEN",
        "botOwned": True,
        "shortAllowed": False,
        "createdAt": "2026-08-26T09:15:00+09:00",
        "closedAt": None,
    }
    fixtures["automation-event.v1"] = {
        "contractId": "automation-event.v1",
        "eventId": "auto_evt_12345678",
        "runId": "auto_run_12345678",
        "sequence": 1,
        "eventType": "RUN_TRANSITIONED",
        "occurredAt": "2026-08-26T08:10:00+09:00",
        "payloadHash": sha,
        "providerCalls": 0,
        "orderSubmits": 0,
        "sanitized": True,
    }
    fixtures["journal.v1"] = {
        "contractId": "journal.v1",
        "journalId": "jnl_12345678",
        "ownerScope": sha,
        "title": "원칙 검토",
        "content": "신호와 위험 근거를 검토했다.",
        "tags": ["RiskEngine"],
        "links": {
            "decisionId": None,
            "backtestRunId": None,
            "ragAnswerId": None,
            "orderId": None,
            "automationRunId": None,
        },
        "version": 1,
        "createdAt": "2026-08-26T10:00:00+09:00",
        "updatedAt": "2026-08-26T10:00:00+09:00",
        "deletedAt": None,
    }
    fixtures["p1-lightgbm-research-evaluation.v1"] = {
        "contractId": "p1-lightgbm-research-evaluation.v1",
        "mode": "RESEARCH_ONLY",
        "evidenceMode": "HISTORICAL_REPRODUCTION",
        "productionSignalAuthority": "NONE",
        "riskDecisionAuthority": "NONE",
        "orderAuthority": "NONE",
        "providerCalls": 0,
        "finalTestAccessCount": 0,
        "releaseCount": 0,
        "batchPublicationCount": 0,
        "status": "ABSTAIN",
        "limitations": ["Production activation is disabled."],
    }
    return fixtures


def _catalog() -> dict[str, Any]:
    return {
        "contractId": "p1-owner-phase-a-contract-lock.v1",
        "returnEngine": {
            "inputPackSchema": SCHEMA_PATHS["p1-return-engine-input-pack.v1"],
            "artifactManifestSchema": SCHEMA_PATHS[
                "p1-return-engine-artifact-manifest.v2"
            ],
            "artifactNames": list(ARTIFACT_NAMES),
            "artifactSemanticSchemas": [
                SCHEMA_PATHS[item] for item in ARTIFACT_SCHEMA_IDS
            ],
            "featureOrder": list(FEATURE_ORDER),
            "newsFeatures": 0,
            "gdeltCalls": 0,
            "vertexCalls": 0,
        },
        "vertexNewsVeto": {
            "schema": SCHEMA_PATHS["vertex-news-veto.v1"],
            "newBuyCandidateCount": 1,
            "secondCandidateFallback": False,
            "sellCalls": 0,
            "orderAuthority": "NONE",
        },
        "automationSchemas": [
            SCHEMA_PATHS[item]
            for item in (
                "automation-control.v1",
                "automation-run.v1",
                "automation-position.v1",
                "automation-event.v1",
            )
        ],
        "journalSchema": SCHEMA_PATHS["journal.v1"],
        "lightgbmResearchSchema": SCHEMA_PATHS["p1-lightgbm-research-evaluation.v1"],
        "additiveOpenApi": "contracts/openapi/p1-automation-journal.v1.openapi.json",
        "rootOpenApiOperationCountBeforeRuntime": 48,
        "rootOpenApiOperationCountAfterRuntime": 56,
        "providerPhysicalCallsDuringContractLock": 0,
    }


def _release_catalog_v3() -> dict[str, Any]:
    return {
        "contractId": "p1-full-app-release-contract.v3",
        "releaseVersion": "1.0.0",
        "manifestSchema": "deploy/p1/full-app-release-manifest.v3.schema.json",
        "historicalContracts": [
            {
                "contractId": "p1-offline-demo-release-manifest.v1",
                "status": "PRESERVED_HISTORICAL_REGRESSION",
            },
            {
                "contractId": "p1-full-app-release-contract.v2",
                "status": "PRESERVED_SUPERSEDED_FULL_APP_V2",
            },
        ],
        "hardGates": list(RELEASE_V3_HARD_GATES),
        "returnEngineArtifactSchema": SCHEMA_PATHS[
            "p1-return-engine-artifact-manifest.v2"
        ],
        "teamARequiredOperationCount": 33,
        "openApiOperationCount": 56,
        "lightgbm": "RESEARCH_ONLY_NO_SIGNAL_OR_ORDER_AUTHORITY",
        "kisLiveOrderCalls": 0,
        "gdeltCalls": 0,
        "releaseAuthority": "NONE_UNTIL_ALL_HARD_GATES_PASS",
    }


def _release_manifest_schema_v3() -> dict[str, Any]:
    gates = _closed(
        list(RELEASE_V3_HARD_GATES),
        {
            gate: {"enum": ["PASS", "BLOCKED", "NOT_RUN"]}
            for gate in RELEASE_V3_HARD_GATES
        },
    )
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "deploy/p1/full-app-release-manifest.v3.schema.json",
        "title": "p1-full-app-release-manifest.v3",
        **_closed(
            [
                "contractId",
                "stage",
                "releaseVersion",
                "commitSha",
                "treeSha",
                "hardGates",
                "teamBManifestSha256",
                "teamAImageDigest",
                "providerReceipt",
                "lightgbm",
                "kisLiveOrderCalls",
                "gdeltCalls",
                "released",
            ],
            {
                "contractId": {"const": "p1-full-app-release-manifest.v3"},
                "stage": {"enum": ["CANDIDATE", "FINAL"]},
                "releaseVersion": {"const": "1.0.0"},
                "commitSha": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
                "treeSha": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
                "hardGates": gates,
                "teamBManifestSha256": _sha256(),
                "teamAImageDigest": {
                    "type": "string",
                    "pattern": "^sha256:(?!0{64}$)[0-9a-f]{64}$",
                },
                "providerReceipt": _closed(
                    [
                        "vertexGroundedCalls",
                        "kisTokenCalls",
                        "kisDailyCalls",
                        "ecosCalls",
                        "kisQuoteCalls",
                        "kisBrokerageCalls",
                        "orderSubmitCalls",
                        "cancelCalls",
                        "retries",
                    ],
                    {
                        "vertexGroundedCalls": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "kisTokenCalls": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "kisDailyCalls": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 31,
                        },
                        "ecosCalls": {"type": "integer", "minimum": 0, "maximum": 2},
                        "kisQuoteCalls": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "kisBrokerageCalls": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 7,
                        },
                        "orderSubmitCalls": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 1,
                        },
                        "cancelCalls": {"type": "integer", "minimum": 0, "maximum": 1},
                        "retries": {"const": 0},
                    },
                ),
                "lightgbm": {"const": "RESEARCH_ONLY_NO_SIGNAL_OR_ORDER_AUTHORITY"},
                "kisLiveOrderCalls": {"const": 0},
                "gdeltCalls": {"const": 0},
                "released": {"type": "boolean"},
            },
        ),
        "allOf": [
            {
                "if": {
                    "properties": {"stage": {"const": "FINAL"}},
                    "required": ["stage"],
                },
                "then": {
                    "properties": {
                        "hardGates": {
                            "properties": {
                                gate: {"const": "PASS"}
                                for gate in RELEASE_V3_HARD_GATES
                            }
                        },
                        "released": {"const": True},
                    }
                },
            },
            {
                "if": {
                    "properties": {"stage": {"const": "CANDIDATE"}},
                    "required": ["stage"],
                },
                "then": {"properties": {"released": {"const": False}}},
            },
        ],
    }
    return schema


def _additive_openapi(schemas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    security = [{"bearerAuth": []}]
    response = {
        "200": {"description": "Success"},
        "400": {"description": "Validation error"},
        "401": {"description": "Authentication required"},
        "404": {"description": "Not found"},
        "409": {"description": "Conflict"},
    }

    def operation(operation_id: str) -> dict[str, Any]:
        return {
            "operationId": operation_id,
            "security": security,
            "responses": copy.deepcopy(response),
        }

    paths = {
        "/api/v1/automation/status": {"get": operation("getAutomationStatus")},
        "/api/v1/automation/arm": {"post": operation("armAutomation")},
        "/api/v1/automation/disarm": {"post": operation("disarmAutomation")},
        "/api/v1/automation/runs": {"get": operation("listAutomationRuns")},
        "/api/v1/journals": {
            "post": operation("createJournal"),
            "get": operation("listJournals"),
        },
        "/api/v1/journals/{journalId}": {
            "patch": operation("updateJournal"),
            "delete": operation("deleteJournal"),
        },
    }
    paths["/api/v1/journals/{journalId}"]["parameters"] = [
        {
            "name": "journalId",
            "in": "path",
            "required": True,
            "schema": {"type": "string", "pattern": "^jnl_[A-Za-z0-9_-]{8,96}$"},
        }
    ]
    return {
        "openapi": "3.1.1",
        "jsonSchemaDialect": "https://spec.openapis.org/oas/3.1/dialect/base",
        "info": {
            "title": "P1 Automation and Journal additive contract",
            "version": "1.0.0",
        },
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                }
            },
            "schemas": {
                "AutomationControl": schemas["automation-control.v1"],
                "AutomationRun": schemas["automation-run.v1"],
                "Journal": schemas["journal.v1"],
            },
        },
    }


def _bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def build_outputs() -> dict[Path, bytes]:
    schemas = build_schemas()
    fixtures = _fixtures()
    outputs: dict[Path, bytes] = {}
    for schema_id, schema in schemas.items():
        outputs[ROOT / SCHEMA_PATHS[schema_id]] = _bytes(schema)
        outputs[ROOT / "contracts" / "examples" / f"{schema_id}.valid.json"] = _bytes(
            fixtures[schema_id]
        )
        invalid = copy.deepcopy(fixtures[schema_id])
        if isinstance(invalid, dict):
            invalid["unexpected"] = True
        outputs[
            ROOT
            / "contracts"
            / "examples"
            / "invalid"
            / f"{schema_id}.unknown-field.invalid.json"
        ] = _bytes(invalid)
    outputs[
        ROOT / "contracts" / "catalogs" / "p1-owner-phase-a-contract-lock.v1.json"
    ] = _bytes(_catalog())
    outputs[
        ROOT / "contracts" / "catalogs" / "p1-full-app-release-contract.v3.json"
    ] = _bytes(_release_catalog_v3())
    outputs[ROOT / "deploy" / "p1" / "full-app-release-manifest.v3.schema.json"] = (
        _bytes(_release_manifest_schema_v3())
    )
    outputs[
        ROOT / "contracts" / "openapi" / "p1-automation-journal.v1.openapi.json"
    ] = _bytes(_additive_openapi(schemas))
    return outputs


def validate_semantics(schema_id: str, payload: dict[str, Any]) -> None:
    if schema_id == "p1-return-engine-input-pack.v1":
        symbols = payload["universe"]["symbols"]
        if len(set(symbols)) != 31 or symbols.count("132030") != 1:
            raise ContractValidationError(
                "input pack must contain exact-31 with 132030 exactly once"
            )
        if [item["symbol"] for item in payload["coverage"]] != symbols:
            raise ContractValidationError("coverage must match universe order exactly")
    elif schema_id == "p1-return-engine-artifact-manifest.v2":
        paths = [item["path"] for item in payload["artifacts"]]
        schemas = [item["semanticSchema"] for item in payload["artifacts"]]
        if paths != list(ARTIFACT_NAMES) or schemas != [
            SCHEMA_PATHS[item] for item in ARTIFACT_SCHEMA_IDS
        ]:
            raise ContractValidationError(
                "artifact manifest must bind exact ordered 10 files and schemas"
            )
        if payload["evidenceMode"] == "REAL_TEAM_B" and not payload["realTeamB"]:
            raise ContractValidationError("REAL_TEAM_B must set realTeamB=true")
        if payload["evidenceMode"] == "SYNTHETIC_GOLDEN" and payload["realTeamB"]:
            raise ContractValidationError("synthetic golden cannot claim real Team B")
    elif schema_id == "p1-scenario-replay-policy.v1":
        if [item["scenario"] for item in payload["scenarios"]] != [
            "BASELINE",
            "GUIDE",
            "STRICT",
        ]:
            raise ContractValidationError(
                "scenario order must be BASELINE/GUIDE/STRICT"
            )
    elif schema_id == "vertex-news-veto.v1" and payload["status"] == "AVAILABLE":
        if payload["verdict"] == "VETO_BUY" and not payload["eventTypes"]:
            raise ContractValidationError(
                "VETO_BUY needs one or more negative event types"
            )
        source_types = [item["sourceType"] for item in payload["evidence"]]
        has_official = "OFFICIAL_PRIMARY" in source_types
        independent_count = source_types.count("REGISTERED_INDEPENDENT")
        if payload["verdict"] == "VETO_BUY" and not (
            has_official or independent_count >= 2
        ):
            raise ContractValidationError(
                "VETO_BUY requires one official primary or two independent sources"
            )
    elif schema_id == "automation-control.v1":
        running = payload["projectionState"] == "RUNNING"
        if running and payload["controlState"] == "HALTED":
            raise ContractValidationError("HALTED control cannot project RUNNING")


def validate_generated() -> None:
    schemas = build_schemas()
    fixtures = _fixtures()
    for schema_id, schema in schemas.items():
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = list(validator.iter_errors(fixtures[schema_id]))
        if errors:
            raise ContractValidationError(
                f"{schema_id} fixture failed: {errors[0].message}"
            )
        validate_semantics(schema_id, fixtures[schema_id])
        invalid = copy.deepcopy(fixtures[schema_id])
        invalid["unexpected"] = True
        if not list(validator.iter_errors(invalid)):
            raise ContractValidationError(f"{schema_id} unknown-field fixture passed")
    release_schema = _release_manifest_schema_v3()
    Draft202012Validator.check_schema(release_schema)
    for relative, expected in FROZEN_SHA256.items():
        path = ROOT / relative
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise ContractValidationError(
                f"frozen historical bytes drifted: {relative}"
            )
    if (
        ROOT / "contracts" / "schemas" / "return-engine-news-feature.v1.schema.json"
    ).exists():
        raise ContractValidationError(
            "forbidden Return Engine news feature contract exists"
        )


def generate(*, check: bool) -> None:
    validate_generated()
    for path, payload in build_outputs().items():
        if check:
            if not path.is_file() or path.read_bytes() != payload:
                raise ContractValidationError(
                    f"generated artifact drift: {path.relative_to(ROOT).as_posix()}"
                )
        else:
            write_generated_path(ROOT, path, payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        generate(check=args.check)
    except (ContractValidationError, OSError) as error:
        print(f"P1_OWNER_PHASE_A_CONTRACT_LOCK_FAILED: {error}", file=sys.stderr)
        return 1
    print("P1_OWNER_PHASE_A_CONTRACT_LOCK_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
