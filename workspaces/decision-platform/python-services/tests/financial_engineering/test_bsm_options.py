from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.special import ndtr

from app.financial_engineering.bsm import black_scholes, discounted_no_arbitrage_bounds
from app.financial_engineering.implied_volatility import (
    LOWER_VOLATILITY,
    UPPER_VOLATILITY,
    implied_volatility,
)
from app.financial_engineering.option_greeks import option_greeks

CANONICAL = {
    "underlying_price": 72_000.0,
    "strike": 75_000.0,
    "tau": 0.25,
    "risk_free_rate": 0.032,
    "dividend_yield": 0.01,
    "volatility": 0.28,
}


def _independent_price(
    right: str, *, s: float, k: float, tau: float, r: float, q: float, sigma: float
) -> float:
    d1 = (math.log(s / k) + (r - q + sigma**2 / 2) * tau) / (sigma * math.sqrt(tau))
    d2 = d1 - sigma * math.sqrt(tau)
    if right == "CALL":
        return s * math.exp(-q * tau) * ndtr(d1) - k * math.exp(-r * tau) * ndtr(d2)
    return k * math.exp(-r * tau) * ndtr(-d2) - s * math.exp(-q * tau) * ndtr(-d1)


def test_public_erratum_and_pinned_golden_vector() -> None:
    call = black_scholes(option_right="CALL", **CANONICAL)
    put = black_scholes(option_right="PUT", **CANONICAL)
    greeks = option_greeks(option_right="PUT", **CANONICAL)
    assert call.d1 == pytest.approx(-0.182299960859, abs=5e-13)
    assert call.d2 == pytest.approx(-0.322299960859, abs=5e-13)
    assert call.theoretical_price == pytest.approx(2917.937245391, abs=5e-10)
    assert put.theoretical_price == pytest.approx(5500.106045553, abs=5e-10)
    assert greeks.delta == pytest.approx(-0.570897306492, abs=5e-13)
    assert greeks.gamma == pytest.approx(0.000038828202272, abs=5e-16)
    assert greeks.vega_per_vol_point == pytest.approx(140.899780404, abs=5e-10)
    assert greeks.calendar_theta_per_year == pytest.approx(-6810.08298, abs=5e-5)
    assert greeks.calendar_theta_per_day == pytest.approx(-18.6577616, abs=5e-7)
    assert greeks.rho_per_rate_point == pytest.approx(-116.5117803, abs=5e-7)


@pytest.mark.parametrize("right", ["CALL", "PUT"])
def test_independent_scipy_oracle_q_parity_and_bounds(right: str) -> None:
    actual = black_scholes(option_right=right, **CANONICAL).theoretical_price
    expected = _independent_price(
        right,
        s=CANONICAL["underlying_price"],
        k=CANONICAL["strike"],
        tau=CANONICAL["tau"],
        r=CANONICAL["risk_free_rate"],
        q=CANONICAL["dividend_yield"],
        sigma=CANONICAL["volatility"],
    )
    lower, upper = discounted_no_arbitrage_bounds(
        option_right=right,
        **{key: value for key, value in CANONICAL.items() if key != "volatility"},
    )
    assert actual == pytest.approx(expected, abs=1e-10)
    assert lower <= actual <= upper
    call = black_scholes(option_right="CALL", **CANONICAL).theoretical_price
    put = black_scholes(option_right="PUT", **CANONICAL).theoretical_price
    parity = CANONICAL["underlying_price"] * math.exp(
        -CANONICAL["dividend_yield"] * CANONICAL["tau"]
    ) - CANONICAL["strike"] * math.exp(-CANONICAL["risk_free_rate"] * CANONICAL["tau"])
    assert call - put == pytest.approx(parity, abs=1e-10)


def test_valid_monotonicities_without_blanket_tau_claim() -> None:
    base = black_scholes(option_right="CALL", **CANONICAL).theoretical_price
    assert (
        black_scholes(
            option_right="CALL", **{**CANONICAL, "underlying_price": 73_000.0}
        ).theoretical_price
        > base
    )
    assert (
        black_scholes(option_right="CALL", **{**CANONICAL, "strike": 76_000.0}).theoretical_price
        < base
    )
    assert (
        black_scholes(option_right="CALL", **{**CANONICAL, "volatility": 0.3}).theoretical_price
        > base
    )


def test_call_put_gamma_vega_equality_and_explicit_units() -> None:
    call = option_greeks(option_right="CALL", **CANONICAL)
    put = option_greeks(option_right="PUT", **CANONICAL)
    assert call.gamma == put.gamma
    assert call.vega_per_unit_volatility == put.vega_per_unit_volatility
    assert call.vega_per_vol_point == call.vega_per_unit_volatility / 100
    assert call.calendar_theta_per_day == call.calendar_theta_per_year / 365
    assert call.rho_per_rate_point == call.rho_per_unit_rate / 100


