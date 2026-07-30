from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[5]
CATALOG_PATH = REPO_ROOT / "contracts/catalogs/s4-rag-contract.v1.json"
CATALOG_SHA256 = "9b9881f9b25b6486f20999f27c0dd7043048fc26491e33cf2af892817dabbe0a"
PROFILE_IDS = ("bge_m3_local_1024_v1", "voyage_context_4_1024_v1")
POLICY_IDS = ("bge_only_v1", "voyage_only_v1", "bge_then_voyage_on_sla_v1")
FORBIDDEN_PROFILE_IDS = frozenset({"voyage_context_3_1024_v1"})


class RagContractCatalogError(ValueError):
    """RAG static catalog가 S4/P1 profile·policy 계약을 위반할 때 발생한다."""


@dataclass(frozen=True)
class RagEmbeddingProfile:
    """문서/query vector space 계약.

    외부 provider credential이나 endpoint client는 여기에 들어오지 않고, fixed allowlist
    identity만 담아 runtime adapter가 임의 문자열을 받지 않게 한다.
    """

    profile_id: str
    provider: str
    model: str
    dimension: int
    embedding_input_strategy: str
    external_provider: bool


@dataclass(frozen=True)
class RagEmbeddingPolicy:
    """profile 선택 정책 계약.

    `bge_then_voyage_on_sla_v1`은 per-request fallback이 아니라 관리자 승인 후 default pointer
    전환 정책이므로 provider 장애를 이유로 조용히 다른 profile을 쓰면 안 된다.
    """

    policy_id: str
    default_profile_id: str
    query_profile_id: str
    document_profile_id: str
    per_request_fallback: bool
    provider_outage_fallback: bool


@dataclass(frozen=True)
class RagContractCatalog:
    """Spring·Python이 공유하는 S4 RAG static contract view."""

    profile_ids: tuple[str, ...]
    policy_ids: tuple[str, ...]
    dimension: int
    ask_forbidden_body_fields: frozenset[str]
    profiles: dict[str, RagEmbeddingProfile]
    policies: dict[str, RagEmbeddingPolicy]


def load_rag_contract_catalog(path: Path = CATALOG_PATH) -> RagContractCatalog:
    """canonical catalog bytes를 읽어 exact digest와 P1 allowlist를 검증한다."""

    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != CATALOG_SHA256:
        raise RagContractCatalogError(
            f"S4 RAG catalog digest mismatch: expected {CATALOG_SHA256}, got {digest}"
        )
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RagContractCatalogError("S4 RAG catalog must be strict UTF-8 JSON.") from error
    if not isinstance(data, dict):
        raise RagContractCatalogError("S4 RAG catalog must be an object.")
    return parse_rag_contract_catalog(data)


def parse_rag_contract_catalog(data: dict[str, Any]) -> RagContractCatalog:
    """테스트 fixture와 runtime resource가 같은 profile/policy 경계를 따르는지 검증한다."""

    if data.get("contractId") != "s4-rag-contract/v1":
        raise RagContractCatalogError("S4 RAG contractId drifted.")
    if data.get("schemaVersion") != 1 or data.get("dimension") != 1024:
        raise RagContractCatalogError("S4 RAG catalog must remain v1 with 1024 dimensions.")
    profile_ids = tuple(data.get("profileIds", ()))
    policy_ids = tuple(data.get("policyIds", ()))
    if profile_ids != PROFILE_IDS:
        raise RagContractCatalogError("S4 RAG profileIds must be the exact approved pair.")
    if policy_ids != POLICY_IDS:
        raise RagContractCatalogError("S4 RAG policyIds must be the exact approved trio.")
    if set(data.get("forbiddenProfileIds", ())) != FORBIDDEN_PROFILE_IDS:
        raise RagContractCatalogError("S4 RAG forbidden profile set drifted.")

    profiles = _parse_profiles(data.get("profiles"))
    policies = _parse_policies(data.get("policies"))
    forbidden_fields = frozenset(data.get("askRequest", {}).get("forbiddenBodyFields", ()))
    required_forbidden = {
        "embeddingProfileId",
        "embeddingPolicyId",
        "profileId",
        "policyId",
        "topK",
        "sourceTier",
        "provider",
        "model",
    }
    if not required_forbidden.issubset(forbidden_fields):
        raise RagContractCatalogError("S4 RAG public ask contract exposes server-owned controls.")
    return RagContractCatalog(
        profile_ids=profile_ids,
        policy_ids=policy_ids,
        dimension=1024,
        ask_forbidden_body_fields=forbidden_fields,
        profiles=profiles,
        policies=policies,
    )


