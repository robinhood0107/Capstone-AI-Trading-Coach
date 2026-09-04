from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import NoReturn, Protocol

import grpc

from app.brokerage.kis_mock_order_gateway import (
    KISMockOrderGateway,
    LiveOrderGateClosed,
    MockOrderIntent,
    MockOrderRejected,
)
from app.generated import brokerage_pb2, brokerage_pb2_grpc

_AUTH_METADATA_KEY = "x-decision-grpc-auth"
_SAFE_SECRET = re.compile(r"[A-Za-z0-9._~:-]{32,256}")
_ORDER_ID = re.compile(r"^ord_mock_[0-9a-f]{32}$")
_ACCOUNT_ID = re.compile(r"^acct_[0-9a-f]{32}$")


class BrokerageRpcProtocolError(RuntimeError):
    """gRPC 요청/응답이 S3.1 bounded mock brokerage 계약을 벗어났다."""


class BalanceReadPort(Protocol):
    """Spring DB projection을 테스트/후속 wiring에서 주입하기 위한 sanitized account reader."""

    def balance(self, account_id: str) -> brokerage_pb2.GetMockBalanceResponse | None: ...

    def buyable(
        self, account_id: str, symbol: str, estimated_price_krw: int
    ) -> brokerage_pb2.GetMockBuyableResponse | None: ...


class BrokerageServicer(brokerage_pb2_grpc.BrokerageServiceServicer):
    """KIS Mock 주문 RPC 경계.

    credential·raw 계좌번호는 proto에 없고, gateway는 injected transport만 받아 테스트 기본값에서 provider 호출을 만들지 않는다.
    """

    def __init__(
        self,
        gateway: KISMockOrderGateway,
        shared_secret: str,
        *,
        bound_account_id: str,
        balance_reader: BalanceReadPort | None = None,
    ) -> None:
        if _SAFE_SECRET.fullmatch(shared_secret) is None:
            raise ValueError("Brokerage gRPC shared secret must be 32..256 safe ASCII characters")
        if _ACCOUNT_ID.fullmatch(bound_account_id) is None:
            raise ValueError("Brokerage gRPC bound account id is invalid")
        self._gateway = gateway
        self._shared_secret = shared_secret
        self._bound_account_id = bound_account_id
        self._balance_reader = balance_reader

    def SubmitMockCashOrder(
        self,
        request: brokerage_pb2.SubmitMockCashOrderRequest,
        context: grpc.ServicerContext,
    ) -> brokerage_pb2.SubmitMockCashOrderResponse:
        _require_authenticated(context, self._shared_secret)
        _validate_submit_request(request, context)
        _require_bound_account(request.account_id, self._bound_account_id, context)
        try:
            receipt = self._gateway.submit_cash_order(
                MockOrderIntent(
                    symbol=request.symbol,
                    side=request.side,  # type: ignore[arg-type]
                    order_type=request.order_type,  # type: ignore[arg-type]
                    quantity=request.quantity,
                    estimated_price=request.estimated_price_krw,
                ),
                order_id=request.order_id,
                account_id=request.account_id,
            )
        except LiveOrderGateClosed:
            _abort(context, grpc.StatusCode.PERMISSION_DENIED, "live order gate is closed")
        except (MockOrderRejected, ValueError):
            _abort(context, grpc.StatusCode.FAILED_PRECONDITION, "mock order was rejected")
        except TimeoutError:
            _abort(context, grpc.StatusCode.DEADLINE_EXCEEDED, "mock order transport timed out")
        except Exception:
            _abort(context, grpc.StatusCode.UNAVAILABLE, "mock order transport unavailable")
        return brokerage_pb2.SubmitMockCashOrderResponse(
            order_id=request.order_id,
            accepted=receipt.accepted,
            provider_order_ref_hash=_receipt_hash(receipt.provider_order_no),
            tr_id=receipt.tr_id,
            received_at=_now(),
        )

    def CancelMockCashOrder(
        self,
        request: brokerage_pb2.CancelMockCashOrderRequest,
        context: grpc.ServicerContext,
    ) -> brokerage_pb2.CancelMockCashOrderResponse:
        _require_authenticated(context, self._shared_secret)
        _validate_order_and_account(request.order_id, request.account_id, context)
        _require_bound_account(request.account_id, self._bound_account_id, context)
        try:
            receipt = self._gateway.cancel_cash_order(
                order_id=request.order_id,
                account_id=request.account_id,
            )
        except LiveOrderGateClosed:
            _abort(context, grpc.StatusCode.PERMISSION_DENIED, "live order gate is closed")
        except (MockOrderRejected, ValueError):
            _abort(context, grpc.StatusCode.FAILED_PRECONDITION, "mock cancel was rejected")
        except TimeoutError:
            _abort(context, grpc.StatusCode.DEADLINE_EXCEEDED, "mock cancel transport timed out")
        except Exception:
            _abort(context, grpc.StatusCode.UNAVAILABLE, "mock cancel transport unavailable")
        return brokerage_pb2.CancelMockCashOrderResponse(
            order_id=request.order_id,
            status=receipt.status,
            received_at=_now(),
        )

    def GetMockBalance(
        self,
        request: brokerage_pb2.GetMockBalanceRequest,
        context: grpc.ServicerContext,
    ) -> brokerage_pb2.GetMockBalanceResponse:
        _require_authenticated(context, self._shared_secret)
        _validate_account(request.account_id, context)
        _require_bound_account(request.account_id, self._bound_account_id, context)
        reader = self._balance_reader
        if reader is None:
            _abort(context, grpc.StatusCode.UNAVAILABLE, "mock balance reader is not wired")
        try:
            response = reader.balance(request.account_id)
        except Exception:
            _abort(context, grpc.StatusCode.UNAVAILABLE, "mock balance source unavailable")
        if response is None:
            _abort(context, grpc.StatusCode.NOT_FOUND, "mock balance was not found")
        return response

    def GetMockBuyable(
        self,
        request: brokerage_pb2.GetMockBuyableRequest,
        context: grpc.ServicerContext,
    ) -> brokerage_pb2.GetMockBuyableResponse:
        _require_authenticated(context, self._shared_secret)
        _validate_account(request.account_id, context)
        _require_bound_account(request.account_id, self._bound_account_id, context)
        if not _symbol(request.symbol) or request.estimated_price_krw <= 0:
            _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "buyable query is invalid")
        reader = self._balance_reader
        if reader is None:
            _abort(context, grpc.StatusCode.UNAVAILABLE, "mock buyable reader is not wired")
        try:
            response = reader.buyable(
                request.account_id,
                request.symbol,
                request.estimated_price_krw,
            )
        except Exception as error:
            # 원인 분류만 남긴다. 계좌번호·토큰·응답 본문은 넣지 않는다. 이것이 없으면 호출자는
            # 언제나 "unavailable" 만 보게 되고, 실측으로 그 때문에 PERMISSION_DENIED 와
            # 전송 장애를 구분하지 못해 원인 추적이 막혔다.
            _abort(
                context,
                grpc.StatusCode.UNAVAILABLE,
                f"mock buyable source unavailable: {type(error).__name__}",
            )
        if response is None:
            _abort(context, grpc.StatusCode.NOT_FOUND, "mock buyable account was not found")
        return response


