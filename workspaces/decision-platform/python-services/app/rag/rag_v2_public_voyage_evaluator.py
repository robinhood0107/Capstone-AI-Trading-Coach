"""Packet-gated live-query evaluation for the public Voyage immutable pair.

This evaluator keeps the pre-activation public vectors and canonical text in one process only.  It
uses the same RRF implementation as the BGE evaluator, but every evaluation question must obtain a
separate closed local Voyage packet and writer lease.  A packet, transport, channel, or scope failure
halts the remaining evaluation queries; no BGE vector or alternate provider can fill the gap.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

from app.rag.authorized_retrieval import (
    ALLOWED_RAG_TOPICS,
    EMBEDDING_DIMENSION,
    ExactIdentifierExtractor,
    QueryNormalizer,
)
from app.rag.oa112_active_registry import Oa112ActiveRegistry
from app.rag.oa_release_manifest import OA_TRACK_IDS
from app.rag.pre_s5_provider_control import (
    PreS5ProviderActivationError,
    PreS5ProviderBinding,
    PreS5VoyageEvaluationBatchActivation,
    load_pre_s5_voyage_evaluation_batch_activation,
    load_pre_s5_voyage_evaluation_query_activation,
    pre_s5_voyage_evaluation_batch_manifest_sha256,
)
from app.rag.pre_s5_voyage_evaluation_batch_transport import (
    PreS5VoyageEvaluationBatchTransport,
    PreS5VoyageEvaluationBatchTransportError,
)
from app.rag.pre_s5_voyage_query_transport import (
    PreS5VoyageContext4QueryEmbedder,
    PreS5VoyageQueryTransportError,
    PreS5VoyageQueryUsageReservationPort,
)
from app.rag.pre_s5_voyage_tokenizer import (
    LocalPreS5VoyageContext4Tokenizer,
    PreS5VoyageTokenizerError,
)
from app.rag.pre_s5_voyage_transport import PreS5VoyageAttemptLease, PreS5VoyageHttpSender
from app.rag.rag_v2_authorized_retrieval import (
    RagV2AuthorizedHybridRetrieval,
    RagV2BundleScope,
    RagV2QueryEmbeddingError,
    RagV2QueryEmbeddingReceipt,
    RagV2RetrievalCandidate,
    RagV2RetrievalFailureCode,
    RagV2RrfFusion,
)
from app.rag.rag_v2_external_exact30_voyage_runner import (
    RagV2PublicVoyageComponentContext,
    RagV2VoyageMaterializedPublicDocument,
)
from app.rag.rag_v2_oa112_voyage_runner import RagV2Oa112VoyageComponentContext
from app.rag.rag_v2_public_bge_evaluator import (
    PublicBgeEvaluationQuery,
    _direct_advice_block_rate,
    _InMemoryCandidate,
    _InMemoryPublicChannels,
    _minimum_track_recall,
    _p95_millis,
    _ratio,
    _sha256_json,
    _valid_public_citation,
    load_exact30_evaluation_queries,
    load_oa112_evaluation_queries,
)
from app.rag.rag_v2_public_voyage_evaluation_manifest import (
    public_voyage_evaluation_input_root,
)
from app.rag.rag_v2_public_voyage_staging_repository import PublicVoyageEvaluationEvidence

_VOYAGE_PROFILE_ID = "voyage_context_4_1024_v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ID = re.compile(r"^src_[a-z0-9][a-z0-9_-]{2,95}$")


class PublicVoyagePairEvaluationError(ValueError):
    """Public Voyage evaluation could not produce safe, complete, packet-accounted evidence."""


class VoyageEvaluationQueryEmbedder(Protocol):
    """The evaluator seam preserves an exact zero-or-one physical-call receipt per logical query."""

    @property
    def embedding_profile_id(self) -> str:
        """Return only the immutable vector-space identity used by both query and document inputs."""

    def embed_query_with_receipt(
        self,
        *,
        question: str,
        scope_claim_id: str,
        external_query_consent_granted: bool,
    ) -> RagV2QueryEmbeddingReceipt:
        """Return one 1024d query vector or a typed zero/one-attempt terminal failure."""

    def embed_query(self, question: str) -> Sequence[float]:
        """Protocol compatibility only; contextual evaluation must use the receipt-bearing method above."""


class _EvaluationQueryReceiptCache:
    """한 번 소비한 provider receipt를 ranking metric 재구성에만 process-local로 보존한다."""

    def __init__(self, delegate: VoyageEvaluationQueryEmbedder) -> None:
        self._delegate = delegate
        self._receipts: dict[str, RagV2QueryEmbeddingReceipt] = {}

    @property
    def embedding_profile_id(self) -> str:
        return self._delegate.embedding_profile_id

    def embed_query_with_receipt(
        self,
        *,
        question: str,
        scope_claim_id: str,
        external_query_consent_granted: bool,
    ) -> RagV2QueryEmbeddingReceipt:
        question_sha256 = hashlib.sha256(question.encode("utf-8")).hexdigest()
        if question_sha256 in self._receipts:
            raise RagV2QueryEmbeddingError(RagV2RetrievalFailureCode.QUERY_EMBEDDING_INVALID)
        receipt = self._delegate.embed_query_with_receipt(
            question=question,
            scope_claim_id=scope_claim_id,
            external_query_consent_granted=external_query_consent_granted,
        )
        self._receipts[question_sha256] = receipt
        return receipt

    def receipt_for_evaluation(self, *, question: str) -> RagV2QueryEmbeddingReceipt:
        """Provider를 다시 부르지 않고 이미 검증된 vector receipt만 반환한다."""

        receipt = self._receipts.get(hashlib.sha256(question.encode("utf-8")).hexdigest())
        if receipt is None:
            raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_QUERY_UNAVAILABLE")
        return receipt

    def embed_query(self, question: str) -> Sequence[float]:
        del question
        raise RagV2QueryEmbeddingError(RagV2RetrievalFailureCode.QUERY_PROFILE_UNAVAILABLE)


class PreS5VoyageEvaluationBatchRepositoryPort(PreS5VoyageQueryUsageReservationPort, Protocol):
    """평가 usage lease와 durable component vector stage/resume를 함께 제공한다."""

    def resume_evaluation_batch(
        self,
        *,
        scope_claim_sha256: str,
        component_scope: Literal["EXACT30", "OA112"],
        query_manifest_sha256: str,
        expected_query_sha256s: Sequence[str],
    ) -> Mapping[str, tuple[float, ...]] | None:
        """완료된 component vector set 또는 아직 미시작인 None을 반환한다."""

    def stage_evaluation_batch(
        self,
        *,
        activation: PreS5VoyageEvaluationBatchActivation,
        lease: PreS5VoyageAttemptLease,
        vectors_by_query_sha256: Mapping[str, Sequence[float]],
        expected_input_tokens: int,
        total_tokens: int,
        actual_cost_microusd: int,
    ) -> None:
        """provider usage와 vector set을 한 transaction으로 durable stage한다."""


@dataclass(frozen=True, slots=True)
class PublicVoyagePairEvaluation:
    """Content-free acceptance result split by the exact fixture and OA112 corpus obligations."""

    evaluation_plan_digest: str
    evaluation_scope_claim_sha256: str
    exact30_generation_id: str
    oa112_generation_id: str
    exact30: PublicVoyageEvaluationEvidence
    oa112: PublicVoyageEvaluationEvidence

    @property
    def acceptance_passed(self) -> bool:
        """A result is usable only if both component records satisfy V45's exact acceptance gates."""

        return _accepts(self.exact30, expected_calls=1) and _accepts(self.oa112, expected_calls=1)


