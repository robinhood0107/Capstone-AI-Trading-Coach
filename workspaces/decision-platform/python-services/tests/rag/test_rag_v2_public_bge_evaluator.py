from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import replace
from pathlib import Path
from typing import Sequence

import numpy as np
import psycopg
import pytest

from app.rag.document_ir_materializer import (
    RagV2CanonicalDocumentChunk,
    RagV2DocumentMaterialization,
)
from app.rag.oa112_active_registry import Oa112ActiveRegistry, Oa112RegistryEntry
from app.rag.oa_release_manifest import OA_TRACK_IDS
from app.rag.rag_v2_bge_materializer import (
    RagV2BgeDocumentEmbedding,
    RagV2BgeMaterializedPublicDocument,
)
from app.rag.rag_v2_public_bge_evaluator import (
    PublicBgeEvaluationQuery,
    PublicBgePairEvaluationError,
    evaluate_public_bge_pair,
    evaluation_plan_digest,
    load_exact30_evaluation_queries,
    load_oa112_evaluation_queries,
    load_public_bge_pair_evaluation_evidence,
    _pg_trgm_similarity,
    write_public_bge_pair_evaluation_receipt,
)
from app.rag.rag_v2_public_bge_staging import (
    PublicBgeSourceMetadata,
    build_public_bge_component_context,
)
from app.rag.rag_v2_public_bge_staging_repository import PublicBgeRecord
from app.rag.rag_v2_authorized_retrieval import (
    RagV2QueryEmbeddingError,
    RagV2QueryEmbeddingReceipt,
    RagV2RetrievalFailureCode,
)
from app.rag.rag_v2_external_exact30_voyage_runner import (
    PublicVoyageSourceMetadata,
    RagV2VoyageDocumentEmbedding,
    RagV2VoyageMaterializedPublicDocument,
    build_external_exact30_public_voyage_component_context,
)
from app.rag.rag_v2_oa112_voyage_runner import build_oa112_public_voyage_component_context
from app.rag import rag_v2_public_voyage_evaluator as voyage_evaluator
from app.rag.rag_v2_public_voyage_evaluator import (
    PublicVoyagePairEvaluationError,
    evaluate_public_voyage_pair,
    evaluation_query_id_by_sha256,
)
from app.rag.rag_v2_public_voyage_evaluation_manifest import (
    prepare_public_voyage_evaluation_manifests,
)
from app.rag.source_card_corpus import load_frozen_source_card_corpus


class _FixtureQueryEmbedder:
    @property
    def embedding_profile_id(self) -> str:
        return "bge_m3_local_1024_v1"

    def embed_query(self, question: str) -> Sequence[float]:
        match = re.search(r"(exact|oa) evidence (\d+)", question)
        if match is None:
            raise ValueError("fixture question")
        index = int(match.group(2))
        coordinate = index if match.group(1) == "exact" else 100 + index
        vector = [0.0] * 1024
        vector[coordinate] = 1.0
        return vector


class _FixtureVoyageQueryEmbedder:
    """Component 첫 질문만 Voyage batch physical call을 나타내는 evaluator fixture다."""

    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.calls: list[str] = []
        self._fail_on_call = fail_on_call
        self._loaded_components: set[str] = set()

    @property
    def embedding_profile_id(self) -> str:
        return "voyage_context_4_1024_v1"

    def embed_query_with_receipt(
        self,
        *,
        question: str,
        scope_claim_id: str,
        external_query_consent_granted: bool,
    ) -> RagV2QueryEmbeddingReceipt:
        assert scope_claim_id.startswith("rvs_")
        assert external_query_consent_granted is True
        self.calls.append(question)
        if self._fail_on_call == len(self.calls):
            raise RagV2QueryEmbeddingError(
                RagV2RetrievalFailureCode.QUERY_EMBEDDING_INVALID,
                voyage_physical_calls=1,
            )
        match = re.search(r"(exact|oa) evidence (\d+)", question)
        if match is None:
            raise ValueError("fixture question")
        index = int(match.group(2))
        coordinate = index if match.group(1) == "exact" else 100 + index
        component = match.group(1)
        physical_calls = 0 if component in self._loaded_components else 1
        self._loaded_components.add(component)
        vector = [0.0] * 1024
        vector[coordinate] = 1.0
        return RagV2QueryEmbeddingReceipt(vector=tuple(vector), voyage_physical_calls=physical_calls)

    def embed_query(self, question: str) -> Sequence[float]:
        del question
        raise AssertionError("the Voyage evaluator must use receipt-bearing query embedding")


