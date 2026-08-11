"""V54 Voyage document batch vector stage/resume repository다.

성공한 provider response의 normalized vectors만 restricted SECURITY DEFINER 함수에 전달한다. canonical
text, raw provider response, credential, local path는 payload/receipt에 포함하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import psycopg
from numpy.typing import NDArray
from psycopg.types.json import Jsonb

from app.rag.pre_s5_provider_control import PreS5VoyageDocumentBatchActivation
from app.rag.pre_s5_voyage_transport import PreS5VoyageDocumentBatchResult
from app.rag.pre_s5_voyage_usage_repository import PsycopgPreS5VoyageDocumentBatchUsageLease
from app.rag.rag_v2_voyage_batching import (
    PublicVoyageBatchPlan,
    VoyageBatchVectorAccumulator,
    VoyageDocumentBatch,
)

_WRITER_ROLE = "decision_rag_writer"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STAGE_FUNCTION = "public.commit_and_stage_rag_v2_immutable_voyage_document_batch(jsonb)"
_LOAD_FUNCTION = "public.load_rag_v2_immutable_voyage_document_batch_vectors(text)"
_SUPERSEDE_FUNCTION = "public.record_rag_v2_bge_public_execution_supersession(text,text)"
_FORBIDDEN_TABLES = (
    "rag_v2_immutable_voyage_document_batch_plans",
    "rag_v2_immutable_voyage_document_batches",
    "rag_v2_immutable_voyage_document_batch_vectors",
    "rag_v2_immutable_voyage_document_batch_attempts",
    "rag_v2_immutable_bge_public_execution_supersessions",
)


class RagV2VoyageBatchRepositoryError(ValueError):
    """V54 batch vector stage/resume/supersession capability가 fail-closed 했다."""


@dataclass(frozen=True, slots=True)
class RagV2VoyageBatchStageReceipt:
    """raw/vector 없는 durable batch 진행 receipt다."""

    batch_plan_sha256: str
    batch_id: str
    state: str
    batch_reused: bool
    completed_batch_count: int
    staged_vector_count: int


class PsycopgRagV2VoyageBatchRepository:
    """writer role의 exact V54 functions만 사용하는 durable resume adapter다."""

    def __init__(self, *, database_dsn: str) -> None:
        if not isinstance(database_dsn, str) or not 1 <= len(database_dsn) <= 4_096:
            raise RagV2VoyageBatchRepositoryError("VOYAGE_BATCH_DATABASE_DSN")
        self._database_dsn = database_dsn

    def stage_success(
        self,
        *,
        activation: PreS5VoyageDocumentBatchActivation,
        plan: PublicVoyageBatchPlan,
        batch: VoyageDocumentBatch,
        result: PreS5VoyageDocumentBatchResult,
        lease: PsycopgPreS5VoyageDocumentBatchUsageLease,
    ) -> RagV2VoyageBatchStageReceipt:
        """usage COMMITTED가 존재하는 one batch vector set을 one transaction으로 durable stage한다."""

        payload = build_voyage_batch_stage_payload(
            activation=activation,
            plan=plan,
            batch=batch,
            result=result,
            lease=lease,
        )
        try:
            with psycopg.connect(self._database_dsn, autocommit=False, connect_timeout=2) as connection:
                _attest_writer_connection(connection)
                with connection.transaction():
                    _set_transaction_timeouts(connection)
                    row = connection.execute(
                        """
                        SELECT batch_plan_sha256, batch_id, state, batch_reused,
                               completed_batch_count, staged_vector_count
                        FROM public.commit_and_stage_rag_v2_immutable_voyage_document_batch(%s::jsonb)
                        """,
                        (Jsonb(payload),),
                    ).fetchone()
        except RagV2VoyageBatchRepositoryError:
            raise
        except psycopg.Error:
            raise RagV2VoyageBatchRepositoryError("VOYAGE_BATCH_STAGE_REJECTED") from None
        return _stage_receipt(row=row, plan=plan, batch=batch)

    def resume(self, *, plan: PublicVoyageBatchPlan) -> VoyageBatchVectorAccumulator:
        """DB에 COMMITTED된 batch만 load해 remaining packet set을 재구성한다."""

        accumulator = VoyageBatchVectorAccumulator(plan=plan)
        try:
            with psycopg.connect(self._database_dsn, autocommit=False, connect_timeout=2) as connection:
                _attest_writer_connection(connection)
                with connection.transaction():
                    _set_transaction_timeouts(connection)
                    rows = connection.execute(
                        """
                        SELECT batch_id, chunk_id, embedding
                        FROM public.load_rag_v2_immutable_voyage_document_batch_vectors(%s)
                        """,
                        (plan.plan_sha256,),
                    ).fetchall()
        except RagV2VoyageBatchRepositoryError:
            raise
        except psycopg.Error:
            raise RagV2VoyageBatchRepositoryError("VOYAGE_BATCH_RESUME_REJECTED") from None
        by_batch: dict[str, dict[str, NDArray[np.float32]]] = {}
        for row in rows:
            if len(row) != 3 or not isinstance(row[0], str) or not isinstance(row[1], str):
                raise RagV2VoyageBatchRepositoryError("VOYAGE_BATCH_RESUME_RECEIPT")
            by_batch.setdefault(row[0], {})[row[1]] = _parse_vector(row[2])
        known_ids = {batch.batch_id for batch in plan.batches}
        if any(batch_id not in known_ids for batch_id in by_batch):
            raise RagV2VoyageBatchRepositoryError("VOYAGE_BATCH_RESUME_RECEIPT")
        for batch in plan.batches:
            vectors_by_chunk = by_batch.get(batch.batch_id)
            if vectors_by_chunk is None:
                continue
            chunk_ids = tuple(chunk.chunk_id for group in batch.groups for chunk in group.chunks)
            if set(chunk_ids) != set(vectors_by_chunk) or len(chunk_ids) != len(vectors_by_chunk):
                raise RagV2VoyageBatchRepositoryError("VOYAGE_BATCH_RESUME_RECEIPT")
            accumulator.record_success(
                batch=batch,
                vectors=np.stack(tuple(vectors_by_chunk[chunk_id] for chunk_id in chunk_ids)),
            )
        return accumulator

    def record_bge_supersession(
        self,
        *,
        exact30_component_generation_id: str,
        oa112_component_generation_id: str,
    ) -> None:
        """partial BGE generation을 삭제/활성화하지 않고 terminal supersession marker만 기록한다."""

        try:
            with psycopg.connect(self._database_dsn, autocommit=False, connect_timeout=2) as connection:
                _attest_writer_connection(connection)
                with connection.transaction():
                    _set_transaction_timeouts(connection)
                    connection.execute(
                        "SELECT public.record_rag_v2_bge_public_execution_supersession(%s, %s)",
                        (exact30_component_generation_id, oa112_component_generation_id),
                    ).fetchone()
        except RagV2VoyageBatchRepositoryError:
            raise
        except psycopg.Error:
            raise RagV2VoyageBatchRepositoryError("BGE_PUBLIC_SUPERSESSION_REJECTED") from None


def build_voyage_batch_stage_payload(
    *,
    activation: PreS5VoyageDocumentBatchActivation,
    plan: PublicVoyageBatchPlan,
    batch: VoyageDocumentBatch,
    result: PreS5VoyageDocumentBatchResult,
    lease: PsycopgPreS5VoyageDocumentBatchUsageLease,
) -> dict[str, object]:
    """content/vector identity만 V54 closed JSON shape로 만들고 raw text를 제외한다."""

    if (
        not isinstance(activation, PreS5VoyageDocumentBatchActivation)
        or not isinstance(plan, PublicVoyageBatchPlan)
        or not isinstance(batch, VoyageDocumentBatch)
        or batch not in plan.batches
        or activation.batch_plan_sha256 != plan.plan_sha256
        or activation.batch_id != batch.batch_id
        or activation.batch_manifest_sha256 != batch.batch_manifest_sha256
        or activation.packet_sha256 == ""
        or not isinstance(result, PreS5VoyageDocumentBatchResult)
        or not isinstance(lease, PsycopgPreS5VoyageDocumentBatchUsageLease)
        or result.expected_input_tokens != batch.token_count
    ):
        raise RagV2VoyageBatchRepositoryError("VOYAGE_BATCH_STAGE_ARGUMENT")
    array = _validated_vectors(vectors=result.vectors, expected_rows=batch.chunk_count)
    vector_rows: list[dict[str, object]] = []
    cursor = 0
    for segment in batch.segments:
        for ordinal, chunk in enumerate(segment.group.chunks, start=1):
            vector = np.array(array[cursor], dtype=np.float32, copy=True)
            vector_sha256 = hashlib.sha256(vector.astype("<f4", copy=False).tobytes()).hexdigest()
            vector_rows.append(
                {
                    "chunkId": chunk.chunk_id,
                    "chunkOrdinal": ordinal,
                    "componentScope": segment.component_scope,
                    "contextSetHash": segment.group.context_set_hash,
                    "embeddingInputHash": chunk.embedding_input_hash,
                    "sourceId": segment.source_id,
                    "sourceRevisionId": segment.source_revision_id,
                    "vector": [float(value) for value in vector],
                    "vectorSha256": vector_sha256,
                }
            )
            cursor += 1
    vector_set_sha256 = hashlib.sha256(
        "\n".join(
            f"{row['chunkId']}:{row['vectorSha256']}" for row in vector_rows
        ).encode("utf-8")
    ).hexdigest()
    return {
        "batch": {
            "batchCount": batch.batch_count,
            "batchId": batch.batch_id,
            "batchManifestSha256": batch.batch_manifest_sha256,
            "batchOrdinal": batch.batch_ordinal,
            "chunkCount": batch.chunk_count,
            "estimatedResponseBytes": batch.estimated_response_bytes,
            "groupCount": batch.group_count,
            "tokenCount": batch.token_count,
            "vectorSetSha256": vector_set_sha256,
        },
        "packetSha256": activation.packet_sha256,
        "plan": {
            "batchCount": len(plan.batches),
            "batchPlanSha256": plan.plan_sha256,
            "chunkCount": plan.chunk_count,
            "officialTokenizerSha256": plan.tokenizer_sha256,
            "ownerPrivateOrderedGroupCount": 0,
            "ownerScopeSha256": None,
            "sourceCount": plan.source_count,
            "tokenCount": plan.token_count,
        },
        "schemaVersion": "pre-s5-voyage-document-batch-stage/v1",
        "usage": {
            "actualCostMicrousd": result.actual_cost_microusd,
            "expectedInputTokens": result.expected_input_tokens,
            "providerTotalTokens": result.provider_total_tokens,
            "usageEventId": lease.usage_event_id,
        },
        "vectors": vector_rows,
    }


def _validated_vectors(*, vectors: object, expected_rows: int) -> NDArray[np.float32]:
    try:
        array = np.asarray(vectors)
    except Exception:
        raise RagV2VoyageBatchRepositoryError("VOYAGE_BATCH_STAGE_VECTOR") from None
    if (
        array.dtype != np.float32
        or array.shape != (expected_rows, 1024)
        or not bool(np.isfinite(array).all())
    ):
        raise RagV2VoyageBatchRepositoryError("VOYAGE_BATCH_STAGE_VECTOR")
    norms = np.linalg.norm(array, axis=1)
    if not bool(np.allclose(norms, np.ones_like(norms), rtol=0.0, atol=1e-5)):
        raise RagV2VoyageBatchRepositoryError("VOYAGE_BATCH_STAGE_VECTOR")
    return cast(NDArray[np.float32], array)


def _parse_vector(value: object) -> NDArray[np.float32]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise RagV2VoyageBatchRepositoryError("VOYAGE_BATCH_RESUME_RECEIPT") from None
    try:
        vector = np.asarray(value, dtype=np.float32)
    except Exception:
        raise RagV2VoyageBatchRepositoryError("VOYAGE_BATCH_RESUME_RECEIPT") from None
    if (
        vector.shape != (1024,)
        or not bool(np.isfinite(vector).all())
        or not math.isclose(float(np.linalg.norm(vector)), 1.0, rel_tol=0.0, abs_tol=1e-5)
    ):
        raise RagV2VoyageBatchRepositoryError("VOYAGE_BATCH_RESUME_RECEIPT")
    return vector


def _stage_receipt(
    *,
    row: tuple[object, ...] | None,
    plan: PublicVoyageBatchPlan,
    batch: VoyageDocumentBatch,
) -> RagV2VoyageBatchStageReceipt:
    if (
        row is None
        or len(row) != 6
        or row[0] != plan.plan_sha256
        or row[1] != batch.batch_id
        or row[2] not in {"STAGING", "COMPLETE"}
        or type(row[3]) is not bool
        or type(row[4]) is not int
        or type(row[5]) is not int
        or not 1 <= row[4] <= len(plan.batches)
        or not batch.chunk_count <= row[5] <= plan.chunk_count
        or (row[2] == "COMPLETE" and (row[4] != len(plan.batches) or row[5] != plan.chunk_count))
    ):
        raise RagV2VoyageBatchRepositoryError("VOYAGE_BATCH_STAGE_RECEIPT")
    return RagV2VoyageBatchStageReceipt(
        batch_plan_sha256=plan.plan_sha256,
        batch_id=batch.batch_id,
        state=row[2],
        batch_reused=row[3],
        completed_batch_count=row[4],
        staged_vector_count=row[5],
    )


def _attest_writer_connection(connection: psycopg.Connection[Any]) -> None:
    if connection.execute("SELECT current_user").fetchone() != (_WRITER_ROLE,):
        raise RagV2VoyageBatchRepositoryError("VOYAGE_BATCH_WRITER_ROLE")
    for table in _FORBIDDEN_TABLES:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            row = connection.execute(
                "SELECT has_table_privilege(current_user, %s, %s)",
                (f"public.{table}", privilege),
            ).fetchone()
            if row is not None and row[0] is True:
                raise RagV2VoyageBatchRepositoryError("VOYAGE_BATCH_WRITER_PRIVILEGE")
    for function in (_STAGE_FUNCTION, _LOAD_FUNCTION, _SUPERSEDE_FUNCTION):
        row = connection.execute(
            "SELECT has_function_privilege(current_user, %s, 'EXECUTE')",
            (function,),
        ).fetchone()
        if row is None or row[0] is not True:
            raise RagV2VoyageBatchRepositoryError("VOYAGE_BATCH_WRITER_PRIVILEGE")


def _set_transaction_timeouts(connection: psycopg.Connection[Any]) -> None:
    connection.execute("SET LOCAL statement_timeout = '60s'")
    connection.execute("SET LOCAL lock_timeout = '10s'")
    connection.execute("SET LOCAL idle_in_transaction_session_timeout = '75s'")
