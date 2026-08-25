from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import time
from collections import defaultdict
from collections.abc import Callable
from typing import Any, TypeVar

import numpy as np
import onnxruntime as ort
import psycopg
import pytest
import tokenizers
from numpy.typing import NDArray

from app.rag.authorized_retrieval import (
    AuthorizedHybridRetrieval,
    AuthorizedRetrievalScope,
    EvidenceSufficiencyPolicy,
    ExactIdentifierExtractor,
    QueryNormalizer,
    RrfFusion,
)
from app.rag.authorized_retrieval_adapters import (
    LocalBgeQueryEmbedder,
    PsycopgAuthorizedRetrievalAdapter,
    build_immutable_card_evidence,
)
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
from tests.support.actor_rls_scope import open_actor_rls_scope

pytestmark = pytest.mark.skipif(
    os.environ.get("S4_3_AUTHORIZED_RETRIEVAL_BENCHMARK") != "1",
    reason="pinned local BGE와 isolated PostgreSQL을 쓰는 명시적 S4.3 benchmark다.",
)

_BATCH_REPORT_PATH = REPO_ROOT / "capstone-rag/reports/s4-2b-batch-memory-benchmark.v1.json"
_QUERY_SET_PATH = REPO_ROOT / "capstone-rag/eval/s4-3-authorized-retrieval-smoke.v1.json"
_RESULT_MARKER = "S4_3_AUTHORIZED_RETRIEVAL_RESULT "
_T = TypeVar("_T")


class _StageRecorder:
    def __init__(self) -> None:
        self.samples: dict[str, list[float]] = defaultdict(list)

    def timed(self, stage: str, operation: Callable[[], _T]) -> _T:
        started = time.perf_counter_ns()
        try:
            return operation()
        finally:
            self.samples[stage].append(_elapsed_ms(time.perf_counter_ns(), started))

    def clear(self) -> None:
        self.samples.clear()


class _CountingBgeRuntime:
    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.physical_calls = 0

    def embed(self, texts: tuple[str, ...]) -> NDArray[np.float32]:
        self.physical_calls += 1
        return self._delegate.embed(texts)

    def embed_query(self, question: str) -> NDArray[np.float32]:
        self.physical_calls += 1
        return self._delegate.embed_query(question)


class _TimedNormalizer:
    def __init__(self, delegate: QueryNormalizer, recorder: _StageRecorder) -> None:
        self._delegate = delegate
        self._recorder = recorder

    def normalize(self, payload: Any) -> Any:
        return self._recorder.timed(
            "normalization",
            lambda: self._delegate.normalize(payload),
        )


class _TimedExtractor:
    def __init__(
        self,
        delegate: ExactIdentifierExtractor,
        recorder: _StageRecorder,
    ) -> None:
        self._delegate = delegate
        self._recorder = recorder

    def extract(self, text: str) -> tuple[str, ...]:
        return self._recorder.timed(
            "identifierExtraction",
            lambda: self._delegate.extract(text),
        )


class _TimedEmbedder:
    def __init__(self, delegate: Any, recorder: _StageRecorder) -> None:
        self._delegate = delegate
        self._recorder = recorder

    @property
    def embedding_profile_id(self) -> str:
        return str(self._delegate.embedding_profile_id)

    def embed_query(self, question: str) -> Any:
        return self._recorder.timed(
            "queryEmbedding",
            lambda: self._delegate.embed_query(question),
        )


class _TimedRetrievers:
    def __init__(self, delegate: Any, recorder: _StageRecorder) -> None:
        self._delegate = delegate
        self._recorder = recorder

    def retrieve_exact(self, **kwargs: Any) -> Any:
        return self._recorder.timed(
            "exactSql",
            lambda: self._delegate.retrieve_exact(**kwargs),
        )

    def retrieve_lexical(self, **kwargs: Any) -> Any:
        return self._recorder.timed(
            "lexicalSql",
            lambda: self._delegate.retrieve_lexical(**kwargs),
        )

    def retrieve_dense(self, **kwargs: Any) -> Any:
        return self._recorder.timed(
            "denseSql",
            lambda: self._delegate.retrieve_dense(**kwargs),
        )


