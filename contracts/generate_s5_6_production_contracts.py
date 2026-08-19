"""S5.6 reconstructed PIT source와 feature bundle v2 계약을 생성한다."""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any, Final, Mapping

from jsonschema import Draft202012Validator

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from contracts.generated_artifact_io import write_generated_artifact  # noqa: E402
from contracts.generate_principle_contracts import (  # noqa: E402
    ContractValidationError,
    canonical_json_bytes,
)


ROOT = _REPO_ROOT
SOURCE_SCHEMA = "contracts/schemas/s5-pit-source-bundle-v1.schema.json"
FEATURE_SCHEMA = "contracts/schemas/s5-feature-bundle-v2.schema.json"
CATALOG = "contracts/catalogs/s5-production-materialization-lock.v1.json"
RECOVERY_CATALOG = "contracts/catalogs/s5-bootstrap-calendar-recovery-lock.v1.json"
FEATURE_COLUMNS: Final[tuple[str, ...]] = (
    "momentum_5",
    "momentum_20",
    "momentum_60",
    "close_sma20_ratio",
    "close_sma60_ratio",
    "rsi14_wilder",
    "macd_signal_spread_ratio",
    "volatility20",
    "log_volume_z20",
    "market_return_5",
    "market_return_20",
    "relative_strength_5",
    "relative_strength_20",
    "base_rate_level",
    "base_rate_change_20",
    "usdkrw_return_5",
    "usdkrw_return_20",
)


