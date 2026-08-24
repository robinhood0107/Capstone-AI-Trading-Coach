from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[5]
CATALOG_PATH = REPO_ROOT / "contracts/catalogs/s4-rag-contract.v1.json"
CATALOG_SHA256_MANIFEST_PATH = REPO_ROOT / "contracts/catalogs/s4-rag-contract.v1.sha256.json"
PROFILE_IDS = ("bge_m3_local_1024_v1", "voyage_context_4_1024_v1")
POLICY_IDS = ("bge_only_v1", "voyage_only_v1", "bge_then_voyage_on_sla_v1")
FORBIDDEN_PROFILE_IDS = frozenset({"voyage_context_3_1024_v1"})
GENERATION_STATUSES = (
    "REGISTERED",
    "PLANNED",
    "MATERIALIZING",
    "MATERIALIZED",
    "EVAL_PASSED",
    "ACTIVE",
    "FAILED_FINAL",
    "DISABLED",
)
TOPIC_ALLOWLIST = (
    "API",
    "DATA",
    "FINANCIAL_ENGINEERING",
    "METHODOLOGY",
    "PRODUCT_RISK",
    "RISK",
)


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

    catalog_sha256: str
    profile_ids: tuple[str, ...]
    policy_ids: tuple[str, ...]
    generation_statuses: tuple[str, ...]
    topic_allowlist: tuple[str, ...]
    dimension: int
    ask_forbidden_body_fields: frozenset[str]
    profiles: dict[str, RagEmbeddingProfile]
    policies: dict[str, RagEmbeddingPolicy]


def load_rag_contract_catalog(path: Path = CATALOG_PATH) -> RagContractCatalog:
    """canonical catalog와 generator-owned digest manifest를 함께 검증한다."""

    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    manifest = _load_digest_manifest()
    if digest != manifest["sha256"]:
        raise RagContractCatalogError("S4 RAG catalog digest does not match the approved manifest.")
    try:
        data = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RagContractCatalogError("S4 RAG catalog must be strict UTF-8 JSON.") from error
    if not isinstance(data, dict):
        raise RagContractCatalogError("S4 RAG catalog must be an object.")
    return parse_rag_contract_catalog(data, catalog_sha256=digest)


def parse_rag_contract_catalog(
    data: dict[str, Any],
    *,
    catalog_sha256: str = "UNVERIFIED_TEST_FIXTURE",
) -> RagContractCatalog:
    """테스트 fixture와 runtime resource가 같은 profile/policy 경계를 따르는지 검증한다."""

    expected_root_keys = {
        "answerModes",
        "askRequest",
        "canonicalChunking",
        "contractId",
        "dimension",
        "embeddingInputStrategies",
        "embeddingOperations",
        "forbiddenProfileIds",
        "generationStatuses",
        "policies",
        "policyIds",
        "profiles",
        "profileIds",
        "schemaVersion",
        "sourceMetadata",
        "topicAllowlist",
    }
    if set(data) != expected_root_keys:
        raise RagContractCatalogError("S4 RAG catalog root shape drifted.")
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
    if data.get("answerModes") != ["CONCISE", "DETAILED"]:
        raise RagContractCatalogError("S4 RAG answer mode set drifted.")
    generation_statuses = tuple(data.get("generationStatuses", ()))
    if generation_statuses != GENERATION_STATUSES:
        raise RagContractCatalogError("S4 RAG generation lifecycle drifted.")
    topic_allowlist = tuple(data.get("topicAllowlist", ()))
    if topic_allowlist != TOPIC_ALLOWLIST:
        raise RagContractCatalogError("S4 RAG topic allowlist drifted.")
    if data.get("embeddingOperations") != ["DOCUMENT_EMBED", "QUERY_EMBED"]:
        raise RagContractCatalogError("S4 RAG embedding operation allowlist drifted.")
    if data.get("embeddingInputStrategies") != [
        "BGE_TRANSIENT_ADJACENT_CONTEXT_MAX_15",
        "VOYAGE_CONTEXTUAL_DOCUMENT_BATCH_OVERLAP_0",
    ]:
        raise RagContractCatalogError("S4 RAG embedding input strategies drifted.")
    if data.get("canonicalChunking") != {
        "boundaryStrategy": "MARKDOWN_HEADING_PARAGRAPH",
        "maximumTargetTokens": 600,
        "minimumTargetTokens": 400,
        "overlapPercent": 0,
        "oversizedTablePolicy": "REJECT",
        "tableSplitAllowed": False,
    }:
        raise RagContractCatalogError("S4 RAG canonical chunking contract drifted.")
    if data.get("sourceMetadata") != {
        "maximumItems": 30,
        "publicSourceType": "PROJECT_SOURCE_CARD",
        "queryParametersAllowed": False,
        "rawChunkIncluded": False,
        "rawUpstreamBodyIncluded": False,
    }:
        raise RagContractCatalogError("S4 RAG public source metadata contract drifted.")

    profiles = _parse_profiles(data.get("profiles"))
    policies = _parse_policies(data.get("policies"))
    ask_request = data.get("askRequest")
    if not isinstance(ask_request, dict):
        raise RagContractCatalogError("S4 RAG askRequest must be an object.")
    if {
        "idempotencyHeader": ask_request.get("idempotencyHeader"),
        "idempotencyKeyPattern": ask_request.get("idempotencyKeyPattern"),
        "maximumQuestionUnicodeScalars": ask_request.get("maximumQuestionUnicodeScalars"),
        "maximumQuestionUtf8Bytes": ask_request.get("maximumQuestionUtf8Bytes"),
        "minimumQuestionUnicodeScalars": ask_request.get("minimumQuestionUnicodeScalars"),
        "normalization": ask_request.get("normalization"),
        "relatedSymbolsMaximumItems": ask_request.get("relatedSymbolsMaximumItems"),
        "relatedSymbolsPattern": ask_request.get("relatedSymbolsPattern"),
        "route": ask_request.get("route"),
        "topicsMaximumItems": ask_request.get("topicsMaximumItems"),
    } != {
        "idempotencyHeader": "X-Idempotency-Key",
        "idempotencyKeyPattern": "^[A-Za-z0-9._~-]{16,128}$",
        "maximumQuestionUnicodeScalars": 1000,
        "maximumQuestionUtf8Bytes": 8192,
        "minimumQuestionUnicodeScalars": 1,
        "normalization": "NFC",
        "relatedSymbolsMaximumItems": 5,
        "relatedSymbolsPattern": "^[0-9]{6}$",
        "route": "POST /api/v1/rag/ask",
        "topicsMaximumItems": 5,
    }:
        raise RagContractCatalogError("S4 RAG public ask bounds drifted.")
    forbidden_fields = frozenset(ask_request.get("forbiddenBodyFields", ()))
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
        catalog_sha256=catalog_sha256,
        profile_ids=profile_ids,
        policy_ids=policy_ids,
        generation_statuses=generation_statuses,
        topic_allowlist=topic_allowlist,
        dimension=1024,
        ask_forbidden_body_fields=forbidden_fields,
        profiles=profiles,
        policies=policies,
    )


