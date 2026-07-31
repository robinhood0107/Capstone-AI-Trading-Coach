from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.gdelt.errors import GdeltAggregateError

_QUERY_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]+_v[0-9]+$")
_SYMBOL_PATTERN = re.compile(r"^[0-9A-Z]{1,16}$")


@dataclass(frozen=True)
class QueryDefinition:
    """bounded alias와 symbol을 versioned query identity로 묶는 내부 계약이다.

    raw 사용자 질문을 받지 않으며 future provider query는 이 registry의 별도 승인 사본에서만
    만들 수 있다.
    """

    query_registry_id: str
    aliases: tuple[str, ...]
    entity_mapping_version: str
    symbol: str

    def __post_init__(self) -> None:
        if _QUERY_ID_PATTERN.fullmatch(self.query_registry_id) is None:
            raise GdeltAggregateError("MAPPING_AMBIGUOUS", "query registry id is invalid")
        if not 1 <= len(self.aliases) <= 4:
            raise GdeltAggregateError("MAPPING_AMBIGUOUS", "alias count is outside policy")
        normalized = tuple(_normalize_alias(alias) for alias in self.aliases)
        if len(set(normalized)) != len(normalized):
            raise GdeltAggregateError("MAPPING_AMBIGUOUS", "aliases collide")
        if not self.entity_mapping_version or len(self.entity_mapping_version) > 64:
            raise GdeltAggregateError("MAPPING_AMBIGUOUS", "mapping version is invalid")
        if _SYMBOL_PATTERN.fullmatch(self.symbol) is None:
            raise GdeltAggregateError("MAPPING_AMBIGUOUS", "symbol is invalid")

    @property
    def definition_hash(self) -> str:
        """query identity의 canonical hash를 계산하되 raw user input은 포함하지 않는다."""

        identity = {
            "aliases": list(self.aliases),
            "entityMappingVersion": self.entity_mapping_version,
            "queryRegistryId": self.query_registry_id,
            "symbol": self.symbol,
        }
        return hashlib.sha256(canonical_json_bytes(identity)).hexdigest()


class QueryRegistry:
    """alias·ticker collision을 시작 시점에 거부하는 immutable query registry다."""

    def __init__(self, definitions: tuple[QueryDefinition, ...]) -> None:
        if not definitions:
            raise GdeltAggregateError("MAPPING_AMBIGUOUS", "query registry is empty")
        by_id: dict[str, QueryDefinition] = {}
        aliases: set[str] = set()
        symbols: set[str] = set()
        for definition in definitions:
            if definition.query_registry_id in by_id:
                raise GdeltAggregateError("MAPPING_AMBIGUOUS", "query ids collide")
            normalized_aliases = {_normalize_alias(alias) for alias in definition.aliases}
            if aliases & normalized_aliases or definition.symbol in symbols:
                raise GdeltAggregateError("MAPPING_AMBIGUOUS", "alias or symbol collides")
            by_id[definition.query_registry_id] = definition
            aliases.update(normalized_aliases)
            symbols.add(definition.symbol)
        self._by_id = by_id

    def get(self, query_registry_id: str) -> QueryDefinition:
        """bounded registry id를 definition으로 해석하고 미등록 값은 provider 전에 거부한다."""

        try:
            return self._by_id[query_registry_id]
        except KeyError:
            raise GdeltAggregateError("MAPPING_AMBIGUOUS", "query is not registered") from None


def _normalize_alias(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).strip().casefold()
    if not normalized or len(normalized) > 80 or len(normalized.encode("utf-8")) > 240:
        raise GdeltAggregateError("MAPPING_AMBIGUOUS", "alias is outside policy")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in normalized):
        raise GdeltAggregateError("MAPPING_AMBIGUOUS", "alias contains controls")
    return normalized