def _closed(required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def _sha256() -> dict[str, Any]:
    return {"type": "string", "pattern": "^[0-9a-f]{64}$"}


def _timestamp() -> dict[str, Any]:
    return {"type": "string", "format": "date-time"}


def _temporal_receipt() -> dict[str, Any]:
    properties = {
        "sourceId": {"enum": ["KIS", "KRX", "ECOS"]},
        "operationId": {"type": "string", "minLength": 1, "maxLength": 80},
        "observationDate": {"type": "string", "format": "date"},
        "retrievedAt": _timestamp(),
        "providerAvailableAt": _timestamp(),
        "policyEffectiveAt": _timestamp(),
        "availabilityBasis": {
            "enum": [
                "PROVIDER_FIELD",
                "PROVIDER_AS_OF_SCHEDULE",
                "PROJECT_FIXED_LAG",
                "RETRIEVAL_ONLY",
            ]
        },
        "providerRevision": {"type": "string", "minLength": 1, "maxLength": 128},
        "revisionBasis": {
            "enum": ["PROVIDER_REVISION", "CONTENT_SNAPSHOT", "NONE"]
        },
        "requestSha256": _sha256(),
        "snapshotSha256": _sha256(),
        "temporalPolicyVersion": {"const": "s5-temporal-policy-v2"},
        "temporalQuality": {
            "enum": [
                "PROVIDER_VINTAGE",
                "PROVIDER_AS_OF_NO_VINTAGE",
                "RECONSTRUCTED_FIXED_LAG",
                "COLLECTION_ONLY",
            ]
        },
    }
    receipt = _closed(
        [
            "sourceId",
            "operationId",
            "observationDate",
            "retrievedAt",
            "availabilityBasis",
            "revisionBasis",
            "requestSha256",
            "snapshotSha256",
            "temporalPolicyVersion",
            "temporalQuality",
        ],
        properties,
    )
    receipt["allOf"] = [
        {
            "if": {"properties": {"availabilityBasis": {"const": "PROVIDER_FIELD"}}},
            "then": {
                "required": ["providerAvailableAt"],
                "not": {"required": ["policyEffectiveAt"]},
            },
        },
        {
            "if": {
                "properties": {
                    "availabilityBasis": {
                        "enum": ["PROVIDER_AS_OF_SCHEDULE", "PROJECT_FIXED_LAG"]
                    }
                }
            },
            "then": {
                "required": ["policyEffectiveAt"],
                "not": {"required": ["providerAvailableAt"]},
            },
        },
        {
            "if": {"properties": {"availabilityBasis": {"const": "RETRIEVAL_ONLY"}}},
            "then": {
                "not": {
                    "anyOf": [
                        {"required": ["providerAvailableAt"]},
                        {"required": ["policyEffectiveAt"]},
                    ]
                }
            },
        },
        {
            "if": {"properties": {"revisionBasis": {"const": "PROVIDER_REVISION"}}},
            "then": {"required": ["providerRevision"]},
            "else": {"not": {"required": ["providerRevision"]}},
        },
        *[
            {
                "if": {"properties": {"temporalQuality": {"const": quality}}},
                "then": {"properties": {"availabilityBasis": {"const": basis}}},
            }
            for quality, basis in (
                ("PROVIDER_VINTAGE", "PROVIDER_FIELD"),
                ("PROVIDER_AS_OF_NO_VINTAGE", "PROVIDER_AS_OF_SCHEDULE"),
                ("RECONSTRUCTED_FIXED_LAG", "PROJECT_FIXED_LAG"),
                ("COLLECTION_ONLY", "RETRIEVAL_ONLY"),
            )
        ],
    ]
    return receipt


def _source_schema() -> dict[str, Any]:
    def provider_branch(
        source: str,
        operation: str,
        *,
        row_max: int,
        byte_max: int,
        availability_basis: str,
        temporal_quality: str,
    ) -> dict[str, Any]:
        operation_schema = {"const": operation}
        return {
            "properties": {
                "sourceId": {"const": source},
                "operationId": operation_schema,
                "rowCount": {"type": "integer", "minimum": 1, "maximum": row_max},
                "bytes": {"type": "integer", "minimum": 1, "maximum": byte_max},
                "receipt": {
                    "properties": {
                        "sourceId": {"const": source},
                        "operationId": operation_schema,
                        "availabilityBasis": {"const": availability_basis},
                        "revisionBasis": {"const": "CONTENT_SNAPSHOT"},
                        "temporalQuality": {"const": temporal_quality},
                    }
                },
            }
        }

    chunk = _closed(
        ["sourceId", "operationId", "queryKey", "contentSha256", "rowCount", "bytes", "receipt"],
        {
            "sourceId": {"enum": ["KIS", "KRX", "ECOS"]},
            "operationId": {"type": "string", "minLength": 1, "maxLength": 80},
            "queryKey": {"type": "string", "minLength": 1, "maxLength": 256},
            "contentSha256": _sha256(),
            "rowCount": {"type": "integer", "minimum": 1, "maximum": 10_000_000},
            "bytes": {"type": "integer", "minimum": 1, "maximum": 17_179_869_184},
            "receipt": _temporal_receipt(),
        },
    )
    chunk["oneOf"] = [
        *[
            provider_branch(
                "KRX",
                operation,
                row_max=5_000,
                byte_max=4 * 1024**2,
                availability_basis="PROVIDER_AS_OF_SCHEDULE",
                temporal_quality="PROVIDER_AS_OF_NO_VINTAGE",
            )
            for operation in (
                "stk_bydd_trd",
                "ksq_bydd_trd",
                "kospi_dd_trd",
                "kosdaq_dd_trd",
                "stk_isu_base_info",
                "ksq_isu_base_info",
                "etf_bydd_trd",
            )
        ],
        provider_branch(
            "KIS",
            "FHKST03010100",
            row_max=100,
            byte_max=10 * 1024**2,
            availability_basis="PROJECT_FIXED_LAG",
            temporal_quality="RECONSTRUCTED_FIXED_LAG",
        ),
        provider_branch(
            "ECOS",
            "722Y001/0101000/D",
            row_max=400,
            byte_max=1 * 1024**2,
            availability_basis="PROJECT_FIXED_LAG",
            temporal_quality="RECONSTRUCTED_FIXED_LAG",
        ),
        provider_branch(
            "ECOS",
            "731Y001/0000001/D",
            row_max=400,
            byte_max=1 * 1024**2,
            availability_basis="PROJECT_FIXED_LAG",
            temporal_quality="RECONSTRUCTED_FIXED_LAG",
        ),
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SOURCE_SCHEMA,
        "title": "S5 PIT source bundle v1",
        **_closed(
            [
                "manifestVersion",
                "historicalMode",
                "futureCollectionMode",
                "strictProviderPITClaim",
                "temporalPolicyVersion",
                "createdAt",
                "datasetCutoff",
                "chunks",
            ],
            {
                "manifestVersion": {"const": "s5-pit-source-bundle-v1"},
                "historicalMode": {"const": "HISTORICAL_REPLAY_RECONSTRUCTED"},
                "futureCollectionMode": {"const": "AS_COLLECTED"},
                "strictProviderPITClaim": {"const": False},
                "temporalPolicyVersion": {"const": "s5-temporal-policy-v2"},
                "createdAt": _timestamp(),
                "datasetCutoff": _timestamp(),
                "chunks": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 7_436 + 8,
                    "items": chunk,
                },
            },
        ),
    }


