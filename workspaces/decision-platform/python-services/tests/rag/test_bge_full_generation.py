from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

import numpy as np
import psycopg
import pytest
from numpy.typing import NDArray

from app.rag.bge_artifact import BgeVerifiedPacket
from app.rag.bge_full_generation import (
    BgeActivationRequest,
    BgeBatchBenchmarkReceipt,
    BgeFullGenerationError,
    BgeGenerationBenchmarkReceipt,
    BgeGenerationDatabaseReceipt,
    PsycopgBgeFullGenerationAdminRepository,
    PsycopgBgeFullGenerationReader,
    PsycopgBgeFullGenerationWriterRepository,
    activate_bge_full_generation,
    execute_bge_full_generation,
    prepare_bge_full_generation,
    verify_bge_full_generation_parity,
)
from app.rag.bge_full_generation_benchmark_cli import batch_receipt_from_report
from app.rag.source_card_corpus import (
    REPO_ROOT,
    FrozenSourceCardCorpus,
    load_frozen_source_card_corpus,
)

_BATCH_REPORT_PATH = (
    REPO_ROOT / "capstone-rag/reports/s4-2b-batch-memory-benchmark.v1.json"
)
_FINAL_REPORT_PATH = (
    REPO_ROOT / "capstone-rag/reports/s4-2b-full-generation-benchmark.v1.json"
)


class _WhitespaceTokenizer:
    def token_spans(self, text: str) -> tuple[tuple[int, int], ...]:
        spans: list[tuple[int, int]] = []
        cursor = 0
        for token in text.split():
            start = text.index(token, cursor)
            end = start + len(token)
            spans.append((start, end))
            cursor = end
        return tuple(spans)


class _FixtureEmbedder:
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def embed(self, texts: tuple[str, ...]) -> NDArray[np.float32]:
        self.batch_sizes.append(len(texts))
        rows: list[NDArray[np.float32]] = []
        for text in texts:
            seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
            vector = np.zeros(1024, dtype=np.float32)
            vector[seed % 1024] = np.float32(1.0)
            rows.append(vector)
        return np.stack(rows)


class _RecordingWriter:
    def __init__(self) -> None:
        self.rows: tuple[Any, ...] = ()

    def materialize(
        self,
        *,
        plan: Any,
        rows: tuple[Any, ...],
        aggregate_row_hash: str,
        generation_vector_hash: str,
    ) -> BgeGenerationDatabaseReceipt:
        self.rows = rows
        return BgeGenerationDatabaseReceipt(
            generation_id=plan.generation_id,
            materialization_run_id=plan.materialization_run_id,
            final_row_count=len(rows),
            status="MATERIALIZED",
            aggregate_row_hash=aggregate_row_hash,
            generation_vector_hash=generation_vector_hash,
            active_pointer_changed=False,
        )


class _RecordingReader:
    def __init__(self, rows: tuple[Any, ...]) -> None:
        self._rows = rows

    def read_embeddings(
        self,
        *,
        generation_id: str,
        expected_corpus_hash: str,
        expected_row_count: int,
    ) -> tuple[tuple[str, NDArray[np.float32], str], ...]:
        assert generation_id
        assert expected_corpus_hash
        assert expected_row_count == len(self._rows)
        return tuple(
            (
                row.chunk_revision_id,
                np.asarray(row.embedding, dtype=np.float32),
                row.staging_row_hash,
            )
            for row in reversed(self._rows)
        )


class _RecordingAdmin:
    def __init__(self) -> None:
        self.request: BgeActivationRequest | None = None

    def activate(self, *, request: BgeActivationRequest) -> Any:
        self.request = request
        return {
            "previousGenerationId": request.expected_current_generation_id,
            "activeGenerationId": request.generation_id,
            "policyVersion": request.expected_policy_version + 1,
            "generationStatus": "ACTIVE",
        }


def test_prepare_full_generation_binds_exact_30_manifest_and_batch_identity() -> None:
    corpus = load_frozen_source_card_corpus()
    first = prepare_bge_full_generation(
        corpus=corpus,
        tokenizer=_WhitespaceTokenizer(),
        artifact=_artifact_receipt(),
        batch_benchmark=_batch_benchmark(selected=16),
    )
    second = prepare_bge_full_generation(
        corpus=corpus,
        tokenizer=_WhitespaceTokenizer(),
        artifact=_artifact_receipt(),
        batch_benchmark=_batch_benchmark(selected=32),
    )

    assert len(first.items) == 30
    assert {len(item.embedding_input.text) > 0 for item in first.items} == {True}
    assert {item.chunk.sequence for item in first.items} == {1}
    assert first.corpus_hash == corpus.corpus_manifest_sha256
    assert first.batch_size == 16
    assert first.generation_hash != second.generation_hash
    assert first.generation_id != second.generation_id
    assert [item.card.source_id for item in first.items] == sorted(
        (card.source_id for card in corpus.cards),
        key=lambda value: value.encode("utf-8"),
    )


