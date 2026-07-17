"""Validated JAX CPU/x64 adapters for the isolated S1.4R research API."""

from __future__ import annotations

import math
from collections.abc import Callable

import jax
import jax.numpy as jnp
import numpy as np

from . import _jax_kernels
from ._numeric_common import (
    EULER_MASCHERONI,
    chi_square_one_survival,
    chi_square_two_survival,
    confidence_exception_log_likelihood,
    finite_probability,
    finite_result,
    independence_likelihood_components,
    kupiec_likelihood_components,
    likelihood_ratio,
    likelihood_roundoff_tolerance,
    normal_cdf,
    normal_inverse_cdf,
    stable_weighted_mean,
)
from ._validation import (
    BacktestInputs,
    transition_counts,
    validate_backtest_sequences,
    validate_confidence,
    validate_integer_scalar,
    validate_moment_pair,
    validate_real_scalar,
    validate_sample_size,
    validate_sequence,
    validate_significance,
    validate_transition_identifiability,
    validate_trial_count,
    validate_trial_provenance,
)
from .errors import ResearchValidationError
from .models import (
    ConditionalCoverageTestResult,
    EffectiveTrialProvenance,
    IndependenceTestResult,
    LikelihoodRatioTestResult,
    TransitionCounts,
)

type ScalarKernel = Callable[..., jax.Array]
type TupleKernel = Callable[..., tuple[jax.Array, ...]]

_FLOAT64_DTYPE = np.dtype(np.float64)

_HISTORICAL_ES_JIT = jax.jit(_jax_kernels.historical_expected_shortfall)
_REALIZED_VARIANCE_JIT = jax.jit(_jax_kernels.realized_variance)
_REALIZED_VOLATILITY_JIT = jax.jit(_jax_kernels.realized_volatility_intraday)
_LO_ADJUSTED_SHARPE_JIT = jax.jit(
    _jax_kernels.lo_adjusted_sharpe_ratio,
    static_argnames=("aggregation_periods",),
)
_PROBABILISTIC_SHARPE_JIT = jax.jit(_jax_kernels.probabilistic_sharpe_ratio)
_DEFLATED_SHARPE_JIT = jax.jit(_jax_kernels.deflated_sharpe_ratio)
_KUPIEC_JIT = jax.jit(_jax_kernels.kupiec_unconditional_coverage_test)
_INDEPENDENCE_JIT = jax.jit(_jax_kernels.christoffersen_independence_test)
_CONDITIONAL_COVERAGE_JIT = jax.jit(
    _jax_kernels.christoffersen_conditional_coverage_test
)


def _assert_runtime_contract() -> None:
    """모든 계산 전에 CPU device와 x64 전역 설정을 fail-closed로 확인한다."""

    devices = jax.devices()
    if (
        jax.default_backend() != "cpu"
        or not devices
        or any(device.platform != "cpu" for device in devices)
        or jax.config.jax_enable_x64 is not True
    ):
        raise RuntimeError("S1.4R JAX requires CPU backend with x64 enabled")


def _validate_jit_flag(value: object) -> bool:
    if type(value) is not bool:
        raise ResearchValidationError("research_input_invalid")
    return value


def _jax_array(values: np.ndarray) -> jax.Array:
    result = jnp.asarray(values, dtype=jnp.float64)
    if np.dtype(result.dtype) != _FLOAT64_DTYPE:
        raise RuntimeError("S1.4R JAX input was not converted to float64")
    return result


def _jax_scalar(value: float | int) -> jax.Array:
    result = jnp.asarray(value, dtype=jnp.float64)
    if np.dtype(result.dtype) != _FLOAT64_DTYPE:
        raise RuntimeError("S1.4R JAX scalar was not converted to float64")
    return result


def _checked_host_float(value: jax.Array) -> float:
    if np.dtype(value.dtype) != _FLOAT64_DTYPE:
        raise RuntimeError("S1.4R JAX output is not float64")
    if any(device.platform != "cpu" for device in value.devices()):
        raise RuntimeError("S1.4R JAX output escaped the CPU backend")
    value.block_until_ready()
    return float(jax.device_get(value))


