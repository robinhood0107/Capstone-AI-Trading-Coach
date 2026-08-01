from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.brokerage.mock_order_reference_store import (
    MockOrderReferenceIntent,
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


class MockOrderRecoveryError(MockOrderRejected):
    """accepted 주문의 reference commit/보상 결과를 원문 없이 typed leaf로 전달한다."""

    def __init__(
        self,
        reason_code: Literal[
            "ORDER_REFERENCE_COMMIT_COMPENSATED",
            "ORDER_OUTCOME_UNCERTAIN",
        ],
        message: str,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.provider_code: str | None = None
        self.http_status: int | None = None


@dataclass(frozen=True, slots=True)
class MockOrderIntent:
    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"]
    quantity: int
    estimated_price: int
    # exact KIS_MOCK probe는 정규장 밖에도 동일 주문/취소/reference 경계를 검증해야 하므로
    # packet에 명시된 KRX 주문구분만 선택적으로 운반한다. 일반 runtime 호출은 None으로 기존 매핑을 쓴다.
    order_division: Literal["00", "01", "05", "06", "07"] | None = None
    # KIS_MOCK 현금 신규주문은 KIS Developers 문서 기준 KRX만 provider handoff 전에 허용한다.
    exchange_division: Literal["KRX", "NXT"] | None = None


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
        approval_anchor: str | None = None,
    ) -> MockOrderReceipt:
        """모의 현금주문 1회를 provider retry 없이 전송한다.

        계좌 원문·credential은 Spring ledger나 fixture에 넣지 않으며, S3.1 테스트는 fake transport만 사용한다.
        """

        if self._mode != "mock":
            raise LiveOrderGateClosed("S3.1 live order gate is immutable closed.")
        _validate_intent(intent)
        tr_id = MOCK_BUY_TR_ID if intent.side == "BUY" else MOCK_SELL_TR_ID
        order_division = _order_division(intent)
        exchange_division = _exchange_division(intent)
        reference_store = self._reference_store
        if reference_store is not None:
            if order_id is None or account_id is None:
                raise MockOrderRejected("KIS mock order identity is missing.")
            # provider 수락 뒤 reference 유실을 막기 위해 비민감 PENDING marker를 send 전에 확정한다.
            reference_store.prepare(
                order_id,
                account_id,
                MockOrderReferenceIntent(
                    order_division=order_division,
                    quantity=intent.quantity,
                    exchange_division=exchange_division,
                    approval_anchor=approval_anchor,
                ),
            )
        response = self._transport.request(
            "POST",
            ORDER_CASH_PATH,
            tr_id,
            json_body={
                "PDNO": intent.symbol,
                "ORD_DVSN": order_division,
                "ORD_QTY": str(intent.quantity),
                # KIS 시장가 현금주문은 가격을 0으로 보낸다. Spring은 추정가를 별도 Decision 근거로 보존한다.
                "ORD_UNPR": "0" if intent.order_type == "MARKET" else str(intent.estimated_price),
                "EXCG_ID_DVSN_CD": exchange_division,
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
        if reference_store is not None:
            assert order_id is not None
            assert account_id is not None
            provider_org_no = output.get("KRX_FWDG_ORD_ORGNO")
            if not isinstance(provider_org_no, str) or not provider_org_no:
                raise MockOrderRecoveryError(
                    "ORDER_OUTCOME_UNCERTAIN",
                    "KIS mock accepted order outcome is uncertain.",
                )
            reference = MockProviderOrderReference(
                provider_order_no=provider_order_no,
                provider_org_no=provider_org_no,
                order_division=order_division,
                quantity=intent.quantity,
                exchange_division=exchange_division,
            )
            try:
                reference_store.commit(order_id, account_id, reference)
            except Exception:
                # accepted 주문은 저장 실패 시 in-memory reference로 전량취소를 단 한 번만 시도한다.
                compensated = False
                try:
                    compensated = self._request_full_cancel(reference)
                except Exception:
                    pass
                if compensated:
                    raise MockOrderRecoveryError(
                        "ORDER_REFERENCE_COMMIT_COMPENSATED",
                        "KIS mock accepted order was compensated after reference storage failure.",
                    ) from None
                raise MockOrderRecoveryError(
                    "ORDER_OUTCOME_UNCERTAIN",
                    "KIS mock accepted order outcome is uncertain.",
                ) from None
        return MockOrderReceipt(provider_order_no=provider_order_no, accepted=True, tr_id=tr_id)

    def cancel_cash_order(
        self,
        *,
        order_id: str,
        account_id: str,
        approval_anchor: str | None = None,
    ) -> MockCancelReceipt:
        """저장된 암호화 reference가 있을 때만 KIS 모의 전량취소를 retry 없이 전송한다."""
        if self._mode != "mock":
            raise LiveOrderGateClosed("S3 live order gate is immutable closed.")
        if self._reference_store is None:
            # 기존 offline ledger-only 배선은 provider 호출 없이 그대로 유지한다.
            return MockCancelReceipt(status="CANCEL_REQUESTED", tr_id=None)
        if approval_anchor is None:
            reference = self._reference_store.get(order_id, account_id)
        else:
            reference = self._reference_store.get_for_recovery(
                order_id,
                account_id,
                approval_anchor,
            )
        if reference is None:
            raise MockOrderRejected("KIS mock cancel reference is unavailable.")
        if not self._request_full_cancel(reference):
            raise MockOrderRejected("KIS mock cancel response was not accepted.")
        return MockCancelReceipt(status="CANCELLED", tr_id=MOCK_CANCEL_TR_ID)

    def _request_full_cancel(self, reference: MockProviderOrderReference) -> bool:
        """검증된 in-memory reference로 mock 전량취소를 provider retry 없이 한 번 요청한다."""
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
                "EXCG_ID_DVSN_CD": reference.exchange_division,
            },
        )
        return response.get("rt_cd") == "0"


def _validate_intent(intent: MockOrderIntent) -> None:
    if not intent.symbol.isdigit() or len(intent.symbol) != 6:
        raise ValueError("KIS domestic mock order requires a six digit symbol.")
    if intent.quantity <= 0 or intent.estimated_price <= 0:
        raise ValueError("KIS mock order quantity and estimated price must be positive.")


def _order_division(intent: MockOrderIntent) -> str:
    if intent.order_division is None:
        return "01" if intent.order_type == "MARKET" else "00"
    if intent.order_type == "MARKET" and intent.order_division != "01":
        raise ValueError("KIS mock market order division is invalid.")
    if intent.order_type == "LIMIT" and intent.order_division not in {"00", "05", "06", "07"}:
        raise ValueError("KIS mock limit order division is invalid.")
    return intent.order_division


def _exchange_division(intent: MockOrderIntent) -> Literal["KRX", "NXT"]:
    exchange_division = intent.exchange_division or "KRX"
    if exchange_division not in {"KRX", "NXT"}:
        raise ValueError("KIS mock exchange division is invalid.")
    if exchange_division != "KRX":
        raise ValueError("KIS mock cash order supports KRX only.")
    return exchange_division
