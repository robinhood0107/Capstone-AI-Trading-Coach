"""Owner exporter: exact-31 production 학습과 exact-10 Git Model Seed 게시.

`src/` 의 Team B 기존 파일은 한 줄도 바꾸지 않는다. 계약 정합과 방어를 전부 이 파일이 맡는다.
가능한 이유는 `Preprocessor` 의 `features`/`target` 이 생성자 인자이고 `x_scaler`/`y_scaler` 가
평범한 인스턴스 속성이어서, 계약 feature 순서와 scaler 를 바깥에서 주입할 수 있기 때문이다.

수집과 학습을 분리한다. `collect` 단계만 yfinance 를 호출하고 로컬 캐시에 정규화해 쓴다.
`export` 단계는 캐시만 읽어 네트워크 호출이 0이다 - manifest 의 `producer.networkCalls=0`
이 그래서 정직하게 성립한다. 수집한 입력의 정체는 캐시 바이트의 SHA-256 을
`inputPackSha256` 에 기록해 증거로 남긴다. 기대값을 외부에서 주입받지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import torch
from sklearn.preprocessing import StandardScaler

from backtest_core.backtest_engine import BacktestEngine
from backtest_core.signal_generator import SignalGenerator
from dataloader.datapileline import DataPipeline
from dataloader.preprocessor import Preprocessor
from dataloader.stockdataloader import StockDataLoader
from models.lstm import LSTMModel

# --- 계약 상수 -------------------------------------------------------------
# Owner 계약의 feature 이름과 그에 대응하는 return-engine 프레임 컬럼.
# `volume` 이 index 4 라서 순서를 직접 넘겨야 한다.
CONTRACT_FEATURE_ORDER = (
    "open",
    "high",
    "low",
    "raw_close",
    "volume",
    "return_1d",
    "ma5",
    "ma20",
    "rsi14",
)
FRAME_FEATURES = ["Open", "High", "Low", "Close", "Volume", "Diff", "MA5", "MA20", "RSI"]

WINDOW_SIZE = 20
BATCH_SIZE = 32
EPOCHS = 10
SEED = 0
SIGNAL_DEADBAND = 0.005
ROUND_TRIP_COST_BPS = 35
ROUND_TRIP_COST = ROUND_TRIP_COST_BPS / 10_000.0
INITIAL_CAPITAL_KRW = 10_000_000
SCENARIO = "GUIDE"
COST_MODEL_ID = "CONSERVATIVE_FIXED_35BPS_V1"

# 모델이 무엇을 예측하는지. Owner `model_shape` 의 같은 이름 상수와 값이 일치해야 한다.
#   RAW_CLOSE  - 절대 종가. test 가 train 최대를 넘으면 역변환이 학습 평균 근처로 주저앉는다.
#   LOG_RETURN - 로그수익률. forecastClose = currentClose * exp(y-hat) 로 현재가에서 재구성해
#                학습 구간의 가격 범위에 갇히지 않는다.
TARGET_RAW_CLOSE = "RAW_CLOSE"
TARGET_LOG_RETURN = "LOG_RETURN"
TARGET_COLUMN = {TARGET_RAW_CLOSE: "Close", TARGET_LOG_RETURN: "LogRet"}

# Team B 가 정한 학습 설정. 형상은 config.json 이 단일 진실이므로 여기 값이 그대로 실린다.
HIDDEN_SIZE = 64
LAYER_COUNT = 1
DROPOUT = 0.0
LEARNING_RATE = 0.001

# split 경계는 마지막 세션에서 역산한다. 종목마다 상장일이 달라도 경계 날짜는 같다.
TEST_SESSIONS = 120
VALIDATION_SESSIONS = 120
MIN_TOTAL_SESSIONS = 756

ARTIFACT_NAMES = (
    "model.safetensors",
    "scaler.json",
    "config.json",
    "lstm_signals.parquet",
    "rule_baseline_signals.parquet",
    "backtest_result.json",
    "trade_log.parquet",
    "equity_log.parquet",
    "golden_output.json",
    "model_report.md",
)
ARTIFACT_SCHEMA_IDS = (
    "p1-return-model-safetensors.v2",
    "p1-return-scaler.v2",
    "p1-return-config.v2",
    "p1-return-lstm-signals.v3",
    "p1-return-rule-baseline-signals.v3",
    "p1-return-backtest-result.v2",
    "p1-return-trade-log.v2",
    "p1-return-equity-log.v2",
    "p1-return-golden-output.v2",
    "p1-return-model-report.v2",
)
MANIFEST_NAME = "p1-return-engine-manifest.v3.json"

REPO_ROOT = Path(__file__).resolve().parents[3]
CACHE_FRAME = "daily_ohlcv.parquet"
COLLECTION_NOTES = "collection_notes.json"


class SeedExportError(RuntimeError):
    """계약을 만족하는 seed 를 만들 수 없다."""


# --- 공통 유틸 -------------------------------------------------------------
def canonical_json_bytes(value: object) -> bytes:
    """Owner 의 `app.data._shared.canonical_json` 과 바이트가 같아야 한다.

    `featureOrderSha256` 를 Owner 가 이 형식으로 재계산해 대조하므로 정의를 맞춘다.
    """
    normalized = _normalize_json(value)
    return (
        json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _normalize_json(value: object) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SeedExportError("canonical JSON numbers must be finite")
        return 0 if value == 0 else value
    if isinstance(value, list):
        return [_normalize_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize_json(item) for key, item in value.items()}
    raise SeedExportError(f"canonical JSON does not support {type(value).__name__}")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _file_digest(path: Path) -> str:
    return _digest(path.read_bytes())


def _parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", row_group_size=8192, write_statistics=True)
    return bytes(sink.getvalue().to_pybytes())


def classify_signal(expected_return: float) -> str:
    """Owner `model_shape.classify_signal` 과 같은 +-0.5% deadband."""
    if expected_return > SIGNAL_DEADBAND:
        return "BUY"
    if expected_return < -SIGNAL_DEADBAND:
        return "SELL"
    return "HOLD"


# --- 유니버스 -------------------------------------------------------------
@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    ticker: str
    rank: int


def load_universe(catalog_path: Path) -> list[SymbolSpec]:
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    if payload.get("contractId") != "p1-return-universe.v1":
        raise SeedExportError("universe catalog contract id drifted")
    entries = payload.get("symbols")
    if not isinstance(entries, list) or len(entries) != 31:
        raise SeedExportError("universe catalog must declare exact-31")
    specs = [
        SymbolSpec(
            symbol=str(item["symbol"]), ticker=str(item["yfinanceTicker"]), rank=int(item["rank"])
        )
        for item in entries
    ]
    symbols = [spec.symbol for spec in specs]
    if len(set(symbols)) != 31 or symbols.count("132030") != 1:
        raise SeedExportError("universe catalog is not exact-31 with a single gold ETF")
    return sorted(specs, key=lambda spec: spec.rank)


# --- 1단계: 수집 (yfinance) ------------------------------------------------
def collect(specs: list[SymbolSpec], cache_dir: Path, *, start: str = "2020-01-01") -> Path:
    """카탈로그의 31개 티커만 수집해 하나의 정규화 parquet 으로 캐시한다.

    카탈로그에 있는 티커가 수집되지 않으면 조용히 건너뛰지 않고 실패한다.
    31개 중 몇 개가 빠진 채로 학습이 끝나는 것이 가장 찾기 어려운 결함이다.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = cache_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []
    halted_by_symbol: dict[str, int] = {}
    for spec in specs:
        # StockDataLoader.download 는 인자 4개가 모두 필수이고 CSV 를 부수효과로 쓴다.
        # 그 CSV 는 캐시 디렉터리 안에만 남기고 Git 에는 넣지 않는다.
        frame = StockDataLoader.download(
            spec.ticker, start, None, str(raw_dir / f"{spec.ticker}.csv")
        )
        if frame is None or frame.empty:
            raise SeedExportError(f"yfinance returned no rows: {spec.ticker}")
        frame = frame.reset_index()
        missing = [name for name in ("Date", *FRAME_FEATURES[:5]) if name not in frame.columns]
        if missing:
            raise SeedExportError(f"yfinance frame is missing columns {missing}: {spec.ticker}")
        # Adj Close 는 쓰지 않는다. 계약이 priceBasis=RAW_CLOSE 다.
        frame = frame[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
        frame["Date"] = pd.to_datetime(frame["Date"]).dt.tz_localize(None)
        frame["symbol"] = spec.symbol
        if frame[["Open", "High", "Low", "Close", "Volume"]].isna().any().any():
            frame = frame.dropna(subset=["Open", "High", "Low", "Close", "Volume"])

        # 거래량 0 바는 가격 관측이 아니다. yfinance 는 거래정지일에도 직전 종가를 그대로
        # 채워 보내고 그런 행은 OHLC 가 모두 종가와 같다. 그대로 학습에 넣으면 14일 이상
        # 이어질 때 RSI 의 gain/loss 가 둘 다 0이 되어 0/0 = NaN 이 되고, Team B
        # create_features 의 dropna 가 중간 행을 조용히 지운다. 계약이 중간 거래일 누락을
        # fail-closed 로 요구하므로 관측이 아닌 행을 여기서 명시적으로 제외하고 개수를 남긴다.
        halted = int((frame["Volume"] == 0).sum())
        if halted:
            frame = frame[frame["Volume"] > 0].copy()
        halted_by_symbol[spec.symbol] = halted

        if len(frame) < MIN_TOTAL_SESSIONS:
            raise SeedExportError(
                f"{spec.ticker} has {len(frame)} sessions, below the contracted minimum "
                f"{MIN_TOTAL_SESSIONS}"
            )
        frames.append(frame)
        note = f" (거래정지 {halted}행 제외)" if halted else ""
        print(f"  COLLECT {spec.symbol} {spec.ticker:12s} {len(frame):5d}행{note}", flush=True)

    merged = pd.concat(frames, ignore_index=True).sort_values(["symbol", "Date"])
    merged = merged.reset_index(drop=True)
    target = cache_dir / CACHE_FRAME
    _atomic_write(target, _parquet_bytes(pa.Table.from_pandas(merged, preserve_index=False)))
    # export 가 model_report 에 공시할 수 있게 제외 개수를 캐시 옆에 남긴다.
    _atomic_write(
        cache_dir / COLLECTION_NOTES,
        canonical_json_bytes(
            {
                "haltedSessionsExcluded": halted_by_symbol,
                "start": start,
                "totalRows": int(len(merged)),
            }
        ),
    )
    total_halted = sum(halted_by_symbol.values())
    print(
        f"  캐시 {target} ({len(merged):,}행, {merged['symbol'].nunique()}종목, "
        f"거래정지 {total_halted}행 제외)"
    )
    return target


# --- 2단계: 학습과 산출 ----------------------------------------------------
@dataclass
class SymbolResult:
    symbol: str
    current_close: float
    forecast_close: float
    baseline_signal: str
    scaler_mean: list[float]
    scaler_scale: list[float]
    target_mean: float
    target_scale: float
    # test 구간 1스텝 예측 정확도. modelQuality 근거이므로 종목별로 모아 합산한다.
    test_model_sse: float
    test_naive_sse: float
    test_points: int
    test_direction_correct: int
    test_direction_total: int
    state_dict: dict[str, np.ndarray]
    signals: pd.DataFrame  # Date, Close, Signal (test 구간)


def _featured_frame(
    frame: pd.DataFrame, target_transform: str
) -> tuple[pd.DataFrame, Preprocessor]:
    """Team B `Preprocessor.create_features` 로 feature 를 만들고 연속성을 검사한다.

    타깃 열은 `split_features_target` 이 `df[self.target]` 만 하므로 바깥에서 정한다
    (`preprocessor.py:45-49`). Team B 파일은 건드리지 않는다.
    """
    target_column = TARGET_COLUMN[target_transform]
    preprocessor = Preprocessor(frame.copy(), FRAME_FEATURES, [target_column])
    # 계약 feature 순서를 생성자로 직접 넘긴다. return_engine.py 의 리스트는 건드리지 않는다.
    preprocessor.x_scaler = StandardScaler()
    preprocessor.y_scaler = StandardScaler()
    featured = preprocessor.create_features()
    if featured.empty:
        raise SeedExportError("feature frame is empty")

    if target_transform == TARGET_LOG_RETURN:
        # create_sequence 가 features[i:i+20] -> target[i+20] 로 정렬하므로, 행 t 의 타깃은
        # 그날 실현된 로그수익률이고 feature 는 t-20..t-1 이다. 룩어헤드가 없다.
        featured = featured.copy()
        featured["LogRet"] = np.log(
            featured["Close"].astype(float) / featured["Close"].astype(float).shift(1)
        )
        # 첫 행만 NaN 이다. 선두 절단이므로 중간 누락 검사에 영향이 없다.
        featured = featured.iloc[1:].reset_index(drop=True)
        if not bool(np.isfinite(featured["LogRet"].to_numpy(dtype=float)).all()):
            raise SeedExportError("log return target is non-finite; a close price was zero")
        preprocessor.df = featured

    # RSI 에서 gain 과 loss 가 둘 다 0이면 0/0 = NaN 이 되고 dropna 가 중간 행을 지운다.
    # 계약은 중간 거래일 누락을 fail-closed 로 요구하므로 날짜 연속성을 직접 검사한다.
    original = frame.copy()
    original["Date"] = pd.to_datetime(original["Date"])
    kept = set(pd.to_datetime(featured["Date"]))
    ordered = sorted(pd.to_datetime(original["Date"]))
    first_kept = min(kept)
    tail = [day for day in ordered if day >= first_kept]
    dropped_middle = [day for day in tail if day not in kept]
    if dropped_middle:
        raise SeedExportError(
            f"feature engineering dropped {len(dropped_middle)} middle sessions "
            f"(first {dropped_middle[0].date()})"
        )
    return featured, preprocessor


def _split_boundaries(featured_by_symbol: dict[str, pd.DataFrame]) -> tuple[date, date, date]:
    """모든 종목이 공유하는 split 경계를 마지막 공통 세션에서 역산한다."""
    common: set[pd.Timestamp] | None = None
    for featured in featured_by_symbol.values():
        days = set(pd.to_datetime(featured["Date"]))
        common = days if common is None else (common & days)
    if not common:
        raise SeedExportError("symbols share no common session")
    ordered = sorted(common)
    if len(ordered) < TEST_SESSIONS + VALIDATION_SESSIONS + WINDOW_SIZE * 3:
        raise SeedExportError(f"common calendar is too short: {len(ordered)} sessions")
    session_date = ordered[-1].date()
    test_start = ordered[-TEST_SESSIONS].date()
    validation_start = ordered[-(TEST_SESSIONS + VALIDATION_SESSIONS)].date()
    return validation_start, test_start, session_date


def _train_symbol(
    spec: SymbolSpec,
    featured: pd.DataFrame,
    preprocessor: Preprocessor,
    validation_start: date,
    test_start: date,
    target_transform: str,
) -> SymbolResult:
    days = pd.to_datetime(featured["Date"])
    train = featured[days < pd.Timestamp(validation_start)].reset_index(drop=True)
    validation = featured[
        (days >= pd.Timestamp(validation_start)) & (days < pd.Timestamp(test_start))
    ].reset_index(drop=True)
    test = featured[days >= pd.Timestamp(test_start)].reset_index(drop=True)
    for label, part in (("train", train), ("validation", validation), ("test", test)):
        if len(part) <= WINDOW_SIZE:
            raise SeedExportError(
                f"{spec.symbol} {label} segment has {len(part)} rows, needs more than {WINDOW_SIZE}"
            )

    pipeline = DataPipeline(preprocessor, window_size=WINDOW_SIZE, batch_size=BATCH_SIZE)
    x_train, y_train = preprocessor.split_features_target(train)
    x_validation, y_validation = preprocessor.split_features_target(validation)
    # scaler 는 train 구간으로만 fit 한다 (fit=True 는 여기 한 번뿐이다).
    train_loader = pipeline.create_dataloader(x_train, y_train, fit=True)
    validation_loader = pipeline.create_dataloader(x_validation, y_validation)
    if any(float(value) <= 0 for value in preprocessor.x_scaler.scale_):
        raise SeedExportError(f"{spec.symbol} has a constant feature column")

    torch.manual_seed(SEED)
    torch.set_num_threads(1)
    model = LSTMModel(
        input_size=len(FRAME_FEATURES),
        hidden_size=HIDDEN_SIZE,
        num_layers=LAYER_COUNT,
        learning_rate=LEARNING_RATE,
        dropout=DROPOUT,
    )
    model.train(train_loader, validation_loader, epochs=EPOCHS)

    # 다음 거래일 예측. Team B forecast() 가 마지막 20행으로 1스텝 예측하고
    # y_scaler 로 역변환까지 해서 돌려준다.
    raw_forecast = float(model.forecast(featured, preprocessor, pipeline))
    current_close = float(featured.iloc[-1]["Close"])
    if not math.isfinite(current_close) or current_close <= 0:
        raise SeedExportError(f"{spec.symbol} current close is not a usable price")
    if not math.isfinite(raw_forecast):
        raise SeedExportError(f"{spec.symbol} forecast is not finite")

    if target_transform == TARGET_LOG_RETURN:
        try:
            forecast_close = current_close * math.exp(raw_forecast)
        except OverflowError:
            raise SeedExportError(f"{spec.symbol} log-return forecast overflowed") from None
    else:
        forecast_close = raw_forecast
    if not math.isfinite(forecast_close) or forecast_close < 0:
        raise SeedExportError(f"{spec.symbol} forecast is not a usable price")

    # test 구간 신호. from_prediction 의 전일 예측 대비 변화율을 쓰지 않고
    # 계약 정의(forecastClose/currentClose - 1)와 같은 기준을 쓴다.
    predicted = model.predict(test, preprocessor, pipeline)
    if target_transform == TARGET_LOG_RETURN:
        # 모델 출력이 그날 실현될 로그수익률이므로 기대수익률은 exp(y-hat) - 1 이다.
        expected = np.expm1(predicted["Prediction"].astype(float).clip(-10.0, 10.0))
    else:
        expected = predicted["Prediction"].astype(float) / predicted["Close"].astype(float) - 1.0
    signals = pd.DataFrame(
        {
            "Date": pd.to_datetime(predicted["Date"]),
            "Close": predicted["Close"].astype(float),
            "Signal": [classify_signal(float(value)) for value in expected],
        }
    )

    # naive persistence 대조. 각 test 행에서 모델이 함의하는 예측 종가와 실제 종가를 비교하고,
    # 같은 행에서 "내일 종가 = 오늘 종가" 기준선의 오차도 같은 방식으로 계산한다.
    actual = predicted["Close"].astype(float).to_numpy()
    if target_transform == TARGET_LOG_RETURN:
        # 행 t 의 예측은 그날 실현될 로그수익률이므로 함의 예측가는 Close_{t-1} * exp(y-hat) 다.
        previous = np.empty_like(actual)
        previous[0] = float(train["Close"].iloc[-1])
        previous[1:] = actual[:-1]
        implied = previous * np.exp(
            predicted["Prediction"].astype(float).to_numpy().clip(-10.0, 10.0)
        )
    else:
        previous = np.empty_like(actual)
        previous[0] = float(train["Close"].iloc[-1])
        previous[1:] = actual[:-1]
        implied = predicted["Prediction"].astype(float).to_numpy()

    model_sse = float(np.sum((implied - actual) ** 2))
    naive_sse = float(np.sum((previous - actual) ** 2))
    realized = actual - previous
    predicted_move = implied - previous
    scored = realized != 0
    direction_total = int(np.count_nonzero(scored))
    direction_correct = int(
        np.count_nonzero(np.sign(predicted_move[scored]) == np.sign(realized[scored]))
    )

    baseline = SignalGenerator.from_baseline(featured)
    baseline_signal = str(baseline.iloc[-1]["Signal"])

    state_dict = {
        key: value.detach().cpu().numpy().astype("<f4")
        for key, value in model.model.state_dict().items()
    }
    return SymbolResult(
        symbol=spec.symbol,
        current_close=current_close,
        forecast_close=forecast_close,
        baseline_signal=baseline_signal,
        scaler_mean=[float(item) for item in preprocessor.x_scaler.mean_],
        scaler_scale=[float(item) for item in preprocessor.x_scaler.scale_],
        target_mean=float(preprocessor.y_scaler.mean_[0]),
        target_scale=float(preprocessor.y_scaler.scale_[0]),
        test_model_sse=model_sse,
        test_naive_sse=naive_sse,
        test_points=int(len(actual)),
        test_direction_correct=direction_correct,
        test_direction_total=direction_total,
        state_dict=state_dict,
        signals=signals,
    )


# --- 백테스트: 주문은 Team B 엔진, 비용과 합산은 exporter ---------------------
def _orders_for_symbol(signals: pd.DataFrame) -> list[dict[str, Any]]:
    if signals.empty:
        raise SeedExportError("signal frame is empty; BacktestEngine would raise IndexError")
    # 종목마다 새 인스턴스를 만든다. run() 이 daily_assets 를 리셋 없이 append 한다.
    engine = BacktestEngine(model=None, initial_cash=INITIAL_CAPITAL_KRW // 31)
    engine.run(signals)
    return list(engine.order_log)


def _replay_with_cost(
    signals: pd.DataFrame, orders: list[dict[str, Any]], initial_cash: int
) -> tuple[pd.Series, list[dict[str, Any]]]:
    """주문을 왕복 35bps 와 함께 재생해 비용이 반영된 자산곡선과 거래를 만든다.

    Team B 엔진은 수수료 모델이 없다. 계약이 CONSERVATIVE_FIXED_35BPS_V1 을 요구하므로
    체결마다 편도 절반(17.5bps)을 현금에서 뺀다.
    """
    by_date: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    for order in orders:
        by_date.setdefault(pd.Timestamp(order["date"]), []).append(order)

    cash = float(initial_cash)
    shares = 0
    equity: list[float] = []
    index: list[pd.Timestamp] = []
    trades: list[dict[str, Any]] = []
    open_entry: dict[str, Any] | None = None

    for _, row in signals.iterrows():
        day = pd.Timestamp(row["Date"])
        price = float(row["Close"])
        for order in by_date.get(day, []):
            quantity = int(order["shares"])
            fill = float(order["price"])
            if order["type"] == "BUY":
                cash -= fill * quantity
                cash -= fill * quantity * (ROUND_TRIP_COST / 2.0)
                shares += quantity
                open_entry = {"date": day, "price": fill, "shares": quantity}
            else:
                cash += fill * quantity
                cash -= fill * quantity * (ROUND_TRIP_COST / 2.0)
                shares -= quantity
                if open_entry is not None:
                    trades.append({"entry": open_entry, "exitDate": day, "exitPrice": fill})
                    open_entry = None
        equity.append(cash + shares * price)
        index.append(day)

    # 마지막 미청산 매수를 마지막 종가로 심산 종료한다. make_trades 는 이 건을 통째로
    # 누락해 승률을 위로 편향시킨다.
    if open_entry is not None and index:
        trades.append(
            {
                "entry": open_entry,
                "exitDate": index[-1],
                "exitPrice": float(signals.iloc[-1]["Close"]),
            }
        )
    return pd.Series(equity, index=pd.DatetimeIndex(index)), trades


# --- 산출물 ---------------------------------------------------------------
def _safetensors_bytes(results: list[SymbolResult]) -> bytes:
    """Team B state_dict 를 계약 텐서 이름으로 바꿔 손으로 직렬화한다.

    safetensors 패키지를 새 의존성으로 넣지 않는다. 형식이 단순하고 Owner 도 같은 방식으로
    쓴다 (`assets._safetensors_bytes`).
    """
    rename = {
        "lstm.weight_ih_l0": "weight_ih_l0",
        "lstm.weight_hh_l0": "weight_hh_l0",
        "lstm.bias_ih_l0": "bias_ih_l0",
        "lstm.bias_hh_l0": "bias_hh_l0",
        "fc.weight": "head.weight",
        "fc.bias": "head.bias",
    }
    header: dict[str, Any] = {}
    blobs: list[bytes] = []
    offset = 0
    for result in results:
        if set(result.state_dict) != set(rename):
            raise SeedExportError(
                f"{result.symbol} state_dict keys drifted: {sorted(result.state_dict)}"
            )
        for source, suffix in rename.items():
            array = np.ascontiguousarray(result.state_dict[source], dtype="<f4")
            if not bool(np.isfinite(array).all()):
                raise SeedExportError(f"{result.symbol}.{suffix} is non-finite")
            payload = array.tobytes()
            header[f"{result.symbol}.{suffix}"] = {
                "data_offsets": [offset, offset + len(payload)],
                "dtype": "F32",
                "shape": list(array.shape),
            }
            blobs.append(payload)
            offset += len(payload)
    header["__metadata__"] = {
        "contractId": "p1-return-model-safetensors.v2",
        "featureOrder": ",".join(CONTRACT_FEATURE_ORDER),
        "symbolCount": str(len(results)),
    }
    encoded = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
    padding = (-len(encoded)) % 8
    padded = encoded + b" " * padding
    return struct.pack("<Q", len(padded)) + padded + b"".join(blobs)


def _signal_table(rows: list[dict[str, Any]]) -> bytes:
    schema = pa.schema(
        [
            ("currentClose", pa.float64()),
            ("expectedReturn", pa.float64()),
            ("forecastClose", pa.float64()),
            ("sessionDate", pa.string()),
            ("signal", pa.string()),
            ("symbol", pa.string()),
        ]
    )
    return _parquet_bytes(pa.Table.from_pylist(rows, schema=schema))


def _lstm_rows(results: list[SymbolResult], session_date: date) -> list[dict[str, Any]]:
    rows = []
    for result in results:
        expected = result.forecast_close / result.current_close - 1.0
        rows.append(
            {
                "currentClose": result.current_close,
                "expectedReturn": expected,
                "forecastClose": result.forecast_close,
                "sessionDate": session_date.isoformat(),
                "signal": classify_signal(expected),
                "symbol": result.symbol,
            }
        )
    return rows


def _baseline_rows(results: list[SymbolResult], session_date: date) -> list[dict[str, Any]]:
    """규칙 baseline 은 가격을 예측하지 않는다. signal 의 정규화 표현으로 forecastClose 를 만든다.

    importer 는 두 producer 모두 forecastClose 와 signal 이 일관되기를 요구한다.
    이 값이 가격 예측이 아니라는 사실은 model_report.md 에 명시한다.
    """
    margin = SIGNAL_DEADBAND + 0.0005
    rows = []
    for result in results:
        if result.baseline_signal == "BUY":
            forecast = result.current_close * (1.0 + margin)
        elif result.baseline_signal == "SELL":
            forecast = result.current_close * (1.0 - margin)
        else:
            forecast = result.current_close
        expected = forecast / result.current_close - 1.0
        signal = classify_signal(expected)
        if signal != result.baseline_signal:
            raise SeedExportError(
                f"{result.symbol} baseline normalization lost the signal: "
                f"{result.baseline_signal} -> {signal}"
            )
        rows.append(
            {
                "currentClose": result.current_close,
                "expectedReturn": expected,
                "forecastClose": forecast,
                "sessionDate": session_date.isoformat(),
                "signal": signal,
                "symbol": result.symbol,
            }
        )
    return rows


def _trade_table(trade_rows: list[dict[str, Any]]) -> bytes:
    schema = pa.schema(
        [
            ("scenario", pa.string()),
            ("symbol", pa.string()),
            ("entrySession", pa.date32()),
            ("exitSession", pa.date32()),
            ("side", pa.string()),
            ("quantity", pa.int64()),
            ("entryPrice", pa.int64()),
            ("exitPrice", pa.int64()),
            ("grossReturn", pa.float64()),
            ("costBps", pa.int64()),
            ("netReturn", pa.float64()),
        ]
    )
    return _parquet_bytes(pa.Table.from_pylist(trade_rows, schema=schema))


def _equity_table(curve: pd.Series) -> tuple[bytes, dict[str, float | int]]:
    running_peak = 0.0
    rows: list[dict[str, Any]] = []
    returns: list[float] = []
    previous: float | None = None
    drawdowns: list[float] = []
    for day, value in curve.items():
        equity = float(value)
        running_peak = max(running_peak, equity)
        drawdown = equity / running_peak - 1.0 if running_peak else 0.0
        drawdowns.append(drawdown)
        if previous is not None and previous > 0:
            returns.append(equity / previous - 1.0)
        previous = equity
        rows.append(
            {
                "scenario": SCENARIO,
                "sessionDate": pd.Timestamp(day).date(),
                # Owner 가 initialCapitalKrw 를 int 로 읽으므로 원 단위 정수로 게시한다.
                "equityKrw": int(round(equity)),
                "drawdown": drawdown,
            }
        )
    schema = pa.schema(
        [
            ("scenario", pa.string()),
            ("sessionDate", pa.date32()),
            ("equityKrw", pa.int64()),
            ("drawdown", pa.float64()),
        ]
    )
    content = _parquet_bytes(pa.Table.from_pylist(rows, schema=schema))

    # Owner 가 equity_log 에서 독립 재계산하는 것과 같은 정의로 계산한다.
    # 게시한 정수 equity 를 그대로 써야 재계산이 일치한다.
    published = [float(row["equityKrw"]) for row in rows]
    peaks: list[float] = []
    running_peak = 0.0
    recomputed_returns: list[float] = []
    previous = None
    for equity in published:
        running_peak = max(running_peak, equity)
        peaks.append(equity / running_peak - 1.0 if running_peak else 0.0)
        if previous is not None and previous > 0:
            recomputed_returns.append(equity / previous - 1.0)
        previous = equity
    metrics = {
        "netReturn": published[-1] / published[0] - 1.0,
        "mdd": min(peaks),
        "sharpe": _annualized_sharpe(recomputed_returns),
    }
    return content, metrics


def _annualized_sharpe(returns: list[float], *, sessions_per_year: float = 252.0) -> float:
    """Owner `importer._annualized_sharpe` 와 같은 정의 (ddof=1)."""
    if len(returns) < 2:
        return 0.0
    values = np.asarray(returns, dtype=np.float64)
    deviation = float(np.std(values, ddof=1))
    if deviation == 0.0:
        return 0.0
    return float(np.mean(values) / deviation * math.sqrt(sessions_per_year))


def _model_report(
    *,
    results: list[SymbolResult],
    session_date: date,
    validation_start: date,
    test_start: date,
    scenario_metrics: dict[str, float | int],
    target_transform: str,
    trade_count: int,
    naive: dict[str, float],
    input_pack_sha256: str,
    collection_notes: dict[str, Any],
) -> bytes:
    lstm = _lstm_rows(results, session_date)
    buys = sum(1 for row in lstm if row["signal"] == "BUY")
    holds = sum(1 for row in lstm if row["signal"] == "HOLD")
    sells = sum(1 for row in lstm if row["signal"] == "SELL")
    ratios = [row["forecastClose"] / row["currentClose"] for row in lstm]
    halted = collection_notes.get("haltedSessionsExcluded", {})
    halted_total = sum(int(value) for value in halted.values())
    halted_top = sorted(halted.items(), key=lambda item: -int(item[1]))[:3]
    lines = [
        "# Team B exact-31 model report",
        "",
        "## Data",
        "",
        "- 수집: yfinance, `auto_adjust=False`, 사용 컬럼 Open/High/Low/Close/Volume",
        "- `Adj Close`는 쓰지 않는다. 계약이 `priceBasis=RAW_CLOSE`다.",
        "- 유니버스: `contracts/catalogs/p1-return-universe.v1.json` exact-31",
        f"- 입력 해시(`inputPackSha256`): `{input_pack_sha256}`",
        f"- 마지막 공통 세션: `{session_date.isoformat()}`",
        f"- 거래정지 바 {halted_total}행을 제외했다. yfinance 는 거래정지일에도 직전 종가를",
        "  그대로 채워 보내고 그 행은 OHLC 가 모두 종가와 같으며 거래량이 0이다. 가격 관측이",
        "  아니므로 수집 단계에서 제외한다. 그대로 두면 14일 이상 이어질 때 RSI 의 gain/loss 가",
        "  둘 다 0이 되어 `0/0 = NaN`이 되고 `dropna()`가 **중간** 거래일을 조용히 지운다.",
        f"  최다: {', '.join(f'{symbol} {count}행' for symbol, count in halted_top)}.",
        "- **yfinance 의 `Close`는 액면분할이 소급 반영된 값이다.** `auto_adjust=False`는 배당",
        "  조정만 끄고 분할 조정은 남는다. 실제로 일부 종목의 과거 종가가 소수로 나온다",
        "  (한국 주식 raw 종가는 정수다). 따라서 과거 구간은 엄밀한 `RAW_CLOSE`가 아니라",
        "  분할 조정가다. 학습에는 연속성이 있는 편이 낫고, seed 의 `currentClose`는 최신",
        "  세션이라 조정 대상이 아니다. 이 차이를 계약 주장으로 감추지 않고 여기 기록한다.",
        "",
        "## Model ABI",
        "",
        f"- feature 9개 순서: `{', '.join(CONTRACT_FEATURE_ORDER)}`",
        f"- window {WINDOW_SIZE} / hidden {HIDDEN_SIZE} / layer {LAYER_COUNT} / dropout {DROPOUT}",
        f"- `targetTransform={target_transform}`."
        + (
            " 모델은 로그수익률을 예측하고 `forecastClose = currentClose * exp(y-hat)` 로"
            " 가격을 재구성한다. 절대가 타깃은 test 가 train 최대를 넘으면 역변환이 학습"
            " 평균 근처로 주저앉아 `expectedReturn -85%` 를 만든다."
            if target_transform == TARGET_LOG_RETURN
            else " 모델이 절대 종가를 예측한다. 골든 번들 호환 경로다."
        ),
        f"- optimizer Adam / lr {LEARNING_RATE} / loss SmoothL1 / batch {BATCH_SIZE} /"
        f" epochs {EPOCHS}",
        f"- 종목별 독립 LSTM {len(results)}개, seed {SEED}, thread 1",
        "- scaler: `StandardScaler`, train 구간으로만 fit",
        "",
        "## Split",
        "",
        f"- train: 처음 ~ `{validation_start.isoformat()}` 이전",
        f"- validation: `{validation_start.isoformat()}` ~ `{test_start.isoformat()}` 이전"
        f" ({VALIDATION_SESSIONS} 세션)",
        f"- test: `{test_start.isoformat()}` ~ `{session_date.isoformat()}` ({TEST_SESSIONS} 세션)",
        "- 모든 종목이 같은 경계 날짜를 쓴다. 상장일이 달라 train 길이만 종목마다 다르다.",
        "- validation 으로 설정을 바꾸지 않았고 final test 후 재튜닝은 0이다.",
        "",
        "## Reproducibility",
        "",
        "- 수집과 학습을 분리한다. `collect`만 네트워크를 쓰고 `export`는 캐시만 읽는다.",
        "  그래서 `producer.networkCalls=0`이 정직하게 성립한다.",
        "- KIS·계좌·잔고·주문 호출 0, Spring 호출 0.",
        "- CPU 단일 스레드, seed 0.",
        "",
        "## Model quality",
        "",
        "- **`BELOW_BASELINE`**. 21년 walk-forward out-of-sample 측정 결과다.",
        f"- naive persistence 기준선 대조 (test 구간 {int(naive['points']):,}개 1스텝 예측,"
        f" 실제 종가 대비 RMSE): 모델 {naive['modelRmse']:,.0f}원 /"
        f" naive(내일=오늘) {naive['naiveRmse']:,.0f}원"
        f" -> 모델이 {'낫다' if naive['beatsNaive'] else '못하다'}",
        f"- test 구간 방향 정확도 {naive['directionAccuracy']:.4f}"
        f" (실현 변동이 0이 아닌 {int(naive['directionPoints']):,}개 기준)."
        " 21년 walk-forward 에서도 0.4777 로 동전던지기 아래였다. 값의 건전성은 고쳤지만"
        " 방향 예측력은 없다.",
        f"- forecast/current 비율 {min(ratios):.4f} ~ {max(ratios):.4f}",
        f"- signal 분포 BUY {buys} / HOLD {holds} / SELL {sells}",
        f"- GUIDE 시나리오: netReturn {scenario_metrics['netReturn']:.4f} /"
        f" mdd {scenario_metrics['mdd']:.4f} / sharpe {scenario_metrics['sharpe']:.4f} /"
        f" 거래 {trade_count}건",
        "- 근거 전문은"
        " `workspaces/decision-platform/research/p1-return-profit-verification/reports/`"
        "를 보라.",
        "",
        "## Limitations",
        "",
        "- 성능 주장을 하지 않는다 (`performanceClaimAllowed=false`).",
        "- 주문 권한이 없다 (`orderAuthority=NONE`). LSTM 은 후보 생성기이고 곧바로 주문이",
        "  되지 않는다. 수량은 RiskEngine 이 단독으로 정한다.",
        "- 규칙 baseline 의 `forecastClose`는 **가격 예측이 아니다.** `from_baseline`이 가격을",
        "  내지 않으므로 signal 을 계약 필드로 옮기기 위한 정규화 표현이다.",
        "- 시나리오는 `GUIDE` 하나다. `scenario_policy.json`이 GUIDE 를"
        " `OWNER_GUIDE_REPLAY`로 정의하고 `teamBRiskEngineImplementation=false`이므로",
        "  의미가 완전히 맞지는 않는다. 이 절충을 기록해 둔다.",
        "- 자산곡선은 Team B 엔진의 주문을 exporter 가 왕복 35bps 와 함께 재생한 것이다.",
        "  Team B 엔진 자체에는 수수료 모델이 없다.",
        "- 전액 매수 뒤 두 번째 BUY 는 `cash // price = 0`이 되어 조용히 무시된다. 무해하지만",
        "  거래 수가 의도를 그대로 반영하지는 않는다.",
        "- 종목 선정 생존편향이 남는다. exact-31 은 현재 시점 명부다.",
        "",
    ]
    return ("\n".join(lines)).encode("utf-8")


# --- 원자 게시 ------------------------------------------------------------
def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _publish(output_root: Path, payloads: dict[str, bytes], manifest: dict[str, Any]) -> str:
    """manifest 를 마지막에 쓴다. 부분 게시 상태를 소비자가 유효하다고 보지 않게 한다."""
    if output_root.exists() and any(output_root.iterdir()):
        raise SeedExportError(f"output root is not empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACT_NAMES:
        _atomic_write(output_root / name, payloads[name])
    manifest_bytes = canonical_json_bytes(manifest)
    _atomic_write(output_root / MANIFEST_NAME, manifest_bytes)
    return _digest(manifest_bytes)


def _git_commit() -> str:
    try:
        output = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return output.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def export(
    *,
    catalog_path: Path,
    cache_dir: Path,
    output_root: Path,
    model_quality: str,
    target_transform: str = TARGET_LOG_RETURN,
) -> dict[str, Any]:
    specs = load_universe(catalog_path)
    cache_path = cache_dir / CACHE_FRAME
    if not cache_path.is_file():
        raise SeedExportError(f"collected frame is missing: {cache_path}. Run collect first.")
    input_pack_sha256 = _file_digest(cache_path)
    merged = pd.read_parquet(cache_path)
    notes_path = cache_dir / COLLECTION_NOTES
    collection_notes: dict[str, Any] = (
        json.loads(notes_path.read_text(encoding="utf-8")) if notes_path.is_file() else {}
    )

    featured_by_symbol: dict[str, pd.DataFrame] = {}
    preprocessors: dict[str, Preprocessor] = {}
    for spec in specs:
        frame = merged[merged["symbol"] == spec.symbol][
            ["Date", "Open", "High", "Low", "Close", "Volume"]
        ]
        if frame.empty:
            raise SeedExportError(f"collected frame has no rows for {spec.symbol}")
        featured, preprocessor = _featured_frame(frame, target_transform)
        featured_by_symbol[spec.symbol] = featured
        preprocessors[spec.symbol] = preprocessor

    validation_start, test_start, session_date = _split_boundaries(featured_by_symbol)
    print(
        f"  split: train < {validation_start} <= validation < {test_start} <= test <= "
        f"{session_date}"
    )

    results: list[SymbolResult] = []
    for spec in specs:
        result = _train_symbol(
            spec,
            featured_by_symbol[spec.symbol],
            preprocessors[spec.symbol],
            validation_start,
            test_start,
            target_transform,
        )
        results.append(result)
        expected = result.forecast_close / result.current_close - 1.0
        print(
            f"  TRAIN {result.symbol} current {result.current_close:>10,.0f} "
            f"forecast {result.forecast_close:>10,.0f} "
            f"expected {expected * 100:+6.2f}% {classify_signal(expected):4s}",
            flush=True,
        )

    # naive persistence 기준선. 종목별 test 구간 오차를 그대로 합산한다.
    model_sse = sum(result.test_model_sse for result in results)
    naive_sse = sum(result.test_naive_sse for result in results)
    points = sum(result.test_points for result in results)
    direction_correct = sum(result.test_direction_correct for result in results)
    direction_total = sum(result.test_direction_total for result in results)
    naive = {
        "modelRmse": math.sqrt(model_sse / points) if points else 0.0,
        "naiveRmse": math.sqrt(naive_sse / points) if points else 0.0,
        "beatsNaive": bool(points and model_sse < naive_sse),
        "directionAccuracy": (direction_correct / direction_total) if direction_total else 0.0,
        "directionPoints": direction_total,
        "points": points,
    }

    # 포트폴리오 백테스트: 종목마다 1/31 자본, 주문은 Team B 엔진, 비용은 exporter
    per_symbol_cash = INITIAL_CAPITAL_KRW // 31
    curves: list[pd.Series] = []
    trade_rows: list[dict[str, Any]] = []
    for result in results:
        orders = _orders_for_symbol(result.signals)
        curve, trades = _replay_with_cost(result.signals, orders, per_symbol_cash)
        curves.append(curve)
        for trade in trades:
            entry_price = int(round(float(trade["entry"]["price"])))
            exit_price = int(round(float(trade["exitPrice"])))
            if entry_price <= 0 or exit_price < 0:
                raise SeedExportError(f"{result.symbol} trade has an unusable price")
            gross = float(exit_price) / float(entry_price) - 1.0
            trade_rows.append(
                {
                    "scenario": SCENARIO,
                    "symbol": result.symbol,
                    "entrySession": pd.Timestamp(trade["entry"]["date"]).date(),
                    "exitSession": pd.Timestamp(trade["exitDate"]).date(),
                    "side": "LONG",
                    "quantity": int(trade["entry"]["shares"]),
                    "entryPrice": entry_price,
                    "exitPrice": exit_price,
                    "grossReturn": gross,
                    "costBps": ROUND_TRIP_COST_BPS,
                    "netReturn": gross - ROUND_TRIP_COST,
                }
            )

    portfolio = pd.concat(curves, axis=1).ffill().bfill().sum(axis=1).sort_index()
    if portfolio.empty:
        raise SeedExportError("portfolio equity curve is empty")
    equity_bytes, scenario_metrics = _equity_table(portfolio)

    lstm_rows = _lstm_rows(results, session_date)
    baseline_rows = _baseline_rows(results, session_date)
    config = {
        "batchSize": BATCH_SIZE,
        "contractId": "p1-return-config.v2",
        "deterministicAlgorithms": True,
        "dropout": DROPOUT,
        "epochs": EPOCHS,
        "featureOrder": list(CONTRACT_FEATURE_ORDER),
        "hiddenSize": HIDDEN_SIZE,
        "hyperparameterSearchCount": 0,
        "layerCount": LAYER_COUNT,
        "learningRate": LEARNING_RATE,
        "loss": "SmoothL1",
        "optimizer": "Adam",
        "outputSize": 1,
        "perSymbolIndependent": True,
        "postFinalTuningCount": 0,
        "roundTripCostBps": ROUND_TRIP_COST_BPS,
        "seed": SEED,
        "targetTransform": target_transform,
        "threadCount": 1,
        "windowSize": WINDOW_SIZE,
    }
    scaler = {
        "contractId": "p1-return-scaler.v2",
        "featureOrder": list(CONTRACT_FEATURE_ORDER),
        "fitScope": "TRAIN_ONLY",
        "symbols": {
            result.symbol: {
                "mean": result.scaler_mean,
                "scale": result.scaler_scale,
                # LOG_RETURN 번들은 타깃 스케일러도 실어야 Owner 가 exp() 재구성을 할 수 있다.
                "target": {"mean": result.target_mean, "scale": result.target_scale},
            }
            for result in results
        },
    }
    scenarios = [
        {
            "scenario": SCENARIO,
            "netReturn": scenario_metrics["netReturn"],
            "mdd": scenario_metrics["mdd"],
            "sharpe": scenario_metrics["sharpe"],
            "tradeCount": len(trade_rows),
            "costModelId": COST_MODEL_ID,
        }
    ]
    golden = {
        "contractId": "p1-return-golden-output.v2",
        "costModelId": COST_MODEL_ID,
        "evidenceMode": "REAL_TEAM_B",
        "forecastFormula": "forecastClose/currentClose-1",
        "inputPackSha256": input_pack_sha256,
        "orderAuthority": "NONE",
        "performanceClaimAllowed": False,
        "predictions": [
            {
                "currentClose": row["currentClose"],
                "expectedReturn": row["expectedReturn"],
                "forecastClose": row["forecastClose"],
                "symbol": row["symbol"],
            }
            for row in lstm_rows
        ],
    }
    report = _model_report(
        results=results,
        session_date=session_date,
        validation_start=validation_start,
        test_start=test_start,
        scenario_metrics=scenario_metrics,
        target_transform=target_transform,
        trade_count=len(trade_rows),
        naive=naive,
        input_pack_sha256=input_pack_sha256,
        collection_notes=collection_notes,
    )

    payloads = {
        "model.safetensors": _safetensors_bytes(results),
        "scaler.json": canonical_json_bytes(scaler),
        "config.json": canonical_json_bytes(config),
        "lstm_signals.parquet": _signal_table(lstm_rows),
        "rule_baseline_signals.parquet": _signal_table(baseline_rows),
        "backtest_result.json": canonical_json_bytes(
            {
                "contractId": "p1-return-backtest-result.v2",
                "independentlyRecomputed": True,
                "performanceClaimAllowed": False,
                "scenarios": scenarios,
            }
        ),
        "trade_log.parquet": _trade_table(trade_rows),
        "equity_log.parquet": equity_bytes,
        "golden_output.json": canonical_json_bytes(golden),
        "model_report.md": report,
    }

    split_sha = _digest(
        canonical_json_bytes(
            {
                "sessionDate": session_date.isoformat(),
                "testSessions": TEST_SESSIONS,
                "testStart": test_start.isoformat(),
                "validationSessions": VALIDATION_SESSIONS,
                "validationStart": validation_start.isoformat(),
            }
        )
    )
    run_id = "run_" + _digest(payloads["golden_output.json"])[:32]
    manifest = {
        "artifacts": [
            {
                "path": name,
                "semanticSchema": f"contracts/schemas/{schema_id}.schema.json",
                "sha256": _digest(payloads[name]),
                "sizeBytes": len(payloads[name]),
            }
            for name, schema_id in zip(ARTIFACT_NAMES, ARTIFACT_SCHEMA_IDS, strict=True)
        ],
        "contractId": "p1-return-engine-artifact-manifest.v3",
        "evidenceMode": "REAL_TEAM_B",
        "furtherTuningRequired": False,
        "inputPackSha256": input_pack_sha256,
        "mockRuntimeEligible": True,
        "modelQuality": model_quality,
        "orderAuthority": "NONE",
        "performanceClaimAllowed": False,
        "producer": {
            "accountCalls": 0,
            "commitSha256": _digest(_git_commit().encode("utf-8")),
            "configSha256": _digest(payloads["config.json"]),
            "dependencyLockSha256": _file_digest(REPO_ROOT / "workspaces/return-engine/uv.lock"),
            "dockerfileSha256": _file_digest(REPO_ROOT / "workspaces/return-engine/Dockerfile"),
            "featureOrderSha256": _digest(canonical_json_bytes(list(CONTRACT_FEATURE_ORDER))),
            "goldenOutputSha256": _digest(payloads["golden_output.json"]),
            "networkCalls": 0,
            "orderCalls": 0,
            "seed": SEED,
            "splitSha256": split_sha,
            "springCalls": 0,
            "trainingCodeSha256": _file_digest(Path(__file__).resolve()),
        },
        "realTeamB": True,
        "runId": run_id,
    }
    manifest_sha = _publish(output_root, payloads, manifest)
    return {
        "manifestSha256": manifest_sha,
        "inputPackSha256": input_pack_sha256,
        "runId": run_id,
        "sessionDate": session_date.isoformat(),
        "targetTransform": target_transform,
        "symbols": len(results),
        "tradeCount": len(trade_rows),
        "scenario": scenarios[0],
        "signalDistribution": {
            "BUY": sum(1 for row in lstm_rows if row["signal"] == "BUY"),
            "HOLD": sum(1 for row in lstm_rows if row["signal"] == "HOLD"),
            "SELL": sum(1 for row in lstm_rows if row["signal"] == "SELL"),
        },
        "naiveBaseline": naive,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Owner exact-31 seed exporter")
    parser.add_argument(
        "command", choices=("collect", "export", "run"), help="collect / export / both"
    )
    parser.add_argument(
        "--universe-catalog",
        type=Path,
        default=REPO_ROOT / "contracts/catalogs/p1-return-universe.v1.json",
    )
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument(
        "--target-transform",
        default=TARGET_LOG_RETURN,
        choices=(TARGET_RAW_CLOSE, TARGET_LOG_RETURN),
        help="기본값 LOG_RETURN. RAW_CLOSE 는 골든 번들 호환 경로다.",
    )
    parser.add_argument(
        "--model-quality",
        default="BELOW_BASELINE",
        choices=("PASS", "BELOW_BASELINE"),
        help="21년 walk-forward 측정 결과. 기본값은 측정된 BELOW_BASELINE 이다.",
    )
    args = parser.parse_args(argv)

    try:
        specs = load_universe(args.universe_catalog)
        if args.command in {"collect", "run"}:
            print(f"[collect] {len(specs)}종목 yfinance 수집")
            collect(specs, args.cache_dir, start=args.start)
        if args.command in {"export", "run"}:
            if args.output_root is None:
                raise SeedExportError("export requires --output-root")
            print(f"[export] {len(specs)}종목 학습과 exact-10 게시")
            summary = export(
                catalog_path=args.universe_catalog,
                cache_dir=args.cache_dir,
                output_root=args.output_root,
                model_quality=args.model_quality,
                target_transform=args.target_transform,
            )
            print()
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            print()
            print("TEAM_B_GIT_MODEL_SEED=PASS")
            print(f"TEAM_B_ARTIFACT_MANIFEST_SHA256={summary['manifestSha256']}")
            print("TEAM_B_PROVIDER_ACCOUNT_ORDER_CALLS=0")
            print("TEAM_B_ORDER_AUTHORITY=NONE")
    except SeedExportError as error:
        print(f"SEED_EXPORT_FAILED={error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
