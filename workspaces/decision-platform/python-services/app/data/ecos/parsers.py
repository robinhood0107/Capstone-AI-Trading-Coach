from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Final, cast

from app.data.ecos.errors import ECOSApplicationError, ECOSParseError
from app.data.ecos.models import (
    ECOSObservation,
    StatisticItemMetadata,
    StatisticSearchPage,
    StatisticTableMetadata,
)

_INVALID_RESPONSE: Final = "invalid ECOS response"
_APPLICATION_CODE = re.compile(r"^(?:INFO|ERROR)-[0-9]{3}$")
_RETRYABLE_CODES: Final = frozenset({"ERROR-500", "ERROR-600", "ERROR-601"})
_COOLDOWN_SECONDS: Final = {"ERROR-602": 1_800}
_MAX_CODE_CHARS: Final = 20
_MAX_DECIMAL_CHARS: Final = 64
_MAX_SIGNIFICANT_DIGITS: Final = 28
_MAX_EXPONENT_MAGNITUDE: Final = 18
_MAX_METADATA_ROWS: Final = 200
_MAX_METADATA_TEXT_CHARS: Final = 256


def parse_statistic_search(
    payload: Mapping[str, object],
    *,
    expected_stat_code: str,
    expected_item_code1: str,
    expected_cycle: str,
    max_rows: int = 400,
) -> StatisticSearchPage:
    """ECOS StatisticSearch 응답을 allowlist 관측치로 정규화하고 provider 원문은 버린다.

    시계열 identity, 일자, Decimal 정밀도, row 상한과 중복 불변식을 검증하며 오류에는
    provider message, row 값, URL 또는 credential을 포함하지 않는다.
    """
    if max_rows < 1:
        raise ECOSParseError("ECOS response exceeded row limit")
    if expected_cycle != "D":
        raise ECOSParseError(_INVALID_RESPONSE)

    if "RESULT" in payload:
        return _parse_result(payload["RESULT"])

    envelope = _mapping(payload.get("StatisticSearch"))
    total_count = _non_negative_int(envelope.get("list_total_count"))
    rows_value = envelope.get("row")
    if not isinstance(rows_value, list):
        raise ECOSParseError(_INVALID_RESPONSE)
    if total_count > max_rows or len(rows_value) > _MAX_METADATA_ROWS:
        raise ECOSParseError("ECOS response exceeded row limit")
    if total_count < len(rows_value):
        raise ECOSParseError(_INVALID_RESPONSE)

    by_time: dict[str, ECOSObservation] = {}
    duplicate_count = 0
    for raw_row in rows_value:
        row = _mapping(raw_row)
        if _bounded_code(row.get("STAT_CODE")) != expected_stat_code:
            raise ECOSParseError(_INVALID_RESPONSE)
        if _bounded_code(row.get("ITEM_CODE1")) != expected_item_code1:
            raise ECOSParseError(_INVALID_RESPONSE)

        time = _calendar_day(row.get("TIME"))
        value = _canonical_decimal(row.get("DATA_VALUE"))
        existing = by_time.get(time)
        if existing is None:
            by_time[time] = ECOSObservation(time=time, value=value)
            continue
        if existing.value != value:
            raise ECOSParseError("conflicting duplicate ECOS observation")
        duplicate_count += 1

    observations = [by_time[key] for key in sorted(by_time)]
    return StatisticSearchPage(
        status="complete" if observations else "empty",
        total_count=total_count,
        observations=observations,
        duplicate_count=duplicate_count,
        retryable=False,
    )


def parse_statistic_table_list(
    payload: Mapping[str, object],
    *,
    expected_stat_code: str,
) -> StatisticTableMetadata:
    """StatisticTableList에서 승인 대상 코드·명칭·주기·검색 가능 여부만 추출한다."""
    rows = _metadata_rows(payload, "StatisticTableList")
    candidates = [row for row in rows if _bounded_code(row.get("STAT_CODE")) == expected_stat_code]
    if len(candidates) != 1:
        raise ECOSParseError(_INVALID_RESPONSE)
    row = candidates[0]
    stat_code = _bounded_code(row.get("STAT_CODE"))
    cycle = _bounded_code(row.get("CYCLE"))
    searchable_value = row.get("SRCH_YN")
    if stat_code != expected_stat_code or cycle != "D" or searchable_value not in {"Y", "N"}:
        raise ECOSParseError(_INVALID_RESPONSE)
    return StatisticTableMetadata(
        stat_code=stat_code,
        name=_bounded_metadata_text(row.get("STAT_NAME")),
        cycle=cycle,
        searchable=searchable_value == "Y",
    )