def test_public_pair_evaluator_runs_full_local_rrf_and_persists_reusable_content_free_receipt(
    tmp_path: Path,
) -> None:
    exact_records = tuple(_record("EXACT30", index) for index in range(30))
    oa_records = tuple(_record("OA112", index) for index in range(112))
    exact_context = build_public_bge_component_context(exact_records)
    oa_context = build_public_bge_component_context(oa_records)
    registry = _registry()

    evaluation = evaluate_public_bge_pair(
        exact30_records=exact_records,
        exact30_context=exact_context,
        oa112_records=oa_records,
        oa112_context=oa_context,
        oa112_registry_digest=registry.registry_digest,
        exact30_queries=_exact_queries(),
        exact30_fixture_digest="b" * 64,
        oa112_queries=_oa_queries(registry),
        oa112_manifest_digest="c" * 64,
        query_embedder=_FixtureQueryEmbedder(),
    )

    assert evaluation.acceptance_passed is True
    assert evaluation.exact_top5_hit_rate == 1.0
    assert evaluation.track_recall_at5 == 1.0
    assert evaluation.citation_coverage == 1.0
    assert evaluation.direct_advice_block_rate == 1.0
    assert 0 < evaluation.warm_p95_millis < 8_000
    assert evaluation.evidence().provider_physical_call_count == 0

    local_root = tmp_path / "private"
    local_root.mkdir(mode=0o700)
    write_public_bge_pair_evaluation_receipt(
        approved_root=local_root,
        evaluation=evaluation,
    )
    restored = load_public_bge_pair_evaluation_evidence(
        approved_root=local_root,
        evaluation_plan_digest=evaluation.evaluation_plan_digest,
    )
    assert restored == evaluation.evidence()
    stored = (local_root / "evaluation/public-bge-pair.v1.json").read_text(encoding="utf-8")
    assert "canonicalText" not in stored
    assert '"embedding":' not in stored
    assert "exact evidence" not in stored


def test_public_pair_evaluator_fails_acceptance_when_actual_local_query_vector_misses_gold() -> None:
    exact_records = tuple(_record("EXACT30", index) for index in range(30))
    oa_records = tuple(_record("OA112", index) for index in range(112))
    registry = _registry()
    wrong_queries = list(_exact_queries())
    wrong_queries[0] = replace(wrong_queries[0], expected_source_id="src_exact_029")

    evaluation = evaluate_public_bge_pair(
        exact30_records=exact_records,
        exact30_context=build_public_bge_component_context(exact_records),
        oa112_records=oa_records,
        oa112_context=build_public_bge_component_context(oa_records),
        oa112_registry_digest=registry.registry_digest,
        exact30_queries=tuple(wrong_queries),
        exact30_fixture_digest="b" * 64,
        oa112_queries=_oa_queries(registry),
        oa112_manifest_digest="c" * 64,
        query_embedder=_FixtureQueryEmbedder(),
    )

    assert evaluation.exact_top5_hit_rate < 1.0
    assert evaluation.acceptance_passed is False


def test_public_voyage_pair_evaluator_runs_all_packet_accounted_queries_without_bge_fallback() -> None:
    exact_records = tuple(_voyage_record("EXACT30", index) for index in range(30))
    oa_records = tuple(_voyage_record("OA112", index) for index in range(112))
    registry = _registry()
    exact_queries = _exact_queries()
    oa_queries = _oa_queries(registry)
    query_embedder = _FixtureVoyageQueryEmbedder()

    evaluation = evaluate_public_voyage_pair(
        exact30_records=exact_records,
        exact30_context=build_external_exact30_public_voyage_component_context(
            records=exact_records,
            source_card_corpus_manifest_sha256="b" * 64,
        ),
        oa112_records=oa_records,
        oa112_context=build_oa112_public_voyage_component_context(
            records=oa_records,
            registry_id=registry.registry_id,
            registry_digest=registry.registry_digest,
        ),
        oa112_registry_digest=registry.registry_digest,
        exact30_queries=exact_queries,
        exact30_fixture_digest="c" * 64,
        oa112_queries=oa_queries,
        oa112_manifest_digest="d" * 64,
        query_embedder=query_embedder,
    )

    assert evaluation.acceptance_passed is True
    assert evaluation.exact30.provider_physical_call_count == 1
    assert evaluation.oa112.provider_physical_call_count == 1
    assert len(query_embedder.calls) == 122
    assert evaluation_query_id_by_sha256(
        exact30_queries=exact_queries,
        oa112_queries=oa_queries,
    )[hashlib.sha256(exact_queries[0].question.encode("utf-8")).hexdigest()] == "q01"


