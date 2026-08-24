from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.data._shared.canonical_json import canonical_json_sha256
from app.verification.models import VerificationReport
from app.verification.runner import (
    S0_S5_GATE_IDS,
    Command,
    CommandEvidence,
    run_s0_s5_current,
)


def test_s0_s5_current_reports_exact_provider_free_gates(tmp_path: Path) -> None:
    commands: list[Command] = []

    def execute(command: Command) -> CommandEvidence:
        commands.append(command)
        return CommandEvidence(return_code=0, output_sha256="c" * 64)

    report = run_s0_s5_current(
        repository_root=tmp_path,
        now=datetime(2026, 8, 21, 0, tzinfo=UTC),
        executor=execute,
        git_identity=lambda _: ("a" * 40, "b" * 64),
    )

    assert report.execution_state == "PASS"
    assert report.aggregate_outcome == "PASS"
    assert tuple(gate.gate_id for gate in report.gates) == S0_S5_GATE_IDS
    assert all(gate.physical_call_count == 0 for gate in report.gates)
    assert report.provider_data_physical_calls == 0
    assert report.account_calls == report.balance_calls == report.order_calls == 0
    assert VerificationReport.from_dict(report.to_dict()) == report
    assert commands

    for mutation in ("extra", "contract", "type", "duplicate_gate"):
        value = copy.deepcopy(report.to_dict())
        if mutation == "extra":
            value["unexpected"] = True
        elif mutation == "contract":
            value["contractId"] = "p1-verification-report.v0"
        elif mutation == "type":
            value["providerDataPhysicalCalls"] = "0"
        else:
            value["gates"] = [value["gates"][0], value["gates"][0]]
        value.pop("evidenceSha256")
        value["evidenceSha256"] = canonical_json_sha256(value)
        with pytest.raises(ValueError):
            VerificationReport.from_dict(value)


def test_s0_s5_current_keeps_lanes_independent_after_failure(tmp_path: Path) -> None:
    call_count = 0

    def execute(command: Command) -> CommandEvidence:
        nonlocal call_count
        call_count += 1
        return CommandEvidence(
            return_code=1 if call_count == 1 else 0,
            output_sha256="d" * 64,
        )

    report = run_s0_s5_current(
        repository_root=tmp_path,
        now=datetime(2026, 8, 21, 0, tzinfo=UTC),
        executor=execute,
        git_identity=lambda _: ("a" * 40, "b" * 64),
    )

    assert report.execution_state == "FAIL"
    assert report.gates[0].failure_code == "REGRESSION_GATE_FAILED"
    assert all(gate.execution_state == "PASS" for gate in report.gates[1:])
    assert call_count > 1
