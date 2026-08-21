from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

import numpy as np
import numpy.typing as npt
from hmmlearn.hmm import GaussianHMM  # type: ignore[import-untyped]

from app.financial_engineering._validation import _raise_stable, _validate_numeric_input

FloatArray = npt.NDArray[np.float64]
ModelFactory = Callable[[int], "GaussianHmmLike"]

FEATURE_WINDOW = 20
MAX_CLOSE_ROWS = 20_000
POSTERIOR_LABEL_THRESHOLD = 0.65
ENTROPY_WARN_THRESHOLD = 0.95
SEEDS = (11, 29, 47, 71, 101)
MIN_COVAR = 1e-3
TIE_TOLERANCE = 1e-8


class MonitorLike(Protocol):
    converged: bool
    history: Sequence[float]


class GaussianHmmLike(Protocol):
    monitor_: MonitorLike
    startprob_: FloatArray
    transmat_: FloatArray
    means_: FloatArray
    _covars_: FloatArray

    def fit(self, observations: FloatArray) -> GaussianHmmLike: ...


@dataclass(frozen=True)
class HMMArtifact:
    selected_seed: int
    scaler_mean: tuple[float, float]
    scaler_std: tuple[float, float]
    start_probability: tuple[float, float]
    transition_probability: tuple[tuple[float, float], tuple[float, float]]
    means: tuple[tuple[float, float], tuple[float, float]]
    diagonal_covariance: tuple[tuple[float, float], tuple[float, float]]
    label_by_internal_state: tuple[str, str]
    train_last_posterior: tuple[float, float]
    artifact_hash: str

    def canonical_payload(self) -> dict[str, object]:
        """pickle 없이 재구성 가능한 수치 파라미터와 label mapping만 반환한다."""
        return {
            "artifactType": "HMM_CANONICAL_NUMERIC_PARAMETERS",
            "covarianceType": "diag",
            "diagonalCovariance": self.diagonal_covariance,
            "labelByInternalState": self.label_by_internal_state,
            "means": self.means,
            "minCovar": MIN_COVAR,
            "nComponents": 2,
            "scalerMean": self.scaler_mean,
            "scalerStd": self.scaler_std,
            "selectedSeed": self.selected_seed,
            "startProbability": self.start_probability,
            "trainLastPosterior": self.train_last_posterior,
            "transitionProbability": self.transition_probability,
        }


@dataclass(frozen=True)
class RegimeObservation:
    feature_index: int
    posterior: tuple[float, float]
    max_posterior: float
    normalized_entropy: float
    state: str | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class HMMRegimeResult:
    availability: str
    artifact: HMMArtifact | None
    observations: tuple[RegimeObservation, ...]
    candidate_failures: tuple[str, ...]


def build_causal_features(closes: object) -> FloatArray:
    """양의 close에서 현재 return과 causal 20-session sample volatility feature를 만든다."""
    values = _validate_numeric_input(closes, min_length=FEATURE_WINDOW + 1)
    if values.size > MAX_CLOSE_ROWS:
        _raise_stable("hmm_input_too_long")
    if bool(np.any(values <= 0.0)):
        _raise_stable("prices_non_positive")
    log_returns = np.diff(np.log(values))
    features = np.empty((log_returns.size - FEATURE_WINDOW + 1, 2), dtype=np.float64)
    for output_index, return_index in enumerate(range(FEATURE_WINDOW - 1, log_returns.size)):
        window = log_returns[return_index - FEATURE_WINDOW + 1 : return_index + 1]
        features[output_index, 0] = log_returns[return_index]
        features[output_index, 1] = np.std(window, ddof=1, dtype=np.float64)
    if not bool(np.all(np.isfinite(features))):
        _raise_stable("hmm_feature_non_finite")
    return features


def _logsumexp(values: FloatArray, axis: int | None = None) -> FloatArray:
    maximum = np.max(values, axis=axis, keepdims=True)
    summed = np.sum(np.exp(values - maximum), axis=axis, keepdims=True)
    result = maximum + np.log(summed)
    if axis is not None:
        result = np.squeeze(result, axis=axis)
    else:
        result = np.squeeze(result)
    return result


def _gaussian_diag_log_emission(
    observations: FloatArray,
    means: FloatArray,
    covariances: FloatArray,
) -> FloatArray:
    differences = observations[:, None, :] - means[None, :, :]
    return -0.5 * (
        np.sum(np.log(2.0 * math.pi * covariances), axis=1)[None, :]
        + np.sum((differences * differences) / covariances[None, :, :], axis=2)
    )


