"""Immutable RAG v2의 profile-selected retrieval-only gRPC 경계다.

Spring이 발급한 opaque retrieval claim을 local query role로 재검증하고, public/owner citation
metadata만 loopback transport로 돌려준다. BGE는 local-only이고 Voyage query transport는
hard-gated profile adapter가 명시적으로 주입될 때만 한 번 호출할 수 있으며 generator transport는
만들지 않는다. canonical text, raw document, owner path는 process 밖으로 직렬화하지 않는다.
"""

from __future__ import annotations

import ipaddress
import math
import re
import unicodedata
from collections.abc import Mapping
from concurrent import futures
from dataclasses import dataclass
from enum import StrEnum
from hmac import compare_digest
from typing import Never, Protocol, cast
from urllib.parse import urlsplit

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from app.generated import rag_v2_pb2, rag_v2_pb2_grpc
from app.rag.guardrail import BoundedFixtureGuardrail, GuardrailDecision
from app.rag.rag_v2_authorized_retrieval import (
    RagV2AuthorizedHybridRetrieval,
    RagV2BundleScope,
    RagV2RetrievalCandidate,
    RagV2RetrievalExecution,
    RagV2RetrievalOutcome,
)

_MAX_REQUEST_BYTES = 65_536
_MAX_RESPONSE_BYTES = 262_144
_MAX_CONCURRENCY = 8
_AUTH_METADATA_KEY = "x-decision-rag-v2-grpc-auth"
_REQUEST_ID = re.compile(r"^req_[A-Za-z0-9_-]{12,96}$")
_SCOPE_CLAIM = re.compile(r"^rvs_[0-9a-f]{32}$")
_SOURCE_ID = re.compile(r"^src_[a-z0-9][a-z0-9_-]{2,95}$")
_SOURCE_REVISION_ID = re.compile(r"^srv_[a-z0-9][a-z0-9_-]{2,95}$")
_CHUNK_ID = re.compile(r"^rag_v2_chk_[0-9a-f]{32}$")
_GENERATION_ID = re.compile(r"^rgr_[0-9a-f]{32}$")
_DOCUMENT_ID = re.compile(r"^doc_[a-z0-9][a-z0-9_-]{10,95}$")
_FLAG = re.compile(r"^[A-Z0-9_]{1,64}$")
_FAILURE_CODE = re.compile(r"^[A-Z0-9_]{1,96}$")
_SYMBOL = re.compile(r"^[0-9]{6}$")
_TOPICS = frozenset({"API", "DATA", "FINANCIAL_ENGINEERING", "METHODOLOGY", "PRODUCT_RISK", "RISK"})
_BGE_PROFILE = "bge_m3_local_1024_v1"
_VOYAGE_PROFILE = "voyage_context_4_1024_v1"
_RETRIEVAL_PROFILES = frozenset({_BGE_PROFILE, _VOYAGE_PROFILE})


class RagV2RpcStatus(StrEnum):
    """locked v2 proto enum을 local engine이 해석하는 typed 상태다."""

    ANSWERED = "ANSWERED"
    RETRIEVAL_ONLY = "RETRIEVAL_ONLY"
    RETRIEVAL_FAILURE = "RETRIEVAL_FAILURE"
    BLOCKED_SENSITIVE = "BLOCKED_SENSITIVE"
    BLOCKED_ADVICE = "BLOCKED_ADVICE"
    GENERATION_UNAVAILABLE = "GENERATION_UNAVAILABLE"
    CORPUS_NOT_READY = "CORPUS_NOT_READY"


@dataclass(frozen=True)
class RagV2PublicWebCitation:
    """public corpus citation의 bounded metadata이며 raw text는 포함하지 않는다."""

    title: str
    canonical_url: str
    locator: dict[str, object]


@dataclass(frozen=True)
class RagV2LocalDocumentCitation:
    """owner document citation은 local path 대신 sanitized display name만 반환한다."""

    document_id: str
    display_name: str
    locator: dict[str, object]


@dataclass(frozen=True)
class RagV2RpcCitation:
    """Spring의 final DB recheck가 사용할 immutable citation identity다."""

    citation_id: str
    source_id: str
    source_revision_id: str
    chunk_revision_id: str
    generation_id: str
    public_web: RagV2PublicWebCitation | None
    local_document: RagV2LocalDocumentCitation | None


