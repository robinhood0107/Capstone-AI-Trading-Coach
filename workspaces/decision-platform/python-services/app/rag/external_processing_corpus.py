from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from app.rag.safe_io import RagSafeIoError, read_approved_regular_file
from app.rag.source_card_corpus import (
    EXPECTED_FINANCE_SOURCE_IDS,
    EXPECTED_OFFICIAL_SOURCE_IDS,
    MAX_CORPUS_MANIFEST_BYTES,
    MAX_SOURCE_CARD_BYTES,
    REPO_ROOT,
    FrozenSourceCard,
    FrozenSourceCardCorpus,
    load_frozen_source_card_corpus,
    parse_source_card_v2_markdown,
)

S4_7C_APPROVAL_ID = "AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731"
S4_7C_PROFILE_ID = "s4_7c_external_v1"
S4_7C_SOURCE_CARD_ROOT = REPO_ROOT / "capstone-rag/source-cards/s4-7c-external"
S4_7C_CORPUS_MANIFEST_PATH = (
    REPO_ROOT / "capstone-rag/manifests/s4-7c-project-source-cards-30-external.v1.json"
)
S4_7C_REVIEWED_AT = "2026-08-01T00:00:00Z"
S4_7B_MANIFEST_FILE_SHA256 = "d772ab9a54c5477afeccfd41cd41e496645967dee59dd50c0bcc304ae3c95558"
S4_7B_CORPUS_MANIFEST_SHA256 = "7f2b4d72dcbaccf57cbe49a980973b17b4a9bfd85bec4694fd66fd7fd2a9decd"
_GENERATION_ID = re.compile(r"^rag_gen_[0-9a-f]{32}$")


class ExternalProcessingCorpusError(ValueError):
    """S4.7C membership·receipt·profile 또는 provider projection drift를 보고한다."""


@dataclass(frozen=True)
class ExternalContextCandidate:
    """authorized retrieval이 외부처리 직전에 전달하는 bounded current-revision projection이다."""

    source_id: str
    card_id: str
    canonical_content: str
    source_revision_current: bool
    chunk_revision_current: bool
    verified_source_check: bool
    generation_id: str


@dataclass(frozen=True)
class ProviderContextProjection:
    """question과 분리된 external-safe top-5 card context와 deterministic hash다."""

    items: tuple[Mapping[str, object], ...]
    context_set_hash: str
    corpus_manifest_sha256: str


def build_external_processing_manifest(
    *, card_root: Path = S4_7C_SOURCE_CARD_ROOT
) -> dict[str, Any]:
    """새 root의 exact 30을 old body와 대조해 deterministic receipt manifest를 만든다."""

    old = load_frozen_source_card_corpus()
    cards = _load_external_cards(card_root)
    _validate_external_cards(cards, old.cards)
    identity = {
        "schemaVersion": "1",
        "profileId": S4_7C_PROFILE_ID,
        "orderedCards": [
            {"sourceId": card.source_id, "cardSha256": card.card_sha256} for card in cards
        ],
    }
    corpus_hash = _sha256(_canonical_json_bytes(identity))
    receipts = [
        _receipt(old_card=old_card, new_card=new_card)
        for old_card, new_card in zip(old.cards, cards, strict=True)
    ]
    return {
        "schemaVersion": "1",
        "corpusId": "s4-7c-project-source-cards-30-external",
        "profileId": S4_7C_PROFILE_ID,
        "status": "FROZEN_EXTERNAL",
        "producer": "app.rag.external_processing_corpus",
        "ordering": "UTF8_NFC_SOURCE_ID_BYTES",
        "parserVersion": old.manifest["parserVersion"],
        "chunkerVersion": old.manifest["chunkerVersion"],
        "tokenizerSha256": old.manifest["tokenizerSha256"],
        "projectCards": 30,
        "externalProcessingCardCount": 30,
        "approvalId": S4_7C_APPROVAL_ID,
        "externalProcessingGate": "LICENSE_AND_CONSENT_VERIFIED",
        "rawOrReferenceEvidenceOutbound": 0,
        "oldProfileId": "s4_7b_internal_v1",
        "oldManifestFileSha256": S4_7B_MANIFEST_FILE_SHA256,
        "oldCorpusManifestSha256": S4_7B_CORPUS_MANIFEST_SHA256,
        "bodyHashRelationship": "EXACT_EQUAL_30_OF_30",
        "corpusManifestSha256": corpus_hash,
        "cards": [_card_projection(card) for card in cards],
        "licenseConsentReceipts": receipts,
    }


