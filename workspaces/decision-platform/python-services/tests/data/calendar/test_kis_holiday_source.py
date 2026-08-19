"""KIS 휴장일 권위 생산자가 승인 상한과 mode 경계를 넘지 않는지 고정한다."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.data.calendar.errors import AdapterValidationError
from app.data.calendar.kis_holiday_source import (
    MAX_KIS_HOLIDAY_CALLS,
    KISHolidayAuthority,
)


class _Client:
    """`holiday_response`만 노출하는 최소 client다."""

    def __init__(self, responses: dict[date, dict[str, Any] | None]) -> None:
        self._responses = responses
        self.calls: list[date] = []

    def holiday_response(self, base_date: date) -> dict[str, Any] | None:
        self.calls.append(base_date)
        return self._responses[base_date]


def _response(day: date, *, opnd_yn: str) -> dict[str, Any]:
    return {
        "rt_cd": "0",
        "output": [{"bass_dt": day.strftime("%Y%m%d"), "opnd_yn": opnd_yn}],
    }


def test_kis_holiday_authority_confirms_closed_session_from_opnd_yn() -> None:
    day = date(2026, 6, 3)
    client = _Client({day: _response(day, opnd_yn="N")})
    authority = KISHolidayAuthority(client)

    observation = authority.observe(day)

    assert observation.is_open is False
    assert observation.source_id == "kis-holiday-ctca0903r"
    assert observation.tr_id == "CTCA0903R"
    assert observation.tier == 1
    assert authority.calls == 1


def test_kis_holiday_authority_reuses_same_day_without_another_physical_call() -> None:
    day = date(2026, 6, 3)
    client = _Client({day: _response(day, opnd_yn="N")})
    authority = KISHolidayAuthority(client)

    first = authority.observe(day)
    second = authority.observe(day)

    assert first == second
    assert client.calls == [day]
    assert authority.calls == 1


def test_kis_holiday_authority_stops_at_the_approved_call_cap() -> None:
    days = [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)]
    client = _Client({day: _response(day, opnd_yn="Y") for day in days})
    authority = KISHolidayAuthority(client, max_calls=2)

    authority.observe(days[0])
    authority.observe(days[1])
    with pytest.raises(AdapterValidationError, match="call cap is exhausted"):
        authority.observe(days[2])

    assert client.calls == days[:2]
    assert authority.calls == 2


def test_kis_holiday_authority_rejects_cap_outside_the_approved_bound() -> None:
    client = _Client({})
    for invalid in (0, MAX_KIS_HOLIDAY_CALLS + 1):
        with pytest.raises(AdapterValidationError, match="call cap is outside"):
            KISHolidayAuthority(client, max_calls=invalid)


def test_mock_mode_skip_never_claims_calendar_authority() -> None:
    day = date(2026, 6, 3)
    client = _Client({day: None})
    authority = KISHolidayAuthority(client)

    with pytest.raises(AdapterValidationError, match="unavailable in this mode"):
        authority.observe(day)


def test_missing_opnd_yn_fails_instead_of_defaulting_to_open() -> None:
    day = date(2026, 6, 3)
    client = _Client(
        {day: {"rt_cd": "0", "output": [{"bass_dt": day.strftime("%Y%m%d")}]}}
    )
    authority = KISHolidayAuthority(client)

    with pytest.raises(AdapterValidationError, match="opnd_yn is required"):
        authority.observe(day)