@dataclass(frozen=True)
class RagV2EngineResult:
    """v2 loopback response의 bounded internal projection이다.

    BGE retrieval has zero provider attempts. A hard-gated Voyage query can report exactly one
    Voyage attempt for its own request, but never makes a generator/provider fallback candidate.
    """

    status: RagV2RpcStatus
    answer: str | None
    citations: tuple[RagV2RpcCitation, ...]
    authorized_top5_chunk_revision_ids: tuple[str, ...]
    citation_coverage: float
    retrieval_failure: bool
    guardrail_flags: tuple[str, ...]
    failure_code: str
    exact30_generation_id: str
    oa112_generation_id: str
    owner_generation_id: str | None
    embedding_profile_id: str
    policy_version: int
    provider_physical_total: int = 0
    gemini_physical_calls: int = 0
    openai_physical_calls: int = 0
    voyage_physical_calls: int = 0
    external_provider_candidate: bool = False


class RagV2ScopeReader(Protocol):
    """opaque claim과 session만으로 current immutable scope를 재검증한다."""

    def read_scope_by_claim(
        self,
        *,
        claim_id: str,
        session_id: str,
    ) -> RagV2BundleScope:
        """query-role DB function이 owner/pointer/expiry를 fail-closed하게 확인한다."""


class RagV2RetrievalPort(Protocol):
    """profile-selected query embedding + exact/trigram/vector RRF engine의 최소 port다."""

    def retrieve(
        self,
        *,
        scope: RagV2BundleScope,
        payload: dict[str, object],
    ) -> RagV2RetrievalOutcome:
        """top-5 evidence 외 canonical raw text를 response contract로 승격하지 않는다."""


class RagV2ExecutionRetrievalPort(RagV2RetrievalPort, Protocol):
    """Optional per-request provider receipt surface for a profile-specific retrieval adapter."""

    def retrieve_with_execution(
        self,
        *,
        scope: RagV2BundleScope,
        payload: dict[str, object],
    ) -> RagV2RetrievalExecution:
        """Return the bounded outcome and one possible Voyage query attempt count together."""


class RagV2AskEngine(Protocol):
    """valid loopback request를 selected retrieval profile로 한 번 평가한다."""

    def ask(self, request: rag_v2_pb2.RagAskRequest) -> RagV2EngineResult:
        """상태와 immutable citation metadata만 반환한다."""


