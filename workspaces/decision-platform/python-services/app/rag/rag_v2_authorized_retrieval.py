from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Protocol, Sequence
from urllib.parse import urlsplit

from app.rag.authorized_retrieval import (
    ALLOWED_RAG_TOPICS,
    EMBEDDING_DIMENSION,
    INTERNAL_CHANNEL_LIMIT,
    INTERNAL_FINAL_LIMIT,
    RRF_K,
    ExactIdentifierExtractor,
    NormalizedRetrievalQuery,
    QueryEmbedder,
    QueryNormalizer,
    QueryValidationError,
)

_BUNDLE_SCOPE_CLAIM = re.compile(r"^rvs_[0-9a-f]{32}$")
_OWNER_ID = re.compile(r"^usr_[a-z0-9][a-z0-9_-]{2,95}$")
_SESSION_ID = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_COMPONENT_GENERATION_ID = re.compile(r"^rgr_[0-9a-f]{32}$")
_SOURCE_ID = re.compile(r"^src_[a-z0-9][a-z0-9_-]{2,95}$")
_SOURCE_REVISION_ID = re.compile(r"^srv_[a-z0-9][a-z0-9_-]{2,95}$")
_CHUNK_ID = re.compile(r"^rag_v2_chk_[0-9a-f]{32}$")
_DOCUMENT_ID = re.compile(r"^doc_[a-z0-9][a-z0-9_-]{10,95}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_PROFILES = frozenset({"bge_m3_local_1024_v1", "voyage_context_4_1024_v1"})
_SOURCE_SCOPES = frozenset({"EXACT30", "OA112", "OWNER_PRIVATE"})


class RagV2RetrievalError(ValueError):
    """v2 immutable bundle retrieval contract가 fail-closed했음을 나타낸다."""


class RagV2RetrievalFailureCode(StrEnum):
    """RAG v2 generator를 호출하지 않는 typed retrieval terminal 상태다."""

    INVALID_QUERY = "RAG_QUERY_INVALID"
    QUERY_EMBEDDING_INVALID = "RAG_QUERY_EMBEDDING_INVALID"
    CHANNEL_UNAVAILABLE = "RAG_RETRIEVAL_CHANNEL_UNAVAILABLE"
    CHANNEL_INCOMPLETE = "RAG_RETRIEVAL_CHANNEL_INCOMPLETE"
    SCOPE_CHANGED = "RAG_RETRIEVAL_SCOPE_CHANGED"
    INSUFFICIENT_EVIDENCE = "RAG_INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class RagV2BundleScope:
    """Spring-issued opaque claim으로 pin된 public+owner immutable generation set이다.

    request body나 Python process가 profile, corpus, owner generation을 고르게 하지 않는다.
    DB scope resolver가 현재 active pointer와 owner RLS를 확인한 뒤 이 value만 만들 수 있다.
    """

    claim_id: str
    owner_user_id: str
    session_id: str
    exact30_generation_id: str
    oa112_generation_id: str
    owner_private_generation_id: str | None
    embedding_profile_id: str
    policy_version: int
    allowed_topics: tuple[str, ...]

    def __post_init__(self) -> None:
        component_ids = (
            self.exact30_generation_id,
            self.oa112_generation_id,
            self.owner_private_generation_id,
        )
        non_null_ids = tuple(value for value in component_ids if value is not None)
        if (
            _BUNDLE_SCOPE_CLAIM.fullmatch(self.claim_id) is None
            or _OWNER_ID.fullmatch(self.owner_user_id) is None
            or _SESSION_ID.fullmatch(self.session_id) is None
            or any(_COMPONENT_GENERATION_ID.fullmatch(value) is None for value in non_null_ids)
            or len(set(non_null_ids)) != len(non_null_ids)
            or self.embedding_profile_id not in _ALLOWED_PROFILES
            or self.policy_version < 1
            or not 1 <= len(self.allowed_topics) <= len(ALLOWED_RAG_TOPICS)
            or len(set(self.allowed_topics)) != len(self.allowed_topics)
            or not set(self.allowed_topics) <= ALLOWED_RAG_TOPICS
        ):
            raise RagV2RetrievalError("RAG v2 bundle scope is invalid.")


@dataclass(frozen=True)
class RagV2RetrievalCandidate:
    """authorised DB channel이 반환하는 transient chunk/evidence projection이다.

    `canonical_content`는 Python process 안에서 only-on-request로 Vertex input 후보가 될 수 있지만,
    history, usage ledger, log, public response에는 직렬화하지 않는다.
    """

    canonical_content: str
    canonical_content_sha256: str
    canonical_https_url: str | None
    chunk_id: str
    document_id: str | None
    embedding_profile_id: str
    external_processing_eligible: bool
    generation_id: str
    heading_path: tuple[str, ...]
    locator: Mapping[str, object]
    owner_user_id: str | None
    policy_version: int
    sanitized_display_name: str | None
    scope_claim_id: str
    session_id: str
    source_id: str
    source_revision_id: str
    source_scope: str
    title: str | None
    topics: tuple[str, ...]


@dataclass(frozen=True)
class RagV2ChannelResult:
    """exact/pg_trgm/pgvector bounded DB read의 complete receipt다."""

    channel: str
    items: tuple[RagV2RetrievalCandidate, ...]
    complete: bool


@dataclass(frozen=True)
class RagV2FusedCandidate:
    """application-only RRF ordering metadata이며 wire payload에는 노출하지 않는다."""

    candidate: RagV2RetrievalCandidate
    rrf_score: float
    channel_count: int
    best_rank: int
    exact_rank: int | None


@dataclass(frozen=True)
class RagV2RetrievalOutcome:
    """top-5 evidence와 generation eligibility를 분리해 반환한다."""

    evidence: tuple[RagV2RetrievalCandidate, ...]
    failure_code: RagV2RetrievalFailureCode | None
    retrieval_permitted: bool
    external_generation_permitted: bool = False

    @property
    def distinct_source_count(self) -> int:
        return len({item.source_id for item in self.evidence})


class RagV2ExactRetriever(Protocol):
    """opaque v2 bundle claim으로 exact identifier channel을 최대 30건 읽는다."""

    def retrieve_exact(
        self,
        *,
        scope: RagV2BundleScope,
        query: NormalizedRetrievalQuery,
        identifiers: tuple[str, ...],
    ) -> RagV2ChannelResult:
        """owner/source permission과 active component membership을 SQL에서 먼저 적용한다."""


class RagV2LexicalRetriever(Protocol):
    """opaque v2 bundle claim으로 pg_trgm channel을 최대 30건 읽는다."""

    def retrieve_lexical(
        self,
        *,
        scope: RagV2BundleScope,
        query: NormalizedRetrievalQuery,
    ) -> RagV2ChannelResult:
        """public/owner bundle scope 밖의 chunk를 query text보다 먼저 제외한다."""


class RagV2DenseRetriever(Protocol):
    """opaque v2 bundle claim으로 pgvector channel을 최대 30건 읽는다."""

    def retrieve_dense(
        self,
        *,
        scope: RagV2BundleScope,
        query: NormalizedRetrievalQuery,
        query_vector: tuple[float, ...],
    ) -> RagV2ChannelResult:
        """profile-isolated 1024-d unit vector만 cosine query에 전달한다."""


class RagV2RrfFusion:
    """exact/lexical/dense 결과를 application RRF(k=60)로만 결합한다."""

    def fuse(self, channels: Sequence[RagV2ChannelResult]) -> tuple[RagV2FusedCandidate, ...]:
        scores: dict[str, float] = {}
        ranks: dict[str, dict[str, int]] = {}
        candidates: dict[str, RagV2RetrievalCandidate] = {}
        for channel in channels:
            seen: set[str] = set()
            for rank, candidate in enumerate(channel.items, start=1):
                if candidate.chunk_id in seen:
                    continue
                seen.add(candidate.chunk_id)
                candidates.setdefault(candidate.chunk_id, candidate)
                scores[candidate.chunk_id] = scores.get(candidate.chunk_id, 0.0) + 1.0 / (RRF_K + rank)
                ranks.setdefault(candidate.chunk_id, {})[channel.channel] = rank
        fused = tuple(
            RagV2FusedCandidate(
                candidate=candidate,
                rrf_score=scores[chunk_id],
                channel_count=len(ranks[chunk_id]),
                best_rank=min(ranks[chunk_id].values()),
                exact_rank=ranks[chunk_id].get("exact"),
            )
            for chunk_id, candidate in candidates.items()
        )
        return tuple(
            sorted(
                fused,
                key=lambda item: (
                    -item.rrf_score,
                    -item.channel_count,
                    item.best_rank,
                    item.exact_rank is None,
                    item.candidate.source_id.encode("utf-8"),
                    item.candidate.chunk_id.encode("utf-8"),
                ),
            )
        )


class RagV2AuthorizedHybridRetrieval:
    """v2 bundle scope에서 3-channel RRF와 top-5 recheck를 조정한다."""

    def __init__(
        self,
        *,
        query_normalizer: QueryNormalizer,
        exact_identifier_extractor: ExactIdentifierExtractor,
        query_embedder: QueryEmbedder,
        exact_retriever: RagV2ExactRetriever,
        lexical_retriever: RagV2LexicalRetriever,
        dense_retriever: RagV2DenseRetriever,
        rrf_fusion: RagV2RrfFusion,
    ) -> None:
        self._query_normalizer = query_normalizer
        self._exact_identifier_extractor = exact_identifier_extractor
        self._query_embedder = query_embedder
        self._exact_retriever = exact_retriever
        self._lexical_retriever = lexical_retriever
        self._dense_retriever = dense_retriever
        self._rrf_fusion = rrf_fusion

    def retrieve(
        self,
        *,
        scope: RagV2BundleScope,
        payload: Mapping[str, object],
    ) -> RagV2RetrievalOutcome:
        """untrusted request가 corpus/profile/topK를 바꾸기 전에 validation부터 수행한다."""

        try:
            query = self._query_normalizer.normalize(payload)
            identifiers = tuple(
                sorted(
                    set(self._exact_identifier_extractor.extract(query.question))
                    | set(query.related_symbols),
                    key=lambda value: value.encode("utf-8"),
                )
            )
            if len(identifiers) > 16:
                raise QueryValidationError("RAG v2 exact identifier count is invalid.")
        except QueryValidationError:
            return _failure(RagV2RetrievalFailureCode.INVALID_QUERY)

        try:
            if self._query_embedder.embedding_profile_id != scope.embedding_profile_id:
                raise ValueError("RAG v2 query profile drifted.")
            vector = _validated_query_vector(self._query_embedder.embed_query(query.question))
        except (ArithmeticError, AttributeError, TypeError, ValueError):
            return _failure(RagV2RetrievalFailureCode.QUERY_EMBEDDING_INVALID)

        try:
            channels = (
                self._exact_retriever.retrieve_exact(
                    scope=scope,
                    query=query,
                    identifiers=identifiers,
                ),
                self._lexical_retriever.retrieve_lexical(scope=scope, query=query),
                self._dense_retriever.retrieve_dense(
                    scope=scope,
                    query=query,
                    query_vector=vector,
                ),
            )
        except (ConnectionError, RuntimeError, TimeoutError):
            return _failure(RagV2RetrievalFailureCode.CHANNEL_UNAVAILABLE)

        if not _channels_are_complete(channels):
            return _failure(RagV2RetrievalFailureCode.CHANNEL_INCOMPLETE)
        if any(
            not _candidate_in_scope(scope=scope, query=query, candidate=candidate)
            for channel in channels
            for candidate in channel.items
        ):
            # SQL channel의 owner/source filtering이 outer top-5 recheck까지 기다려서는 안 된다.
            return _failure(RagV2RetrievalFailureCode.SCOPE_CHANGED)

        fused = self._rrf_fusion.fuse(channels)[:INTERNAL_FINAL_LIMIT]
        evidence = tuple(item.candidate for item in fused)
        if not _evidence_is_sufficient(evidence=evidence, fusion=fused):
            return _failure(RagV2RetrievalFailureCode.INSUFFICIENT_EVIDENCE)
        return RagV2RetrievalOutcome(
            evidence=evidence,
            failure_code=None,
            retrieval_permitted=True,
            # One unsafe source must withhold the complete generator input; never omit just that
            # chunk and claim the resulting answer covers the original full bundle request.
            external_generation_permitted=all(
                item.external_processing_eligible for item in evidence
            ),
        )


def _channels_are_complete(channels: tuple[RagV2ChannelResult, ...]) -> bool:
    return all(
        channel.channel == expected
        and channel.complete
        and len(channel.items) <= INTERNAL_CHANNEL_LIMIT
        for channel, expected in zip(channels, ("exact", "lexical", "dense"), strict=True)
    )


def _evidence_is_sufficient(
    *,
    evidence: tuple[RagV2RetrievalCandidate, ...],
    fusion: tuple[RagV2FusedCandidate, ...],
) -> bool:
    if len({item.source_id for item in evidence}) < 2:
        return False
    if fusion and fusion[0].channel_count < 2 and fusion[0].exact_rank is None:
        return False
    return bool(evidence)


def _candidate_in_scope(
    *,
    scope: RagV2BundleScope,
    query: NormalizedRetrievalQuery,
    candidate: RagV2RetrievalCandidate,
) -> bool:
    expected_generation_id = {
        "EXACT30": scope.exact30_generation_id,
        "OA112": scope.oa112_generation_id,
        "OWNER_PRIVATE": scope.owner_private_generation_id,
    }.get(candidate.source_scope)
    effective_topics = set(query.topics) or set(scope.allowed_topics)
    candidate_topics = set(candidate.topics)
    if (
        candidate.source_scope not in _SOURCE_SCOPES
        or expected_generation_id is None
        or candidate.scope_claim_id != scope.claim_id
        or candidate.session_id != scope.session_id
        or candidate.generation_id != expected_generation_id
        or candidate.embedding_profile_id != scope.embedding_profile_id
        or candidate.policy_version != scope.policy_version
        or not candidate_topics <= ALLOWED_RAG_TOPICS
        or not candidate_topics.intersection(scope.allowed_topics)
        or not candidate_topics.intersection(effective_topics)
        or not _candidate_identity_is_valid(candidate)
        or not _candidate_content_is_valid(candidate)
        or not _candidate_citation_shape_is_valid(candidate)
    ):
        return False
    if candidate.source_scope in {"EXACT30", "OA112"}:
        return candidate.owner_user_id is None
    return candidate.owner_user_id == scope.owner_user_id


def _candidate_identity_is_valid(candidate: RagV2RetrievalCandidate) -> bool:
    return (
        _SOURCE_ID.fullmatch(candidate.source_id) is not None
        and _SOURCE_REVISION_ID.fullmatch(candidate.source_revision_id) is not None
        and _CHUNK_ID.fullmatch(candidate.chunk_id) is not None
        and _COMPONENT_GENERATION_ID.fullmatch(candidate.generation_id) is not None
        and isinstance(candidate.external_processing_eligible, bool)
        and candidate.policy_version >= 1
        and 0 <= len(candidate.heading_path) <= 12
        and all(isinstance(part, str) and 0 < len(part) <= 300 for part in candidate.heading_path)
        and _locator_is_valid(candidate.locator)
    )


def _candidate_content_is_valid(candidate: RagV2RetrievalCandidate) -> bool:
    if (
        not isinstance(candidate.canonical_content, str)
        or not 1 <= len(candidate.canonical_content.encode("utf-8")) <= 1_048_576
        or _SHA256.fullmatch(candidate.canonical_content_sha256) is None
        or hashlib.sha256(candidate.canonical_content.encode("utf-8")).hexdigest()
        != candidate.canonical_content_sha256
    ):
        return False
    return True


def _candidate_citation_shape_is_valid(candidate: RagV2RetrievalCandidate) -> bool:
    if candidate.source_scope in {"EXACT30", "OA112"}:
        return (
            isinstance(candidate.title, str)
            and 1 <= len(candidate.title) <= 1_024
            and _safe_public_https(candidate.canonical_https_url)
            # Public source document IDs are internal immutable graph identities. They may be
            # present in the DB row but are never emitted in PublicWebCitation.
            and (
                candidate.document_id is None
                or _DOCUMENT_ID.fullmatch(candidate.document_id) is not None
            )
            and candidate.sanitized_display_name is None
        )
    return (
        _DOCUMENT_ID.fullmatch(candidate.document_id or "") is not None
        and isinstance(candidate.sanitized_display_name, str)
        and 1 <= len(candidate.sanitized_display_name) <= 160
        and "/" not in candidate.sanitized_display_name
        and "\\" not in candidate.sanitized_display_name
        and ":" not in candidate.sanitized_display_name
        and candidate.title is None
        and candidate.canonical_https_url is None
    )


def _safe_public_https(value: str | None) -> bool:
    if not isinstance(value, str) or not 9 <= len(value) <= 2_048:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    hostname = (parsed.hostname or "").lower().strip("[]")
    return (
        parsed.scheme == "https"
        and bool(hostname)
        and parsed.username is None
        and parsed.password is None
        and parsed.port in (None, 443)
        and not parsed.fragment
        and hostname not in {"localhost", "0.0.0.0", "127.0.0.1", "::1"}
        and not hostname.endswith((".localhost", ".local", ".internal", ".home.arpa", ".test"))
    )


def _locator_is_valid(locator: Mapping[str, object]) -> bool:
    if not isinstance(locator, Mapping) or len(locator) != 1:
        return False
    key, value = next(iter(locator.items()))
    if key in {"page", "slide"}:
        return type(value) is int and value > 0
    if key == "sheet":
        return _locator_text_is_valid(value, maximum=128)
    if key == "section":
        return _locator_text_is_valid(value, maximum=300)
    return False


def _locator_text_is_valid(value: object, *, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and not any(character in value for character in ("/", "\\", "\x00", "\r", "\n"))
        and not value.startswith((".", "~"))
    )


def _validated_query_vector(vector: Sequence[float]) -> tuple[float, ...]:
    if isinstance(vector, (bytes, str)) or len(vector) != EMBEDDING_DIMENSION:
        raise ValueError("RAG v2 query embedding dimension is invalid.")
    values = tuple(float(value) for value in vector)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("RAG v2 query embedding must be finite.")
    norm = math.sqrt(math.fsum(value * value for value in values))
    if not math.isfinite(norm) or abs(norm - 1.0) > 0.00001:
        raise ValueError("RAG v2 query embedding must be unit normalized.")
    return values


def _failure(code: RagV2RetrievalFailureCode) -> RagV2RetrievalOutcome:
    return RagV2RetrievalOutcome(
        evidence=(),
        failure_code=code,
        retrieval_permitted=False,
        external_generation_permitted=False,
    )
