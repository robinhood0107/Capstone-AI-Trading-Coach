from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from app.financial_engineering._validation import _raise_stable, _validate_finite_scalar

FloatArray = npt.NDArray[np.float64]

DEFAULT_PATHS = 10_000
MAX_PATHS = 10_000
MIN_PATHS = 20
MAX_STEPS = 1_024
MAX_DRAW_ELEMENTS = 10_000_000
FAN_QUANTILES = (0.05, 0.25, 0.5, 0.75, 0.95)
PREFIX_PATHS = (1_000, 5_000, 10_000)
T19_975 = 2.093024054408263


@dataclass(frozen=True)
class ConfidenceInterval:
    lower: float
    upper: float


@dataclass(frozen=True)
class BatchEstimate:
    value: float
    standard_error: float
    interval95: ConfidenceInterval


@dataclass(frozen=True)
class StochasticMetrics:
    path_count: int
    loss_probability: float
    loss_probability_standard_error: float
    loss_probability_wilson95: ConfidenceInterval
    var_loss95_amount: BatchEstimate
    tail_mean_loss95_amount: BatchEstimate
    var_loss95_return: BatchEstimate
    tail_mean_loss95_return: BatchEstimate
    terminal_fan_quantiles: tuple[float, float, float, float, float]


@dataclass(frozen=True)
class DeterministicStressResult:
    jump_gap: float
    fat_tail_proxy: float
    volatility_cluster_burst: float
    leverage_margin_shortfall: float
    liquidity_spread_impact_crowding: float
    crisis_correlation: float | None
    result_type: str = "DETERMINISTIC_STRESS"


@dataclass(frozen=True)
class GBMMonteCarloReport:
    terminal_prices: FloatArray
    prefix_metrics: tuple[StochasticMetrics, ...]
    quality: str
    warnings: tuple[str, ...]
    seed: int
    step_count: int
    numpy_version: str
    rng: str = "PCG64"
    draw_order: str = "PATH_MAJOR_MAX_DRAW_PREFIX"
    model: str = "EXACT_EXPONENTIAL_GBM"
    measure: str = "P_PREDICTIVE_MEAN"
    iid_innovations: bool = True
    lognormal_terminal: bool = True
    constant_mu_sigma: bool = True
    jumps_included: bool = False


def log_return_mean_to_sde_drift(
    log_return_mean: object,
    *,
    dt: object,
    sigma: object,
) -> float:
    """주기 log-return mean을 GBM SDE price drift로 변환한다."""
    g = _validate_finite_scalar(log_return_mean, code="log_return_mean_invalid")
    validated_dt = _validate_finite_scalar(dt, code="dt_invalid")
    validated_sigma = _validate_finite_scalar(sigma, code="sigma_invalid")
    if validated_dt <= 0 or validated_sigma < 0:
        _raise_stable("gbm_domain_invalid")
    result = g / validated_dt + 0.5 * validated_sigma * validated_sigma
    if not math.isfinite(result):
        _raise_stable("result_non_finite")
    return result


def _validate_inputs(
    *,
    s0: object,
    mu_sde: object,
    sigma: object,
    horizon: object,
    dt: object,
    n_paths: object,
    seed: object,
) -> tuple[float, float, float, float, int, int, int]:
    validated_s0 = _validate_finite_scalar(s0, code="s0_invalid")
    validated_mu = _validate_finite_scalar(mu_sde, code="mu_sde_invalid")
    validated_sigma = _validate_finite_scalar(sigma, code="sigma_invalid")
    validated_horizon = _validate_finite_scalar(horizon, code="horizon_invalid")
    validated_dt = _validate_finite_scalar(dt, code="dt_invalid")
    if validated_s0 <= 0 or validated_sigma < 0 or validated_horizon <= 0 or validated_dt <= 0:
        _raise_stable("gbm_domain_invalid")
    if type(n_paths) is not int or not MIN_PATHS <= n_paths <= MAX_PATHS:
        _raise_stable("n_paths_invalid")
    if type(seed) is not int or not 0 <= seed <= 4_294_967_295:
        _raise_stable("seed_invalid")
    raw_steps = validated_horizon / validated_dt
    rounded_steps = round(raw_steps)
    if rounded_steps <= 0 or abs(raw_steps - rounded_steps) > 1e-10 * max(1.0, abs(raw_steps)):
        _raise_stable("horizon_dt_not_integer")
    if rounded_steps > MAX_STEPS or rounded_steps * n_paths > MAX_DRAW_ELEMENTS:
        _raise_stable("gbm_resource_cap_exceeded")
    return (
        validated_s0,
        validated_mu,
        validated_sigma,
        validated_dt,
        rounded_steps,
        n_paths,
        seed,
    )


