"""사전 등록된 v3 protocol의 LSTM 5/20일 후보만 새 파일로 생성한다."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.preprocessing import StandardScaler
from torch import nn

from walk_forward import (
    BATCH,
    EPOCHS,
    FEATURES,
    LR,
    MIN_TRAIN_ROWS,
    REPO,
    SEED,
    WINDOW,
    LSTMRegressor,
    create_features,
    sequences,
)

CACHE = Path("/tmp/p1exp")
HORIZONS = (5, 20)


def horizon_frame(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """정답 session과 누적 로그수익률을 명시해 purge/maturity를 날짜로 검증한다."""

    if horizon not in HORIZONS:
        raise ValueError("unsupported LSTM horizon")
    out = create_features(frame)
    out[f"TargetLogRet{horizon}"] = np.log(out["Close"].shift(-horizon) / out["Close"])
    out[f"TargetDate{horizon}"] = out["Date"].shift(-horizon)
    return out


def train_and_predict(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    target_column: str,
) -> np.ndarray | None:
    if len(train) < MIN_TRAIN_ROWS or test.empty:
        return None
    x_scaler = StandardScaler().fit(train[FEATURES].to_numpy(dtype=float))
    y_scaler = StandardScaler().fit(train[[target_column]].to_numpy(dtype=float))
    x_train = x_scaler.transform(train[FEATURES].to_numpy(dtype=float))
    y_train = y_scaler.transform(train[[target_column]].to_numpy(dtype=float)).ravel()
    xs, ys = sequences(x_train, y_train)
    if len(xs) == 0:
        return None
    torch.manual_seed(SEED)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(1)
    model: nn.Module = LSTMRegressor()
    optimizer = torch.optim.Adam(model.parameters(), lr=LR)
    loss_fn = nn.SmoothL1Loss()
    x_tensor = torch.from_numpy(xs)
    y_tensor = torch.from_numpy(ys).unsqueeze(1)
    model.train()
    for _ in range(EPOCHS):
        for start in range(0, len(x_tensor), BATCH):
            optimizer.zero_grad()
            loss = loss_fn(model(x_tensor[start : start + BATCH]), y_tensor[start : start + BATCH])
            loss.backward()
            optimizer.step()
    combined = pd.concat([train.tail(WINDOW - 1), test], ignore_index=True)
    x_test = x_scaler.transform(combined[FEATURES].to_numpy(dtype=float))
    xs_test, _ = sequences(x_test, np.zeros(len(x_test)))
    if len(xs_test) != len(test):
        return None
    model.eval()
    with torch.no_grad():
        scaled = model(torch.from_numpy(xs_test)).numpy()
    return y_scaler.inverse_transform(scaled).ravel()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="predictions_lstm_h5_h20.v3.parquet")
    parser.add_argument("--limit-folds", type=int, default=0)
    parser.add_argument("--limit-symbols", type=int, default=0)
    args = parser.parse_args()
    protocol = json.loads(
        (Path(__file__).parent / "reports" / "evaluation-protocol.v3.json").read_text()
    )
    if protocol["multipleTesting"]["newTrainingRunBudget"] != len(HORIZONS):
        raise ValueError("LSTM v3 trial budget drift")
    catalog = json.loads((REPO / "contracts/catalogs/p1-return-universe.v1.json").read_text())
    tickers = [item["yfinanceTicker"] for item in catalog["symbols"]]
    if args.limit_symbols:
        tickers = tickers[: args.limit_symbols]
    history = pd.read_parquet(CACHE / "long_history.parquet")
    years = list(range(2005, int(history["Date"].max().year) + 1))
    if args.limit_folds:
        years = years[: args.limit_folds]
    rows: list[dict[str, object]] = []
    trained = skipped = 0
    for test_year in years:
        train_start = pd.Timestamp(f"{test_year - 3}-01-01")
        train_end = pd.Timestamp(f"{test_year}-01-01")
        test_end = pd.Timestamp(f"{test_year + 1}-01-01")
        fold_started = time.monotonic()
        for ticker in tickers:
            source = history[history["ticker"] == ticker].copy()
            for horizon in HORIZONS:
                target = f"TargetLogRet{horizon}"
                target_date = f"TargetDate{horizon}"
                featured = horizon_frame(source, horizon).dropna(
                    subset=[*FEATURES, target, target_date]
                )
                train = featured[
                    (featured["Date"] >= train_start)
                    & (featured["Date"] < train_end)
                    & (featured[target_date] < train_end)
                ].reset_index(drop=True)
                test = featured[
                    (featured["Date"] >= train_end)
                    & (featured["Date"] < test_end)
                    & (featured[target_date] < test_end)
                ].reset_index(drop=True)
                predictions = train_and_predict(train, test, target_column=target)
                if predictions is None:
                    skipped += 1
                    continue
                trained += 1
                for offset, prediction in enumerate(predictions):
                    rows.append(
                        {
                            "actualSimpleReturn": float(np.exp(test[target].iloc[offset]) - 1),
                            "date": test["Date"].iloc[offset],
                            "horizonSessions": horizon,
                            "predictionSimpleReturn": float(np.exp(prediction) - 1),
                            "targetDate": test[target_date].iloc[offset],
                            "testYear": test_year,
                            "ticker": ticker,
                        }
                    )
        print(
            f"fold={test_year} elapsedSeconds={time.monotonic() - fold_started:.1f}",
            flush=True,
        )
    output = CACHE / args.out
    pd.DataFrame(rows).to_parquet(output, index=False)
    print(
        json.dumps(
            {
                "output": str(output),
                "predictionRows": len(rows),
                "providerCalls": 0,
                "skippedFits": skipped,
                "trainedFits": trained,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
