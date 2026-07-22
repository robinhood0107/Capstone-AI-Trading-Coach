from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlsplit

import yaml

from app.data.calendar.errors import RegistryValidationError

_TOP_LEVEL_FIELDS = frozenset({"version", "verifiedAt", "sources"})
_SOURCE_FIELDS = frozenset(
    {
        "sourceId",
        "sourceKind",
        "officialDocs",
        "termsUrl",
        "verifiedAt",
        "cost",
        "usageRestriction",
        "freshness",
        "quotaScope",
        "adoptionStatus",
        "activationGate",
        "projectUsage",
        "webSocketRole",
        "tier",
        "originGroup",
        "capabilities",
        "networkReady",
        "enabledByDefault",
        "adapterVersion",
        "mappingVersion",
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
_ADOPTION_STATUSES = frozenset(
    {"ACTIVE_PRIMARY", "ACTIVE_SUPPORTING", "CANDIDATE_DISABLED", "BLOCKED_LICENSE", "RESEARCH_ONLY"}
)
_ACTIVE_STATUSES = frozenset({"ACTIVE_PRIMARY", "ACTIVE_SUPPORTING"})


@dataclass(frozen=True)
class SourceDefinition:
    """runtime 비밀값 없이 source의 provenance, authority, activation 상태만 표현한다."""

    source_id: str
    source_kind: str
    official_docs: tuple[str, ...]
    terms_url: str
    verified_at: str
    cost: str
    usage_restriction: str
    freshness: str
    quota_scope: str
    adoption_status: str
    activation_gate: str
    project_usage: str
    web_socket_role: str
    tier: int
    origin_group: str
    capabilities: frozenset[str]
    network_ready: bool
    enabled_by_default: bool
    adapter_version: str
    mapping_version: str


@dataclass(frozen=True)
class SourceRegistry:
    """strict YAML seed를 immutable source-id mapping으로 노출한다."""

    version: str
    verified_at: str
    sources: Mapping[str, SourceDefinition]
    seed_path: Path


def load_default_registry() -> SourceRegistry:
    """package에 commit된 S1.6 v1 source seed를 읽으며 환경변수나 provider key를 보지 않는다."""
    seed = files("app.data.calendar").joinpath("calendar_source_seed.yaml")
    return load_registry(Path(str(seed)))


def load_registry(path: Path) -> SourceRegistry:
    """unknown/missing field와 unsafe activation을 모두 fail-closed하는 strict registry loader다."""
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        raise RegistryValidationError("registry YAML is invalid") from None
    if not isinstance(payload, dict):
        raise RegistryValidationError("registry root must be an object")
    _require_exact_fields(payload, _TOP_LEVEL_FIELDS, "registry")
    version = _required_text(payload, "version")
    verified_at = _required_text(payload, "verifiedAt")
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
        version=version,
        verified_at=verified_at,
        sources=MappingProxyType(sources),
        seed_path=path,
    )


def _parse_source(value: dict[str, Any]) -> SourceDefinition:
    source_id = _required_text(value, "sourceId")
    documents = value.get("officialDocs")
    if not isinstance(documents, list) or not documents or not all(isinstance(item, str) for item in documents):
        raise RegistryValidationError("officialDocs must be a non-empty URL list")
    official_docs = tuple(str(item) for item in documents)
    terms_url = _required_text(value, "termsUrl")
    for url in (*official_docs, terms_url):
        _require_https_url(url)
    tier = value.get("tier")
    if type(tier) is not int or tier not in {1, 2, 3, 4}:
        raise RegistryValidationError("tier must be 1..4")
    capabilities_value = value.get("capabilities")
    if not isinstance(capabilities_value, list) or not capabilities_value:
        raise RegistryValidationError("capability list is required")
    capabilities = frozenset(str(item) for item in capabilities_value)
    if not capabilities <= _CAPABILITIES:
        raise RegistryValidationError("invalid capability")
    adoption_status = _required_text(value, "adoptionStatus")
    if adoption_status not in _ADOPTION_STATUSES:
        raise RegistryValidationError("invalid adoption status")
    network_ready = _required_bool(value, "networkReady")
    enabled_by_default = _required_bool(value, "enabledByDefault")
    if enabled_by_default and (adoption_status not in _ACTIVE_STATUSES or not network_ready):
        raise RegistryValidationError("unsafe source cannot be enabled")
    return SourceDefinition(
        source_id=source_id,
        source_kind=_required_text(value, "sourceKind"),
        official_docs=official_docs,
        terms_url=terms_url,
        verified_at=_required_text(value, "verifiedAt"),
        cost=_required_text(value, "cost"),
        usage_restriction=_required_text(value, "usageRestriction"),
        freshness=_required_text(value, "freshness"),
        quota_scope=_required_text(value, "quotaScope"),
        adoption_status=adoption_status,
        activation_gate=_required_text(value, "activationGate"),
        project_usage=_required_text(value, "projectUsage"),
        web_socket_role=_required_text(value, "webSocketRole"),
        tier=tier,
        origin_group=_required_text(value, "originGroup"),
        capabilities=capabilities,
        network_ready=network_ready,
        enabled_by_default=enabled_by_default,
        adapter_version=_required_text(value, "adapterVersion"),
        mapping_version=_required_text(value, "mappingVersion"),
    )


def _require_exact_fields(value: dict[str, Any], expected: frozenset[str], label: str) -> None:
    actual = set(value)
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise RegistryValidationError(f"{label} missing required field")
    if unknown:
        raise RegistryValidationError(f"{label} contains unknown field")


def _required_text(value: dict[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise RegistryValidationError(f"{field} must be non-empty text")
    return item.strip()


def _required_bool(value: dict[str, Any], field: str) -> bool:
    item = value.get(field)
    if type(item) is not bool:
        raise RegistryValidationError(f"{field} must be boolean")
    return item


def _require_https_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise RegistryValidationError("registry URLs must use absolute https")
