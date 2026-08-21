from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
import numpy as np
import pytest

from app.s8_demo.demo_seed import build_demo_seed, materialize_demo
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


def test_demo_seed_is_explicit_offline_bounded_and_idempotent(tmp_path: Path) -> None:
    seed = build_demo_seed(brokerage_mode="INTERNAL_PAPER")
    _validate("s8-demo-seed.v1.schema.json", seed)
    assert [scenario["expectedOutcome"] for scenario in seed["scenarios"]] == ["ALLOW", "WARN", "BLOCK", "HOLD"]
    assert len(seed["ragQuestions"]) == 3
    assert seed["crossMarketCapability"] == "RETIRED_NOT_APPLICABLE"
    assert seed["providerCalls"] == seed["liveAccountCalls"] == seed["liveOrderCalls"] == 0
    first = materialize_demo(config_path=CONFIG, output_dir=tmp_path, brokerage_mode="INTERNAL_PAPER")
    second = materialize_demo(config_path=CONFIG, output_dir=tmp_path, brokerage_mode="INTERNAL_PAPER")
    assert first == second
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "backtest.json",
        "demo-receipt.json",
        "demo-seed.json",
        "manifest.json",
        "model-evaluation.json",
    ]


def test_demo_seed_rejects_implicit_mode_and_conflicting_output(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="explicit_internal_paper"):
        build_demo_seed(brokerage_mode="")
    materialize_demo(config_path=CONFIG, output_dir=tmp_path, brokerage_mode="INTERNAL_PAPER")
    (tmp_path / "demo-seed.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="demo_seed_conflict"):
        materialize_demo(config_path=CONFIG, output_dir=tmp_path, brokerage_mode="INTERNAL_PAPER")


def _validate(schema_name: str, payload: dict[str, object]) -> None:
    schema = json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)
