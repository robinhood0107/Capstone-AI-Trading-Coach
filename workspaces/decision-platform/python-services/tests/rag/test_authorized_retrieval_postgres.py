from __future__ import annotations

import hashlib
from dataclasses import replace

import numpy as np
import psycopg
import pytest
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
    PsycopgAuthorizedRetrievalAdapter,
    build_immutable_card_evidence,
)
from app.rag.bge_artifact import BgeVerifiedPacket
from app.rag.bge_full_generation import (
    BgeBatchBenchmarkReceipt,
    BgeGenerationBenchmarkReceipt,
    PsycopgBgeFullGenerationAdminRepository,
    PsycopgBgeFullGenerationReader,
    PsycopgBgeFullGenerationWriterRepository,
    activate_bge_full_generation,
    execute_bge_full_generation,
    prepare_bge_full_generation,
    verify_bge_full_generation_parity,
)
from app.rag.source_card_corpus import load_frozen_source_card_corpus


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
    @property
    def embedding_profile_id(self) -> str:
        return "bge_m3_local_1024_v1"

    def embed(self, texts: tuple[str, ...]) -> NDArray[np.float32]:
        rows: list[NDArray[np.float32]] = []
        for text in texts:
            seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
            vector = np.zeros(1024, dtype=np.float32)
            vector[seed % 1024] = np.float32(1.0)
            rows.append(vector)
        return np.stack(rows)

    def embed_query(self, question: str) -> tuple[float, ...]:
        return tuple(float(value) for value in self.embed((question,))[0])


