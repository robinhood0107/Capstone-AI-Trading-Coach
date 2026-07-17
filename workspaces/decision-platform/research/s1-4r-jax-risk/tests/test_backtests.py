from __future__ import annotations

import json
from pathlib import Path

import pytest

from s1_4r_risk_research.errors import ResearchValidationError
from s1_4r_risk_research.numpy_reference import (
    christoffersen_conditional_coverage_test,
    christoffersen_independence_test,
    kupiec_unconditional_coverage_test,
    lo_adjusted_sharpe_ratio,
)

FIXTURE = json.loads(
    (
        Path(__file__).parent / "fixtures/canonical/advanced_risk_v1.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", FIXTURE["kupiec"], ids=lambda x: x["id"])
def test_kupiec_full_sample_hand_cases(case: dict[str, object]) -> None:
    result = kupiec_unconditional_coverage_test(
        case["realizedLosses"],
        case["forecastVars"],
        confidence=case["confidence"],
    )
    assert result.statistic == pytest.approx(case["statistic"], rel=0.0, abs=1e-14)
    assert result.p_value == pytest.approx(case["pValue"], rel=0.0, abs=2e-15)
    assert result.observations == case["observations"]
    assert result.exceptions == case["exceptions"]
    assert result.degrees_of_freedom == 1


def test_equality_is_non_exception() -> None:
    result = kupiec_unconditional_coverage_test(
        [1.0, 1.0, 1.0],
        [1.0, 1.0, 1.0],
        confidence=0.75,
    )
    assert result.exceptions == 0


def test_independence_transition_fixture() -> None:
    case = FIXTURE["christoffersen"]
    result = christoffersen_independence_test(
        case["realizedLosses"],
        case["forecastVars"],
    )
    assert result.transitions.n00 == case["transitions"]["n00"]
    assert result.transitions.n01 == case["transitions"]["n01"]
    assert result.transitions.n10 == case["transitions"]["n10"]
    assert result.transitions.n11 == case["transitions"]["n11"]
    assert result.statistic == pytest.approx(case["independenceStatistic"], abs=1e-14)
    assert result.p_value == pytest.approx(case["independencePValue"], abs=2e-15)


def test_conditional_coverage_uses_conditioned_uc_component() -> None:
    case = FIXTURE["christoffersen"]
    conditional = christoffersen_conditional_coverage_test(
        case["realizedLosses"],
        case["forecastVars"],
        confidence=case["confidence"],
    )
    full_sample = kupiec_unconditional_coverage_test(
        case["realizedLosses"],
        case["forecastVars"],
        confidence=case["confidence"],
    )
    assert conditional.conditioned_observations == case["conditionedObservations"]
    assert conditional.conditioned_exceptions == case["conditionedExceptions"]
    assert conditional.unconditional_component_statistic == pytest.approx(
        case["conditionedUnconditionalStatistic"], abs=1e-14
    )
    assert conditional.independence_component_statistic == pytest.approx(
        case["independenceStatistic"], abs=1e-14
    )
    assert conditional.statistic == pytest.approx(
        conditional.unconditional_component_statistic
        + conditional.independence_component_statistic,
        abs=1e-15,
    )
    assert conditional.statistic == pytest.approx(
        case["conditionalCoverageStatistic"], abs=1e-14
    )
    assert conditional.p_value == pytest.approx(case["conditionalCoveragePValue"], abs=2e-15)
    assert conditional.unconditional_component_statistic != pytest.approx(
        full_sample.statistic, abs=1e-12
    )


def test_order_changes_lo_and_christoffersen_results() -> None:
    ordered_lo = lo_adjusted_sharpe_ratio(
        [-1.0, 0.0, 1.0, 2.0],
        aggregation_periods=2,
    )
    permuted_lo = lo_adjusted_sharpe_ratio(
        [-1.0, 2.0, 0.0, 1.0],
        aggregation_periods=2,
    )
    ordered_independence = christoffersen_independence_test(
        [0.0, 2.0, 0.0, 2.0, 0.0],
        [1.0] * 5,
    )
    permuted_independence = christoffersen_independence_test(
        [0.0, 0.0, 2.0, 2.0, 0.0],
        [1.0] * 5,
    )
    ordered_conditional = christoffersen_conditional_coverage_test(
        [0.0, 2.0, 0.0, 2.0, 0.0],
        [1.0] * 5,
        confidence=0.6,
    )
    permuted_conditional = christoffersen_conditional_coverage_test(
        [0.0, 0.0, 2.0, 2.0, 0.0],
        [1.0] * 5,
        confidence=0.6,
    )
    assert ordered_lo != pytest.approx(permuted_lo)
    assert ordered_independence.statistic != pytest.approx(permuted_independence.statistic)
    assert ordered_conditional.statistic != pytest.approx(permuted_conditional.statistic)


def test_reject_uses_strict_p_value_comparison() -> None:
    case = FIXTURE["kupiec"][0]
    baseline = kupiec_unconditional_coverage_test(
        case["realizedLosses"],
        case["forecastVars"],
        confidence=case["confidence"],
    )
    equal = kupiec_unconditional_coverage_test(
        case["realizedLosses"],
        case["forecastVars"],
        confidence=case["confidence"],
        significance=baseline.p_value,
    )
    assert equal.reject is False


def test_low_positive_confidence_does_not_round_exception_probability_to_one() -> None:
    kupiec = kupiec_unconditional_coverage_test(
        [0.0, 2.0],
        [1.0, 1.0],
        confidence=1e-20,
    )
    conditional = christoffersen_conditional_coverage_test(
        [0.0, 2.0, 0.0, 2.0, 0.0],
        [1.0] * 5,
        confidence=1e-20,
    )

    assert kupiec.statistic == pytest.approx(
        89.33081499752205,
        rel=1e-12,
        abs=1e-12,
    )
    assert kupiec.p_value == pytest.approx(
        3.3401567794716263e-21,
        rel=1e-12,
        abs=1e-30,
    )
    assert conditional.statistic >= 0.0
    assert 0.0 <= conditional.p_value <= 1.0


@pytest.mark.parametrize(
    ("realized", "forecast"),
    [
        ([0.0, 0.0, 0.0], [1.0, 1.0, 1.0]),
        ([2.0, 2.0, 2.0], [1.0, 1.0, 1.0]),
        ([0.0, 2.0], [1.0, 1.0]),
    ],
)
def test_independence_unidentifiable_rows_are_insufficient(
    realized: list[float], forecast: list[float]
) -> None:
    calls = (
        lambda: christoffersen_independence_test(realized, forecast),
        lambda: christoffersen_conditional_coverage_test(
            realized,
            forecast,
            confidence=0.95,
        ),
    )
    for call in calls:
        with pytest.raises(ResearchValidationError, match=r"^insufficient_sample$"):
            call()


@pytest.mark.parametrize(
    ("call", "code"),
    [
        (
            lambda: kupiec_unconditional_coverage_test(
                [1.0, 2.0], [1.0], confidence=0.95
            ),
            "forecast_shape_invalid",
        ),
        (
            lambda: kupiec_unconditional_coverage_test(
                [1.0, 2.0], [[1.0, 1.0]], confidence=0.95
            ),
            "forecast_shape_invalid",
        ),
        (
            lambda: kupiec_unconditional_coverage_test(
                [1.0, 2.0], [[1.0], [1.0, 1.0]], confidence=0.95
            ),
            "forecast_shape_invalid",
        ),
        (
            lambda: kupiec_unconditional_coverage_test(
                [1.0, 2.0], [1.0, -1.0], confidence=0.95
            ),
            "forecast_var_negative",
        ),
        (
            lambda: kupiec_unconditional_coverage_test(
                [1.0, 2.0], [1.0, 1.0], confidence=True
            ),
            "research_input_invalid",
        ),
        (
            lambda: kupiec_unconditional_coverage_test(
                [1.0, 2.0], [1.0, 1.0], confidence=0.95, significance=1.0
            ),
            "significance_invalid",
        ),
    ],
)
def test_backtest_validation_precedence(call: object, code: str) -> None:
    with pytest.raises(ResearchValidationError, match=f"^{code}$"):
        call()
