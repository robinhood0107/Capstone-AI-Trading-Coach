from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Mapping, Protocol, Sequence, cast
from urllib.parse import urlsplit

import numpy as np
import psycopg
from numpy.typing import NDArray

from app.rag.bge_artifact import BgeVerifiedPacket
from app.rag.bge_runtime import validate_embedding_batch
from app.rag.ingest_pipeline import (
    RagCanonicalChunk,
    RagEmbeddingInput,
    RagTokenizer,
    build_canonical_chunks,
    build_embedding_inputs,
    parse_markdown_document,
)
from app.rag.source_card_corpus import (
    PUBLIC_TOPICS_BY_SOURCE_ID,
    FrozenSourceCard,
    FrozenSourceCardCorpus,
)

_PROFILE_ID = "bge_m3_local_1024_v1"
_MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
_ARTIFACT_MANIFEST_SHA256 = (
    "a0ae6372b2d735b593d806d24c1155cb48dd7188adebe7d6b7619a1622fb71aa"
)
_TOKENIZER_SHA256 = (
    "6710678b12670bc442b99edc952c4d996ae309a7020c1fa0096dd245c2faf790"
)
_PARSER_VERSION = "rag-source-card-v2-markdown-v1"
_CANONICALIZER_VERSION = "utf8-nfc-lf-source-card-body-v1"
_CHUNKER_VERSION = "bge-tokenizer-heading-400-600-v1"
_INPUT_STRATEGY_VERSION = "adjacent-7.5pct-per-side-no-reallocation-v1"
_CORPUS_MANIFEST_SHA256 = (
    "7f2b4d72dcbaccf57cbe49a980973b17b4a9bfd85bec4694fd66fd7fd2a9decd"
)
_EXPECTED_ARTIFACT_FILE_COUNT = 10
_EXPECTED_ARTIFACT_BYTES = 2_289_781_803
_EXPECTED_CARD_COUNT = 30
_EXPECTED_WRITER_ROLE = "decision_rag_writer"
_EXPECTED_ADMIN_ROLE = "decision_rag_admin"
_ALLOWED_TARGETS = frozenset({"local", "offline", "test", "testcontainers"})
_TARGET_ENV = "RAG_SOURCE_REGISTER_TARGET"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class BgeFullGenerationError(ValueError):
    """S4.2B corpus·vector·DB·activation gate를 bounded marker로 보고한다."""


@dataclass(frozen=True)
class BgeBatchBenchmarkReceipt:
    """16~64 후보의 memory/latency 측정과 선택 batch를 결합한 immutable receipt."""

    selected_batch_size: int
    candidates: tuple[int, ...]
    peak_rss_bytes: tuple[tuple[int, int], ...]
    elapsed_ms: tuple[tuple[int, float], ...]
    environment_fingerprint_sha256: str
    benchmark_sha256: str


@dataclass(frozen=True)
class BgeFullGenerationItem:
    """exact v2 card의 revision, one-card-one-chunk와 BGE input 계획."""

    card: FrozenSourceCard
    source_revision_id: str
    ingest_run_id: str
    chunk: RagCanonicalChunk
    embedding_input: RagEmbeddingInput


@dataclass(frozen=True)
class BgeFullGenerationPlan:
    """frozen corpus와 pinned runtime identity를 결합한 deterministic full generation."""

    generation_id: str
    generation_hash: str
    corpus_hash: str
    membership_hash: str
    materialization_run_id: str
    embedding_profile_id: str
    model_revision: str
    artifact_manifest_sha256: str
    tokenizer_sha256: str
    parser_version: str
    canonicalizer_version: str
    chunker_version: str
    input_strategy_version: str
    batch_size: int
    batch_benchmark_sha256: str
    environment_fingerprint_sha256: str
    items: tuple[BgeFullGenerationItem, ...]


@dataclass(frozen=True)
class BgeFullStagedEmbedding:
    """full generation COPY 한 행과 raw float32 integrity hash."""

    generation_id: str
    materialization_run_id: str
    chunk_revision_id: str
    embedding_profile_id: str
    embedding_input_hash: str
    context_set_hash: str | None
    embedding: NDArray[np.float32]
    staging_row_hash: str


@dataclass(frozen=True)
class BgeGenerationDatabaseReceipt:
    """writer finalizer가 pointer 변경 없이 반환한 MATERIALIZED receipt."""

    generation_id: str
    materialization_run_id: str
    final_row_count: int
    status: Literal["MATERIALIZED"]
    aggregate_row_hash: str
    generation_vector_hash: str
    active_pointer_changed: bool


@dataclass(frozen=True)
class BgeMaterializedGeneration:
    """독립 DB reread 전 application memory와 database receipt의 결합."""

    plan: BgeFullGenerationPlan
    rows: tuple[BgeFullStagedEmbedding, ...]
    aggregate_row_hash: str
    generation_vector_hash: str
    database_receipt: BgeGenerationDatabaseReceipt


@dataclass(frozen=True)
class BgeGenerationParityReceipt:
    """독립 admin projection에서 다시 읽은 pgvector와 raw float32 parity."""

    generation_id: str
    row_count: int
    generation_vector_hash: str
    max_absolute_error: float
    minimum_cosine_similarity: float
    passed: bool


@dataclass(frozen=True)
class BgeGenerationBenchmarkReceipt:
    """full 30 corpus의 최종 warm latency와 외부 physical call receipt."""

    report_sha256: str
    query_set_sha256: str
    environment_fingerprint_sha256: str
    warmup_count: int
    measured_count: int
    warm_p95_ms: float
    provider_physical_calls: int
    voyage_physical_calls: int
    gemini_physical_calls: int
    openai_physical_calls: int
    passed: bool


@dataclass(frozen=True)
class BgeActivationRequest:
    """bounded SECURITY DEFINER activation 함수에 전달할 exact CAS/evidence projection."""

    generation_id: str
    expected_current_generation_id: str | None
    expected_policy_version: int
    corpus_hash: str
    generation_hash: str
    membership_hash: str
    aggregate_row_hash: str
    db_vector_hash: str
    expected_source_revision_count: int
    expected_chunk_count: int
    batch_size: int
    model_revision: str
    model_file_manifest_hash: str
    tokenizer_sha256: str
    parser_version: str
    chunker_version: str
    input_strategy_version: str
    batch_benchmark_sha256: str
    environment_fingerprint_sha256: str
    benchmark_report_sha256: str
    warm_p95_ms: float
    approved_by_audit_ref: str


