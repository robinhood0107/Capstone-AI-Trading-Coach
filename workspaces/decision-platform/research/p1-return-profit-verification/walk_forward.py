"""Phase 5-A 1단계: point-in-time walk-forward LSTM 예측 생성. (return-engine venv)

계약 설정을 그대로 쓴다 - feature 9개, window 20, hidden 64, layer 1, dropout 0,
Adam lr 0.001, SmoothL1, batch 32, epochs 10, seed 0, per-symbol 독립.
타깃만 절대가에서 로그수익률로 바꾼다 (계획 결정 #9).

파라미터 탐색을 하지 않는다 - 요청서의 hyperparameterSearchCount=0 을 지킨다.
"""

import argparse
import json
import pathlib
import time

import numpy as np
import pandas as pd
import torch
from sklearn.preprocessing import StandardScaler
from torch import nn

REPO = pathlib.Path("/home/pjjpj/projects/Capstone-AI-Trading-Coach")
CACHE = pathlib.Path("/tmp/p1exp")

WINDOW = 20
HIDDEN = 64
LAYERS = 1
DROPOUT = 0.0
LR = 0.001
EPOCHS = 10
BATCH = 32
SEED = 0
FEATURES = ["Open", "High", "Low", "Close", "Volume", "Diff", "MA5", "MA20", "RSI"]
MIN_TRAIN_ROWS = 300  # window+시퀀스 확보. 계약 minimumHistory 756 세션의 train 몫보다 작다.


class LSTMRegressor(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=len(FEATURES),
            hidden_size=HIDDEN,
            num_layers=LAYERS,
            dropout=DROPOUT,
            batch_first=True,
        )
        self.head = nn.Linear(HIDDEN, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        return self.head(out[:, -1, :])


def create_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Team B create_features 와 같은 정의. RSI 는 14일 Wilder 없는 단순 이동평균 비율."""
    out = frame.copy()
    out["Diff"] = out["Close"].pct_change()
    out["MA5"] = out["Close"].rolling(5).mean()
    out["MA20"] = out["Close"].rolling(20).mean()
    delta = out["Close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    out["RSI"] = 100 - (100 / (1 + gain / loss))
    # 타깃: 다음 거래일 로그수익률
    out["LogRet"] = np.log(out["Close"] / out["Close"].shift(1))
    out["TargetLogRet"] = out["LogRet"].shift(-1)
    return out


def sequences(
    features: np.ndarray, target: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    xs, ys = [], []
    for i in range(len(features) - WINDOW + 1):
        xs.append(features[i : i + WINDOW])
        ys.append(target[i + WINDOW - 1])
    if not xs:
        return np.empty((0, WINDOW, len(FEATURES))), np.empty((0,))
    return np.asarray(xs, dtype=np.float32), np.asarray(ys, dtype=np.float32)


def train_and_predict(
    train: pd.DataFrame, test: pd.DataFrame
) -> np.ndarray | None:
    """train 구간으로만 scaler fit + 학습하고 test 각 행의 다음날 로그수익률을 예측한다."""
    if len(train) < MIN_TRAIN_ROWS or test.empty:
        return None

    x_scaler = StandardScaler().fit(train[FEATURES].to_numpy())
    y_scaler = StandardScaler().fit(train[["TargetLogRet"]].to_numpy())

    x_train = x_scaler.transform(train[FEATURES].to_numpy())
    y_train = y_scaler.transform(train[["TargetLogRet"]].to_numpy()).ravel()
    xs, ys = sequences(x_train, y_train)
    if len(xs) == 0:
        return None

    torch.manual_seed(SEED)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(1)
    model = LSTMRegressor()
    optimizer = torch.optim.Adam(model.parameters(), lr=LR)
    loss_fn = nn.SmoothL1Loss()

    x_t = torch.from_numpy(xs)
    y_t = torch.from_numpy(ys).unsqueeze(1)
    model.train()
    for _ in range(EPOCHS):
        for start in range(0, len(x_t), BATCH):
            optimizer.zero_grad()
            pred = model(x_t[start : start + BATCH])
            loss = loss_fn(pred, y_t[start : start + BATCH])
            loss.backward()
            optimizer.step()

    # test 예측: 각 test 행에서 끝나는 window 를 만든다. window 는 train 끝부분을 포함할 수 있다.
    combined = pd.concat([train.tail(WINDOW - 1), test], ignore_index=True)
    x_test = x_scaler.transform(combined[FEATURES].to_numpy())
    xs_test, _ = sequences(x_test, np.zeros(len(x_test)))
    if len(xs_test) != len(test):
        return None

    model.eval()
    with torch.no_grad():
        scaled = model(torch.from_numpy(xs_test)).numpy()
    return y_scaler.inverse_transform(scaled).ravel()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-folds", type=int, default=0)
    parser.add_argument("--limit-symbols", type=int, default=0)
    parser.add_argument("--first-test-year", type=int, default=2005)
    parser.add_argument("--out", default="predictions.parquet")
    args = parser.parse_args()

    catalog = json.loads(
        (REPO / "contracts/catalogs/p1-return-universe.v1.json").read_text()
    )
    tickers = [item["yfinanceTicker"] for item in catalog["symbols"]]
    if args.limit_symbols:
        tickers = tickers[: args.limit_symbols]

    history = pd.read_parquet(CACHE / "long_history.parquet")
    last_year = int(history["Date"].max().year)
    years = list(range(args.first_test_year, last_year + 1))
    if args.limit_folds:
        years = years[: args.limit_folds]

    rows: list[dict[str, object]] = []
    started = time.time()
    trained = skipped = 0

    for test_year in years:
        train_start = pd.Timestamp(f"{test_year - 3}-01-01")
        train_end = pd.Timestamp(f"{test_year}-01-01")
        test_end = pd.Timestamp(f"{test_year + 1}-01-01")
        fold_started = time.time()
        fold_symbols = 0

        for ticker in tickers:
            frame = history[history["ticker"] == ticker].copy()
            featured = create_features(frame).dropna(
                subset=[*FEATURES, "TargetLogRet"]
            )
            train = featured[
                (featured["Date"] >= train_start) & (featured["Date"] < train_end)
            ].reset_index(drop=True)
            test = featured[
                (featured["Date"] >= train_end) & (featured["Date"] < test_end)
            ].reset_index(drop=True)

            predictions = train_and_predict(train, test)
            if predictions is None:
                skipped += 1
                continue
            trained += 1
            fold_symbols += 1
            for offset, prediction in enumerate(predictions):
                rows.append(
                    {
                        "testYear": test_year,
                        "date": test["Date"].iloc[offset],
                        "ticker": ticker,
                        "predLogRet": float(prediction),
                        "actualLogRet": float(test["TargetLogRet"].iloc[offset]),
                        "close": float(test["Close"].iloc[offset]),
                    }
                )

        print(
            f"  fold {test_year}: {fold_symbols:2d}종목 "
            f"{time.time() - fold_started:6.1f}초 (누적 {time.time() - started:6.1f}초)",
            flush=True,
        )

    out = CACHE / args.out
    pd.DataFrame(rows).to_parquet(out, index=False)
    print()
    print(f"저장: {out}")
    print(f"학습 {trained}회 / 건너뜀 {skipped}회 / 예측 {len(rows):,}행")
    print(f"총 {time.time() - started:.0f}초")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
