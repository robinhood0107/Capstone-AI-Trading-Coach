from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.rag.external_exact30_source_card_parser import (
    EXTERNAL_EXACT30_LANGUAGE_TAGS,
    ExternalExact30SourceCardDocumentParser,
    ExternalExact30SourceCardParserError,
    external_exact30_document_id,
    external_exact30_source_revision_id,
)
from app.rag.external_processing_corpus import (
    S4_7C_SOURCE_CARD_ROOT,
    load_external_processing_corpus,
)


def test_external_exact30_parser_binds_external_card_bytes_and_excludes_front_matter() -> None:
    corpus = load_external_processing_corpus()
    card = corpus.cards[0]
    parser = ExternalExact30SourceCardDocumentParser(corpus=corpus)

    document_ir = parser.parse_approved_document(
        approved_root=S4_7C_SOURCE_CARD_ROOT,
        relative_path=Path(card.relative_path).name,
        source_id=card.source_id,
        source_revision_id=external_exact30_source_revision_id(card),
        language_tags=EXTERNAL_EXACT30_LANGUAGE_TAGS,
    )

    assert document_ir["sourceId"] == card.source_id
    assert document_ir["sourceRevisionId"] == external_exact30_source_revision_id(card)
    assert document_ir["rawContentSha256"] == card.content_sha256
    assert document_ir["mimeType"] == "text/markdown"
    assert document_ir["safetyClassification"] == {
        "externalLlmEligible": True,
        "piiDetected": False,
        "promptInjectionDetected": False,
        "secretDetected": False,
    }
    assert document_ir["blocks"] == [
        {
            "blockType": "PARAGRAPH",
            "locator": {"section": "source-card"},
            "ocrConfidence": None,
            "readingOrder": 1,
            "text": card.canonical_body.strip(),
        }
    ]
    serialized = json.dumps(document_ir, ensure_ascii=False, sort_keys=True)
    assert "schemaVersion:" not in serialized
    assert str(S4_7C_SOURCE_CARD_ROOT) not in serialized
    assert '"rawPath"' not in serialized
    assert external_exact30_document_id(card).startswith("doc_exact30_external_")


def test_external_exact30_parser_rejects_root_locator_revision_language_and_profile_drift() -> None:
    corpus = load_external_processing_corpus()
    card = corpus.cards[0]
    parser = ExternalExact30SourceCardDocumentParser(corpus=corpus)

    with pytest.raises(ExternalExact30SourceCardParserError, match="EXTERNAL_EXACT30_CARD_ROOT_DRIFT"):
        parser.parse_approved_document(
            approved_root=Path("/tmp"),
            relative_path=Path(card.relative_path).name,
            source_id=card.source_id,
            source_revision_id=external_exact30_source_revision_id(card),
            language_tags=EXTERNAL_EXACT30_LANGUAGE_TAGS,
        )

    with pytest.raises(ExternalExact30SourceCardParserError, match="EXTERNAL_EXACT30_CARD_LOCATOR_DRIFT"):
        parser.parse_approved_document(
            approved_root=S4_7C_SOURCE_CARD_ROOT,
            relative_path="card.md",
            source_id=card.source_id,
            source_revision_id=external_exact30_source_revision_id(card),
            language_tags=EXTERNAL_EXACT30_LANGUAGE_TAGS,
        )

    with pytest.raises(ExternalExact30SourceCardParserError, match="EXTERNAL_EXACT30_CARD_REVISION_DRIFT"):
        parser.parse_approved_document(
            approved_root=S4_7C_SOURCE_CARD_ROOT,
            relative_path=Path(card.relative_path).name,
            source_id=card.source_id,
            source_revision_id="srv_exact30_external_drift",
            language_tags=EXTERNAL_EXACT30_LANGUAGE_TAGS,
        )

    with pytest.raises(ExternalExact30SourceCardParserError, match="EXTERNAL_EXACT30_CARD_LANGUAGE_DRIFT"):
        parser.parse_approved_document(
            approved_root=S4_7C_SOURCE_CARD_ROOT,
            relative_path=Path(card.relative_path).name,
            source_id=card.source_id,
            source_revision_id=external_exact30_source_revision_id(card),
            language_tags=("en",),
        )

    with pytest.raises(ExternalExact30SourceCardParserError, match="EXTERNAL_EXACT30_CARD_MEMBERSHIP"):
        parser.parse_approved_document(
            approved_root=S4_7C_SOURCE_CARD_ROOT,
            relative_path=Path(card.relative_path).name,
            source_id="src_unknown_card_001",
            source_revision_id=external_exact30_source_revision_id(card),
            language_tags=EXTERNAL_EXACT30_LANGUAGE_TAGS,
        )
