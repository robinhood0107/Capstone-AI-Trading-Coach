from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.async_worker import replay_cli
from app.async_worker.replay_cli import ReplayCliError, author_packet, validate_packet


def _secret(path: Path, value: bytes) -> Path:
    path.write_bytes(value)
    path.chmod(0o600)
    return path


def test_packet_is_exactly_five_minutes_signed_bounded_and_execute_explicit(tmp_path: Path) -> None:
    now = datetime(2026, 8, 22, tzinfo=UTC)
    private_key = Ed25519PrivateKey.generate()
    private_path = _secret(
        tmp_path / "packet-private.pem",
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    args = argparse.Namespace(
        actor_user_id="usr_demo_admin",
        security_version=1,
        target_kind="EVENT",
        target_id=["evt_fixture_00000001"],
        expected_count=1,
        reason_code="OPERATOR_RECOVERY",
        authorize_execute=False,
        private_key_file=private_path,
    )
    packet = author_packet(args, now=now)
    public_key = private_key.public_key()
    validate_packet(packet, public_key, execute=False, now=now)
    with pytest.raises(ReplayCliError, match="EXECUTE_NOT_AUTHORIZED"):
        validate_packet(packet, public_key, execute=True, now=now)
    packet["expectedCount"] = 2
    with pytest.raises(ReplayCliError, match="PACKET_SIGNATURE_INVALID"):
        validate_packet(packet, public_key, execute=False, now=now)


def test_secret_file_must_not_be_group_or_world_readable(tmp_path: Path) -> None:
    key_path = tmp_path / "packet-private.pem"
    key_path.write_bytes(
        Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o644)
    args = argparse.Namespace(
        actor_user_id="usr_demo_admin",
        security_version=1,
        target_kind="JOB",
        target_id=["job_fixture_00000001"],
        expected_count=1,
        reason_code="OPERATOR_RECOVERY",
        authorize_execute=False,
        private_key_file=key_path,
    )
    with pytest.raises(ReplayCliError, match="SECRET_FILE_PERMISSIONS_INVALID"):
        author_packet(args)


def test_private_reader_is_bounded_nofollow_and_tolerates_short_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = _secret(tmp_path / "packet.json", b"{" + b"x" * 127 + b"}")
    original_read = replay_cli.os.read
    monkeypatch.setattr(
        replay_cli.os, "read", lambda descriptor, size: original_read(descriptor, min(size, 7))
    )
    assert replay_cli._read_private_file(packet, maximum=256, code="PACKET") == packet.read_bytes()

    oversized = _secret(tmp_path / "oversized.json", b"x" * 257)
    with pytest.raises(ReplayCliError, match="PACKET_TOO_LARGE"):
        replay_cli._read_private_file(oversized, maximum=256, code="PACKET")

    link = tmp_path / "packet-link.json"
    link.symlink_to(packet)
    with pytest.raises(OSError):
        replay_cli._read_private_file(link, maximum=256, code="PACKET")
