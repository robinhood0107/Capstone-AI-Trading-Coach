from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlsplit

import yaml

from app.data.calendar.errors import RegistryValidationError

_TOP_LEVEL_FIELDS = frozenset({"schemaVersion", "registryVersion", "generatedAt", "sources"})
_SOURCE_FIELDS = frozenset(
    {
        "sourceId",
        "provider",
        "category",
        "licenseClass",
        "reliabilityTier",
        "capabilities",
        "originGroup",
        "origin",
        "mappingVersion",
        "networkReady",
        "enabledByDefault",
        "retention",
        "provenance",
    }
)
_PROVIDERS = frozenset({"EXCHANGE_CALENDARS", "KIS", "KASI", "OPENDART", "FIXTURE"})
_CATEGORIES = frozenset({"SESSION", "CORPORATE_ACTION", "DISCLOSURE", "MACRO"})
_LICENSE_CLASSES = frozenset(
    {
        "OFFICIAL_NO_FEE",
        "OFFICIAL_NONCOMMERCIAL_RESTRICTED",
        "FREE_TIER_AGGREGATOR",
        "SCRAPE_OR_RSS_ONLY",
        "PAID_OR_UNKNOWN",
        "UNSAFE_OR_EXCLUDE",
    }
)
_DEFAULT_BLOCKED_LICENSES = frozenset(
    {
        "FREE_TIER_AGGREGATOR",
        "SCRAPE_OR_RSS_ONLY",
        "PAID_OR_UNKNOWN",
        "UNSAFE_OR_EXCLUDE",
    }
)
_CAPABILITIES = frozenset(
    {
        "MARKET_SESSION",
        "HOLIDAY_REASON",
        "DISCLOSURE_EVENT",
        "DIVIDEND_EVENT",
        "DISCLOSURE_STATE",
        "OWNERSHIP_PROJECTION",
    }
)
_ORIGIN_FIELDS = {
    "OFFLINE": frozenset({"kind", "identifier"}),
    "HTTPS": frozenset({"kind", "baseUrl"}),
}
_RETENTION_FIELDS = {
    "EPHEMERAL_ONLY": frozenset({"mode"}),
    "OPERATOR_REQUIRED": frozenset({"mode"}),
    "PERSISTENT": frozenset({"mode", "days", "owner"}),
}
_PROVENANCE_FIELDS = frozenset({"verifiedAt", "sourceVersion", "evidenceUrl", "attribution"})


@dataclass(frozen=True)
class SourceOrigin:
    """credential을 포함하지 않는 offline identifier 또는 exact HTTPS fixed origin이다."""

    kind: str
    identifier: str | None = None
    base_url: str | None = None


@dataclass(frozen=True)
class RetentionDefinition:
    """seed에는 영구보존 기본값 대신 operator 설정 필요 여부만 명시한다."""

    mode: str
    days: int | None = None
    owner: str | None = None


@dataclass(frozen=True)
class RegistryProvenance:
    """source 채택 근거의 검증 날짜, 버전, 공개 evidence와 attribution이다."""

    verified_at: date
    source_version: str
    evidence_url: str
    attribution: str


@dataclass(frozen=True)
class SourceDefinition:
    """runtime 비밀값 없이 source authority, fixed origin과 activation 상태만 표현한다."""

    source_id: str
    provider: str
    category: str
    license_class: str
    reliability_tier: int
    capabilities: frozenset[str]
    origin_group: str
    origin: SourceOrigin
    mapping_version: str
    network_ready: bool
    enabled_by_default: bool
    retention: RetentionDefinition
    provenance: RegistryProvenance


@dataclass(frozen=True)
class SourceRegistry:
    """strict YAML seed를 immutable source-id mapping으로 노출한다."""

    schema_version: str
    registry_version: str
    generated_at: datetime
    sources: Mapping[str, SourceDefinition]
    seed_path: Path


