from __future__ import annotations

import copy
from typing import Any

import pytest

from app.rag.contract_catalog import (
    RagContractCatalogError,
    load_rag_contract_catalog,
    parse_rag_contract_catalog,
)


def test_python_rag_catalog_reads_exact_two_profiles_and_three_policies() -> None:
    catalog = load_rag_contract_catalog()

    assert catalog.profile_ids == (
        "bge_m3_local_1024_v1",
        "voyage_context_4_1024_v1",
    )
    assert catalog.policy_ids == (
        "bge_only_v1",
        "voyage_only_v1",
        "bge_then_voyage_on_sla_v1",
    )
    assert catalog.dimension == 1024
    assert not catalog.profiles["bge_m3_local_1024_v1"].external_provider
    assert catalog.profiles["voyage_context_4_1024_v1"].external_provider
    assert (
        catalog.profiles["bge_m3_local_1024_v1"].embedding_input_strategy
        == "BGE_TRANSIENT_ADJACENT_CONTEXT_MAX_15"
    )
    assert (
        catalog.profiles["voyage_context_4_1024_v1"].embedding_input_strategy
        == "VOYAGE_CONTEXTUAL_DOCUMENT_BATCH_OVERLAP_0"
    )
    assert catalog.policies["bge_then_voyage_on_sla_v1"].per_request_fallback is False
    assert "embeddingProfileId" in catalog.ask_forbidden_body_fields
    assert "topK" in catalog.ask_forbidden_body_fields


def test_python_rag_catalog_rejects_context3_and_profile_policy_confusion() -> None:
    baseline = load_rag_contract_catalog()
    data: dict[str, Any] = {
        "contractId": "s4-rag-contract/v1",
        "schemaVersion": 1,
        "dimension": baseline.dimension,
        "profileIds": list(baseline.profile_ids),
        "policyIds": list(baseline.policy_ids),
        "forbiddenProfileIds": ["voyage_context_3_1024_v1"],
        "askRequest": {
            "forbiddenBodyFields": sorted(baseline.ask_forbidden_body_fields),
        },
        "profiles": [
            {
                "profileId": profile.profile_id,
                "provider": profile.provider,
                "model": profile.model,
                "dimension": profile.dimension,
                "embeddingInputStrategy": profile.embedding_input_strategy,
                "externalProvider": profile.external_provider,
            }
            for profile in baseline.profiles.values()
        ],
        "policies": [
            {
                "policyId": policy.policy_id,
                "defaultProfileId": policy.default_profile_id,
                "queryProfileId": policy.query_profile_id,
                "documentProfileId": policy.document_profile_id,
                "perRequestFallback": policy.per_request_fallback,
                "providerOutageFallback": policy.provider_outage_fallback,
            }
            for policy in baseline.policies.values()
        ],
    }

    with_context3 = copy.deepcopy(data)
    with_context3["profiles"][0]["profileId"] = "voyage_context_3_1024_v1"
    with pytest.raises(RagContractCatalogError):
        parse_rag_contract_catalog(with_context3)

    profile_as_policy = copy.deepcopy(data)
    profile_as_policy["policies"][0]["policyId"] = "bge_m3_local_1024_v1"
    with pytest.raises(RagContractCatalogError):
        parse_rag_contract_catalog(profile_as_policy)

    runtime_fallback = copy.deepcopy(data)
    runtime_fallback["policies"][2]["perRequestFallback"] = True
    with pytest.raises(RagContractCatalogError):
        parse_rag_contract_catalog(runtime_fallback)

    legacy_strategy_field = copy.deepcopy(data)
    strategy = legacy_strategy_field["profiles"][0].pop("embeddingInputStrategy")
    legacy_strategy_field["profiles"][0]["chunkInputStrategy"] = strategy
    with pytest.raises(RagContractCatalogError):
        parse_rag_contract_catalog(legacy_strategy_field)
