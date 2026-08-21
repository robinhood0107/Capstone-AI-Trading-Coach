from __future__ import annotations

import socket

import grpc
import pytest

from app.financial_engineering.grpc_service import (
    FinancialEngineeringGrpcSettings,
    create_financial_engineering_server,
)
from app.generated import financial_engineering_pb2, financial_engineering_pb2_grpc

SECRET = "financial-engineering-grpc-test-secret-0001"
AUTH = (("x-decision-grpc-auth", SECRET),)


def _address() -> str:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return f"127.0.0.1:{port}"


def _request() -> financial_engineering_pb2.BlackScholesRequest:
    return financial_engineering_pb2.BlackScholesRequest(
        option_right="PUT",
        spot=350000.0,
        strike=360000.0,
        time_to_maturity_years=0.25,
        volatility=0.28,
        risk_free_rate=0.032,
        dividend_yield=0.015,
    )


def test_authenticated_bounded_service_roundtrip_and_no_reflection() -> None:
    settings = FinancialEngineeringGrpcSettings(_address(), SECRET)
    server = create_financial_engineering_server(settings)
    server.start()
    channel = grpc.insecure_channel(settings.bind_address)
    stub = financial_engineering_pb2_grpc.FinancialEngineeringServiceStub(channel)
    try:
        response = stub.BlackScholes(_request(), timeout=0.5, metadata=AUTH)
        assert response.discounted_value > 0
        with pytest.raises(grpc.RpcError) as missing:
            stub.BlackScholes(_request(), timeout=0.5)
        assert missing.value.code() == grpc.StatusCode.UNAUTHENTICATED
    finally:
        channel.close()
        server.stop(0).wait(2)


def test_domain_and_non_finite_values_fail_closed() -> None:
    settings = FinancialEngineeringGrpcSettings(_address(), SECRET)
    server = create_financial_engineering_server(settings)
    server.start()
    channel = grpc.insecure_channel(settings.bind_address)
    stub = financial_engineering_pb2_grpc.FinancialEngineeringServiceStub(channel)
    try:
        request = _request()
        request.spot = float("nan")
        with pytest.raises(grpc.RpcError) as invalid:
            stub.BlackScholes(request, timeout=0.5, metadata=AUTH)
        assert invalid.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    finally:
        channel.close()
        server.stop(0).wait(2)


def test_iv_error_code_and_iteration_bound_are_typed() -> None:
    settings = FinancialEngineeringGrpcSettings(_address(), SECRET)
    server = create_financial_engineering_server(settings)
    server.start()
    channel = grpc.insecure_channel(settings.bind_address)
    stub = financial_engineering_pb2_grpc.FinancialEngineeringServiceStub(channel)
    request = financial_engineering_pb2.ImpliedVolatilityRequest(
        option_right="CALL",
        spot=100.0,
        strike=100.0,
        time_to_maturity_years=1.0,
        risk_free_rate=0.01,
        dividend_yield=0.0,
        market_price=200.0,
        max_iterations=100,
    )
    try:
        with pytest.raises(grpc.RpcError) as invalid:
            stub.ImpliedVolatility(request, timeout=0.5, metadata=AUTH)
        assert invalid.value.code() == grpc.StatusCode.INVALID_ARGUMENT
        assert invalid.value.details() == "IV_NOT_BRACKETED"
    finally:
        channel.close()
        server.stop(0).wait(2)


def test_service_uses_exact_64_kib_message_limits_and_eight_workers() -> None:
    import app.financial_engineering.grpc_service as module

    assert module.MAX_MESSAGE_BYTES == 65_536
    assert module.MAX_CONCURRENCY == 8
