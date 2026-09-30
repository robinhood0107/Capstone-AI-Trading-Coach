#!/usr/bin/env python3
"""Generate per-symbol HMM regimes from a train-only, causal research kernel.

Run with the decision-platform Python environment. The output is an intermediate
local receipt; merge_signal_fixture.py packages only the validated projection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from app.financial_engineering.hmm_regime import fit_hmm_regime

TRAIN_END = "2026-06-30"
SOURCE_END = "2026-09-29"
FIRST_ROW = "2023-01-01"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
    source_hash = sha256(args.prices)
    if source_hash != scenario["source"]["sourceSha256"]:
        raise ValueError("HMM price source differs from scenario receipt")
    universe = sorted(scenario["backtest"]["universe"])
    frame = pd.read_parquet(args.prices)
    frame["Date"] = pd.to_datetime(frame["Date"]).dt.strftime("%Y-%m-%d")
    rows = []
    for symbol in universe:
        history = frame[(frame.ticker == symbol) & frame.Date.between(FIRST_ROW, SOURCE_END)]
        history = history.dropna(subset=["Close"]).sort_values("Date")
        if history.empty or history.Date.iloc[-1] != SOURCE_END or bool((history.Close <= 0).any()):
            raise ValueError(f"HMM history incomplete: {symbol}")
        train_count = int((history.Date <= TRAIN_END).sum())
        # FEATURE_WINDOW=20 consumes 20 returns before the first feature.
        train_rows = train_count - 20
        if train_rows < 40 or train_rows >= len(history) - 20:
            raise ValueError(f"HMM training partition invalid: {symbol}")
        result = fit_hmm_regime(history.Close.to_numpy(dtype=float), train_rows=train_rows)
        if result.availability != "AVAILABLE" or result.artifact is None or not result.observations:
            rows.append({"symbol": symbol, "status": "ABSTAIN", "reason": "CALIBRATION_FAILED", "candidateFailures": list(result.candidate_failures)})
            continue
        latest = result.observations[-1]
        state = latest.state if latest.state is not None else "SIDEWAYS"
        rows.append({
            "symbol": symbol,
            "status": "AVAILABLE",
            "state": state,
            "posterior": list(latest.posterior),
            "confidence": latest.max_posterior,
            "warnings": list(latest.warnings),
            "selectedSeed": result.artifact.selected_seed,
            "artifactHash": result.artifact.artifact_hash,
            "trainRows": train_rows,
            "trainedThrough": TRAIN_END,
            "asOf": SOURCE_END,
        })
    output = {
        "schemaVersion": "mars-demo.hmm-intermediate.v1",
        "sourcePriceSha256": source_hash,
        "trainingEnd": TRAIN_END,
        "asOf": SOURCE_END,
        "method": "two-state GaussianHMM, five deterministic seeds, train-only scale, causal filtered posterior; uncertain posterior shown as SIDEWAYS",
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"symbols": len(rows), "available": sum(row["status"] == "AVAILABLE" for row in rows), "abstain": sum(row["status"] != "AVAILABLE" for row in rows), "sourcePriceSha256": source_hash}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
