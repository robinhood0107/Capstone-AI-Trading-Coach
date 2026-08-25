"""Short-lived, repository-bound authority packet for the isolated provider smoke."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Final, cast
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.data._shared.canonical_json import canonical_json_bytes, canonical_json_sha256
from app.verification.git_identity import current_clean_git_identity

LIVE_OPERATIONS: Final[tuple[str, ...]] = (
    "KRX_KOSPI_DAILY",
    "KRX_KOSDAQ_DAILY",
    "KIS_CURRENT_PRICE",
    "KIS_DAILY_BAR",
    "ECOS_POLICY_RATE_DAILY",
    "ECOS_KRW_USD_DAILY",
)
_KST = ZoneInfo("Asia/Seoul")
_APPROVAL_ID = re.compile(r"^[A-Z0-9][A-Z0-9._-]{7,95}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HEAD = re.compile(r"^[0-9a-f]{40}$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_KEY_ID = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,63}$")
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_SIGNATURE = re.compile(r"^[A-Za-z0-9_-]{86}$")
_MAX_KEY_BYTES: Final = 8_192
_SIGNED_CONTRACT_ID: Final = "p1-approval-packet.v2"


class VerificationPacketError(RuntimeError):
    """The provider smoke packet is invalid, stale, or not repository-bound."""


@dataclass(frozen=True, slots=True)
class VerificationTarget:
    session_date: date
    symbol: str = "005930"

    def to_dict(self) -> dict[str, str]:
        return {
            "ecosFrom": (self.session_date - timedelta(days=29)).isoformat(),
            "ecosTo": self.session_date.isoformat(),
            "sessionDate": self.session_date.isoformat(),
            "symbol": self.symbol,
        }


@dataclass(frozen=True, slots=True)
class P1VerificationPacket:
    approval_id: str
    issued_at: datetime
    expires_at: datetime
    head_sha: str
    tree_sha256: str
    uv_lock_sha256: str
    contract_catalog_sha256: str
    target: VerificationTarget
    kis_token_physical_call_cap: int

    @property
    def packet_sha256(self) -> str:
        return canonical_json_sha256(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "accountCallCap": 0,
            "approvalId": self.approval_id,
            "balanceCallCap": 0,
            "contractCatalogSha256": self.contract_catalog_sha256,
            "contractId": "p1-verification-packet.v1",
            "expiresAt": _iso(self.expires_at),
            "headSha": self.head_sha,
            "issuedAt": _iso(self.issued_at),
            "kisTokenPhysicalCallCap": self.kis_token_physical_call_cap,
            "operations": list(LIVE_OPERATIONS),
            "orderCallCap": 0,
            "productDbWriteAllowed": False,
            "profile": "PROVIDER_READ_SMOKE",
            "providerDataPhysicalCallCap": 6,
            "retransmissionAllowed": False,
            "target": self.target.to_dict(),
            "totalPhysicalCallCap": 6 + self.kis_token_physical_call_cap,
            "treeSha256": self.tree_sha256,
            "uvLockSha256": self.uv_lock_sha256,
        }

    def validate(self, *, now: datetime | None = None) -> None:
        if _APPROVAL_ID.fullmatch(self.approval_id) is None:
            raise VerificationPacketError("P1 verification approval id is invalid")
        if self.issued_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise VerificationPacketError("P1 verification packet times must be timezone aware")
        lifetime = self.expires_at - self.issued_at
        if lifetime <= timedelta(0) or lifetime > timedelta(minutes=60):
            raise VerificationPacketError("P1 verification packet TTL exceeds 60 minutes")
        if now is not None and not self.issued_at <= now <= self.expires_at:
            raise VerificationPacketError("P1 verification packet is not currently valid")
        if _HEAD.fullmatch(self.head_sha) is None or any(
            _SHA256.fullmatch(value) is None
            for value in (
                self.tree_sha256,
                self.uv_lock_sha256,
                self.contract_catalog_sha256,
            )
        ):
            raise VerificationPacketError("P1 verification repository binding is invalid")
        if self.target.symbol != "005930" or self.kis_token_physical_call_cap not in {0, 1}:
            raise VerificationPacketError("P1 verification target or token cap is invalid")


@dataclass(frozen=True, slots=True)
class P1SignedApprovalPacket:
    """Short-lived provider authority signed outside the application trust boundary."""

    approval_id: str
    nonce: str
    issuer_key_id: str
    allowed_operations: tuple[str, ...]
    physical_call_cap: int
    head_sha: str
    tree_sha256: str
    target: VerificationTarget
    expires_at: datetime
    reason_code: str
    signature: str

    @property
    def packet_sha256(self) -> str:
        return canonical_json_sha256(self.to_dict())

    @property
    def kis_token_physical_call_cap(self) -> int:
        return self.physical_call_cap - len(LIVE_OPERATIONS)

    def signing_body(self) -> dict[str, object]:
        return {
            "allowedOperations": list(self.allowed_operations),
            "approvalId": self.approval_id,
            "contractId": _SIGNED_CONTRACT_ID,
            "expiresAt": _iso(self.expires_at),
            "headSha": self.head_sha,
            "issuerKeyId": self.issuer_key_id,
            "nonce": self.nonce,
            "physicalCallCap": self.physical_call_cap,
            "reasonCode": self.reason_code,
            "target": self.target.to_dict(),
            "treeSha": self.tree_sha256,
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.signing_body(), "signature": self.signature}

    def validate(self, *, now: datetime | None = None) -> None:
        if _APPROVAL_ID.fullmatch(self.approval_id) is None:
            raise VerificationPacketError("P1 approval id is invalid")
        if _NONCE.fullmatch(self.nonce) is None:
            raise VerificationPacketError("P1 approval nonce is invalid")
        if _KEY_ID.fullmatch(self.issuer_key_id) is None:
            raise VerificationPacketError("P1 approval issuer key id is invalid")
        if self.allowed_operations != LIVE_OPERATIONS:
            raise VerificationPacketError("P1 approval operation set drifted")
        if self.physical_call_cap not in {len(LIVE_OPERATIONS), len(LIVE_OPERATIONS) + 1}:
            raise VerificationPacketError("P1 approval physical call cap is invalid")
        if _HEAD.fullmatch(self.head_sha) is None or _SHA256.fullmatch(self.tree_sha256) is None:
            raise VerificationPacketError("P1 approval repository binding is invalid")
        if self.target.symbol != "005930":
            raise VerificationPacketError("P1 approval target is invalid")
        if self.expires_at.tzinfo is None:
            raise VerificationPacketError("P1 approval expiry must be timezone aware")
        if _REASON_CODE.fullmatch(self.reason_code) is None:
            raise VerificationPacketError("P1 approval reason code is invalid")
        if _SIGNATURE.fullmatch(self.signature) is None:
            raise VerificationPacketError("P1 approval signature encoding is invalid")
        if now is not None:
            current = now.astimezone(UTC)
            expiry = self.expires_at.astimezone(UTC)
            if not current < expiry <= current + timedelta(minutes=5):
                raise VerificationPacketError(
                    "P1 approval is expired or exceeds the five-minute window"
                )


def author_signed_provider_read_smoke_packet(
    *,
    repository_root: Path,
    approval_id: str,
    private_key_path: Path,
    issuer_key_id: str,
    reason_code: str,
    now: datetime,
    kis_token_physical_call_cap: int,
    git_identity: GitIdentity = current_clean_git_identity,
) -> P1SignedApprovalPacket:
    """Sign a v2 authority packet with an owner-private host key."""

    if now.tzinfo is None:
        raise VerificationPacketError("P1 approval authoring clock must be timezone aware")
    if kis_token_physical_call_cap not in {0, 1}:
        raise VerificationPacketError("P1 approval token cap is invalid")
    root = repository_root.resolve(strict=True)
    private_key_absolute = private_key_path.absolute()
    try:
        private_key_absolute.relative_to(root)
    except ValueError:
        pass
    else:
        raise VerificationPacketError("P1 approval private key must remain outside the repository")
    try:
        private_key_absolute.resolve(strict=True).relative_to(root)
    except ValueError:
        pass
    else:
        raise VerificationPacketError("P1 approval private key must remain outside the repository")
    head_sha, tree_sha256 = git_identity(root)
    unsigned = P1SignedApprovalPacket(
        approval_id=approval_id,
        nonce=secrets.token_hex(16),
        issuer_key_id=issuer_key_id,
        allowed_operations=LIVE_OPERATIONS,
        physical_call_cap=len(LIVE_OPERATIONS) + kis_token_physical_call_cap,
        head_sha=head_sha,
        tree_sha256=tree_sha256,
        target=VerificationTarget(session_date=latest_evidence_ready_session(now)),
        expires_at=now.astimezone(UTC) + timedelta(minutes=5),
        reason_code=reason_code,
        signature="A" * 86,
    )
    key = _load_private_key(private_key_path)
    signature = _base64url(key.sign(canonical_json_bytes(unsigned.signing_body())))
    packet = P1SignedApprovalPacket(
        approval_id=unsigned.approval_id,
        nonce=unsigned.nonce,
        issuer_key_id=unsigned.issuer_key_id,
        allowed_operations=unsigned.allowed_operations,
        physical_call_cap=unsigned.physical_call_cap,
        head_sha=unsigned.head_sha,
        tree_sha256=unsigned.tree_sha256,
        target=unsigned.target,
        expires_at=unsigned.expires_at,
        reason_code=unsigned.reason_code,
        signature=signature,
    )
    packet.validate(now=now)
    return packet


def signed_packet_from_dict(value: Mapping[str, object]) -> P1SignedApprovalPacket:
    expected = {
        "allowedOperations",
        "approvalId",
        "contractId",
        "expiresAt",
        "headSha",
        "issuerKeyId",
        "nonce",
        "physicalCallCap",
        "reasonCode",
        "signature",
        "target",
        "treeSha",
    }
    if set(value) != expected or value.get("contractId") != _SIGNED_CONTRACT_ID:
        raise VerificationPacketError("P1 signed approval fields are not closed")
    operations = value.get("allowedOperations")
    if not isinstance(operations, list) or not all(isinstance(item, str) for item in operations):
        raise VerificationPacketError("P1 signed approval operations are invalid")
    string_fields = (
        "approvalId",
        "contractId",
        "expiresAt",
        "headSha",
        "issuerKeyId",
        "nonce",
        "reasonCode",
        "signature",
        "treeSha",
    )
    if (
        any(not isinstance(value.get(field), str) for field in string_fields)
        or type(value.get("physicalCallCap")) is not int
    ):
        raise VerificationPacketError("P1 signed approval values are invalid")
    target_value = value.get("target")
    if not isinstance(target_value, dict) or set(target_value) != {
        "ecosFrom",
        "ecosTo",
        "sessionDate",
        "symbol",
    }:
        raise VerificationPacketError("P1 signed approval target is invalid")
    try:
        session_date = date.fromisoformat(cast(str, target_value["sessionDate"]))
        target = VerificationTarget(
            session_date=session_date, symbol=cast(str, target_value["symbol"])
        )
        if target.to_dict() != target_value:
            raise VerificationPacketError("P1 signed approval target is invalid")
        packet = P1SignedApprovalPacket(
            approval_id=cast(str, value["approvalId"]),
            nonce=cast(str, value["nonce"]),
            issuer_key_id=cast(str, value["issuerKeyId"]),
            allowed_operations=tuple(cast(list[str], operations)),
            physical_call_cap=cast(int, value["physicalCallCap"]),
            head_sha=cast(str, value["headSha"]),
            tree_sha256=cast(str, value["treeSha"]),
            target=target,
            expires_at=_datetime(cast(str, value["expiresAt"])),
            reason_code=cast(str, value["reasonCode"]),
            signature=cast(str, value["signature"]),
        )
    except (KeyError, TypeError, ValueError, VerificationPacketError) as error:
        raise VerificationPacketError("P1 signed approval values are invalid") from error
    packet.validate()
    return packet


def verify_signed_packet(
    packet: P1SignedApprovalPacket,
    public_key_path: Path,
    *,
    expected_issuer_key_id: str,
    expected_public_key_sha256: str,
    now: datetime | None = None,
) -> None:
    current = now or datetime.now(UTC)
    packet.validate(now=current)
    if packet.issuer_key_id != expected_issuer_key_id:
        raise VerificationPacketError("P1 approval issuer key id is not trusted")
    public_key_bytes = _read_public_key(public_key_path)
    if not secrets.compare_digest(
        hashlib.sha256(public_key_bytes).hexdigest(), expected_public_key_sha256
    ):
        raise VerificationPacketError("P1 approval public key does not match the pinned digest")
    key = _load_public_key(public_key_bytes)
    try:
        key.verify(_base64url_decode(packet.signature), canonical_json_bytes(packet.signing_body()))
    except InvalidSignature as error:
        raise VerificationPacketError("P1 approval signature is invalid") from error


GitIdentity = Callable[[Path], tuple[str, str]]


def author_provider_read_smoke_packet(
    *,
    repository_root: Path,
    approval_id: str,
    now: datetime,
    kis_token_physical_call_cap: int,
    git_identity: GitIdentity = current_clean_git_identity,
) -> P1VerificationPacket:
    """Create exact read-only authority only from a clean repository identity."""

    if now.tzinfo is None:
        raise VerificationPacketError("P1 verification authoring clock must be timezone aware")
    root = repository_root.resolve(strict=True)
    head_sha, tree_sha256 = git_identity(root)
    uv_lock = root / "workspaces/decision-platform/python-services/uv.lock"
    catalog = root / "contracts/catalogs/p1-verification-catalog.v1.json"
    packet = P1VerificationPacket(
        approval_id=approval_id,
        issued_at=now.astimezone(UTC),
        expires_at=now.astimezone(UTC) + timedelta(minutes=60),
        head_sha=head_sha,
        tree_sha256=tree_sha256,
        uv_lock_sha256=_regular_file_sha256(uv_lock),
        contract_catalog_sha256=_regular_file_sha256(catalog),
        target=VerificationTarget(session_date=latest_evidence_ready_session(now)),
        kis_token_physical_call_cap=kis_token_physical_call_cap,
    )
    packet.validate(now=now)
    return packet


def packet_from_dict(value: Mapping[str, object]) -> P1VerificationPacket:
    """Parse the closed packet form without accepting capability drift."""

    expected_keys = set(
        P1VerificationPacket(
            approval_id="P1.V1-PLACEHOLDER",
            issued_at=datetime(2026, 1, 1, tzinfo=UTC),
            expires_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
            head_sha="a" * 40,
            tree_sha256="b" * 64,
            uv_lock_sha256="c" * 64,
            contract_catalog_sha256="d" * 64,
            target=VerificationTarget(date(2026, 1, 1)),
            kis_token_physical_call_cap=0,
        ).to_dict()
    )
    if set(value) != expected_keys:
        raise VerificationPacketError("P1 verification packet fields are not closed")
    if value.get("contractId") != "p1-verification-packet.v1":
        raise VerificationPacketError("P1 verification packet contract id is invalid")
    if value.get("profile") != "PROVIDER_READ_SMOKE":
        raise VerificationPacketError("P1 verification packet profile is invalid")
    operations = value.get("operations")
    if not isinstance(operations, list) or tuple(operations) != LIVE_OPERATIONS:
        raise VerificationPacketError("P1 verification operation set drifted")
    if any(
        value.get(key) != expected
        for key, expected in (
            ("providerDataPhysicalCallCap", 6),
            ("retransmissionAllowed", False),
            ("accountCallCap", 0),
            ("balanceCallCap", 0),
            ("orderCallCap", 0),
            ("productDbWriteAllowed", False),
        )
    ):
        raise VerificationPacketError("P1 verification authority bounds drifted")
    target_value = value.get("target")
    if not isinstance(target_value, dict) or set(target_value) != {
        "ecosFrom",
        "ecosTo",
        "sessionDate",
        "symbol",
    }:
        raise VerificationPacketError("P1 verification target is invalid")
    try:
        session_date = date.fromisoformat(cast(str, target_value["sessionDate"]))
        packet = P1VerificationPacket(
            approval_id=cast(str, value["approvalId"]),
            issued_at=_datetime(cast(str, value["issuedAt"])),
            expires_at=_datetime(cast(str, value["expiresAt"])),
            head_sha=cast(str, value["headSha"]),
            tree_sha256=cast(str, value["treeSha256"]),
            uv_lock_sha256=cast(str, value["uvLockSha256"]),
            contract_catalog_sha256=cast(str, value["contractCatalogSha256"]),
            target=VerificationTarget(
                session_date=session_date,
                symbol=cast(str, target_value["symbol"]),
            ),
            kis_token_physical_call_cap=cast(int, value["kisTokenPhysicalCallCap"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise VerificationPacketError("P1 verification packet values are invalid") from error
    if target_value != packet.target.to_dict():
        raise VerificationPacketError("P1 verification ECOS window drifted")
    if value.get("totalPhysicalCallCap") != 6 + packet.kis_token_physical_call_cap:
        raise VerificationPacketError("P1 verification total cap drifted")
    packet.validate()
    return packet


def latest_evidence_ready_session(now: datetime) -> date:
    """Return latest XKRX D whose next-session 08:10 KST evidence clock is open."""

    if now.tzinfo is None:
        raise VerificationPacketError("P1 verification clock must be timezone aware")
    if version("exchange-calendars") != "4.13.2":
        raise VerificationPacketError("P1 verification XKRX calendar version drifted")
    calendar = xcals.get_calendar("XKRX")
    local_now = now.astimezone(_KST)
    candidate = calendar.date_to_session(pd.Timestamp(local_now.date()), direction="previous")
    while True:
        next_session = calendar.next_session(candidate)
        evidence_clock = datetime.combine(next_session.date(), time(8, 10), tzinfo=_KST)
        if evidence_clock <= local_now:
            return cast(date, candidate.date())
        candidate = calendar.previous_session(candidate)


def verify_repository_binding(packet: P1VerificationPacket, repository_root: Path) -> None:
    """Reject a packet before provider construction when repository bytes drift."""

    packet.validate(now=datetime.now(UTC))
    head_sha, tree_sha256 = current_clean_git_identity(repository_root.resolve(strict=True))
    if (head_sha, tree_sha256) != (packet.head_sha, packet.tree_sha256):
        raise VerificationPacketError("P1 verification Git binding drifted")
    if (
        _regular_file_sha256(
            repository_root / "workspaces/decision-platform/python-services/uv.lock"
        )
        != packet.uv_lock_sha256
    ):
        raise VerificationPacketError("P1 verification uv.lock binding drifted")
    if (
        _regular_file_sha256(repository_root / "contracts/catalogs/p1-verification-catalog.v1.json")
        != packet.contract_catalog_sha256
    ):
        raise VerificationPacketError("P1 verification catalog binding drifted")


def verify_signed_repository_binding(
    packet: P1SignedApprovalPacket,
    repository_root: Path,
    public_key_path: Path,
    *,
    expected_issuer_key_id: str,
    expected_public_key_sha256: str,
) -> None:
    """Verify signature and clean repository identity before any provider setup."""

    verify_signed_packet(
        packet,
        public_key_path,
        expected_issuer_key_id=expected_issuer_key_id,
        expected_public_key_sha256=expected_public_key_sha256,
    )
    head_sha, tree_sha256 = current_clean_git_identity(repository_root.resolve(strict=True))
    if (head_sha, tree_sha256) != (packet.head_sha, packet.tree_sha256):
        raise VerificationPacketError("P1 signed approval Git binding drifted")


def _regular_file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise VerificationPacketError("P1 verification binding file is unavailable")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_bounded_key(path: Path, *, owner_only: bool) -> bytes:
    absolute = path.absolute()
    try:
        info = absolute.lstat()
    except OSError as error:
        raise VerificationPacketError("P1 approval key is unavailable") from error
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or (info.st_uid != os.geteuid() if owner_only else info.st_uid not in {0, os.geteuid()})
        or (
            stat.S_IMODE(info.st_mode) != 0o600
            if owner_only
            else bool(stat.S_IMODE(info.st_mode) & 0o022)
        )
        or not 1 <= info.st_size <= _MAX_KEY_BYTES
    ):
        boundary = "owner-only" if owner_only else "read-only"
        raise VerificationPacketError(f"P1 approval key must be a bounded {boundary} regular file")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(absolute, flags)
    except OSError as error:
        raise VerificationPacketError("P1 approval key is unavailable") from error
    try:
        observed = os.fstat(descriptor)
        if (
            observed.st_dev != info.st_dev
            or observed.st_ino != info.st_ino
            or observed.st_uid != info.st_uid
            or observed.st_size != info.st_size
            or stat.S_IMODE(observed.st_mode) != stat.S_IMODE(info.st_mode)
        ):
            raise VerificationPacketError("P1 approval key changed during validation")
        content = os.read(descriptor, _MAX_KEY_BYTES + 1)
        if len(content) > _MAX_KEY_BYTES:
            raise VerificationPacketError("P1 approval key exceeds the size limit")
        return content
    finally:
        os.close(descriptor)


def _load_private_key(path: Path) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(
            _read_bounded_key(path, owner_only=True), password=None
        )
    except (TypeError, ValueError) as error:
        raise VerificationPacketError("P1 approval private key is invalid") from error
    if not isinstance(key, Ed25519PrivateKey):
        raise VerificationPacketError("P1 approval private key must be Ed25519")
    return key


def _read_public_key(path: Path) -> bytes:
    return _read_bounded_key(path, owner_only=False)


def _load_public_key(content: bytes) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(content)
    except (TypeError, ValueError) as error:
        raise VerificationPacketError("P1 approval public key is invalid") from error
    if not isinstance(key, Ed25519PublicKey):
        raise VerificationPacketError("P1 approval public key must be Ed25519")
    return key


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value + "==", altchars=b"-_", validate=True)
    except ValueError as error:
        raise VerificationPacketError("P1 approval signature encoding is invalid") from error
    if _base64url(decoded) != value:
        raise VerificationPacketError("P1 approval signature encoding is invalid")
    return decoded


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise VerificationPacketError("P1 verification timestamp is invalid")
    return parsed


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise VerificationPacketError("P1 verification timestamp is invalid")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
