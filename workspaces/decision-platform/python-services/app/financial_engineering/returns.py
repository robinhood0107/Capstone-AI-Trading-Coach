from __future__ import annotations

import numpy as np

from app.financial_engineering._validation import (
    FloatArray,
    NumericInput,
    _ensure_finite_array,
    _ensure_finite_scalar,
    _raise_stable,
    _validate_numeric_input,
    _validate_periods_per_year,
)


def simple_returns(prices: NumericInput) -> FloatArray:
    """양의 가격 시계열에서 인접 단순수익률을 새 float64 배열로 반환한다.

    입력·출력과 오류 계약은 `shared-docs/metrics_definitions.md`를 따른다.
    """
    values = _validate_numeric_input(prices, min_length=2)
    if bool(np.any(values <= 0.0)):
        _raise_stable("prices_non_positive")

    try:
        with np.errstate(over="raise", divide="raise", invalid="raise", under="ignore"):
            result = values[1:] / values[:-1] - 1.0
    except (FloatingPointError, OverflowError):
        _raise_stable("result_non_finite")
    return _ensure_finite_array(result)


def log_returns(prices: NumericInput) -> FloatArray:
    """양의 가격 시계열에서 인접 로그수익률을 새 float64 배열로 반환한다.

    가격 비율 대신 로그 차를 사용하며 계약은 `shared-docs/metrics_definitions.md`를 따른다.
    """
    values = _validate_numeric_input(prices, min_length=2)
    if bool(np.any(values <= 0.0)):
        _raise_stable("prices_non_positive")

    try:
        with np.errstate(over="raise", divide="raise", invalid="raise", under="ignore"):
            result = np.log(values[1:]) - np.log(values[:-1])
    except (FloatingPointError, OverflowError):
        _raise_stable("result_non_finite")
    return _ensure_finite_array(result)


def cumulative_return(returns: NumericInput) -> float:
    """단순수익률 시계열을 복리 누적한 signed decimal 수익률로 반환한다.

    `-1`은 전손으로 허용하며 입력·출력 계약은 `shared-docs/metrics_definitions.md`를 따른다.
    """
    values = _validate_numeric_input(returns, min_length=1)
    if bool(np.any(values < -1.0)):
        _raise_stable("simple_return_below_minus_one")
    # 전손 뒤의 곱은 결과에 영향을 주지 않으므로 불필요한 overflow 전에 종료한다.
    if bool(np.any(values == -1.0)):
        return -1.0

    try:
        with np.errstate(over="raise", divide="raise", invalid="raise", under="ignore"):
            result = np.prod(1.0 + values, dtype=np.float64) - 1.0
    except (FloatingPointError, OverflowError):
        _raise_stable("result_non_finite")
    return _ensure_finite_scalar(result)


def cagr(prices: NumericInput, *, periods_per_year: int = 252) -> float:
    """균등 주기 양의 가격 시계열의 연복리성장률을 signed decimal로 반환한다.

    연간 주기 수와 가격 가정은 `shared-docs/metrics_definitions.md`를 따른다.
    """
    values = _validate_numeric_input(prices, min_length=2)
    validated_periods = _validate_periods_per_year(periods_per_year)
    if bool(np.any(values <= 0.0)):
        _raise_stable("prices_non_positive")

    try:
        with np.errstate(over="raise", divide="raise", invalid="raise", under="ignore"):
            annualization = float(validated_periods) / float(values.size - 1)
            log_growth = np.log(values[-1]) - np.log(values[0])
            result = np.expm1(annualization * log_growth)
    except (FloatingPointError, OverflowError):
        _raise_stable("result_non_finite")
    return _ensure_finite_scalar(result)
