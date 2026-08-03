from __future__ import annotations

import re

import numpy as np
import psycopg
import pytest

from app.rag.external_processing_corpus import load_external_processing_corpus
from app.rag.rag_v2_external_exact30_voyage_runner import (
    VoyagePreChunkedDocumentGroup,
    materialize_external_exact30_public_voyage_component,
)
from app.rag.rag_v2_external_exact30_voyage_staging import (
    build_external_exact30_voyage_staging_payload,
)
from app.rag.rag_v2_external_exact30_voyage_staging_repository import (
    ExternalExact30VoyageStagingRepositoryError,
    PsycopgExternalExact30VoyageStagingRepository,
)


class _FixtureTokenizer:
    """S4.7C sanitized card의 one-chunk identity만 deterministic하게 만든다."""

    def token_spans(self, text: str) -> tuple[tuple[int, int], ...]:
        return tuple((match.start(), match.end()) for match in re.finditer(r"\S+", text))


class _FixtureVoyageEmbedder:
    """외부 transport 없이 unit-vector contract를 재현하는 test-only embedder다."""

    def embed_document_groups(
        self,
        *,
        groups: tuple[VoyagePreChunkedDocumentGroup, ...],
    ) -> np.ndarray:
        vector_count = sum(len(group.chunks) for group in groups)
        vectors = np.zeros((vector_count, 1024), dtype=np.float32)
        for index in range(vector_count):
            vectors[index, index % 1024] = 1.0
        return vectors


def test_external_exact30_voyage_writer_stages_full_component_and_keeps_direct_tables_closed(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    materialization = _materialization()
    repository = PsycopgExternalExact30VoyageStagingRepository(
        database_dsn=isolated_postgres_cluster["rag_writer_dsn"],
    )

    receipts = repository.stage_component(
        records=materialization.records,
        context=materialization.context,
    )

    assert len(receipts) == 30
    assert all(receipt.component_generation_id == materialization.context.component_generation_id for receipt in receipts)
    assert all(receipt.component_scope == "EXACT30" for receipt in receipts)
    assert all(receipt.embedding_profile_id == "voyage_context_4_1024_v1" for receipt in receipts)
    assert all(receipt.source_reused is False for receipt in receipts)
    assert receipts[-1].state == "STAGED"
    assert receipts[-1].source_count == 30
    assert receipts[-1].chunk_count == materialization.context.expected_chunk_count

    resumed = repository.stage(record=materialization.records[0], context=materialization.context)
    assert resumed.source_reused is True
    assert resumed.state == "STAGED"
    assert resumed.source_count == 30
    assert resumed.chunk_count == materialization.context.expected_chunk_count

    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            """
            SELECT generation.state, generation.evaluation_status, generation.actual_source_count,
                   generation.actual_chunk_count, run.state, count(embedding.chunk_id)
            FROM rag_v2_immutable_component_generations AS generation
            JOIN rag_v2_immutable_materialization_runs AS run
              ON run.component_generation_id = generation.component_generation_id
            JOIN rag_v2_immutable_generation_embeddings AS embedding
              ON embedding.component_generation_id = generation.component_generation_id
            WHERE generation.component_generation_id = %s
            GROUP BY generation.state, generation.evaluation_status, generation.actual_source_count,
                     generation.actual_chunk_count, run.state
            """,
            (materialization.context.component_generation_id,),
        ).fetchone() == (
            "STAGING",
            "PENDING",
            30,
            materialization.context.expected_chunk_count,
            "STAGED",
            materialization.context.expected_chunk_count,
        )
        assert connection.execute(
            """
            SELECT count(*)
            FROM rag_v2_immutable_external_exact30_voyage_component_manifests
            WHERE component_generation_id = %s
            """,
            (materialization.context.component_generation_id,),
        ).fetchone() == (1,)

    with psycopg.connect(isolated_postgres_cluster["rag_writer_dsn"]) as connection:
        assert connection.execute("SELECT current_user").fetchone() == ("decision_rag_writer",)
        assert connection.execute(
            """
            SELECT has_function_privilege(
              current_user,
              'public.stage_rag_v2_immutable_external_exact30_voyage_document(jsonb)',
              'EXECUTE'
            )
            """
        ).fetchone() == (True,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT * FROM rag_v2_immutable_external_exact30_voyage_component_manifests")


def test_external_exact30_voyage_writer_rejects_allowlist_drift_before_persisting(
    isolated_postgres_cluster: dict[str, str],
) -> None:
    materialization = _materialization()
    payload = build_external_exact30_voyage_staging_payload(
        materialization.records[0],
        context=materialization.context,
    )
    source = payload["source"]
    assert isinstance(source, dict)
    source["sourceCardSha256"] = "0" * 64

    with pytest.raises(psycopg.Error):
        with psycopg.connect(isolated_postgres_cluster["rag_writer_dsn"], autocommit=False) as connection:
            with connection.transaction():
                connection.execute(
                    """
                    SELECT *
                    FROM public.stage_rag_v2_immutable_external_exact30_voyage_document(%s::jsonb)
                    """,
                    (psycopg.types.json.Jsonb(payload),),
                ).fetchall()

    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM rag_v2_immutable_source_revisions"
        ).fetchone() == (0,)


def test_external_exact30_voyage_repository_rejects_partial_component_before_connecting() -> None:
    materialization = _materialization()
    repository = PsycopgExternalExact30VoyageStagingRepository(database_dsn="postgresql://invalid")

    with pytest.raises(
        ExternalExact30VoyageStagingRepositoryError,
        match="EXTERNAL_EXACT30_VOYAGE_STAGE_COMPONENT_MEMBERSHIP",
    ):
        repository.stage_component(
            records=materialization.records[:-1],
            context=materialization.context,
        )


def _materialization():
    return materialize_external_exact30_public_voyage_component(
        tokenizer=_FixtureTokenizer(),
        embedder=_FixtureVoyageEmbedder(),
        corpus=load_external_processing_corpus(),
    )
