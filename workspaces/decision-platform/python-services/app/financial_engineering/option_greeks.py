from __future__ import annotations

import math
from dataclasses import dataclass

from app.financial_engineering.bsm import _d1_d2, normal_cdf, normal_pdf, validate_bsm_inputs


@dataclass(frozen=True)
class OptionGreeks:
    option_right: str
    delta: float
    gamma: float
    vega_per_unit_volatility: float
    vega_per_vol_point: float
    calendar_theta_per_year: float
    calendar_theta_per_day: float
    rho_per_unit_rate: float
    rho_per_rate_point: float
    measure: str = "Q_DISCOUNTED_VALUE"


def option_greeks(
    *,
    option_right: object,
    underlying_price: object,
    strike: object,
    tau: object,
    risk_free_rate: object,
    dividend_yield: object,
    volatility: object,
) -> OptionGreeks:
    """valuation Delta와 명시적 단위의 analytic BSM Greeks를 계산한다.

    반환 Delta는 conservativeRiskDelta와 다른 valuation estimator다.
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
    discount_q = math.exp(-inputs.dividend_yield * inputs.tau)
    discount_r = math.exp(-inputs.risk_free_rate * inputs.tau)
    pdf = normal_pdf(d1)
    root_tau = math.sqrt(inputs.tau)
    gamma = discount_q * pdf / (inputs.underlying_price * inputs.volatility * root_tau)
    vega = inputs.underlying_price * discount_q * pdf * root_tau
    common_theta = (
        -inputs.underlying_price * discount_q * pdf * inputs.volatility / (2.0 * root_tau)
    )
    if inputs.option_right == "CALL":
        delta = discount_q * normal_cdf(d1)
        theta = (
            common_theta
            - inputs.risk_free_rate * inputs.strike * discount_r * normal_cdf(d2)
            + inputs.dividend_yield * inputs.underlying_price * discount_q * normal_cdf(d1)
        )
        rho = inputs.strike * inputs.tau * discount_r * normal_cdf(d2)
    else:
        delta = discount_q * (normal_cdf(d1) - 1.0)
        theta = (
            common_theta
            + inputs.risk_free_rate * inputs.strike * discount_r * normal_cdf(-d2)
            - inputs.dividend_yield * inputs.underlying_price * discount_q * normal_cdf(-d1)
        )
        rho = -inputs.strike * inputs.tau * discount_r * normal_cdf(-d2)
    return OptionGreeks(
        option_right=inputs.option_right,
        delta=delta,
        gamma=gamma,
        vega_per_unit_volatility=vega,
        vega_per_vol_point=vega / 100.0,
        calendar_theta_per_year=theta,
        calendar_theta_per_day=theta / 365.0,
        rho_per_unit_rate=rho,
        rho_per_rate_point=rho / 100.0,
    )
