import json
import math
import os
from pathlib import Path


def _reject_non_finite(value):
    if isinstance(value, dict):
        return {key: _reject_non_finite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_reject_non_finite(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("artifact contains a non-finite number")
    return value

class ArtifactGenerator:
    def __init__(self, stock_code, date):
        self.artifact = {
            "stock_code": stock_code,
            "date": date.strftime("%Y-%m-%d"),
            "prediction": [],
            "recent_prediction": [],
            "backtest": {}
        }

    def add_prediction(self, predict, pred_change):
        self.artifact["prediction"] = {
            "prediction": predict,
            "prediction_change": pred_change
        }

    def add_recent_prediction(self, date, actual, predict, act_change, pred_change):
        self.artifact["recent_prediction"].append({
            "date": date.strftime("%Y-%m-%d"),
            "actual": actual,
            "prediction": predict,
            "actual_change": act_change,
            "prediction_change": pred_change
        })

    def add_backtest_report(self, model_name, report):
        self.artifact["backtest"][model_name] = report

    def save(self, path):
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        payload = _reject_non_finite(self.artifact)
        with open(temporary, 'w', encoding="utf-8", newline="\n") as f:
            json.dump(
                payload,
                f,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
                default=float,
            )
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, destination)
