from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Final, cast

from app.data.ecos.errors import ECOSApplicationError, ECOSDiagnostic, ECOSParseError
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
_ITEM_CODE1_GROUP_CODE: Final = "Group1"
_MISSING: Final = object()


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
    if total_count > max_rows or len(rows_value) > max_rows:
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
    candidates: list[Mapping[str, object]] = []
    for row in rows:
        stat_code = _metadata_code(
            row,
            "STAT_CODE",
            field="stat_code",
            failure_stage="candidate_scan",
            failure_reason="candidate_invalid",
        )
        if stat_code == expected_stat_code:
            candidates.append(row)
    if len(candidates) != 1:
        raise _candidate_match_error(count=len(candidates), field="stat_code")
    row = candidates[0]
    stat_code = _metadata_code(row, "STAT_CODE", field="stat_code")
    if stat_code != expected_stat_code:
        raise _field_error(field="stat_code", field_kind="mismatch")
    cycle = _metadata_code(row, "CYCLE", field="cycle")
    if cycle != "D":
        raise _field_error(field="cycle", field_kind="mismatch")
    searchable_value = row.get("SRCH_YN", _MISSING)
    if searchable_value not in {"Y", "N"}:
        raise _field_error(
            field="searchable",
            field_kind=_metadata_field_kind(searchable_value, max_length=1) or "mismatch",
        )
    return StatisticTableMetadata(
        stat_code=stat_code,
        name=_metadata_text(row, "STAT_NAME", field=None),
        cycle=cycle,
        searchable=searchable_value == "Y",
    )


def parse_statistic_item_list(
    payload: Mapping[str, object],
    *,
    expected_stat_code: str,
    expected_item_code: str,
    expected_cycle: str,
) -> StatisticItemMetadata:
    """StatisticItemList에서 ITEM_CODE1·주기까지 일치하는 metadata 한 건만 추출한다.

    StatisticSearch가 첫 번째 항목 차원만 전송하므로 Group1 이외의 같은 ITEM_CODE와
    다른 주기 행은 후보에서 제외하고, 완전 identity 중복은 임의 선택하지 않는다.
    """
    if expected_cycle != "D":
        raise ECOSParseError(_INVALID_RESPONSE)

    rows = _metadata_rows(payload, "StatisticItemList")
    primary_item_candidates: list[tuple[Mapping[str, object], str]] = []
    candidates: list[Mapping[str, object]] = []
    for row in rows:
        stat_code = _metadata_code(
            row,
            "STAT_CODE",
            field="stat_code",
            failure_stage="candidate_scan",
            failure_reason="candidate_invalid",
        )
        item_code = _metadata_code(
            row,
            "ITEM_CODE",
            field="item_code",
            failure_stage="candidate_scan",
            failure_reason="candidate_invalid",
        )
        group_code = _metadata_code(
            row,
            "GRP_CODE",
            field="item_code",
            failure_stage="candidate_scan",
            failure_reason="candidate_invalid",
        )
        cycle = _metadata_code(
            row,
            "CYCLE",
            field="cycle",
            failure_stage="candidate_scan",
            failure_reason="candidate_invalid",
        )
        if (
            stat_code == expected_stat_code
            and group_code == _ITEM_CODE1_GROUP_CODE
            and item_code == expected_item_code
        ):
            primary_item_candidates.append((row, cycle))
            if cycle == expected_cycle:
                candidates.append(row)
    if len(candidates) != 1:
        if len(primary_item_candidates) == 1 and not candidates:
            raise _field_error(field="cycle", field_kind="mismatch")
        raise _candidate_match_error(count=len(candidates), field="item_code")
    row = candidates[0]
    stat_code = _metadata_code(row, "STAT_CODE", field="stat_code")
    group_code = _metadata_code(row, "GRP_CODE", field="item_code")
    item_code = _metadata_code(row, "ITEM_CODE", field="item_code")
    cycle = _metadata_code(row, "CYCLE", field="cycle")
    if stat_code != expected_stat_code:
        raise _field_error(field="stat_code", field_kind="mismatch")
    if item_code != expected_item_code:
        raise _field_error(field="item_code", field_kind="mismatch")
    if group_code != _ITEM_CODE1_GROUP_CODE:
        raise _field_error(field="item_code", field_kind="mismatch")
    if cycle != expected_cycle:
        raise _field_error(field="cycle", field_kind="mismatch")
    return StatisticItemMetadata(
        stat_code=stat_code,
        item_code=item_code,
        name=_metadata_text(row, "ITEM_NAME", field="item_name"),
        cycle=cycle,
        unit=_metadata_text(row, "UNIT_NAME", field="unit_name"),
    )


