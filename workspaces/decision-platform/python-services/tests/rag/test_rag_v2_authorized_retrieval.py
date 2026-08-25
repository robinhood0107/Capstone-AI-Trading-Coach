from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import replace

import pytest

from app.rag.authorized_retrieval import (
    ExactIdentifierExtractor,
    QueryNormalizer,
)
from app.rag.rag_v2_authorized_retrieval import (
    RagV2AuthorizedHybridRetrieval,
    RagV2BundleScope,
    RagV2ChannelResult,
    RagV2RetrievalCandidate,
    RagV2RetrievalFailureCode,
    RagV2RrfFusion,
)

PROFILE = "bge_m3_local_1024_v1"
VOYAGE_PROFILE = "voyage_context_4_1024_v1"


class _StaticEmbedder:
    @property
    def embedding_profile_id(self) -> str:
        return PROFILE

    def embed_query(self, _: str) -> Sequence[float]:
        return [1.0] + [0.0] * 1023


class _RecordingEmbedder:
    def __init__(self, profile_id: str, vector: Sequence[float]) -> None:
        self.embedding_profile_id = profile_id
        self.vector = vector
        self.questions: list[str] = []

    def embed_query(self, question: str) -> Sequence[float]:
        self.questions.append(question)
        return self.vector


class _StaticExact:
    def __init__(self, result: RagV2ChannelResult) -> None:
        self.result = result

    def retrieve_exact(self, **_: object) -> RagV2ChannelResult:
        return self.result


class _StaticLexical:
    def __init__(self, result: RagV2ChannelResult) -> None:
        self.result = result

    def retrieve_lexical(self, **_: object) -> RagV2ChannelResult:
        return self.result


class _StaticDense:
    def __init__(self, result: RagV2ChannelResult) -> None:
        self.result = result

    def retrieve_dense(self, **_: object) -> RagV2ChannelResult:
        return self.result


def test_owner_bundle_scope_requires_an_explicit_owner_profile() -> None:
    with pytest.raises(ValueError, match="bundle scope is invalid"):
        RagV2BundleScope(
            claim_id="rvs_" + "a" * 32,
            owner_user_id="usr_demo_owner",
            session_id="req_v2_retrieval_000000000001",
            exact30_generation_id="rgr_" + "1" * 32,
            oa112_generation_id="rgr_" + "2" * 32,
            owner_private_generation_id="rgr_" + "3" * 32,
            embedding_profile_id=VOYAGE_PROFILE,
            owner_embedding_profile_id=None,
            policy_version=1,
            allowed_topics=("RISK",),
        )


class _RecordingDense(_StaticDense):
    def __init__(self, result: RagV2ChannelResult) -> None:
        super().__init__(result)
        self.calls: list[dict[str, object]] = []

    def retrieve_dense(self, **kwargs: object) -> RagV2ChannelResult:
        self.calls.append(kwargs)
        return self.result


def test_v2_retrieval_fuses_exact_oa_and_owner_channels_to_a_bounded_top_five() -> None:
    scope = _scope(owner_generation=True)
    exact = _candidate(1, scope, source_scope="EXACT30")
    oa = _candidate(2, scope, source_scope="OA112")
    owner = _candidate(3, scope, source_scope="OWNER_PRIVATE")
    fourth = _candidate(4, scope, source_scope="OA112")
    fifth = _candidate(5, scope, source_scope="EXACT30")
    sixth = _candidate(6, scope, source_scope="OA112")

    outcome = _retrieval(
        exact=RagV2ChannelResult("exact", (exact, oa, owner), complete=True),
        lexical=RagV2ChannelResult("lexical", (oa, exact, fourth, fifth), complete=True),
        dense=RagV2ChannelResult("dense", (owner, exact, oa, sixth), complete=True),
    ).retrieve(
        scope=scope,
        payload={
            "question": "005930과 금융공학 근거를 비교해 설명해 주세요.",
            "answerMode": "DETAILED",
            "relatedSymbols": ["005930"],
            "topics": ["FINANCIAL_ENGINEERING"],
        },
    )

    assert outcome.failure_code is None
    assert outcome.retrieval_permitted is True
    assert [item.chunk_id for item in outcome.evidence] == [
        exact.chunk_id,
        oa.chunk_id,
        owner.chunk_id,
        fourth.chunk_id,
        fifth.chunk_id,
    ]
    assert len(outcome.evidence) == 5
    assert outcome.distinct_source_count == 5
    assert outcome.external_generation_permitted is False
    assert not hasattr(outcome.evidence[0], "rrf_score")