def _feature_schema() -> dict[str, Any]:
    provenance = _closed(
        [
            "producer",
            "sourceWorkspace",
            "datasetCutoff",
            "exchangeMic",
            "calendarName",
            "calendarVersion",
            "universePolicyVersion",
            "featurePolicyVersion",
            "rawSessionStart",
            "rawSessionEnd",
            "rawSessionCount",
            "eligibleSessionStart",
            "eligibleSessionEnd",
            "eligibleSessionCount",
            "temporalPolicyVersion",
            "temporalQuality",
            "sourceBundleSetSha256",
            "sourcePolicySetSha256",
            "universeScheduleSha256",
            "pitInputSha256",
            "optionalFeatureGroups",
        ],
        {
            "producer": {"const": "decision-platform"},
            "sourceWorkspace": {"const": "decision-platform"},
            "datasetCutoff": _timestamp(),
            "exchangeMic": {"const": "XKRX"},
            "calendarName": {"const": "XKRX"},
            "calendarVersion": {"const": "4.13.2"},
            "universePolicyVersion": {"const": "top30-plus-132030-v1"},
            "featurePolicyVersion": {"const": "s5-core-features-v1"},
            "rawSessionStart": {"type": "string", "format": "date"},
            "rawSessionEnd": {"type": "string", "format": "date"},
            "rawSessionCount": {"const": 1072},
            "eligibleSessionStart": {"type": "string", "format": "date"},
            "eligibleSessionEnd": {"type": "string", "format": "date"},
            "eligibleSessionCount": {"const": 1007},
            "temporalPolicyVersion": {"const": "s5-temporal-policy-v2"},
            "temporalQuality": {"const": "RECONSTRUCTED_FIXED_LAG"},
            "sourceBundleSetSha256": _sha256(),
            "sourcePolicySetSha256": _sha256(),
            "universeScheduleSha256": _sha256(),
            "pitInputSha256": _sha256(),
            "optionalFeatureGroups": {"const": []},
        },
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": FEATURE_SCHEMA,
        "title": "S5 production feature bundle v2",
        **_closed(
            [
                "manifestVersion",
                "schemaVersion",
                "parquetFile",
                "parquetSha256",
                "logicalDatasetHash",
                "rowCount",
                "columnCount",
                "featureColumns",
                "provenance",
            ],
            {
                "manifestVersion": {"const": "s5-feature-bundle-v2"},
                "schemaVersion": {"const": "s5-feature-table-v1"},
                "parquetFile": {"const": "features.parquet"},
                "parquetSha256": _sha256(),
                "logicalDatasetHash": _sha256(),
                "rowCount": {"type": "integer", "minimum": 1, "maximum": 250_000},
                "columnCount": {"const": 19},
                "featureColumns": {
                    "const": list(FEATURE_COLUMNS),
                },
                "provenance": provenance,
            },
        ),
    }


def _catalog() -> dict[str, Any]:
    return {
        "contractId": "s5-production-materialization-lock.v1",
        "historicalMode": "HISTORICAL_REPLAY_RECONSTRUCTED",
        "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
        "futureCollectionMode": "AS_COLLECTED",
        "strictProviderPITClaim": False,
        "productionEligibility": "DUAL_SENSITIVITY_PASS_REQUIRED",
        "bootstrap": {
            "order": ["KRX", "KIS", "ECOS"],
            "retry": 0,
            "costMax": 0,
            "krxMaxGet": 4441,
            "kisTokenMax": 1,
            "kisMaxGet": 2970,
            "ecosMaxGet": 24,
            "totalMaxPhysicalCalls": 7436,
            "horizonUnionSize": 270,
            "horizonUnionSizeSource": "MEASURED_FROM_COLLECTED_KRX_LIQUIDITY_EVIDENCE",
            "accountBalanceOrderCalls": 0,
            "freshSupersededAllowance": 0,
            "maxSupersededAllowance": 8,
            "maxSupersededAllowancePerProvider": 8,
            "supersededAllowanceProviders": ["KRX", "KIS", "KIS_TOKEN"],
            "supersededAllowanceLineage": "CALENDAR_RECOVERY",
            "supersededAllowanceDerivation": "PROVEN_SUPERSEDED_CONSUMED_CALLS",
            "kisSupersededAllowanceAbsentFromPacketBytesWhenZero": True,
        },
        "sourceBundle": {
            "manifestMaxBytes": 16 * 1024 * 1024,
            "maxChunks": 7_436 + 8 + 8 + 8,
            "krxMaxRows": 10_000_000,
            "krxMaxBytes": 16 * 1024**3,
            "kisMaxRows": 192_960,
            "kisMaxBytes": 2 * 1024**3,
            "ecosMaxRows": 10_000,
            "ecosMaxBytes": 64 * 1024**2,
        },
        "runtime": {
            "riskDecisionWiring": 0,
            "orderWiring": 0,
            "firstCrossMarketJoin": "S6.6",
            "automaticRetrain": 0,
            "automaticModelActivation": 0,
        },
    }