def _parse_result(value: object) -> StatisticSearchPage:
    result = _application_result(value)
    code = _application_code(result.get("CODE", _MISSING))
    if _APPLICATION_CODE.fullmatch(code) is None:
        raise _application_parse_error()
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
        diagnostic=ECOSDiagnostic(
            failure_stage="application_envelope",
            failure_reason="application_error",
        ),
    )


def raise_for_ecos_application_error(payload: Mapping[str, object]) -> None:
    """모든 ECOS service의 top-level RESULT 오류를 동일한 stable taxonomy로 변환한다."""
    if "RESULT" not in payload:
        return
    result = _application_result(payload["RESULT"])
    code = _application_code(result.get("CODE", _MISSING))
    if _APPLICATION_CODE.fullmatch(code) is None:
        raise _application_parse_error()
    if code == "INFO-200":
        return
    raise ECOSApplicationError(
        code,
        retryable=code in _RETRYABLE_CODES,
        cooldown_seconds=_COOLDOWN_SECONDS.get(code, 0),
        diagnostic=ECOSDiagnostic(
            failure_stage="application_envelope",
            failure_reason="application_error",
        ),
    )


def _metadata_rows(
    payload: Mapping[str, object],
    envelope_name: str,
) -> tuple[Mapping[str, object], ...]:
    if envelope_name not in payload:
        raise ECOSParseError(
            _INVALID_RESPONSE,
            diagnostic=ECOSDiagnostic(
                failure_stage="metadata_envelope",
                failure_reason="metadata_envelope_missing",
            ),
        )
    envelope_value = payload[envelope_name]
    if not isinstance(envelope_value, Mapping):
        raise ECOSParseError(
            _INVALID_RESPONSE,
            diagnostic=ECOSDiagnostic(
                failure_stage="metadata_envelope",
                failure_reason="metadata_envelope_invalid",
            ),
        )
    envelope = cast(Mapping[str, object], envelope_value)
    total_value = envelope.get("list_total_count", _MISSING)
    if isinstance(total_value, bool) or not isinstance(total_value, int) or total_value < 1:
        raise ECOSParseError(
            _INVALID_RESPONSE,
            diagnostic=ECOSDiagnostic(
                failure_stage="pagination",
                failure_reason="pagination_invalid",
                field="list_total_count",
                field_kind=_pagination_field_kind(total_value),
            ),
        )
    total_count = total_value
    rows_value = envelope.get("row", _MISSING)
    if not isinstance(rows_value, list):
        raise ECOSParseError(
            _INVALID_RESPONSE,
            diagnostic=ECOSDiagnostic(
                failure_stage="pagination",
                failure_reason="pagination_invalid",
                list_total_count=total_count,
                field="row",
                field_kind=_container_field_kind(rows_value),
            ),
        )
    expected_page_size = min(total_count, _MAX_METADATA_ROWS)
    row_count = len(rows_value)
    if row_count != expected_page_size:
        raise ECOSParseError(
            _INVALID_RESPONSE,
            diagnostic=ECOSDiagnostic(
                failure_stage="pagination",
                failure_reason="pagination_invalid",
                list_total_count=total_count,
                row_count=row_count,
                expected_page_size=expected_page_size,
                field="row",
                field_kind="truncated" if row_count < expected_page_size else "mismatch",
            ),
        )
    rows: list[Mapping[str, object]] = []
    for row in rows_value:
        if not isinstance(row, Mapping):
            raise ECOSParseError(
                _INVALID_RESPONSE,
                diagnostic=ECOSDiagnostic(
                    failure_stage="candidate_scan",
                    failure_reason="candidate_invalid",
                    list_total_count=total_count,
                    row_count=row_count,
                    expected_page_size=expected_page_size,
                    field="row",
                    field_kind="wrong_type",
                ),
            )
        rows.append(cast(Mapping[str, object], row))
    return tuple(rows)