def load_default_registry() -> SourceRegistry:
    """package에 commit된 S1.6 v1 seed를 읽으며 환경변수나 provider key를 보지 않는다."""
    seed = files("app.data.calendar").joinpath("calendar_source_seed.yaml")
    return load_registry(Path(str(seed)))


def load_registry(path: Path) -> SourceRegistry:
    """unknown/missing field와 unsafe activation을 모두 fail-closed하는 registry loader다."""
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        raise RegistryValidationError("registry YAML is invalid") from None
    if not isinstance(payload, dict):
        raise RegistryValidationError("registry root must be an object")
    _require_exact_fields(payload, _TOP_LEVEL_FIELDS, "registry")
    schema_version = _required_text(payload, "schemaVersion")
    if schema_version != "1":
        raise RegistryValidationError("registry schema version is unsupported")
    registry_version = _required_text(payload, "registryVersion")
    generated_at = _required_utc_datetime(payload, "generatedAt")
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise RegistryValidationError("registry sources must be a non-empty list")

    sources: dict[str, SourceDefinition] = {}
    for raw_source in raw_sources:
        if not isinstance(raw_source, dict):
            raise RegistryValidationError("source entry must be an object")
        _require_exact_fields(raw_source, _SOURCE_FIELDS, "source")
        source = _parse_source(raw_source)
        if source.source_id in sources:
            raise RegistryValidationError("duplicate source ID")
        sources[source.source_id] = source
    return SourceRegistry(
        schema_version=schema_version,
        registry_version=registry_version,
        generated_at=generated_at,
        sources=MappingProxyType(sources),
        seed_path=path,
    )


def _parse_source(value: dict[str, Any]) -> SourceDefinition:
    provider = _required_enum(value, "provider", _PROVIDERS, "provider")
    category = _required_enum(value, "category", _CATEGORIES, "category")
    license_class = _required_enum(value, "licenseClass", _LICENSE_CLASSES, "license")
    tier = value.get("reliabilityTier")
    if type(tier) is not int or tier not in {1, 2, 3, 4}:
        raise RegistryValidationError("reliability tier must be 1..4")
    capabilities_value = value.get("capabilities")
    if not isinstance(capabilities_value, list) or not capabilities_value:
        raise RegistryValidationError("capability list is required")
    if not all(isinstance(item, str) and item.strip() for item in capabilities_value):
        raise RegistryValidationError("invalid capability")
    capabilities = frozenset(item.strip() for item in capabilities_value)
    if len(capabilities) != len(capabilities_value) or not capabilities <= _CAPABILITIES:
        raise RegistryValidationError("invalid capability")
    origin = _parse_origin(value.get("origin"))
    retention = _parse_retention(value.get("retention"), origin)
    provenance = _parse_provenance(value.get("provenance"))
    network_ready = _required_bool(value, "networkReady")
    enabled_by_default = _required_bool(value, "enabledByDefault")
    if enabled_by_default and (not network_ready or license_class in _DEFAULT_BLOCKED_LICENSES):
        raise RegistryValidationError("unsafe or inactive source cannot be enabled")
    if enabled_by_default and origin.kind == "HTTPS" and retention.mode != "PERSISTENT":
        raise RegistryValidationError("enabled online source requires persistent retention")
    return SourceDefinition(
        source_id=_required_text(value, "sourceId"),
        provider=provider,
        category=category,
        license_class=license_class,
        reliability_tier=tier,
        capabilities=capabilities,
        origin_group=_required_text(value, "originGroup"),
        origin=origin,
        mapping_version=_required_text(value, "mappingVersion"),
        network_ready=network_ready,
        enabled_by_default=enabled_by_default,
        retention=retention,
        provenance=provenance,
    )


