from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any


class KISResponseError(RuntimeError):
    pass


@dataclass(frozen=True)
class CurrentPrice:
    symbol: str
    price: int
    open: int
    high: int
    low: int
    volume: int
    turnover: int
    previous_diff: int
    previous_rate: Decimal


@dataclass(frozen=True)
class DailyBar:
    symbol: str
    date: date
    open: int
    high: int
    low: int
    close: int
    volume: int
    turnover: int = 0


@dataclass(frozen=True)
class HolidayRow:
    date: date
    is_trading_day: bool


def parse_current_price(response: dict[str, Any], symbol: str) -> CurrentPrice:
    _ensure_success(response)
    output = response.get("output")
    if not isinstance(output, dict):
        raise KISResponseError("KIS current price response missing output")
    return CurrentPrice(
        symbol=str(output.get("stck_shrn_iscd") or symbol),
        price=_to_int(output.get("stck_prpr")),
        open=_to_int(output.get("stck_oprc")),
        high=_to_int(output.get("stck_hgpr")),
        low=_to_int(output.get("stck_lwpr")),
        volume=_to_int(output.get("acml_vol")),
        turnover=_to_int(output.get("acml_tr_pbmn")),
        previous_diff=_to_int(output.get("prdy_vrss")),
        previous_rate=_to_decimal(output.get("prdy_ctrt")),
    )


def parse_daily_bars(response: dict[str, Any], symbol: str) -> list[DailyBar]:
    _ensure_success(response)
    rows = response.get("output2") or []
    if not isinstance(rows, list):
        raise KISResponseError("KIS daily response output2 must be a list")
    return [
        DailyBar(
            symbol=symbol,
            date=_to_date(row.get("stck_bsop_date")),
            open=_to_int(row.get("stck_oprc")),
            high=_to_int(row.get("stck_hgpr")),
            low=_to_int(row.get("stck_lwpr")),
            close=_to_int(row.get("stck_clpr")),
            volume=_to_int(row.get("acml_vol")),
            turnover=_to_int(row.get("acml_tr_pbmn")),
        )
        for row in rows
        if isinstance(row, dict)
    ]


def parse_holidays(response: dict[str, Any]) -> list[HolidayRow]:
    _ensure_success(response)
    rows = response.get("output") or response.get("output2") or []
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        raise KISResponseError("KIS holiday response output must be a list")
    parsed: list[HolidayRow] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        day_value = row.get("bass_dt") or row.get("tr_day") or row.get("stck_bsop_date")
        trading_flag = row.get("opnd_yn") or row.get("bzdy_yn") or row.get("tr_day_yn")
        parsed.append(HolidayRow(date=_to_date(day_value), is_trading_day=str(trading_flag).upper() == "Y"))
    return parsed


def _ensure_success(response: dict[str, Any]) -> None:
    rt_cd = response.get("rt_cd")
    if rt_cd not in (None, "0", 0):
        raise KISResponseError(f"KIS response failed: {response.get('msg_cd')} {response.get('msg1')}")


def _to_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    return int(str(value).replace(",", "").strip())


def _to_decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value).replace(",", "").strip())


def _to_date(value: Any) -> date:
    text = str(value)
    return datetime.strptime(text, "%Y%m%d").date()