def _run_scalar(
    eager_kernel: ScalarKernel,
    compiled_kernel: ScalarKernel,
    *args: object,
    jit: object,
    **kwargs: object,
) -> float:
    use_jit = _validate_jit_flag(jit)
    _assert_runtime_contract()
    kernel = compiled_kernel if use_jit else eager_kernel
    return _checked_host_float(kernel(*args, **kwargs))


def _run_tuple(
    eager_kernel: TupleKernel,
    compiled_kernel: TupleKernel,
    *args: object,
    jit: object,
) -> tuple[float, ...]:
    use_jit = _validate_jit_flag(jit)
    _assert_runtime_contract()
    kernel = compiled_kernel if use_jit else eager_kernel
    result = kernel(*args)
    return tuple(_checked_host_float(value) for value in result)


def _validated_lo_inputs(
    returns: object,
    *,
    aggregation_periods: object,
    risk_free_rate: object,
) -> tuple[np.ndarray, int, float, float]:
    values = validate_sequence(returns, minimum_length=2)
    periods = validate_integer_scalar(
        aggregation_periods,
        code="aggregation_periods_invalid",
    )
    risk_free = validate_real_scalar(risk_free_rate)
    if periods <= 0:
        raise ResearchValidationError("aggregation_periods_invalid")
    if values.size <= periods:
        raise ResearchValidationError("research_input_too_short")

    # Kernel 진입 전에 moment 오류를 식별해야 eager/JIT가 같은 stable code를 낸다.
    excess = values - risk_free
    mean = float(np.sum(excess, dtype=np.float64) / float(excess.size))
    centered = excess - mean
    gamma_zero = float(
        np.sum(centered * centered, dtype=np.float64) / float(excess.size)
    )
    if not math.isfinite(gamma_zero) or gamma_zero <= 0.0:
        raise ResearchValidationError("moment_invalid")
    weighted_autocorrelation = 0.0
    for lag in range(1, periods):
        gamma_lag = float(
            np.sum(centered[lag:] * centered[:-lag], dtype=np.float64)
            / float(excess.size)
        )
        weighted_autocorrelation += (1.0 - lag / periods) * (
            gamma_lag / gamma_zero
        )
    denominator = 1.0 + 2.0 * weighted_autocorrelation
    if not math.isfinite(denominator) or denominator <= 0.0:
        raise ResearchValidationError("moment_invalid")
    expected = finite_result(
        (mean / math.sqrt(gamma_zero))
        * math.sqrt(float(periods) / denominator)
    )
    return values, periods, risk_free, expected


def _validated_psr_inputs(
    observed_sharpe: object,
    *,
    benchmark_sharpe: object,
    sample_size: object,
    skewness: object,
    kurtosis: object,
) -> tuple[float, float, int, float, float, float]:
    observed = validate_real_scalar(observed_sharpe)
    benchmark = validate_real_scalar(benchmark_sharpe)
    observations = validate_sample_size(sample_size)
    skew = validate_real_scalar(skewness, code="moment_invalid")
    pearson_kurtosis = validate_real_scalar(kurtosis, code="moment_invalid")
    validate_moment_pair(skew, pearson_kurtosis)
    with np.errstate(over="ignore", invalid="ignore"):
        radicand = (
            1.0
            - skew * observed
            + ((pearson_kurtosis - 1.0) / 4.0) * observed * observed
        )
    if not math.isfinite(radicand) or radicand <= 0.0:
        raise ResearchValidationError("moment_invalid")
    return observed, benchmark, observations, skew, pearson_kurtosis, radicand


