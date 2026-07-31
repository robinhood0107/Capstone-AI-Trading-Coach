from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import time
import unicodedata
from collections import defaultdict
from typing import Any

import numpy as np
import onnxruntime as ort
import psycopg
import pytest
import tokenizers
from numpy.typing import NDArray

from app.rag.bge_acquisition import (
    DEFAULT_MODEL_MANIFEST,
    DEFAULT_MODEL_ROOT,
    verify_bge_completion_manifest,
)
from app.rag.bge_full_generation import (
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
from app.rag.source_card_corpus import REPO_ROOT, load_frozen_source_card_corpus

pytestmark = pytest.mark.skipif(
    os.environ.get("BGE_FULL_GENERATION_TESTS") != "1",
    reason="exact ignored local model의 명시적 30-card final benchmark에서만 실행한다.",
)

_BATCH_REPORT_PATH = (
    REPO_ROOT / "capstone-rag/reports/s4-2b-batch-memory-benchmark.v1.json"
)
_QUERY_SET_PATH = REPO_ROOT / "capstone-rag/eval/s4-2b-30-card-smoke.v1.json"
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}|[0-9]{3,}")


class _CountingEmbedder:
    """local ONNX session.run 횟수를 provider 호출과 분리해 benchmark에 기록한다."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.physical_calls = 0

    def embed(self, texts: tuple[str, ...]) -> NDArray[np.float32]:
        self.physical_calls += 1
        return self._delegate.embed(texts)

    def embed_query(self, question: str) -> NDArray[np.float32]:
        return self.embed((question,))[0]


def test_exact_30_generation_parity_benchmark_and_atomic_activation(
    isolated_postgres_cluster: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pinned exact 30 corpus를 materialize·재독립 검증한 뒤 최종 SLA로 CAS 활성화한다."""

    postgres_cluster = isolated_postgres_cluster
    monkeypatch.setenv("RAG_SOURCE_REGISTER_TARGET", "testcontainers")
    batch_report = json.loads(_BATCH_REPORT_PATH.read_text(encoding="utf-8"))
    batch_receipt = batch_receipt_from_report(batch_report)
    environment = _environment_payload()
    assert _canonical_json_hash(environment) == (
        batch_receipt.environment_fingerprint_sha256
    )

    artifact = verify_bge_completion_manifest(
        DEFAULT_MODEL_ROOT,
        manifest_path=DEFAULT_MODEL_MANIFEST,
    )
    tokenizer = BgeStaticTokenizer.from_file(
        DEFAULT_MODEL_ROOT / "onnx/tokenizer.json"
    )
    plan = prepare_bge_full_generation(
        corpus=load_frozen_source_card_corpus(),
        tokenizer=tokenizer,
        artifact=artifact,
        batch_benchmark=batch_receipt,
    )
    embedder = _CountingEmbedder(load_bge_onnx_embedder(DEFAULT_MODEL_ROOT))

    generation_started = time.perf_counter_ns()
    materialized = execute_bge_full_generation(
        plan=plan,
        embedder=embedder,
        repository=PsycopgBgeFullGenerationWriterRepository(
            database_dsn=postgres_cluster["rag_writer_dsn"],
        ),
    )
    generation_elapsed_ms = _elapsed_ms(
        time.perf_counter_ns(),
        generation_started,
    )
    parity_started = time.perf_counter_ns()
    parity = verify_bge_full_generation_parity(
        materialized=materialized,
        reader=PsycopgBgeFullGenerationReader(
            database_dsn=postgres_cluster["rag_admin_dsn"],
        ),
    )
    parity_elapsed_ms = _elapsed_ms(time.perf_counter_ns(), parity_started)

    query_set = json.loads(_QUERY_SET_PATH.read_text(encoding="utf-8"))
    queries = query_set["queries"]
    assert isinstance(queries, list) and len(queries) == 10
    admin_repository = PsycopgBgeFullGenerationAdminRepository(
        database_dsn=postgres_cluster["rag_admin_dsn"],
    )
    pointer_before, policy_version_before = admin_repository.read_activation_state()

    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        connection.execute("SET statement_timeout = '1500ms'")
        for index in range(20):
            _run_query(
                connection,
                plan.generation_id,
                str(queries[index % len(queries)]["text"]),
                embedder,
            )

        samples: dict[str, list[float]] = defaultdict(list)
        expected_hits = 0
        for index in range(100):
            query = queries[index % len(queries)]
            result, timings = _run_query(
                connection,
                plan.generation_id,
                str(query["text"]),
                embedder,
            )
            for stage, elapsed_ms in timings.items():
                samples[stage].append(elapsed_ms)
            expected = set(query["expectedSourceIds"])
            if expected.intersection(item["sourceId"] for item in result[:5]):
                expected_hits += 1

        database_version = str(
            connection.execute("SHOW server_version").fetchone()[0]
        )
        extension_versions = dict(
            connection.execute(
                """
                SELECT extname, extversion
                FROM pg_extension
                WHERE extname IN ('vector', 'pg_trgm', 'pgcrypto')
                ORDER BY extname
                """
            ).fetchall()
        )

    stage_percentiles = {
        stage: _percentiles(values)
        for stage, values in sorted(samples.items())
    }
    warm_p95_ms = stage_percentiles["total"]["p95"]
    expected_pointer_after = {
        "generationId": plan.generation_id,
        "policyVersion": policy_version_before + 1,
    }
    report: dict[str, Any] = {
        "schemaVersion": "s4-2b-full-generation-benchmark/v1",
        "status": "PASS",
        "scope": "FINAL_EXACT_30_LOCAL_CAPACITY_ONLY",
        "commitSha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
        ).strip(),
        "corpusManifestSha256": plan.corpus_hash,
        "generationId": plan.generation_id,
        "generationHash": plan.generation_hash,
        "membershipHash": plan.membership_hash,
        "aggregateRowHash": materialized.aggregate_row_hash,
        "generationVectorHash": materialized.generation_vector_hash,
        "dbVectorHash": parity.generation_vector_hash,
        "embeddingProfileId": plan.embedding_profile_id,
        "modelRevision": plan.model_revision,
        "modelFileManifestHash": plan.artifact_manifest_sha256,
        "tokenizerSha256": plan.tokenizer_sha256,
        "parserVersion": plan.parser_version,
        "canonicalizerVersion": plan.canonicalizer_version,
        "chunkerVersion": plan.chunker_version,
        "inputStrategyVersion": plan.input_strategy_version,
        "batchSize": plan.batch_size,
        "batchBenchmarkSha256": plan.batch_benchmark_sha256,
        "environment": environment,
        "environmentFingerprintSha256": (
            plan.environment_fingerprint_sha256
        ),
        "host": {
            **environment,
            "memoryBytes": (
                os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
            ),
        },
        "postgresVersion": database_version,
        "postgresExtensions": extension_versions,
        "queryDatasetId": query_set["datasetId"],
        "querySetSha256": hashlib.sha256(
            _QUERY_SET_PATH.read_bytes()
        ).hexdigest(),
        "warmup": 20,
        "measured": 100,
        "concurrency": 1,
        "outliersRetained": True,
        "expectedTop5HitRate": expected_hits / 100,
        "generationElapsedMs": generation_elapsed_ms,
        "parityElapsedMs": parity_elapsed_ms,
        "parity": {
            "rowCount": parity.row_count,
            "maxAbsoluteError": parity.max_absolute_error,
            "minimumCosineSimilarity": parity.minimum_cosine_similarity,
            "passed": parity.passed,
        },
        "stagesMs": stage_percentiles,
        "activePointerBefore": {
            "generationId": pointer_before,
            "policyVersion": policy_version_before,
        },
        "activePointerAfter": expected_pointer_after,
        "activePointerTransition": "ATOMIC_BOUNDED_ADMIN_CAS",
        "networkCalls": 0,
        "physicalCalls": {
            "bgeLocalOnnx": embedder.physical_calls,
            "providerTotal": 0,
            "voyage": 0,
            "gemini": 0,
            "openai": 0,
        },
        "hashScope": (
            "CANONICAL_JSON_WITHOUT_BENCHMARK_REPORT_SHA256"
        ),
    }
    benchmark_report_sha256 = _canonical_json_hash(report)
    report["benchmarkReportSha256"] = benchmark_report_sha256
    benchmark = BgeGenerationBenchmarkReceipt(
        report_sha256=benchmark_report_sha256,
        query_set_sha256=str(report["querySetSha256"]),
        environment_fingerprint_sha256=plan.environment_fingerprint_sha256,
        warmup_count=20,
        measured_count=100,
        warm_p95_ms=warm_p95_ms,
        provider_physical_calls=0,
        voyage_physical_calls=0,
        gemini_physical_calls=0,
        openai_physical_calls=0,
        passed=(
            warm_p95_ms <= 1500.0
            and report["expectedTop5HitRate"] == 1.0
        ),
    )

    activation = activate_bge_full_generation(
        materialized=materialized,
        parity=parity,
        benchmark=benchmark,
        expected_current_generation_id=pointer_before,
        expected_policy_version=policy_version_before,
        approved_by_audit_ref="s4-2b-final-local-benchmark-20260731",
        repository=admin_repository,
    )
    pointer_after, policy_version_after = admin_repository.read_activation_state()
    assert {
        "generationId": pointer_after,
        "policyVersion": policy_version_after,
    } == expected_pointer_after
    assert activation.active_generation_id == plan.generation_id
    assert activation.generation_status == "ACTIVE"

    with psycopg.connect(postgres_cluster["rag_query_dsn"]) as connection:
        topic = str(plan.items[0].card.front_matter["topic"])
        active_projection = connection.execute(
            "SELECT * FROM read_active_rag_chunks(%s, 30)",
            (topic,),
        ).fetchall()
    assert active_projection
    assert all(str(row[1]).startswith("src_project_") for row in active_projection)

    print(
        "S4_2B_FULL_GENERATION_RESULT "
        + json.dumps(report, ensure_ascii=False, sort_keys=True)
    )
    assert materialized.database_receipt.active_pointer_changed is False
    assert report["expectedTop5HitRate"] == 1.0
    assert warm_p95_ms <= 1500.0
    assert report["physicalCalls"]["bgeLocalOnnx"] == 121


