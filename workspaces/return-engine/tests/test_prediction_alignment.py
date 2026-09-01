from __future__ import annotations

from unittest.mock import Mock

import joblib
import numpy as np
import pandas as pd
import pytest
import torch

from backtest_core.signal_generator import SignalGenerator
from dataloader.preprocessor import Preprocessor
from models.lstm import LSTMModel
from return_engine import _split_date_from_predictions


class _Preprocessor:
    def split_features_target(self, frame: pd.DataFrame):
        return frame[["feature"]], frame[["target"]]

    def inverse_transform(self, values: np.ndarray) -> np.ndarray:
        return values

    def transform(self, values: pd.DataFrame):
        return values.to_numpy(dtype=np.float32), None


class _Pipeline:
    def __init__(self, window_size: int, output_count: int) -> None:
        self.window_size = window_size
        self._output_count = output_count

    def create_dataloader(self, _x, _y):
        return [
            (
                torch.zeros((self._output_count, self.window_size, 1), dtype=torch.float32),
                torch.zeros((self._output_count, 1), dtype=torch.float32),
            )
        ]


def _frame(rows: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Date": pd.date_range("2026-01-01", periods=rows, freq="D"),
            "feature": np.arange(rows, dtype=np.float32),
            "target": np.arange(rows, dtype=np.float32),
        }
    )


def test_prediction_change_and_signal_share_the_same_row() -> None:
    source = pd.DataFrame(
        {
            "Date": pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"]),
            "Close": [100.0, 100.0, 100.0],
            "Prediction": [100.0, 102.0, 99.0],
        }
    )

    result = SignalGenerator.from_prediction(source, buy_threshold=0.01, sell_threshold=0.01)

    assert result.loc[result["Signal"] == "BUY", "Date"].tolist() == [pd.Timestamp("2026-01-05")]
    assert result.loc[result["Signal"] == "SELL", "Date"].tolist() == [pd.Timestamp("2026-01-06")]


@pytest.mark.parametrize("window_size", [20, 30])
def test_prediction_rows_follow_the_pipeline_window_size(window_size: int) -> None:
    frame = _frame(window_size + 5)
    pipeline = _Pipeline(window_size, output_count=5)
    model = LSTMModel(input_size=1, hidden_size=2, num_layers=1)

    result = model.predict(frame, _Preprocessor(), pipeline)

    assert len(result) == 5
    assert result["Date"].tolist() == frame["Date"].iloc[window_size:].tolist()


def test_prediction_count_mismatch_is_rejected() -> None:
    frame = _frame(25)
    model = LSTMModel(input_size=1, hidden_size=2, num_layers=1)

    with pytest.raises(ValueError, match="prediction count"):
        model.predict(frame, _Preprocessor(), _Pipeline(window_size=20, output_count=4))


def test_make_prediction_record_uses_the_pipeline_window_owner() -> None:
    frame = _frame(30)
    predicted = frame.iloc[20:].copy().reset_index(drop=True)
    predicted["Prediction"] = np.linspace(100.0, 110.0, len(predicted))
    for column in ("Open", "High", "Low", "Close", "Volume", "MA5", "MA20", "RSI"):
        predicted[column] = 100.0
    pipeline = _Pipeline(window_size=20, output_count=len(predicted))
    model = LSTMModel(input_size=1, hidden_size=2, num_layers=1)
    model.forecast = Mock(return_value=111.0)
    preprocessor = _Preprocessor()

    result = model.make_predict_record(
        frame,
        predicted,
        preprocessor,
        pipeline,
        next_session=pd.Timestamp("2026-02-16"),
    )

    model.forecast.assert_called_once_with(frame, preprocessor, pipeline)
    assert result.iloc[-1]["Prediction"] == 111.0


def test_scaler_save_contains_both_owned_scalers(tmp_path) -> None:
    preprocessor = Preprocessor(features=["feature"], target=["target"])
    x_data = pd.DataFrame({"feature": [1.0, 2.0, 3.0]})
    y_data = pd.DataFrame({"target": [10.0, 20.0, 30.0]})
    preprocessor.fit_transform(x_data, y_data)
    output = preprocessor.save_scaler(tmp_path / "owned-scaler.gz")

    # This is a test-owned file written in the same process, not a production loader.
    stored = joblib.load(output)
    assert set(stored) == {"x_scaler", "y_scaler"}
    np.testing.assert_allclose(stored["x_scaler"].transform(x_data), preprocessor.x_scaler.transform(x_data))
    np.testing.assert_allclose(stored["y_scaler"].transform(y_data), preprocessor.y_scaler.transform(y_data))


def test_split_date_uses_the_named_date_column_not_column_position() -> None:
    prediction = pd.DataFrame(
        {
            "Prediction": [101.0],
            "Date": [pd.Timestamp("2026-02-02")],
            "Close": [100.0],
        }
    )

    assert _split_date_from_predictions(prediction) == pd.Timestamp("2026-02-02")
