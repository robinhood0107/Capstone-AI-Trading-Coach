"""Public exact-30 + OA112 local-BGE pair evaluator.

This module evaluates only process-local canonical chunks and local BGE query vectors.  It does
not download a source, persist raw text/vector data, activate a public pointer, or call a model
provider.  The writer capability receives only the aggregate evidence returned here.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import time
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.rag.authorized_retrieval import (
    ALLOWED_RAG_TOPICS,
    EMBEDDING_DIMENSION,
    ExactIdentifierExtractor,
    QueryEmbedder,
    QueryNormalizer,
)
from app.rag.benchmark_receipt_io import (
    BenchmarkReceiptIoError,
    write_benchmark_receipt,
)
from app.rag.guardrail import BoundedFixtureGuardrail, GuardrailDecision
from app.rag.oa112_active_registry import Oa112ActiveRegistry
from app.rag.oa_release_manifest import OA_TRACK_IDS
from app.rag.rag_v2_authorized_retrieval import (
    RagV2AuthorizedHybridRetrieval,
    RagV2BundleScope,
    RagV2ChannelResult,
    RagV2RetrievalCandidate,
    RagV2RrfFusion,
)
from app.rag.rag_v2_public_bge_staging import RagV2PublicBgeComponentContext
from app.rag.rag_v2_public_bge_staging_repository import PublicBgeEvaluationEvidence
from app.rag.rag_v2_public_bge_staging_repository import PublicBgeStagingRepositoryError
from app.rag.rag_v2_public_bge_staging_repository import PublicBgeRecord
from app.rag.safe_io import RagSafeIoError, read_approved_regular_file


_REPO_ROOT = Path(__file__).resolve().parents[5]
_DEFAULT_EXACT30_FIXTURE = _REPO_ROOT / "capstone-rag/eval/s4-2b-30-card-smoke.v1.json"
_OA112_EVALUATION_RELATIVE_PATH = "oa112-evaluation-manifest.v1.json"
_RECEIPT_DIRECTORY = "evaluation"
_RECEIPT_FILENAME = "public-bge-pair.v1.json"
_MAX_EXACT_FIXTURE_BYTES = 512 * 1024
_MAX_OA112_MANIFEST_BYTES = 2 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ID = re.compile(r"^src_[a-z0-9][a-z0-9_-]{2,95}$")
_QUERY_ID = re.compile(r"^[a-z][a-z0-9_-]{1,95}$")
_EXACT_FIXTURE_KEYS = frozenset(
    {"schemaVersion", "datasetId", "corpusManifestSha256", "queries"}
)
_EXACT_QUERY_KEYS = frozenset({"id", "text", "expectedSourceIds"})
_OA112_MANIFEST_KEYS = frozenset(
    {
        "contractId",
        "schemaVersion",
        "registryDigest",
        "queryCount",
        "queries",
        "evaluationManifestDigest",
    }
)
_OA112_QUERY_KEYS = frozenset(
    {"id", "question", "expectedSourceId", "trackId", "topics"}
)
_RECEIPT_KEYS = frozenset(
    {
        "acceptancePassed",
        "citationCoverage",
        "contractId",
        "directAdviceBlockRate",
        "embeddingProfileId",
        "evaluationDigest",
        "evaluationPlanDigest",
        "exact30GenerationId",
        "exactTop5HitRate",
        "oa112GenerationId",
        "oa112RegistryDigest",
        "providerPhysicalCallCount",
        "schemaVersion",
        "trackRecallAt5",
        "warmP95Millis",
    }
)
_ADVICE_PROBES = (
    "Should I buy this stock now?",
    "How many shares should I sell today?",
    "저는 지금 이 종목을 몇 주 매수해야 하나요?",
    "내일 이 주식을 팔아야 하나요?",
)


class PublicBgePairEvaluationError(ValueError):
    """Public BGE pair evaluation의 local fixture, receipt, 또는 retrieval contract 실패다."""


@dataclass(frozen=True, slots=True)
class PublicBgeEvaluationQuery:
    """한 synthetic evaluation query의 content-free expected-source metadata다."""

    query_id: str
    question: str
    expected_source_id: str
    topics: tuple[str, ...]
    track_id: str | None


@dataclass(frozen=True, slots=True)
class PublicBgePairEvaluation:
    """One local exact-30/OA112 pair evaluation result.

    The result intentionally contains only component identities and aggregate metrics.  Query
    text, canonical content, raw paths, and vectors remain inside the running evaluator.
    """

    exact30_generation_id: str
    oa112_generation_id: str
    oa112_registry_digest: str
    evaluation_plan_digest: str
    evaluation_digest: str
    exact_top5_hit_rate: float
    track_recall_at5: float
    citation_coverage: float
    direct_advice_block_rate: float
    warm_p95_millis: float

    @property
    def acceptance_passed(self) -> bool:
        """S4.7D local-BGE acceptance threshold를 one place에서 적용한다."""

        return (
            self.exact_top5_hit_rate == 1.0
            and self.track_recall_at5 >= 0.80
            and self.citation_coverage >= 0.80
            and self.direct_advice_block_rate == 1.0
            and 0 < self.warm_p95_millis < 8_000
        )

    def evidence(self) -> PublicBgeEvaluationEvidence:
        """Writer DB에는 source-free aggregate acceptance evidence만 전달한다."""

        return PublicBgeEvaluationEvidence(
            evaluation_digest=self.evaluation_digest,
            exact_top5_hit_rate=self.exact_top5_hit_rate,
            track_recall_at5=self.track_recall_at5,
            citation_coverage=self.citation_coverage,
            direct_advice_block_rate=self.direct_advice_block_rate,
            cross_owner_leak_count=0,
            mixed_profile_row_count=0,
            owner_delete_residual_row_count=0,
            warm_p95_millis=self.warm_p95_millis,
            provider_physical_call_count=0,
        )

    def content_free_receipt(self) -> dict[str, object]:
        """Resume에 필요한 metric/identity만 local 0600 receipt로 투영한다."""

        return {
            "acceptancePassed": self.acceptance_passed,
            "citationCoverage": self.citation_coverage,
            "contractId": "rag-v2-public-bge-pair-evaluation-receipt-v1",
            "directAdviceBlockRate": self.direct_advice_block_rate,
            "embeddingProfileId": "bge_m3_local_1024_v1",
            "evaluationDigest": self.evaluation_digest,
            "evaluationPlanDigest": self.evaluation_plan_digest,
            "exact30GenerationId": self.exact30_generation_id,
            "exactTop5HitRate": self.exact_top5_hit_rate,
            "oa112GenerationId": self.oa112_generation_id,
            "oa112RegistryDigest": self.oa112_registry_digest,
            "providerPhysicalCallCount": 0,
            "schemaVersion": 1,
            "trackRecallAt5": self.track_recall_at5,
            "warmP95Millis": self.warm_p95_millis,
        }


@dataclass(frozen=True, slots=True)
class _InMemoryCandidate:
    """Evaluator process 안에서만 chunk/vector를 함께 보관하는 retrieval row다."""

    candidate: RagV2RetrievalCandidate
    vector: tuple[float, ...]


class _InMemoryPublicChannels:
    """Production RRF policy를 그대로 exercise하는 local immutable-pair channel adapter."""

    def __init__(self, candidates: tuple[_InMemoryCandidate, ...]) -> None:
        if not candidates:
            raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_CANDIDATES")
        self._candidates = candidates

    def retrieve_exact(
        self,
        *,
        query: object,
        identifiers: tuple[str, ...],
        **_: object,
    ) -> RagV2ChannelResult:
        """Mirror V29's source/title/content match-kind ordering after the same topic scope guard."""

        topics = getattr(query, "topics", None)
        if not isinstance(topics, tuple):
            raise RuntimeError("PUBLIC_BGE_EVALUATION_QUERY")
        # V29's `matched` CTE groups all identifier matches and retains the lowest match kind.
        # Keep that shape rather than reducing the evaluator to source-ID-only matching: synthetic
        # queries deliberately cannot name their expected source IDs, but valid title/content
        # identifiers must still exercise the production exact channel.
        ranked: list[tuple[int, bytes, bytes, RagV2RetrievalCandidate]] = []
        for item in self._candidates:
            candidate = item.candidate
            if not _candidate_matches_topics(candidate, topics):
                continue
            match_kinds = tuple(
                _exact_match_kind(candidate=candidate, identifier=identifier)
                for identifier in identifiers
            )
            present_kinds = tuple(kind for kind in match_kinds if kind is not None)
            if not present_kinds:
                continue
            ranked.append(
                (
                    min(present_kinds),
                    candidate.source_id.encode("utf-8"),
                    candidate.chunk_id.encode("utf-8"),
                    candidate,
                )
            )
        ranked.sort(key=lambda item: (item[0], item[1], item[2]))
        return RagV2ChannelResult(
            channel="exact",
            items=tuple(item[3] for item in ranked[:30]),
            complete=True,
        )

    def retrieve_lexical(self, *, query: object, **_: object) -> RagV2ChannelResult:
        """Deterministic local lexical ranking mirrors bounded pg_trgm channel semantics for evaluation."""

        lexical_query = getattr(query, "lexical_query", None)
        topics = getattr(query, "topics", None)
        if not isinstance(lexical_query, str) or not isinstance(topics, tuple):
            raise RuntimeError("PUBLIC_BGE_EVALUATION_QUERY")
        ranked = sorted(
            (
                (
                    _pg_trgm_similarity(
                        " ".join(
                            (
                                item.candidate.source_id,
                                item.candidate.title or "",
                                item.candidate.sanitized_display_name or "",
                                item.candidate.canonical_content,
                            )
                        ),
                        lexical_query,
                    ),
                    item.candidate.source_id.encode("utf-8"),
                    item.candidate.chunk_id.encode("utf-8"),
                    item.candidate,
                )
                for item in self._candidates
                if _candidate_matches_topics(item.candidate, topics)
                and _pg_trgm_similarity(
                    " ".join(
                        (
                            item.candidate.source_id,
                            item.candidate.title or "",
                            item.candidate.sanitized_display_name or "",
                            item.candidate.canonical_content,
                        )
                    ),
                    lexical_query,
                )
                >= 0.05
            ),
            key=lambda item: (-item[0], item[1], item[2]),
        )
        return RagV2ChannelResult(
            channel="lexical",
            items=tuple(item[3] for item in ranked[:30]),
            complete=True,
        )

    def retrieve_dense(
        self,
        *,
        query: object,
        query_vector: tuple[float, ...],
        **_: object,
    ) -> RagV2ChannelResult:
        """Actual local BGE query vector is compared with transient local materialization vectors only."""

        topics = getattr(query, "topics", None)
        if not isinstance(topics, tuple) or len(query_vector) != EMBEDDING_DIMENSION:
            raise RuntimeError("PUBLIC_BGE_EVALUATION_QUERY")
        ranked = sorted(
            (
                (
                    -math.fsum(left * right for left, right in zip(query_vector, item.vector, strict=True)),
                    item.candidate.source_id.encode("utf-8"),
                    item.candidate.chunk_id.encode("utf-8"),
                    item.candidate,
                )
                for item in self._candidates
                if _candidate_matches_topics(item.candidate, topics)
                and 1.0
                - math.fsum(left * right for left, right in zip(query_vector, item.vector, strict=True))
                <= 0.55
            ),
            key=lambda item: (item[0], item[1], item[2]),
        )
        return RagV2ChannelResult(
            channel="dense",
            items=tuple(item[3] for item in ranked[:30]),
            complete=True,
        )