def _require_authenticated(context: grpc.ServicerContext, shared_secret: str) -> None:
    values = [value for key, value in context.invocation_metadata() if key == _AUTH_METADATA_KEY]
    if (
        len(values) != 1
        or not isinstance(values[0], str)
        or not hmac.compare_digest(values[0], shared_secret)
    ):
        _abort(context, grpc.StatusCode.UNAUTHENTICATED, "brokerage grpc authentication failed")


def _validate_submit_request(
    request: brokerage_pb2.SubmitMockCashOrderRequest,
    context: grpc.ServicerContext,
) -> None:
    _validate_order_and_account(request.order_id, request.account_id, context)
    if (
        not _symbol(request.symbol)
        or request.side not in {"BUY", "SELL"}
        or request.order_type not in {"MARKET", "LIMIT"}
        or request.quantity <= 0
        or request.estimated_price_krw <= 0
    ):
        _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "mock order request is invalid")


def _validate_order_and_account(
    order_id: str, account_id: str, context: grpc.ServicerContext
) -> None:
    if _ORDER_ID.fullmatch(order_id) is None:
        _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "order id is invalid")
    _validate_account(account_id, context)


def _validate_account(account_id: str, context: grpc.ServicerContext) -> None:
    if _ACCOUNT_ID.fullmatch(account_id) is None:
        _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "account id is invalid")


def _require_bound_account(
    account_id: str,
    bound_account_id: str,
    context: grpc.ServicerContext,
) -> None:
    # opaque account ownership과 실제 KIS_MOCK credential의 1:1 결속을 provider 접근 전에 강제한다.
    if not hmac.compare_digest(account_id, bound_account_id):
        _abort(context, grpc.StatusCode.PERMISSION_DENIED, "mock account binding rejected")


def _symbol(symbol: str) -> bool:
    return symbol.isdigit() and len(symbol) == 6


def _receipt_hash(provider_order_no: str) -> str:
    payload = f"kis-mock-order-receipt/v1\0{provider_order_no}".encode()
    return hashlib.sha256(payload).hexdigest()


def _now() -> str:
    # Spring의 submittedAt보다 같은 초 안에서 과거로 잘리지 않도록 microsecond를 보존한다.
    return datetime.now(tz=UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _abort(context: grpc.ServicerContext, code: grpc.StatusCode, detail: str) -> NoReturn:
    context.abort(code, detail)
    raise BrokerageRpcProtocolError(detail)


def metadata(shared_secret: str) -> Sequence[tuple[str, str]]:
    """Spring adapter tests share the exact metadata key without duplicating a string literal."""

    return ((_AUTH_METADATA_KEY, shared_secret),)
