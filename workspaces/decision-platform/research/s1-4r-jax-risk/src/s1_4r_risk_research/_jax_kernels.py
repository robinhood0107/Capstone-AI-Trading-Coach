"""Pure fixed-shape JAX kernels for the isolated S1.4R research API."""

from __future__ import annotations

from typing import cast

import jax
import jax.numpy as jnp
from jax.scipy import special

_FLOAT64 = jnp.float64
_ZERO = 0.0
_ONE = 1.0
_SQRT_TWO = 1.4142135623730951
_EULER_MASCHERONI = 0.5772156649015329
_HALF_LOG_TWO_PI = 0.9189385332046727
_ACKLAM_C = (
    -7.784894002430293e-03,
    -3.223964580411365e-01,
    -2.400758277161838,
    -2.549732539343734,
    4.374664141464968,
    2.938163982698783,
)
_ACKLAM_D = (
    7.784695709041462e-03,
    3.224671290700398e-01,
    2.445134137142996,
    3.754408661907416,
)


def _compensated_sum(values: jax.Array) -> jax.Array:
    """Cancellation이 큰 Lo moment에서도 float64 누적 오차를 제한한다."""

    zero = jnp.asarray(_ZERO, dtype=_FLOAT64)

    def add_value(
        index: int,
        state: tuple[jax.Array, jax.Array],
    ) -> tuple[jax.Array, jax.Array]:
        total, compensation = state
        corrected = values[index] - compensation
        updated = total + corrected
        next_compensation = (updated - total) - corrected
        return updated, next_compensation

    total, _ = jax.lax.fori_loop(
        0,
        values.shape[0],
        add_value,
        (zero, zero),
    )
    return cast(jax.Array, total)


def historical_expected_shortfall(
    losses: jax.Array,
    confidence: jax.Array,
) -> jax.Array:
    """정렬된 전체 길이에 fractional tail weight를 곱해 shape를 고정한다."""

    ordered = jnp.sort(losses)[::-1]
    tail_mass = losses.shape[0] * (_ONE - confidence)
    indices = jnp.arange(losses.shape[0], dtype=_FLOAT64)
    weights = jnp.clip(tail_mass - indices, _ZERO, _ONE)
    normalized_weights = weights / tail_mass
    scale = jnp.max(jnp.abs(ordered))
    scale_mantissa, scale_exponent = jnp.frexp(scale)
    safe_mantissa = jnp.where(scale == _ZERO, _ONE, scale_mantissa)
    normalized_ordered = (
        jnp.ldexp(ordered, -scale_exponent) / safe_mantissa
    )
    normalized_mean = jnp.sum(
        normalized_weights * normalized_ordered,
        dtype=_FLOAT64,
    )
    bounded_mean = jnp.clip(normalized_mean, -_ONE, _ONE)
    return jnp.where(scale == _ZERO, _ZERO, bounded_mean * scale)


def realized_variance(intraday_log_returns: jax.Array) -> jax.Array:
    """한 세션 intraday log return의 float64 제곱합을 계산한다."""

    return jnp.sum(
        intraday_log_returns * intraday_log_returns,
        dtype=_FLOAT64,
    )


def realized_volatility_intraday(intraday_log_returns: jax.Array) -> jax.Array:
    """Realized variance의 non-negative square root를 계산한다."""

    return jnp.sqrt(realized_variance(intraday_log_returns))


def lo_adjusted_sharpe_ratio(
    returns: jax.Array,
    *,
    aggregation_periods: int,
    risk_free_rate: jax.Array,
) -> jax.Array:
    """Static q의 고정 slice를 사용해 Lo autocorrelation adjustment를 계산한다."""

    excess = returns - risk_free_rate
    observations = returns.shape[0]
    mean = _compensated_sum(excess) / observations
    centered = excess - mean
    gamma_zero = _compensated_sum(centered * centered) / observations
    weighted_autocorrelation = jnp.asarray(_ZERO, dtype=_FLOAT64)
    for lag in range(1, aggregation_periods):
        gamma_lag = (
            _compensated_sum(centered[lag:] * centered[:-lag])
            / observations
        )
        weight = _ONE - lag / aggregation_periods
        weighted_autocorrelation = (
            weighted_autocorrelation + weight * gamma_lag / gamma_zero
        )
    denominator = _ONE + 2.0 * weighted_autocorrelation
    base_sharpe = mean / jnp.sqrt(gamma_zero)
    return base_sharpe * jnp.sqrt(aggregation_periods / denominator)


