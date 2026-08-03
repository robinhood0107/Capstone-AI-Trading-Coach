from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Mapping
from urllib.parse import urlsplit

from app.rag.authorized_retrieval import ALLOWED_RAG_TOPICS
from app.rag.oa_release_manifest import OA_TRACK_IDS
from app.rag.safe_io import RagSafeIoError, read_approved_regular_file

_MAX_REGISTRY_BYTES = 2_000_000
_REGISTRY_ID = re.compile(r"^[a-z][a-z0-9_-]{2,127}$")
_SOURCE_ID = re.compile(r"^src_[a-z0-9][a-z0-9_-]{2,95}$")
_SOURCE_REVISION_ID = re.compile(r"^srv_[a-z0-9][a-z0-9_-]{2,95}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LANGUAGE_TAG = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
_ROOT_FIELDS = frozenset(
    {
        "activeSourceCount",
        "activeSources",
        "automaticReservePromotion",
        "contractId",
        "registryDigest",
        "registryId",
        "reserveSourceCount",
        "reserveSources",
        "schemaVersion",
    }
)
_ENTRY_FIELDS = frozenset(
    {
        "languageTags",
        "retrievalTopics",
        "sourceCard",
        "sourceRevisionId",
        "trackId",
    }
)
_SOURCE_CARD_FIELDS = frozenset(
    {
        "accessEvidence",
        "activeOa112Eligible",
        "authors",
        "canonicalUrl",
        "canonicalUrlSha256",
        "contractId",
        "identifier",
        "licenseEvidenceDigest",
        "mimeType",
        "permissions",
        "rawContentSha256",
        "revision",
        "revisionDate",
        "schemaVersion",
        "sourceId",
        "sourceKind",
        "title",
    }
)
_ACCESS_EVIDENCE_FIELDS = frozenset(
    {"accessCheckedAt", "accessEvidenceDigest", "verificationState"}
)
_IDENTIFIER_FIELDS = frozenset({"scheme", "value"})
_PERMISSION_FIELDS = frozenset(
    {
        "externalEmbeddingAllowed",
        "externalGenerationAllowed",
        "localProcessingAllowed",
        "machineFetchAllowed",
    }
)
_MIME_TYPES = frozenset({"application/pdf", "text/html", "text/plain"})


class Oa112ActiveRegistryError(ValueError):
    """local-only OA112 activation registry가 권리·수량·경계 계약을 위반했음을 나타낸다."""


@dataclass(frozen=True, slots=True)
class Oa112RegistryEntry:
    """한 OA source의 content-free local activation metadata다.

    원문 bytes, local raw cache 위치, operator packet은 이 객체에 보관하지 않는다. downloader와
    materializer는 canonical URL과 검증된 digest만 사용해 안전한 local cache/file descriptor로
    이어진다.
    """

    source_id: str
    source_revision_id: str
    document_id: str
    track_id: str
    language_tags: tuple[str, ...]
    retrieval_topics: tuple[str, ...]
    source_card: dict[str, object]
    title: str
    canonical_url: str
    raw_content_sha256: str
    mime_type: str
    license_evidence_sha256: str
    access_evidence_sha256: str
    machine_fetch_allowed: bool
    local_processing_allowed: bool
    external_embedding_allowed: bool
    external_generation_allowed: bool


@dataclass(frozen=True, slots=True)
class Oa112ActiveRegistry:
    """정확히 14 track × 8 active source와 non-active reserve를 분리한 local registry다."""

    registry_id: str
    registry_digest: str
    active_entries: tuple[Oa112RegistryEntry, ...]
    reserve_entries: tuple[Oa112RegistryEntry, ...]

    @property
    def active_source_count(self) -> int:
        return len(self.active_entries)

    @property
    def reserve_source_count(self) -> int:
        return len(self.reserve_entries)

    @property
    def active_source_ids(self) -> tuple[str, ...]:
        return tuple(entry.source_id for entry in self.active_entries)

    @property
    def reserve_source_ids(self) -> tuple[str, ...]:
        return tuple(entry.source_id for entry in self.reserve_entries)

    @property
    def track_counts(self) -> dict[str, int]:
        return {
            track_id: sum(entry.track_id == track_id for entry in self.active_entries)
            for track_id in OA_TRACK_IDS
        }


def load_oa112_active_registry(
    *,
    approved_root: Path,
    relative_path: str,
) -> Oa112ActiveRegistry:
    """0600 local registry를 descriptor-safe read로 읽고 active OA112 selection을 검증한다.

    registry는 Git에 source metadata/raw corpus를 추가하는 경로가 아니다. approved root의 strict
    ownership/mode와 closed JSON shape를 모두 통과하지 않으면 DNS나 provider transport를 만들지
    않고 `OA112_REGISTRY_UNSAFE`로 닫는다.
    """

    if not _is_leaf(relative_path) or not _is_private_registry_root(approved_root):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_UNSAFE")
    try:
        result = read_approved_regular_file(
            approved_root=approved_root,
            relative_path=relative_path,
            max_bytes=_MAX_REGISTRY_BYTES,
        )
    except RagSafeIoError as error:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_UNSAFE") from error
    try:
        payload = _parse_registry_json(result.content)
        return _validate_registry(payload)
    except Oa112ActiveRegistryError:
        raise
    except (TypeError, ValueError) as error:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID") from error


def canonical_oa112_active_registry_digest(payload: Mapping[str, object]) -> str:
    """registryDigest self-reference를 제외한 canonical UTF-8 JSON SHA-256을 계산한다."""

    detached = json.loads(json.dumps(payload, ensure_ascii=False))
    if not isinstance(detached, dict):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    detached["registryDigest"] = None
    encoded = json.dumps(
        detached,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_registry_json(content: bytes) -> dict[str, object]:
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID") from error
    if (
        not text.endswith("\n")
        or "\r" in text
        or text.startswith("\ufeff")
        or unicodedata.normalize("NFC", text) != text
    ):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    try:
        parsed = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, TypeError) as error:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID") from error
    if not isinstance(parsed, dict):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    return parsed


def _validate_registry(payload: Mapping[str, object]) -> Oa112ActiveRegistry:
    _require_exact_keys(payload, _ROOT_FIELDS)
    if (
        payload.get("contractId") != "rag-v2-oa112-local-activation-registry-v1"
        or payload.get("schemaVersion") != 1
        or payload.get("activeSourceCount") != 112
        or payload.get("automaticReservePromotion") is not False
    ):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    registry_id = _required_text(payload, "registryId", maximum=128)
    if _REGISTRY_ID.fullmatch(registry_id) is None:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    registry_digest = _required_sha256(payload, "registryDigest")
    if registry_digest != canonical_oa112_active_registry_digest(payload):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_DIGEST_DRIFT")

    active_raw = payload.get("activeSources")
    reserve_raw = payload.get("reserveSources")
    reserve_count = payload.get("reserveSourceCount")
    if (
        not isinstance(active_raw, list)
        or len(active_raw) != 112
        or not isinstance(reserve_raw, list)
        or type(reserve_count) is not int
        or reserve_count != len(reserve_raw)
        or not 0 <= reserve_count <= 28
    ):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")

    active_entries = tuple(
        _parse_entry(value, active=True)
        for value in active_raw
    )
    reserve_entries = tuple(
        _parse_entry(value, active=False)
        for value in reserve_raw
    )
    _validate_active_distribution(active_entries)
    _validate_unique_identities(active_entries, reserve_entries)
    if tuple(entry.source_id for entry in reserve_entries) != tuple(
        sorted((entry.source_id for entry in reserve_entries), key=lambda value: value.encode("utf-8"))
    ):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_ORDER_INVALID")
    return Oa112ActiveRegistry(
        registry_id=registry_id,
        registry_digest=registry_digest,
        active_entries=active_entries,
        reserve_entries=reserve_entries,
    )


def _parse_entry(value: object, *, active: bool) -> Oa112RegistryEntry:
    if not isinstance(value, Mapping):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    _require_exact_keys(value, _ENTRY_FIELDS)
    track_id = _required_text(value, "trackId", maximum=128)
    if track_id not in OA_TRACK_IDS:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    source_revision_id = _required_text(value, "sourceRevisionId", maximum=128)
    if _SOURCE_REVISION_ID.fullmatch(source_revision_id) is None:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    language_tags = _text_array(value.get("languageTags"), maximum=8, pattern=_LANGUAGE_TAG)
    retrieval_topics = _retrieval_topics(value.get("retrievalTopics"))
    source_card = _parse_source_card(value.get("sourceCard"), active=active)
    source_id = _required_text(source_card, "sourceId", maximum=128)
    permissions = source_card["permissions"]
    assert isinstance(permissions, Mapping)
    return Oa112RegistryEntry(
        source_id=source_id,
        source_revision_id=source_revision_id,
        document_id=_document_id(source_id=source_id, source_revision_id=source_revision_id),
        track_id=track_id,
        language_tags=language_tags,
        retrieval_topics=retrieval_topics,
        source_card=json.loads(json.dumps(source_card, ensure_ascii=False)),
        title=_required_text(source_card, "title", maximum=500),
        canonical_url=_required_text(source_card, "canonicalUrl", maximum=2_048),
        raw_content_sha256=_required_sha256(source_card, "rawContentSha256"),
        mime_type=_required_text(source_card, "mimeType", maximum=128),
        license_evidence_sha256=_required_sha256(source_card, "licenseEvidenceDigest"),
        access_evidence_sha256=_required_sha256(
            _required_mapping(source_card, "accessEvidence"),
            "accessEvidenceDigest",
        ),
        machine_fetch_allowed=permissions["machineFetchAllowed"] is True,
        local_processing_allowed=permissions["localProcessingAllowed"] is True,
        external_embedding_allowed=permissions["externalEmbeddingAllowed"] is True,
        external_generation_allowed=permissions["externalGenerationAllowed"] is True,
    )


def _parse_source_card(value: object, *, active: bool) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    _require_exact_keys(value, _SOURCE_CARD_FIELDS)
    if (
        value.get("contractId") != "rag-source-card-v4"
        or value.get("schemaVersion") != 4
        or value.get("sourceKind") != "OPEN_ACCESS_DOCUMENT"
        or value.get("mimeType") not in _MIME_TYPES
        or value.get("activeOa112Eligible") is not active
    ):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    source_id = _required_text(value, "sourceId", maximum=128)
    if _SOURCE_ID.fullmatch(source_id) is None:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    _required_text(value, "title", maximum=500)
    authors = _text_array(value.get("authors"), maximum=50, pattern=None)
    if any(len(author) > 300 for author in authors):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    canonical_url = _required_text(value, "canonicalUrl", maximum=2_048)
    _validate_public_https_url(canonical_url)
    if hashlib.sha256(canonical_url.encode("utf-8")).hexdigest() != _required_sha256(
        value,
        "canonicalUrlSha256",
    ):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    identifier = _required_mapping(value, "identifier")
    _require_exact_keys(identifier, _IDENTIFIER_FIELDS)
    if identifier.get("scheme") not in {"DOI", "ISBN", "ARXIV"}:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    _required_text(identifier, "value", maximum=256)
    _required_text(value, "revision", maximum=128)
    _required_date(value, "revisionDate")
    _required_sha256(value, "rawContentSha256")
    _required_sha256(value, "licenseEvidenceDigest")
    access = _required_mapping(value, "accessEvidence")
    _require_exact_keys(access, _ACCESS_EVIDENCE_FIELDS)
    if access.get("verificationState") != "VERIFIED":
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    _required_utc_datetime(access, "accessCheckedAt")
    _required_sha256(access, "accessEvidenceDigest")
    permissions = _required_mapping(value, "permissions")
    _require_exact_keys(permissions, _PERMISSION_FIELDS)
    if any(type(permissions[key]) is not bool for key in _PERMISSION_FIELDS):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    if active and not all(permissions[key] is True for key in _PERMISSION_FIELDS):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_RIGHTS_REQUIRED")
    return dict(value)


def _validate_active_distribution(entries: tuple[Oa112RegistryEntry, ...]) -> None:
    expected_tracks = tuple(
        track_id
        for track_id in OA_TRACK_IDS
        for _ in range(8)
    )
    if tuple(entry.track_id for entry in entries) != expected_tracks:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_TRACK_DISTRIBUTION")
    for track_id in OA_TRACK_IDS:
        if sum(entry.track_id == track_id for entry in entries) != 8:
            raise Oa112ActiveRegistryError("OA112_REGISTRY_TRACK_DISTRIBUTION")
    current_index = 0
    for _track_id in OA_TRACK_IDS:
        source_ids = tuple(entry.source_id for entry in entries[current_index : current_index + 8])
        if source_ids != tuple(sorted(source_ids, key=lambda value: value.encode("utf-8"))):
            raise Oa112ActiveRegistryError("OA112_REGISTRY_ORDER_INVALID")
        current_index += 8


def _validate_unique_identities(
    active_entries: tuple[Oa112RegistryEntry, ...],
    reserve_entries: tuple[Oa112RegistryEntry, ...],
) -> None:
    entries = active_entries + reserve_entries
    for values in (
        [entry.source_id for entry in entries],
        [entry.source_revision_id for entry in entries],
        [entry.canonical_url for entry in entries],
        [entry.document_id for entry in entries],
    ):
        if len(values) != len(set(values)):
            raise Oa112ActiveRegistryError("OA112_REGISTRY_IDENTITY_DUPLICATE")


def _document_id(*, source_id: str, source_revision_id: str) -> str:
    identity = hashlib.sha256(f"oa112\0{source_id}\0{source_revision_id}".encode("utf-8")).hexdigest()
    return f"doc_oa_{identity[:32]}"


def _text_array(
    value: object,
    *,
    maximum: int,
    pattern: re.Pattern[str] | None,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    if any(not isinstance(item, str) or not item or item != item.strip() for item in value):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    if len(value) != len(set(value)):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    if pattern is not None and any(pattern.fullmatch(item) is None for item in value):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    ordered = tuple(sorted(value, key=lambda item: item.encode("utf-8")))
    if tuple(value) != ordered:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_ORDER_INVALID")
    return ordered


def _retrieval_topics(value: object) -> tuple[str, ...]:
    topics = _text_array(value, maximum=len(ALLOWED_RAG_TOPICS), pattern=None)
    if not set(topics) <= ALLOWED_RAG_TOPICS:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    return topics


def _validate_public_https_url(value: str) -> None:
    if (
        not value
        or any(character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F for character in value)
        or "\\" in value
    ):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID") from error
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
        or not parsed.path.startswith("/")
        or "//" in parsed.path
        or any(segment in {".", ".."} for segment in parsed.path.split("/"))
        or hostname in {"localhost", "0.0.0.0", "::1"}
        or hostname.endswith((".localhost", ".local", ".internal", ".home.arpa"))
        or re.fullmatch(r"[0-9.]+", hostname) is not None
    ):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")


def _required_mapping(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    nested = value.get(key)
    if not isinstance(nested, Mapping):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    return nested


def _required_text(value: Mapping[str, object], key: str, *, maximum: int) -> str:
    item = value.get(key)
    if (
        not isinstance(item, str)
        or not item
        or item != item.strip()
        or len(item) > maximum
        or unicodedata.normalize("NFC", item) != item
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in item)
    ):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    return item


def _required_sha256(value: Mapping[str, object], key: str) -> str:
    item = _required_text(value, key, maximum=64)
    if _SHA256.fullmatch(item) is None:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    return item


def _required_date(value: Mapping[str, object], key: str) -> None:
    item = _required_text(value, key, maximum=10)
    try:
        parsed = datetime.fromisoformat(f"{item}T00:00:00+00:00")
    except ValueError as error:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID") from error
    if parsed.strftime("%Y-%m-%d") != item:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")


def _required_utc_datetime(value: Mapping[str, object], key: str) -> None:
    item = _required_text(value, key, maximum=32)
    if not item.endswith("Z"):
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
    try:
        parsed = datetime.fromisoformat(item.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID") from error
    if parsed.tzinfo != UTC or parsed.isoformat().replace("+00:00", "Z") != item:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")


def _require_exact_keys(value: Mapping[str, object], keys: frozenset[str]) -> None:
    if set(value) != keys:
        raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise Oa112ActiveRegistryError("OA112_REGISTRY_INVALID")
        result[key] = value
    return result


def _is_leaf(relative_path: str) -> bool:
    if not relative_path or "\\" in relative_path or "\x00" in relative_path:
        return False
    path = PurePosixPath(relative_path)
    return (
        len(path.parts) == 1
        and path.name not in {"", ".", ".."}
        and not relative_path.startswith("/")
    )


def _is_private_registry_root(root: Path) -> bool:
    """OA rights registry는 public worktree가 아닌 owner-only local root에서만 읽는다."""

    try:
        metadata = root.lstat()
    except OSError:
        return False
    return (
        root.is_absolute()
        and ".." not in root.parts
        and stat.S_ISDIR(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and metadata.st_uid == os.getuid()
        and stat.S_IMODE(metadata.st_mode) == 0o700
    )
