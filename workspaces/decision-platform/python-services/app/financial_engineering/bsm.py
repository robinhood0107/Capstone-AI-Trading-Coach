from __future__ import annotations

import math
from dataclasses import dataclass

from app.financial_engineering._validation import _raise_stable, _validate_finite_scalar


@dataclass(frozen=True)
class BSMInputs:
    option_right: str
    underlying_price: float
    strike: float
    tau: float
    risk_free_rate: float
    dividend_yield: float
    volatility: float


@dataclass(frozen=True)
class BSMPrice:
    option_right: str
    theoretical_price: float
    d1: float
    d2: float
    measure: str = "Q_DISCOUNTED_VALUE"


def normal_cdf(value: float) -> float:
    """외부 수치 runtime 없이 math.erfc로 표준정규 CDF를 계산한다."""
    return 0.5 * math.erfc(-value / math.sqrt(2.0))


def normal_pdf(value: float) -> float:
    return math.exp(-0.5 * value * value) / math.sqrt(2.0 * math.pi)


def validate_bsm_inputs(
    *,
    option_right: object,
    underlying_price: object,
    strike: object,
    tau: object,
    risk_free_rate: object,
    dividend_yield: object,
    volatility: object,
) -> BSMInputs:
    if option_right not in {"CALL", "PUT"}:
        _raise_stable("option_right_invalid")
    s = _validate_finite_scalar(underlying_price, code="underlying_price_invalid")
    k = _validate_finite_scalar(strike, code="strike_invalid")
    t = _validate_finite_scalar(tau, code="tau_invalid")
    r = _validate_finite_scalar(risk_free_rate, code="risk_free_rate_invalid")
    q = _validate_finite_scalar(dividend_yield, code="dividend_yield_invalid")
    sigma = _validate_finite_scalar(volatility, code="volatility_invalid")
    if s <= 0 or k <= 0 or t <= 0 or sigma <= 0:
        _raise_stable("bsm_domain_invalid")
    return BSMInputs(str(option_right), s, k, t, r, q, sigma)


def _d1_d2(inputs: BSMInputs) -> tuple[float, float]:
    denominator = inputs.volatility * math.sqrt(inputs.tau)
    d1 = (
        math.log(inputs.underlying_price / inputs.strike)
        + (
            inputs.risk_free_rate
            - inputs.dividend_yield
            + 0.5 * inputs.volatility * inputs.volatility
        )
        * inputs.tau
    ) / denominator
    d2 = d1 - denominator
    if not math.isfinite(d1) or not math.isfinite(d2):
        _raise_stable("result_non_finite")
    return d1, d2


def black_scholes(
    *,
    option_right: object,
    underlying_price: object,
    strike: object,
    tau: object,
    risk_free_rate: object,
    dividend_yield: object,
    volatility: object,
) -> BSMPrice:
    """European continuous-q BSM의 Q-discounted value를 계산한다.

    계산 결과는 교육 valuation이며 예측 평균, hard risk delta, 주문 신호가 아니다.
    """
    inputs = validate_bsm_inputs(
        option_right=option_right,
        underlying_price=underlying_price,
        strike=strike,
        tau=tau,
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield,
        volatility=volatility,
    )
    d1, d2 = _d1_d2(inputs)
    discounted_spot = inputs.underlying_price * math.exp(-inputs.dividend_yield * inputs.tau)
    discounted_strike = inputs.strike * math.exp(-inputs.risk_free_rate * inputs.tau)
    if inputs.option_right == "CALL":
        value = discounted_spot * normal_cdf(d1) - discounted_strike * normal_cdf(d2)
    else:
        value = discounted_strike * normal_cdf(-d2) - discounted_spot * normal_cdf(-d1)
    if not math.isfinite(value) or value < 0:
        _raise_stable("result_non_finite")
    return BSMPrice(inputs.option_right, value, d1, d2)


def discounted_no_arbitrage_bounds(
    *,
    option_right: object,
    underlying_price: object,
    strike: object,
    tau: object,
    risk_free_rate: object,
    dividend_yield: object,
) -> tuple[float, float]:
    """q-inclusive discounted European call/put price bounds를 반환한다."""
    inputs = validate_bsm_inputs(
        option_right=option_right,
        underlying_price=underlying_price,
        strike=strike,
        tau=tau,
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield,
        volatility=1.0,
    )
    discounted_spot = inputs.underlying_price * math.exp(-inputs.dividend_yield * inputs.tau)
    discounted_strike = inputs.strike * math.exp(-inputs.risk_free_rate * inputs.tau)
    if inputs.option_right == "CALL":
        return max(0.0, discounted_spot - discounted_strike), discounted_spot
    return max(0.0, discounted_strike - discounted_spot), discounted_strike