def parse_statistic_item_list(
    payload: Mapping[str, object],
    *,
    expected_stat_code: str,
    expected_item_code: str,
) -> StatisticItemMetadata:
    """StatisticItemList에서 승인 대상 series item의 allowlist metadata만 추출한다."""
    rows = _metadata_rows(payload, "StatisticItemList")
    candidates = [
        row
        for row in rows
        if _bounded_code(row.get("STAT_CODE")) == expected_stat_code
        and _bounded_code(row.get("ITEM_CODE")) == expected_item_code
    ]
    if len(candidates) != 1:
        raise ECOSParseError(_INVALID_RESPONSE)
    row = candidates[0]
    stat_code = _bounded_code(row.get("STAT_CODE"))
    item_code = _bounded_code(row.get("ITEM_CODE"))
    cycle = _bounded_code(row.get("CYCLE"))
    if stat_code != expected_stat_code or item_code != expected_item_code or cycle != "D":
        raise ECOSParseError(_INVALID_RESPONSE)
    return StatisticItemMetadata(
        stat_code=stat_code,
        item_code=item_code,
        name=_bounded_metadata_text(row.get("ITEM_NAME")),
        cycle=cycle,
        unit=_bounded_metadata_text(row.get("UNIT_NAME")),
    )


def _parse_result(value: object) -> StatisticSearchPage:
    result = _mapping(value)
    code = _bounded_code(result.get("CODE"))
    if _APPLICATION_CODE.fullmatch(code) is None:
        raise ECOSParseError(_INVALID_RESPONSE)
    if code == "INFO-200":
        return StatisticSearchPage(
            status="empty",
            total_count=0,
            observations=[],
            duplicate_count=0,
            retryable=False,
        )
    raise ECOSApplicationError(
        code,
        retryable=code in _RETRYABLE_CODES,
        cooldown_seconds=_COOLDOWN_SECONDS.get(code, 0),
    )


def raise_for_ecos_application_error(payload: Mapping[str, object]) -> None:
    """모든 ECOS service의 top-level RESULT 오류를 동일한 stable taxonomy로 변환한다."""
    if "RESULT" not in payload:
        return
    result = _mapping(payload["RESULT"])
    code = _bounded_code(result.get("CODE"))
    if _APPLICATION_CODE.fullmatch(code) is None:
        raise ECOSParseError(_INVALID_RESPONSE)
    if code == "INFO-200":
        return
    raise ECOSApplicationError(
        code,
        retryable=code in _RETRYABLE_CODES,
        cooldown_seconds=_COOLDOWN_SECONDS.get(code, 0),
    )


def _metadata_rows(
    payload: Mapping[str, object],
    envelope_name: str,
) -> tuple[Mapping[str, object], ...]:
    envelope = _mapping(payload.get(envelope_name))
    total_count = _non_negative_int(envelope.get("list_total_count"))
    rows_value = envelope.get("row")
    if (
        not isinstance(rows_value, list)
        or total_count < 1
        or len(rows_value) > _MAX_METADATA_ROWS
        # metadata 요청은 1..200 첫 page로 고정되므로 truncated page도 거부한다.
        or len(rows_value) != min(total_count, _MAX_METADATA_ROWS)
    ):
        raise ECOSParseError(_INVALID_RESPONSE)
    return tuple(_mapping(row) for row in rows_value)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ECOSParseError(_INVALID_RESPONSE)
    return cast(Mapping[str, object], value)


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ECOSParseError(_INVALID_RESPONSE)
    return value


def _bounded_code(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_CODE_CHARS:
        raise ECOSParseError(_INVALID_RESPONSE)
    return value


def _bounded_metadata_text(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > _MAX_METADATA_TEXT_CHARS
    ):
        raise ECOSParseError(_INVALID_RESPONSE)
    return value


def _calendar_day(value: object) -> str:
    if not isinstance(value, str) or len(value) != 8 or not value.isascii() or not value.isdigit():
        raise ECOSParseError(_INVALID_RESPONSE)
    try:
        parsed = datetime.strptime(value, "%Y%m%d")
    except ValueError:
        raise ECOSParseError(_INVALID_RESPONSE) from None
    if parsed.strftime("%Y%m%d") != value:
        raise ECOSParseError(_INVALID_RESPONSE)
    return value


def _canonical_decimal(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > _MAX_DECIMAL_CHARS
        or "," in value
    ):
        raise ECOSParseError(_INVALID_RESPONSE)
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise ECOSParseError(_INVALID_RESPONSE) from None
    if not parsed.is_finite():
        raise ECOSParseError(_INVALID_RESPONSE)
    decimal_tuple = parsed.as_tuple()
    if len(decimal_tuple.digits) > _MAX_SIGNIFICANT_DIGITS:
        raise ECOSParseError(_INVALID_RESPONSE)
    exponent = cast(int, decimal_tuple.exponent)
    if abs(exponent) > _MAX_EXPONENT_MAGNITUDE:
        raise ECOSParseError(_INVALID_RESPONSE)
    if parsed.is_zero():
        return "0"
    rendered = format(parsed, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered
