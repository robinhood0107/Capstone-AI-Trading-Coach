from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from app.cross_market import optional3_probe_cli


def test_execute_without_packet_fails_closed_before_git_evidence_or_provider_transport(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(optional3_probe_cli, "_repository_root", lambda: tmp_path)

    assert optional3_probe_cli.main(("execute",)) == 2

    assert json.loads(capsys.readouterr().out) == {
        "code": "OPTIONAL3_PROBE_PACKET_UNAVAILABLE",
        "providerPhysicalCalls": 0,
        "state": "FAILED",
    }


def test_cli_rejects_nonleaf_packet_selector_without_opening_a_control_file(capsys) -> None:
    assert optional3_probe_cli.main(("execute", "--packet", "../approval.json")) == 2

    assert json.loads(capsys.readouterr().out) == {
        "code": "OPTIONAL3_PROBE_ARGUMENT_INVALID",
        "providerPhysicalCalls": 0,
        "state": "FAILED",
    }


def test_cli_rejects_unknown_command_without_provider_transport(capsys) -> None:
    assert optional3_probe_cli.main(("anything-else",)) == 2

    assert json.loads(capsys.readouterr().out) == {
        "code": "OPTIONAL3_PROBE_COMMAND_INVALID",
        "providerPhysicalCalls": 0,
        "state": "FAILED",
    }


def test_git_identity_hashes_the_binary_tree_object_without_text_decoding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_tree = b"100644 README.md\x00\xff\x00\x81\x7f"
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[object]:
        calls.append((command, kwargs))
        if command[-3:] == ["cat-file", "tree", "HEAD^{tree}"]:
            assert kwargs.get("text", False) is False
            return subprocess.CompletedProcess(command, 0, stdout=raw_tree, stderr=b"")
        if "status" in command:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if "rev-parse" in command:
            return subprocess.CompletedProcess(command, 0, stdout="a" * 40 + "\n", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(optional3_probe_cli.subprocess, "run", fake_run)

    head_sha, tree_sha256 = optional3_probe_cli._current_clean_git_identity(tmp_path)

    assert head_sha == "a" * 40
    assert tree_sha256 == hashlib.sha256(raw_tree).hexdigest()
    assert len(calls) == 3