def _forward_posteriors(
    observations: FloatArray,
    *,
    start_probability: FloatArray,
    transition_probability: FloatArray,
    means: FloatArray,
    covariances: FloatArray,
    initial_filtered_posterior: FloatArray | None = None,
) -> tuple[FloatArray, float]:
    """smoothing/Viterbi 없이 log-space causal forward posterior만 계산한다."""
    emissions = _gaussian_diag_log_emission(observations, means, covariances)
    log_transition = np.log(transition_probability)
    posteriors = np.empty((observations.shape[0], 2), dtype=np.float64)
    likelihood = 0.0
    previous_log = np.log(start_probability)
    for index, emission in enumerate(emissions):
        if index == 0 and initial_filtered_posterior is None:
            unnormalized = previous_log + emission
        else:
            prior = (
                initial_filtered_posterior
                if index == 0 and initial_filtered_posterior is not None
                else posteriors[index - 1]
            )
            log_prior = _logsumexp(
                np.log(np.maximum(prior, np.finfo(np.float64).tiny))[:, None]
                + log_transition,
                axis=0,
            )
            unnormalized = log_prior + emission
        normalizer = float(_logsumexp(unnormalized))
        posterior = np.maximum(
            np.exp(unnormalized - normalizer),
            np.finfo(np.float64).tiny,
        )
        posterior /= posterior.sum()
        posteriors[index] = posterior
        likelihood += normalizer
    return posteriors, likelihood


def _validate_probability_vector(values: FloatArray) -> bool:
    return (
        values.shape == (2,)
        and bool(np.all(np.isfinite(values)))
        and bool(np.all(values > 0.0))
        and abs(float(np.sum(values)) - 1.0) <= 1e-8
    )


def _validate_candidate(model: GaussianHmmLike, observations: FloatArray) -> tuple[FloatArray, float]:
    if not model.monitor_.converged or not model.monitor_.history:
        raise ValueError("not_converged")
    likelihood = float(model.monitor_.history[-1])
    if not math.isfinite(likelihood):
        raise ValueError("likelihood_non_finite")
    start = np.asarray(model.startprob_, dtype=np.float64)
    transition = np.asarray(model.transmat_, dtype=np.float64)
    means = np.asarray(model.means_, dtype=np.float64)
    covariances = np.asarray(model._covars_, dtype=np.float64)
    if not _validate_probability_vector(start):
        raise ValueError("start_probability_invalid")
    if transition.shape != (2, 2) or not bool(np.all(np.isfinite(transition))):
        raise ValueError("transition_invalid")
    if not bool(np.all(transition > 0.0)) or not bool(np.allclose(transition.sum(axis=1), 1.0, atol=1e-8)):
        raise ValueError("transition_invalid")
    if means.shape != (2, 2) or not bool(np.all(np.isfinite(means))):
        raise ValueError("means_invalid")
    if covariances.shape != (2, 2) or not bool(np.all(np.isfinite(covariances))):
        raise ValueError("covariance_invalid")
    if bool(np.any(covariances < MIN_COVAR)):
        raise ValueError("covariance_below_minimum")
    posteriors, _ = _forward_posteriors(
        observations,
        start_probability=start,
        transition_probability=transition,
        means=means,
        covariances=covariances,
    )
    minimum_occupancy = max(20.0, 0.05 * observations.shape[0])
    if bool(np.any(posteriors.sum(axis=0) < minimum_occupancy)):
        raise ValueError("occupancy_below_minimum")
    return posteriors, likelihood


def _default_model_factory(seed: int) -> GaussianHmmLike:
    return cast(
        GaussianHmmLike,
        GaussianHMM(
            n_components=2,
            covariance_type="diag",
            min_covar=MIN_COVAR,
            n_iter=500,
            tol=1e-4,
            random_state=seed,
        ),
    )


def _label_mapping(means: FloatArray) -> tuple[str, str]:
    risk_scores = means[:, 1] - means[:, 0]
    if abs(float(risk_scores[0] - risk_scores[1])) <= TIE_TOLERANCE:
        if abs(float(means[0, 0] - means[1, 0])) <= TIE_TOLERANCE:
            risk_off_index = 0
        else:
            risk_off_index = int(np.argmin(means[:, 0]))
    else:
        risk_off_index = int(np.argmax(risk_scores))
    return tuple("RISK_OFF" if index == risk_off_index else "RISK_ON" for index in range(2))  # type: ignore[return-value]


