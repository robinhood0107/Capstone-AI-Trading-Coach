from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "contracts" / "schemas"
EXAMPLE_DIR = ROOT / "contracts" / "examples"
INVALID_DIR = EXAMPLE_DIR / "invalid"
CATALOG_DIR = ROOT / "contracts" / "catalogs"
OPENAPI_DIR = ROOT / "contracts" / "openapi"

HASH_PATTERN = "^[0-9a-f]{64}$"
SYMBOL_PATTERN = "^[0-9A-Z./-]{1,32}$"
SCHEMA_IDS = (
    "hmm_regime_report.v1",
    "gbm_monte_carlo_report.v1",
    "mean_reversion_report.v1",
    "financial_engineering_snapshot.v1",
    "financial_engineering_report_manifest.v1",
    "option_contract_terms.v1",
    "cross_market_event_study.v2",
    "lightgbm_policy_replay.v1",
    "cross_market_threshold_freeze.v1",
    "cross_market_risk_snapshot.v2",
)


class ContractValidationError(ValueError):
    pass


def _hash() -> dict[str, Any]:
    return {"type": "string", "pattern": HASH_PATTERN}


def _timestamp() -> dict[str, Any]:
    return {"type": "string", "format": "date-time"}


def _number(minimum: float | None = None, maximum: float | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "number"}
    if minimum is not None:
        result["minimum"] = minimum
    if maximum is not None:
        result["maximum"] = maximum
    return result


def _object(schema_id: str, required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://capstone.local/contracts/{schema_id}",
        "title": schema_id,
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": {
            "contractId": {"const": schema_id},
            **properties,
        },
    }


def _research_authority_properties() -> dict[str, Any]:
    return {
        "decisionAuthority": {"const": "NONE"},
        "runtimeRiskEngineSource": {"const": False},
        "productionSignalAuthority": {"const": False},
        "researchOnly": {"const": True},
    }


