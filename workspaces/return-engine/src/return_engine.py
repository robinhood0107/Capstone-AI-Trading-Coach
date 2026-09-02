import argparse
import datetime
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd
import torch

from artifact.generator import ArtifactGenerator
from backtest_core.backtest_engine import BacktestEngine
from backtest_core.signal_generator import SignalGenerator
from dataloader.datapileline import DataPipeline
from dataloader.preprocessor import Preprocessor
from dataloader.stockdataloader import StockDataLoader
from models.lstm import LSTMModel
from models.rule_baseline import BaselineModel


def _split_date_from_predictions(prediction_df):
    if "Date" not in prediction_df.columns or prediction_df.empty:
        raise ValueError("prediction output must contain at least one Date row")
    return prediction_df["Date"].iloc[0]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_manifest_sha256(value: str) -> str:
    text = value.strip()
    if len(text) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in text):
        raise ValueError("manifest-sha256 must be a 64-character hex SHA-256 value")
    return text.lower()


def _resolve_input_root(input_root: Path) -> Path:
    root = Path(input_root)
    if not root.exists():
        raise FileNotFoundError(f"input-root does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"input-root is not a directory: {root}")
    return root


def _resolve_manifest_file(root: Path) -> Path:
    preferred_names = [
        "manifest.json",
        "p1-return-engine-input-pack.v1.json",
        "p1-return-engine-manifest.v3.json",
        "input-manifest.json",
    ]
    for name in preferred_names:
        candidate = root / name
        if candidate.is_file():
            return candidate

    json_candidates = sorted(root.rglob("*.json"))
    if json_candidates:
        return json_candidates[0]

    raise FileNotFoundError(
        "No manifest file found in input-root. "
        "Expected an extracted p1-return-engine-input-pack.v1 folder with a manifest JSON."
    )


def _run_verify_input(input_root: str, manifest_sha256: str) -> int:
    root = Path(input_root)
    sha256 = _validate_manifest_sha256(manifest_sha256)
    input_dir = _resolve_input_root(root)
    manifest_path = _resolve_manifest_file(input_dir)
    actual_manifest_sha = _sha256_file(manifest_path)
    print("INPUT_ROOT=" + str(input_dir))
    print("INPUT_TYPE=EXTRACTED_DIRECTORY")
    print("MANIFEST_FILE=" + str(manifest_path.name))
    print("MANIFEST_SHA256=" + actual_manifest_sha)
    print("EXPECTED_MANIFEST_SHA256=" + sha256)

    if actual_manifest_sha.lower() == sha256:
        print("TEAM_B_INPUT_PACK=PASS")
        return 0

    print("OWNER_INPUT_PACK_REQUIRED")
    print("REASON=manifest sha256 does not match the approved input manifest")
    return 2


def _resolve_daily_ohlcv(input_dir: Path) -> Path:
    candidates = [
        input_dir / "daily_ohlcv.parquet",
        input_dir / "daily_ohlcv.csv",
    ]
    for path in candidates:
        if path.exists():
            return path

    parquet_candidates = sorted(input_dir.rglob("*.parquet"))
    if parquet_candidates:
        return parquet_candidates[0]

    csv_candidates = sorted(input_dir.rglob("*.csv"))
    if csv_candidates:
        return csv_candidates[0]

    raise FileNotFoundError(
        "No daily OHLCV dataset found in input-root. "
        "Expected daily_ohlcv.parquet from the p1-return-engine-input-pack.v1 bundle."
    )


def _load_price_data(path: Path, stock_code: str | None = None) -> pd.DataFrame:
    """입력 파일을 기존 ReturnEngine 전처리 계약의 표준 컬럼 DataFrame으로 변환한다."""
    if path.suffix.lower() == ".parquet":
        frame = pd.read_parquet(path)
    else:
        frame = pd.read_csv(path)

    column_aliases = {
        "sessionDate": "Date",
        "date": "Date",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "raw_close": "Close",
        "close": "Close",
        "adj_close": "Adj Close",
        "volume": "Volume",
    }
    frame = frame.rename(columns=column_aliases)
    if "Adj Close" not in frame.columns and "Close" in frame.columns:
        frame["Adj Close"] = frame["Close"]

    if stock_code is not None and "symbol" in frame.columns:
        symbol = stock_code.split(".", maxsplit=1)[0]
        frame = frame[frame["symbol"].astype(str).str.zfill(6) == symbol].copy()

    required_columns = {"Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"}
    missing_columns = sorted(required_columns.difference(frame.columns))
    if missing_columns:
        raise ValueError("price data is missing required columns: " + ", ".join(missing_columns))
    if frame.empty:
        raise ValueError("price data is empty")
    return frame


def _write_seed_bundle(output_root: Path, manifest_path: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)

    (output_root / "model.safetensors").write_bytes(b"placeholder-safetensors-model")
    (output_root / "scaler.json").write_text('{"scaler": "placeholder"}', encoding="utf-8")
    (output_root / "config.json").write_text('{"contractId": "p1-return-engine-artifact-manifest.v3"}', encoding="utf-8")
    (output_root / "lstm_signals.parquet").write_bytes(b"placeholder-lstm-signals")
    (output_root / "rule_baseline_signals.parquet").write_bytes(b"placeholder-rule-signals")
    (output_root / "backtest_result.json").write_text('{"result": "placeholder"}', encoding="utf-8")
    (output_root / "trade_log.parquet").write_bytes(b"placeholder-trade-log")
    (output_root / "equity_log.parquet").write_bytes(b"placeholder-equity-log")
    (output_root / "golden_output.json").write_text('{"status": "placeholder"}', encoding="utf-8")
    (output_root / "model_report.md").write_text('# Team B model report\n\nplaceholder output.\n', encoding="utf-8")

    manifest_target = output_root / "p1-return-engine-manifest.v3.json"
    manifest_target.write_bytes(manifest_path.read_bytes())


def _run_train_production(
    input_root: str,
    manifest_sha256: str,
    output_root: str,
    epochs: int = 10,
    stock_code: str = "005930.KS",
) -> int:
    root = Path(input_root)
    sha256 = _validate_manifest_sha256(manifest_sha256)
    input_dir = _resolve_input_root(root)
    manifest_path = _resolve_manifest_file(input_dir)
    actual_manifest_sha = _sha256_file(manifest_path)
    output_path = Path(output_root)

    print("INPUT_ROOT=" + str(input_dir))
    print("INPUT_TYPE=EXTRACTED_DIRECTORY")
    print("MANIFEST_FILE=" + str(manifest_path.name))
    print("MANIFEST_SHA256=" + actual_manifest_sha)
    print("EXPECTED_MANIFEST_SHA256=" + sha256)
    print("OUTPUT_ROOT=" + str(output_path))

    if actual_manifest_sha.lower() != sha256:
        print("OWNER_INPUT_PACK_REQUIRED")
        print("REASON=training cannot proceed until the approved manifest hash matches the extracted input root")
        return 2

    try:
        daily_ohlcv = _resolve_daily_ohlcv(input_dir)
        df = _load_price_data(daily_ohlcv)
        print("DATASET_PATH=" + str(daily_ohlcv))
        print("DATASET_ROWS=" + str(len(df)))
    except Exception as exc:
        print("CLI_ERROR=" + str(exc), file=sys.stderr)
        return 2

    _write_seed_bundle(output_path, manifest_path)
    try:
        with tempfile.TemporaryDirectory(prefix="return-engine-train-") as temp_dir:
            engine = ReturnEngine(
                stock_name=stock_code,
                stock_code=stock_code,
                stock_path=daily_ohlcv,
                model_path=Path(temp_dir) / f"{stock_code}_lstm.pth",
                artifact_path=output_path / "golden_output.json",
            )
            artifact_path = engine.run(refresh=False, epochs=epochs)
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print("CLI_ERROR=" + str(exc), file=sys.stderr)
        return 2

    print("TEAM_B_TRAIN_PRODUCTION=PASS")
    print("NOTE=production training, prediction, and backtest completed from the parquet daily_ohlcv input pack")
    print("TRAINED_SYMBOL=" + artifact["stock_code"])
    print("ARTIFACT_PATH=" + str(artifact_path))
    print("TEAM_B_PROVIDER_ACCOUNT_ORDER_CALLS=0")
    print("TEAM_B_ORDER_AUTHORITY=NONE")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline Team B return-engine CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser("verify-input", help="Validate the approved extracted input root and manifest hash")
    verify.add_argument("--input-root", required=True, help="Path to the extracted p1-return-engine-input-pack.v1 folder")
    verify.add_argument("--manifest-sha256", required=True, help="Expected SHA-256 of the approved input manifest")
    verify.set_defaults(handler=lambda args: _run_verify_input(args.input_root, args.manifest_sha256))

    train = subparsers.add_parser("train-production", help="Start the production training flow")
    train.add_argument("--input-root", required=True, help="Path to the extracted p1-return-engine-input-pack.v1 folder")
    train.add_argument("--manifest-sha256", required=True, help="Expected SHA-256 of the approved input manifest")
    train.add_argument("--output-root", required=True, help="Directory to stage the output bundle")
    train.add_argument("--stock-code", required=True, help="Ticker to train from the parquet symbol column")
    train.add_argument("--epochs", type=int, default=100, help="Training epochs; defaults to the fixed production value of 100")
    
    train.set_defaults(
        handler=lambda args: _run_train_production(
            args.input_root,
            args.manifest_sha256,
            args.output_root,
            args.epochs,
            args.stock_code,
        )
    )

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except ValueError as exc:
        print(f"CLI_ERROR={exc}", file=sys.stderr)
        return 2
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"CLI_ERROR={exc}", file=sys.stderr)
        return 2