def probabilistic_sharpe_ratio(
    observed_sharpe: jax.Array,
    benchmark_sharpe: jax.Array,
    sample_size: jax.Array,
    skewness: jax.Array,
    kurtosis: jax.Array,
) -> jax.Array:
    """Validated original-frequency inputs의 probabilistic Sharpe를 계산한다."""

    radicand = (
        _ONE
        - skewness * observed_sharpe
        + ((kurtosis - _ONE) / 4.0) * observed_sharpe * observed_sharpe
    )
    z_score = (
        (observed_sharpe - benchmark_sharpe) * jnp.sqrt(sample_size - _ONE)
    ) / jnp.sqrt(radicand)
    return 0.5 * (_ONE + special.erf(z_score / _SQRT_TWO))


def _lower_normal_log_cdf_asymptotic(value: jax.Array) -> jax.Array:
    """극단적인 lower tail에서 subnormal probability 없이 log CDF를 계산한다."""

    # jnp.where가 양쪽 branch를 평가해도 미선택 branch에서 0 나눗셈이 생기지 않게 한다.
    magnitude = jnp.maximum(-value, 12.0)
    inverse_square = _ONE / (magnitude * magnitude)
    # Mills ratio의 alternating asymptotic series를 x^-16까지 사용한다.
    correction = _ONE + inverse_square * (
        -_ONE
        + inverse_square
        * (
            3.0
            + inverse_square
            * (
                -15.0
                + inverse_square
                * (
                    105.0
                    + inverse_square
                    * (
                        -945.0
                        + inverse_square
                        * (
                            10_395.0
                            + inverse_square
                            * (-135_135.0 + inverse_square * 2_027_025.0)
                        )
                    )
                )
            )
        )
    )
    return (
        -0.5 * magnitude * magnitude
        - jnp.log(magnitude)
        - _HALF_LOG_TWO_PI
        + jnp.log(correction)
    )


def _normal_inverse_cdf_from_log_probability(
    log_probability: jax.Array,
) -> jax.Array:
    """Validated lower-tail log probability를 float64 inverse CDF로 바꾼다."""

    # XLA가 subnormal 확률을 0으로 flush해도 Acklam 근사로 finite seed를 만든다.
    q = jnp.sqrt(-2.0 * log_probability)
    numerator = (
        (
            (
                (
                    (_ACKLAM_C[0] * q + _ACKLAM_C[1]) * q
                    + _ACKLAM_C[2]
                )
                * q
                + _ACKLAM_C[3]
            )
            * q
            + _ACKLAM_C[4]
        )
        * q
        + _ACKLAM_C[5]
    )
    denominator = (
        (
            (
                (_ACKLAM_D[0] * q + _ACKLAM_D[1]) * q
                + _ACKLAM_D[2]
            )
            * q
            + _ACKLAM_D[3]
        )
        * q
        + _ONE
    )
    quantile = numerator / denominator
    for _ in range(2):
        log_cdf = jnp.where(
            quantile < -12.0,
            _lower_normal_log_cdf_asymptotic(quantile),
            special.log_ndtr(quantile),
        )
        derivative = jnp.exp(
            -0.5 * quantile * quantile - _HALF_LOG_TWO_PI - log_cdf
        )
        quantile = quantile - (log_cdf - log_probability) / derivative
    return quantile


