from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml
from yaml.nodes import MappingNode
from yaml.tokens import AliasToken, AnchorToken, TagToken

from app.rag.safe_io import RagSafeIoError, read_approved_regular_file
from app.rag.source_registry import (
    RagSourceRegistry,
    RagSourceRegistryError,
    load_default_source_registry,
    validate_canonical_https_url,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
RAG_SOURCE_CARD_SCHEMA_PATH = REPO_ROOT / "contracts/schemas/rag-source-card-v1.schema.json"
# 원문 카드는 Git 작업트리 밖의 사용자 로컬 데이터 경계에서만 읽는다.
OFFICIAL_SOURCE_CARD_ROOT = (
    Path.home() / ".local" / "share" / "capstone-ai-trading-coach" / "rag-source-cards" / "official"
)
MAX_SOURCE_CARD_BYTES = 32_768
SOURCE_CARD_HEADINGS = (
    "핵심 claim",
    "적용 범위와 전제",
    "프로젝트 적용",
    "한계와 반례",
    "허용 사용",
    "금지 추론",
    "근거 위치",
)
_INSTRUCTION_LIKE_PATTERN = re.compile(
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
_RAW_HTML_PATTERN = re.compile(r"<(?:[A-Za-z!/][^>\n]*)>")
_AUTHORITY_INSTITUTIONS = {
    "OFFICIAL_API_DOCUMENTATION": frozenset({"kis", "opendart", "ecos"}),
    "OFFICIAL_SERVICE_DOCUMENTATION": frozenset({"krx"}),
    "OFFICIAL_PRODUCT_DOCUMENTATION": frozenset({"samsungfund"}),
}


class RagSourceCardError(ValueError):
    """RAG source card가 schema·safe-I/O·corpus 안전 계약을 위반할 때 발생한다."""


@dataclass(frozen=True)
class RagSourceCard:
    """검증된 project-authored one-claim card의 bounded view.

    upstream raw body는 보관하지 않으며, public projection이나 embedding 단계는 이 객체를
    곧바로 활성 corpus로 간주하지 않고 별도 generation materialization을 거쳐야 한다.
    """

    source_id: str
    card_id: str
    title: str
    institution: str
    topic: str
    claim: str
    status: str
    verified_at: datetime
    canonical_url: str
    canonical_url_sha256: str
    evidence_content_sha256: str
    upstream_source_ids: tuple[str, ...]
    contradicts: tuple[str, ...]
    representative_questions: tuple[str, ...]
    relative_path: str
    content_sha256: str
    canonical_body: str
    license_note: str
    attribution: str
    retention_owner: str
    retention_days: int
    external_processing_allowed: bool
    sections: Mapping[str, str]


class _StrictSafeLoader(yaml.SafeLoader):
    """duplicate key와 merge key를 허용하지 않는 source-card 전용 SafeLoader."""


def _construct_unique_mapping(
    loader: _StrictSafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    if not isinstance(node, MappingNode):
        raise RagSourceCardError("RAG source card YAML mapping node is invalid.")
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key == "<<":
            raise RagSourceCardError("RAG source card YAML merge keys are forbidden.")
        try:
            duplicate = key in result
        except TypeError as error:
            raise RagSourceCardError("RAG source card YAML keys must be scalar.") from error
        if duplicate:
            raise RagSourceCardError("RAG source card YAML contains a duplicate key.")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_rag_source_cards(
    *,
    approved_root: Path,
    relative_paths: Sequence[str],
    schema_path: Path = RAG_SOURCE_CARD_SCHEMA_PATH,
    registry: RagSourceRegistry | None = None,
) -> tuple[RagSourceCard, ...]:
    """approved-root의 Markdown card 묶음을 exact schema와 registry seed로 검증한다.

    file read는 symlink·hardlink·path escape·race·oversize를 먼저 차단하고, network/provider
    호출은 만들지 않는다. `contradicts`는 같은 묶음의 알려진 card ID만 참조할 수 있다.
    """

    if not relative_paths:
        raise RagSourceCardError("At least one RAG source card path is required.")
    if len(relative_paths) > 30 or len(set(relative_paths)) != len(relative_paths):
        raise RagSourceCardError("RAG source card path set is duplicate or exceeds 30.")
    contract_schema = _load_contract_schema(schema_path)
    source_registry = registry or load_default_source_registry()
    cards = tuple(
        _load_single_source_card(
            approved_root=approved_root,
            relative_path=relative_path,
            contract_schema=contract_schema,
            registry=source_registry,
        )
        for relative_path in relative_paths
    )
    source_ids = [card.source_id for card in cards]
    card_ids = [card.card_id for card in cards]
    if len(set(source_ids)) != len(source_ids) or len(set(card_ids)) != len(card_ids):
        raise RagSourceCardError("RAG source card sourceId/cardId identities must be unique.")
    known_card_ids = frozenset(card_ids)
    for card in cards:
        for contradicted_card_id in card.contradicts:
            if contradicted_card_id == card.card_id:
                raise RagSourceCardError("RAG source card cannot contradict itself.")
            if contradicted_card_id not in known_card_ids:
                raise RagSourceCardError(
                    "RAG source card contradiction must reference a known card in the batch."
                )
    return cards


def _load_single_source_card(
    *,
    approved_root: Path,
    relative_path: str,
    contract_schema: Mapping[str, Any],
    registry: RagSourceRegistry,
) -> RagSourceCard:
    try:
        result = read_approved_regular_file(
            approved_root=approved_root,
            relative_path=relative_path,
            max_bytes=MAX_SOURCE_CARD_BYTES,
        )
    except RagSafeIoError as error:
        raise RagSourceCardError("RAG source card safe read failed.") from error
    try:
        text = result.content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise RagSourceCardError("RAG source card must be strict UTF-8.") from error
    if "\r" in text or unicodedata.normalize("NFC", text) != text:
        raise RagSourceCardError("RAG source card must use NFC text and LF line endings.")
    front_matter, body = _split_and_load_markdown(text)
    _validate_contract_value(front_matter, contract_schema, path="$")
    _validate_front_matter_semantics(front_matter, registry)
    sections = _validate_body(body, front_matter)
    verified_at = _require_utc_datetime(front_matter["verifiedAt"], field="verifiedAt")
    return RagSourceCard(
        source_id=_require_text(front_matter, "sourceId"),
        card_id=_require_text(front_matter, "cardId"),
        title=_require_text(front_matter, "title"),
        institution=_require_text(front_matter, "institution"),
        topic=_require_text(front_matter, "topic"),
        claim=_require_text(front_matter, "claim"),
        status=_require_text(front_matter, "status"),
        verified_at=verified_at,
        canonical_url=_require_text(front_matter, "canonicalUrl"),
        canonical_url_sha256=_require_text(front_matter, "canonicalUrlSha256"),
        evidence_content_sha256=_require_text(front_matter, "evidenceContentSha256"),
        upstream_source_ids=_require_text_tuple(front_matter, "upstreamSourceIds"),
        contradicts=_require_text_tuple(front_matter, "contradicts"),
        representative_questions=_require_text_tuple(
            front_matter,
            "representativeQuestions",
        ),
        relative_path=result.relative_path,
        content_sha256=result.content_sha256,
        canonical_body=body,
        license_note=_require_text(front_matter, "licenseNote"),
        attribution=_require_text(front_matter, "attribution"),
        retention_owner=_require_text(front_matter, "retentionOwner"),
        retention_days=_require_int(front_matter, "retentionDays"),
        external_processing_allowed=_require_bool(front_matter, "externalProcessingAllowed"),
        sections=MappingProxyType(sections),
    )


def _split_and_load_markdown(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise RagSourceCardError("RAG source card must start with exact YAML front matter.")
    closing_index = text.find("\n---\n", 4)
    if closing_index < 0:
        raise RagSourceCardError("RAG source card front matter closing delimiter is missing.")
    yaml_text = text[4:closing_index]
    body = text[closing_index + 5 :]
    if not yaml_text or not body:
        raise RagSourceCardError("RAG source card front matter and body must be non-empty.")
    try:
        tokens = yaml.scan(yaml_text)
        if any(isinstance(token, (AnchorToken, AliasToken, TagToken)) for token in tokens):
            raise RagSourceCardError(
                "RAG source card YAML tags, anchors, and aliases are forbidden."
            )
        loaded = yaml.load(yaml_text, Loader=_StrictSafeLoader)
    except RagSourceCardError:
        raise
    except yaml.YAMLError as error:
        raise RagSourceCardError("RAG source card YAML is invalid.") from error
    if not isinstance(loaded, dict) or not all(isinstance(key, str) for key in loaded):
        raise RagSourceCardError("RAG source card front matter must be a string-keyed object.")
    return loaded, body


def _load_contract_schema(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="strict")
        schema = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RagSourceCardError("RAG source card contract schema could not be loaded.") from error
    if (
        not isinstance(schema, dict)
        or schema.get("$id") != "contracts/schemas/rag-source-card-v1.schema.json"
        or schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or not isinstance(schema.get("properties"), dict)
        or not isinstance(schema.get("required"), list)
        or set(schema["properties"]) != set(schema["required"])
    ):
        raise RagSourceCardError("RAG source card contract schema root drifted.")
    return schema


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RagSourceCardError("RAG source card contract schema has duplicate JSON keys.")
        result[key] = value
    return result


def _validate_contract_value(value: Any, schema: Mapping[str, Any], *, path: str) -> None:
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(value, dict):
            raise RagSourceCardError(f"{path} must be an object.")
        properties = schema.get("properties")
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise RagSourceCardError("RAG source card contract object schema is invalid.")
        missing = set(required) - set(value)
        extra = set(value) - set(properties)
        if missing or extra:
            raise RagSourceCardError(
                f"{path} fields drifted: missing={sorted(missing)}, extra={sorted(extra)}"
            )
        for key, item in value.items():
            property_schema = properties.get(key)
            if not isinstance(property_schema, dict):
                raise RagSourceCardError("RAG source card property schema is invalid.")
            _validate_contract_value(item, property_schema, path=f"{path}.{key}")
    elif expected_type == "string" and not isinstance(value, str):
        raise RagSourceCardError(f"{path} must be a string.")
    elif expected_type == "array":
        if not isinstance(value, list):
            raise RagSourceCardError(f"{path} must be an array.")
        item_schema = schema.get("items")
        if not isinstance(item_schema, dict):
            raise RagSourceCardError("RAG source card array item schema is invalid.")
        for index, item in enumerate(value):
            _validate_contract_value(item, item_schema, path=f"{path}[{index}]")
        if schema.get("uniqueItems") is True and len(value) != len(set(value)):
            raise RagSourceCardError(f"{path} items must be unique.")
    elif expected_type == "integer" and type(value) is not int:
        raise RagSourceCardError(f"{path} must be an integer.")
    elif expected_type is not None and expected_type not in {
        "object",
        "string",
        "array",
        "integer",
    }:
        raise RagSourceCardError("RAG source card contract uses an unsupported schema type.")

    if "const" in schema:
        expected = schema["const"]
        if type(value) is not type(expected) or value != expected:
            raise RagSourceCardError(f"{path} does not match its contract constant.")
    if "enum" in schema:
        allowed = schema["enum"]
        if not isinstance(allowed, list) or value not in allowed:
            raise RagSourceCardError(f"{path} is not an allowed contract value.")
    if isinstance(value, str):
        minimum_length = schema.get("minLength")
        maximum_length = schema.get("maxLength")
        pattern = schema.get("pattern")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            raise RagSourceCardError(f"{path} is shorter than its contract bound.")
        if isinstance(maximum_length, int) and len(value) > maximum_length:
            raise RagSourceCardError(f"{path} exceeds its contract bound.")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            raise RagSourceCardError(f"{path} does not match its contract pattern.")
        if schema.get("format") == "date-time":
            _require_utc_datetime(value, field=path)
    if isinstance(value, list):
        minimum_items = schema.get("minItems")
        maximum_items = schema.get("maxItems")
        if isinstance(minimum_items, int) and len(value) < minimum_items:
            raise RagSourceCardError(f"{path} has too few items.")
        if isinstance(maximum_items, int) and len(value) > maximum_items:
            raise RagSourceCardError(f"{path} has too many items.")
    if type(value) is int:
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, int) and value < minimum:
            raise RagSourceCardError(f"{path} is below its contract bound.")
        if isinstance(maximum, int) and value > maximum:
            raise RagSourceCardError(f"{path} exceeds its contract bound.")


def _validate_front_matter_semantics(
    front_matter: Mapping[str, Any],
    registry: RagSourceRegistry,
) -> None:
    for text in _walk_text(front_matter):
        _validate_corpus_text(text)
    source_id = _require_text(front_matter, "sourceId")
    card_id = _require_text(front_matter, "cardId")
    topic = _require_text(front_matter, "topic")
    institution = _require_text(front_matter, "institution")
    source_match = re.fullmatch(
        r"src_project_(?P<topic>[a-z0-9][a-z0-9_]*)_(?P<sequence>[0-9]{3})",
        source_id,
    )
    if source_match is None or source_match.group("topic") != topic:
        raise RagSourceCardError("RAG sourceId must encode the exact topic.")
    if card_id != f"card_{topic}_{source_match.group('sequence')}":
        raise RagSourceCardError("RAG cardId must match source topic and sequence.")

    canonical_url = _require_text(front_matter, "canonicalUrl")
    try:
        validate_canonical_https_url(canonical_url)
    except RagSourceRegistryError as error:
        raise RagSourceCardError("RAG source card canonical URL is unsafe.") from error
    expected_url_digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
    if front_matter.get("canonicalUrlSha256") != expected_url_digest:
        raise RagSourceCardError("RAG source card canonical URL digest mismatched.")

    upstream_source_ids = _require_text_tuple(front_matter, "upstreamSourceIds")
    if any(source_id not in registry.sources for source_id in upstream_source_ids):
        raise RagSourceCardError("RAG source card cites an unknown upstream source.")
    if not any(
        registry.sources[upstream_source_id].institution == institution
        for upstream_source_id in upstream_source_ids
    ):
        raise RagSourceCardError(
            "RAG source card must cite an institution-matching upstream source."
        )

    evidence_class = _require_text(front_matter, "evidenceClass")
    allowed_institutions = _AUTHORITY_INSTITUTIONS.get(evidence_class)
    if allowed_institutions is not None and institution not in allowed_institutions:
        raise RagSourceCardError("RAG source card evidence authority mismatched its institution.")
    if evidence_class == "MODEL_ESTIMATOR" and not front_matter.get("modelAssumptions"):
        raise RagSourceCardError("Model/estimator source cards require stable assumptions.")
    if front_matter.get("externalProcessingAllowed") is not False:
        raise RagSourceCardError("Reference-only source cards cannot enable external processing.")


def _validate_body(
    body: str,
    front_matter: Mapping[str, Any],
) -> dict[str, str]:
    _validate_corpus_text(body)
    if "```" in body or "~~~" in body or _RAW_HTML_PATTERN.search(body):
        raise RagSourceCardError("RAG source card body cannot contain code fences or raw HTML.")
    lines = body.splitlines()
    expected_h1 = f"# Source Card: {_require_text(front_matter, 'title')}"
    if not lines or lines[0] != expected_h1:
        raise RagSourceCardError("RAG source card H1 must match its exact title.")
    for line in lines[1:]:
        if line.startswith("#") and not line.startswith("## "):
            raise RagSourceCardError("RAG source card body contains an unsupported heading.")
    heading_rows = [
        (index, line.removeprefix("## "))
        for index, line in enumerate(lines)
        if line.startswith("## ")
    ]
    if tuple(title for _, title in heading_rows) != SOURCE_CARD_HEADINGS:
        raise RagSourceCardError("RAG source card H2 headings drifted.")
    sections: dict[str, str] = {}
    for heading_index, (line_index, title) in enumerate(heading_rows):
        end_index = (
            heading_rows[heading_index + 1][0]
            if heading_index + 1 < len(heading_rows)
            else len(lines)
        )
        content = "\n".join(lines[line_index + 1 : end_index]).strip()
        if not content:
            raise RagSourceCardError("RAG source card body section cannot be empty.")
        sections[title] = content
    claim = _require_text(front_matter, "claim")
    if sections["핵심 claim"] != claim:
        raise RagSourceCardError("RAG source card must contain exactly one matching core claim.")
    project_section = sections["프로젝트 적용"]
    for question in _require_text_tuple(front_matter, "representativeQuestions"):
        if question not in project_section:
            raise RagSourceCardError(
                "RAG source card project section must include every retrieval question."
            )
    return sections


def _walk_text(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        found: list[str] = []
        for item in value.values():
            found.extend(_walk_text(item))
        return tuple(found)
    if isinstance(value, list):
        found = []
        for item in value:
            found.extend(_walk_text(item))
        return tuple(found)
    return ()


def _validate_corpus_text(text: str) -> None:
    if (
        not text
        or unicodedata.normalize("NFC", text) != text
        or any(
            (ord(character) < 0x20 and character not in {"\n", "\t"}) or ord(character) == 0x7F
            for character in text
        )
        or _INSTRUCTION_LIKE_PATTERN.search(text)
    ):
        raise RagSourceCardError("RAG source card contains unsafe or instruction-like text.")


def _require_text(value: Mapping[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item or item != item.strip():
        raise RagSourceCardError(f"{field} must be canonical non-empty text.")
    return item


def _require_text_tuple(value: Mapping[str, Any], field: str) -> tuple[str, ...]:
    item = value.get(field)
    if not isinstance(item, list) or not all(isinstance(entry, str) for entry in item):
        raise RagSourceCardError(f"{field} must be a string array.")
    return tuple(item)


def _require_int(value: Mapping[str, Any], field: str) -> int:
    item = value.get(field)
    if type(item) is not int:
        raise RagSourceCardError(f"{field} must be an integer.")
    return item


def _require_bool(value: Mapping[str, Any], field: str) -> bool:
    item = value.get(field)
    if type(item) is not bool:
        raise RagSourceCardError(f"{field} must be a boolean.")
    return item


def _require_utc_datetime(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RagSourceCardError(f"{field} must be a canonical UTC datetime.")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise RagSourceCardError(f"{field} must be a canonical UTC datetime.") from error
    if parsed.tzinfo != UTC:
        raise RagSourceCardError(f"{field} must use UTC.")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Validate local-only RAG source cards without network or provider calls."),
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("relative_paths", nargs="+")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """로컬 전용 card 원문을 출력하지 않고 ID/hash receipt만 반환하는 offline validator CLI."""

    args = _build_parser().parse_args(argv)
    try:
        cards = load_rag_source_cards(
            approved_root=OFFICIAL_SOURCE_CARD_ROOT,
            relative_paths=tuple(args.relative_paths),
            schema_path=RAG_SOURCE_CARD_SCHEMA_PATH,
        )
    except RagSourceCardError as error:
        print(
            json.dumps(
                {"error": "RAG_SOURCE_CARD_INVALID", "message": str(error)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    summary = {
        "cardCount": len(cards),
        "cardIds": [card.card_id for card in cards],
        "contentSha256": [card.content_sha256 for card in cards],
        "sourceIds": [card.source_id for card in cards],
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"validated RAG source cards: {len(cards)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
