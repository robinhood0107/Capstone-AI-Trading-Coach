import json
from datetime import date
from importlib.resources import files

import pytest

from app.data.kis.parsers import KISResponseError, parse_current_price, parse_daily_bars, parse_holidays

FIXTURE_PACKAGE = "app.data.kis.fixtures"


def _load(name: str) -> dict:
    return json.loads(files(FIXTURE_PACKAGE).joinpath(name).read_text(encoding="utf-8"))


def test_parse_current_price_normalizes_numeric_fields() -> None:
    price = parse_current_price(_load("current_price_005930.json"), symbol="005930")

    assert price.symbol == "005930"
    assert price.price == 73500
    assert price.open == 72800
    assert price.high == 73900
    assert price.low == 72400
    assert price.volume == 12123456


def test_parse_current_price_accepts_comma_numeric_fields() -> None:
    price = parse_current_price(_load("current_price_comma_000660.json"), symbol="000660")

    assert price.symbol == "000660"
    assert price.price == 240000
    assert price.volume == 1234567
    assert price.turnover == 296296080000


def test_parse_daily_bars_normalizes_output2_rows() -> None:
    bars = parse_daily_bars(_load("daily_itemchart_005930_page1.json"), symbol="005930")

    assert [bar.date for bar in bars] == [date(2026, 7, 8), date(2026, 7, 7)]
    assert bars[0].close == 73500
    assert bars[1].turnover == 735000000000


def test_parse_daily_bars_accepts_comma_numeric_fields() -> None:
    bars = parse_daily_bars(_load("daily_itemchart_comma_000660_page1.json"), symbol="000660")

    assert bars[0].close == 240000
    assert bars[0].volume == 1234567
    assert bars[0].turnover == 296296080000


def test_parse_daily_bars_accepts_empty_output2() -> None:
    assert parse_daily_bars(_load("daily_itemchart_empty_output2.json"), symbol="005930") == []


def test_parse_holidays_reads_business_day_flags() -> None:
    rows = parse_holidays(_load("holiday_202607.json"))

    assert rows[0].date == date(2026, 7, 11)
    assert rows[0].is_trading_day is False
    assert rows[1].date == date(2026, 7, 13)
    assert rows[1].is_trading_day is True


def test_parse_holidays_reads_output2_rows() -> None:
    rows = parse_holidays(_load("holiday_output2_202601.json"))

    assert rows[0].date == date(2026, 1, 1)
    assert rows[0].is_trading_day is False


def test_parse_holidays_reads_single_output_object() -> None:
    rows = parse_holidays(_load("holiday_single_output_202602.json"))

    assert len(rows) == 1
    assert rows[0].date == date(2026, 2, 16)
    assert rows[0].is_trading_day is False


def test_parse_holidays_prefers_market_open_flag_over_transfer_day_flag() -> None:
    response = {
        "output": [
            {
                "bass_dt": "20260711",
                "bzdy_yn": "N",
                "tr_day_yn": "Y",
                "opnd_yn": "N",
                "sttl_day_yn": "N",
            }
        ]
    }

    assert parse_holidays(response)[0].is_trading_day is False


def test_parse_current_price_raises_on_kis_error_response() -> None:
    with pytest.raises(KISResponseError, match="EGW00123"):
        parse_current_price(_load("current_price_error.json"), symbol="005930")


def test_committed_kis_fixture_count_reaches_s1_1b_target() -> None:
    # offline mode is a runtime fallback, so count the package the CLI actually loads.
    assert len([item for item in files(FIXTURE_PACKAGE).iterdir() if item.name.endswith(".json")]) >= 20