class ProfileSelectedRagV2RetrievalOnlyEngine:
    """DB scope가 선택한 embedding profile 하나로만 v2 retrieval을 수행한다.

    local guardrail은 scope DB access보다 먼저 실행한다. 그 뒤 current immutable scope를 읽어
    profile별 retrieval engine을 선택한다. request는 corpus/profile/embedding provider를 고르지
    못하며, Voyage engine이 준비되지 않아도 BGE fallback을 만들지 않는다.
    """

    def __init__(
        self,
        *,
        scope_reader: RagV2ScopeReader,
        retrievals: Mapping[
            str,
            RagV2RetrievalPort | RagV2AuthorizedHybridRetrieval,
        ],
        guardrail: BoundedFixtureGuardrail | None = None,
    ) -> None:
        if (
            not retrievals
            or not set(retrievals).issubset(_RETRIEVAL_PROFILES)
            or any(retrieval is None for retrieval in retrievals.values())
        ):
            raise ValueError("RAG v2 profile-selected retrieval configuration is invalid")
        self._scope_reader = scope_reader
        self._retrievals = dict(retrievals)
        self._guardrail = guardrail or BoundedFixtureGuardrail()

    def ask(self, request: rag_v2_pb2.RagAskRequest) -> RagV2EngineResult:
        """질문을 local guard→opaque scope→scope-selected RRF 순서로만 처리한다."""

        guard = self._guardrail.classify(request.question)
        if guard.decision is GuardrailDecision.BLOCKED_ADVICE:
            return _blocked(RagV2RpcStatus.BLOCKED_ADVICE, guard.flags)
        if guard.decision is GuardrailDecision.BLOCKED_SENSITIVE:
            return _blocked(RagV2RpcStatus.BLOCKED_SENSITIVE, guard.flags)

        try:
            scope = self._scope_reader.read_scope_by_claim(
                claim_id=request.owner_scope_claim,
                session_id=request.request_id,
            )
        except (ConnectionError, OSError, RuntimeError, TimeoutError, ValueError):
            return _retrieval_failure("RAG_RETRIEVAL_CHANNEL_UNAVAILABLE")
        if (
            scope.claim_id != request.owner_scope_claim
            or scope.session_id != request.request_id
            or scope.embedding_profile_id not in _RETRIEVAL_PROFILES
        ):
            return _retrieval_failure("RAG_RETRIEVAL_SCOPE_CHANGED")
        retrieval = self._retrievals.get(scope.embedding_profile_id)
        if retrieval is None:
            # A Voyage scope must never silently execute a local BGE query embedding.  The current
            # bundle remains intact and the caller receives a resumable typed terminal state instead.
            return _retrieval_failure("RAG_QUERY_PROFILE_UNAVAILABLE", scope=scope)

        try:
            execution = _retrieve_with_execution(
                retrieval,
                scope=scope,
                payload={
                    "question": request.question,
                    "answerMode": request.answer_mode,
                    "relatedSymbols": list(request.related_symbols),
                    "topics": list(request.topics),
                    "externalQueryConsentGranted": request.consent_context.granted,
                },
            )
        except (ConnectionError, OSError, RuntimeError, TimeoutError, ValueError):
            return _retrieval_failure("RAG_RETRIEVAL_CHANNEL_UNAVAILABLE", scope=scope)
        outcome = execution.outcome
        if outcome.failure_code is not None or not outcome.retrieval_permitted:
            return _retrieval_failure(
                outcome.failure_code.value
                if outcome.failure_code is not None
                else "RAG_INSUFFICIENT_EVIDENCE",
                scope=scope,
                voyage_physical_calls=execution.voyage_physical_calls,
            )

        try:
            citations = tuple(
                _citation_from_candidate(candidate, scope=scope, ordinal=ordinal)
                for ordinal, candidate in enumerate(outcome.evidence, start=1)
            )
        except (TypeError, ValueError):
            return _retrieval_failure(
                "RAG_RETRIEVAL_SCOPE_CHANGED",
                scope=scope,
                voyage_physical_calls=execution.voyage_physical_calls,
            )
        if not citations:
            return _retrieval_failure(
                "RAG_INSUFFICIENT_EVIDENCE",
                scope=scope,
                voyage_physical_calls=execution.voyage_physical_calls,
            )

        return RagV2EngineResult(
            status=RagV2RpcStatus.RETRIEVAL_ONLY,
            answer=None,
            citations=citations,
            authorized_top5_chunk_revision_ids=tuple(
                candidate.chunk_id for candidate in outcome.evidence
            ),
            # This is evidence coverage for the retrieval-only response, not an unsupported
            # generated factual sentence claim. Every exposed item is a validated citation.
            citation_coverage=1.0,
            retrieval_failure=False,
            guardrail_flags=(),
            failure_code="",
            exact30_generation_id=scope.exact30_generation_id,
            oa112_generation_id=scope.oa112_generation_id,
            owner_generation_id=scope.owner_private_generation_id,
            embedding_profile_id=scope.embedding_profile_id,
            policy_version=scope.policy_version,
            provider_physical_total=execution.voyage_physical_calls,
            voyage_physical_calls=execution.voyage_physical_calls,
        )


class BgeRagV2RetrievalOnlyEngine(ProfileSelectedRagV2RetrievalOnlyEngine):
    """Existing local-BGE entrypoint with a deliberately single-profile retrieval map.

    Keeping this wrapper preserves the current local-only process contract.  The profile-selected
    engine is used only where a separately hard-gated Voyage query implementation is injected.
    """

    def __init__(
        self,
        *,
        scope_reader: RagV2ScopeReader,
        retrieval: RagV2RetrievalPort | RagV2AuthorizedHybridRetrieval,
        guardrail: BoundedFixtureGuardrail | None = None,
    ) -> None:
        super().__init__(
            scope_reader=scope_reader,
            retrievals={_BGE_PROFILE: retrieval},
            guardrail=guardrail,
        )


class LoopbackRagV2ServerSettings(Protocol):
    """v2 server factory가 받는 purpose-separated loopback 설정 표면이다."""

    @property
    def bind_address(self) -> str: ...

    @property
    def shared_secret(self) -> str: ...


@dataclass(frozen=True)
class RagV2ServerResources:
    """process entrypoint와 test가 명시적으로 종료할 server/actual port다."""

    server: grpc.Server
    bound_port: int


