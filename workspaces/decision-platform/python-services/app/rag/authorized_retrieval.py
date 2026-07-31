from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Protocol, Sequence

ALLOWED_RAG_TOPICS = frozenset(
    {
        "API",
        "DATA",
        "FINANCIAL_ENGINEERING",
        "METHODOLOGY",
        "PRODUCT_RISK",
        "RISK",
    }
)
SYNONYM_VERSION = "s4-rag-synonyms-v1"
SYNONYM_PAIRS_V1 = (
    ("위험중립", "risk neutral"),
    ("만기", "time to expiry"),
    ("델타 헤지", "delta hedge"),
    ("명목금액", "notional"),
    ("기대손실", "expected shortfall"),
    ("손실위험값", "value at risk"),
    ("백테스트 과적합", "backtest overfitting"),
    ("현재가", "price snapshot"),
    ("휴장일", "market calendar"),
    ("토큰 발급", "oauth token"),
    ("유량 제한", "rate limit"),
    ("공시", "disclosure"),
)
INTERNAL_CHANNEL_LIMIT = 30
INTERNAL_FINAL_LIMIT = 5
RRF_K = 60
EMBEDDING_DIMENSION = 1024
_MAX_LEXICAL_QUERY_BYTES = 12_288
_MAX_EXACT_IDENTIFIERS = 16
_ALLOWED_REQUEST_FIELDS = frozenset(
    {"question", "answerMode", "relatedSymbols", "topics"}
)
_ANSWER_MODES = frozenset({"CONCISE", "DETAILED"})
_SIX_DIGIT_SYMBOL = re.compile(r"(?<![0-9])[0-9]{6}(?![0-9])")
_SOURCE_ID = re.compile(
    r"(?<![a-z0-9_])src_[a-z0-9]{1,16}_[a-z0-9_]{1,64}_[0-9]{3}"
    r"(?![a-z0-9_])"
)
_KIS_TR_ID = re.compile(
    r"(?<![A-Z0-9_])(?:FHKST|HHDFS|TTTC|VTTC|JTTT|CTRP)"
    r"[A-Z0-9]{6,10}(?![A-Z0-9_])"
)
_SCOPE_CLAIM_ID = re.compile(r"^rag_scope_[0-9a-f]{32}$")
_GENERATION_ID = re.compile(r"^rag_gen_[0-9a-f]{32}$")
_OWNER_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SESSION_ID = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")


class QueryValidationError(ValueError):
    """공개 RAG query가 고정된 contract bound를 벗어났을 때 발생한다."""


class RetrievalFailureCode(StrEnum):
    """generation을 시작하지 않고 반환하는 typed retrieval failure."""

    INVALID_QUERY = "RAG_QUERY_INVALID"
    QUERY_EMBEDDING_INVALID = "RAG_QUERY_EMBEDDING_INVALID"
    CHANNEL_UNAVAILABLE = "RAG_RETRIEVAL_CHANNEL_UNAVAILABLE"
    CHANNEL_INCOMPLETE = "RAG_RETRIEVAL_CHANNEL_INCOMPLETE"
    SCOPE_CHANGED = "RAG_RETRIEVAL_SCOPE_CHANGED"
    INSUFFICIENT_EVIDENCE = "RAG_INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class NormalizedRetrievalQuery:
    """NFC·size·allowlist 검증을 마친 retrieval 전용 query."""

    question: str
    answer_mode: str
    related_symbols: tuple[str, ...]
    topics: tuple[str, ...]
    lexical_query: str
    synonym_version: str = SYNONYM_VERSION
    internal_channel_limit: int = INTERNAL_CHANNEL_LIMIT
    internal_final_limit: int = INTERNAL_FINAL_LIMIT


@dataclass(frozen=True)
class AuthorizedRetrievalScope:
    """Spring이 인증 뒤 발급한 짧은 수명의 owner/session DB claim projection."""

    claim_id: str
    owner_user_id: str
    session_id: str
    allowed_topics: tuple[str, ...]
    generation_id: str
    embedding_profile_id: str
    policy_version: int

    def __post_init__(self) -> None:
        if (
            _SCOPE_CLAIM_ID.fullmatch(self.claim_id) is None
            or _OWNER_ID.fullmatch(self.owner_user_id) is None
            or _SESSION_ID.fullmatch(self.session_id) is None
            or _GENERATION_ID.fullmatch(self.generation_id) is None
            or self.embedding_profile_id
            not in {"bge_m3_local_1024_v1", "voyage_context_4_1024_v1"}
            or self.policy_version < 1
            or not 1 <= len(self.allowed_topics) <= len(ALLOWED_RAG_TOPICS)
            or len(set(self.allowed_topics)) != len(self.allowed_topics)
            or not set(self.allowed_topics) <= ALLOWED_RAG_TOPICS
        ):
            raise QueryValidationError("RAG authorized scope is invalid.")


