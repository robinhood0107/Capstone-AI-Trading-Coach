from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from app.cross_market import core6_probe_cli
from app.cross_market.core6_probe import (
    Core6ProbeExecutionBinding,
    Core6ProbePacket,
    core6_endpoint_set_identity_hash,
    core6_request_plan_digest,
)


def test_execute_without_packet_fails_closed_before_git_evidence_or_provider_backend(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(core6_probe_cli, "_repository_root", lambda: tmp_path)

    assert core6_probe_cli.main(("execute",)) == 2

    assert json.loads(capsys.readouterr().out) == {
        "code": "CORE6_PROBE_PACKET_UNAVAILABLE",
        "providerPhysicalCalls": 0,
        "state": "FAILED",
    }


def test_cli_rejects_nonleaf_packet_selector_without_opening_control_file(capsys) -> None:
    assert core6_probe_cli.main(("execute", "--packet", "../approval.json")) == 2

    assert json.loads(capsys.readouterr().out) == {
        "code": "CORE6_PROBE_ARGUMENT_INVALID",
        "providerPhysicalCalls": 0,
        "state": "FAILED",
    }


def test_execute_constructs_only_fixed_backend_and_emits_content_free_receipt(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    packet = _packet()
    binding = _binding()
    sentinel_backend = object()
    captured: dict[str, object] = {}

    monkeypatch.setattr(core6_probe_cli, "_repository_root", lambda: tmp_path)
    monkeypatch.setattr(
        core6_probe_cli.Core6ProbePacket,
        "load_from_control_root",
        classmethod(lambda cls, **kwargs: packet),
    )
    monkeypatch.setattr(core6_probe_cli, "_load_execution_binding", lambda **kwargs: binding)
    approval = object()
    monkeypatch.setattr(
        core6_probe_cli,
        "load_and_verify_execution_approval",
        lambda *args, **kwargs: approval,
    )
    monkeypatch.setattr(
        core6_probe_cli,
        "claim_signed_provider_approval",
        lambda value: None if value is approval else (_ for _ in ()).throw(AssertionError()),
    )
    monkeypatch.setattr(
        core6_probe_cli,
        "build_core6_backend",
        lambda *, operation: captured.setdefault("operation", operation) and sentinel_backend,
    )

    class _Executor:
        def __init__(self, *, control_root: Path, backend: object) -> None:
            captured["controlRoot"] = control_root
            captured["backend"] = backend

        def execute(
            self,
            *,
            packet: Core6ProbePacket,
            binding: Core6ProbeExecutionBinding,
            now: datetime,
        ) -> SimpleNamespace:
            assert packet is not None
            assert binding is not None
            assert now.tzinfo is not None
            return SimpleNamespace(
                outcome="SUCCESS",
                physical_call_count=1,
                provider_family="SEC_EDGAR",
                provider_status_class="HTTP_2XX",
                source_id="S48_CORE6_SEC_EDGAR",
            )

    monkeypatch.setattr(core6_probe_cli, "Core6ProbeExecutor", _Executor)

    assert core6_probe_cli.main(("execute",)) == 0

    assert captured == {
        "backend": sentinel_backend,
        "controlRoot": tmp_path / "capstone-rag/secrets/core6-probes",
        "operation": "SEC_EDGAR_SUBMISSIONS",
    }
    payload = json.loads(capsys.readouterr().out)
    assert re.fullmatch(r"receipt-[0-9a-f]{64}\.json", payload.pop("receiptFile"))
    assert payload == {
        "code": "CORE6_PROBE_EXECUTED",
        "outcome": "SUCCESS",
        "providerFamily": "SEC_EDGAR",
        "providerPhysicalCalls": 1,
        "providerStatusClass": "HTTP_2XX",
        "sourceId": "S48_CORE6_SEC_EDGAR",
        "state": "COMPLETE",
    }


def test_git_identity_hashes_binary_tree_without_text_decoding(tmp_path: Path, monkeypatch) -> None:
    raw_tree = b"100644 README.md\x00\xff\x00\x81\x7f"

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[object]:
        if command[-3:] == ["cat-file", "tree", "HEAD^{tree}"]:
            assert kwargs.get("text", False) is False
            return subprocess.CompletedProcess(command, 0, stdout=raw_tree, stderr=b"")
        if "status" in command:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if "rev-parse" in command:
            return subprocess.CompletedProcess(command, 0, stdout="a" * 40 + "\n", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(core6_probe_cli.subprocess, "run", fake_run)

    head_sha, tree_sha256 = core6_probe_cli._current_clean_git_identity(tmp_path)

    assert head_sha == "a" * 40
    assert tree_sha256 == hashlib.sha256(raw_tree).hexdigest()


def _packet() -> Core6ProbePacket:
    now = datetime.now(UTC)
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