def test_v2_retrieval_rejects_old_generation_or_other_owner_before_returning_evidence() -> None:
    scope = _scope(owner_generation=True)
    public = _candidate(1, scope, source_scope="EXACT30")
    leaked = replace(
        _candidate(2, scope, source_scope="OWNER_PRIVATE"),
        owner_user_id="usr_other_owner",
    )

    outcome = _retrieval(
        exact=RagV2ChannelResult("exact", (public,), complete=True),
        lexical=RagV2ChannelResult("lexical", (leaked,), complete=True),
        dense=RagV2ChannelResult("dense", (public, leaked), complete=True),
    ).retrieve(
        scope=scope,
        payload={"question": "근거를 보여 주세요.", "answerMode": "CONCISE"},
    )

    assert outcome.failure_code is RagV2RetrievalFailureCode.SCOPE_CHANGED
    assert outcome.evidence == ()


def test_v2_retrieval_rejects_owner_rows_when_no_active_owner_generation_is_pinned() -> None:
    scope = _scope(owner_generation=False)
    exact = _candidate(1, scope, source_scope="EXACT30")
    oa = _candidate(2, scope, source_scope="OA112")
    owner = _candidate(3, _scope(owner_generation=True), source_scope="OWNER_PRIVATE")

    outcome = _retrieval(
        exact=RagV2ChannelResult("exact", (exact,), complete=True),
        lexical=RagV2ChannelResult("lexical", (oa,), complete=True),
        dense=RagV2ChannelResult("dense", (owner,), complete=True),
    ).retrieve(
        scope=scope,
        payload={"question": "public corpus 근거를 보여 주세요.", "answerMode": "CONCISE"},
    )

    assert outcome.failure_code is RagV2RetrievalFailureCode.SCOPE_CHANGED
    assert outcome.evidence == ()


def test_v2_retrieval_requires_complete_bounded_channels_and_two_distinct_sources() -> None:
    scope = _scope(owner_generation=False)
    crowded = tuple(_candidate(index, scope, source_scope="EXACT30") for index in range(1, 32))
    outcome = _retrieval(
        exact=RagV2ChannelResult("exact", crowded, complete=True),
        lexical=RagV2ChannelResult("lexical", (), complete=True),
        dense=RagV2ChannelResult("dense", (), complete=True),
    ).retrieve(
        scope=scope,
        payload={"question": "근거", "answerMode": "CONCISE"},
    )

    assert outcome.failure_code is RagV2RetrievalFailureCode.CHANNEL_INCOMPLETE
    assert outcome.evidence == ()


def test_v2_retrieval_accepts_disjoint_lexical_and_dense_evidence_from_distinct_sources() -> None:
    scope = _scope(owner_generation=False)
    lexical = _candidate(1, scope, source_scope="EXACT30")
    dense = _candidate(2, scope, source_scope="OA112")

    outcome = _retrieval(
        exact=RagV2ChannelResult("exact", (), complete=True),
        lexical=RagV2ChannelResult("lexical", (lexical,), complete=True),
        dense=RagV2ChannelResult("dense", (dense,), complete=True),
    ).retrieve(
        scope=scope,
        payload={"question": "분산투자 위험 근거", "answerMode": "DETAILED"},
    )

    assert outcome.failure_code is None
    assert outcome.retrieval_permitted is True
    assert {item.source_id for item in outcome.evidence} == {
        lexical.source_id,
        dense.source_id,
    }


def test_v2_retrieval_accepts_cross_language_dense_only_evidence_from_distinct_sources() -> None:
    scope = _scope(owner_generation=False)
    first = _candidate(1, scope, source_scope="EXACT30")
    second = _candidate(2, scope, source_scope="OA112")

    outcome = _retrieval(
        exact=RagV2ChannelResult("exact", (), complete=True),
        lexical=RagV2ChannelResult("lexical", (), complete=True),
        dense=RagV2ChannelResult("dense", (first, second), complete=True),
    ).retrieve(
        scope=scope,
        payload={
            "question": "분산투자에서 자산 간 상관관계가 위험에 미치는 영향",
            "answerMode": "DETAILED",
        },
    )

    assert outcome.failure_code is None
    assert outcome.retrieval_permitted is True
    assert {item.source_id for item in outcome.evidence} == {
        first.source_id,
        second.source_id,
    }


def test_v2_retrieval_replaces_only_fifth_result_when_one_source_monopolizes_top_five() -> None:
    scope = _scope(owner_generation=False)
    first = _candidate(1, scope, source_scope="OA112")
    same_source = tuple(
        replace(
            _candidate(index, scope, source_scope="OA112"),
            source_id=first.source_id,
        )
        for index in range(2, 6)
    )
    independent = _candidate(6, scope, source_scope="EXACT30")

    outcome = _retrieval(
        exact=RagV2ChannelResult("exact", (), complete=True),
        lexical=RagV2ChannelResult("lexical", (), complete=True),
        dense=RagV2ChannelResult(
            "dense",
            (first, *same_source, independent),
            complete=True,
        ),
    ).retrieve(
        scope=scope,
        payload={
            "question": "분산투자에서 자산 간 상관관계가 위험에 미치는 영향",
            "answerMode": "DETAILED",
        },
    )

    assert outcome.failure_code is None
    assert [item.chunk_id for item in outcome.evidence] == [
        first.chunk_id,
        same_source[0].chunk_id,
        same_source[1].chunk_id,
        same_source[2].chunk_id,
        independent.chunk_id,
    ]
    assert outcome.distinct_source_count == 2