def _parse_profiles(value: Any) -> dict[str, RagEmbeddingProfile]:
    if not isinstance(value, list) or len(value) != 2:
        raise RagContractCatalogError("S4 RAG profiles must contain exactly two entries.")
    profiles: dict[str, RagEmbeddingProfile] = {}
    for item in value:
        if not isinstance(item, dict):
            raise RagContractCatalogError("S4 RAG profile item must be an object.")
        profile_id = item.get("profileId")
        if not isinstance(profile_id, str):
            raise RagContractCatalogError("S4 RAG profileId must be a string.")
        if profile_id in FORBIDDEN_PROFILE_IDS:
            raise RagContractCatalogError("voyage_context_3_1024_v1 is not an active P1 profile.")
        if profile_id not in PROFILE_IDS:
            raise RagContractCatalogError("S4 RAG profile item has an unknown profileId.")
        dimension = item.get("dimension")
        if not isinstance(dimension, int) or isinstance(dimension, bool):
            raise RagContractCatalogError("S4 RAG profile dimension must be an integer.")
        external_provider = item.get("externalProvider")
        if not isinstance(external_provider, bool):
            raise RagContractCatalogError("S4 RAG profile externalProvider must be boolean.")
        profile = RagEmbeddingProfile(
            profile_id=profile_id,
            provider=_required_text(item, "provider"),
            model=_required_text(item, "model"),
            dimension=dimension,
            embedding_input_strategy=_required_text(item, "embeddingInputStrategy"),
            external_provider=external_provider,
        )
        if profile.dimension != 1024:
            raise RagContractCatalogError(f"{profile.profile_id} must be 1024-dimensional.")
        profiles[profile.profile_id] = profile
    if tuple(profiles) != PROFILE_IDS:
        raise RagContractCatalogError("S4 RAG active profiles must preserve catalog order.")
    if profiles["bge_m3_local_1024_v1"].external_provider:
        raise RagContractCatalogError("BGE profile must remain local-only.")
    if not profiles["voyage_context_4_1024_v1"].external_provider:
        raise RagContractCatalogError("Voyage context-4 profile must remain provider-backed.")
    if (
        profiles["bge_m3_local_1024_v1"].embedding_input_strategy
        != "BGE_TRANSIENT_ADJACENT_CONTEXT_MAX_15"
    ):
        raise RagContractCatalogError("BGE embedding input strategy drifted.")
    if (
        profiles["voyage_context_4_1024_v1"].embedding_input_strategy
        != "VOYAGE_CONTEXTUAL_DOCUMENT_BATCH_OVERLAP_0"
    ):
        raise RagContractCatalogError("Voyage embedding input strategy drifted.")
    return profiles


def _parse_policies(value: Any) -> dict[str, RagEmbeddingPolicy]:
    if not isinstance(value, list) or len(value) != 3:
        raise RagContractCatalogError("S4 RAG policies must contain exactly three entries.")
    policies: dict[str, RagEmbeddingPolicy] = {}
    for item in value:
        if not isinstance(item, dict):
            raise RagContractCatalogError("S4 RAG policy item must be an object.")
        policy_id = item.get("policyId")
        if policy_id in PROFILE_IDS:
            raise RagContractCatalogError("Profile ID cannot be used in the policy namespace.")
        if policy_id not in POLICY_IDS:
            raise RagContractCatalogError("S4 RAG policy item has an unknown policyId.")
        policy = RagEmbeddingPolicy(
            policy_id=policy_id,
            default_profile_id=str(item.get("defaultProfileId")),
            query_profile_id=str(item.get("queryProfileId")),
            document_profile_id=str(item.get("documentProfileId")),
            per_request_fallback=bool(item.get("perRequestFallback")),
            provider_outage_fallback=bool(item.get("providerOutageFallback")),
        )
        if policy.per_request_fallback or policy.provider_outage_fallback:
            raise RagContractCatalogError(f"{policy.policy_id} cannot fallback at request time.")
        if policy.query_profile_id != policy.document_profile_id:
            raise RagContractCatalogError(f"{policy.policy_id} cannot mix vector spaces.")
        if policy.default_profile_id != policy.query_profile_id:
            raise RagContractCatalogError(f"{policy.policy_id} default profile drifted.")
        if policy.default_profile_id not in PROFILE_IDS:
            raise RagContractCatalogError(f"{policy.policy_id} points to an unknown profile.")
        policies[policy.policy_id] = policy
    if tuple(policies) != POLICY_IDS:
        raise RagContractCatalogError("S4 RAG active policies must preserve catalog order.")
    return policies


def _required_text(item: dict[str, Any], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value:
        raise RagContractCatalogError(f"S4 RAG profile {field} must be a non-empty string.")
    return value
