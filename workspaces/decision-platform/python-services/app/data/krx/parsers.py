from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Final, cast

from app.data.krx.catalog import ENABLED_UNIVERSE_ENDPOINTS, KrxEndpoint, KrxMarket
from app.data.krx.errors import KrxParseError


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
        raise KrxParseError() from None

    raw_rows = payload.get(endpoint.response_block)
    if not isinstance(raw_rows, list) or not raw_rows or len(raw_rows) > _MAX_ROWS:
        raise KrxParseError() from None

    expected_date = requested_date.strftime("%Y%m%d")
    seen_symbols: set[str] = set()
    parsed_rows: list[KrxDailyRow] = []
    for raw_row in raw_rows:
        row = _strict_string_row(raw_row)
        if row["BAS_DD"] != expected_date or row["MKT_NM"] != endpoint.market:
            raise KrxParseError() from None

        symbol = row["ISU_CD"]
        name = row["ISU_NM"]
        if (
            _ASCII_SYMBOL.fullmatch(symbol) is None
            or not name
            or name != name.strip()
            or len(name) > 256
            or any(unicodedata.category(character).startswith("C") for character in name)
        ):
            raise KrxParseError() from None
        if symbol in seen_symbols:
            raise KrxParseError() from None

        trading_value = _nonnegative_int64(row["ACC_TRDVAL"])
        market_cap = _nonnegative_int64(row["MKTCAP"])
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


def _strict_string_row(value: object) -> Mapping[str, str]:
    if not isinstance(value, Mapping) or set(value) != _OFFICIAL_DAILY_FIELDS:
        raise KrxParseError() from None
    if any(not isinstance(child, str) for child in value.values()):
        raise KrxParseError() from None
    return cast(Mapping[str, str], value)


def _nonnegative_int64(value: str) -> int:
    # 공식 명세의 unavailable 표기 '-'와 실제 무거래 0은 후보 필터가 처리할 수 있게 0으로 정규화한다.
    if value == "-":
        return 0
    if _NONNEGATIVE_INTEGER.fullmatch(value) is None:
        raise KrxParseError() from None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        raise KrxParseError() from None
    if not 0 <= parsed <= _MAX_INT64:
        raise KrxParseError() from None
    return parsed