def test_postgres_three_channels_share_only_active_opaque_scope(
    isolated_postgres_cluster: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cluster = isolated_postgres_cluster
    corpus = load_frozen_source_card_corpus()
    plan = prepare_bge_full_generation(
        corpus=corpus,
        tokenizer=_WhitespaceTokenizer(),
        artifact=_artifact_receipt(),
        batch_benchmark=_batch_benchmark(),
    )
    monkeypatch.setenv("RAG_SOURCE_REGISTER_TARGET", "testcontainers")
    materialized = execute_bge_full_generation(
        plan=plan,
        embedder=_FixtureEmbedder(),
        repository=PsycopgBgeFullGenerationWriterRepository(
            database_dsn=cluster["rag_writer_dsn"],
        ),
    )
    parity = verify_bge_full_generation_parity(
        materialized=materialized,
        reader=PsycopgBgeFullGenerationReader(
            database_dsn=cluster["rag_admin_dsn"],
        ),
    )
    activation = activate_bge_full_generation(
        materialized=materialized,
        parity=parity,
        benchmark=_final_benchmark(),
        expected_current_generation_id=None,
        expected_policy_version=1,
        approved_by_audit_ref="s4-3-fixture-approval-0001",
        repository=PsycopgBgeFullGenerationAdminRepository(
            database_dsn=cluster["rag_admin_dsn"],
        ),
    )
    assert activation.generation_status == "ACTIVE"

    owner_user_id = "usr_demo_user"
    session_id = "s4-3-session-00000001"
    allowed_topics = (
        "API",
        "DATA",
        "FINANCIAL_ENGINEERING",
        "METHODOLOGY",
        "RISK",
    )
    with psycopg.connect(cluster["app_dsn"], autocommit=False) as connection:
        connection.execute(
            "SELECT set_config('app.actor_user_id', %s, true)",
            (owner_user_id,),
        )
        claim_row = connection.execute(
            """
            SELECT scope_claim_id, owner_user_id, session_id, allowed_topics,
                   active_generation_id, effective_profile_id, policy_version
            FROM public.create_rag_retrieval_scope_claim(%s, %s, %s)
            """,
            (owner_user_id, session_id, list(allowed_topics)),
        ).fetchone()
        connection.commit()
    assert claim_row is not None
    scope = AuthorizedRetrievalScope(
        claim_id=claim_row[0],
        owner_user_id=claim_row[1],
        session_id=claim_row[2],
        allowed_topics=tuple(claim_row[3]),
        generation_id=claim_row[4],
        embedding_profile_id=claim_row[5],
        policy_version=claim_row[6],
    )
    assert scope.generation_id == plan.generation_id

    adapter = PsycopgAuthorizedRetrievalAdapter(
        database_dsn=cluster["rag_query_dsn"],
        card_evidence=build_immutable_card_evidence(corpus.cards),
    )
    normalizer = QueryNormalizer()
    api_query = normalizer.normalize(
        {
            "question": (
                "src_project_kis_rate_limit_token_001의 FHKST01010100 "
                "토큰 발급 유량 제한을 설명해 주세요."
            ),
            "answerMode": "CONCISE",
            "topics": ["API"],
        }
    )
    identifiers = ExactIdentifierExtractor().extract(api_query.question)
    exact = adapter.retrieve_exact(
        scope=scope,
        query=api_query,
        identifiers=identifiers,
    )
    lexical = adapter.retrieve_lexical(scope=scope, query=api_query)
    dense = adapter.retrieve_dense(
        scope=scope,
        query=api_query,
        query_vector=_FixtureEmbedder().embed_query(api_query.question),
    )

    assert exact.complete and lexical.complete and dense.complete
    assert exact.items[0].source_id == "src_project_kis_rate_limit_token_001"
    assert 1 <= len(lexical.items) <= 30
    assert 1 <= len(dense.items) <= 30
    for channel in (exact, lexical, dense):
        assert all(item.scope_claim_id == scope.claim_id for item in channel.items)
        assert all(item.owner_user_id == owner_user_id for item in channel.items)
        assert all(item.session_id == session_id for item in channel.items)
        assert all(item.generation_id == plan.generation_id for item in channel.items)
        assert all(item.access_level == "PUBLIC" for item in channel.items)
        assert all(item.tier == "PROJECT" for item in channel.items)
        assert all(item.source_status == "VERIFIED" for item in channel.items)

    hybrid = AuthorizedHybridRetrieval(
        query_normalizer=normalizer,
        exact_identifier_extractor=ExactIdentifierExtractor(),
        query_embedder=_FixtureEmbedder(),
        exact_retriever=adapter,
        lexical_retriever=adapter,
        dense_retriever=adapter,
        rrf_fusion=RrfFusion(),
        evidence_sufficiency_policy=EvidenceSufficiencyPolicy(),
    )
    outcome = hybrid.retrieve(
        scope=scope,
        payload={
            "question": (
                "src_project_bsm_risk_neutral_001와 VaR ES 근거를 함께 "
                "사용해 위험중립 확률의 한계를 설명해 주세요."
            ),
            "answerMode": "DETAILED",
            "topics": ["FINANCIAL_ENGINEERING", "RISK"],
        },
    )
    assert outcome.failure_code is None
    assert outcome.generation_permitted
    assert outcome.distinct_source_count >= 2

    for crossed_scope in (
        replace(scope, owner_user_id="usr_demo_admin"),
        replace(scope, session_id="s4-3-session-00000002"),
        replace(scope, claim_id="rag_scope_" + "f" * 32),
    ):
        assert adapter.retrieve_exact(
            scope=crossed_scope,
            query=api_query,
            identifiers=identifiers,
        ).items == ()
        assert adapter.retrieve_lexical(
            scope=crossed_scope,
            query=api_query,
        ).items == ()
        assert adapter.retrieve_dense(
            scope=crossed_scope,
            query=api_query,
            query_vector=_FixtureEmbedder().embed_query(api_query.question),
        ).items == ()

    malicious_query = normalizer.normalize(
        {
            "question": "%' OR 1=1; DROP TABLE rag_sources; --",
            "answerMode": "CONCISE",
            "topics": ["API"],
        }
    )
    assert adapter.retrieve_exact(
        scope=scope,
        query=malicious_query,
        identifiers=ExactIdentifierExtractor().extract(malicious_query.question),
    ).items == ()

    with psycopg.connect(cluster["rag_query_dsn"]) as connection:
        assert connection.execute("SHOW statement_timeout").fetchone() == ("1500ms",)
    _assert_forbidden(cluster["rag_query_dsn"], "SELECT * FROM rag_chunk_revisions")
    _assert_forbidden(
        cluster["rag_query_dsn"],
        "INSERT INTO rag_retrieval_scope_claims DEFAULT VALUES",
    )
    _assert_forbidden(cluster["rag_query_dsn"], "CREATE TABLE forbidden_table(id integer)")
    _assert_forbidden(cluster["app_dsn"], "SELECT * FROM rag_chunk_embeddings")


def _assert_forbidden(database_dsn: str, statement: str) -> None:
    with psycopg.connect(database_dsn) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(statement).fetchone()


def _artifact_receipt() -> BgeVerifiedPacket:
    return BgeVerifiedPacket(
        revision="5617a9f61b028005a4858fdac845db406aefb181",
        file_count=10,
        total_bytes=2_289_781_803,
        file_manifest_sha256=(
            "a0ae6372b2d735b593d806d24c1155cb48dd7188adebe7d6b7619a1622fb71aa"
        ),
    )


def _batch_benchmark() -> BgeBatchBenchmarkReceipt:
    return BgeBatchBenchmarkReceipt(
        selected_batch_size=16,
        candidates=(16, 32, 64),
        peak_rss_bytes=((16, 4_000_000_000), (32, 5_000_000_000), (64, 7_000_000_000)),
        elapsed_ms=((16, 1_000.0), (32, 900.0), (64, 850.0)),
        environment_fingerprint_sha256="1" * 64,
        benchmark_sha256="2" * 64,
    )


def _final_benchmark() -> BgeGenerationBenchmarkReceipt:
    return BgeGenerationBenchmarkReceipt(
        report_sha256="3" * 64,
        query_set_sha256="4" * 64,
        environment_fingerprint_sha256="1" * 64,
        warmup_count=20,
        measured_count=100,
        warm_p95_ms=140.0,
        provider_physical_calls=0,
        voyage_physical_calls=0,
        gemini_physical_calls=0,
        openai_physical_calls=0,
        passed=True,
    )
