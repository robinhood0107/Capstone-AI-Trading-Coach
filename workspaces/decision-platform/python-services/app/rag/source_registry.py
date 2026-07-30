from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
from urllib.parse import SplitResult, urlsplit

import yaml

SOURCE_ID_PATTERN = re.compile(r"^src_[a-z0-9]+_[a-z0-9_]+_[0-9]{3}$")
RAG_SOURCE_OWNER = "python-rag-corpus-privacy"
ALLOWED_ACCESS_LEVELS = frozenset({"PUBLIC", "INTERNAL"})
ALLOWED_INITIAL_PROCESSING = frozenset({"REFERENCE_ONLY"})
ALLOWED_LICENSE_DECISIONS = frozenset(
    {
        "REFERENCE_ONLY_NO_EXTERNAL_PROCESSING",
        "REFERENCE_ONLY_TERMS_RESTRICTED",
        "REFERENCE_ONLY_LICENSE_UNSPECIFIED",
    }
)


class RagSourceRegistryError(ValueError):
    """RAG source seed가 S4.1 source registry 보안 계약을 위반할 때 발생한다."""


@dataclass(frozen=True)
class RagSourceLocator:
    """fetch가 아니라 fixed origin/path allowlist 검증에만 쓰는 canonical HTTPS locator."""

    canonical_url: str
    allowed_origin: str
    allowed_path: str


@dataclass(frozen=True)
class RagSourceRetention:
    """upstream raw body를 보존하지 않는 reference metadata retention 계약."""

    mode: str
    days: int
    owner: str


@dataclass(frozen=True)
class RagSourceDefinition:
    """P0 upstream reference source.

    이 정의는 임베딩 corpus가 아니며, source card 작성과 변경 감지를 위한 lineage만 제공한다.
    """

    source_id: str
    title: str
    institution: str
    topic: str
    sequence: int
    source_type: str
    tier: str
    access_level: str
    license_decision: str
    external_processing_allowed: bool
    initial_processing: str
    owner: str
    attribution: str
    locator: RagSourceLocator
    retention: RagSourceRetention


@dataclass(frozen=True)
class RagSourceRegistry:
    """strict YAML seed를 immutable source-id mapping으로 노출한다."""

    schema_version: str
    registry_version: str
    generated_at: datetime
    sources: Mapping[str, RagSourceDefinition]
    seed_path: Path


def load_default_source_registry() -> RagSourceRegistry:
    """package에 commit된 S4.1 P0 seed를 읽으며 provider key나 network를 보지 않는다."""

    seed = files("app.rag").joinpath("rag_source_seed.yaml")
    return load_source_registry(Path(str(seed)))


def load_source_registry(path: Path) -> RagSourceRegistry:
    """unknown field, unsafe URL, duplicate ID, external-processing drift를 fail-closed한다."""

    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        raise RagSourceRegistryError("RAG source seed YAML is invalid.") from None
    if not isinstance(payload, dict):
        raise RagSourceRegistryError("RAG source seed root must be an object.")
    _require_exact_fields(
        payload,
        {"schemaVersion", "registryVersion", "generatedAt", "sources"},
        "registry",
    )
    schema_version = _required_text(payload, "schemaVersion")
    if schema_version != "1":
        raise RagSourceRegistryError("RAG source seed schema version is unsupported.")
    registry_version = _required_text(payload, "registryVersion")
    generated_at = _required_utc_datetime(payload, "generatedAt")
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list) or len(raw_sources) != 20:
        raise RagSourceRegistryError("S4.1 P0 source seed must contain exactly 20 sources.")

    sources: dict[str, RagSourceDefinition] = {}
    for raw_source in raw_sources:
        source = _parse_source(raw_source)
        if source.source_id in sources:
            raise RagSourceRegistryError("duplicate RAG sourceId")
        sources[source.source_id] = source
    return RagSourceRegistry(
        schema_version=schema_version,
        registry_version=registry_version,
        generated_at=generated_at,
        sources=MappingProxyType(sources),
        seed_path=path,
    )


def validate_resolved_addresses(hostname: str, addresses: list[str]) -> None:
    """DNS rebinding 방어용 post-resolution 검증.

    실제 fetch 구현은 connect 직전 이 함수를 호출해 모든 resolved peer가 global address인지 확인한다.
    """

    if not hostname.strip() or not addresses:
        raise RagSourceRegistryError("resolved DNS address set is empty.")
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as error:
            raise RagSourceRegistryError("resolved DNS address is invalid.") from error
        if not parsed.is_global:
            raise RagSourceRegistryError("resolved DNS address is not globally routable.")


