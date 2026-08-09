from __future__ import annotations

import json
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
