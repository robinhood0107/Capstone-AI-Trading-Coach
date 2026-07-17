"""NumPy float64 references for the nine isolated S1.4R statistics."""

from __future__ import annotations

import math

import numpy as np

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


def historical_expected_shortfall(
    losses: object,
    *,
    confidence: float = 0.95,
) -> float:
    """Loss-space 1-D 표본의 exact fractional finite-tail ES를 반환한다."""

    values = validate_sequence(losses)
    validated_confidence = validate_confidence(confidence)
    tail_mass = float(values.size) * (1.0 - validated_confidence)
    ordered = np.sort(values)[::-1]
    indices = np.arange(values.size, dtype=np.float64)
    weights = np.clip(tail_mass - indices, 0.0, 1.0)
    normalized_weights = weights / tail_mass
    return stable_weighted_mean(ordered, normalized_weights)


def realized_variance(intraday_log_returns: object) -> float:
    """한 세션 intraday log return의 제곱합을 반환하며 연율화하지 않는다."""

    values = validate_sequence(intraday_log_returns)
    with np.errstate(over="ignore", invalid="ignore"):
        result = float(np.sum(values * values, dtype=np.float64))
    return finite_result(result)


def realized_volatility_intraday(intraday_log_returns: object) -> float:
    """Realized variance의 non-negative square root를 반환한다."""

    result = math.sqrt(realized_variance(intraday_log_returns))
    return finite_result(result)


def lo_adjusted_sharpe_ratio(
    returns: object,
    *,
    aggregation_periods: int,
    risk_free_rate: float = 0.0,
) -> float:
    """원주기 excess return에 Lo autocorrelation adjustment를 적용한다."""

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
        weighted_autocorrelation += (1.0 - lag / periods) * (gamma_lag / gamma_zero)

    denominator = 1.0 + 2.0 * weighted_autocorrelation
    if not math.isfinite(denominator) or denominator <= 0.0:
        raise ResearchValidationError("moment_invalid")
    base_sharpe = mean / math.sqrt(gamma_zero)
    result = base_sharpe * math.sqrt(float(periods) / denominator)
    return finite_result(result)


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


def probabilistic_sharpe_ratio(
    observed_sharpe: float,
    *,
    benchmark_sharpe: float,
    sample_size: int,
    skewness: float,
    kurtosis: float,
) -> float:
    """Original-frequency SR와 Pearson moments로 asymptotic PSR을 반환한다."""

    observed, benchmark, observations, _, _, radicand = _validated_psr_inputs(
        observed_sharpe,
        benchmark_sharpe=benchmark_sharpe,
        sample_size=sample_size,
        skewness=skewness,
        kurtosis=kurtosis,
    )
    z_score = (
        (observed - benchmark) * math.sqrt(float(observations - 1))
    ) / math.sqrt(radicand)
    return finite_probability(normal_cdf(finite_result(z_score)))


