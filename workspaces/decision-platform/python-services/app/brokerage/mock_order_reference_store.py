"""KIS Mock 취소에 필요한 provider reference를 Redis에 암호화해 보관한다."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Protocol, cast

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

_ORDER_ID = re.compile(r"^ord_mock_[0-9a-f]{32}$")
_ACCOUNT_ID = re.compile(r"^acct_[0-9a-f]{32}$")
_PROVIDER_REFERENCE = re.compile(r"^[0-9A-Za-z._:-]{1,64}$")
_ORDER_DIVISION = re.compile(r"^[0-9]{2}$")
_FIELDS = {
    "accountId",
    "orderDivision",
    "orderId",
    "providerOrderNo",
    "providerOrgNo",
    "quantity",
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


class MockOrderReferenceStore(Protocol):
    """Spring/DB에는 raw reference를 넘기지 않는 최소 저장 port다."""

    def put(
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
    ) -> object: ...

    def get(self, name: str) -> object: ...


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
        raw_key = b""
        decoded = b""

    def put(
        self,
        order_id: str,
        account_id: str,
        reference: MockProviderOrderReference,
    ) -> None:
        """검증된 reference를 account/order에 결속한 ciphertext로 덮어쓴다."""
        _validate_identity(order_id, account_id)
        _validate_reference(reference)
        plaintext = json.dumps(
            {
                "accountId": account_id,
                "orderDivision": reference.order_division,
                "orderId": order_id,
                "providerOrderNo": reference.provider_order_no,
                "providerOrgNo": reference.provider_org_no,
                "quantity": reference.quantity,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            encrypted = self._fernet.encrypt(plaintext)
            stored = self._redis.set(
                self._key(order_id, account_id),
                encrypted,
                ex=self._ttl_seconds,
            )
        except Exception:
            raise MockOrderReferenceUnavailable(
                "mock order reference storage is unavailable"
            ) from None
        finally:
            plaintext = b""
        if stored is not True:
            raise MockOrderReferenceUnavailable(
                "mock order reference storage is unavailable"
            )

    def get(
        self,
        order_id: str,
        account_id: str,
    ) -> MockProviderOrderReference | None:
        """ciphertext를 복호화한 뒤 embedded owner/order 결속과 exact field set을 재검증한다."""
        _validate_identity(order_id, account_id)
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
        plaintext = b""
        try:
            plaintext = self._fernet.decrypt(bytes(stored))
            payload: object = json.loads(plaintext)
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError):
            raise MockOrderReferenceUnavailable("mock order reference is invalid") from None
        finally:
            plaintext = b""
        if not isinstance(payload, dict) or set(payload) != _FIELDS:
            raise MockOrderReferenceUnavailable("mock order reference is invalid")
        typed = cast(dict[str, object], payload)
        if typed["orderId"] != order_id or typed["accountId"] != account_id:
            raise MockOrderReferenceUnavailable("mock order reference is invalid")
        reference = MockProviderOrderReference(
            provider_order_no=str(typed["providerOrderNo"]),
            provider_org_no=str(typed["providerOrgNo"]),
            order_division=str(typed["orderDivision"]),
            quantity=typed["quantity"] if type(typed["quantity"]) is int else -1,
        )
        try:
            _validate_reference(reference)
        except ValueError:
            raise MockOrderReferenceUnavailable("mock order reference is invalid") from None
        return reference

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