def generate_terminal_prices(
    *,
    s0: object,
    mu_sde: object,
    sigma: object,
    horizon: object,
    dt: object,
    n_paths: object = DEFAULT_PATHS,
    seed: object = 0,
) -> FloatArray:
    """PCG64 path-major exact exponential GBM terminal price를 bounded 배열로 반환한다."""
    validated_s0, validated_mu, validated_sigma, validated_dt, steps, paths, validated_seed = (
        _validate_inputs(
            s0=s0,
            mu_sde=mu_sde,
            sigma=sigma,
            horizon=horizon,
            dt=dt,
            n_paths=n_paths,
            seed=seed,
        )
    )
    rng = np.random.Generator(np.random.PCG64(validated_seed))
    draws = rng.standard_normal((paths, steps), dtype=np.float64)
    exponent = (
        (validated_mu - 0.5 * validated_sigma * validated_sigma) * validated_dt * steps
        + validated_sigma * math.sqrt(validated_dt) * np.sum(draws, axis=1, dtype=np.float64)
    )
    with np.errstate(over="ignore", invalid="ignore"):
        terminal = validated_s0 * np.exp(exponent)
    if not bool(np.all(np.isfinite(terminal))):
        _raise_stable("result_non_finite")
    return terminal


def _wilson_interval(successes: int, total: int) -> ConfidenceInterval:
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1.0 + z * z / total
    center = (proportion + z * z / (2.0 * total)) / denominator
    radius = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return ConfidenceInterval(center - radius, center + radius)


def _batch_estimate(
    values: FloatArray,
    estimator: Callable[[FloatArray], float],
) -> BatchEstimate:
    batches = np.array_split(values, 20)
    estimates = np.asarray([estimator(batch) for batch in batches], dtype=np.float64)
    value = float(estimator(values))
    standard_error = float(np.std(estimates, ddof=1, dtype=np.float64) / math.sqrt(20.0))
    half_width = T19_975 * standard_error
    return BatchEstimate(value, standard_error, ConfidenceInterval(value - half_width, value + half_width))


def _tail_mean(values: FloatArray) -> float:
    threshold = float(np.quantile(values, 0.95, method="linear"))
    return float(np.mean(values[values >= threshold], dtype=np.float64))


def calculate_stochastic_metrics(terminal_prices: FloatArray, *, s0: float) -> StochasticMetrics:
    """loss amount와 return loss를 분리해 v1 quantile/tail conventions로 계산한다."""
    loss_amount = s0 - terminal_prices
    loss_return = loss_amount / s0
    losses = int(np.count_nonzero(terminal_prices < s0))
    probability = losses / terminal_prices.size
    probability_se = math.sqrt(probability * (1.0 - probability) / terminal_prices.size)
    def quantile(values: FloatArray) -> float:
        return float(np.quantile(values, 0.95, method="linear"))

    fan = tuple(float(value) for value in np.quantile(terminal_prices, FAN_QUANTILES, method="linear"))
    return StochasticMetrics(
        path_count=int(terminal_prices.size),
        loss_probability=probability,
        loss_probability_standard_error=probability_se,
        loss_probability_wilson95=_wilson_interval(losses, int(terminal_prices.size)),
        var_loss95_amount=_batch_estimate(loss_amount, quantile),
        tail_mean_loss95_amount=_batch_estimate(loss_amount, _tail_mean),
        var_loss95_return=_batch_estimate(loss_return, quantile),
        tail_mean_loss95_return=_batch_estimate(loss_return, _tail_mean),
        terminal_fan_quantiles=fan,  # type: ignore[arg-type]
    )


def _relative_or_absolute_failed(estimate: BatchEstimate) -> bool:
    if abs(estimate.value) < 0.001:
        return estimate.standard_error > 0.001
    return estimate.standard_error / abs(estimate.value) > 0.10


