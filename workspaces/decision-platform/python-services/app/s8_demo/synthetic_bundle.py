from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from app.financial_engineering import (
    cagr,
    historical_cvar,
    historical_var,
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
)


_RUN_ID = "demo_s8_fake_e2e_0001"
_AS_OF = "2026-08-22T00:00:00Z"
_FRESH_UNTIL = "2026-09-21T00:00:00Z"
_MONTHS = tuple(f"{year}-{month:02d}" for year in (2024, 2025) for month in range(1, 13))
_BASE_RETURNS = (
    0.011,
    -0.006,
    0.014,
    0.004,
    -0.009,
    0.012,
    0.006,
    -0.004,
    0.008,
    0.003,
    -0.007,
    0.010,
    0.005,
    -0.003,
    0.009,
    0.002,
    -0.005,
    0.011,
    0.004,
    -0.002,
    0.007,
    0.003,
    -0.004,
    0.008,
)


@dataclass(frozen=True)
class SyntheticBundle:
    artifact_id: str
    run_id: str
    content_hash: str
    manifest: dict[str, Any]
    model_projection: dict[str, Any]
    backtest_projection: dict[str, Any]
    model_projection_text: str
    backtest_projection_text: str
    model_projection_hash: str
    backtest_projection_hash: str

    def write(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "manifest.json": _canonical_json(self.manifest),
            "model-evaluation.json": self.model_projection_text,
            "backtest.json": self.backtest_projection_text,
        }
        for name, value in files.items():
            (output_dir / name).write_text(value + "\n", encoding="utf-8")


def build_synthetic_bundle(config_path: Path) -> SyntheticBundle:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    scenarios = config["modelComparison"]["scenarios"]
    if scenarios != ["Baseline", "Guide", "Strict"]:
        raise ValueError("synthetic_scenarios_not_exact")
    initial_cash = float(config["capital"]["initialCash"])
    strategy_returns = {
        "Baseline": _BASE_RETURNS,
        "Guide": tuple(value - 0.0008 if value > 0 else value * 0.72 for value in _BASE_RETURNS),
        "Strict": tuple(value * 0.68 if value > 0 else value * 0.45 for value in _BASE_RETURNS),
    }
    strategies: list[dict[str, Any]] = []
    for name in scenarios:
        returns = strategy_returns[name]
        equity = _equity_curve(initial_cash, returns)
        strategies.append(
            {
                "strategy": name,
                "metrics": _metrics(equity, returns),
                "curve": [
                    {"at": f"{month}-28T00:00:00Z", "value": _stable_number(value)}
                    for month, value in zip(_MONTHS, equity[1:], strict=True)
                ],
            }
        )
    projection_core: dict[str, Any] = {
        "runId": _RUN_ID,
        "fixtureClass": "SYNTHETIC_FAKE_E2E",
        "strategies": strategies,
        "heatmap": [
            {"month": month, "return": _stable_number(value)}
            for month, value in zip(_MONTHS, _BASE_RETURNS, strict=True)
        ],
        "metricCards": _metric_cards(strategies),
    }
    projection_hash = _sha256(_canonical_json(projection_core))
    backtest_view = {**projection_core, "projectionHash": projection_hash}
    baseline_metrics = strategies[0]["metrics"]
    empty_metrics = {key: None for key in baseline_metrics}
    model_view = {
        "runId": _RUN_ID,
        "models": [
            {"modelId": "BASELINE", "status": "AVAILABLE", "metrics": baseline_metrics},
            {"modelId": "LSTM", "status": "ABSTAIN", "metrics": empty_metrics},
            {"modelId": "LIGHTGBM", "status": "ABSTAIN", "metrics": empty_metrics},
        ],
        "timeline": strategies[0]["curve"],
        "sourceRunIds": [_RUN_ID],
    }
    model_projection = _view_envelope(model_view)
    backtest_projection = _view_envelope(backtest_view)
    model_text = _canonical_json(model_projection)
    backtest_text = _canonical_json(backtest_projection)
    file_entries = [
        _file_entry("model-evaluation.json", model_text),
        _file_entry("backtest.json", backtest_text),
    ]
    manifest_core = {
        "schemaVersion": "1.0.0",
        "producer": "decision-platform",
        "fixtureClass": "SYNTHETIC_FAKE_E2E",
        "runId": _RUN_ID,
        "asOf": _AS_OF,
        "performanceClaimAllowed": False,
        "config": {
            "schemaVersion": config["schemaVersion"],
            "currency": config["currency"],
            "initialCash": config["capital"]["initialCash"],
            "roundTripCostEstimateBps": config["executionCost"]["roundTripCostEstimateBps"],
            "marketOrderSlippageBps": config["executionCost"]["marketOrderSlippageBps"],
            "scenarios": scenarios,
        },
        "files": file_entries,
    }
    content_hash = _sha256(_canonical_json(manifest_core))
    artifact_id = "artifact_s8_" + content_hash.removeprefix("sha256:")[:24]
    manifest = {**manifest_core, "artifactId": artifact_id, "contentHash": content_hash}
    return SyntheticBundle(
        artifact_id=artifact_id,
        run_id=_RUN_ID,
        content_hash=content_hash,
        manifest=manifest,
        model_projection=model_projection,
        backtest_projection=backtest_projection,
        model_projection_text=model_text,
        backtest_projection_text=backtest_text,
        model_projection_hash=_sha256(model_text),
        backtest_projection_hash=_sha256(backtest_text),
    )