class PacketGatedPublicVoyageEvaluationQueryEmbedder:
    """Resolve one closed evaluation fixture ID to one local packet and one Voyage query attempt.

    Unlike the normal ask-runtime loader, this object reads only the fixed `q01..q10` and
    `oa112-q001..oa112-q112` packet leaves.  It cannot choose a pathname, change the query, or use a
    packet after its scope binding changes.
    """

    def __init__(
        self,
        *,
        local_root: Path,
        binding: PreS5ProviderBinding,
        api_key: str,
        usage_repository: PreS5VoyageQueryUsageReservationPort,
        query_id_by_sha256: Mapping[str, str],
        sender: PreS5VoyageHttpSender,
    ) -> None:
        if (
            not isinstance(local_root, Path)
            or not local_root.is_absolute()
            or not isinstance(binding, PreS5ProviderBinding)
            or not isinstance(api_key, str)
            or not api_key
            or usage_repository is None
            or sender is None
        ):
            raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_RUNTIME")
        copied = dict(query_id_by_sha256)
        if (
            len(copied) != 122
            or any(_SHA256.fullmatch(key) is None for key in copied)
            or len(set(copied.values())) != 122
        ):
            raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_RUNTIME")
        self._local_root = local_root
        self._binding = binding
        self._api_key = api_key
        self._usage_repository = usage_repository
        self._query_id_by_sha256 = copied
        self._sender = sender
        self._attempt_lock = threading.Lock()
        self._physical_attempts = {"EXACT30": 0, "OA112": 0}

    @property
    def embedding_profile_id(self) -> str:
        """The evaluator is not permitted to substitute local BGE into an active Voyage profile."""

        return _VOYAGE_PROFILE_ID

    def embed_query_with_receipt(
        self,
        *,
        question: str,
        scope_claim_id: str,
        external_query_consent_granted: bool,
    ) -> RagV2QueryEmbeddingReceipt:
        """Load the question's own packet immediately before its single fixed-origin HTTP request."""

        if not external_query_consent_granted:
            raise RagV2QueryEmbeddingError(RagV2RetrievalFailureCode.QUERY_PROFILE_UNAVAILABLE)
        query_sha256 = hashlib.sha256(question.encode("utf-8")).hexdigest()
        evaluation_query_id = self._query_id_by_sha256.get(query_sha256)
        if evaluation_query_id is None:
            raise RagV2QueryEmbeddingError(RagV2RetrievalFailureCode.QUERY_PROFILE_UNAVAILABLE)
        try:
            activation = load_pre_s5_voyage_evaluation_query_activation(
                local_root=self._local_root,
                binding=self._binding,
                evaluation_query_id=evaluation_query_id,
                question=question,
                scope_claim_id=scope_claim_id,
            )
            token_counter = LocalPreS5VoyageContext4Tokenizer.from_local_root(
                local_root=self._local_root,
                expected_sha256=activation.tokenizer_sha256,
            )
            lease = self._usage_repository.reserve(
                activation=activation,
                evaluation_component_scope=(
                    "EXACT30" if evaluation_query_id.startswith("q") else "OA112"
                ),
            )
            request_embedder = PreS5VoyageContext4QueryEmbedder(
                activation=activation,
                api_key=self._api_key,
                lease=lease,
                token_counter=token_counter,
                sender=self._sender,
            )
        except (
            PreS5ProviderActivationError,
            PreS5VoyageQueryTransportError,
            PreS5VoyageTokenizerError,
            ValueError,
        ):
            raise RagV2QueryEmbeddingError(
                RagV2RetrievalFailureCode.QUERY_PROFILE_UNAVAILABLE
            ) from None
        component_scope = "EXACT30" if evaluation_query_id.startswith("q") else "OA112"
        try:
            receipt = request_embedder.embed_query_with_receipt(
                question=question,
                scope_claim_id=scope_claim_id,
                external_query_consent_granted=True,
            )
        except RagV2QueryEmbeddingError as error:
            self._record_physical_attempt(component_scope, error.voyage_physical_calls)
            raise
        self._record_physical_attempt(component_scope, receipt.voyage_physical_calls)
        return receipt

    def embed_query(self, question: str) -> Sequence[float]:
        """Prevent any caller from bypassing the packet/lease receipt path through a legacy method."""

        del question
        raise RagV2QueryEmbeddingError(RagV2RetrievalFailureCode.QUERY_PROFILE_UNAVAILABLE)

    def content_free_summary(self) -> dict[str, int | str]:
        """Return only aggregate consumed query-attempt counts for a terminal operator receipt."""

        with self._attempt_lock:
            exact30_attempts = self._physical_attempts["EXACT30"]
            oa112_attempts = self._physical_attempts["OA112"]
        return {
            "code": "PUBLIC_VOYAGE_EVALUATION_QUERY_ATTEMPTS",
            "exact30QueryPhysicalCallCount": exact30_attempts,
            "oa112QueryPhysicalCallCount": oa112_attempts,
            "rawArtifactCount": 0,
        }

    def _record_physical_attempt(self, component_scope: str, count: int) -> None:
        """A failed one-shot call still counts once, but malformed zero-call setup does not."""

        if component_scope not in self._physical_attempts or count not in {0, 1}:
            raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_RUNTIME")
        if count == 0:
            return
        with self._attempt_lock:
            self._physical_attempts[component_scope] += 1


