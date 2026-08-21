from __future__ import annotations

import math

import numpy as np
import pytest

from app.financial_engineering.mean_reversion import (
    MAX_CLOSE_ROWS,
    diagnose_mean_reversion,
    rolling_mean_reversion,
)


def _exact_ou_closes(*, phi: float, long_run_mean: float = 4.5, rows: int = 600, seed: int = 11) -> np.ndarray:
    rng = np.random.default_rng(seed)
    levels = np.empty(rows)
    levels[0] = long_run_mean
    intercept = long_run_mean * (1.0 - phi)
    for index in range(1, rows):
        levels[index] = intercept + phi * levels[index - 1] + rng.normal(0.0, 0.015)
    return np.exp(levels)


def test_fixed_seed_exact_ou_half_life_is_recovered_approximately() -> None:
    theta = 0.15
    report = diagnose_mean_reversion(_exact_ou_closes(phi=math.exp(-theta), rows=60, seed=20260821))
    assert report.availability == "AVAILABLE"
    assert report.classification == "MEAN_REVERTING"
    assert report.half_life_sessions == pytest.approx(math.log(2) / theta, rel=0.35)
    assert report.theta == pytest.approx(-math.log(report.phi))  # type: ignore[arg-type]
    assert report.long_run_mean == pytest.approx(report.intercept / (1 - report.phi))  # type: ignore[operator]


@pytest.mark.parametrize("phi", [-0.2, 0.0, 1.0, 1.1])
def test_phi_boundaries_are_not_mislabeled_as_exact_ou(phi: float) -> None:
    levels = [0.01]
    intercept = 0.0
    for _ in range(59):
        levels.append(intercept + phi * levels[-1])
    report = diagnose_mean_reversion(np.exp(levels))
    if report.availability == "AVAILABLE":
        assert report.classification == "NOT_MEAN_REVERTING"
        assert report.theta is None
        assert report.half_life_sessions is None


def test_constant_short_nonpositive_and_nonfinite_data_fail_closed() -> None:
    assert diagnose_mean_reversion(np.full(60, 100.0)).availability == "ABSTAIN"
    with pytest.raises(ValueError, match="input_too_short"):
        diagnose_mean_reversion(np.ones(59))
    with pytest.raises(ValueError, match="prices_non_positive"):
        diagnose_mean_reversion(np.r_[np.ones(59), 0.0])
    with pytest.raises(ValueError, match="input_non_finite"):
        diagnose_mean_reversion(np.r_[np.ones(59), np.nan])
    with pytest.raises(ValueError, match="mean_reversion_input_too_long"):
        diagnose_mean_reversion(np.ones(MAX_CLOSE_ROWS + 1))


def test_sixty_window_is_causal_and_includes_current_observation() -> None:
    closes = _exact_ou_closes(phi=0.85, rows=100)
    baseline = rolling_mean_reversion(closes[:80])
    mutated = closes.copy()
    mutated[80:] *= 4.0
    assert rolling_mean_reversion(mutated)[: len(baseline)] == baseline
    changed_current = closes[:60].copy()
    changed_current[-1] *= 1.2
    assert diagnose_mean_reversion(changed_current).z_score != diagnose_mean_reversion(closes[:60]).z_score


def test_adf_output_is_reference_metadata_not_a_gate() -> None:
    report = diagnose_mean_reversion(_exact_ou_closes(phi=0.8, rows=60))
    assert report.adf is not None
    assert report.adf.regression == "c"
    assert report.adf.autolag == "AIC"
    assert math.isfinite(report.adf.statistic)
    assert 0 <= report.adf.p_value <= 1
    assert {name for name, _ in report.adf.critical_values} == {"1%", "5%", "10%"}
    assert 1 <= report.adf.nobs <= 60
    assert report.adf.authority == "REFERENCE_ONLY"
    assert report.decision_authority == "WARN_CANDIDATE_ONLY"


def test_structural_break_adf_does_not_become_a_definitive_decision() -> None:
    rng = np.random.default_rng(42)
    levels = np.r_[rng.normal(4.5, 0.01, 30), rng.normal(5.0, 0.01, 30)]
    report = diagnose_mean_reversion(np.exp(levels))
    assert report.availability == "AVAILABLE"
    assert report.adf is not None
    assert report.adf.authority == "REFERENCE_ONLY"
    assert report.decision_authority == "WARN_CANDIDATE_ONLY"
    assert report.warning_candidate in {None, "ABOVE_TWO_SIGMA", "BELOW_MINUS_TWO_SIGMA"}
