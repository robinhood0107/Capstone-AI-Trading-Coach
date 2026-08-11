from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.rag.pre_s5_provider_control import PreS5VoyageDocumentBatchActivation
from app.rag.pre_s5_voyage_usage_repository import PsycopgPreS5VoyageUsageRepository
from app.rag.rag_v2_external_exact30_voyage_runner import (
    VoyagePreChunkedChunk,
    VoyagePreChunkedDocumentGroup,
)
from app.rag.rag_v2_voyage_batch_repository import (
    PsycopgRagV2VoyageBatchRepository,
    RagV2VoyageBatchRepositoryError,
    build_voyage_batch_stage_payload,
)
from app.rag.rag_v2_voyage_batching import VoyagePreparedComponent, build_public_voyage_batch_plan


class _TokenCounter:
    model = "voyage-context-4"
    tokenizer_sha256 = "e" * 64

    def count_texts(self, *, texts: tuple[str, ...], token_cap: int) -> int:
        assert token_cap >= len(texts)
        return len(texts)


def test_batch_stage_payload_contains_only_identity_and_normalized_vectors() -> None:
    plan = _plan()
    batch = plan.batches[0]
    vectors = np.zeros((batch.chunk_count, 1024), dtype=np.float32)
    vectors[:, 0] = 1.0

    payload = build_voyage_batch_stage_payload(
        activation=_activation(plan.plan_sha256, batch),
        plan=plan,
        batch=batch,
        vectors=vectors,
    )

    assert payload["schemaVersion"] == "pre-s5-voyage-document-batch-stage/v1"
    assert payload["plan"]["sourceCount"] == 142  # type: ignore[index]
    assert payload["batch"]["chunkCount"] == 142  # type: ignore[index]
    assert len(payload["vectors"]) == 142  # type: ignore[arg-type]
    encoded = str(payload)
    assert "canonical text" not in encoded
    assert "providerResponse" not in encoded
    assert "test-key" not in encoded

    with pytest.raises(RagV2VoyageBatchRepositoryError, match="VOYAGE_BATCH_STAGE_ARGUMENT"):
        build_voyage_batch_stage_payload(
            activation=replace(_activation(plan.plan_sha256, batch), batch_manifest_sha256="f" * 64),
            plan=plan,
            batch=batch,
            vectors=vectors,
        )


def test_batch_repository_initial_resume_is_empty_then_reuses_committed_vectors(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    plan = _plan()
    batch = plan.batches[0]
    activation = _activation(plan.plan_sha256, batch)
    vectors = np.zeros((batch.chunk_count, 1024), dtype=np.float32)
    vectors[:, 0] = 1.0
    repository = PsycopgRagV2VoyageBatchRepository(
        database_dsn=isolated_postgres_cluster["rag_writer_dsn"]
    )

    initial = repository.resume(plan=plan)
    assert initial.pending_batches == (batch,)

    lease = PsycopgPreS5VoyageUsageRepository(
        database_dsn=isolated_postgres_cluster["rag_writer_dsn"]
    ).reserve_document_batch(activation=activation, plan=plan, batch=batch)
    lease.claim_attempt(now=datetime.now(UTC))
    lease.commit(
        expected_input_tokens=batch.token_count,
        total_tokens=batch.token_count,
        actual_cost_microusd=batch.token_count,
    )
    receipt = repository.stage_success(
        activation=activation,
        plan=plan,
        batch=batch,
        vectors=vectors,
    )
    resumed = repository.resume(plan=plan)

    assert receipt.state == "COMPLETE"
    assert receipt.completed_batch_count == 1
    assert resumed.complete is True
    assert resumed.pending_batches == ()
    assert resumed.ordered_vectors(groups=tuple(segment.group for segment in batch.segments)).shape == (
        142,
        1024,
    )


def _plan():
    exact = tuple(_group("exact", index) for index in range(30))
    oa = tuple(_group("oa", index) for index in range(112))
    return build_public_voyage_batch_plan(
        components=(
            VoyagePreparedComponent("EXACT30", None, exact),
            VoyagePreparedComponent("OA112", None, oa),
            VoyagePreparedComponent("OWNER_PRIVATE", None, ()),
        ),
        token_counter=_TokenCounter(),
    )


def _activation(plan_sha256: str, batch) -> PreS5VoyageDocumentBatchActivation:
    return PreS5VoyageDocumentBatchActivation(
        packet_sha256="a" * 64,
        nonce_sha256="b" * 64,
        batch_plan_sha256=plan_sha256,
        batch_id=batch.batch_id,
        batch_manifest_sha256=batch.batch_manifest_sha256,
        batch_ordinal=batch.batch_ordinal,
        batch_count=batch.batch_count,
        expected_token_count=batch.token_count,
        expected_chunk_count=batch.chunk_count,
        expected_group_count=batch.group_count,
        rate_evidence_sha256="c" * 64,
        tokenizer_sha256="e" * 64,
        provider="VOYAGE",
        operation="CONTEXTUALIZED_DOCUMENT_EMBEDDING",
        origin="https://api.voyageai.com",
        endpoint="/v1/contextualizedembeddings",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        logical_call_cap=1,
        physical_call_cap=1,
        token_cap=110_000,
        byte_cap=4_194_304,
        cost_cap_microusd=110_000,
        input_microusd_per_token=1,
        retry_count=0,
        raw_artifact_count=0,
    )


def _group(prefix: str, index: int) -> VoyagePreChunkedDocumentGroup:
    text = f"{prefix} source {index}"
    digest = hashlib.sha256(text.encode()).hexdigest()
    return VoyagePreChunkedDocumentGroup(
        source_id=f"src_{prefix}_{index:03d}",
        source_revision_id=f"srv_{prefix}_{index:03d}",
        context_set_hash=hashlib.sha256(f"context:{text}".encode()).hexdigest(),
        chunks=(
            VoyagePreChunkedChunk(
                chunk_id=f"rag_v2_chk_{digest[:32]}",
                canonical_text=text,
                canonical_text_sha256=digest,
                embedding_input_hash=hashlib.sha256(f"input:{text}".encode()).hexdigest(),
                token_count=1,
            ),
        ),
    )