def test_v2_retrieval_allows_one_source_for_strong_llm_warning_classification() -> None:
    scope = _scope(owner_generation=False)
    first = _candidate(1, scope, source_scope="OA112")
    same_source = replace(
        _candidate(2, scope, source_scope="OA112"),
        source_id=first.source_id,
    )

    outcome = _retrieval(
        exact=RagV2ChannelResult("exact", (), complete=True),
        lexical=RagV2ChannelResult("lexical", (), complete=True),
        dense=RagV2ChannelResult("dense", (first, same_source), complete=True),
    ).retrieve(
        scope=scope,
        payload={"question": "분산투자 위험 근거", "answerMode": "DETAILED"},
    )

    assert outcome.failure_code is None
    assert outcome.retrieval_permitted is True
    assert outcome.distinct_source_count == 1


def test_v2_retrieval_rejects_when_all_channels_have_no_evidence() -> None:
    scope = _scope(owner_generation=False)

    outcome = _retrieval(
        exact=RagV2ChannelResult("exact", (), complete=True),
        lexical=RagV2ChannelResult("lexical", (), complete=True),
        dense=RagV2ChannelResult("dense", (), complete=True),
    ).retrieve(
        scope=scope,
        payload={"question": "분산투자 위험 근거", "answerMode": "DETAILED"},
    )

    assert outcome.failure_code is RagV2RetrievalFailureCode.INSUFFICIENT_EVIDENCE
    assert outcome.evidence == ()


def test_v2_retrieval_only_permits_external_generation_when_every_top_five_source_allows_it() -> (
    None
):
    scope = _scope(owner_generation=False)
    first = _candidate(1, scope, source_scope="EXACT30", external_processing_eligible=True)
    second = _candidate(2, scope, source_scope="OA112", external_processing_eligible=True)

    outcome = _retrieval(
        exact=RagV2ChannelResult("exact", (first,), complete=True),
        lexical=RagV2ChannelResult("lexical", (second,), complete=True),
        dense=RagV2ChannelResult("dense", (first, second), complete=True),
    ).retrieve(
        scope=scope,
        payload={"question": "근거 비교", "answerMode": "CONCISE"},
    )

    assert outcome.failure_code is None
    assert outcome.retrieval_permitted is True
    assert outcome.external_generation_permitted is True


def test_v2_retrieval_uses_separate_public_voyage_and_owner_bge_vectors_without_cross_space_scores() -> (
    None
):
    scope = _scope(
        owner_generation=True,
        public_profile=VOYAGE_PROFILE,
        owner_profile=PROFILE,
    )
    public = _candidate(
        1,
        scope,
        source_scope="EXACT30",
        external_processing_eligible=True,
    )
    owner = _candidate(2, scope, source_scope="OWNER_PRIVATE")
    public_embedder = _RecordingEmbedder(VOYAGE_PROFILE, [1.0] + [0.0] * 1023)
    owner_embedder = _RecordingEmbedder(PROFILE, [0.0, 1.0] + [0.0] * 1022)
    dense = _RecordingDense(RagV2ChannelResult("dense", (public, owner), complete=True))

    outcome = _retrieval(
        exact=RagV2ChannelResult("exact", (public,), complete=True),
        lexical=RagV2ChannelResult("lexical", (owner,), complete=True),
        dense=RagV2ChannelResult("dense", (public, owner), complete=True),
        query_embedder=public_embedder,
        owner_query_embedder=owner_embedder,
        dense_retriever=dense,
    ).retrieve(
        scope=scope,
        payload={
            "question": "공용 근거와 내 문서를 함께 비교해 주세요.",
            "answerMode": "CONCISE",
            "externalQueryConsentGranted": True,
        },
    )

    assert outcome.failure_code is None
    assert outcome.retrieval_permitted is True
    assert outcome.external_generation_permitted is False
    assert public_embedder.questions == ["공용 근거와 내 문서를 함께 비교해 주세요."]
    assert owner_embedder.questions == ["공용 근거와 내 문서를 함께 비교해 주세요."]
    assert dense.calls[0]["query_vector"] == (1.0,) + (0.0,) * 1023
    assert dense.calls[0]["owner_query_vector"] == (0.0, 1.0) + (0.0,) * 1022