def load_exact30_evaluation_queries(
    *,
    source_card_corpus_manifest_sha256: str,
    fixture_path: Path = _DEFAULT_EXACT30_FIXTURE,
) -> tuple[tuple[PublicBgeEvaluationQuery, ...], str]:
    """Freeze the tracked exact-30 smoke fixture to the materializer's actual card corpus digest."""

    if _SHA256.fullmatch(source_card_corpus_manifest_sha256) is None:
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_EXACT_FIXTURE")
    payload, fixture_digest = _read_tracked_json(
        fixture_path,
        maximum_bytes=_MAX_EXACT_FIXTURE_BYTES,
    )
    raw_queries = payload.get("queries")
    if (
        set(payload) != _EXACT_FIXTURE_KEYS
        or payload.get("schemaVersion") != "s4-2b-30-card-smoke/v1"
        or payload.get("datasetId") != "s4-2b-30-card-smoke/v1"
        or payload.get("corpusManifestSha256") != source_card_corpus_manifest_sha256
        or not isinstance(raw_queries, list)
        or len(raw_queries) != 10
    ):
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_EXACT_FIXTURE")
    queries: list[PublicBgeEvaluationQuery] = []
    for index, item in enumerate(raw_queries, start=1):
        if (
            not isinstance(item, Mapping)
            or set(item) != _EXACT_QUERY_KEYS
            or item.get("id") != f"q{index:02d}"
            or not isinstance(item.get("expectedSourceIds"), list)
            or len(item["expectedSourceIds"]) != 1
        ):
            raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_EXACT_FIXTURE")
        query = _evaluation_query(
            query_id=item["id"],
            question=item.get("text"),
            expected_source_id=item["expectedSourceIds"][0],
            topics=(),
            track_id=None,
        )
        queries.append(query)
    return tuple(queries), fixture_digest


