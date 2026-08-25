from __future__ import annotations

import itertools
import math
import time
import tracemalloc
from dataclasses import dataclass

import numpy as np
import pytest

from app.financial_engineering.hmm_regime import (
    MAX_CLOSE_ROWS,
    MIN_COVAR,
    SEEDS,
    _default_model_factory,
    _forward_posteriors,
    _label_mapping,
    build_causal_features,
    fit_hmm_regime,
)


def _brute_force_posterior(
    observations: np.ndarray,
    start: np.ndarray,
    transition: np.ndarray,
    means: np.ndarray,
    covars: np.ndarray,
) -> np.ndarray:
    rows: list[np.ndarray] = []
    for end in range(1, len(observations) + 1):
        weights = np.zeros(2)
        for states in itertools.product(range(2), repeat=end):
            probability = start[states[0]]
            for index, state in enumerate(states):
                if index:
                    probability *= transition[states[index - 1], state]
                diff = observations[index] - means[state]
                emission = math.exp(-0.5 * float(np.sum(diff * diff / covars[state]))) / math.sqrt(
                    float(np.prod(2 * math.pi * covars[state]))
                )
                probability *= emission
            weights[states[-1]] += probability
        rows.append(weights / weights.sum())
    return np.asarray(rows)


def test_manual_forward_matches_brute_force_tiny_oracle() -> None:
    observations = np.array([[0.1, -0.2], [0.2, 0.3], [-0.1, 0.4]])
    start = np.array([0.55, 0.45])
    transition = np.array([[0.8, 0.2], [0.3, 0.7]])
    means = np.array([[0.0, 0.0], [0.4, 0.5]])
    covars = np.array([[0.7, 1.2], [1.1, 0.8]])
    actual, _ = _forward_posteriors(
        observations,
        start_probability=start,
        transition_probability=transition,
        means=means,
        covariances=covars,
    )
    assert actual == pytest.approx(
        _brute_force_posterior(observations, start, transition, means, covars), abs=1e-12
    )


def test_features_are_causal_sample_volatility_and_suffix_invariant() -> None:
    closes = np.exp(np.linspace(4.0, 4.5, 100) + 0.01 * np.sin(np.arange(100)))
    prefix = build_causal_features(closes[:80])
    mutated = closes.copy()
    mutated[80:] *= np.linspace(1.0, 3.0, 20)
    assert build_causal_features(mutated)[: len(prefix)] == pytest.approx(prefix, abs=0)
    returns = np.diff(np.log(closes))
    assert prefix[0, 1] == pytest.approx(np.std(returns[:20], ddof=1))


@dataclass
class _Monitor:
    converged: bool
    history: tuple[float, ...]


class _FakeModel:
    def __init__(self, seed: int, *, fail: bool = False, likelihood: float | None = None) -> None:
        self.seed = seed
        self.fail = fail
        self.monitor_ = _Monitor(
            not fail, ((likelihood if likelihood is not None else float(seed)),)
        )
        self.startprob_ = np.array([0.5, 0.5])
        self.transmat_ = np.array([[0.95, 0.05], [0.05, 0.95]])
        self.means_ = np.array([[1.5, -1.5], [-1.5, 1.5]])
        self._covars_ = np.full((2, 2), MIN_COVAR)

    def fit(self, observations: np.ndarray) -> _FakeModel:
        if self.fail:
            raise ValueError("fit_failed")
        return self


