from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import replace

import pytest

from app.rag.authorized_retrieval import (
    ALLOWED_RAG_TOPICS,
    SYNONYM_PAIRS_V1,
    AuthorizedDenseRetriever,
    AuthorizedExactRetriever,
    AuthorizedHybridRetrieval,
    AuthorizedLexicalRetriever,
    AuthorizedRetrievalScope,
    ChannelResult,
    EvidenceSufficiencyPolicy,
    ExactIdentifierExtractor,
    NormalizedRetrievalQuery,
    QueryEmbedder,
    QueryNormalizer,
    QueryValidationError,
    RetrievalCandidate,
    RetrievalFailureCode,
    RrfFusion,
)
from app.rag.source_card_corpus import REPO_ROOT

OWNER_ID = "00000000-0000-0000-0000-000000000001"
OTHER_OWNER_ID = "00000000-0000-0000-0000-000000000002"
SESSION_ID = "10000000-0000-0000-0000-000000000001"
OTHER_SESSION_ID = "10000000-0000-0000-0000-000000000002"
CLAIM_ID = "rag_scope_" + "a" * 32
GENERATION_ID = "rag_gen_" + "b" * 32
PROFILE_ID = "bge_m3_local_1024_v1"


def _scope() -> AuthorizedRetrievalScope:
    return AuthorizedRetrievalScope(
        claim_id=CLAIM_ID,
        owner_user_id=OWNER_ID,
        session_id=SESSION_ID,
        allowed_topics=("FINANCIAL_ENGINEERING", "RISK"),
        generation_id=GENERATION_ID,
        embedding_profile_id=PROFILE_ID,
        policy_version=2,
    )


def _candidate(
    index: int,
    *,
    source_id: str | None = None,
    card_id: str | None = None,
    evidence_class: str = "PRIMARY_RESEARCH",
    model_sensitive: bool = False,
    assumption_keys: tuple[str, ...] = (),
    limitations: tuple[str, ...] = ("bounded limitation",),
    contradicts_card_ids: tuple[str, ...] = (),
    owner_user_id: str = OWNER_ID,
    session_id: str = SESSION_ID,
    claim_id: str = CLAIM_ID,
    generation_id: str = GENERATION_ID,
    embedding_profile_id: str = PROFILE_ID,
    policy_version: int = 2,
    access_level: str = "PUBLIC",
    tier: str = "PROJECT",
    source_status: str = "VERIFIED",
    public_topics: tuple[str, ...] = ("FINANCIAL_ENGINEERING",),
) -> RetrievalCandidate:
    resolved_source_id = source_id or f"src_project_fixture_{index:03d}"
    return RetrievalCandidate(
        chunk_revision_id=f"rag_chk_{index:032x}",
        source_revision_id=f"src_rev_{index:032x}",
        source_id=resolved_source_id,
        card_id=card_id or f"card_fixture_{index:03d}",
        title=f"fixture {index}",
        heading_path=("핵심 claim",),
        canonical_content=f"fixture bounded evidence {index}",
        canonical_content_hash=f"{index:064x}",
        topic="bsm_risk_neutral",
        public_topics=public_topics,
        access_level=access_level,
        tier=tier,
        source_status=source_status,
        evidence_class=evidence_class,
        model_sensitive=model_sensitive,
        assumption_keys=assumption_keys,
        limitations=limitations,
        contradicts_card_ids=contradicts_card_ids,
        scope_claim_id=claim_id,
        owner_user_id=owner_user_id,
        session_id=session_id,
        generation_id=generation_id,
        embedding_profile_id=embedding_profile_id,
        policy_version=policy_version,
    )


class _StaticEmbedder(QueryEmbedder):
    def __init__(self, vector: Sequence[float] | None = None) -> None:
        self.vector = tuple(vector or ([1.0] + [0.0] * 1023))
        self.calls = 0

    @property
    def embedding_profile_id(self) -> str:
        return PROFILE_ID

    def embed_query(self, question: str) -> Sequence[float]:
        self.calls += 1
        return self.vector


class _StaticExact(AuthorizedExactRetriever):
    def __init__(self, result: ChannelResult) -> None:
        self.result = result
        self.calls = 0

    def retrieve_exact(
        self,
        *,
        scope: AuthorizedRetrievalScope,
        query: NormalizedRetrievalQuery,
        identifiers: tuple[str, ...],
    ) -> ChannelResult:
        self.calls += 1
        return self.result