def _recovery_catalog() -> dict[str, Any]:
    return {
        "contractId": "s5-bootstrap-calendar-recovery-lock.v1",
        "issue": 135,
        "calendar": {
            "authorityOrder": [
                "KIS_CTCA0903R_OPND_YN",
                "NON_EXPIRED_HEALTHY_NON_CONFLICTED_PRIOR_CANONICAL",
                "PINNED_XKRX_BASE",
            ],
            "baseCalendar": "XKRX",
            "baseLibrary": "exchange-calendars",
            "baseVersion": "4.13.2",
            "correctionSetSha256": (
                "20e17fc6bf8b21b2ad8acfc18e8f541535f300471e77f08464cfbfa70c6cdeb9"
            ),
            "corrections": [
                {
                    "evidenceClass": "CTCA0903R_CONFIRMED_CALENDAR_CORRECTION",
                    "isOpen": False,
                    "reason": "2026_LOCAL_ELECTION_MARKET_CLOSURE",
                    "sessionDate": "2026-06-03",
                },
                {
                    "evidenceClass": "CTCA0903R_CONFIRMED_CALENDAR_CORRECTION",
                    "isOpen": False,
                    "reason": "2026_CONSTITUTION_DAY_MARKET_CLOSURE",
                    "sessionDate": "2026-07-17",
                },
            ],
            "policyVersion": "xkrx-4.13.2-kis-corrections-v1",
            "supersededCorrectionSets": [
                {
                    "correctionSetSha256": (
                        "82fbb91db94aaae154323be3340648f37afcead3e42ab8b600c1b22699475ad5"
                    ),
                    "sessionDates": [],
                    "usage": "READ_ONLY_RECOVERY_VALIDATION",
                },
                {
                    "correctionSetSha256": (
                        "30530e6f4ff06ac3ab71a748c910c87cd9233c70382f348e00c387684cdde169"
                    ),
                    "sessionDates": ["2026-06-03"],
                    "usage": "READ_ONLY_RECOVERY_VALIDATION",
                },
            ],
            "supersededGenerationsArePreserved": True,
        },
        "packet": {
            "currentVersion": "s5-production-bootstrap-packet-v2",
            "freshAuthorityFile": "fresh-bootstrap-authority.v1.json",
            "freshAuthorityMode": "IMMUTABLE_ABSENT_TO_SHA_CAS",
            "freshExecutionsPerApprovedRoot": 1,
            "historicalV1Mode": "READ_ONLY_RECOVERY_VALIDATION",
            "recoveryBindingRequired": True,
            "recoveryLineageMode": "CALENDAR_RECOVERY",
            "rootLockFile": ".bootstrap-root.lock",
        },
        "recovery": {
            "adoptionJournalRequired": True,
            "approvedKrxMaxPhysicalGet": 4_441,
            "blockBeforeProviderClientWhenCapacityExhausted": True,
            "cumulativeFailedAttemptsConsumeBudget": True,
            "providerCallsDuringAssessment": 0,
            "receiptMissingResult": "PACKET_OR_ROOT_INVALID",
            "successfulChunkRecall": 0,
            "temporalReceiptRebindingRequired": True,
            "supersededAllowanceDerivation": "PROVEN_SUPERSEDED_CONSUMED_CALLS",
            "supersededAllowanceEqualsSupersededConsumedCalls": True,
            "supersededAllowanceLineage": "CALENDAR_RECOVERY",
            "maxSupersededAllowance": 8,
            "logicalQueryCountUnchangedByAllowance": True,
            "physicalAttemptsPerLogicalQuery": 2,
            "chainableFromSupersededRecoveryPacket": True,
            "receiptCarriesSupersededQuerySet": True,
            "supersededQueryIdentityResolvedAcrossApprovedGenerations": True,
            "priorPacketIsChainHead": True,
            "chainHeadDerivedFromConsumedQueryMultiset": True,
            "supersedeMustChangePacketIdentity": True,
            "priorJournalReadUnderItsOwnGeneration": True,
            "supersededProviders": ["KRX", "KIS"],
            "adoptedProvidersCarryingChunks": ["KRX", "KIS"],
            "kisTokenSuccessAdoptable": False,
            "perQueryRetryBudgetScope": "CURRENT_GENERATION",
            "supersededConsumedCallsCountTowardCumulativeBudget": True,
            "kisLogicalQuerySetDerivedFromCollectedKrxEvidence": True,
            "allowanceBindingSurfaces": [
                "PACKET_BYTES",
                "RECOVERY_BINDING_PREIMAGE",
                "RECOVERY_RECEIPT",
                "ADOPTION_JOURNAL",
            ],
        },
        "coverage": {
            "kisCoverageAuthority": "COLLECTED_KRX_DAILY_TRADING_EVIDENCE",
            "kisCoverageRule": "EXACT_MATCH_WITH_KRX_TRADING_EVIDENCE",
            "fixedEtfCoverageRule": "FULL_RAW_WINDOW",
            "tradedSessionsMustBeContiguous": True,
            "pagingWindowSource": "PACKET_RAW_WINDOW",
            "pagingStopsWhenEvidenceIsSatisfied": True,
            "diagnosticLedger": "diagnostics.jsonl",
            "ledgerNamesDivergingSymbol": True,
            "ledgerCarriesProviderPayload": False,
            "providerCallsDuringBlock": 0,
        },
        "autonomy": {
            "tickEntrypoint": "s5-tick",
            "stateFile": "state.json",
            "stateHistoryFile": "state-history.jsonl",
            "stateVersion": "s5-run-state-v1",
            "stateHistoryAppendOnly": True,
            # tick이 실제로 멈출 수 있는 경계만 단계로 둔다. materialization과 qualification은
            # 각각 한 호출이라 그 안에서는 멈출 수 없다.
            "phases": ["MATERIALIZING", "QUALIFYING", "SERVING", "NEEDS_HUMAN"],
            "forwardOnlyExceptRequalification": True,
            "requalificationReentersFrom": "SERVING",
            "needsHumanIsTerminalWithoutOperator": True,
            "exitCodes": {
                "progress": 0,
                "noProgress": 1,
                "needsHuman": 2,
            },
            "noProgressIsNotFailure": True,
            "tickIsIdempotent": True,
            "tickReliesOnJournalQueryIdempotence": True,
            "automaticRetrain": True,
            "automaticModelActivation": False,
            "activationRemainsManualCas": True,
        },
        "diagnostics": {
            "ledgerFile": "diagnostics.jsonl",
            "eventVersion": "s5-diagnostic-event-v1",
            "appendOnly": True,
            "carriesProviderPayload": False,
            "outcomeClasses": [
                "BUDGET_EXHAUSTED",
                "CONTRACT_VIOLATION",
                "EVIDENCE_GAP",
                "RETRYABLE_TRANSIENT",
            ],
            "unclassifiedFailureOutcome": "CONTRACT_VIOLATION",
            "reportKinds": [
                "CALENDAR_DIVERGENCE_SUSPECTED",
                "COVERAGE_REPORT",
                "QUALIFICATION_REPORT",
            ],
            "reportKindsAreNotOutcomeClasses": True,
            "maxExcludedUnitRatio": 0.01,
            "recordingFailureChangesCollectionOutcome": False,
        },
        "divergence": {
            "blockFile": "calendar-divergence-candidates.json",
            "blockVersion": "s5-calendar-divergence-block-v1",
            "result": "CALENDAR_DIVERGENCE_SUSPECTED",
            "providerCallsDuringBlock": 0,
            "blockIsGateTokenNotLedgerEntry": True,
            "mirroredToDiagnosticLedger": True,
            "blockBytesDependOnPacketAndCandidatesOnly": True,
            "evidenceClasses": [
                {
                    "evidence": "EMPTY_DAILY_PROJECTION",
                    "detection": "EMPTY_DAILY_PROJECTION_ON_CLAIMED_SESSION",
                    "resumePacketAuthored": False,
                    "unresolvedBlockStopsResume": True,
                },
                {
                    "evidence": "SINGLE_SESSION_QUERY_FAILURE",
                    "detection": "SINGLE_SESSION_FAILURE_AFTER_HEALTHY_NEIGHBOURS",
                    "resumePacketAuthored": True,
                    "unresolvedBlockStopsResume": False,
                },
            ],
        },
        "holidayAuthority": {
            "transactionId": "CTCA0903R",
            "sourceId": "kis-holiday-ctca0903r",
            "mode": "LIVE_ONLY",
            "maxPhysicalCalls": 32,
            "scope": "DIVERGENCE_CANDIDATE_SESSIONS_ONLY",
            "separateFromBootstrapBudget": True,
            "requiredRole": "decision_collector",
            "canonicalTable": "trading_sessions",
            "attestationStatuses": [
                "API_CONFIRMED",
                "CALENDAR_AUTHORITY_CONFLICT",
                "CALENDAR_AUTHORITY_UNVERIFIED",
            ],
            "unobservedCorrectionResult": "CALENDAR_AUTHORITY_UNVERIFIED",
            "packetHashRemainsStatic": True,
        },
        "downstream": {
            "openApiChange": 0,
            "orderWiring": 0,
            "providerRawArtifactInGit": 0,
            "riskDecisionWiring": 0,
            "signalContractChange": 0,
        },
    }