def _metadata_code(
    row: Mapping[str, object],
    key: str,
    *,
    field: str,
    failure_stage: str = "field_validation",
    failure_reason: str = "field_invalid",
) -> str:
    value = row.get(key, _MISSING)
    kind = _metadata_field_kind(value, max_length=_MAX_CODE_CHARS)
    if kind is not None:
        raise ECOSParseError(
            _INVALID_RESPONSE,
            diagnostic=ECOSDiagnostic(
                failure_stage=failure_stage,
                failure_reason=failure_reason,
                field=field,
                field_kind=kind,
            ),
        )
    return cast(str, value)


def _metadata_text(
    row: Mapping[str, object],
    key: str,
    *,
    field: str | None,
) -> str:
    value = row.get(key, _MISSING)
    kind = _metadata_field_kind(value, max_length=_MAX_METADATA_TEXT_CHARS)
    if kind is not None:
        raise ECOSParseError(
            _INVALID_RESPONSE,
            diagnostic=ECOSDiagnostic(
                failure_stage="field_validation",
                failure_reason="field_invalid",
                field=field,
                field_kind=kind,
            ),
        )
    return cast(str, value)


def _metadata_field_kind(value: object, *, max_length: int) -> str | None:
    if value is _MISSING:
        return "missing"
    if value is None:
        return "null"
    if not isinstance(value, str):
        return "wrong_type"
    if not value:
        return "empty"
    if value != value.strip():
        return "untrimmed"
    if len(value) > max_length:
        return "too_long"
    return None


def _pagination_field_kind(value: object) -> str:
    if value is _MISSING:
        return "missing"
    if value is None:
        return "null"
    if isinstance(value, bool) or not isinstance(value, int):
        return "wrong_type"
    return "mismatch"


def _container_field_kind(value: object) -> str:
    if value is _MISSING:
        return "missing"
    if value is None:
        return "null"
    return "wrong_type"


def _candidate_match_error(*, count: int, field: str) -> ECOSParseError:
    duplicate = count > 1
    return ECOSParseError(
        _INVALID_RESPONSE,
        diagnostic=ECOSDiagnostic(
            failure_stage="candidate_match",
            failure_reason="candidate_duplicate" if duplicate else "candidate_not_found",
            candidate_match_count=count,
            field=field,
            field_kind="duplicate" if duplicate else "not_found",
        ),
    )


def _field_error(*, field: str, field_kind: str) -> ECOSParseError:
    return ECOSParseError(
        _INVALID_RESPONSE,
        diagnostic=ECOSDiagnostic(
            failure_stage="field_validation",
            failure_reason="field_invalid",
            field=field,
            field_kind=field_kind,
        ),
    )


def _application_result(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _application_parse_error()
    return cast(Mapping[str, object], value)


def _application_code(value: object) -> str:
    if value is _MISSING or not isinstance(value, str) or not value or len(value) > _MAX_CODE_CHARS:
        raise _application_parse_error()
    return value


def _application_parse_error() -> ECOSParseError:
    return ECOSParseError(
        _INVALID_RESPONSE,
        diagnostic=ECOSDiagnostic(
            failure_stage="application_envelope",
            failure_reason="application_error",
        ),
    )


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
