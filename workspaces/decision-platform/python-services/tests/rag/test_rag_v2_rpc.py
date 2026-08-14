from __future__ import annotations

import hashlib
from dataclasses import dataclass

import grpc
import pytest
from grpc_reflection.v1alpha import reflection_pb2, reflection_pb2_grpc

from app.generated import rag_v2_pb2, rag_v2_pb2_grpc
from app.rag.rag_v2_authorized_retrieval import (
    RagV2BundleScope,
    RagV2RetrievalCandidate,
    RagV2RetrievalExecution,
    RagV2RetrievalFailureCode,
    RagV2RetrievalOutcome,
)
from app.rag.rag_v2_rpc import (
    BgeRagV2RetrievalOnlyEngine,
    ProfileSelectedRagV2RetrievalOnlyEngine,
    RagV2EngineResult,
    RagV2RpcStatus,
    create_rag_v2_server,
)


SHARED_SECRET = "rag-v2-grpc-shared-secret-for-s4-7d-tests-0001"
AUTH = (("x-decision-rag-v2-grpc-auth", SHARED_SECRET),)


@dataclass(frozen=True)
class _Settings:
    bind_address: str = "127.0.0.1:0"
    shared_secret: str = SHARED_SECRET


class _ScopeReader:
    def __init__(self, scope: RagV2BundleScope) -> None:
        self.scope = scope
        self.calls = 0

    def read_scope_by_claim(
        self,
        *,
        claim_id: str,
        session_id: str,
    ) -> RagV2BundleScope:
        self.calls += 1
        assert claim_id == self.scope.claim_id
        assert session_id == self.scope.session_id
        return self.scope


class _Retrieval:
    def __init__(self, outcome: RagV2RetrievalOutcome) -> None:
        self.outcome = outcome
        self.calls = 0

    def retrieve(
        self,
        *,
        scope: RagV2BundleScope,
        payload: dict[str, object],
    ) -> RagV2RetrievalOutcome:
        self.calls += 1
        assert payload["answerMode"] in {"CONCISE", "DETAILED"}
        if self.outcome.evidence:
            assert scope.claim_id == self.outcome.evidence[0].scope_claim_id
        return self.outcome


class _ExecutionRetrieval(_Retrieval):
    def __init__(
        self,
        outcome: RagV2RetrievalOutcome,
        *,
        voyage_physical_calls: int,
    ) -> None:
        super().__init__(outcome)
        self._voyage_physical_calls = voyage_physical_calls

    def retrieve_with_execution(
        self,
        *,
        scope: RagV2BundleScope,
        payload: dict[str, object],
    ) -> RagV2RetrievalExecution:
        outcome = self.retrieve(scope=scope, payload=payload)
        return RagV2RetrievalExecution(
            outcome=outcome,
            voyage_physical_calls=self._voyage_physical_calls,
        )


def test_bge_v2_engine_returns_retrieval_only_public_and_owner_metadata_without_content() -> None:
    scope = _scope(owner_generation=True)
    public = _candidate(1, scope, source_scope="EXACT30")
    owner = _candidate(2, scope, source_scope="OWNER_PRIVATE")
    engine = BgeRagV2RetrievalOnlyEngine(
        scope_reader=_ScopeReader(scope),
        retrieval=_Retrieval(_success(public, owner)),
    )

    result = engine.ask(_request())

    assert result.status is RagV2RpcStatus.RETRIEVAL_ONLY
    assert result.answer is None
    assert result.retrieval_failure is False
    assert result.failure_code == ""
    assert result.citation_coverage == 1.0
    assert result.authorized_top5_chunk_revision_ids == (public.chunk_id, owner.chunk_id)
    assert result.exact30_generation_id == scope.exact30_generation_id
    assert result.oa112_generation_id == scope.oa112_generation_id
    assert result.owner_generation_id == scope.owner_private_generation_id
    assert result.embedding_profile_id == "bge_m3_local_1024_v1"
    assert result.provider_physical_total == 0
    assert result.external_provider_candidate is False
    assert result.citations[0].public_web is not None
    assert result.citations[0].local_document is None
    assert result.citations[1].local_document is not None
    assert result.citations[1].public_web is None
    assert all("Canonical content" not in str(item) for item in result.citations)