class PacketGatedPublicVoyageEvaluationBatchEmbedder:
    """첫 component 질문에서 singleton-group batch 한 번을 실행하고 나머지 vector를 reuse한다."""

    def __init__(
        self,
        *,
        local_root: Path,
        tokenizer_local_root: Path,
        binding: PreS5ProviderBinding,
        api_key: str,
        usage_repository: PreS5VoyageEvaluationBatchRepositoryPort,
        exact30_queries: Sequence[PublicBgeEvaluationQuery],
        oa112_queries: Sequence[PublicBgeEvaluationQuery],
        tokenizer_sha256: str,
        sender: PreS5VoyageHttpSender,
    ) -> None:
        _validate_queries(exact30_queries=exact30_queries, oa112_queries=oa112_queries)
        if (
            not isinstance(local_root, Path)
            or not local_root.is_absolute()
            or not isinstance(tokenizer_local_root, Path)
            or not tokenizer_local_root.is_absolute()
            or not isinstance(binding, PreS5ProviderBinding)
            or not isinstance(api_key, str)
            or not api_key
            or usage_repository is None
            or _SHA256.fullmatch(tokenizer_sha256) is None
            or sender is None
        ):
            raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_RUNTIME")
        self._local_root = local_root
        self._tokenizer_local_root = tokenizer_local_root
        self._binding = binding
        self._api_key = api_key
        self._usage_repository = usage_repository
        self._tokenizer_sha256 = tokenizer_sha256
        self._sender = sender
        self._queries = {
            "EXACT30": tuple((query.query_id, query.question) for query in exact30_queries),
            "OA112": tuple((query.query_id, query.question) for query in oa112_queries),
        }
        self._component_by_question_sha256 = {
            hashlib.sha256(question.encode("utf-8")).hexdigest(): component_scope
            for component_scope, queries in self._queries.items()
            for _, question in queries
        }
        if len(self._component_by_question_sha256) != 122:
            raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_RUNTIME")
        self._vectors: dict[str, tuple[float, ...]] = {}
        self._loaded_components: set[str] = set()
        self._attempt_lock = threading.Lock()
        self._physical_attempts = {"EXACT30": 0, "OA112": 0}

    @property
    def embedding_profile_id(self) -> str:
        return _VOYAGE_PROFILE_ID

    def embed_query_with_receipt(
        self,
        *,
        question: str,
        scope_claim_id: str,
        external_query_consent_granted: bool,
    ) -> RagV2QueryEmbeddingReceipt:
        """component 첫 lookup만 physical call 1을 보고하고 이후 질문은 staged vector를 reuse한다."""

        if not external_query_consent_granted:
            raise RagV2QueryEmbeddingError(RagV2RetrievalFailureCode.QUERY_PROFILE_UNAVAILABLE)
        query_sha256 = hashlib.sha256(question.encode("utf-8")).hexdigest()
        component_scope = self._component_by_question_sha256.get(query_sha256)
        if component_scope is None:
            raise RagV2QueryEmbeddingError(RagV2RetrievalFailureCode.QUERY_PROFILE_UNAVAILABLE)
        physical_calls = 0
        with self._attempt_lock:
            if component_scope not in self._loaded_components:
                physical_calls = self._load_component(
                    component_scope=component_scope,
                    scope_claim_id=scope_claim_id,
                )
            vector = self._vectors.get(query_sha256)
        if vector is None:
            raise RagV2QueryEmbeddingError(RagV2RetrievalFailureCode.QUERY_EMBEDDING_INVALID)
        return RagV2QueryEmbeddingReceipt(vector=vector, voyage_physical_calls=physical_calls)

    def embed_query(self, question: str) -> Sequence[float]:
        del question
        raise RagV2QueryEmbeddingError(RagV2RetrievalFailureCode.QUERY_PROFILE_UNAVAILABLE)

    def content_free_summary(self) -> dict[str, int | str]:
        with self._attempt_lock:
            exact30_attempts = self._physical_attempts["EXACT30"]
            oa112_attempts = self._physical_attempts["OA112"]
        return {
            "code": "PUBLIC_VOYAGE_EVALUATION_BATCH_ATTEMPTS",
            "exact30QueryPhysicalCallCount": exact30_attempts,
            "oa112QueryPhysicalCallCount": oa112_attempts,
            "rawArtifactCount": 0,
        }

    def _load_component(self, *, component_scope: str, scope_claim_id: str) -> int:
        """lock 아래 한 번만 packet/lease/provider를 소비해 concurrent partial reload를 차단한다."""

        queries = self._queries[component_scope]
        typed_scope = cast(Literal["EXACT30", "OA112"], component_scope)
        query_manifest_sha256 = pre_s5_voyage_evaluation_batch_manifest_sha256(
            component_scope=component_scope,
            query_id_questions=queries,
            scope_claim_id=scope_claim_id,
        )
        scope_claim_sha256 = hashlib.sha256(scope_claim_id.encode("utf-8")).hexdigest()
        query_sha256s = tuple(
            hashlib.sha256(question.encode("utf-8")).hexdigest() for _, question in queries
        )
        resumed = self._usage_repository.resume_evaluation_batch(
            scope_claim_sha256=scope_claim_sha256,
            component_scope=typed_scope,
            query_manifest_sha256=query_manifest_sha256,
            expected_query_sha256s=query_sha256s,
        )
        if resumed is not None:
            if set(resumed) & set(self._vectors):
                raise RagV2QueryEmbeddingError(RagV2RetrievalFailureCode.QUERY_EMBEDDING_INVALID)
            self._vectors.update(resumed)
            self._loaded_components.add(component_scope)
            # receipt는 이번 process 호출 수가 아니라 generation ledger의 exact physical count다.
            self._physical_attempts[component_scope] += 1
            return 1
        physical_calls = 0
        lease = None
        try:
            tokenizer = LocalPreS5VoyageContext4Tokenizer.from_local_root(
                local_root=self._tokenizer_local_root,
                expected_sha256=self._tokenizer_sha256,
            )
            expected_tokens = tokenizer.count_texts(
                texts=tuple(question for _, question in queries),
                token_cap=8_192,
            )
            activation = load_pre_s5_voyage_evaluation_batch_activation(
                local_root=self._local_root,
                binding=self._binding,
                component_scope=component_scope,
                query_id_questions=queries,
                scope_claim_id=scope_claim_id,
                expected_token_count=expected_tokens,
            )
            lease = self._usage_repository.reserve(
                activation=activation,
                evaluation_component_scope=cast(Literal["EXACT30", "OA112"], component_scope),
            )
            result = PreS5VoyageEvaluationBatchTransport(
                activation=activation,
                api_key=self._api_key,
                lease=lease,
                token_counter=tokenizer,
                sender=self._sender,
            ).embed(query_id_questions=queries)
            physical_calls = result.voyage_physical_calls
            self._usage_repository.stage_evaluation_batch(
                activation=activation,
                lease=lease,
                vectors_by_query_sha256=result.vectors_by_query_sha256,
                expected_input_tokens=result.expected_input_tokens,
                total_tokens=result.total_tokens,
                actual_cost_microusd=result.actual_cost_microusd,
            )
        except (
            PreS5ProviderActivationError,
            PreS5VoyageTokenizerError,
            PreS5VoyageEvaluationBatchTransportError,
            ValueError,
        ) as error:
            physical_calls = max(physical_calls, getattr(error, "voyage_physical_calls", 0))
            if physical_calls == 1 and lease is not None:
                try:
                    lease.mark_unknown_billing()
                except Exception:
                    pass
            if physical_calls in {0, 1}:
                self._physical_attempts[component_scope] += physical_calls
            raise RagV2QueryEmbeddingError(
                RagV2RetrievalFailureCode.QUERY_EMBEDDING_INVALID,
                voyage_physical_calls=physical_calls,
            ) from None
        if set(result.vectors_by_query_sha256) & set(self._vectors):
            raise RagV2QueryEmbeddingError(RagV2RetrievalFailureCode.QUERY_EMBEDDING_INVALID)
        self._vectors.update(result.vectors_by_query_sha256)
        self._loaded_components.add(component_scope)
        self._physical_attempts[component_scope] += result.voyage_physical_calls
        return result.voyage_physical_calls


