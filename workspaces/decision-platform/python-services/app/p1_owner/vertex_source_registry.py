"""Vertex 뉴스 거부권이 근거로 인정할 출처를 host가 판정한다.

`vertex_veto.py`는 근거마다 `sourceType`이 OFFICIAL_PRIMARY 또는 REGISTERED_INDEPENDENT인지를
신뢰 host가 관측한 사실로 요구한다. 모델이 "공식 발표"라고 적는 것은 근거가 아니므로, grounding
URI의 host name을 등록 카탈로그에 대조해 여기서 정한다. 카탈로그에 없는 domain은 근거가 아니다.

이 모듈은 provider를 호출하지 않는다. credential도 읽지 않는다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Final
from urllib.parse import urlsplit

from app.data._shared.repository_root import repository_root

_CATALOG_PATH: Final = (
    repository_root(__file__, 5) / "contracts/catalogs/p1-vertex-news-sources.v1.json"
)
_SOURCE_TYPES: Final = frozenset({"OFFICIAL_PRIMARY", "REGISTERED_INDEPENDENT"})


class VertexSourceRegistryError(RuntimeError):
    """등록 카탈로그가 없거나 형식이 어긋날 때 발생한다."""


@lru_cache(maxsize=1)
def registered_sources() -> dict[str, tuple[str, str]]:
    """domain -> (sourceId, sourceType). 카탈로그 자체가 단일 진실이다."""

    try:
        catalog = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VertexSourceRegistryError("vertex news source catalog is unavailable") from error
    entries = catalog.get("sources")
    if not isinstance(entries, list) or not entries:
        raise VertexSourceRegistryError("vertex news source catalog is empty")
    mapping: dict[str, tuple[str, str]] = {}
    for item in entries:
        if not isinstance(item, dict):
            raise VertexSourceRegistryError("vertex news source entry is invalid")
        domain = item.get("domain")
        source_id = item.get("sourceId")
        source_type = item.get("sourceType")
        if (
            not isinstance(domain, str)
            or not isinstance(source_id, str)
            or source_type not in _SOURCE_TYPES
            or domain in mapping
        ):
            raise VertexSourceRegistryError("vertex news source entry is invalid")
        mapping[domain] = (source_id, str(source_type))
    return mapping


def registered_source_for_uri(uri: str) -> tuple[str, str] | None:
    """grounding URI 하나를 등록 출처로 판정한다. 아니면 None이다.

    https만 인정한다. 등록 domain의 하위 domain은 인정하되 문자열 접미사 우연 일치는 막는다.
    """

    if not isinstance(uri, str) or len(uri) > 2_048:
        return None
    try:
        parts = urlsplit(uri)
    except ValueError:
        return None
    if parts.scheme != "https" or parts.username or parts.password:
        return None
    host = parts.hostname
    if host is None:
        return None
    host = host.rstrip(".").lower()
    if not host or ".." in host:
        return None
    catalog = registered_sources()
    # 더 구체적인 domain이 먼저 이기게 해서 kind.krx.co.kr이 krx.co.kr로 뭉개지지 않게 한다.
    for domain in sorted(catalog, key=len, reverse=True):
        if host == domain or host.endswith("." + domain):
            return catalog[domain]
    return None
