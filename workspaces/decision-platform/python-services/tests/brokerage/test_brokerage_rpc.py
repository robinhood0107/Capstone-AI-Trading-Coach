from __future__ import annotations

import re
from typing import Any

import grpc
import pytest

from app.brokerage.brokerage_rpc import BrokerageServicer, _now, metadata
from app.brokerage.kis_mock_order_gateway import KISMockOrderGateway
from app.generated import brokerage_pb2


class RpcAborted(RuntimeError):
    def __init__(self, code: grpc.StatusCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class FakeContext:
    def __init__(self, shared_secret: str = "s" * 32) -> None:
        self._metadata = tuple(metadata(shared_secret))

    def invocation_metadata(self) -> tuple[tuple[str, str], ...]:
        return self._metadata

    def abort(self, code: grpc.StatusCode, details: str) -> None:
        raise RpcAborted(code, details)


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, dict[str, str]]] = []

    def request(
        self,
        method: str,
        path: str,
        tr_id: str,
        json_body: dict[str, str],
    ) -> dict[str, Any]:
        self.calls.append((method, path, tr_id, json_body))
        return {"rt_cd": "0", "output": {"ODNO": "raw-provider-order-no"}}


class FakeBalanceReader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def balance(self, account_id: str) -> brokerage_pb2.GetMockBalanceResponse:
        self.calls.append(("balance", account_id))
        return brokerage_pb2.GetMockBalanceResponse(account_id=account_id)

    def buyable(
        self,
        account_id: str,
        symbol: str,
        estimated_price_krw: int,
    ) -> brokerage_pb2.GetMockBuyableResponse:
        self.calls.append(("buyable", account_id))
        return brokerage_pb2.GetMockBuyableResponse(
            account_id=account_id,
            symbol=symbol,
            estimated_price_krw=estimated_price_krw,
        )


def test_provider_received_at_preserves_microseconds_for_db_ordering() -> None:
    assert re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z",
        _now(),
    )


def test_submit_rpc_hashes_provider_receipt_and_uses_one_fake_transport_call() -> None:
    transport = FakeTransport()
    account_id = "acct_" + "c" * 32
    servicer = BrokerageServicer(
        KISMockOrderGateway(transport),
        "s" * 32,
        bound_account_id=account_id,
    )

    response = servicer.SubmitMockCashOrder(
        brokerage_pb2.SubmitMockCashOrderRequest(
            request_id="req-rpc-submit",
            order_id="ord_mock_" + "1" * 32,
            account_id=account_id,
            symbol="005930",
            side="BUY",
            order_type="MARKET",
            quantity=2,
            estimated_price_krw=70000,
        ),
        FakeContext(),  # type: ignore[arg-type]
    )

    assert response.accepted is True
    assert response.order_id == "ord_mock_" + "1" * 32
    assert response.provider_order_ref_hash != "raw-provider-order-no"
    assert len(response.provider_order_ref_hash) == 64
    assert len(transport.calls) == 1


def test_rpc_auth_and_live_order_gate_fail_before_transport_side_effect() -> None:
    transport = FakeTransport()
    account_id = "acct_" + "c" * 32
    servicer = BrokerageServicer(
        KISMockOrderGateway(transport),
        "s" * 32,
        bound_account_id=account_id,
    )

    with pytest.raises(RpcAborted) as auth_error:
        servicer.SubmitMockCashOrder(
            brokerage_pb2.SubmitMockCashOrderRequest(
                order_id="ord_mock_" + "1" * 32,
                account_id=account_id,
                symbol="005930",
                side="BUY",
                order_type="MARKET",
                quantity=1,
                estimated_price_krw=70000,
            ),
            FakeContext("wrong" * 8),  # type: ignore[arg-type]
        )
    assert auth_error.value.code == grpc.StatusCode.UNAUTHENTICATED
    assert transport.calls == []

    live_servicer = BrokerageServicer(
        KISMockOrderGateway(transport, mode="live"),
        "s" * 32,
        bound_account_id=account_id,
    )
    with pytest.raises(RpcAborted) as live_error:
        live_servicer.SubmitMockCashOrder(
            brokerage_pb2.SubmitMockCashOrderRequest(
                order_id="ord_mock_" + "2" * 32,
                account_id=account_id,
                symbol="005930",
                side="BUY",
                order_type="MARKET",
                quantity=1,
                estimated_price_krw=70000,
            ),
            FakeContext(),  # type: ignore[arg-type]
        )
    assert live_error.value.code == grpc.StatusCode.PERMISSION_DENIED
    assert transport.calls == []


