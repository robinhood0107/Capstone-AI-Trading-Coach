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
    flng_cls_code: str = ""
    prtt_rate: Decimal = Decimal("0")
    mod_yn: str = "N"
    revl_issu_reas: str = ""


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


def parse_daily_bars(
    response: dict[str, Any], symbol: str, *, require_adjustment_fields: bool = False
) -> list[DailyBar]:
    """기간별시세를 파싱하며 production에서는 기업행사 evidence 네 필드를 필수화한다."""

    _ensure_success(response)
    rows = response.get("output2") or []
    if not isinstance(rows, list):
        raise KISResponseError("RESPONSE_SHAPE_INVALID")
    # output1의 메타와 output2의 시계열을 분리해, parquet 저장에는 검증된 일봉 row만 흘려보낸다.
    parsed: list[DailyBar] = []
    for row in rows:
        if not isinstance(row, dict):
            raise KISResponseError("RESPONSE_SHAPE_INVALID")
        if require_adjustment_fields and not {
            "flng_cls_code",
            "prtt_rate",
            "mod_yn",
            "revl_issu_reas",
        }.issubset(row):
            raise KISResponseError("ADJUSTMENT_FIELD_MISSING")
        if require_adjustment_fields and any(
            not isinstance(row[field], str)
            for field in ("flng_cls_code", "prtt_rate", "mod_yn", "revl_issu_reas")
        ):
            raise KISResponseError("ADJUSTMENT_FIELD_INVALID")
        falling_code = _to_falling_code(row.get("flng_cls_code"))
        adjustment_rate = _to_adjustment_rate(row.get("prtt_rate"))
        modification_flag = _to_modification_flag(row.get("mod_yn"))
        revision_reason = _to_bounded_text(row.get("revl_issu_reas"))
        if require_adjustment_fields:
            _validate_adjustment_evidence(
                falling_code=falling_code,
                adjustment_rate=adjustment_rate,
                modification_flag=modification_flag,
                revision_reason=revision_reason,
            )
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
                flng_cls_code=falling_code,
                prtt_rate=adjustment_rate,
                mod_yn=modification_flag,
                revl_issu_reas=revision_reason,
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


# 조정 비율의 부호는 조정 방향이며 음수도 정상이다. 사람이 못 읽을 크기만 거부한다.
_MAX_ADJUSTMENT_RATE_MAGNITUDE = Decimal("1000")


def _to_adjustment_rate(value: Any) -> Decimal:
    try:
        parsed = _to_decimal(value)
    except Exception:
        raise KISResponseError("ADJUSTMENT_FIELD_INVALID") from None
    if not parsed.is_finite() or abs(parsed) > _MAX_ADJUSTMENT_RATE_MAGNITUDE:
        raise KISResponseError("ADJUSTMENT_FIELD_INVALID")
    return parsed


def _to_required_date(value: Any) -> date:
    if value in (None, "") or isinstance(value, bool):
        raise KISResponseError("REQUIRED_FIELD_INVALID")
    try:
        return datetime.strptime(str(value), "%Y%m%d").date()
    except (TypeError, ValueError):
        raise KISResponseError("REQUIRED_FIELD_INVALID") from None


def _to_bounded_code(value: Any) -> str:
    text = "" if value in (None, "") else str(value).strip()
    if len(text) > 32 or any(ord(character) < 32 for character in text):
        raise KISResponseError("ADJUSTMENT_FIELD_INVALID")
    return text


def _to_falling_code(value: Any) -> str:
    # KIS 공식 master의 락구분 코드만 허용하고 미문서 코드는 sensitivity 대상에 넣지 않는다.
    text = _to_bounded_code(value)
    if text not in {"", "00", "01", "02", "03", "04", "05", "06", "99"}:
        raise KISResponseError("ADJUSTMENT_FIELD_INVALID")
    return text


def _to_bounded_text(value: Any) -> str:
    text = "" if value in (None, "") else str(value).strip()
    if len(text) > 256 or any(ord(character) < 32 for character in text):
        raise KISResponseError("ADJUSTMENT_FIELD_INVALID")
    return text


def _to_modification_flag(value: Any) -> str:
    text = "N" if value in (None, "") else str(value).strip().upper()
    if text not in {"Y", "N"}:
        raise KISResponseError("ADJUSTMENT_FIELD_INVALID")
    return text


def _validate_adjustment_evidence(
    *,
    falling_code: str,
    adjustment_rate: Decimal,
    modification_flag: str,
    revision_reason: str,
) -> None:
    # mod_yn은 반환된 가격이 수정주가인지를 나타내며 요청한 조정 모드에서 따라온다. 락 구분과
    # 조정 비율, 사유는 그 날짜의 corporate action 증거이며 mod_yn과 독립이다. 둘을 양방향으로
    # 묶으면 원주가 응답의 정상적인 배당락 표시를 provider 모순으로 오판한다.
    if modification_flag not in {"Y", "N"}:
        raise KISResponseError("ADJUSTMENT_FIELD_INVALID")
    marked = falling_code not in {"", "00"}
    if (adjustment_rate != 0 or bool(revision_reason)) and not marked:
        raise KISResponseError("ADJUSTMENT_FIELD_CONTRADICTORY")
