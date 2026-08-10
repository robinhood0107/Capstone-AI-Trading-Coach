from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import numpy as np
import psycopg
import pytest

import app.rag.local_document_parser as local_document_parser
import app.rag.rag_v2_oa112_bge_runner as oa112_bge_runner
from app.rag.external_processing_corpus import load_external_processing_corpus
from app.rag.oa112_active_registry import Oa112ActiveRegistry, Oa112RegistryEntry
from app.rag.oa_release_manifest import OA_TRACK_IDS
from app.rag.rag_v2_oa112_bge_runner import (
    RagV2Oa112BgeRunnerError,
    materialize_oa112_public_bge_component,
)
from app.rag.rag_v2_oa112_voyage_runner import (
    materialize_prepared_oa112_public_voyage_component,
    prepare_oa112_public_voyage_component,
)
from app.rag.rag_v2_voyage_full_bundle import (
    materialize_public_base_voyage_full_bundle,
    prepare_public_base_voyage_full_bundle,
)
from app.rag.rag_v2_public_voyage_staging import build_public_voyage_staging_payload
from app.rag.rag_v2_public_voyage_activation_repository import (
    PublicVoyageActivationRequest,
    PsycopgRagV2PublicVoyageActivationRepository,
)
from app.rag.rag_v2_public_voyage_staging_repository import (
    PublicVoyageEvaluationEvidence,
    PublicVoyageStagingRepositoryError,
    PsycopgRagV2PublicVoyageStagingRepository,
)
from app.rag.pre_s5_provider_control import PreS5VoyageQueryActivation
from app.rag.pre_s5_voyage_query_usage_repository import PsycopgPreS5VoyageQueryUsageRepository


class _FixtureTokenizer:
    def token_spans(self, text: str) -> tuple[tuple[int, int], ...]:
        return tuple((match.start(), match.end()) for match in re.finditer(r"\S+", text))

    def take_prefix(self, text: str, maximum_tokens: int) -> str:
        spans = self.token_spans(text)
        return text[: spans[min(len(spans), maximum_tokens) - 1][1]] if spans else ""

    def take_suffix(self, text: str, maximum_tokens: int) -> str:
        spans = self.token_spans(text)
        return text[spans[max(0, len(spans) - maximum_tokens)][0] :] if spans else ""


class _FixtureEmbedder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def embed(self, texts: tuple[str, ...]) -> np.ndarray:
        self.calls.append(texts)
        vectors = np.zeros((len(texts), 1024), dtype=np.float32)
        for index in range(len(texts)):
            vectors[index, index] = 1.0
        return vectors


class _FixtureFullBundleEmbedder:
    """provider socket 없이 all public groups one call로 받는 coordinator seam이다."""

    def __init__(self) -> None:
        self.calls = 0
        self.group_counts: list[int] = []

    def embed_full_bundle(self, *, bundle: object) -> np.ndarray:
        components = getattr(bundle, "components")
        assert len(components) == 3
        self.calls += 1
        groups = tuple(group for component in components for group in component.groups)
        self.group_counts.append(len(groups))
        vectors = np.zeros((sum(len(group.chunks) for group in groups), 1024), dtype=np.float32)
        for index in range(len(vectors)):
            vectors[index, index % 1024] = 1.0
        return vectors


