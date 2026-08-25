from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from app.rag import rag_v2_bge_materializer
from app.rag.rag_v2_bge_materializer import (
    RagV2BgeMaterializationError,
    RagV2OwnerDocumentRequest,
    materialize_owner_bge_document,
)


class _FixtureTokenizer:
    """실제 BGE artifact 없이 canonical chunk와 input hash를 검증하는 tokenizer다."""

    def token_spans(self, text: str) -> tuple[tuple[int, int], ...]:
        return tuple((match.start(), match.end()) for match in re.finditer(r"\S+", text))

    def take_prefix(self, text: str, maximum_tokens: int) -> str:
        spans = self.token_spans(text)
        return text[: spans[min(len(spans), maximum_tokens) - 1][1]] if spans else ""

    def take_suffix(self, text: str, maximum_tokens: int) -> str:
        spans = self.token_spans(text)
        return text[spans[max(0, len(spans) - maximum_tokens)][0] :] if spans else ""


class _FixtureParser:
    def __init__(self, document_ir: dict[str, object]) -> None:
        self.document_ir = document_ir
        self.calls: list[dict[str, object]] = []

    def parse_owner_document(self, **kwargs: object) -> dict[str, object]:
        self.calls.append(kwargs)
        return self.document_ir


class _FixtureEmbedder:
    def embed(self, texts: tuple[str, ...]) -> np.ndarray:
        values = np.zeros((len(texts), 1024), dtype=np.float32)
        for index in range(len(texts)):
            values[index, index] = 1.0
        return values


def test_owner_bge_materialization_parses_once_and_returns_no_path_or_raw_in_receipt(
    tmp_path: Path,
) -> None:
    parser = _FixtureParser(_document_ir())

    result = materialize_owner_bge_document(
        parser=parser,
        tokenizer=_FixtureTokenizer(),
        embedder=_FixtureEmbedder(),
        request=_request(tmp_path),
    )

    assert len(parser.calls) == 1
    assert len(result.embeddings) == len(result.document.chunks) == 1
    assert result.embeddings[0].chunk_id == result.document.chunks[0].chunk_id
    assert result.embeddings[0].context_set_hash is None
    assert result.embeddings[0].embedding.shape == (1024,)
    assert float(np.linalg.norm(result.embeddings[0].embedding)) == 1.0

    receipt = result.content_free_receipt()
    encoded = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    assert receipt["embeddingProfileId"] == "bge_m3_local_1024_v1"
    assert receipt["ownerRawCopies"] == 0
    assert "private.pdf" not in encoded
    assert str(tmp_path) not in encoded
    assert "canonicalText" not in encoded
    assert '"rawContent":' not in encoded


def test_owner_bge_materialization_rejects_non_bge_profile_before_file_parse(
    tmp_path: Path,
) -> None:
    parser = _FixtureParser(_document_ir())
    request = _request(tmp_path)
    request = replace(request, embedding_profile_id="voyage_context_4_1024_v1")

    with pytest.raises(RagV2BgeMaterializationError, match="BGE_PROFILE_REQUIRED"):
        materialize_owner_bge_document(
            parser=parser,
            tokenizer=_FixtureTokenizer(),
            embedder=_FixtureEmbedder(),
            request=request,
        )

    assert parser.calls == []


def test_owner_bge_materialization_rejects_invalid_embedding_shape(tmp_path: Path) -> None:
    class _WrongShapeEmbedder:
        def embed(self, texts: tuple[str, ...]) -> np.ndarray:
            return np.zeros((len(texts), 16), dtype=np.float32)

    with pytest.raises(RagV2BgeMaterializationError, match="BGE_EMBEDDING_CONTRACT"):
        materialize_owner_bge_document(
            parser=_FixtureParser(_document_ir()),
            tokenizer=_FixtureTokenizer(),
            embedder=_WrongShapeEmbedder(),
            request=_request(tmp_path),
        )


def test_owner_voyage_preparation_parses_once_and_stops_before_embedding_transport(
    tmp_path: Path,
) -> None:
    parser = _FixtureParser(_document_ir(external_llm_eligible=True))
    request = replace(
        _request(tmp_path),
        embedding_profile_id="voyage_context_4_1024_v1",
    )

    prepared = rag_v2_bge_materializer.prepare_owner_document_for_embedding(
        parser=parser,
        tokenizer=_FixtureTokenizer(),
        request=request,
        external_processing_authorized=True,
    )

    assert len(parser.calls) == 1
    assert len(prepared.embedding_inputs) == len(prepared.document.chunks) == 1
    assert prepared.document.external_processing_eligible is True
    assert prepared.embedding_inputs[0].embedding_profile_id == "voyage_context_4_1024_v1"


def test_owner_voyage_preparation_rejects_secret_or_prompt_injection_before_transport(
    tmp_path: Path,
) -> None:
    unsafe_ir = _document_ir(external_llm_eligible=False)
    unsafe_ir["safetyClassification"] = {
        "externalLlmEligible": False,
        "piiDetected": False,
        "promptInjectionDetected": False,
        "secretDetected": True,
    }
    parser = _FixtureParser(unsafe_ir)

    with pytest.raises(RagV2BgeMaterializationError, match="OWNER_VOYAGE_DOCUMENT_UNSAFE"):
        rag_v2_bge_materializer.prepare_owner_document_for_embedding(
            parser=parser,
            tokenizer=_FixtureTokenizer(),
            request=replace(
                _request(tmp_path),
                embedding_profile_id="voyage_context_4_1024_v1",
            ),
            external_processing_authorized=True,
        )

    assert len(parser.calls) == 1


def _request(root: Path) -> RagV2OwnerDocumentRequest:
    return RagV2OwnerDocumentRequest(
        approved_root=root,
        relative_path="owner/private.pdf",
        document_id="doc_owner_fixture_0001",
        source_id="src_owner_fixture_001",
        source_revision_id="srv_owner_fixture_001",
        language_tags=("en",),
        embedding_profile_id="bge_m3_local_1024_v1",
    )


def _document_ir(*, external_llm_eligible: bool = False) -> dict[str, object]:
    raw = "a" * 64
    normalized = "b" * 64
    return {
        "blocks": [
            {
                "blockType": "PARAGRAPH",
                "locator": {"page": 1},
                "ocrConfidence": None,
                "readingOrder": 1,
                "text": "Local evidence stays in the owner-scoped generation only.",
            }
        ],
        "contractId": "rag-document-ir-v1",
        "documentIrVersion": 1,
        "normalizedContentSha256": normalized,
        "rawContentSha256": raw,
        "safetyClassification": {
            "externalLlmEligible": external_llm_eligible,
            "piiDetected": False,
            "promptInjectionDetected": False,
            "secretDetected": False,
        },
        "sourceId": "src_owner_fixture_001",
        "sourceRevisionId": "srv_owner_fixture_001",
    }