def _run_query(
    connection: psycopg.Connection[Any],
    generation_id: str,
    raw_query: str,
    embedder: _CountingEmbedder,
) -> tuple[list[dict[str, str | float]], dict[str, float]]:
    started = time.perf_counter_ns()
    normalized = " ".join(unicodedata.normalize("NFC", raw_query).split())
    normalized_at = time.perf_counter_ns()
    query_vector = embedder.embed_query(normalized)
    embedded_at = time.perf_counter_ns()
    vector_text = (
        "["
        + ",".join(format(float(value), ".9g") for value in query_vector)
        + "]"
    )
    identifiers = sorted(set(_IDENTIFIER_PATTERN.findall(normalized)))

    exact = (
        connection.execute(
            """
            SELECT chunk.chunk_revision_id, source.source_id
            FROM rag_chunk_revisions AS chunk
            JOIN rag_generation_chunks AS membership
              ON membership.chunk_revision_id = chunk.chunk_revision_id
            JOIN rag_corpus_generations AS generation
              ON generation.corpus_generation_id = membership.corpus_generation_id
            JOIN rag_source_revisions AS revision
              ON revision.source_revision_id = chunk.source_revision_id
             AND revision.access_level = 'PUBLIC' AND revision.tier = 'PROJECT'
            JOIN rag_sources AS source
              ON source.source_id = revision.source_id
             AND source.source_type = 'PROJECT_SOURCE_CARD'
             AND source.retired_at IS NULL
            WHERE generation.corpus_generation_id = %s
              AND generation.status = 'MATERIALIZED'
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
        JOIN rag_corpus_generations AS generation
          ON generation.corpus_generation_id = membership.corpus_generation_id
        JOIN rag_source_revisions AS revision
          ON revision.source_revision_id = chunk.source_revision_id
         AND revision.access_level = 'PUBLIC' AND revision.tier = 'PROJECT'
        JOIN rag_sources AS source
          ON source.source_id = revision.source_id
         AND source.source_type = 'PROJECT_SOURCE_CARD'
         AND source.retired_at IS NULL
        WHERE generation.corpus_generation_id = %s
          AND generation.status = 'MATERIALIZED'
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
        JOIN rag_corpus_generations AS generation
          ON generation.corpus_generation_id = membership.corpus_generation_id
        JOIN rag_chunk_embeddings AS embedding
          ON embedding.corpus_generation_id = membership.corpus_generation_id
         AND embedding.chunk_revision_id = membership.chunk_revision_id
         AND embedding.embedding_profile_id = membership.embedding_profile_id
        JOIN rag_source_revisions AS revision
          ON revision.source_revision_id = chunk.source_revision_id
         AND revision.access_level = 'PUBLIC' AND revision.tier = 'PROJECT'
        JOIN rag_sources AS source
          ON source.source_id = revision.source_id
         AND source.source_type = 'PROJECT_SOURCE_CARD'
         AND source.retired_at IS NULL
        WHERE generation.corpus_generation_id = %s
          AND generation.status = 'MATERIALIZED'
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


def _rrf(
    channels: tuple[
        list[tuple[Any, ...]],
        list[tuple[Any, ...]],
        list[tuple[Any, ...]],
    ],
) -> list[dict[str, str | float]]:
    scores: dict[str, float] = defaultdict(float)
    sources: dict[str, str] = {}
    for channel in channels:
        seen: set[str] = set()
        rank = 0
        for chunk_id_raw, source_id_raw in channel:
            chunk_id = str(chunk_id_raw)
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            rank += 1
            scores[chunk_id] += 1.0 / (60 + rank)
            sources[chunk_id] = str(source_id_raw)
    ordered = sorted(
        scores,
        key=lambda chunk_id: (
            -scores[chunk_id],
            sources[chunk_id].encode("utf-8"),
            chunk_id.encode("utf-8"),
        ),
    )
    return [
        {
            "chunkRevisionId": chunk_id,
            "rrfScore": scores[chunk_id],
            "sourceId": sources[chunk_id],
        }
        for chunk_id in ordered
    ]


def _environment_payload() -> dict[str, str | int | None]:
    return {
        "cpuCount": os.cpu_count(),
        "kernelRelease": platform.release(),
        "machine": platform.machine(),
        "onnxRuntimeVersion": ort.__version__,
        "processor": platform.processor(),
        "pythonVersion": platform.python_version(),
        "system": platform.system(),
        "tokenizersVersion": tokenizers.__version__,
    }


def _percentiles(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {
        "max": float(np.max(array)),
        "p50": float(np.percentile(array, 50)),
        "p90": float(np.percentile(array, 90)),
        "p95": float(np.percentile(array, 95)),
        "p99": float(np.percentile(array, 99)),
    }


def _canonical_json_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()


def _elapsed_ms(later: int, earlier: int) -> float:
    return (later - earlier) / 1_000_000.0
