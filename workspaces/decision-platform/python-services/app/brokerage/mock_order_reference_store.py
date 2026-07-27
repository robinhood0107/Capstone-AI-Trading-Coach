"""KIS Mock 취소에 필요한 provider reference를 Redis에 암호화해 보관한다."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from threading import Lock
from typing import Any, Literal, Protocol, cast

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

_ORDER_ID = re.compile(r"^ord_mock_[0-9a-f]{32}$")
_ACCOUNT_ID = re.compile(r"^acct_[0-9a-f]{32}$")
_PROVIDER_REFERENCE = re.compile(r"^[0-9A-Za-z._:-]{1,64}$")
_ORDER_DIVISION = re.compile(r"^[0-9]{2}$")
_PENDING_FIELDS = {
    "accountId",
    "orderDivision",
    "orderId",
    "quantity",
    "state",
}
_COMMITTED_FIELDS = {
    "accountId",
    "orderDivision",
    "orderId",
    "providerOrderNo",
    "providerOrgNo",
    "quantity",
    "state",
}
_MAX_QUANTITY = 9_223_372_036_854_775_807


class MockOrderReferenceUnavailable(RuntimeError):
    """암호화 reference가 없거나 손상되면 취소 호출 전에 fail-closed한다."""


@dataclass(frozen=True, slots=True, repr=False)
class MockProviderOrderReference:
    """provider 원주문번호를 Python private boundary 안에서만 운반한다."""

    provider_order_no: str
    provider_org_no: str
    order_division: str
    quantity: int


@dataclass(frozen=True, slots=True)
class MockOrderReferenceIntent:
    """provider send 전에 durable pending marker에 넣는 비민감 주문 취소 계약이다."""

    order_division: str
    quantity: int


class MockOrderReferenceStore(Protocol):
    """Spring/DB에는 raw reference를 넘기지 않는 최소 저장 port다."""

    def prepare(
        self,
        order_id: str,
        account_id: str,
        intent: MockOrderReferenceIntent,
    ) -> None: ...

    def commit(
        self,
        order_id: str,
        account_id: str,
        reference: MockProviderOrderReference,
    ) -> None: ...

    def get(
        self,
        order_id: str,
        account_id: str,
    ) -> MockProviderOrderReference | None: ...


class RedisReferenceClient(Protocol):
    def set(
        self,
        name: str,
        value: bytes,
        *,
        ex: int,
        nx: bool = False,
    ) -> object: ...

    def get(self, name: str) -> object: ...

    def pipeline(self) -> Any: ...


class EncryptedRedisOrderReferenceStore:
    """Fernet ciphertext만 Redis에 저장하고 key 이름도 HMAC으로 비가역화한다."""

    def __init__(
        self,
        redis_client: RedisReferenceClient,
        *,
        encryption_key: SecretStr,
        ttl_seconds: int,
    ) -> None:
        if not 60 <= ttl_seconds <= 7 * 24 * 60 * 60:
            raise ValueError("mock order reference TTL must be between 60 seconds and 7 days")
        raw_key = encryption_key.get_secret_value().encode("ascii", errors="strict")
        try:
            decoded = base64.urlsafe_b64decode(raw_key)
        except (ValueError, TypeError):
            raise ValueError("mock order reference encryption key is invalid") from None
        if len(decoded) != 32 or base64.urlsafe_b64encode(decoded) != raw_key:
            raise ValueError("mock order reference encryption key is invalid")
        self._redis = redis_client
        self._fernet = Fernet(raw_key)
        # Redis key 파생은 암호화 key와 purpose를 분리한 digest를 사용한다.
        self._lookup_key = hmac.new(
            decoded,
            b"s3-kis-mock-order-reference-lookup/v1",
            hashlib.sha256,
        ).digest()
        self._ttl_seconds = ttl_seconds
        self._pending_ciphertexts: dict[str, bytes] = {}
        self._pending_lock = Lock()
        raw_key = b""
        decoded = b""

    def prepare(
        self,
        order_id: str,
        account_id: str,
        intent: MockOrderReferenceIntent,
    ) -> None:
        """provider send 전에 encrypted PENDING marker를 NX로 확정한다."""
        _validate_identity(order_id, account_id)
        _validate_intent(intent)
        key = self._key(order_id, account_id)
        encrypted = self._encrypt(
            {
                "accountId": account_id,
                "orderDivision": intent.order_division,
                "orderId": order_id,
                "quantity": intent.quantity,
                "state": "PENDING",
            }
        )
        try:
            stored = self._redis.set(
                key,
                encrypted,
                ex=self._ttl_seconds,
                nx=True,
            )
        except Exception:
            raise MockOrderReferenceUnavailable(
                "mock order reference storage is unavailable"
            ) from None
        if stored is not True:
            raise MockOrderReferenceUnavailable("mock order reference storage is unavailable")
        with self._pending_lock:
            self._pending_ciphertexts[key] = encrypted

    def commit(
        self,
        order_id: str,
        account_id: str,
        reference: MockProviderOrderReference,
    ) -> None:
        """같은 PENDING ciphertext일 때만 provider reference를 COMMITTED로 원자 전환한다."""
        _validate_identity(order_id, account_id)
        _validate_reference(reference)
        key = self._key(order_id, account_id)
        with self._pending_lock:
            expected = self._pending_ciphertexts.get(key)
        if expected is None:
            raise MockOrderReferenceUnavailable("mock order reference storage is unavailable")
        pending = self._decode(expected, order_id, account_id)
        if (
            pending.get("state") != "PENDING"
            or pending.get("orderDivision") != reference.order_division
            or pending.get("quantity") != reference.quantity
        ):
            raise MockOrderReferenceUnavailable("mock order reference is invalid")
        encrypted = self._encrypt(
            {
                "accountId": account_id,
                "orderDivision": reference.order_division,
                "orderId": order_id,
                "providerOrderNo": reference.provider_order_no,
                "providerOrgNo": reference.provider_org_no,
                "quantity": reference.quantity,
                "state": "COMMITTED",
            }
        )
        try:
            with self._redis.pipeline() as pipeline:
                pipeline.watch(key)
                stored = pipeline.get(key)
                if not isinstance(
                    stored, (bytes, bytearray, memoryview)
                ) or not hmac.compare_digest(
                    bytes(stored),
                    expected,
                ):
                    pipeline.unwatch()
                    raise MockOrderReferenceUnavailable("mock order reference is invalid")
                pipeline.multi()
                pipeline.set(key, encrypted, ex=self._ttl_seconds)
                results = pipeline.execute()
        except Exception:
            raise MockOrderReferenceUnavailable(
                "mock order reference storage is unavailable"
            ) from None
        if not isinstance(results, list) or results != [True]:
            raise MockOrderReferenceUnavailable("mock order reference storage is unavailable")
        with self._pending_lock:
            self._pending_ciphertexts.pop(key, None)

    def get(
        self,
        order_id: str,
        account_id: str,
    ) -> MockProviderOrderReference | None:
        """ciphertext를 복호화한 뒤 embedded owner/order 결속과 exact field set을 재검증한다."""
        _validate_identity(order_id, account_id)
        payload = self._read(order_id, account_id)
        if payload is None:
            return None
        if payload.get("state") != "COMMITTED" or set(payload) != _COMMITTED_FIELDS:
            raise MockOrderReferenceUnavailable("mock order reference is invalid")
        reference = MockProviderOrderReference(
            provider_order_no=str(payload["providerOrderNo"]),
            provider_org_no=str(payload["providerOrgNo"]),
            order_division=str(payload["orderDivision"]),
            quantity=payload["quantity"] if type(payload["quantity"]) is int else -1,
        )
        try:
            _validate_reference(reference)
        except ValueError:
            raise MockOrderReferenceUnavailable("mock order reference is invalid") from None
        return reference

    def state(
        self,
        order_id: str,
        account_id: str,
    ) -> Literal["PENDING", "COMMITTED"] | None:
        """운영 대사가 raw reference 없이 durable submit 상태만 확인하게 한다."""
        _validate_identity(order_id, account_id)
        payload = self._read(order_id, account_id)
        if payload is None:
            return None
        state = payload.get("state")
        expected_fields = _PENDING_FIELDS if state == "PENDING" else _COMMITTED_FIELDS
        if state not in {"PENDING", "COMMITTED"} or set(payload) != expected_fields:
            raise MockOrderReferenceUnavailable("mock order reference is invalid")
        try:
            if state == "PENDING":
                _validate_intent(
                    MockOrderReferenceIntent(
                        order_division=str(payload["orderDivision"]),
                        quantity=payload["quantity"] if type(payload["quantity"]) is int else -1,
                    )
                )
            else:
                _validate_reference(
                    MockProviderOrderReference(
                        provider_order_no=str(payload["providerOrderNo"]),
                        provider_org_no=str(payload["providerOrgNo"]),
                        order_division=str(payload["orderDivision"]),
                        quantity=payload["quantity"] if type(payload["quantity"]) is int else -1,
                    )
                )
        except ValueError:
            raise MockOrderReferenceUnavailable("mock order reference is invalid") from None
        return state

    def _read(
        self,
        order_id: str,
        account_id: str,
    ) -> dict[str, object] | None:
        try:
            stored = self._redis.get(self._key(order_id, account_id))
        except Exception:
            raise MockOrderReferenceUnavailable(
                "mock order reference storage is unavailable"
            ) from None
        if stored is None:
            return None
        if not isinstance(stored, (bytes, bytearray, memoryview)):
            raise MockOrderReferenceUnavailable("mock order reference is invalid")
        return self._decode(bytes(stored), order_id, account_id)

    def _decode(
        self,
        encrypted: bytes,
        order_id: str,
        account_id: str,
    ) -> dict[str, object]:
        plaintext = b""
        try:
            plaintext = self._fernet.decrypt(encrypted)
            payload: object = json.loads(plaintext)
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError):
            raise MockOrderReferenceUnavailable("mock order reference is invalid") from None
        finally:
            plaintext = b""
        if not isinstance(payload, dict):
            raise MockOrderReferenceUnavailable("mock order reference is invalid")
        typed = cast(dict[str, object], payload)
        if typed.get("orderId") != order_id or typed.get("accountId") != account_id:
            raise MockOrderReferenceUnavailable("mock order reference is invalid")
        return typed

    def _encrypt(self, payload: dict[str, object]) -> bytes:
        plaintext = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            return self._fernet.encrypt(plaintext)
        finally:
            plaintext = b""

    def _key(self, order_id: str, account_id: str) -> str:
        digest = hmac.new(
            self._lookup_key,
            f"{order_id}\0{account_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"kis:brokerage:order-ref:v1:{digest}"


def _validate_identity(order_id: str, account_id: str) -> None:
    if _ORDER_ID.fullmatch(order_id) is None or _ACCOUNT_ID.fullmatch(account_id) is None:
        raise ValueError("mock order reference identity is invalid")


def _validate_reference(reference: MockProviderOrderReference) -> None:
    if (
        _PROVIDER_REFERENCE.fullmatch(reference.provider_order_no) is None
        or _PROVIDER_REFERENCE.fullmatch(reference.provider_org_no) is None
        or _ORDER_DIVISION.fullmatch(reference.order_division) is None
        or type(reference.quantity) is not int
        or not 1 <= reference.quantity <= _MAX_QUANTITY
    ):
        raise ValueError("mock provider order reference is invalid")


def _validate_intent(intent: MockOrderReferenceIntent) -> None:
    if (
        _ORDER_DIVISION.fullmatch(intent.order_division) is None
        or type(intent.quantity) is not int
        or not 1 <= intent.quantity <= _MAX_QUANTITY
    ):
        raise ValueError("mock order reference intent is invalid")