@dataclass(frozen=True)
class BgeActivationReceipt:
    """한 transaction CAS 뒤 공개 가능한 이전/현재 pointer와 generation 상태."""

    previous_generation_id: str | None
    active_generation_id: str
    policy_version: int
    generation_status: Literal["ACTIVE"]


class BgeEmbeddingPort(Protocol):
    def embed(self, texts: tuple[str, ...]) -> NDArray[np.float32]:
        """network 없이 canonical BGE input batch를 1024-d unit vector로 변환한다."""


class BgeFullGenerationWriterPort(Protocol):
    def materialize(
        self,
        *,
        plan: BgeFullGenerationPlan,
        rows: tuple[BgeFullStagedEmbedding, ...],
        aggregate_row_hash: str,
        generation_vector_hash: str,
    ) -> BgeGenerationDatabaseReceipt:
        """writer-only staging/COPY/finalize를 수행하고 MATERIALIZED에서 멈춘다."""


class BgeFullGenerationReaderPort(Protocol):
    def read_embeddings(
        self,
        *,
        generation_id: str,
        expected_corpus_hash: str,
        expected_row_count: int,
    ) -> tuple[tuple[str, NDArray[np.float32], str], ...]:
        """admin bounded projection으로 지정 generation의 vector를 다시 읽는다."""


class BgeFullGenerationAdminPort(Protocol):
    def activate(self, *, request: BgeActivationRequest) -> object:
        """attestation, prior supersede와 pointer CAS를 한 transaction으로 수행한다."""


def prepare_bge_full_generation(
    *,
    corpus: FrozenSourceCardCorpus,
    tokenizer: RagTokenizer,
    artifact: BgeVerifiedPacket,
    batch_benchmark: BgeBatchBenchmarkReceipt,
) -> BgeFullGenerationPlan:
    """merged exact 30 manifest와 pinned BGE identity에서 immutable generation을 만든다."""

    _validate_corpus_binding(corpus)
    _validate_artifact(artifact)
    _validate_batch_benchmark(batch_benchmark)

    intermediate: list[
        tuple[FrozenSourceCard, str, str, RagCanonicalChunk]
    ] = []
    for card in sorted(
        corpus.cards,
        key=lambda value: value.source_id.encode("utf-8"),
    ):
        source_revision_id = (
            f"src_rev_{_hash_parts(card.source_id, card.card_sha256)[:32]}"
        )
        ingest_run_id = (
            "rag_ing_"
            + _hash_parts(
                source_revision_id,
                _PARSER_VERSION,
                _CANONICALIZER_VERSION,
                card.content_sha256,
            )[:32]
        )
        chunks = build_canonical_chunks(
            source_id=card.source_id,
            source_revision_id=source_revision_id,
            blocks=parse_markdown_document(card.canonical_body),
            tokenizer=tokenizer,
            min_tokens=400,
            max_tokens=600,
            atomic_document=True,
        )
        if len(chunks) != 1 or chunks[0].sequence != 1:
            raise BgeFullGenerationError("ONE_CARD_ONE_CHUNK")
        intermediate.append((card, source_revision_id, ingest_run_id, chunks[0]))

    embedding_inputs = build_embedding_inputs(
        (item[3] for item in intermediate),
        embedding_profile_id=_PROFILE_ID,
        tokenizer=tokenizer,
    )
    if len(embedding_inputs) != _EXPECTED_CARD_COUNT:
        raise BgeFullGenerationError("EXACT_EMBEDDING_INPUT_COUNT")
    items = tuple(
        BgeFullGenerationItem(
            card=card,
            source_revision_id=source_revision_id,
            ingest_run_id=ingest_run_id,
            chunk=chunk,
            embedding_input=embedding_input,
        )
        for (
            card,
            source_revision_id,
            ingest_run_id,
            chunk,
        ), embedding_input in zip(intermediate, embedding_inputs, strict=True)
    )
    if any(
        item.chunk.chunk_revision_id != item.embedding_input.chunk_revision_id
        or item.embedding_input.embedding_profile_id != _PROFILE_ID
        or item.embedding_input.context_set_hash is not None
        for item in items
    ):
        raise BgeFullGenerationError("BGE_INPUT_IDENTITY")

    membership_hash = _membership_hash(items)
    generation_payload = {
        "artifactManifestSha256": artifact.file_manifest_sha256,
        "batchBenchmarkSha256": batch_benchmark.benchmark_sha256,
        "batchSize": batch_benchmark.selected_batch_size,
        "canonicalizerVersion": _CANONICALIZER_VERSION,
        "chunkerVersion": _CHUNKER_VERSION,
        "corpusManifestSha256": corpus.corpus_manifest_sha256,
        "embeddingProfileId": _PROFILE_ID,
        "inputStrategyVersion": _INPUT_STRATEGY_VERSION,
        "membershipHash": membership_hash,
        "modelRevision": artifact.revision,
        "parserVersion": _PARSER_VERSION,
        "tokenizerSha256": _TOKENIZER_SHA256,
    }
    generation_hash = _canonical_json_hash(generation_payload)
    generation_id = f"rag_gen_{generation_hash[:32]}"
    materialization_run_id = (
        "rag_mat_"
        + _hash_parts(
            generation_id,
            artifact.file_manifest_sha256,
            batch_benchmark.benchmark_sha256,
        )[:32]
    )
    return BgeFullGenerationPlan(
        generation_id=generation_id,
        generation_hash=generation_hash,
        corpus_hash=corpus.corpus_manifest_sha256,
        membership_hash=membership_hash,
        materialization_run_id=materialization_run_id,
        embedding_profile_id=_PROFILE_ID,
        model_revision=artifact.revision,
        artifact_manifest_sha256=artifact.file_manifest_sha256,
        tokenizer_sha256=_TOKENIZER_SHA256,
        parser_version=_PARSER_VERSION,
        canonicalizer_version=_CANONICALIZER_VERSION,
        chunker_version=_CHUNKER_VERSION,
        input_strategy_version=_INPUT_STRATEGY_VERSION,
        batch_size=batch_benchmark.selected_batch_size,
        batch_benchmark_sha256=batch_benchmark.benchmark_sha256,
        environment_fingerprint_sha256=(
            batch_benchmark.environment_fingerprint_sha256
        ),
        items=items,
    )