def load_oa112_evaluation_queries(
    *,
    approved_root: Path,
    registry: Oa112ActiveRegistry,
) -> tuple[tuple[PublicBgeEvaluationQuery, ...], str]:
    """Load the owner-only OA112 evaluator manifest and bind every query to the active registry."""

    try:
        source = read_approved_regular_file(
            approved_root=approved_root,
            relative_path=_OA112_EVALUATION_RELATIVE_PATH,
            max_bytes=_MAX_OA112_MANIFEST_BYTES,
        )
        payload = _parse_object_json(source.content)
    except (RagSafeIoError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_OA_MANIFEST") from error
    raw_queries = payload.get("queries")
    if (
        set(payload) != _OA112_MANIFEST_KEYS
        or payload.get("contractId") != "rag-v2-oa112-evaluation-manifest-v1"
        or payload.get("schemaVersion") != 1
        or payload.get("registryDigest") != registry.registry_digest
        or payload.get("evaluationManifestDigest") != _oa112_manifest_digest(payload)
        or payload.get("queryCount") != 112
        or not isinstance(raw_queries, list)
        or len(raw_queries) != 112
    ):
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_OA_MANIFEST")
    active_by_source = {entry.source_id: entry for entry in registry.active_entries}
    queries: list[PublicBgeEvaluationQuery] = []
    for index, item in enumerate(raw_queries, start=1):
        if (
            not isinstance(item, Mapping)
            or set(item) != _OA112_QUERY_KEYS
            or item.get("id") != f"oa112-q{index:03d}"
            or not isinstance(item.get("expectedSourceId"), str)
            or not isinstance(item.get("trackId"), str)
            or not isinstance(item.get("topics"), list)
        ):
            raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_OA_MANIFEST")
        source_id = item["expectedSourceId"]
        entry = active_by_source.get(source_id)
        topics = _topics(item["topics"])
        if (
            entry is None
            or entry.track_id != item["trackId"]
            or not set(topics).issubset(entry.retrieval_topics)
        ):
            raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_OA_BINDING")
        queries.append(
            _evaluation_query(
                query_id=item["id"],
                question=item.get("question"),
                expected_source_id=source_id,
                topics=topics,
                track_id=entry.track_id,
            )
        )
    if (
        {query.expected_source_id for query in queries} != set(active_by_source)
        or tuple(query.track_id for query in queries)
        != tuple(track_id for track_id in OA_TRACK_IDS for _ in range(8))
    ):
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_OA_BINDING")
    digest = payload["evaluationManifestDigest"]
    assert isinstance(digest, str)
    return tuple(queries), digest


def evaluate_public_bge_pair(
    *,
    exact30_records: Sequence[PublicBgeRecord],
    exact30_context: RagV2PublicBgeComponentContext,
    oa112_records: Sequence[PublicBgeRecord],
    oa112_context: RagV2PublicBgeComponentContext,
    oa112_registry_digest: str,
    exact30_queries: Sequence[PublicBgeEvaluationQuery],
    exact30_fixture_digest: str,
    oa112_queries: Sequence[PublicBgeEvaluationQuery],
    oa112_manifest_digest: str,
    query_embedder: QueryEmbedder,
) -> PublicBgePairEvaluation:
    """Run actual local-BGE top-5 retrieval across the full immutable public pair.

    The evaluator deliberately uses the same `RagV2AuthorizedHybridRetrieval` and RRF k=60
    policy as the loopback runtime.  Its only adapter replacement is the transient, in-memory
    public pair in place of PostgreSQL while a pair is still awaiting writer evaluation.
    """

    _validate_pair_inputs(
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
    scope = RagV2BundleScope(
        claim_id="rvs_" + "a" * 32,
        owner_user_id="usr_public_evaluator",
        session_id="public-bge-evaluation-session-v1",
        exact30_generation_id=exact30_context.component_generation_id,
        oa112_generation_id=oa112_context.component_generation_id,
        owner_private_generation_id=None,
        embedding_profile_id="bge_m3_local_1024_v1",
        policy_version=1,
        allowed_topics=tuple(sorted(ALLOWED_RAG_TOPICS)),
    )
    channels = _InMemoryPublicChannels(
        _build_candidates(
            records=tuple(exact30_records) + tuple(oa112_records),
            contexts={
                "EXACT30": exact30_context,
                "OA112": oa112_context,
            },
            scope=scope,
        )
    )
    retrieval = RagV2AuthorizedHybridRetrieval(
        query_normalizer=QueryNormalizer(),
        exact_identifier_extractor=ExactIdentifierExtractor(),
        query_embedder=query_embedder,
        exact_retriever=channels,
        lexical_retriever=channels,
        dense_retriever=channels,
        rrf_fusion=RagV2RrfFusion(),
    )
    durations: list[float] = []
    exact_hits, exact_citations = _evaluate_queries(
        retrieval=retrieval,
        scope=scope,
        queries=tuple(exact30_queries),
        durations=durations,
    )
    oa_hits, oa_citations = _evaluate_queries(
        retrieval=retrieval,
        scope=scope,
        queries=tuple(oa112_queries),
        durations=durations,
    )
    track_recall = _minimum_track_recall(queries=tuple(oa112_queries), hits=oa_hits)
    exact_rate = _ratio(len(exact_hits), len(exact30_queries))
    citation_coverage = _ratio(len(exact_citations) + len(oa_citations), len(exact30_queries) + len(oa112_queries))
    direct_advice_block_rate = _direct_advice_block_rate()
    warm_p95_millis = _p95_millis(durations)
    evaluation_digest = _sha256_json(
        {
            "citationCoverage": citation_coverage,
            "directAdviceBlockRate": direct_advice_block_rate,
            "evaluationPlanDigest": plan_digest,
            "exactTop5HitRate": exact_rate,
            "providerPhysicalCallCount": 0,
            "schemaVersion": 1,
            "trackRecallAt5": track_recall,
            "warmP95Millis": warm_p95_millis,
        }
    )
    return PublicBgePairEvaluation(
        exact30_generation_id=exact30_context.component_generation_id,
        oa112_generation_id=oa112_context.component_generation_id,
        oa112_registry_digest=oa112_registry_digest,
        evaluation_plan_digest=plan_digest,
        evaluation_digest=evaluation_digest,
        exact_top5_hit_rate=exact_rate,
        track_recall_at5=track_recall,
        citation_coverage=citation_coverage,
        direct_advice_block_rate=direct_advice_block_rate,
        warm_p95_millis=warm_p95_millis,
    )


def write_public_bge_pair_evaluation_receipt(
    *,
    approved_root: Path,
    evaluation: PublicBgePairEvaluation,
) -> None:
    """Persist the accepted/rejected aggregate pair result before independent writer transitions.

    The same receipt lets a retry submit byte-identical evidence if exact evaluation has already
    changed state but OA112's subsequent writer transition was interrupted.
    """

    payload = json.dumps(
        evaluation.content_free_receipt(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    try:
        write_benchmark_receipt(
            approved_root=approved_root,
            relative_directory=_RECEIPT_DIRECTORY,
            filename=_RECEIPT_FILENAME,
            payload=payload,
        )
    except BenchmarkReceiptIoError as error:
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_RECEIPT") from error


def load_public_bge_pair_evaluation_evidence(
    *,
    approved_root: Path,
    evaluation_plan_digest: str,
) -> PublicBgeEvaluationEvidence | None:
    """Return only a byte-bound matching local receipt; an absent leaf is a fresh evaluation."""

    if _SHA256.fullmatch(evaluation_plan_digest) is None:
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_RECEIPT")
    target = approved_root / _RECEIPT_DIRECTORY / _RECEIPT_FILENAME
    if not target.exists():
        return None
    try:
        read = read_approved_regular_file(
            approved_root=approved_root,
            relative_path=f"{_RECEIPT_DIRECTORY}/{_RECEIPT_FILENAME}",
            max_bytes=64 * 1024,
        )
        payload = _parse_object_json(read.content)
    except (RagSafeIoError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_RECEIPT") from error
    if set(payload) != _RECEIPT_KEYS or payload.get("evaluationPlanDigest") != evaluation_plan_digest:
        return None
    if (
        payload.get("contractId") != "rag-v2-public-bge-pair-evaluation-receipt-v1"
        or payload.get("schemaVersion") != 1
        or payload.get("embeddingProfileId") != "bge_m3_local_1024_v1"
        or payload.get("providerPhysicalCallCount") != 0
        or payload.get("acceptancePassed") is not True
    ):
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_RECEIPT")
    try:
        evidence = PublicBgeEvaluationEvidence(
            evaluation_digest=_strict_hash(payload.get("evaluationDigest")),
            exact_top5_hit_rate=_strict_ratio(payload.get("exactTop5HitRate")),
            track_recall_at5=_strict_ratio(payload.get("trackRecallAt5")),
            citation_coverage=_strict_ratio(payload.get("citationCoverage")),
            direct_advice_block_rate=_strict_ratio(payload.get("directAdviceBlockRate")),
            cross_owner_leak_count=0,
            mixed_profile_row_count=0,
            owner_delete_residual_row_count=0,
            warm_p95_millis=_strict_positive_float(payload.get("warmP95Millis")),
            provider_physical_call_count=0,
        )
    except (PublicBgeStagingRepositoryError, ValueError) as error:
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_RECEIPT") from error
    if (
        evidence.exact_top5_hit_rate != 1.0
        or evidence.track_recall_at5 < 0.80
        or evidence.citation_coverage < 0.80
        or evidence.direct_advice_block_rate != 1.0
        or evidence.warm_p95_millis >= 8_000
    ):
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_RECEIPT")
    return evidence


def evaluation_plan_digest(
    *,
    exact30_context: RagV2PublicBgeComponentContext,
    oa112_context: RagV2PublicBgeComponentContext,
    oa112_registry_digest: str,
    exact30_fixture_digest: str,
    oa112_manifest_digest: str,
) -> str:
    """Expose the content-free deterministic evaluation-plan binding used for safe receipt reuse."""

    _validate_component_context(exact30_context, scope="EXACT30", source_count=30)
    _validate_component_context(oa112_context, scope="OA112", source_count=112)
    if any(
        _SHA256.fullmatch(value) is None
        for value in (oa112_registry_digest, exact30_fixture_digest, oa112_manifest_digest)
    ):
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_ARGUMENT")
    return _evaluation_plan_digest(
        exact30_context=exact30_context,
        oa112_context=oa112_context,
        oa112_registry_digest=oa112_registry_digest,
        exact30_fixture_digest=exact30_fixture_digest,
        oa112_manifest_digest=oa112_manifest_digest,
    )


def _evaluate_queries(
    *,
    retrieval: RagV2AuthorizedHybridRetrieval,
    scope: RagV2BundleScope,
    queries: tuple[PublicBgeEvaluationQuery, ...],
    durations: list[float],
) -> tuple[set[str], set[str]]:
    hits: set[str] = set()
    citations: set[str] = set()
    for query in queries:
        started = time.perf_counter_ns()
        outcome = retrieval.retrieve(
            scope=scope,
            payload={
                "question": query.question,
                "answerMode": "CONCISE",
                "topics": list(query.topics),
            },
        )
        durations.append((time.perf_counter_ns() - started) / 1_000_000)
        evidence = tuple(item for item in outcome.evidence if item.source_id == query.expected_source_id)
        if outcome.retrieval_permitted and evidence:
            hits.add(query.query_id)
            if all(_valid_public_citation(item) for item in evidence):
                citations.add(query.query_id)
    return hits, citations


def _build_candidates(
    *,
    records: tuple[PublicBgeRecord, ...],
    contexts: Mapping[str, RagV2PublicBgeComponentContext],
    scope: RagV2BundleScope,
) -> tuple[_InMemoryCandidate, ...]:
    candidates: list[_InMemoryCandidate] = []
    seen_chunks: set[str] = set()
    for materialized, metadata in records:
        document = materialized.document
        context = contexts.get(document.source_scope)
        if context is None:
            raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_CANDIDATES")
        embeddings = {embedding.chunk_id: embedding for embedding in materialized.embeddings}
        if len(embeddings) != len(document.chunks):
            raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_CANDIDATES")
        for chunk in document.chunks:
            embedding = embeddings.get(chunk.chunk_id)
            if embedding is None or chunk.chunk_id in seen_chunks:
                raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_CANDIDATES")
            vector = _vector(embedding.embedding)
            candidate = RagV2RetrievalCandidate(
                canonical_content=chunk.canonical_text,
                canonical_content_sha256=chunk.canonical_text_sha256,
                canonical_https_url=metadata.canonical_https_url,
                chunk_id=chunk.chunk_id,
                document_id=document.document_id,
                embedding_profile_id="bge_m3_local_1024_v1",
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
                title=metadata.citation_title,
                topics=metadata.retrieval_topics,
            )
            candidates.append(_InMemoryCandidate(candidate=candidate, vector=vector))
            seen_chunks.add(chunk.chunk_id)
    return tuple(candidates)


def _validate_pair_inputs(
    *,
    exact30_records: Sequence[PublicBgeRecord],
    exact30_context: RagV2PublicBgeComponentContext,
    oa112_records: Sequence[PublicBgeRecord],
    oa112_context: RagV2PublicBgeComponentContext,
    oa112_registry_digest: str,
    exact30_queries: Sequence[PublicBgeEvaluationQuery],
    exact30_fixture_digest: str,
    oa112_queries: Sequence[PublicBgeEvaluationQuery],
    oa112_manifest_digest: str,
    query_embedder: QueryEmbedder,
) -> None:
    _validate_component_context(exact30_context, scope="EXACT30", source_count=30)
    _validate_component_context(oa112_context, scope="OA112", source_count=112)
    if (
        len(exact30_records) != 30
        or len(oa112_records) != 112
        or len(exact30_queries) != 10
        or len(oa112_queries) != 112
        or _SHA256.fullmatch(oa112_registry_digest) is None
        or _SHA256.fullmatch(exact30_fixture_digest) is None
        or _SHA256.fullmatch(oa112_manifest_digest) is None
        or getattr(query_embedder, "embedding_profile_id", None) != "bge_m3_local_1024_v1"
    ):
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_ARGUMENT")
    if (
        len({item.query_id for item in tuple(exact30_queries) + tuple(oa112_queries)}) != 122
        or len({item.expected_source_id for item in exact30_queries}) != 10
        or len({item.expected_source_id for item in oa112_queries}) != 112
        or any(item.track_id is not None for item in exact30_queries)
        or tuple(item.track_id for item in oa112_queries)
        != tuple(track_id for track_id in OA_TRACK_IDS for _ in range(8))
    ):
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_ARGUMENT")
    exact_source_ids = {record[0].document.source_id for record in exact30_records}
    oa_source_ids = {record[0].document.source_id for record in oa112_records}
    if (
        not {item.expected_source_id for item in exact30_queries}.issubset(exact_source_ids)
        or {item.expected_source_id for item in oa112_queries} != oa_source_ids
    ):
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_ARGUMENT")


def _validate_component_context(
    context: RagV2PublicBgeComponentContext,
    *,
    scope: Literal["EXACT30", "OA112"],
    source_count: int,
) -> None:
    if (
        context.component_scope != scope
        or context.expected_source_count != source_count
        or context.expected_chunk_count < source_count
        or context.embedding_profile_id != "bge_m3_local_1024_v1"
        or _SHA256.fullmatch(context.generation_hash) is None
        or _SHA256.fullmatch(context.manifest_hash) is None
        or len(context.member_digests) != source_count
    ):
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_ARGUMENT")


def _evaluation_plan_digest(
    *,
    exact30_context: RagV2PublicBgeComponentContext,
    oa112_context: RagV2PublicBgeComponentContext,
    oa112_registry_digest: str,
    exact30_fixture_digest: str,
    oa112_manifest_digest: str,
) -> str:
    return _sha256_json(
        {
            "embeddingProfileId": "bge_m3_local_1024_v1",
            "exact30": _context_identity(exact30_context),
            "exact30FixtureDigest": exact30_fixture_digest,
            "oa112": _context_identity(oa112_context),
            "oa112EvaluationManifestDigest": oa112_manifest_digest,
            "oa112RegistryDigest": oa112_registry_digest,
            "schemaVersion": 1,
        }
    )


def _context_identity(context: RagV2PublicBgeComponentContext) -> dict[str, object]:
    return {
        "componentGenerationId": context.component_generation_id,
        "expectedChunkCount": context.expected_chunk_count,
        "generationHash": context.generation_hash,
        "manifestHash": context.manifest_hash,
    }


def _minimum_track_recall(
    *,
    queries: tuple[PublicBgeEvaluationQuery, ...],
    hits: set[str],
) -> float:
    values: list[float] = []
    for track_id in OA_TRACK_IDS:
        track_queries = tuple(item for item in queries if item.track_id == track_id)
        if len(track_queries) != 8:
            raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_ARGUMENT")
        values.append(_ratio(sum(item.query_id in hits for item in track_queries), 8))
    return min(values)


def _direct_advice_block_rate() -> float:
    guardrail = BoundedFixtureGuardrail()
    blocked = sum(
        guardrail.classify(question).decision is GuardrailDecision.BLOCKED_ADVICE
        for question in _ADVICE_PROBES
    )
    return _ratio(blocked, len(_ADVICE_PROBES))


def _valid_public_citation(candidate: RagV2RetrievalCandidate) -> bool:
    return (
        candidate.source_scope in {"EXACT30", "OA112"}
        and candidate.owner_user_id is None
        and candidate.sanitized_display_name is None
        and isinstance(candidate.title, str)
        and bool(candidate.title)
        and isinstance(candidate.canonical_https_url, str)
        and candidate.canonical_https_url.startswith("https://")
    )


def _candidate_matches_topics(candidate: RagV2RetrievalCandidate, topics: tuple[str, ...]) -> bool:
    return not topics or bool(set(candidate.topics).intersection(topics))


def _exact_match_kind(
    *,
    candidate: RagV2RetrievalCandidate,
    identifier: str,
) -> int | None:
    """Return V29's ordered exact match kind without exposing evaluator content outside process."""

    if candidate.source_id == identifier:
        return 0
    display_name = candidate.title or candidate.sanitized_display_name or ""
    if display_name.lower() == identifier.lower():
        return 1
    if identifier.lower() in candidate.canonical_content.lower():
        return 2
    return None


def _pg_trgm_similarity(left: str, right: str) -> float:
    """Match PostgreSQL `pg_trgm similarity(lower(left), lower(right))` for local pre-activation RRF.

    V29 builds trigrams from PostgreSQL `lower(concat_ws(...))`, pads each alphanumeric word with
    two leading and one trailing ASCII spaces, deduplicates the trigrams, and uses Jaccard overlap.
    Keeping this implementation parity-tested avoids a convenient local lexical approximation from
    over-reporting an activation candidate before the DB pointer exists.
    """

    left_trigrams = _pg_trigrams(left.lower())
    right_trigrams = _pg_trigrams(right.lower())
    if not left_trigrams and not right_trigrams:
        return 0.0
    shared = len(left_trigrams.intersection(right_trigrams))
    # PostgreSQL's similarity() returns `real`, so apply its float4 boundary before the stable
    # source/chunk tie-break order is evaluated locally.
    score = float(shared) / float(len(left_trigrams) + len(right_trigrams) - shared)
    return float(struct.unpack("!f", struct.pack("!f", score))[0])


def _pg_trigrams(value: str) -> frozenset[str]:
    """Generate pg_trgm's word-padded UTF-8 character trigrams without any DB/network call."""

    words = tuple(re.findall(r"[^\W_]+", value, flags=re.UNICODE))
    trigrams: set[str] = set()
    for word in words:
        padded = f"  {word} "
        trigrams.update(padded[index : index + 3] for index in range(len(padded) - 2))
    return frozenset(trigrams)


def _vector(value: object) -> tuple[float, ...]:
    if not hasattr(value, "tolist"):
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_CANDIDATES")
    raw = value.tolist()
    if not isinstance(raw, list) or len(raw) != EMBEDDING_DIMENSION:
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_CANDIDATES")
    try:
        vector = tuple(float(item) for item in raw)
    except (TypeError, ValueError) as error:
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_CANDIDATES") from error
    norm = math.sqrt(math.fsum(item * item for item in vector))
    if not all(math.isfinite(item) for item in vector) or abs(norm - 1.0) > 1e-5:
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_CANDIDATES")
    return vector


def _evaluation_query(
    *,
    query_id: object,
    question: object,
    expected_source_id: object,
    topics: tuple[str, ...],
    track_id: str | None,
) -> PublicBgeEvaluationQuery:
    if (
        not isinstance(query_id, str)
        or _QUERY_ID.fullmatch(query_id) is None
        or not isinstance(question, str)
        or not 1 <= len(question) <= 1_000
        or len(question.encode("utf-8")) > 8_192
        or question != question.strip()
        or unicodedata.normalize("NFC", question) != question
        or not isinstance(expected_source_id, str)
        or _SOURCE_ID.fullmatch(expected_source_id) is None
        or expected_source_id.casefold() in question.casefold()
        or (track_id is not None and track_id not in OA_TRACK_IDS)
    ):
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_QUERY")
    return PublicBgeEvaluationQuery(
        query_id=query_id,
        question=question,
        expected_source_id=expected_source_id,
        topics=topics,
        track_id=track_id,
    )


def _topics(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= len(ALLOWED_RAG_TOPICS)
        or any(not isinstance(item, str) or item not in ALLOWED_RAG_TOPICS for item in value)
        or len(value) != len(set(value))
        or tuple(value) != tuple(sorted(value))
    ):
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_OA_MANIFEST")
    return tuple(value)


def _p95_millis(durations: Sequence[float]) -> float:
    if not durations or any(not math.isfinite(value) or value < 0 for value in durations):
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_RETRIEVAL")
    ordered = sorted(durations)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return max(0.001, round(float(ordered[index]), 6))


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0 or numerator < 0 or numerator > denominator:
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_ARGUMENT")
    return float(numerator) / float(denominator)


def _read_tracked_json(path: Path, *, maximum_bytes: int) -> tuple[dict[str, object], str]:
    try:
        metadata = path.lstat()
        if not path.is_file() or path.is_symlink() or metadata.st_size <= 0 or metadata.st_size > maximum_bytes:
            raise OSError("unsafe exact fixture")
        raw = path.read_bytes()
        if len(raw) != metadata.st_size:
            raise OSError("exact fixture drift")
        return _parse_object_json(raw), hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise PublicBgePairEvaluationError("PUBLIC_BGE_EVALUATION_EXACT_FIXTURE") from error


def _parse_object_json(raw: bytes) -> dict[str, object]:
    text = raw.decode("utf-8", errors="strict")
    if not text.endswith("\n") or "\r" in text or text.startswith("\ufeff"):
        raise ValueError("json text boundary")
    value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    if not isinstance(value, dict):
        raise ValueError("json object required")
    return value


def _oa112_manifest_digest(payload: Mapping[str, object]) -> str:
    detached = json.loads(json.dumps(payload, ensure_ascii=False))
    if not isinstance(detached, dict):
        raise ValueError("manifest object required")
    detached["evaluationManifestDigest"] = None
    return _sha256_json(detached)


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _reject_duplicate_keys(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _strict_hash(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError("hash")
    return value


def _strict_ratio(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("ratio")
    result: float = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise ValueError("ratio")
    return result


def _strict_positive_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("positive float")
    result: float = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("positive float")
    return result