def deflated_sharpe_ratio(
    observed_sharpe: jax.Array,
    sample_size: jax.Array,
    skewness: jax.Array,
    kurtosis: jax.Array,
    trial_count: jax.Array,
    sharpe_estimate_variance: jax.Array,
) -> jax.Array:
    """Effective trial count와 sample variance로 DSR benchmark를 계산한다."""

    log_reciprocal_trials = -jnp.log(trial_count)
    first_quantile = -_normal_inverse_cdf_from_log_probability(
        log_reciprocal_trials
    )
    second_quantile = -_normal_inverse_cdf_from_log_probability(
        log_reciprocal_trials - _ONE
    )
    benchmark_sharpe = jnp.sqrt(sharpe_estimate_variance) * (
        (_ONE - _EULER_MASCHERONI) * first_quantile
        + _EULER_MASCHERONI * second_quantile
    )
    return probabilistic_sharpe_ratio(
        observed_sharpe,
        benchmark_sharpe,
        sample_size,
        skewness,
        kurtosis,
    )


def _xlog_probability(count: jax.Array, probability: jax.Array) -> jax.Array:
    """Count가 0일 때만 log boundary를 읽지 않는 수학적 극한을 적용한다."""

    safe_probability = jnp.where(count == _ZERO, _ONE, probability)
    return jnp.where(
        count == _ZERO,
        jnp.asarray(_ZERO, dtype=_FLOAT64),
        count * jnp.log(safe_probability),
    )


def _xlog_complement(count: jax.Array, probability: jax.Array) -> jax.Array:
    """Complement likelihood를 fixed-shape three-argument where로 계산한다."""

    safe_probability = jnp.where(count == _ZERO, _ZERO, probability)
    return jnp.where(
        count == _ZERO,
        jnp.asarray(_ZERO, dtype=_FLOAT64),
        count * jnp.log1p(-safe_probability),
    )


def _likelihood_ratio(
    null_log_likelihood: jax.Array,
    alternative_log_likelihood: jax.Array,
) -> jax.Array:
    """Raw LR을 반환해 host가 허용 roundoff와 material negative를 구분하게 한다."""

    return 2.0 * (alternative_log_likelihood - null_log_likelihood)


def _confidence_exception_log_likelihood(
    observations: jax.Array,
    exceptions: jax.Array,
    confidence: jax.Array,
) -> jax.Array:
    """Exception probability complement를 만들지 않고 null logL을 계산한다."""

    non_exceptions = observations - exceptions
    return _xlog_probability(non_exceptions, confidence) + jnp.where(
        exceptions == _ZERO,
        jnp.asarray(_ZERO, dtype=_FLOAT64),
        exceptions * jnp.log1p(-confidence),
    )


def _exception_sequence(
    realized_losses: jax.Array,
    forecast_vars: jax.Array,
) -> jax.Array:
    """Strict loss > VaR exception을 float64 indicator로 고정한다."""

    return (realized_losses > forecast_vars).astype(_FLOAT64)


def _transition_terms(
    exceptions: jax.Array,
) -> tuple[
    jax.Array,
    jax.Array,
    jax.Array,
    jax.Array,
    jax.Array,
    jax.Array,
]:
    """Time축 slice와 reduction만으로 transition counts와 두 logL을 만든다."""

    previous = exceptions[:-1]
    current = exceptions[1:]
    n00 = jnp.sum((_ONE - previous) * (_ONE - current), dtype=_FLOAT64)
    n01 = jnp.sum((_ONE - previous) * current, dtype=_FLOAT64)
    n10 = jnp.sum(previous * (_ONE - current), dtype=_FLOAT64)
    n11 = jnp.sum(previous * current, dtype=_FLOAT64)
    row_zero = n00 + n01
    row_one = n10 + n11
    transition_count = row_zero + row_one
    pi_zero_one = n01 / row_zero
    pi_one_one = n11 / row_one
    pi = (n01 + n11) / transition_count
    independent_log = _xlog_complement(n00 + n10, pi) + _xlog_probability(
        n01 + n11,
        pi,
    )
    markov_log = (
        _xlog_complement(n00, pi_zero_one)
        + _xlog_probability(n01, pi_zero_one)
        + _xlog_complement(n10, pi_one_one)
        + _xlog_probability(n11, pi_one_one)
    )
    return n00, n01, n10, n11, independent_log, markov_log


