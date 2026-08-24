from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.data._shared.canonical_json import canonical_json_bytes
from app.verification import packet as packet_module
from app.verification.artifacts import VerificationArtifactError, publish_packet, read_packet
from app.verification.packet import (
    VerificationPacketError,
    VerificationTarget,
    author_provider_read_smoke_packet,
    author_signed_provider_read_smoke_packet,
    latest_evidence_ready_session,
    packet_from_dict,
    signed_packet_from_dict,
    verify_signed_packet,
)

_KST = ZoneInfo("Asia/Seoul")


def test_latest_evidence_ready_session_uses_next_xkrx_session_clock() -> None:
    assert latest_evidence_ready_session(datetime(2026, 8, 18, 8, 9, tzinfo=_KST)).isoformat() == (
        "2026-08-13"
    )
    assert latest_evidence_ready_session(datetime(2026, 8, 18, 8, 10, tzinfo=_KST)).isoformat() == (
        "2026-08-14"
    )


def test_author_packet_is_exact_cap_bound_and_round_trips(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    (root / "workspaces/decision-platform/python-services").mkdir(parents=True)
    (root / "contracts/catalogs").mkdir(parents=True)
    (root / "workspaces/decision-platform/python-services/uv.lock").write_bytes(b"lock")
    (root / "contracts/catalogs/p1-verification-catalog.v1.json").write_bytes(b"catalog")
    now = datetime(2026, 8, 18, 8, 10, tzinfo=_KST)
    packet = author_provider_read_smoke_packet(
        repository_root=root,
        approval_id="P1.V1-20260821-READ-SMOKE",
        now=now,
        kis_token_physical_call_cap=1,
        git_identity=lambda _: ("a" * 40, "b" * 64),
    )

    payload = packet.to_dict()
    assert payload["providerDataPhysicalCallCap"] == 6
    assert payload["totalPhysicalCallCap"] == 7
    assert payload["retransmissionAllowed"] is False
    assert payload["productDbWriteAllowed"] is False
    assert payload["target"] == {
        "ecosFrom": "2026-07-16",
        "ecosTo": "2026-08-14",
        "sessionDate": "2026-08-14",
        "symbol": "005930",
    }
    artifact_root = tmp_path / "artifacts"
    artifact = publish_packet(artifact_root, packet)
    assert read_packet(artifact) == packet
    assert artifact.stat().st_mode & 0o777 == 0o600
    assert artifact_root.stat().st_mode & 0o777 == 0o700


def test_packet_rejects_operation_and_ecos_window_drift(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    (root / "workspaces/decision-platform/python-services").mkdir(parents=True)
    (root / "contracts/catalogs").mkdir(parents=True)
    (root / "workspaces/decision-platform/python-services/uv.lock").write_bytes(b"lock")
    (root / "contracts/catalogs/p1-verification-catalog.v1.json").write_bytes(b"catalog")
    packet = author_provider_read_smoke_packet(
        repository_root=root,
        approval_id="P1.V1-20260821-READ-SMOKE",
        now=datetime(2026, 8, 18, 0, tzinfo=UTC),
        kis_token_physical_call_cap=0,
        git_identity=lambda _: ("a" * 40, "b" * 64),
    )
    operation_drift = packet.to_dict()
    operation_drift["operations"] = list(reversed(operation_drift["operations"]))
    with pytest.raises(VerificationPacketError, match="operation set"):
        packet_from_dict(operation_drift)
    window_drift = packet.to_dict()
    target = dict(window_drift["target"])
    target["ecosFrom"] = target["sessionDate"]
    window_drift["target"] = target
    with pytest.raises(VerificationPacketError, match="ECOS window"):
        packet_from_dict(window_drift)

    assert canonical_json_bytes(packet.to_dict()).endswith(b"\n")


def test_packet_reader_rejects_symlinks_and_excessive_json_depth(tmp_path: Path) -> None:
    target = tmp_path / "packet.json"
    target.write_bytes(b'{"nested":' + b"[" * 20 + b"0" + b"]" * 20 + b"}\n")
    target.chmod(0o600)
    link = tmp_path / "packet-link.json"
    link.symlink_to(target)

    with pytest.raises(VerificationArtifactError, match="unavailable"):
        read_packet(link.absolute())
    with pytest.raises(VerificationArtifactError, match="invalid JSON"):
        read_packet(target.absolute())


def test_signed_v2_packet_round_trip_tamper_and_key_boundary(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    private_key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "issuer-private.pem"
    public_path = tmp_path / "issuer-public.pem"
    private_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    private_path.chmod(0o600)
    public_path.chmod(0o644)
    public_digest = hashlib.sha256(public_path.read_bytes()).hexdigest()
    now = datetime(2026, 8, 21, 0, tzinfo=UTC)
    packet = author_signed_provider_read_smoke_packet(
        repository_root=root,
        approval_id="P1.V2-20260821-READ-SMOKE",
        private_key_path=private_path,
        issuer_key_id="P1.TEST",
        reason_code="P1_READ_SMOKE",
        now=now,
        kis_token_physical_call_cap=1,
        git_identity=lambda _: ("a" * 40, "b" * 64),
    )

    assert packet.physical_call_cap == 7
    assert packet.target.to_dict() == {
        "ecosFrom": "2026-07-22",
        "ecosTo": "2026-08-20",
        "sessionDate": "2026-08-20",
        "symbol": "005930",
    }
    assert signed_packet_from_dict(packet.to_dict()) == packet
    wrong_type = packet.to_dict()
    wrong_type["approvalId"] = 42
    with pytest.raises(VerificationPacketError, match="values"):
        signed_packet_from_dict(wrong_type)
    verify_signed_packet(
        packet,
        public_path,
        expected_issuer_key_id="P1.TEST",
        expected_public_key_sha256=public_digest,
        now=now + timedelta(minutes=1),
    )

    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    noncanonical = packet.to_dict()
    signature = packet.signature
    noncanonical["signature"] = signature[:-1] + alphabet[alphabet.index(signature[-1]) ^ 1]
    with pytest.raises(VerificationPacketError, match="signature encoding"):
        verify_signed_packet(
            signed_packet_from_dict(noncanonical),
            public_path,
            expected_issuer_key_id="P1.TEST",
            expected_public_key_sha256=public_digest,
            now=now + timedelta(minutes=1),
        )

    tampered = packet.to_dict()
    tampered["physicalCallCap"] = 6
    with pytest.raises(VerificationPacketError, match="signature"):
        verify_signed_packet(
            signed_packet_from_dict(tampered),
            public_path,
            expected_issuer_key_id="P1.TEST",
            expected_public_key_sha256=public_digest,
            now=now + timedelta(minutes=1),
        )

    tampered_target = packet.to_dict()
    tampered_target["target"] = VerificationTarget(
        packet.target.session_date - timedelta(days=1)
    ).to_dict()
    with pytest.raises(VerificationPacketError, match="signature"):
        verify_signed_packet(
            signed_packet_from_dict(tampered_target),
            public_path,
            expected_issuer_key_id="P1.TEST",
            expected_public_key_sha256=public_digest,
            now=now + timedelta(minutes=1),
        )

    with pytest.raises(VerificationPacketError, match="issuer key id"):
        verify_signed_packet(
            packet,
            public_path,
            expected_issuer_key_id="P1.OTHER",
            expected_public_key_sha256=public_digest,
            now=now + timedelta(minutes=1),
        )
    with pytest.raises(VerificationPacketError, match="pinned digest"):
        verify_signed_packet(
            packet,
            public_path,
            expected_issuer_key_id="P1.TEST",
            expected_public_key_sha256="0" * 64,
            now=now + timedelta(minutes=1),
        )

    private_path.chmod(0o640)
    with pytest.raises(VerificationPacketError, match="owner-only"):
        author_signed_provider_read_smoke_packet(
            repository_root=root,
            approval_id="P1.V2-20260821-READ-SMOKE",
            private_key_path=private_path,
            issuer_key_id="P1.TEST",
            reason_code="P1_READ_SMOKE",
            now=now,
            kis_token_physical_call_cap=0,
            git_identity=lambda _: ("a" * 40, "b" * 64),
        )

    private_path.chmod(0o600)
    repository_private_path = root / "ignored-private.key"
    repository_private_path.write_bytes(private_path.read_bytes())
    repository_private_path.chmod(0o600)
    with pytest.raises(VerificationPacketError, match="outside the repository"):
        author_signed_provider_read_smoke_packet(
            repository_root=root,
            approval_id="P1.V2-20260821-READ-SMOKE",
            private_key_path=repository_private_path,
            issuer_key_id="P1.TEST",
            reason_code="P1_READ_SMOKE",
            now=now,
            kis_token_physical_call_cap=0,
            git_identity=lambda _: ("a" * 40, "b" * 64),
        )


def test_root_owned_public_key_is_readable_by_non_root_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public_path = tmp_path / "issuer-public.pem"
    public_path.write_bytes(b"root-owned-public-key")
    public_path.chmod(0o644)
    actual = public_path.stat()
    protected = SimpleNamespace(
        st_dev=actual.st_dev,
        st_ino=actual.st_ino,
        st_mode=actual.st_mode,
        st_size=actual.st_size,
        st_uid=0,
    )
    original_lstat = Path.lstat
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda path: (
            protected if path.absolute() == public_path.absolute() else original_lstat(path)
        ),
    )
    monkeypatch.setattr(packet_module.os, "fstat", lambda _: protected)
    monkeypatch.setattr(packet_module.os, "geteuid", lambda: 1000)
    assert packet_module._read_public_key(public_path) == b"root-owned-public-key"


def test_signed_v2_packet_rejects_symlink_and_expiry(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    private_key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "issuer-private.pem"
    private_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private_path.chmod(0o600)
    link = tmp_path / "issuer-link.pem"
    link.symlink_to(private_path)
    now = datetime(2026, 8, 21, 0, tzinfo=UTC)

    with pytest.raises(VerificationPacketError, match="owner-only"):
        author_signed_provider_read_smoke_packet(
            repository_root=root,
            approval_id="P1.V2-20260821-READ-SMOKE",
            private_key_path=link,
            issuer_key_id="P1.TEST",
            reason_code="P1_READ_SMOKE",
            now=now,
            kis_token_physical_call_cap=0,
            git_identity=lambda _: ("a" * 40, "b" * 64),
        )

    packet = author_signed_provider_read_smoke_packet(
        repository_root=root,
        approval_id="P1.V2-20260821-READ-SMOKE",
        private_key_path=private_path,
        issuer_key_id="P1.TEST",
        reason_code="P1_READ_SMOKE",
        now=now,
        kis_token_physical_call_cap=0,
        git_identity=lambda _: ("a" * 40, "b" * 64),
    )
    public_path = tmp_path / "issuer-public.pem"
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    public_path.chmod(0o644)
    with pytest.raises(VerificationPacketError, match="expired"):
        verify_signed_packet(
            packet,
            public_path,
            expected_issuer_key_id="P1.TEST",
            expected_public_key_sha256=hashlib.sha256(public_path.read_bytes()).hexdigest(),
            now=now + timedelta(minutes=5),
        )
