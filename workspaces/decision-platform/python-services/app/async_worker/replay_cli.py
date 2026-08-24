from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

import psycopg
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from psycopg.conninfo import conninfo_to_dict

from app.data._shared.bounded_json import (
    BoundedJsonError,
    BoundedJsonLimits,
    parse_bounded_json_bytes,
)

_ID = re.compile(r"^(?:evt|job)_[A-Za-z0-9_-]{8,96}$")
_REASON = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_USER = re.compile(r"^usr_[A-Za-z0-9_-]{8,64}$")
_MAX_TARGETS = 100
_MAX_PACKET_BYTES = 1_048_576
_MAX_SECRET_BYTES = 4_096
_SIGNATURE = re.compile(r"^[A-Za-z0-9_-]{86}$")
_PACKET_JSON_LIMITS = BoundedJsonLimits(
    max_bytes=_MAX_PACKET_BYTES,
    max_depth=8,
    max_list_items=_MAX_TARGETS,
    max_object_keys=16,
    max_text_codepoints=2_048,
    max_text_bytes=2_048,
    max_number_characters=32,
)


class ReplayCliError(RuntimeError):
    pass


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _read_private_file(path: Path, *, maximum: int, code: str) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
            raise ReplayCliError(f"{code}_PERMISSIONS_INVALID")
        if info.st_size > maximum:
            raise ReplayCliError(f"{code}_TOO_LARGE")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        if len(value) > maximum:
            raise ReplayCliError(f"{code}_TOO_LARGE")
        return value
    finally:
        os.close(descriptor)


def _read_secret(path: Path, *, minimum: int = 32) -> bytes:
    value = _read_private_file(path, maximum=_MAX_SECRET_BYTES, code="SECRET_FILE").rstrip(b"\r\n")
    if len(value) < minimum:
        raise ReplayCliError("SECRET_FILE_TOO_SHORT")
    return value


def _read_public_file(path: Path, *, maximum: int = _MAX_SECRET_BYTES) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o022
            or info.st_uid not in {0, os.geteuid()}
            or info.st_size > maximum
        ):
            raise ReplayCliError("PUBLIC_KEY_FILE_PERMISSIONS_INVALID")
        value = os.read(descriptor, maximum + 1)
        if len(value) > maximum or os.read(descriptor, 1):
            raise ReplayCliError("PUBLIC_KEY_FILE_TOO_LARGE")
        return value
    finally:
        os.close(descriptor)


def _load_private_key(path: Path) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(_read_secret(path), password=None)
    except (TypeError, ValueError) as error:
        raise ReplayCliError("PRIVATE_KEY_INVALID") from error
    if not isinstance(key, Ed25519PrivateKey):
        raise ReplayCliError("PRIVATE_KEY_INVALID")
    return key


def _load_public_key(path: Path) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(_read_public_file(path))
    except (TypeError, ValueError) as error:
        raise ReplayCliError("PUBLIC_KEY_INVALID") from error
    if not isinstance(key, Ed25519PublicKey):
        raise ReplayCliError("PUBLIC_KEY_INVALID")
    return key


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    if _SIGNATURE.fullmatch(value) is None:
        raise ReplayCliError("PACKET_SIGNATURE_INVALID")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError as error:
        raise ReplayCliError("PACKET_SIGNATURE_INVALID") from error
    if _b64url(decoded) != value:
        raise ReplayCliError("PACKET_SIGNATURE_INVALID")
    return decoded


