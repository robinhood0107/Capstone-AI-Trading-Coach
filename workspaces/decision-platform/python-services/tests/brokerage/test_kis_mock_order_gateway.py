from __future__ import annotations

from typing import Any

import pytest

from app.brokerage.kis_mock_order_gateway import (
    KISMockOrderGateway,
    LiveOrderGateClosed,
    MOCK_BUY_TR_ID,
    MOCK_SELL_TR_ID,
    ORDER_CASH_PATH,
    MockOrderIntent,
    MockOrderRejected,
)


class FakeTransport:
    def __init__(self, response: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, str, dict[str, str]]] = []
        self.response = response or {"rt_cd": "0", "output": {"ODNO": "000001"}}
        self.error = error

    def request(
        self,
        method: str,
        path: str,
        tr_id: str,
        json_body: dict[str, str],
    ) -> dict[str, Any]:
        self.calls.append((method, path, tr_id, json_body))
        if self.error is not None:
            raise self.error
        return self.response


def test_mock_cash_order_maps_buy_sell_tr_ids_and_does_not_retry() -> None:
    transport = FakeTransport()
    gateway = KISMockOrderGateway(transport, mode="mock")

    buy = gateway.submit_cash_order(
        MockOrderIntent("005930", "BUY", "MARKET", quantity=2, estimated_price=70_000)
    )
    sell = gateway.submit_cash_order(
        MockOrderIntent("005930", "SELL", "LIMIT", quantity=1, estimated_price=70_000)
    )

    assert buy.tr_id == MOCK_BUY_TR_ID
    assert sell.tr_id == MOCK_SELL_TR_ID
    assert len(transport.calls) == 2
    assert transport.calls[0] == (
        "POST",
        ORDER_CASH_PATH,
        MOCK_BUY_TR_ID,
        {"PDNO": "005930", "ORD_DVSN": "01", "ORD_QTY": "2", "ORD_UNPR": "0"},
    )
    assert transport.calls[1][3]["ORD_UNPR"] == "70000"


def test_live_mode_fails_before_transport_call() -> None:
    transport = FakeTransport()
    gateway = KISMockOrderGateway(transport, mode="live")

    with pytest.raises(LiveOrderGateClosed):
        gateway.submit_cash_order(
            MockOrderIntent("005930", "BUY", "MARKET", quantity=1, estimated_price=70_000)
        )

    assert transport.calls == []


def test_provider_error_is_not_retried_by_order_gateway() -> None:
    transport = FakeTransport(error=TimeoutError("synthetic timeout"))
    gateway = KISMockOrderGateway(transport, mode="mock")

    with pytest.raises(TimeoutError):
        gateway.submit_cash_order(
            MockOrderIntent("005930", "BUY", "MARKET", quantity=1, estimated_price=70_000)
        )

    assert len(transport.calls) == 1


def test_rejected_or_malformed_receipt_is_fail_closed() -> None:
    for response in ({"rt_cd": "1", "msg1": "rejected"}, {"rt_cd": "0", "output": {}}):
        gateway = KISMockOrderGateway(FakeTransport(response=response), mode="mock")
        with pytest.raises(MockOrderRejected):
            gateway.submit_cash_order(
                MockOrderIntent("005930", "BUY", "MARKET", quantity=1, estimated_price=70_000)
            )