SCHEMAS: dict[str, dict[str, Any]] = {
    "hmm_regime_report.v1": _object(
        "hmm_regime_report.v1",
        [
            "contractId",
            "availability",
            "asOf",
            "featureContract",
            "selectedSeed",
            "state",
            "posterior",
            "normalizedEntropy",
            "warnings",
            "activeWireAuthority",
            "artifactHash",
        ],
        {
            "availability": {"enum": ["AVAILABLE", "ABSTAIN"]},
            "asOf": _timestamp(),
            "featureContract": {"const": "LOG_RETURN_CAUSAL_VOL20_DDOF1_TRAIN_SCALE_DDOF0"},
            "selectedSeed": {"type": ["integer", "null"], "enum": [11, 29, 47, 71, 101, None]},
            "state": {"type": ["string", "null"], "enum": ["RISK_ON", "RISK_OFF", None]},
            "posterior": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "required": ["RISK_ON", "RISK_OFF", "maxPosterior"],
                "properties": {
                    "RISK_ON": _number(0, 1),
                    "RISK_OFF": _number(0, 1),
                    "maxPosterior": _number(0, 1),
                },
            },
            "normalizedEntropy": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "warnings": {"type": "array", "maxItems": 16, "items": {"type": "string", "maxLength": 128}},
            "activeWireAuthority": {"const": "NONE"},
            "artifactHash": _hash(),
        },
    ),
    "gbm_monte_carlo_report.v1": _object(
        "gbm_monte_carlo_report.v1",
        [
            "contractId",
            "asOf",
            "model",
            "measure",
            "nPaths",
            "seed",
            "rng",
            "numpyVersion",
            "drawOrder",
            "fanQuantiles",
            "stochastic",
            "deterministicStress",
            "quality",
            "artifactHash",
        ],
        {
            "asOf": _timestamp(),
            "model": {"const": "EXACT_EXPONENTIAL_GBM"},
            "measure": {"const": "P_PREDICTIVE_MEAN"},
            "nPaths": {"type": "integer", "minimum": 1, "maximum": 10000},
            "seed": {"type": "integer", "minimum": 0, "maximum": 4294967295},
            "rng": {"const": "PCG64"},
            "numpyVersion": {"type": "string", "minLength": 1, "maxLength": 32},
            "drawOrder": {"const": "PATH_MAJOR_MAX_DRAW_PREFIX"},
            "fanQuantiles": {"const": [0.05, 0.25, 0.5, 0.75, 0.95]},
            "stochastic": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "lossProbability",
                    "lossProbabilityStandardError",
                    "wilson95",
                    "varLoss95Amount",
                    "tailMeanLoss95Amount",
                    "varLoss95Return",
                    "tailMeanLoss95Return",
                    "batchCount",
                ],
                "properties": {
                    "lossProbability": _number(0, 1),
                    "lossProbabilityStandardError": _number(0),
                    "wilson95": {"type": "array", "prefixItems": [_number(0, 1), _number(0, 1)], "minItems": 2, "maxItems": 2},
                    "varLoss95Amount": _number(),
                    "tailMeanLoss95Amount": _number(),
                    "varLoss95Return": _number(),
                    "tailMeanLoss95Return": _number(),
                    "batchCount": {"const": 20},
                },
            },
            "deterministicStress": {
                "type": "object",
                "additionalProperties": False,
                "required": ["jumpGap", "fatTailProxy", "volatilityClusterBurst", "leverageMarginShortfall", "liquiditySpreadImpactCrowding"],
                "properties": {name: _number() for name in ("jumpGap", "fatTailProxy", "volatilityClusterBurst", "leverageMarginShortfall", "liquiditySpreadImpactCrowding")},
            },
            "quality": {"enum": ["PASS", "WARN", "UNCERTAIN"]},
            "artifactHash": _hash(),
        },
    ),
    "mean_reversion_report.v1": _object(
        "mean_reversion_report.v1",
        ["contractId", "availability", "asOf", "windowObservations", "phi", "theta", "longRunMean", "halfLifeSessions", "zScore", "classification", "adf", "decisionAuthority", "artifactHash"],
        {
            "availability": {"enum": ["AVAILABLE", "ABSTAIN"]},
            "asOf": _timestamp(),
            "windowObservations": {"const": 60},
            "phi": {"type": ["number", "null"]},
            "theta": {"type": ["number", "null"], "exclusiveMinimum": 0},
            "longRunMean": {"type": ["number", "null"]},
            "halfLifeSessions": {"type": ["number", "null"], "exclusiveMinimum": 0},
            "zScore": {"type": ["number", "null"]},
            "classification": {"enum": ["MEAN_REVERTING", "NOT_MEAN_REVERTING", "NOT_ESTIMABLE"]},
            "adf": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "required": ["regression", "autolag", "statistic", "pValue", "criticalValues", "nobs", "authority"],
                "properties": {
                    "regression": {"const": "c"},
                    "autolag": {"const": "AIC"},
                    "statistic": _number(),
                    "pValue": _number(0, 1),
                    "criticalValues": {"type": "object", "additionalProperties": {"type": "number"}, "minProperties": 1, "maxProperties": 8},
                    "nobs": {"type": "integer", "minimum": 1, "maximum": 60},
                    "authority": {"const": "REFERENCE_ONLY"},
                },
            },
            "decisionAuthority": {"const": "WARN_CANDIDATE_ONLY"},
            "artifactHash": _hash(),
        },
    ),
    "financial_engineering_snapshot.v1": _object(
        "financial_engineering_snapshot.v1",
        ["contractId", "schemaVersion", "symbol", "sessionDate", "asOf", "availableAt", "sourceManifestHash", "configHash", "numericPayloadHash", "artifactHash", "availability", "quality", "staleness", "numericPayload", "createdAt"],
        {
            "schemaVersion": {"const": 1},
            "symbol": {"type": "string", "pattern": SYMBOL_PATTERN},
            "sessionDate": {"type": "string", "format": "date"},
            "asOf": _timestamp(),
            "availableAt": _timestamp(),
            "sourceManifestHash": _hash(),
            "configHash": _hash(),
            "numericPayloadHash": _hash(),
            "artifactHash": _hash(),
            "availability": {"enum": ["AVAILABLE", "ABSTAIN", "NOT_AVAILABLE"]},
            "quality": {"enum": ["PASS", "WARN", "EVIDENCE_GAP"]},
            "staleness": {"enum": ["FRESH", "STALE", "NOT_ESTIMABLE"]},
            "numericPayload": {"type": "object", "maxProperties": 32, "additionalProperties": {"type": ["number", "string", "boolean", "null"]}},
            "createdAt": _timestamp(),
        },
    ),
    "financial_engineering_report_manifest.v1": _object(
        "financial_engineering_report_manifest.v1",
        ["contractId", "runId", "snapshotArtifactHash", "reportArtifactHash", "reportBytes", "complete", "steps", "createdAt"],
        {
            "runId": {"type": "string", "pattern": "^[0-9a-f-]{36}$"},
            "snapshotArtifactHash": _hash(),
            "reportArtifactHash": _hash(),
            "reportBytes": {"type": "integer", "minimum": 1, "maximum": 1048576},
            "complete": {"const": True},
            "steps": {
                "type": "array",
                "minItems": 5,
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "status", "errorCode", "wallTimeMillis", "peakMemoryBytes"],
                    "properties": {
                        "name": {"enum": ["STORED_COLLECTION", "FEATURE", "INFERENCE", "SNAPSHOT", "REPORT"]},
                        "status": {"enum": ["COMPLETE", "NOT_AVAILABLE", "FAILED"]},
                        "errorCode": {"type": ["string", "null"], "maxLength": 64},
                        "wallTimeMillis": {"type": "integer", "minimum": 0, "maximum": 1800000},
                        "peakMemoryBytes": {"type": "integer", "minimum": 0, "maximum": 2147483648},
                    },
                },
            },
            "createdAt": _timestamp(),
        },
    ),
    "option_contract_terms.v1": _object(
        "option_contract_terms.v1",
        ["contractId", "termsId", "optionRight", "strike", "lastTradingAt", "timezone", "multiplier", "exerciseStyle", "settlementType", "effectiveFrom", "effectiveTo", "sourceUrl", "sourceHash"],
        {
            "termsId": {"type": "string", "pattern": "^[A-Z0-9_.-]{1,128}$"},
            "optionRight": {"enum": ["CALL", "PUT"]},
            "strike": {"type": "number", "exclusiveMinimum": 0},
            "lastTradingAt": _timestamp(),
            "timezone": {"const": "Asia/Seoul"},
            "multiplier": {"type": "number", "exclusiveMinimum": 0},
            "exerciseStyle": {"const": "EUROPEAN"},
            "settlementType": {"const": "CASH"},
            "effectiveFrom": _timestamp(),
            "effectiveTo": {"type": ["string", "null"], "format": "date-time"},
            "sourceUrl": {"type": "string", "format": "uri", "pattern": "^https://"},
            "sourceHash": _hash(),
        },
    ),
    "cross_market_event_study.v2": _object(
        "cross_market_event_study.v2",
        ["contractId", *list(_research_authority_properties()), "evidenceMode", "datasetStatus", "coverageYears", "split", "purgeEmbargoSessions", "thresholdCandidates", "severeLossCutoff", "transactionCostSensitivityBps", "timing", "metrics", "causeEvidence", "bootstrap", "performanceClaimAllowed", "artifactHash"],
        {
            **_research_authority_properties(),
            "evidenceMode": {"enum": ["SYNTHETIC_FIXTURE", "HISTORICAL_REPLAY", "PROSPECTIVE_SHADOW"]},
            "datasetStatus": {"enum": ["AVAILABLE", "DATASET_UNAVAILABLE", "NOT_ESTIMABLE"]},
            "coverageYears": _number(0, 10),
            "split": {"const": [0.6, 0.2, 0.2]},
            "purgeEmbargoSessions": {"const": 5},
            "thresholdCandidates": {"const": [95, 97.5, 99]},
            "severeLossCutoff": {"type": ["number", "null"]},
            "transactionCostSensitivityBps": {"const": [25, 30, 35]},
            "timing": {
                "type": "object",
                "additionalProperties": False,
                "required": ["detectionLatencyMillis", "preOpenLeadTimeMillis", "preOpenStatus", "estimationStatus"],
                "properties": {
                    "detectionLatencyMillis": {"type": ["integer", "null"], "minimum": 0},
                    "preOpenLeadTimeMillis": {"type": ["integer", "null"]},
                    "preOpenStatus": {"enum": ["EARLY", "AT_OPEN", "LATE", "NOT_ESTIMABLE"]},
                    "estimationStatus": {"enum": ["ESTIMATED", "NOT_ESTIMABLE"]},
                },
            },
            "metrics": {
                "type": "object",
                "additionalProperties": False,
                "required": ["triggerCount", "falseBlockRate", "downsideAvoidedBps", "missedUpsideBps", "netProtectionBps"],
                "properties": {
                    "triggerCount": {"type": "integer", "minimum": 0},
                    **{name: {"type": "object", "additionalProperties": False, "required": ["value", "estimationStatus"], "properties": {"value": {"type": ["number", "null"]}, "estimationStatus": {"enum": ["ESTIMATED", "NOT_ESTIMABLE"]}}} for name in ("falseBlockRate", "downsideAvoidedBps", "missedUpsideBps", "netProtectionBps")},
                },
            },
            "causeEvidence": {
                "type": "object",
                "additionalProperties": False,
                "required": ["conflictDenominator", "unsupportedDenominator", "evidenceConflictRate", "unsupportedCausalityRate"],
                "properties": {
                    "conflictDenominator": {"type": "integer", "minimum": 0},
                    "unsupportedDenominator": {"type": "integer", "minimum": 0},
                    **{name: {"type": "object", "additionalProperties": False, "required": ["value", "estimationStatus"], "properties": {"value": {"type": ["number", "null"], "minimum": 0, "maximum": 1}, "estimationStatus": {"enum": ["ESTIMATED", "NOT_ESTIMABLE"]}}} for name in ("evidenceConflictRate", "unsupportedCausalityRate")},
                },
            },
            "bootstrap": {
                "type": "object",
                "additionalProperties": False,
                "required": ["unit", "blockLengthSessions", "replications", "seed", "interval", "superiorityClaimAllowed"],
                "properties": {
                    "unit": {"const": "EVENT_DATE"},
                    "blockLengthSessions": {"const": 5},
                    "replications": {"const": 2000},
                    "seed": {"type": "integer", "minimum": 0},
                    "interval": {"type": ["array", "null"], "prefixItems": [_number(), _number()], "minItems": 2, "maxItems": 2},
                    "superiorityClaimAllowed": {"type": "boolean"},
                },
            },
            "performanceClaimAllowed": {"type": "boolean"},
            "artifactHash": _hash(),
        },
    ),
    "lightgbm_policy_replay.v1": _object(
        "lightgbm_policy_replay.v1",
        ["contractId", *list(_research_authority_properties()), "datasetStatus", "candidateArtifactHash", "candidateQualificationStatus", "eligibleSide", "evidenceLabel", "performanceClaimAllowed", "artifactHash"],
        {
            **_research_authority_properties(),
            "datasetStatus": {"enum": ["AVAILABLE", "DATASET_UNAVAILABLE", "NOT_ESTIMABLE"]},
            "candidateArtifactHash": {"type": ["string", "null"], "pattern": HASH_PATTERN},
            "candidateQualificationStatus": {"enum": ["AVAILABLE", "FAILED", "NOT_AVAILABLE"]},
            "eligibleSide": {"const": "BUY"},
            "evidenceLabel": {"enum": ["REAL_PIT", "SYNTHETIC_FIXTURE", "NONE"]},
            "performanceClaimAllowed": {"type": "boolean"},
            "artifactHash": _hash(),
        },
    ),
    "cross_market_threshold_freeze.v1": _object(
        "cross_market_threshold_freeze.v1",
        ["contractId", "selectedOn", "selectedPercentile", "candidatePercentiles", "selectionMetricOrder", "validationArtifactHash", "configHash", "immutable", "createdAt"],
        {
            "selectedOn": {"const": "VALIDATION_ONLY"},
            "selectedPercentile": {"enum": [95, 97.5, 99]},
            "candidatePercentiles": {"const": [95, 97.5, 99]},
            "selectionMetricOrder": {"const": ["MAX_NET_PROTECTION_BPS", "MAX_SEVERE_LOSS_RECALL", "MAX_SEVERE_LOSS_PRECISION", "MIN_FALSE_BLOCK_RATE", "HIGHER_PERCENTILE"]},
            "validationArtifactHash": _hash(),
            "configHash": _hash(),
            "immutable": {"const": True},
            "createdAt": _timestamp(),
        },
    ),
    "cross_market_risk_snapshot.v2": _object(
        "cross_market_risk_snapshot.v2",
        ["contractId", "snapshotId", "symbol", "availableAt", "staleAt", "evidenceMode", "storageMode", "availability", "quality", "score", "thresholdPercentile", "thresholdArtifactHash", "configHash", "exposure", "exposureAvailableAt", "exposureCatalogHash", "artifactHash", "semanticInputHash", "runtimeMode", "providerFanoutAllowed"],
        {
            "snapshotId": {"type": "string", "pattern": "^[0-9a-f-]{36}$"},
            "symbol": {"type": "string", "pattern": SYMBOL_PATTERN},
            "availableAt": _timestamp(),
            "staleAt": _timestamp(),
            "evidenceMode": {"enum": ["SYNTHETIC_FIXTURE", "HISTORICAL_REPLAY", "PROSPECTIVE_SHADOW", "MANUAL_EOD"]},
            "storageMode": {"enum": ["ARTIFACT_ONLY", "STORED_SNAPSHOT"]},
            "availability": {"enum": ["AVAILABLE", "UNAVAILABLE", "STALE"]},
            "quality": {"enum": ["PASS", "WARN", "EVIDENCE_GAP"]},
            "score": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
            "thresholdPercentile": {"type": ["number", "null"], "enum": [95, 97.5, 99, None]},
            "thresholdArtifactHash": {"type": ["string", "null"], "pattern": HASH_PATTERN},
            "configHash": _hash(),
            "exposure": {"enum": ["NEW_BUY", "INCREASE_BUY", "SELL", "REDUCE", "LIQUIDATION", "EXISTING_POSITION", "UNCLASSIFIED"]},
            "exposureAvailableAt": _timestamp(),
            "exposureCatalogHash": _hash(),
            "artifactHash": _hash(),
            "semanticInputHash": _hash(),
            "runtimeMode": {"enum": ["OFF", "SHADOW", "WARN_ONLY", "ENFORCED"]},
            "providerFanoutAllowed": {"const": False},
        },
    ),
}