def kupiec_unconditional_coverage_test(
    realized_losses: jax.Array,
    forecast_vars: jax.Array,
    confidence: jax.Array,
) -> tuple[jax.Array, jax.Array, jax.Array, jax.Array, jax.Array]:
    """전체 exception sequence의 Kupiec statistic, p-value, raw logL을 계산한다."""

    exceptions = _exception_sequence(realized_losses, forecast_vars)
    exception_count = jnp.sum(exceptions, dtype=_FLOAT64)
    observations = jnp.asarray(exceptions.shape[0], dtype=_FLOAT64)
    maximum_likelihood_probability = exception_count / observations
    null_log = _confidence_exception_log_likelihood(
        observations,
        exception_count,
        confidence,
    )
    alternative_log = _xlog_complement(
        observations - exception_count,
        maximum_likelihood_probability,
    ) + _xlog_probability(exception_count, maximum_likelihood_probability)
    statistic = _likelihood_ratio(null_log, alternative_log)
    # P-value 계산의 sqrt domain만 보호하며, raw statistic/logL은 host가 별도 검증한다.
    p_value_statistic = jnp.maximum(
        statistic,
        jnp.asarray(_ZERO, dtype=_FLOAT64),
    )
    p_value = special.erfc(jnp.sqrt(p_value_statistic / 2.0))
    return statistic, p_value, exception_count, null_log, alternative_log


def christoffersen_independence_test(
    realized_losses: jax.Array,
    forecast_vars: jax.Array,
) -> tuple[
    jax.Array,
    jax.Array,
    jax.Array,
    jax.Array,
    jax.Array,
    jax.Array,
    jax.Array,
    jax.Array,
    jax.Array,
]:
    """Markov 대 iid transition likelihood와 fixed-size counts를 계산한다."""

    exceptions = _exception_sequence(realized_losses, forecast_vars)
    exception_count = jnp.sum(exceptions, dtype=_FLOAT64)
    n00, n01, n10, n11, independent_log, markov_log = _transition_terms(
        exceptions
    )
    statistic = _likelihood_ratio(independent_log, markov_log)
    p_value_statistic = jnp.maximum(
        statistic,
        jnp.asarray(_ZERO, dtype=_FLOAT64),
    )
    p_value = special.erfc(jnp.sqrt(p_value_statistic / 2.0))
    return (
        statistic,
        p_value,
        exception_count,
        n00,
        n01,
        n10,
        n11,
        independent_log,
        markov_log,
    )


def christoffersen_conditional_coverage_test(
    realized_losses: jax.Array,
    forecast_vars: jax.Array,
    confidence: jax.Array,
) -> tuple[
    jax.Array,
    jax.Array,
    jax.Array,
    jax.Array,
    jax.Array,
    jax.Array,
    jax.Array,
    jax.Array,
    jax.Array,
    jax.Array,
    jax.Array,
    jax.Array,
]:
    """First-observation-conditioned UC와 independence LR component를 계산한다."""

    exceptions = _exception_sequence(realized_losses, forecast_vars)
    exception_count = jnp.sum(exceptions, dtype=_FLOAT64)
    n00, n01, n10, n11, independent_log, markov_log = _transition_terms(
        exceptions
    )
    conditioned_observations = jnp.asarray(
        exceptions.shape[0] - 1,
        dtype=_FLOAT64,
    )
    conditioned_exceptions = n01 + n11
    conditional_null_log = _confidence_exception_log_likelihood(
        conditioned_observations,
        conditioned_exceptions,
        confidence,
    )
    unconditional_component = _likelihood_ratio(
        conditional_null_log,
        independent_log,
    )
    independence_component = _likelihood_ratio(independent_log, markov_log)
    statistic = unconditional_component + independence_component
    p_value_statistic = jnp.maximum(
        statistic,
        jnp.asarray(_ZERO, dtype=_FLOAT64),
    )
    p_value = jnp.exp(-p_value_statistic / 2.0)
    return (
        statistic,
        p_value,
        exception_count,
        n00,
        n01,
        n10,
        n11,
        conditional_null_log,
        independent_log,
        markov_log,
        unconditional_component,
        independence_component,
    )