def test_v2_retrieval_keeps_public_internal_document_identity_out_of_the_citation_projection() -> (
    None
):
    scope = _scope(owner_generation=False)
    exact = replace(
        _candidate(1, scope, source_scope="EXACT30"),
        document_id="doc_public_exact_0001",
    )
    oa = replace(
        _candidate(2, scope, source_scope="OA112"),
        document_id="doc_public_oa_0000001",
    )

    outcome = _retrieval(
        exact=RagV2ChannelResult("exact", (exact,), complete=True),
        lexical=RagV2ChannelResult("lexical", (oa,), complete=True),
        dense=RagV2ChannelResult("dense", (exact, oa), complete=True),
    ).retrieve(
        scope=scope,
        payload={"question": "근거 비교", "answerMode": "CONCISE"},
    )

    assert outcome.failure_code is None
    assert [item.document_id for item in outcome.evidence] == [
        "doc_public_exact_0001",
        "doc_public_oa_0000001",
    ]


def _retrieval(
    *,
    exact: RagV2ChannelResult,
    lexical: RagV2ChannelResult,
    dense: RagV2ChannelResult,
    query_embedder: object | None = None,
    owner_query_embedder: object | None = None,
    dense_retriever: object | None = None,
) -> RagV2AuthorizedHybridRetrieval:
    return RagV2AuthorizedHybridRetrieval(
        query_normalizer=QueryNormalizer(),
        exact_identifier_extractor=ExactIdentifierExtractor(),
        query_embedder=query_embedder or _StaticEmbedder(),
        owner_query_embedder=owner_query_embedder,
        exact_retriever=_StaticExact(exact),
        lexical_retriever=_StaticLexical(lexical),
        dense_retriever=dense_retriever or _StaticDense(dense),
        rrf_fusion=RagV2RrfFusion(),
    )


def _scope(
    *,
    owner_generation: bool,
    public_profile: str = PROFILE,
    owner_profile: str | None = None,
) -> RagV2BundleScope:
    return RagV2BundleScope(
        claim_id="rvs_" + "a" * 32,
        owner_user_id="usr_demo_owner",
        session_id="req_v2_retrieval_000000000001",
        exact30_generation_id="rgr_" + "1" * 32,
        oa112_generation_id="rgr_" + "2" * 32,
        owner_private_generation_id=("rgr_" + "3" * 32) if owner_generation else None,
        embedding_profile_id=public_profile,
        policy_version=1,
        allowed_topics=("FINANCIAL_ENGINEERING", "RISK"),
        owner_embedding_profile_id=(owner_profile or public_profile) if owner_generation else None,
    )


def _candidate(
    index: int,
    scope: RagV2BundleScope,
    *,
    source_scope: str,
    external_processing_eligible: bool = False,
) -> RagV2RetrievalCandidate:
    digest = hashlib.sha256(f"candidate-{index}".encode()).hexdigest()
    if source_scope == "EXACT30":
        generation_id = scope.exact30_generation_id
        owner_user_id = None
        document_id = None
        display_name = None
        canonical_url = f"https://public.example.com/exact/{index}"
        title = f"Exact source {index}"
    elif source_scope == "OA112":
        generation_id = scope.oa112_generation_id
        owner_user_id = None
        document_id = None
        display_name = None
        canonical_url = f"https://public.example.com/oa/{index}"
        title = f"OA source {index}"
    else:
        generation_id = scope.owner_private_generation_id or "rgr_" + "3" * 32
        owner_user_id = scope.owner_user_id
        document_id = f"doc_owner_document_{index:04d}"
        display_name = f"Owner document {index}"
        canonical_url = None
        title = None
    content = f"Canonical content for source {index}."
    return RagV2RetrievalCandidate(
        canonical_content=content,
        canonical_content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        canonical_https_url=canonical_url,
        chunk_id=f"rag_v2_chk_{digest[:32]}",
        document_id=document_id,
        embedding_profile_id=(
            scope.owner_embedding_profile_id
            if source_scope == "OWNER_PRIVATE"
            else scope.embedding_profile_id
        ),
        external_processing_eligible=external_processing_eligible,
        generation_id=generation_id,
        heading_path=("Evidence",),
        locator={"section": f"Section {index}"},
        owner_user_id=owner_user_id,
        policy_version=scope.policy_version,
        sanitized_display_name=display_name,
        scope_claim_id=scope.claim_id,
        session_id=scope.session_id,
        source_id=f"src_v2_fixture_{index:03d}",
        source_revision_id=f"srv_v2_fixture_{index:03d}",
        source_scope=source_scope,
        title=title,
        topics=("FINANCIAL_ENGINEERING",),
    )
