"""config.json 주도 모델 형상 파생과 공유 signal deadband 회귀."""

from __future__ import annotations

import unittest

from app.p1_owner.assets import FEATURE_ORDER
from app.p1_owner.model_shape import (
    SIGNAL_DEADBAND,
    TARGET_LOG_RETURN,
    TARGET_RAW_CLOSE,
    ModelShapeError,
    classify_signal,
    resolve_shape,
)

_BASE_CONFIG = {
    "contractId": "p1-return-config.v2",
    "deterministicAlgorithms": True,
    "dropout": 0.2,
    "featureOrder": list(FEATURE_ORDER),
    "hiddenSize": 128,
    "layerCount": 3,
    "learningRate": 0.0005,
    "loss": "SmoothL1",
    "optimizer": "Adam",
    "outputSize": 1,
    "perSymbolIndependent": True,
    "seed": 0,
    "threadCount": 1,
    "windowSize": 20,
}


def _config(**overrides: object) -> dict[str, object]:
    payload = dict(_BASE_CONFIG)
    payload.update(overrides)
    return payload


class ModelShapeDerivationTest(unittest.TestCase):
    def test_contract_baseline_derives_fourteen_suffixes_per_symbol(self) -> None:
        shape = resolve_shape(_config(), FEATURE_ORDER)
        self.assertEqual(shape.gate_width, 512)
        self.assertEqual(len(shape.suffixes), 14)
        self.assertEqual(shape.tensor_count(31), 31 * 14)
        self.assertEqual(shape.shapes["weight_ih_l0"], (512, len(FEATURE_ORDER)))
        self.assertEqual(shape.shapes["weight_ih_l1"], (512, 128))
        self.assertEqual(shape.shapes["weight_hh_l2"], (512, 128))
        self.assertEqual(shape.shapes["bias_ih_l0"], (512,))
        self.assertEqual(shape.shapes["head.weight"], (1, 128))
        self.assertEqual(shape.shapes["head.bias"], (1,))

    def test_team_b_single_layer_hidden_64_derives_six_suffixes_per_symbol(self) -> None:
        """Team B가 실제로 제출한 hidden 64 / 1층 설정이 파생 가능해야 한다."""

        shape = resolve_shape(_config(hiddenSize=64, layerCount=1, dropout=0), FEATURE_ORDER)
        self.assertEqual(shape.gate_width, 256)
        self.assertEqual(
            shape.suffixes,
            (
                "weight_ih_l0",
                "weight_hh_l0",
                "bias_ih_l0",
                "bias_hh_l0",
                "head.weight",
                "head.bias",
            ),
        )
        self.assertEqual(shape.tensor_count(31), 31 * 6)
        self.assertEqual(shape.shapes["weight_ih_l0"], (256, len(FEATURE_ORDER)))
        self.assertEqual(shape.shapes["weight_hh_l0"], (256, 64))
        self.assertEqual(shape.shapes["head.weight"], (1, 64))
        self.assertEqual(shape.layer_input_size(0), len(FEATURE_ORDER))

    def test_tensor_names_are_symbol_namespaced(self) -> None:
        shape = resolve_shape(_config(hiddenSize=64, layerCount=1, dropout=0), FEATURE_ORDER)
        names = shape.tensor_names(("005930", "132030"))
        self.assertEqual(len(names), 12)
        self.assertIn("005930.weight_ih_l0", names)
        self.assertIn("132030.head.bias", names)

    def test_missing_or_out_of_range_shape_fields_fail_closed(self) -> None:
        cases: tuple[tuple[str, dict[str, object]], ...] = (
            ("hiddenSize 누락", {"hiddenSize": None}),
            ("hiddenSize 하한 미달", {"hiddenSize": 4}),
            ("hiddenSize 상한 초과", {"hiddenSize": 2048}),
            ("layerCount 0", {"layerCount": 0}),
            ("layerCount 상한 초과", {"layerCount": 9}),
            ("hiddenSize bool", {"hiddenSize": True}),
            ("dropout 1.0", {"dropout": 1.0}),
            ("dropout 음수", {"dropout": -0.1}),
            ("outputSize 2", {"outputSize": 2}),
            ("windowSize 30", {"windowSize": 30}),
        )
        for label, overrides in cases:
            with self.subTest(case=label):
                with self.assertRaises(ModelShapeError):
                    resolve_shape(_config(**overrides), FEATURE_ORDER)

    def test_feature_order_drift_fails_closed(self) -> None:
        reordered = list(FEATURE_ORDER)
        reordered.append(reordered.pop(reordered.index("volume")))
        for label, value in (
            ("순서 변경", reordered),
            ("원소 누락", list(FEATURE_ORDER)[:-1]),
            ("필드 부재", None),
        ):
            with self.subTest(case=label):
                with self.assertRaises(ModelShapeError):
                    resolve_shape(_config(featureOrder=value), FEATURE_ORDER)

    def test_non_object_config_fails_closed(self) -> None:
        with self.assertRaises(ModelShapeError):
            resolve_shape([], FEATURE_ORDER)


