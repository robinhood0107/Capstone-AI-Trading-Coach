from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from s1_4r_risk_research.numpy_reference import (
    christoffersen_conditional_coverage_test,
    christoffersen_independence_test,
    historical_expected_shortfall,
    kupiec_unconditional_coverage_test,
    realized_variance,
    realized_volatility_intraday,
)

REL_TOL = 1e-10
ABS_TOL = 1e-12
FINITE = st.floats(
    min_value=-1_000.0,
    max_value=1_000.0,
    allow_nan=False,
    allow_infinity=False,
    allow_subnormal=False,
    width=64,
)
NONNEGATIVE_SCALE = st.floats(
    min_value=0.0,
    max_value=100.0,
    allow_nan=False,
    allow_infinity=False,
    allow_subnormal=False,
    width=64,
)
SIGNED_SCALE = st.floats(
    min_value=-100.0,
    max_value=100.0,
    allow_nan=False,
    allow_infinity=False,
    allow_subnormal=False,
    width=64,
)
POSITIVE_SCALE = st.floats(
    min_value=0.01,
    max_value=100.0,
    allow_nan=False,
    allow_infinity=False,
    allow_subnormal=False,
    width=64,
)
UNIT_INTERVAL_INTERIOR = st.floats(
    min_value=0.01,
    max_value=0.99,
    allow_nan=False,
    allow_infinity=False,
    allow_subnormal=False,
    width=64,
)
IDENTIFIABLE_EXCEPTIONS = st.lists(
    st.integers(min_value=0, max_value=1),
    min_size=4,
    max_size=30,
).filter(lambda values: 0 in values[:-1] and 1 in values[:-1])


@given(
    data=st.data(),
    values=st.lists(FINITE, min_size=1, max_size=30),
    scale=NONNEGATIVE_SCALE,
    shift=FINITE,
)
def test_historical_es_homogeneity_translation_and_permutation(
    data: st.DataObject,
    values: list[float],
    scale: float,
    shift: float,
) -> None:
    original = historical_expected_shortfall(values, confidence=0.73)
    scaled = historical_expected_shortfall(
        [scale * value for value in values],
        confidence=0.73,
    )
    shifted = historical_expected_shortfall(
        [value + shift for value in values],
        confidence=0.73,
    )
    permuted = historical_expected_shortfall(
        data.draw(st.permutations(values), label="loss-permutation"),
        confidence=0.73,
    )
    assert scaled == pytest.approx(scale * original, rel=REL_TOL, abs=ABS_TOL)
    assert shifted == pytest.approx(original + shift, rel=REL_TOL, abs=ABS_TOL)
    assert permuted == pytest.approx(original, rel=REL_TOL, abs=ABS_TOL)


@given(
    data=st.data(),
    values=st.lists(FINITE, min_size=1, max_size=50),
    scale=SIGNED_SCALE,
)
def test_realized_metrics_scaling_and_permutation(
    data: st.DataObject,
    values: list[float],
    scale: float,
) -> None:
    # Bounds keep the squared sum finite while still exercising float64 reductions.
    variance = realized_variance(values)
    volatility = realized_volatility_intraday(values)
    scaled_values = [scale * value for value in values]
    permutation = data.draw(st.permutations(values), label="return-permutation")
    scaled_variance = realized_variance(scaled_values)
    scaled_volatility = realized_volatility_intraday(scaled_values)
    assert scaled_variance == pytest.approx(
        scale * scale * variance,
        rel=REL_TOL,
        abs=ABS_TOL,
    )
    assert scaled_volatility == pytest.approx(
        abs(scale) * volatility,
        rel=REL_TOL,
        abs=ABS_TOL,
    )
    assert realized_variance(permutation) == pytest.approx(
        variance,
        rel=REL_TOL,
        abs=ABS_TOL,
    )
    assert realized_volatility_intraday(permutation) == pytest.approx(
        volatility,
        rel=REL_TOL,
        abs=ABS_TOL,
    )
    assert math.isfinite(variance)
    assert math.isfinite(volatility)


def test_realized_metrics_use_float64_without_input_aliasing() -> None:
    values = np.array([0.1, -0.2, 0.3], dtype=np.float32)
    before = values.copy()
    expected = float(
        np.sum(
            np.square(values.astype(np.float64)),
            dtype=np.float64,
        )
    )
    assert realized_variance(values) == pytest.approx(
        expected,
        rel=REL_TOL,
        abs=ABS_TOL,
    )
    assert np.array_equal(values, before)


@given(
    data=st.data(),
    exceptions=IDENTIFIABLE_EXCEPTIONS,
    confidence=UNIT_INTERVAL_INTERIOR,
    significance=UNIT_INTERVAL_INTERIOR,
    scale=POSITIVE_SCALE,
)
def test_backtest_properties_and_paired_permutation(
    data: st.DataObject,
    exceptions: list[int],
    confidence: float,
    significance: float,
    scale: float,
) -> None:
    realized = [2.0 if exception else 0.0 for exception in exceptions]
    forecast = [1.0] * len(exceptions)
    indices = data.draw(
        st.permutations(tuple(range(len(exceptions)))),
        label="paired-permutation",
    )
    permuted_realized = [realized[index] for index in indices]
    permuted_forecast = [forecast[index] for index in indices]
    scaled_realized = [scale * value for value in realized]
    scaled_forecast = [scale * value for value in forecast]

    kupiec = kupiec_unconditional_coverage_test(
        realized,
        forecast,
        confidence=confidence,
        significance=significance,
    )
    independence = christoffersen_independence_test(
        realized,
        forecast,
        significance=significance,
    )
    conditional = christoffersen_conditional_coverage_test(
        realized,
        forecast,
        confidence=confidence,
        significance=significance,
    )

    assert kupiec_unconditional_coverage_test(
        permuted_realized,
        permuted_forecast,
        confidence=confidence,
        significance=significance,
    ) == kupiec
    assert kupiec_unconditional_coverage_test(
        scaled_realized,
        scaled_forecast,
        confidence=confidence,
        significance=significance,
    ) == kupiec
    assert christoffersen_independence_test(
        scaled_realized,
        scaled_forecast,
        significance=significance,
    ) == independence
    assert christoffersen_conditional_coverage_test(
        scaled_realized,
        scaled_forecast,
        confidence=confidence,
        significance=significance,
    ) == conditional

    for result in (kupiec, independence, conditional):
        assert result.statistic >= 0.0
        assert 0.0 <= result.p_value <= 1.0
        assert result.reject is (result.p_value < significance)
    assert conditional.statistic == pytest.approx(
        conditional.unconditional_component_statistic
        + conditional.independence_component_statistic,
        rel=REL_TOL,
        abs=ABS_TOL,
    )