def _balanced_closes(rows: int = 600) -> np.ndarray:
    rng = np.random.default_rng(20260821)
    returns = np.concatenate(
        [rng.normal(-0.002, 0.008, rows // 2), rng.normal(0.003, 0.025, rows // 2)]
    )
    return 100 * np.exp(np.concatenate([[0.0], np.cumsum(returns)]))


def test_manual_multistart_selects_likelihood_then_smaller_seed() -> None:
    likelihoods = {11: 7.0, 29: 9.0, 47: 9.0 + 5e-9, 71: 8.0, 101: 6.0}
    result = fit_hmm_regime(
        _balanced_closes(),
        train_rows=500,
        model_factory=lambda seed: _FakeModel(seed, likelihood=likelihoods[seed]),
    )
    assert result.availability == "AVAILABLE"
    assert result.artifact is not None
    assert result.artifact.selected_seed == 29
    assert result.artifact.label_by_internal_state == ("RISK_ON", "RISK_OFF")
    assert all(
        math.isfinite(item.normalized_entropy) and 0 <= item.normalized_entropy <= 1
        for item in result.observations
    )
    assert all(sum(item.posterior) == pytest.approx(1.0) for item in result.observations)


def test_every_failed_candidate_abstains_without_artifact() -> None:
    result = fit_hmm_regime(
        _balanced_closes(), train_rows=500, model_factory=lambda seed: _FakeModel(seed, fail=True)
    )
    assert result.availability == "ABSTAIN"
    assert result.artifact is None
    assert len(result.candidate_failures) == len(SEEDS)


def test_suffix_mutation_does_not_change_filtered_prefix() -> None:
    closes = _balanced_closes(650)
    baseline = fit_hmm_regime(closes, train_rows=400, model_factory=lambda seed: _FakeModel(seed))
    mutated = closes.copy()
    mutated[550:] *= np.linspace(1.0, 5.0, len(mutated) - 550)
    changed = fit_hmm_regime(mutated, train_rows=400, model_factory=lambda seed: _FakeModel(seed))
    assert baseline.artifact == changed.artifact
    assert baseline.observations[:530] == changed.observations[:530]


def test_occupancy_guard_rejects_collapsed_state() -> None:
    def factory(seed: int) -> _FakeModel:
        model = _FakeModel(seed)
        model.startprob_ = np.array([1.0 - 1e-12, 1e-12])
        model.transmat_ = np.array([[1.0 - 1e-12, 1e-12], [1e-12, 1.0 - 1e-12]])
        model.means_ = np.array([[0.0, 0.0], [100.0, 100.0]])
        return model

    result = fit_hmm_regime(_balanced_closes(), train_rows=500, model_factory=factory)
    assert result.availability == "ABSTAIN"
    assert all("occupancy_below_minimum" in failure for failure in result.candidate_failures)


def test_default_model_contract_is_exact() -> None:
    model = _default_model_factory(11)
    assert model.n_components == 2  # type: ignore[attr-defined]
    assert model.covariance_type == "diag"  # type: ignore[attr-defined]
    assert model.min_covar == MIN_COVAR  # type: ignore[attr-defined]
    assert model.n_iter == 500  # type: ignore[attr-defined]
    assert model.tol == 1e-4  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            lambda model: setattr(model, "_covars_", np.full((2, 2), MIN_COVAR / 2)),
            "covariance_below_minimum",
        ),
        (
            lambda model: setattr(model, "transmat_", np.array([[0.9, 0.2], [0.1, 0.9]])),
            "transition_invalid",
        ),
        (
            lambda model: setattr(model, "startprob_", np.array([float("nan"), 1.0])),
            "start_probability_invalid",
        ),
    ],
)
def test_covariance_transition_and_probability_guards(mutation: object, expected: str) -> None:
    def factory(seed: int) -> _FakeModel:
        model = _FakeModel(seed)
        mutation(model)  # type: ignore[operator]
        return model

    result = fit_hmm_regime(_balanced_closes(), train_rows=500, model_factory=factory)
    assert result.availability == "ABSTAIN"
    assert all(expected in failure for failure in result.candidate_failures)


def test_state_label_is_permutation_invariant_and_ties_are_stable() -> None:
    means = np.array([[0.2, -0.1], [-0.4, 1.0]])
    labels = _label_mapping(means)
    reversed_labels = _label_mapping(means[::-1])
    assert labels == tuple(reversed(reversed_labels))
    assert _label_mapping(np.zeros((2, 2))) == ("RISK_OFF", "RISK_ON")


def test_real_fit_is_bounded_and_artifact_contains_no_serialized_object() -> None:
    closes = _balanced_closes(800)
    tracemalloc.start()
    started = time.perf_counter()
    result = fit_hmm_regime(closes, train_rows=650)
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert result.availability in {"AVAILABLE", "ABSTAIN"}
    assert elapsed < 10
    assert peak < 256 * 1024 * 1024
    if result.artifact:
        serialized = str(result.artifact.canonical_payload()).lower()
        assert (
            "pickle" not in serialized
            and "joblib" not in serialized
            and "cloudpickle" not in serialized
        )


def test_row_cap_nonfinite_and_active_three_state_export_are_rejected() -> None:
    with pytest.raises(ValueError, match="hmm_input_too_long"):
        build_causal_features(np.ones(MAX_CLOSE_ROWS + 1))
    with pytest.raises(ValueError, match="input_non_finite"):
        build_causal_features([1.0] * 20 + [float("inf")])
    import app.financial_engineering as package

    assert not hasattr(package, "THREE_STATE_HMM")