class _StaticLexical(AuthorizedLexicalRetriever):
    def __init__(self, result: ChannelResult) -> None:
        self.result = result
        self.calls = 0

    def retrieve_lexical(
        self,
        *,
        scope: AuthorizedRetrievalScope,
        query: NormalizedRetrievalQuery,
    ) -> ChannelResult:
        self.calls += 1
        return self.result


class _StaticDense(AuthorizedDenseRetriever):
    def __init__(self, result: ChannelResult) -> None:
        self.result = result
        self.calls = 0

    def retrieve_dense(
        self,
        *,
        scope: AuthorizedRetrievalScope,
        query: NormalizedRetrievalQuery,
        query_vector: tuple[float, ...],
    ) -> ChannelResult:
        self.calls += 1
        return self.result


def _channel(name: str, *items: RetrievalCandidate, complete: bool = True) -> ChannelResult:
    return ChannelResult(channel=name, items=tuple(items), complete=complete)


def _hybrid(
    *,
    exact: ChannelResult,
    lexical: ChannelResult,
    dense: ChannelResult,
    vector: Sequence[float] | None = None,
) -> AuthorizedHybridRetrieval:
    return AuthorizedHybridRetrieval(
        query_normalizer=QueryNormalizer(),
        exact_identifier_extractor=ExactIdentifierExtractor(),
        query_embedder=_StaticEmbedder(vector),
        exact_retriever=_StaticExact(exact),
        lexical_retriever=_StaticLexical(lexical),
        dense_retriever=_StaticDense(dense),
        rrf_fusion=RrfFusion(),
        evidence_sufficiency_policy=EvidenceSufficiencyPolicy(),
    )


def test_query_normalizer_enforces_contract_bounds_and_forbidden_controls() -> None:
    normalizer = QueryNormalizer()
    normalized = normalizer.normalize(
        {
            "question": "BSM 위험중립 가격을 실제 확률로 읽어도 되나요?",
            "answerMode": "CONCISE",
            "relatedSymbols": ["005930"],
            "topics": ["FINANCIAL_ENGINEERING", "RISK"],
        }
    )

    assert normalized.question.startswith("BSM")
    assert normalized.related_symbols == ("005930",)
    assert normalized.topics == ("FINANCIAL_ENGINEERING", "RISK")
    assert normalized.answer_mode == "CONCISE"
    assert normalized.internal_channel_limit == 30
    assert normalized.internal_final_limit == 5
    assert len(SYNONYM_PAIRS_V1) == 12
    assert frozenset(normalized.topics) <= ALLOWED_RAG_TOPICS

    invalid_payloads = (
        {"question": "", "answerMode": "CONCISE"},
        {"question": "\ud800", "answerMode": "CONCISE"},
        {"question": "e\u0301", "answerMode": "CONCISE"},
        {"question": "가" * 1001, "answerMode": "CONCISE"},
        {"question": "a" * 1000, "answerMode": "CONCISE", "topK": 1},
        {"question": "ok", "answerMode": "CONCISE", "provider": "LOCAL"},
        {"question": "ok", "answerMode": "CONCISE", "relatedSymbols": ["5930"]},
        {
            "question": "ok",
            "answerMode": "CONCISE",
            "relatedSymbols": [f"{value:06d}" for value in range(6)],
        },
        {"question": "ok", "answerMode": "CONCISE", "topics": ["INTERNAL"]},
        {
            "question": "ok",
            "answerMode": "CONCISE",
            "topics": list(ALLOWED_RAG_TOPICS),
        },
    )
    for payload in invalid_payloads:
        with pytest.raises(QueryValidationError):
            normalizer.normalize(payload)


def test_synonym_expansion_is_versioned_bounded_and_one_pass() -> None:
    query = QueryNormalizer().normalize(
        {
            "question": "위험중립 가격과 토큰 발급 유량 제한을 설명해 주세요.",
            "answerMode": "DETAILED",
        }
    )

    assert query.synonym_version == "s4-rag-synonyms-v1"
    assert "risk neutral" in query.lexical_query
    assert "oauth token" in query.lexical_query
    assert "rate limit" in query.lexical_query
    assert query.lexical_query.count("risk neutral") == 1
    assert len(query.lexical_query.encode("utf-8")) <= 12_288


