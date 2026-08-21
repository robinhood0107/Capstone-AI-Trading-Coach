from __future__ import annotations

import math
from dataclasses import dataclass

from app.financial_engineering._validation import _raise_stable, _validate_finite_scalar
from app.financial_engineering.bsm import black_scholes, discounted_no_arbitrage_bounds

LOWER_VOLATILITY = 0.0001
UPPER_VOLATILITY = 5.0
MAX_ITERATIONS_CAP = 1_000


@dataclass(frozen=True)
class ImpliedVolatilityResult:
    implied_volatility: float
    iterations: int
    pricing_error: float
    status: str = "CONVERGED"
    solver: str = "BISECTION"
    warning: str = "CALIBRATION_IDENTITY"


def implied_volatility(
    *,
    option_right: object,
    market_price: object,
    underlying_price: object,
    strike: object,
    tau: object,
    risk_free_rate: object,
    dividend_yield: object,
    tolerance: object = 1e-8,
    max_iterations: object = 100,
) -> ImpliedVolatilityResult:
    """고정 [0.0001,5.0] bracket의 bounded bisection으로 BSM IV를 역산한다."""
    price = _validate_finite_scalar(market_price, code="market_price_invalid")
    validated_tolerance = _validate_finite_scalar(tolerance, code="tolerance_invalid")
    if price <= 0 or validated_tolerance <= 0 or validated_tolerance > 0.01:
        _raise_stable("iv_domain_invalid")
    if type(max_iterations) is not int or not 1 <= max_iterations <= MAX_ITERATIONS_CAP:
        _raise_stable("max_iterations_invalid")
    lower_bound, upper_bound = discounted_no_arbitrage_bounds(
        option_right=option_right,
        underlying_price=underlying_price,
        strike=strike,
        tau=tau,
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield,
    )
    if price < lower_bound - validated_tolerance or price > upper_bound + validated_tolerance:
        _raise_stable("IV_NOT_BRACKETED")

    def residual(volatility: float) -> float:
        return black_scholes(
            option_right=option_right,
            underlying_price=underlying_price,
            strike=strike,
            tau=tau,
            risk_free_rate=risk_free_rate,
            dividend_yield=dividend_yield,
            volatility=volatility,
        ).theoretical_price - price

    low = LOWER_VOLATILITY
    high = UPPER_VOLATILITY
    low_residual = residual(low)
    high_residual = residual(high)
    if abs(low_residual) <= validated_tolerance:
        return ImpliedVolatilityResult(low, 0, low_residual)
    if abs(high_residual) <= validated_tolerance:
        return ImpliedVolatilityResult(high, 0, high_residual)
    if math.copysign(1.0, low_residual) == math.copysign(1.0, high_residual):
        _raise_stable("IV_NOT_BRACKETED")
    for iteration in range(1, max_iterations + 1):
        middle = (low + high) / 2.0
        middle_residual = residual(middle)
        if abs(middle_residual) <= validated_tolerance or (high - low) / 2.0 <= validated_tolerance:
            return ImpliedVolatilityResult(middle, iteration, middle_residual)
        if math.copysign(1.0, middle_residual) == math.copysign(1.0, low_residual):
            low = middle
            low_residual = middle_residual
        else:
            high = middle
    _raise_stable("IV_NOT_CONVERGED")
