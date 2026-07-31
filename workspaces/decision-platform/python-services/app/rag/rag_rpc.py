from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
import unicodedata
from concurrent import futures
from dataclasses import dataclass
from enum import StrEnum
from hmac import compare_digest
from typing import Never, Protocol
from urllib.parse import urlsplit

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from app.generated import rag_pb2, rag_pb2_grpc
from app.rag.authorized_retrieval import ChannelResult, RetrievalCandidate, RrfFusion
from app.rag.external_processing_corpus import load_external_processing_corpus
from app.rag.fixture_answering import EvidenceChunk, build_fixture_prompt, parse_structured_answer
from app.rag.guardrail import BoundedFixtureGuardrail, GuardrailDecision
from app.rag.s4_5_evaluation import load_s4_5_manifest


_MAX_REQUEST_BYTES = 65_536
_MAX_RESPONSE_BYTES = 262_144
_MAX_CONCURRENCY = 8
_AUTH_METADATA_KEY = "x-decision-grpc-auth"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_SCOPE_CLAIM = re.compile(r"^rag_scope_[0-9a-f]{32}$")
_GENERATION_ID = re.compile(r"^rag_gen_[0-9a-f]{32}$")
_CHUNK_ID = re.compile(r"^rag_chk_[0-9a-f]{32}$")
_SOURCE_REVISION_ID = re.compile(r"^src_rev_[0-9a-f]{32}$")
_SOURCE_ID = re.compile(r"^src_project_[a-z0-9][a-z0-9_]*_[0-9]{3}$")
_FLAG = re.compile(r"^[A-Z0-9_]{1,64}$")
_POLICY_VERSION = re.compile(r"^(?:NONE|EXTERNAL_AI_RAG_V1)$")
_SYMBOL = re.compile(r"^[0-9]{6}$")
_TOPICS = frozenset(
    {"API", "DATA", "FINANCIAL_ENGINEERING", "METHODOLOGY", "PRODUCT_RISK", "RISK"}
)
_PROFILES = frozenset({"bge_m3_local_1024_v1", "voyage_context_4_1024_v1"})
_POLICIES = frozenset({"bge_only_v1", "voyage_only_v1", "bge_then_voyage_on_sla_v1"})


class RagRpcStatus(StrEnum):
    """proto enum과 1:1로 대응하는 내부 typed RAG status다."""

    ANSWERED = "ANSWERED"
    RETRIEVAL_ONLY = "RETRIEVAL_ONLY"
    RETRIEVAL_FAILURE = "RETRIEVAL_FAILURE"
    BLOCKED_SENSITIVE = "BLOCKED_SENSITIVE"
    BLOCKED_ADVICE = "BLOCKED_ADVICE"
    GENERATION_UNAVAILABLE = "GENERATION_UNAVAILABLE"


@dataclass(frozen=True)
class RagRpcCitation:
    """Spring이 active generation과 top-5 scope를 재검증할 citation identity다."""

    citation_id: str
    source_id: str
    source_revision_id: str
    chunk_revision_id: str
    generation_id: str
    title: str
    section_title: str
    canonical_url: str


@dataclass(frozen=True)
class RagEngineResult:
    """provider transport를 포함하지 않는 Python RAG engine의 bounded 결과다."""

    status: RagRpcStatus
    answer: str | None
    citations: tuple[RagRpcCitation, ...]
    authorized_top5_chunk_revision_ids: tuple[str, ...]
    citation_coverage: float
    retrieval_failure: bool
    guardrail_flags: tuple[str, ...]
    failure_code: str
    provider_physical_total: int = 0
    gemini_physical_calls: int = 0
    openai_physical_calls: int = 0
    voyage_physical_calls: int = 0
    external_provider_candidate: bool = False


class RagAskEngine(Protocol):
    """validated gRPC request를 local guard/retrieval/fixture generation으로 평가하는 port다."""

    def ask(self, request: rag_pb2.RagAskRequest) -> RagEngineResult: ...