def test_exact_identifier_extractor_uses_literal_bounded_identifiers() -> None:
    extracted = ExactIdentifierExtractor().extract(
        "005930, src_project_kis_rate_limit_token_001, FHKST01010100; "
        "0059300 and .* DROP TABLE rag_sources"
    )

    assert extracted == (
        "005930",
        "FHKST01010100",
        "src_project_kis_rate_limit_token_001",
    )
    assert "0059300" not in extracted
    assert ".*" not in extracted
    assert "DROP" not in extracted
    with pytest.raises(QueryValidationError):
        ExactIdentifierExtractor().extract("\ud800")


def test_hybrid_rejects_combined_exact_identifier_count_before_embedding() -> None:
    exact = _StaticExact(_channel("exact"))
    lexical = _StaticLexical(_channel("lexical"))
    dense = _StaticDense(_channel("dense"))
    embedder = _StaticEmbedder()
    hybrid = AuthorizedHybridRetrieval(
        query_normalizer=QueryNormalizer(),
        exact_identifier_extractor=ExactIdentifierExtractor(),
        query_embedder=embedder,
        exact_retriever=exact,
        lexical_retriever=lexical,
        dense_retriever=dense,
        rrf_fusion=RrfFusion(),
        evidence_sufficiency_policy=EvidenceSufficiencyPolicy(),
    )

    outcome = hybrid.retrieve(
        scope=_scope(),
        payload={
            "question": " ".join(f"{value:06d}" for value in range(16)),
            "answerMode": "CONCISE",
            "relatedSymbols": [f"{100_000 + value:06d}" for value in range(5)],
        },
    )

    assert outcome.failure_code is RetrievalFailureCode.INVALID_QUERY
    assert outcome.evidence == ()
    assert embedder.calls == exact.calls == lexical.calls == dense.calls == 0


def test_rrf_uses_k60_deduplicates_channel_and_applies_deterministic_tie_break() -> None:
    first = _candidate(1, source_id="src_project_a_001")
    second = _candidate(2, source_id="src_project_b_001")
    third = _candidate(3, source_id="src_project_c_001")

    fused = RrfFusion().fuse(
        (
            _channel("exact", second, second, first),
            _channel("lexical", first, third),
            _channel("dense", third, first),
        )
    )

    assert [item.candidate.chunk_revision_id for item in fused] == [
        first.chunk_revision_id,
        third.chunk_revision_id,
        second.chunk_revision_id,
    ]
    assert fused[0].channel_count == 3
    assert fused[0].best_rank == 1
    assert fused[0].exact_rank == 3
    assert math.isclose(fused[0].rrf_score, 1 / 63 + 1 / 61 + 1 / 62)


def test_rrf_exact_presence_precedes_utf8_identity_when_other_ties_match() -> None:
    exact_candidate = _candidate(2, source_id="src_project_z_001")
    lexical_candidate = _candidate(1, source_id="src_project_a_001")

    fused = RrfFusion().fuse(
        (
            _channel("exact", exact_candidate),
            _channel("lexical", lexical_candidate),
            _channel("dense"),
        )
    )

    assert fused[0].candidate == exact_candidate


def test_hybrid_retrieval_returns_only_sufficient_active_scoped_evidence() -> None:
    first = _candidate(1)
    second = _candidate(2)
    hybrid = _hybrid(
        exact=_channel("exact", first),
        lexical=_channel("lexical", second, first),
        dense=_channel("dense", first, second),
    )

    outcome = hybrid.retrieve(
        scope=_scope(),
        payload={
            "question": "두 근거로 VaR와 ES 차이를 설명해 주세요.",
            "answerMode": "CONCISE",
            "topics": ["FINANCIAL_ENGINEERING", "RISK"],
        },
    )

    assert outcome.failure_code is None
    assert len(outcome.evidence) == 2
    assert outcome.distinct_source_count == 2
    assert outcome.generation_permitted
    assert not hasattr(outcome.evidence[0], "rrf_score")