def _sha(char: str) -> str:
    return char * 64


VALID_FIXTURES: dict[str, dict[str, Any]] = {
    "hmm_regime_report.v1": {"contractId": "hmm_regime_report.v1", "availability": "AVAILABLE", "asOf": "2026-08-20T15:30:00+09:00", "featureContract": "LOG_RETURN_CAUSAL_VOL20_DDOF1_TRAIN_SCALE_DDOF0", "selectedSeed": 11, "state": "RISK_OFF", "posterior": {"RISK_ON": 0.2, "RISK_OFF": 0.8, "maxPosterior": 0.8}, "normalizedEntropy": 0.7219, "warnings": [], "activeWireAuthority": "NONE", "artifactHash": _sha("a")},
    "gbm_monte_carlo_report.v1": {"contractId": "gbm_monte_carlo_report.v1", "asOf": "2026-08-20T15:30:00+09:00", "model": "EXACT_EXPONENTIAL_GBM", "measure": "P_PREDICTIVE_MEAN", "nPaths": 10000, "seed": 20260821, "rng": "PCG64", "numpyVersion": "2.5.1", "drawOrder": "PATH_MAJOR_MAX_DRAW_PREFIX", "fanQuantiles": [0.05, 0.25, 0.5, 0.75, 0.95], "stochastic": {"lossProbability": 0.48, "lossProbabilityStandardError": 0.005, "wilson95": [0.4702, 0.4898], "varLoss95Amount": 12.5, "tailMeanLoss95Amount": 16.1, "varLoss95Return": 0.125, "tailMeanLoss95Return": 0.161, "batchCount": 20}, "deterministicStress": {"jumpGap": -0.1, "fatTailProxy": -0.13, "volatilityClusterBurst": -0.08, "leverageMarginShortfall": 0.0, "liquiditySpreadImpactCrowding": -0.03}, "quality": "PASS", "artifactHash": _sha("b")},
    "mean_reversion_report.v1": {"contractId": "mean_reversion_report.v1", "availability": "AVAILABLE", "asOf": "2026-08-20T15:30:00+09:00", "windowObservations": 60, "phi": 0.92, "theta": 0.0833816089, "longRunMean": 11.2, "halfLifeSessions": 8.31295, "zScore": 2.1, "classification": "MEAN_REVERTING", "adf": {"regression": "c", "autolag": "AIC", "statistic": -3.1, "pValue": 0.026, "criticalValues": {"1%": -3.55, "5%": -2.91, "10%": -2.59}, "nobs": 58, "authority": "REFERENCE_ONLY"}, "decisionAuthority": "WARN_CANDIDATE_ONLY", "artifactHash": _sha("c")},
    "financial_engineering_snapshot.v1": {"contractId": "financial_engineering_snapshot.v1", "schemaVersion": 1, "symbol": "005930", "sessionDate": "2026-08-20", "asOf": "2026-08-20T15:30:00+09:00", "availableAt": "2026-08-21T08:10:00+09:00", "sourceManifestHash": _sha("1"), "configHash": _sha("2"), "numericPayloadHash": _sha("3"), "artifactHash": _sha("4"), "availability": "AVAILABLE", "quality": "PASS", "staleness": "FRESH", "numericPayload": {"annualizedVolatility": 0.21, "hmmRiskOffPosterior": 0.8, "ouZScore": 2.1}, "createdAt": "2026-08-21T08:11:00+09:00"},
    "financial_engineering_report_manifest.v1": {"contractId": "financial_engineering_report_manifest.v1", "runId": "00000000-0000-4000-8000-000000000601", "snapshotArtifactHash": _sha("4"), "reportArtifactHash": _sha("5"), "reportBytes": 4096, "complete": True, "steps": [{"name": name, "status": "COMPLETE", "errorCode": None, "wallTimeMillis": 10, "peakMemoryBytes": 1024} for name in ("STORED_COLLECTION", "FEATURE", "INFERENCE", "SNAPSHOT", "REPORT")], "createdAt": "2026-08-21T08:12:00+09:00"},
    "option_contract_terms.v1": {"contractId": "option_contract_terms.v1", "termsId": "KOSPI200_OPTION_FIXTURE_202609_CALL_75000", "optionRight": "CALL", "strike": 75000, "lastTradingAt": "2026-09-10T15:20:00+09:00", "timezone": "Asia/Seoul", "multiplier": 250000, "exerciseStyle": "EUROPEAN", "settlementType": "CASH", "effectiveFrom": "2026-01-01T00:00:00+09:00", "effectiveTo": None, "sourceUrl": "https://global.krx.co.kr/", "sourceHash": _sha("6")},
    "cross_market_event_study.v2": {"contractId": "cross_market_event_study.v2", **{k: v["const"] for k, v in _research_authority_properties().items()}, "evidenceMode": "PROSPECTIVE_SHADOW", "datasetStatus": "DATASET_UNAVAILABLE", "coverageYears": 0, "split": [0.6, 0.2, 0.2], "purgeEmbargoSessions": 5, "thresholdCandidates": [95, 97.5, 99], "severeLossCutoff": None, "transactionCostSensitivityBps": [25, 30, 35], "timing": {"detectionLatencyMillis": None, "preOpenLeadTimeMillis": None, "preOpenStatus": "NOT_ESTIMABLE", "estimationStatus": "NOT_ESTIMABLE"}, "metrics": {"triggerCount": 0, **{name: {"value": None, "estimationStatus": "NOT_ESTIMABLE"} for name in ("falseBlockRate", "downsideAvoidedBps", "missedUpsideBps", "netProtectionBps")}}, "causeEvidence": {"conflictDenominator": 0, "unsupportedDenominator": 0, "evidenceConflictRate": {"value": None, "estimationStatus": "NOT_ESTIMABLE"}, "unsupportedCausalityRate": {"value": None, "estimationStatus": "NOT_ESTIMABLE"}}, "bootstrap": {"unit": "EVENT_DATE", "blockLengthSessions": 5, "replications": 2000, "seed": 20260821, "interval": None, "superiorityClaimAllowed": False}, "performanceClaimAllowed": False, "artifactHash": _sha("7")},
    "lightgbm_policy_replay.v1": {"contractId": "lightgbm_policy_replay.v1", **{k: v["const"] for k, v in _research_authority_properties().items()}, "datasetStatus": "DATASET_UNAVAILABLE", "candidateArtifactHash": None, "candidateQualificationStatus": "NOT_AVAILABLE", "eligibleSide": "BUY", "evidenceLabel": "NONE", "performanceClaimAllowed": False, "artifactHash": _sha("8")},
    "cross_market_threshold_freeze.v1": {"contractId": "cross_market_threshold_freeze.v1", "selectedOn": "VALIDATION_ONLY", "selectedPercentile": 97.5, "candidatePercentiles": [95, 97.5, 99], "selectionMetricOrder": ["MAX_NET_PROTECTION_BPS", "MAX_SEVERE_LOSS_RECALL", "MAX_SEVERE_LOSS_PRECISION", "MIN_FALSE_BLOCK_RATE", "HIGHER_PERCENTILE"], "validationArtifactHash": _sha("9"), "configHash": _sha("a"), "immutable": True, "createdAt": "2026-08-21T08:15:00+09:00"},
    "cross_market_risk_snapshot.v2": {"contractId": "cross_market_risk_snapshot.v2", "snapshotId": "00000000-0000-4000-8000-000000000607", "symbol": "005930", "availableAt": "2026-08-21T08:10:00+09:00", "staleAt": "2026-08-22T08:10:00+09:00", "evidenceMode": "MANUAL_EOD", "storageMode": "STORED_SNAPSHOT", "availability": "AVAILABLE", "quality": "PASS", "score": 98.2, "thresholdPercentile": 97.5, "thresholdArtifactHash": _sha("9"), "configHash": _sha("a"), "exposure": "NEW_BUY", "exposureAvailableAt": "2026-08-21T08:10:00+09:00", "exposureCatalogHash": _sha("b"), "artifactHash": _sha("c"), "semanticInputHash": _sha("d"), "runtimeMode": "WARN_ONLY", "providerFanoutAllowed": False},
}


