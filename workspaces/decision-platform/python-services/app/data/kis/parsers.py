from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any


class KISResponseError(RuntimeError):
    """allowlisted parser code만 노출하고 provider message와 원시 field 값은 버린다."""

    def __init__(self, code: str) -> None:
        super().__init__(f"KIS response failed: {code}")
        self.code = code


@dataclass(frozen=True)
class CurrentPrice:
    # parser 바깥에서는 KIS 원문 key를 다루지 않게 정규화된 타입으로 경계를 만든다.
    # raw response를 저장하거나 로그로 흘리는 대신 필요한 숫자만 넘겨 보안 표면을 줄인다.
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
        raise KISResponseError("RESPONSE_SHAPE_INVALID")
    # KIS 숫자 필드는 문자열/콤마 문자열로 흔들리므로 여기서 int/Decimal로 고정한다.
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
        raise KISResponseError("RESPONSE_SHAPE_INVALID")
    # output1의 메타와 output2의 시계열을 분리해, parquet 저장에는 검증된 일봉 row만 흘려보낸다.
    parsed: list[DailyBar] = []
    for row in rows:
        if not isinstance(row, dict):
            raise KISResponseError("RESPONSE_SHAPE_INVALID")
        parsed.append(
            DailyBar(
                symbol=symbol,
                date=_to_required_date(row.get("stck_bsop_date")),
                open=_to_required_int(row.get("stck_oprc")),
                high=_to_required_int(row.get("stck_hgpr")),
                low=_to_required_int(row.get("stck_lwpr")),
                close=_to_required_int(row.get("stck_clpr")),
                volume=_to_required_int(row.get("acml_vol")),
                turnover=_to_int(row.get("acml_tr_pbmn")),
            )
        )
    return parsed


def parse_holidays(response: dict[str, Any]) -> list[HolidayRow]:
    _ensure_success(response)
    # KIS chk-holiday 응답은 output/output2, 단건/list 형태가 섞일 수 있어 supporting read만 넓게 흡수한다.
    rows = response.get("output") or response.get("output2") or []
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        raise KISResponseError("RESPONSE_SHAPE_INVALID")
    parsed: list[HolidayRow] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        day_value = row.get("bass_dt") or row.get("tr_day") or row.get("stck_bsop_date")
        # opnd_yn은 실제 개장 여부에 가까워 결제/대체영업일 플래그보다 먼저 본다.
        trading_flag = row.get("opnd_yn") or row.get("bzdy_yn") or row.get("tr_day_yn")
        parsed.append(
            HolidayRow(
                date=_to_required_date(day_value),
                is_trading_day=str(trading_flag).upper() == "Y",
            )
        )
    return parsed


def _ensure_success(response: dict[str, Any]) -> None:
    rt_cd = response.get("rt_cd")
    # 일부 sanitized fixture에는 rt_cd가 없을 수 있어 None은 성공처럼 처리한다.
    # provider msg_cd/msg1은 외부로 전달하지 않고 낮은 cardinality의 stable code만 남긴다.
    if rt_cd not in (None, "0", 0):
        message_code = response.get("msg_cd")
        if message_code == "EGW00201":
            raise KISResponseError("PROVIDER_RATE_LIMIT")
        if message_code in {"EGW00001", "EGW00002", "EGW00202", "EGW00203", "EGW00300"}:
            raise KISResponseError("PROVIDER_ROUTING")
        raise KISResponseError("PROVIDER_ERROR")


def _to_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        raise KISResponseError("OPTIONAL_FIELD_INVALID") from None


def _to_required_int(value: Any) -> int:
    if value in (None, "") or isinstance(value, bool):
        raise KISResponseError("REQUIRED_FIELD_INVALID")
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        raise KISResponseError("REQUIRED_FIELD_INVALID") from None


def _to_decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return Decimal(str(value).replace(",", "").strip())


def _to_required_date(value: Any) -> date:
    if value in (None, "") or isinstance(value, bool):
        raise KISResponseError("REQUIRED_FIELD_INVALID")
    try:
        return datetime.strptime(str(value), "%Y%m%d").date()
    except (TypeError, ValueError):
        raise KISResponseError("REQUIRED_FIELD_INVALID") from None