def test_model_sensitive_evidence_requires_primary_assumption_and_limitation() -> None:
    key = "RISK_NEUTRAL_NOT_PHYSICAL_PROBABILITY"
    sensitive = _candidate(
        1,
        model_sensitive=True,
        assumption_keys=(key,),
        limitations=("physical probability를 보장하지 않는다.",),
    )
    supporting = _candidate(2, evidence_class="PRIMARY_RESEARCH")
    passing = _hybrid(
        exact=_channel("exact", sensitive),
        lexical=_channel("lexical", supporting),
        dense=_channel("dense", sensitive, supporting),
    ).retrieve(
        scope=_scope(),
        payload={
            "question": "BSM 위험중립 가격은 실제 확률인가요?",
            "answerMode": "DETAILED",
            "topics": ["FINANCIAL_ENGINEERING"],
        },
    )
    assert passing.failure_code is None

    missing_assumption = replace(sensitive, assumption_keys=())
    failing = _hybrid(
        exact=_channel("exact", missing_assumption),
        lexical=_channel("lexical", supporting),
        dense=_channel("dense", missing_assumption, supporting),
    ).retrieve(
        scope=_scope(),
        payload={
            "question": "BSM 위험중립 가격은 실제 확률인가요?",
            "answerMode": "DETAILED",
            "topics": ["FINANCIAL_ENGINEERING"],
        },
    )
    assert failing.failure_code is RetrievalFailureCode.INSUFFICIENT_EVIDENCE
    assert failing.evidence == ()
    assert not failing.generation_permitted


def test_contradiction_is_preserved_as_bounded_conflict_flag() -> None:
    first = _candidate(1, card_id="card_first", contradicts_card_ids=("card_second",))
    second = _candidate(2, card_id="card_second")
    outcome = _hybrid(
        exact=_channel("exact", first),
        lexical=_channel("lexical", second),
        dense=_channel("dense", first, second),
    ).retrieve(
        scope=_scope(),
        payload={
            "question": "서로 다른 근거를 비교해 주세요.",
            "answerMode": "CONCISE",
            "topics": ["FINANCIAL_ENGINEERING"],
        },
    )

    assert outcome.failure_code is None
    assert outcome.conflict_detected
    assert outcome.conflicting_card_ids == ("card_first", "card_second")


@pytest.mark.parametrize(
    ("tamper", "value"),
    (
        ("owner_user_id", OTHER_OWNER_ID),
        ("session_id", OTHER_SESSION_ID),
        ("scope_claim_id", "rag_scope_" + "f" * 32),
        ("generation_id", "rag_gen_" + "f" * 32),
        ("embedding_profile_id", "voyage_context_4_1024_v1"),
        ("policy_version", 3),
        ("access_level", "INTERNAL"),
        ("tier", "OFFICIAL"),
        ("source_status", "UNVERIFIED"),
        ("public_topics", ("API",)),
    ),
)
def test_post_fusion_scope_tamper_fails_closed_with_exact_zero(
    tamper: str,
    value: object,
) -> None:
    safe = _candidate(1)
    tampered = replace(_candidate(2), **{tamper: value})
    outcome = _hybrid(
        exact=_channel("exact", safe),
        lexical=_channel("lexical", tampered),
        dense=_channel("dense", safe, tampered),
    ).retrieve(
        scope=_scope(),
        payload={
            "question": "authorized evidence",
            "answerMode": "CONCISE",
            "topics": ["FINANCIAL_ENGINEERING"],
        },
    )

    assert outcome.failure_code is RetrievalFailureCode.SCOPE_CHANGED
    assert outcome.evidence == ()
    assert outcome.distinct_source_count == 0
    assert not outcome.generation_permitted


def test_channel_timeout_or_partial_result_discards_every_channel() -> None:
    first = _candidate(1)
    second = _candidate(2)
    outcome = _hybrid(
        exact=_channel("exact", first),
        lexical=_channel("lexical", second, complete=False),
        dense=_channel("dense", first, second),
    ).retrieve(
        scope=_scope(),
        payload={
            "question": "bounded evidence",
            "answerMode": "CONCISE",
            "topics": ["FINANCIAL_ENGINEERING"],
        },
    )

    assert outcome.failure_code is RetrievalFailureCode.CHANNEL_INCOMPLETE
    assert outcome.evidence == ()