INVALID_MUTATIONS: dict[str, tuple[str, Any]] = {
    "hmm_regime_report.v1": ("activeWireAuthority", "RISK_DECISION"),
    "gbm_monte_carlo_report.v1": ("nPaths", 10001),
    "mean_reversion_report.v1": ("windowObservations", 59),
    "financial_engineering_snapshot.v1": ("symbol", "../../secret"),
    "financial_engineering_report_manifest.v1": ("complete", False),
    "option_contract_terms.v1": ("exerciseStyle", "AMERICAN"),
    "cross_market_event_study.v2": ("decisionAuthority", "WARN"),
    "lightgbm_policy_replay.v1": ("productionSignalAuthority", True),
    "cross_market_threshold_freeze.v1": ("selectedPercentile", 80),
    "cross_market_risk_snapshot.v2": ("providerFanoutAllowed", True),
}


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _build_catalog_v3() -> dict[str, Any]:
    v2 = json.loads((CATALOG_DIR / "s2-2-system-rule-catalog.v2.json").read_text())
    v3 = json.loads(json.dumps(v2))
    v3["catalogVersion"] = 3
    rule = v3["rules"][14]
    rule["defaultThreshold"] = None
    rule["freshnessPolicy"] = "cross-market-stored-snapshot-v2"
    rule["severitySource"] = "P1_WARN_ONLY_CONFIG"
    rule["thresholdSource"] = "S6_6_IMMUTABLE_THRESHOLD_ARTIFACT"
    rule["requiredConfigFields"] = ["selectedPercentile", "thresholdArtifactHash", "configHash"]
    v3["crossMarketOverlay"] = {
        "approvedRuntimeModes": ["OFF", "SHADOW", "WARN_ONLY"],
        "schemaAcceptedButRuntimeRejectedMode": "ENFORCED",
        "missingThresholdDisposition": "UNAVAILABLE",
        "allowedFrozenPercentiles": [95, 97.5, 99],
        "semanticInputIncludedFields": ["score", "selectedPercentile", "thresholdArtifactHash", "configHash", "exposureCatalogHash"],
        "semanticInputExcludedFields": ["analystEvidence", "ragOutput", "llmOutput", "explanation", "artifactHash"],
        "maximumAuthority": "ALLOW_TO_WARN_NEW_BUY_ONLY",
        "providerFanoutAllowed": False,
    }
    return v3


