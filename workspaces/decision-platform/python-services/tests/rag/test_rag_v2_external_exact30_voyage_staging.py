from __future__ import annotations

import re

import numpy as np
import pytest

from app.rag.external_processing_corpus import load_external_processing_corpus
from app.rag.rag_v2_external_exact30_voyage_runner import (
    VoyagePreChunkedDocumentGroup,
    materialize_external_exact30_public_voyage_component,
)
from app.rag.rag_v2_external_exact30_voyage_staging import (
    ExternalExact30VoyageStagingError,
    build_external_exact30_voyage_staging_payload,
)


class _FixtureTokenizer:
    def token_spans(self, text: str) -> tuple[tuple[int, int], ...]:
        return tuple((match.start(), match.end()) for match in re.finditer(r"\S+", text))


class _FixtureVoyageEmbedder:
    def embed_document_groups(
        self,
        *,
        groups: tuple[VoyagePreChunkedDocumentGroup, ...],
    ) -> np.ndarray:
        vector_count = sum(len(group.chunks) for group in groups)
        vectors = np.zeros((vector_count, 1024), dtype=np.float32)
        for index in range(vector_count):
            vectors[index, index % 1024] = 1.0
        return vectors


def test_external_exact30_voyage_staging_payload_binds_one_full_external_component() -> None:
    materialization = materialize_external_exact30_public_voyage_component(
        tokenizer=_FixtureTokenizer(),
        embedder=_FixtureVoyageEmbedder(),
        corpus=load_external_processing_corpus(),
    )

    payload = build_external_exact30_voyage_staging_payload(
        materialization.records[0],
        context=materialization.context,
    )

    assert payload["componentScope"] == "EXACT30"
    assert payload["embeddingProfileId"] == "voyage_context_4_1024_v1"
    assert payload["expectedSourceCount"] == 30
    assert len(payload["memberDigests"]) == 30
    source = payload["source"]
    assert isinstance(source, dict)
    assert source["machineFetchAllowed"] is False
    assert source["localProcessingAllowed"] is True
    assert source["externalEmbeddingAllowed"] is True
    assert source["externalGenerationAllowed"] is True
    assert source["externalProcessingEligible"] is True
    assert source["oaSourceCard"] is None
    assert source["oaTrackId"] is None
    assert source["licenseEvidenceSha256"] is None
    assert source["accessEvidenceSha256"] is None
    assert source["embeddings"][0]["contextSetHash"]
    assert len(source["embeddings"][0]["embedding"]) == 1024


def test_external_exact30_voyage_staging_rejects_a_record_outside_the_bound_manifest() -> None:
    materialization = materialize_external_exact30_public_voyage_component(
        tokenizer=_FixtureTokenizer(),
        embedder=_FixtureVoyageEmbedder(),
        corpus=load_external_processing_corpus(),
    )
    with pytest.raises(ExternalExact30VoyageStagingError, match="EXTERNAL_EXACT30_VOYAGE_MEMBER_MANIFEST"):
        build_external_exact30_voyage_staging_payload(
            materialization.records[0],
            context=materialization.context.__class__(
                component_scope=materialization.context.component_scope,
                component_generation_id=materialization.context.component_generation_id,
                materialization_run_id=materialization.context.materialization_run_id,
                generation_hash=materialization.context.generation_hash,
                manifest_hash=materialization.context.manifest_hash,
                expected_source_count=materialization.context.expected_source_count,
                expected_chunk_count=materialization.context.expected_chunk_count,
                embedding_profile_id=materialization.context.embedding_profile_id,
                member_digests=tuple(f"{index + 1:064x}" for index in range(30)),
                source_card_corpus_manifest_sha256=("1" * 64),
            ),
        )
