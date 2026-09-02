"""scaler.json 의 선택 `target` 항목 검증 회귀.

LOG_RETURN 번들만 싣는 항목이라 있으면 검증하고 없으면 통과해야 한다.
"""

from __future__ import annotations

import json
import unittest

from app.data._shared.canonical_json import canonical_json_bytes
from app.p1_owner.assets import FEATURE_ORDER
from app.p1_owner.importer import P1ArtifactImportError, _validate_scaler


def _symbols() -> list[str]:
    return [f"{index:06d}" for index in range(1, 31)] + ["132030"]


def _scaler(target: object | None = None, *, only_for_first: bool = False) -> bytes:
    entries: dict[str, object] = {}
    for index, symbol in enumerate(_symbols()):
        parameters: dict[str, object] = {
            "mean": [0.0] * len(FEATURE_ORDER),
            "scale": [1.0] * len(FEATURE_ORDER),
        }
        if target is not None and (not only_for_first or index == 0):
            parameters["target"] = target
        entries[symbol] = parameters
    return canonical_json_bytes(
        {
            "contractId": "p1-return-scaler.v2",
            "featureOrder": list(FEATURE_ORDER),
            "fitScope": "TRAIN_ONLY",
            "symbols": entries,
        }
    )


class ScalerTargetTest(unittest.TestCase):
    def test_scaler_without_target_is_accepted(self) -> None:
        """RAW_CLOSE 번들은 target 을 싣지 않는다."""

        self.assertEqual(31, len(_validate_scaler(_scaler())))

    def test_scaler_with_valid_target_is_accepted(self) -> None:
        symbols = _validate_scaler(_scaler({"mean": 0.0005, "scale": 0.0182}))
        self.assertEqual(31, len(symbols))

    def test_invalid_target_fails_closed(self) -> None:
        cases = {
            "필드 초과": {"mean": 0.0, "scale": 1.0, "unexpected": True},
            "필드 부족": {"mean": 0.0},
            "scale 0": {"mean": 0.0, "scale": 0.0},
            "scale 음수": {"mean": 0.0, "scale": -1.0},
            "객체 아님": [0.0, 1.0],
        }
        for label, target in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(P1ArtifactImportError):
                    _validate_scaler(_scaler(target))

    def test_non_finite_target_fails_closed(self) -> None:
        """비유한 값은 canonical JSON 으로 직렬화되지 않으므로 raw JSON 으로 시험한다.

        직렬화 단계에서 이미 막히는 것이 첫 방어선이고, 그 방어선을 우회해 들어와도
        검증기가 다시 막아야 한다.
        """
        payload = json.loads(_scaler({"mean": 0.0, "scale": 1.0}))
        for value in ("Infinity", "-Infinity", "NaN"):
            with self.subTest(value=value):
                raw = json.dumps(payload).replace('"scale": 1.0}', f'"scale": {value}}}')
                raw = raw.replace('"scale":1.0}', f'"scale":{value}}}')
                with self.assertRaises(P1ArtifactImportError):
                    _validate_scaler(raw.encode("utf-8"))

    def test_unknown_symbol_parameter_key_fails_closed(self) -> None:
        payload = json.loads(_scaler())
        payload["symbols"]["005930" if "005930" in payload["symbols"] else "000001"][
            "unexpected"
        ] = True
        with self.assertRaises(P1ArtifactImportError):
            _validate_scaler(canonical_json_bytes(payload))


if __name__ == "__main__":
    unittest.main()