def _build_s6_option_openapi() -> dict[str, Any]:
    common_request = {
        "type": "object",
        "additionalProperties": False,
        "required": ["contractId", "valuationAt", "spot", "riskFreeRate", "dividendYield"],
        "properties": {
            "contractId": {"type": "string", "minLength": 1, "maxLength": 128},
            "valuationAt": {"type": "string", "format": "date-time"},
            "spot": {"type": "number", "exclusiveMinimum": 0},
            "riskFreeRate": {"type": "number"},
            "dividendYield": {"type": "number"},
        },
    }
    bsm_request = json.loads(json.dumps(common_request))
    bsm_request["required"].append("volatility")
    bsm_request["properties"]["volatility"] = {"type": "number", "exclusiveMinimum": 0}
    iv_request = json.loads(json.dumps(common_request))
    iv_request["required"].extend(["marketPrice", "maxIterations"])
    iv_request["properties"].update(
        {
            "marketPrice": {"type": "number", "exclusiveMinimum": 0},
            "maxIterations": {"type": "integer", "minimum": 1, "maximum": 1000},
        }
    )
    provenance = {
        "type": "object",
        "additionalProperties": False,
        "required": ["termsId", "sourceUrl", "sourceHash", "multiplier", "exerciseStyle", "settlementType", "timezone"],
        "properties": {
            "termsId": {"type": "string"},
            "sourceUrl": {"type": "string", "format": "uri"},
            "sourceHash": _hash(),
            "multiplier": {"type": "number", "exclusiveMinimum": 0},
            "exerciseStyle": {"const": "EUROPEAN"},
            "settlementType": {"const": "CASH"},
            "timezone": {"const": "Asia/Seoul"},
        },
    }
    response_fields = {
        "timeToMaturityYears": {"type": "number", "exclusiveMinimum": 0},
        "provenance": {"$ref": "#/components/schemas/S64ContractProvenance"},
    }
    bsm_response = _object(
        "s6-4-bsm-response.v1",
        ["contractId", "measure", "discountedValue", "timeToMaturityYears", "provenance"],
        {"measure": {"const": "Q_DISCOUNTED_VALUE"}, "discountedValue": _number(0), **response_fields},
    )
    greeks_response = _object(
        "s6-4-greeks-response.v1",
        ["contractId", "measure", "valuationDelta", "conservativeRiskDelta", "gamma", "vegaPerUnitVolatility", "vegaPerVolPoint", "calendarThetaPerYear", "calendarThetaPerDay", "rhoPerUnitRate", "rhoPerRatePoint", "timeToMaturityYears", "provenance"],
        {
            "measure": {"const": "Q_DISCOUNTED_VALUE"},
            "valuationDelta": _number(),
            "conservativeRiskDelta": {"enum": [-1, 1]},
            "gamma": _number(0),
            "vegaPerUnitVolatility": _number(0),
            "vegaPerVolPoint": _number(0),
            "calendarThetaPerYear": _number(),
            "calendarThetaPerDay": _number(),
            "rhoPerUnitRate": _number(),
            "rhoPerRatePoint": _number(),
            **response_fields,
        },
    )
    iv_response = _object(
        "s6-4-iv-response.v1",
        ["contractId", "impliedVolatility", "solver", "measure", "timeToMaturityYears", "provenance"],
        {
            "impliedVolatility": {"type": "number", "minimum": 0.0001, "maximum": 5},
            "solver": {"const": "BOUNDED_BISECTION_0.0001_5.0"},
            "measure": {"const": "Q_DISCOUNTED_VALUE"},
            **response_fields,
        },
    )

    def operation(summary: str, request_ref: str, response_ref: str) -> dict[str, Any]:
        return {
            "summary": summary,
            "security": [{"bearerAuth": []}],
            "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": request_ref}}}},
            "responses": {
                "200": {"description": "Educational valuation result", "content": {"application/json": {"schema": {"$ref": response_ref}}}},
                "400": {"description": "VALIDATION_ERROR, IV_NOT_BRACKETED, or IV_NOT_CONVERGED", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/S64ErrorEnvelope"}}}},
                "401": {"description": "Authentication required", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/S64ErrorEnvelope"}}}},
                "503": {"description": "Bounded Python service unavailable", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/S64ErrorEnvelope"}}}},
            },
        }

    return {
        "openapi": "3.1.1",
        "jsonSchemaDialect": "https://spec.openapis.org/oas/3.1/dialect/base",
        "info": {"title": "Capstone S6.4 educational option valuation", "version": "1.0.0"},
        "paths": {
            "/api/v1/financial-engineering/options/black-scholes": {"post": operation("Black-Scholes valuation", "#/components/schemas/S64BlackScholesRequest", "#/components/schemas/S64BlackScholesResponse")},
            "/api/v1/financial-engineering/options/greeks": {"post": operation("Black-Scholes Greeks", "#/components/schemas/S64GreeksRequest", "#/components/schemas/S64GreeksResponse")},
            "/api/v1/financial-engineering/options/implied-volatility": {"post": operation("Bounded implied volatility", "#/components/schemas/S64ImpliedVolatilityRequest", "#/components/schemas/S64ImpliedVolatilityResponse")},
        },
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}},
            "schemas": {
                "S64BlackScholesRequest": bsm_request,
                "S64GreeksRequest": bsm_request,
                "S64ImpliedVolatilityRequest": iv_request,
                "S64ContractProvenance": provenance,
                "S64BlackScholesResponse": bsm_response,
                "S64GreeksResponse": greeks_response,
                "S64ImpliedVolatilityResponse": iv_response,
                "S64ErrorEnvelope": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["success", "requestId", "data", "warnings", "error"],
                    "properties": {
                        "success": {"const": False},
                        "requestId": {"type": "string"},
                        "data": {"type": "null"},
                        "warnings": {"type": "array", "maxItems": 0},
                        "error": {"type": "object", "required": ["code", "message", "details"], "properties": {"code": {"enum": ["VALIDATION_ERROR", "PYTHON_SERVICE_UNAVAILABLE"]}, "message": {"type": "string"}, "details": {"type": "object"}}},
                    },
                },
            },
        },
    }