def execute_bge_full_generation(
    *,
    plan: BgeFullGenerationPlan,
    embedder: BgeEmbeddingPort,
    repository: BgeFullGenerationWriterPort,
) -> BgeMaterializedGeneration:
    """deterministic batches를 생성해 COPY/finalize하고 pointer 변경 없이 MATERIALIZED로 끝낸다."""

    if (
        len(plan.items) != _EXPECTED_CARD_COUNT
        or plan.batch_size not in range(16, 65)
        or plan.corpus_hash != _CORPUS_MANIFEST_SHA256
    ):
        raise BgeFullGenerationError("FULL_GENERATION_PLAN")
    vectors: list[NDArray[np.float32]] = []
    texts = tuple(item.embedding_input.text for item in plan.items)
    for start in range(0, len(texts), plan.batch_size):
        batch_texts = texts[start : start + plan.batch_size]
        batch = validate_embedding_batch(
            embedder.embed(batch_texts),
            expected_rows=len(batch_texts),
        )
        vectors.extend(
            cast(NDArray[np.float32], np.asarray(row, dtype=np.float32))
            for row in batch
        )
    if len(vectors) != len(plan.items):
        raise BgeFullGenerationError("FULL_GENERATION_VECTOR_COUNT")
    rows = tuple(
        _build_staging_row(plan=plan, item=item, embedding=vectors[index])
        for index, item in enumerate(plan.items)
    )
    aggregate_row_hash = _aggregate_row_hash(rows)
    generation_vector_hash = _generation_vector_hash(
        (row.chunk_revision_id, row.embedding) for row in rows
    )
    receipt = repository.materialize(
        plan=plan,
        rows=rows,
        aggregate_row_hash=aggregate_row_hash,
        generation_vector_hash=generation_vector_hash,
    )
    if (
        receipt.generation_id != plan.generation_id
        or receipt.materialization_run_id != plan.materialization_run_id
        or receipt.final_row_count != len(rows)
        or receipt.status != "MATERIALIZED"
        or receipt.aggregate_row_hash != aggregate_row_hash
        or receipt.generation_vector_hash != generation_vector_hash
        or receipt.active_pointer_changed
    ):
        raise BgeFullGenerationError("FULL_GENERATION_DATABASE_RECEIPT")
    return BgeMaterializedGeneration(
        plan=plan,
        rows=rows,
        aggregate_row_hash=aggregate_row_hash,
        generation_vector_hash=generation_vector_hash,
        database_receipt=receipt,
    )


def verify_bge_full_generation_parity(
    *,
    materialized: BgeMaterializedGeneration,
    reader: BgeFullGenerationReaderPort,
    max_absolute_error_tolerance: float = 1e-6,
    minimum_cosine_similarity_tolerance: float = 0.999999,
) -> BgeGenerationParityReceipt:
    """독립 DB projection을 reread해 membership, row hash와 float32 vector parity를 검증한다."""

    expected = {
        row.chunk_revision_id: row
        for row in materialized.rows
    }
    if len(expected) != _EXPECTED_CARD_COUNT:
        raise BgeFullGenerationError("PARITY_EXPECTED_MEMBERSHIP")
    persisted = reader.read_embeddings(
        generation_id=materialized.plan.generation_id,
        expected_corpus_hash=materialized.plan.corpus_hash,
        expected_row_count=len(expected),
    )
    persisted_by_id = {chunk_id: (vector, row_hash) for chunk_id, vector, row_hash in persisted}
    if len(persisted) != len(persisted_by_id) or set(persisted_by_id) != set(expected):
        raise BgeFullGenerationError("PARITY_DATABASE_MEMBERSHIP")

    maximum_error = 0.0
    minimum_cosine = 1.0
    persisted_vectors: list[tuple[str, NDArray[np.float32]]] = []
    for chunk_id in sorted(expected, key=lambda value: value.encode("utf-8")):
        expected_row = expected[chunk_id]
        persisted_vector, persisted_row_hash = persisted_by_id[chunk_id]
        validated = validate_embedding_batch(
            np.asarray(persisted_vector, dtype=np.float32).reshape(1, -1),
            expected_rows=1,
        )[0]
        if persisted_row_hash != expected_row.staging_row_hash:
            raise BgeFullGenerationError("PARITY_ROW_HASH")
        error = float(
            np.max(
                np.abs(
                    np.asarray(expected_row.embedding, dtype=np.float64)
                    - np.asarray(validated, dtype=np.float64)
                )
            )
        )
        cosine = float(
            np.dot(
                np.asarray(expected_row.embedding, dtype=np.float64),
                np.asarray(validated, dtype=np.float64),
            )
        )
        maximum_error = max(maximum_error, error)
        minimum_cosine = min(minimum_cosine, cosine)
        persisted_vectors.append(
            (chunk_id, cast(NDArray[np.float32], np.asarray(validated, dtype=np.float32)))
        )
    db_vector_hash = _generation_vector_hash(persisted_vectors)
    passed = (
        maximum_error <= max_absolute_error_tolerance
        and minimum_cosine >= minimum_cosine_similarity_tolerance
        and db_vector_hash == materialized.generation_vector_hash
    )
    if not passed:
        raise BgeFullGenerationError("PARITY_VECTOR")
    return BgeGenerationParityReceipt(
        generation_id=materialized.plan.generation_id,
        row_count=len(persisted_vectors),
        generation_vector_hash=db_vector_hash,
        max_absolute_error=maximum_error,
        minimum_cosine_similarity=minimum_cosine,
        passed=True,
    )