class SignalDeadbandTest(unittest.TestCase):
    def test_deadband_boundaries_are_hold(self) -> None:
        self.assertEqual(classify_signal(SIGNAL_DEADBAND), "HOLD")
        self.assertEqual(classify_signal(-SIGNAL_DEADBAND), "HOLD")
        self.assertEqual(classify_signal(0.0), "HOLD")

    def test_outside_deadband_is_directional(self) -> None:
        self.assertEqual(classify_signal(0.02), "BUY")
        self.assertEqual(classify_signal(-0.02), "SELL")
        self.assertEqual(classify_signal(-1.0), "SELL")

    def test_small_moves_inside_deadband_are_hold(self) -> None:
        """이전 임계값 0 규칙에서는 BUY/SELL이 됐던 값들이다."""

        self.assertEqual(classify_signal(0.0001), "HOLD")
        self.assertEqual(classify_signal(-0.0001), "HOLD")


if __name__ == "__main__":
    unittest.main()


class TargetTransformTest(unittest.TestCase):
    """모델이 무엇을 예측하는지가 forecastClose 재구성 방법을 정한다."""

    def test_absent_target_transform_defaults_to_raw_close(self) -> None:
        """선언하지 않은 기존 번들(SYNTHETIC_GOLDEN 포함)이 그대로 통과해야 한다."""

        shape = resolve_shape(_config(), FEATURE_ORDER)
        self.assertEqual(TARGET_RAW_CLOSE, shape.target_transform)

    def test_declared_transforms_are_derived(self) -> None:
        for value in (TARGET_RAW_CLOSE, TARGET_LOG_RETURN):
            with self.subTest(targetTransform=value):
                shape = resolve_shape(_config(targetTransform=value), FEATURE_ORDER)
                self.assertEqual(value, shape.target_transform)

    def test_unknown_transform_fails_closed(self) -> None:
        for value in ("LOG_PRICE", "raw_close", "", None, 0):
            with self.subTest(targetTransform=value):
                with self.assertRaises(ModelShapeError):
                    resolve_shape(_config(targetTransform=value), FEATURE_ORDER)

    def test_target_transform_does_not_change_the_tensor_inventory(self) -> None:
        """타깃을 바꿔도 텐서 집합은 같다. 형상은 hidden/layer/feature 만으로 파생된다."""

        raw = resolve_shape(_config(targetTransform=TARGET_RAW_CLOSE), FEATURE_ORDER)
        log_return = resolve_shape(_config(targetTransform=TARGET_LOG_RETURN), FEATURE_ORDER)
        self.assertEqual(raw.suffixes, log_return.suffixes)
        self.assertEqual(raw.shapes, log_return.shapes)
