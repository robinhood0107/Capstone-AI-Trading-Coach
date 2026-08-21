"""Short-lived, repository-bound authority packet for the isolated provider smoke."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
import hashlib
from importlib.metadata import version
from pathlib import Path
import re
from typing import Callable, Final, Mapping, cast
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
from app.data._shared.canonical_json import canonical_json_sha256
from app.verification.git_identity import current_clean_git_identity
import pandas as pd


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

    expected_keys = set(P1VerificationPacket(
        approval_id="P1.V1-PLACEHOLDER",
        issued_at=datetime(2026, 1, 1, tzinfo=UTC),
        expires_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
        head_sha="a" * 40,
        tree_sha256="b" * 64,
        uv_lock_sha256="c" * 64,
        contract_catalog_sha256="d" * 64,
        target=VerificationTarget(date(2026, 1, 1)),
        kis_token_physical_call_cap=0,
    ).to_dict())
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
        "ecosFrom", "ecosTo", "sessionDate", "symbol"
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
        _regular_file_sha256(
            repository_root / "contracts/catalogs/p1-verification-catalog.v1.json"
        )
        != packet.contract_catalog_sha256
    ):
        raise VerificationPacketError("P1 verification catalog binding drifted")


def _regular_file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise VerificationPacketError("P1 verification binding file is unavailable")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise VerificationPacketError("P1 verification timestamp is invalid")
    return parsed


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise VerificationPacketError("P1 verification timestamp is invalid")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
