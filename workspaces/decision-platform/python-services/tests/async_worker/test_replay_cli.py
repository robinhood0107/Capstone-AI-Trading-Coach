from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.async_worker.replay_cli import ReplayCliError, author_packet, validate_jwt, validate_packet


def _secret(path: Path, value: bytes) -> Path:
    path.write_bytes(value)
    path.chmod(0o600)
    return path


def _segment(value: dict[str, object]) -> str:
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _jwt(secret: bytes, now: datetime) -> str:
    header = _segment({"alg": "HS256", "typ": "JWT"})
    claims = _segment(
        {
            "iss": "test-issuer",
            "aud": ["test-audience"],
            "sub": "usr_demo_admin",
            "role": "ADMIN",
            "securityVersion": 1,
            "iat": int(now.timestamp()),
            "exp": int(now.timestamp()) + 300,
        }
    )
    signature = base64.urlsafe_b64encode(hmac.new(secret, f"{header}.{claims}".encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return f"{header}.{claims}.{signature}"


def test_packet_is_exactly_five_minutes_signed_bounded_and_execute_explicit(tmp_path: Path) -> None:
    now = datetime(2026, 8, 22, tzinfo=UTC)
    key = b"k" * 32
    args = argparse.Namespace(
        actor_user_id="usr_demo_admin",
        security_version=1,
        target_kind="EVENT",
        target_id=["evt_fixture_00000001"],
        expected_count=1,
        reason_code="OPERATOR_RECOVERY",
        authorize_execute=False,
        signing_key_file=_secret(tmp_path / "packet.key", key),
    )
    packet = author_packet(args, now=now)
    validate_packet(packet, key, execute=False, now=now)
    with pytest.raises(ReplayCliError, match="EXECUTE_NOT_AUTHORIZED"):
        validate_packet(packet, key, execute=True, now=now)
    packet["expectedCount"] = 2
    with pytest.raises(ReplayCliError, match="PACKET_SIGNATURE_INVALID"):
        validate_packet(packet, key, execute=False, now=now)


def test_jwt_is_hs256_admin_exact_issuer_audience_and_security_version() -> None:
    now = datetime(2026, 8, 22, tzinfo=UTC)
    secret = b"j" * 32
    claims = validate_jwt(_jwt(secret, now), secret, "test-issuer", "test-audience", now=now)
    assert claims["sub"] == "usr_demo_admin"
    with pytest.raises(ReplayCliError, match="JWT_INVALID"):
        validate_jwt(_jwt(secret, now), b"x" * 32, "test-issuer", "test-audience", now=now)


def test_secret_file_must_not_be_group_or_world_readable(tmp_path: Path) -> None:
    key_path = tmp_path / "packet.key"
    key_path.write_bytes(b"k" * 32)
    key_path.chmod(0o644)
    args = argparse.Namespace(
        actor_user_id="usr_demo_admin",
        security_version=1,
        target_kind="JOB",
        target_id=["job_fixture_00000001"],
        expected_count=1,
        reason_code="OPERATOR_RECOVERY",
        authorize_execute=False,
        signing_key_file=key_path,
    )
    with pytest.raises(ReplayCliError, match="SECRET_FILE_PERMISSIONS_INVALID"):
        author_packet(args)
