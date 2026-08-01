"""KIS Mock 취소에 필요한 provider reference를 Redis에 암호화해 보관한다."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from threading import Lock
from collections.abc import Mapping
from typing import Any, Literal, Protocol, cast

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

_ORDER_ID = re.compile(r"^ord_mock_[0-9a-f]{32}$")
_ACCOUNT_ID = re.compile(r"^acct_[0-9a-f]{32}$")
_PROVIDER_REFERENCE = re.compile(r"^[0-9A-Za-z._:-]{1,64}$")
_ORDER_DIVISION = re.compile(r"^[0-9]{2}$")
_EXCHANGE_DIVISION = re.compile(r"^(?:KRX|NXT)$")
_APPROVAL_ANCHOR = re.compile(r"^[0-9a-f]{64}$")
_APPROVAL_ID = re.compile(r"^approval-s3-online-[a-z0-9][a-z0-9-]{3,95}$")
_PACKET_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NONCE = re.compile(r"^[0-9a-f]{64}$")
_OUTCOME_STEP = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,63}$")
_RECOVERABLE_FAILED_STEPS = frozenset({"cancelFull", "executionRead"})
_PENDING_FIELDS = {
    "accountId",
    "exchangeDivision",
    "orderDivision",
    "orderId",
    "quantity",
    "state",
}
_PENDING_ANCHORED_FIELDS = _PENDING_FIELDS | {"approvalAnchor"}
_COMMITTED_FIELDS = {
    "accountId",
    "exchangeDivision",
    "orderDivision",
    "orderId",
    "providerOrderNo",
    "providerOrgNo",
    "quantity",
    "state",
}
_COMMITTED_ANCHORED_FIELDS = _COMMITTED_FIELDS | {"approvalAnchor"}
_APPROVAL_OUTCOME_FIELDS = {
    "accountId",
    "approvalId",
    "failedStep",
    "nonce",
    "orderId",
    "packetSha256",
    "probeType",
    "referenceAnchor",
}
_MAX_QUANTITY = 9_223_372_036_854_775_807


class MockOrderReferenceUnavailable(RuntimeError):
    """암호화 reference가 없거나 손상되면 취소 호출 전에 fail-closed한다."""


class KISMockApprovalOutcomeUnavailable(RuntimeError):
    """source probe 결과가 없거나 완결성이 깨지면 recovery provider 호출 전에 fail-closed한다."""


@dataclass(frozen=True, slots=True, repr=False)
class MockProviderOrderReference:
    """provider 원주문번호를 Python private boundary 안에서만 운반한다."""

    provider_order_no: str
    provider_org_no: str
    order_division: str
    quantity: int
    exchange_division: Literal["KRX", "NXT"] = "KRX"
    approval_anchor: str | None = None


@dataclass(frozen=True, slots=True)
class MockOrderReferenceIntent:
    """provider send 전에 durable pending marker에 넣는 비민감 주문 취소 계약이다."""

    order_division: str
    quantity: int
    exchange_division: Literal["KRX", "NXT"] = "KRX"
    approval_anchor: str | None = None


@dataclass(frozen=True, slots=True)
class KISMockApprovalOutcome:
    """v2 source packet의 실패 단계와 encrypted order-reference lineage를 Redis에 결속한다."""

    approval_id: str
    packet_sha256: str
    nonce: str
    probe_type: Literal["FULL", "CANCEL_RECOVERY"]
    order_id: str
    account_id: str
    reference_anchor: str
    failed_step: str | None


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

    def get_for_recovery(
        self,
        order_id: str,
        account_id: str,
        approval_anchor: str,
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
        payload: dict[str, object] = {
            "accountId": account_id,
            "exchangeDivision": intent.exchange_division,
            "orderDivision": intent.order_division,
            "orderId": order_id,
            "quantity": intent.quantity,
            "state": "PENDING",
        }
        if intent.approval_anchor is not None:
            payload["approvalAnchor"] = intent.approval_anchor
        encrypted = self._encrypt(payload)
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
        """Redis의 durable PENDING ciphertext일 때만 reference를 COMMITTED로 원자 전환한다."""
        _validate_identity(order_id, account_id)
        _validate_reference(reference)
        key = self._key(order_id, account_id)
        expected, pending = self._pending_snapshot(key, order_id, account_id)
        expected_pending_fields = (
            _PENDING_ANCHORED_FIELDS if "approvalAnchor" in pending else _PENDING_FIELDS
        )
        if (
            pending.get("state") != "PENDING"
            or set(pending) != expected_pending_fields
            or pending.get("exchangeDivision") != reference.exchange_division
            or pending.get("orderDivision") != reference.order_division
            or pending.get("quantity") != reference.quantity
        ):
            raise MockOrderReferenceUnavailable("mock order reference is invalid")
        payload: dict[str, object] = {
            "accountId": account_id,
            "exchangeDivision": reference.exchange_division,
            "orderDivision": reference.order_division,
            "orderId": order_id,
            "providerOrderNo": reference.provider_order_no,
            "providerOrgNo": reference.provider_org_no,
            "quantity": reference.quantity,
            "state": "COMMITTED",
        }
        if "approvalAnchor" in pending:
            approval_anchor = pending["approvalAnchor"]
            if not isinstance(approval_anchor, str) or _APPROVAL_ANCHOR.fullmatch(
                approval_anchor
            ) is None:
                raise MockOrderReferenceUnavailable("mock order reference is invalid")
            payload["approvalAnchor"] = approval_anchor
        encrypted = self._encrypt(payload)
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
        except MockOrderReferenceUnavailable:
            raise
        except Exception:
            raise MockOrderReferenceUnavailable(
                "mock order reference storage is unavailable"
            ) from None
        if not isinstance(results, list) or results != [True]:
            raise MockOrderReferenceUnavailable("mock order reference storage is unavailable")
        with self._pending_lock:
            self._pending_ciphertexts.pop(key, None)

    def _pending_snapshot(
        self,
        key: str,
        order_id: str,
        account_id: str,
    ) -> tuple[bytes, dict[str, object]]:
        """재시작 후 process-local map이 비어도 Redis PENDING 자체를 CAS 기준으로 사용한다."""
        with self._pending_lock:
            expected = self._pending_ciphertexts.get(key)
        if expected is None:
            try:
                stored = self._redis.get(key)
            except Exception:
                raise MockOrderReferenceUnavailable(
                    "mock order reference storage is unavailable"
                ) from None
            if not isinstance(stored, (bytes, bytearray, memoryview)):
                raise MockOrderReferenceUnavailable(
                    "mock order reference storage is unavailable"
                )
            expected = bytes(stored)
        pending = self._decode(expected, order_id, account_id)
        return expected, pending

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
        expected_fields = (
            _COMMITTED_ANCHORED_FIELDS if "approvalAnchor" in payload else _COMMITTED_FIELDS
        )
        if payload.get("state") != "COMMITTED" or set(payload) != expected_fields:
            raise MockOrderReferenceUnavailable("mock order reference is invalid")
        approval_anchor = payload.get("approvalAnchor")
        if approval_anchor is not None and not isinstance(approval_anchor, str):
            raise MockOrderReferenceUnavailable("mock order reference is invalid")
        reference = MockProviderOrderReference(
            provider_order_no=str(payload["providerOrderNo"]),
            provider_org_no=str(payload["providerOrgNo"]),
            order_division=str(payload["orderDivision"]),
            quantity=payload["quantity"] if type(payload["quantity"]) is int else -1,
            exchange_division=cast(Literal["KRX", "NXT"], str(payload["exchangeDivision"])),
            approval_anchor=approval_anchor,
        )
        try:
            _validate_reference(reference)
        except ValueError:
            raise MockOrderReferenceUnavailable("mock order reference is invalid") from None
        return reference

    def get_for_recovery(
        self,
        order_id: str,
        account_id: str,
        approval_anchor: str,
    ) -> MockProviderOrderReference | None:
        """recovery packet은 source packet에 anchor된 COMMITTED reference만 재사용한다."""

        if _APPROVAL_ANCHOR.fullmatch(approval_anchor) is None:
            raise MockOrderReferenceUnavailable("mock order reference is invalid")
        reference = self.get(order_id, account_id)
        if (
            reference is None
            or reference.approval_anchor is None
            or not hmac.compare_digest(reference.approval_anchor, approval_anchor)
        ):
            raise MockOrderReferenceUnavailable("mock order reference is unavailable")
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
        if state == "PENDING":
            expected_fields = (
                _PENDING_ANCHORED_FIELDS if "approvalAnchor" in payload else _PENDING_FIELDS
            )
        else:
            expected_fields = (
                _COMMITTED_ANCHORED_FIELDS if "approvalAnchor" in payload else _COMMITTED_FIELDS
            )
        if state not in {"PENDING", "COMMITTED"} or set(payload) != expected_fields:
            raise MockOrderReferenceUnavailable("mock order reference is invalid")
        approval_anchor = payload.get("approvalAnchor")
        if approval_anchor is not None and (
            not isinstance(approval_anchor, str)
            or _APPROVAL_ANCHOR.fullmatch(approval_anchor) is None
        ):
            raise MockOrderReferenceUnavailable("mock order reference is invalid")
        try:
            if state == "PENDING":
                _validate_intent(
                    MockOrderReferenceIntent(
                        order_division=str(payload["orderDivision"]),
                        quantity=payload["quantity"] if type(payload["quantity"]) is int else -1,
                        exchange_division=cast(
                            Literal["KRX", "NXT"],
                            str(payload["exchangeDivision"]),
                        ),
                    )
                )
            else:
                _validate_reference(
                    MockProviderOrderReference(
                        provider_order_no=str(payload["providerOrderNo"]),
                        provider_org_no=str(payload["providerOrgNo"]),
                        order_division=str(payload["orderDivision"]),
                        quantity=payload["quantity"] if type(payload["quantity"]) is int else -1,
                        exchange_division=cast(
                            Literal["KRX", "NXT"],
                            str(payload["exchangeDivision"]),
                        ),
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

    def _encrypt(self, payload: Mapping[str, object]) -> bytes:
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


class EncryptedRedisApprovalOutcomeStore:
    """recovery 대상 source packet의 실제 종료 결과만 encrypted Redis receipt로 보존한다.

    이 store는 provider raw payload나 order number를 기록하지 않는다. 같은 Fernet key에서
    별도 lookup purpose를 파생해 source packet, nonce, order identity, 실패 단계가 모두
    일치할 때만 recovery 실행을 허용한다.
    """

    def __init__(
        self,
        redis_client: RedisReferenceClient,
        *,
        encryption_key: SecretStr,
        ttl_seconds: int,
    ) -> None:
        if not 60 <= ttl_seconds <= 7 * 24 * 60 * 60:
            raise ValueError("approval outcome TTL must be between 60 seconds and 7 days")
        raw_key = encryption_key.get_secret_value().encode("ascii", errors="strict")
        try:
            decoded = base64.urlsafe_b64decode(raw_key)
        except (ValueError, TypeError):
            raise ValueError("approval outcome encryption key is invalid") from None
        if len(decoded) != 32 or base64.urlsafe_b64encode(decoded) != raw_key:
            raise ValueError("approval outcome encryption key is invalid")
        self._redis = redis_client
        self._fernet = Fernet(raw_key)
        self._lookup_key = hmac.new(
            decoded,
            b"s3-kis-mock-approval-outcome-lookup/v1",
            hashlib.sha256,
        ).digest()
        self._ttl_seconds = ttl_seconds
        raw_key = b""
        decoded = b""

    def record(self, outcome: KISMockApprovalOutcome) -> None:
        """exact packet은 성공·실패 어느 경우에도 outcome을 NX로 한 번만 봉인한다."""

        _validate_approval_outcome(outcome)
        payload = {
            "accountId": outcome.account_id,
            "approvalId": outcome.approval_id,
            "failedStep": outcome.failed_step,
            "nonce": outcome.nonce,
            "orderId": outcome.order_id,
            "packetSha256": outcome.packet_sha256,
            "probeType": outcome.probe_type,
            "referenceAnchor": outcome.reference_anchor,
        }
        try:
            stored = self._redis.set(
                self._outcome_key(
                    outcome.approval_id,
                    outcome.packet_sha256,
                    outcome.nonce,
                ),
                self._encrypt(payload),
                ex=self._ttl_seconds,
                nx=True,
            )
        except Exception:
            raise KISMockApprovalOutcomeUnavailable(
                "approval source outcome storage is unavailable"
            ) from None
        if stored is not True:
            raise KISMockApprovalOutcomeUnavailable("approval source outcome is unavailable")

    def require_recovery(
        self,
        *,
        source_approval_id: str,
        source_packet_sha256: str,
        source_nonce: str,
        expected_failed_step: Literal["cancelFull", "executionRead"],
        order_id: str,
        account_id: str,
    ) -> KISMockApprovalOutcome:
        """user input failedStep 대신 source executor가 봉인한 exact failure만 허용한다."""

        _validate_recovery_lookup(
            source_approval_id,
            source_packet_sha256,
            source_nonce,
            expected_failed_step,
            order_id,
            account_id,
        )
        outcome = self._read(
            source_approval_id,
            source_packet_sha256,
            source_nonce,
        )
        if (
            outcome.approval_id != source_approval_id
            or not hmac.compare_digest(outcome.packet_sha256, source_packet_sha256)
            or not hmac.compare_digest(outcome.nonce, source_nonce)
            or outcome.order_id != order_id
            or outcome.account_id != account_id
            or outcome.failed_step != expected_failed_step
            or outcome.failed_step not in _RECOVERABLE_FAILED_STEPS
        ):
            raise KISMockApprovalOutcomeUnavailable("approval source outcome does not match")
        return outcome

    def claim_recovery(
        self,
        *,
        source_approval_id: str,
        source_packet_sha256: str,
        source_nonce: str,
        recovery_packet_sha256: str,
    ) -> None:
        """한 source failure는 한 recovery packet만 실제 실행하도록 Redis NX로 잠근다."""

        if (
            _APPROVAL_ID.fullmatch(source_approval_id) is None
            or _PACKET_SHA256.fullmatch(source_packet_sha256) is None
            or _NONCE.fullmatch(source_nonce) is None
            or _PACKET_SHA256.fullmatch(recovery_packet_sha256) is None
        ):
            raise KISMockApprovalOutcomeUnavailable("approval source outcome is invalid")
        try:
            stored = self._redis.set(
                self._claim_key(
                    source_approval_id,
                    source_packet_sha256,
                    source_nonce,
                ),
                self._encrypt({"recoveryPacketSha256": recovery_packet_sha256}),
                ex=self._ttl_seconds,
                nx=True,
            )
        except Exception:
            raise KISMockApprovalOutcomeUnavailable(
                "approval recovery claim is unavailable"
            ) from None
        if stored is not True:
            raise KISMockApprovalOutcomeUnavailable("approval source outcome was recovered")

    def _read(
        self,
        approval_id: str,
        packet_sha256: str,
        nonce: str,
    ) -> KISMockApprovalOutcome:
        try:
            stored = self._redis.get(self._outcome_key(approval_id, packet_sha256, nonce))
        except Exception:
            raise KISMockApprovalOutcomeUnavailable(
                "approval source outcome storage is unavailable"
            ) from None
        if not isinstance(stored, (bytes, bytearray, memoryview)):
            raise KISMockApprovalOutcomeUnavailable("approval source outcome is unavailable")
        plaintext = b""
        try:
            plaintext = self._fernet.decrypt(bytes(stored))
            payload: object = json.loads(plaintext)
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError):
            raise KISMockApprovalOutcomeUnavailable("approval source outcome is invalid") from None
        finally:
            plaintext = b""
        if not isinstance(payload, dict) or set(payload) != _APPROVAL_OUTCOME_FIELDS:
            raise KISMockApprovalOutcomeUnavailable("approval source outcome is invalid")
        try:
            outcome = KISMockApprovalOutcome(
                approval_id=_required_text(payload, "approvalId"),
                packet_sha256=_required_text(payload, "packetSha256"),
                nonce=_required_text(payload, "nonce"),
                probe_type=cast(
                    Literal["FULL", "CANCEL_RECOVERY"],
                    _required_text(payload, "probeType"),
                ),
                order_id=_required_text(payload, "orderId"),
                account_id=_required_text(payload, "accountId"),
                reference_anchor=_required_text(payload, "referenceAnchor"),
                failed_step=_optional_text(payload, "failedStep"),
            )
            _validate_approval_outcome(outcome)
        except (TypeError, ValueError):
            raise KISMockApprovalOutcomeUnavailable("approval source outcome is invalid") from None
        return outcome

    def _encrypt(self, payload: Mapping[str, object]) -> bytes:
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

    def _outcome_key(self, approval_id: str, packet_sha256: str, nonce: str) -> str:
        return self._key("outcome", approval_id, packet_sha256, nonce)

    def _claim_key(self, approval_id: str, packet_sha256: str, nonce: str) -> str:
        return self._key("recovery-claim", approval_id, packet_sha256, nonce)

    def _key(self, purpose: str, approval_id: str, packet_sha256: str, nonce: str) -> str:
        digest = hmac.new(
            self._lookup_key,
            f"{purpose}\0{approval_id}\0{packet_sha256}\0{nonce}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"kis:brokerage:approval-outcome:v1:{digest}"


def _required_text(payload: dict[object, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValueError("approval outcome field is invalid")
    return value


def _optional_text(payload: dict[object, object], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("approval outcome field is invalid")
    return value


def _validate_approval_outcome(outcome: KISMockApprovalOutcome) -> None:
    _validate_identity(outcome.order_id, outcome.account_id)
    if (
        _APPROVAL_ID.fullmatch(outcome.approval_id) is None
        or _PACKET_SHA256.fullmatch(outcome.packet_sha256) is None
        or _NONCE.fullmatch(outcome.nonce) is None
        or outcome.probe_type not in {"FULL", "CANCEL_RECOVERY"}
        or _APPROVAL_ANCHOR.fullmatch(outcome.reference_anchor) is None
        or (
            outcome.failed_step is not None
            and _OUTCOME_STEP.fullmatch(outcome.failed_step) is None
        )
    ):
        raise ValueError("approval outcome is invalid")


def _validate_recovery_lookup(
    source_approval_id: str,
    source_packet_sha256: str,
    source_nonce: str,
    expected_failed_step: str,
    order_id: str,
    account_id: str,
) -> None:
    _validate_identity(order_id, account_id)
    if (
        _APPROVAL_ID.fullmatch(source_approval_id) is None
        or _PACKET_SHA256.fullmatch(source_packet_sha256) is None
        or _NONCE.fullmatch(source_nonce) is None
        or expected_failed_step not in _RECOVERABLE_FAILED_STEPS
    ):
        raise ValueError("approval recovery lookup is invalid")


def _validate_identity(order_id: str, account_id: str) -> None:
    if _ORDER_ID.fullmatch(order_id) is None or _ACCOUNT_ID.fullmatch(account_id) is None:
        raise ValueError("mock order reference identity is invalid")


def _validate_reference(reference: MockProviderOrderReference) -> None:
    if (
        _PROVIDER_REFERENCE.fullmatch(reference.provider_order_no) is None
        or _PROVIDER_REFERENCE.fullmatch(reference.provider_org_no) is None
        or _ORDER_DIVISION.fullmatch(reference.order_division) is None
        or _EXCHANGE_DIVISION.fullmatch(reference.exchange_division) is None
        or type(reference.quantity) is not int
        or not 1 <= reference.quantity <= _MAX_QUANTITY
        or (
            reference.approval_anchor is not None
            and _APPROVAL_ANCHOR.fullmatch(reference.approval_anchor) is None
        )
    ):
        raise ValueError("mock provider order reference is invalid")


def _validate_intent(intent: MockOrderReferenceIntent) -> None:
    if (
        _ORDER_DIVISION.fullmatch(intent.order_division) is None
        or _EXCHANGE_DIVISION.fullmatch(intent.exchange_division) is None
        or type(intent.quantity) is not int
        or not 1 <= intent.quantity <= _MAX_QUANTITY
        or (
            intent.approval_anchor is not None
            and _APPROVAL_ANCHOR.fullmatch(intent.approval_anchor) is None
        )
    ):
        raise ValueError("mock order reference intent is invalid")
