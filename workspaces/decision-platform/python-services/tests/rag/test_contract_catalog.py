from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.rag import contract_catalog
from app.rag.contract_catalog import (
    CATALOG_PATH,
    RagContractCatalogError,
    load_rag_contract_catalog,
    parse_rag_contract_catalog,
)


def test_python_rag_catalog_reads_exact_two_profiles_and_three_policies() -> None:
    catalog = load_rag_contract_catalog()

    assert (
        catalog.catalog_sha256 == "9b9881f9b25b6486f20999f27c0dd7043048fc26491e33cf2af892817dabbe0a"
    )
    assert catalog.profile_ids == (
        "bge_m3_local_1024_v1",
        "voyage_context_4_1024_v1",
    )
    assert catalog.policy_ids == (
        "bge_only_v1",
        "voyage_only_v1",
        "bge_then_voyage_on_sla_v1",
    )
    assert catalog.generation_statuses == (
        "REGISTERED",
        "PLANNED",
        "MATERIALIZING",
        "MATERIALIZED",
        "EVAL_PASSED",
        "ACTIVE",
        "FAILED_FINAL",
        "DISABLED",
    )
    assert catalog.topic_allowlist == (
        "API",
        "DATA",
        "FINANCIAL_ENGINEERING",
        "METHODOLOGY",
        "PRODUCT_RISK",
        "RISK",
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
    data: dict[str, Any] = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

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

    string_boolean = copy.deepcopy(data)
    string_boolean["policies"][0]["perRequestFallback"] = "false"
    with pytest.raises(RagContractCatalogError):
        parse_rag_contract_catalog(string_boolean)

    null_profile = copy.deepcopy(data)
    null_profile["policies"][0]["defaultProfileId"] = None
    with pytest.raises(RagContractCatalogError):
        parse_rag_contract_catalog(null_profile)

    unsafe_artifact = copy.deepcopy(data)
    unsafe_artifact["profiles"][0]["artifactFormat"] = "PICKLE"
    with pytest.raises(RagContractCatalogError):
        parse_rag_contract_catalog(unsafe_artifact)

    unsafe_provider_artifact = copy.deepcopy(data)
    unsafe_provider_artifact["profiles"][1]["artifactFormat"] = "PICKLE"
    with pytest.raises(RagContractCatalogError):
        parse_rag_contract_catalog(unsafe_provider_artifact)

    outbound_drift = copy.deepcopy(data)
    outbound_drift["policies"][1]["outboundProviderCalls"] = False
    with pytest.raises(RagContractCatalogError):
        parse_rag_contract_catalog(outbound_drift)

    transition_drift = copy.deepcopy(data)
    transition_drift["policies"][0]["transition"]["allowed"] = True
    with pytest.raises(RagContractCatalogError):
        parse_rag_contract_catalog(transition_drift)

    wrong_answer_mode = copy.deepcopy(data)
    wrong_answer_mode["answerModes"] = ["CONCISE", "VERBOSE"]
    with pytest.raises(RagContractCatalogError):
        parse_rag_contract_catalog(wrong_answer_mode)

    lifecycle_drift = copy.deepcopy(data)
    lifecycle_drift["generationStatuses"] = ["MATERIALIZING", "ACTIVE"]
    with pytest.raises(RagContractCatalogError):
        parse_rag_contract_catalog(lifecycle_drift)

    topic_drift = copy.deepcopy(data)
    topic_drift["topicAllowlist"] = ["RISK", "API"]
    with pytest.raises(RagContractCatalogError):
        parse_rag_contract_catalog(topic_drift)

    idempotency_drift = copy.deepcopy(data)
    idempotency_drift["askRequest"]["idempotencyKeyPattern"] = "^.*$"
    with pytest.raises(RagContractCatalogError):
        parse_rag_contract_catalog(idempotency_drift)


def test_python_rag_catalog_rejects_duplicate_json_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = CATALOG_PATH.read_bytes()
    duplicated = raw.replace(
        b'  "schemaVersion": 1,',
        b'  "schemaVersion": 1,\n  "schemaVersion": 1,',
        1,
    )
    path = tmp_path / "duplicate-catalog.json"
    path.write_bytes(duplicated)
    monkeypatch.setattr(
        contract_catalog,
        "_load_digest_manifest",
        lambda: {"sha256": hashlib.sha256(duplicated).hexdigest()},
    )

    with pytest.raises(RagContractCatalogError, match="duplicate key"):
        contract_catalog.load_rag_contract_catalog(path)


def test_python_rag_catalog_rejects_digest_manifest_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        contract_catalog,
        "_load_digest_manifest",
        lambda: {"sha256": "0" * 64},
    )

    with pytest.raises(RagContractCatalogError, match="digest"):
        contract_catalog.load_rag_contract_catalog()
