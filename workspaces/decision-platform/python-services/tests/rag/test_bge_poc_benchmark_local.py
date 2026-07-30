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

from app.rag.bge_acquisition import (
    DEFAULT_MODEL_MANIFEST,
    DEFAULT_MODEL_ROOT,
    verify_bge_completion_manifest,
)
from app.rag.bge_poc import (
    PsycopgBgePocRepository,
    execute_bge_poc,
    prepare_bge_poc,
)
from app.rag.bge_runtime import BgeStaticTokenizer, load_bge_onnx_embedder
from app.rag.source_card import OFFICIAL_SOURCE_CARD_ROOT, REPO_ROOT, load_rag_source_cards

pytestmark = pytest.mark.skipif(
    os.environ.get("BGE_LOCAL_BENCHMARK_TESTS") != "1",
    reason="exact ignored local model의 명시적 preliminary benchmark에서만 실행한다.",
)

_CARD_PATHS = (
    "src_project_ecos_pit_availability_001.md",
    "src_project_gold_futures_etf_132030_001.md",
    "src_project_kis_adjusted_price_001.md",
    "src_project_krx_service_coverage_001.md",
    "src_project_opendart_status_quota_001.md",
)
_QUERY_SET_PATH = REPO_ROOT / "capstone-rag/eval/s4-2a-five-card-smoke.v1.json"
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}|[0-9]{3,}")


def test_preliminary_five_card_warm_p95(
    postgres_cluster: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """test-only SQL channel로 20 warmup/100 measured latency와 app RRF를 측정한다."""

    artifact = verify_bge_completion_manifest(
        DEFAULT_MODEL_ROOT,
        manifest_path=DEFAULT_MODEL_MANIFEST,
    )
    cards = load_rag_source_cards(
        approved_root=OFFICIAL_SOURCE_CARD_ROOT,
        relative_paths=_CARD_PATHS,
    )
    tokenizer = BgeStaticTokenizer.from_file(
        DEFAULT_MODEL_ROOT / "onnx/tokenizer.json"
    )
    plan = prepare_bge_poc(cards=cards, tokenizer=tokenizer, artifact=artifact)
    embedder = load_bge_onnx_embedder(DEFAULT_MODEL_ROOT)
    monkeypatch.setenv("RAG_SOURCE_REGISTER_TARGET", "testcontainers")
    execute_bge_poc(
        plan=plan,
        embedder=embedder,
        repository=PsycopgBgePocRepository(
            database_dsn=postgres_cluster["rag_writer_dsn"],
        ),
    )
    query_set = json.loads(_QUERY_SET_PATH.read_text(encoding="utf-8"))
    queries = query_set["queries"]
    assert isinstance(queries, list) and len(queries) == 10

    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        connection.execute("SET statement_timeout = '5s'")
        pointer_before = connection.execute(
            """
            SELECT policy_id, effective_profile_id, active_generation_id, version
            FROM rag_embedding_policy_state WHERE state_id = 'default'
            """
        ).fetchone()
        for index in range(20):
            _run_query(
                connection,
                plan.generation_id,
                str(queries[index % len(queries)]["text"]),
                embedder,
            )

        samples: dict[str, list[float]] = defaultdict(list)
        expected_hits = 0
        expected_queries = 0
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
            if expected:
                expected_queries += 1
                if expected.intersection(item["sourceId"] for item in result[:5]):
                    expected_hits += 1

        pointer_after = connection.execute(
            """
            SELECT policy_id, effective_profile_id, active_generation_id, version
            FROM rag_embedding_policy_state WHERE state_id = 'default'
            """
        ).fetchone()
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

    report = {
        "activePointerChanged": pointer_after != pointer_before,
        "artifactManifestSha256": plan.artifact_manifest_sha256,
        "authorizationMode": "TEST_ONLY_EXPLICIT_PUBLIC_PROJECT_GENERATION_PREDICATES",
        "commitSha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
        ).strip(),
        "concurrency": 1,
        "corpusManifestSha256": plan.corpus_hash,
        "datasetId": query_set["datasetId"],
        "evaluationExpectedTop5HitRate": expected_hits / expected_queries,
        "generationHash": plan.generation_hash,
        "generationId": plan.generation_id,
        "host": {
            "cpuCount": os.cpu_count(),
            "machine": platform.machine(),
            "memoryBytes": os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"),
            "processor": platform.processor(),
            "release": platform.release(),
            "system": platform.system(),
        },
        "measured": 100,
        "modelRevision": plan.model_revision,
        "networkCalls": 0,
        "onnxRuntimeVersion": ort.__version__,
        "outliersRetained": True,
        "physicalCalls": {
            "gemini": 0,
            "openai": 0,
            "voyage": 0,
        },
        "postgresExtensions": extension_versions,
        "postgresVersion": database_version,
        "pythonVersion": platform.python_version(),
        "querySetSha256": hashlib.sha256(_QUERY_SET_PATH.read_bytes()).hexdigest(),
        "stagesMs": {
            stage: _percentiles(values)
            for stage, values in sorted(samples.items())
        },
        "tokenizersVersion": tokenizers.__version__,
        "warmup": 20,
    }
    print("S4_2A_BENCHMARK_RESULT " + json.dumps(report, sort_keys=True))
    assert report["activePointerChanged"] is False
    assert report["evaluationExpectedTop5HitRate"] == 1.0
    assert report["stagesMs"]["total"]["p95"] <= 1500.0


def _run_query(
    connection: psycopg.Connection[Any],
    generation_id: str,
    raw_query: str,
    embedder: Any,
) -> tuple[list[dict[str, str | float]], dict[str, float]]:
    started = time.perf_counter_ns()
    normalized = " ".join(unicodedata.normalize("NFC", raw_query).split())
    normalized_at = time.perf_counter_ns()
    query_vector = embedder.embed_query(normalized)
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
              AND generation.status = 'EVAL_PASSED'
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
          AND generation.status = 'EVAL_PASSED'
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
          AND generation.status = 'EVAL_PASSED'
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
    channels: tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]]],
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


def _percentiles(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {
        "max": float(np.max(array)),
        "p50": float(np.percentile(array, 50)),
        "p90": float(np.percentile(array, 90)),
        "p95": float(np.percentile(array, 95)),
        "p99": float(np.percentile(array, 99)),
    }


def _elapsed_ms(later: int, earlier: int) -> float:
    return (later - earlier) / 1_000_000.0
