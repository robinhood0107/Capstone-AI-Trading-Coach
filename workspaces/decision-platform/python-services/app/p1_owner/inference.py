"""Fixed-ABI Return Engine LSTM inference with no provider or database access."""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np

from app.data._shared.canonical_json import canonical_json_bytes
from app.p1_owner.assets import FEATURE_ORDER
from app.p1_owner.importer import P1ArtifactImportError, validate_artifact_bundle
from app.p1_owner.model_shape import (
    TARGET_LOG_RETURN,
    WINDOW_SIZE,
    ModelShape,
    ModelShapeError,
    classify_signal,
    resolve_shape,
)

_REQUEST_FIELDS = frozenset({"artifactId", "bundleSha256", "contractId", "rows", "sessionDate"})
_ROW_FIELDS = frozenset({"currentClose", "features", "sessionDate", "symbol"})
_WINDOW_SIZE = WINDOW_SIZE
_FEATURE_COUNT = len(FEATURE_ORDER)
_MAX_REQUEST_BYTES = 256 * 1024
_SIGNALS = ("BUY", "HOLD", "SELL")


class ReturnInferenceError(ValueError):
    """A model bundle or inference request violates the fixed runtime ABI."""


@dataclass(frozen=True, slots=True)
class ReturnPrediction:
    symbol: str
    forecast_close: float
    expected_return: float
    signal: str