class S45FixtureRagEngine:
    """S4.5 exact-60의 production RRF와 citation parser를 재사용하는 offline answerer다."""

    def __init__(self) -> None:
        corpus = load_external_processing_corpus()
        self._cards = {card.source_id: card for card in corpus.cards}
        self._questions = {
            item["question"]: item for item in load_s4_5_manifest()["questions"]
        }
        self._guardrail = BoundedFixtureGuardrail()
        self._fusion = RrfFusion()

    def ask(self, request: rag_pb2.RagAskRequest) -> RagEngineResult:
        guard = self._guardrail.classify(request.question)
        if guard.decision is GuardrailDecision.BLOCKED_ADVICE:
            return _blocked(RagRpcStatus.BLOCKED_ADVICE, guard.flags[0])
        if guard.decision is GuardrailDecision.BLOCKED_SENSITIVE:
            return _blocked(RagRpcStatus.BLOCKED_SENSITIVE, guard.flags[0])
        item = self._questions.get(request.question)
        if item is None or item["allowedAnswerStatus"] != "ANSWER":
            return RagEngineResult(
                status=RagRpcStatus.RETRIEVAL_FAILURE,
                answer=None,
                citations=(),
                authorized_top5_chunk_revision_ids=(),
                citation_coverage=0.0,
                retrieval_failure=True,
                guardrail_flags=(),
                failure_code="RAG_INSUFFICIENT_EVIDENCE",
            )

        channels = tuple(
            ChannelResult(
                channel=channel,
                items=tuple(self._candidate(source_id) for source_id in source_ids),
                complete=True,
            )
            for channel, source_ids in (
                ("exact", item["fixtureChannels"]["exact"]),
                ("lexical", item["fixtureChannels"]["lexical"]),
                ("dense", item["fixtureChannels"]["dense"]),
            )
        )
        top5 = tuple(value.candidate for value in self._fusion.fuse(channels)[:5])
        top5_ids = {candidate.source_id for candidate in top5}
        authorized = set(item["authorizedCitationSourceIds"])
        citation_sources = tuple(
            source_id
            for source_id in item["goldRelevantSourceIds"]
            if source_id in top5_ids and source_id in authorized
        )
        if not citation_sources:
            return RagEngineResult(
                status=RagRpcStatus.RETRIEVAL_FAILURE,
                answer=None,
                citations=(),
                authorized_top5_chunk_revision_ids=tuple(
                    candidate.chunk_revision_id for candidate in top5
                ),
                citation_coverage=0.0,
                retrieval_failure=True,
                guardrail_flags=(),
                failure_code="RAG_INSUFFICIENT_EVIDENCE",
            )
        evidence = tuple(
            self._evidence(source_id, index, request.policy_context.active_generation_id)
            for index, source_id in enumerate(citation_sources, start=1)
        )
        prompt = build_fixture_prompt(request.question, evidence)
        if "UNTRUSTED_EVIDENCE_DATA=" not in prompt.payload:
            raise RuntimeError("RAG fixture evidence delimiter drifted")
        citation_ids = tuple(f"cit_{index}" for index in range(1, len(evidence) + 1))
        raw_answer = json.dumps(
            {
                "answer": "공개 fixture 근거로 확인된 경계입니다. "
                + "".join(f"[{citation_id}]" for citation_id in citation_ids),
                "citations": list(citation_ids),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        parsed = parse_structured_answer(
            raw_answer,
            evidence,
            active_generation_id=request.policy_context.active_generation_id,
        )
        citations = tuple(
            self._citation(source_id, index, request.policy_context.active_generation_id)
            for index, source_id in enumerate(citation_sources, start=1)
        )
        return RagEngineResult(
            status=RagRpcStatus.ANSWERED,
            answer=parsed.answer,
            citations=citations,
            authorized_top5_chunk_revision_ids=tuple(
                candidate.chunk_revision_id for candidate in top5
            ),
            citation_coverage=len(citation_sources) / len(item["goldRelevantSourceIds"]),
            retrieval_failure=False,
            guardrail_flags=("FIXTURE_S4_5",),
            failure_code="",
        )

    def _candidate(self, source_id: str) -> RetrievalCandidate:
        card = self._cards[source_id]
        digest = _source_digest(source_id)
        assumptions = tuple(
            str(item["key"])
            for item in card.front_matter.get("modelAssumptions", [])
            if isinstance(item, dict) and isinstance(item.get("key"), str)
        )
        return RetrievalCandidate(
            chunk_revision_id="rag_chk_" + digest[:32],
            source_revision_id="src_rev_" + digest[:32],
            source_id=source_id,
            card_id=card.card_id,
            title=str(card.front_matter["title"]),
            heading_path=("핵심 claim",),
            canonical_content=card.canonical_body,
            canonical_content_hash=card.body_sha256,
            topic=str(card.front_matter["topic"]),
            public_topics=("METHODOLOGY",),
            access_level="PUBLIC",
            tier="PROJECT",
            source_status="VERIFIED",
            evidence_class=str(card.front_matter["evidenceClass"]),
            model_sensitive=card.front_matter.get("modelSensitive") is True,
            assumption_keys=assumptions,
            limitations=tuple(str(value) for value in card.front_matter["limitations"]),
            contradicts_card_ids=tuple(
                str(value) for value in card.front_matter["contradicts"]
            ),
            scope_claim_id="rag_scope_" + "0" * 32,
            owner_user_id="fixture-owner",
            session_id="fixture-session-0001",
            generation_id="rag_gen_" + "0" * 32,
            embedding_profile_id="bge_m3_local_1024_v1",
            policy_version=1,
        )

    def _evidence(
        self, source_id: str, citation_index: int, generation_id: str
    ) -> EvidenceChunk:
        card = self._cards[source_id]
        digest = _source_digest(source_id)
        return EvidenceChunk(
            citation_id=f"cit_{citation_index}",
            source_id=source_id,
            source_revision_id="src_rev_" + digest[:32],
            chunk_revision_id="rag_chk_" + digest[:32],
            generation_id=generation_id,
            title=str(card.front_matter["title"]),
            section_title="핵심 claim",
            canonical_url=str(card.front_matter["canonicalUrl"]),
            content=card.canonical_body,
            access_level="PUBLIC",
            source_status="VERIFIED",
            external_processing_allowed=True,
        )

    def _citation(
        self, source_id: str, citation_index: int, generation_id: str
    ) -> RagRpcCitation:
        card = self._cards[source_id]
        digest = _source_digest(source_id)
        return RagRpcCitation(
            citation_id=f"cit_{citation_index}",
            source_id=source_id,
            source_revision_id="src_rev_" + digest[:32],
            chunk_revision_id="rag_chk_" + digest[:32],
            generation_id=generation_id,
            title=str(card.front_matter["title"]),
            section_title="핵심 claim",
            canonical_url=str(card.front_matter["canonicalUrl"]),
        )


class LoopbackRagServerSettings(Protocol):
    """RAG server factory가 필요한 검증된 loopback bind와 shared secret 표면이다."""

    @property
    def bind_address(self) -> str: ...

    @property
    def shared_secret(self) -> str: ...


@dataclass(frozen=True)
class RagServerResources:
    """테스트와 process entrypoint가 명시적으로 종료할 server와 실제 port다."""

    server: grpc.Server
    bound_port: int


class RagServiceServicer(rag_pb2_grpc.RagServiceServicer):
    """인증·bound·engine result를 재검증하는 unary loopback transport adapter다."""

    def __init__(self, engine: RagAskEngine, shared_secret: str) -> None:
        self._engine = engine
        self._shared_secret = shared_secret

    def Ask(
        self,
        request: rag_pb2.RagAskRequest,
        context: grpc.ServicerContext,
    ) -> rag_pb2.RagAskResponse:
        """JWT/secret/account/history를 받지 않고 bounded owner claim만 평가한다."""

        _require_authenticated(context, self._shared_secret)
        _validate_request(request, context)
        if not context.is_active():
            _abort(context, grpc.StatusCode.CANCELLED, "RAG request was cancelled")
        try:
            result = self._engine.ask(request)
        except Exception:
            _abort(context, grpc.StatusCode.INTERNAL, "RAG local engine failed closed")
        if not context.is_active():
            _abort(context, grpc.StatusCode.CANCELLED, "RAG request was cancelled")
        try:
            _validate_engine_result(result, request)
        except ValueError:
            _abort(context, grpc.StatusCode.DATA_LOSS, "RAG engine response violated contract")
        response = _to_response(request, result)
        if response.ByteSize() > _MAX_RESPONSE_BYTES:
            _abort(context, grpc.StatusCode.DATA_LOSS, "RAG response exceeded bound")
        return response


def create_rag_server(
    settings: LoopbackRagServerSettings,
    engine: RagAskEngine,
) -> RagServerResources:
    """numeric loopback에 health와 RagService만 등록하고 reflection은 등록하지 않는다."""

    if not _is_loopback_address(settings.bind_address):
        raise ValueError("RAG gRPC must bind to numeric loopback")
    if re.fullmatch(r"[A-Za-z0-9._~:-]{32,256}", settings.shared_secret) is None:
        raise ValueError("RAG gRPC shared secret is invalid")
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
    service_name = rag_pb2.DESCRIPTOR.services_by_name["RagService"].full_name
    health_servicer.set(service_name, health_pb2.HealthCheckResponse.SERVING)
    rag_pb2_grpc.add_RagServiceServicer_to_server(  # type: ignore[no-untyped-call]
        RagServiceServicer(engine, settings.shared_secret), server
    )
    bound_port = server.add_insecure_port(settings.bind_address)
    if bound_port == 0:
        raise RuntimeError("RAG gRPC loopback port could not be bound")
    return RagServerResources(server=server, bound_port=bound_port)


def _require_authenticated(context: grpc.ServicerContext, shared_secret: str) -> None:
    values = [
        value
        for key, value in context.invocation_metadata()
        if key == _AUTH_METADATA_KEY
    ]
    supplied = values[0] if len(values) == 1 else None
    if not isinstance(supplied, str) or not compare_digest(supplied, shared_secret):
        _abort(context, grpc.StatusCode.UNAUTHENTICATED, "RAG gRPC authentication failed")


def _validate_request(
    request: rag_pb2.RagAskRequest, context: grpc.ServicerContext
) -> None:
    if request.ByteSize() > _MAX_REQUEST_BYTES:
        _abort(context, grpc.StatusCode.RESOURCE_EXHAUSTED, "RAG request exceeded bound")
    policy = request.policy_context
    consent = request.consent_context
    question_bytes = request.question.encode("utf-8", errors="strict")
    if (
        _REQUEST_ID.fullmatch(request.request_id) is None
        or _SCOPE_CLAIM.fullmatch(request.owner_scope_claim) is None
        or not 1 <= len(request.question) <= 1_000
        or len(question_bytes) > 8_192
        or unicodedata.normalize("NFC", request.question) != request.question
        or any(unicodedata.category(character) in {"Cc", "Cs"} for character in request.question)
        or request.answer_mode not in {"CONCISE", "DETAILED"}
        or len(request.related_symbols) > 5
        or len(set(request.related_symbols)) != len(request.related_symbols)
        or any(_SYMBOL.fullmatch(value) is None for value in request.related_symbols)
        or not 1 <= len(request.topics) <= 5
        or len(set(request.topics)) != len(request.topics)
        or not set(request.topics) <= _TOPICS
        or _POLICY_VERSION.fullmatch(consent.policy_version) is None
        or (consent.granted and consent.policy_version == "NONE")
        or (not consent.granted and consent.policy_version != "NONE")
        or policy.policy_id not in _POLICIES
        or policy.policy_version < 1
        or _GENERATION_ID.fullmatch(policy.active_generation_id) is None
        or policy.embedding_profile_id not in _PROFILES
        or (policy.policy_id == "bge_only_v1" and policy.embedding_profile_id != "bge_m3_local_1024_v1")
        or (policy.policy_id == "voyage_only_v1" and policy.embedding_profile_id != "voyage_context_4_1024_v1")
    ):
        _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "RAG request contract is invalid")


def _validate_engine_result(
    result: RagEngineResult, request: rag_pb2.RagAskRequest
) -> None:
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
        or result.provider_physical_total != 0
        or result.external_provider_candidate
        or not math.isfinite(result.citation_coverage)
        or not 0.0 <= result.citation_coverage <= 1.0
        or len(result.guardrail_flags) > 8
        or len(set(result.guardrail_flags)) != len(result.guardrail_flags)
        or any(_FLAG.fullmatch(value) is None for value in result.guardrail_flags)
        or len(result.citations) > 5
        or len({item.chunk_revision_id for item in result.citations})
        != len(result.citations)
        or len(result.authorized_top5_chunk_revision_ids) > 5
        or len(set(result.authorized_top5_chunk_revision_ids))
        != len(result.authorized_top5_chunk_revision_ids)
        or any(
            _CHUNK_ID.fullmatch(value) is None
            for value in result.authorized_top5_chunk_revision_ids
        )
    ):
        raise ValueError("RAG engine result envelope invalid")
    top5 = set(result.authorized_top5_chunk_revision_ids)
    for index, citation in enumerate(result.citations, start=1):
        if (
            citation.citation_id != f"cit_{index}"
            or _SOURCE_ID.fullmatch(citation.source_id) is None
            or _SOURCE_REVISION_ID.fullmatch(citation.source_revision_id) is None
            or _CHUNK_ID.fullmatch(citation.chunk_revision_id) is None
            or citation.chunk_revision_id not in top5
            or citation.generation_id != request.policy_context.active_generation_id
            or not _bounded_text(citation.title, 1_024)
            or not _bounded_text(citation.section_title, 512)
            or not _safe_public_https_url(citation.canonical_url)
        ):
            raise ValueError("RAG engine citation invalid")
    if result.status is RagRpcStatus.ANSWERED:
        if (
            not _bounded_text(result.answer, 8_192)
            or not result.citations
            or result.citation_coverage != 1.0
            or result.retrieval_failure
            or result.failure_code
        ):
            raise ValueError("RAG answered result invalid")
    elif result.status is RagRpcStatus.RETRIEVAL_FAILURE:
        if (
            result.answer is not None
            or result.citations
            or result.citation_coverage != 0.0
            or not result.retrieval_failure
            or result.failure_code != "RAG_INSUFFICIENT_EVIDENCE"
        ):
            raise ValueError("RAG retrieval failure invalid")
    else:
        if (
            result.answer is not None
            or result.citations
            or result.citation_coverage != 0.0
            or result.retrieval_failure
            or not result.failure_code
        ):
            raise ValueError("RAG withheld result invalid")


