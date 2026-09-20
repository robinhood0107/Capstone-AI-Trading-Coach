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

from app.p1_owner.ridge_returns import MIN_TRAIN_ROWS, RidgeReturnError, predict

REPO = pathlib.Path("/home/pjjpj/projects/Capstone-AI-Trading-Coach")
CACHE = pathlib.Path("/tmp/p1exp")

# production 과 같은 정규화. `ridge_returns.fit_forecasts` 가 `Ridge(alpha=1.0, solver="svd")`
# 를 쓴다. 값을 여기서 정하지 않고 그 파일과 같은 값임을 테스트가 대조한다.
ALPHA = 1.0
SOLVER = "svd"
# LSTM 하네스와 같은 feature 정의를 쓴다. 이름만 production 표기로 옮긴 것이다
# (`app.p1_owner.assets.FEATURE_ORDER` = open/high/low/raw_close/volume/return_1d/ma5/ma20/rsi14).
FEATURES = ["Open", "High", "Low", "Close", "Volume", "Diff", "MA5", "MA20", "RSI"]
# production `ridge_returns.HORIZONS` 와 같은 셋이다. 운용은 지금 1일만 읽고 5·20일을
# 버린다 - 그 둘을 함께 만들어 "지평을 맞추면 달라지는가"를 재게 한다.
HORIZONS = (1, 5, 20)


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
    for horizon in HORIZONS:
        out[f"TargetSimpleRet{horizon}"] = out["Close"].shift(-horizon) / out["Close"] - 1.0
    return out


def fit_and_predict(train: pd.DataFrame, test: pd.DataFrame, horizon: int) -> np.ndarray | None:
    """train 구간으로만 scaler 와 회귀를 fit 하고 test 각 행을 예측한다.

    누출 방지는 두 곳이다 - scaler 를 train 으로만 fit 하고, 타깃이 기준일까지 완성된 행만
    학습에 넣는다(마지막 `HORIZON` 행은 정답이 미래에 있으므로 제외된다).
    """

    if len(train) < MIN_TRAIN_ROWS + horizon or test.empty:
        return None
    x_train = train[FEATURES].to_numpy(dtype=float)[:-horizon]
    y_train = train[f"TargetSimpleRet{horizon}"].to_numpy(dtype=float)[:-horizon]
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
    # production `predict` 의 출력 가드를 그대로 지난다 - `result <= -1` 이나 `> 1000` 은
    # 거부된다. 지평이 길어지면 선형 모델이 훈련 범위를 벗어나 그런 값을 내고, 실제로
    # 20일 지평에서 발화했다. 가드를 약화시키지 않고 그 행을 NaN 으로 두어 비교에서
    # 제외하고, 몇 건이 그랬는지는 호출부가 센다. 값을 clipping 으로 숨기지 않는다.
    values = np.empty(len(x_test), dtype=float)
    for index, row in enumerate(x_test):
        try:
            values[index] = predict(model, row.tolist())
        except RidgeReturnError:
            values[index] = np.nan
    return values


def mature_horizon_frame(featured: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """각 지평의 label maturity만 적용해 다른 지평의 표본을 침범하지 않는다."""

    if horizon not in HORIZONS:
        raise ValueError("unsupported horizon")
    return featured.dropna(subset=[*FEATURES, f"TargetSimpleRet{horizon}"])


def merge_arm_predictions(lstm: pd.DataFrame, ridge: pd.DataFrame) -> pd.DataFrame:
    """Ridge 한 arm의 거부가 LSTM이나 다른 Ridge 지평 행을 삭제하지 않는 left join이다."""

    merged = lstm.merge(ridge, on=["testYear", "date", "ticker"], how="left")
    merged["predLstmRet"] = np.exp(merged["predLogRet"]) - 1.0
    merged["predRidgeRet"] = merged["predRidgeRet1"]
    merged["predBlendRet"] = 0.5 * merged["predLstmRet"] + 0.5 * merged["predRidgeRet1"]
    merged["actualSimpleRet"] = np.exp(merged["actualLogRet"]) - 1.0
    return merged


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

    rows_by_key: dict[tuple[int, pd.Timestamp, str], dict[str, object]] = {}
    started = time.time()
    fitted_by_horizon = {horizon: 0 for horizon in HORIZONS}
    skipped_by_horizon = {horizon: 0 for horizon in HORIZONS}

    for test_year in years:
        train_start = pd.Timestamp(f"{test_year - 3}-01-01")
        train_end = pd.Timestamp(f"{test_year}-01-01")
        test_end = pd.Timestamp(f"{test_year + 1}-01-01")
        fold_symbols = 0
        for ticker in tickers:
            frame = history[history["ticker"] == ticker].copy()
            featured = create_features(frame)
            symbol_fitted = False
            for horizon in HORIZONS:
                # 각 지평은 자기 feature/label maturity로만 표본을 만든다. h20의 label이 없다는
                # 이유로 h1/h5의 완성된 행을 함께 버리지 않는다.
                horizon_frame = mature_horizon_frame(featured, horizon)
                train = horizon_frame[
                    (horizon_frame["Date"] >= train_start)
                    & (horizon_frame["Date"] < train_end)
                ].reset_index(drop=True)
                test = horizon_frame[
                    (horizon_frame["Date"] >= train_end)
                    & (horizon_frame["Date"] < test_end)
                ].reset_index(drop=True)
                values = fit_and_predict(train, test, horizon)
                if values is None:
                    skipped_by_horizon[horizon] += 1
                    continue
                fitted_by_horizon[horizon] += 1
                symbol_fitted = True
                for offset in range(len(test)):
                    key = (test_year, pd.Timestamp(test["Date"].iloc[offset]), ticker)
                    row = rows_by_key.setdefault(
                        key,
                        {"testYear": test_year, "date": key[1], "ticker": ticker},
                    )
                    row[f"predRidgeRet{horizon}"] = float(values[offset])
                    row[f"actualSimpleRet{horizon}"] = float(
                        test[f"TargetSimpleRet{horizon}"].iloc[offset]
                    )
            if symbol_fitted:
                fold_symbols += 1
        print(f"  fold {test_year}: {fold_symbols:2d}종목", flush=True)

    ridge = pd.DataFrame(rows_by_key.values())
    horizon_columns = [f"predRidgeRet{horizon}" for horizon in HORIZONS]
    rejected = {
        column: int(ridge[column].isna().sum()) if column in ridge else len(ridge)
        for column in horizon_columns
    }
    print()
    print(f"production predict 가드가 거부한 행: {rejected}")
    print("거부 행은 다른 지평/arm 행을 삭제하지 않고 NaN abstention으로 보존")
    merged = merge_arm_predictions(lstm, ridge)

    out = CACHE / args.out
    merged.to_parquet(out, index=False)
    print()
    print(f"저장: {out}")
    print(f"지평별 fit {fitted_by_horizon} / 건너뜀 {skipped_by_horizon}")
    print(f"LSTM 기준 {len(lstm):,}행 / left-joined arm 행 {len(merged):,}행")
    print(f"총 {time.time() - started:.0f}초 / providerCalls=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