@dataclass(frozen=True, slots=True)
class ReturnInferenceModel:
    artifact_id: str
    bundle_sha256: str
    evidence_mode: str
    symbols: tuple[str, ...]
    scaler: dict[str, tuple[np.ndarray, np.ndarray]]
    tensors: dict[str, np.ndarray]
    shape: ModelShape
    # targetTransform=LOG_RETURN 번들만 채운다. RAW_CLOSE 면 비어 있다.
    target_scaler: dict[str, tuple[float, float]]

    @classmethod
    def load(
        cls,
        *,
        bundle_root: Path,
        manifest_sha256: str,
        allow_synthetic: bool,
    ) -> ReturnInferenceModel:
        """Load only a fully revalidated v3 bundle and its three reviewed runtime files."""

        try:
            validated = validate_artifact_bundle(
                bundle_root=bundle_root,
                expected_manifest_sha256=manifest_sha256,
            )
        except P1ArtifactImportError as error:
            raise ReturnInferenceError(str(error)) from error
        if validated.evidence_mode == "SYNTHETIC_GOLDEN":
            if not allow_synthetic:
                raise ReturnInferenceError("synthetic bundle is disabled outside the test profile")
        elif not (
            validated.real_team_b
            and validated.model_quality in {"PASS", "BELOW_BASELINE"}
            and validated.mock_runtime_eligible
        ):
            raise ReturnInferenceError("real bundle is not runtime eligible")
        scaler_payload = _object(validated.payloads["scaler.json"], "scaler")
        symbols_payload = scaler_payload.get("symbols")
        if not isinstance(symbols_payload, dict):
            raise ReturnInferenceError("scaler symbol map is unavailable")
        symbols = tuple(symbols_payload)
        scaler: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        target_scaler: dict[str, tuple[float, float]] = {}
        for symbol, parameters in symbols_payload.items():
            if not isinstance(parameters, dict):
                raise ReturnInferenceError("scaler parameter object is invalid")
            target = parameters.get("target")
            if target is not None:
                if (
                    not isinstance(target, dict)
                    or not _finite_number(target.get("mean"))
                    or not _finite_number(target.get("scale"))
                    or float(target["scale"]) <= 0
                ):
                    raise ReturnInferenceError("scaler target values are unusable")
                target_scaler[symbol] = (float(target["mean"]), float(target["scale"]))
            mean = np.asarray(parameters.get("mean"), dtype=np.float64)
            scale = np.asarray(parameters.get("scale"), dtype=np.float64)
            if (
                mean.shape != (_FEATURE_COUNT,)
                or scale.shape != (_FEATURE_COUNT,)
                or not bool(np.isfinite(mean).all())
                or not bool(np.isfinite(scale).all())
                or bool((scale <= 0).any())
            ):
                raise ReturnInferenceError("scaler values violate the fixed feature ABI")
            scaler[symbol] = (mean, scale)
        # 모델 형상의 진실 소스는 번들의 config.json 하나다.
        try:
            shape = resolve_shape(
                _object(validated.payloads["config.json"], "config"), FEATURE_ORDER
            )
        except ModelShapeError as error:
            raise ReturnInferenceError(f"config model shape is unusable: {error}") from error
        if shape.target_transform == TARGET_LOG_RETURN and set(target_scaler) != set(symbols):
            raise ReturnInferenceError(
                "LOG_RETURN bundle must publish a target scaler for every symbol"
            )
        tensors = _read_safetensors(validated.payloads["model.safetensors"])
        if set(tensors) != shape.tensor_names(symbols):
            raise ReturnInferenceError("model tensor namespace differs from scaler symbols")
        return cls(
            artifact_id=validated.artifact_id,
            bundle_sha256=validated.bundle_sha256,
            evidence_mode=validated.evidence_mode,
            symbols=symbols,
            scaler=scaler,
            tensors=tensors,
            shape=shape,
            target_scaler=target_scaler,
        )

    def infer_bytes(self, request_bytes: bytes) -> bytes:
        """Validate one canonical exact-31 request and return canonical bounded predictions."""

        if not request_bytes or len(request_bytes) > _MAX_REQUEST_BYTES:
            raise ReturnInferenceError("inference request exceeds its byte bound")
        request = _object(request_bytes, "inference request")
        if canonical_json_bytes(request) != request_bytes:
            raise ReturnInferenceError("inference request must be canonical JSON")
        if set(request) != _REQUEST_FIELDS:
            raise ReturnInferenceError("inference request root fields are not closed")
        if (
            request.get("contractId") != "p1-return-inference-request.v1"
            or request.get("artifactId") != self.artifact_id
            or request.get("bundleSha256") != self.bundle_sha256
        ):
            raise ReturnInferenceError("inference request model binding mismatch")
        session_date = request.get("sessionDate")
        if not isinstance(session_date, str):
            raise ReturnInferenceError("inference session date is invalid")
        rows = request.get("rows")
        if not isinstance(rows, list) or len(rows) != 31:
            raise ReturnInferenceError("inference request must contain exact-31 rows")
        by_symbol: dict[str, tuple[float, np.ndarray]] = {}
        for row in rows:
            if not isinstance(row, dict) or set(row) != _ROW_FIELDS:
                raise ReturnInferenceError("inference row fields are not closed")
            symbol = row.get("symbol")
            if not isinstance(symbol, str) or symbol not in self.scaler or symbol in by_symbol:
                raise ReturnInferenceError("inference symbol set is invalid")
            if row.get("sessionDate") != session_date:
                raise ReturnInferenceError("inference row session date drifted")
            current_close = row.get("currentClose")
            features_value = row.get("features")
            if not _finite_number(current_close) or not isinstance(features_value, list):
                raise ReturnInferenceError("inference feature window is invalid")
            current_close_value = float(cast(float | int, current_close))
            try:
                features = np.asarray(cast(list[list[float]], features_value), dtype=np.float64)
            except (TypeError, ValueError) as error:
                raise ReturnInferenceError("inference feature window is invalid") from error
            if (
                current_close_value <= 0
                or features.shape != (_WINDOW_SIZE, _FEATURE_COUNT)
                or not bool(np.isfinite(features).all())
                or not math.isclose(
                    current_close_value,
                    float(features[-1, FEATURE_ORDER.index("raw_close")]),
                    rel_tol=0.0,
                    abs_tol=1e-9,
                )
            ):
                raise ReturnInferenceError("inference feature window is invalid")
            by_symbol[symbol] = (current_close_value, features)
        if set(by_symbol) != set(self.symbols):
            raise ReturnInferenceError("inference symbol namespace is not exact-31")
        predictions = [self._infer_symbol(symbol, *by_symbol[symbol]) for symbol in self.symbols]
        response = {
            "artifactId": self.artifact_id,
            "bundleSha256": self.bundle_sha256,
            "contractId": "p1-return-inference-response.v1",
            "orderAuthority": "NONE",
            "predictions": [
                {
                    "expectedReturn": prediction.expected_return,
                    "forecastClose": prediction.forecast_close,
                    "signal": prediction.signal,
                    "symbol": prediction.symbol,
                }
                for prediction in predictions
            ],
            "providerCalls": 0,
            "sessionDate": session_date,
        }
        return canonical_json_bytes(response)

    def _infer_symbol(
        self,
        symbol: str,
        current_close: float,
        features: np.ndarray,
    ) -> ReturnPrediction:
        mean, scale = self.scaler[symbol]
        values = (features - mean) / scale
        hidden_size = self.shape.hidden_size
        gate_width = self.shape.gate_width
        hidden: np.ndarray | None = None
        for layer in range(self.shape.layer_count):
            input_size = self.shape.layer_input_size(layer)
            layer_input = values if hidden is None else hidden
            h = np.zeros(hidden_size, dtype=np.float64)
            c = np.zeros(hidden_size, dtype=np.float64)
            weight_ih = self.tensors[f"{symbol}.weight_ih_l{layer}"].astype(np.float64, copy=False)
            weight_hh = self.tensors[f"{symbol}.weight_hh_l{layer}"].astype(np.float64, copy=False)
            bias = self.tensors[f"{symbol}.bias_ih_l{layer}"].astype(
                np.float64, copy=False
            ) + self.tensors[f"{symbol}.bias_hh_l{layer}"].astype(np.float64, copy=False)
            if weight_ih.shape != (gate_width, input_size) or weight_hh.shape != (
                gate_width,
                hidden_size,
            ):
                raise ReturnInferenceError("runtime tensor shape drifted")
            outputs = np.empty((_WINDOW_SIZE, hidden_size), dtype=np.float64)
            for index in range(_WINDOW_SIZE):
                gates = weight_ih @ layer_input[index] + bias + weight_hh @ h
                input_gate, forget_gate, cell_gate, output_gate = np.split(gates, 4)
                input_gate = _sigmoid(input_gate)
                forget_gate = _sigmoid(forget_gate)
                cell_gate = np.tanh(cell_gate)
                output_gate = _sigmoid(output_gate)
                c = forget_gate * c + input_gate * cell_gate
                h = output_gate * np.tanh(c)
                outputs[index] = h
            hidden = outputs
        assert hidden is not None
        head_weight = self.tensors[f"{symbol}.head.weight"].astype(np.float64, copy=False)
        head_bias = self.tensors[f"{symbol}.head.bias"].astype(np.float64, copy=False)
        scaled_forecast = float((head_weight @ hidden[-1] + head_bias)[0])
        if self.shape.target_transform == TARGET_LOG_RETURN:
            # 모델이 로그수익률을 낸다. 가격을 현재가에서 재구성하므로 학습 구간의 가격
            # 범위에 갇히지 않는다. 절대가 타깃은 test 가 train 최대를 넘으면 역변환이
            # 학습 평균 근처로 주저앉아 expectedReturn -85% 를 만든다.
            target_mean, target_scale = self.target_scaler[symbol]
            log_return = scaled_forecast * target_scale + target_mean
            try:
                forecast_close = current_close * math.exp(log_return)
            except OverflowError:
                raise ReturnInferenceError("model produced a non-finite prediction") from None
        else:
            raw_close_index = FEATURE_ORDER.index("raw_close")
            forecast_close = max(
                0.0,
                scaled_forecast * float(scale[raw_close_index]) + float(mean[raw_close_index]),
            )
        expected_return = forecast_close / current_close - 1.0
        signal = classify_signal(expected_return)
        if signal not in _SIGNALS or not all(
            math.isfinite(value) for value in (forecast_close, expected_return)
        ):
            raise ReturnInferenceError("model produced a non-finite prediction")
        return ReturnPrediction(symbol, forecast_close, expected_return, signal)