def _artifact_from_model(
    model: GaussianHmmLike,
    *,
    seed: int,
    scaler_mean: FloatArray,
    scaler_std: FloatArray,
    train_last_posterior: FloatArray,
) -> HMMArtifact:
    parameters: dict[str, object] = {
        "artifactType": "HMM_CANONICAL_NUMERIC_PARAMETERS",
        "covarianceType": "diag",
        "diagonalCovariance": np.asarray(model._covars_, dtype=np.float64).tolist(),
        "labelByInternalState": _label_mapping(np.asarray(model.means_, dtype=np.float64)),
        "means": np.asarray(model.means_, dtype=np.float64).tolist(),
        "minCovar": MIN_COVAR,
        "nComponents": 2,
        "scalerMean": scaler_mean.tolist(),
        "scalerStd": scaler_std.tolist(),
        "selectedSeed": seed,
        "startProbability": np.asarray(model.startprob_, dtype=np.float64).tolist(),
        "trainLastPosterior": train_last_posterior.tolist(),
        "transitionProbability": np.asarray(model.transmat_, dtype=np.float64).tolist(),
    }
    canonical = json.dumps(parameters, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return HMMArtifact(
        selected_seed=seed,
        scaler_mean=tuple(float(v) for v in scaler_mean),  # type: ignore[arg-type]
        scaler_std=tuple(float(v) for v in scaler_std),  # type: ignore[arg-type]
        start_probability=tuple(float(v) for v in model.startprob_),  # type: ignore[arg-type]
        transition_probability=tuple(tuple(float(v) for v in row) for row in model.transmat_),  # type: ignore[arg-type]
        means=tuple(tuple(float(v) for v in row) for row in model.means_),  # type: ignore[arg-type]
        diagonal_covariance=tuple(tuple(float(v) for v in row) for row in model._covars_),  # type: ignore[arg-type]
        label_by_internal_state=cast(tuple[str, str], parameters["labelByInternalState"]),
        train_last_posterior=tuple(float(v) for v in train_last_posterior),  # type: ignore[arg-type]
        artifact_hash=hashlib.sha256(canonical).hexdigest(),
    )


def fit_hmm_regime(
    closes: object,
    *,
    train_rows: int,
    model_factory: ModelFactory = _default_model_factory,
) -> HMMRegimeResult:
    """train-only scaling/multi-start 뒤 전체 prefix에 causal filtered posterior를 반환한다.

    결과는 offline report 전용이며 Signal, RiskDecision, order 또는 risk snapshot을 게시하지 않는다.
    """
    features = build_causal_features(closes)
    if type(train_rows) is not int or train_rows < 40 or train_rows > features.shape[0]:
        _raise_stable("hmm_train_rows_invalid")
    train = features[:train_rows]
    scaler_mean = np.mean(train, axis=0, dtype=np.float64)
    scaler_std = np.std(train, axis=0, ddof=0, dtype=np.float64)
    if bool(np.any(~np.isfinite(scaler_std))) or bool(np.any(scaler_std <= 0.0)):
        return HMMRegimeResult("ABSTAIN", None, (), ("train_scaler_invalid",))
    standardized = (features - scaler_mean) / scaler_std
    train_standardized = standardized[:train_rows]
    candidates: list[tuple[float, int, GaussianHmmLike, FloatArray]] = []
    failures: list[str] = []
    for seed in SEEDS:
        try:
            model = model_factory(seed)
            model.fit(train_standardized.copy())
            posteriors, likelihood = _validate_candidate(model, train_standardized)
            candidates.append((likelihood, seed, model, posteriors))
        except Exception as exc:
            failures.append(f"seed_{seed}:{type(exc).__name__}:{exc}")
    if not candidates:
        return HMMRegimeResult("ABSTAIN", None, (), tuple(failures))
    candidates.sort(key=lambda item: item[1])
    best = candidates[0]
    for candidate in candidates[1:]:
        if candidate[0] > best[0] + TIE_TOLERANCE:
            best = candidate
    _, selected_seed, model, train_posteriors = best
    all_posteriors, _ = _forward_posteriors(
        standardized,
        start_probability=np.asarray(model.startprob_, dtype=np.float64),
        transition_probability=np.asarray(model.transmat_, dtype=np.float64),
        means=np.asarray(model.means_, dtype=np.float64),
        covariances=np.asarray(model._covars_, dtype=np.float64),
    )
    labels = _label_mapping(np.asarray(model.means_, dtype=np.float64))
    observations: list[RegimeObservation] = []
    for index, posterior in enumerate(all_posteriors):
        maximum = float(np.max(posterior))
        entropy = float(-np.sum(posterior * np.log(posterior)) / math.log(2.0))
        warnings: list[str] = []
        state: str | None = labels[int(np.argmax(posterior))]
        if maximum < POSTERIOR_LABEL_THRESHOLD:
            state = None
            warnings.append("POSTERIOR_BELOW_THRESHOLD")
        if entropy > ENTROPY_WARN_THRESHOLD:
            warnings.append("HIGH_NORMALIZED_ENTROPY")
        observations.append(
            RegimeObservation(
                feature_index=index,
                posterior=tuple(float(v) for v in posterior),  # type: ignore[arg-type]
                max_posterior=maximum,
                normalized_entropy=entropy,
                state=state,
                warnings=tuple(warnings),
            )
        )
    artifact = _artifact_from_model(
        model,
        seed=selected_seed,
        scaler_mean=scaler_mean,
        scaler_std=scaler_std,
        train_last_posterior=train_posteriors[-1],
    )
    return HMMRegimeResult("AVAILABLE", artifact, tuple(observations), tuple(failures))