class _TimedRrf:
    def __init__(self, delegate: RrfFusion, recorder: _StageRecorder) -> None:
        self._delegate = delegate
        self._recorder = recorder

    def fuse(self, channels: Any) -> Any:
        return self._recorder.timed(
            "rrf",
            lambda: self._delegate.fuse(channels),
        )


class _TimedEvidencePolicy:
    def __init__(
        self,
        delegate: EvidenceSufficiencyPolicy,
        recorder: _StageRecorder,
    ) -> None:
        self._delegate = delegate
        self._recorder = recorder

    def evaluate(self, **kwargs: Any) -> Any:
        return self._recorder.timed(
            "evidencePolicy",
            lambda: self._delegate.evaluate(**kwargs),
        )


def test_s4_3_authorized_retrieval_exact_10_smoke_and_benchmark(
    isolated_postgres_cluster: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cluster = isolated_postgres_cluster
    monkeypatch.setenv("RAG_SOURCE_REGISTER_TARGET", "testcontainers")
    batch_report = json.loads(_BATCH_REPORT_PATH.read_text(encoding="utf-8"))
    batch_receipt = batch_receipt_from_report(batch_report)
    environment = _environment_payload()
    assert _canonical_json_hash(environment) == (batch_receipt.environment_fingerprint_sha256)

    corpus = load_frozen_source_card_corpus()
    artifact = verify_bge_completion_manifest(
        DEFAULT_MODEL_ROOT,
        manifest_path=DEFAULT_MODEL_MANIFEST,
    )
    tokenizer = BgeStaticTokenizer.from_file(DEFAULT_MODEL_ROOT / "onnx/tokenizer.json")
    plan = prepare_bge_full_generation(
        corpus=corpus,
        tokenizer=tokenizer,
        artifact=artifact,
        batch_benchmark=batch_receipt,
    )
    runtime = _CountingBgeRuntime(load_bge_onnx_embedder(DEFAULT_MODEL_ROOT))

    generation_started = time.perf_counter_ns()
    materialized = execute_bge_full_generation(
        plan=plan,
        embedder=runtime,
        repository=PsycopgBgeFullGenerationWriterRepository(
            database_dsn=cluster["rag_writer_dsn"],
        ),
    )
    generation_elapsed_ms = _elapsed_ms(
        time.perf_counter_ns(),
        generation_started,
    )
    parity = verify_bge_full_generation_parity(
        materialized=materialized,
        reader=PsycopgBgeFullGenerationReader(
            database_dsn=cluster["rag_admin_dsn"],
        ),
    )
    admin_repository = PsycopgBgeFullGenerationAdminRepository(
        database_dsn=cluster["rag_admin_dsn"],
    )
    pointer_before, policy_version_before = admin_repository.read_activation_state()
    bootstrap_benchmark = BgeGenerationBenchmarkReceipt(
        report_sha256="3" * 64,
        query_set_sha256=hashlib.sha256(_QUERY_SET_PATH.read_bytes()).hexdigest(),
        environment_fingerprint_sha256=plan.environment_fingerprint_sha256,
        warmup_count=20,
        measured_count=100,
        warm_p95_ms=220.0,
        provider_physical_calls=0,
        voyage_physical_calls=0,
        gemini_physical_calls=0,
        openai_physical_calls=0,
        passed=True,
    )
    activate_bge_full_generation(
        materialized=materialized,
        parity=parity,
        benchmark=bootstrap_benchmark,
        expected_current_generation_id=pointer_before,
        expected_policy_version=policy_version_before,
        approved_by_audit_ref="s4-3-local-benchmark-activation-20260731",
        repository=admin_repository,
    )
    pointer_after, policy_version_after = admin_repository.read_activation_state()
    assert pointer_after == plan.generation_id

    scope = _create_scope(
        app_dsn=cluster["app_dsn"],
        identity_dsn=cluster["identity_dsn"],
        generation_id=plan.generation_id,
        policy_version=policy_version_after,
    )
    base_adapter = PsycopgAuthorizedRetrievalAdapter(
        database_dsn=cluster["rag_query_dsn"],
        card_evidence=build_immutable_card_evidence(corpus.cards),
    )
    recorder = _StageRecorder()
    timed_adapter = _TimedRetrievers(base_adapter, recorder)
    hybrid = AuthorizedHybridRetrieval(
        query_normalizer=_TimedNormalizer(QueryNormalizer(), recorder),
        exact_identifier_extractor=_TimedExtractor(
            ExactIdentifierExtractor(),
            recorder,
        ),
        query_embedder=_TimedEmbedder(
            LocalBgeQueryEmbedder(runtime),
            recorder,
        ),
        exact_retriever=timed_adapter,
        lexical_retriever=timed_adapter,
        dense_retriever=timed_adapter,
        rrf_fusion=_TimedRrf(RrfFusion(), recorder),
        evidence_sufficiency_policy=_TimedEvidencePolicy(
            EvidenceSufficiencyPolicy(),
            recorder,
        ),
    )
    query_set = json.loads(_QUERY_SET_PATH.read_text(encoding="utf-8"))
    queries = query_set["queries"]
    assert isinstance(queries, list) and len(queries) == 10

    for _ in range(2):
        for query in queries:
            _run_one(hybrid=hybrid, scope=scope, query=query)
    recorder.clear()

    expected_hit_count = 0
    expected_success_count = 0
    refusal_count = 0
    per_query_receipts: dict[str, dict[str, Any]] = {}
    for index in range(100):
        query = queries[index % len(queries)]
        started = time.perf_counter_ns()
        outcome = _run_one(hybrid=hybrid, scope=scope, query=query)
        recorder.samples["total"].append(_elapsed_ms(time.perf_counter_ns(), started))
        query_id = str(query["id"])
        expected_failure = query["expectedFailure"]
        if expected_failure is None:
            expected_success_count += 1
            expected_sources = set(query["expectedSourceIds"])
            returned_sources = {item.source_id for item in outcome.evidence}
            if outcome.failure_code is None and expected_sources & returned_sources:
                expected_hit_count += 1
            per_query_receipts[query_id] = {
                "failure": (
                    outcome.failure_code.value if outcome.failure_code is not None else None
                ),
                "top5SourceIds": [item.source_id for item in outcome.evidence],
            }
        else:
            if (
                outcome.failure_code is not None
                and outcome.failure_code.value == expected_failure
                and not outcome.evidence
            ):
                refusal_count += 1
            per_query_receipts[query_id] = {
                "failure": (
                    outcome.failure_code.value if outcome.failure_code is not None else None
                ),
                "top5SourceIds": [],
            }

    stage_percentiles = {
        stage: _percentiles(values) for stage, values in sorted(recorder.samples.items())
    }
    with psycopg.connect(cluster["admin_dsn"]) as connection:
        postgres_version = str(connection.execute("SHOW server_version").fetchone()[0])
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

    expected_hit_rate = expected_hit_count / expected_success_count
    refusal_rate = refusal_count / 10
    report: dict[str, Any] = {
        "schemaVersion": "s4-3-authorized-retrieval-benchmark/v1",
        "status": "PASS",
        "scope": "EXACT_30_AUTHORIZED_HYBRID_LOCAL_ONLY",
        "commitSha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
        ).strip(),
        "corpusManifestSha256": plan.corpus_hash,
        "generationId": plan.generation_id,
        "generationHash": plan.generation_hash,
        "embeddingProfileId": plan.embedding_profile_id,
        "modelRevision": plan.model_revision,
        "modelFileManifestHash": plan.artifact_manifest_sha256,
        "tokenizerSha256": plan.tokenizer_sha256,
        "parserVersion": plan.parser_version,
        "chunkerVersion": plan.chunker_version,
        "inputStrategyVersion": plan.input_strategy_version,
        "host": {
            **environment,
            "memoryBytes": (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")),
        },
        "environmentFingerprintSha256": plan.environment_fingerprint_sha256,
        "postgresVersion": postgres_version,
        "postgresExtensions": extension_versions,
        "queryDatasetId": query_set["datasetId"],
        "querySetSha256": hashlib.sha256(_QUERY_SET_PATH.read_bytes()).hexdigest(),
        "warmup": 20,
        "measured": 100,
        "concurrency": 1,
        "outliersRetained": True,
        "expectedTop5HitRate": expected_hit_rate,
        "noEvidenceRefusalRate": refusal_rate,
        "generationElapsedMs": generation_elapsed_ms,
        "stagesMs": stage_percentiles,
        "queryReceipts": per_query_receipts,
        "activePointerBefore": {
            "generationId": pointer_before,
            "policyVersion": policy_version_before,
        },
        "activePointerAfter": {
            "generationId": pointer_after,
            "policyVersion": policy_version_after,
        },
        "networkCalls": 0,
        "physicalCalls": {
            "bgeLocalOnnx": runtime.physical_calls,
            "providerTotal": 0,
            "voyage": 0,
            "gemini": 0,
            "openai": 0,
        },
        "hashScope": "CANONICAL_JSON_WITHOUT_BENCHMARK_REPORT_SHA256",
    }
    report["benchmarkReportSha256"] = _canonical_json_hash(report)
    print(_RESULT_MARKER + json.dumps(report, ensure_ascii=False, sort_keys=True))

    assert parity.passed
    assert expected_hit_rate == 1.0
    assert refusal_rate == 1.0
    assert stage_percentiles["total"]["p95"] <= 1500.0
    assert report["physicalCalls"]["providerTotal"] == 0


def _create_scope(
    *,
    app_dsn: str,
    identity_dsn: str,
    generation_id: str,
    policy_version: int,
) -> AuthorizedRetrievalScope:
    owner_user_id = "usr_demo_user"
    session_id = "s4-3-benchmark-session-0001"
    topics = (
        "API",
        "DATA",
        "FINANCIAL_ENGINEERING",
        "METHODOLOGY",
        "PRODUCT_RISK",
        "RISK",
    )
    with psycopg.connect(app_dsn, autocommit=False) as connection:
        open_actor_rls_scope(
            identity_dsn=identity_dsn,
            connection=connection,
            actor_user_id=owner_user_id,
            actor_role="USER",
            operation="ISSUE_RAG_RETRIEVAL_SCOPE",
            target_kind="RAG_SESSION",
            target_id=session_id,
        )
        row = connection.execute(
            """
            SELECT scope_claim_id, owner_user_id, session_id, allowed_topics,
                   active_generation_id, effective_profile_id, policy_version
            FROM public.create_rag_retrieval_scope_claim(%s, %s, %s)
            """,
            (owner_user_id, session_id, list(topics)),
        ).fetchone()
        connection.commit()
    assert row is not None
    assert row[4] == generation_id
    assert row[6] == policy_version
    return AuthorizedRetrievalScope(
        claim_id=row[0],
        owner_user_id=row[1],
        session_id=row[2],
        allowed_topics=tuple(row[3]),
        generation_id=row[4],
        embedding_profile_id=row[5],
        policy_version=row[6],
    )


def _run_one(
    *,
    hybrid: AuthorizedHybridRetrieval,
    scope: AuthorizedRetrievalScope,
    query: dict[str, Any],
) -> Any:
    payload = {
        key: query[key]
        for key in ("question", "answerMode", "relatedSymbols", "topics")
        if key in query
    }
    return hybrid.retrieve(scope=scope, payload=payload)


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


def _elapsed_ms(later_ns: int, earlier_ns: int) -> float:
    return (later_ns - earlier_ns) / 1_000_000


def _canonical_json_hash(value: object) -> str:
    return hashlib.sha256(
        (
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()