def _parse_origin(value: object) -> SourceOrigin:
    if not isinstance(value, dict):
        raise RegistryValidationError("origin must be an object")
    kind = _required_text(value, "kind")
    fields = _ORIGIN_FIELDS.get(kind)
    if fields is None:
        raise RegistryValidationError("origin kind is invalid")
    _require_exact_fields(value, fields, "origin")
    if kind == "OFFLINE":
        return SourceOrigin(kind=kind, identifier=_required_text(value, "identifier"))
    base_url = _required_text(value, "baseUrl")
    _require_exact_https_origin(base_url)
    return SourceOrigin(kind=kind, base_url=base_url)


def _parse_retention(value: object, origin: SourceOrigin) -> RetentionDefinition:
    if not isinstance(value, dict):
        raise RegistryValidationError("retention must be an object")
    mode = _required_text(value, "mode")
    fields = _RETENTION_FIELDS.get(mode)
    if fields is None:
        raise RegistryValidationError("retention mode is invalid")
    _require_exact_fields(value, fields, "retention")
    if origin.kind == "OFFLINE" and mode != "EPHEMERAL_ONLY":
        raise RegistryValidationError("offline retention must be ephemeral")
    if origin.kind == "HTTPS" and mode == "EPHEMERAL_ONLY":
        raise RegistryValidationError("online retention requires operator policy")
    if mode != "PERSISTENT":
        return RetentionDefinition(mode=mode)
    days = value.get("days")
    owner = value.get("owner")
    if type(days) is not int or days <= 0 or not isinstance(owner, str) or not owner.strip():
        raise RegistryValidationError("persistent retention requires positive days and owner")
    return RetentionDefinition(mode=mode, days=days, owner=owner.strip())


def _parse_provenance(value: object) -> RegistryProvenance:
    if not isinstance(value, dict):
        raise RegistryValidationError("provenance must be an object")
    _require_exact_fields(value, _PROVENANCE_FIELDS, "provenance")
    verified_at = _required_date(value, "verifiedAt", "provenance")
    evidence_url = _required_text(value, "evidenceUrl")
    _require_https_url(evidence_url)
    return RegistryProvenance(
        verified_at=verified_at,
        source_version=_required_text(value, "sourceVersion"),
        evidence_url=evidence_url,
        attribution=_required_text(value, "attribution"),
    )


def _require_exact_fields(value: dict[str, Any], expected: frozenset[str], label: str) -> None:
    actual = set(value)
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise RegistryValidationError(f"{label} missing required field")
    if unknown:
        raise RegistryValidationError(f"{label} contains unknown field")


def _required_text(value: Mapping[str, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise RegistryValidationError(f"{field} must be non-empty text")
    return item.strip()


def _required_bool(value: Mapping[str, object], field: str) -> bool:
    item = value.get(field)
    if type(item) is not bool:
        raise RegistryValidationError(f"{field} must be boolean")
    return item


def _required_enum(
    value: Mapping[str, object],
    field: str,
    allowed: frozenset[str],
    label: str,
) -> str:
    item = _required_text(value, field)
    if item not in allowed:
        raise RegistryValidationError(f"invalid {label}")
    return item


def _required_date(value: Mapping[str, object], field: str, label: str) -> date:
    item = _required_text(value, field)
    try:
        parsed = date.fromisoformat(item)
    except ValueError:
        raise RegistryValidationError(f"{label} date is invalid") from None
    if parsed.isoformat() != item:
        raise RegistryValidationError(f"{label} date is invalid")
    return parsed


def _required_utc_datetime(value: Mapping[str, object], field: str) -> datetime:
    item = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(item.replace("Z", "+00:00"))
    except ValueError:
        raise RegistryValidationError("generatedAt timestamp is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise RegistryValidationError("generatedAt must be an absolute UTC timestamp")
    return parsed


def _require_https_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or not parsed.netloc
    ):
        raise RegistryValidationError("registry URLs must use absolute https")


def _require_exact_https_origin(value: str) -> None:
    _require_https_url(value)
    parsed = urlsplit(value)
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise RegistryValidationError("origin must be an exact https fixed origin")
