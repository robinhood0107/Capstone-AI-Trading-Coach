#!/usr/bin/env python3
"""Validate exact-31 signal completeness, source timing and model receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("signals", type=Path)
    parser.add_argument("scenario", type=Path)
    parser.add_argument("calendar", type=Path)
    args = parser.parse_args()
    data = json.loads(args.signals.read_text(encoding="utf-8"))
    scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
    calendar = json.loads(args.calendar.read_text(encoding="utf-8"))
    require(data.get("schemaVersion") == "mars-demo.signals.v1", "signal schema")
    require(data.get("seedVersion") == scenario["seedVersion"], "scenario version")
    require(data.get("scenarioSha256") == sha256(args.scenario), "scenario hash")
    require(data.get("calendarSha256") == sha256(args.calendar), "calendar hash")
    require(data.get("sourcePriceSha256") == scenario["source"]["sourceSha256"], "licensed price source hash")
    require(len(data.get("lstmReceiptSha256", "")) == 64 and len(data.get("hmmReceiptSha256", "")) == 64, "model source receipts")
    dates = [row["date"] for row in calendar["sessions"]]
    source = data["sourceSession"]
    require(source in dates and data["targetSession"] == dates[dates.index(source) + 1], "target is not next pinned KRX session")
    require(data["sourceAsOf"] == f"{source}T15:30:00+09:00", "source timestamp")
    universe = sorted(scenario["backtest"]["universe"])
    rows = data["rows"]
    require([row["symbol"] for row in rows] == universe and len(rows) == 31, "exact-31 signal coverage")
    for row in rows:
        rule, lstm, hmm, combined = (row[key] for key in ("rule", "lstm", "hmm", "composite"))
        require(rule["status"] == lstm["status"] == hmm["status"] == combined["status"] == "AVAILABLE", "unavailable component")
        require(rule["sourceSession"] == lstm["sourceSession"] == hmm["asOf"] == source, "future or stale source session")
        require(lstm["targetSession"] == data["targetSession"], "LSTM target session")
        require(lstm["trainedThrough"] <= "2026-06-30" and lstm["trainedThrough"] < source, "LSTM training leakage")
        require(lstm["referenceError"] <= 1e-6 and len(lstm["modelHash"]) == 64, "LSTM reproduction receipt")
        predicted = lstm["predictedReturn"]
        require(math.isfinite(predicted) and abs(predicted) < 1, "LSTM output finite range")
        expected_lstm = "BUY" if predicted > 0.005 else "SELL" if predicted < -0.005 else "HOLD"
        require(lstm["signal"] == expected_lstm, "LSTM signal deadband")
        close, trend, rsi = (rule[key] for key in ("close", "trendAverage", "rsi14"))
        require(all(math.isfinite(number) for number in (close, trend, rsi)) and close > 0 and trend > 0 and 0 <= rsi <= 100, "rule input range")
        expected_rule = "BUY" if close > trend and rsi < 70 else "SELL" if close < trend and rsi > 30 else "HOLD"
        require(rule["signal"] == expected_rule, "rule signal reproducibility")
        posterior = hmm["posterior"]
        require(len(posterior) == 2 and all(math.isfinite(number) and 0 <= number <= 1 for number in posterior) and abs(sum(posterior) - 1) < 1e-8, "HMM posterior")
        require(hmm["state"] in {"RISK_ON", "RISK_OFF", "SIDEWAYS"} and len(hmm["artifactHash"]) == 64 and hmm["trainedThrough"] <= "2026-06-30", "HMM state receipt")
        expected_combined = "BUY" if expected_rule == "BUY" and expected_lstm != "SELL" else "SELL" if expected_rule == "SELL" and expected_lstm == "SELL" else "HOLD"
        require(combined["signal"] == expected_combined and combined["predictedReturn"] == predicted, "composite signal receipt")
    print("MARS_DEMO_SIGNALS_VALID=" + json.dumps({"count": len(rows), "source": source, "target": data["targetSession"], "fixtureSha256": sha256(args.signals)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
