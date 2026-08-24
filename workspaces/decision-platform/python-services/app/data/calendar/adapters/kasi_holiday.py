from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as StandardElementTree

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from app.data.calendar.errors import AdapterValidationError, NetworkActivationError

MAX_KASI_COMPRESSED_BYTES = 256 * 1024
MAX_KASI_RESPONSE_BYTES = 512 * 1024
MAX_KASI_DEPTH = 8
MAX_KASI_NODES = 4_096
MAX_KASI_ITEMS = 128
MAX_KASI_TEXT_CHARS = 2_048
MAX_KASI_TEXT_BYTES = 8_192
_XML_CONTENT_TYPES = frozenset({"application/xml", "text/xml"})


class KASIHolidayAdapter:
    """공식 exact HTTPS origin 검증 전에는 fetch callable 자체를 호출하지 않는 parser adapter다."""

    def __init__(self, fetch: Callable[[int], bytes], *, network_ready: bool) -> None:
        self._fetch = fetch
        self._network_ready = network_ready

    def collect(self, year: int) -> list[dict[str, object]]:
        """KASI가 network-ready일 때만 연도 XML을 읽고 holiday reason projection을 반환한다."""
        if not self._network_ready:
            raise NetworkActivationError("KASI exact HTTPS origin is not verified")
        return parse_kasi_holidays(self._fetch(year))


def parse_kasi_holidays(
    payload: bytes,
    *,
    content_type: str = "application/xml",
    compressed_size: int | None = None,
) -> list[dict[str, object]]:
    """defusedxml과 byte/depth/node/item/text 상한으로 KASI XML reason만 추출한다."""
    media_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    if media_type not in _XML_CONTENT_TYPES:
        raise AdapterValidationError("KASI content type is not XML")
    if compressed_size is not None and (
        compressed_size < 0 or compressed_size > MAX_KASI_COMPRESSED_BYTES
    ):
        raise AdapterValidationError("KASI compressed size exceeded the safety limit")
    if len(payload) > MAX_KASI_RESPONSE_BYTES:
        raise AdapterValidationError("KASI response size exceeded the safety limit")
    try:
        root = ElementTree.fromstring(
            payload,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except (DefusedXmlException, StandardElementTree.ParseError):
        raise AdapterValidationError("KASI XML is invalid or unsafe") from None
    _validate_tree_bounds(root)
    items = root.findall(".//item")
    if len(items) > MAX_KASI_ITEMS:
        raise AdapterValidationError("KASI item count exceeded the safety limit")
    holidays: list[dict[str, object]] = []
    for item in items:
        holiday_flag = _text(item, "isHoliday", required=False)
        if holiday_flag and holiday_flag.upper() != "Y":
            continue
        raw_date = _text(item, "locdate", required=True)
        reason = _text(item, "dateName", required=True)
        try:
            day = datetime.strptime(raw_date, "%Y%m%d").date()
        except ValueError:
            raise AdapterValidationError("KASI holiday date is invalid") from None
        holidays.append({"date": day, "reason": reason, "can_change_is_open": False})
    return sorted(holidays, key=lambda item: (str(item["date"]), str(item["reason"])))


def _validate_tree_bounds(root: Any) -> None:
    stack: list[tuple[Any, int]] = [(root, 1)]
    nodes = 0
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if nodes > MAX_KASI_NODES:
            raise AdapterValidationError("KASI node count exceeded the safety limit")
        if depth > MAX_KASI_DEPTH:
            raise AdapterValidationError("KASI XML depth exceeded the safety limit")
        _validate_text_node(node.text)
        _validate_text_node(node.tail)
        children = list(node)
        stack.extend((child, depth + 1) for child in children)


def _text(element: Any, name: str, *, required: bool) -> str:
    child = element.find(name)
    value = "" if child is None or child.text is None else child.text.strip()
    _validate_text_node(value)
    if required and not value:
        raise AdapterValidationError("KASI required XML field is missing")
    return value


def _validate_text_node(value: str | None) -> None:
    if value is None:
        return
    if len(value) > MAX_KASI_TEXT_CHARS or len(value.encode("utf-8")) > MAX_KASI_TEXT_BYTES:
        raise AdapterValidationError("KASI text exceeded the safety limit")
