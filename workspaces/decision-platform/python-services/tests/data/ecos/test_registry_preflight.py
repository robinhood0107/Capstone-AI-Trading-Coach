from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.data.ecos.errors import ECOSParseError
from app.data.ecos.models import StatisticItemMetadata, StatisticTableMetadata
from app.data.ecos.parsers import parse_statistic_item_list, parse_statistic_table_list
from app.data.ecos.series_registry import CANDIDATE_SERIES, ECOSSeries

_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "ecos"
_VERIFIED_AT = datetime(2026, 7, 14, 1, 2, 3, tzinfo=UTC)


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
        expected_cycle="D",
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
            expected_cycle="D",
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
        expected_cycle="D",
    )

    assert item.item_code == "0101000"


@pytest.mark.parametrize("target_index", range(4))
def test_item_preflight_selects_requested_cycle_from_a3_four_cycle_shape(
    target_index: int,
) -> None:
    payload = _fixture("statistic_item_list_metadata.json")
    envelope = payload["StatisticItemList"]
    assert isinstance(envelope, dict)
    rows = envelope["row"]
    assert isinstance(rows, list)
    target = rows[0]
    assert isinstance(target, dict)

    distractors = []
    for cycle, label in (
        ("A", "annual"),
        ("M", "monthly"),
        ("Q", "quarterly"),
    ):
        row = dict(target)
        row["GRP_CODE"] = "Group1"
        row["CYCLE"] = cycle
        row["ITEM_NAME"] = f"합성 비대상 {label}"
        row["UNIT_NAME"] = f"합성단위-{label}"
        distractors.append(row)

    exact = dict(target)
    exact["ITEM_NAME"] = "합성 기준금리 일별 항목"
    exact["UNIT_NAME"] = "%"
    candidates = distractors
    candidates.insert(target_index, exact)
    envelope["row"] = candidates
    envelope["list_total_count"] = 4

    item = parse_statistic_item_list(
        payload,
        expected_stat_code="722Y001",
        expected_item_code="0101000",
        expected_cycle="D",
    )

    assert item.model_dump() == {
        "stat_code": "722Y001",
        "item_code": "0101000",
        "name": "합성 기준금리 일별 항목",
        "cycle": "D",
        "unit": "%",
    }


@pytest.mark.parametrize("target_index", range(4))
def test_item_preflight_qualifies_item_code1_with_primary_group(
    target_index: int,
) -> None:
    payload = _fixture("statistic_item_list_metadata.json")
    envelope = payload["StatisticItemList"]
    assert isinstance(envelope, dict)
    rows = envelope["row"]
    assert isinstance(rows, list)
    target = rows[0]
    assert isinstance(target, dict)

    distractors = []
    for group_code in ("Group2", "Group3", "Group4"):
        row = dict(target)
        row["GRP_CODE"] = group_code
        row["ITEM_NAME"] = f"합성 비대상 {group_code}"
        row["UNIT_NAME"] = f"합성단위-{group_code}"
        distractors.append(row)

    exact = dict(target)
    exact["ITEM_NAME"] = "합성 Group1 기준금리 항목"
    candidates = distractors
    candidates.insert(target_index, exact)
    envelope["row"] = candidates
    envelope["list_total_count"] = 4

    item = parse_statistic_item_list(
        payload,
        expected_stat_code="722Y001",
        expected_item_code="0101000",
        expected_cycle="D",
    )

    assert item.name == "합성 Group1 기준금리 항목"
    assert item.cycle == "D"


def test_item_preflight_rejects_nonrequested_cycles_without_fallback() -> None:
    payload = _fixture("statistic_item_list_metadata.json")
    envelope = payload["StatisticItemList"]
    assert isinstance(envelope, dict)
    rows = envelope["row"]
    assert isinstance(rows, list)
    target = rows[0]
    assert isinstance(target, dict)
    envelope["row"] = [dict(target, CYCLE=cycle) for cycle in ("A", "M", "Q")]
    envelope["list_total_count"] = 3

    with pytest.raises(ECOSParseError) as exc_info:
        parse_statistic_item_list(
            payload,
            expected_stat_code="722Y001",
            expected_item_code="0101000",
            expected_cycle="D",
        )

    assert exc_info.value.diagnostic.to_payload() == {
        "candidateMatchCount": 0,
        "diagnosticVersion": 1,
        "failureReason": "candidate_not_found",
        "failureStage": "candidate_match",
        "field": "item_code",
        "fieldKind": "not_found",
    }


def test_item_preflight_accepts_a_valid_partial_metadata_page() -> None:
    payload = _fixture("statistic_item_list_metadata.json")
    envelope = payload["StatisticItemList"]
    assert isinstance(envelope, dict)
    rows = envelope["row"]
    assert isinstance(rows, list)
    target = rows[0]
    assert isinstance(target, dict)
    page = []
    for index in range(199):
        unrelated = dict(target)
        unrelated["ITEM_CODE"] = f"9{index:06d}"
        unrelated["ITEM_NAME"] = f"합성 비대상 항목 {index}"
        page.append(unrelated)
    page.append(target)
    # 실제 1..200 요청에서도 list_total_count는 전체 결과 건수이므로 page 길이보다 클 수 있다.
    envelope["list_total_count"] = 201
    envelope["row"] = page

    item = parse_statistic_item_list(
        payload,
        expected_stat_code="722Y001",
        expected_item_code="0101000",
        expected_cycle="D",
    )

    assert item.item_code == "0101000"


