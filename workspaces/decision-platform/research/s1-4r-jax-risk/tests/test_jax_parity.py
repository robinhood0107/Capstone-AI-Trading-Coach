from __future__ import annotations

import math
from dataclasses import fields, is_dataclass
from typing import Any

import jax
import jax.numpy as jnp
import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from s1_4r_risk_research import _jax_kernels, jax_reference, numpy_reference
from s1_4r_risk_research.errors import ResearchValidationError
from s1_4r_risk_research.models import EffectiveTrialProvenance

PARITY_REL_TOL = 1e-10
PARITY_ABS_TOL = 1e-12

_VALID_PROVENANCE = EffectiveTrialProvenance(
    schema_version="s1.4r-effective-trials-v1",
    method="pre_registered_independent",
    raw_trial_count=2,
    effective_trial_count=2,
    sampling_frequency="daily",
    trial_registry_sha256="b" * 64,
    variance_ddof=1,
)
_MISMATCHED_PROVENANCE = EffectiveTrialProvenance(
    schema_version="s1.4r-effective-trials-v1",
    method="externally_estimated_effective_count",
    raw_trial_count=3,
    effective_trial_count=3,
    sampling_frequency="daily",
    trial_registry_sha256="c" * 64,
    variance_ddof=1,
)
_MALFORMED_PROVENANCE = EffectiveTrialProvenance(
    schema_version="s1.4r-effective-trials-v1",
    method=[],  # type: ignore[arg-type]
    raw_trial_count=2,
    effective_trial_count=2,
    sampling_frequency="daily",
    trial_registry_sha256="d" * 64,
    variance_ddof=1,
)


def _success_cases() -> tuple[tuple[str, tuple[Any, ...], dict[str, Any]], ...]:
    alternating_losses = np.asarray([0.0, 2.0, 0.0, 2.0, 0.0], dtype=np.float64)
    alternating_vars = np.ones(5, dtype=np.float64)
    return (
        (
            "historical_expected_shortfall",
            (np.asarray([1.0, 2.0, 3.0, 4.0], dtype=np.float64),),
            {"confidence": 0.625},
        ),
        (
            "realized_variance",
            (np.asarray([0.01, -0.02, 0.03], dtype=np.float64),),
            {},
        ),
        (
            "realized_volatility_intraday",
            (np.asarray([1.0, -2.0, 2.0], dtype=np.float64),),
            {},
        ),
        (
            "lo_adjusted_sharpe_ratio",
            (np.asarray([-1.0, 0.0, 1.0, 2.0], dtype=np.float64),),
            {"aggregation_periods": 2, "risk_free_rate": 0.0},
        ),
        (
            "probabilistic_sharpe_ratio",
            (1.0,),
            {
                "benchmark_sharpe": 0.0,
                "sample_size": 6,
                "skewness": 0.0,
                "kurtosis": 3.0,
            },
        ),
        (
            "deflated_sharpe_ratio",
            (1.0,),
            {
                "sample_size": 6,
                "skewness": 0.0,
                "kurtosis": 3.0,
                "trial_count": 2,
                "sharpe_estimate_variance": 1.0,
                "trial_provenance": _VALID_PROVENANCE,
            },
        ),
        (
            "kupiec_unconditional_coverage_test",
            (
                np.asarray([0.0, 0.0, 0.0, 0.0], dtype=np.float64),
                np.ones(4, dtype=np.float64),
            ),
            {"confidence": 0.75, "significance": 0.05},
        ),
        (
            "christoffersen_independence_test",
            (alternating_losses, alternating_vars),
            {"significance": 0.05},
        ),
        (
            "christoffersen_conditional_coverage_test",
            (alternating_losses, alternating_vars),
            {"confidence": 0.6, "significance": 0.05},
        ),
    )


