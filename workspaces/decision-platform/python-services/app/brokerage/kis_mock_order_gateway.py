from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


ORDER_CASH_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
MOCK_BUY_TR_ID = "VTTC0012U"
MOCK_SELL_TR_ID = "VTTC0011U"


class MockOrderTransport(Protocol):
    """KIS transport boundary. Tests inject a fake; production wiring must share S1.1 quota."""

    def request(
        self,
        method: str,
        path: str,
        tr_id: str,
        json_body: dict[str, str],
    ) -> dict[str, Any]: ...


class LiveOrderGateClosed(RuntimeError):
    """S3.1 never opens live trading; live mode must fail before any transport call."""


class MockOrderRejected(RuntimeError):
    """KIS mock adapter returned a non-success response."""


@dataclass(frozen=True, slots=True)
class MockOrderIntent:
    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"]
    quantity: int
    estimated_price: int


@dataclass(frozen=True, slots=True)
class MockOrderReceipt:
    provider_order_no: str
    accepted: bool
    tr_id: str


class KISMockOrderGateway:
    def __init__(
        self,
        transport: MockOrderTransport,
        *,
        mode: Literal["mock", "live"] = "mock",
    ) -> None:
        self._transport = transport
        self._mode = mode

    def submit_cash_order(self, intent: MockOrderIntent) -> MockOrderReceipt:
        """모의 현금주문 1회를 provider retry 없이 전송한다.

        계좌 원문·credential은 Spring ledger나 fixture에 넣지 않으며, S3.1 테스트는 fake transport만 사용한다.
        """

        if self._mode != "mock":
            raise LiveOrderGateClosed("S3.1 live order gate is immutable closed.")
        _validate_intent(intent)
        tr_id = MOCK_BUY_TR_ID if intent.side == "BUY" else MOCK_SELL_TR_ID
        response = self._transport.request(
            "POST",
            ORDER_CASH_PATH,
            tr_id,
            json_body={
                "PDNO": intent.symbol,
                "ORD_DVSN": "01" if intent.order_type == "MARKET" else "00",
                "ORD_QTY": str(intent.quantity),
                # KIS 시장가 현금주문은 가격을 0으로 보낸다. Spring은 추정가를 별도 Decision 근거로 보존한다.
                "ORD_UNPR": "0" if intent.order_type == "MARKET" else str(intent.estimated_price),
            },
        )
        if response.get("rt_cd") != "0":
            raise MockOrderRejected("KIS mock order response was not accepted.")
        output = response.get("output")
        provider_order_no = output.get("ODNO") if isinstance(output, dict) else None
        if not isinstance(provider_order_no, str) or not provider_order_no:
            raise MockOrderRejected("KIS mock order receipt is missing ODNO.")
        return MockOrderReceipt(provider_order_no=provider_order_no, accepted=True, tr_id=tr_id)


def _validate_intent(intent: MockOrderIntent) -> None:
    if not intent.symbol.isdigit() or len(intent.symbol) != 6:
        raise ValueError("KIS domestic mock order requires a six digit symbol.")
    if intent.quantity <= 0 or intent.estimated_price <= 0:
        raise ValueError("KIS mock order quantity and estimated price must be positive.")