def _validated_dsr_inputs(
    observed_sharpe: object,
    *,
    sample_size: object,
    skewness: object,
    kurtosis: object,
    trial_count: object,
    sharpe_estimate_variance: object,
    trial_provenance: object,
) -> tuple[float, int, float, float, int, float, float]:
    observed, _, observations, skew, pearson_kurtosis, radicand = _validated_psr_inputs(
        observed_sharpe,
        benchmark_sharpe=0.0,
        sample_size=sample_size,
        skewness=skewness,
        kurtosis=kurtosis,
    )
    trials = validate_trial_count(trial_count)
    variance = validate_real_scalar(
        sharpe_estimate_variance,
        code="trial_variance_invalid",
    )
    if variance <= 0.0:
        raise ResearchValidationError("trial_variance_invalid")
    validate_trial_provenance(trial_provenance, trial_count=trials)
    reciprocal_trials = 1.0 / trials
    benchmark = math.sqrt(variance) * (
        (1.0 - EULER_MASCHERONI) * -normal_inverse_cdf(reciprocal_trials)
        + EULER_MASCHERONI * -normal_inverse_cdf(reciprocal_trials / math.e)
    )
    benchmark = finite_result(benchmark)
    z_score = (
        (observed - benchmark) * math.sqrt(float(observations - 1))
    ) / math.sqrt(radicand)
    expected_probability = finite_probability(normal_cdf(finite_result(z_score)))
    return (
        observed,
        observations,
        skew,
        pearson_kurtosis,
        trials,
        variance,
        expected_probability,
    )


def _validated_transition_inputs(
    realized_losses: object,
    forecast_vars: object,
    *,
    significance: object,
) -> tuple[BacktestInputs, float, TransitionCounts]:
    inputs = validate_backtest_sequences(
        realized_losses,
        forecast_vars,
        minimum_length=2,
    )
    validated_significance = validate_significance(significance)
    counts = transition_counts(inputs.exceptions)
    validate_transition_identifiability(counts)
    return inputs, validated_significance, counts


def _checked_count(value: float, expected: int) -> int:
    if not math.isfinite(value) or value != float(expected):
        raise ResearchValidationError("likelihood_invalid")
    return expected


def _checked_statistic(
    value: float,
    *,
    expected: float,
) -> float:
    return _checked_numeric(value, expected=expected)


def _checked_numeric(value: float, *, expected: float) -> float:
    """Device 결과를 canonical host float64 값과 검증하고 host 값을 반환한다."""

    return _checked_reference_numeric(
        value,
        expected=expected,
        error_code="likelihood_invalid",
    )


def _checked_reference_numeric(
    value: float,
    *,
    expected: float,
    error_code: str,
    rel_tol: float = 1e-10,
) -> float:
    """Validated device result를 host canonical 값과 비교한 뒤 canonicalize한다."""

    actual = finite_result(value)
    canonical = finite_result(expected)
    if not math.isclose(actual, canonical, rel_tol=rel_tol, abs_tol=1e-12):
        raise ResearchValidationError(error_code)
    return canonical


def _checked_probability(value: float) -> float:
    return finite_probability(value)


def _checked_backtest_probability(value: float, *, expected: float) -> float:
    """Device p-value를 검증하되 reject 계약에는 canonical host 값을 사용한다."""

    actual = finite_probability(value)
    canonical = finite_probability(expected)
    if not math.isclose(actual, canonical, rel_tol=1e-10, abs_tol=1e-12):
        raise ResearchValidationError("likelihood_invalid")
    return canonical


def historical_expected_shortfall(
    losses: object,
    *,
    confidence: float = 0.95,
    jit: bool = False,
) -> float:
    """Loss-space 1-D 표본의 fixed-shape fractional-tail ES를 반환한다."""

    values = validate_sequence(losses)
    validated_confidence = validate_confidence(confidence)
    tail_mass = float(values.size) * (1.0 - validated_confidence)
    ordered = np.sort(values)[::-1]
    indices = np.arange(values.size, dtype=np.float64)
    normalized_weights = np.clip(tail_mass - indices, 0.0, 1.0) / tail_mass
    expected = stable_weighted_mean(ordered, normalized_weights)
    result = _run_scalar(
        _jax_kernels.historical_expected_shortfall,
        _HISTORICAL_ES_JIT,
        _jax_array(values),
        _jax_scalar(validated_confidence),
        jit=jit,
    )
    return _checked_reference_numeric(
        result,
        expected=expected,
        error_code="research_result_non_finite",
    )