def _view_envelope(view: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": True,
        "requestId": "req_s8_fake_e2e_0001",
        "data": {
            "viewState": "READY",
            "asOf": _AS_OF,
            "freshUntil": _FRESH_UNTIL,
            "evidenceMode": "SYNTHETIC_DEMO",
            "performanceClaimAllowed": False,
            "view": view,
        },
        "warnings": [],
        "error": None,
    }


def _equity_curve(initial_cash: float, returns: tuple[float, ...]) -> tuple[float, ...]:
    output = [initial_cash]
    for value in returns:
        output.append(output[-1] * (1.0 + value))
    return tuple(output)


def _metrics(equity: tuple[float, ...], returns: tuple[float, ...]) -> dict[str, float]:
    return {
        "cagr": _stable_number(cagr(equity, periods_per_year=12)),
        "mdd": _stable_number(max_drawdown(equity)),
        "sharpe": _stable_number(sharpe_ratio(returns, periods_per_year=12)),
        "sortino": _stable_number(sortino_ratio(returns, periods_per_year=12)),
        "var95": _stable_number(historical_var(returns, confidence=0.95)),
        "cvar95": _stable_number(historical_cvar(returns, confidence=0.95)),
    }


def _metric_cards(strategies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    baseline = strategies[0]
    metrics = baseline["metrics"]
    returns = _BASE_RETURNS
    positive = sum(1 for value in returns if value > 0)
    cards = [{"metric": key, "value": value} for key, value in metrics.items()]
    cards.extend(
        [
            {"metric": "win_rate", "value": _stable_number(positive / len(returns))},
            {"metric": "trade_count", "value": len(returns)},
            {"metric": "turnover", "value": _stable_number(len(returns) / 12)},
            {"metric": "principle_violation_count", "value": 0},
            {
                "metric": "guarded_performance_delta",
                "value": _stable_number(strategies[1]["metrics"]["cagr"] - metrics["cagr"]),
            },
        ]
    )
    return cards


def _file_entry(name: str, content: str) -> dict[str, Any]:
    return {"name": name, "sha256": _sha256(content), "bytes": len(content.encode("utf-8"))}


def _stable_number(value: float) -> float:
    return float(format(value, ".15g"))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Build the offline-only S8 synthetic fake E2E bundle.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build_synthetic_bundle(args.config).write(args.output)
    print("S8_1_FAKE_E2E_BUNDLE_WRITTEN")


if __name__ == "__main__":
    main()