def _invalid_cases() -> tuple[tuple[str, tuple[Any, ...], dict[str, Any], str], ...]:
    return (
        (
            "historical_expected_shortfall",
            (np.asarray([1.0, np.nan], dtype=np.float64),),
            {"confidence": 0.95},
            "research_input_invalid",
        ),
        (
            "historical_expected_shortfall",
            (np.asarray([], dtype=np.float64),),
            {"confidence": 0.95},
            "research_input_too_short",
        ),
        (
            "realized_variance",
            (np.asarray([[1.0, 2.0]], dtype=np.float64),),
            {},
            "research_input_invalid",
        ),
        (
            "realized_variance",
            (np.asarray([np.finfo(np.float64).max], dtype=np.float64),),
            {},
            "research_result_non_finite",
        ),
        (
            "realized_volatility_intraday",
            (np.asarray([], dtype=np.float64),),
            {},
            "research_input_too_short",
        ),
        (
            "lo_adjusted_sharpe_ratio",
            (np.asarray([-1.0, 0.0, 1.0, 2.0], dtype=np.float64),),
            {"aggregation_periods": True, "risk_free_rate": 0.0},
            "aggregation_periods_invalid",
        ),
        (
            "lo_adjusted_sharpe_ratio",
            (np.asarray([1.0, 1.0, 1.0], dtype=np.float64),),
            {"aggregation_periods": 1, "risk_free_rate": 0.0},
            "moment_invalid",
        ),
        (
            "lo_adjusted_sharpe_ratio",
            (np.asarray([1.0, 2.0, 3.0], dtype=np.float64),),
            {"aggregation_periods": 0, "risk_free_rate": "bad"},
            "research_input_invalid",
        ),
        (
            "probabilistic_sharpe_ratio",
            (1.0,),
            {
                "benchmark_sharpe": 0.0,
                "sample_size": 1,
                "skewness": 0.0,
                "kurtosis": 3.0,
            },
            "research_input_too_short",
        ),
        (
            "probabilistic_sharpe_ratio",
            (1.0,),
            {
                "benchmark_sharpe": 0.0,
                "sample_size": 6,
                "skewness": 2.0,
                "kurtosis": 3.0,
            },
            "moment_invalid",
        ),
        (
            "probabilistic_sharpe_ratio",
            (1.0,),
            {
                "benchmark_sharpe": -1e308,
                "sample_size": 6,
                "skewness": 0.0,
                "kurtosis": 3.0,
            },
            "research_result_non_finite",
        ),
        (
            "probabilistic_sharpe_ratio",
            (1e308,),
            {
                "benchmark_sharpe": -1e308,
                "sample_size": 2,
                "skewness": 0.0,
                "kurtosis": 1.0,
            },
            "research_result_non_finite",
        ),
        (
            "probabilistic_sharpe_ratio",
            (1.0,),
            {
                "benchmark_sharpe": 0.0,
                "sample_size": 10**400,
                "skewness": 0.0,
                "kurtosis": 3.0,
            },
            "research_input_invalid",
        ),
        (
            "deflated_sharpe_ratio",
            (1.0,),
            {
                "sample_size": 6,
                "skewness": 0.0,
                "kurtosis": 3.0,
                "trial_count": True,
                "sharpe_estimate_variance": 1.0,
                "trial_provenance": _VALID_PROVENANCE,
            },
            "trial_count_invalid",
        ),
        (
            "deflated_sharpe_ratio",
            (1.0,),
            {
                "sample_size": 6,
                "skewness": 0.0,
                "kurtosis": 3.0,
                "trial_count": 2,
                "sharpe_estimate_variance": 0.0,
                "trial_provenance": _VALID_PROVENANCE,
            },
            "trial_variance_invalid",
        ),
        (
            "deflated_sharpe_ratio",
            (1.0,),
            {
                "sample_size": 6,
                "skewness": 0.0,
                "kurtosis": 3.0,
                "trial_count": 2,
                "sharpe_estimate_variance": 1.0,
                "trial_provenance": _MISMATCHED_PROVENANCE,
            },
            "trial_provenance_invalid",
        ),
        (
            "deflated_sharpe_ratio",
            (1.0,),
            {
                "sample_size": 6,
                "skewness": 0.0,
                "kurtosis": 3.0,
                "trial_count": 2,
                "sharpe_estimate_variance": 1.0,
                "trial_provenance": _MALFORMED_PROVENANCE,
            },
            "trial_provenance_invalid",
        ),
        (
            "deflated_sharpe_ratio",
            (1.0,),
            {
                "sample_size": 6,
                "skewness": 0.0,
                "kurtosis": 3.0,
                "trial_count": 10**400,
                "sharpe_estimate_variance": 1.0,
                "trial_provenance": _VALID_PROVENANCE,
            },
            "trial_count_invalid",
        ),
        (
            "kupiec_unconditional_coverage_test",
            (
                np.asarray([0.0, 2.0], dtype=np.float64),
                np.asarray([1.0], dtype=np.float64),
            ),
            {"confidence": 0.95, "significance": 0.05},
            "forecast_shape_invalid",
        ),
        (
            "kupiec_unconditional_coverage_test",
            (
                np.asarray([0.0, 2.0], dtype=np.float64),
                np.asarray([1.0, -1.0], dtype=np.float64),
            ),
            {"confidence": 0.95, "significance": 0.05},
            "forecast_var_negative",
        ),
        (
            "christoffersen_independence_test",
            (
                np.asarray([0.0, 0.0, 0.0], dtype=np.float64),
                np.ones(3, dtype=np.float64),
            ),
            {"significance": 0.05},
            "insufficient_sample",
        ),
        (
            "christoffersen_conditional_coverage_test",
            (
                np.asarray([0.0, 2.0, 0.0, 2.0, 0.0], dtype=np.float64),
                np.ones(5, dtype=np.float64),
            ),
            {"confidence": 0.6, "significance": 1.0},
            "significance_invalid",
        ),
    )