def load_public_voyage_evaluation_inputs(
    *,
    local_root: Path,
    registry: Oa112ActiveRegistry,
    exact30_context: RagV2PublicVoyageComponentContext,
) -> tuple[tuple[PublicBgeEvaluationQuery, ...], str, tuple[PublicBgeEvaluationQuery, ...], str]:
    """Load the same frozen exact/OA query sets while binding them to the current public source identities."""

    try:
        input_root = public_voyage_evaluation_input_root(local_root)
        exact30_queries, exact30_fixture_digest = load_exact30_evaluation_queries(
            source_card_corpus_manifest_sha256=exact30_context.source_card_corpus_manifest_sha256,
            fixture_path=input_root / "exact30-evaluation-manifest.v1.json",
        )
        oa112_queries, oa112_manifest_digest = load_oa112_evaluation_queries(
            approved_root=input_root,
            registry=registry,
        )
    except Exception:
        raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_INPUT") from None
    return exact30_queries, exact30_fixture_digest, oa112_queries, oa112_manifest_digest


def evaluation_query_id_by_sha256(
    *,
    exact30_queries: Sequence[PublicBgeEvaluationQuery],
    oa112_queries: Sequence[PublicBgeEvaluationQuery],
) -> dict[str, str]:
    """Map every fixture question to its closed packet filename without retaining text outside the process."""

    _validate_queries(exact30_queries=exact30_queries, oa112_queries=oa112_queries)
    mapping: dict[str, str] = {}
    for query in exact30_queries:
        mapping[hashlib.sha256(query.question.encode("utf-8")).hexdigest()] = query.query_id
    for query in oa112_queries:
        mapping[hashlib.sha256(query.question.encode("utf-8")).hexdigest()] = query.query_id
    if len(mapping) != 122:
        raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_INPUT")
    return mapping


