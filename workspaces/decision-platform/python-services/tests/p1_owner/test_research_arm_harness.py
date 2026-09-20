from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest


RESEARCH = Path(__file__).resolve().parents[3] / "research" / "p1-return-profit-verification"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, RESEARCH / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_horizon_maturity_does_not_limit_other_horizons() -> None:
    ridge = load("ridge_walk_forward")
    frame = pd.DataFrame({name: [1.0, 2.0, 3.0] for name in ridge.FEATURES})
    frame["TargetSimpleRet1"] = [0.1, 0.2, 0.3]
    frame["TargetSimpleRet5"] = [0.1, 0.2, np.nan]
    frame["TargetSimpleRet20"] = [0.1, np.nan, np.nan]

    assert len(ridge.mature_horizon_frame(frame, 1)) == 3
    assert len(ridge.mature_horizon_frame(frame, 5)) == 2
    assert len(ridge.mature_horizon_frame(frame, 20)) == 1


def test_one_ridge_arm_abstention_does_not_delete_lstm_rows() -> None:
    ridge = load("ridge_walk_forward")
    keys = {
        "testYear": [2026, 2026],
        "date": pd.to_datetime(["2026-09-01", "2026-09-02"]),
        "ticker": ["005930.KS", "005930.KS"],
    }
    lstm = pd.DataFrame(keys | {"predLogRet": [0.01, 0.02], "actualLogRet": [0.0, 0.01]})
    ridge_rows = pd.DataFrame(
        keys | {"predRidgeRet1": [0.01, np.nan], "predRidgeRet5": [0.03, 0.04]}
    )

    merged = ridge.merge_arm_predictions(lstm, ridge_rows)

    assert len(merged) == len(lstm)
    assert merged["predLstmRet"].notna().all()
    assert merged["predRidgeRet"].isna().sum() == 1
    assert merged["predRidgeRet5"].notna().all()


def test_abstention_stays_cash_and_is_reported_as_coverage() -> None:
    arm = load("arm_comparison_eval")
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-01", "2026-09-02"]),
            "ticker": ["005930.KS", "005930.KS"],
            "score": [1.0, np.nan],
            "actualSimpleRet": [0.1, 0.1],
        }
    )

    equity = arm.simulate(frame, 1, score="score")
    coverage = arm._selection_coverage(frame, "score", 1)

    assert equity.iloc[0] < 1.1
    assert coverage["coverage"] == 0.1
    assert coverage["cashPolicy"] == "UNFILLED_TOP_K_SLOTS_STAY_CASH"


def test_missing_held_terminal_return_is_not_filled_with_zero_or_minus_one() -> None:
    arm = load("arm_comparison_eval")
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-09-01", "2026-09-02"]),
            "ticker": ["005930.KS", "000660.KS"],
            "score": [1.0, 1.0],
            "actualSimpleRet": [0.1, 0.1],
        }
    )
    with pytest.raises(ValueError, match="MISSING_TERMINAL_RETURN"):
        arm.simulate(frame, 20, score="score")


def test_prediction_metrics_use_the_matching_horizon_and_expose_abstention() -> None:
    arm = load("arm_comparison_eval")
    frame = pd.DataFrame(
        {
            "testYear": [2025, 2025, 2026],
            "prediction": [0.1, 0.2, np.nan],
            "actual1": [0.1, -0.2, 0.3],
            "actual5": [0.08, 0.18, 0.28],
        }
    )
    value = arm._prediction_accuracy(frame, "prediction", "actual5", 5)
    assert value["horizonSessions"] == 5
    assert value["rows"] == 2
    assert value["abstentionRows"] == 1
    assert value["coverage"] == pytest.approx(2 / 3, abs=1e-6)