class RagV2ServiceServicer(rag_v2_pb2_grpc.RagServiceServicer):
    """v1과 별도 service namespace/secret을 쓰는 v2 unary loopback adapter다."""

    def __init__(self, engine: RagV2AskEngine, shared_secret: str) -> None:
        self._engine = engine
        self._shared_secret = shared_secret

    def Ask(
        self,
        request: rag_v2_pb2.RagAskRequest,
        context: grpc.ServicerContext,
    ) -> rag_v2_pb2.RagAskResponse:
        """JWT, raw owner document, provider key를 받지 않고 opaque claim만 평가한다."""

        _require_authenticated(context, self._shared_secret)
        _validate_request(request, context)
        if not context.is_active():
            _abort(context, grpc.StatusCode.CANCELLED, "RAG v2 request was cancelled")
        try:
            result = self._engine.ask(request)
        except Exception:
            _abort(context, grpc.StatusCode.INTERNAL, "RAG v2 local engine failed closed")
        if not context.is_active():
            _abort(context, grpc.StatusCode.CANCELLED, "RAG v2 request was cancelled")
        try:
            _validate_engine_result(result)
        except ValueError:
            _abort(
                context,
                grpc.StatusCode.DATA_LOSS,
                "RAG v2 engine response violated contract",
            )
        response = _to_response(request, result)
        if response.ByteSize() > _MAX_RESPONSE_BYTES:
            _abort(
                context,
                grpc.StatusCode.DATA_LOSS,
                "RAG v2 response exceeded bound",
            )
        return response


def create_rag_v2_server(
    settings: LoopbackRagV2ServerSettings,
    engine: RagV2AskEngine,
) -> RagV2ServerResources:
    """numeric loopback에 v2 health/RagService만 등록하며 reflection은 등록하지 않는다."""

    if not _is_loopback_address(settings.bind_address):
        raise ValueError("RAG v2 gRPC must bind to numeric loopback")
    if re.fullmatch(r"[A-Za-z0-9._~:-]{32,256}", settings.shared_secret) is None:
        raise ValueError("RAG v2 gRPC shared secret is invalid")
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=_MAX_CONCURRENCY),
        options=(
            ("grpc.max_receive_message_length", _MAX_REQUEST_BYTES),
            ("grpc.max_send_message_length", _MAX_RESPONSE_BYTES),
        ),
        maximum_concurrent_rpcs=_MAX_CONCURRENCY,
    )
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    service_name = rag_v2_pb2.DESCRIPTOR.services_by_name["RagService"].full_name
    health_servicer.set(service_name, health_pb2.HealthCheckResponse.SERVING)
    rag_v2_pb2_grpc.add_RagServiceServicer_to_server(  # type: ignore[no-untyped-call]
        RagV2ServiceServicer(engine, settings.shared_secret), server
    )
    bound_port = server.add_insecure_port(settings.bind_address)
    if bound_port == 0:
        raise RuntimeError("RAG v2 gRPC loopback port could not be bound")
    return RagV2ServerResources(server=server, bound_port=bound_port)


def _citation_from_candidate(
    candidate: RagV2RetrievalCandidate,
    *,
    scope: RagV2BundleScope,
    ordinal: int,
) -> RagV2RpcCitation:
    """retrieval row의 safe identity만 preserve하고 canonical text/path는 버린다."""

    if not 1 <= ordinal <= 5 or not _candidate_matches_scope(candidate, scope):
        raise ValueError("RAG v2 retrieval candidate scope is invalid")
    locator = _validated_locator(candidate.locator)
    common = {
        "citation_id": f"cit_{ordinal}",
        "source_id": candidate.source_id,
        "source_revision_id": candidate.source_revision_id,
        "chunk_revision_id": candidate.chunk_id,
        "generation_id": candidate.generation_id,
    }
    if candidate.source_scope in {"EXACT30", "OA112"}:
        if (
            candidate.owner_user_id is not None
            or candidate.sanitized_display_name is not None
            or not _bounded_text(candidate.title, 1_024)
            or not _safe_public_https_url(candidate.canonical_https_url)
        ):
            raise ValueError("RAG v2 public citation is invalid")
        title = candidate.title
        canonical_url = candidate.canonical_https_url
        if not isinstance(title, str) or not isinstance(canonical_url, str):
            raise ValueError("RAG v2 public citation is invalid")
        return RagV2RpcCitation(
            **common,
            public_web=RagV2PublicWebCitation(
                title=title,
                canonical_url=canonical_url,
                locator=locator,
            ),
            local_document=None,
        )
    if (
        candidate.source_scope != "OWNER_PRIVATE"
        or candidate.owner_user_id != scope.owner_user_id
        or _DOCUMENT_ID.fullmatch(candidate.document_id or "") is None
        or not _bounded_display_name(candidate.sanitized_display_name)
        or candidate.title is not None
        or candidate.canonical_https_url is not None
    ):
        raise ValueError("RAG v2 owner citation is invalid")
    document_id = candidate.document_id
    display_name = candidate.sanitized_display_name
    if not isinstance(document_id, str) or not isinstance(display_name, str):
        raise ValueError("RAG v2 owner citation is invalid")
    return RagV2RpcCitation(
        **common,
        public_web=None,
        local_document=RagV2LocalDocumentCitation(
            document_id=document_id,
            display_name=display_name,
            locator=locator,
        ),
    )