def activate_bge_full_generation(
    *,
    materialized: BgeMaterializedGeneration,
    parity: BgeGenerationParityReceipt,
    benchmark: BgeGenerationBenchmarkReceipt,
    expected_current_generation_id: str | None,
    expected_policy_version: int,
    approved_by_audit_ref: str,
    repository: BgeFullGenerationAdminPort,
) -> BgeActivationReceipt:
    """모든 local gate가 PASS일 때만 전용 admin CAS transaction을 호출한다."""

    plan = materialized.plan
    if (
        parity.generation_id != plan.generation_id
        or parity.row_count != len(plan.items)
        or parity.generation_vector_hash != materialized.generation_vector_hash
        or not parity.passed
    ):
        raise BgeFullGenerationError("PARITY_RECEIPT")
    if (
        not benchmark.passed
        or benchmark.warmup_count < 20
        or benchmark.measured_count < 100
        or not math.isfinite(benchmark.warm_p95_ms)
        or benchmark.warm_p95_ms <= 0
        or benchmark.warm_p95_ms > 1500.0
        or any(
            count != 0
            for count in (
                benchmark.provider_physical_calls,
                benchmark.voyage_physical_calls,
                benchmark.gemini_physical_calls,
                benchmark.openai_physical_calls,
            )
        )
        or not _is_sha256(benchmark.report_sha256)
        or not _is_sha256(benchmark.query_set_sha256)
        or not _is_sha256(benchmark.environment_fingerprint_sha256)
        or benchmark.environment_fingerprint_sha256
        != plan.environment_fingerprint_sha256
    ):
        raise BgeFullGenerationError("FINAL_BENCHMARK")
    if (
        expected_policy_version < 1
        or (
            expected_current_generation_id is not None
            and not re.fullmatch(r"rag_gen_[0-9a-f]{32}", expected_current_generation_id)
        )
        or not 16 <= len(approved_by_audit_ref) <= 128
    ):
        raise BgeFullGenerationError("ACTIVATION_CAS_INPUT")
    request = BgeActivationRequest(
        generation_id=plan.generation_id,
        expected_current_generation_id=expected_current_generation_id,
        expected_policy_version=expected_policy_version,
        corpus_hash=plan.corpus_hash,
        generation_hash=plan.generation_hash,
        membership_hash=plan.membership_hash,
        aggregate_row_hash=materialized.aggregate_row_hash,
        db_vector_hash=parity.generation_vector_hash,
        expected_source_revision_count=_EXPECTED_CARD_COUNT,
        expected_chunk_count=len(plan.items),
        batch_size=plan.batch_size,
        model_revision=plan.model_revision,
        model_file_manifest_hash=plan.artifact_manifest_sha256,
        tokenizer_sha256=plan.tokenizer_sha256,
        parser_version=plan.parser_version,
        chunker_version=plan.chunker_version,
        input_strategy_version=plan.input_strategy_version,
        batch_benchmark_sha256=plan.batch_benchmark_sha256,
        environment_fingerprint_sha256=benchmark.environment_fingerprint_sha256,
        benchmark_report_sha256=benchmark.report_sha256,
        warm_p95_ms=benchmark.warm_p95_ms,
        approved_by_audit_ref=approved_by_audit_ref,
    )
    raw_receipt = repository.activate(request=request)
    receipt = _coerce_activation_receipt(raw_receipt)
    if (
        receipt.previous_generation_id != expected_current_generation_id
        or receipt.active_generation_id != plan.generation_id
        or receipt.policy_version != expected_policy_version + 1
        or receipt.generation_status != "ACTIVE"
    ):
        raise BgeFullGenerationError("ACTIVATION_DATABASE_RECEIPT")
    return receipt


class PsycopgBgeFullGenerationWriterRepository:
    """전용 writer DSN으로 exact card append와 v2 staging finalizer만 수행한다."""

    def __init__(self, *, database_dsn: str) -> None:
        self._database_dsn = _validated_dsn(database_dsn, "FULL_WRITER_DATABASE_DSN")

    def materialize(
        self,
        *,
        plan: BgeFullGenerationPlan,
        rows: tuple[BgeFullStagedEmbedding, ...],
        aggregate_row_hash: str,
        generation_vector_hash: str,
    ) -> BgeGenerationDatabaseReceipt:
        _require_offline_target()
        try:
            with psycopg.connect(
                self._database_dsn,
                autocommit=False,
                connect_timeout=2,
            ) as connection:
                _attest_writer_connection(connection)
                with connection.transaction():
                    connection.execute("set local statement_timeout = '60s'")
                    connection.execute("set local lock_timeout = '1s'")
                    connection.execute(
                        "set local idle_in_transaction_session_timeout = '75s'"
                    )
                    _insert_cards_and_chunks(connection, plan=plan)
                    _insert_generation_membership(connection, plan=plan)
                    _copy_staging_rows(connection, rows=rows)
                    finalized = _required_int(
                        connection.execute(
                            """
                            SELECT public.finalize_rag_embedding_staging_v2(
                              %s, %s, %s, %s, %s
                            )
                            """,
                            (
                                plan.generation_id,
                                plan.materialization_run_id,
                                _EXPECTED_WRITER_ROLE,
                                len(rows),
                                aggregate_row_hash,
                            ),
                        ).fetchone()
                    )
                    if finalized != len(rows):
                        raise BgeFullGenerationError("FULL_FINALIZE_COUNT")
                    updated = connection.execute(
                        """
                        UPDATE rag_corpus_generations
                        SET status = 'MATERIALIZED'
                        WHERE corpus_generation_id = %s
                          AND status = 'MATERIALIZING'
                          AND actual_chunk_count = expected_chunk_count
                        """,
                        (plan.generation_id,),
                    ).rowcount
                    if updated != 1:
                        raise BgeFullGenerationError("FULL_MATERIALIZED_TRANSITION")
                    status_row = connection.execute(
                        """
                        SELECT status, actual_chunk_count, activated_at IS NOT NULL
                        FROM rag_corpus_generations
                        WHERE corpus_generation_id = %s
                        """,
                        (plan.generation_id,),
                    ).fetchone()
                    if status_row != ("MATERIALIZED", len(rows), False):
                        raise BgeFullGenerationError("FULL_MATERIALIZED_RECEIPT")
        except BgeFullGenerationError:
            raise
        except psycopg.Error as error:
            raise BgeFullGenerationError("FULL_DATABASE_OPERATION_FAILED") from error
        return BgeGenerationDatabaseReceipt(
            generation_id=plan.generation_id,
            materialization_run_id=plan.materialization_run_id,
            final_row_count=len(rows),
            status="MATERIALIZED",
            aggregate_row_hash=aggregate_row_hash,
            generation_vector_hash=generation_vector_hash,
            active_pointer_changed=False,
        )


class PsycopgBgeFullGenerationReader:
    """raw table SELECT 없이 admin verification projection만 호출하는 independent reader."""

    def __init__(self, *, database_dsn: str) -> None:
        self._database_dsn = _validated_dsn(database_dsn, "FULL_READER_DATABASE_DSN")

    def read_embeddings(
        self,
        *,
        generation_id: str,
        expected_corpus_hash: str,
        expected_row_count: int,
    ) -> tuple[tuple[str, NDArray[np.float32], str], ...]:
        _require_offline_target()
        try:
            with psycopg.connect(
                self._database_dsn,
                autocommit=False,
                connect_timeout=2,
            ) as connection:
                _attest_admin_connection(connection)
                rows = connection.execute(
                    """
                    SELECT chunk_revision_id, embedding_text, materialization_row_hash
                    FROM public.read_rag_generation_embeddings_for_verification(
                      %s, %s, %s
                    )
                    """,
                    (generation_id, expected_corpus_hash, expected_row_count),
                ).fetchall()
        except BgeFullGenerationError:
            raise
        except psycopg.Error as error:
            raise BgeFullGenerationError("FULL_READER_DATABASE_OPERATION_FAILED") from error
        if len(rows) != expected_row_count:
            raise BgeFullGenerationError("FULL_READER_ROW_COUNT")
        return tuple(
            (
                str(row[0]),
                _parse_vector_text(str(row[1])),
                str(row[2]),
            )
            for row in rows
        )


