"""Team B 모델 형상과 공유 signal 규칙을 config.json에서 파생한다.

이전에는 hidden 128 / 3층이 importer, inference, assets 세 모듈에 각각 하드코딩돼 있었다.
Team B가 hidden 64 / 1층으로 학습 설정을 바꾸면 게이트 폭과 텐서 개수가 함께 바뀌므로,
상수를 다시 못박는 대신 `config.json`의 `hiddenSize`/`layerCount`/`featureOrder`에서 파생한다.

형상의 진실 소스는 `config.json` 하나다. safetensors 헤더 역추론 폴백은 두지 않으며,
필요한 필드가 없으면 fail-closed한다.

이 모듈은 순수 함수만 두고 provider, account, order, DB client를 import하지 않는다.
`app.p1_owner.assets`도 import하지 않아 순환 import를 만들지 않는다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

# LSTM 게이트는 input/forget/cell/output 네 묶음이다.
_GATE_GROUPS = 4

# window size는 계약 고정값이다. daily inference가 20행 창을 조립하고 raw history limit도
# 여기에 맞춰져 있어 config 주도로 풀지 않는다.
WINDOW_SIZE = 20

# Team B `SignalGenerator`의 ±0.5% deadband를 공유 규칙으로 채택한다.
# 판정 기준량은 expectedReturn = forecastClose / currentClose - 1 이다.
SIGNAL_DEADBAND = 0.005

_MIN_HIDDEN_SIZE = 8
_MAX_HIDDEN_SIZE = 1024
_MIN_LAYER_COUNT = 1
_MAX_LAYER_COUNT = 8

_LAYER_SUFFIX_TEMPLATES = ("weight_ih_l{0}", "weight_hh_l{0}", "bias_ih_l{0}", "bias_hh_l{0}")
_HEAD_SUFFIXES = ("head.weight", "head.bias")

# 모델이 무엇을 예측하는지. 이 값이 forecastClose 재구성 방법을 정한다.
#   RAW_CLOSE  - 출력이 종가의 z-score. forecast = scaled*scale[raw_close] + mean[raw_close]
#   LOG_RETURN - 출력이 로그수익률의 z-score. forecast = currentClose * exp(scaled*scale + mean)
# 기본값은 RAW_CLOSE 다. 선언하지 않은 기존 번들(SYNTHETIC_GOLDEN 포함)이 그대로 통과한다.
TARGET_RAW_CLOSE = "RAW_CLOSE"
TARGET_LOG_RETURN = "LOG_RETURN"
_TARGET_TRANSFORMS = (TARGET_RAW_CLOSE, TARGET_LOG_RETURN)


class ModelShapeError(ValueError):
    """config.json이 파생 가능한 모델 형상을 기술하지 못한다."""


@dataclass(frozen=True, slots=True)
class ModelShape:
    """config.json에서 파생한 고정 ABI."""

    hidden_size: int
    layer_count: int
    feature_count: int
    gate_width: int
    suffixes: tuple[str, ...]
    shapes: dict[str, tuple[int, ...]]
    target_transform: str = TARGET_RAW_CLOSE

    def tensor_count(self, symbol_count: int) -> int:
        return symbol_count * len(self.suffixes)

    def tensor_names(self, symbols: tuple[str, ...] | list[str]) -> set[str]:
        return {f"{symbol}.{suffix}" for symbol in symbols for suffix in self.suffixes}

    def layer_input_size(self, layer: int) -> int:
        return self.feature_count if layer == 0 else self.hidden_size


def _positive_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    # bool은 int의 하위 타입이므로 명시적으로 배제한다.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModelShapeError(f"{label} must be an integer")
    if value < minimum or value > maximum:
        raise ModelShapeError(f"{label} is out of the supported range: {value}")
    return value


def resolve_shape(config: Any, expected_feature_order: tuple[str, ...]) -> ModelShape:
    """config.json payload에서 텐서 접미사와 shape를 파생한다."""

    if not isinstance(config, dict):
        raise ModelShapeError("config payload must be an object")

    hidden_size = _positive_int(
        config.get("hiddenSize"), "hiddenSize", _MIN_HIDDEN_SIZE, _MAX_HIDDEN_SIZE
    )
    layer_count = _positive_int(
        config.get("layerCount"), "layerCount", _MIN_LAYER_COUNT, _MAX_LAYER_COUNT
    )

    # head가 (1, hidden_size)라서 출력 폭 1만 파생 가능하다.
    if config.get("outputSize") != 1:
        raise ModelShapeError("outputSize must be 1")
    if config.get("windowSize") != WINDOW_SIZE:
        raise ModelShapeError(f"windowSize must be {WINDOW_SIZE}")

    dropout = config.get("dropout")
    if isinstance(dropout, bool) or not isinstance(dropout, (int, float)):
        raise ModelShapeError("dropout must be a number")
    if not math.isfinite(float(dropout)) or not 0.0 <= float(dropout) < 1.0:
        raise ModelShapeError(f"dropout is out of the supported range: {dropout}")

    feature_order = config.get("featureOrder")
    if feature_order != list(expected_feature_order):
        raise ModelShapeError("config featureOrder drifted from the fixed feature ABI")
    feature_count = len(expected_feature_order)

    # 선언하지 않으면 RAW_CLOSE 다. 값을 준 경우에만 열거형을 강제한다.
    target_transform = config.get("targetTransform", TARGET_RAW_CLOSE)
    if target_transform not in _TARGET_TRANSFORMS:
        raise ModelShapeError(f"targetTransform is unsupported: {target_transform!r}")

    gate_width = _GATE_GROUPS * hidden_size
    suffixes: list[str] = []
    shapes: dict[str, tuple[int, ...]] = {}
    for layer in range(layer_count):
        input_size = feature_count if layer == 0 else hidden_size
        for template in _LAYER_SUFFIX_TEMPLATES:
            suffix = template.format(layer)
            suffixes.append(suffix)
            if suffix.startswith("weight_ih"):
                shapes[suffix] = (gate_width, input_size)
            elif suffix.startswith("weight_hh"):
                shapes[suffix] = (gate_width, hidden_size)
            else:
                shapes[suffix] = (gate_width,)
    suffixes.extend(_HEAD_SUFFIXES)
    shapes["head.weight"] = (1, hidden_size)
    shapes["head.bias"] = (1,)

    return ModelShape(
        hidden_size=hidden_size,
        layer_count=layer_count,
        feature_count=feature_count,
        gate_width=gate_width,
        suffixes=tuple(suffixes),
        shapes=shapes,
        target_transform=target_transform,
    )


def classify_signal(expected_return: float) -> str:
    """expectedReturn을 ±0.5% deadband로 분류한다."""

    if expected_return > SIGNAL_DEADBAND:
        return "BUY"
    if expected_return < -SIGNAL_DEADBAND:
        return "SELL"
    return "HOLD"