def _read_safetensors(content: bytes) -> dict[str, np.ndarray]:
    if len(content) < 16:
        raise ReturnInferenceError("safetensors file is truncated")
    header_length = struct.unpack("<Q", content[:8])[0]
    try:
        header = json.loads(content[8 : 8 + header_length])
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReturnInferenceError("safetensors header is invalid") from error
    if not isinstance(header, dict):
        raise ReturnInferenceError("safetensors header is not an object")
    header.pop("__metadata__", None)
    data = memoryview(content)[8 + header_length :]
    tensors: dict[str, np.ndarray] = {}
    for name, descriptor in header.items():
        if not isinstance(name, str) or not isinstance(descriptor, dict):
            raise ReturnInferenceError("safetensors descriptor is invalid")
        offsets = descriptor.get("data_offsets")
        shape = descriptor.get("shape")
        if (
            descriptor.get("dtype") != "F32"
            or not isinstance(offsets, list)
            or len(offsets) != 2
            or not isinstance(shape, list)
            or not all(isinstance(item, int) and item > 0 for item in shape)
        ):
            raise ReturnInferenceError("safetensors descriptor is invalid")
        start, end = offsets
        if not isinstance(start, int) or not isinstance(end, int):
            raise ReturnInferenceError("safetensors extent is invalid")
        tensor = np.frombuffer(data[start:end], dtype="<f4").reshape(tuple(shape)).copy()
        if not bool(np.isfinite(tensor).all()):
            raise ReturnInferenceError("safetensors tensor is non-finite")
        tensors[name] = tensor
    return tensors


def _sigmoid(values: np.ndarray) -> np.ndarray:
    positive = values >= 0
    result = np.empty_like(values)
    result[positive] = 1.0 / (1.0 + np.exp(-values[positive]))
    exponential = np.exp(values[~positive])
    result[~positive] = exponential / (1.0 + exponential)
    return result


def _object(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReturnInferenceError(f"{label} is invalid JSON") from error
    if not isinstance(value, dict):
        raise ReturnInferenceError(f"{label} must be a JSON object")
    return cast(dict[str, Any], value)


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )
