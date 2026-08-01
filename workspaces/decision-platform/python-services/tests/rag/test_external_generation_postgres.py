from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import psycopg
import pytest
from numpy.typing import NDArray

from app.rag.bge_artifact import BgeVerifiedPacket
from app.rag.bge_full_generation import (
    BgeBatchBenchmarkReceipt,
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
from app.rag.corpus_profiles import load_source_card_corpus


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
    physical_provider_calls = 0

    def embed(self, texts: tuple[str, ...]) -> NDArray[np.float32]:
        rows: list[NDArray[np.float32]] = []
        for text in texts:
            vector = np.zeros(1024, dtype=np.float32)
            vector[int(hashlib.sha256(text.encode()).hexdigest()[:8], 16) % 1024] = 1
            rows.append(vector)
        return np.stack(rows)


def test_external_generation_appends_revisions_and_atomically_supersedes_internal(
    isolated_postgres_cluster: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cluster = isolated_postgres_cluster
    monkeypatch.setenv("RAG_SOURCE_REGISTER_TARGET", "testcontainers")
    old_plan = _plan("s4_7b_internal_v1")
    new_plan = _plan("s4_7c_external_v1")
    writer = PsycopgBgeFullGenerationWriterRepository(database_dsn=cluster["rag_writer_dsn"])
    reader = PsycopgBgeFullGenerationReader(database_dsn=cluster["rag_admin_dsn"])
    admin = PsycopgBgeFullGenerationAdminRepository(database_dsn=cluster["rag_admin_dsn"])
    embedder = _FixtureEmbedder()

    old_materialized = execute_bge_full_generation(
        plan=old_plan,
        embedder=embedder,
        repository=writer,
    )
    old_parity = verify_bge_full_generation_parity(materialized=old_materialized, reader=reader)
    pointer, version = admin.read_activation_state()
    activate_bge_full_generation(
        materialized=old_materialized,
        parity=old_parity,
        benchmark=_benchmark(),
        expected_current_generation_id=pointer,
        expected_policy_version=version,
        approved_by_audit_ref="s4-7c-test-old-generation-activation",
        repository=admin,
    )
    old_pointer, old_version = admin.read_activation_state()
    assert old_pointer == old_plan.generation_id

    new_materialized = execute_bge_full_generation(
        plan=new_plan,
        embedder=embedder,
        repository=writer,
    )
    new_parity = verify_bge_full_generation_parity(materialized=new_materialized, reader=reader)
    assert admin.read_activation_state() == (old_pointer, old_version)

    with pytest.raises(BgeFullGenerationError, match="ACTIVATION_DATABASE_OPERATION_FAILED"):
        activate_bge_full_generation(
            materialized=new_materialized,
            parity=new_parity,
            benchmark=_benchmark(),
            expected_current_generation_id=old_pointer,
            expected_policy_version=old_version + 1,
            approved_by_audit_ref="s4-7c-test-stale-cas-must-rollback",
            repository=admin,
        )
    assert admin.read_activation_state() == (old_pointer, old_version)

    activation = activate_bge_full_generation(
        materialized=new_materialized,
        parity=new_parity,
        benchmark=_benchmark(),
        expected_current_generation_id=old_pointer,
        expected_policy_version=old_version,
        approved_by_audit_ref="s4-7c-test-external-generation-activation",
        repository=admin,
    )
    assert activation.previous_generation_id == old_plan.generation_id
    assert activation.active_generation_id == new_plan.generation_id
    assert embedder.physical_provider_calls == 0

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
        revision_counts = connection.execute(
            """
            SELECT registry_version, external_processing_allowed, count(*)
            FROM rag_source_revisions
            WHERE registry_version IN ('s4-7b-source-card-v2', 's4-7c-source-card-v2')
            GROUP BY registry_version, external_processing_allowed
            ORDER BY registry_version
            """
        ).fetchall()
        active_count = connection.execute(
            "SELECT count(*) FROM rag_corpus_generations WHERE status = 'ACTIVE'"
        ).fetchone()

    assert statuses == {old_plan.generation_id: "DISABLED", new_plan.generation_id: "ACTIVE"}
    assert revision_counts == [
        ("s4-7b-source-card-v2", False, 30),
        ("s4-7c-source-card-v2", True, 30),
    ]
    assert active_count == (1,)


def _plan(profile_id: str) -> Any:
    return prepare_bge_full_generation(
        corpus=load_source_card_corpus(profile_id=profile_id),
        tokenizer=_WhitespaceTokenizer(),
        artifact=BgeVerifiedPacket(
            revision="5617a9f61b028005a4858fdac845db406aefb181",
            file_count=10,
            total_bytes=2_289_781_803,
            file_manifest_sha256=(
                "a0ae6372b2d735b593d806d24c1155cb48dd7188adebe7d6b7619a1622fb71aa"
            ),
        ),
        batch_benchmark=BgeBatchBenchmarkReceipt(
            selected_batch_size=32,
            candidates=(16, 32, 64),
            peak_rss_bytes=((16, 1), (32, 1), (64, 1)),
            elapsed_ms=((16, 1.0), (32, 1.0), (64, 1.0)),
            environment_fingerprint_sha256="1" * 64,
            benchmark_sha256="2" * 64,
        ),
    )


def _benchmark() -> BgeGenerationBenchmarkReceipt:
    return BgeGenerationBenchmarkReceipt(
        report_sha256="3" * 64,
        query_set_sha256="4" * 64,
        environment_fingerprint_sha256="1" * 64,
        warmup_count=20,
        measured_count=100,
        warm_p95_ms=100.0,
        provider_physical_calls=0,
        voyage_physical_calls=0,
        gemini_physical_calls=0,
        openai_physical_calls=0,
        passed=True,
    )