def load_external_processing_corpus(
    *,
    card_root: Path = S4_7C_SOURCE_CARD_ROOT,
    manifest_path: Path = S4_7C_CORPUS_MANIFEST_PATH,
) -> FrozenSourceCardCorpus:
    """tracked S4.7C manifest와 exact 30 external-enabled card가 같을 때만 corpus를 연다."""

    old = load_frozen_source_card_corpus()
    cards = _load_external_cards(card_root)
    _validate_external_cards(cards, old.cards)
    expected = build_external_processing_manifest(card_root=card_root)
    tracked = _read_manifest(manifest_path)
    if tracked != expected:
        raise ExternalProcessingCorpusError("CORPUS_PROFILE_MIXED: manifest or card bytes drifted")
    return FrozenSourceCardCorpus(
        cards=cards,
        manifest=MappingProxyType(tracked),
        corpus_manifest_sha256=str(tracked["corpusManifestSha256"]),
    )


def project_provider_context(
    *,
    corpus: FrozenSourceCardCorpus,
    candidates: tuple[ExternalContextCandidate, ...],
    active_generation_id: str,
    question_authorized: bool,
) -> ProviderContextProjection:
    """새 external corpus의 current top-5만 external-safe context로 투영한다.

    source-card consent와 별개인 question gate를 필수로 받고 user/account/order/secret 또는
    raw/reference bytes를 추가하지 않는다. 이 함수는 provider 호출을 수행하지 않는다.
    """

    if not question_authorized:
        raise ExternalProcessingCorpusError("QUESTION_GATE: question is not authorized")
    if _GENERATION_ID.fullmatch(active_generation_id) is None or not 1 <= len(candidates) <= 5:
        raise ExternalProcessingCorpusError("GENERATION_DRIFT: active generation is invalid")
    if corpus.manifest.get("profileId") != S4_7C_PROFILE_ID:
        raise ExternalProcessingCorpusError("CARD_NOT_EXTERNAL: external profile is required")
    by_source = {card.source_id: card for card in corpus.cards}
    projected: list[Mapping[str, object]] = []
    seen: set[str] = set()
    for candidate in candidates:
        card = by_source.get(candidate.source_id)
        if (
            card is None
            or candidate.source_id in seen
            or candidate.card_id != card.card_id
            or card.front_matter.get("externalProcessingAllowed") is not True
            or card.front_matter.get("externalProcessingGate") != "LICENSE_AND_CONSENT_VERIFIED"
            or card.front_matter.get("contentClass") != "PROJECT_AUTHORED_SANITIZED_CARD"
            or card.front_matter.get("accessLevel") != "PUBLIC"
            or card.front_matter.get("tier") != "PROJECT"
        ):
            raise ExternalProcessingCorpusError("CARD_NOT_EXTERNAL: card is not authorized")
        if (
            not candidate.source_revision_current
            or not candidate.chunk_revision_current
            or not candidate.verified_source_check
            or candidate.generation_id != active_generation_id
        ):
            raise ExternalProcessingCorpusError("GENERATION_DRIFT: candidate is not current")
        if candidate.canonical_content != card.canonical_body:
            raise ExternalProcessingCorpusError("CARD_NOT_EXTERNAL: content bytes drifted")
        seen.add(candidate.source_id)
        projected.append(
            MappingProxyType(
                {
                    "sourceId": card.source_id,
                    "cardId": card.card_id,
                    "content": card.canonical_body,
                    "contentSha256": card.body_sha256,
                    "accessLevel": "PUBLIC",
                    "tier": "PROJECT",
                }
            )
        )
    context_hash = _sha256(_canonical_json_bytes([dict(item) for item in projected]))
    return ProviderContextProjection(
        items=tuple(projected),
        context_set_hash=context_hash,
        corpus_manifest_sha256=corpus.corpus_manifest_sha256,
    )


def _load_external_cards(card_root: Path) -> tuple[FrozenSourceCard, ...]:
    try:
        entries = tuple(card_root.iterdir())
    except OSError as error:
        raise ExternalProcessingCorpusError("CORPUS_PROFILE_MIXED: card root missing") from error
    if any(entry.name.startswith(".") or entry.suffix != ".md" for entry in entries):
        raise ExternalProcessingCorpusError("CORPUS_PROFILE_MIXED: unexpected card artifact")
    cards: list[FrozenSourceCard] = []
    for entry in entries:
        try:
            result = read_approved_regular_file(
                approved_root=card_root,
                relative_path=entry.name,
                max_bytes=MAX_SOURCE_CARD_BYTES,
            )
            cards.append(
                parse_source_card_v2_markdown(
                    result.content,
                    relative_path=f"capstone-rag/source-cards/s4-7c-external/{entry.name}",
                )
            )
        except (RagSafeIoError, ValueError) as error:
            raise ExternalProcessingCorpusError(
                "CORPUS_PROFILE_MIXED: external card validation failed"
            ) from error
    return tuple(sorted(cards, key=lambda card: card.source_id.encode("utf-8")))