def test_bge_v2_engine_blocks_advice_before_scope_or_retrieval() -> None:
    scope = _scope(owner_generation=False)
    reader = _ScopeReader(scope)
    retrieval = _Retrieval(_success(_candidate(1, scope, source_scope="EXACT30"), _candidate(2, scope, source_scope="OA112")))
    engine = BgeRagV2RetrievalOnlyEngine(scope_reader=reader, retrieval=retrieval)

    result = engine.ask(_request(question="나는 지금 005930을 몇 주 매수해야 하나요?"))

    assert result.status is RagV2RpcStatus.BLOCKED_ADVICE
    assert result.failure_code == "PERSONALIZED_TRADING_ADVICE"
    assert result.guardrail_flags == ("PERSONALIZED_TRADING_ADVICE",)
    assert reader.calls == 0
    assert retrieval.calls == 0


def test_bge_v2_engine_maps_scope_or_retrieval_failure_without_fabricating_citations() -> None:
    scope = _scope(owner_generation=False)
    engine = BgeRagV2RetrievalOnlyEngine(
        scope_reader=_ScopeReader(scope),
        retrieval=_Retrieval(
            RagV2RetrievalOutcome(
                evidence=(),
                failure_code=RagV2RetrievalFailureCode.CHANNEL_UNAVAILABLE,
                retrieval_permitted=False,
            )
        ),
    )

    result = engine.ask(_request())

    assert result.status is RagV2RpcStatus.RETRIEVAL_FAILURE
    assert result.retrieval_failure is True
    assert result.failure_code == "RAG_RETRIEVAL_CHANNEL_UNAVAILABLE"
    assert result.citations == ()
    assert result.authorized_top5_chunk_revision_ids == ()
    assert result.citation_coverage == 0.0


def test_profile_selected_engine_uses_only_the_db_scope_voyage_retrieval_without_bge_fallback() -> None:
    voyage_scope = _scope(owner_generation=False, profile="voyage_context_4_1024_v1")
    bge_scope = _scope(owner_generation=False)
    bge = _Retrieval(_success(_candidate(1, bge_scope, source_scope="EXACT30")))
    voyage = _Retrieval(
        _success(
            _candidate(1, voyage_scope, source_scope="EXACT30"),
            _candidate(2, voyage_scope, source_scope="OA112"),
        )
    )
    engine = ProfileSelectedRagV2RetrievalOnlyEngine(
        scope_reader=_ScopeReader(voyage_scope),
        retrievals={
            "bge_m3_local_1024_v1": bge,
            "voyage_context_4_1024_v1": voyage,
        },
    )

    result = engine.ask(_request())

    assert result.status is RagV2RpcStatus.RETRIEVAL_ONLY
    assert result.embedding_profile_id == "voyage_context_4_1024_v1"
    assert voyage.calls == 1
    assert bge.calls == 0


def test_public_voyage_owner_bge_scope_returns_owner_citation_as_retrieval_only() -> None:
    scope = _scope(
        owner_generation=True,
        profile="voyage_context_4_1024_v1",
        owner_profile="bge_m3_local_1024_v1",
    )
    public = _candidate(1, scope, source_scope="EXACT30")
    owner = _candidate(2, scope, source_scope="OWNER_PRIVATE")
    voyage = _Retrieval(_success(public, owner))
    engine = ProfileSelectedRagV2RetrievalOnlyEngine(
        scope_reader=_ScopeReader(scope),
        retrievals={"voyage_context_4_1024_v1": voyage},
    )

    result = engine.ask(_request())

    assert result.status is RagV2RpcStatus.RETRIEVAL_ONLY
    assert result.embedding_profile_id == "voyage_context_4_1024_v1"
    assert result.citations[1].local_document is not None
    assert result.external_provider_candidate is False
    assert result.provider_physical_total == 0


def test_profile_selected_engine_reports_unavailable_voyage_profile_without_bge_fallback() -> None:
    voyage_scope = _scope(owner_generation=False, profile="voyage_context_4_1024_v1")
    bge = _Retrieval(_success(_candidate(1, _scope(owner_generation=False), source_scope="EXACT30")))
    engine = ProfileSelectedRagV2RetrievalOnlyEngine(
        scope_reader=_ScopeReader(voyage_scope),
        retrievals={"bge_m3_local_1024_v1": bge},
    )

    result = engine.ask(_request())

    assert result.status is RagV2RpcStatus.RETRIEVAL_FAILURE
    assert result.failure_code == "RAG_QUERY_PROFILE_UNAVAILABLE"
    assert result.embedding_profile_id == "voyage_context_4_1024_v1"
    assert bge.calls == 0


