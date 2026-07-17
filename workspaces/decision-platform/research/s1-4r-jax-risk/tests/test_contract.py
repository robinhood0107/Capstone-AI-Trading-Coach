from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
from pathlib import Path

import pytest

import s1_4r_risk_research
from s1_4r_risk_research import numpy_reference
from s1_4r_risk_research.errors import RESEARCH_ERROR_CODES, ResearchValidationError
from s1_4r_risk_research.models import (
    ConditionalCoverageTestResult,
    EffectiveTrialProvenance,
    IndependenceTestResult,
    LikelihoodRatioTestResult,
    TransitionCounts,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = PROJECT_ROOT / "tests/fixtures/canonical/advanced_risk_v1.json"
FIXTURE_DIGEST_PATH = FIXTURE_PATH.with_suffix(".sha256")

EXPECTED_FUNCTIONS = {
    "historical_expected_shortfall",
    "realized_variance",
    "realized_volatility_intraday",
    "lo_adjusted_sharpe_ratio",
    "probabilistic_sharpe_ratio",
    "deflated_sharpe_ratio",
    "kupiec_unconditional_coverage_test",
    "christoffersen_independence_test",
    "christoffersen_conditional_coverage_test",
}

EXPECTED_ERROR_CODES = {
    "research_input_invalid",
    "research_input_too_short",
    "aggregation_periods_invalid",
    "moment_invalid",
    "trial_count_invalid",
    "trial_variance_invalid",
    "trial_provenance_invalid",
    "significance_invalid",
    "forecast_shape_invalid",
    "forecast_var_negative",
    "insufficient_sample",
    "likelihood_invalid",
    "research_result_non_finite",
}


def test_canonical_fixture_raw_bytes_match_adjacent_sha256() -> None:
    expected = FIXTURE_DIGEST_PATH.read_text(encoding="ascii").strip()
    actual = hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    assert actual == expected


def test_canonical_fixture_is_strict_finite_json() -> None:
    def reject_non_finite(token: str) -> None:
        raise AssertionError(f"non-standard JSON number: {token}")

    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"), parse_constant=reject_non_finite)
    assert fixture["schemaVersion"] == "s1.4r-advanced-risk-v1"
    assert set(fixture["stableErrorCodes"]) == EXPECTED_ERROR_CODES


def test_research_package_has_no_production_style_re_exports() -> None:
    assert s1_4r_risk_research.__all__ == ()
    assert EXPECTED_FUNCTIONS.isdisjoint(vars(s1_4r_risk_research))


def test_numpy_module_exposes_exact_research_function_set() -> None:
    public_functions = {
        name
        for name, value in vars(numpy_reference).items()
        if (
            not name.startswith("_")
            and inspect.isfunction(value)
            and value.__module__ == numpy_reference.__name__
        )
    }
    assert public_functions == EXPECTED_FUNCTIONS


def test_error_module_exposes_exact_stable_code_set() -> None:
    assert RESEARCH_ERROR_CODES == EXPECTED_ERROR_CODES


@pytest.mark.parametrize(
    "model",
    [
        EffectiveTrialProvenance,
        LikelihoodRatioTestResult,
        TransitionCounts,
        IndependenceTestResult,
        ConditionalCoverageTestResult,
    ],
)
def test_result_models_are_frozen_slotted_dataclasses(model: type[object]) -> None:
    assert dataclasses.is_dataclass(model)
    assert model.__dataclass_params__.frozen is True
    assert "__slots__" in vars(model)


def test_conditional_result_freezes_component_fields() -> None:
    assert [field.name for field in dataclasses.fields(EffectiveTrialProvenance)] == [
        "schema_version",
        "method",
        "raw_trial_count",
        "effective_trial_count",
        "sampling_frequency",
        "trial_registry_sha256",
        "variance_ddof",
    ]
    assert [field.name for field in dataclasses.fields(LikelihoodRatioTestResult)] == [
        "statistic",
        "p_value",
        "reject",
        "observations",
        "exceptions",
        "degrees_of_freedom",
        "significance",
    ]
    assert [field.name for field in dataclasses.fields(TransitionCounts)] == [
        "n00",
        "n01",
        "n10",
        "n11",
    ]
    assert [field.name for field in dataclasses.fields(IndependenceTestResult)] == [
        "statistic",
        "p_value",
        "reject",
        "observations",
        "exceptions",
        "degrees_of_freedom",
        "significance",
        "transitions",
    ]
    assert [field.name for field in dataclasses.fields(ConditionalCoverageTestResult)] == [
        "statistic",
        "p_value",
        "reject",
        "observations",
        "exceptions",
        "degrees_of_freedom",
        "significance",
        "transitions",
        "conditioned_observations",
        "conditioned_exceptions",
        "unconditional_component_statistic",
        "independence_component_statistic",
    ]


@pytest.mark.parametrize("code", sorted(EXPECTED_ERROR_CODES))
def test_stable_error_preserves_exact_string_contract(code: str) -> None:
    error = ResearchValidationError(code)
    assert error.code == code
    assert str(error) == code


def test_dsr_signature_requires_keyword_only_provenance() -> None:
    parameter = inspect.signature(numpy_reference.deflated_sharpe_ratio).parameters[
        "trial_provenance"
    ]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