@dataclass(frozen=True)
class RetrievalCandidate:
    """SQL channel이 반환하고 immutable card metadata로 보강한 내부 evidence."""

    chunk_revision_id: str
    source_revision_id: str
    source_id: str
    card_id: str
    title: str
    heading_path: tuple[str, ...]
    canonical_content: str
    canonical_content_hash: str
    topic: str
    public_topics: tuple[str, ...]
    access_level: str
    tier: str
    source_status: str
    evidence_class: str
    model_sensitive: bool
    assumption_keys: tuple[str, ...]
    limitations: tuple[str, ...]
    contradicts_card_ids: tuple[str, ...]
    scope_claim_id: str
    owner_user_id: str
    session_id: str
    generation_id: str
    embedding_profile_id: str
    policy_version: int


@dataclass(frozen=True)
class ChannelResult:
    """한 channel의 bounded 결과와 partial/timeout 여부."""

    channel: str
    items: tuple[RetrievalCandidate, ...]
    complete: bool


@dataclass(frozen=True)
class FusedCandidate:
    """RRF 정렬에만 쓰는 내부 projection이며 public 응답으로 직렬화하지 않는다."""

    candidate: RetrievalCandidate
    rrf_score: float
    channel_count: int
    best_rank: int
    exact_rank: int | None


@dataclass(frozen=True)
class EvidenceDecision:
    """top-5 이후 별도 policy가 판정한 evidence sufficiency."""

    sufficient: bool
    conflict_detected: bool
    conflicting_card_ids: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalOutcome:
    """generation 허용 여부와 bounded evidence만 담는 application 결과."""

    evidence: tuple[RetrievalCandidate, ...]
    failure_code: RetrievalFailureCode | None
    generation_permitted: bool
    conflict_detected: bool = False
    conflicting_card_ids: tuple[str, ...] = ()

    @property
    def distinct_source_count(self) -> int:
        return len({item.source_id for item in self.evidence})


class QueryEmbedder(Protocol):
    """query text를 active profile과 같은 1024차원 단위 vector로 변환한다."""

    @property
    def embedding_profile_id(self) -> str:
        """이 embedder가 생성하는 vector space identity를 반환한다."""

    def embed_query(self, question: str) -> Sequence[float]:
        """provider 호출 없이 한 query vector를 반환한다."""


class AuthorizedExactRetriever(Protocol):
    """opaque claim 안에서 exact identifiers를 최대 30건 조회한다."""

    def retrieve_exact(
        self,
        *,
        scope: AuthorizedRetrievalScope,
        query: NormalizedRetrievalQuery,
        identifiers: tuple[str, ...],
    ) -> ChannelResult:
        """active/public/project/topic/session 범위를 독립적으로 확인한다."""


class AuthorizedLexicalRetriever(Protocol):
    """opaque claim 안에서 pg_trgm 결과를 최대 30건 조회한다."""

    def retrieve_lexical(
        self,
        *,
        scope: AuthorizedRetrievalScope,
        query: NormalizedRetrievalQuery,
    ) -> ChannelResult:
        """active/public/project/topic/session 범위를 독립적으로 확인한다."""


class AuthorizedDenseRetriever(Protocol):
    """opaque claim 안에서 pgvector cosine 결과를 최대 30건 조회한다."""

    def retrieve_dense(
        self,
        *,
        scope: AuthorizedRetrievalScope,
        query: NormalizedRetrievalQuery,
        query_vector: tuple[float, ...],
    ) -> ChannelResult:
        """active/public/project/topic/session 범위를 독립적으로 확인한다."""


