from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from app.data._shared.canonical_json import canonical_json_bytes
from app.verification.artifacts import VerificationArtifactError, publish_packet, read_packet
from app.verification.packet import (
    VerificationPacketError,
    author_provider_read_smoke_packet,
    latest_evidence_ready_session,
    packet_from_dict,
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