def test_channel_timeout_discards_every_channel() -> None:
    class _TimeoutExact(_StaticExact):
        def retrieve_exact(
            self,
            *,
            scope: AuthorizedRetrievalScope,
            query: NormalizedRetrievalQuery,
            identifiers: tuple[str, ...],
        ) -> ChannelResult:
            self.calls += 1
            raise TimeoutError("fixture timeout")

    first = _candidate(1)
    exact = _TimeoutExact(_channel("exact", first))
    lexical = _StaticLexical(_channel("lexical", first))
    dense = _StaticDense(_channel("dense", first))
    hybrid = AuthorizedHybridRetrieval(
        query_normalizer=QueryNormalizer(),
        exact_identifier_extractor=ExactIdentifierExtractor(),
        query_embedder=_StaticEmbedder(),
        exact_retriever=exact,
        lexical_retriever=lexical,
        dense_retriever=dense,
        rrf_fusion=RrfFusion(),
        evidence_sufficiency_policy=EvidenceSufficiencyPolicy(),
    )

    outcome = hybrid.retrieve(
        scope=_scope(),
        payload={
            "question": "bounded evidence",
            "answerMode": "CONCISE",
            "topics": ["FINANCIAL_ENGINEERING"],
        },
    )

    assert outcome.failure_code is RetrievalFailureCode.CHANNEL_UNAVAILABLE
    assert outcome.evidence == ()
    assert exact.calls == 1
    assert lexical.calls == dense.calls == 0


def test_nan_query_vector_fails_before_any_retrieval_channel() -> None:
    first = _candidate(1)
    exact = _StaticExact(_channel("exact", first))
    lexical = _StaticLexical(_channel("lexical", first))
    dense = _StaticDense(_channel("dense", first))
    hybrid = AuthorizedHybridRetrieval(
        query_normalizer=QueryNormalizer(),
        exact_identifier_extractor=ExactIdentifierExtractor(),
        query_embedder=_StaticEmbedder([float("nan")] + [0.0] * 1023),
        exact_retriever=exact,
        lexical_retriever=lexical,
        dense_retriever=dense,
        rrf_fusion=RrfFusion(),
        evidence_sufficiency_policy=EvidenceSufficiencyPolicy(),
    )

    outcome = hybrid.retrieve(
        scope=_scope(),
        payload={
            "question": "bounded evidence",
            "answerMode": "CONCISE",
            "topics": ["FINANCIAL_ENGINEERING"],
        },
    )

    assert outcome.failure_code is RetrievalFailureCode.QUERY_EMBEDDING_INVALID
    assert exact.calls == lexical.calls == dense.calls == 0


def test_no_evidence_refuses_generation() -> None:
    outcome = _hybrid(
        exact=_channel("exact"),
        lexical=_channel("lexical"),
        dense=_channel("dense"),
    ).retrieve(
        scope=_scope(),
        payload={
            "question": "corpus 밖의 근거 없는 질문",
            "answerMode": "CONCISE",
            "topics": ["FINANCIAL_ENGINEERING"],
        },
    )

    assert outcome.failure_code is RetrievalFailureCode.INSUFFICIENT_EVIDENCE
    assert outcome.evidence == ()
    assert not outcome.generation_permitted


def test_dense_only_top_candidate_is_not_sufficient_relevance() -> None:
    first = _candidate(1)
    second = _candidate(2)
    outcome = _hybrid(
        exact=_channel("exact"),
        lexical=_channel("lexical"),
        dense=_channel("dense", first, second),
    ).retrieve(
        scope=_scope(),
        payload={
            "question": "dense only",
            "answerMode": "CONCISE",
            "topics": ["FINANCIAL_ENGINEERING"],
        },
    )

    assert outcome.failure_code is RetrievalFailureCode.INSUFFICIENT_EVIDENCE
    assert outcome.evidence == ()


def test_tracked_s4_3_benchmark_is_hash_bound_and_passed() -> None:
    report_path = REPO_ROOT / "capstone-rag/reports/s4-3-authorized-retrieval-benchmark.v1.json"
    query_path = REPO_ROOT / "capstone-rag/eval/s4-3-authorized-retrieval-smoke.v1.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    expected_hash = report.pop("benchmarkReportSha256")
    actual_hash = hashlib.sha256(
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

    assert actual_hash == expected_hash
    assert report["commitSha"] == "bb64fed76f37f297796d8fac02d2b8a97ab78d34"
    assert report["querySetSha256"] == hashlib.sha256(query_path.read_bytes()).hexdigest()
    assert report["status"] == "PASS"
    assert report["expectedTop5HitRate"] == 1.0
    assert report["noEvidenceRefusalRate"] == 1.0
    assert report["warmup"] >= 20
    assert report["measured"] >= 100
    assert report["stagesMs"]["total"]["p95"] <= 1500.0
    assert report["physicalCalls"]["providerTotal"] == 0