class QueryNormalizer:
    """public ask contract의 query 관련 필드만 strict하게 정규화한다."""

    def normalize(self, payload: Mapping[str, object]) -> NormalizedRetrievalQuery:
        if not isinstance(payload, Mapping):
            raise QueryValidationError("RAG query payload must be an object.")
        keys = set(payload)
        if not all(isinstance(key, str) for key in keys):
            raise QueryValidationError("RAG query fields must be strings.")
        if keys - _ALLOWED_REQUEST_FIELDS:
            raise QueryValidationError("RAG query contains a forbidden field.")

        question = payload.get("question")
        answer_mode = payload.get("answerMode")
        if not isinstance(question, str):
            raise QueryValidationError("RAG question bounds are invalid.")
        question_octets = _utf8_octet_length(question)
        if (
            not 1 <= len(question) <= 1000
            or question_octets is None
            or question_octets > 8192
            or unicodedata.normalize("NFC", question) != question
            or any(
                (ord(character) < 0x20 and character not in "\t\n")
                or ord(character) == 0x7F
                for character in question
            )
            or not question.strip()
        ):
            raise QueryValidationError("RAG question bounds are invalid.")
        if not isinstance(answer_mode, str) or answer_mode not in _ANSWER_MODES:
            raise QueryValidationError("RAG answer mode is invalid.")

        symbols = _bounded_string_array(
            payload.get("relatedSymbols", ()),
            maximum=5,
            allowed=None,
            pattern=_SIX_DIGIT_SYMBOL,
            field_name="relatedSymbols",
        )
        topics = _bounded_string_array(
            payload.get("topics", ()),
            maximum=5,
            allowed=ALLOWED_RAG_TOPICS,
            pattern=None,
            field_name="topics",
        )
        lexical_query = _expand_synonyms(question)
        return NormalizedRetrievalQuery(
            question=question,
            answer_mode=answer_mode,
            related_symbols=symbols,
            topics=topics,
            lexical_query=lexical_query,
        )


class ExactIdentifierExtractor:
    """고정 길이·boundary regex로 symbol/source/TR_ID literal만 추출한다."""

    def extract(self, text: str) -> tuple[str, ...]:
        if not isinstance(text, str):
            raise QueryValidationError("RAG identifier input is invalid.")
        text_octets = _utf8_octet_length(text)
        if text_octets is None or text_octets > 8192:
            raise QueryValidationError("RAG identifier input is invalid.")
        identifiers = {
            match.group(0)
            for pattern in (_SIX_DIGIT_SYMBOL, _SOURCE_ID, _KIS_TR_ID)
            for match in pattern.finditer(text)
        }
        if len(identifiers) > _MAX_EXACT_IDENTIFIERS:
            raise QueryValidationError("RAG exact identifier count is invalid.")
        return tuple(sorted(identifiers, key=lambda value: value.encode("utf-8")))


class RrfFusion:
    """세 channel을 k=60 RRF로 합치고 public score 노출 전 내부에서만 정렬한다."""

    def fuse(self, channels: Sequence[ChannelResult]) -> tuple[FusedCandidate, ...]:
        scores: dict[str, float] = {}
        ranks: dict[str, dict[str, int]] = {}
        candidates: dict[str, RetrievalCandidate] = {}

        for channel in channels:
            seen: set[str] = set()
            for rank, candidate in enumerate(channel.items, start=1):
                identity = candidate.chunk_revision_id
                if identity in seen:
                    continue
                seen.add(identity)
                candidates.setdefault(identity, candidate)
                scores[identity] = scores.get(identity, 0.0) + 1.0 / (RRF_K + rank)
                ranks.setdefault(identity, {})[channel.channel] = rank

        fused = tuple(
            FusedCandidate(
                candidate=candidate,
                rrf_score=scores[identity],
                channel_count=len(ranks[identity]),
                best_rank=min(ranks[identity].values()),
                exact_rank=ranks[identity].get("exact"),
            )
            for identity, candidate in candidates.items()
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
                    item.candidate.chunk_revision_id.encode("utf-8"),
                ),
            )
        )


