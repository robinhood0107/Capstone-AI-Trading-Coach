from __future__ import annotations

import math
import time
import tracemalloc

import numpy as np
import pytest
from scipy.special import ndtr

from app.financial_engineering.gbm_monte_carlo import (
    MAX_PATHS,
    DeterministicStressResult,
    StochasticMetrics,
    calculate_stochastic_metrics,
    deterministic_stress,
    generate_terminal_prices,
    log_return_mean_to_sde_drift,
    run_gbm_monte_carlo,
)


def test_seed_and_max_draw_prefix_are_exactly_reproducible() -> None:
    kwargs = {"s0": 100.0, "mu_sde": 0.04, "sigma": 0.2, "horizon": 1.0, "dt": 1 / 252, "seed": 19}
    maximum = generate_terminal_prices(**kwargs, n_paths=10_000)
    assert generate_terminal_prices(**kwargs, n_paths=1_000) == pytest.approx(
        maximum[:1_000], abs=0
    )
    assert generate_terminal_prices(**kwargs, n_paths=5_000) == pytest.approx(
        maximum[:5_000], abs=0
    )
    assert generate_terminal_prices(**kwargs, n_paths=10_000) == pytest.approx(maximum, abs=0)


def test_terminal_mean_and_loss_probability_match_lognormal_oracle() -> None:
    s0, mu, sigma, horizon = 100.0, 0.04, 0.2, 1.0
    terminal = generate_terminal_prices(
        s0=s0, mu_sde=mu, sigma=sigma, horizon=horizon, dt=1 / 252, n_paths=10_000, seed=20260821
    )
    expected_mean = s0 * math.exp(mu * horizon)
    expected_loss_probability = float(
        ndtr(
            (math.log(s0 / s0) - (mu - sigma * sigma / 2) * horizon) / (sigma * math.sqrt(horizon))
        )
    )
    assert float(np.mean(terminal)) == pytest.approx(expected_mean, rel=0.01)
    assert float(np.mean(terminal < s0)) == pytest.approx(expected_loss_probability, abs=0.015)


def test_log_return_mean_conversion_uses_ito_adjustment() -> None:
    assert log_return_mean_to_sde_drift(0.001, dt=1 / 252, sigma=0.2) == pytest.approx(0.272)


def test_quantile_tail_duplicate_threshold_and_units_are_separate() -> None:
    terminal = np.array([80.0] * 10 + [90.0] * 10 + [100.0] * 10 + [110.0] * 10)
    metrics = calculate_stochastic_metrics(terminal, s0=100.0)
    losses = 100.0 - terminal
    threshold = float(np.quantile(losses, 0.95, method="linear"))
    assert metrics.var_loss95_amount.value == threshold
    assert metrics.tail_mean_loss95_amount.value == float(np.mean(losses[losses >= threshold]))
    assert metrics.var_loss95_return.value == pytest.approx(metrics.var_loss95_amount.value / 100.0)
    assert metrics.loss_probability == 0.5


def test_twenty_batch_se_ci_and_warn_gates_are_reported_without_auto_growth() -> None:
    report = run_gbm_monte_carlo(
        s0=100.0, mu_sde=0.0, sigma=0.7, horizon=1.0, dt=1 / 252, n_paths=1_000, seed=7
    )
    final = report.prefix_metrics[-1]
    assert final.path_count == 1_000
    assert (
        final.var_loss95_return.interval95.lower
        <= final.var_loss95_return.value
        <= final.var_loss95_return.interval95.upper
    )
    assert len(report.terminal_prices) == 1_000
    assert report.quality in {"PASS", "WARN"}


def test_stochastic_and_deterministic_result_types_cannot_be_confused() -> None:
    metrics = calculate_stochastic_metrics(np.linspace(80.0, 120.0, 100), s0=100.0)
    stress = deterministic_stress(
        jump_gap=-0.1,
        fat_tail_proxy=-0.2,
        volatility_cluster_burst=-0.08,
        leverage_margin_shortfall=0.03,
        liquidity_spread_impact_crowding=-0.04,
    )
    assert isinstance(metrics, StochasticMetrics)
    assert isinstance(stress, DeterministicStressResult)
    assert stress.result_type == "DETERMINISTIC_STRESS"
    with pytest.raises(ValueError, match="crisis_correlation_requires_portfolio"):
        deterministic_stress(
            jump_gap=0,
            fat_tail_proxy=0,
            volatility_cluster_burst=0,
            leverage_margin_shortfall=0,
            liquidity_spread_impact_crowding=0,
            crisis_correlation=0.8,
        )


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"s0": float("nan")}, "s0_invalid"),
        ({"sigma": float("inf")}, "sigma_invalid"),
        ({"n_paths": MAX_PATHS + 1}, "n_paths_invalid"),
        ({"horizon": 1.0, "dt": 0.003}, "horizon_dt_not_integer"),
        ({"horizon": 2.0, "dt": 1 / 1000}, "gbm_resource_cap_exceeded"),
    ],
)
def test_domain_finite_and_resource_caps(kwargs: dict[str, object], code: str) -> None:
    inputs: dict[str, object] = {
        "s0": 100.0,
        "mu_sde": 0.04,
        "sigma": 0.2,
        "horizon": 1.0,
        "dt": 1 / 252,
        "n_paths": 1_000,
        "seed": 1,
    }
    inputs.update(kwargs)
    with pytest.raises(ValueError, match=code):
        generate_terminal_prices(**inputs)


def test_10k_hard_cap_runtime_and_memory_are_bounded() -> None:
    tracemalloc.start()
    started = time.perf_counter()
    report = run_gbm_monte_carlo(
        s0=100.0, mu_sde=0.04, sigma=0.2, horizon=1.0, dt=1 / 252, n_paths=10_000, seed=3
    )
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert report.prefix_metrics[-1].path_count == 10_000
    assert elapsed < 10
    assert peak < 128 * 1024 * 1024
