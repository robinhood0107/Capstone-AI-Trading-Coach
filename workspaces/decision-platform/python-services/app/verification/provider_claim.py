"""Durable one-shot consumption for signed P1 provider authority."""

from __future__ import annotations

import hashlib
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol

import psycopg
from psycopg.conninfo import conninfo_to_dict

from app.data._shared.canonical_json import canonical_json_bytes

_CLAIM_DSN_FILE: Final = Path("/run/secrets/p1-provider-claim-dsn")
_MAX_DSN_BYTES: Final = 4_096


class ProviderApprovalClaimError(RuntimeError):
    """The signed provider authority could not be consumed durably."""


class ClaimableApproval(Protocol):
    @property
    def approval_id(self) -> str: ...

    @property
    def nonce(self) -> str: ...

    @property
    def physical_call_cap(self) -> int: ...

    @property
    def expires_at(self) -> datetime: ...

    @property
    def allowed_operations(self) -> tuple[str, ...]: ...

    def to_dict(self) -> dict[str, object]: ...


def claim_signed_provider_approval(
    packet: ClaimableApproval,
    *,
    dsn_file: Path = _CLAIM_DSN_FILE,
) -> None:
    """Consume the signed packet once in PostgreSQL before provider construction."""

    dsn = _read_owner_private_file(dsn_file).decode("utf-8")
    try:
        parsed = conninfo_to_dict(dsn)
    except psycopg.Error as error:
        raise ProviderApprovalClaimError("P1_PROVIDER_CLAIM_DSN_INVALID") from error
    database_name = parsed.get("dbname")
    if parsed.get("user") != "decision_replay" or not database_name:
        raise ProviderApprovalClaimError("P1_PROVIDER_CLAIM_DSN_INVALID")
    operations_digest = _sha256("\n".join(packet.allowed_operations).encode("ascii"))
    with psycopg.connect(dsn, autocommit=False, connect_timeout=2) as connection:
        with connection.cursor() as cursor:
            cursor.execute("select current_user,session_user,current_database()")
            if cursor.fetchone() != ("decision_replay", "decision_replay", database_name):
                raise ProviderApprovalClaimError("P1_PROVIDER_CLAIM_ROLE_INVALID")
            cursor.execute(
                "select consume_p1_provider_approval(%s,%s,%s,%s,%s,%s)",
                (
                    "sha256:" + _sha256(canonical_json_bytes(packet.to_dict())),
                    "sha256:" + _sha256(packet.approval_id.encode("utf-8")),
                    "sha256:" + _sha256(packet.nonce.encode("ascii")),
                    "sha256:" + operations_digest,
                    packet.physical_call_cap,
                    packet.expires_at,
                ),
            )
            row = cursor.fetchone()
            if row != (True,):
                raise ProviderApprovalClaimError("P1_PROVIDER_APPROVAL_ALREADY_CONSUMED")
        connection.commit()


def _read_owner_private_file(path: Path) -> bytes:
    if path != _CLAIM_DSN_FILE or not path.is_absolute():
        raise ProviderApprovalClaimError("P1_PROVIDER_CLAIM_DSN_INVALID")
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ProviderApprovalClaimError("P1_PROVIDER_CLAIM_DSN_UNAVAILABLE") from error
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size not in range(16, _MAX_DSN_BYTES + 1)
        ):
            raise ProviderApprovalClaimError("P1_PROVIDER_CLAIM_DSN_INVALID")
        value = os.read(descriptor, _MAX_DSN_BYTES + 1)
        if len(value) > _MAX_DSN_BYTES or os.read(descriptor, 1):
            raise ProviderApprovalClaimError("P1_PROVIDER_CLAIM_DSN_INVALID")
        return value.rstrip(b"\r\n")
    finally:
        os.close(descriptor)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
