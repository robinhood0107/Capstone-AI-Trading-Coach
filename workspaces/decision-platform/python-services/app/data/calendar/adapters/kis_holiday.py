from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from app.data.calendar.errors import AdapterValidationError
from app.data.calendar.models import KISHolidayObservation


class KISHolidayAdapter:
    """기존 S1.1 KIS client/공유 limiter를 주입받아 날짜별 CTCA0903R 결과를 한 번만 읽는다."""

    def __init__(self, fetch: Callable[[date], dict[str, Any]]) -> None:
        self._fetch = fetch
        self._cache: dict[date, KISHolidayObservation] = {}

    def observe(self, day: date) -> KISHolidayObservation:
        """같은 날짜 재조회는 provider 호출을 만들지 않고 sanitized observation을 재사용한다."""
        cached = self._cache.get(day)
        if cached is not None:
            return cached
        observation = parse_kis_holiday(self._fetch(day), requested_day=day)
        self._cache[day] = observation
        return observation


def parse_kis_holiday(
    response: dict[str, Any],
    *,
    requested_day: date,
) -> KISHolidayObservation:
    """CTCA0903R에서 exact requested date의 opnd_yn만 운영 is_open으로 정규화한다."""
    if response.get("rt_cd") not in (None, "0", 0):
        raise AdapterValidationError("KIS holiday provider failure")
    rows_value = response.get("output")
    if rows_value is None:
        rows_value = response.get("output2")
    if isinstance(rows_value, dict):
        rows = [rows_value]
    elif isinstance(rows_value, list):
        rows = rows_value
    else:
        raise AdapterValidationError("KIS holiday response shape is invalid")
    matches: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise AdapterValidationError("KIS holiday response shape is invalid")
        raw_day = row.get("bass_dt")
        try:
            parsed_day = datetime.strptime(str(raw_day), "%Y%m%d").date()
        except (TypeError, ValueError):
            raise AdapterValidationError("KIS holiday date is invalid") from None
        if parsed_day == requested_day:
            matches.append(row)
    if len(matches) != 1:
        raise AdapterValidationError("KIS holiday requested date must have exactly one row")
    row = matches[0]
    opnd_yn = row.get("opnd_yn")
    if opnd_yn not in {"Y", "N"}:
        raise AdapterValidationError("KIS holiday opnd_yn is required")
    return KISHolidayObservation(
        day=requested_day,
        is_open=opnd_yn == "Y",
        business_day_flag=_optional_flag(row.get("bzdy_yn")),
        trading_day_flag=_optional_flag(row.get("tr_day_yn")),
        settlement_day_flag=_optional_flag(row.get("sttl_day_yn")),
        source_id="kis-holiday-ctca0903r",
        origin_group="kis",
        tier=1,
        tr_id="CTCA0903R",
    )


def _optional_flag(value: object) -> bool | None:
    if value in (None, ""):
        return None
    if value not in {"Y", "N"}:
        raise AdapterValidationError("KIS holiday auxiliary flag is invalid")
    return value == "Y"