def _candidate_matches_scope(
    candidate: RagV2RetrievalCandidate,
    scope: RagV2BundleScope,
) -> bool:
    expected_generation = {
        "EXACT30": scope.exact30_generation_id,
        "OA112": scope.oa112_generation_id,
        "OWNER_PRIVATE": scope.owner_private_generation_id,
    }.get(candidate.source_scope)
    return (
        _SOURCE_ID.fullmatch(candidate.source_id) is not None
        and _SOURCE_REVISION_ID.fullmatch(candidate.source_revision_id) is not None
        and _CHUNK_ID.fullmatch(candidate.chunk_id) is not None
        and expected_generation is not None
        and candidate.generation_id == expected_generation
        and candidate.scope_claim_id == scope.claim_id
        and candidate.session_id == scope.session_id
        and candidate.embedding_profile_id
        == (
            scope.owner_embedding_profile_id
            if candidate.source_scope == "OWNER_PRIVATE"
            else scope.embedding_profile_id
        )
        and candidate.policy_version == scope.policy_version
        and bool(set(candidate.topics).intersection(scope.allowed_topics))
    )


def _blocked(
    status: RagV2RpcStatus,
    flags: tuple[str, ...],
) -> RagV2EngineResult:
    """scope를 읽지 못한 block은 generation identifier도 노출하지 않는다."""

    failure_code = flags[0] if len(flags) == 1 else "RAG_GUARDRAIL_BLOCKED"
    return RagV2EngineResult(
        status=status,
        answer=None,
        citations=(),
        authorized_top5_chunk_revision_ids=(),
        citation_coverage=0.0,
        retrieval_failure=False,
        guardrail_flags=flags,
        failure_code=failure_code,
        exact30_generation_id="",
        oa112_generation_id="",
        owner_generation_id=None,
        embedding_profile_id="",
        policy_version=0,
    )


def _retrieval_failure(
    failure_code: str,
    *,
    scope: RagV2BundleScope | None = None,
    voyage_physical_calls: int = 0,
) -> RagV2EngineResult:
    """DB/query failure remains terminal; a failed approved Voyage query still records its one attempt."""

    return RagV2EngineResult(
        status=RagV2RpcStatus.RETRIEVAL_FAILURE,
        answer=None,
        citations=(),
        authorized_top5_chunk_revision_ids=(),
        citation_coverage=0.0,
        retrieval_failure=True,
        guardrail_flags=(),
        failure_code=failure_code,
        exact30_generation_id=scope.exact30_generation_id if scope else "",
        oa112_generation_id=scope.oa112_generation_id if scope else "",
        owner_generation_id=scope.owner_private_generation_id if scope else None,
        embedding_profile_id=scope.embedding_profile_id if scope else "",
        policy_version=scope.policy_version if scope else 0,
        provider_physical_total=voyage_physical_calls,
        voyage_physical_calls=voyage_physical_calls,
    )


def _require_authenticated(context: grpc.ServicerContext, shared_secret: str) -> None:
    values = [value for key, value in context.invocation_metadata() if key == _AUTH_METADATA_KEY]
    supplied = values[0] if len(values) == 1 else None
    if not isinstance(supplied, str) or not compare_digest(supplied, shared_secret):
        _abort(context, grpc.StatusCode.UNAUTHENTICATED, "RAG v2 gRPC authentication failed")


def _validate_request(
    request: rag_v2_pb2.RagAskRequest,
    context: grpc.ServicerContext,
) -> None:
    """wire request가 corpus/profile/owner raw data capability로 변하지 않게 닫는다."""

    question_bytes = request.question.encode("utf-8", errors="strict")
    consent = request.consent_context
    if (
        request.ByteSize() > _MAX_REQUEST_BYTES
        or _REQUEST_ID.fullmatch(request.request_id) is None
        or _SCOPE_CLAIM.fullmatch(request.owner_scope_claim) is None
        or not 1 <= len(request.question) <= 1_000
        or len(question_bytes) > 8_192
        or unicodedata.normalize("NFC", request.question) != request.question
        or any(unicodedata.category(character) in {"Cc", "Cs"} for character in request.question)
        or request.answer_mode not in {"CONCISE", "DETAILED"}
        or len(request.related_symbols) > 5
        or len(set(request.related_symbols)) != len(request.related_symbols)
        or any(_SYMBOL.fullmatch(value) is None for value in request.related_symbols)
        or not 1 <= len(request.topics) <= len(_TOPICS)
        or len(set(request.topics)) != len(request.topics)
        or not set(request.topics) <= _TOPICS
        or consent.policy_version not in {"NONE", "EXTERNAL_AI_RAG_V2"}
        or (consent.granted and consent.policy_version != "EXTERNAL_AI_RAG_V2")
        or (not consent.granted and consent.policy_version != "NONE")
    ):
        _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "RAG v2 request contract is invalid")