def test_public_voyage_evaluation_scores_precise_single_source_ranking_without_weakening_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source-targeted 평가에서는 정확한 단일-source ranking을 answer 안전조건과 혼동하지 않는다."""

    original_retrieve_lexical = voyage_evaluator._InMemoryPublicChannels.retrieve_lexical

    def _retrieve_only_best_lexical(
        channel: object,
        **kwargs: object,
    ) -> object:
        result = original_retrieve_lexical(channel, **kwargs)
        return replace(result, items=result.items[:1])

    monkeypatch.setattr(
        voyage_evaluator._InMemoryPublicChannels,
        "retrieve_lexical",
        _retrieve_only_best_lexical,
    )
    exact_records = tuple(_voyage_record("EXACT30", index) for index in range(30))
    oa_records = tuple(_voyage_record("OA112", index) for index in range(112))
    registry = _registry()
    query_embedder = _FixtureVoyageQueryEmbedder()

    evaluation = evaluate_public_voyage_pair(
        exact30_records=exact_records,
        exact30_context=build_external_exact30_public_voyage_component_context(
            records=exact_records,
            source_card_corpus_manifest_sha256="b" * 64,
        ),
        oa112_records=oa_records,
        oa112_context=build_oa112_public_voyage_component_context(
            records=oa_records,
            registry_id=registry.registry_id,
            registry_digest=registry.registry_digest,
        ),
        oa112_registry_digest=registry.registry_digest,
        exact30_queries=_exact_queries(),
        exact30_fixture_digest="c" * 64,
        oa112_queries=_oa_queries(registry),
        oa112_manifest_digest="d" * 64,
        query_embedder=query_embedder,
    )

    assert evaluation.acceptance_passed is True
    assert evaluation.exact30.exact_top5_hit_rate == 1.0
    assert evaluation.oa112.track_recall_at5 == 1.0
    assert len(query_embedder.calls) == 122


def test_public_voyage_pair_evaluator_stops_after_one_failed_physical_query_attempt() -> None:
    exact_records = tuple(_voyage_record("EXACT30", index) for index in range(30))
    oa_records = tuple(_voyage_record("OA112", index) for index in range(112))
    registry = _registry()
    query_embedder = _FixtureVoyageQueryEmbedder(fail_on_call=11)

    with pytest.raises(PublicVoyagePairEvaluationError, match="PUBLIC_VOYAGE_EVALUATION_QUERY_FAILED"):
        evaluate_public_voyage_pair(
            exact30_records=exact_records,
            exact30_context=build_external_exact30_public_voyage_component_context(
                records=exact_records,
                source_card_corpus_manifest_sha256="b" * 64,
            ),
            oa112_records=oa_records,
            oa112_context=build_oa112_public_voyage_component_context(
                records=oa_records,
                registry_id=registry.registry_id,
                registry_digest=registry.registry_digest,
            ),
            oa112_registry_digest=registry.registry_digest,
            exact30_queries=_exact_queries(),
            exact30_fixture_digest="c" * 64,
            oa112_queries=_oa_queries(registry),
            oa112_manifest_digest="d" * 64,
            query_embedder=query_embedder,
        )

    assert len(query_embedder.calls) == 11


def test_oa112_local_manifest_is_registry_bound_complete_and_cannot_name_gold_source(
    tmp_path: Path,
) -> None:
    local_root = tmp_path / "private"
    local_root.mkdir(mode=0o700)
    registry = _registry()
    payload = _oa_manifest(registry)
    _write_private_json(local_root / "oa112-evaluation-manifest.v1.json", payload)

    queries, digest = load_oa112_evaluation_queries(
        approved_root=local_root,
        registry=registry,
    )
    assert len(queries) == 112
    assert digest == payload["evaluationManifestDigest"]
    assert tuple(item.track_id for item in queries) == tuple(
        track_id for track_id in OA_TRACK_IDS for _ in range(8)
    )

    payload = _oa_manifest(registry)
    query_payloads = payload["queries"]
    assert isinstance(query_payloads, list)
    first = query_payloads[0]
    assert isinstance(first, dict)
    first["question"] = f"Explain {first['expectedSourceId']} safely."
    payload["evaluationManifestDigest"] = _manifest_digest(payload)
    _write_private_json(local_root / "oa112-evaluation-manifest.v1.json", payload)
    with pytest.raises(PublicBgePairEvaluationError, match="PUBLIC_BGE_EVALUATION_QUERY"):
        load_oa112_evaluation_queries(approved_root=local_root, registry=registry)


def test_voyage_evaluation_manifest_preparation_keeps_tracked_exact_fixture_immutable(
    tmp_path: Path,
) -> None:
    local_root = tmp_path / "private"
    local_root.mkdir(mode=0o700)
    registry = _registry()
    tracked_fixture = Path(__file__).resolve().parents[5] / "capstone-rag/eval/s4-2b-30-card-smoke.v1.json"
    before = tracked_fixture.read_bytes()
    expected_source_ids = tuple(
        item["expectedSourceIds"][0] for item in json.loads(before)["queries"]
    )

    first = prepare_public_voyage_evaluation_manifests(
        local_root=local_root,
        exact30_source_card_corpus_manifest_sha256="e" * 64,
        exact30_source_ids=expected_source_ids + tuple(f"src_extra_{index:02d}" for index in range(20)),
        registry=registry,
    )
    second = prepare_public_voyage_evaluation_manifests(
        local_root=local_root,
        exact30_source_card_corpus_manifest_sha256="e" * 64,
        exact30_source_ids=expected_source_ids + tuple(f"src_extra_{index:02d}" for index in range(20)),
        registry=registry,
    )

    assert first == second
    assert tracked_fixture.read_bytes() == before
    input_root = local_root / "evaluation-inputs"
    exact_queries, _ = load_exact30_evaluation_queries(
        source_card_corpus_manifest_sha256="e" * 64,
        fixture_path=input_root / "exact30-evaluation-manifest.v1.json",
    )
    oa_queries, _ = load_oa112_evaluation_queries(approved_root=input_root, registry=registry)
    assert len(exact_queries) == 10
    assert len(oa_queries) == 112
    assert all(query.expected_source_id not in query.question for query in oa_queries)
    assert all((input_root / name).stat().st_mode & 0o777 == 0o600 for name in (
        "exact30-evaluation-manifest.v1.json",
        "oa112-evaluation-manifest.v1.json",
    ))


def test_exact30_fixture_is_bound_to_the_frozen_source_card_manifest() -> None:
    corpus = load_frozen_source_card_corpus()
    queries, digest = load_exact30_evaluation_queries(
        source_card_corpus_manifest_sha256=corpus.corpus_manifest_sha256,
    )

    assert len(queries) == 10
    assert len(digest) == 64
    assert all(query.track_id is None for query in queries)


def test_receipt_lookup_does_not_reuse_a_different_pair_plan(tmp_path: Path) -> None:
    exact_records = tuple(_record("EXACT30", index) for index in range(30))
    oa_records = tuple(_record("OA112", index) for index in range(112))
    exact_context = build_public_bge_component_context(exact_records)
    oa_context = build_public_bge_component_context(oa_records)
    registry = _registry()
    evaluation = evaluate_public_bge_pair(
        exact30_records=exact_records,
        exact30_context=exact_context,
        oa112_records=oa_records,
        oa112_context=oa_context,
        oa112_registry_digest=registry.registry_digest,
        exact30_queries=_exact_queries(),
        exact30_fixture_digest="b" * 64,
        oa112_queries=_oa_queries(registry),
        oa112_manifest_digest="c" * 64,
        query_embedder=_FixtureQueryEmbedder(),
    )
    local_root = tmp_path / "private"
    local_root.mkdir(mode=0o700)
    write_public_bge_pair_evaluation_receipt(approved_root=local_root, evaluation=evaluation)

    different_plan = evaluation_plan_digest(
        exact30_context=exact_context,
        oa112_context=oa_context,
        oa112_registry_digest=registry.registry_digest,
        exact30_fixture_digest="d" * 64,
        oa112_manifest_digest="c" * 64,
    )
    assert different_plan != evaluation.evaluation_plan_digest
    assert (
        load_public_bge_pair_evaluation_evidence(
            approved_root=local_root,
            evaluation_plan_digest=different_plan,
        )
        is None
    )


@pytest.mark.parametrize(
    ("left", "right"),
    (
        ("src_exact_000 Exact Title local evidence.", "exact evidence 0"),
        ("가격 변동성과 위험 한계", "위험 한계는 무엇인가요?"),
        ("KIS REST / token issue", "KIS token"),
        ("source_id_with_underscore", "source id"),
    ),
)
def test_local_lexical_score_matches_postgresql_pg_trgm_similarity(
    postgres_cluster: dict[str, str],
    left: str,
    right: str,
) -> None:
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        actual = connection.execute(
            "SELECT encode(float4send(similarity(lower(%s), lower(%s))), 'hex')",
            (left, right),
        ).fetchone()
    assert actual is not None
    assert struct.pack("!f", _pg_trgm_similarity(left, right)).hex() == actual[0]


def _record(scope: str, index: int) -> PublicBgeRecord:
    prefix = "exact" if scope == "EXACT30" else "oa"
    source_id = f"src_{prefix}_{index:03d}"
    source_revision_id = f"srv_{prefix}_{index:03d}"
    document_id = f"doc_{prefix}_{index:014d}"
    marker = f"{prefix} evidence {index}"
    text = f"{marker} explains immutable local retrieval evidence."
    text_hash = hashlib.sha256(text.encode()).hexdigest()
    raw_hash = hashlib.sha256(f"raw-{scope}-{index}".encode()).hexdigest()
    normalized_hash = hashlib.sha256(f"normalized-{scope}-{index}".encode()).hexdigest()
    chunk_id = "rag_v2_chk_" + hashlib.sha256(f"chunk-{scope}-{index}".encode()).hexdigest()[:32]
    canonical_url = f"https://example.com/{prefix}/{index}"
    document_ir = {
        "blocks": [
            {
                "blockType": "PARAGRAPH",
                "locator": {"section": "evidence"},
                "ocrConfidence": None,
                "readingOrder": 1,
                "text": text,
            }
        ],
        "contractId": "rag-document-ir-v1",
        "documentIrVersion": 1,
        "extractionMode": "NATIVE",
        "languageTags": ["en"],
        "mimeType": "text/plain",
        "normalizedContentSha256": normalized_hash,
        "parserEvidence": {
            "ocr": {"backend": "NOT_USED", "backendVersion": None, "modelSha256": None},
            "parserArtifactSha256": "d" * 64,
            "parserBackend": "fixture",
            "parserVersion": "fixture-v1",
        },
        "rawContentSha256": raw_hash,
        "safetyClassification": {
            "externalLlmEligible": scope == "OA112",
            "piiDetected": False,
            "promptInjectionDetected": False,
            "secretDetected": False,
        },
        "sourceId": source_id,
        "sourceRevisionId": source_revision_id,
    }
    document = RagV2DocumentMaterialization(
        document_id=document_id,
        source_scope=scope,  # type: ignore[arg-type]
        source_id=source_id,
        source_revision_id=source_revision_id,
        raw_content_sha256=raw_hash,
        normalized_content_sha256=normalized_hash,
        external_processing_eligible=scope == "OA112",
        chunks=(
            RagV2CanonicalDocumentChunk(
                chunk_id=chunk_id,
                document_id=document_id,
                sequence=1,
                heading_path=("Evidence",),
                locator={"section": "evidence"},
                canonical_text=text,
                canonical_text_sha256=text_hash,
                token_count=400,
                contains_table=False,
            ),
        ),
    )
    coordinate = index if scope == "EXACT30" else 100 + index
    vector = np.zeros(1024, dtype=np.float32)
    vector[coordinate] = 1.0
    materialized = RagV2BgeMaterializedPublicDocument(
        document=document,
        embeddings=(
            RagV2BgeDocumentEmbedding(
                chunk_id=chunk_id,
                embedding_input_hash=hashlib.sha256(f"input-{scope}-{index}".encode()).hexdigest(),
                context_set_hash=None,
                embedding=vector,
            ),
        ),
        source_revision_sha256=hashlib.sha256(
            json.dumps(document_ir, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest(),
        document_ir=document_ir,
    )
    return (
        materialized,
        PublicBgeSourceMetadata(
            citation_title=f"{prefix} title {index}",
            retrieval_topics=("METHODOLOGY",),
            canonical_https_url=canonical_url,
            source_card_sha256=(hashlib.sha256(f"card-{index}".encode()).hexdigest() if scope == "EXACT30" else None),
            oa_track_id=_track_for_index(index) if scope == "OA112" else None,
            source_card=(
                _source_card(
                    source_id=source_id,
                    ordinal=index,
                    raw_hash=raw_hash,
                    canonical_url=canonical_url,
                )
                if scope == "OA112"
                else None
            ),
            license_evidence_sha256=(hashlib.sha256(f"license-{index}".encode()).hexdigest() if scope == "OA112" else None),
            access_evidence_sha256=(hashlib.sha256(f"access-{index}".encode()).hexdigest() if scope == "OA112" else None),
            machine_fetch_allowed=scope == "OA112",
            local_processing_allowed=True,
            external_embedding_allowed=scope == "OA112",
            external_generation_allowed=scope == "OA112",
        ),
    )


def _voyage_record(scope: str, index: int) -> RagV2VoyageMaterializedPublicDocument:
    """Reuse the fixture IR but give every Voyage chunk its required contextual input-set hash."""

    materialized, metadata = _record(scope, index)
    voyage_metadata = PublicVoyageSourceMetadata(
        citation_title=metadata.citation_title,
        retrieval_topics=metadata.retrieval_topics,
        canonical_https_url=metadata.canonical_https_url,
        source_card_sha256=metadata.source_card_sha256,
        machine_fetch_allowed=metadata.machine_fetch_allowed,
        local_processing_allowed=metadata.local_processing_allowed,
        # The BGE fixture models an offline-only exact card.  The Voyage fixture intentionally
        # models the separately consented external-safe exact-30 projection.
        external_embedding_allowed=True,
        external_generation_allowed=True,
        oa_track_id=metadata.oa_track_id,
        oa_source_card=dict(metadata.source_card) if metadata.source_card is not None else None,
        license_evidence_sha256=metadata.license_evidence_sha256,
        access_evidence_sha256=metadata.access_evidence_sha256,
    )
    return RagV2VoyageMaterializedPublicDocument(
        document=replace(materialized.document, external_processing_eligible=True),
        embeddings=tuple(
            RagV2VoyageDocumentEmbedding(
                chunk_id=embedding.chunk_id,
                embedding_input_hash=embedding.embedding_input_hash,
                context_set_hash=hashlib.sha256(
                    f"voyage-context-{scope}-{index}".encode("utf-8")
                ).hexdigest(),
                embedding=np.array(embedding.embedding, dtype=np.float32, copy=True),
            )
            for embedding in materialized.embeddings
        ),
        source_revision_sha256=materialized.source_revision_sha256,
        document_ir=dict(materialized.document_ir),
        metadata=voyage_metadata,
    )


def _source_card(
    *,
    source_id: str,
    ordinal: int,
    raw_hash: str,
    canonical_url: str,
) -> dict[str, object]:
    return {
        "accessEvidence": {
            "accessCheckedAt": "2026-08-03T00:00:00Z",
            "accessEvidenceDigest": hashlib.sha256(f"access-{ordinal}".encode()).hexdigest(),
            "verificationState": "VERIFIED",
        },
        "activeOa112Eligible": True,
        "authors": [f"Fixture {ordinal}"],
        "canonicalUrl": canonical_url,
        "canonicalUrlSha256": hashlib.sha256(canonical_url.encode()).hexdigest(),
        "contractId": "rag-source-card-v4",
        "identifier": {"scheme": "DOI", "value": f"10.0000/fixture-{ordinal}"},
        "licenseEvidenceDigest": hashlib.sha256(f"license-{ordinal}".encode()).hexdigest(),
        "mimeType": "text/plain",
        "permissions": {
            "externalEmbeddingAllowed": True,
            "externalGenerationAllowed": True,
            "localProcessingAllowed": True,
            "machineFetchAllowed": True,
        },
        "rawContentSha256": raw_hash,
        "revision": f"r{ordinal}",
        "revisionDate": "2026-08-03",
        "schemaVersion": 4,
        "sourceId": source_id,
        "sourceKind": "OPEN_ACCESS_DOCUMENT",
        "title": f"OA {ordinal}",
    }


def _exact_queries() -> tuple[PublicBgeEvaluationQuery, ...]:
    return tuple(
        PublicBgeEvaluationQuery(
            query_id=f"q{index + 1:02d}",
            question=f"Explain exact evidence {index} without an investment recommendation.",
            expected_source_id=f"src_exact_{index:03d}",
            topics=(),
            track_id=None,
        )
        for index in range(10)
    )


def _oa_queries(registry: Oa112ActiveRegistry) -> tuple[PublicBgeEvaluationQuery, ...]:
    return tuple(
        PublicBgeEvaluationQuery(
            query_id=f"oa112-q{index + 1:03d}",
            question=f"Explain oa evidence {index} without an investment recommendation.",
            expected_source_id=entry.source_id,
            topics=("METHODOLOGY",),
            track_id=entry.track_id,
        )
        for index, entry in enumerate(registry.active_entries)
    )


def _registry() -> Oa112ActiveRegistry:
    entries: list[Oa112RegistryEntry] = []
    for index in range(112):
        track_id = _track_for_index(index)
        source_id = f"src_oa_{index:03d}"
        source_revision_id = f"srv_oa_{index:03d}"
        entries.append(
            Oa112RegistryEntry(
                source_id=source_id,
                source_revision_id=source_revision_id,
                document_id=f"doc_oa_{index:014d}",
                track_id=track_id,
                language_tags=("en",),
                retrieval_topics=("METHODOLOGY",),
                source_card={},
                title=f"OA {index}",
                canonical_url=f"https://example.com/oa/{index}",
                raw_content_sha256=hashlib.sha256(f"raw-{index}".encode()).hexdigest(),
                mime_type="text/plain",
                license_evidence_sha256=hashlib.sha256(f"license-{index}".encode()).hexdigest(),
                access_evidence_sha256=hashlib.sha256(f"access-{index}".encode()).hexdigest(),
                machine_fetch_allowed=True,
                local_processing_allowed=True,
                external_embedding_allowed=True,
                external_generation_allowed=True,
            )
        )
    return Oa112ActiveRegistry(
        registry_id="fixture-oa112-evaluation",
        registry_digest="a" * 64,
        active_entries=tuple(entries),
        reserve_entries=(),
    )


def _track_for_index(index: int) -> str:
    return OA_TRACK_IDS[index // 8]


def _oa_manifest(registry: Oa112ActiveRegistry) -> dict[str, object]:
    queries: list[dict[str, object]] = []
    for index, entry in enumerate(registry.active_entries, start=1):
        queries.append(
            {
                "id": f"oa112-q{index:03d}",
                "question": f"Explain oa evidence {index - 1} with source limitations.",
                "expectedSourceId": entry.source_id,
                "trackId": entry.track_id,
                "topics": ["METHODOLOGY"],
            }
        )
    payload: dict[str, object] = {
        "contractId": "rag-v2-oa112-evaluation-manifest-v1",
        "schemaVersion": 1,
        "registryDigest": registry.registry_digest,
        "queryCount": 112,
        "queries": queries,
        "evaluationManifestDigest": None,
    }
    payload["evaluationManifestDigest"] = _manifest_digest(payload)
    return payload


def _manifest_digest(payload: dict[str, object]) -> str:
    detached = json.loads(json.dumps(payload, ensure_ascii=False))
    assert isinstance(detached, dict)
    detached["evaluationManifestDigest"] = None
    return hashlib.sha256(
        json.dumps(detached, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _write_private_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
