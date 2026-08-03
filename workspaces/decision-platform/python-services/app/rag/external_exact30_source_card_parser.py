from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Final

from app.rag.external_processing_corpus import (
    S4_7C_PROFILE_ID,
    S4_7C_SOURCE_CARD_ROOT,
)
from app.rag.local_document_parser import DocumentParseError, LocalDocumentParser
from app.rag.source_card_corpus import FrozenSourceCard, FrozenSourceCardCorpus

EXTERNAL_EXACT30_LANGUAGE_TAGS: Final = ("ko",)
_PARSER_VERSION: Final = "1.0.0"
_PARSER_ARTIFACT_SHA256: Final = hashlib.sha256(
    b"capstone-s4-7d-external-exact30-source-card-document-parser-v1"
).hexdigest()
_EXPECTED_SAFETY: Final = {
    "externalLlmEligible": True,
    "piiDetected": False,
    "promptInjectionDetected": False,
    "secretDetected": False,
}


class ExternalExact30SourceCardParserError(ValueError):
    """S4.7C external-safe exact-30 card의 raw binding 또는 IR projection drift다."""


class ExternalExact30SourceCardDocumentParser:
    """S4.7C external-safe card만 path-free Document IR로 투영하는 parser boundary다.

    safe parser가 현재 tracked card bytes와 MIME/safety를 다시 bind한 뒤 external provider에 허용된
    project-authored canonical body만 남긴다. 이 adapter 자체는 network 또는 provider transport를 만들지 않는다.
    """

    def __init__(self, *, corpus: FrozenSourceCardCorpus) -> None:
        cards = tuple(corpus.cards)
        if (
            corpus.manifest.get("profileId") != S4_7C_PROFILE_ID
            or len(cards) != 30
            or len({card.source_id for card in cards}) != 30
            or any(
                card.front_matter.get("externalProcessingAllowed") is not True
                or card.front_matter.get("externalProcessingGate")
                != "LICENSE_AND_CONSENT_VERIFIED"
                or card.front_matter.get("contentClass")
                != "PROJECT_AUTHORED_SANITIZED_CARD"
                for card in cards
            )
        ):
            raise ExternalExact30SourceCardParserError("EXTERNAL_EXACT30_CARD_PROFILE_DRIFT")
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
        """one external-safe card의 tracked bytes를 recheck하고 canonical IR만 반환한다.

        filesystem locator는 safe reader 호출에만 쓰며 DB payload, receipt, history에 복사하지 않는다.
        S4.7B card나 raw/reference source는 이 entrypoint로 external embedding 후보가 될 수 없다.
        """

        card = self._cards_by_source_id.get(source_id)
        if card is None:
            raise ExternalExact30SourceCardParserError("EXTERNAL_EXACT30_CARD_MEMBERSHIP")
        if approved_root != S4_7C_SOURCE_CARD_ROOT:
            raise ExternalExact30SourceCardParserError("EXTERNAL_EXACT30_CARD_ROOT_DRIFT")
        expected_leaf = PurePosixPath(card.relative_path).name
        if relative_path != expected_leaf:
            raise ExternalExact30SourceCardParserError("EXTERNAL_EXACT30_CARD_LOCATOR_DRIFT")
        if source_revision_id != external_exact30_source_revision_id(card):
            raise ExternalExact30SourceCardParserError("EXTERNAL_EXACT30_CARD_REVISION_DRIFT")
        if language_tags != EXTERNAL_EXACT30_LANGUAGE_TAGS:
            raise ExternalExact30SourceCardParserError("EXTERNAL_EXACT30_CARD_LANGUAGE_DRIFT")

        try:
            parsed = self._safe_parser.parse_approved_document(
                approved_root=approved_root,
                relative_path=relative_path,
                source_id=source_id,
                source_revision_id=source_revision_id,
                language_tags=language_tags,
            )
        except DocumentParseError as error:
            raise ExternalExact30SourceCardParserError("EXTERNAL_EXACT30_CARD_SAFE_PARSE") from error
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
                "parserBackend": "capstone-external-exact30-source-card-parser",
                "parserVersion": _PARSER_VERSION,
            },
            "rawContentSha256": card.content_sha256,
            "safetyClassification": dict(_EXPECTED_SAFETY),
            "sourceId": source_id,
            "sourceRevisionId": source_revision_id,
        }


def external_exact30_source_revision_id(card: FrozenSourceCard) -> str:
    """S4.7C card bytes와 external-processing consent revision을 bind한 immutable ID다."""

    digest = hashlib.sha256(
        (
            f"{S4_7C_PROFILE_ID}\0{card.source_id}\0{card.card_sha256}"
            f"\0{card.content_sha256}"
        ).encode("utf-8")
    ).hexdigest()
    return f"srv_exact30_external_{digest[:32]}"


def external_exact30_document_id(card: FrozenSourceCard) -> str:
    """external-safe source revision과 분리된 stable document identity를 반환한다."""

    digest = hashlib.sha256(
        f"{S4_7C_PROFILE_ID}\0{card.source_id}\0{external_exact30_source_revision_id(card)}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"doc_exact30_external_{digest[:32]}"


def _validate_safe_parse(
    parsed: Mapping[str, Any],
    *,
    card: FrozenSourceCard,
    source_revision_id: str,
) -> None:
    """safe reader가 external card와 같은 byte/safety identity인지 provider 전송 전에 검증한다."""

    if (
        parsed.get("sourceId") != card.source_id
        or parsed.get("sourceRevisionId") != source_revision_id
        or parsed.get("rawContentSha256") != card.content_sha256
        or parsed.get("mimeType") != "text/markdown"
    ):
        raise ExternalExact30SourceCardParserError("EXTERNAL_EXACT30_CARD_RAW_DRIFT")
    safety = parsed.get("safetyClassification")
    if (
        not isinstance(safety, Mapping)
        or set(safety) != set(_EXPECTED_SAFETY)
        or any(type(safety.get(key)) is not bool for key in _EXPECTED_SAFETY)
        or dict(safety) != _EXPECTED_SAFETY
    ):
        raise ExternalExact30SourceCardParserError("EXTERNAL_EXACT30_CARD_SAFETY_DRIFT")
