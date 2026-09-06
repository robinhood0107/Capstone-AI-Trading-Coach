"""기존 OHLCV feature로 기간별 Ridge 추정치를 만든다. I/O·provider·pickle 사용은 없다."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.calendar.evidence_clock import next_session_evidence_clock
from app.data.calendar.xkrx_policy import corrected_calendar
from app.p1_owner.model_shape import WINDOW_SIZE

HORIZONS = (1, 5, 20)
MIN_TRAIN_ROWS = 2 * WINDOW_SIZE


class RidgeReturnError(ValueError):
    """불완전한 입력이나 비정상 회귀값은 고정 수익률로 대체하지 않는다."""


def fit_forecasts(
    symbol: str,
    history: list[dict[str, Any]],
    feature_rows: list[list[float]],
    feature_order: tuple[str, ...],
) -> dict[str, Any]:
    """정답이 기준일까지 완성된 행만 fit한다. 반환 artifact는 JSON만으로 추론을 재현할 수 있다."""
    days = [date.fromisoformat(str(item["sessionDate"])) for item in history]
    calendar = corrected_calendar()
    if not days or days != [
        value.date()
        for value in calendar.sessions_in_range(pd.Timestamp(days[0]), pd.Timestamp(days[-1]))
    ]:
        raise RidgeReturnError("RIDGE_HISTORY_SESSION_GAP")
    closes = np.asarray([float(item["close"]) for item in history])
    features = np.asarray(feature_rows, dtype=float)
    if (
        features.ndim != 2
        or features.shape[1] != len(feature_order)
        or not np.isfinite(features).all()
    ):
        raise RidgeReturnError("RIDGE_FEATURE_INVALID")
    if not np.isfinite(closes).all() or (closes <= 0).any():
        raise RidgeReturnError("RIDGE_PRICE_INVALID")
    offset = len(history) - len(feature_rows)
    if offset < 0:
        raise RidgeReturnError("RIDGE_FEATURE_ALIGNMENT_INVALID")
    fits: list[dict[str, Any]] = []
    forecasts: list[dict[str, Any]] = []
    for horizon in HORIZONS:
        count = len(features) - horizon
        if count < MIN_TRAIN_ROWS:
            raise RidgeReturnError(f"RIDGE_TRAIN_HISTORY_SHORT horizon={horizon} rows={count}")
        x = features[:count]
        y = closes[offset + horizon :] / closes[offset:-horizon] - 1.0
        scaler = StandardScaler().fit(x)
        regressor = Ridge(alpha=1.0, solver="svd").fit(scaler.transform(x), y)
        fit = {
            "horizonSessions": horizon,
            "mean": scaler.mean_.tolist(),
            "scale": scaler.scale_.tolist(),
            "coefficients": regressor.coef_.tolist(),
            "intercept": float(regressor.intercept_),
            "trainSamples": count,
            "lastFeatureSession": days[-horizon - 1].isoformat(),
            "trainedThrough": days[-1].isoformat(),
        }
        expected = predict(fit, features[-1].tolist())
        forecasts.append(
            {
                "horizonSessions": horizon,
                "targetSession": next_session_evidence_clock(days[-1], extra_sessions=horizon - 1)
                .date()
                .isoformat(),
                "expectedReturn": expected,
                "forecastClose": float(closes[-1]) * (1.0 + expected),
                "trainSamples": count,
                "trainedThrough": days[-1].isoformat(),
            }
        )
        fits.append(fit)
    model = {
        "contractId": "p1-ridge-return-model.v1",
        "estimator": "STANDARD_SCALER_RIDGE",
        "alpha": 1.0,
        "symbol": symbol,
        "featureOrder": list(feature_order),
        "sourceSession": days[-1].isoformat(),
        "firstSession": days[0].isoformat(),
        "inputSha256": hashlib.sha256(canonical_json_bytes(history)).hexdigest(),
        "qualityStatus": "COMPARISON_PENDING",
        "models": fits,
    }
    content = canonical_json_bytes(model)
    # JSON round-trip에서 계수·scaler 정밀도가 보존되는지 같은 입력으로 확인한다.
    restored = json.loads(content)
    for fit, forecast in zip(restored["models"], forecasts, strict=True):
        if predict(fit, features[-1].tolist()) != forecast["expectedReturn"]:
            raise RidgeReturnError("RIDGE_SERIALIZATION_DRIFT")
    return {
        "symbol": symbol,
        "modelJson": content.decode(),
        "modelSha256": hashlib.sha256(content).hexdigest(),
        "forecasts": forecasts,
    }


def predict(model: dict[str, Any], feature: list[float]) -> float:
    """검증된 JSON 계수의 예측. 출력 범위를 벗어나면 clipping으로 숨기지 않는다."""
    size = len(feature)
    if any(len(model[key]) != size for key in ("mean", "scale", "coefficients")):
        raise RidgeReturnError("RIDGE_MODEL_SHAPE_INVALID")
    scale = np.asarray(model["scale"], dtype=float)
    if (scale <= 0).any() or not np.isfinite(scale).all():
        raise RidgeReturnError("RIDGE_MODEL_SCALE_INVALID")
    result = float(
        np.dot(
            (np.asarray(feature) - np.asarray(model["mean"])) / scale,
            np.asarray(model["coefficients"]),
        )
        + float(model["intercept"])
    )
    if not math.isfinite(result) or result <= -1 or result > 1000:
        raise RidgeReturnError("RIDGE_PREDICTION_INVALID")
    return result
