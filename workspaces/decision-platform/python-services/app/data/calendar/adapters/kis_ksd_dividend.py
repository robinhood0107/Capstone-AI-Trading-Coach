from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

from app.data.calendar.errors import AdapterValidationError
from app.data.calendar.models import NormalizedCalendarEvent
from app.data.calendar.normalizer import canonical_hash

KSD_DIVIDEND_PATH = "/uapi/domestic-stock/v1/ksdinfo/dividend"
KSD_DIVIDEND_TR_ID = "HHKDB669102C0"
MAX_KSD_PAGES = 100
MAX_KSD_ROWS_PER_PAGE = 1_000

KSDPageFetcher = Callable[[str, str], tuple[dict[str, object], str]]
KSDPublisher = Callable[[list[NormalizedCalendarEvent]], object]


def collect_ksd_dividends(
    symbol: str,
    fetch_page: KSDPageFetcher,
    *,
    publish: KSDPublisher | None = None,
) -> list[NormalizedCalendarEvent]:
    """기존 KIS client로 모든 page를 검증한 뒤 record/pay event만 한 번에 publish한다."""
    if not _valid_symbol(symbol):
        raise AdapterValidationError("KSD dividend symbol is invalid")
    fk = ""
    nk = ""
    events: list[NormalizedCalendarEvent] = []
    seen: set[tuple[str, str]] = set()
    for _ in range(MAX_KSD_PAGES):
        response, continuation_header = fetch_page(fk, nk)
        page_events, next_fk, next_nk, has_more = _parse_page(
            response,
            symbol=symbol,
            continuation_header=continuation_header,
        )
        for event in page_events:
            key = (event.event_type, event.source_event_key)
            if key not in seen:
                seen.add(key)
                events.append(event)
        if not has_more:
            result = sorted(
                events,
                key=lambda event: (event.event_date, event.event_type, event.source_event_key),
            )
            if publish is not None:
                publish(result)
            return result
        fk, nk = next_fk, next_nk
    raise AdapterValidationError("KSD dividend pagination exceeded the page limit")


def _parse_page(
    response: dict[str, object],
    *,
    symbol: str,
    continuation_header: str,
) -> tuple[list[NormalizedCalendarEvent], str, str, bool]:
    if response.get("rt_cd") not in (None, "0", 0):
        raise AdapterValidationError("KSD dividend provider failure")
    rows = response.get("output1")
    if not isinstance(rows, list) or len(rows) > MAX_KSD_ROWS_PER_PAGE:
        raise AdapterValidationError("KSD dividend schema is invalid")
    fk = _optional_text(response.get("ctx_area_fk100"))
    nk = _optional_text(response.get("ctx_area_nk100"))
    header = continuation_header.strip().upper()
    has_more = header in {"M", "F"}
    if header not in {"", "M", "F"}:
        raise AdapterValidationError("KSD dividend continuation header is invalid")
    if has_more != bool(fk and nk) or (bool(fk) != bool(nk)):
        raise AdapterValidationError("KSD dividend continuation mismatch")
    events: list[NormalizedCalendarEvent] = []
    for value in rows:
        if not isinstance(value, dict):
            raise AdapterValidationError("KSD dividend schema is invalid")
        events.extend(_parse_row(value, requested_symbol=symbol))
    return events, fk, nk, has_more


def _parse_row(
    row: dict[object, object], *, requested_symbol: str
) -> list[NormalizedCalendarEvent]:
    try:
        symbol = _required_text(row, "sht_cd")
        raw_record_date = _required_text(row, "record_date")
        dividend_kind = _required_text(row, "divi_kind")
        stock_kind = _required_text(row, "stk_kind")
    except AdapterValidationError:
        raise AdapterValidationError("KSD dividend schema is invalid") from None
    if symbol != requested_symbol or not _valid_symbol(symbol):
        raise AdapterValidationError("KSD dividend symbol mismatch")
    record_date = _required_date(raw_record_date)
    raw_pay_date = _optional_text(row.get("divi_pay_dt"))
    pay_date = _required_date(raw_pay_date) if raw_pay_date else None
    if pay_date is not None and pay_date < record_date:
        raise AdapterValidationError("KSD dividend date reversal")
    high_dividend = _optional_text(row.get("high_divi_gb"))
    detail = {
        "dividend_kind": dividend_kind,
        "stock_kind": stock_kind,
        "high_dividend_flag": high_dividend,
    }
    result = [
        _event(
            symbol=symbol,
            event_type="DIVIDEND_RECORD",
            event_date=record_date,
            cycle_record_date=record_date,
            dividend_kind=dividend_kind,
            stock_kind=stock_kind,
            detail=detail,
        )
    ]
    if pay_date is not None:
        result.append(
            _event(
                symbol=symbol,
                event_type="DIVIDEND_PAY",
                event_date=pay_date,
                cycle_record_date=record_date,
                dividend_kind=dividend_kind,
                stock_kind=stock_kind,
                detail=detail,
            )
        )
    return result


def _event(
    *,
    symbol: str,
    event_type: str,
    event_date: date,
    cycle_record_date: date,
    dividend_kind: str,
    stock_kind: str,
    detail: dict[str, str],
) -> NormalizedCalendarEvent:
    # provider가 별도 cycle ID를 주지 않으므로 record date를 cycle anchor로 분리해 반복 배당을 합치지 않는다.
    stable_identity = (
        f"{symbol}:{cycle_record_date.isoformat()}:{dividend_kind}:{stock_kind}:{event_type}"
    )
    source_event_key = canonical_hash(
        {
            "stable_identity": stable_identity,
            "event_date": event_date,
            "detail": detail,
        }
    )
    return NormalizedCalendarEvent(
        source_id="kis-ksd-dividend-hhkdb669102c0",
        origin_group="ksd",
        tier=1,
        source_event_key=source_event_key,
        stable_identity=stable_identity,
        source_revision=None,
        event_type=event_type,
        symbol=symbol,
        exchange_mic="XKRX",
        event_date=event_date,
        detail=detail,
        operation=KSD_DIVIDEND_PATH,
        tr_id=KSD_DIVIDEND_TR_ID,
        freshness="UNVERIFIED",
    )


def _required_text(row: dict[object, object], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise AdapterValidationError("required field is invalid")
    return value.strip()


def _optional_text(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > 512:
        raise AdapterValidationError("KSD dividend schema is invalid")
    return value.strip()


def _required_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise AdapterValidationError("KSD dividend date is invalid") from None


def _valid_symbol(value: str) -> bool:
    return len(value) == 6 and value.isascii() and value.isdigit()