def _validate_engine_result(result: RagV2EngineResult) -> None:
    """untrusted local engine object가 raw data/provider success로 승격되지 않게 재검증한다."""

    if (
        result.provider_physical_total
        != result.gemini_physical_calls
        + result.openai_physical_calls
        + result.voyage_physical_calls
        or min(
            result.provider_physical_total,
            result.gemini_physical_calls,
            result.openai_physical_calls,
            result.voyage_physical_calls,
        )
        < 0
        or result.external_provider_candidate
        or not math.isfinite(result.citation_coverage)
        or result.citation_coverage < 0.0
        or result.citation_coverage > 1.0
        or len(result.guardrail_flags) > 8
        or len(set(result.guardrail_flags)) != len(result.guardrail_flags)
        or any(_FLAG.fullmatch(flag) is None for flag in result.guardrail_flags)
        or len(result.citations) > 5
        or len({item.chunk_revision_id for item in result.citations}) != len(result.citations)
        or len(result.authorized_top5_chunk_revision_ids) > 5
        or len(set(result.authorized_top5_chunk_revision_ids))
        != len(result.authorized_top5_chunk_revision_ids)
        or any(
            _CHUNK_ID.fullmatch(value) is None
            for value in result.authorized_top5_chunk_revision_ids
        )
        or (result.failure_code and _FAILURE_CODE.fullmatch(result.failure_code) is None)
    ):
        raise ValueError("RAG v2 engine result envelope is invalid")
    if not _provider_receipt_is_valid(result):
        raise ValueError("RAG v2 provider receipt is invalid")
    _validate_bundle_metadata(result)
    top5 = set(result.authorized_top5_chunk_revision_ids)
    for ordinal, citation in enumerate(result.citations, start=1):
        _validate_citation(citation, ordinal=ordinal, top5=top5, result=result)
    if result.status is RagV2RpcStatus.RETRIEVAL_ONLY:
        if (
            result.answer is not None
            or not result.citations
            or result.citation_coverage != 1.0
            or result.retrieval_failure
            or result.failure_code
        ):
            raise ValueError("RAG v2 retrieval-only result is invalid")
    elif result.status is RagV2RpcStatus.RETRIEVAL_FAILURE:
        if (
            result.answer is not None
            or result.citations
            or result.authorized_top5_chunk_revision_ids
            or result.citation_coverage != 0.0
            or not result.retrieval_failure
            or not result.failure_code
        ):
            raise ValueError("RAG v2 retrieval failure result is invalid")
    else:
        if (
            result.answer is not None
            or result.citations
            or result.authorized_top5_chunk_revision_ids
            or result.citation_coverage != 0.0
            or result.retrieval_failure
            or not result.failure_code
        ):
            raise ValueError("RAG v2 withheld result is invalid")


def _validate_bundle_metadata(result: RagV2EngineResult) -> None:
    present = (
        result.exact30_generation_id,
        result.oa112_generation_id,
        result.embedding_profile_id,
        result.policy_version,
    )
    absent = present == ("", "", "", 0) and result.owner_generation_id is None
    if absent:
        return
    if (
        _GENERATION_ID.fullmatch(result.exact30_generation_id) is None
        or _GENERATION_ID.fullmatch(result.oa112_generation_id) is None
        or result.exact30_generation_id == result.oa112_generation_id
        or result.embedding_profile_id not in _RETRIEVAL_PROFILES
        or result.policy_version < 1
        or (
            result.owner_generation_id is not None
            and (
                _GENERATION_ID.fullmatch(result.owner_generation_id) is None
                or result.owner_generation_id
                in {result.exact30_generation_id, result.oa112_generation_id}
            )
        )
    ):
        raise ValueError("RAG v2 bundle metadata is invalid")


