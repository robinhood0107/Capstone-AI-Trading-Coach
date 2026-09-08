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


def test_the_research_arm_fits_the_same_estimator_as_production():
    """검증 하네스의 Ridge arm 이 화면이 쓰는 것과 같은 추정기인지 대조한다.

    왜 필요한가. `research/p1-return-profit-verification/ridge_walk_forward.py` 는
    `fit_forecasts` 를 통째로 부르지 않는다 - 그 함수가 이력의 세션이 XKRX 달력과 빈틈없이
    일치할 것을 요구하는데, 21년치 yfinance 행은 달력과 행 단위로 맞지 않는다. 그래서 fit
    절차만 같은 상수로 다시 쓰고 추론은 production `predict` 를 그대로 부른다.

    그 "같은 상수"가 갈라지면 검증이 화면과 다른 모델을 재고, 그 결과로 채택 상태를 쓰면
    거짓이 된다. 그래서 같은 입력으로 두 경로를 fit 해 **예측이 일치하는지** 본다.
    """

    import numpy as np
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler

    from app.p1_owner.ridge_returns import MIN_TRAIN_ROWS

    rows = history()
    features, _ = _features_and_rule(rows, rows[-1]["sessionDate"], full_history=True)
    closes = np.asarray([float(item["close"]) for item in rows])
    offset = len(rows) - len(features)
    horizon = 1

    # production 경로.
    produced = fit_forecasts("005930", rows, features, FEATURE_ORDER)
    production_model = json.loads(produced["modelJson"])
    production_fit = next(
        item for item in production_model["models"] if item["horizonSessions"] == horizon
    )

    # 하네스 경로. 같은 상수, 같은 타깃 정의로 다시 fit 한다.
    count = len(features) - horizon
    assert count >= MIN_TRAIN_ROWS
    x = np.asarray(features, dtype=float)[:count]
    y = closes[offset + horizon :] / closes[offset:-horizon] - 1.0
    scaler = StandardScaler().fit(x)
    regressor = Ridge(alpha=1.0, solver="svd").fit(scaler.transform(x), y)
    research_fit = json.loads(
        json.dumps(
            {
                "mean": scaler.mean_.tolist(),
                "scale": scaler.scale_.tolist(),
                "coefficients": regressor.coef_.tolist(),
                "intercept": float(regressor.intercept_),
            }
        )
    )

    last = list(features[-1])
    assert predict(research_fit, last) == pytest.approx(predict(production_fit, last), abs=1e-12)
    # 계수 자체도 같아야 한다. 예측만 우연히 맞는 경우를 배제한다.
    assert research_fit["coefficients"] == pytest.approx(production_fit["coefficients"], abs=1e-12)
    assert research_fit["intercept"] == pytest.approx(production_fit["intercept"], abs=1e-12)


def test_production_ridge_targets_simple_returns_not_log_returns():
    """타깃 단위를 고정한다.

    production Ridge 는 단순수익률을, LSTM 하네스는 로그수익률을 예측한다. 결합 arm 이
    단위를 맞추지 않으면 50:50 이 두 다른 척도의 평균이 되어 비교가 무의미해진다.
    """

    rows = history()
    features, _ = _features_and_rule(rows, rows[-1]["sessionDate"], full_history=True)
    produced = fit_forecasts("005930", rows, features, FEATURE_ORDER)
    forecast = next(item for item in produced["forecasts"] if item["horizonSessions"] == 1)

    close = float(rows[-1]["close"])
    # 단순수익률 정의: forecastClose = close * (1 + expectedReturn)
    assert forecast["forecastClose"] == pytest.approx(
        close * (1.0 + forecast["expectedReturn"]), rel=1e-12
    )
