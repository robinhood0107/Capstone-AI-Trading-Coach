"""Ridge는 기준일 뒤의 정답을 fit하지 않고 실제 거래일을 사용한다."""

import json
import math

import pandas as pd
import pytest

from app.data.calendar.xkrx_policy import corrected_calendar
from app.p1_owner.assets import FEATURE_ORDER
from app.p1_owner.daily_inference import _features_and_rule
from app.p1_owner.ridge_returns import RidgeReturnError, fit_forecasts, predict


def history(end="2026-08-14"):
    days = corrected_calendar().sessions_in_range(pd.Timestamp("2026-01-01"), pd.Timestamp(end))[
        -100:
    ]
    return [
        {
            "sessionDate": day.date().isoformat(),
            "close": 10000 + i * 7 + 100 * math.sin(i),
            "open": 10000 + i * 7,
            "high": 11000 + i * 7,
            "low": 9000 + i * 7,
            "volume": 1000 + i,
        }
        for i, day in enumerate(days)
    ]


def test_horizons_use_mature_labels_and_holiday_correction():
    rows = history()
    features, _ = _features_and_rule(rows, rows[-1]["sessionDate"], full_history=True)
    result = fit_forecasts("005930", rows, features, FEATURE_ORDER)
    models = json.loads(result["modelJson"])["models"]
    assert [item["horizonSessions"] for item in result["forecasts"]] == [1, 5, 20]
    assert result["forecasts"][0]["targetSession"] == "2026-08-18"
    for model, forecast in zip(models, result["forecasts"], strict=True):
        h = model["horizonSessions"]
        assert model["lastFeatureSession"] == rows[-h - 1]["sessionDate"]
        assert model["trainSamples"] == len(features) - h
        assert predict(model, features[-1]) == forecast["expectedReturn"]
        assert forecast["forecastClose"] == rows[-1]["close"] * (1 + forecast["expectedReturn"])


def test_missing_session_is_rejected():
    rows = history()
    del rows[45]
    features, _ = _features_and_rule(rows, rows[-1]["sessionDate"], full_history=True)
    with pytest.raises(RidgeReturnError, match="SESSION_GAP"):
        fit_forecasts("005930", rows, features, FEATURE_ORDER)


def test_zero_scale_and_corrupt_model_are_rejected():
    with pytest.raises(RidgeReturnError, match="SCALE"):
        predict({"scale": [0], "mean": [0], "coefficients": [1], "intercept": 0}, [1])


def test_gap_up_and_cost_leave_no_expected_buy_profit():
    from app.p1_owner.automation import SignalCandidate, remaining_expected_return

    candidate = SignalCandidate("005930", "BUY", "BUY", 0.01, forecast_close=10100.0)
    assert remaining_expected_return(candidate, 10000) > 0
    assert remaining_expected_return(candidate, 10100) < 0
    assert remaining_expected_return(candidate, 10090) < 0


def test_combination_method_is_bound_even_when_returns_are_equal():
    from dataclasses import replace
    from app.p1_owner.automation import SignalCandidate, _candidate_set_sha256

    candidate = SignalCandidate(
        "005930",
        "BUY",
        "BUY",
        0.01,
        forecast_close=10100.0,
        combination_method="EQUAL_WEIGHT_50_50",
    )
    assert _candidate_set_sha256((candidate,)) != _candidate_set_sha256(
        (replace(candidate, combination_method=None),)
    )
