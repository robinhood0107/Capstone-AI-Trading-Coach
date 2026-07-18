"""Schema의 S1.4X uniqueness annotation이 단순 문서가 아니라 실행 gate인지 검증한다."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from oracle_common import OracleContractError, strict_json_load
from validate_contract import validate_executable_schema_annotations


@pytest.mark.parametrize(
    ("annotation", "entries"),
    [
        (
            {"x-s1-4x-unique-by": "featureId"},
            [{"featureId": "same", "value": 1}, {"featureId": "same", "value": 2}],
        ),
        (
            {
                "x-s1-4x-unique-by-composite": [
                    "language",
                    "file",
                    "rule",
                    "symbol",
                ]
            },
            [
                {
                    "language": "scala",
                    "file": "Kernel.scala",
                    "rule": "no-partial",
                    "symbol": "Option.get",
                },
                {
                    "language": "scala",
                    "file": "Kernel.scala",
                    "rule": "no-partial",
                    "symbol": "Option.get",
                },
            ],
        ),
    ],
)
def test_custom_uniqueness_annotations_reject_duplicate_identities(
    annotation: dict[str, Any],
    entries: list[dict[str, Any]],
) -> None:
    schema = {
        "type": "object",
        "properties": {
            "entries": {
                "type": "array",
                **annotation,
            }
        },
    }

    with pytest.raises(OracleContractError, match="duplicate annotated identity"):
        validate_executable_schema_annotations(schema, {"entries": entries})


def test_custom_uniqueness_annotation_accepts_distinct_identities() -> None:
    schema = {
        "type": "array",
        "x-s1-4x-unique-by": "featureId",
    }
    validate_executable_schema_annotations(
        schema,
        [{"featureId": "one"}, {"featureId": "two"}],
    )


def test_local_ref_json_pointer_executes_annotation_at_instance_path() -> None:
    schema = {
        "type": "object",
        "properties": {
            "entries": {
                "$ref": "#/$defs/list~1with~0escape",
            }
        },
        "$defs": {
            "list/with~escape": {
                "type": "array",
                "x-s1-4x-unique-by": "featureId",
            }
        },
    }

    with pytest.raises(
        OracleContractError,
        match=r"\$\.entries contains duplicate annotated identity",
    ):
        validate_executable_schema_annotations(
            schema,
            {
                "entries": [
                    {"featureId": "same"},
                    {"featureId": "same"},
                ]
            },
        )


def test_local_self_reference_cycle_does_not_skip_adjacent_annotation() -> None:
    schema = {
        "$ref": "#",
        "type": "array",
        "x-s1-4x-unique-by": "featureId",
    }

    with pytest.raises(OracleContractError, match="duplicate annotated identity"):
        validate_executable_schema_annotations(
            schema,
            [{"featureId": "same"}, {"featureId": "same"}],
        )


def test_actual_scala_capability_smoke_duplicate_passes_draft_but_fails_executor() -> None:
    contract_root = Path(__file__).resolve().parents[2] / "contract"
    schema_value = strict_json_load(
        contract_root / "schemas" / "capability-smoke-plan.schema.json"
    )
    instance_value = strict_json_load(contract_root / "capability-smoke-plan.v1.json")
    assert isinstance(schema_value, dict)
    assert isinstance(instance_value, dict)
    languages = instance_value["languages"]
    assert isinstance(languages, dict)
    scala = languages["scala"]
    assert isinstance(scala, dict)
    smokes = scala["smokes"]
    assert isinstance(smokes, list)
    smokes.append(deepcopy(smokes[0]))

    assert Draft202012Validator(schema_value).is_valid(instance_value)
    with pytest.raises(
        OracleContractError,
        match=r"languages\.scala\.smokes contains duplicate annotated identity",
    ):
        validate_executable_schema_annotations(schema_value, instance_value)