def _source_fixture() -> dict[str, Any]:
    return {
        "manifestVersion": "s5-pit-source-bundle-v1",
        "historicalMode": "HISTORICAL_REPLAY_RECONSTRUCTED",
        "futureCollectionMode": "AS_COLLECTED",
        "strictProviderPITClaim": False,
        "temporalPolicyVersion": "s5-temporal-policy-v2",
        "createdAt": "2026-08-16T00:00:00Z",
        "datasetCutoff": "2026-08-17T23:10:00Z",
        "chunks": [
            {
                "sourceId": "KIS",
                "operationId": "FHKST03010100",
                "queryKey": "005930:20260401:20260814:0",
                "contentSha256": "1" * 64,
                "rowCount": 100,
                "bytes": 4096,
                "receipt": {
                    "sourceId": "KIS",
                    "operationId": "FHKST03010100",
                    "observationDate": "2026-08-14",
                    "retrievedAt": "2026-08-16T00:00:00Z",
                    "policyEffectiveAt": "2026-08-17T23:10:00Z",
                    "availabilityBasis": "PROJECT_FIXED_LAG",
                    "revisionBasis": "CONTENT_SNAPSHOT",
                    "requestSha256": "2" * 64,
                    "snapshotSha256": "1" * 64,
                    "temporalPolicyVersion": "s5-temporal-policy-v2",
                    "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
                },
            }
        ],
    }