def deflated_sharpe_ratio(
    observed_sharpe: float,
    *,
    sample_size: int,
    skewness: float,
    kurtosis: float,
    trial_count: int,
    sharpe_estimate_variance: float,
    trial_provenance: EffectiveTrialProvenance,
) -> float:
    """검증된 effective-trial provenance를 요구하는 DSR을 반환한다."""

    observed, _, observations, skew, pearson_kurtosis, _ = _validated_psr_inputs(
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
    first_quantile = -normal_inverse_cdf(reciprocal_trials)
    second_quantile = -normal_inverse_cdf(reciprocal_trials / math.e)
    benchmark = math.sqrt(variance) * (
        (1.0 - EULER_MASCHERONI) * first_quantile
        + EULER_MASCHERONI * second_quantile
    )
    benchmark = finite_result(benchmark)
    return probabilistic_sharpe_ratio(
        observed,
        benchmark_sharpe=benchmark,
        sample_size=observations,
        skewness=skew,
        kurtosis=pearson_kurtosis,
    )


def _kupiec_statistic(exceptions: np.ndarray, *, confidence: float) -> float:
    observations = int(exceptions.size)
    exception_count = int(np.sum(exceptions, dtype=np.int64))
    statistic, _, _ = kupiec_likelihood_components(
        observations,
        exception_count,
        confidence,
    )
    return statistic


def _independence_logs(
    counts: TransitionCounts,
) -> tuple[float, float]:
    _, independent_log, markov_log = independence_likelihood_components(
        counts.n00,
        counts.n01,
        counts.n10,
        counts.n11,
    )
    return independent_log, markov_log


def _validated_transition_inputs(
    realized_losses: object,
    forecast_vars: object,
    *,
    significance: object,
) -> tuple[BacktestInputs, float, TransitionCounts, float, float]:
    inputs = validate_backtest_sequences(
        realized_losses,
        forecast_vars,
        minimum_length=2,
    )
    validated_significance = validate_significance(significance)
    counts = transition_counts(inputs.exceptions)
    validate_transition_identifiability(counts)
    independent_log, markov_log = _independence_logs(counts)
    return inputs, validated_significance, counts, independent_log, markov_log


def kupiec_unconditional_coverage_test(
    realized_losses: object,
    forecast_vars: object,
    *,
    confidence: float,
    significance: float = 0.05,
) -> LikelihoodRatioTestResult:
    """전체 exception sequence의 Kupiec unconditional coverage LR을 계산한다."""

    inputs = validate_backtest_sequences(
        realized_losses,
        forecast_vars,
        minimum_length=1,
    )
    validated_confidence = validate_confidence(confidence)
    validated_significance = validate_significance(significance)
    statistic = _kupiec_statistic(
        inputs.exceptions,
        confidence=validated_confidence,
    )
    p_value = chi_square_one_survival(statistic)
    exceptions = int(np.sum(inputs.exceptions, dtype=np.int64))
    return LikelihoodRatioTestResult(
        statistic=float(statistic),
        p_value=float(p_value),
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
) -> IndependenceTestResult:
    """Exception clustering의 two-state Markov 대 iid LR을 계산한다."""

    inputs, validated_significance, counts, independent_log, markov_log = (
        _validated_transition_inputs(
            realized_losses,
            forecast_vars,
            significance=significance,
        )
    )
    statistic = likelihood_ratio(independent_log, markov_log)
    p_value = chi_square_one_survival(statistic)
    exceptions = int(np.sum(inputs.exceptions, dtype=np.int64))
    return IndependenceTestResult(
        statistic=float(statistic),
        p_value=float(p_value),
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
) -> ConditionalCoverageTestResult:
    """First-observation-conditioned UC와 independence의 exact CC 합을 계산한다."""

    inputs = validate_backtest_sequences(
        realized_losses,
        forecast_vars,
        minimum_length=2,
    )
    validated_confidence = validate_confidence(confidence)
    validated_significance = validate_significance(significance)
    counts = transition_counts(inputs.exceptions)
    validate_transition_identifiability(counts)
    independent_log, markov_log = _independence_logs(counts)

    conditioned_observations = int(inputs.exceptions.size - 1)
    conditioned_exceptions = counts.n01 + counts.n11
    conditional_null_log = confidence_exception_log_likelihood(
        conditioned_observations,
        conditioned_exceptions,
        validated_confidence,
    )
    unconditional_component = likelihood_ratio(conditional_null_log, independent_log)
    independence_component = likelihood_ratio(independent_log, markov_log)
    direct_statistic = likelihood_ratio(conditional_null_log, markov_log)
    statistic = unconditional_component + independence_component
    identity_tolerance = likelihood_roundoff_tolerance(conditional_null_log, markov_log)
    if abs(direct_statistic - statistic) > identity_tolerance:
        raise ResearchValidationError("likelihood_invalid")
    statistic = finite_result(statistic)
    p_value = chi_square_two_survival(statistic)
    exceptions = int(np.sum(inputs.exceptions, dtype=np.int64))
    return ConditionalCoverageTestResult(
        statistic=float(statistic),
        p_value=float(p_value),
        reject=bool(p_value < validated_significance),
        observations=int(inputs.exceptions.size),
        exceptions=exceptions,
        degrees_of_freedom=2,
        significance=float(validated_significance),
        transitions=counts,
        conditioned_observations=conditioned_observations,
        conditioned_exceptions=conditioned_exceptions,
        unconditional_component_statistic=float(unconditional_component),
        independence_component_statistic=float(independence_component),
    )