def evaluate_public_voyage_pair(
    *,
    exact30_records: Sequence[RagV2VoyageMaterializedPublicDocument],
    exact30_context: RagV2PublicVoyageComponentContext,
    oa112_records: Sequence[RagV2VoyageMaterializedPublicDocument],
    oa112_context: RagV2Oa112VoyageComponentContext,
    oa112_registry_digest: str,
    exact30_queries: Sequence[PublicBgeEvaluationQuery],
    exact30_fixture_digest: str,
    oa112_queries: Sequence[PublicBgeEvaluationQuery],
    oa112_manifest_digest: str,
    query_embedder: VoyageEvaluationQueryEmbedder,
) -> PublicVoyagePairEvaluation:
    """Run all 122 packet-gated Voyage queries against the transient full public pair using production RRF.

    Any provider/packet/channel failure stops this function before a later fixture question is attempted.
    A successful but semantically poor vector still produces a complete aggregate result, which V45
    rejects instead of silently shopping a different model or fallback.
    """

    _validate_inputs(
        exact30_records=exact30_records,
        exact30_context=exact30_context,
        oa112_records=oa112_records,
        oa112_context=oa112_context,
        oa112_registry_digest=oa112_registry_digest,
        exact30_queries=exact30_queries,
        exact30_fixture_digest=exact30_fixture_digest,
        oa112_queries=oa112_queries,
        oa112_manifest_digest=oa112_manifest_digest,
        query_embedder=query_embedder,
    )
    plan_digest = _evaluation_plan_digest(
        exact30_context=exact30_context,
        oa112_context=oa112_context,
        oa112_registry_digest=oa112_registry_digest,
        exact30_fixture_digest=exact30_fixture_digest,
        oa112_manifest_digest=oa112_manifest_digest,
    )
    scope = _evaluation_scope(
        exact30_context=exact30_context,
        oa112_context=oa112_context,
        plan_digest=plan_digest,
    )
    scope_claim_sha256 = hashlib.sha256(scope.claim_id.encode("utf-8")).hexdigest()
    channels = _InMemoryPublicChannels(
        _build_candidates(
            exact30_records=tuple(exact30_records),
            exact30_context=exact30_context,
            oa112_records=tuple(oa112_records),
            oa112_context=oa112_context,
            scope=scope,
        )
    )
    receipt_cache = _EvaluationQueryReceiptCache(query_embedder)
    retrieval = RagV2AuthorizedHybridRetrieval(
        query_normalizer=QueryNormalizer(),
        exact_identifier_extractor=ExactIdentifierExtractor(),
        query_embedder=receipt_cache,
        exact_retriever=channels,
        lexical_retriever=channels,
        dense_retriever=channels,
        rrf_fusion=RagV2RrfFusion(),
    )
    exact_hits, exact_citations, exact_durations, exact_calls = _evaluate_queries(
        retrieval=retrieval,
        channels=channels,
        receipt_cache=receipt_cache,
        scope=scope,
        queries=tuple(exact30_queries),
    )
    oa_hits, oa_citations, oa_durations, oa_calls = _evaluate_queries(
        retrieval=retrieval,
        channels=channels,
        receipt_cache=receipt_cache,
        scope=scope,
        queries=tuple(oa112_queries),
    )
    exact_top5_hit_rate = _ratio(len(exact_hits), len(exact30_queries))
    track_recall_at5 = _minimum_track_recall(queries=tuple(oa112_queries), hits=oa_hits)
    exact_citation_coverage = _ratio(len(exact_citations), len(exact30_queries))
    oa112_citation_coverage = _ratio(len(oa_citations), len(oa112_queries))
    direct_advice_block_rate = _direct_advice_block_rate()
    exact_evidence = _evidence(
        component_scope="EXACT30",
        component_generation_id=exact30_context.component_generation_id,
        plan_digest=plan_digest,
        exact_top5_hit_rate=exact_top5_hit_rate,
        track_recall_at5=track_recall_at5,
        citation_coverage=exact_citation_coverage,
        direct_advice_block_rate=direct_advice_block_rate,
        warm_p95_millis=_p95_millis(exact_durations),
        provider_physical_call_count=exact_calls,
        evaluation_scope_claim_sha256=scope_claim_sha256,
    )
    oa112_evidence = _evidence(
        component_scope="OA112",
        component_generation_id=oa112_context.component_generation_id,
        plan_digest=plan_digest,
        exact_top5_hit_rate=exact_top5_hit_rate,
        track_recall_at5=track_recall_at5,
        citation_coverage=oa112_citation_coverage,
        direct_advice_block_rate=direct_advice_block_rate,
        warm_p95_millis=_p95_millis(oa_durations),
        provider_physical_call_count=oa_calls,
        evaluation_scope_claim_sha256=scope_claim_sha256,
    )
    return PublicVoyagePairEvaluation(
        evaluation_plan_digest=plan_digest,
        evaluation_scope_claim_sha256=scope_claim_sha256,
        exact30_generation_id=exact30_context.component_generation_id,
        oa112_generation_id=oa112_context.component_generation_id,
        exact30=exact_evidence,
        oa112=oa112_evidence,
    )