def realized_variance(
    intraday_log_returns: object,
    *,
    jit: bool = False,
) -> float:
    """한 세션 intraday log return의 JAX float64 제곱합을 반환한다."""

    values = validate_sequence(intraday_log_returns)
    result = _run_scalar(
        _jax_kernels.realized_variance,
        _REALIZED_VARIANCE_JIT,
        _jax_array(values),
        jit=jit,
    )
    return finite_result(result)


def realized_volatility_intraday(
    intraday_log_returns: object,
    *,
    jit: bool = False,
) -> float:
    """한 세션 realized volatility를 JAX CPU에서 계산한다."""

    values = validate_sequence(intraday_log_returns)
    result = _run_scalar(
        _jax_kernels.realized_volatility_intraday,
        _REALIZED_VOLATILITY_JIT,
        _jax_array(values),
        jit=jit,
    )
    return finite_result(result)


def lo_adjusted_sharpe_ratio(
    returns: object,
    *,
    aggregation_periods: int,
    risk_free_rate: float = 0.0,
    jit: bool = False,
) -> float:
    """Host-validated original-frequency return의 Lo-adjusted Sharpe를 반환한다."""

    values, periods, risk_free, expected = _validated_lo_inputs(
        returns,
        aggregation_periods=aggregation_periods,
        risk_free_rate=risk_free_rate,
    )
    result = _run_scalar(
        _jax_kernels.lo_adjusted_sharpe_ratio,
        _LO_ADJUSTED_SHARPE_JIT,
        _jax_array(values),
        aggregation_periods=periods,
        risk_free_rate=_jax_scalar(risk_free),
        jit=jit,
    )
    return _checked_reference_numeric(
        result,
        expected=expected,
        error_code="moment_invalid",
    )


def probabilistic_sharpe_ratio(
    observed_sharpe: float,
    *,
    benchmark_sharpe: float,
    sample_size: int,
    skewness: float,
    kurtosis: float,
    jit: bool = False,
) -> float:
    """Validated original-frequency SR와 Pearson moments의 PSR을 반환한다."""

    observed, benchmark, observations, skew, pearson_kurtosis, radicand = (
        _validated_psr_inputs(
            observed_sharpe,
            benchmark_sharpe=benchmark_sharpe,
            sample_size=sample_size,
            skewness=skewness,
            kurtosis=kurtosis,
        )
    )
    z_score = (
        (observed - benchmark) * math.sqrt(float(observations - 1))
    ) / math.sqrt(radicand)
    finite_result(z_score)
    result = _run_scalar(
        _jax_kernels.probabilistic_sharpe_ratio,
        _PROBABILISTIC_SHARPE_JIT,
        _jax_scalar(observed),
        _jax_scalar(benchmark),
        _jax_scalar(observations),
        _jax_scalar(skew),
        _jax_scalar(pearson_kurtosis),
        jit=jit,
    )
    return _checked_probability(result)


def deflated_sharpe_ratio(
    observed_sharpe: float,
    *,
    sample_size: int,
    skewness: float,
    kurtosis: float,
    trial_count: int,
    sharpe_estimate_variance: float,
    trial_provenance: EffectiveTrialProvenance,
    jit: bool = False,
) -> float:
    """검증된 effective-trial provenance를 요구하는 JAX DSR을 반환한다."""

    (
        observed,
        observations,
        skew,
        pearson_kurtosis,
        trials,
        variance,
        expected_probability,
    ) = (
        _validated_dsr_inputs(
            observed_sharpe,
            sample_size=sample_size,
            skewness=skewness,
            kurtosis=kurtosis,
            trial_count=trial_count,
            sharpe_estimate_variance=sharpe_estimate_variance,
            trial_provenance=trial_provenance,
        )
    )
    result = _run_scalar(
        _jax_kernels.deflated_sharpe_ratio,
        _DEFLATED_SHARPE_JIT,
        _jax_scalar(observed),
        _jax_scalar(observations),
        _jax_scalar(skew),
        _jax_scalar(pearson_kurtosis),
        _jax_scalar(trials),
        _jax_scalar(variance),
        jit=jit,
    )
    return _checked_reference_numeric(
        result,
        expected=expected_probability,
        error_code="research_result_non_finite",
    )


