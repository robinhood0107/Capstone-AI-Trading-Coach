import datetime
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


class ReturnEngine:
    INITIAL_CASH = 10000000  # 초기 자본금

    HIDDEN_SIZE = 128
    NUM_LAYERS = 3
    LEARNING_RATE = 0.0005

    BUY_THRESHOLD = 0.005    # LSTM 모델용 매수 역치
    SELL_THRESHOLD = 0.005   # LSTM 모델용 매도 역치

    def __init__(self, stock_name, stock_code, stock_path=None, model_path=None, artifact_path=None):
        self.stock_name = stock_name
        self.stock_code = stock_code

        BASE_DIR = Path(__file__).resolve().parent.parent
        self.stock_path = Path(stock_path) if stock_path else BASE_DIR / "data" / "stock" / f"{stock_code}.csv"
        self.model_path = Path(model_path) if model_path else BASE_DIR / "data" / "model" / f"{stock_code}_lstm.pth"
        self.artifact_path = Path(artifact_path) if artifact_path else BASE_DIR / "artifacts" / f"{stock_code}.json"

    def run(self, refresh=False):
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
            raise FileNotFoundError(f"offline CSV not found: {self.stock_path}")
        if not self.model_path.is_file():
            raise FileNotFoundError(f"received PTH not found: {self.model_path}")
        df = pd.read_csv(self.stock_path)

        features = ['Open', 'High', 'Low', 'Close', 'Adj Close', 'Diff', 'MA5', 'MA20', 'RSI', 'Volume']
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
            learning_rate=ReturnEngine.LEARNING_RATE
        )
        
        if not self.model_path.exists():
            lstm_model.train(train_loader, val_loader, 100)
            lstm_model.save(self.model_path)
        else :
            lstm_model.load(self.model_path)

        prediction_df = lstm_model.predict(test_df, preprocessor, data_pipeline)
        lstm_signal_df = SignalGenerator.from_prediction(prediction_df, ReturnEngine.BUY_THRESHOLD, ReturnEngine.SELL_THRESHOLD)

        # 백테스트 엔진
        baseline_backtest_engine = BacktestEngine('baseline_model', ReturnEngine.INITIAL_CASH)
        lstm_backtest_engine = BacktestEngine('lstm_model', ReturnEngine.INITIAL_CASH)

        split_date = prediction_df.iloc[0, 0]

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

