from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Final

from app.rag.local_document_parser import DocumentParseError, LocalDocumentParser
from app.rag.source_card_corpus import (
    S4_7B_SOURCE_CARD_ROOT,
    FrozenSourceCard,
    FrozenSourceCardCorpus,
)

EXACT30_LANGUAGE_TAGS: Final = ("ko",)
_PARSER_VERSION: Final = "1.0.0"
_PARSER_ARTIFACT_SHA256: Final = hashlib.sha256(
    b"capstone-s4-7d-exact30-source-card-document-parser-v1"
).hexdigest()
_EXPECTED_SAFETY: Final = {
    "externalLlmEligible": False,
    "piiDetected": False,
    "promptInjectionDetected": False,
    "secretDetected": False,
}


class Exact30SourceCardParserError(ValueError):
    """frozen exact-30 card의 raw binding 또는 sanitized IR projection drift다."""


class Exact30SourceCardDocumentParser:
    """frozen exact-30 card만 single-locator Document IR로 투영하는 parser boundary다.

    기존 안전 parser가 실제 tracked card bytes와 MIME/safety를 다시 bind한 뒤, front matter는
    embedding input에서 제외하고 source-card corpus가 이미 검증한 canonical body만 사용한다.
    이 adapter는 network/provider transport를 만들지 않는다.
    """

    def __init__(self, *, corpus: FrozenSourceCardCorpus) -> None:
        cards = tuple(corpus.cards)
        if len(cards) != 30 or len({card.source_id for card in cards}) != 30:
            raise Exact30SourceCardParserError("EXACT30_CARD_MEMBERSHIP")
        self._cards_by_source_id = {card.source_id: card for card in cards}
        self._safe_parser = LocalDocumentParser()

    def parse_approved_document(
        self,
        *,
        approved_root: Path,
        relative_path: str,
        source_id: str,
        source_revision_id: str,
        language_tags: tuple[str, ...],
    ) -> dict[str, object]:
        """one frozen card의 current tracked bytes를 verify하고 path-free canonical IR만 반환한다.

        `approved_root`/`relative_path`는 safe reader 호출에만 쓰며 return value, DB payload, receipt에
        복사하지 않는다. 같은 card가 수정·교체되면 raw hash 또는 canonical corpus drift에서 embed 전에
        실패한다.
        """

        card = self._cards_by_source_id.get(source_id)
        if card is None:
            raise Exact30SourceCardParserError("EXACT30_CARD_MEMBERSHIP")
        if approved_root != S4_7B_SOURCE_CARD_ROOT:
            raise Exact30SourceCardParserError("EXACT30_CARD_ROOT_DRIFT")
        expected_leaf = PurePosixPath(card.relative_path).name
        if relative_path != expected_leaf:
            raise Exact30SourceCardParserError("EXACT30_CARD_LOCATOR_DRIFT")
        if source_revision_id != exact30_source_revision_id(card):
            raise Exact30SourceCardParserError("EXACT30_CARD_REVISION_DRIFT")
        if language_tags != EXACT30_LANGUAGE_TAGS:
            raise Exact30SourceCardParserError("EXACT30_CARD_LANGUAGE_DRIFT")

        try:
            parsed = self._safe_parser.parse_approved_document(
                approved_root=approved_root,
                relative_path=relative_path,
                source_id=source_id,
                source_revision_id=source_revision_id,
                language_tags=language_tags,
            )
        except DocumentParseError as error:
            raise Exact30SourceCardParserError("EXACT30_CARD_SAFE_PARSE") from error
        _validate_safe_parse(parsed, card=card, source_revision_id=source_revision_id)

        blocks: list[dict[str, object]] = [
            {
                "blockType": "PARAGRAPH",
                "locator": {"section": "source-card"},
                "ocrConfidence": None,
                "readingOrder": 1,
                "text": card.canonical_body.strip(),
            }
        ]
        normalized = json.dumps(
            blocks,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return {
            "blocks": blocks,
            "contractId": "rag-document-ir-v1",
            "documentIrVersion": 1,
            "extractionMode": "SOURCE_CARD_CANONICAL",
            "languageTags": list(language_tags),
            "mimeType": "text/markdown",
            "normalizedContentSha256": hashlib.sha256(normalized).hexdigest(),
            "parserEvidence": {
                "ocr": {
                    "backend": "NOT_USED",
                    "backendVersion": None,
                    "modelSha256": None,
                },
                "parserArtifactSha256": _PARSER_ARTIFACT_SHA256,
                "parserBackend": "capstone-exact30-source-card-parser",
                "parserVersion": _PARSER_VERSION,
            },
            "rawContentSha256": card.content_sha256,
            "safetyClassification": dict(_EXPECTED_SAFETY),
            "sourceId": source_id,
            "sourceRevisionId": source_revision_id,
        }


def exact30_source_revision_id(card: FrozenSourceCard) -> str:
    """source bytes/card semantics가 바뀔 때만 변하는 immutable exact-30 revision ID다."""

    digest = hashlib.sha256(
        f"{card.source_id}\0{card.card_sha256}\0{card.content_sha256}".encode("utf-8")
    ).hexdigest()
    return f"srv_exact30_{digest[:32]}"


def exact30_document_id(card: FrozenSourceCard) -> str:
    """card revision과 분리된 stable exact-30 document identity를 반환한다."""

    digest = hashlib.sha256(
        f"{card.source_id}\0{exact30_source_revision_id(card)}".encode("utf-8")
    ).hexdigest()
    return f"doc_exact30_{digest[:32]}"


def _validate_safe_parse(
    parsed: Mapping[str, Any],
    *,
    card: FrozenSourceCard,
    source_revision_id: str,
) -> None:
    """safe reader 결과가 frozen card와 같은 byte/safety identity인지 embed 전에 검증한다."""

    if (
        parsed.get("sourceId") != card.source_id
        or parsed.get("sourceRevisionId") != source_revision_id
        or parsed.get("rawContentSha256") != card.content_sha256
        or parsed.get("mimeType") != "text/markdown"
    ):
        raise Exact30SourceCardParserError("EXACT30_CARD_RAW_DRIFT")
    safety = parsed.get("safetyClassification")
    if (
        not isinstance(safety, Mapping)
        or set(safety) != set(_EXPECTED_SAFETY)
        or any(type(safety.get(key)) is not bool for key in _EXPECTED_SAFETY)
        or safety.get("piiDetected") is not False
        or safety.get("promptInjectionDetected") is not False
        or safety.get("secretDetected") is not False
    ):
        raise Exact30SourceCardParserError("EXACT30_CARD_SAFETY_DRIFT")