def _adjacent_unstable(previous: StochasticMetrics, current: StochasticMetrics) -> bool:
    pairs = (
        (previous.loss_probability, current.loss_probability),
        (previous.var_loss95_return.value, current.var_loss95_return.value),
        (previous.tail_mean_loss95_return.value, current.tail_mean_loss95_return.value),
    )
    return any(abs(right - left) > max(0.001, 0.05 * abs(left)) for left, right in pairs)


def run_gbm_monte_carlo(
    *,
    s0: object,
    mu_sde: object,
    sigma: object,
    horizon: object,
    dt: object,
    n_paths: object = DEFAULT_PATHS,
    seed: object = 0,
) -> GBMMonteCarloReport:
    """한 번 생성한 max draw prefix들만 평가하며 gate 실패로 path 수를 늘리지 않는다."""
    validated_s0, validated_mu, validated_sigma, validated_dt, steps, paths, validated_seed = (
        _validate_inputs(
            s0=s0,
            mu_sde=mu_sde,
            sigma=sigma,
            horizon=horizon,
            dt=dt,
            n_paths=n_paths,
            seed=seed,
        )
    )
    terminal = generate_terminal_prices(
        s0=validated_s0,
        mu_sde=validated_mu,
        sigma=validated_sigma,
        horizon=steps * validated_dt,
        dt=validated_dt,
        n_paths=paths,
        seed=validated_seed,
    )
    counts = [count for count in PREFIX_PATHS if count <= terminal.size]
    if not counts or counts[-1] != terminal.size:
        counts.append(int(terminal.size))
    metrics = tuple(calculate_stochastic_metrics(terminal[:count], s0=validated_s0) for count in counts)
    warnings: list[str] = []
    final = metrics[-1]
    wilson_half_width = (final.loss_probability_wilson95.upper - final.loss_probability_wilson95.lower) / 2.0
    if wilson_half_width > 0.02:
        warnings.append("LOSS_PROBABILITY_HALF_WIDTH_EXCEEDED")
    for name, estimate in (
        ("VAR_RETURN", final.var_loss95_return),
        ("TAIL_MEAN_RETURN", final.tail_mean_loss95_return),
    ):
        if _relative_or_absolute_failed(estimate):
            warnings.append(f"{name}_STANDARD_ERROR_EXCEEDED")
    if any(_adjacent_unstable(left, right) for left, right in zip(metrics, metrics[1:])):
        warnings.append("ADJACENT_PREFIX_DELTA_EXCEEDED")
    quality = "PASS" if not warnings else ("UNCERTAIN" if terminal.size < 1_000 else "WARN")
    return GBMMonteCarloReport(
        terminal_prices=terminal,
        prefix_metrics=metrics,
        quality=quality,
        warnings=tuple(warnings),
        seed=validated_seed,
        step_count=steps,
        numpy_version=np.__version__,
    )


def deterministic_stress(
    *,
    jump_gap: object,
    fat_tail_proxy: object,
    volatility_cluster_burst: object,
    leverage_margin_shortfall: object,
    liquidity_spread_impact_crowding: object,
    crisis_correlation: object | None = None,
    portfolio_supplied: bool = False,
) -> DeterministicStressResult:
    """선택 shock를 확률분포와 분리하며 portfolio 없는 correlation stress를 거부한다."""
    if crisis_correlation is not None and not portfolio_supplied:
        _raise_stable("crisis_correlation_requires_portfolio")
    values = [
        _validate_finite_scalar(value, code="stress_value_invalid")
        for value in (
            jump_gap,
            fat_tail_proxy,
            volatility_cluster_burst,
            leverage_margin_shortfall,
            liquidity_spread_impact_crowding,
        )
    ]
    correlation = (
        None
        if crisis_correlation is None
        else _validate_finite_scalar(crisis_correlation, code="stress_value_invalid")
    )
    return DeterministicStressResult(
        jump_gap=values[0],
        fat_tail_proxy=values[1],
        volatility_cluster_burst=values[2],
        leverage_margin_shortfall=values[3],
        liquidity_spread_impact_crowding=values[4],
        crisis_correlation=correlation,
    )