@pytest.mark.parametrize("right", ["CALL", "PUT"])
def test_all_analytic_greeks_match_central_finite_differences(right: str) -> None:
    analytic = option_greeks(option_right=right, **CANONICAL)

    def price(**changes: float) -> float:
        return black_scholes(
            option_right=right,
            **{**CANONICAL, **changes},
        ).theoretical_price

    h_s, h_v, h_r, h_t = 1.0, 1e-5, 1e-5, 1e-5
    delta = (
        price(underlying_price=CANONICAL["underlying_price"] + h_s)
        - price(underlying_price=CANONICAL["underlying_price"] - h_s)
    ) / (2 * h_s)
    gamma = (
        price(underlying_price=CANONICAL["underlying_price"] + h_s)
        - 2 * price()
        + price(underlying_price=CANONICAL["underlying_price"] - h_s)
    ) / h_s**2
    vega = (
        price(volatility=CANONICAL["volatility"] + h_v)
        - price(volatility=CANONICAL["volatility"] - h_v)
    ) / (2 * h_v)
    rho = (
        price(risk_free_rate=CANONICAL["risk_free_rate"] + h_r)
        - price(risk_free_rate=CANONICAL["risk_free_rate"] - h_r)
    ) / (2 * h_r)
    theta = -(price(tau=CANONICAL["tau"] + h_t) - price(tau=CANONICAL["tau"] - h_t)) / (2 * h_t)
    assert analytic.delta == pytest.approx(delta, rel=1e-8)
    assert analytic.gamma == pytest.approx(gamma, rel=5e-5)
    assert analytic.vega_per_unit_volatility == pytest.approx(vega, rel=1e-9)
    assert analytic.rho_per_unit_rate == pytest.approx(rho, rel=1e-9)
    assert analytic.calendar_theta_per_year == pytest.approx(theta, rel=1e-9)


def test_iv_round_trip_endpoint_bounds_bracket_and_iteration_failure() -> None:
    market = black_scholes(option_right="CALL", **CANONICAL).theoretical_price
    result = implied_volatility(
        option_right="CALL",
        market_price=market,
        **{key: value for key, value in CANONICAL.items() if key != "volatility"},
    )
    assert result.implied_volatility == pytest.approx(0.28, abs=1e-8)
    assert result.warning == "CALIBRATION_IDENTITY"
    assert (
        implied_volatility(
            option_right="PUT",
            market_price=black_scholes(
                option_right="PUT", **{**CANONICAL, "volatility": LOWER_VOLATILITY}
            ).theoretical_price,
            **{key: value for key, value in CANONICAL.items() if key != "volatility"},
        ).implied_volatility
        == LOWER_VOLATILITY
    )
    assert (
        implied_volatility(
            option_right="CALL",
            market_price=black_scholes(
                option_right="CALL", **{**CANONICAL, "volatility": UPPER_VOLATILITY}
            ).theoretical_price,
            **{key: value for key, value in CANONICAL.items() if key != "volatility"},
        ).implied_volatility
        == UPPER_VOLATILITY
    )
    lower, upper = discounted_no_arbitrage_bounds(
        option_right="CALL",
        **{key: value for key, value in CANONICAL.items() if key != "volatility"},
    )
    with pytest.raises(ValueError, match="IV_NOT_BRACKETED"):
        implied_volatility(
            option_right="CALL",
            market_price=upper + 1,
            **{key: value for key, value in CANONICAL.items() if key != "volatility"},
        )
    with pytest.raises(ValueError, match="IV_NOT_CONVERGED"):
        implied_volatility(
            option_right="CALL",
            market_price=market,
            max_iterations=1,
            tolerance=1e-12,
            **{key: value for key, value in CANONICAL.items() if key != "volatility"},
        )
    assert lower >= 0


def test_secondary_2315_42_fixture_has_correct_iv() -> None:
    result = implied_volatility(
        option_right="CALL",
        market_price=2315.42,
        tolerance=1e-10,
        **{key: value for key, value in CANONICAL.items() if key != "volatility"},
    )
    assert result.implied_volatility == pytest.approx(0.237005877501, abs=1e-10)


@pytest.mark.parametrize(
    "field,value",
    [("tau", 0.0), ("volatility", 0.0), ("underlying_price", np.nan), ("strike", np.inf)],
)
def test_public_zero_and_nonfinite_domain_failures(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        black_scholes(option_right="CALL", **{**CANONICAL, field: value})


def test_near_zero_off_kink_converges_without_atm_singularity_convention() -> None:
    call = black_scholes(
        option_right="CALL",
        underlying_price=120,
        strike=100,
        tau=1e-8,
        risk_free_rate=0.01,
        dividend_yield=0,
        volatility=1e-4,
    )
    assert call.theoretical_price == pytest.approx(20.0, rel=1e-7)
    with pytest.raises(ValueError, match="bsm_domain_invalid"):
        option_greeks(
            option_right="CALL",
            underlying_price=100,
            strike=100,
            tau=0,
            risk_free_rate=0,
            dividend_yield=0,
            volatility=0.2,
        )
