from __future__ import annotations

from collections.abc import Callable

import pytest

from app.data.calendar.adapters.kis_ksd_dividend import collect_ksd_dividends
from app.data.calendar.errors import AdapterValidationError


def test_ksd_dividend_maps_only_record_and_pay_dates() -> None:
    events = collect_ksd_dividends(
        "005930",
        lambda *_: (_page([_row()]), ""),
    )

    assert [event.event_type for event in events] == ["DIVIDEND_RECORD", "DIVIDEND_PAY"]
    assert [event.event_date.isoformat() for event in events] == ["2026-03-31", "2026-04-20"]
    assert all(event.operation == "/uapi/domestic-stock/v1/ksdinfo/dividend" for event in events)
    assert all(event.tr_id == "HHKDB669102C0" for event in events)
    assert all(event.freshness == "UNVERIFIED" for event in events)
    assert "DIVIDEND_EX" not in {event.event_type for event in events}


def test_ksd_dividend_paginates_then_publishes_once_atomically() -> None:
    calls: list[tuple[str, str]] = []
    published: list[list[object]] = []

    def fetch(fk: str, nk: str) -> tuple[dict[str, object], str]:
        calls.append((fk, nk))
        if not fk:
            return _page([_row()], fk="next-fk", nk="next-nk"), "M"
        return _page([_row(record_date="20260930", pay_date="20261020")]), ""

    events = collect_ksd_dividends("005930", fetch, publish=published.append)

    assert calls == [("", ""), ("next-fk", "next-nk")]
    assert len(events) == 4
    assert published == [events]


@pytest.mark.parametrize(
    "fetcher, expected",
    [
        (lambda *_: (_page([_row()], fk="next", nk="next"), ""), "continuation"),
        (lambda *_: (_page([_row(symbol="000660")]), ""), "symbol"),
        (lambda *_: (_page([_row(record_date="20260430", pay_date="20260401")]), ""), "date"),
        (lambda *_: ({"rt_cd": "0", "output1": [{"sht_cd": "005930"}]}, ""), "schema"),
    ],
)
def test_ksd_invalid_page_never_publishes(
    fetcher: Callable[[str, str], tuple[dict[str, object], str]],
    expected: str,
) -> None:
    published: list[list[object]] = []
    with pytest.raises(AdapterValidationError, match=expected):
        collect_ksd_dividends("005930", fetcher, publish=published.append)
    assert published == []


def _page(
    rows: list[dict[str, str]],
    *,
    fk: str = "",
    nk: str = "",
) -> dict[str, object]:
    return {"rt_cd": "0", "output1": rows, "ctx_area_fk100": fk, "ctx_area_nk100": nk}


def _row(
    *,
    symbol: str = "005930",
    record_date: str = "20260331",
    pay_date: str = "20260420",
) -> dict[str, str]:
    return {
        "sht_cd": symbol,
        "record_date": record_date,
        "divi_pay_dt": pay_date,
        "divi_kind": "CASH",
        "stk_kind": "COMMON",
        "high_divi_gb": "N",
    }
