from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.cross_market.core6_probe import (
    Core6ProbeBackendResult,
    Core6ProbeError,
    Core6ProbeExecutionBinding,
    Core6ProbeExecutor,
    Core6ProbePacket,
    Core6ProbeReceipt,
    core6_endpoint_set_identity_hash,
    core6_request_plan_digest,
)


def test_packet_accepts_only_fixed_core6_operation_plan() -> None:
    packet = _packet()

    assert packet.source_id == "S48_CORE6_SEC_EDGAR"
    assert packet.endpoint_set_identity_hash == core6_endpoint_set_identity_hash("SEC_EDGAR")
    assert packet.request_plan_digest == core6_request_plan_digest(
        operation="SEC_EDGAR_SUBMISSIONS",
        resource_id="CIK0000320193",
        date="NONE",
    )

    with pytest.raises(Core6ProbeError, match="CORE6_PROBE_REQUEST_PLAN_DIGEST_INVALID"):
        replace(packet, request_plan_digest="0" * 64)


def test_executor_preflights_before_claim_and_uses_one_content_free_receipt(tmp_path: Path) -> None:
    _secure_root(tmp_path)
    backend = _Backend()
    now = datetime(2026, 8, 9, 1, tzinfo=UTC)
    packet = _packet(now=now)

    receipt = Core6ProbeExecutor(
        control_root=tmp_path,
        backend=backend,
        now_provider=lambda: now,
    ).execute(
        packet=packet,
        binding=_binding(),
        now=now,
    )

    assert backend.events == ["preflight", "execute"]
    assert receipt.outcome == "SUCCESS"
    assert receipt.physical_call_count == 1
    assert receipt.projection_hash == "a" * 64
    claim_files = sorted(tmp_path.glob("consumed-*.json"))
    receipt_files = sorted(tmp_path.glob("receipt-*.json"))
    assert len(claim_files) == 1
    assert len(receipt_files) == 1
    stored = json.loads(receipt_files[0].read_text(encoding="utf-8"))
    assert stored["rawProviderDataStored"] is False
    assert stored["rawHeaderStored"] is False
    assert stored["rawQueryStored"] is False
    assert "resourceId" not in stored

    with pytest.raises(Core6ProbeError, match="CORE6_PROBE_PACKET_ALREADY_CONSUMED"):
        Core6ProbeExecutor(
            control_root=tmp_path,
            backend=_Backend(),
            now_provider=lambda: now,
        ).execute(
            packet=packet,
            binding=_binding(),
            now=now,
        )


def test_execution_binding_drift_does_not_preflight_or_consume_packet(tmp_path: Path) -> None:
    _secure_root(tmp_path)
    backend = _Backend()
    now = datetime(2026, 8, 9, 1, tzinfo=UTC)
    packet = _packet(now=now)
    drifted = Core6ProbeExecutionBinding(
        ci_digest="b" * 64,
        head_sha="c" * 40,
        security_digest="d" * 64,
        tree_sha256="e" * 64,
    )

    with pytest.raises(Core6ProbeError, match="CORE6_PROBE_EXECUTION_BINDING_DRIFT"):
        Core6ProbeExecutor(
            control_root=tmp_path,
            backend=backend,
            now_provider=lambda: now,
        ).execute(
            packet=packet,
            binding=drifted,
            now=now,
        )

    assert backend.events == []
    assert tuple(tmp_path.iterdir()) == ()


def test_failed_post_claim_attempt_seals_one_failed_receipt(tmp_path: Path) -> None:
    _secure_root(tmp_path)
    backend = _Backend(
        result=Core6ProbeBackendResult(
            outcome="FAILED",
            provider_status_class="HTTP_5XX",
            projection_hash=None,
            physical_call_count=1,
        )
    )
    now = datetime(2026, 8, 9, 1, tzinfo=UTC)

    receipt = Core6ProbeExecutor(
        control_root=tmp_path,
        backend=backend,
        now_provider=lambda: now,
    ).execute(
        packet=_packet(now=now),
        binding=_binding(),
        now=now,
    )

    assert receipt.outcome == "FAILED"
    assert receipt.provider_status_class == "HTTP_5XX"
    assert receipt.projection_hash is None
    assert receipt.physical_call_count == 1


