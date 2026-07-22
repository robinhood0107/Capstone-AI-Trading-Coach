from __future__ import annotations

from datetime import date

import pytest

from app.data.calendar.adapters.kasi_holiday import (
    MAX_KASI_COMPRESSED_BYTES,
    MAX_KASI_DEPTH,
    MAX_KASI_ITEMS,
    MAX_KASI_NODES,
    MAX_KASI_RESPONSE_BYTES,
    MAX_KASI_TEXT_BYTES,
    MAX_KASI_TEXT_CHARS,
    KASIHolidayAdapter,
    parse_kasi_holidays,
)
from app.data.calendar.errors import AdapterValidationError, NetworkActivationError


def test_kasi_xml_parses_holiday_reason_without_market_authority() -> None:
    result = parse_kasi_holidays(
        _xml("<item><locdate>20260505</locdate><dateName>어린이날</dateName><isHoliday>Y</isHoliday></item>")
    )

    assert result == [
        {
            "date": date(2026, 5, 5),
            "reason": "어린이날",
            "can_change_is_open": False,
        }
    ]


def test_kasi_xml_empty_response_is_valid() -> None:
    assert parse_kasi_holidays(_xml("")) == []


def test_kasi_xml_safety_caps_match_the_frozen_s1_6_contract() -> None:
    assert MAX_KASI_COMPRESSED_BYTES == 256 * 1024
    assert MAX_KASI_RESPONSE_BYTES == 512 * 1024
    assert MAX_KASI_DEPTH == 8
    assert MAX_KASI_NODES == 4_096
    assert MAX_KASI_ITEMS == 128
    assert MAX_KASI_TEXT_CHARS == 2_048
    assert MAX_KASI_TEXT_BYTES == 8_192


@pytest.mark.parametrize(
    "payload",
    [
        b"<response>",
        b"<!DOCTYPE x [<!ENTITY y 'boom'>]><response>&y;</response>",
        b"<!DOCTYPE x SYSTEM 'file:///etc/passwd'><response/>",
        b"<!DOCTYPE lolz [<!ENTITY lol 'lol'><!ENTITY lol1 '&lol;&lol;'>]><response>&lol1;</response>",
    ],
)
def test_kasi_xml_rejects_malformed_dtd_entity_and_external_resource(payload: bytes) -> None:
    with pytest.raises(AdapterValidationError):
        parse_kasi_holidays(payload)


def test_kasi_xml_enforces_size_depth_node_and_text_caps() -> None:
    with pytest.raises(AdapterValidationError, match="compressed"):
        parse_kasi_holidays(_xml(""), compressed_size=MAX_KASI_COMPRESSED_BYTES + 1)
    with pytest.raises(AdapterValidationError, match="size"):
        parse_kasi_holidays(b"x" * (MAX_KASI_RESPONSE_BYTES + 1))
    with pytest.raises(AdapterValidationError, match="depth"):
        parse_kasi_holidays(("<a>" * (MAX_KASI_DEPTH + 2) + "</a>" * (MAX_KASI_DEPTH + 2)).encode())
    with pytest.raises(AdapterValidationError, match="node"):
        parse_kasi_holidays(("<response>" + "<a/>" * (MAX_KASI_NODES + 1) + "</response>").encode())
    with pytest.raises(AdapterValidationError, match="item"):
        parse_kasi_holidays(
            _xml(
                "<item><locdate>20260505</locdate><dateName>fixture</dateName></item>"
                * (MAX_KASI_ITEMS + 1)
            )
        )
    with pytest.raises(AdapterValidationError, match="text"):
        parse_kasi_holidays(_xml(f"<item><locdate>20260505</locdate><dateName>{'가' * (MAX_KASI_TEXT_CHARS + 1)}</dateName></item>"))


def test_kasi_network_is_fail_closed_until_exact_https_origin_is_verified() -> None:
    calls = 0

    def fetch(_: int) -> bytes:
        nonlocal calls
        calls += 1
        return _xml("")

    adapter = KASIHolidayAdapter(fetch, network_ready=False)
    with pytest.raises(NetworkActivationError, match="HTTPS"):
        adapter.collect(2026)
    assert calls == 0


def _xml(items: str) -> bytes:
    return (
        "<?xml version='1.0' encoding='UTF-8'?><response><body><items>"
        + items
        + "</items></body></response>"
    ).encode()