class EvidenceSufficiencyPolicy:
    """fused top-5의 source diversity, assumption, scope, conflict를 판정한다."""

    def evaluate(
        self,
        *,
        scope: AuthorizedRetrievalScope,
        query: NormalizedRetrievalQuery,
        evidence: Sequence[RetrievalCandidate],
        fusion: Sequence[FusedCandidate] = (),
    ) -> EvidenceDecision:
        if len({item.source_id for item in evidence}) < 2:
            return EvidenceDecision(False, False, ())
        if fusion and fusion[0].channel_count < 2 and fusion[0].exact_rank is None:
            return EvidenceDecision(False, False, ())
        if any(not _candidate_in_scope(scope, query, item) for item in evidence):
            return EvidenceDecision(False, False, ())

        sensitive = tuple(item for item in evidence if item.model_sensitive)
        if sensitive:
            if not any(
                item.evidence_class == "PRIMARY_RESEARCH"
                or "METHODOLOGY" in item.public_topics
                for item in evidence
            ):
                return EvidenceDecision(False, False, ())
            for item in sensitive:
                if not item.assumption_keys or not item.limitations:
                    return EvidenceDecision(False, False, ())
                for key in item.assumption_keys:
                    if not any(
                        key in cited.assumption_keys and cited.limitations
                        for cited in evidence
                    ):
                        return EvidenceDecision(False, False, ())

        cited_card_ids = {item.card_id for item in evidence}
        conflict_cards: set[str] = set()
        for item in evidence:
            intersections = cited_card_ids.intersection(item.contradicts_card_ids)
            if intersections:
                conflict_cards.add(item.card_id)
                conflict_cards.update(intersections)
        ordered_conflicts = tuple(
            sorted(conflict_cards, key=lambda value: value.encode("utf-8"))
        )
        return EvidenceDecision(True, bool(ordered_conflicts), ordered_conflicts)


class AuthorizedHybridRetrieval:
    """query validation부터 three-channel RRF와 final scope recheck까지 조정한다."""

    def __init__(
        self,
        *,
        query_normalizer: QueryNormalizer,
        exact_identifier_extractor: ExactIdentifierExtractor,
        query_embedder: QueryEmbedder,
        exact_retriever: AuthorizedExactRetriever,
        lexical_retriever: AuthorizedLexicalRetriever,
        dense_retriever: AuthorizedDenseRetriever,
        rrf_fusion: RrfFusion,
        evidence_sufficiency_policy: EvidenceSufficiencyPolicy,
    ) -> None:
        self._query_normalizer = query_normalizer
        self._exact_identifier_extractor = exact_identifier_extractor
        self._query_embedder = query_embedder
        self._exact_retriever = exact_retriever
        self._lexical_retriever = lexical_retriever
        self._dense_retriever = dense_retriever
        self._rrf_fusion = rrf_fusion
        self._evidence_sufficiency_policy = evidence_sufficiency_policy

    def retrieve(
        self,
        *,
        scope: AuthorizedRetrievalScope,
        payload: Mapping[str, object],
    ) -> RetrievalOutcome:
        try:
            query = self._query_normalizer.normalize(payload)
        except QueryValidationError:
            return _failure(RetrievalFailureCode.INVALID_QUERY)

        try:
            identifiers = tuple(
                sorted(
                    set(self._exact_identifier_extractor.extract(query.question))
                    | set(query.related_symbols),
                    key=lambda value: value.encode("utf-8"),
                )
            )
            if len(identifiers) > _MAX_EXACT_IDENTIFIERS:
                raise QueryValidationError("RAG exact identifier count is invalid.")
        except QueryValidationError:
            return _failure(RetrievalFailureCode.INVALID_QUERY)
        try:
            if (
                self._query_embedder.embedding_profile_id
                != scope.embedding_profile_id
            ):
                raise ValueError("RAG query embedding profile drifted.")
            query_vector = _validated_query_vector(
                self._query_embedder.embed_query(query.question)
            )
        except (ArithmeticError, AttributeError, TypeError, ValueError):
            return _failure(RetrievalFailureCode.QUERY_EMBEDDING_INVALID)
        try:
            channels = (
                self._exact_retriever.retrieve_exact(
                    scope=scope,
                    query=query,
                    identifiers=identifiers,
                ),
                self._lexical_retriever.retrieve_lexical(
                    scope=scope,
                    query=query,
                ),
                self._dense_retriever.retrieve_dense(
                    scope=scope,
                    query=query,
                    query_vector=query_vector,
                ),
            )
        except (TimeoutError, ConnectionError, RuntimeError):
            return _failure(RetrievalFailureCode.CHANNEL_UNAVAILABLE)

        if any(
            channel.channel != expected
            or not channel.complete
            or len(channel.items) > INTERNAL_CHANNEL_LIMIT
            for channel, expected in zip(
                channels,
                ("exact", "lexical", "dense"),
                strict=True,
            )
        ):
            return _failure(RetrievalFailureCode.CHANNEL_INCOMPLETE)

        fused = self._rrf_fusion.fuse(channels)[:INTERNAL_FINAL_LIMIT]
        candidates = tuple(item.candidate for item in fused)
        if any(not _candidate_in_scope(scope, query, item) for item in candidates):
            return _failure(RetrievalFailureCode.SCOPE_CHANGED)

        decision = self._evidence_sufficiency_policy.evaluate(
            scope=scope,
            query=query,
            evidence=candidates,
            fusion=fused,
        )
        if not decision.sufficient:
            return _failure(RetrievalFailureCode.INSUFFICIENT_EVIDENCE)
        return RetrievalOutcome(
            evidence=candidates,
            failure_code=None,
            generation_permitted=True,
            conflict_detected=decision.conflict_detected,
            conflicting_card_ids=decision.conflicting_card_ids,
        )


