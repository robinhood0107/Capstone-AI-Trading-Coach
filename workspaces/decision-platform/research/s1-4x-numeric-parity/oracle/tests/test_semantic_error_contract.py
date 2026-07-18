"""Semantic-invalid fixture가 frozen stable error와 1:1로 대응하는지 검증한다."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from oracle_common import OracleContractError, atomic_write_json, strict_json_load
from validate_contract import (
    validate_registries,
    validate_semantic_error_fixtures,
)


def _contract() -> Path:
    return Path(__file__).resolve().parents[2] / "contract"


def test_semantic_error_corpus_has_all_15_exact_oracle_results() -> None:
    functions, errors = validate_registries(_contract())

    assert (
        validate_semantic_error_fixtures(
            _contract(),
            functions=functions,
            errors=errors,
        )
        == 15
    )


def test_semantic_error_corpus_rejects_wrong_stable_error(tmp_path: Path) -> None:
    contract = tmp_path / "contract"
    shutil.copytree(_contract(), contract)
    expected_path = (
        contract
        / "fixtures"
        / "invalid"
        / "semantic-errors.expected.v1.json"
    )
    expected = strict_json_load(expected_path)
    expected["results"][0]["errorCode"] = "input_empty"
    atomic_write_json(expected_path, expected)
    functions, errors = validate_registries(contract)

    with pytest.raises(OracleContractError, match="expected result mismatch"):
        validate_semantic_error_fixtures(
            contract,
            functions=functions,
            errors=errors,
        )
