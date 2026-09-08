"""Ridge arm 의 point-in-time walk-forward 예측 생성. (decision-platform venv)

## 왜 이것이 필요한가

`ridge_returns.fit_forecasts` 가 만든 모델의 `qualityStatus` 가 `COMPARISON_PENDING` 이다.
LSTM 은 22 fold walk-forward 로 `BELOW_BASELINE` 판정을 받았는데 Ridge 와 50:50 결합은
같은 잣대로 재 본 적이 없다. 화면이 "50:50 을 쓴다"고 말하면서 그 결합이 각 단독보다
나은지 모르는 상태였다.

## production 의 무엇을 그대로 쓰는가

추정기 정의를 재해석하지 않고 production 모듈에서 가져온다.

  * `ridge_returns.RIDGE_ALPHA` 상당(=1.0)과 `solver="svd"`, `StandardScaler`
  * 타깃: **단순수익률** `close[t+h]/close[t] - 1`. LSTM 하네스의 로그수익률과 다르다 -
    production 이 그렇게 학습하므로 여기서 바꾸지 않는다.
  * `MIN_TRAIN_ROWS`(= 2 x WINDOW_SIZE)와 `HORIZONS` 를 import 한다.
  * 추론은 `ridge_returns.predict` 를 그대로 호출한다 - 계수·scaler 를 JSON 왕복시킨 뒤
    production 함수로 예측하므로, 화면이 쓰는 것과 같은 산술이다.

## `fit_forecasts` 를 통째로 부르지 않은 이유

정직하게 적는다. `fit_forecasts` 는 첫 줄에서 이력의 세션이 XKRX 달력과 **빈틈없이 일치**할
것을 요구한다(`RIDGE_HISTORY_SESSION_GAP`). 그것은 운영에서 옳은 제약이다 - `trading_sessions`
가 채워진 구간만 학습하라는 뜻이다. 그런데 이 하네스의 이력은 21년치 yfinance 행이고
XKRX 달력과 행 단위로 일치하지 않는다(누락일·초과일이 섞인다). 그 검사를 만족시키려고
이력을 잘라 맞추면 검증 표본이 달력 정합성에 따라 달라진다.

그래서 **fit 절차만 같은 상수로 다시 쓰고, 추론은 production 함수를 그대로 부른다.** 두
경로가 같은 추정기인지는 `tests/p1_owner/test_ridge_returns.py` 의 대조 테스트가 지킨다 -
같은 입력에 두 경로의 예측이 같은지 본다.

## 실행

    cd workspaces/decision-platform/python-services
    uv run --frozen python ../research/p1-return-profit-verification/ridge_walk_forward.py

`walk_forward.py` 가 만든 `predictions.parquet` 의 (testYear, date, ticker) 키에 맞춰
`predRidgeRet` 열을 더한 `predictions_arms.parquet` 를 쓴다. provider 호출 0 - 이미 받아 둔
`long_history.parquet` 만 읽는다.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from app.p1_owner.ridge_returns import MIN_TRAIN_ROWS, predict

REPO = pathlib.Path("/home/pjjpj/projects/Capstone-AI-Trading-Coach")
CACHE = pathlib.Path("/tmp/p1exp")

# production 과 같은 정규화. `ridge_returns.fit_forecasts` 가 `Ridge(alpha=1.0, solver="svd")`
# 를 쓴다. 값을 여기서 정하지 않고 그 파일과 같은 값임을 테스트가 대조한다.
ALPHA = 1.0
SOLVER = "svd"
# LSTM 하네스와 같은 feature 정의를 쓴다. 이름만 production 표기로 옮긴 것이다
# (`app.p1_owner.assets.FEATURE_ORDER` = open/high/low/raw_close/volume/return_1d/ma5/ma20/rsi14).
FEATURES = ["Open", "High", "Low", "Close", "Volume", "Diff", "MA5", "MA20", "RSI"]
# 이 arm 이 평가하는 지평. 1일이 LSTM 과 같은 잣대이므로 비교의 축이다.
HORIZON = 1


def create_features(frame: pd.DataFrame) -> pd.DataFrame:
    """`walk_forward.py` 와 같은 정의. 두 arm 이 다른 feature 를 보면 비교가 성립하지 않는다."""

    out = frame.copy()
    out["Diff"] = out["Close"].pct_change()
    out["MA5"] = out["Close"].rolling(5).mean()
    out["MA20"] = out["Close"].rolling(20).mean()
    delta = out["Close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    out["RSI"] = 100.0 - (100.0 / (1.0 + gain / loss.replace(0.0, np.nan)))
    out["TargetSimpleRet"] = out["Close"].shift(-HORIZON) / out["Close"] - 1.0
    return out


def fit_and_predict(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray | None:
    """train 구간으로만 scaler 와 회귀를 fit 하고 test 각 행을 예측한다.

    누출 방지는 두 곳이다 - scaler 를 train 으로만 fit 하고, 타깃이 기준일까지 완성된 행만
    학습에 넣는다(마지막 `HORIZON` 행은 정답이 미래에 있으므로 제외된다).
    """

    if len(train) < MIN_TRAIN_ROWS + HORIZON or test.empty:
        return None
    x_train = train[FEATURES].to_numpy(dtype=float)[:-HORIZON]
    y_train = train["TargetSimpleRet"].to_numpy(dtype=float)[:-HORIZON]
    if not np.isfinite(x_train).all() or not np.isfinite(y_train).all():
        return None
    scaler = StandardScaler().fit(x_train)
    if (scaler.scale_ <= 0).any():
        return None
    regressor = Ridge(alpha=ALPHA, solver=SOLVER).fit(scaler.transform(x_train), y_train)
    # production 이 저장하는 것과 같은 JSON 모양으로 옮긴 뒤 production 추론 함수를 부른다.
    model = json.loads(
        json.dumps(
            {
                "mean": scaler.mean_.tolist(),
                "scale": scaler.scale_.tolist(),
                "coefficients": regressor.coef_.tolist(),
                "intercept": float(regressor.intercept_),
            }
        )
    )
    x_test = test[FEATURES].to_numpy(dtype=float)
    if not np.isfinite(x_test).all():
        return None
    return np.asarray([predict(model, row.tolist()) for row in x_test], dtype=float)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", default="predictions.parquet")
    parser.add_argument("--out", default="predictions_arms.parquet")
    args = parser.parse_args()

    catalog = json.loads((REPO / "contracts/catalogs/p1-return-universe.v1.json").read_text())
    tickers = [item["yfinanceTicker"] for item in catalog["symbols"]]
    history = pd.read_parquet(CACHE / "long_history.parquet")
    lstm = pd.read_parquet(CACHE / args.predictions)
    years = sorted(int(value) for value in lstm["testYear"].unique())

    rows: list[dict[str, object]] = []
    started = time.time()
    fitted = skipped = 0

    for test_year in years:
        train_start = pd.Timestamp(f"{test_year - 3}-01-01")
        train_end = pd.Timestamp(f"{test_year}-01-01")
        test_end = pd.Timestamp(f"{test_year + 1}-01-01")
        fold_symbols = 0
        for ticker in tickers:
            frame = history[history["ticker"] == ticker].copy()
            featured = create_features(frame).dropna(subset=[*FEATURES, "TargetSimpleRet"])
            train = featured[
                (featured["Date"] >= train_start) & (featured["Date"] < train_end)
            ].reset_index(drop=True)
            test = featured[
                (featured["Date"] >= train_end) & (featured["Date"] < test_end)
            ].reset_index(drop=True)
            predictions = fit_and_predict(train, test)
            if predictions is None:
                skipped += 1
                continue
            fitted += 1
            fold_symbols += 1
            for offset, value in enumerate(predictions):
                rows.append(
                    {
                        "testYear": test_year,
                        "date": test["Date"].iloc[offset],
                        "ticker": ticker,
                        "predRidgeRet": float(value),
                    }
                )
        print(f"  fold {test_year}: {fold_symbols:2d}종목", flush=True)

    ridge = pd.DataFrame(rows)
    merged = lstm.merge(ridge, on=["testYear", "date", "ticker"], how="inner")
    # 두 arm 의 단위를 맞춘다. LSTM 은 로그수익률, production Ridge 는 단순수익률이다.
    merged["predLstmRet"] = np.exp(merged["predLogRet"]) - 1.0
    # production 의 결합은 두 기대수익률의 산술평균이다(V134 의 EQUAL_WEIGHT_50_50).
    merged["predBlendRet"] = 0.5 * merged["predLstmRet"] + 0.5 * merged["predRidgeRet"]
    merged["actualSimpleRet"] = np.exp(merged["actualLogRet"]) - 1.0

    out = CACHE / args.out
    merged.to_parquet(out, index=False)
    print()
    print(f"저장: {out}")
    print(f"fit {fitted}회 / 건너뜀 {skipped}회")
    print(f"LSTM 예측 {len(lstm):,}행 / 두 arm 교집합 {len(merged):,}행")
    print(f"총 {time.time() - started:.0f}초 / providerCalls=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