def test_cancel_rpc_is_ledger_only_and_balance_reader_is_explicitly_required() -> None:
    transport = FakeTransport()
    account_id = "acct_" + "c" * 32
    servicer = BrokerageServicer(
        KISMockOrderGateway(transport),
        "s" * 32,
        bound_account_id=account_id,
    )

    cancel = servicer.CancelMockCashOrder(
        brokerage_pb2.CancelMockCashOrderRequest(
            request_id="req-rpc-cancel",
            order_id="ord_mock_" + "3" * 32,
            account_id=account_id,
        ),
        FakeContext(),  # type: ignore[arg-type]
    )
    assert cancel.status == "CANCEL_REQUESTED"
    assert transport.calls == []

    with pytest.raises(RpcAborted) as balance_error:
        servicer.GetMockBalance(
            brokerage_pb2.GetMockBalanceRequest(
                request_id="req-rpc-balance",
                account_id=account_id,
            ),
            FakeContext(),  # type: ignore[arg-type]
        )
    assert balance_error.value.code == grpc.StatusCode.UNAVAILABLE
    assert transport.calls == []


def test_all_rpc_surfaces_reject_an_unbound_account_before_reader_or_transport() -> None:
    transport = FakeTransport()
    reader = FakeBalanceReader()
    bound_account_id = "acct_" + "a" * 32
    other_account_id = "acct_" + "b" * 32
    servicer = BrokerageServicer(
        KISMockOrderGateway(transport),
        "s" * 32,
        balance_reader=reader,
        bound_account_id=bound_account_id,
    )

    calls = (
        lambda: servicer.SubmitMockCashOrder(
            brokerage_pb2.SubmitMockCashOrderRequest(
                order_id="ord_mock_" + "1" * 32,
                account_id=other_account_id,
                symbol="005930",
                side="BUY",
                order_type="LIMIT",
                quantity=1,
                estimated_price_krw=70_000,
            ),
            FakeContext(),  # type: ignore[arg-type]
        ),
        lambda: servicer.CancelMockCashOrder(
            brokerage_pb2.CancelMockCashOrderRequest(
                order_id="ord_mock_" + "2" * 32,
                account_id=other_account_id,
            ),
            FakeContext(),  # type: ignore[arg-type]
        ),
        lambda: servicer.GetMockBalance(
            brokerage_pb2.GetMockBalanceRequest(account_id=other_account_id),
            FakeContext(),  # type: ignore[arg-type]
        ),
        lambda: servicer.GetMockBuyable(
            brokerage_pb2.GetMockBuyableRequest(
                account_id=other_account_id,
                symbol="005930",
                estimated_price_krw=70_000,
            ),
            FakeContext(),  # type: ignore[arg-type]
        ),
    )

    for call in calls:
        with pytest.raises(RpcAborted) as captured:
            call()
        assert captured.value.code == grpc.StatusCode.PERMISSION_DENIED

    assert reader.calls == []
    assert transport.calls == []


def test_balance_projection_failure_is_sanitized_as_unavailable() -> None:
    account_id = "acct_" + "a" * 32

    class FailingBalanceReader(FakeBalanceReader):
        def balance(self, account_id: str) -> brokerage_pb2.GetMockBalanceResponse:
            self.calls.append(("balance", account_id))
            raise RuntimeError("raw provider account must not escape")

    reader = FailingBalanceReader()
    servicer = BrokerageServicer(
        KISMockOrderGateway(FakeTransport()),
        "s" * 32,
        balance_reader=reader,
        bound_account_id=account_id,
    )

    with pytest.raises(RpcAborted) as captured:
        servicer.GetMockBalance(
            brokerage_pb2.GetMockBalanceRequest(account_id=account_id),
            FakeContext(),  # type: ignore[arg-type]
        )

    assert captured.value.code == grpc.StatusCode.UNAVAILABLE
    assert captured.value.detail == "mock balance source unavailable"
    assert "provider" not in captured.value.detail
