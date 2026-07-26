from __future__ import annotations

from typing import Any

import grpc
import pytest

from app.brokerage.brokerage_rpc import BrokerageServicer, metadata
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


def test_submit_rpc_hashes_provider_receipt_and_uses_one_fake_transport_call() -> None:
    transport = FakeTransport()
    servicer = BrokerageServicer(KISMockOrderGateway(transport), "s" * 32)

    response = servicer.SubmitMockCashOrder(
        brokerage_pb2.SubmitMockCashOrderRequest(
            request_id="req-rpc-submit",
            order_id="ord_mock_" + "1" * 32,
            account_id="acct_" + "c" * 32,
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
    servicer = BrokerageServicer(KISMockOrderGateway(transport), "s" * 32)

    with pytest.raises(RpcAborted) as auth_error:
        servicer.SubmitMockCashOrder(
            brokerage_pb2.SubmitMockCashOrderRequest(
                order_id="ord_mock_" + "1" * 32,
                account_id="acct_" + "c" * 32,
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

    live_servicer = BrokerageServicer(KISMockOrderGateway(transport, mode="live"), "s" * 32)
    with pytest.raises(RpcAborted) as live_error:
        live_servicer.SubmitMockCashOrder(
            brokerage_pb2.SubmitMockCashOrderRequest(
                order_id="ord_mock_" + "2" * 32,
                account_id="acct_" + "c" * 32,
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
    servicer = BrokerageServicer(KISMockOrderGateway(transport), "s" * 32)

    cancel = servicer.CancelMockCashOrder(
        brokerage_pb2.CancelMockCashOrderRequest(
            request_id="req-rpc-cancel",
            order_id="ord_mock_" + "3" * 32,
            account_id="acct_" + "c" * 32,
        ),
        FakeContext(),  # type: ignore[arg-type]
    )
    assert cancel.status == "CANCEL_REQUESTED"
    assert transport.calls == []

    with pytest.raises(RpcAborted) as balance_error:
        servicer.GetMockBalance(
            brokerage_pb2.GetMockBalanceRequest(
                request_id="req-rpc-balance",
                account_id="acct_" + "c" * 32,
            ),
            FakeContext(),  # type: ignore[arg-type]
        )
    assert balance_error.value.code == grpc.StatusCode.UNAVAILABLE
    assert transport.calls == []
