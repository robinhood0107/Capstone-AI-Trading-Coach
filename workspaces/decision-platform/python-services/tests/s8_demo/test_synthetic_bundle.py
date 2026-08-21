from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
import numpy as np

from app.s8_demo.synthetic_bundle import build_synthetic_bundle


ROOT = Path(__file__).resolve().parents[5]
CONFIG = ROOT / "shared-docs" / "backtest_config.yaml"
SCHEMAS = ROOT / "contracts" / "schemas"


def test_bundle_is_deterministic_bounded_and_contract_valid(tmp_path: Path) -> None:
    first = build_synthetic_bundle(CONFIG)
    second = build_synthetic_bundle(CONFIG)
    assert first == second
    assert first.manifest["producer"] == "decision-platform"
    assert first.manifest["fixtureClass"] == "SYNTHETIC_FAKE_E2E"
    assert first.manifest["performanceClaimAllowed"] is False
    assert [item["strategy"] for item in first.backtest_projection["data"]["view"]["strategies"]] == [
        "Baseline",
        "Guide",
        "Strict",
    ]
    assert first.model_projection["data"]["view"]["models"][2]["status"] == "ABSTAIN"
    assert first.model_projection["data"]["view"]["models"][2]["metrics"] == {
        "cagr": None,
        "mdd": None,
        "sharpe": None,
        "sortino": None,
        "var95": None,
        "cvar95": None,
    }
    _validate("dashboard-model-evaluation.v1.schema.json", first.model_projection)
    _validate("dashboard-backtest.v1.schema.json", first.backtest_projection)
    first.write(tmp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "backtest.json",
        "manifest.json",
        "model-evaluation.json",
    ]
    assert all(path.is_file() and not path.is_symlink() and path.stat().st_size < 524_288 for path in tmp_path.iterdir())


def test_scalar_metrics_are_reproducible_at_frozen_tolerance() -> None:
    first = build_synthetic_bundle(CONFIG)
    second = build_synthetic_bundle(CONFIG)
    first_metrics = first.backtest_projection["data"]["view"]["strategies"][0]["metrics"]
    second_metrics = second.backtest_projection["data"]["view"]["strategies"][0]["metrics"]
    for name in first_metrics:
        assert np.isclose(first_metrics[name], second_metrics[name], rtol=1e-12, atol=1e-12)


def _validate(schema_name: str, payload: dict[str, object]) -> None:
    schema = json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)