class _FixtureApprovedParser:
    def __init__(self, entries: tuple[Oa112RegistryEntry, ...]) -> None:
        self._entries = {entry.source_id: entry for entry in entries}
        self.calls: list[dict[str, object]] = []

    def parse_approved_document(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        source_id = kwargs["source_id"]
        assert isinstance(source_id, str)
        entry = self._entries[source_id]
        text = f"Approved OA evidence {entry.source_id} stays in its immutable source generation."
        blocks = [
            {
                "blockType": "PARAGRAPH",
                "locator": {"section": "document"},
                "ocrConfidence": None,
                "readingOrder": 1,
                "text": text,
            }
        ]
        return {
            "blocks": blocks,
            "contractId": "rag-document-ir-v1",
            "documentIrVersion": 1,
            "extractionMode": "NATIVE",
            "languageTags": ["en"],
            "mimeType": entry.mime_type,
            "normalizedContentSha256": hashlib.sha256(
                json.dumps(blocks, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
            ).hexdigest(),
            "parserEvidence": {
                "ocr": {"backend": "NOT_USED", "backendVersion": None, "modelSha256": None},
                "parserArtifactSha256": "b" * 64,
                "parserBackend": "fixture",
                "parserVersion": "fixture-v1",
            },
            "rawContentSha256": entry.raw_content_sha256,
            "safetyClassification": {
                "externalLlmEligible": True,
                "piiDetected": False,
                "promptInjectionDetected": False,
                "secretDetected": False,
            },
            "sourceId": entry.source_id,
            "sourceRevisionId": entry.source_revision_id,
        }


def test_oa112_runner_materializes_only_the_full_active_registry_with_local_bge(
    tmp_path: Path,
) -> None:
    registry = _registry()
    parser = _FixtureApprovedParser(registry.active_entries)
    embedder = _FixtureEmbedder()

    materialization = materialize_oa112_public_bge_component(
        tokenizer=_FixtureTokenizer(),
        embedder=embedder,
        registry=registry,
        local_cache_root=tmp_path,
        parser=parser,
    )

    assert materialization.context.component_scope == "OA112"
    assert materialization.context.expected_source_count == 112
    assert len(materialization.records) == 112
    assert len(parser.calls) == len(embedder.calls) == 112
    assert all(
        record[0].document.external_processing_eligible is True
        and record[1].source_card is not None
        and record[1].machine_fetch_allowed is True
        and record[1].external_embedding_allowed is True
        and record[1].external_generation_allowed is True
        for record in materialization.records
    )
    receipt = materialization.content_free_receipt()
    assert receipt["sourceCount"] == 112
    assert receipt["chunkCount"] == materialization.context.expected_chunk_count
    serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    assert "canonicalText" not in serialized
    assert '"embedding":' not in serialized
    assert str(tmp_path) not in serialized


def test_oa112_default_parsers_enable_only_inert_pdf_attachment_stripping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _registry()
    bge_parser = _FixtureApprovedParser(registry.active_entries)
    voyage_parser = _FixtureApprovedParser(registry.active_entries)
    bge_options: list[dict[str, object]] = []
    voyage_options: list[dict[str, object]] = []

    def bge_factory(**options: object) -> _FixtureApprovedParser:
        bge_options.append(options)
        return bge_parser

    def voyage_factory(**options: object) -> _FixtureApprovedParser:
        voyage_options.append(options)
        return voyage_parser

    monkeypatch.setattr(oa112_bge_runner, "LocalDocumentParser", bge_factory)
    materialize_oa112_public_bge_component(
        tokenizer=_FixtureTokenizer(),
        embedder=_FixtureEmbedder(),
        registry=registry,
        local_cache_root=tmp_path,
    )
    monkeypatch.setattr(local_document_parser, "LocalDocumentParser", voyage_factory)
    prepare_oa112_public_voyage_component(
        tokenizer=_FixtureTokenizer(),
        registry=registry,
        local_cache_root=tmp_path,
    )

    expected = {"strip_inert_pdf_attachments": True}
    assert bge_options == voyage_options == [expected]


def test_oa112_runner_rejects_any_active_source_without_all_four_permissions(
    tmp_path: Path,
) -> None:
    registry = _registry()
    first = registry.active_entries[0]
    invalid_registry = replace(
        registry,
        active_entries=(
            replace(first, external_generation_allowed=False),
            *registry.active_entries[1:],
        ),
    )

    with pytest.raises(RagV2Oa112BgeRunnerError, match="OA112_RIGHTS_REQUIRED"):
        materialize_oa112_public_bge_component(
            tokenizer=_FixtureTokenizer(),
            embedder=_FixtureEmbedder(),
            registry=invalid_registry,
            local_cache_root=tmp_path,
            parser=_FixtureApprovedParser(invalid_registry.active_entries),
        )


def test_oa112_voyage_runner_prepares_full_registry_before_assigning_one_full_bundle_slice(
    tmp_path: Path,
) -> None:
    registry = _registry()
    parser = _FixtureApprovedParser(registry.active_entries)

    preparation = prepare_oa112_public_voyage_component(
        tokenizer=_FixtureTokenizer(),
        registry=registry,
        local_cache_root=tmp_path,
        parser=parser,
    )

    assert len(preparation.prepared_documents) == 112
    assert len(preparation.groups) == 112
    assert len(parser.calls) == 112
    assert tuple(group.source_id for group in preparation.groups) == tuple(
        sorted((entry.source_id for entry in registry.active_entries), key=lambda value: value.encode("utf-8"))
    )
    assert all(
        group.context_set_hash
        and all(chunk.token_count >= 1 for chunk in group.chunks)
        for group in preparation.groups
    )
    vector_count = sum(len(group.chunks) for group in preparation.groups)
    vectors = np.zeros((vector_count, 1024), dtype=np.float32)
    for index in range(vector_count):
        vectors[index, index % 1024] = 1.0

    materialization = materialize_prepared_oa112_public_voyage_component(
        preparation=preparation,
        vectors=vectors,
    )

    assert materialization.context.component_scope == "OA112"
    assert materialization.context.embedding_profile_id == "voyage_context_4_1024_v1"
    assert materialization.context.expected_source_count == 112
    assert len(materialization.records) == 112
    assert all(
        record.document.external_processing_eligible
        and record.metadata.machine_fetch_allowed
        and record.metadata.external_embedding_allowed
        and record.metadata.external_generation_allowed
        and record.metadata.oa_track_id in OA_TRACK_IDS
        and record.metadata.oa_source_card is not None
        for record in materialization.records
    )
    receipt = json.dumps(materialization.content_free_receipt(), ensure_ascii=False, sort_keys=True)
    assert "canonicalText" not in receipt
    assert '"embedding":' not in receipt
    assert str(tmp_path) not in receipt


def test_public_voyage_coordinator_uses_one_exact30_plus_oa112_call_and_empty_owner_sentinel(
    tmp_path: Path,
) -> None:
    registry = _registry()
    preparation = prepare_public_base_voyage_full_bundle(
        tokenizer=_FixtureTokenizer(),
        oa112_registry=registry,
        oa112_local_cache_root=tmp_path,
        oa112_parser=_FixtureApprovedParser(registry.active_entries),
        exact30_corpus=load_external_processing_corpus(),
    )
    embedder = _FixtureFullBundleEmbedder()

    materialization = materialize_public_base_voyage_full_bundle(
        preparation=preparation,
        embedder=embedder,
    )

    assert embedder.calls == 1
    assert embedder.group_counts == [142]
    assert preparation.bundle.components[2].component_scope == "OWNER_PRIVATE"
    assert preparation.bundle.components[2].owner_scope_sha256 is None
    assert preparation.bundle.components[2].groups == ()
    assert materialization.exact30.context.expected_source_count == 30
    assert materialization.oa112.context.expected_source_count == 112
    receipt = json.dumps(materialization.content_free_receipt(), ensure_ascii=False, sort_keys=True)
    assert '"ownerScopeSha256": null' in receipt
    assert "Approved OA evidence" not in receipt
    assert "canonicalText" not in receipt


def test_public_voyage_writer_payload_keeps_contextual_hash_and_oa_rights_in_the_same_full_profile(
    tmp_path: Path,
) -> None:
    registry = _registry()
    preparation = prepare_public_base_voyage_full_bundle(
        tokenizer=_FixtureTokenizer(),
        oa112_registry=registry,
        oa112_local_cache_root=tmp_path,
        oa112_parser=_FixtureApprovedParser(registry.active_entries),
        exact30_corpus=load_external_processing_corpus(),
    )
    materialization = materialize_public_base_voyage_full_bundle(
        preparation=preparation,
        embedder=_FixtureFullBundleEmbedder(),
    )

    exact_payload = build_public_voyage_staging_payload(
        materialization.exact30.records[0],
        context=materialization.exact30.context,
    )
    oa_payload = build_public_voyage_staging_payload(
        materialization.oa112.records[0],
        context=materialization.oa112.context,
    )

    exact_source = exact_payload["source"]
    oa_source = oa_payload["source"]
    assert isinstance(exact_source, dict)
    assert isinstance(oa_source, dict)
    assert exact_payload["componentScope"] == "EXACT30"
    assert oa_payload["componentScope"] == "OA112"
    assert exact_source["machineFetchAllowed"] is False
    assert oa_source["machineFetchAllowed"] is True
    assert oa_source["oaSourceCard"] is not None
    assert oa_source["sourceCardSha256"] is None
    assert all("contextSetHash" in value for value in exact_source["embeddings"])
    assert all("contextSetHash" in value for value in oa_source["embeddings"])
    assert str(tmp_path) not in json.dumps(oa_payload, ensure_ascii=False, sort_keys=True)


def test_public_voyage_writer_stages_and_evaluates_both_public_components(
    tmp_path: Path,
    isolated_postgres_cluster: dict[str, str],
) -> None:
    """V45 must persist the single full-bundle result with contextual hashes and no direct writer grants."""

    registry = _registry()
    preparation = prepare_public_base_voyage_full_bundle(
        tokenizer=_FixtureTokenizer(),
        oa112_registry=registry,
        oa112_local_cache_root=tmp_path,
        oa112_parser=_FixtureApprovedParser(registry.active_entries),
        exact30_corpus=load_external_processing_corpus(),
    )
    materialization = materialize_public_base_voyage_full_bundle(
        preparation=preparation,
        embedder=_FixtureFullBundleEmbedder(),
    )
    repository = PsycopgRagV2PublicVoyageStagingRepository(
        database_dsn=isolated_postgres_cluster["rag_writer_dsn"],
    )

    exact_receipts = repository.stage_component(
        records=materialization.exact30.records,
        context=materialization.exact30.context,
    )
    oa_receipts = repository.stage_component(
        records=materialization.oa112.records,
        context=materialization.oa112.context,
    )

    assert len(exact_receipts) == 30
    assert len(oa_receipts) == 112
    assert exact_receipts[-1].state == "STAGED"
    assert oa_receipts[-1].state == "STAGED"
    assert exact_receipts[-1].chunk_count == materialization.exact30.context.expected_chunk_count
    assert oa_receipts[-1].chunk_count == materialization.oa112.context.expected_chunk_count

    with pytest.raises(PublicVoyageStagingRepositoryError, match="PUBLIC_VOYAGE_EVALUATION_REJECTED"):
        repository.evaluate(
            context=materialization.exact30.context,
            evidence=_voyage_evaluation_evidence("exact30-wrong-count", physical_call_count=0),
        )
    evaluation_scope_claim_sha256 = hashlib.sha256(
        b"public-voyage-evaluation-scope-pair"
    ).hexdigest()
    _seed_voyage_evaluation_query_usage(
        cluster=isolated_postgres_cluster,
        component_scope="EXACT30",
        count=10,
        scope_claim_sha256=evaluation_scope_claim_sha256,
    )
    _seed_voyage_evaluation_query_usage(
        cluster=isolated_postgres_cluster,
        component_scope="OA112",
        count=112,
        scope_claim_sha256=evaluation_scope_claim_sha256,
    )
    exact_evaluation = repository.evaluate(
        context=materialization.exact30.context,
        evidence=_voyage_evaluation_evidence(
            "exact30",
            physical_call_count=10,
            evaluation_scope_claim_sha256=evaluation_scope_claim_sha256,
        ),
    )
    oa_evaluation = repository.evaluate(
        context=materialization.oa112.context,
        evidence=_voyage_evaluation_evidence(
            "oa112",
            physical_call_count=112,
            evaluation_scope_claim_sha256=evaluation_scope_claim_sha256,
        ),
    )
    assert exact_evaluation.state == oa_evaluation.state == "EVALUATED"

    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            """
            SELECT component_scope, state, evaluation_status, embedding_profile_id
            FROM rag_v2_immutable_component_generations
            WHERE component_generation_id IN (%s, %s)
            ORDER BY component_scope
            """,
            (
                materialization.exact30.context.component_generation_id,
                materialization.oa112.context.component_generation_id,
            ),
        ).fetchall() == [
            ("EXACT30", "EVALUATED", "PASSED", "voyage_context_4_1024_v1"),
            ("OA112", "EVALUATED", "PASSED", "voyage_context_4_1024_v1"),
        ]
        assert connection.execute(
            """
            SELECT count(*)
            FROM rag_v2_immutable_generation_embeddings
            WHERE embedding_profile_id = 'voyage_context_4_1024_v1'
              AND context_set_hash ~ '^[0-9a-f]{64}$'
            """,
        ).fetchone() == (
            materialization.exact30.context.expected_chunk_count
            + materialization.oa112.context.expected_chunk_count,
        )

    activation = PsycopgRagV2PublicVoyageActivationRepository(
        database_dsn=isolated_postgres_cluster["rag_admin_dsn"],
    ).activate(
        request=PublicVoyageActivationRequest(
            exact30=materialization.exact30.context,
            oa112=materialization.oa112.context,
        ),
    )
    assert activation.embedding_profile_id == "voyage_context_4_1024_v1"
    assert activation.previous_pointer_version == 1
    assert activation.new_pointer_version == 2
    with psycopg.connect(isolated_postgres_cluster["admin_dsn"]) as connection:
        assert connection.execute(
            """
            SELECT state, exact30_generation_id, oa112_generation_id, embedding_profile_id, pointer_version
            FROM rag_v2_immutable_public_bundle_pointers
            WHERE state_id = 'default'
            """,
        ).fetchone() == (
            "ACTIVE",
            materialization.exact30.context.component_generation_id,
            materialization.oa112.context.component_generation_id,
            "voyage_context_4_1024_v1",
            2,
        )

    with psycopg.connect(isolated_postgres_cluster["rag_writer_dsn"], autocommit=True) as connection:
        for table in (
            "rag_v2_immutable_public_voyage_component_evaluations",
            "rag_v2_immutable_public_voyage_component_manifests",
            "rag_v2_immutable_generation_embeddings",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(f"SELECT * FROM {table}").fetchall()


def _registry() -> Oa112ActiveRegistry:
    entries: list[Oa112RegistryEntry] = []
    for track_index, track_id in enumerate(OA_TRACK_IDS):
        for source_index in range(8):
            ordinal = track_index * 8 + source_index
            source_id = f"src_oa_{track_index:02d}_{source_index:02d}"
            source_revision_id = f"srv_oa_{track_index:02d}_{source_index:02d}"
            raw_hash = hashlib.sha256(f"raw-{ordinal}".encode()).hexdigest()
            canonical_url = f"https://example.invalid/oa/{ordinal:03d}.txt"
            entries.append(
                Oa112RegistryEntry(
                    source_id=source_id,
                    source_revision_id=source_revision_id,
                    document_id=f"doc_oa_{hashlib.sha256(source_id.encode()).hexdigest()[:32]}",
                    track_id=track_id,
                    language_tags=("en",),
                    retrieval_topics=("METHODOLOGY",),
                    source_card=_source_card(
                        source_id=source_id,
                        ordinal=ordinal,
                        raw_hash=raw_hash,
                        canonical_url=canonical_url,
                    ),
                    title=f"OA fixture {ordinal}",
                    canonical_url=canonical_url,
                    raw_content_sha256=raw_hash,
                    mime_type="text/plain",
                    license_evidence_sha256=hashlib.sha256(f"license-{ordinal}".encode()).hexdigest(),
                    access_evidence_sha256=hashlib.sha256(f"access-{ordinal}".encode()).hexdigest(),
                    machine_fetch_allowed=True,
                    local_processing_allowed=True,
                    external_embedding_allowed=True,
                    external_generation_allowed=True,
                )
            )
    return Oa112ActiveRegistry(
        registry_id="oa112-runner-fixture-v1",
        registry_digest="a" * 64,
        active_entries=tuple(entries),
        reserve_entries=(),
    )


def _voyage_evaluation_evidence(
    marker: str,
    *,
    physical_call_count: int,
    evaluation_scope_claim_sha256: str | None = None,
) -> PublicVoyageEvaluationEvidence:
    """Fixture vectors model the separately packet-gated query evaluation count, not a hidden provider call."""

    return PublicVoyageEvaluationEvidence(
        evaluation_digest=hashlib.sha256(f"public-voyage-evaluation-{marker}".encode()).hexdigest(),
        evaluation_scope_claim_sha256=evaluation_scope_claim_sha256
        or hashlib.sha256(f"public-voyage-evaluation-scope-{marker}".encode()).hexdigest(),
        exact_top5_hit_rate=1.0,
        track_recall_at5=0.8,
        citation_coverage=0.8,
        direct_advice_block_rate=1.0,
        cross_owner_leak_count=0,
        mixed_profile_row_count=0,
        owner_delete_residual_row_count=0,
        warm_p95_millis=123.0,
        provider_physical_call_count=physical_call_count,
    )


def _seed_voyage_evaluation_query_usage(
    *,
    cluster: dict[str, str],
    component_scope: Literal["EXACT30", "OA112"],
    count: int,
    scope_claim_sha256: str,
) -> None:
    """Fixture-only ledger rows prove V47 rejects a self-reported 10/112 count without one-shot outcomes."""

    repository = PsycopgPreS5VoyageQueryUsageRepository(database_dsn=cluster["rag_writer_dsn"])
    for ordinal in range(count):
        activation = PreS5VoyageQueryActivation(
            packet_sha256=hashlib.sha256(f"packet-{component_scope}-{ordinal}".encode()).hexdigest(),
            nonce_sha256=hashlib.sha256(f"nonce-{component_scope}-{ordinal}".encode()).hexdigest(),
            query_sha256=hashlib.sha256(f"query-{component_scope}-{ordinal}".encode()).hexdigest(),
            scope_claim_sha256=scope_claim_sha256,
            rate_evidence_sha256=hashlib.sha256(f"rate-{component_scope}-{ordinal}".encode()).hexdigest(),
            tokenizer_sha256=hashlib.sha256(
                f"tokenizer-{component_scope}-{ordinal}".encode()
            ).hexdigest(),
            provider="VOYAGE",
            operation="CONTEXTUALIZED_QUERY_EMBEDDING",
            origin="https://api.voyageai.com",
            endpoint="/v1/contextualizedembeddings",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            logical_call_cap=1,
            physical_call_cap=1,
            token_cap=16,
            byte_cap=1_024,
            cost_cap_microusd=16,
            input_microusd_per_token=1,
            retry_count=0,
            raw_artifact_count=0,
        )
        lease = repository.reserve(
            activation=activation,
            evaluation_component_scope=component_scope,
        )
        lease.claim_attempt(now=datetime.now(UTC))
        lease.commit(expected_input_tokens=1, total_tokens=1, actual_cost_microusd=1)


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
        "authors": [f"Fixture Author {ordinal}"],
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
        "title": f"OA fixture {ordinal}",
    }