def build_outputs() -> dict[Path, bytes]:
    outputs: dict[Path, bytes] = {}
    for schema_id, schema in SCHEMAS.items():
        outputs[SCHEMA_DIR / f"{schema_id}.schema.json"] = _canonical_bytes(schema)
        outputs[EXAMPLE_DIR / f"{schema_id}.valid.json"] = _canonical_bytes(VALID_FIXTURES[schema_id])
        invalid = json.loads(json.dumps(VALID_FIXTURES[schema_id]))
        field, value = INVALID_MUTATIONS[schema_id]
        invalid[field] = value
        outputs[INVALID_DIR / f"{schema_id}.contract.invalid.json"] = _canonical_bytes(invalid)
    outputs[CATALOG_DIR / "s2-2-system-rule-catalog.v3.json"] = _canonical_bytes(_build_catalog_v3())
    outputs[OPENAPI_DIR / "s6-financial-engineering.v1.openapi.json"] = _canonical_bytes(_build_s6_option_openapi())
    lock = {
        "contractId": "s6-contract-lock.v1",
        "schemaIds": list(SCHEMA_IDS),
        "catalog": "s2-2-system-rule-catalog.v3",
        "publicOptionValuation": True,
        "providerCallsAllowed": False,
        "hmmActiveWireAllowed": False,
        "lightgbmProductionAllowed": False,
        "p1RuntimeModes": ["OFF", "SHADOW", "WARN_ONLY"],
        "enforcedRuntimeAllowed": False,
    }
    lock["generatedSetHash"] = hashlib.sha256(
        b"".join(outputs[path] for path in sorted(outputs))
    ).hexdigest()
    outputs[CATALOG_DIR / "s6-contract-lock.v1.json"] = _canonical_bytes(lock)
    return outputs


