"""Common Ed25519 approval envelope for offline provider executors.

Provider-specific packets stay narrow. This outer packet is the only execution
authority: verify it with the pinned public key, then consume it once in
PostgreSQL before constructing a provider client.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, cast

from cryptography.exceptions import InvalidSignature

from app.data._shared.canonical_json import canonical_json_bytes, canonical_json_sha256
from app.verification.packet import _load_private_key, _load_public_key, _read_public_key

_CONTRACT_ID: Final = "p1-approval-packet.v2"
_MAX_PACKET_BYTES: Final = 32 * 1024
_APPROVAL_ID = re.compile(r"^[A-Z0-9][A-Z0-9._-]{7,95}$")
_KEY_ID = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,63}$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_TOKEN = re.compile(r"^[A-Z][A-Z0-9._-]{1,95}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SIGNATURE = re.compile(r"^[A-Za-z0-9_-]{86}$")
ZERO_SCOPE_SHA256: Final = hashlib.sha256(b"NONE").hexdigest()


class ExecutionApprovalError(RuntimeError):
    """The detached approval is absent, stale, invalid, or mismatched."""


@dataclass(frozen=True, slots=True)
class P1ExecutionApproval:
    approval_id: str
    issuer_key_id: str
    nonce: str
    provider_family: str
    exact_operations: tuple[str, ...]
    payload_sha256: str
    repository_digest: str
    evidence_digest: str
    owner_scope_digest: str
    account_scope_digest: str
    credential_scope_digest: str
    physical_call_cap: int
    cost_cap_microusd: int
    expires_at: datetime
    signature: str

    @property
    def allowed_operations(self) -> tuple[str, ...]:
        return self.exact_operations

    @property
    def packet_sha256(self) -> str:
        return canonical_json_sha256(self.to_dict())

    def signing_body(self) -> dict[str, object]:
        return {
            "accountScopeDigest": self.account_scope_digest,
            "approvalId": self.approval_id,
            "contractId": _CONTRACT_ID,
            "costCapMicrousd": self.cost_cap_microusd,
            "credentialScopeDigest": self.credential_scope_digest,
            "evidenceDigest": self.evidence_digest,
            "exactOperations": list(self.exact_operations),
            "expiresAt": _instant(self.expires_at),
            "issuerKeyId": self.issuer_key_id,
            "nonce": self.nonce,
            "ownerScopeDigest": self.owner_scope_digest,
            "payloadSha256": self.payload_sha256,
            "physicalCallCap": self.physical_call_cap,
            "providerFamily": self.provider_family,
            "repositoryDigest": self.repository_digest,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.signing_body(), "signature": self.signature}

    def validate(self, *, now: datetime | None = None) -> None:
        if _APPROVAL_ID.fullmatch(self.approval_id) is None:
            raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_ID_INVALID")
        if _KEY_ID.fullmatch(self.issuer_key_id) is None or _NONCE.fullmatch(self.nonce) is None:
            raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_ISSUER_INVALID")
        if _TOKEN.fullmatch(self.provider_family) is None:
            raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_PROVIDER_INVALID")
        if (
            not self.exact_operations
            or len(set(self.exact_operations)) != len(self.exact_operations)
            or any(_TOKEN.fullmatch(item) is None for item in self.exact_operations)
        ):
            raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_OPERATION_INVALID")
        digests = (
            self.payload_sha256,
            self.repository_digest,
            self.evidence_digest,
            self.owner_scope_digest,
            self.account_scope_digest,
            self.credential_scope_digest,
        )
        if any(_SHA256.fullmatch(item) is None for item in digests):
            raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_DIGEST_INVALID")
        if (
            type(self.physical_call_cap) is not int
            or not 1 <= self.physical_call_cap <= 112
            or type(self.cost_cap_microusd) is not int
            or not 0 <= self.cost_cap_microusd <= 1_000_000
        ):
            raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_CAP_INVALID")
        if self.expires_at.tzinfo is None or _SIGNATURE.fullmatch(self.signature) is None:
            raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_ENCODING_INVALID")
        if now is not None:
            current = now.astimezone(UTC)
            expiry = self.expires_at.astimezone(UTC)
            if not current < expiry <= current + timedelta(minutes=5):
                raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_EXPIRED")


def author_execution_approval(
    *,
    approval_id: str,
    issuer_key_id: str,
    private_key_path: Path,
    provider_family: str,
    exact_operations: Sequence[str],
    payload_sha256: str,
    repository_digest: str,
    evidence_digest: str,
    owner_scope_digest: str,
    account_scope_digest: str,
    credential_scope_digest: str,
    physical_call_cap: int,
    cost_cap_microusd: int,
    now: datetime,
) -> P1ExecutionApproval:
    """Author one five-minute outer packet outside the executor boundary."""
    if now.tzinfo is None:
        raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_CLOCK_INVALID")
    unsigned = P1ExecutionApproval(
        approval_id,
        issuer_key_id,
        secrets.token_hex(16),
        provider_family,
        tuple(exact_operations),
        payload_sha256,
        repository_digest,
        evidence_digest,
        owner_scope_digest,
        account_scope_digest,
        credential_scope_digest,
        physical_call_cap,
        cost_cap_microusd,
        now.astimezone(UTC) + timedelta(minutes=5),
        "A" * 86,
    )
    unsigned.validate(now=now)
    signature = _b64url(
        _load_private_key(private_key_path).sign(canonical_json_bytes(unsigned.signing_body()))
    )
    return replace(unsigned, signature=signature)


def load_and_verify_execution_approval(
    approval_path: Path,
    *,
    provider_family: str,
    exact_operations: Sequence[str],
    payload_sha256: str,
    repository_digest: str,
    evidence_digest: str,
    physical_call_cap: int,
    cost_cap_microusd: int,
    now: datetime,
    owner_scope_digest: str = ZERO_SCOPE_SHA256,
    account_scope_digest: str = ZERO_SCOPE_SHA256,
    credential_scope_digest: str = ZERO_SCOPE_SHA256,
) -> P1ExecutionApproval:
    """Verify the exact scope using the root-owned public-key trust policy."""
    approval = execution_approval_from_dict(_read_packet(approval_path))
    observed = (
        approval.provider_family,
        approval.exact_operations,
        approval.payload_sha256,
        approval.repository_digest,
        approval.evidence_digest,
        approval.owner_scope_digest,
        approval.account_scope_digest,
        approval.credential_scope_digest,
        approval.physical_call_cap,
        approval.cost_cap_microusd,
    )
    expected = (
        provider_family,
        tuple(exact_operations),
        payload_sha256,
        repository_digest,
        evidence_digest,
        owner_scope_digest,
        account_scope_digest,
        credential_scope_digest,
        physical_call_cap,
        cost_cap_microusd,
    )
    if observed != expected:
        raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_SCOPE_MISMATCH")
    from app.verification.cli import _approval_trust_anchor

    key_path, issuer, key_digest = _approval_trust_anchor()
    approval.validate(now=now)
    if approval.issuer_key_id != issuer:
        raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_ISSUER_UNTRUSTED")
    public_bytes = _read_public_key(key_path)
    if not secrets.compare_digest(hashlib.sha256(public_bytes).hexdigest(), key_digest):
        raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_KEY_MISMATCH")
    try:
        _load_public_key(public_bytes).verify(
            _decode(approval.signature), canonical_json_bytes(approval.signing_body())
        )
    except InvalidSignature as error:
        raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_SIGNATURE_INVALID") from error
    return approval


def execution_approval_from_dict(value: Mapping[str, object]) -> P1ExecutionApproval:
    expected = {
        "accountScopeDigest",
        "approvalId",
        "contractId",
        "costCapMicrousd",
        "credentialScopeDigest",
        "evidenceDigest",
        "exactOperations",
        "expiresAt",
        "issuerKeyId",
        "nonce",
        "ownerScopeDigest",
        "payloadSha256",
        "physicalCallCap",
        "providerFamily",
        "repositoryDigest",
        "signature",
    }
    operations = value.get("exactOperations")
    strings = expected - {"costCapMicrousd", "exactOperations", "physicalCallCap"}
    if (
        set(value) != expected
        or value.get("contractId") != _CONTRACT_ID
        or not isinstance(operations, list)
        or not all(isinstance(item, str) for item in operations)
        or any(not isinstance(value.get(item), str) for item in strings)
        or type(value.get("physicalCallCap")) is not int
        or type(value.get("costCapMicrousd")) is not int
    ):
        raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_FIELDS_INVALID")
    try:
        packet = P1ExecutionApproval(
            cast(str, value["approvalId"]),
            cast(str, value["issuerKeyId"]),
            cast(str, value["nonce"]),
            cast(str, value["providerFamily"]),
            tuple(cast(list[str], operations)),
            cast(str, value["payloadSha256"]),
            cast(str, value["repositoryDigest"]),
            cast(str, value["evidenceDigest"]),
            cast(str, value["ownerScopeDigest"]),
            cast(str, value["accountScopeDigest"]),
            cast(str, value["credentialScopeDigest"]),
            cast(int, value["physicalCallCap"]),
            cast(int, value["costCapMicrousd"]),
            _parse_instant(cast(str, value["expiresAt"])),
            cast(str, value["signature"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_VALUES_INVALID") from error
    packet.validate()
    return packet


def scope_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_packet(path: Path) -> Mapping[str, object]:
    if not path.is_absolute():
        raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_PATH_INVALID")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_UNAVAILABLE") from error
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(info.st_mode) & 0o077
            or not 1 <= info.st_size <= _MAX_PACKET_BYTES
        ):
            raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_FILE_UNSAFE")
        content = os.read(descriptor, _MAX_PACKET_BYTES + 1)
        if len(content) > _MAX_PACKET_BYTES or os.read(descriptor, 1):
            raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_FILE_UNSAFE")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_JSON_INVALID") from error
    if not isinstance(value, Mapping) or canonical_json_bytes(value) != content:
        raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_CANONICAL_INVALID")
    return value


def _parse_instant(value: str) -> datetime:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset() != UTC.utcoffset(instant):
        raise ValueError("instant must be UTC")
    return instant


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    decoded = base64.urlsafe_b64decode(value + "==")
    if _b64url(decoded) != value:
        raise ExecutionApprovalError("P1_EXECUTION_APPROVAL_SIGNATURE_ENCODING_INVALID")
    return decoded
