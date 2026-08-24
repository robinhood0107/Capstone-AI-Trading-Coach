"""S5.3 independent 21-session OVR Platt calibration."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import minimize  # type: ignore[import-untyped]
from scipy.special import expit  # type: ignore[import-untyped]

from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.errors import LightGbmContractError


@dataclass(frozen=True)
class PlattClassParameters:
    """한 OVR class의 numeric sigmoid parameter와 calibration counts."""

    class_index: int
    a: float
    b: float
    positives: int
    negatives: int


@dataclass(frozen=True)
class OvrPlattCalibrator:
    """pickle 없이 JSON으로 직렬화 가능한 exact three-class OVR calibrator."""

    classes: tuple[PlattClassParameters, PlattClassParameters, PlattClassParameters]

    def transform(self, margins: np.ndarray) -> np.ndarray:
        """세 sigmoid를 epsilon clip한 뒤 행별 합 1로 renormalize한다."""

        values = _margins(margins)
        probabilities = np.empty_like(values)
        for parameters in self.classes:
            probabilities[:, parameters.class_index] = expit(
                parameters.a * values[:, parameters.class_index] + parameters.b
            )
        epsilon = np.finfo(np.float64).eps
        probabilities = np.clip(probabilities, epsilon, 1.0 - epsilon)
        denominator = probabilities.sum(axis=1, keepdims=True)
        if not np.isfinite(denominator).all() or (denominator <= 0).any():
            raise LightGbmContractError("Platt probability denominator is invalid")
        probabilities /= denominator
        if not np.isfinite(probabilities).all():
            raise LightGbmContractError("Platt probabilities are non-finite")
        return np.asarray(probabilities, dtype=np.float64)

    def canonical_bytes(self) -> bytes:
        """numeric JSON 외 임의 object serialization 없이 canonical calibrator bytes를 만든다."""

        return canonical_json_bytes(
            {
                "calibratorVersion": "ovr-platt-v1",
                "classOrder": ["SELL", "HOLD", "BUY"],
                "classes": [
                    {
                        "classIndex": item.class_index,
                        "a": item.a,
                        "b": item.b,
                        "positives": item.positives,
                        "negatives": item.negatives,
                    }
                    for item in self.classes
                ],
                "optimizer": {
                    "method": "L-BFGS-B",
                    "regularization": 0,
                    "maxIter": 1000,
                    "ftol": 1e-12,
                    "gtol": 1e-8,
                },
            }
        )


def fit_ovr_platt(margins: np.ndarray, y_true: np.ndarray) -> OvrPlattCalibrator:
    """독립 block의 raw margin vector에 exact target smoothing으로 OVR Platt를 fit한다."""

    values = _margins(margins)
    labels = np.asarray(y_true, dtype=np.int64)
    if labels.shape != (len(values),) or not np.isin(labels, (0, 1, 2)).all():
        raise LightGbmContractError("Platt labels must use exact class indices")
    parameters: list[PlattClassParameters] = []
    for class_index in range(3):
        binary = labels == class_index
        positives = int(binary.sum())
        negatives = len(binary) - positives
        if positives == 0 or negatives == 0:
            raise LightGbmContractError("UNIDENTIFIABLE_OUTPUT: calibration class is missing")
        positive_target = (positives + 1.0) / (positives + 2.0)
        negative_target = 1.0 / (negatives + 2.0)
        targets = np.where(binary, positive_target, negative_target)
        initial = np.asarray(
            [0.0, math.log((positives + 1.0) / (negatives + 1.0))], dtype=np.float64
        )
        feature = values[:, class_index]

        def objective(vector: np.ndarray) -> tuple[float, np.ndarray]:
            logits = vector[0] * feature + vector[1]
            probability = expit(logits)
            epsilon = np.finfo(np.float64).eps
            clipped = np.clip(probability, epsilon, 1.0 - epsilon)
            loss = -np.sum(targets * np.log(clipped) + (1.0 - targets) * np.log1p(-clipped))
            residual = probability - targets
            gradient = np.asarray([np.dot(residual, feature), residual.sum()], dtype=np.float64)
            return float(loss), gradient

        result: Any = minimize(
            objective,
            initial,
            method="L-BFGS-B",
            jac=True,
            options={"maxiter": 1000, "ftol": 1e-12, "gtol": 1e-8},
        )
        if not result.success or not np.isfinite(result.x).all():
            raise LightGbmContractError("CALIBRATION_FAILED: Platt optimizer did not converge")
        parameters.append(
            PlattClassParameters(
                class_index=class_index,
                a=float(result.x[0]),
                b=float(result.x[1]),
                positives=positives,
                negatives=negatives,
            )
        )
    return OvrPlattCalibrator((parameters[0], parameters[1], parameters[2]))


def calibrator_from_mapping(value: object) -> OvrPlattCalibrator:
    """검증된 canonical JSON mapping을 arbitrary object deserialization 없이 calibrator로 복원한다."""

    if not isinstance(value, dict) or set(value) != {
        "calibratorVersion",
        "classOrder",
        "classes",
        "optimizer",
    }:
        raise LightGbmContractError("calibrator mapping field set is invalid")
    if value["calibratorVersion"] != "ovr-platt-v1" or value["classOrder"] != [
        "SELL",
        "HOLD",
        "BUY",
    ]:
        raise LightGbmContractError("calibrator mapping version or class order is invalid")
    optimizer = value["optimizer"]
    if optimizer != {
        "method": "L-BFGS-B",
        "regularization": 0,
        "maxIter": 1000,
        "ftol": 1e-12,
        "gtol": 1e-8,
    }:
        raise LightGbmContractError("calibrator optimizer contract is invalid")
    raw_classes = value["classes"]
    if not isinstance(raw_classes, list) or len(raw_classes) != 3:
        raise LightGbmContractError("calibrator class count is invalid")
    result: list[PlattClassParameters] = []
    for expected_index, item in enumerate(raw_classes):
        if not isinstance(item, dict) or set(item) != {
            "classIndex",
            "a",
            "b",
            "positives",
            "negatives",
        }:
            raise LightGbmContractError("calibrator class field set is invalid")
        if item["classIndex"] != expected_index:
            raise LightGbmContractError("calibrator class index is invalid")
        a, b = item["a"], item["b"]
        positives, negatives = item["positives"], item["negatives"]
        if (
            not isinstance(a, (int, float))
            or isinstance(a, bool)
            or not isinstance(b, (int, float))
            or isinstance(b, bool)
            or not math.isfinite(float(a))
            or not math.isfinite(float(b))
            or not isinstance(positives, int)
            or isinstance(positives, bool)
            or not isinstance(negatives, int)
            or isinstance(negatives, bool)
            or positives <= 0
            or negatives <= 0
        ):
            raise LightGbmContractError("calibrator class numeric value is invalid")
        result.append(
            PlattClassParameters(expected_index, float(a), float(b), positives, negatives)
        )
    return OvrPlattCalibrator((result[0], result[1], result[2]))


def _margins(value: np.ndarray) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if (
        array.ndim != 2
        or array.shape[1] != 3
        or array.shape[0] == 0
        or not np.isfinite(array).all()
    ):
        raise LightGbmContractError("raw margins must be finite with shape (n, 3)")
    return array