def test_profile_selected_engine_preserves_one_hard_gated_voyage_query_attempt_in_the_result() -> None:
    scope = _scope(owner_generation=False, profile="voyage_context_4_1024_v1")
    voyage = _ExecutionRetrieval(
        _success(
            _candidate(1, scope, source_scope="EXACT30"),
            _candidate(2, scope, source_scope="OA112"),
        ),
        voyage_physical_calls=1,
    )
    engine = ProfileSelectedRagV2RetrievalOnlyEngine(
        scope_reader=_ScopeReader(scope),
        retrievals={"voyage_context_4_1024_v1": voyage},
    )

    result = engine.ask(_request())

    assert result.status is RagV2RpcStatus.RETRIEVAL_ONLY
    assert result.provider_physical_total == 1
    assert result.voyage_physical_calls == 1
    assert result.gemini_physical_calls == result.openai_physical_calls == 0


def test_v2_loopback_transport_serializes_only_safe_citations_and_disables_reflection() -> None:
    scope = _scope(owner_generation=True)
    public = _candidate(1, scope, source_scope="EXACT30")
    owner = _candidate(2, scope, source_scope="OWNER_PRIVATE")
    resources = create_rag_v2_server(
        _Settings(),
        BgeRagV2RetrievalOnlyEngine(
            scope_reader=_ScopeReader(scope),
            retrieval=_Retrieval(_success(public, owner)),
        ),
    )
    resources.server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{resources.bound_port}")
    try:
        response = rag_v2_pb2_grpc.RagServiceStub(channel).Ask(
            _request(), metadata=AUTH, timeout=2
        )
        assert response.status == rag_v2_pb2.RAG_RESPONSE_STATUS_RETRIEVAL_ONLY
        assert not response.HasField("answer")
        assert response.citation_coverage == 1.0
        assert response.provider_physical_counts.total == 0
        assert response.external_provider_candidate is False
        assert response.exact30_generation_id == scope.exact30_generation_id
        assert response.oa_generation_id == scope.oa112_generation_id
        assert response.owner_generation_id == scope.owner_private_generation_id
        assert response.citations[0].WhichOneof("citation") == "public_web"
        assert response.citations[1].WhichOneof("citation") == "local_document"
        assert "Canonical content" not in str(response)

        with pytest.raises(grpc.RpcError) as missing_auth:
            rag_v2_pb2_grpc.RagServiceStub(channel).Ask(_request(), timeout=1)
        assert missing_auth.value.code() == grpc.StatusCode.UNAUTHENTICATED

        reflection = reflection_pb2_grpc.ServerReflectionStub(channel)
        with pytest.raises(grpc.RpcError) as reflection_error:
            tuple(
                reflection.ServerReflectionInfo(
                    iter([reflection_pb2.ServerReflectionRequest(list_services="")]),
                    timeout=1,
                )
            )
        assert reflection_error.value.code() == grpc.StatusCode.UNIMPLEMENTED
    finally:
        channel.close()
        resources.server.stop(grace=0).wait(timeout=2)