def kupiec_unconditional_coverage_test(
    realized_losses: object,
    forecast_vars: object,
    *,
    confidence: float,
    significance: float = 0.05,
    jit: bool = False,
) -> LikelihoodRatioTestResult:
    """전체 strict exception sequence의 JAX Kupiec UC 결과를 반환한다."""

    inputs = validate_backtest_sequences(
        realized_losses,
        forecast_vars,
        minimum_length=1,
    )
    validated_confidence = validate_confidence(confidence)
    validated_significance = validate_significance(significance)
    statistic_value, p_value_value, exception_value, null_log, alternative_log = (
        _run_tuple(
            _jax_kernels.kupiec_unconditional_coverage_test,
            _KUPIEC_JIT,
            _jax_array(inputs.realized_losses),
            _jax_array(inputs.forecast_vars),
            _jax_scalar(validated_confidence),
            jit=jit,
        )
    )
    expected_exceptions = int(np.sum(inputs.exceptions, dtype=np.int64))
    exceptions = _checked_count(exception_value, expected_exceptions)
    canonical_statistic, canonical_null_log, canonical_alternative_log = (
        kupiec_likelihood_components(
            int(inputs.exceptions.size),
            expected_exceptions,
            validated_confidence,
        )
    )
    _checked_numeric(null_log, expected=canonical_null_log)
    _checked_numeric(alternative_log, expected=canonical_alternative_log)
    statistic = _checked_statistic(
        statistic_value,
        expected=canonical_statistic,
    )
    p_value = _checked_backtest_probability(
        p_value_value,
        expected=chi_square_one_survival(canonical_statistic),
    )
    return LikelihoodRatioTestResult(
        statistic=statistic,
        p_value=p_value,
        reject=bool(p_value < validated_significance),
        observations=int(inputs.exceptions.size),
        exceptions=exceptions,
        degrees_of_freedom=1,
        significance=float(validated_significance),
    )


def christoffersen_independence_test(
    realized_losses: object,
    forecast_vars: object,
    *,
    significance: float = 0.05,
    jit: bool = False,
) -> IndependenceTestResult:
    """Exception clustering의 JAX Markov 대 iid LR 결과를 반환한다."""

    inputs, validated_significance, counts = _validated_transition_inputs(
        realized_losses,
        forecast_vars,
        significance=significance,
    )
    (
        statistic_value,
        p_value_value,
        exception_value,
        n00_value,
        n01_value,
        n10_value,
        n11_value,
        independent_log,
        markov_log,
    ) = _run_tuple(
        _jax_kernels.christoffersen_independence_test,
        _INDEPENDENCE_JIT,
        _jax_array(inputs.realized_losses),
        _jax_array(inputs.forecast_vars),
        jit=jit,
    )
    expected_exceptions = int(np.sum(inputs.exceptions, dtype=np.int64))
    exceptions = _checked_count(exception_value, expected_exceptions)
    _checked_count(n00_value, counts.n00)
    _checked_count(n01_value, counts.n01)
    _checked_count(n10_value, counts.n10)
    _checked_count(n11_value, counts.n11)
    canonical_statistic, canonical_independent_log, canonical_markov_log = (
        independence_likelihood_components(
            counts.n00,
            counts.n01,
            counts.n10,
            counts.n11,
        )
    )
    _checked_numeric(independent_log, expected=canonical_independent_log)
    _checked_numeric(markov_log, expected=canonical_markov_log)
    statistic = _checked_statistic(
        statistic_value,
        expected=canonical_statistic,
    )
    p_value = _checked_backtest_probability(
        p_value_value,
        expected=chi_square_one_survival(canonical_statistic),
    )
    return IndependenceTestResult(
        statistic=statistic,
        p_value=p_value,
        reject=bool(p_value < validated_significance),
        observations=int(inputs.exceptions.size),
        exceptions=exceptions,
        degrees_of_freedom=1,
        significance=float(validated_significance),
        transitions=counts,
    )