def _feature_fixture() -> dict[str, Any]:
    from contracts.generate_s5_signal_runtime_contracts import _policy_catalog  # noqa: PLC0415

    del _policy_catalog  # import asserts the historical generator remains importable
    return {
        "manifestVersion": "s5-feature-bundle-v2",
        "schemaVersion": "s5-feature-table-v1",
        "parquetFile": "features.parquet",
        "parquetSha256": "3" * 64,
        "logicalDatasetHash": "4" * 64,
        "rowCount": 1007,
        "columnCount": 19,
        "featureColumns": list(FEATURE_COLUMNS),
        "provenance": {
            "producer": "decision-platform",
            "sourceWorkspace": "decision-platform",
            "datasetCutoff": "2026-08-17T23:10:00Z",
            "exchangeMic": "XKRX",
            "calendarName": "XKRX",
            "calendarVersion": "4.13.2",
            "universePolicyVersion": "top30-plus-132030-v1",
            "featurePolicyVersion": "s5-core-features-v1",
            "rawSessionStart": "2022-04-01",
            "rawSessionEnd": "2026-08-14",
            "rawSessionCount": 1072,
            "eligibleSessionStart": "2022-06-28",
            "eligibleSessionEnd": "2026-08-06",
            "eligibleSessionCount": 1007,
            "temporalPolicyVersion": "s5-temporal-policy-v2",
            "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
            "sourceBundleSetSha256": "5" * 64,
            "sourcePolicySetSha256": "6" * 64,
            "universeScheduleSha256": "7" * 64,
            "pitInputSha256": "8" * 64,
            "optionalFeatureGroups": [],
        },
    }