def test_v2_loopback_transport_rejects_non_nfc_and_malformed_engine_output() -> None:
    scope = _scope(owner_generation=False)

    class _MalformedEngine:
        def ask(self, _: rag_v2_pb2.RagAskRequest) -> RagV2EngineResult:
            return RagV2EngineResult(
                status=RagV2RpcStatus.RETRIEVAL_ONLY,
                answer=None,
                citations=(),
                authorized_top5_chunk_revision_ids=(),
                citation_coverage=1.0,
                retrieval_failure=False,
                guardrail_flags=(),
                failure_code="",
                exact30_generation_id=scope.exact30_generation_id,
                oa112_generation_id=scope.oa112_generation_id,
                owner_generation_id=None,
                embedding_profile_id=scope.embedding_profile_id,
                policy_version=scope.policy_version,
            )

    resources = create_rag_v2_server(_Settings(), _MalformedEngine())
    resources.server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{resources.bound_port}")
    try:
        non_nfc = _request(question="e\u0301 근거를 보여 주세요.")
        with pytest.raises(grpc.RpcError) as invalid:
            rag_v2_pb2_grpc.RagServiceStub(channel).Ask(non_nfc, metadata=AUTH, timeout=1)
        assert invalid.value.code() == grpc.StatusCode.INVALID_ARGUMENT

        with pytest.raises(grpc.RpcError) as malformed:
            rag_v2_pb2_grpc.RagServiceStub(channel).Ask(_request(), metadata=AUTH, timeout=1)
        assert malformed.value.code() == grpc.StatusCode.DATA_LOSS
    finally:
        channel.close()
        resources.server.stop(grace=0).wait(timeout=2)


def _request(
    *,
    question: str = "공개와 개인 문서의 근거를 비교해 보여 주세요.",
) -> rag_v2_pb2.RagAskRequest:
    return rag_v2_pb2.RagAskRequest(
        request_id="req_v2_runtime_000000000001",
        owner_scope_claim="rvs_" + "a" * 32,
        question=question,
        answer_mode="CONCISE",
        related_symbols=["005930"],
        topics=["FINANCIAL_ENGINEERING", "RISK"],
        consent_context=rag_v2_pb2.RagConsentContext(
            granted=False,
            policy_version="NONE",
        ),
    )


def _scope(
    *,
    owner_generation: bool,
    profile: str = "bge_m3_local_1024_v1",
    owner_profile: str | None = None,
) -> RagV2BundleScope:
    return RagV2BundleScope(
        claim_id="rvs_" + "a" * 32,
        owner_user_id="usr_demo_owner",
        session_id="req_v2_runtime_000000000001",
        exact30_generation_id="rgr_" + "1" * 32,
        oa112_generation_id="rgr_" + "2" * 32,
        owner_private_generation_id=("rgr_" + "3" * 32) if owner_generation else None,
        embedding_profile_id=profile,
        owner_embedding_profile_id=(owner_profile or profile) if owner_generation else None,
        policy_version=1,
        allowed_topics=("FINANCIAL_ENGINEERING", "RISK"),
    )


def _success(*candidates: RagV2RetrievalCandidate) -> RagV2RetrievalOutcome:
    return RagV2RetrievalOutcome(
        evidence=tuple(candidates),
        failure_code=None,
        retrieval_permitted=True,
        external_generation_permitted=False,
    )


def _candidate(
    index: int,
    scope: RagV2BundleScope,
    *,
    source_scope: str,
) -> RagV2RetrievalCandidate:
    digest = hashlib.sha256(f"v2-rpc-{index}".encode()).hexdigest()
    content = f"Canonical content for v2 RPC source {index}."
    if source_scope == "OWNER_PRIVATE":
        generation_id = scope.owner_private_generation_id or "rgr_" + "3" * 32
        owner_user_id = scope.owner_user_id
        document_id = f"doc_v2_rpc_document_{index:04d}"
        title = None
        display_name = f"Personal note {index}"
        canonical_url = None
    elif source_scope == "EXACT30":
        generation_id = scope.exact30_generation_id
        owner_user_id = None
        document_id = None
        title = f"Exact public source {index}"
        display_name = None
        canonical_url = f"https://public.example.com/exact/{index}"
    else:
        generation_id = scope.oa112_generation_id
        owner_user_id = None
        document_id = None
        title = f"OA public source {index}"
        display_name = None
        canonical_url = f"https://public.example.com/oa/{index}"
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
        external_processing_eligible=False,
        generation_id=generation_id,
        heading_path=("Evidence",),
        locator={"section": f"Section {index}"},
        owner_user_id=owner_user_id,
        policy_version=scope.policy_version,
        sanitized_display_name=display_name,
        scope_claim_id=scope.claim_id,
        session_id=scope.session_id,
        source_id=f"src_v2_rpc_fixture_{index:03d}",
        source_revision_id=f"srv_v2_rpc_fixture_{index:03d}",
        source_scope=source_scope,
        title=title,
        topics=("FINANCIAL_ENGINEERING", "RISK"),
    )