def _to_response(
    request: rag_pb2.RagAskRequest, result: RagEngineResult
) -> rag_pb2.RagAskResponse:
    response = rag_pb2.RagAskResponse(
        request_id=request.request_id,
        status=_PROTO_STATUS[result.status],
        citation_coverage=result.citation_coverage,
        retrieval_failure=result.retrieval_failure,
        guardrail_flags=result.guardrail_flags,
        generation_id=request.policy_context.active_generation_id,
        embedding_profile_id=request.policy_context.embedding_profile_id,
        failure_code=result.failure_code,
        provider_physical_counts=rag_pb2.ProviderPhysicalCounts(
            total=result.provider_physical_total,
            gemini=result.gemini_physical_calls,
            openai=result.openai_physical_calls,
            voyage=result.voyage_physical_calls,
        ),
        authorized_top5_chunk_revision_ids=result.authorized_top5_chunk_revision_ids,
        external_provider_candidate=result.external_provider_candidate,
        policy_version=request.policy_context.policy_version,
    )
    if result.answer is not None:
        response.answer = result.answer
    response.citations.extend(
        rag_pb2.RagCitation(
            citation_id=item.citation_id,
            source_id=item.source_id,
            source_revision_id=item.source_revision_id,
            chunk_revision_id=item.chunk_revision_id,
            generation_id=item.generation_id,
            title=item.title,
            section_title=item.section_title,
            canonical_url=item.canonical_url,
        )
        for item in result.citations
    )
    return response


