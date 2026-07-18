from __future__ import annotations

import copy
from typing import Any

import pytest

from compare_results import _validate_batch, compare_batches
from oracle_common import OracleContractError


def _batch(*, implementation: str, value: float = 1.0) -> dict[str, Any]:
    return {
        "schemaVersion": "s1.4x-result-batch-v1",
        "requestId": "request-1",
        "implementation": implementation,
        "results": [
            {
                "schemaVersion": "s1.4x-result-v1",
                "functionId": "cumulative_return",
                "fixtureId": "case-1",
                "status": "ok",
                "values": value,
            },
            {
                "schemaVersion": "s1.4x-result-v1",
                "functionId": "historical_var",
                "fixtureId": "case-error",
                "status": "error",
                "errorCode": "confidence_invalid",
            },
        ],
    }


def test_comparator_applies_frozen_tolerance() -> None:
    expected = _validate_batch(_batch(implementation="oracle"), label="expected")
    actual = _validate_batch(
        _batch(implementation="scala", value=1.0 + 5.0e-13),
        label="actual",
    )

    assert (
        compare_batches(
            expected,
            actual,
            tolerance_classes={"case-1": "handPaper"},
            comparison="oracle->scala",
        )
        == []
    )

    actual["results"][0]["values"] = 1.0 + 2.0e-12
    mismatches = compare_batches(
        expected,
        actual,
        tolerance_classes={"case-1": "handPaper"},
        comparison="oracle->scala",
    )
    assert mismatches[0]["path"] == "$.values"
    assert mismatches[0]["absoluteError"] > 1.0e-12


def test_comparator_reports_order_exact_fields_and_error_code_drift() -> None:
    expected = _validate_batch(_batch(implementation="oracle"), label="expected")
    actual = _batch(implementation="haskell")
    actual["results"].reverse()
    actual["results"][0]["errorCode"] = "tail_empty"
    validated = _validate_batch(actual, label="actual")

    mismatches = compare_batches(
        expected,
        validated,
        tolerance_classes={},
        comparison="oracle->haskell",
    )

    assert any(item["path"] == "results.fixtureIdOrder" for item in mismatches)
    assert any(item["path"].endswith(".keys") for item in mismatches)


def test_batch_rejects_recursive_negative_zero_and_duplicate_id() -> None:
    negative_zero = _batch(implementation="candidate", value=-0.0)
    with pytest.raises(OracleContractError, match="negative zero"):
        _validate_batch(negative_zero, label="candidate")

    duplicate = copy.deepcopy(_batch(implementation="candidate"))
    duplicate["results"][1]["fixtureId"] = "case-1"
    with pytest.raises(OracleContractError, match="duplicate"):
        _validate_batch(duplicate, label="candidate")


def test_batch_rejects_extra_top_level_field_via_canonical_schema() -> None:
    batch = _batch(implementation="scala")
    batch["untrackedEvidence"] = "must-not-cross-the-wire"

    with pytest.raises(
        OracleContractError,
        match=r"violates canonical-result schema.*Additional properties",
    ):
        _validate_batch(batch, label="candidate")


def test_batch_rejects_absolute_path_implementation_via_canonical_schema() -> None:
    batch = _batch(implementation="/home/user/private")

    with pytest.raises(
        OracleContractError,
        match=r"violates canonical-result schema.*does not match",
    ):
        _validate_batch(batch, label="candidate")
