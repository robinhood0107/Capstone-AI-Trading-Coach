from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.data.ecos.errors import ECOSParseError
from app.data.ecos.parsers import parse_statistic_item_list, parse_statistic_table_list


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
