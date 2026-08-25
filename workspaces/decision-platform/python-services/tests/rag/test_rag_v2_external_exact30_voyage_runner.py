from __future__ import annotations

import json
import re

import numpy as np
import pytest

from app.rag.external_processing_corpus import load_external_processing_corpus
from app.rag.rag_v2_external_exact30_voyage_runner import (
    RagV2ExternalExact30VoyageRunnerError,
    VoyagePreChunkedDocumentGroup,
    materialize_external_exact30_public_voyage_component,
)


class _FixtureTokenizer:
    def token_spans(self, text: str) -> tuple[tuple[int, int], ...]:
        return tuple((match.start(), match.end()) for match in re.finditer(r"\S+", text))


class _FixtureVoyageEmbedder:
    def __init__(self) -> None:
        self.calls: list[tuple[VoyagePreChunkedDocumentGroup, ...]] = []

    def embed_document_groups(
        self,
        *,
        groups: tuple[VoyagePreChunkedDocumentGroup, ...],
    ) -> np.ndarray:
        self.calls.append(groups)
        vector_count = sum(len(group.chunks) for group in groups)
        vectors = np.zeros((vector_count, 1024), dtype=np.float32)
        for index in range(vector_count):
            vectors[index, index % 1024] = 1.0
        return vectors


def test_external_exact30_voyage_runner_materializes_all_external_cards_in_one_ordered_group_call() -> (
    None
):
    corpus = load_external_processing_corpus()
    embedder = _FixtureVoyageEmbedder()

    materialization = materialize_external_exact30_public_voyage_component(
        tokenizer=_FixtureTokenizer(),
        embedder=embedder,
        corpus=corpus,
    )

    assert materialization.context.component_scope == "EXACT30"
    assert materialization.context.embedding_profile_id == "voyage_context_4_1024_v1"
    assert materialization.context.expected_source_count == 30
    assert len(materialization.records) == 30
    assert len(embedder.calls) == 1
    groups = embedder.calls[0]
    assert len(groups) == 30
    assert tuple(group.source_id for group in groups) == tuple(
        sorted((card.source_id for card in corpus.cards), key=lambda value: value.encode("utf-8"))
    )
    assert all(
        group.context_set_hash
        and all(chunk.canonical_text == card.canonical_body.strip() for chunk in group.chunks)
        for group, card in zip(
            groups,
            sorted(corpus.cards, key=lambda value: value.source_id.encode("utf-8")),
            strict=True,
        )
    )
    assert all(
        record.document.external_processing_eligible
        and record.metadata.external_embedding_allowed
        and record.metadata.external_generation_allowed
        and all(embedding.context_set_hash for embedding in record.embeddings)
        for record in materialization.records
    )
    receipt = materialization.content_free_receipt()
    assert receipt["sourceCount"] == 30
    assert receipt["chunkCount"] == materialization.context.expected_chunk_count
    serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    assert "canonicalText" not in serialized
    assert '"embedding":' not in serialized
    assert "/capstone-rag/" not in serialized


def test_external_exact30_voyage_runner_rejects_non_unit_or_partial_provider_output() -> None:
    class _BadEmbedder:
        def embed_document_groups(
            self,
            *,
            groups: tuple[VoyagePreChunkedDocumentGroup, ...],
        ) -> np.ndarray:
            return np.zeros(
                (sum(len(group.chunks) for group in groups) - 1, 1024), dtype=np.float32
            )

    with pytest.raises(RagV2ExternalExact30VoyageRunnerError, match="VOYAGE_COMPONENT_EMBEDDING"):
        materialize_external_exact30_public_voyage_component(
            tokenizer=_FixtureTokenizer(),
            embedder=_BadEmbedder(),
            corpus=load_external_processing_corpus(),
        )
