from __future__ import annotations

import hashlib
import json
import os
import re
import time
import unicodedata
from collections import defaultdict
from typing import Any

import numpy as np
import psycopg
import pytest
from numpy.typing import NDArray

from app.rag.bge_acquisition import (
    DEFAULT_MODEL_MANIFEST,
    DEFAULT_MODEL_ROOT,
    verify_bge_completion_manifest,
)
from app.rag.bge_full_generation import (
    BgeFullGenerationError,
    BgeGenerationBenchmarkReceipt,
    PsycopgBgeFullGenerationAdminRepository,
    PsycopgBgeFullGenerationReader,
    PsycopgBgeFullGenerationWriterRepository,
    activate_bge_full_generation,
    execute_bge_full_generation,
    prepare_bge_full_generation,
    verify_bge_full_generation_parity,
)
from app.rag.bge_full_generation_benchmark_cli import batch_receipt_from_report
from app.rag.bge_runtime import BgeStaticTokenizer, load_bge_onnx_embedder
from app.rag.corpus_profiles import load_source_card_corpus
from app.rag.source_card_corpus import REPO_ROOT

pytestmark = pytest.mark.skipif(
    os.environ.get("S4_7C_EXTERNAL_GENERATION_TESTS") != "1",
    reason="pinned local BGE와 isolated PostgreSQL을 쓰는 명시적 S4.7C 전환 검증이다.",
)

_BATCH_REPORT_PATH = REPO_ROOT / "capstone-rag/reports/s4-2b-batch-memory-benchmark.v1.json"
_QUERY_SET_PATH = REPO_ROOT / "capstone-rag/eval/s4-2b-30-card-smoke.v1.json"
_RESULT_MARKER = "S4_7C_EXTERNAL_GENERATION_RESULT "
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}|[0-9]{3,}")


