from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.brokerage.mock_order_reference_store import (
    MockOrderReferenceStore,
    MockProviderOrderReference,
)

ORDER_CASH_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
ORDER_CANCEL_PATH = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
MOCK_BUY_TR_ID = "VTTC0012U"
MOCK_SELL_TR_ID = "VTTC0011U"
MOCK_CANCEL_TR_ID = "VTTC0013U"


class MockOrderTransport(Protocol):
    """KIS transport boundary. Tests inject a fake; production wiring must share S1.1 quota."""

    def request(
        self,
        method: str,
        path: str,
        tr_id: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, str] | None = None,
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


@dataclass(frozen=True, slots=True, repr=False)
class MockOrderReceipt:
    provider_order_no: str
    accepted: bool
    tr_id: str


@dataclass(frozen=True, slots=True)
class MockCancelReceipt:
    status: Literal["CANCEL_REQUESTED", "CANCELLED"]
    tr_id: str | None


class KISMockOrderGateway:
    def __init__(
        self,
        transport: MockOrderTransport,
        *,
        mode: Literal["mock", "live"] = "mock",
        reference_store: MockOrderReferenceStore | None = None,
    ) -> None:
        self._transport = transport
        self._mode = mode
        self._reference_store = reference_store

    def submit_cash_order(
        self,
        intent: MockOrderIntent,
        *,
        order_id: str | None = None,
        account_id: str | None = None,
    ) -> MockOrderReceipt:
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
        if not isinstance(output, dict):
            raise MockOrderRejected("KIS mock order receipt is missing ODNO.")
        provider_order_no = output.get("ODNO")
        if not isinstance(provider_order_no, str) or not provider_order_no:
            raise MockOrderRejected("KIS mock order receipt is missing ODNO.")
        if self._reference_store is not None:
            if order_id is None or account_id is None:
                raise MockOrderRejected("KIS mock order identity is missing.")
            provider_org_no = output.get("KRX_FWDG_ORD_ORGNO")
            if not isinstance(provider_org_no, str) or not provider_org_no:
                raise MockOrderRejected("KIS mock order receipt is missing provider org reference.")
            self._reference_store.put(
                order_id,
                account_id,
                MockProviderOrderReference(
                    provider_order_no=provider_order_no,
                    provider_org_no=provider_org_no,
                    order_division="01" if intent.order_type == "MARKET" else "00",
                    quantity=intent.quantity,
                ),
            )
        return MockOrderReceipt(provider_order_no=provider_order_no, accepted=True, tr_id=tr_id)

    def cancel_cash_order(
        self,
        *,
        order_id: str,
        account_id: str,
    ) -> MockCancelReceipt:
        """저장된 암호화 reference가 있을 때만 KIS 모의 전량취소를 retry 없이 전송한다."""
        if self._mode != "mock":
            raise LiveOrderGateClosed("S3 live order gate is immutable closed.")
        if self._reference_store is None:
            # 기존 offline ledger-only 배선은 provider 호출 없이 그대로 유지한다.
            return MockCancelReceipt(status="CANCEL_REQUESTED", tr_id=None)
        reference = self._reference_store.get(order_id, account_id)
        if reference is None:
            raise MockOrderRejected("KIS mock cancel reference is unavailable.")
        response = self._transport.request(
            "POST",
            ORDER_CANCEL_PATH,
            MOCK_CANCEL_TR_ID,
            json_body={
                "KRX_FWDG_ORD_ORGNO": reference.provider_org_no,
                "ORGN_ODNO": reference.provider_order_no,
                "ORD_DVSN": reference.order_division,
                "RVSE_CNCL_DVSN_CD": "02",
                "ORD_QTY": str(reference.quantity),
                "ORD_UNPR": "0",
                "QTY_ALL_ORD_YN": "Y",
                "EXCG_ID_DVSN_CD": "KRX",
            },
        )
        if response.get("rt_cd") != "0":
            raise MockOrderRejected("KIS mock cancel response was not accepted.")
        return MockCancelReceipt(status="CANCELLED", tr_id=MOCK_CANCEL_TR_ID)


def _validate_intent(intent: MockOrderIntent) -> None:
    if not intent.symbol.isdigit() or len(intent.symbol) != 6:
        raise ValueError("KIS domestic mock order requires a six digit symbol.")
    if intent.quantity <= 0 or intent.estimated_price <= 0:
        raise ValueError("KIS mock order quantity and estimated price must be positive.")