def christoffersen_conditional_coverage_test(
    realized_losses: object,
    forecast_vars: object,
    *,
    confidence: float,
    significance: float = 0.05,
    jit: bool = False,
) -> ConditionalCoverageTestResult:
    """First-observation-conditioned JAX conditional coverage 결과를 반환한다."""

    inputs = validate_backtest_sequences(
        realized_losses,
        forecast_vars,
        minimum_length=2,
    )
    validated_confidence = validate_confidence(confidence)
    validated_significance = validate_significance(significance)
    counts = transition_counts(inputs.exceptions)
    validate_transition_identifiability(counts)
    (
        statistic_value,
        p_value_value,
        exception_value,
        n00_value,
        n01_value,
        n10_value,
        n11_value,
        conditional_null_log,
        independent_log,
        markov_log,
        unconditional_value,
        independence_value,
    ) = _run_tuple(
        _jax_kernels.christoffersen_conditional_coverage_test,
        _CONDITIONAL_COVERAGE_JIT,
        _jax_array(inputs.realized_losses),
        _jax_array(inputs.forecast_vars),
        _jax_scalar(validated_confidence),
        jit=jit,
    )
    expected_exceptions = int(np.sum(inputs.exceptions, dtype=np.int64))
    exceptions = _checked_count(exception_value, expected_exceptions)
    _checked_count(n00_value, counts.n00)
    _checked_count(n01_value, counts.n01)
    _checked_count(n10_value, counts.n10)
    _checked_count(n11_value, counts.n11)
    (
        canonical_independence_component,
        canonical_independent_log,
        canonical_markov_log,
    ) = independence_likelihood_components(
        counts.n00,
        counts.n01,
        counts.n10,
        counts.n11,
    )
    conditioned_observations = int(inputs.exceptions.size - 1)
    conditioned_exceptions = counts.n01 + counts.n11
    canonical_conditional_null_log = confidence_exception_log_likelihood(
        conditioned_observations,
        conditioned_exceptions,
        validated_confidence,
    )
    canonical_unconditional_component = likelihood_ratio(
        canonical_conditional_null_log,
        canonical_independent_log,
    )
    canonical_statistic = finite_result(
        canonical_unconditional_component + canonical_independence_component
    )
    canonical_direct_statistic = likelihood_ratio(
        canonical_conditional_null_log,
        canonical_markov_log,
    )
    identity_tolerance = likelihood_roundoff_tolerance(
        canonical_conditional_null_log,
        canonical_markov_log,
    )
    if abs(canonical_direct_statistic - canonical_statistic) > identity_tolerance:
        raise ResearchValidationError("likelihood_invalid")
    _checked_numeric(
        conditional_null_log,
        expected=canonical_conditional_null_log,
    )
    _checked_numeric(independent_log, expected=canonical_independent_log)
    _checked_numeric(markov_log, expected=canonical_markov_log)
    unconditional_component = _checked_statistic(
        unconditional_value,
        expected=canonical_unconditional_component,
    )
    independence_component = _checked_statistic(
        independence_value,
        expected=canonical_independence_component,
    )
    statistic = _checked_statistic(
        statistic_value,
        expected=canonical_statistic,
    )
    p_value = _checked_backtest_probability(
        p_value_value,
        expected=chi_square_two_survival(canonical_statistic),
    )
    return ConditionalCoverageTestResult(
        statistic=statistic,
        p_value=p_value,
        reject=bool(p_value < validated_significance),
        observations=int(inputs.exceptions.size),
        exceptions=exceptions,
        degrees_of_freedom=2,
        significance=float(validated_significance),
        transitions=counts,
        conditioned_observations=conditioned_observations,
        conditioned_exceptions=conditioned_exceptions,
        unconditional_component_statistic=unconditional_component,
        independence_component_statistic=independence_component,
    )
