from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from yaml.nodes import MappingNode
from yaml.tokens import AliasToken, AnchorToken, TagToken

from app.rag.source_registry import (
    RagSourceRegistryError,
    validate_canonical_https_url,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
RAG_SOURCE_CARD_V2_SCHEMA_PATH = (
    REPO_ROOT / "contracts/schemas/rag-source-card-v2.schema.json"
)
MAX_SOURCE_CARD_V2_FRONT_MATTER_BYTES = 32_768
_OFFICIAL_VARIANT = "OFFICIAL_UPSTREAM_CARD"
_SCHOLARLY_VARIANT = "SCHOLARLY_PRIMARY_CARD"
_OFFICIAL_AUTHORITY_INSTITUTIONS = {
    "OFFICIAL_API_DOCUMENTATION": frozenset({"ecos", "kis", "naver", "opendart"}),
    "OFFICIAL_SERVICE_DOCUMENTATION": frozenset({"krx"}),
    "OFFICIAL_PRODUCT_DOCUMENTATION": frozenset({"samsungfund"}),
}
_SECONDARY_HOSTS = frozenset(
    {
        "blogspot.com",
        "medium.com",
        "substack.com",
        "wikipedia.org",
        "wordpress.com",
    }
)
_REDIRECT_QUERY_KEYS = frozenset(
    {
        "continue",
        "dest",
        "destination",
        "next",
        "redirect",
        "redirect_uri",
        "return",
        "return_to",
        "target",
        "url",
    }
)
_INSTRUCTION_LIKE_PATTERN = re.compile(
    (
        r"(?i)(ignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior)\s+instructions"
        r"|system\s+prompt"
        r"|(?:reveal|print|exfiltrate)\b.{0,40}\b(?:secret|token|credential)s?\b"
        r"|(?:execute|run)\b.{0,30}\b(?:shell|command|code)\b"
        r"|(?:call|invoke)\b.{0,30}\b(?:tool|mcp|plugin)\b"
        r"|(?:place|submit|cancel)\b.{0,30}\border\b"
        r"|(?:이전|기존)\s*지시.{0,12}무시"
        r"|시스템\s*프롬프트"
        r"|비밀.{0,20}(?:출력|노출)"
        r"|도구.{0,20}(?:호출|실행))"
    )
)
_PRIVATE_PATH_PATTERN = re.compile(
    r"(?i)(?:^|[\s\"'])("
    r"(?:/home|/Users|/mnt/[a-z]|[A-Z]:[\\/]|\\\\wsl(?:\.localhost)?\\)"
    r"[^\s\"']*"
    r"|file:(?://)?[^\s\"']*"
    r")"
)


class RagSourceCardV2ContractError(ValueError):
    """source-card v2 schema 또는 안전·lineage 정책이 어긋날 때 발생한다."""


class _StrictSafeLoader(yaml.SafeLoader):
    """duplicate key와 YAML object controls를 거부하는 bounded loader."""


def _construct_unique_mapping(
    loader: _StrictSafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    if not isinstance(node, MappingNode):
        raise RagSourceCardV2ContractError(
            "RAG source card v2 YAML mapping node is invalid."
        )
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key == "<<":
            raise RagSourceCardV2ContractError(
                "RAG source card v2 YAML merge keys are forbidden."
            )
        try:
            duplicate = key in result
        except TypeError as error:
            raise RagSourceCardV2ContractError(
                "RAG source card v2 YAML keys must be scalar."
            ) from error
        if duplicate:
            raise RagSourceCardV2ContractError(
                "RAG source card v2 YAML contains a duplicate key."
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def parse_source_card_v2_front_matter(raw: bytes) -> Mapping[str, Any]:
    """strict UTF-8 YAML front matter를 object-control 없이 읽고 v2 계약으로 검증한다.

    이 함수는 파일이나 network를 열지 않으며, caller가 safe-I/O 경계에서 읽은 bounded
    front matter bytes만 받아야 한다.
    """

    if not raw or len(raw) > MAX_SOURCE_CARD_V2_FRONT_MATTER_BYTES:
        raise RagSourceCardV2ContractError(
            "RAG source card v2 front matter size is invalid."
        )
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise RagSourceCardV2ContractError(
            "RAG source card v2 front matter must be strict UTF-8."
        ) from error
    if "\r" in text or unicodedata.normalize("NFC", text) != text:
        raise RagSourceCardV2ContractError(
            "RAG source card v2 front matter must use NFC and LF."
        )
    try:
        tokens = yaml.scan(text)
        if any(
            isinstance(token, (AnchorToken, AliasToken, TagToken))
            for token in tokens
        ):
            raise RagSourceCardV2ContractError(
                "RAG source card v2 YAML tags, anchors, and aliases are forbidden."
            )
        loaded = yaml.load(text, Loader=_StrictSafeLoader)
    except RagSourceCardV2ContractError:
        raise
    except yaml.YAMLError as error:
        raise RagSourceCardV2ContractError(
            "RAG source card v2 YAML is invalid."
        ) from error
    if not isinstance(loaded, dict) or not all(
        isinstance(key, str) for key in loaded
    ):
        raise RagSourceCardV2ContractError(
            "RAG source card v2 front matter must be a string-keyed object."
        )
    return validate_source_card_v2_payload(loaded)


def validate_source_card_v2_payload(
    payload: object,
    *,
    schema_path: Path = RAG_SOURCE_CARD_V2_SCHEMA_PATH,
) -> Mapping[str, Any]:
    """canonical v2 schema와 semantic guard를 모두 통과한 immutable top-level view를 반환한다.

    raw/reference external processing과 unsafe locator는 schema shape가 맞더라도 fail-closed한다.
    """

    schema = _load_schema(schema_path)
    errors = sorted(
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).iter_errors(payload),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    if errors:
        raise RagSourceCardV2ContractError(
            f"RAG source card v2 schema rejected payload: {errors[0].message}"
        )
    if not isinstance(payload, dict):
        raise RagSourceCardV2ContractError(
            "RAG source card v2 payload must be an object."
        )
    _validate_semantics(payload)
    return MappingProxyType(dict(payload))


def _load_schema(path: Path) -> dict[str, Any]:
    try:
        schema = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RagSourceCardV2ContractError(
            "RAG source card v2 canonical schema could not be loaded."
        ) from error
    if (
        not isinstance(schema, dict)
        or schema.get("$id")
        != "contracts/schemas/rag-source-card-v2.schema.json"
        or schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or not isinstance(schema.get("properties"), dict)
        or not isinstance(schema.get("required"), list)
        or not isinstance(schema.get("oneOf"), list)
        or len(schema["oneOf"]) != 2
    ):
        raise RagSourceCardV2ContractError(
            "RAG source card v2 canonical schema root drifted."
        )
    return schema


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RagSourceCardV2ContractError(
                "RAG source card v2 schema contains duplicate JSON keys."
            )
        result[key] = value
    return result


def _walk_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        found: list[str] = []
        for item in value.values():
            found.extend(_walk_text(item))
        return found
    if isinstance(value, list):
        found = []
        for item in value:
            found.extend(_walk_text(item))
        return found
    return []


def _validate_text(text: str) -> None:
    if unicodedata.normalize("NFC", text) != text:
        raise RagSourceCardV2ContractError(
            "RAG source card v2 text must be NFC-normalized."
        )
    if any(
        ord(character) < 0x20
        or ord(character) == 0x7F
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in text
    ):
        raise RagSourceCardV2ContractError(
            "RAG source card v2 text contains control or surrogate characters."
        )
    if _INSTRUCTION_LIKE_PATTERN.search(text):
        raise RagSourceCardV2ContractError(
            "RAG source card v2 contains instruction-like control text."
        )
    if _PRIVATE_PATH_PATTERN.search(text):
        raise RagSourceCardV2ContractError(
            "RAG source card v2 contains a private filesystem locator."
        )


def _validate_semantics(card: Mapping[str, Any]) -> None:
    for text in _walk_text(card):
        _validate_text(text)

    source_id = _required_text(card, "sourceId")
    card_id = _required_text(card, "cardId")
    topic = _required_text(card, "topic")
    source_match = re.fullmatch(
        r"src_project_(?P<topic>[a-z0-9][a-z0-9_]*)_(?P<sequence>[0-9]{3})",
        source_id,
    )
    if source_match is None or source_match.group("topic") != topic:
        raise RagSourceCardV2ContractError(
            "RAG source card v2 sourceId must encode the exact topic."
        )
    if card_id != f"card_{topic}_{source_match.group('sequence')}":
        raise RagSourceCardV2ContractError(
            "RAG source card v2 cardId must match source topic and sequence."
        )
    contradicts = _required_text_list(card, "contradicts")
    if card_id in contradicts:
        raise RagSourceCardV2ContractError(
            "RAG source card v2 cannot contradict itself."
        )

    verified_at = _required_text(card, "verifiedAt")
    try:
        parsed_verified_at = datetime.fromisoformat(
            verified_at.removesuffix("Z") + "+00:00"
        )
    except ValueError as error:
        raise RagSourceCardV2ContractError(
            "RAG source card v2 verifiedAt is invalid."
        ) from error
    if not verified_at.endswith("Z") or parsed_verified_at.tzinfo != UTC:
        raise RagSourceCardV2ContractError(
            "RAG source card v2 verifiedAt must use UTC Z."
        )

    canonical_url = _required_text(card, "canonicalUrl")
    _validate_url(canonical_url)
    if card.get("canonicalUrlSha256") != hashlib.sha256(
        canonical_url.encode("utf-8")
    ).hexdigest():
        raise RagSourceCardV2ContractError(
            "RAG source card v2 canonical URL digest mismatched."
        )

    variant = _required_text(card, "cardVariant")
    institution = _required_text(card, "institution")
    upstream_ids = _required_text_list(card, "upstreamSourceIds")
    evidence_class = _required_text(card, "evidenceClass")
    if variant == _OFFICIAL_VARIANT:
        if not any(
            upstream_id.startswith(f"src_{institution}_")
            for upstream_id in upstream_ids
        ):
            raise RagSourceCardV2ContractError(
                "RAG source card v2 official authority mismatched upstream lineage."
            )
        if institution not in _OFFICIAL_AUTHORITY_INSTITUTIONS.get(
            evidence_class,
            frozenset(),
        ):
            raise RagSourceCardV2ContractError(
                "RAG source card v2 official evidence authority mismatched."
            )
    elif variant == _SCHOLARLY_VARIANT:
        if upstream_ids:
            raise RagSourceCardV2ContractError(
                "RAG source card v2 scholarly lineage must not use upstream IDs."
            )
        _validate_bibliography(card, canonical_url)
    else:
        raise RagSourceCardV2ContractError(
            "RAG source card v2 discriminator is invalid."
        )

    external_allowed = card.get("externalProcessingAllowed")
    content_class = _required_text(card, "contentClass")
    external_gate = _required_text(card, "externalProcessingGate")
    if external_allowed is True and (
        content_class != "PROJECT_AUTHORED_SANITIZED_CARD"
        or external_gate != "LICENSE_AND_CONSENT_VERIFIED"
    ):
        raise RagSourceCardV2ContractError(
            "RAG source card v2 external processing lacks its explicit gate."
        )
    if external_allowed is False and external_gate != "NOT_GRANTED":
        raise RagSourceCardV2ContractError(
            "RAG source card v2 disabled external processing gate drifted."
        )
    if content_class == "RAW_OR_REFERENCE_EVIDENCE" and external_allowed is not False:
        raise RagSourceCardV2ContractError(
            "RAG source card v2 raw/reference evidence cannot leave the boundary."
        )

    assumptions = card.get("modelAssumptions")
    if not isinstance(assumptions, list):
        raise RagSourceCardV2ContractError(
            "RAG source card v2 modelAssumptions must be an array."
        )
    model_sensitive = card.get("modelSensitive")
    if model_sensitive is not bool(assumptions):
        raise RagSourceCardV2ContractError(
            "RAG source card v2 modelSensitive and assumptions disagree."
        )
    keys = [
        _required_text(assumption, "key")
        for assumption in assumptions
        if isinstance(assumption, Mapping)
    ]
    if len(keys) != len(assumptions) or len(keys) != len(set(keys)):
        raise RagSourceCardV2ContractError(
            "RAG source card v2 model assumption keys must be unique."
        )


def _validate_url(url: str) -> None:
    try:
        validate_canonical_https_url(url)
    except RagSourceRegistryError as error:
        raise RagSourceCardV2ContractError(
            "RAG source card v2 canonical URL is unsafe."
        ) from error
    parsed = urlsplit(url)
    try:
        query_pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except ValueError as error:
        raise RagSourceCardV2ContractError(
            "RAG source card v2 canonical URL query is invalid."
        ) from error
    if any(key.lower() in _REDIRECT_QUERY_KEYS for key, _ in query_pairs):
        raise RagSourceCardV2ContractError(
            "RAG source card v2 redirect URL is forbidden."
        )


def _validate_bibliography(
    card: Mapping[str, Any],
    canonical_url: str,
) -> None:
    locator = card.get("bibliographicLocator")
    metadata = card.get("bibliographicMetadata")
    if not isinstance(locator, Mapping) or not isinstance(metadata, Mapping):
        raise RagSourceCardV2ContractError(
            "RAG source card v2 scholarly bibliography is missing."
        )
    hostname = urlsplit(canonical_url).hostname or ""
    if any(
        hostname == blocked or hostname.endswith(f".{blocked}")
        for blocked in _SECONDARY_HOSTS
    ):
        raise RagSourceCardV2ContractError(
            "RAG source card v2 secondary blog cannot be primary evidence."
        )
    locator_type = _required_text(locator, "locatorType")
    authority_type = _required_text(locator, "authorityType")
    value = _required_text(locator, "value")
    if locator_type == "DOI":
        if (
            authority_type != "DOI_REGISTRY"
            or re.fullmatch(
                r"10\.[0-9]{4,9}/[-._;()/:A-Za-z0-9]+",
                value,
            )
            is None
            or canonical_url != f"https://doi.org/{value}"
        ):
            raise RagSourceCardV2ContractError(
                "RAG source card v2 DOI locator is invalid."
            )
    elif locator_type == "ISBN":
        compact = re.sub(r"[- ]", "", value)
        if (
            authority_type
            not in {
                "ISBN_REGISTRY",
                "OFFICIAL_PUBLISHER",
                "OFFICIAL_INSTITUTION",
            }
            or re.fullmatch(r"(?:[0-9]{9}[0-9X]|[0-9]{13})", compact) is None
        ):
            raise RagSourceCardV2ContractError(
                "RAG source card v2 ISBN locator is invalid."
            )
    elif locator_type == "OFFICIAL_URL":
        if (
            authority_type
            not in {
                "OFFICIAL_PUBLISHER",
                "OFFICIAL_AUTHOR_ARCHIVE",
                "OFFICIAL_INSTITUTION",
            }
            or value != canonical_url
        ):
            raise RagSourceCardV2ContractError(
                "RAG source card v2 official locator is invalid."
            )
    else:
        raise RagSourceCardV2ContractError(
            "RAG source card v2 locator type is invalid."
        )


def _required_text(mapping: Mapping[str, Any], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise RagSourceCardV2ContractError(
            f"RAG source card v2 {field} must be non-empty text."
        )
    return value


def _required_text_list(
    mapping: Mapping[str, Any],
    field: str,
) -> list[str]:
    value = mapping.get(field)
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise RagSourceCardV2ContractError(
            f"RAG source card v2 {field} must be a text array."
        )
    return value
