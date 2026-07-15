from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.data.krx.catalog import KOSDAQ_DAILY, KOSPI_DAILY, KrxEndpoint
from app.data.krx.errors import KrxParseError
from app.data.krx.parsers import KrxDailyRow, parse_daily_response


_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "krx"
_REQUESTED_DATE = date(2026, 7, 15)


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _parse(
    payload: dict[str, Any],
    *,
    endpoint: KrxEndpoint = KOSPI_DAILY,
) -> tuple[KrxDailyRow, ...]:
    return parse_daily_response(
        payload,
        endpoint=endpoint,
        requested_date=_REQUESTED_DATE,
    )


def test_kospi_success_converts_only_the_allowlisted_universe_fields() -> None:
    rows = _parse(_fixture("kospi_daily_success.json"))

    assert rows == (
        KrxDailyRow(
            as_of_date=_REQUESTED_DATE,
            symbol="900001",
            name="합성코스피일호",
            market="KOSPI",
            trading_value=987_654_321,
            market_cap=8_765_432_100,
        ),
        KrxDailyRow(
            as_of_date=_REQUESTED_DATE,
            symbol="900002",
            name="합성코스피이호",
            market="KOSPI",
            trading_value=876_543_210,
            market_cap=7_654_321_000,
        ),
    )


def test_kosdaq_success_uses_the_same_normalized_model() -> None:
    rows = _parse(
        _fixture("kosdaq_daily_success.json"),
        endpoint=KOSDAQ_DAILY,
    )

    assert [row.symbol for row in rows] == ["800001", "800002"]
    assert [row.market for row in rows] == ["KOSDAQ", "KOSDAQ"]
    assert all(row.as_of_date == _REQUESTED_DATE for row in rows)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"OutBlock_1": {}},
        {"outBlock1": []},
        {"OutBlock_1": [], "unexpected": []},
    ],
)
def test_only_the_exact_outblock_1_list_envelope_is_accepted(payload: dict[str, Any]) -> None:
    with pytest.raises(KrxParseError):
        _parse(payload)


def test_empty_daily_response_fails_closed_before_the_second_market_is_requested() -> None:
    with pytest.raises(KrxParseError):
        _parse({"OutBlock_1": []})


def test_each_row_requires_exactly_the_official_fifteen_string_fields() -> None:
    base = _fixture("kospi_daily_success.json")

    missing = deepcopy(base)
    del missing["OutBlock_1"][0]["TDD_CLSPRC"]
    extra = deepcopy(base)
    extra["OutBlock_1"][0]["UNEXPECTED"] = "synthetic"
    non_string = deepcopy(base)
    non_string["OutBlock_1"][0]["ACC_TRDVOL"] = 123

    for payload in (missing, extra, non_string):
        with pytest.raises(KrxParseError) as exc_info:
            _parse(payload)
        assert "synthetic" not in str(exc_info.value)
        assert exc_info.value.__cause__ is None


@pytest.mark.parametrize("invalid_date", ["20260714", "2026-07-15", "20260229", "２０２６０７１５"])
def test_row_date_must_match_the_requested_calendar_date(invalid_date: str) -> None:
    payload = _fixture("kospi_daily_success.json")
    payload["OutBlock_1"][0]["BAS_DD"] = invalid_date

    with pytest.raises(KrxParseError):
        _parse(payload)


@pytest.mark.parametrize("invalid_market", ["KOSDAQ", "KOSPI ", "", "전체"])
def test_row_market_must_match_the_catalog_endpoint(invalid_market: str) -> None:
    payload = _fixture("kospi_daily_success.json")
    payload["OutBlock_1"][0]["MKT_NM"] = invalid_market

    with pytest.raises(KrxParseError):
        _parse(payload)


@pytest.mark.parametrize(
    "invalid_symbol",
    ["90001", "9000001", "ABC123", "９００００１", "90000 ", ""],
)
def test_symbol_requires_exactly_six_ascii_digits(invalid_symbol: str) -> None:
    payload = _fixture("kospi_daily_success.json")
    payload["OutBlock_1"][0]["ISU_CD"] = invalid_symbol

    with pytest.raises(KrxParseError):
        _parse(payload)


@pytest.mark.parametrize("invalid_name", ["", " ", "\t", "합성\n종목", "합성\x00종목", "가" * 257])
def test_security_name_must_be_bounded_plain_text(invalid_name: str) -> None:
    payload = _fixture("kospi_daily_success.json")
    payload["OutBlock_1"][0]["ISU_NM"] = invalid_name

    with pytest.raises(KrxParseError):
        _parse(payload)


@pytest.mark.parametrize("field", ["ACC_TRDVAL", "MKTCAP"])
@pytest.mark.parametrize(
    "invalid_number",
    ["", "-", "0", "-1", "+1", "1,000", " 1", "1 ", "1.0", "1e3", "9223372036854775808"],
)
def test_ranking_numbers_require_bounded_positive_ascii_integers(
    field: str,
    invalid_number: str,
) -> None:
    payload = _fixture("kospi_daily_success.json")
    payload["OutBlock_1"][0][field] = invalid_number

    with pytest.raises(KrxParseError) as exc_info:
        _parse(payload)

    if invalid_number:
        assert invalid_number not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_duplicate_symbol_fails_closed_even_when_the_rows_are_identical() -> None:
    payload = _fixture("kospi_daily_success.json")
    duplicate = deepcopy(payload["OutBlock_1"][0])
    payload["OutBlock_1"].append(duplicate)

    with pytest.raises(KrxParseError):
        _parse(payload)

    duplicate["MKTCAP"] = "123456789"
    with pytest.raises(KrxParseError):
        _parse(payload)


def test_response_row_count_above_the_hard_cap_fails_before_row_processing() -> None:
    payload = _fixture("kospi_daily_success.json")
    template = payload["OutBlock_1"][0]
    payload["OutBlock_1"] = [template] * 5_001

    with pytest.raises(KrxParseError) as exc_info:
        _parse(payload)

    assert "900001" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
