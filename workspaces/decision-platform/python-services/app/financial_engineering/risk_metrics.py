from __future__ import annotations

import numpy as np

from app.financial_engineering._validation import (
    FloatArray,
    NumericInput,
    _ensure_finite_scalar,
    _raise_stable,
    _validate_confidence,
    _validate_finite_scalar,
    _validate_numeric_input,
    _validate_periods_per_year,
)


def _realized_volatility_kernel(values: FloatArray) -> float:
    return float(np.std(values, ddof=1, dtype=np.float64))


def _historical_var_kernel(values: FloatArray, confidence: float) -> float:
    return float(np.quantile(values, 1.0 - confidence, method="linear"))


def _threshold_tail_mean(values: FloatArray, threshold: float) -> float:
    tail = values[values <= threshold]
    if tail.size == 0:
        _raise_stable("tail_empty")
    try:
        with np.errstate(over="raise", divide="raise", invalid="raise", under="ignore"):
            result = np.mean(tail, dtype=np.float64)
    except (FloatingPointError, OverflowError):
        _raise_stable("result_non_finite")
    return _ensure_finite_scalar(result)


def realized_volatility(log_returns: NumericInput) -> float:
    """일별 로그수익률의 N-1 표본 표준편차를 비연환산 변동성으로 반환한다.

    입력·출력 가정과 명칭 한계는 `shared-docs/metrics_definitions.md`를 따른다.
    """
    values = _validate_numeric_input(log_returns, min_length=2)
    try:
        with np.errstate(over="raise", divide="raise", invalid="raise", under="ignore"):
            result = _realized_volatility_kernel(values)
    except (FloatingPointError, OverflowError):
        _raise_stable("result_non_finite")
    return _ensure_finite_scalar(result)


def annualized_volatility(
    log_returns: NumericInput,
    *,
    periods_per_year: int = 252,
) -> float:
    """일별 로그수익률 표본 변동성을 연간 주기 수의 제곱근으로 연환산한다.

    252 주기 관례와 한계는 `shared-docs/metrics_definitions.md`를 따른다.
    """
    values = _validate_numeric_input(log_returns, min_length=2)
    validated_periods = _validate_periods_per_year(periods_per_year)
    try:
        with np.errstate(over="raise", divide="raise", invalid="raise", under="ignore"):
            result = _realized_volatility_kernel(values) * np.sqrt(float(validated_periods))
    except (FloatingPointError, OverflowError):
        _raise_stable("result_non_finite")
    return _ensure_finite_scalar(result)


def max_drawdown(equity_curve: NumericInput) -> float:
    """자산곡선의 running peak 대비 최저 낙폭을 signed decimal `[-1, 0]`로 반환한다.

    초기값과 이후 equity domain은 `shared-docs/metrics_definitions.md`를 따른다.
    """
    values = _validate_numeric_input(equity_curve, min_length=1)
    if values[0] <= 0.0:
        _raise_stable("equity_initial_non_positive")
    if bool(np.any(values[1:] < 0.0)):
        _raise_stable("equity_negative")

    try:
        with np.errstate(over="raise", divide="raise", invalid="raise", under="ignore"):
            running_peak = np.maximum.accumulate(values)
            result = np.min(values / running_peak - 1.0)
    except (FloatingPointError, OverflowError):
        _raise_stable("result_non_finite")
    return _ensure_finite_scalar(result)


def sharpe_ratio(
    returns: NumericInput,
    *,
    risk_free_rate: int | float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """동일 주기의 무위험수익률을 뺀 N-1 표본 Sharpe ratio를 연환산해 반환한다.

    입력·부호·주기 가정은 `shared-docs/metrics_definitions.md`를 따른다.
    """
    values = _validate_numeric_input(returns, min_length=2)
    validated_risk_free = _validate_finite_scalar(
        risk_free_rate,
        code="risk_free_rate_invalid",
    )
    validated_periods = _validate_periods_per_year(periods_per_year)

    try:
        with np.errstate(over="raise", divide="raise", invalid="raise", under="ignore"):
            excess = values - validated_risk_free
            denominator = np.std(excess, ddof=1, dtype=np.float64)
            if denominator == 0.0:
                _raise_stable("denominator_zero")
            result = (
                np.mean(excess, dtype=np.float64) / denominator * np.sqrt(float(validated_periods))
            )
    except (FloatingPointError, OverflowError):
        _raise_stable("result_non_finite")
    return _ensure_finite_scalar(result)


def sortino_ratio(
    returns: NumericInput,
    *,
    target_return: int | float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """목표수익률 미달분을 전체 표본으로 나눈 downside deviation 기반 Sortino를 반환한다.

    목표·부호·연환산 가정은 `shared-docs/metrics_definitions.md`를 따른다.
    """
    values = _validate_numeric_input(returns, min_length=2)
    validated_target = _validate_finite_scalar(
        target_return,
        code="target_return_invalid",
    )
    validated_periods = _validate_periods_per_year(periods_per_year)

    try:
        with np.errstate(over="raise", divide="raise", invalid="raise", under="ignore"):
            excess = values - validated_target
            downside_values = np.minimum(excess, 0.0)
            denominator = np.sqrt(np.mean(downside_values * downside_values, dtype=np.float64))
            if denominator == 0.0:
                _raise_stable("denominator_zero")
            result = (
                np.mean(excess, dtype=np.float64) / denominator * np.sqrt(float(validated_periods))
            )
    except (FloatingPointError, OverflowError):
        _raise_stable("result_non_finite")
    return _ensure_finite_scalar(result)


def historical_var(
    returns: NumericInput,
    *,
    confidence: int | float = 0.95,
) -> float:
    """수익률의 linear quantile을 signed lower-tail historical VaR로 반환한다.

    confidence와 부호 계약은 `shared-docs/metrics_definitions.md`를 따른다.
    """
    values = _validate_numeric_input(returns, min_length=2)
    validated_confidence = _validate_confidence(confidence)
    try:
        with np.errstate(over="raise", divide="raise", invalid="raise", under="ignore"):
            result = _historical_var_kernel(values, validated_confidence)
    except (FloatingPointError, OverflowError):
        _raise_stable("result_non_finite")
    return _ensure_finite_scalar(result)


def historical_cvar(
    returns: NumericInput,
    *,
    confidence: int | float = 0.95,
) -> float:
    """VaR 이하 관측의 평균을 signed lower-tail v1 threshold CVaR로 반환한다.

    exact finite-sample ES가 아니며 계약은 `shared-docs/metrics_definitions.md`를 따른다.
    """
    values = _validate_numeric_input(returns, min_length=2)
    validated_confidence = _validate_confidence(confidence)
    try:
        with np.errstate(over="raise", divide="raise", invalid="raise", under="ignore"):
            threshold = _ensure_finite_scalar(_historical_var_kernel(values, validated_confidence))
            result = _threshold_tail_mean(values, threshold)
    except (FloatingPointError, OverflowError):
        _raise_stable("result_non_finite")
    return _ensure_finite_scalar(result)