def test_packet_loader_requires_canonical_private_regular_file(tmp_path: Path) -> None:
    _secure_root(tmp_path)
    packet = _packet()
    payload = json.dumps(
        packet.to_local_document(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    path = tmp_path / "packet.json"
    path.write_text(payload, encoding="utf-8")
    os.chmod(path, 0o600)

    loaded = Core6ProbePacket.load_from_control_root(
        control_root=tmp_path,
        relative_path="packet.json",
        now=datetime(2026, 8, 9, 1, tzinfo=UTC),
    )

    assert loaded.packet_sha256() == packet.packet_sha256()


def test_receipt_loader_accepts_only_closed_canonical_content_free_shape(tmp_path: Path) -> None:
    _secure_root(tmp_path)
    now = datetime(2026, 8, 9, 1, tzinfo=UTC)
    receipt = Core6ProbeExecutor(
        control_root=tmp_path,
        backend=_Backend(),
        now_provider=lambda: now,
    ).execute(
        packet=_packet(now=now),
        binding=_binding(),
        now=now,
    )
    receipt_file = next(tmp_path.glob("receipt-*.json"))

    loaded = type(receipt).load_from_control_root(
        control_root=tmp_path,
        relative_path=receipt_file.name,
    )

    assert loaded == receipt
    payload = json.loads(receipt_file.read_text(encoding="utf-8"))
    payload["rawProviderDataStored"] = True
    receipt_file.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    os.chmod(receipt_file, 0o600)

    with pytest.raises(Core6ProbeError, match="CORE6_PROBE_RECEIPT_SHAPE_INVALID"):
        type(receipt).load_from_control_root(
            control_root=tmp_path,
            relative_path=receipt_file.name,
        )


def test_executor_rechecks_packet_expiry_immediately_before_provider_handoff(
    tmp_path: Path,
) -> None:
    _secure_root(tmp_path)
    initial_now = datetime(2026, 8, 9, 1, tzinfo=UTC)
    backend = _ExpiryAdvancingBackend()

    receipt = Core6ProbeExecutor(
        control_root=tmp_path,
        backend=backend,
        now_provider=lambda: initial_now + timedelta(minutes=31),
    ).execute(
        packet=_packet(now=initial_now),
        binding=_binding(),
        now=initial_now,
    )

    assert backend.events == ["preflight"]
    assert receipt.outcome == "NOT_EXECUTED"
    assert receipt.physical_call_count == 0


def test_receipt_rejects_endpoint_identity_not_owned_by_its_provider() -> None:
    now = datetime(2026, 8, 9, 1, tzinfo=UTC)
    with pytest.raises(Core6ProbeError, match="CORE6_PROBE_RECEIPT_ENDPOINT_SET_DRIFT"):
        Core6ProbeReceipt(
            approval_id_hash="a" * 64,
            approval_packet_sha256="b" * 64,
            completed_at=now,
            endpoint_set_identity_hash=core6_endpoint_set_identity_hash("KIS"),
            logical_call_count=1,
            operation="SEC_EDGAR_SUBMISSIONS",
            outcome="SUCCESS",
            physical_call_count=1,
            projection_hash="c" * 64,
            provider_family="SEC_EDGAR",
            provider_status_class="HTTP_2XX",
            request_plan_digest="d" * 64,
            source_id="S48_CORE6_SEC_EDGAR",
            started_at=now,
        )


class _Backend:
    def __init__(self, *, result: Core6ProbeBackendResult | None = None) -> None:
        self.events: list[str] = []
        self._result = result or Core6ProbeBackendResult(
            outcome="SUCCESS",
            provider_status_class="HTTP_2XX",
            projection_hash="a" * 64,
            physical_call_count=1,
        )

    def preflight(self, *, packet: Core6ProbePacket) -> None:
        assert packet.operation == "SEC_EDGAR_SUBMISSIONS"
        self.events.append("preflight")

    def execute(self, *, packet: Core6ProbePacket) -> Core6ProbeBackendResult:
        assert packet.provider_family == "SEC_EDGAR"
        self.events.append("execute")
        return self._result


class _ExpiryAdvancingBackend(_Backend):
    def execute(self, *, packet: Core6ProbePacket) -> Core6ProbeBackendResult:
        raise AssertionError("expired packet must not reach the provider backend")


def _packet(*, now: datetime | None = None) -> Core6ProbePacket:
    now = now or datetime(2026, 8, 9, 1, tzinfo=UTC)
    return Core6ProbePacket(
        approval_id="c6p_0123456789abcdef0123456789abcdef",
        ci_digest="a" * 64,
        cost_cap_microusd=0,
        date="NONE",
        endpoint_set_identity_hash=core6_endpoint_set_identity_hash("SEC_EDGAR"),
        expires_at=now + timedelta(minutes=30),
        head_sha="b" * 40,
        logical_call_cap=1,
        nonce="core6-probe-nonce-0001",
        operation="SEC_EDGAR_SUBMISSIONS",
        operator="local-operator",
        physical_call_cap=1,
        provider_family="SEC_EDGAR",
        request_plan_digest=core6_request_plan_digest(
            operation="SEC_EDGAR_SUBMISSIONS",
            resource_id="CIK0000320193",
            date="NONE",
        ),
        resource_id="CIK0000320193",
        retry_count=0,
        security_digest="c" * 64,
        tracked_raw_artifact_count=0,
        tree_sha256="d" * 64,
    )


def _binding() -> Core6ProbeExecutionBinding:
    return Core6ProbeExecutionBinding(
        ci_digest="a" * 64,
        head_sha="b" * 40,
        security_digest="c" * 64,
        tree_sha256="d" * 64,
    )


def _secure_root(path: Path) -> None:
    os.chmod(path, 0o700)