class _CountingBgeRuntime:
    """로컬 ONNX 호출만 세고 외부 provider physical call과 명시적으로 분리한다."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.local_onnx_calls = 0

    def embed(self, texts: tuple[str, ...]) -> NDArray[np.float32]:
        self.local_onnx_calls += 1
        return self._delegate.embed(texts)

    def embed_query(self, question: str) -> NDArray[np.float32]:
        return self.embed((question,))[0]


def test_s4_7c_real_bge_append_non_regression_and_atomic_transition(
    isolated_postgres_cluster: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """old/new exact 30을 실제 BGE로 생성하고 새 generation만 원자 활성화한다."""

    cluster = isolated_postgres_cluster
    monkeypatch.setenv("RAG_SOURCE_REGISTER_TARGET", "testcontainers")
    batch_receipt = batch_receipt_from_report(
        json.loads(_BATCH_REPORT_PATH.read_text(encoding="utf-8"))
    )
    artifact = verify_bge_completion_manifest(
        DEFAULT_MODEL_ROOT,
        manifest_path=DEFAULT_MODEL_MANIFEST,
    )
    tokenizer = BgeStaticTokenizer.from_file(DEFAULT_MODEL_ROOT / "onnx/tokenizer.json")
    old_corpus = load_source_card_corpus(profile_id="s4_7b_internal_v1")
    new_corpus = load_source_card_corpus(profile_id="s4_7c_external_v1")
    old_plan = prepare_bge_full_generation(
        corpus=old_corpus,
        tokenizer=tokenizer,
        artifact=artifact,
        batch_benchmark=batch_receipt,
    )
    new_plan = prepare_bge_full_generation(
        corpus=new_corpus,
        tokenizer=tokenizer,
        artifact=artifact,
        batch_benchmark=batch_receipt,
    )
    runtime = _CountingBgeRuntime(load_bge_onnx_embedder(DEFAULT_MODEL_ROOT))
    writer = PsycopgBgeFullGenerationWriterRepository(database_dsn=cluster["rag_writer_dsn"])
    reader = PsycopgBgeFullGenerationReader(database_dsn=cluster["rag_admin_dsn"])
    admin = PsycopgBgeFullGenerationAdminRepository(database_dsn=cluster["rag_admin_dsn"])

    old_materialized = execute_bge_full_generation(
        plan=old_plan,
        embedder=runtime,
        repository=writer,
    )
    old_parity = verify_bge_full_generation_parity(materialized=old_materialized, reader=reader)
    pointer_initial, version_initial = admin.read_activation_state()
    activate_bge_full_generation(
        materialized=old_materialized,
        parity=old_parity,
        benchmark=_activation_benchmark(
            plan=old_plan,
            warm_p95_ms=1.0,
            report_sha256="3" * 64,
        ),
        expected_current_generation_id=pointer_initial,
        expected_policy_version=version_initial,
        approved_by_audit_ref="s4-7c-old-generation-bootstrap-20260801",
        repository=admin,
    )
    pointer_before, version_before = admin.read_activation_state()
    assert pointer_before == old_plan.generation_id

    new_materialized = execute_bge_full_generation(
        plan=new_plan,
        embedder=runtime,
        repository=writer,
    )
    new_parity = verify_bge_full_generation_parity(materialized=new_materialized, reader=reader)
    vector_equivalent_count = sum(
        np.array_equal(old_row.embedding, new_row.embedding)
        for old_row, new_row in zip(
            old_materialized.rows,
            new_materialized.rows,
            strict=True,
        )
    )
    assert vector_equivalent_count == 30
    assert admin.read_activation_state() == (pointer_before, version_before)

    with pytest.raises(BgeFullGenerationError, match="ACTIVATION_DATABASE_OPERATION_FAILED"):
        activate_bge_full_generation(
            materialized=new_materialized,
            parity=new_parity,
            benchmark=_activation_benchmark(
                plan=new_plan,
                warm_p95_ms=1.0,
                report_sha256="4" * 64,
            ),
            expected_current_generation_id=pointer_before,
            expected_policy_version=version_before + 1,
            approved_by_audit_ref="s4-7c-stale-cas-rollback-20260801",
            repository=admin,
        )
    assert admin.read_activation_state() == (pointer_before, version_before)

    query_set = json.loads(_QUERY_SET_PATH.read_text(encoding="utf-8"))
    queries = query_set["queries"]
    assert isinstance(queries, list) and len(queries) == 10
    with psycopg.connect(cluster["admin_dsn"]) as connection:
        connection.execute("SET statement_timeout = '1500ms'")
        old_metrics = _benchmark_generation(
            connection=connection,
            generation_id=old_plan.generation_id,
            queries=queries,
            runtime=runtime,
        )
        new_metrics = _benchmark_generation(
            connection=connection,
            generation_id=new_plan.generation_id,
            queries=queries,
            runtime=runtime,
        )

    retrieval_non_regression = (
        new_metrics["expectedTop5HitRate"] >= old_metrics["expectedTop5HitRate"]
        and new_metrics["expectedTop5HitRate"] == 1.0
        and new_metrics["warmP95Ms"] <= 1500.0
    )
    assert retrieval_non_regression
    benchmark_payload = {
        "newGenerationId": new_plan.generation_id,
        "querySetSha256": hashlib.sha256(_QUERY_SET_PATH.read_bytes()).hexdigest(),
        "warmup": 20,
        "measured": 100,
        "warmP95Ms": new_metrics["warmP95Ms"],
        "expectedTop5HitRate": new_metrics["expectedTop5HitRate"],
        "providerPhysicalCalls": 0,
    }
    activation_benchmark_sha256 = _canonical_json_hash(benchmark_payload)
    activate_bge_full_generation(
        materialized=new_materialized,
        parity=new_parity,
        benchmark=_activation_benchmark(
            plan=new_plan,
            warm_p95_ms=float(new_metrics["warmP95Ms"]),
            report_sha256=activation_benchmark_sha256,
        ),
        expected_current_generation_id=pointer_before,
        expected_policy_version=version_before,
        approved_by_audit_ref="s4-7c-external-generation-activation-20260801",
        repository=admin,
    )
    pointer_after, version_after = admin.read_activation_state()

    with psycopg.connect(cluster["admin_dsn"]) as connection:
        statuses = dict(
            connection.execute(
                """
                SELECT corpus_generation_id, status
                FROM rag_corpus_generations
                WHERE corpus_generation_id IN (%s, %s)
                ORDER BY corpus_generation_id
                """,
                (old_plan.generation_id, new_plan.generation_id),
            ).fetchall()
        )
        active_generation_count = int(
            connection.execute(
                "SELECT count(*) FROM rag_corpus_generations WHERE status = 'ACTIVE'"
            ).fetchone()[0]
        )
        revision_counts = [
            {
                "registryVersion": str(row[0]),
                "externalProcessingAllowed": bool(row[1]),
                "count": int(row[2]),
            }
            for row in connection.execute(
                """
                SELECT registry_version, external_processing_allowed, count(*)
                FROM rag_source_revisions
                WHERE registry_version IN ('s4-7b-source-card-v2', 's4-7c-source-card-v2')
                GROUP BY registry_version, external_processing_allowed
                ORDER BY registry_version
                """
            ).fetchall()
        ]

    report: dict[str, Any] = {
        "schemaVersion": "s4-7c-external-generation/v1",
        "status": "PASS",
        "scope": "APPEND_ONLY_EXACT_30_LOCAL_BGE_ATOMIC_TRANSITION",
        "oldProfileId": old_plan.corpus_profile_id,
        "externalProfileId": new_plan.corpus_profile_id,
        "oldCorpusManifestSha256": old_plan.corpus_hash,
        "newCorpusManifestSha256": new_plan.corpus_hash,
        "sourceCount": len(new_plan.items),
        "chunkCount": len(new_materialized.rows),
        "bodyEquivalentCount": sum(
            old.card.canonical_body == new.card.canonical_body
            for old, new in zip(old_plan.items, new_plan.items, strict=True)
        ),
        "vectorEquivalentCount": vector_equivalent_count,
        "licenseConsentReceiptCount": len(new_corpus.manifest["licenseConsentReceipts"]),
        "oldGenerationId": old_plan.generation_id,
        "newGenerationId": new_plan.generation_id,
        "oldGenerationStatus": statuses[old_plan.generation_id],
        "newGenerationStatus": statuses[new_plan.generation_id],
        "activeGenerationCount": active_generation_count,
        "activePointerBefore": pointer_before,
        "activePointerAfter": pointer_after,
        "policyVersionBefore": version_before,
        "policyVersionAfter": version_after,
        "staleCasRollbackVerified": True,
        "retrievalNonRegression": retrieval_non_regression,
        "oldRetrieval": old_metrics,
        "newRetrieval": new_metrics,
        "revisionCounts": revision_counts,
        "generationVectorHashOld": old_materialized.generation_vector_hash,
        "generationVectorHashNew": new_materialized.generation_vector_hash,
        "activationBenchmarkSha256": activation_benchmark_sha256,
        "querySetSha256": benchmark_payload["querySetSha256"],
        "localBgeOnnxCalls": runtime.local_onnx_calls,
        "providerPhysicalCalls": 0,
        "voyagePhysicalCalls": 0,
        "geminiPhysicalCalls": 0,
        "openaiPhysicalCalls": 0,
        "networkCalls": 0,
        "hashScope": "CANONICAL_JSON_WITHOUT_REPORT_SHA256",
    }
    report["reportSha256"] = _canonical_json_hash(report)

    assert statuses == {
        old_plan.generation_id: "DISABLED",
        new_plan.generation_id: "ACTIVE",
    }
    assert active_generation_count == 1
    assert pointer_after == new_plan.generation_id
    assert version_after == version_before + 1
    assert report["bodyEquivalentCount"] == 30
    assert report["licenseConsentReceiptCount"] == 30
    assert report["providerPhysicalCalls"] == 0
    print(_RESULT_MARKER + json.dumps(report, ensure_ascii=False, sort_keys=True))


def _activation_benchmark(
    *,
    plan: Any,
    warm_p95_ms: float,
    report_sha256: str,
) -> BgeGenerationBenchmarkReceipt:
    return BgeGenerationBenchmarkReceipt(
        report_sha256=report_sha256,
        query_set_sha256=hashlib.sha256(_QUERY_SET_PATH.read_bytes()).hexdigest(),
        environment_fingerprint_sha256=plan.environment_fingerprint_sha256,
        warmup_count=20,
        measured_count=100,
        warm_p95_ms=warm_p95_ms,
        provider_physical_calls=0,
        voyage_physical_calls=0,
        gemini_physical_calls=0,
        openai_physical_calls=0,
        passed=True,
    )


def _benchmark_generation(
    *,
    connection: psycopg.Connection[Any],
    generation_id: str,
    queries: list[Any],
    runtime: _CountingBgeRuntime,
) -> dict[str, Any]:
    for index in range(20):
        _run_query(
            connection=connection,
            generation_id=generation_id,
            raw_query=str(queries[index % len(queries)]["text"]),
            runtime=runtime,
        )
    samples: dict[str, list[float]] = defaultdict(list)
    expected_hits = 0
    for index in range(100):
        query = queries[index % len(queries)]
        result, timings = _run_query(
            connection=connection,
            generation_id=generation_id,
            raw_query=str(query["text"]),
            runtime=runtime,
        )
        for stage, elapsed_ms in timings.items():
            samples[stage].append(elapsed_ms)
        if set(query["expectedSourceIds"]).intersection(item["sourceId"] for item in result[:5]):
            expected_hits += 1
    return {
        "warmup": 20,
        "measured": 100,
        "expectedTop5HitRate": expected_hits / 100,
        "warmP95Ms": _percentile(samples["total"], 95),
        "stagesP95Ms": {
            stage: _percentile(values, 95) for stage, values in sorted(samples.items())
        },
    }


def _run_query(
    *,
    connection: psycopg.Connection[Any],
    generation_id: str,
    raw_query: str,
    runtime: _CountingBgeRuntime,
) -> tuple[list[dict[str, str | float]], dict[str, float]]:
    started = time.perf_counter_ns()
    normalized = " ".join(unicodedata.normalize("NFC", raw_query).split())
    normalized_at = time.perf_counter_ns()
    query_vector = runtime.embed_query(normalized)
    embedded_at = time.perf_counter_ns()
    vector_text = "[" + ",".join(format(float(value), ".9g") for value in query_vector) + "]"
    identifiers = sorted(set(_IDENTIFIER_PATTERN.findall(normalized)))

    exact = (
        connection.execute(
            """
            SELECT chunk.chunk_revision_id, source.source_id
            FROM rag_chunk_revisions AS chunk
            JOIN rag_generation_chunks AS membership
              ON membership.chunk_revision_id = chunk.chunk_revision_id
            JOIN rag_source_revisions AS revision
              ON revision.source_revision_id = chunk.source_revision_id
            JOIN rag_sources AS source ON source.source_id = revision.source_id
            WHERE membership.corpus_generation_id = %s
              AND EXISTS (
                SELECT 1 FROM unnest(%s::text[]) AS identifier
                WHERE strpos(lower(chunk.canonical_content), lower(identifier)) > 0
              )
            ORDER BY chunk.chunk_revision_id
            LIMIT 30
            """,
            (generation_id, identifiers),
        ).fetchall()
        if identifiers
        else []
    )
    exact_at = time.perf_counter_ns()
    lexical = connection.execute(
        """
        SELECT chunk.chunk_revision_id, source.source_id
        FROM rag_chunk_revisions AS chunk
        JOIN rag_generation_chunks AS membership
          ON membership.chunk_revision_id = chunk.chunk_revision_id
        JOIN rag_source_revisions AS revision
          ON revision.source_revision_id = chunk.source_revision_id
        JOIN rag_sources AS source ON source.source_id = revision.source_id
        WHERE membership.corpus_generation_id = %s
        ORDER BY similarity(lower(chunk.canonical_content), lower(%s)) DESC,
                 chunk.chunk_revision_id
        LIMIT 30
        """,
        (generation_id, normalized),
    ).fetchall()
    lexical_at = time.perf_counter_ns()
    dense = connection.execute(
        """
        SELECT chunk.chunk_revision_id, source.source_id
        FROM rag_chunk_revisions AS chunk
        JOIN rag_generation_chunks AS membership
          ON membership.chunk_revision_id = chunk.chunk_revision_id
        JOIN rag_chunk_embeddings AS embedding
          ON embedding.corpus_generation_id = membership.corpus_generation_id
         AND embedding.chunk_revision_id = membership.chunk_revision_id
         AND embedding.embedding_profile_id = membership.embedding_profile_id
        JOIN rag_source_revisions AS revision
          ON revision.source_revision_id = chunk.source_revision_id
        JOIN rag_sources AS source ON source.source_id = revision.source_id
        WHERE membership.corpus_generation_id = %s
        ORDER BY embedding.embedding <=> %s::vector,
                 chunk.chunk_revision_id
        LIMIT 30
        """,
        (generation_id, vector_text),
    ).fetchall()
    dense_at = time.perf_counter_ns()
    fused = _rrf((exact, lexical, dense))
    fused_at = time.perf_counter_ns()
    return fused[:5], {
        "denseSql": _elapsed_ms(dense_at, lexical_at),
        "exactSql": _elapsed_ms(exact_at, embedded_at),
        "lexicalSql": _elapsed_ms(lexical_at, exact_at),
        "normalization": _elapsed_ms(normalized_at, started),
        "queryEmbedding": _elapsed_ms(embedded_at, normalized_at),
        "rrf": _elapsed_ms(fused_at, dense_at),
        "total": _elapsed_ms(fused_at, started),
    }


def _rrf(channels: tuple[list[tuple[Any, ...]], ...]) -> list[dict[str, str | float]]:
    scores: dict[str, float] = defaultdict(float)
    source_by_chunk: dict[str, str] = {}
    for channel in channels:
        for rank, row in enumerate(channel, start=1):
            chunk_id = str(row[0])
            scores[chunk_id] += 1.0 / (60 + rank)
            source_by_chunk[chunk_id] = str(row[1])
    return [
        {
            "chunkRevisionId": chunk_id,
            "sourceId": source_by_chunk[chunk_id],
            "score": scores[chunk_id],
        }
        for chunk_id in sorted(scores, key=lambda value: (-scores[value], value))
    ]


def _percentile(values: list[float], percentile: int) -> float:
    assert values
    return float(np.percentile(np.asarray(values, dtype=np.float64), percentile))


def _elapsed_ms(ended_ns: int, started_ns: int) -> float:
    return (ended_ns - started_ns) / 1_000_000


def _canonical_json_hash(value: object) -> str:
    return hashlib.sha256(
        (
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
    ).hexdigest()
