from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.rag.exact30_source_card_parser import (
    EXACT30_LANGUAGE_TAGS,
    Exact30SourceCardDocumentParser,
    Exact30SourceCardParserError,
    exact30_document_id,
    exact30_source_revision_id,
)
from app.rag.source_card_corpus import (
    S4_7B_SOURCE_CARD_ROOT,
    load_frozen_source_card_corpus,
)


def test_exact30_parser_binds_safe_card_bytes_but_excludes_front_matter_from_ir() -> None:
    corpus = load_frozen_source_card_corpus()
    card = corpus.cards[0]
    parser = Exact30SourceCardDocumentParser(corpus=corpus)

    document_ir = parser.parse_approved_document(
        approved_root=S4_7B_SOURCE_CARD_ROOT,
        relative_path=Path(card.relative_path).name,
        source_id=card.source_id,
        source_revision_id=exact30_source_revision_id(card),
        language_tags=EXACT30_LANGUAGE_TAGS,
    )

    assert document_ir["sourceId"] == card.source_id
    assert document_ir["sourceRevisionId"] == exact30_source_revision_id(card)
    assert document_ir["rawContentSha256"] == card.content_sha256
    assert document_ir["mimeType"] == "text/markdown"
    # exact-30 card 본문은 tracked Markdown의 native projection이므로 shared IR enum 밖 값을 만들면 안 된다.
    assert document_ir["extractionMode"] == "NATIVE"
    assert document_ir["safetyClassification"] == {
        "externalLlmEligible": False,
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
    assert str(S4_7B_SOURCE_CARD_ROOT) not in serialized
    assert '"rawPath"' not in serialized
    assert exact30_document_id(card).startswith("doc_exact30_")


def test_exact30_parser_rejects_root_locator_revision_language_and_membership_drift() -> None:
    corpus = load_frozen_source_card_corpus()
    card = corpus.cards[0]
    parser = Exact30SourceCardDocumentParser(corpus=corpus)
    with pytest.raises(Exact30SourceCardParserError, match="EXACT30_CARD_ROOT_DRIFT"):
        parser.parse_approved_document(
            approved_root=Path("/tmp"),
            relative_path=Path(card.relative_path).name,
            source_id=card.source_id,
            source_revision_id=exact30_source_revision_id(card),
            language_tags=EXACT30_LANGUAGE_TAGS,
        )

    with pytest.raises(Exact30SourceCardParserError, match="EXACT30_CARD_LOCATOR_DRIFT"):
        parser.parse_approved_document(
            approved_root=S4_7B_SOURCE_CARD_ROOT,
            relative_path="card.md",
            source_id=card.source_id,
            source_revision_id=exact30_source_revision_id(card),
            language_tags=EXACT30_LANGUAGE_TAGS,
        )

    with pytest.raises(Exact30SourceCardParserError, match="EXACT30_CARD_REVISION_DRIFT"):
        parser.parse_approved_document(
            approved_root=S4_7B_SOURCE_CARD_ROOT,
            relative_path=Path(card.relative_path).name,
            source_id=card.source_id,
            source_revision_id="srv_exact30_drift",
            language_tags=EXACT30_LANGUAGE_TAGS,
        )

    with pytest.raises(Exact30SourceCardParserError, match="EXACT30_CARD_LANGUAGE_DRIFT"):
        parser.parse_approved_document(
            approved_root=S4_7B_SOURCE_CARD_ROOT,
            relative_path=Path(card.relative_path).name,
            source_id=card.source_id,
            source_revision_id=exact30_source_revision_id(card),
            language_tags=("en",),
        )

    with pytest.raises(Exact30SourceCardParserError, match="EXACT30_CARD_MEMBERSHIP"):
        parser.parse_approved_document(
            approved_root=S4_7B_SOURCE_CARD_ROOT,
            relative_path=Path(card.relative_path).name,
            source_id="src_unknown_card_001",
            source_revision_id=exact30_source_revision_id(card),
            language_tags=EXACT30_LANGUAGE_TAGS,
        )