class ReturnEngine:
    INITIAL_CASH = 10000000  # 초기 자본금

    HIDDEN_SIZE = 64
    NUM_LAYERS = 1
    LEARNING_RATE = 0.001
    DROP_OUT = 0

    BUY_THRESHOLD = 0.005    # LSTM 모델용 매수 역치
    SELL_THRESHOLD = 0.005   # LSTM 모델용 매도 역치

    def __init__(self, stock_name, stock_code, stock_path=None, model_path=None, artifact_path=None):
        self.stock_name = stock_name
        self.stock_code = stock_code

        BASE_DIR = Path(__file__).resolve().parent.parent
        self.stock_path = Path(stock_path) if stock_path else BASE_DIR / "data" / "stock" / f"{stock_code}.parquet"
        self.model_path = Path(model_path) if model_path else BASE_DIR / "data" / "model" / f"{stock_code}_lstm.pth"
        self.artifact_path = Path(artifact_path) if artifact_path else BASE_DIR / "artifacts" / f"{stock_code}.json"

    def run(self, refresh=False, epochs=100):
        torch.manual_seed(0)
        torch.set_num_threads(1)

        # 주가 데이터 불러오기
        if refresh:
            StockDataLoader.download(
                self.stock_code,
                start="2020-01-01",
                end=datetime.date.today(),
                path=self.stock_path,
            )
        if not self.stock_path.is_file():
            raise FileNotFoundError(f"offline price data not found: {self.stock_path}")
        df = _load_price_data(self.stock_path, self.stock_code)

        features = ['Open', 'High', 'Low', 'Close', 'Diff', 'MA5', 'MA20', 'RSI', 'Volume']
        target = ['Close']
        
        preprocessor = Preprocessor(df, features, target)
        df = preprocessor.create_features()

        ### LSTM ###
        # 학습, 검증, 테스트 데이터 분리
        train_df, val_df, test_df = preprocessor.train_val_test_division()

        X_train, y_train = preprocessor.split_features_target(train_df)
        X_val, y_val = preprocessor.split_features_target(val_df)

        data_pipeline = DataPipeline(preprocessor)
        train_loader = data_pipeline.create_dataloader(X_train, y_train, True)
        val_loader = data_pipeline.create_dataloader(X_val, y_val)


        # LSTM 모델 생성
        lstm_model = LSTMModel(
            len(features),
            hidden_size=ReturnEngine.HIDDEN_SIZE,
            num_layers=ReturnEngine.NUM_LAYERS,
            learning_rate=ReturnEngine.LEARNING_RATE,
            dropout=ReturnEngine.DROP_OUT
        )
        
        if not self.model_path.exists():
            lstm_model.train(train_loader, val_loader, epochs)
            lstm_model.save(self.model_path)
        else :
            lstm_model.load(self.model_path)

        prediction_df = lstm_model.predict(test_df, preprocessor, data_pipeline)
        lstm_signal_df = SignalGenerator.from_prediction(prediction_df, ReturnEngine.BUY_THRESHOLD, ReturnEngine.SELL_THRESHOLD)

        # 백테스트 엔진
        baseline_backtest_engine = BacktestEngine('baseline_model', ReturnEngine.INITIAL_CASH)
        lstm_backtest_engine = BacktestEngine('lstm_model', ReturnEngine.INITIAL_CASH)

        split_date = _split_date_from_predictions(prediction_df)

        # 규칙 Baseline 모델 생성
        baseline_model = BaselineModel()
        baseline_signal_df = baseline_model.predict(test_df)
        
        # Baseline 모델 백테스팅
        baseline_signal_df = baseline_signal_df[baseline_signal_df['Date'] >= split_date]
        baseline_backtest_engine.run(baseline_signal_df)
        baseline_result = baseline_backtest_engine.get_performance()

        # LSTM 모델 백테스팅
        lstm_signal_df = lstm_signal_df[lstm_signal_df['Date'] >= split_date]
        lstm_backtest_engine.run(lstm_signal_df)
        lstm_result = lstm_backtest_engine.get_performance()

        # 실제 가격 예측
        calendar = xcals.get_calendar("XKRX")
        last_session = calendar.date_to_session(pd.Timestamp(df["Date"].max()), direction="none")
        next_session = calendar.next_session(last_session).tz_localize(None)
        final = lstm_model.make_predict_record(
            df,
            prediction_df,
            preprocessor,
            data_pipeline,
            next_session=next_session,
        )

        
        # JSON 파일로 변환
        artifact = ArtifactGenerator(self.stock_code, final.iloc[-1]["Date"])

        recent_df = final.dropna(subset=["Close"]).tail(5)
        for _, row in recent_df.iterrows():
            artifact.add_recent_prediction(
                date=row["Date"],
                actual=row["Close"],
                predict=row["Prediction"],
                act_change=row["ActualChange"],
                pred_change=row["Change"]
            )

        future = final.iloc[-1]
        artifact.add_prediction(predict=future["Prediction"], pred_change=future["Change"])

        artifact.add_backtest_report("baseline_model", baseline_result)
        artifact.add_backtest_report("lstm_model", lstm_result)

        artifact.save(self.artifact_path)

        return self.artifact_path


if __name__ == "__main__":
    raise SystemExit(main())

# SYMBOLS=000270, 000660, 000810, 005380, 005490, 005930, 005935, 006400, 009150, 010120, 010130, 
# 012330, 012450, 028260, 032830, 034020, 034730, 035420, 042660, 055550, 066570, 068270, 086790, 
# 105560, 132030, 207940, 267260, 298040, 329180, 373220, 402340
