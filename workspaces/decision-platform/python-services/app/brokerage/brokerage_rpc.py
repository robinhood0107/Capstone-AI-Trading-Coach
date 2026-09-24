from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, date, datetime
from typing import Any, NoReturn, Protocol

import grpc

from app.brokerage.kis_mock_order_gateway import (
    KISMockOrderGateway,
    LiveOrderGateClosed,
    MockOrderIntent,
    MockOrderRejected,
)
from app.brokerage.owner_credential_envelope import OwnerCredentialUnavailable
from app.generated import brokerage_pb2, brokerage_pb2_grpc

_AUTH_METADATA_KEY = "x-decision-grpc-auth"
_SAFE_SECRET = re.compile(r"[A-Za-z0-9._~:-]{32,256}")
_ORDER_ID = re.compile(r"^ord_mock_[0-9a-f]{32}$")
_ACCOUNT_ID = re.compile(r"^acct_[0-9a-f]{32}$")
_OWNER_ID = re.compile(r"^usr_[A-Za-z0-9_-]{8,96}$")
_CERTIFICATION_ID = re.compile(r"^cert_[0-9a-f]{32}$")
_REQUEST_ID = re.compile(r"^req_[A-Za-z0-9_-]{8,96}$")
_SESSION_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class BrokerageRpcProtocolError(RuntimeError):
    """gRPC 요청/응답이 S3.1 bounded mock brokerage 계약을 벗어났다."""


class BalanceReadPort(Protocol):
    """Spring DB projection을 테스트/후속 wiring에서 주입하기 위한 sanitized account reader."""

    def balance(self, account_id: str) -> brokerage_pb2.GetMockBalanceResponse | None: ...

    def verify_connection(self, account_id: str) -> None: ...

    def buyable(
        self, account_id: str, symbol: str, estimated_price_krw: int
    ) -> brokerage_pb2.GetMockBuyableResponse | None: ...


class OwnerBoundBrokerageFactory(Protocol):
    def open(
        self,
        envelope: brokerage_pb2.BoundMockCredentialEnvelope,
        *,
        account_id: str,
        allowed_states: frozenset[str],
    ) -> AbstractContextManager[tuple[KISMockOrderGateway, BalanceReadPort]]: ...

    def certify(
        self,
        envelope: brokerage_pb2.BoundMockCredentialEnvelope,
        *,
        owner_user_id: str,
        account_id: str,
        certification_id: str,
        session_date: str,
        recovery: bool,
    ) -> Any: ...


