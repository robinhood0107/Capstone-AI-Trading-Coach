"""S5.3의 locked multiclass calibration metrics와 tie policy."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.lightgbm.errors import LightGbmContractError

CLASS_COUNT = 3
TIE_ORDER = (1, 0, 2)  # HOLD, SELL, BUY
ECE_BINS = 10


@dataclass(frozen=True)
class CalibrationMetrics:
    """selection과 drift가 공유하는 unscaled Brier, natural log loss, top-label ECE."""

    brier: float
    log_loss: float
    ece: float


def tie_aware_argmax(probabilities: np.ndarray) -> np.ndarray:
    """확률 tie에서 HOLD, SELL, BUY 순으로 class index를 고른다."""

    values = _probabilities(probabilities)
    result = np.empty(values.shape[0], dtype=np.int8)
    maxima = values.max(axis=1)
    for row in range(values.shape[0]):
        for class_index in TIE_ORDER:
            if values[row, class_index] == maxima[row]:
                result[row] = class_index
                break
    return result


def multiclass_brier(y_true: np.ndarray, probabilities: np.ndarray) -> float:
    """범위 [0,2]의 `mean sum(y-p)^2` unscaled multiclass Brier를 계산한다."""

    values = _probabilities(probabilities)
    labels = _labels(y_true, len(values))
    one_hot = np.eye(CLASS_COUNT, dtype=np.float64)[labels]
    return float(np.mean(np.sum((one_hot - values) ** 2, axis=1)))


def natural_log_loss(y_true: np.ndarray, probabilities: np.ndarray) -> float:
    """float64 epsilon clipping 뒤 renormalize한 자연로그 multiclass loss를 계산한다."""

    values = _probabilities(probabilities)
    labels = _labels(y_true, len(values))
    epsilon = np.finfo(np.float64).eps
    clipped = np.clip(values, epsilon, 1.0)
    clipped /= clipped.sum(axis=1, keepdims=True)
    return float(-np.mean(np.log(clipped[np.arange(len(labels)), labels])))


def top_label_ece(y_true: np.ndarray, probabilities: np.ndarray) -> float:
    """`min(floor(conf*10),9)`의 10 equal-width bins로 weighted absolute gap을 계산한다."""

    values = _probabilities(probabilities)
    labels = _labels(y_true, len(values))
    predicted = tie_aware_argmax(values)
    confidence = values[np.arange(len(values)), predicted]
    bins = np.minimum(np.floor(confidence * ECE_BINS).astype(np.int64), ECE_BINS - 1)
    correct = predicted == labels
    ece = 0.0
    for bin_index in range(ECE_BINS):
        mask = bins == bin_index
        count = int(mask.sum())
        if count:
            ece += (
                count
                / len(values)
                * abs(float(correct[mask].mean()) - float(confidence[mask].mean()))
            )
    return ece


def class_reliability(
    y_true: np.ndarray,
    probabilities: np.ndarray,
) -> dict[int, tuple[tuple[int, float, float], ...]]:
    """동일 boundary를 쓰는 class별 OVR reliability `(count, observed, predicted)`를 반환한다."""

    values = _probabilities(probabilities)
    labels = _labels(y_true, len(values))
    output: dict[int, tuple[tuple[int, float, float], ...]] = {}
    for class_index in range(CLASS_COUNT):
        confidence = values[:, class_index]
        bins = np.minimum(np.floor(confidence * ECE_BINS).astype(np.int64), ECE_BINS - 1)
        target = labels == class_index
        rows: list[tuple[int, float, float]] = []
        for bin_index in range(ECE_BINS):
            mask = bins == bin_index
            count = int(mask.sum())
            rows.append(
                (
                    count,
                    float(target[mask].mean()) if count else 0.0,
                    float(confidence[mask].mean()) if count else 0.0,
                )
            )
        output[class_index] = tuple(rows)
    return output


def calibration_metrics(y_true: np.ndarray, probabilities: np.ndarray) -> CalibrationMetrics:
    """세 selection metric을 같은 probability validation 경계에서 계산한다."""

    return CalibrationMetrics(
        brier=multiclass_brier(y_true, probabilities),
        log_loss=natural_log_loss(y_true, probabilities),
        ece=top_label_ece(y_true, probabilities),
    )


def _probabilities(value: np.ndarray) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != CLASS_COUNT or array.shape[0] == 0:
        raise LightGbmContractError("probabilities must have shape (n, 3)")
    if not np.isfinite(array).all() or (array < 0).any():
        raise LightGbmContractError("probabilities must be finite and non-negative")
    if not np.allclose(array.sum(axis=1), 1.0, rtol=0.0, atol=1e-12):
        raise LightGbmContractError("probability rows must sum to one")
    return array


def _labels(value: np.ndarray, expected: int) -> np.ndarray:
    labels = np.asarray(value, dtype=np.int64)
    if labels.shape != (expected,) or not np.isin(labels, np.arange(CLASS_COUNT)).all():
        raise LightGbmContractError("labels must use exact class indices 0, 1, 2")
    return labels