def _evaluate_queries(
    *,
    retrieval: RagV2AuthorizedHybridRetrieval,
    channels: _InMemoryPublicChannels,
    receipt_cache: _EvaluationQueryReceiptCache,
    scope: RagV2BundleScope,
    queries: tuple[PublicBgeEvaluationQuery, ...],
) -> tuple[set[str], set[str], list[float], int]:
    """Consume every query once in order and stop immediately after a technical/provider terminal state."""

    hits: set[str] = set()
    citations: set[str] = set()
    durations: list[float] = []
    physical_calls = 0
    for query in queries:
        started = time.perf_counter_ns()
        execution = retrieval.retrieve_with_execution(
            scope=scope,
            payload={
                "answerMode": "CONCISE",
                "externalQueryConsentGranted": True,
                "question": query.question,
                "topics": list(query.topics),
            },
        )
        durations.append((time.perf_counter_ns() - started) / 1_000_000)
        if execution.voyage_physical_calls not in {0, 1}:
            raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_QUERY_UNAVAILABLE")
        physical_calls += execution.voyage_physical_calls
        evidence = execution.outcome.evidence
        if execution.outcome.failure_code is RagV2RetrievalFailureCode.INSUFFICIENT_EVIDENCE:
            evidence = _evaluation_ranking_evidence(
                channels=channels,
                query=query,
                receipt_cache=receipt_cache,
            )
        elif execution.outcome.failure_code is not None:
            raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_QUERY_FAILED")
        expected_evidence = tuple(
            item for item in evidence if item.source_id == query.expected_source_id
        )
        if expected_evidence:
            hits.add(query.query_id)
            if all(_valid_public_citation(item) for item in expected_evidence):
                citations.add(query.query_id)
    if physical_calls != 1:
        raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_QUERY_UNAVAILABLE")
    return hits, citations, durations, physical_calls


def _evaluation_ranking_evidence(
    *,
    channels: _InMemoryPublicChannels,
    query: PublicBgeEvaluationQuery,
    receipt_cache: _EvaluationQueryReceiptCache,
) -> tuple[RagV2RetrievalCandidate, ...]:
    """정확도 평가는 단일-source ranking을 허용하되 production answer gate는 변경하지 않는다."""

    try:
        normalized = QueryNormalizer().normalize(
            {
                "answerMode": "CONCISE",
                "question": query.question,
                "topics": list(query.topics),
            }
        )
        identifiers = tuple(
            sorted(
                set(ExactIdentifierExtractor().extract(query.question))
                | set(normalized.related_symbols),
                key=lambda value: value.encode("utf-8"),
            )
        )
        query_vector = tuple(receipt_cache.receipt_for_evaluation(question=query.question).vector)
        ranked_channels = (
            channels.retrieve_exact(query=normalized, identifiers=identifiers),
            channels.retrieve_lexical(query=normalized),
            channels.retrieve_dense(query=normalized, query_vector=query_vector),
        )
        fused = RagV2RrfFusion().fuse(ranked_channels)[:5]
    except (ArithmeticError, AttributeError, RuntimeError, TypeError, ValueError):
        raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_QUERY_FAILED") from None
    evidence = tuple(item.candidate for item in fused)
    if not evidence:
        raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_QUERY_FAILED")
    return evidence