def validate_semantics(schema_id: str, payload: dict[str, Any]) -> None:
    if schema_id == "hmm_regime_report.v1":
        posterior = payload.get("posterior")
        if isinstance(posterior, dict):
            total = float(posterior["RISK_ON"]) + float(posterior["RISK_OFF"])
            if abs(total - 1.0) > 1e-9:
                raise ContractValidationError("HMM posterior must sum to one")
            if payload["availability"] == "AVAILABLE" and posterior["maxPosterior"] < 0.65:
                raise ContractValidationError("low-posterior HMM output must abstain")
    if schema_id == "cross_market_event_study.v2":
        metrics = payload["metrics"]
        if metrics["triggerCount"] == 0:
            for name in ("falseBlockRate", "downsideAvoidedBps", "missedUpsideBps", "netProtectionBps"):
                if metrics[name] != {"value": None, "estimationStatus": "NOT_ESTIMABLE"}:
                    raise ContractValidationError("zero-trigger metrics must be NOT_ESTIMABLE")
        cause = payload["causeEvidence"]
        for denominator, name in (
            ("conflictDenominator", "evidenceConflictRate"),
            ("unsupportedDenominator", "unsupportedCausalityRate"),
        ):
            if cause[denominator] == 0 and cause[name] != {
                "value": None,
                "estimationStatus": "NOT_ESTIMABLE",
            }:
                raise ContractValidationError("zero-denominator cause evidence must be NOT_ESTIMABLE")
        interval = payload["bootstrap"]["interval"]
        if payload["evidenceMode"] != "HISTORICAL_REPLAY" and payload["bootstrap"]["superiorityClaimAllowed"]:
            raise ValueError("non-historical evidence cannot claim superiority")
        if interval is not None and interval[0] <= 0 <= interval[1] and payload["bootstrap"]["superiorityClaimAllowed"]:
            raise ContractValidationError("zero-containing CI forbids superiority claim")
    if schema_id == "lightgbm_policy_replay.v1":
        if payload["candidateQualificationStatus"] != "AVAILABLE" and payload["performanceClaimAllowed"]:
            raise ContractValidationError("unqualified candidate cannot support performance claims")
    if schema_id == "cross_market_risk_snapshot.v2":
        if payload["artifactHash"] == payload["semanticInputHash"]:
            raise ContractValidationError("artifact and semantic input hashes are distinct authorities")
        if payload["availability"] == "AVAILABLE":
            if payload["thresholdPercentile"] not in (95, 97.5, 99) or payload["thresholdArtifactHash"] is None:
                raise ContractValidationError("available snapshot requires immutable S6.6 threshold")
        if payload["availableAt"] != payload["exposureAvailableAt"]:
            raise ContractValidationError("snapshot and exposure must share availableAt")


def write_or_check(*, check: bool) -> None:
    mismatches: list[str] = []
    for path, expected in build_outputs().items():
        if check:
            if not path.exists() or path.read_bytes() != expected:
                mismatches.append(path.relative_to(ROOT).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)
    if mismatches:
        raise SystemExit("S6 contract drift: " + ", ".join(mismatches))
    print("S6_CONTRACT_LOCK_VERIFIED" if check else "S6_CONTRACT_LOCK_GENERATED")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    write_or_check(check=args.check)


if __name__ == "__main__":
    main()