def _corrected_calendar_feature_fixture() -> dict[str, Any]:
    """Historical v2 bytes와 분리해 현재 correction-set session 산술을 고정한다."""

    feature = copy.deepcopy(_feature_fixture())
    feature.update(
        {
            "parquetSha256": "9" * 64,
            "logicalDatasetHash": "a" * 64,
        }
    )
    feature["provenance"].update(
        {
            "datasetCutoff": "2026-08-13T23:10:00Z",
            "rawSessionStart": "2022-03-29",
            "rawSessionEnd": "2026-08-13",
            "eligibleSessionStart": "2022-06-23",
            "eligibleSessionEnd": "2026-08-05",
            "pitInputSha256": "b" * 64,
        }
    )
    return feature


def build_artifacts() -> dict[str, dict[str, Any]]:
    source = _source_fixture()
    feature = _feature_fixture()
    source_unknown = copy.deepcopy(source)
    source_unknown["rawResponse"] = {}
    source_operation = copy.deepcopy(source)
    source_operation["chunks"][0]["operationId"] = "account-balance"
    source_operation["chunks"][0]["receipt"]["operationId"] = "account-balance"
    source_receipt_mismatch = copy.deepcopy(source)
    source_receipt_mismatch["chunks"][0]["receipt"]["sourceId"] = "ECOS"
    source_receipt_policy = copy.deepcopy(source)
    source_receipt_policy["chunks"][0]["receipt"].update(
        {
            "availabilityBasis": "PROVIDER_AS_OF_SCHEDULE",
            "temporalQuality": "PROVIDER_AS_OF_NO_VINTAGE",
        }
    )
    source_availability_clocks = copy.deepcopy(source)
    source_availability_clocks["chunks"][0]["receipt"]["providerAvailableAt"] = (
        "2026-08-16T00:00:00Z"
    )
    feature_unknown = copy.deepcopy(feature)
    feature_unknown["featureColumns"][0] = "cross_market_score"
    artifacts = {
        CATALOG: _catalog(),
        RECOVERY_CATALOG: _recovery_catalog(),
        SOURCE_SCHEMA: _source_schema(),
        FEATURE_SCHEMA: _feature_schema(),
        "contracts/examples/s5-pit-source-bundle-v1.valid.json": source,
        "contracts/examples/s5-feature-bundle-v2.valid.json": feature,
        "contracts/examples/s5-feature-bundle-v2.corrected-calendar.valid.json": (
            _corrected_calendar_feature_fixture()
        ),
        "contracts/examples/invalid/s5-pit-source-bundle-v1.unknown-field.invalid.json": source_unknown,
        "contracts/examples/invalid/s5-pit-source-bundle-v1.operation.invalid.json": source_operation,
        "contracts/examples/invalid/s5-pit-source-bundle-v1.receipt-source.invalid.json": source_receipt_mismatch,
        "contracts/examples/invalid/s5-pit-source-bundle-v1.receipt-policy.invalid.json": source_receipt_policy,
        "contracts/examples/invalid/s5-pit-source-bundle-v1.availability-clocks.invalid.json": source_availability_clocks,
        "contracts/examples/invalid/s5-feature-bundle-v2.cross-market.invalid.json": feature_unknown,
    }
    Draft202012Validator.check_schema(artifacts[SOURCE_SCHEMA])
    Draft202012Validator.check_schema(artifacts[FEATURE_SCHEMA])
    return artifacts


ARTIFACT_PATHS: Final[frozenset[str]] = frozenset(build_artifacts())


def generate_outputs() -> dict[str, bytes]:
    return {path: canonical_json_bytes(value) for path, value in build_artifacts().items()}


def _write(outputs: Mapping[str, bytes]) -> None:
    for relative, payload in sorted(outputs.items()):
        write_generated_artifact(ROOT, relative, payload)


def _check(outputs: Mapping[str, bytes]) -> None:
    drifted = [
        relative
        for relative, expected in sorted(outputs.items())
        if not (ROOT / relative).is_file()
        or (ROOT / relative).is_symlink()
        or (ROOT / relative).read_bytes() != expected
    ]
    if drifted:
        raise ContractValidationError(
            "generated S5.6 production artifacts drifted:\n"
            + "\n".join(f"- {path}" for path in drifted)
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        outputs = generate_outputs()
        _write(outputs) if args.write else _check(outputs)
    except (ContractValidationError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print("S5_6_PRODUCTION_CONTRACTS_VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