def _validate_external_cards(
    cards: tuple[FrozenSourceCard, ...],
    old_cards: tuple[FrozenSourceCard, ...],
) -> None:
    expected_ids = EXPECTED_FINANCE_SOURCE_IDS | EXPECTED_OFFICIAL_SOURCE_IDS
    if (
        len(cards) != 30
        or len(old_cards) != 30
        or {card.source_id for card in cards} != expected_ids
        or len({card.card_id for card in cards}) != 30
    ):
        raise ExternalProcessingCorpusError("CORPUS_PROFILE_MIXED: membership drifted")
    for old, new in zip(old_cards, cards, strict=True):
        payload = new.front_matter
        if (
            old.source_id != new.source_id
            or old.card_id != new.card_id
            or old.canonical_body != new.canonical_body
            or old.body_sha256 != new.body_sha256
            or payload.get("contentClass") != "PROJECT_AUTHORED_SANITIZED_CARD"
            or payload.get("accessLevel") != "PUBLIC"
            or payload.get("tier") != "PROJECT"
            or payload.get("externalProcessingAllowed") is not True
            or payload.get("externalProcessingGate") != "LICENSE_AND_CONSENT_VERIFIED"
        ):
            raise ExternalProcessingCorpusError("CORPUS_PROFILE_MIXED: card transition drifted")


def _receipt(*, old_card: FrozenSourceCard, new_card: FrozenSourceCard) -> dict[str, object]:
    payload = new_card.front_matter
    receipt: dict[str, object] = {
        "sourceId": new_card.source_id,
        "cardId": new_card.card_id,
        "contentClass": payload["contentClass"],
        "accessLevel": payload["accessLevel"],
        "projectAuthoredBody": True,
        "rawReferenceCopied": False,
        "providerPayloadCopied": False,
        "longQuotePresent": False,
        "publicLocatorOnly": str(payload["canonicalUrl"]).startswith("https://"),
        "attributionPresent": bool(payload["attribution"]),
        "licenseNoteReviewed": bool(payload["licenseNote"]),
        "thirdPartyRestrictionReviewed": True,
        "userConsentDecisionId": S4_7C_APPROVAL_ID,
        "reviewerDecision": "PASS",
        "reviewedAt": S4_7C_REVIEWED_AT,
        "oldContentSha256": old_card.content_sha256,
        "newContentSha256": new_card.content_sha256,
        "oldCardSha256": old_card.card_sha256,
        "newCardSha256": new_card.card_sha256,
        "oldBodySha256": old_card.body_sha256,
        "newBodySha256": new_card.body_sha256,
    }
    if new_card.source_id == "src_project_naver_news_discovery_boundary_001":
        receipt.update(
            {
                "projectAuthoredPolicyBoundaryOnly": True,
                "naverSearchResultMetadataCount": 0,
                "deletedSnapshotContentCount": 0,
                "externalProviderProcessesNaverApiResult": False,
            }
        )
    return receipt


def _card_projection(card: FrozenSourceCard) -> dict[str, object]:
    return {
        "sourceId": card.source_id,
        "cardId": card.card_id,
        "relativePath": card.relative_path,
        "contentSha256": card.content_sha256,
        "frontMatterSha256": card.front_matter_sha256,
        "bodySha256": card.body_sha256,
        "cardSha256": card.card_sha256,
        "contentClass": "PROJECT_AUTHORED_SANITIZED_CARD",
        "accessLevel": "PUBLIC",
        "externalProcessingAllowed": True,
        "externalProcessingGate": "LICENSE_AND_CONSENT_VERIFIED",
    }


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        result = read_approved_regular_file(
            approved_root=path.parent,
            relative_path=path.name,
            max_bytes=MAX_CORPUS_MANIFEST_BYTES,
        )
        text = result.content.decode("utf-8", errors="strict")
        if "\r" in text or unicodedata.normalize("NFC", text) != text:
            raise ExternalProcessingCorpusError("CORPUS_PROFILE_MIXED: manifest text drifted")
        value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (RagSafeIoError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExternalProcessingCorpusError("CORPUS_PROFILE_MIXED: manifest unreadable") from error
    if not isinstance(value, dict):
        raise ExternalProcessingCorpusError("CORPUS_PROFILE_MIXED: manifest shape drifted")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExternalProcessingCorpusError("CORPUS_PROFILE_MIXED: duplicate manifest key")
        result[key] = value
    return result


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
