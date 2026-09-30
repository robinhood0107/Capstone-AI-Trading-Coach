#!/usr/bin/env python3
"""Merge genuine prior-close RULE, LSTM and HMM signals into the DEMO image."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

SOURCE = "2026-09-29"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rsi14(closes: list[float]) -> float:
    changes = [closes[index] - closes[index - 1] for index in range(len(closes) - 14, len(closes))]
    gains = sum(max(change, 0.0) for change in changes) / 14
    losses = sum(max(-change, 0.0) for change in changes) / 14
    if gains == losses == 0:
        return 50.0
    if losses == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + gains / losses)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--calendar", type=Path, required=True)
    parser.add_argument("--lstm", type=Path, required=True)
    parser.add_argument("--hmm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
    calendar = json.loads(args.calendar.read_text(encoding="utf-8"))
    lstm = json.loads(args.lstm.read_text(encoding="utf-8"))
    hmm = json.loads(args.hmm.read_text(encoding="utf-8"))
    price_hash = sha256(args.prices)
    if any(value != price_hash for value in [scenario["source"]["sourceSha256"], lstm["sourcePriceSha256"], hmm["sourcePriceSha256"]]):
        raise ValueError("signal sources do not match the licensed scenario price receipt")
    dates = [row["date"] for row in calendar["sessions"]]
    if SOURCE not in dates:
        raise ValueError("signal source date is not a pinned KRX session")
    target = dates[dates.index(SOURCE) + 1]
    if lstm["targetSession"] != target or lstm["sourceSession"] != SOURCE or hmm["asOf"] != SOURCE:
        raise ValueError("signal target/source session mismatch")
    universe = sorted(scenario["backtest"]["universe"])
    lstm_rows = {row["symbol"]: row for row in lstm["rows"]}
    hmm_rows = {row["symbol"]: row for row in hmm["rows"]}
    if set(lstm_rows) != set(universe) or set(hmm_rows) != set(universe):
        raise ValueError("exact-31 signal coverage missing")
    prices = pd.read_parquet(args.prices)
    prices["Date"] = pd.to_datetime(prices["Date"]).dt.strftime("%Y-%m-%d")
    rows = []
    for symbol in universe:
        frame = prices[(prices.ticker == symbol) & (prices.Date <= SOURCE)]
        frame = frame.dropna(subset=["Close"]).sort_values("Date")
        if frame.empty or frame.Date.iloc[-1] != SOURCE or len(frame) < 39:
            raise ValueError(f"rule price history incomplete: {symbol}")
        closes = [float(close) for close in frame.Close]
        if any(not math.isfinite(close) or close <= 0 for close in closes[-200:]):
            raise ValueError(f"rule price input invalid: {symbol}")
        trend_window = min(200, len(closes))
        trend = sum(closes[-trend_window:]) / trend_window
        rsi = rsi14(closes)
        close = closes[-1]
        rule = "BUY" if close > trend and rsi < 70 else "SELL" if close < trend and rsi > 30 else "HOLD"
        model = lstm_rows[symbol]
        regime = hmm_rows[symbol]
        if model["status"] != "AVAILABLE" or regime["status"] != "AVAILABLE":
            raise ValueError(f"full signal coverage not achieved: {symbol}")
        # Same DEMO decision explanation as the existing rule BUY / LSTM veto
        # path. HMM is informational and does not authorize an order.
        combined = "BUY" if rule == "BUY" and model["signal"] != "SELL" else "SELL" if rule == "SELL" and model["signal"] == "SELL" else "HOLD"
        rows.append({
            "symbol": symbol,
            "rule": {"status": "AVAILABLE", "signal": rule, "sourceSession": SOURCE, "trendAverage": trend, "rsi14": rsi, "close": close, "trendWindow": trend_window},
            "lstm": model,
            "hmm": regime,
            "composite": {"status": "AVAILABLE", "signal": combined, "predictedReturn": model["predictedReturn"], "method": "RULE_BUY_AND_LSTM_NOT_SELL; RULE_SELL_AND_LSTM_SELL; HMM_INFORMATION_ONLY"},
        })
    output = {
        "schemaVersion": "mars-demo.signals.v1",
        "seedVersion": scenario["seedVersion"],
        "sourcePriceSha256": price_hash,
        "scenarioSha256": sha256(args.scenario),
        "calendarSha256": sha256(args.calendar),
        "lstmReceiptSha256": sha256(args.lstm),
        "hmmReceiptSha256": sha256(args.hmm),
        "sourceSession": SOURCE,
        "targetSession": target,
        "sourceAsOf": f"{SOURCE}T15:30:00+09:00",
        "method": "point-in-time prior-close educational signal projection; no live price or broker call; no automatic order authorization",
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    from collections import Counter
    print(json.dumps({"symbols": len(rows), "targetSession": target, "rule": dict(Counter(row["rule"]["signal"] for row in rows)), "lstm": dict(Counter(row["lstm"]["signal"] for row in rows)), "hmm": dict(Counter(row["hmm"]["state"] for row in rows))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
