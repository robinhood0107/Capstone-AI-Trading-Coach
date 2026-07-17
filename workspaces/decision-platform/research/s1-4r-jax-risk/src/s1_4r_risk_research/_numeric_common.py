"""Backend-independent scalar stability helpers."""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

from .errors import ResearchValidationError

_NORMAL = NormalDist()
EULER_MASCHERONI = 0.5772156649015329
_FLOAT64_EPS = np.finfo(np.float64).eps


def normal_cdf(value: float) -> float:
    """표준정규 CDF를 Python float로 반환한다."""

    return float(_NORMAL.cdf(value))


def normal_inverse_cdf(probability: float) -> float:
    """Open-unit probability의 표준정규 inverse CDF를 반환한다."""

    return float(_NORMAL.inv_cdf(probability))


def xlog_probability(count: int, probability: float) -> float:
    """Probability를 epsilon으로 바꾸지 않고 0*log(0)=0 극한을 적용한다."""

    if count == 0:
        return 0.0
    if not 0.0 < probability <= 1.0:
        raise ResearchValidationError("likelihood_invalid")
    return float(count) * math.log(probability)


def xlog_complement(count: int, probability: float) -> float:
    """Complement likelihood term에 안정적인 log1p를 사용한다."""

    if count == 0:
        return 0.0
    if not 0.0 <= probability < 1.0:
        raise ResearchValidationError("likelihood_invalid")
    return float(count) * math.log1p(-probability)


def bernoulli_log_likelihood(
    observations: int,
    successes: int,
    probability: float,
) -> float:
    """Count-aware Bernoulli log likelihood를 boundary 이동 없이 계산한다."""

    return xlog_complement(observations - successes, probability) + xlog_probability(
        successes,
        probability,
    )


def confidence_exception_log_likelihood(
    observations: int,
    exceptions: int,
    confidence: float,
) -> float:
    """`p=1-confidence`를 만들지 않고 exception null likelihood를 계산한다."""

    non_exceptions = observations - exceptions
    return (
        float(non_exceptions) * math.log(confidence)
        if non_exceptions
        else 0.0
    ) + (
        float(exceptions) * math.log1p(-confidence)
        if exceptions
        else 0.0
    )


def stable_weighted_mean(
    values: np.ndarray,
    normalized_weights: np.ndarray,
) -> float:
    """Finite values의 non-negative normalized weighted mean을 overflow 없이 계산한다."""

    scale = float(np.max(np.abs(values)))
    if scale == 0.0:
        return 0.0
    normalized = math.fsum(
        float(weight) * (float(value) / scale)
        for value, weight in zip(values, normalized_weights, strict=True)
    )
    tolerance = 64.0 * _FLOAT64_EPS
    if normalized < -1.0 - tolerance or normalized > 1.0 + tolerance:
        raise ResearchValidationError("research_result_non_finite")
    bounded = min(1.0, max(-1.0, normalized))
    return finite_result(bounded * scale)


def likelihood_roundoff_tolerance(null_log: float, alternative_log: float) -> float:
    """LR의 작은 음수만 허용하는 float64 scale-aware tolerance다."""

    return 128.0 * _FLOAT64_EPS * max(1.0, abs(null_log), abs(alternative_log))


def likelihood_ratio(null_log: float, alternative_log: float) -> float:
    """두 finite log-likelihood에서 non-negative LR statistic을 만든다."""

    if not math.isfinite(null_log) or not math.isfinite(alternative_log):
        raise ResearchValidationError("research_result_non_finite")
    statistic = 2.0 * (alternative_log - null_log)
    tolerance = likelihood_roundoff_tolerance(null_log, alternative_log)
    if statistic < -tolerance:
        raise ResearchValidationError("likelihood_invalid")
    if statistic < 0.0:
        statistic = 0.0
    if not math.isfinite(statistic):
        raise ResearchValidationError("research_result_non_finite")
    return float(statistic)


def kupiec_likelihood_components(
    observations: int,
    exceptions: int,
    confidence: float,
) -> tuple[float, float, float]:
    """Full-sample Kupiec statistic과 두 canonical host log likelihood를 반환한다."""

    maximum_likelihood_probability = exceptions / observations
    null_log = confidence_exception_log_likelihood(
        observations,
        exceptions,
        confidence,
    )
    alternative_log = bernoulli_log_likelihood(
        observations,
        exceptions,
        maximum_likelihood_probability,
    )
    return likelihood_ratio(null_log, alternative_log), null_log, alternative_log


def independence_likelihood_components(
    n00: int,
    n01: int,
    n10: int,
    n11: int,
) -> tuple[float, float, float]:
    """Transition counts의 iid/Markov canonical host likelihood를 반환한다."""

    row_zero = n00 + n01
    row_one = n10 + n11
    transitions = row_zero + row_one
    pi_zero_one = n01 / row_zero
    pi_one_one = n11 / row_one
    pi = (n01 + n11) / transitions
    independent_log = bernoulli_log_likelihood(
        transitions,
        n01 + n11,
        pi,
    )
    markov_log = (
        xlog_complement(n00, pi_zero_one)
        + xlog_probability(n01, pi_zero_one)
        + xlog_complement(n10, pi_one_one)
        + xlog_probability(n11, pi_one_one)
    )
    return likelihood_ratio(independent_log, markov_log), independent_log, markov_log


def finite_probability(value: float) -> float:
    """Roundoff 범위에서만 probability를 unit interval로 정규화한다."""

    tolerance = 64.0 * _FLOAT64_EPS
    if not math.isfinite(value):
        raise ResearchValidationError("research_result_non_finite")
    if value < -tolerance or value > 1.0 + tolerance:
        raise ResearchValidationError("research_result_non_finite")
    return float(min(1.0, max(0.0, value)))


def finite_result(value: float) -> float:
    """공개 numeric 성공 결과의 NaN/Inf 유출을 차단한다."""

    if not math.isfinite(value):
        raise ResearchValidationError("research_result_non_finite")
    return float(value)


def chi_square_one_survival(statistic: float) -> float:
    """Chi-square df=1 survival을 stdlib erfc로 계산한다."""

    return finite_probability(math.erfc(math.sqrt(statistic / 2.0)))


def chi_square_two_survival(statistic: float) -> float:
    """Chi-square df=2 survival을 exact exponential form으로 계산한다."""

    return finite_probability(math.exp(-statistic / 2.0))