class BrokerageServicer(brokerage_pb2_grpc.BrokerageServiceServicer):
    """One mock RPC boundary; public requests require an owner-bound sealed envelope."""

    def __init__(
        self,
        gateway: KISMockOrderGateway | None,
        shared_secret: str,
        *,
        bound_account_id: str | None = None,
        balance_reader: BalanceReadPort | None = None,
        owner_factory: OwnerBoundBrokerageFactory | None = None,
    ) -> None:
        if _SAFE_SECRET.fullmatch(shared_secret) is None:
            raise ValueError("Brokerage gRPC shared secret must be 32..256 safe ASCII characters")
        if owner_factory is None:
            if (
                gateway is None
                or bound_account_id is None
                or _ACCOUNT_ID.fullmatch(bound_account_id) is None
            ):
                raise ValueError("Brokerage gRPC bound account id is invalid")
        elif gateway is not None or bound_account_id is not None or balance_reader is not None:
            raise ValueError("Owner-bound brokerage cannot use a deployment account")
        self._gateway = gateway
        self._shared_secret = shared_secret
        self._bound_account_id = bound_account_id
        self._balance_reader = balance_reader
        self._owner_factory = owner_factory

    @contextmanager
    def _session(
        self,
        request: Any,
        allowed_states: frozenset[str],
    ) -> Iterator[tuple[KISMockOrderGateway, BalanceReadPort | None]]:
        factory = self._owner_factory
        if factory is not None:
            if not request.HasField("credential"):
                raise OwnerCredentialUnavailable("BROKERAGE_CREDENTIAL_UNAVAILABLE")
            with factory.open(
                request.credential,
                account_id=request.account_id,
                allowed_states=allowed_states,
            ) as session:
                yield session
        else:
            if request.HasField("credential"):
                raise OwnerCredentialUnavailable("BROKERAGE_CREDENTIAL_UNAVAILABLE")
            assert self._bound_account_id is not None and self._gateway is not None
            if request.account_id != self._bound_account_id:
                raise OwnerCredentialUnavailable("BROKERAGE_CREDENTIAL_UNAVAILABLE")
            yield self._gateway, self._balance_reader

    def SubmitMockCashOrder(
        self,
        request: brokerage_pb2.SubmitMockCashOrderRequest,
        context: grpc.ServicerContext,
    ) -> brokerage_pb2.SubmitMockCashOrderResponse:
        _require_authenticated(context, self._shared_secret)
        _validate_submit_request(request, context)
        try:
            with self._session(request, frozenset({"CERTIFIED"})) as (gateway, _):
                receipt = gateway.submit_cash_order(
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
        except OwnerCredentialUnavailable:
            _abort(context, grpc.StatusCode.PERMISSION_DENIED, "mock owner credential unavailable")
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
        try:
            with self._session(request, frozenset({"CERTIFIED", "DISCONNECTING"})) as (gateway, _):
                receipt = gateway.cancel_cash_order(
                    order_id=request.order_id,
                    account_id=request.account_id,
                )
        except OwnerCredentialUnavailable:
            _abort(context, grpc.StatusCode.PERMISSION_DENIED, "mock owner credential unavailable")
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
        try:
            with self._session(
                request, frozenset({"STORED", "CONNECTED", "CERTIFIED", "DISCONNECTING"})
            ) as (_, reader):
                if reader is None:
                    _abort(context, grpc.StatusCode.UNAVAILABLE, "mock balance reader is not wired")
                response = reader.balance(request.account_id)
        except OwnerCredentialUnavailable:
            _abort(context, grpc.StatusCode.PERMISSION_DENIED, "mock owner credential unavailable")
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
        if not _symbol(request.symbol) or request.estimated_price_krw <= 0:
            _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "buyable query is invalid")
        try:
            with self._session(request, frozenset({"CONNECTED", "CERTIFIED"})) as (_, reader):
                if reader is None:
                    _abort(context, grpc.StatusCode.UNAVAILABLE, "mock buyable reader is not wired")
                response = reader.buyable(
                    request.account_id,
                    request.symbol,
                    request.estimated_price_krw,
                )
        except OwnerCredentialUnavailable:
            _abort(context, grpc.StatusCode.PERMISSION_DENIED, "mock owner credential unavailable")
        except Exception as error:
            # Preserve the error class without exposing account or provider payloads.
            _abort(
                context,
                grpc.StatusCode.UNAVAILABLE,
                f"mock buyable source unavailable: {type(error).__name__}",
            )
        if response is None:
            _abort(context, grpc.StatusCode.NOT_FOUND, "mock buyable account was not found")
        return response

    def VerifyMockConnection(
        self,
        request: brokerage_pb2.VerifyMockConnectionRequest,
        context: grpc.ServicerContext,
    ) -> brokerage_pb2.VerifyMockConnectionResponse:
        _require_authenticated(context, self._shared_secret)
        _validate_account(request.account_id, context)
        try:
            with self._session(request, frozenset({"STORED", "CONNECTED", "CERTIFIED"})) as (
                _,
                reader,
            ):
                if reader is None:
                    raise RuntimeError("BROKERAGE_CONNECTION_READER_UNAVAILABLE")
                reader.verify_connection(request.account_id)
        except OwnerCredentialUnavailable:
            _abort(context, grpc.StatusCode.PERMISSION_DENIED, "mock owner credential unavailable")
        except Exception:
            _abort(context, grpc.StatusCode.UNAVAILABLE, "mock connection check unavailable")
        return brokerage_pb2.VerifyMockConnectionResponse(
            account_id=request.account_id,
            connected=True,
        )

    def CertifyMockCredential(
        self,
        request: brokerage_pb2.CertifyMockCredentialRequest,
        context: grpc.ServicerContext,
    ) -> brokerage_pb2.CertifyMockCredentialResponse:
        _require_authenticated(context, self._shared_secret)
        if (
            self._owner_factory is None
            or not request.HasField("credential")
            or _OWNER_ID.fullmatch(request.owner_user_id) is None
            or _ACCOUNT_ID.fullmatch(request.account_id) is None
            or request.credential.owner_user_id != request.owner_user_id
            or request.credential.account_id != request.account_id
            or request.credential.credential_state != "CONNECTED"
            or _CERTIFICATION_ID.fullmatch(request.certification_id) is None
            or _REQUEST_ID.fullmatch(request.request_id) is None
            or _SESSION_DATE.fullmatch(request.session_date) is None
        ):
            _abort(
                context, grpc.StatusCode.INVALID_ARGUMENT, "mock certification request is invalid"
            )
        try:
            date.fromisoformat(request.session_date)
        except ValueError:
            _abort(
                context, grpc.StatusCode.INVALID_ARGUMENT, "mock certification request is invalid"
            )
        try:
            result = self._owner_factory.certify(
                request.credential,
                owner_user_id=request.owner_user_id,
                account_id=request.account_id,
                certification_id=request.certification_id,
                session_date=request.session_date,
                recovery=request.recovery,
            )
        except OwnerCredentialUnavailable:
            return _certification_failure(request, "PROVIDER_FAILED")
        except Exception:
            # Initialization and transport failures return fixed reason codes only.
            return _certification_failure(request, "PROVIDER_FAILED")
        states = {
            "PASS": brokerage_pb2.MOCK_CREDENTIAL_CERTIFICATION_PASS,
            "FAILED": brokerage_pb2.MOCK_CREDENTIAL_CERTIFICATION_FAILED,
            "RECOVERY_REQUIRED": brokerage_pb2.MOCK_CREDENTIAL_CERTIFICATION_RECOVERY_REQUIRED,
        }
        state = states.get(result.state)
        if state is None:
            return _certification_failure(request, "PROVIDER_FAILED")
        return brokerage_pb2.CertifyMockCredentialResponse(
            account_id=request.account_id,
            certification_id=request.certification_id,
            state=state,
            receipt_sha256=result.receipt_sha256,
            session_date=result.session_date,
            quote_calls=result.quote_calls,
            brokerage_calls=result.brokerage_calls,
            token_calls=result.token_calls,
            failure_code=result.failure_code,
        )


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


def _certification_failure(
    request: brokerage_pb2.CertifyMockCredentialRequest,
    failure_code: str,
) -> brokerage_pb2.CertifyMockCredentialResponse:
    return brokerage_pb2.CertifyMockCredentialResponse(
        account_id=request.account_id,
        certification_id=request.certification_id,
        state=brokerage_pb2.MOCK_CREDENTIAL_CERTIFICATION_FAILED,
        session_date=request.session_date,
        failure_code=failure_code,
    )


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