def _blocked(status: RagRpcStatus, reason: str) -> RagEngineResult:
    return RagEngineResult(
        status=status,
        answer=None,
        citations=(),
        authorized_top5_chunk_revision_ids=(),
        citation_coverage=0.0,
        retrieval_failure=False,
        guardrail_flags=(reason,),
        failure_code=reason,
    )


def _source_digest(source_id: str) -> str:
    return hashlib.sha256(source_id.encode("utf-8")).hexdigest()


def _bounded_text(value: object, maximum_bytes: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value.encode("utf-8")) <= maximum_bytes
        and unicodedata.normalize("NFC", value) == value
        and not any(unicodedata.category(character) in {"Cc", "Cs"} for character in value)
    )


def _safe_public_https_url(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 2_048:
        return False
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    host = parsed.hostname.rstrip(".").casefold()
    if host == "localhost" or host.endswith(".localhost"):
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
    RagRpcStatus.ANSWERED: rag_pb2.RAG_RESPONSE_STATUS_ANSWERED,
    RagRpcStatus.RETRIEVAL_ONLY: rag_pb2.RAG_RESPONSE_STATUS_RETRIEVAL_ONLY,
    RagRpcStatus.RETRIEVAL_FAILURE: rag_pb2.RAG_RESPONSE_STATUS_RETRIEVAL_FAILURE,
    RagRpcStatus.BLOCKED_SENSITIVE: rag_pb2.RAG_RESPONSE_STATUS_BLOCKED_SENSITIVE,
    RagRpcStatus.BLOCKED_ADVICE: rag_pb2.RAG_RESPONSE_STATUS_BLOCKED_ADVICE,
    RagRpcStatus.GENERATION_UNAVAILABLE: rag_pb2.RAG_RESPONSE_STATUS_GENERATION_UNAVAILABLE,
}