def _provider_receipt_is_valid(result: RagV2EngineResult) -> bool:
    """Only a Voyage-selected scope may report the single packet-gated query physical attempt."""

    if result.embedding_profile_id in {"", _BGE_PROFILE}:
        return (
            result.provider_physical_total == 0
            and result.gemini_physical_calls == 0
            and result.openai_physical_calls == 0
            and result.voyage_physical_calls == 0
        )
    if result.embedding_profile_id == _VOYAGE_PROFILE:
        return (
            result.gemini_physical_calls == 0
            and result.openai_physical_calls == 0
            and result.provider_physical_total == result.voyage_physical_calls
            and result.voyage_physical_calls in {0, 1}
        )
    return False


def _retrieve_with_execution(
    retrieval: RagV2RetrievalPort | RagV2AuthorizedHybridRetrieval,
    *,
    scope: RagV2BundleScope,
    payload: dict[str, object],
) -> RagV2RetrievalExecution:
    """Prefer a thread-safe per-call execution receipt and retain legacy local retrieval compatibility."""

    execute = getattr(retrieval, "retrieve_with_execution", None)
    if callable(execute):
        result = execute(scope=scope, payload=payload)
        if not isinstance(result, RagV2RetrievalExecution):
            raise ValueError("RAG v2 retrieval execution receipt is invalid")
        return result
    outcome = retrieval.retrieve(scope=scope, payload=payload)
    if not isinstance(outcome, RagV2RetrievalOutcome):
        raise ValueError("RAG v2 retrieval outcome is invalid")
    return RagV2RetrievalExecution(outcome=outcome)


def _validate_citation(
    citation: RagV2RpcCitation,
    *,
    ordinal: int,
    top5: set[str],
    result: RagV2EngineResult,
) -> None:
    """citation oneof와 immutable generation identity를 protocol boundary에서 다시 확인한다."""

    if (
        citation.citation_id != f"cit_{ordinal}"
        or _SOURCE_ID.fullmatch(citation.source_id) is None
        or _SOURCE_REVISION_ID.fullmatch(citation.source_revision_id) is None
        or _CHUNK_ID.fullmatch(citation.chunk_revision_id) is None
        or citation.chunk_revision_id not in top5
        or _GENERATION_ID.fullmatch(citation.generation_id) is None
        or citation.generation_id
        not in {
            result.exact30_generation_id,
            result.oa112_generation_id,
            result.owner_generation_id,
        }
        or (citation.public_web is None) == (citation.local_document is None)
    ):
        raise ValueError("RAG v2 citation identity is invalid")
    if citation.public_web is not None and (
        not _bounded_text(citation.public_web.title, 1_024)
        or not _safe_public_https_url(citation.public_web.canonical_url)
        or _validated_locator(citation.public_web.locator) != citation.public_web.locator
    ):
        raise ValueError("RAG v2 public citation is invalid")
    if citation.local_document is not None and (
        _DOCUMENT_ID.fullmatch(citation.local_document.document_id) is None
        or not _bounded_display_name(citation.local_document.display_name)
        or _validated_locator(citation.local_document.locator) != citation.local_document.locator
    ):
        raise ValueError("RAG v2 owner citation is invalid")


def _to_response(
    request: rag_v2_pb2.RagAskRequest,
    result: RagV2EngineResult,
) -> rag_v2_pb2.RagAskResponse:
    """validated result만 proto oneof로 변환하며 canonical content는 사용하지 않는다."""

    response = rag_v2_pb2.RagAskResponse(
        request_id=request.request_id,
        status=_PROTO_STATUS[result.status],
        citation_coverage=result.citation_coverage,
        retrieval_failure=result.retrieval_failure,
        guardrail_flags=result.guardrail_flags,
        exact30_generation_id=result.exact30_generation_id,
        oa_generation_id=result.oa112_generation_id,
        embedding_profile_id=result.embedding_profile_id,
        failure_code=result.failure_code,
        provider_physical_counts=rag_v2_pb2.ProviderPhysicalCounts(
            total=result.provider_physical_total,
            gemini=result.gemini_physical_calls,
            openai=result.openai_physical_calls,
            voyage=result.voyage_physical_calls,
        ),
        authorized_top5_chunk_revision_ids=result.authorized_top5_chunk_revision_ids,
        external_provider_candidate=result.external_provider_candidate,
        policy_version=result.policy_version,
    )
    if result.owner_generation_id is not None:
        response.owner_generation_id = result.owner_generation_id
    if result.answer is not None:
        response.answer = result.answer
    for citation in result.citations:
        proto = rag_v2_pb2.RagCitation(
            citation_id=citation.citation_id,
            source_id=citation.source_id,
            source_revision_id=citation.source_revision_id,
            chunk_revision_id=citation.chunk_revision_id,
            generation_id=citation.generation_id,
        )
        if citation.public_web is not None:
            proto.public_web.CopyFrom(
                rag_v2_pb2.PublicWebCitation(
                    title=citation.public_web.title,
                    canonical_url=citation.public_web.canonical_url,
                    locator=_to_proto_locator(citation.public_web.locator),
                )
            )
        elif citation.local_document is not None:
            proto.local_document.CopyFrom(
                rag_v2_pb2.LocalDocumentCitation(
                    document_id=citation.local_document.document_id,
                    display_name=citation.local_document.display_name,
                    locator=_to_proto_locator(citation.local_document.locator),
                )
            )
        response.citations.append(proto)
    return response