def test_item_preflight_rejects_a_truncated_first_metadata_page() -> None:
    payload = _fixture("statistic_item_list_metadata.json")
    envelope = payload["StatisticItemList"]
    assert isinstance(envelope, dict)
    envelope["list_total_count"] = 201

    with pytest.raises(ECOSParseError, match="invalid ECOS response"):
        parse_statistic_item_list(
            payload,
            expected_stat_code="722Y001",
            expected_item_code="0101000",
            expected_cycle="D",
        )


class _PreflightClient:
    def __init__(
        self,
        *,
        mismatched_unit: bool = False,
        mismatched_identity: bool = False,
        searchable: bool = True,
    ) -> None:
        self.mismatched_unit = mismatched_unit
        self.mismatched_identity = mismatched_identity
        self.searchable = searchable
        self.calls: list[tuple[str, str]] = []

    def statistic_table_list(self, *, series: ECOSSeries) -> StatisticTableMetadata:
        self.calls.append(("table", series.series_id))
        return StatisticTableMetadata(
            stat_code=series.stat_code,
            name=f"synthetic-{series.series_id}",
            cycle="D",
            searchable=self.searchable,
        )

    def statistic_item_list(self, *, series: ECOSSeries) -> StatisticItemMetadata:
        self.calls.append(("item", series.series_id))
        unit = "mismatch" if self.mismatched_unit and series == CANDIDATE_SERIES[1] else "%"
        return StatisticItemMetadata(
            stat_code=series.stat_code,
            item_code="9999999" if self.mismatched_identity else series.item_code1,
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


def test_preflight_makes_exactly_four_calls_and_records_real_completion_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.data.ecos import registry_preflight

    client = _PreflightClient()
    expected_calls = [
        ("table", "policy-rate"),
        ("item", "policy-rate"),
        ("table", "krw-usd-rate"),
        ("item", "krw-usd-rate"),
    ]

    def completed_at() -> datetime:
        assert client.calls == expected_calls
        return _VERIFIED_AT

    monkeypatch.setattr(registry_preflight, "_utc_now", completed_at)

    result = registry_preflight.run_registry_preflight(
        client=client,
        expectations=_expectations(),
    )

    assert client.calls == expected_calls
    assert result.can_activate is True
    assert result.registry_verified_at == _VERIFIED_AT
    assert {entry.registry_verified_at for entry in result.verified_series} == {_VERIFIED_AT}
    assert [entry.name for entry in result.verified_series] == [
        f"synthetic-{series.series_id}-item" for series in CANDIDATE_SERIES
    ]
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
    assert result.registry_verified_at is None


def test_metadata_inspection_returns_only_allowlisted_fields_without_activation() -> None:
    from app.data.ecos.registry_preflight import inspect_registry_metadata

    client = _PreflightClient()

    result = inspect_registry_metadata(
        client=client,
        series=CANDIDATE_SERIES,
        observed_at=_VERIFIED_AT,
    )

    assert client.calls == [
        ("table", "policy-rate"),
        ("item", "policy-rate"),
        ("table", "krw-usd-rate"),
        ("item", "krw-usd-rate"),
    ]
    assert result.observed_at == _VERIFIED_AT
    assert [entry.series_id for entry in result.entries] == [
        "policy-rate",
        "krw-usd-rate",
    ]
    assert result.can_activate is False
    assert result.verified_series == ()
    assert "raw" not in repr(result).lower()


def test_metadata_inspection_rejects_mismatched_identity_without_activation() -> None:
    from app.data.ecos.registry_preflight import inspect_registry_metadata

    client = _PreflightClient(mismatched_identity=True)

    with pytest.raises(ECOSParseError) as exc_info:
        inspect_registry_metadata(
            client=client,
            series=CANDIDATE_SERIES,
            observed_at=_VERIFIED_AT,
        )

    assert exc_info.value.diagnostic.to_payload() == {
        "diagnosticVersion": 1,
        "failureReason": "identity_mismatch",
        "failureStage": "registry_identity",
        "field": "item_code",
        "fieldKind": "mismatch",
        "requestOrdinal": 2,
        "seriesId": "policy-rate",
        "service": "StatisticItemList",
    }


def test_metadata_inspection_rejects_non_searchable_table_before_item_call() -> None:
    from app.data.ecos.registry_preflight import inspect_registry_metadata

    client = _PreflightClient(searchable=False)

    with pytest.raises(ECOSParseError) as exc_info:
        inspect_registry_metadata(
            client=client,
            series=CANDIDATE_SERIES,
            observed_at=_VERIFIED_AT,
        )

    assert client.calls == [("table", "policy-rate")]
    assert exc_info.value.diagnostic.to_payload() == {
        "diagnosticVersion": 1,
        "failureReason": "not_searchable",
        "failureStage": "searchability",
        "field": "searchable",
        "fieldKind": "mismatch",
        "requestOrdinal": 1,
        "seriesId": "policy-rate",
        "service": "StatisticTableList",
    }