class PsycopgBgeFullGenerationAdminRepository:
    """table DML 권한 없이 bounded activation function 한 개만 호출하는 CAS adapter."""

    def __init__(self, *, database_dsn: str) -> None:
        self._database_dsn = _validated_dsn(database_dsn, "FULL_ADMIN_DATABASE_DSN")

    def read_activation_state(self) -> tuple[str | None, int]:
        """현재 pointer/version을 bounded admin projection으로 읽어 caller CAS 입력을 만든다."""

        _require_offline_target()
        try:
            with psycopg.connect(
                self._database_dsn,
                autocommit=True,
                connect_timeout=2,
            ) as connection:
                _attest_admin_connection(connection)
                row = connection.execute(
                    """
                    SELECT active_generation_id, policy_version, policy_id, effective_profile_id
                    FROM public.read_rag_activation_state()
                    """
                ).fetchone()
        except BgeFullGenerationError:
            raise
        except psycopg.Error as error:
            raise BgeFullGenerationError("ACTIVATION_STATE_DATABASE_OPERATION_FAILED") from error
        if (
            row is None
            or type(row[1]) is not int
            or str(row[2]) != "bge_only_v1"
            or str(row[3]) != _PROFILE_ID
        ):
            raise BgeFullGenerationError("ACTIVATION_STATE_RECEIPT")
        return (None if row[0] is None else str(row[0]), row[1])

    def activate(self, *, request: BgeActivationRequest) -> BgeActivationReceipt:
        _require_offline_target()
        try:
            with psycopg.connect(
                self._database_dsn,
                autocommit=False,
                connect_timeout=2,
            ) as connection:
                _attest_admin_connection(connection)
                with connection.transaction():
                    connection.execute("set local statement_timeout = '5s'")
                    connection.execute("set local lock_timeout = '500ms'")
                    connection.execute(
                        "set local idle_in_transaction_session_timeout = '5s'"
                    )
                    row = connection.execute(
                        """
                        SELECT
                          previous_generation_id,
                          active_generation_id,
                          policy_version,
                          generation_status
                        FROM public.activate_verified_rag_generation(
                          %s::text, %s::text, %s::bigint,
                          %s::text, %s::text, %s::text, %s::text, %s::text,
                          %s::integer, %s::integer, %s::integer,
                          %s::text, %s::text, %s::text, %s::text, %s::text,
                          %s::text, %s::text, %s::text, %s::text,
                          %s::numeric, %s::text
                        )
                        """,
                        (
                            request.generation_id,
                            request.expected_current_generation_id,
                            request.expected_policy_version,
                            request.corpus_hash,
                            request.generation_hash,
                            request.membership_hash,
                            request.aggregate_row_hash,
                            request.db_vector_hash,
                            request.expected_source_revision_count,
                            request.expected_chunk_count,
                            request.batch_size,
                            request.model_revision,
                            request.model_file_manifest_hash,
                            request.tokenizer_sha256,
                            request.parser_version,
                            request.chunker_version,
                            request.input_strategy_version,
                            request.batch_benchmark_sha256,
                            request.environment_fingerprint_sha256,
                            request.benchmark_report_sha256,
                            request.warm_p95_ms,
                            request.approved_by_audit_ref,
                        ),
                    ).fetchone()
        except BgeFullGenerationError:
            raise
        except psycopg.Error as error:
            raise BgeFullGenerationError("ACTIVATION_DATABASE_OPERATION_FAILED") from error
        if row is None:
            raise BgeFullGenerationError("ACTIVATION_EMPTY_RECEIPT")
        return BgeActivationReceipt(
            previous_generation_id=None if row[0] is None else str(row[0]),
            active_generation_id=str(row[1]),
            policy_version=_required_int((row[2],)),
            generation_status=cast(Literal["ACTIVE"], str(row[3])),
        )


def _validate_corpus_binding(corpus: FrozenSourceCardCorpus) -> None:
    ordered = tuple(
        sorted(corpus.cards, key=lambda card: card.source_id.encode("utf-8"))
    )
    manifest_cards = corpus.manifest.get("cards")
    identity = {
        "schemaVersion": "1",
        "orderedCards": [
            {"sourceId": card.source_id, "cardSha256": card.card_sha256}
            for card in ordered
        ],
    }
    derived_hash = hashlib.sha256(_canonical_json_bytes(identity)).hexdigest()
    if (
        len(ordered) != _EXPECTED_CARD_COUNT
        or len({card.source_id for card in ordered}) != _EXPECTED_CARD_COUNT
        or corpus.corpus_manifest_sha256 != _CORPUS_MANIFEST_SHA256
        or corpus.manifest.get("corpusManifestSha256") != corpus.corpus_manifest_sha256
        or corpus.manifest.get("status") != "FROZEN"
        or corpus.manifest.get("projectCards") != _EXPECTED_CARD_COUNT
        or corpus.manifest.get("parserVersion") != _PARSER_VERSION
        or corpus.manifest.get("chunkerVersion") != _CHUNKER_VERSION
        or corpus.manifest.get("tokenizerSha256") != _TOKENIZER_SHA256
        or derived_hash != corpus.corpus_manifest_sha256
        or not isinstance(manifest_cards, list)
        or len(manifest_cards) != _EXPECTED_CARD_COUNT
        or any(
            not isinstance(manifest_card, dict)
            or manifest_card.get("sourceId") != card.source_id
            or manifest_card.get("cardSha256") != card.card_sha256
            or manifest_card.get("contentSha256") != card.content_sha256
            for manifest_card, card in zip(manifest_cards, ordered, strict=True)
        )
    ):
        raise BgeFullGenerationError("CORPUS_MANIFEST_BINDING")


def _validate_artifact(artifact: BgeVerifiedPacket) -> None:
    if (
        artifact.revision != _MODEL_REVISION
        or artifact.file_count != _EXPECTED_ARTIFACT_FILE_COUNT
        or artifact.total_bytes != _EXPECTED_ARTIFACT_BYTES
        or artifact.file_manifest_sha256 != _ARTIFACT_MANIFEST_SHA256
    ):
        raise BgeFullGenerationError("BGE_ARTIFACT_RECEIPT")


def _validate_batch_benchmark(receipt: BgeBatchBenchmarkReceipt) -> None:
    peaks = dict(receipt.peak_rss_bytes)
    elapsed = dict(receipt.elapsed_ms)
    if (
        receipt.candidates != (16, 32, 64)
        or receipt.selected_batch_size not in receipt.candidates
        or set(peaks) != set(receipt.candidates)
        or set(elapsed) != set(receipt.candidates)
        or any(value <= 0 for value in peaks.values())
        or any(not math.isfinite(value) or value <= 0 for value in elapsed.values())
        or not _is_sha256(receipt.environment_fingerprint_sha256)
        or not _is_sha256(receipt.benchmark_sha256)
    ):
        raise BgeFullGenerationError("BATCH_BENCHMARK")


