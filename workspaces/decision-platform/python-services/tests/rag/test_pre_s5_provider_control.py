from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.rag.pre_s5_provider_control import (
    PreS5ProviderActivationError,
    PreS5ProviderBinding,
    load_rag_v2_query_database_dsn,
    load_optional_pre_s5_voyage_query_runtime_configuration,
    load_pre_s5_voyage_activation,
    load_pre_s5_voyage_document_batch_activation,
    load_pre_s5_voyage_evaluation_batch_activation,
    load_pre_s5_voyage_evaluation_query_activation,
    load_pre_s5_voyage_query_activation,
    load_pre_s5_voyage_query_writer_database_dsn,
    resolve_voyage_api_key,
)


def test_voyage_evaluation_batch_packet_binds_ordered_component_manifest_and_one_call(
    tmp_path: Path,
) -> None:
    _secure_root(tmp_path)
    now = datetime(2026, 8, 3, 1, tzinfo=UTC)
    scope_claim = "rvs_" + "7" * 32
    queries = tuple((f"q{index:02d}", f"question {index}") for index in range(1, 11))
    query_manifest_sha256 = _evaluation_manifest_sha256("EXACT30", queries)
    packet_directory = tmp_path / "control" / "voyage-evaluation-batch-packets"
    packet_directory.mkdir(mode=0o700)
    packet_path = packet_directory / "exact30.json"
    packet_path.write_text(
        json.dumps(
            _evaluation_batch_packet(
                now=now,
                query_manifest_sha256=query_manifest_sha256,
                scope_claim=scope_claim,
            ),
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    packet_path.chmod(0o600)

    activation = load_pre_s5_voyage_evaluation_batch_activation(
        local_root=tmp_path,
        binding=_binding(),
        component_scope="EXACT30",
        query_id_questions=queries,
        scope_claim_id=scope_claim,
        expected_token_count=10,
        now=now,
    )

    assert activation.expected_query_count == 10
    assert activation.query_manifest_sha256 == query_manifest_sha256
    assert activation.physical_call_cap == 1
    with pytest.raises(PreS5ProviderActivationError, match="PRE_S5_PROVIDER_PACKET_BINDING"):
        load_pre_s5_voyage_evaluation_batch_activation(
            local_root=tmp_path,
            binding=_binding(),
            component_scope="EXACT30",
            query_id_questions=(*queries[:-1], ("q10", "changed question")),
            scope_claim_id=scope_claim,
            expected_token_count=10,
            now=now,
        )


def test_voyage_evaluation_batch_packet_allows_window_a_and_rejects_over_two_hours(
    tmp_path: Path,
) -> None:
    _secure_root(tmp_path)
    now = datetime(2026, 8, 3, 1, tzinfo=UTC)
    scope_claim = "rvs_" + "7" * 32
    queries = tuple((f"q{index:02d}", f"question {index}") for index in range(1, 11))
    packet = _evaluation_batch_packet(
        now=now,
        query_manifest_sha256=_evaluation_manifest_sha256("EXACT30", queries),
        scope_claim=scope_claim,
    )
    packet["expiresAt"] = (now + timedelta(minutes=90)).isoformat().replace("+00:00", "Z")
    _write_packet(tmp_path, packet, filename="voyage-evaluation-batch-packets/exact30.json")

    activation = load_pre_s5_voyage_evaluation_batch_activation(
        local_root=tmp_path,
        binding=_binding(),
        component_scope="EXACT30",
        query_id_questions=queries,
        scope_claim_id=scope_claim,
        expected_token_count=10,
        now=now,
    )
    assert activation.expires_at == now + timedelta(minutes=90)

    packet["expiresAt"] = (now + timedelta(hours=2, seconds=1)).isoformat().replace("+00:00", "Z")
    _write_packet(tmp_path, packet, filename="voyage-evaluation-batch-packets/exact30.json")
    with pytest.raises(PreS5ProviderActivationError, match="PRE_S5_PROVIDER_PACKET_INVALID"):
        load_pre_s5_voyage_evaluation_batch_activation(
            local_root=tmp_path,
            binding=_binding(),
            component_scope="EXACT30",
            query_id_questions=queries,
            scope_claim_id=scope_claim,
            expected_token_count=10,
            now=now,
        )


def test_voyage_document_batch_packet_binds_exact_plan_member_and_counts(tmp_path: Path) -> None:
    _secure_root(tmp_path)
    now = datetime(2026, 8, 3, 1, tzinfo=UTC)
    batch_id = "ps5_voyage_doc_0001_0123456789abcdef"
    packet_directory = tmp_path / "control" / "voyage-document-batch-packets"
    packet_directory.mkdir(mode=0o700)
    packet_path = packet_directory / f"{batch_id}.json"
    packet_path.write_text(
        json.dumps(_document_batch_packet(now=now, batch_id=batch_id), separators=(",", ":")),
        encoding="utf-8",
    )
    packet_path.chmod(0o600)

    activation = load_pre_s5_voyage_document_batch_activation(
        local_root=tmp_path,
        binding=_binding(),
        batch_plan_sha256="3" * 64,
        batch_id=batch_id,
        batch_manifest_sha256="4" * 64,
        batch_ordinal=1,
        batch_count=3,
        token_count=100_000,
        chunk_count=2_000,
        group_count=40,
        estimated_response_bytes=16_000_000,
        now=now,
    )

    assert activation.batch_id == batch_id
    assert activation.expected_token_count == 100_000
    # input_type=document prompt tokens are billed by the provider but are not part of the
    # canonical corpus count, so the approved accounting cap must retain full API headroom.
    assert activation.token_cap == 120_000
    assert activation.byte_cap == 16_777_216
    assert activation.physical_call_cap == 1
    assert activation.retry_count == 0
    with pytest.raises(PreS5ProviderActivationError, match="PRE_S5_PROVIDER_PACKET_BINDING"):
        load_pre_s5_voyage_document_batch_activation(
            local_root=tmp_path,
            binding=_binding(),
            batch_plan_sha256="3" * 64,
            batch_id=batch_id,
            batch_manifest_sha256="4" * 64,
            batch_ordinal=1,
            batch_count=3,
            token_count=99_999,
            chunk_count=2_000,
            group_count=40,
            estimated_response_bytes=16_000_000,
            now=now,
        )


def test_voyage_document_batch_packet_allows_window_a_and_rejects_over_two_hours(
    tmp_path: Path,
) -> None:
    _secure_root(tmp_path)
    now = datetime(2026, 8, 3, 1, tzinfo=UTC)
    batch_id = "ps5_voyage_doc_0001_0123456789abcdef"
    packet = _document_batch_packet(now=now, batch_id=batch_id)
    packet["expiresAt"] = (now + timedelta(minutes=90)).isoformat().replace("+00:00", "Z")
    _write_packet(tmp_path, packet, filename=f"voyage-document-batch-packets/{batch_id}.json")

    activation = load_pre_s5_voyage_document_batch_activation(
        local_root=tmp_path,
        binding=_binding(),
        batch_plan_sha256="3" * 64,
        batch_id=batch_id,
        batch_manifest_sha256="4" * 64,
        batch_ordinal=1,
        batch_count=3,
        token_count=100_000,
        chunk_count=2_000,
        group_count=40,
        estimated_response_bytes=16_000_000,
        now=now,
    )
    assert activation.expires_at == now + timedelta(minutes=90)

    packet["expiresAt"] = (now + timedelta(hours=2, seconds=1)).isoformat().replace("+00:00", "Z")
    _write_packet(tmp_path, packet, filename=f"voyage-document-batch-packets/{batch_id}.json")
    with pytest.raises(PreS5ProviderActivationError, match="PRE_S5_PROVIDER_PACKET_INVALID"):
        load_pre_s5_voyage_document_batch_activation(
            local_root=tmp_path,
            binding=_binding(),
            batch_plan_sha256="3" * 64,
            batch_id=batch_id,
            batch_manifest_sha256="4" * 64,
            batch_ordinal=1,
            batch_count=3,
            token_count=100_000,
            chunk_count=2_000,
            group_count=40,
            estimated_response_bytes=16_000_000,
            now=now,
        )

    under_budget = _document_batch_packet(now=now, batch_id=batch_id)
    under_budget["byteCap"] = 15_999_999
    _write_packet(
        tmp_path,
        under_budget,
        filename=f"voyage-document-batch-packets/{batch_id}.json",
    )
    with pytest.raises(PreS5ProviderActivationError, match="PRE_S5_PROVIDER_PACKET_INVALID"):
        load_pre_s5_voyage_document_batch_activation(
            local_root=tmp_path,
            binding=_binding(),
            batch_plan_sha256="3" * 64,
            batch_id=batch_id,
            batch_manifest_sha256="4" * 64,
            batch_ordinal=1,
            batch_count=3,
            token_count=100_000,
            chunk_count=2_000,
            group_count=40,
            estimated_response_bytes=16_000_000,
            now=now,
        )


def test_voyage_activation_packet_is_local_only_bound_and_content_free(tmp_path: Path) -> None:
    _secure_root(tmp_path)
    now = datetime(2026, 8, 3, 1, tzinfo=UTC)
    _write_packet(tmp_path, _packet(now=now))

    activation = load_pre_s5_voyage_activation(
        local_root=tmp_path,
        binding=_binding(),
        now=now,
    )

    assert activation.provider == "VOYAGE"
    assert activation.operation == "CONTEXTUALIZED_DOCUMENT_EMBEDDING"
    assert activation.origin == "https://api.voyageai.com"
    assert activation.endpoint == "/v1/contextualizedembeddings"
    assert activation.logical_call_cap == activation.physical_call_cap == 1
    assert activation.input_microusd_per_token == 1
    assert activation.retry_count == 0
    assert activation.raw_artifact_count == 0
    summary = json.dumps(activation.content_free_summary(), ensure_ascii=False, sort_keys=True)
    assert "operator" not in summary
    assert "nonce" not in summary
    assert "evidence" not in summary
    assert "query" not in summary


def test_voyage_activation_packet_rejects_shared_mode_expiry_and_binding_drift(
    tmp_path: Path,
) -> None:
    _secure_root(tmp_path)
    now = datetime(2026, 8, 3, 1, tzinfo=UTC)
    _write_packet(tmp_path, _packet(now=now))
    packet_path = tmp_path / "control" / "pre-s5-voyage-activation.json"

    os.chmod(packet_path, 0o640)
    with pytest.raises(PreS5ProviderActivationError, match="PRE_S5_PROVIDER_PACKET_BOUNDARY"):
        load_pre_s5_voyage_activation(local_root=tmp_path, binding=_binding(), now=now)

    os.chmod(packet_path, 0o600)
    with pytest.raises(PreS5ProviderActivationError, match="PRE_S5_PROVIDER_PACKET_EXPIRED"):
        load_pre_s5_voyage_activation(
            local_root=tmp_path,
            binding=_binding(),
            now=now + timedelta(minutes=6),
        )
    with pytest.raises(PreS5ProviderActivationError, match="PRE_S5_PROVIDER_PACKET_BINDING"):
        load_pre_s5_voyage_activation(
            local_root=tmp_path,
            binding=PreS5ProviderBinding(
                head_commit="f" * 40,
                tree_object="b" * 40,
                ci_digest="c" * 64,
                security_digest="d" * 64,
            ),
            now=now,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("retryCount", 1),
        ("rawArtifactCount", 1),
        ("origin", "https://attacker.invalid"),
        ("endpoint", "/v1/files"),
        ("physicalCallCap", 2),
        ("operation", "QUERY_FALLBACK"),
        ("inputMicrousdPerToken", 0),
        ("costCapMicrousd", 119_999),
    ),
)
def test_voyage_activation_packet_rejects_scope_expansion(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    _secure_root(tmp_path)
    now = datetime(2026, 8, 3, 1, tzinfo=UTC)
    packet = _packet(now=now)
    packet[field] = value
    _write_packet(tmp_path, packet)

    with pytest.raises(PreS5ProviderActivationError, match="PRE_S5_PROVIDER_PACKET_INVALID"):
        load_pre_s5_voyage_activation(local_root=tmp_path, binding=_binding(), now=now)


def test_voyage_key_reader_uses_only_standard_environment_variable() -> None:
    assert (
        resolve_voyage_api_key({"VOYAGE_API_KEY": "test-key", "VOYAGE_TOKEN": "legacy-key"})
        == "test-key"
    )
    with pytest.raises(PreS5ProviderActivationError, match="PRE_S5_VOYAGE_API_KEY_REQUIRED"):
        resolve_voyage_api_key({"VOYAGE_TOKEN": "legacy-key"})


def test_voyage_query_packet_is_bound_to_one_normalized_question_and_opaque_scope(
    tmp_path: Path,
) -> None:
    _secure_root(tmp_path)
    now = datetime(2026, 8, 3, 1, tzinfo=UTC)
    question = "공개 근거를 비교해 보여 주세요."
    scope_claim = "rvs_" + "a" * 32
    _write_packet(
        tmp_path,
        _query_packet(now=now, question=question, scope_claim=scope_claim),
        filename="pre-s5-voyage-query-activation.json",
    )

    activation = load_pre_s5_voyage_query_activation(
        local_root=tmp_path,
        binding=_binding(),
        question=question,
        scope_claim_id=scope_claim,
        now=now,
    )

    assert activation.operation == "CONTEXTUALIZED_QUERY_EMBEDDING"
    assert activation.logical_call_cap == activation.physical_call_cap == 1
    assert activation.query_sha256 == hashlib.sha256(question.encode()).hexdigest()
    assert activation.scope_claim_sha256 == hashlib.sha256(scope_claim.encode()).hexdigest()
    summary = json.dumps(activation.content_free_summary(), ensure_ascii=False, sort_keys=True)
    assert question not in summary
    assert scope_claim not in summary

    with pytest.raises(PreS5ProviderActivationError, match="PRE_S5_PROVIDER_PACKET_BINDING"):
        load_pre_s5_voyage_query_activation(
            local_root=tmp_path,
            binding=_binding(),
            question="다른 질문입니다.",
            scope_claim_id=scope_claim,
            now=now,
        )


def test_voyage_query_packet_rejects_scope_expansion_and_missing_exact_binding(
    tmp_path: Path,
) -> None:
    _secure_root(tmp_path)
    now = datetime(2026, 8, 3, 1, tzinfo=UTC)
    question = "public corpus evidence"
    scope_claim = "rvs_" + "b" * 32
    packet = _query_packet(now=now, question=question, scope_claim=scope_claim)
    packet["physicalCallCap"] = 2
    _write_packet(tmp_path, packet, filename="pre-s5-voyage-query-activation.json")

    with pytest.raises(PreS5ProviderActivationError, match="PRE_S5_PROVIDER_PACKET_INVALID"):
        load_pre_s5_voyage_query_activation(
            local_root=tmp_path,
            binding=_binding(),
            question=question,
            scope_claim_id=scope_claim,
            now=now,
        )


def test_voyage_evaluation_query_packet_uses_only_closed_fixture_ids_and_private_packet_leaves(
    tmp_path: Path,
) -> None:
    _secure_root(tmp_path)
    now = datetime(2026, 8, 3, 1, tzinfo=UTC)
    question = "public corpus evaluation evidence"
    scope_claim = "rvs_" + "e" * 32
    packet_directory = tmp_path / "control" / "voyage-evaluation-query-packets"
    packet_directory.mkdir(mode=0o700)
    packet_path = packet_directory / "q01.json"
    packet_path.write_text(
        json.dumps(
            _query_packet(now=now, question=question, scope_claim=scope_claim),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    os.chmod(packet_path, 0o600)

    activation = load_pre_s5_voyage_evaluation_query_activation(
        local_root=tmp_path,
        binding=_binding(),
        evaluation_query_id="q01",
        question=question,
        scope_claim_id=scope_claim,
        now=now,
    )
    assert activation.operation == "CONTEXTUALIZED_QUERY_EMBEDDING"
    assert activation.query_sha256 == hashlib.sha256(question.encode()).hexdigest()

    with pytest.raises(PreS5ProviderActivationError, match="PRE_S5_PROVIDER_PACKET_INVALID"):
        load_pre_s5_voyage_evaluation_query_activation(
            local_root=tmp_path,
            binding=_binding(),
            evaluation_query_id="../../packet",
            question=question,
            scope_claim_id=scope_claim,
            now=now,
        )
    os.chmod(packet_path, 0o640)
    with pytest.raises(PreS5ProviderActivationError, match="PRE_S5_PROVIDER_PACKET_BOUNDARY"):
        load_pre_s5_voyage_evaluation_query_activation(
            local_root=tmp_path,
            binding=_binding(),
            evaluation_query_id="q01",
            question=question,
            scope_claim_id=scope_claim,
            now=now,
        )


def test_optional_voyage_query_runtime_configuration_is_local_only_and_binds_the_current_execution(
    tmp_path: Path,
) -> None:
    _secure_root(tmp_path)

    assert load_optional_pre_s5_voyage_query_runtime_configuration(local_root=tmp_path) is None

    _write_packet(
        tmp_path,
        {
            "bgeEnabled": False,
            "ciDigest": "c" * 64,
            "headCommit": "a" * 40,
            "schemaVersion": "pre-s5-voyage-query-runtime/v1",
            "securityDigest": "d" * 64,
            "treeObject": "b" * 40,
        },
        filename="pre-s5-voyage-query-runtime.json",
    )

    runtime = load_optional_pre_s5_voyage_query_runtime_configuration(local_root=tmp_path)

    assert runtime is not None
    assert runtime.local_root == tmp_path
    assert runtime.bge_enabled is False
    assert runtime.binding == _binding()


def test_voyage_query_writer_dsn_requires_a_local_secret_leaf(tmp_path: Path) -> None:
    _secure_root(tmp_path)
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    dsn_path = secrets / "rag-v2-voyage-query-writer-dsn"
    dsn_path.write_text("postgresql://decision_rag_writer@localhost/rag", encoding="utf-8")
    os.chmod(dsn_path, 0o600)

    assert (
        load_pre_s5_voyage_query_writer_database_dsn(local_root=tmp_path)
        == "postgresql://decision_rag_writer@localhost/rag"
    )

    os.chmod(dsn_path, 0o640)
    with pytest.raises(PreS5ProviderActivationError):
        load_pre_s5_voyage_query_writer_database_dsn(local_root=tmp_path)


def test_rag_v2_query_dsn_requires_the_same_owner_only_secret_boundary(tmp_path: Path) -> None:
    _secure_root(tmp_path)
    secrets = tmp_path / "secrets"
    secrets.mkdir(mode=0o700)
    dsn_path = secrets / "rag-v2-query-database-dsn"
    dsn_path.write_text("postgresql://decision_rag_query@localhost/rag", encoding="utf-8")
    os.chmod(dsn_path, 0o600)

    assert (
        load_rag_v2_query_database_dsn(local_root=tmp_path)
        == "postgresql://decision_rag_query@localhost/rag"
    )

    os.chmod(dsn_path, 0o640)
    with pytest.raises(PreS5ProviderActivationError):
        load_rag_v2_query_database_dsn(local_root=tmp_path)


def _packet(*, now: datetime) -> dict[str, object]:
    return {
        "bundleManifestSha256": "e" * 64,
        "byteCap": 4_194_304,
        "ciDigest": "c" * 64,
        "costCapMicrousd": 200_000,
        "date": "NONE",
        "endpoint": "/v1/contextualizedembeddings",
        "expiresAt": (now + timedelta(minutes=5))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "headCommit": "a" * 40,
        "issuedAt": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "inputMicrousdPerToken": 1,
        "logicalCallCap": 1,
        "operation": "CONTEXTUALIZED_DOCUMENT_EMBEDDING",
        "operator": "local-operator",
        "organizationTrainingOptOutEvidenceSha256": "f" * 64,
        "origin": "https://api.voyageai.com",
        "paymentMethodPrivacyEvidenceSha256": "0" * 64,
        "physicalCallCap": 1,
        "provider": "VOYAGE",
        "query": "FULL_BUNDLE_ORDERED_PRECHUNKED_DOCUMENTS",
        "rawArtifactCount": 0,
        "rateEvidenceSha256": "1" * 64,
        "retryCount": 0,
        "schemaVersion": "pre-s5-provider-activation/v1",
        "securityDigest": "d" * 64,
        "state": "APPROVED",
        "symbol": "NONE",
        "tokenizerSha256": "2" * 64,
        "tokenCap": 120_000,
        "treeObject": "b" * 40,
        "nonce": "ps5_voyage_activation_0001",
    }


def _query_packet(*, now: datetime, question: str, scope_claim: str) -> dict[str, object]:
    return {
        "byteCap": 1_048_576,
        "ciDigest": "c" * 64,
        "costCapMicrousd": 8_192,
        "date": "NONE",
        "endpoint": "/v1/contextualizedembeddings",
        "expiresAt": (now + timedelta(minutes=5))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "headCommit": "a" * 40,
        "inputMicrousdPerToken": 1,
        "issuedAt": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "logicalCallCap": 1,
        "nonce": "ps5_voyage_query_activation_0001",
        "operation": "CONTEXTUALIZED_QUERY_EMBEDDING",
        "operator": "local-operator",
        "organizationTrainingOptOutEvidenceSha256": "f" * 64,
        "origin": "https://api.voyageai.com",
        "paymentMethodPrivacyEvidenceSha256": "0" * 64,
        "physicalCallCap": 1,
        "provider": "VOYAGE",
        "query": "SINGLE_RAG_QUERY_SHA256_BOUND",
        "querySha256": hashlib.sha256(question.encode()).hexdigest(),
        "rawArtifactCount": 0,
        "rateEvidenceSha256": "1" * 64,
        "retryCount": 0,
        "schemaVersion": "pre-s5-voyage-query-activation/v1",
        "scopeClaimSha256": hashlib.sha256(scope_claim.encode()).hexdigest(),
        "securityDigest": "d" * 64,
        "state": "APPROVED",
        "symbol": "NONE",
        "tokenizerSha256": "2" * 64,
        "tokenCap": 8_192,
        "treeObject": "b" * 40,
    }


def _document_batch_packet(*, now: datetime, batch_id: str) -> dict[str, object]:
    return {
        "batchCount": 3,
        "batchId": batch_id,
        "batchManifestSha256": "4" * 64,
        "batchOrdinal": 1,
        "batchPlanSha256": "3" * 64,
        "byteCap": 16_777_216,
        "chunkCount": 2_000,
        "ciDigest": "c" * 64,
        "costCapMicrousd": 120_000,
        "date": "NONE",
        "endpoint": "/v1/contextualizedembeddings",
        "expiresAt": (now + timedelta(minutes=5))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "groupCount": 40,
        "headCommit": "a" * 40,
        "inputMicrousdPerToken": 1,
        "issuedAt": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "logicalCallCap": 1,
        "nonce": "ps5_voyage_document_batch_0001",
        "operation": "CONTEXTUALIZED_DOCUMENT_EMBEDDING",
        "operator": "local-operator",
        "organizationTrainingOptOutEvidenceSha256": "f" * 64,
        "origin": "https://api.voyageai.com",
        "paymentMethodPrivacyEvidenceSha256": "0" * 64,
        "physicalCallCap": 1,
        "provider": "VOYAGE",
        "query": "MANIFEST_BOUND_ORDERED_PRECHUNKED_DOCUMENT_BATCH",
        "rawArtifactCount": 0,
        "rateEvidenceSha256": "1" * 64,
        "retryCount": 0,
        "schemaVersion": "pre-s5-voyage-document-batch-activation/v1",
        "securityDigest": "d" * 64,
        "state": "APPROVED",
        "symbol": "NONE",
        "tokenCap": 120_000,
        "tokenCount": 100_000,
        "tokenizerSha256": "2" * 64,
        "treeObject": "b" * 40,
    }


def _evaluation_manifest_sha256(component_scope: str, queries: tuple[tuple[str, str], ...]) -> str:
    encoded = json.dumps(
        {
            "componentScope": component_scope,
            "queries": [
                {
                    "queryId": query_id,
                    "querySha256": hashlib.sha256(question.encode()).hexdigest(),
                }
                for query_id, question in queries
            ],
            "schemaVersion": 1,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _evaluation_batch_packet(
    *, now: datetime, query_manifest_sha256: str, scope_claim: str
) -> dict[str, object]:
    return {
        "byteCap": 4_194_304,
        "ciDigest": "c" * 64,
        "componentScope": "EXACT30",
        "costCapMicrousd": 100,
        "date": "NONE",
        "endpoint": "/v1/contextualizedembeddings",
        "expiresAt": (now + timedelta(minutes=5))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "headCommit": "a" * 40,
        "inputMicrousdPerToken": 1,
        "issuedAt": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "logicalCallCap": 1,
        "nonce": "ps5_voyage_evaluation_batch_exact30_0001",
        "operation": "CONTEXTUALIZED_QUERY_EMBEDDING",
        "operator": "local-operator",
        "organizationTrainingOptOutEvidenceSha256": "f" * 64,
        "origin": "https://api.voyageai.com",
        "paymentMethodPrivacyEvidenceSha256": "0" * 64,
        "physicalCallCap": 1,
        "provider": "VOYAGE",
        "query": "MANIFEST_BOUND_SINGLETON_QUERY_GROUP_BATCH",
        "queryCount": 10,
        "queryManifestSha256": query_manifest_sha256,
        "rawArtifactCount": 0,
        "rateEvidenceSha256": "1" * 64,
        "retryCount": 0,
        "schemaVersion": "pre-s5-voyage-evaluation-batch-activation/v1",
        "scopeClaimSha256": hashlib.sha256(scope_claim.encode()).hexdigest(),
        "securityDigest": "d" * 64,
        "state": "APPROVED",
        "symbol": "NONE",
        "tokenCap": 100,
        "tokenCount": 10,
        "tokenizerSha256": "2" * 64,
        "treeObject": "b" * 40,
    }


def _binding() -> PreS5ProviderBinding:
    return PreS5ProviderBinding(
        head_commit="a" * 40,
        tree_object="b" * 40,
        ci_digest="c" * 64,
        security_digest="d" * 64,
    )


def _secure_root(root: Path) -> None:
    os.chmod(root, 0o700)
    (root / "control").mkdir(mode=0o700)


def _write_packet(
    root: Path,
    packet: dict[str, object],
    *,
    filename: str = "pre-s5-voyage-activation.json",
) -> None:
    path = root / "control" / filename
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_text(json.dumps(packet, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.chmod(path, 0o600)
