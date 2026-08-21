from __future__ import annotations

import math
import os
import re
from concurrent import futures
from dataclasses import dataclass
from hmac import compare_digest
from typing import Never

import grpc

from app.financial_engineering.bsm import black_scholes
from app.financial_engineering.implied_volatility import implied_volatility
from app.financial_engineering.option_greeks import option_greeks
from app.generated import financial_engineering_pb2, financial_engineering_pb2_grpc

MAX_MESSAGE_BYTES = 64 * 1024
MAX_CONCURRENCY = 8
AUTH_METADATA_KEY = "x-decision-grpc-auth"
_SAFE_SECRET = re.compile(r"[A-Za-z0-9._~:-]{32,256}")


@dataclass(frozen=True)
class FinancialEngineeringGrpcSettings:
    bind_address: str = "127.0.0.1:50054"
    shared_secret: str = ""

    def __post_init__(self) -> None:
        host, separator, port = self.bind_address.rpartition(":")
        if host not in {"127.0.0.1", "[::1]"} or separator != ":" or not port.isdigit():
            raise ValueError("financial engineering gRPC must bind to loopback")
        if not 1 <= int(port) <= 65_535:
            raise ValueError("financial engineering gRPC port is invalid")
        if _SAFE_SECRET.fullmatch(self.shared_secret) is None:
            raise ValueError("financial engineering gRPC shared secret is invalid")

    @classmethod
    def from_env(cls) -> FinancialEngineeringGrpcSettings:
        return cls(
            bind_address=os.environ.get(
                "FINANCIAL_ENGINEERING_GRPC_BIND_ADDRESS", "127.0.0.1:50054"
            ).strip(),
            shared_secret=os.environ.get(
                "FINANCIAL_ENGINEERING_GRPC_SHARED_SECRET", ""
            ).strip(),
        )


class FinancialEngineeringServicer(
    financial_engineering_pb2_grpc.FinancialEngineeringServiceServicer
):
    def __init__(self, shared_secret: str) -> None:
        self._shared_secret = shared_secret

    def BlackScholes(
        self,
        request: financial_engineering_pb2.BlackScholesRequest,
        context: grpc.ServicerContext,
    ) -> financial_engineering_pb2.BlackScholesResponse:
        _authenticate(context, self._shared_secret)
        try:
            value = black_scholes(**_pricing_arguments(request)).theoretical_price
        except ValueError as error:
            _invalid(context, error)
        return financial_engineering_pb2.BlackScholesResponse(discounted_value=value)

    def Greeks(
        self,
        request: financial_engineering_pb2.GreeksRequest,
        context: grpc.ServicerContext,
    ) -> financial_engineering_pb2.GreeksResponse:
        _authenticate(context, self._shared_secret)
        try:
            value = option_greeks(**_pricing_arguments(request))
        except ValueError as error:
            _invalid(context, error)
        return financial_engineering_pb2.GreeksResponse(
            delta=value.delta,
            gamma=value.gamma,
            vega_per_unit_volatility=value.vega_per_unit_volatility,
            vega_per_vol_point=value.vega_per_vol_point,
            calendar_theta_per_year=value.calendar_theta_per_year,
            calendar_theta_per_day=value.calendar_theta_per_day,
            rho_per_unit_rate=value.rho_per_unit_rate,
            rho_per_rate_point=value.rho_per_rate_point,
        )

    def ImpliedVolatility(
        self,
        request: financial_engineering_pb2.ImpliedVolatilityRequest,
        context: grpc.ServicerContext,
    ) -> financial_engineering_pb2.ImpliedVolatilityResponse:
        _authenticate(context, self._shared_secret)
        try:
            value = implied_volatility(
                option_right=request.option_right,
                market_price=request.market_price,
                underlying_price=request.spot,
                strike=request.strike,
                tau=request.time_to_maturity_years,
                risk_free_rate=request.risk_free_rate,
                dividend_yield=request.dividend_yield,
                max_iterations=request.max_iterations,
            )
        except ValueError as error:
            _invalid(context, error)
        return financial_engineering_pb2.ImpliedVolatilityResponse(
            implied_volatility=value.implied_volatility
        )


def create_financial_engineering_server(
    settings: FinancialEngineeringGrpcSettings,
) -> grpc.Server:
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENCY),
        options=(
            ("grpc.max_receive_message_length", MAX_MESSAGE_BYTES),
            ("grpc.max_send_message_length", MAX_MESSAGE_BYTES),
        ),
        maximum_concurrent_rpcs=MAX_CONCURRENCY,
    )
    financial_engineering_pb2_grpc.add_FinancialEngineeringServiceServicer_to_server(
        FinancialEngineeringServicer(settings.shared_secret), server
    )  # type: ignore[no-untyped-call]
    if server.add_insecure_port(settings.bind_address) == 0:
        raise RuntimeError("financial engineering gRPC loopback port could not be bound")
    return server


def serve(settings: FinancialEngineeringGrpcSettings | None = None) -> None:
    server = create_financial_engineering_server(
        settings or FinancialEngineeringGrpcSettings.from_env()
    )
    try:
        server.start()
        server.wait_for_termination()
    finally:
        server.stop(grace=0).wait(timeout=2)


def _pricing_arguments(
    request: financial_engineering_pb2.BlackScholesRequest
    | financial_engineering_pb2.GreeksRequest,
) -> dict[str, object]:
    numeric_values = (
        request.spot,
        request.strike,
        request.time_to_maturity_years,
        request.risk_free_rate,
        request.dividend_yield,
        request.volatility,
    )
    if not all(math.isfinite(value) for value in numeric_values):
        raise ValueError("numeric_input_non_finite")
    values = {
        "option_right": request.option_right,
        "underlying_price": request.spot,
        "strike": request.strike,
        "tau": request.time_to_maturity_years,
        "risk_free_rate": request.risk_free_rate,
        "dividend_yield": request.dividend_yield,
        "volatility": request.volatility,
    }
    return values


def _authenticate(context: grpc.ServicerContext, shared_secret: str) -> None:
    values = [value for key, value in context.invocation_metadata() if key == AUTH_METADATA_KEY]
    if len(values) != 1 or not isinstance(values[0], str) or not compare_digest(values[0], shared_secret):
        _abort(context, grpc.StatusCode.UNAUTHENTICATED, "financial engineering grpc authentication failed")


def _invalid(context: grpc.ServicerContext, error: ValueError) -> Never:
    code = str(error)
    if code not in {"IV_NOT_BRACKETED", "IV_NOT_CONVERGED"}:
        code = "VALIDATION_ERROR"
    _abort(context, grpc.StatusCode.INVALID_ARGUMENT, code)


def _abort(context: grpc.ServicerContext, status: grpc.StatusCode, detail: str) -> Never:
    context.abort(status, detail)
    raise AssertionError("gRPC abort returned unexpectedly")