def _to_proto_locator(locator: dict[str, object]) -> rag_v2_pb2.DocumentLocator:
    validated = _validated_locator(locator)
    if "page" in validated:
        return rag_v2_pb2.DocumentLocator(page=cast(int, validated["page"]))
    if "slide" in validated:
        return rag_v2_pb2.DocumentLocator(slide=cast(int, validated["slide"]))
    if "sheet" in validated:
        return rag_v2_pb2.DocumentLocator(sheet=cast(str, validated["sheet"]))
    return rag_v2_pb2.DocumentLocator(section=cast(str, validated["section"]))


def _validated_locator(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError("RAG v2 citation locator is invalid")
    key, locator_value = next(iter(value.items()))
    if key in {"page", "slide"}:
        if type(locator_value) is not int or locator_value <= 0:
            raise ValueError("RAG v2 citation locator is invalid")
        return {key: locator_value}
    if key == "sheet" and _locator_text(locator_value, maximum=128):
        return {key: locator_value}
    if key == "section" and _locator_text(locator_value, maximum=300):
        return {key: locator_value}
    raise ValueError("RAG v2 citation locator is invalid")


def _locator_text(value: object, *, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and unicodedata.normalize("NFC", value) == value
        and not any(character in value for character in ("/", "\\", "\x00", "\r", "\n"))
        and not value.startswith((".", "~"))
    )


def _bounded_display_name(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return _bounded_text(value, 160) and not any(
        character in value for character in ("/", "\\", ":")
    )


def _bounded_text(value: object, maximum_bytes: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value.encode("utf-8")) <= maximum_bytes
        and unicodedata.normalize("NFC", value) == value
        and not any(unicodedata.category(character) in {"Cc", "Cs"} for character in value)
    )


def _safe_public_https_url(value: object) -> bool:
    if not isinstance(value, str) or not 9 <= len(value) <= 2_048:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        return False
    host = parsed.hostname.rstrip(".").casefold()
    if host == "localhost" or host.endswith(
        (".localhost", ".local", ".internal", ".home.arpa", ".test")
    ):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _is_loopback_address(value: str) -> bool:
    if value.startswith("127.0.0.1:"):
        port = value.removeprefix("127.0.0.1:")
    elif value.startswith("[::1]:"):
        port = value.removeprefix("[::1]:")
    else:
        return False
    return port.isdigit() and 0 <= int(port) <= 65_535


def _abort(context: grpc.ServicerContext, code: grpc.StatusCode, detail: str) -> Never:
    context.abort(code, detail)
    raise RuntimeError("grpc context.abort unexpectedly returned")


_PROTO_STATUS = {
    RagV2RpcStatus.ANSWERED: rag_v2_pb2.RAG_RESPONSE_STATUS_ANSWERED,
    RagV2RpcStatus.RETRIEVAL_ONLY: rag_v2_pb2.RAG_RESPONSE_STATUS_RETRIEVAL_ONLY,
    RagV2RpcStatus.RETRIEVAL_FAILURE: rag_v2_pb2.RAG_RESPONSE_STATUS_RETRIEVAL_FAILURE,
    RagV2RpcStatus.BLOCKED_SENSITIVE: rag_v2_pb2.RAG_RESPONSE_STATUS_BLOCKED_SENSITIVE,
    RagV2RpcStatus.BLOCKED_ADVICE: rag_v2_pb2.RAG_RESPONSE_STATUS_BLOCKED_ADVICE,
    RagV2RpcStatus.GENERATION_UNAVAILABLE: rag_v2_pb2.RAG_RESPONSE_STATUS_GENERATION_UNAVAILABLE,
    RagV2RpcStatus.CORPUS_NOT_READY: rag_v2_pb2.RAG_RESPONSE_STATUS_CORPUS_NOT_READY,
}