def author_packet(args: argparse.Namespace, *, now: datetime | None = None) -> dict[str, Any]:
    issued_at = (now or datetime.now(UTC)).replace(microsecond=0)
    target_ids = list(args.target_id)
    if not 1 <= len(target_ids) <= _MAX_TARGETS or len(target_ids) != len(set(target_ids)):
        raise ReplayCliError("TARGET_COUNT_INVALID")
    prefix = "evt_" if args.target_kind == "EVENT" else "job_"
    if any(not _ID.fullmatch(item) or not item.startswith(prefix) for item in target_ids):
        raise ReplayCliError("TARGET_ID_INVALID")
    if not _USER.fullmatch(args.actor_user_id) or not _REASON.fullmatch(args.reason_code):
        raise ReplayCliError("PACKET_FIELD_INVALID")
    if args.security_version <= 0 or not 1 <= args.expected_count <= _MAX_TARGETS:
        raise ReplayCliError("PACKET_FIELD_INVALID")
    unsigned: dict[str, Any] = {
        "version": "s7-async-replay-packet.v1",
        "replayBatchId": f"replay_{uuid.uuid4().hex}",
        "issuedAt": issued_at.isoformat().replace("+00:00", "Z"),
        "expiresAt": (issued_at + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "actorUserId": args.actor_user_id,
        "securityVersion": args.security_version,
        "targetKind": args.target_kind,
        "targetIds": target_ids,
        "expectedCount": args.expected_count,
        "reasonCode": args.reason_code,
        "executeAuthorized": bool(args.authorize_execute),
    }
    signature = _load_private_key(args.private_key_file).sign(_canonical(unsigned))
    return unsigned | {"signature": _b64url(signature)}


def validate_packet(
    packet: dict[str, Any],
    public_key: Ed25519PublicKey,
    *,
    execute: bool,
    now: datetime | None = None,
) -> None:
    signature = packet.get("signature")
    unsigned = {name: value for name, value in packet.items() if name != "signature"}
    if not isinstance(signature, str):
        raise ReplayCliError("PACKET_SIGNATURE_INVALID")
    try:
        public_key.verify(_b64url_decode(signature), _canonical(unsigned))
    except InvalidSignature as error:
        raise ReplayCliError("PACKET_SIGNATURE_INVALID") from error
    required = {
        "version",
        "replayBatchId",
        "issuedAt",
        "expiresAt",
        "actorUserId",
        "securityVersion",
        "targetKind",
        "targetIds",
        "expectedCount",
        "reasonCode",
        "executeAuthorized",
    }
    if set(unsigned) != required or unsigned["version"] != "s7-async-replay-packet.v1":
        raise ReplayCliError("PACKET_SCHEMA_INVALID")
    current = now or datetime.now(UTC)
    issued = _instant(unsigned["issuedAt"])
    expires = _instant(unsigned["expiresAt"])
    if (
        issued > current + timedelta(seconds=30)
        or expires <= current
        or expires - issued != timedelta(minutes=5)
    ):
        raise ReplayCliError("PACKET_EXPIRED")
    targets = unsigned["targetIds"]
    prefix = "evt_" if unsigned["targetKind"] == "EVENT" else "job_"
    if (
        not isinstance(targets, list)
        or not 1 <= len(targets) <= _MAX_TARGETS
        or len(targets) != len(set(targets))
        or any(
            not isinstance(item, str) or not _ID.fullmatch(item) or not item.startswith(prefix)
            for item in targets
        )
    ):
        raise ReplayCliError("TARGET_ID_INVALID")
    if execute and unsigned["executeAuthorized"] is not True:
        raise ReplayCliError("EXECUTE_NOT_AUTHORIZED")


def _instant(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ReplayCliError("PACKET_TIME_INVALID")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ReplayCliError("PACKET_TIME_INVALID") from error


def _load_and_validate_packet(args: argparse.Namespace) -> dict[str, Any]:
    try:
        packet = parse_bounded_json_bytes(
            _read_private_file(args.packet, maximum=_MAX_PACKET_BYTES, code="PACKET"),
            limits=_PACKET_JSON_LIMITS,
        )
    except BoundedJsonError as error:
        raise ReplayCliError("PACKET_SCHEMA_INVALID") from error
    if not isinstance(packet, dict):
        raise ReplayCliError("PACKET_SCHEMA_INVALID")
    validate_packet(packet, _load_public_key(args.public_key_file), execute=args.execute)
    return packet


def authorize_packet(args: argparse.Namespace) -> dict[str, Any]:
    packet = _load_and_validate_packet(args)
    authorizer_dsn = _read_secret(args.authorizer_dsn_file, minimum=16).decode("utf-8")
    try:
        if conninfo_to_dict(authorizer_dsn).get("user") != "decision_replay_authorizer":
            raise ReplayCliError("REPLAY_AUTHORIZER_DATABASE_ROLE_INVALID")
    except psycopg.Error as error:
        raise ReplayCliError("REPLAY_DATABASE_DSN_INVALID") from error
    packet_hash = "sha256:" + hashlib.sha256(_canonical(packet)).hexdigest()
    with psycopg.connect(authorizer_dsn, autocommit=False, connect_timeout=2) as connection:
        with connection.cursor() as cursor:
            cursor.execute("select current_user, session_user")
            if cursor.fetchone() != ("decision_replay_authorizer", "decision_replay_authorizer"):
                raise ReplayCliError("REPLAY_AUTHORIZER_DATABASE_ROLE_INVALID")
            cursor.execute(
                "select authorize_async_replay(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    packet_hash,
                    packet["actorUserId"],
                    packet["securityVersion"],
                    packet["replayBatchId"],
                    packet["targetKind"],
                    packet["targetIds"],
                    packet["expectedCount"],
                    packet["reasonCode"],
                    packet["executeAuthorized"],
                    _instant(packet["issuedAt"]),
                    _instant(packet["expiresAt"]),
                ),
            )
            row = cursor.fetchone()
            if row is None or row[0] is not True:
                raise ReplayCliError("REPLAY_AUTHORIZATION_CONFLICT")
        connection.commit()
    return packet


def execute_packet(args: argparse.Namespace) -> list[dict[str, Any]]:
    packet = _load_and_validate_packet(args)
    dsn = _read_secret(args.database_dsn_file, minimum=16).decode("utf-8")
    try:
        if conninfo_to_dict(dsn).get("user") != "decision_replay":
            raise ReplayCliError("REPLAY_DATABASE_ROLE_INVALID")
    except psycopg.Error as error:
        raise ReplayCliError("REPLAY_DATABASE_DSN_INVALID") from error
    packet_hash = "sha256:" + hashlib.sha256(_canonical(packet)).hexdigest()
    with psycopg.connect(dsn, autocommit=False, connect_timeout=2) as connection:
        with connection.cursor() as cursor:
            cursor.execute("select current_user, session_user")
            if cursor.fetchone() != ("decision_replay", "decision_replay"):
                raise ReplayCliError("REPLAY_DATABASE_ROLE_INVALID")
            cursor.execute(
                "select * from replay_async_work(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    packet["actorUserId"],
                    packet["securityVersion"],
                    packet["replayBatchId"],
                    packet["targetKind"],
                    packet["targetIds"],
                    packet["expectedCount"],
                    packet["reasonCode"],
                    packet_hash,
                    args.execute,
                ),
            )
            columns = [item.name for item in cursor.description or ()]
            rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        if not rows:
            raise ReplayCliError("CURRENT_ADMIN_REVALIDATION_FAILED")
        connection.commit()
    return rows


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.async_worker.replay_cli")
    commands = parser.add_subparsers(dest="command", required=True)
    author = commands.add_parser("author")
    author.add_argument("--actor-user-id", required=True)
    author.add_argument("--security-version", required=True, type=int)
    author.add_argument("--target-kind", required=True, choices=("EVENT", "JOB"))
    author.add_argument("--target-id", required=True, action="append")
    author.add_argument("--expected-count", required=True, type=int)
    author.add_argument("--reason-code", required=True)
    author.add_argument("--authorize-execute", action="store_true")
    author.add_argument("--private-key-file", required=True, type=Path)
    author.add_argument("--output", required=True, type=Path)
    authorize = commands.add_parser("authorize")
    authorize.add_argument("--packet", required=True, type=Path)
    authorize.add_argument("--public-key-file", required=True, type=Path)
    authorize.add_argument("--authorizer-dsn-file", required=True, type=Path)
    authorize.add_argument("--execute", action="store_true")
    run = commands.add_parser("run")
    run.add_argument("--packet", required=True, type=Path)
    run.add_argument("--public-key-file", required=True, type=Path)
    run.add_argument("--database-dsn-file", required=True, type=Path)
    run.add_argument("--execute", action="store_true")
    return parser


def _write_new(path: Path, payload: dict[str, Any]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(payload, output, sort_keys=True, separators=(",", ":"))
        output.write("\n")


def _fail(code: str) -> NoReturn:
    print(json.dumps({"success": False, "code": code}, separators=(",", ":")))
    raise SystemExit(2)


def main() -> None:
    args = _parser().parse_args()
    try:
        if args.command == "author":
            packet = author_packet(args)
            _write_new(args.output, packet)
            print(json.dumps({"success": True, "packet": str(args.output)}, separators=(",", ":")))
        elif args.command == "authorize":
            packet = authorize_packet(args)
            print(
                json.dumps(
                    {"success": True, "authorized": True, "replayBatchId": packet["replayBatchId"]},
                    separators=(",", ":"),
                )
            )
        else:
            rows = execute_packet(args)
            print(
                json.dumps(
                    {"success": True, "executed": bool(args.execute), "items": rows},
                    separators=(",", ":"),
                )
            )
    except (OSError, UnicodeError, json.JSONDecodeError, psycopg.Error, ReplayCliError) as error:
        _fail(str(error) if isinstance(error, ReplayCliError) else "REPLAY_OPERATION_FAILED")


if __name__ == "__main__":
    main()
