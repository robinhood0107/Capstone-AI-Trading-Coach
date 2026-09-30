#!/usr/bin/env python3
"""Reproduce the fixed Q3 LSTM and infer the next session for all DEMO symbols.

Run in the return-engine Torch environment. Training uses target labels through
2026-06-30 only. The saved September 18 predictions are checked before the
September 29 feature window is used for the September 30 forecast.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.preprocessing import StandardScaler

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "workspaces/decision-platform/research/p1-return-profit-verification"))
from walk_forward import BATCH, EPOCHS, FEATURES, LR, WINDOW, LSTMRegressor  # noqa: E402

TRAIN_END = "2026-06-30"
SOURCE_END = "2026-09-29"
PREVIOUS_REFERENCE_SOURCE = "2026-09-17"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def current_features(prices: pd.DataFrame, symbol: str, sessions: pd.DatetimeIndex) -> pd.DataFrame:
    raw = prices[prices.ticker == symbol].sort_values("Date").set_index("Date")
    frame = raw.reindex(sessions)
    valid = (frame[["Open", "High", "Low", "Close"]] > 0).all(axis=1) & frame.Volume.gt(0)
    frame.loc[~valid, ["Open", "High", "Low", "Close", "Volume"]] = np.nan
    frame["Diff"] = frame.Close.pct_change(fill_method=None)
    frame["MA5"] = frame.Close.rolling(5).mean()
    frame["MA20"] = frame.Close.rolling(20).mean()
    change = frame.Close.diff()
    gain = change.clip(lower=0).rolling(14).mean()
    loss = (-change.clip(upper=0)).rolling(14).mean()
    frame["RSI"] = 100 - 100 / (1 + gain / loss)
    frame.loc[(gain == 0) & (loss == 0), "RSI"] = 50
    return frame


def model_hash(model: LSTMRegressor) -> str:
    digest = hashlib.sha256()
    for name, tensor in sorted(model.state_dict().items()):
        digest.update(name.encode("ascii"))
        digest.update(tensor.detach().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--reference-predictions-root", type=Path, required=True)
    parser.add_argument("--calendar", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if sha256(args.prices) != scenario["source"]["sourceSha256"]:
        raise ValueError("LSTM price source differs from scenario receipt")
    if (WINDOW, EPOCHS, BATCH, LR) != (config["windowSize"], config["epochs"], config["batchSize"], config["learningRate"]):
        raise ValueError("LSTM hyperparameter contract changed")
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    prices = pd.read_parquet(args.prices)
    prices["Date"] = pd.to_datetime(prices["Date"]).dt.tz_localize(None)
    stored = pd.read_parquet(args.features)
    stored["sourceDate"] = pd.to_datetime(stored.sourceDate).dt.tz_localize(None)
    stored["targetDate"] = pd.to_datetime(stored.targetDate).dt.tz_localize(None)
    calendar = json.loads(args.calendar.read_text(encoding="utf-8"))
    dates = [row["date"] for row in calendar["sessions"]]
    if SOURCE_END not in dates:
        raise ValueError("latest source date missing from pinned KRX calendar")
    target = dates[dates.index(SOURCE_END) + 1]
    sessions = pd.DatetimeIndex(dates[:dates.index(SOURCE_END) + 1])
    universe = sorted(scenario["backtest"]["universe"])
    outputs = []
    combined_reference_hash = hashlib.sha256()
    maximum_reference_error = 0.0
    for symbol in universe:
        frame = stored[stored.ticker == symbol].sort_values("sourceDate").reset_index(drop=True)
        raw_features = frame[FEATURES].to_numpy(dtype=float)
        targets = np.log1p(frame.target.to_numpy(dtype=float))
        source_dates = frame.sourceDate.to_numpy()
        target_dates = frame.targetDate.to_numpy()
        windows = np.lib.stride_tricks.sliding_window_view(raw_features, (WINDOW, raw_features.shape[1]))[:, 0, :, :]
        endpoints = np.arange(WINDOW - 1, len(frame))
        valid_windows = np.isfinite(windows).all(axis=(1, 2))
        eligible = endpoints[
            valid_windows
            & np.isfinite(targets[endpoints])
            & (target_dates[endpoints] <= np.datetime64(TRAIN_END))
        ]
        training = eligible[-756:]
        if len(training) < 200 or str(frame.sourceDate.iloc[training[-1]].date()) >= TRAIN_END:
            raise ValueError(f"Q3 LSTM training partition invalid: {symbol}")
        x_scaler = StandardScaler().fit(raw_features[training])
        y_scaler = StandardScaler().fit(targets[training, None])
        training_x = x_scaler.transform(windows[training - WINDOW + 1].reshape(-1, raw_features.shape[1])).reshape(len(training), WINDOW, raw_features.shape[1]).astype("float32")
        training_y = y_scaler.transform(targets[training, None]).astype("float32")
        torch.manual_seed(config["seed"])
        model = LSTMRegressor()
        optimizer = torch.optim.Adam(model.parameters(), lr=LR)
        loss = torch.nn.SmoothL1Loss()
        model.train()
        x_tensor = torch.from_numpy(training_x)
        y_tensor = torch.from_numpy(training_y)
        for _ in range(EPOCHS):
            for offset in range(0, len(x_tensor), BATCH):
                optimizer.zero_grad()
                error = loss(model(x_tensor[offset:offset + BATCH]), y_tensor[offset:offset + BATCH])
                error.backward()
                optimizer.step()
        model.eval()

        latest_frame = current_features(prices, symbol, sessions)
        latest_window = latest_frame[FEATURES].tail(WINDOW).to_numpy(dtype=float)
        if latest_frame.index[-1].strftime("%Y-%m-%d") != SOURCE_END or not np.isfinite(latest_window).all():
            raise ValueError(f"latest LSTM feature window incomplete: {symbol}")
        overlap = latest_frame.loc[pd.Timestamp(PREVIOUS_REFERENCE_SOURCE), FEATURES].to_numpy(dtype=float)
        prior_index = int(frame.index[frame.sourceDate.eq(pd.Timestamp(PREVIOUS_REFERENCE_SOURCE))][0])
        if not np.allclose(overlap, raw_features[prior_index], atol=1e-8, rtol=1e-9):
            raise ValueError(f"new source feature construction differs from recorded Q3 features: {symbol}")
        reference_path = args.reference_predictions_root / f"{symbol}-2026.parquet"
        combined_reference_hash.update(bytes.fromhex(sha256(reference_path)))
        reference_rows = pd.read_parquet(reference_path)
        reference = reference_rows[reference_rows.sourceDate.eq(pd.Timestamp(PREVIOUS_REFERENCE_SOURCE))]
        if len(reference) != 1:
            raise ValueError(f"recorded LSTM reference prediction missing: {symbol}")
        with torch.no_grad():
            previous_window = x_scaler.transform(windows[prior_index - WINDOW + 1].reshape(-1, raw_features.shape[1])).reshape(1, WINDOW, raw_features.shape[1]).astype("float32")
            previous_prediction = float(np.expm1(y_scaler.inverse_transform(model(torch.from_numpy(previous_window)).numpy()).ravel()[0]))
            scaled_window = x_scaler.transform(latest_window).reshape(1, WINDOW, len(FEATURES)).astype("float32")
            predicted_return = float(np.expm1(y_scaler.inverse_transform(model(torch.from_numpy(scaled_window)).numpy()).ravel()[0]))
        reference_error = abs(previous_prediction - float(reference.prediction.iloc[0]))
        maximum_reference_error = max(maximum_reference_error, reference_error)
        if reference_error > 1e-6 or not math.isfinite(predicted_return) or abs(predicted_return) > 1:
            raise ValueError(f"LSTM reproduction or forecast invalid: {symbol} reference_error={reference_error}")
        signal = "BUY" if predicted_return > 0.005 else "SELL" if predicted_return < -0.005 else "HOLD"
        outputs.append({
            "symbol": symbol,
            "status": "AVAILABLE",
            "signal": signal,
            "predictedReturn": predicted_return,
            "sourceSession": SOURCE_END,
            "targetSession": target,
            "trainedThrough": str(reference.trainedThrough.iloc[0])[:10],
            "trainSamples": len(training),
            "modelHash": model_hash(model),
            "referenceError": reference_error,
        })
        print(f"SIGNAL_LSTM {symbol} {signal} source={SOURCE_END} target={target}", flush=True)
    output = {
        "schemaVersion": "mars-demo.lstm-intermediate.v1",
        "sourcePriceSha256": scenario["source"]["sourceSha256"],
        "featuresSha256": sha256(args.features),
        "referencePredictionsSha256": combined_reference_hash.hexdigest(),
        "configSha256": sha256(args.config),
        "sourceSession": SOURCE_END,
        "targetSession": target,
        "method": "same fixed w756-quarterly LSTM architecture and Q3 training partition; train-only scalers; deterministic seed; signed next-session return deadband ±0.5%",
        "maximumReferenceError": maximum_reference_error,
        "rows": outputs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"symbols": len(outputs), "maximumReferenceError": maximum_reference_error, "sourcePriceSha256": output["sourcePriceSha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
