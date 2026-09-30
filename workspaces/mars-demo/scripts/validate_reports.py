#!/usr/bin/env python3
"""Validate the DEMO report artifact against its immutable scenario fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(report_path: Path, scenario_path: Path) -> dict[str, object]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    require(report.get("schemaVersion") == "mars-demo.reports.v1", "report schema")
    require(report.get("seedVersion") == scenario["seedVersion"], "scenario version")
    require(report.get("scenarioSha256") == file_sha256(scenario_path), "scenario file hash")
    require(report.get("sourcePriceSha256") == scenario["source"]["sourceSha256"], "source price hash")
    require(report.get("assumptions") == scenario["assumptions"], "cost assumptions")
    dates = [row["date"] for row in scenario["backtest"]["daily"]]
    backtest = report["backtestReport"]
    require(backtest["period"] == scenario["backtest"]["testRange"], "backtest period")
    strategies = backtest["strategies"]
    require([entry["strategy"] for entry in strategies] == ["Baseline", "Guide", "Strict"], "strategy order")
    metric_cards = {card["metric"]: card["value"] for card in backtest["metricCards"]}
    for entry in strategies:
        curve = entry["curve"]
        require([point["at"] for point in curve] == dates, f"{entry['strategy']} curve coverage")
        require(all(math.isfinite(point["value"]) and point["value"] > 0 for point in curve), "invalid equity curve")
        require(all(isinstance(value, (int, float)) and math.isfinite(value) for value in entry["metrics"].values()), "missing strategy metric")
        expected = curve[-1]["value"] - 1
        require(abs(metric_cards[f"{entry['strategy']}.netReturn"] - expected) < 1e-12, "net return receipt")
        require(metric_cards[f"{entry['strategy']}.tradeCount"] >= 0, "trade count")
    guide = strategies[1]
    require(all(round(point["value"] * scenario["backtest"]["initialCapital"]) == row["equity"] for point, row in zip(guide["curve"], scenario["backtest"]["daily"])), "guide ledger receipt")
    require(metric_cards["Strict.principleViolationCount"] == backtest["strictBlockedCandidateCount"], "strict veto count")
    monthly = {cell["month"]: cell["return"] for cell in backtest["heatmap"]}
    require(set(monthly) == {date[:7] for date in dates}, "month coverage")
    require(any(value != 0 for value in monthly.values()), "monthly returns left blank")

    model = report["modelEvaluation"]
    model_dates = [date for date in dates if date <= model["period"]["end"]]
    require(model["period"] == {"start": model_dates[0], "end": model_dates[-1]}, "model period")
    require(len(model["predictionSha256"]) == 64 and model["sourceRunIds"], "prediction provenance")
    require([row["modelId"] for row in model["models"]] == ["BASELINE", "LSTM"], "model comparison rows")
    universe = set(scenario["backtest"]["universe"])
    for entry in model["models"]:
        require(entry["status"] == "AVAILABLE", "model result missing")
        require([point["date"] for point in entry["daily"]] == model_dates, "model date coverage")
        require(all(point["equity"] > 0 and len(point["selectedSymbols"]) == 5 and len(set(point["selectedSymbols"])) == 5 and set(point["selectedSymbols"]) <= universe for point in entry["daily"]), "model position receipt")
        require(all(isinstance(value, (int, float)) and math.isfinite(value) for value in entry["metrics"].values()), "missing model metric")
    return {
        "reportSha256": file_sha256(report_path),
        "backtestDays": len(dates),
        "modelDays": len(model_dates),
        "strictBlockedCandidates": backtest["strictBlockedCandidateCount"],
        "modelRows": len(model["models"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("scenario", type=Path)
    args = parser.parse_args()
    print("MARS_DEMO_REPORT_VALID=" + json.dumps(verify(args.report, args.scenario), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
