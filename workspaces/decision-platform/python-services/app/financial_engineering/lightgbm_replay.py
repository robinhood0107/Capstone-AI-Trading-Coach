from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.data._shared.canonical_json import canonical_json_bytes


@dataclass(frozen=True)
class ResearchCandidate:
    artifact_hash: str
    qualification_status: str
    immutable: bool
    side: str
    evidence_label: str


def build_lightgbm_policy_replay(
    candidate: ResearchCandidate | None,
    *,
    pit_dataset_available: bool,
    empirical_performance_claim_allowed: bool = True,
) -> dict[str, object]:
    eligible = (
        candidate is not None
        and candidate.qualification_status == "AVAILABLE"
        and candidate.immutable
        and candidate.side == "BUY"
        and candidate.evidence_label in {"REAL_PIT", "SYNTHETIC_FIXTURE"}
        and _is_hash(candidate.artifact_hash)
    )
    dataset_available = bool(
        eligible
        and candidate is not None
        and (candidate.evidence_label == "SYNTHETIC_FIXTURE" or pit_dataset_available)
    )
    eligible_candidate = candidate if eligible else None
    real = (
        eligible_candidate is not None
        and pit_dataset_available
        and eligible_candidate.evidence_label == "REAL_PIT"
    )
    payload: dict[str, object] = {
        "contractId": "lightgbm_policy_replay.v1",
        "decisionAuthority": "NONE",
        "runtimeRiskEngineSource": False,
        "productionSignalAuthority": False,
        "researchOnly": True,
        "datasetStatus": "AVAILABLE" if dataset_available else "DATASET_UNAVAILABLE",
        "candidateArtifactHash": eligible_candidate.artifact_hash
        if eligible_candidate is not None
        else None,
        "candidateQualificationStatus": "AVAILABLE"
        if eligible
        else (
            "FAILED"
            if candidate is not None and candidate.qualification_status == "FAILED"
            else "NOT_AVAILABLE"
        ),
        "eligibleSide": "BUY",
        "evidenceLabel": eligible_candidate.evidence_label
        if eligible_candidate is not None
        else "NONE",
        "performanceClaimAllowed": bool(real and empirical_performance_claim_allowed),
    }
    payload["artifactHash"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return payload


def data_requirements_packet() -> dict[str, object]:
    return {
        "contractId": "s6-6-data-requirements.v1",
        "providerCallsAuthorized": False,
        "sources": ["S5_7_STORED_XKRX", "S4_8_STORED_CROSS_MARKET", "QUALIFIED_RESEARCH_CANDIDATE"],
        "entitlementRequired": True,
        "retention": "PROJECT_POLICY_BOUND",
        "pitAvailableAtRequired": True,
        "minimumCoverageYears": 3,
        "targetCoverageYears": 5,
        "expectedBytes": None,
        "expectedCalls": 0,
        "expectedCostKrw": 0,
        "checksumPlan": "SHA256_CANONICAL_MANIFEST_AND_EACH_ARTIFACT",
    }


def _is_hash(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
