from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Final, NoReturn, cast

from app.data.krx.catalog import ENABLED_UNIVERSE_ENDPOINTS, KrxEndpoint, KrxMarket
from app.data.krx.errors import KrxParseError, KrxValidationDiagnostic


_OFFICIAL_DAILY_FIELDS: Final = frozenset(
    {
        "BAS_DD",
        "ISU_CD",
        "ISU_NM",
        "MKT_NM",
        "SECT_TP_NM",
        "TDD_CLSPRC",
        "CMPPREVDD_PRC",
        "FLUC_RT",
        "TDD_OPNPRC",
        "TDD_HGPRC",
        "TDD_LWPRC",
        "ACC_TRDVOL",
        "ACC_TRDVAL",
        "MKTCAP",
        "LIST_SHRS",
    }
)
_ASCII_SYMBOL = re.compile(r"[0-9]{6}")
_NONNEGATIVE_INTEGER = re.compile(r"[0-9]+")
_MAX_ROWS: Final = 5_000
_MAX_INT64: Final = 9_223_372_036_854_775_807


@dataclass(frozen=True, slots=True)
class KrxDailyRow:
    """KRX 일별매매정보에서 universe ranking에 필요한 안전한 필드만 보존한다."""

    as_of_date: date
    symbol: str
    name: str
    market: KrxMarket
    trading_value: int
    market_cap: int


def parse_daily_response(
    payload: Mapping[str, object],
    *,
    endpoint: KrxEndpoint,
    requested_date: date,
) -> tuple[KrxDailyRow, ...]:
    """공식 15필드 응답을 요청 시장·날짜와 대조해 immutable universe 행으로 변환한다.

    provider 원문과 잘못된 scalar는 오류에 포함하지 않으며 한 행이라도 계약을 벗어나면
    부분 결과 없이 전체 payload를 거부한다.
    """
    if endpoint not in ENABLED_UNIVERSE_ENDPOINTS or type(requested_date) is not date:
        raise KrxParseError() from None
    if set(payload) != {endpoint.response_block}:
        _fail(
            "envelope_key_mismatch",
            top_level_type="object",
            top_level_key_count=min(len(payload), 16),
            expected_block_present=endpoint.response_block in payload,
        )

    raw_rows = payload.get(endpoint.response_block)
    if not isinstance(raw_rows, list):
        _fail(
            "rows_not_list",
            top_level_type="object",
            top_level_key_count=1,
            expected_block_present=True,
            row_container_type=_container_type(raw_rows),
        )
    if not raw_rows:
        _fail(
            "rows_empty",
            top_level_type="object",
            top_level_key_count=1,
            expected_block_present=True,
            row_container_type="list",
            row_count=0,
        )
    if len(raw_rows) > _MAX_ROWS:
        _fail(
            "rows_too_many",
            top_level_type="object",
            top_level_key_count=1,
            expected_block_present=True,
            row_container_type="list",
            row_count=min(len(raw_rows), _MAX_ROWS + 1),
        )

    expected_date = requested_date.strftime("%Y%m%d")
    seen_symbols: set[str] = set()
    parsed_rows: list[KrxDailyRow] = []
    for row_ordinal, raw_row in enumerate(raw_rows, start=1):
        row = _strict_string_row(raw_row, row_ordinal=row_ordinal)
        if row["BAS_DD"] != expected_date:
            _fail(
                "row_date_mismatch",
                row_ordinal=row_ordinal,
                official_field="BAS_DD",
            )
        if row["MKT_NM"] != endpoint.market:
            _fail(
                "row_market_mismatch",
                row_ordinal=row_ordinal,
                official_field="MKT_NM",
            )

        symbol = row["ISU_CD"]
        name = row["ISU_NM"]
        if _ASCII_SYMBOL.fullmatch(symbol) is None:
            _fail(
                "row_symbol_invalid",
                row_ordinal=row_ordinal,
                official_field="ISU_CD",
            )
        if (
            not name
            or name != name.strip()
            or len(name) > 256
            or any(unicodedata.category(character).startswith("C") for character in name)
        ):
            _fail(
                "row_name_invalid",
                row_ordinal=row_ordinal,
                official_field="ISU_NM",
            )
        if symbol in seen_symbols:
            _fail(
                "row_symbol_duplicate",
                row_ordinal=row_ordinal,
                official_field="ISU_CD",
            )

        trading_value = _nonnegative_int64(
            row["ACC_TRDVAL"],
            row_ordinal=row_ordinal,
            official_field="ACC_TRDVAL",
        )
        market_cap = _nonnegative_int64(
            row["MKTCAP"],
            row_ordinal=row_ordinal,
            official_field="MKTCAP",
        )
        seen_symbols.add(symbol)
        parsed_rows.append(
            KrxDailyRow(
                as_of_date=requested_date,
                symbol=symbol,
                name=name,
                market=endpoint.market,
                trading_value=trading_value,
                market_cap=market_cap,
            )
        )
    return tuple(parsed_rows)


def _strict_string_row(value: object, *, row_ordinal: int) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        _fail("row_not_object", row_ordinal=row_ordinal)
    keys = set(value)
    missing = _OFFICIAL_DAILY_FIELDS - keys
    unexpected = keys - _OFFICIAL_DAILY_FIELDS
    if missing or unexpected:
        official_field = next(iter(missing)) if len(missing) == 1 else None
        _fail(
            "row_field_set_mismatch",
            row_ordinal=row_ordinal,
            official_field=official_field,
            missing_official_field_count=len(missing),
            unexpected_row_key_count=min(len(unexpected), 16),
        )
    for field in sorted(_OFFICIAL_DAILY_FIELDS):
        if not isinstance(value[field], str):
            _fail(
                "row_non_string",
                row_ordinal=row_ordinal,
                official_field=field,
            )
    return cast(Mapping[str, str], value)


def _nonnegative_int64(
    value: str,
    *,
    row_ordinal: int,
    official_field: str,
) -> int:
    # 공식 명세의 unavailable 표기 '-'와 실제 무거래 0은 후보 필터가 처리할 수 있게 0으로 정규화한다.
    if value == "-":
        return 0
    if _NONNEGATIVE_INTEGER.fullmatch(value) is None:
        _fail(
            "row_numeric_invalid",
            row_ordinal=row_ordinal,
            official_field=official_field,
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        _fail(
            "row_numeric_invalid",
            row_ordinal=row_ordinal,
            official_field=official_field,
        )
    if not 0 <= parsed <= _MAX_INT64:
        _fail(
            "row_numeric_invalid",
            row_ordinal=row_ordinal,
            official_field=official_field,
        )
    return parsed


def _container_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, Mapping):
        return "object"
    return "scalar"


def _fail(leaf: str, **values: object) -> NoReturn:
    raise KrxParseError(KrxValidationDiagnostic.for_leaf(leaf, **values)) from None