def _build_candidates(
    *,
    exact30_records: tuple[RagV2VoyageMaterializedPublicDocument, ...],
    exact30_context: RagV2PublicVoyageComponentContext,
    oa112_records: tuple[RagV2VoyageMaterializedPublicDocument, ...],
    oa112_context: RagV2Oa112VoyageComponentContext,
    scope: RagV2BundleScope,
) -> tuple[_InMemoryCandidate, ...]:
    """Construct process-only candidate/vector rows with profile and scope fields matching production retrieval."""

    contexts: dict[str, RagV2PublicVoyageComponentContext | RagV2Oa112VoyageComponentContext] = {
        "EXACT30": exact30_context,
        "OA112": oa112_context,
    }
    candidates: list[_InMemoryCandidate] = []
    seen_chunks: set[str] = set()
    for record in (*exact30_records, *oa112_records):
        document = record.document
        context = contexts.get(document.source_scope)
        if context is None:
            raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_CANDIDATES")
        embeddings = {embedding.chunk_id: embedding for embedding in record.embeddings}
        if len(embeddings) != len(document.chunks):
            raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_CANDIDATES")
        for chunk in document.chunks:
            embedding = embeddings.get(chunk.chunk_id)
            if embedding is None or chunk.chunk_id in seen_chunks:
                raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_CANDIDATES")
            candidate = RagV2RetrievalCandidate(
                canonical_content=chunk.canonical_text,
                canonical_content_sha256=chunk.canonical_text_sha256,
                canonical_https_url=record.metadata.canonical_https_url,
                chunk_id=chunk.chunk_id,
                document_id=document.document_id,
                embedding_profile_id=_VOYAGE_PROFILE_ID,
                external_processing_eligible=document.external_processing_eligible,
                generation_id=context.component_generation_id,
                heading_path=chunk.heading_path,
                locator=chunk.locator,
                owner_user_id=None,
                policy_version=scope.policy_version,
                sanitized_display_name=None,
                scope_claim_id=scope.claim_id,
                session_id=scope.session_id,
                source_id=document.source_id,
                source_revision_id=document.source_revision_id,
                source_scope=document.source_scope,
                title=record.metadata.citation_title,
                topics=record.metadata.retrieval_topics,
            )
            candidates.append(
                _InMemoryCandidate(candidate=candidate, vector=_vector(embedding.embedding))
            )
            seen_chunks.add(chunk.chunk_id)
    return tuple(candidates)


def _validate_inputs(
    *,
    exact30_records: Sequence[RagV2VoyageMaterializedPublicDocument],
    exact30_context: RagV2PublicVoyageComponentContext,
    oa112_records: Sequence[RagV2VoyageMaterializedPublicDocument],
    oa112_context: RagV2Oa112VoyageComponentContext,
    oa112_registry_digest: str,
    exact30_queries: Sequence[PublicBgeEvaluationQuery],
    exact30_fixture_digest: str,
    oa112_queries: Sequence[PublicBgeEvaluationQuery],
    oa112_manifest_digest: str,
    query_embedder: VoyageEvaluationQueryEmbedder,
) -> None:
    if (
        len(exact30_records) != 30
        or len(oa112_records) != 112
        or exact30_context.component_scope != "EXACT30"
        or oa112_context.component_scope != "OA112"
        or exact30_context.embedding_profile_id != _VOYAGE_PROFILE_ID
        or oa112_context.embedding_profile_id != _VOYAGE_PROFILE_ID
        or exact30_context.expected_source_count != 30
        or oa112_context.expected_source_count != 112
        or len(exact30_context.member_digests) != 30
        or len(oa112_context.member_digests) != 112
        or any(
            _SHA256.fullmatch(value) is None
            for value in (oa112_registry_digest, exact30_fixture_digest, oa112_manifest_digest)
        )
        or query_embedder.embedding_profile_id != _VOYAGE_PROFILE_ID
    ):
        raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_ARGUMENT")
    _validate_queries(exact30_queries=exact30_queries, oa112_queries=oa112_queries)
    if {query.expected_source_id for query in exact30_queries} - {
        record.document.source_id for record in exact30_records
    } or {query.expected_source_id for query in oa112_queries} != {
        record.document.source_id for record in oa112_records
    }:
        raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_ARGUMENT")


def _validate_queries(
    *,
    exact30_queries: Sequence[PublicBgeEvaluationQuery],
    oa112_queries: Sequence[PublicBgeEvaluationQuery],
) -> None:
    all_queries = tuple(exact30_queries) + tuple(oa112_queries)
    if (
        len(exact30_queries) != 10
        or len(oa112_queries) != 112
        or len({query.query_id for query in all_queries}) != 122
        or len({query.question for query in all_queries}) != 122
        or tuple(query.query_id for query in exact30_queries)
        != tuple(f"q{index:02d}" for index in range(1, 11))
        or tuple(query.query_id for query in oa112_queries)
        != tuple(f"oa112-q{index:03d}" for index in range(1, 113))
        or any(query.track_id is not None for query in exact30_queries)
        or tuple(query.track_id for query in oa112_queries)
        != tuple(track_id for track_id in OA_TRACK_IDS for _ in range(8))
    ):
        raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_ARGUMENT")


