"""S5.3 exact four-grid LightGBM training, determinism and candidate selection."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, TypeVar

import lightgbm as lgb
import numpy as np
from sklearn.metrics import confusion_matrix, f1_score  # type: ignore[import-untyped]

from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.calibration import OvrPlattCalibrator, fit_ovr_platt
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.features import CORE_FEATURE_COLUMNS
from app.lightgbm.metrics import CalibrationMetrics, calibration_metrics, tie_aware_argmax
from app.lightgbm.walk_forward import UntouchedTestLoader

ClassWeightMode = Literal["NONE", "CAPPED_BALANCED"]
GRID: tuple[tuple[int, ClassWeightMode], ...] = (
    (15, "NONE"),
    (15, "CAPPED_BALANCED"),
    (31, "NONE"),
    (31, "CAPPED_BALANCED"),
)


@dataclass(frozen=True)
class Candidate:
    """허용된 num_leaves/class-weight pair와 고정 grid order."""

    num_leaves: int
    class_weight: ClassWeightMode
    grid_index: int


@dataclass(frozen=True)
class TrainedBooster:
    """재현성 검증을 통과한 text model과 iteration/thread receipt."""

    booster: lgb.Booster
    model_text: bytes
    model_sha256: str
    best_iteration: int
    num_threads: int


@dataclass(frozen=True)
class FoldEvaluation:
    """한 fold의 raw/calibrated metrics와 per-fold PASS."""

    raw: CalibrationMetrics
    calibrated: CalibrationMetrics
    passed: bool


@dataclass(frozen=True)
class CandidateEvaluation:
    """세 fold 결과를 가진 candidate selection record."""

    candidate: Candidate
    folds: tuple[FoldEvaluation, FoldEvaluation, FoldEvaluation]

    @property
    def passed(self) -> bool:
        return all(fold.passed for fold in self.folds)

    @property
    def selection_key(self) -> tuple[float, float, float, int]:
        return (
            float(np.mean([fold.calibrated.log_loss for fold in self.folds])),
            float(np.mean([fold.calibrated.brier for fold in self.folds])),
            float(np.mean([fold.calibrated.ece for fold in self.folds])),
            self.candidate.grid_index,
        )


@dataclass(frozen=True)
class FoldArrays:
    """한 primary/final split의 fit·early·calibration·evaluation numeric blocks."""

    x_fit: np.ndarray
    y_fit: np.ndarray
    x_early: np.ndarray
    y_early: np.ndarray
    x_calibration: np.ndarray
    y_calibration: np.ndarray
    x_evaluation: np.ndarray
    y_evaluation: np.ndarray


@dataclass(frozen=True)
class FinalFitArrays:
    """untouched test를 담을 수 없는 final fit·early·calibration 전용 blocks."""

    x_fit: np.ndarray
    y_fit: np.ndarray
    x_early: np.ndarray
    y_early: np.ndarray
    x_calibration: np.ndarray
    y_calibration: np.ndarray


@dataclass(frozen=True)
class CalibratedFoldRun:
    """candidate/fold의 재현 model, 독립 calibrator와 gate 결과."""

    model: TrainedBooster
    calibrator: OvrPlattCalibrator
    evaluation: FoldEvaluation


@dataclass(frozen=True)
class CandidateRun:
    """exact grid candidate 하나의 세 primary fold 실행 결과."""

    evaluation: CandidateEvaluation
    folds: tuple[CalibratedFoldRun, CalibratedFoldRun, CalibratedFoldRun]


@dataclass(frozen=True)
class FinalCandidateRun:
    """선택 후에만 열린 untouched test의 final model/calibrator/metric receipt."""

    candidate: Candidate
    model: TrainedBooster
    calibrator: OvrPlattCalibrator
    evaluation: FoldEvaluation


def exact_grid() -> tuple[Candidate, ...]:
    """재시도 후보 없이 정확히 네 candidate를 고정 순서로 반환한다."""

    return tuple(Candidate(leaves, mode, index) for index, (leaves, mode) in enumerate(GRID))


def capped_balanced_weights(labels: np.ndarray) -> np.ndarray:
    """fit block class weight를 5.0 cap 뒤 전체 산술평균 1로 정규화한다."""

    values = np.asarray(labels, dtype=np.int64)
    if values.ndim != 1 or len(values) == 0 or not np.isin(values, (0, 1, 2)).all():
        raise LightGbmContractError("fit labels must use exact class indices")
    counts = np.bincount(values, minlength=3)
    if (counts == 0).any():
        raise LightGbmContractError("UNIDENTIFIABLE_OUTPUT: fit class is missing")
    raw = len(values) / (3.0 * counts.astype(np.float64))
    capped = np.minimum(raw, 5.0)
    weights = capped[values]
    mean = float(weights.mean())
    if not np.isfinite(mean) or mean <= 0:
        raise LightGbmContractError("fit sample weight normalization failed")
    return weights / mean


def fit_lightgbm_reproducible(
    x_fit: np.ndarray,
    y_fit: np.ndarray,
    x_early: np.ndarray,
    y_early: np.ndarray,
    candidate: Candidate,
) -> TrainedBooster:
    """동일 candidate를 threads 4로 두 번 fit하고 drift일 때만 threads 1로 재검증한다."""

    _validate_candidate(candidate)

    def fit_once(num_threads: int) -> tuple[bytes, lgb.Booster]:
        weights = (
            capped_balanced_weights(y_fit) if candidate.class_weight == "CAPPED_BALANCED" else None
        )
        feature_names = (
            list(CORE_FEATURE_COLUMNS)
            if x_fit.shape[1] == len(CORE_FEATURE_COLUMNS)
            else [f"Column_{index}" for index in range(x_fit.shape[1])]
        )
        train_data = lgb.Dataset(
            x_fit,
            label=y_fit,
            weight=weights,
            feature_name=feature_names,
            free_raw_data=False,
        )
        early_data = lgb.Dataset(
            x_early,
            label=y_early,
            reference=train_data,
            feature_name=feature_names,
            free_raw_data=False,
        )
        parameters: dict[str, object] = {
            "objective": "multiclass",
            "num_class": 3,
            "metric": "multi_logloss",
            "learning_rate": 0.05,
            "num_leaves": candidate.num_leaves,
            "min_data_in_leaf": 50,
            "feature_fraction": 0.8,
            "bagging_fraction": 1.0,
            "bagging_freq": 0,
            "deterministic": True,
            "force_col_wise": True,
            "seed": 20260729,
            "data_random_seed": 20260729,
            "feature_fraction_seed": 20260729,
            "bagging_seed": 20260729,
            "drop_seed": 20260729,
            "num_threads": num_threads,
            "verbosity": -1,
        }
        booster = lgb.train(
            parameters,
            train_data,
            num_boost_round=500,
            valid_sets=[early_data],
            valid_names=["early"],
            callbacks=[lgb.early_stopping(50, verbose=False)],
        )
        best_iteration = booster.best_iteration
        if best_iteration <= 0 or best_iteration > 500:
            raise LightGbmContractError("LightGBM best iteration is invalid")
        model_text = booster.model_to_string(num_iteration=best_iteration).encode("utf-8")
        return model_text, booster

    model_text, booster, threads = resolve_deterministic_fit(fit_once)
    return TrainedBooster(
        booster=booster,
        model_text=model_text,
        model_sha256=hashlib.sha256(model_text).hexdigest(),
        best_iteration=booster.best_iteration,
        num_threads=threads,
    )


T = TypeVar("T")


def resolve_deterministic_fit(
    fit_once: Callable[[int], tuple[bytes, T]],
) -> tuple[bytes, T, int]:
    """threads 4 drift면 threads 1을 검증하고, 계속 drift면 전체 model FAIL로 종료한다."""

    first_bytes, first_value = fit_once(4)
    second_bytes, _ = fit_once(4)
    if first_bytes == second_bytes:
        return first_bytes, first_value, 4
    single_first, single_value = fit_once(1)
    single_second, _ = fit_once(1)
    if single_first == single_second:
        return single_first, single_value, 1
    raise LightGbmContractError("LightGBM repeated hash drifted with threads=1")


def raw_margins(model: TrainedBooster, features: np.ndarray) -> np.ndarray:
    """calibrator 입력용 raw margin vector를 float64로 반환한다."""

    result = model.booster.predict(
        features,
        raw_score=True,
        num_iteration=model.best_iteration,
        num_threads=model.num_threads,
    )
    margins = np.asarray(result, dtype=np.float64)
    if margins.ndim != 2 or margins.shape[1] != 3 or not np.isfinite(margins).all():
        raise LightGbmContractError("LightGBM raw margin output is invalid")
    return margins


def calibrated_probabilities(
    model: TrainedBooster,
    calibrator: OvrPlattCalibrator,
    features: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """같은 best iteration의 raw probability와 calibrated probability를 함께 반환한다."""

    raw = np.asarray(
        model.booster.predict(
            features,
            num_iteration=model.best_iteration,
            num_threads=model.num_threads,
        ),
        dtype=np.float64,
    )
    if (
        raw.ndim != 2
        or raw.shape[1] != 3
        or not np.isfinite(raw).all()
        or (raw < 0).any()
        or not np.allclose(raw.sum(axis=1), 1.0, rtol=0.0, atol=1e-12)
    ):
        raise LightGbmContractError("LightGBM raw probability output is invalid")
    return raw, calibrator.transform(raw_margins(model, features))


def run_exact_four_grid(
    folds: Sequence[FoldArrays],
) -> tuple[CandidateRun, CandidateRun, CandidateRun, CandidateRun]:
    """세 primary fold 전체에서 exact 네 candidate를 한 번씩 평가하고 추가 grid는 만들지 않는다."""

    if len(folds) != 3:
        raise LightGbmContractError("exact grid requires three primary folds")
    for fold in folds:
        _require_all_classes(fold.y_fit, "fit")
        _require_all_classes(fold.y_calibration, "calibration")
    runs: list[CandidateRun] = []
    for candidate in exact_grid():
        fold_runs: list[CalibratedFoldRun] = []
        for fold in folds:
            model = fit_lightgbm_reproducible(
                fold.x_fit,
                fold.y_fit,
                fold.x_early,
                fold.y_early,
                candidate,
            )
            calibrator = fit_ovr_platt(raw_margins(model, fold.x_calibration), fold.y_calibration)
            raw, calibrated = calibrated_probabilities(model, calibrator, fold.x_evaluation)
            evaluation = evaluate_calibration_gate(fold.y_evaluation, raw, calibrated)
            fold_runs.append(CalibratedFoldRun(model, calibrator, evaluation))
        evaluations = tuple(item.evaluation for item in fold_runs)
        runs.append(
            CandidateRun(
                CandidateEvaluation(candidate, (evaluations[0], evaluations[1], evaluations[2])),
                (fold_runs[0], fold_runs[1], fold_runs[2]),
            )
        )
    return (runs[0], runs[1], runs[2], runs[3])


def run_final_candidate(
    candidate_runs: Sequence[CandidateRun],
    final_blocks: FinalFitArrays,
    untouched_test: UntouchedTestLoader[tuple[np.ndarray, np.ndarray]],
) -> FinalCandidateRun | None:
    """no-pass면 test를 열지 않고, 선택 후보만 final early/calibration 뒤 test를 정확히 한 번 평가한다."""

    if tuple(item.evaluation.candidate for item in candidate_runs) != exact_grid():
        raise LightGbmContractError("final candidate runs drifted from exact four-grid")
    selected = select_candidate([item.evaluation for item in candidate_runs])
    if selected is None:
        return None
    _require_all_classes(final_blocks.y_fit, "final fit")
    _require_all_classes(final_blocks.y_calibration, "final calibration")
    model = fit_lightgbm_reproducible(
        final_blocks.x_fit,
        final_blocks.y_fit,
        final_blocks.x_early,
        final_blocks.y_early,
        selected.candidate,
    )
    calibrator = fit_ovr_platt(
        raw_margins(model, final_blocks.x_calibration),
        final_blocks.y_calibration,
    )
    x_test, y_test = untouched_test.read(phase="FINAL_REPORT")
    raw, calibrated = calibrated_probabilities(model, calibrator, x_test)
    return FinalCandidateRun(
        selected.candidate,
        model,
        calibrator,
        evaluate_calibration_gate(y_test, raw, calibrated),
    )


def evaluate_calibration_gate(
    y_true: np.ndarray,
    raw_probabilities: np.ndarray,
    calibrated_probabilities: np.ndarray,
) -> FoldEvaluation:
    """ECE/Brier/log-loss 조건을 한 fold에서 모두 만족해야 PASS로 표시한다."""

    raw = calibration_metrics(y_true, raw_probabilities)
    calibrated = calibration_metrics(y_true, calibrated_probabilities)
    passed = (
        calibrated.ece <= 0.05
        and calibrated.brier <= raw.brier + 0.005
        and calibrated.log_loss <= raw.log_loss + 0.01
    )
    return FoldEvaluation(raw, calibrated, passed)


def select_candidate(evaluations: Sequence[CandidateEvaluation]) -> CandidateEvaluation | None:
    """세 fold 모두 PASS한 후보만 locked lexicographic key로 선택한다."""

    if tuple(item.candidate for item in evaluations) != exact_grid():
        raise LightGbmContractError(
            "candidate evaluation set or order drifted from exact four-grid"
        )
    passed = [evaluation for evaluation in evaluations if evaluation.passed]
    return min(passed, key=lambda evaluation: evaluation.selection_key) if passed else None


def open_final_test_after_selection(
    evaluations: Sequence[CandidateEvaluation],
    loader: UntouchedTestLoader[T],
) -> tuple[CandidateEvaluation | None, T | None]:
    """PASS 후보가 없으면 final test access 0을 보장하고 ABSTAIN 경로를 반환한다."""

    selected = select_candidate(evaluations)
    if selected is None:
        return None, None
    return selected, loader.read(phase="FINAL_REPORT")


def research_cost_report(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    forward_returns: np.ndarray,
    fit_class_counts: np.ndarray,
) -> dict[str, object]:
    """25/30/35 bps directional edge와 always-HOLD/train-prior baseline을 report-only로 계산한다."""

    labels = np.asarray(y_true, dtype=np.int64)
    returns = np.asarray(forward_returns, dtype=np.float64)
    predicted = tie_aware_argmax(probabilities)
    if (
        labels.shape != returns.shape
        or labels.shape != predicted.shape
        or not np.isfinite(returns).all()
    ):
        raise LightGbmContractError("cost report inputs are invalid")
    counts = np.asarray(fit_class_counts, dtype=np.float64)
    if counts.shape != (3,) or (counts <= 0).any():
        raise LightGbmContractError("train-only prior baseline requires all classes")
    prior = counts / counts.sum()

    sensitivity: dict[str, float] = {}
    for basis_points in (25, 30, 35):
        cost = basis_points / 10_000.0
        edges = np.where(
            predicted == 2, returns - cost, np.where(predicted == 0, -returns - cost, 0.0)
        )
        sensitivity[str(basis_points)] = float(edges.mean())
    always_hold_probabilities = np.zeros_like(probabilities, dtype=np.float64)
    always_hold_probabilities[:, 1] = 1.0
    prior_probabilities = np.tile(prior, (len(labels), 1))
    return {
        "directionalEdgeOnly": True,
        "costSensitivityBps": sensitivity,
        "meanEdge35Bps": sensitivity["35"],
        "logLoss": calibration_metrics(labels, probabilities).log_loss,
        "brier": calibration_metrics(labels, probabilities).brier,
        "ece": calibration_metrics(labels, probabilities).ece,
        "macroF1": float(
            f1_score(labels, predicted, labels=[0, 1, 2], average="macro", zero_division=0)
        ),
        "confusionMatrix": confusion_matrix(labels, predicted, labels=[0, 1, 2]).tolist(),
        "alwaysHold": _baseline_report(labels, always_hold_probabilities, returns),
        "trainOnlyPrior": {
            "probabilities": prior.tolist(),
            **_baseline_report(labels, prior_probabilities, returns),
        },
        "fakeArtifactsIncluded": False,
    }


def _baseline_report(
    labels: np.ndarray,
    probabilities: np.ndarray,
    returns: np.ndarray,
) -> dict[str, object]:
    """selection과 무관한 baseline도 같은 metric/cost 정의로 비교 가능하게 만든다."""

    predicted = tie_aware_argmax(probabilities)
    metrics = calibration_metrics(labels, probabilities)
    sensitivity: dict[str, float] = {}
    for basis_points in (25, 30, 35):
        cost = basis_points / 10_000.0
        edges = np.where(
            predicted == 2,
            returns - cost,
            np.where(predicted == 0, -returns - cost, 0.0),
        )
        sensitivity[str(basis_points)] = float(edges.mean())
    return {
        "costSensitivityBps": sensitivity,
        "meanEdge35Bps": sensitivity["35"],
        "logLoss": metrics.log_loss,
        "brier": metrics.brier,
        "ece": metrics.ece,
        "macroF1": float(
            f1_score(labels, predicted, labels=[0, 1, 2], average="macro", zero_division=0)
        ),
        "confusionMatrix": confusion_matrix(labels, predicted, labels=[0, 1, 2]).tolist(),
    }


def model_manifest_bytes(
    model: TrainedBooster, candidate: Candidate, calibrator_sha256: str
) -> bytes:
    """model/report ID를 wall clock이 아닌 content hash에서 만드는 closed numeric manifest다."""

    manifest = {
        "manifestVersion": "s5-lightgbm-model-v1",
        "modelVersion": f"lgbm-v1-{model.model_sha256[:12]}",
        "modelSha256": model.model_sha256,
        "calibratorSha256": calibrator_sha256,
        "bestIteration": model.best_iteration,
        "numThreads": model.num_threads,
        "numLeaves": candidate.num_leaves,
        "classWeight": candidate.class_weight,
        "classOrder": ["SELL", "HOLD", "BUY"],
    }
    return canonical_json_bytes(manifest)


def _validate_candidate(candidate: Candidate) -> None:
    if candidate not in exact_grid():
        raise LightGbmContractError("LightGBM candidate is outside exact four-grid")


def _require_all_classes(labels: np.ndarray, block: str) -> None:
    values = np.asarray(labels, dtype=np.int64)
    if values.ndim != 1 or set(values.tolist()) != {0, 1, 2}:
        raise LightGbmContractError(f"UNIDENTIFIABLE_OUTPUT: {block} class is missing")