def _load_digest_manifest() -> dict[str, str | int]:
    try:
        raw = CATALOG_SHA256_MANIFEST_PATH.read_bytes()
        manifest = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RagContractCatalogError(
            "S4 RAG catalog digest manifest must be strict UTF-8 JSON."
        ) from error
    expected_keys = {
        "catalogPath",
        "contractChangePath",
        "schemaVersion",
        "sha256",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_keys:
        raise RagContractCatalogError("S4 RAG catalog digest manifest shape drifted.")
    if (
        manifest["catalogPath"] != "contracts/catalogs/s4-rag-contract.v1.json"
        or manifest["contractChangePath"] != "contracts/changes/20260729-s4-rag-contract-catalog.md"
        or manifest["schemaVersion"] != 1
        or not isinstance(manifest["sha256"], str)
        or len(manifest["sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in manifest["sha256"])
    ):
        raise RagContractCatalogError("S4 RAG catalog digest manifest values drifted.")
    return manifest


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RagContractCatalogError("S4 RAG JSON contains a duplicate key.")
        result[key] = value
    return result


def _parse_profiles(value: Any) -> dict[str, RagEmbeddingProfile]:
    if not isinstance(value, list) or len(value) != 2:
        raise RagContractCatalogError("S4 RAG profiles must contain exactly two entries.")
    profiles: dict[str, RagEmbeddingProfile] = {}
    for item in value:
        if not isinstance(item, dict):
            raise RagContractCatalogError("S4 RAG profile item must be an object.")
        if set(item) != {
            "artifactFormat",
            "canonicalChunkOverlapPercent",
            "dimension",
            "embeddingInputStrategy",
            "externalProvider",
            "freeTokenEligible",
            "model",
            "operationAllowlist",
            "profileId",
            "provider",
            "providerEndpoint",
            "providerOrigin",
            "transientAdjacentContextMaxPercent",
            "trustRemoteCode",
            "vectorSpace",
        }:
            raise RagContractCatalogError("S4 RAG profile shape drifted.")
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
        if item.get("vectorSpace") != profile.profile_id:
            raise RagContractCatalogError("S4 RAG profile vector space drifted.")
        if item.get("operationAllowlist") != ["DOCUMENT_EMBED", "QUERY_EMBED"]:
            raise RagContractCatalogError("S4 RAG profile operation allowlist drifted.")
        if _required_bool(item, "trustRemoteCode"):
            raise RagContractCatalogError("S4 RAG profile cannot trust remote code.")
        if item.get("canonicalChunkOverlapPercent") != 0:
            raise RagContractCatalogError("S4 RAG canonical chunk overlap must be zero.")
        profiles[profile.profile_id] = profile
    if tuple(profiles) != PROFILE_IDS:
        raise RagContractCatalogError("S4 RAG active profiles must preserve catalog order.")
    if profiles["bge_m3_local_1024_v1"].external_provider:
        raise RagContractCatalogError("BGE profile must remain local-only.")
    bge = next(item for item in value if item["profileId"] == "bge_m3_local_1024_v1")
    if (
        bge.get("provider") != "LOCAL"
        or bge.get("model") != "BAAI/bge-m3"
        or bge.get("artifactFormat") != "ONNX_DATA_ONLY"
        or bge.get("freeTokenEligible") is not False
        or bge.get("providerOrigin") is not None
        or bge.get("providerEndpoint") is not None
        or bge.get("transientAdjacentContextMaxPercent") != 15
    ):
        raise RagContractCatalogError("BGE local data-only artifact contract drifted.")
    if not profiles["voyage_context_4_1024_v1"].external_provider:
        raise RagContractCatalogError("Voyage context-4 profile must remain provider-backed.")
    voyage = next(item for item in value if item["profileId"] == "voyage_context_4_1024_v1")
    if (
        voyage.get("provider") != "VOYAGE"
        or voyage.get("model") != "voyage-context-4"
        or voyage.get("artifactFormat") != "PROVIDER_API_RESPONSE_DATA_ONLY"
        or voyage.get("freeTokenEligible") is not True
        or voyage.get("providerOrigin") != "https://api.voyageai.com"
        or voyage.get("providerEndpoint") != "POST /v1/contextualizedembeddings"
        or voyage.get("transientAdjacentContextMaxPercent") != 0
    ):
        raise RagContractCatalogError("Voyage context-4 provider contract drifted.")
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
        if set(item) != {
            "defaultProfileId",
            "documentProfileId",
            "outboundProviderCalls",
            "perRequestFallback",
            "policyId",
            "providerOutageFallback",
            "queryProfileId",
            "transition",
        }:
            raise RagContractCatalogError("S4 RAG policy shape drifted.")
        policy_id = item.get("policyId")
        if policy_id in PROFILE_IDS:
            raise RagContractCatalogError("Profile ID cannot be used in the policy namespace.")
        if policy_id not in POLICY_IDS:
            raise RagContractCatalogError("S4 RAG policy item has an unknown policyId.")
        policy = RagEmbeddingPolicy(
            policy_id=policy_id,
            default_profile_id=_required_text(item, "defaultProfileId"),
            query_profile_id=_required_text(item, "queryProfileId"),
            document_profile_id=_required_text(item, "documentProfileId"),
            per_request_fallback=_required_bool(item, "perRequestFallback"),
            provider_outage_fallback=_required_bool(item, "providerOutageFallback"),
        )
        _required_bool(item, "outboundProviderCalls")
        transition = item.get("transition")
        if not isinstance(transition, dict) or set(transition) != {
            "adminApprovalRequired",
            "allowed",
            "targetProfileId",
            "trigger",
        }:
            raise RagContractCatalogError("S4 RAG policy transition shape drifted.")
        _required_bool(transition, "adminApprovalRequired")
        _required_bool(transition, "allowed")
        expected_outbound = policy.default_profile_id == "voyage_context_4_1024_v1"
        if item.get("outboundProviderCalls") is not expected_outbound:
            raise RagContractCatalogError(
                f"{policy.policy_id} outbound provider declaration drifted."
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
    expected_transitions = (
        {
            "adminApprovalRequired": False,
            "allowed": False,
            "targetProfileId": None,
            "trigger": None,
        },
        {
            "adminApprovalRequired": False,
            "allowed": False,
            "targetProfileId": None,
            "trigger": None,
        },
        {
            "adminApprovalRequired": True,
            "allowed": True,
            "targetProfileId": "voyage_context_4_1024_v1",
            "trigger": "BGE_WARM_P95_SLA_FAILED_AND_VOYAGE_EVAL_PASSED",
        },
    )
    if tuple(item["transition"] for item in value) != expected_transitions:
        raise RagContractCatalogError("S4 RAG approved policy transitions drifted.")
    return policies


def _required_text(item: dict[str, Any], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value:
        raise RagContractCatalogError(f"S4 RAG profile {field} must be a non-empty string.")
    return value


def _required_bool(item: dict[str, Any], field: str) -> bool:
    value = item.get(field)
    if type(value) is not bool:
        raise RagContractCatalogError(f"S4 RAG {field} must be boolean.")
    return value