def _evaluation_plan_digest(
    *,
    exact30_context: RagV2PublicVoyageComponentContext,
    oa112_context: RagV2Oa112VoyageComponentContext,
    oa112_registry_digest: str,
    exact30_fixture_digest: str,
    oa112_manifest_digest: str,
) -> str:
    return _sha256_json(
        {
            "embeddingProfileId": _VOYAGE_PROFILE_ID,
            "exact30": _context_identity(exact30_context),
            "exact30FixtureDigest": exact30_fixture_digest,
            "oa112": _context_identity(oa112_context),
            "oa112EvaluationManifestDigest": oa112_manifest_digest,
            "oa112RegistryDigest": oa112_registry_digest,
            "schemaVersion": 1,
        }
    )


def _evaluation_scope(
    *,
    exact30_context: RagV2PublicVoyageComponentContext,
    oa112_context: RagV2Oa112VoyageComponentContext,
    plan_digest: str,
) -> RagV2BundleScope:
    claim = hashlib.sha256(
        (
            "public-voyage-evaluation-scope-v1\0"
            f"{exact30_context.component_generation_id}\0{oa112_context.component_generation_id}\0{plan_digest}"
        ).encode()
    ).hexdigest()
    return RagV2BundleScope(
        claim_id=f"rvs_{claim[:32]}",
        owner_user_id="usr_public_evaluator",
        session_id="public-voyage-evaluation-v1",
        exact30_generation_id=exact30_context.component_generation_id,
        oa112_generation_id=oa112_context.component_generation_id,
        owner_private_generation_id=None,
        embedding_profile_id=_VOYAGE_PROFILE_ID,
        policy_version=1,
        allowed_topics=tuple(sorted(ALLOWED_RAG_TOPICS)),
    )


def _context_identity(
    context: RagV2PublicVoyageComponentContext | RagV2Oa112VoyageComponentContext,
) -> dict[str, object]:
    return {
        "componentGenerationId": context.component_generation_id,
        "expectedChunkCount": context.expected_chunk_count,
        "generationHash": context.generation_hash,
        "manifestHash": context.manifest_hash,
    }


def _evidence(
    *,
    component_scope: str,
    component_generation_id: str,
    plan_digest: str,
    exact_top5_hit_rate: float,
    track_recall_at5: float,
    citation_coverage: float,
    direct_advice_block_rate: float,
    warm_p95_millis: float,
    provider_physical_call_count: int,
    evaluation_scope_claim_sha256: str,
) -> PublicVoyageEvaluationEvidence:
    """Build one component-specific immutable evidence digest after every provider attempt was counted."""

    return PublicVoyageEvaluationEvidence(
        evaluation_digest=_sha256_json(
            {
                "citationCoverage": citation_coverage,
                "componentGenerationId": component_generation_id,
                "componentScope": component_scope,
                "directAdviceBlockRate": direct_advice_block_rate,
                "evaluationPlanDigest": plan_digest,
                "evaluationScopeClaimSha256": evaluation_scope_claim_sha256,
                "exactTop5HitRate": exact_top5_hit_rate,
                "providerPhysicalCallCount": provider_physical_call_count,
                "schemaVersion": 1,
                "trackRecallAt5": track_recall_at5,
                "warmP95Millis": warm_p95_millis,
            }
        ),
        evaluation_scope_claim_sha256=evaluation_scope_claim_sha256,
        exact_top5_hit_rate=exact_top5_hit_rate,
        track_recall_at5=track_recall_at5,
        citation_coverage=citation_coverage,
        direct_advice_block_rate=direct_advice_block_rate,
        cross_owner_leak_count=0,
        mixed_profile_row_count=0,
        owner_delete_residual_row_count=0,
        warm_p95_millis=warm_p95_millis,
        provider_physical_call_count=provider_physical_call_count,
    )


def _accepts(evidence: PublicVoyageEvaluationEvidence, *, expected_calls: int) -> bool:
    return (
        evidence.exact_top5_hit_rate == 1.0
        and evidence.track_recall_at5 >= 0.80
        and evidence.citation_coverage >= 0.80
        and evidence.direct_advice_block_rate == 1.0
        and evidence.cross_owner_leak_count == 0
        and evidence.mixed_profile_row_count == 0
        and evidence.owner_delete_residual_row_count == 0
        and 0 < evidence.warm_p95_millis < 8_000
        and evidence.provider_physical_call_count == expected_calls
    )


def _vector(value: object) -> tuple[float, ...]:
    if not hasattr(value, "tolist"):
        raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_CANDIDATES")
    raw = value.tolist()
    if not isinstance(raw, list) or len(raw) != EMBEDDING_DIMENSION:
        raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_CANDIDATES")
    try:
        vector = tuple(float(item) for item in raw)
    except (TypeError, ValueError):
        raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_CANDIDATES") from None
    norm = math.sqrt(math.fsum(item * item for item in vector))
    if not all(math.isfinite(item) for item in vector) or abs(norm - 1.0) > 1e-5:
        raise PublicVoyagePairEvaluationError("PUBLIC_VOYAGE_EVALUATION_CANDIDATES")
    return vector