def _membership_hash(items: Sequence[BgeFullGenerationItem]) -> str:
    row_hashes = []
    for ordinal, item in enumerate(items, start=1):
        row = "\n".join(
            (
                item.card.source_id,
                item.source_revision_id,
                item.chunk.chunk_revision_id,
                item.chunk.content_hash,
                item.embedding_input.embedding_input_hash,
                str(ordinal),
            )
        )
        row_hashes.append(hashlib.sha256(row.encode("utf-8")).hexdigest())
    return hashlib.sha256("".join(row_hashes).encode("ascii")).hexdigest()


def _build_staging_row(
    *,
    plan: BgeFullGenerationPlan,
    item: BgeFullGenerationItem,
    embedding: NDArray[np.float32],
) -> BgeFullStagedEmbedding:
    canonical_vector = cast(
        NDArray[np.float32],
        np.asarray(embedding, dtype="<f4"),
    )
    digest = hashlib.sha256()
    for value in (
        plan.generation_id,
        plan.materialization_run_id,
        item.chunk.chunk_revision_id,
        plan.embedding_profile_id,
        item.embedding_input.embedding_input_hash,
        item.embedding_input.context_set_hash or "",
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    digest.update(canonical_vector.tobytes(order="C"))
    return BgeFullStagedEmbedding(
        generation_id=plan.generation_id,
        materialization_run_id=plan.materialization_run_id,
        chunk_revision_id=item.chunk.chunk_revision_id,
        embedding_profile_id=plan.embedding_profile_id,
        embedding_input_hash=item.embedding_input.embedding_input_hash,
        context_set_hash=item.embedding_input.context_set_hash,
        embedding=canonical_vector,
        staging_row_hash=digest.hexdigest(),
    )


def _aggregate_row_hash(rows: Sequence[BgeFullStagedEmbedding]) -> str:
    return hashlib.sha256(
        "".join(
            row.staging_row_hash
            for row in sorted(rows, key=lambda value: value.chunk_revision_id.encode("utf-8"))
        ).encode("ascii")
    ).hexdigest()


def _generation_vector_hash(
    rows: Sequence[tuple[str, NDArray[np.float32]]] | Any,
) -> str:
    ordered = sorted(tuple(rows), key=lambda value: value[0].encode("utf-8"))
    digest = hashlib.sha256()
    for chunk_id, vector in ordered:
        digest.update(chunk_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(np.asarray(vector, dtype="<f4").tobytes(order="C"))
    return digest.hexdigest()


def _insert_cards_and_chunks(
    connection: psycopg.Connection[Any],
    *,
    plan: BgeFullGenerationPlan,
) -> None:
    for item in plan.items:
        card = item.card
        payload = card.front_matter
        canonical_url = _required_mapping_text(payload, "canonicalUrl")
        locator = urlsplit(canonical_url)
        if (
            locator.scheme != "https"
            or not locator.hostname
            or not locator.path
            or locator.username is not None
            or locator.password is not None
            or locator.fragment
        ):
            raise BgeFullGenerationError("FULL_CARD_LOCATOR")
        allowed_origin = f"https://{locator.hostname.lower()}"
        if locator.port not in (None, 443):
            allowed_origin += f":{locator.port}"

        connection.execute(
            """
            INSERT INTO rag_sources (
              source_id, source_type, institution, topic, owner_identity
            )
            VALUES (%s, 'PROJECT_SOURCE_CARD', %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                card.source_id,
                _required_mapping_text(payload, "institution"),
                _required_mapping_text(payload, "topic"),
                _required_mapping_text(payload, "retentionOwner"),
            ),
        )
        source_row = connection.execute(
            """
            SELECT source_type, institution, topic, owner_identity, retired_at IS NULL
            FROM rag_sources
            WHERE source_id = %s
            """,
            (card.source_id,),
        ).fetchone()
        if source_row != (
            "PROJECT_SOURCE_CARD",
            _required_mapping_text(payload, "institution"),
            _required_mapping_text(payload, "topic"),
            _required_mapping_text(payload, "retentionOwner"),
            True,
        ):
            raise BgeFullGenerationError("FULL_SOURCE_IDENTITY_DRIFT")

        existing_revision = connection.execute(
            """
            SELECT source_id, metadata_hash
            FROM rag_source_revisions
            WHERE source_revision_id = %s
            """,
            (item.source_revision_id,),
        ).fetchone()
        if existing_revision is None:
            next_revision = _required_int(
                connection.execute(
                    """
                    SELECT coalesce(max(revision_seq), 0)::integer + 1
                    FROM rag_source_revisions
                    WHERE source_id = %s
                    """,
                    (card.source_id,),
                ).fetchone()
            )
            connection.execute(
                """
                INSERT INTO rag_source_revisions (
                  source_revision_id, source_id, revision_seq, registry_version,
                  title, tier, access_level, license_decision, license_note, attribution,
                  retention_mode, retention_days, retention_owner, external_processing_allowed,
                  initial_processing, canonical_url, allowed_origin, allowed_path,
                  locator_sha256, metadata_hash
                )
                VALUES (
                  %s, %s, %s, 's4-7b-source-card-v2',
                  %s, 'PROJECT', 'PUBLIC', 'PROJECT_AUTHORED_PUBLIC', %s, %s,
                  'PROJECT_CARD', %s, %s, false,
                  'PROJECT_AUTHORED_CARD', %s, %s, %s,
                  %s, %s
                )
                """,
                (
                    item.source_revision_id,
                    card.source_id,
                    next_revision,
                    _required_mapping_text(payload, "title"),
                    _required_mapping_text(payload, "licenseNote"),
                    _required_mapping_text(payload, "attribution"),
                    _required_mapping_int(payload, "retentionDays"),
                    _required_mapping_text(payload, "retentionOwner"),
                    canonical_url,
                    allowed_origin,
                    locator.path,
                    _required_mapping_text(payload, "canonicalUrlSha256"),
                    card.card_sha256,
                ),
            )
        elif existing_revision != (card.source_id, card.card_sha256):
            raise BgeFullGenerationError("FULL_SOURCE_REVISION_DRIFT")

        verified_at_text = _required_mapping_text(payload, "verifiedAt")
        try:
            verified_at = datetime.fromisoformat(
                verified_at_text.replace("Z", "+00:00")
            )
        except ValueError as error:
            raise BgeFullGenerationError("FULL_CARD_VERIFIEDAT") from error
        registration = connection.execute(
            """
            SELECT public.register_rag_verified_source_card(
              %s, %s, %s, %s, %s, %s
            )
            """,
            (
                item.source_revision_id,
                card.source_id,
                card.card_id,
                card.card_sha256,
                verified_at,
                list(PUBLIC_TOPICS_BY_SOURCE_ID[card.source_id]),
            ),
        ).fetchone()
        if (
            registration is None
            or len(registration) != 1
            or type(registration[0]) is not int
            or registration[0] not in (0, 1)
        ):
            raise BgeFullGenerationError("FULL_SOURCE_VERIFICATION_RECEIPT")

        existing_ingest = connection.execute(
            """
            SELECT source_revision_id, status, expected_chunk_count, actual_chunk_count
            FROM rag_ingest_runs
            WHERE ingest_run_id = %s
            """,
            (item.ingest_run_id,),
        ).fetchone()
        if existing_ingest is None:
            connection.execute(
                """
                INSERT INTO rag_ingest_runs (
                  ingest_run_id, source_revision_id, parser_version, canonicalizer_version,
                  card_schema_version, input_content_hash, status, expected_chunk_count
                )
                VALUES (%s, %s, %s, %s, 'rag-source-card-v2', %s, 'PLANNED', 1)
                """,
                (
                    item.ingest_run_id,
                    item.source_revision_id,
                    plan.parser_version,
                    plan.canonicalizer_version,
                    card.content_sha256,
                ),
            )
            connection.execute(
                """
                UPDATE rag_ingest_runs
                SET status = 'RUNNING', started_at = transaction_timestamp()
                WHERE ingest_run_id = %s AND status = 'PLANNED'
                """,
                (item.ingest_run_id,),
            )
            connection.execute(
                """
                INSERT INTO rag_chunk_revisions (
                  chunk_revision_id, ingest_run_id, source_revision_id, chunk_seq,
                  heading_path, canonical_content, canonical_content_hash, token_count,
                  topic, access_level, tier
                )
                VALUES (%s, %s, %s, 1, %s, %s, %s, %s, %s, 'PUBLIC', 'PROJECT')
                """,
                (
                    item.chunk.chunk_revision_id,
                    item.ingest_run_id,
                    item.source_revision_id,
                    list(item.chunk.heading_path),
                    item.chunk.text,
                    item.chunk.content_hash,
                    item.chunk.token_count,
                    _required_mapping_text(payload, "topic"),
                ),
            )
            connection.execute(
                """
                UPDATE rag_ingest_runs
                SET status = 'SUCCEEDED',
                    actual_chunk_count = 1,
                    completed_at = transaction_timestamp()
                WHERE ingest_run_id = %s AND status = 'RUNNING'
                """,
                (item.ingest_run_id,),
            )
        elif existing_ingest != (item.source_revision_id, "SUCCEEDED", 1, 1):
            raise BgeFullGenerationError("FULL_INGEST_IDENTITY_DRIFT")

        chunk_row = connection.execute(
            """
            SELECT ingest_run_id, source_revision_id, chunk_seq,
                   canonical_content_hash, token_count
            FROM rag_chunk_revisions
            WHERE chunk_revision_id = %s
            """,
            (item.chunk.chunk_revision_id,),
        ).fetchone()
        if chunk_row != (
            item.ingest_run_id,
            item.source_revision_id,
            1,
            item.chunk.content_hash,
            item.chunk.token_count,
        ):
            raise BgeFullGenerationError("FULL_CHUNK_IDENTITY_DRIFT")


def _insert_generation_membership(
    connection: psycopg.Connection[Any],
    *,
    plan: BgeFullGenerationPlan,
) -> None:
    connection.execute(
        """
        INSERT INTO rag_corpus_generations (
          corpus_generation_id, corpus_hash, embedding_profile_id, vector_space,
          status, expected_chunk_count
        )
        VALUES (%s, %s, %s, %s, 'REGISTERED', %s)
        """,
        (
            plan.generation_id,
            plan.corpus_hash,
            plan.embedding_profile_id,
            plan.embedding_profile_id,
            len(plan.items),
        ),
    )
    if (
        connection.execute(
            """
            UPDATE rag_corpus_generations
            SET status = 'PLANNED'
            WHERE corpus_generation_id = %s AND status = 'REGISTERED'
            """,
            (plan.generation_id,),
        ).rowcount
        != 1
    ):
        raise BgeFullGenerationError("FULL_GENERATION_PLANNED_TRANSITION")
    if (
        connection.execute(
            """
            UPDATE rag_corpus_generations
            SET status = 'MATERIALIZING'
            WHERE corpus_generation_id = %s AND status = 'PLANNED'
            """,
            (plan.generation_id,),
        ).rowcount
        != 1
    ):
        raise BgeFullGenerationError("FULL_GENERATION_BUILD_TRANSITION")
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO rag_generation_chunks (
              corpus_generation_id, chunk_revision_id, embedding_profile_id,
              embedding_input_hash, context_set_hash, ordinal
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    plan.generation_id,
                    item.chunk.chunk_revision_id,
                    plan.embedding_profile_id,
                    item.embedding_input.embedding_input_hash,
                    item.embedding_input.context_set_hash,
                    ordinal,
                )
                for ordinal, item in enumerate(plan.items, start=1)
            ],
        )


def _copy_staging_rows(
    connection: psycopg.Connection[Any],
    *,
    rows: tuple[BgeFullStagedEmbedding, ...],
) -> None:
    with connection.cursor() as cursor:
        with cursor.copy(
            """
            COPY rag_embedding_staging (
              generation_id, materialization_run_id, chunk_revision_id,
              embedding_profile_id, embedding_input_hash, context_set_hash,
              embedding, staging_row_hash
            ) FROM STDIN
            """
        ) as copy:
            for row in rows:
                copy.write_row(
                    (
                        row.generation_id,
                        row.materialization_run_id,
                        row.chunk_revision_id,
                        row.embedding_profile_id,
                        row.embedding_input_hash,
                        row.context_set_hash,
                        _vector_text(row.embedding),
                        row.staging_row_hash,
                    )
                )


def _attest_writer_connection(connection: psycopg.Connection[Any]) -> None:
    user = connection.execute("SELECT current_user").fetchone()
    if user != (_EXPECTED_WRITER_ROLE,):
        raise BgeFullGenerationError("FULL_DATABASE_WRITER_ROLE")
    for table in (
        "rag_sources",
        "rag_source_revisions",
        "rag_ingest_runs",
        "rag_chunk_revisions",
        "rag_corpus_generations",
        "rag_generation_chunks",
        "rag_embedding_staging",
    ):
        if not _has_table_privilege(connection, table, "INSERT"):
            raise BgeFullGenerationError("FULL_DATABASE_WRITER_PRIVILEGE")
    for table in (
        "rag_chunk_embeddings",
        "rag_embedding_policy_state",
        "rag_generation_attestations",
        "rag_source_card_verifications",
        "rag_source_public_topics",
        "rag_retrieval_scope_claims",
    ):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            if _has_table_privilege(connection, table, privilege):
                raise BgeFullGenerationError("FULL_DATABASE_FORBIDDEN_WRITER_PRIVILEGE")
    if not _has_function_privilege(
        connection,
        "public.finalize_rag_embedding_staging_v2(text,text,text,integer,text)",
    ):
        raise BgeFullGenerationError("FULL_DATABASE_FINALIZER_PRIVILEGE")
    if not _has_function_privilege(
        connection,
        (
            "public.register_rag_verified_source_card("
            "text,text,text,text,timestamp with time zone,text[])"
        ),
    ):
        raise BgeFullGenerationError("FULL_DATABASE_VERIFICATION_FUNCTION_PRIVILEGE")
    if _has_function_privilege(
        connection,
        (
            "public.activate_verified_rag_generation("
            "text,text,bigint,text,text,text,text,text,integer,integer,integer,"
            "text,text,text,text,text,text,text,text,text,numeric,text)"
        ),
    ):
        raise BgeFullGenerationError("FULL_DATABASE_FORBIDDEN_ACTIVATION_PRIVILEGE")


def _attest_admin_connection(connection: psycopg.Connection[Any]) -> None:
    user = connection.execute("SELECT current_user").fetchone()
    if user != (_EXPECTED_ADMIN_ROLE,):
        raise BgeFullGenerationError("FULL_DATABASE_ADMIN_ROLE")
    for table in (
        "rag_sources",
        "rag_source_revisions",
        "rag_ingest_runs",
        "rag_chunk_revisions",
        "rag_corpus_generations",
        "rag_generation_chunks",
        "rag_chunk_embeddings",
        "rag_embedding_staging",
        "rag_embedding_policy_state",
        "rag_embedding_policy_transitions",
        "rag_generation_attestations",
    ):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            if _has_table_privilege(connection, table, privilege):
                raise BgeFullGenerationError("FULL_DATABASE_FORBIDDEN_ADMIN_PRIVILEGE")
    signatures = (
        "public.read_rag_activation_state()",
        "public.read_rag_generation_embeddings_for_verification(text,text,integer)",
        (
            "public.activate_verified_rag_generation("
            "text,text,bigint,text,text,text,text,text,integer,integer,integer,"
            "text,text,text,text,text,text,text,text,text,numeric,text)"
        ),
    )
    if not all(_has_function_privilege(connection, signature) for signature in signatures):
        raise BgeFullGenerationError("FULL_DATABASE_ADMIN_FUNCTION_PRIVILEGE")


def _has_table_privilege(
    connection: psycopg.Connection[Any],
    table: str,
    privilege: str,
) -> bool:
    row = connection.execute(
        "SELECT has_table_privilege(current_user, %s, %s)",
        (f"public.{table}", privilege),
    ).fetchone()
    return row is not None and row[0] is True


def _has_function_privilege(
    connection: psycopg.Connection[Any],
    signature: str,
) -> bool:
    row = connection.execute(
        "SELECT has_function_privilege(current_user, %s, 'EXECUTE')",
        (signature,),
    ).fetchone()
    return row is not None and row[0] is True


def _parse_vector_text(value: str) -> NDArray[np.float32]:
    if (
        len(value) > 32_768
        or not value.startswith("[")
        or not value.endswith("]")
    ):
        raise BgeFullGenerationError("FULL_READER_VECTOR_TEXT")
    parts = value[1:-1].split(",")
    if len(parts) != 1024:
        raise BgeFullGenerationError("FULL_READER_VECTOR_DIMENSION")
    try:
        result = np.asarray([float(part) for part in parts], dtype=np.float32)
    except ValueError as error:
        raise BgeFullGenerationError("FULL_READER_VECTOR_NUMBER") from error
    if not np.isfinite(result).all():
        raise BgeFullGenerationError("FULL_READER_VECTOR_FINITE")
    return cast(NDArray[np.float32], result)


def _coerce_activation_receipt(value: object) -> BgeActivationReceipt:
    if isinstance(value, BgeActivationReceipt):
        return value
    if not isinstance(value, Mapping):
        raise BgeFullGenerationError("ACTIVATION_RECEIPT_TYPE")
    previous = value.get("previousGenerationId")
    active = value.get("activeGenerationId")
    version = value.get("policyVersion")
    status = value.get("generationStatus")
    if (
        previous is not None
        and not isinstance(previous, str)
        or not isinstance(active, str)
        or type(version) is not int
        or status != "ACTIVE"
    ):
        raise BgeFullGenerationError("ACTIVATION_RECEIPT_SHAPE")
    return BgeActivationReceipt(
        previous_generation_id=previous,
        active_generation_id=active,
        policy_version=version,
        generation_status="ACTIVE",
    )


def _vector_text(embedding: NDArray[np.float32]) -> str:
    return "[" + ",".join(format(float(value), ".9g") for value in embedding) + "]"


def _validated_dsn(value: str, marker: str) -> str:
    if not value or len(value) > 4_096:
        raise BgeFullGenerationError(marker)
    return value


def _require_offline_target() -> None:
    target = os.environ.get(_TARGET_ENV, "").strip().lower()
    if target not in _ALLOWED_TARGETS:
        raise BgeFullGenerationError(f"{_TARGET_ENV}_INVALID")


def _required_mapping_text(mapping: Mapping[str, Any], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise BgeFullGenerationError(f"FULL_CARD_{field.upper()}")
    return value


def _required_mapping_int(mapping: Mapping[str, Any], field: str) -> int:
    value = mapping.get(field)
    if type(value) is not int:
        raise BgeFullGenerationError(f"FULL_CARD_{field.upper()}")
    return value


def _required_int(row: tuple[object, ...] | None) -> int:
    if row is None or len(row) != 1 or type(row[0]) is not int:
        raise BgeFullGenerationError("FULL_DATABASE_INTEGER_RECEIPT")
    return row[0]


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_json_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value) + b"\n").hexdigest()


def _hash_parts(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return bool(_SHA256_PATTERN.fullmatch(value))
