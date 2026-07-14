from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from app.data.ecos.errors import ECOSApplicationError, ECOSParseError
from app.data.ecos.parsers import parse_statistic_search


_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "ecos"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _parse(payload: dict[str, Any], *, max_rows: int = 400):
    return parse_statistic_search(
        payload,
        expected_stat_code="722Y001",
        expected_item_code1="0101000",
        expected_cycle="D",
        max_rows=max_rows,
    )


def test_statistic_search_parses_allowlisted_rows_and_canonical_decimal_strings() -> None:
    page = _parse(_fixture("statistic_search_success.json"))

    assert page.status == "complete"
    assert page.total_count == 3
    assert [observation.time for observation in page.observations] == [
        "20260711",
        "20260712",
        "20260713",
    ]
    assert [observation.value for observation in page.observations] == ["2.5", "0", "2.5"]
    assert all(
        set(observation.model_dump()) == {"time", "value"} for observation in page.observations
    )


def test_info_200_is_normal_empty_and_not_a_retryable_error() -> None:
    page = _parse(_fixture("statistic_search_empty.json"))

    assert page.status == "empty"
    assert page.total_count == 0
    assert page.observations == []
    assert page.retryable is False


def test_application_errors_keep_only_stable_code_and_retry_policy() -> None:
    cases = _fixture("statistic_search_error_cases.json")["cases"]
    provider_message = "synthetic-provider-message-must-not-escape"

    for case in cases:
        with pytest.raises(ECOSApplicationError) as exc_info:
            _parse({"RESULT": {"CODE": case["code"], "MESSAGE": provider_message}})

        error = exc_info.value
        assert error.code == case["code"]
        assert error.retryable is case["retryable"]
        assert error.cooldown_seconds == case.get("cooldownSeconds", 0)
        assert provider_message not in f"{error!r} {error}"
        assert error.__cause__ is None


@pytest.mark.parametrize(
    "invalid_value",
    ["NaN", "Infinity", "-Infinity", "1,234.5", "1e19", "9" * 29, "", " 2.5"],
)
def test_numeric_bounds_reject_noncanonical_or_excessive_values(invalid_value: str) -> None:
    payload = _fixture("statistic_search_success.json")
    payload["StatisticSearch"]["row"][0]["DATA_VALUE"] = invalid_value

    with pytest.raises(ECOSParseError, match="invalid ECOS response") as exc_info:
        _parse(payload)

    assert invalid_value not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize(
    "invalid_time", ["20260229", "20261301", "2026071", "202607140", "abcdefgh"]
)
def test_time_requires_exactly_eight_calendar_valid_digits(invalid_time: str) -> None:
    payload = _fixture("statistic_search_success.json")
    payload["StatisticSearch"]["row"][0]["TIME"] = invalid_time

    with pytest.raises(ECOSParseError, match="invalid ECOS response"):
        _parse(payload)


def test_parser_rejects_identity_mismatch_and_row_count_over_bound() -> None:
    mismatched = _fixture("statistic_search_success.json")
    mismatched["StatisticSearch"]["row"][0]["STAT_CODE"] = "731Y001"

    with pytest.raises(ECOSParseError, match="invalid ECOS response"):
        _parse(mismatched)

    oversized = _fixture("statistic_search_success.json")
    with pytest.raises(ECOSParseError, match="row limit"):
        _parse(oversized, max_rows=2)


def test_exact_duplicates_collapse_but_conflicting_duplicates_fail_closed() -> None:
    payload = _fixture("statistic_search_success.json")
    duplicate = deepcopy(payload["StatisticSearch"]["row"][0])
    payload["StatisticSearch"]["row"].append(duplicate)
    payload["StatisticSearch"]["list_total_count"] = 4

    page = _parse(payload)

    assert len(page.observations) == 3
    assert page.duplicate_count == 1

    duplicate["DATA_VALUE"] = "2.75"
    with pytest.raises(ECOSParseError, match="conflicting duplicate"):
        _parse(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"StatisticSearch": []},
        {"StatisticSearch": {"list_total_count": 1}},
        {"StatisticSearch": {"list_total_count": -1, "row": []}},
        {"StatisticSearch": {"list_total_count": 0, "row": "not-a-list"}},
    ],
)
def test_malformed_envelope_fails_with_sanitized_error(payload: dict[str, Any]) -> None:
    with pytest.raises(ECOSParseError, match="invalid ECOS response") as exc_info:
        _parse(payload)

    assert "StatisticSearch" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
