from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.data.calendar.errors import AdapterValidationError
from app.data.calendar.models import NormalizedCalendarEvent

_STATE_TRANSITIONS = {
    "bnkMngtPcbg": ("BANK_MANAGEMENT", "OPEN"),
    "bnkMngtPcsp": ("BANK_MANAGEMENT", "CLOSE"),
}
_STRUCTURED_EVENT_ENDPOINTS = frozenset(
    {
        "piicDecsn",
        "cvbdIsDecsn",
        "lwstLg",
        "dfOcr",
        "ctrcvsBgrq",
        "dsRsOcr",
        "bnkMngtPcbg",
        "bnkMngtPcsp",
        "bsnSp",
        "crDecsn",
        "bdwtIsDecsn",
        "exbdIsDecsn",
        "cmpMgDecsn",
        "cmpDvDecsn",
        "cmpDvmgDecsn",
        "bsnTrfDecsn",
    }
)


def normalize_opendart_structured_event(
    endpoint_id: str,
    row: dict[str, Any],
    *,
    symbol: str,
) -> NormalizedCalendarEvent:
    """approved structured endpoint identity와 allowlisted field만 OpenDART canonical event로 만든다."""
    if endpoint_id not in _STRUCTURED_EVENT_ENDPOINTS:
        raise AdapterValidationError("OpenDART endpoint is not approved for event mapping")
    corp_code = _digits(row.get("corp_code"), length=8, label="corp_code")
    receipt_no = _digits(row.get("rcept_no"), length=14, label="receipt_no")
    if len(symbol) != 6 or not symbol.isascii() or not symbol.isdigit():
        raise AdapterValidationError("OpenDART symbol is invalid")
    event_date = _event_date(row, receipt_no)
    if endpoint_id in _STATE_TRANSITIONS:
        state_type, transition = _STATE_TRANSITIONS[endpoint_id]
        event_type = "DISCLOSURE_RISK_STATE"
        detail = {
            "corp_code": corp_code,
            "state_type": state_type,
            "transition": transition,
        }
        stable_identity = f"{corp_code}:{endpoint_id}:{receipt_no}"
    else:
        event_type = "DISCLOSURE"
        detail = {"corp_code": corp_code, "endpoint_id": endpoint_id}
        stable_identity = f"{corp_code}:{endpoint_id}:{receipt_no}"
    return NormalizedCalendarEvent(
        source_id="opendart-structured-events",
        origin_group="opendart",
        tier=1,
        source_event_key=receipt_no,
        stable_identity=stable_identity,
        source_revision=None,
        event_type=event_type,
        symbol=symbol,
        event_date=event_date,
        detail=detail,
        operation=f"/api/{endpoint_id}.json",
        freshness="PROVIDER_PUBLICATION_TIME",
    )


def _event_date(row: dict[str, Any], receipt_no: str) -> date:
    value = row.get("rcept_dt")
    raw = value if isinstance(value, str) and value else receipt_no[:8]
    for date_format in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(raw), date_format).date()
        except ValueError:
            continue
    raise AdapterValidationError("OpenDART event date is invalid")


def _digits(value: object, *, length: int, label: str) -> str:
    if not isinstance(value, str) or len(value) != length or not value.isascii() or not value.isdigit():
        raise AdapterValidationError(f"OpenDART {label} is invalid")
    return value
