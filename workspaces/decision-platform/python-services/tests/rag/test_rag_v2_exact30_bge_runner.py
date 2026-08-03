from __future__ import annotations

import json
import re

import numpy as np

from app.rag.rag_v2_exact30_bge_runner import materialize_exact30_public_bge_component
from app.rag.source_card_corpus import load_frozen_source_card_corpus


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


def test_exact30_runner_materializes_the_full_frozen_component_with_local_bge_only() -> None:
    corpus = load_frozen_source_card_corpus()
    embedder = _FixtureEmbedder()

    materialization = materialize_exact30_public_bge_component(
        tokenizer=_FixtureTokenizer(),
        embedder=embedder,
        corpus=corpus,
    )

    assert materialization.context.component_scope == "EXACT30"
    assert materialization.context.expected_source_count == 30
    assert len(materialization.records) == 30
    assert all(
        record[0].document.external_processing_eligible is False
        and record[1].machine_fetch_allowed is False
        and record[1].external_embedding_allowed is False
        and record[1].external_generation_allowed is False
        for record in materialization.records
    )
    assert len(embedder.calls) == 30
    receipt = materialization.content_free_receipt()
    assert receipt["sourceCount"] == 30
    assert receipt["chunkCount"] == materialization.context.expected_chunk_count
    serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    assert "canonicalText" not in serialized
    assert '"embedding":' not in serialized
    assert "/capstone-rag/" not in serialized