def _bounded_string_array(
    value: object,
    *,
    maximum: int,
    allowed: frozenset[str] | None,
    pattern: re.Pattern[str] | None,
    field_name: str,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise QueryValidationError(f"RAG {field_name} must be an array.")
    if len(value) > maximum or any(not isinstance(item, str) for item in value):
        raise QueryValidationError(f"RAG {field_name} bounds are invalid.")
    items = tuple(value)
    if len(set(items)) != len(items):
        raise QueryValidationError(f"RAG {field_name} must be unique.")
    if allowed is not None and not set(items) <= allowed:
        raise QueryValidationError(f"RAG {field_name} contains a forbidden value.")
    if pattern is not None and any(pattern.fullmatch(item) is None for item in items):
        raise QueryValidationError(f"RAG {field_name} format is invalid.")
    return items


def _utf8_octet_length(value: str) -> int | None:
    """surrogate를 Unicode scalar 위반으로 처리해 public query 예외를 typed failure로 고정한다."""

    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def _expand_synonyms(question: str) -> str:
    lowered = question.casefold()
    expansions: list[str] = []
    for left, right in SYNONYM_PAIRS_V1:
        left_present = left.casefold() in lowered
        right_present = right.casefold() in lowered
        if left_present and not right_present:
            expansions.append(right)
        elif right_present and not left_present:
            expansions.append(left)
    lexical_query = " ".join((question, *expansions))
    if len(lexical_query.encode("utf-8")) > _MAX_LEXICAL_QUERY_BYTES:
        raise QueryValidationError("RAG synonym expansion exceeded its bound.")
    return lexical_query


def _validated_query_vector(vector: Sequence[float]) -> tuple[float, ...]:
    if isinstance(vector, (str, bytes)) or len(vector) != EMBEDDING_DIMENSION:
        raise ValueError("RAG query embedding dimension is invalid.")
    values = tuple(float(value) for value in vector)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("RAG query embedding must be finite.")
    norm = math.sqrt(math.fsum(value * value for value in values))
    if not math.isfinite(norm) or abs(norm - 1.0) > 0.00001:
        raise ValueError("RAG query embedding must be unit normalized.")
    return values


def _candidate_in_scope(
    scope: AuthorizedRetrievalScope,
    query: NormalizedRetrievalQuery,
    candidate: RetrievalCandidate,
) -> bool:
    candidate_topics = set(candidate.public_topics)
    requested_topics = set(query.topics) or set(scope.allowed_topics)
    return (
        candidate.scope_claim_id == scope.claim_id
        and candidate.owner_user_id == scope.owner_user_id
        and candidate.session_id == scope.session_id
        and candidate.generation_id == scope.generation_id
        and candidate.embedding_profile_id == scope.embedding_profile_id
        and candidate.policy_version == scope.policy_version
        and candidate.access_level == "PUBLIC"
        and candidate.tier == "PROJECT"
        and candidate.source_status == "VERIFIED"
        and bool(candidate_topics.intersection(scope.allowed_topics))
        and bool(candidate_topics.intersection(requested_topics))
    )


def _failure(code: RetrievalFailureCode) -> RetrievalOutcome:
    return RetrievalOutcome(
        evidence=(),
        failure_code=code,
        generation_permitted=False,
    )