def _snapshot(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.copy()
    if isinstance(value, tuple):
        return tuple(_snapshot(item) for item in value)
    if isinstance(value, dict):
        return {key: _snapshot(item) for key, item in value.items()}
    return value


def _assert_unchanged(before: Any, after: Any) -> None:
    if isinstance(before, np.ndarray):
        assert isinstance(after, np.ndarray)
        np.testing.assert_array_equal(after, before)
        return
    if isinstance(before, tuple):
        assert isinstance(after, tuple)
        assert len(after) == len(before)
        for expected_item, actual_item in zip(before, after, strict=True):
            _assert_unchanged(expected_item, actual_item)
        return
    if isinstance(before, dict):
        assert isinstance(after, dict)
        assert after.keys() == before.keys()
        for key in before:
            _assert_unchanged(before[key], after[key])
        return
    assert after == before


def _assert_parity(expected: object, actual: object) -> None:
    assert type(actual) is type(expected)
    if is_dataclass(expected) and not isinstance(expected, type):
        for field in fields(expected):
            _assert_parity(getattr(expected, field.name), getattr(actual, field.name))
        return
    if isinstance(expected, bool):
        assert actual is expected
        return
    if isinstance(expected, int):
        assert actual == expected
        return
    if isinstance(expected, float):
        assert math.isfinite(expected)
        assert math.isfinite(actual)
        assert math.isclose(
            actual,
            expected,
            rel_tol=PARITY_REL_TOL,
            abs_tol=PARITY_ABS_TOL,
        )
        return
    pytest.fail(f"unsupported result field type: {type(expected)!r}")


@pytest.mark.parametrize("jit", [False, True], ids=["eager", "jit"])
@pytest.mark.parametrize(
    ("function_name", "args", "kwargs"),
    _success_cases(),
    ids=[case[0] for case in _success_cases()],
)
def test_numpy_and_jax_have_nine_function_success_parity(
    function_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    jit: bool,
) -> None:
    numpy_function = getattr(numpy_reference, function_name)
    jax_function = getattr(jax_reference, function_name)
    args_snapshot = _snapshot(args)
    kwargs_snapshot = _snapshot(kwargs)

    expected = numpy_function(*args, **kwargs)
    _assert_unchanged(args_snapshot, args)
    _assert_unchanged(kwargs_snapshot, kwargs)

    actual = jax_function(*args, **kwargs, jit=jit)
    _assert_unchanged(args_snapshot, args)
    _assert_unchanged(kwargs_snapshot, kwargs)
    _assert_parity(expected, actual)


@pytest.mark.parametrize(
    ("function_name", "args", "kwargs", "expected_code"),
    _invalid_cases(),
    ids=[
        f"{case[0]}-{case[3]}-{index}"
        for index, case in enumerate(_invalid_cases(), start=1)
    ],
)
def test_numpy_and_jax_share_host_validation_errors(
    function_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    expected_code: str,
) -> None:
    implementations = (
        ("numpy", getattr(numpy_reference, function_name), {}),
        ("jax-eager", getattr(jax_reference, function_name), {"jit": False}),
        ("jax-jit", getattr(jax_reference, function_name), {"jit": True}),
    )

    for implementation_name, function, extra_kwargs in implementations:
        with pytest.raises(ResearchValidationError) as raised:
            function(*args, **kwargs, **extra_kwargs)
        assert raised.value.code == expected_code, implementation_name
        assert str(raised.value) == expected_code, implementation_name


@pytest.mark.parametrize(
    ("losses", "confidence"),
    [
        ([1e308, 1e308, 0.0, 0.0], 0.5),
        ([1e-308], np.nextafter(1.0, 0.0)),
        ([np.finfo(np.float64).max] * 8, 0.01),
        ([np.finfo(np.float64).max] * 20, 0.5),
    ],
    ids=[
        "large-representable-es",
        "subnormal-fractional-tail",
        "max-constant-fractional-tail",
        "max-constant-half-tail",
    ],
)
def test_es_stability_regressions_match_eager_and_jit(
    losses: list[float],
    confidence: float,
) -> None:
    expected = numpy_reference.historical_expected_shortfall(
        losses,
        confidence=confidence,
    )
    for jit in (False, True):
        actual = jax_reference.historical_expected_shortfall(
            losses,
            confidence=confidence,
            jit=jit,
        )
        _assert_parity(expected, actual)


def test_large_representable_trial_count_matches_eager_and_jit() -> None:
    trial_count = 10**20
    provenance = EffectiveTrialProvenance(
        schema_version="s1.4r-effective-trials-v1",
        method="externally_estimated_effective_count",
        raw_trial_count=trial_count,
        effective_trial_count=trial_count,
        sampling_frequency="daily",
        trial_registry_sha256="e" * 64,
        variance_ddof=1,
    )
    kwargs = {
        "sample_size": 100,
        "skewness": 0.0,
        "kurtosis": 3.0,
        "trial_count": trial_count,
        "sharpe_estimate_variance": 1.0,
        "trial_provenance": provenance,
    }
    expected = numpy_reference.deflated_sharpe_ratio(10.0, **kwargs)
    for jit in (False, True):
        actual = jax_reference.deflated_sharpe_ratio(10.0, **kwargs, jit=jit)
        _assert_parity(expected, actual)


def test_near_float64_limit_trial_count_matches_eager_and_jit() -> None:
    trial_count = 10**308
    provenance = EffectiveTrialProvenance(
        schema_version="s1.4r-effective-trials-v1",
        method="externally_estimated_effective_count",
        raw_trial_count=trial_count,
        effective_trial_count=trial_count,
        sampling_frequency="daily",
        trial_registry_sha256="9" * 64,
        variance_ddof=1,
    )
    kwargs = {
        "sample_size": 2,
        "skewness": 0.0,
        "kurtosis": 3.0,
        "trial_count": trial_count,
        "sharpe_estimate_variance": 1.0,
        "trial_provenance": provenance,
    }
    expected = numpy_reference.deflated_sharpe_ratio(100.0, **kwargs)
    raw_arguments = (
        jnp.asarray(100.0, dtype=jnp.float64),
        jnp.asarray(2.0, dtype=jnp.float64),
        jnp.asarray(0.0, dtype=jnp.float64),
        jnp.asarray(3.0, dtype=jnp.float64),
        jnp.asarray(float(trial_count), dtype=jnp.float64),
        jnp.asarray(1.0, dtype=jnp.float64),
    )
    for kernel in (
        _jax_kernels.deflated_sharpe_ratio,
        jax.jit(_jax_kernels.deflated_sharpe_ratio),
    ):
        raw = kernel(*raw_arguments)
        raw.block_until_ready()
        _assert_parity(expected, float(jax.device_get(raw)))
    for jit in (False, True):
        actual = jax_reference.deflated_sharpe_ratio(
            100.0,
            **kwargs,
            jit=jit,
        )
        _assert_parity(expected, actual)


@pytest.mark.parametrize("jit", [False, True])
def test_public_deflated_sharpe_routes_all_six_inputs_to_dsr_kernel(
    monkeypatch: pytest.MonkeyPatch,
    jit: bool,
) -> None:
    original = _jax_kernels.deflated_sharpe_ratio
    calls: list[tuple[float, ...]] = []

    def recording_kernel(*arguments: jax.Array) -> jax.Array:
        calls.append(
            tuple(float(jax.device_get(argument)) for argument in arguments)
        )
        return original(*arguments)

    monkeypatch.setattr(
        _jax_kernels,
        "deflated_sharpe_ratio",
        recording_kernel,
    )
    if jit:
        monkeypatch.setattr(
            jax_reference,
            "_DEFLATED_SHARPE_JIT",
            recording_kernel,
        )

    actual = jax_reference.deflated_sharpe_ratio(
        1.0,
        sample_size=6,
        skewness=0.0,
        kurtosis=3.0,
        trial_count=2,
        sharpe_estimate_variance=1.0,
        trial_provenance=_VALID_PROVENANCE,
        jit=jit,
    )

    assert math.isfinite(actual)
    assert calls == [(1.0, 6.0, 0.0, 3.0, 2.0, 1.0)]


def test_low_confidence_backtests_match_eager_and_jit() -> None:
    calls = (
        (
            "kupiec_unconditional_coverage_test",
            ([0.0, 2.0], [1.0, 1.0]),
            {"confidence": 1e-20},
        ),
        (
            "christoffersen_conditional_coverage_test",
            ([0.0, 2.0, 0.0, 2.0, 0.0], [1.0] * 5),
            {"confidence": 1e-20},
        ),
    )
    for function_name, args, kwargs in calls:
        expected = getattr(numpy_reference, function_name)(*args, **kwargs)
        for jit in (False, True):
            actual = getattr(jax_reference, function_name)(
                *args,
                **kwargs,
                jit=jit,
            )
            _assert_parity(expected, actual)


def test_near_boundary_lo_denominator_matches_eager_and_jit() -> None:
    observations = 4_000
    indices = np.arange(1, observations + 1, dtype=np.float64)
    returns = ((-1.0) ** indices) * np.sin(
        np.pi * indices / (observations + 1)
    )
    returns = returns - np.mean(returns) + 0.1
    expected = numpy_reference.lo_adjusted_sharpe_ratio(
        returns,
        aggregation_periods=2,
    )
    for jit in (False, True):
        actual = jax_reference.lo_adjusted_sharpe_ratio(
            returns,
            aggregation_periods=2,
            jit=jit,
        )
        _assert_parity(expected, actual)


def test_near_boundary_lo_numeric_kernel_meets_parity_contract() -> None:
    observations = 4_000
    indices = np.arange(1, observations + 1, dtype=np.float64)
    returns = ((-1.0) ** indices) * np.sin(
        np.pi * indices / (observations + 1)
    )
    returns = returns - np.mean(returns) + 0.1
    expected = numpy_reference.lo_adjusted_sharpe_ratio(
        returns,
        aggregation_periods=2,
    )
    device_returns = jnp.asarray(returns, dtype=jnp.float64)
    risk_free_rate = jnp.asarray(0.0, dtype=jnp.float64)
    kernels = (
        _jax_kernels.lo_adjusted_sharpe_ratio,
        jax.jit(
            _jax_kernels.lo_adjusted_sharpe_ratio,
            static_argnames=("aggregation_periods",),
        ),
    )

    for kernel in kernels:
        result = kernel(
            device_returns,
            aggregation_periods=2,
            risk_free_rate=risk_free_rate,
        )
        actual = float(jax.device_get(result.block_until_ready()))
        assert math.isclose(
            actual,
            expected,
            rel_tol=PARITY_REL_TOL,
            abs_tol=PARITY_ABS_TOL,
        )


def test_lo_wrapper_rejects_device_drift_outside_parity_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    returns = np.asarray([-1.0, 0.0, 1.0, 2.0], dtype=np.float64)
    expected = numpy_reference.lo_adjusted_sharpe_ratio(
        returns,
        aggregation_periods=2,
    )
    monkeypatch.setattr(
        jax_reference,
        "_run_scalar",
        lambda *_args, **_kwargs: expected * (1.0 + 5e-9),
    )

    with pytest.raises(ResearchValidationError) as raised:
        jax_reference.lo_adjusted_sharpe_ratio(
            returns,
            aggregation_periods=2,
            jit=True,
        )

    assert raised.value.code == "moment_invalid"


@pytest.mark.parametrize(
    "significance_selector",
    [
        lambda value: math.nextafter(value, 0.0),
        lambda value: value,
        lambda value: math.nextafter(value, 1.0),
    ],
    ids=["below-p", "equal-p", "above-p"],
)
def test_conditional_coverage_strict_reject_uses_canonical_host_probability(
    significance_selector: Any,
) -> None:
    realized = [0.0, 2.0, 0.0, 2.0, 0.0]
    forecast = [1.0] * 5
    baseline = numpy_reference.christoffersen_conditional_coverage_test(
        realized,
        forecast,
        confidence=0.6,
    )
    significance = significance_selector(baseline.p_value)
    expected = numpy_reference.christoffersen_conditional_coverage_test(
        realized,
        forecast,
        confidence=0.6,
        significance=significance,
    )
    for jit in (False, True):
        actual = jax_reference.christoffersen_conditional_coverage_test(
            realized,
            forecast,
            confidence=0.6,
            significance=significance,
            jit=jit,
        )
        assert actual.p_value == expected.p_value
        assert actual.reject is expected.reject
        _assert_parity(expected, actual)


_BOUNDED_FLOAT = st.floats(
    min_value=-10.0,
    max_value=10.0,
    allow_nan=False,
    allow_infinity=False,
    allow_subnormal=False,
    width=64,
)
_POSITIVE_FLOAT = st.floats(
    min_value=0.1,
    max_value=10.0,
    allow_nan=False,
    allow_infinity=False,
    allow_subnormal=False,
    width=64,
)
_INTERIOR_PROBABILITY = st.floats(
    min_value=0.05,
    max_value=0.95,
    allow_nan=False,
    allow_infinity=False,
    allow_subnormal=False,
    width=64,
)
_FIXED_IDENTIFIABLE_EXCEPTIONS = st.lists(
    st.integers(min_value=0, max_value=1),
    min_size=8,
    max_size=8,
).filter(lambda values: 0 in values[:-1] and 1 in values[:-1])


@settings(max_examples=12)
@given(
    losses=st.lists(_BOUNDED_FLOAT, min_size=8, max_size=8),
    scale=_POSITIVE_FLOAT,
    shift=_BOUNDED_FLOAT,
    observed=_BOUNDED_FLOAT,
    benchmark=_BOUNDED_FLOAT,
    trial_count=st.integers(min_value=2, max_value=100),
    variance=_POSITIVE_FLOAT,
    exceptions=_FIXED_IDENTIFIABLE_EXCEPTIONS,
    confidence=_INTERIOR_PROBABILITY,
    significance=_INTERIOR_PROBABILITY,
)
def test_generated_numpy_jax_eager_jit_parity_for_all_nine_functions(
    losses: list[float],
    scale: float,
    shift: float,
    observed: float,
    benchmark: float,
    trial_count: int,
    variance: float,
    exceptions: list[int],
    confidence: float,
    significance: float,
) -> None:
    base_returns = np.asarray(
        [-2.0, -1.0, 0.0, 1.0, 2.0, 3.0, -0.5, 0.5],
        dtype=np.float64,
    )
    ordered_returns = base_returns * scale + shift
    realized = [2.0 if exception else 0.0 for exception in exceptions]
    forecast = [1.0] * len(exceptions)
    provenance = EffectiveTrialProvenance(
        schema_version="s1.4r-effective-trials-v1",
        method="pre_registered_independent",
        raw_trial_count=trial_count,
        effective_trial_count=trial_count,
        sampling_frequency="daily",
        trial_registry_sha256="f" * 64,
        variance_ddof=1,
    )
    calls: tuple[tuple[str, tuple[Any, ...], dict[str, Any]], ...] = (
        ("historical_expected_shortfall", (losses,), {"confidence": confidence}),
        ("realized_variance", (losses,), {}),
        ("realized_volatility_intraday", (losses,), {}),
        (
            "lo_adjusted_sharpe_ratio",
            (ordered_returns,),
            {"aggregation_periods": 2, "risk_free_rate": shift},
        ),
        (
            "probabilistic_sharpe_ratio",
            (observed,),
            {
                "benchmark_sharpe": benchmark,
                "sample_size": 20,
                "skewness": 0.0,
                "kurtosis": 3.0,
            },
        ),
        (
            "deflated_sharpe_ratio",
            (observed,),
            {
                "sample_size": 20,
                "skewness": 0.0,
                "kurtosis": 3.0,
                "trial_count": trial_count,
                "sharpe_estimate_variance": variance,
                "trial_provenance": provenance,
            },
        ),
        (
            "kupiec_unconditional_coverage_test",
            (realized, forecast),
            {"confidence": confidence, "significance": significance},
        ),
        (
            "christoffersen_independence_test",
            (realized, forecast),
            {"significance": significance},
        ),
        (
            "christoffersen_conditional_coverage_test",
            (realized, forecast),
            {"confidence": confidence, "significance": significance},
        ),
    )
    for function_name, args, kwargs in calls:
        expected = getattr(numpy_reference, function_name)(*args, **kwargs)
        for jit in (False, True):
            actual = getattr(jax_reference, function_name)(
                *args,
                **kwargs,
                jit=jit,
            )
            _assert_parity(expected, actual)


@settings(max_examples=12)
@given(magnitude=st.integers(min_value=0, max_value=10_000), high=st.booleans())
def test_generated_invalid_significance_has_stable_backend_error(
    magnitude: int,
    *,
    high: bool,
) -> None:
    significance = 1.0 + magnitude if high else -float(magnitude)
    implementations = (
        (numpy_reference.christoffersen_independence_test, (None,)),
        (jax_reference.christoffersen_independence_test, (False, True)),
    )
    for implementation, jit_values in implementations:
        for jit in jit_values:
            kwargs = {"significance": significance}
            if jit is not None:
                kwargs["jit"] = jit
            with pytest.raises(
                ResearchValidationError,
                match=r"^significance_invalid$",
            ):
                implementation(
                    [0.0, 2.0, 0.0, 2.0, 0.0],
                    [1.0] * 5,
                    **kwargs,
                )