def test_tracked_batch_memory_report_is_hash_bound_and_selects_32() -> None:
    report = json.loads(_BATCH_REPORT_PATH.read_text(encoding="utf-8"))
    receipt = batch_receipt_from_report(report)

    assert receipt.selected_batch_size == 32
    assert receipt.candidates == (16, 32, 64)
    assert receipt.environment_fingerprint_sha256 == (
        "7f5821b582e14ff0d5671381d48492ad0cee705e0341dbfb72d5ba8689a8412d"
    )
    assert receipt.benchmark_sha256 == (
        "9aa704533622c4014a463706e85084ac2aab0ab6b1e578a445fff0376e9296c0"
    )


def test_tracked_final_report_is_hash_bound_to_active_exact_30_generation() -> None:
    report = json.loads(_FINAL_REPORT_PATH.read_text(encoding="utf-8"))
    expected_report_hash = report.pop("benchmarkReportSha256")
    actual_report_hash = hashlib.sha256(
        (
            json.dumps(
                report,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()

    assert actual_report_hash == expected_report_hash
    assert report["commitSha"] == "418858b97f597a96cab85a0781a5961de6efc8f2"
    assert report["status"] == "PASS"
    assert report["parity"]["rowCount"] == 30
    assert report["expectedTop5HitRate"] == 1.0
    assert report["stagesMs"]["total"]["p95"] <= 1500.0
    assert report["activePointerBefore"]["generationId"] is None
    assert report["activePointerAfter"]["generationId"] == report["generationId"]
    assert report["physicalCalls"]["providerTotal"] == 0


def test_prepare_full_generation_rejects_manifest_or_batch_drift() -> None:
    corpus = load_frozen_source_card_corpus()
    drifted = FrozenSourceCardCorpus(
        cards=corpus.cards,
        manifest=corpus.manifest,
        corpus_manifest_sha256="0" * 64,
    )
    with pytest.raises(BgeFullGenerationError, match="CORPUS_MANIFEST_BINDING"):
        prepare_bge_full_generation(
            corpus=drifted,
            tokenizer=_WhitespaceTokenizer(),
            artifact=_artifact_receipt(),
            batch_benchmark=_batch_benchmark(selected=16),
        )
    with pytest.raises(BgeFullGenerationError, match="BATCH_BENCHMARK"):
        prepare_bge_full_generation(
            corpus=corpus,
            tokenizer=_WhitespaceTokenizer(),
            artifact=_artifact_receipt(),
            batch_benchmark=replace(
                _batch_benchmark(selected=16),
                selected_batch_size=8,
            ),
        )


def test_execute_and_independent_reader_preserve_deterministic_batches_and_vectors() -> None:
    plan = prepare_bge_full_generation(
        corpus=load_frozen_source_card_corpus(),
        tokenizer=_WhitespaceTokenizer(),
        artifact=_artifact_receipt(),
        batch_benchmark=_batch_benchmark(selected=16),
    )
    embedder = _FixtureEmbedder()
    writer = _RecordingWriter()

    materialized = execute_bge_full_generation(
        plan=plan,
        embedder=embedder,
        repository=writer,
    )
    parity = verify_bge_full_generation_parity(
        materialized=materialized,
        reader=_RecordingReader(writer.rows),
    )

    assert embedder.batch_sizes == [16, 14]
    assert materialized.database_receipt.status == "MATERIALIZED"
    assert materialized.database_receipt.active_pointer_changed is False
    assert parity.row_count == 30
    assert parity.max_absolute_error == 0.0
    assert parity.minimum_cosine_similarity == 1.0
    assert parity.generation_vector_hash == materialized.generation_vector_hash


def test_activation_requires_parity_and_final_benchmark_before_admin_call() -> None:
    plan = prepare_bge_full_generation(
        corpus=load_frozen_source_card_corpus(),
        tokenizer=_WhitespaceTokenizer(),
        artifact=_artifact_receipt(),
        batch_benchmark=_batch_benchmark(selected=16),
    )
    embedder = _FixtureEmbedder()
    writer = _RecordingWriter()
    materialized = execute_bge_full_generation(
        plan=plan,
        embedder=embedder,
        repository=writer,
    )
    parity = verify_bge_full_generation_parity(
        materialized=materialized,
        reader=_RecordingReader(writer.rows),
    )
    admin = _RecordingAdmin()

    with pytest.raises(BgeFullGenerationError, match="FINAL_BENCHMARK"):
        activate_bge_full_generation(
            materialized=materialized,
            parity=parity,
            benchmark=_final_benchmark(p95_ms=1500.001),
            expected_current_generation_id=None,
            expected_policy_version=1,
            approved_by_audit_ref="s4-2b-test-approval-0001",
            repository=admin,
        )
    assert admin.request is None

    receipt = activate_bge_full_generation(
        materialized=materialized,
        parity=parity,
        benchmark=_final_benchmark(p95_ms=140.0),
        expected_current_generation_id=None,
        expected_policy_version=1,
        approved_by_audit_ref="s4-2b-test-approval-0001",
        repository=admin,
    )

    assert receipt.active_generation_id == plan.generation_id
    assert receipt.policy_version == 2
    assert receipt.generation_status == "ACTIVE"
    assert admin.request is not None
    assert admin.request.corpus_hash == plan.corpus_hash
    assert admin.request.expected_source_revision_count == 30
    assert admin.request.expected_chunk_count == 30


def test_postgres_full_generation_uses_writer_reader_and_admin_boundaries(
    isolated_postgres_cluster: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    postgres_cluster = isolated_postgres_cluster
    plan = prepare_bge_full_generation(
        corpus=load_frozen_source_card_corpus(),
        tokenizer=_WhitespaceTokenizer(),
        artifact=_artifact_receipt(),
        batch_benchmark=_batch_benchmark(selected=16),
    )
    monkeypatch.setenv("RAG_SOURCE_REGISTER_TARGET", "testcontainers")
    materialized = execute_bge_full_generation(
        plan=plan,
        embedder=_FixtureEmbedder(),
        repository=PsycopgBgeFullGenerationWriterRepository(
            database_dsn=postgres_cluster["rag_writer_dsn"],
        ),
    )
    parity = verify_bge_full_generation_parity(
        materialized=materialized,
        reader=PsycopgBgeFullGenerationReader(
            database_dsn=postgres_cluster["rag_admin_dsn"],
        ),
    )
    receipt = activate_bge_full_generation(
        materialized=materialized,
        parity=parity,
        benchmark=_final_benchmark(p95_ms=140.0),
        expected_current_generation_id=None,
        expected_policy_version=1,
        approved_by_audit_ref="s4-2b-test-approval-0001",
        repository=PsycopgBgeFullGenerationAdminRepository(
            database_dsn=postgres_cluster["rag_admin_dsn"],
        ),
    )

    assert receipt.generation_status == "ACTIVE"
    assert receipt.active_generation_id == plan.generation_id
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            """
            SELECT generation.status, generation.actual_chunk_count,
                   state.active_generation_id, state.version,
                   attestation.source_revision_count, attestation.chunk_count
            FROM rag_corpus_generations AS generation
            JOIN rag_embedding_policy_state AS state
              ON state.state_id = 'default'
            JOIN rag_generation_attestations AS attestation
              ON attestation.corpus_generation_id = generation.corpus_generation_id
            WHERE generation.corpus_generation_id = %s
            """,
            (plan.generation_id,),
        ).fetchone() == ("ACTIVE", 30, plan.generation_id, 2, 30, 30)

    with psycopg.connect(postgres_cluster["rag_writer_dsn"]) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                """
                SELECT * FROM activate_verified_rag_generation(
                  NULL::text, NULL::text, 1::bigint,
                  NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
                  30::integer, 30::integer, 16::integer,
                  NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
                  NULL::text, NULL::text, NULL::text, NULL::text,
                  1::numeric, NULL::text
                )
                """
            ).fetchone()


def _artifact_receipt() -> BgeVerifiedPacket:
    return BgeVerifiedPacket(
        revision="5617a9f61b028005a4858fdac845db406aefb181",
        file_count=10,
        total_bytes=2_289_781_803,
        file_manifest_sha256=(
            "a0ae6372b2d735b593d806d24c1155cb48dd7188adebe7d6b7619a1622fb71aa"
        ),
    )


def _batch_benchmark(*, selected: int) -> BgeBatchBenchmarkReceipt:
    return BgeBatchBenchmarkReceipt(
        selected_batch_size=selected,
        candidates=(16, 32, 64),
        peak_rss_bytes=((16, 4_000_000_000), (32, 5_000_000_000), (64, 7_000_000_000)),
        elapsed_ms=((16, 1_000.0), (32, 900.0), (64, 850.0)),
        environment_fingerprint_sha256="1" * 64,
        benchmark_sha256="2" * 64,
    )


def _final_benchmark(*, p95_ms: float) -> BgeGenerationBenchmarkReceipt:
    return BgeGenerationBenchmarkReceipt(
        report_sha256="3" * 64,
        query_set_sha256="4" * 64,
        environment_fingerprint_sha256="1" * 64,
        warmup_count=20,
        measured_count=100,
        warm_p95_ms=p95_ms,
        provider_physical_calls=0,
        voyage_physical_calls=0,
        gemini_physical_calls=0,
        openai_physical_calls=0,
        passed=p95_ms <= 1500.0,
    )
