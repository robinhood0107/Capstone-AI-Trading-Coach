from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.data.ecos.errors import ECOSParseError
from app.data.ecos.parsers import parse_statistic_item_list, parse_statistic_table_list
from app.data.ecos.models import StatisticItemMetadata, StatisticTableMetadata
from app.data.ecos.series_registry import CANDIDATE_SERIES, ECOSSeries


_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "ecos"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_registry_metadata_parsers_return_only_sanitized_allowlisted_fields() -> None:
    table = parse_statistic_table_list(
        _fixture("statistic_table_list_metadata.json"), expected_stat_code="722Y001"
    )
    item = parse_statistic_item_list(
        _fixture("statistic_item_list_metadata.json"),
        expected_stat_code="722Y001",
        expected_item_code="0101000",
    )

    assert table.model_dump() == {
        "stat_code": "722Y001",
        "name": "합성 기준금리",
        "cycle": "D",
        "searchable": True,
    }
    assert item.model_dump() == {
        "stat_code": "722Y001",
        "item_code": "0101000",
        "name": "합성 기준금리 항목",
        "cycle": "D",
        "unit": "%",
    }


def test_registry_metadata_identity_mismatch_fails_closed() -> None:
    with pytest.raises(ECOSParseError, match="invalid ECOS response"):
        parse_statistic_table_list(
            _fixture("statistic_table_list_metadata.json"), expected_stat_code="731Y001"
        )
    with pytest.raises(ECOSParseError, match="invalid ECOS response"):
        parse_statistic_item_list(
            _fixture("statistic_item_list_metadata.json"),
            expected_stat_code="722Y001",
            expected_item_code="0000001",
        )


def test_item_preflight_selects_the_exact_candidate_from_a_bounded_item_list() -> None:
    payload = _fixture("statistic_item_list_metadata.json")
    envelope = payload["StatisticItemList"]
    assert isinstance(envelope, dict)
    rows = envelope["row"]
    assert isinstance(rows, list)
    unrelated = dict(rows[0])
    unrelated["ITEM_CODE"] = "9999999"
    unrelated["ITEM_NAME"] = "합성 비대상 항목"
    envelope["row"] = [unrelated, rows[0]]
    envelope["list_total_count"] = 2

    item = parse_statistic_item_list(
        payload,
        expected_stat_code="722Y001",
        expected_item_code="0101000",
    )

    assert item.item_code == "0101000"


class _PreflightClient:
    def __init__(self, *, mismatched_unit: bool = False) -> None:
        self.mismatched_unit = mismatched_unit
        self.calls: list[tuple[str, str]] = []

    def statistic_table_list(self, *, series: ECOSSeries) -> StatisticTableMetadata:
        self.calls.append(("table", series.series_id))
        return StatisticTableMetadata(
            stat_code=series.stat_code,
            name=f"synthetic-{series.series_id}",
            cycle="D",
            searchable=True,
        )

    def statistic_item_list(self, *, series: ECOSSeries) -> StatisticItemMetadata:
        self.calls.append(("item", series.series_id))
        unit = "mismatch" if self.mismatched_unit and series == CANDIDATE_SERIES[1] else "%"
        return StatisticItemMetadata(
            stat_code=series.stat_code,
            item_code=series.item_code1,
            name=f"synthetic-{series.series_id}-item",
            cycle="D",
            unit=unit,
        )


def _expectations():
    from app.data.ecos.registry_preflight import RegistryExpectation

    return tuple(
        RegistryExpectation(
            series=series,
            table_name=f"synthetic-{series.series_id}",
            item_name=f"synthetic-{series.series_id}-item",
            unit="%",
        )
        for series in CANDIDATE_SERIES
    )


def test_preflight_makes_exactly_four_calls_without_retry_and_returns_sanitized_result() -> None:
    from app.data.ecos.registry_preflight import run_registry_preflight

    client = _PreflightClient()

    result = run_registry_preflight(client=client, expectations=_expectations())

    assert client.calls == [
        ("table", "policy-rate"),
        ("item", "policy-rate"),
        ("table", "krw-usd-rate"),
        ("item", "krw-usd-rate"),
    ]
    assert result.can_activate is True
    rendered = repr(result).lower()
    assert "raw" not in rendered
    assert "credential" not in rendered
    assert "url" not in rendered


def test_preflight_mismatch_keeps_registry_disabled() -> None:
    from app.data.ecos.registry_preflight import run_registry_preflight

    client = _PreflightClient(mismatched_unit=True)

    result = run_registry_preflight(client=client, expectations=_expectations())

    assert len(client.calls) == 4
    assert result.can_activate is False
    assert result.verified_series == ()