def _parse_source(value: object) -> RagSourceDefinition:
    if not isinstance(value, dict):
        raise RagSourceRegistryError("RAG source entry must be an object.")
    _require_exact_fields(
        value,
        {
            "accessLevel",
            "attribution",
            "externalProcessingAllowed",
            "initialProcessing",
            "institution",
            "licenseDecision",
            "locator",
            "owner",
            "retention",
            "sequence",
            "sourceId",
            "sourceType",
            "tier",
            "title",
            "topic",
        },
        "source",
    )
    source_id = _required_text(value, "sourceId")
    if not SOURCE_ID_PATTERN.fullmatch(source_id):
        raise RagSourceRegistryError("RAG sourceId does not match src_<기관>_<주제>_<seq>.")
    institution = _required_text(value, "institution")
    topic = _required_text(value, "topic")
    sequence = value.get("sequence")
    if type(sequence) is not int or sequence <= 0 or sequence > 999:
        raise RagSourceRegistryError("RAG source sequence must be 1..999.")
    _validate_source_id_components(source_id, institution, topic, sequence)
    access_level = _required_enum(value, "accessLevel", ALLOWED_ACCESS_LEVELS)
    license_decision = _required_enum(value, "licenseDecision", ALLOWED_LICENSE_DECISIONS)
    initial_processing = _required_enum(value, "initialProcessing", ALLOWED_INITIAL_PROCESSING)
    external_processing_allowed = _required_bool(value, "externalProcessingAllowed")
    if external_processing_allowed:
        raise RagSourceRegistryError("P0 upstream references cannot be sent to external providers.")
    owner = _required_text(value, "owner")
    if owner != RAG_SOURCE_OWNER:
        raise RagSourceRegistryError("RAG source owner drifted.")
    locator = _parse_locator(value.get("locator"))
    retention = _parse_retention(value.get("retention"))
    return RagSourceDefinition(
        source_id=source_id,
        title=_required_text(value, "title"),
        institution=institution,
        topic=topic,
        sequence=sequence,
        source_type=_required_text(value, "sourceType"),
        tier=_required_text(value, "tier"),
        access_level=access_level,
        license_decision=license_decision,
        external_processing_allowed=external_processing_allowed,
        initial_processing=initial_processing,
        owner=owner,
        attribution=_required_text(value, "attribution"),
        locator=locator,
        retention=retention,
    )


def _parse_locator(value: object) -> RagSourceLocator:
    if not isinstance(value, dict):
        raise RagSourceRegistryError("RAG source locator must be an object.")
    _require_exact_fields(value, {"allowedOrigin", "allowedPath", "canonicalUrl"}, "locator")
    canonical_url = _required_text(value, "canonicalUrl")
    allowed_origin = _required_text(value, "allowedOrigin")
    allowed_path = _required_text(value, "allowedPath")
    parsed = _require_safe_https_url(canonical_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if allowed_origin != origin:
        raise RagSourceRegistryError("RAG source allowed origin must exactly match canonical URL origin.")
    if allowed_path != parsed.path:
        raise RagSourceRegistryError("RAG source allowed path must exactly match canonical URL path.")
    return RagSourceLocator(
        canonical_url=canonical_url,
        allowed_origin=allowed_origin,
        allowed_path=allowed_path,
    )


def _parse_retention(value: object) -> RagSourceRetention:
    if not isinstance(value, dict):
        raise RagSourceRegistryError("RAG source retention must be an object.")
    _require_exact_fields(value, {"days", "mode", "owner"}, "retention")
    mode = _required_text(value, "mode")
    if mode != "REFERENCE_METADATA_ONLY":
        raise RagSourceRegistryError("P0 upstream source retention must be metadata-only.")
    days = value.get("days")
    if type(days) is not int or days <= 0 or days > 3650:
        raise RagSourceRegistryError("RAG source retention days must be 1..3650.")
    owner = _required_text(value, "owner")
    if owner != RAG_SOURCE_OWNER:
        raise RagSourceRegistryError("RAG source retention owner drifted.")
    return RagSourceRetention(mode=mode, days=days, owner=owner)


def _require_safe_https_url(url: str) -> SplitResult:
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise RagSourceRegistryError("RAG source URL must use https.")
    if parsed.username or parsed.password:
        raise RagSourceRegistryError("RAG source URL userinfo is forbidden.")
    if parsed.fragment:
        raise RagSourceRegistryError("RAG source URL fragment is forbidden.")
    if not parsed.hostname or not parsed.path:
        raise RagSourceRegistryError("RAG source URL must include host and path.")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return parsed
    raise RagSourceRegistryError("RAG source URL IP literal is forbidden.")


def _validate_source_id_components(
    source_id: str,
    institution: str,
    topic: str,
    sequence: int,
) -> None:
    expected_prefix = f"src_{_slug(institution)}_"
    expected_suffix = f"_{sequence:03d}"
    if not source_id.startswith(expected_prefix) or not source_id.endswith(expected_suffix):
        raise RagSourceRegistryError("RAG sourceId institution or sequence does not match fields.")
    topic_slug = _slug(topic)
    inner = source_id.removeprefix(expected_prefix).removesuffix(expected_suffix)
    if inner != topic_slug:
        raise RagSourceRegistryError("RAG sourceId topic slug does not match fields.")


def _slug(value: str) -> str:
    slug = value.strip().lower().replace("-", "_")
    if not re.fullmatch(r"[a-z0-9_]+", slug):
        raise RagSourceRegistryError("RAG source slug contains unsupported characters.")
    return slug


def _require_exact_fields(value: Mapping[str, object], fields: set[str], context: str) -> None:
    missing = fields - set(value)
    extra = set(value) - fields
    if missing or extra:
        raise RagSourceRegistryError(
            f"{context} fields drifted: missing={sorted(missing)}, extra={sorted(extra)}"
        )


def _required_text(value: Mapping[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise RagSourceRegistryError(f"{field} must be a non-empty string.")
    return item.strip()


def _required_enum(value: Mapping[str, object], field: str, allowed: frozenset[str]) -> str:
    item = _required_text(value, field)
    if item not in allowed:
        raise RagSourceRegistryError(f"{field} is not an allowed value.")
    return item


def _required_bool(value: Mapping[str, object], field: str) -> bool:
    item = value.get(field)
    if type(item) is not bool:
        raise RagSourceRegistryError(f"{field} must be boolean.")
    return item


def _required_utc_datetime(value: Mapping[str, object], field: str) -> datetime:
    raw = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise RagSourceRegistryError(f"{field} must be an ISO-8601 UTC datetime.") from error
    if parsed.tzinfo != UTC:
        raise RagSourceRegistryError(f"{field} must use UTC.")
    return parsed
