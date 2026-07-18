from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from capture_reference_results import _partition_request, capture
from oracle_common import OracleContractError, atomic_write_json, strict_json_load


def _request() -> dict[str, Any]:
    return {
        "schemaVersion": "s1.4x-request-v1",
        "requestId": "capture-1",
        "cases": [
            {
                "fixtureId": "production-case",
                "functionId": "cumulative_return",
                "arguments": {"returns": [0.1]},
            },
            {
                "fixtureId": "research-case",
                "functionId": "realized_variance",
                "arguments": {"returns": [0.1, 0.2]},
            },
        ],
    }


class FakeRunner:
    """uv command boundary를 재현하되 Python reference를 현재 process에 import하지 않는다."""

    def __init__(self, *, uv_version: bytes = b"uv 0.11.26\n") -> None:
        self.uv_version = uv_version
        self.commands: list[list[str]] = []
        self.uv_python_values: list[str | None] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.commands.append(argv)
        environment = kwargs.get("env")
        self.uv_python_values.append(
            environment.get("UV_PYTHON") if isinstance(environment, dict) else None
        )
        if argv[1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout=self.uv_version, stderr=b"")
        if "lock" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")
        track = argv[argv.index("--track") + 1]
        request_path = Path(argv[argv.index("--request") + 1])
        output_path = Path(argv[argv.index("--output") + 1])
        runtime_path = Path(argv[argv.index("--runtime-output") + 1])
        request = strict_json_load(request_path)
        results = [
            {
                "schemaVersion": "s1.4x-result-v1",
                "functionId": case["functionId"],
                "fixtureId": case["fixtureId"],
                "status": "ok",
                "values": float(index + 1),
            }
            for index, case in enumerate(request["cases"])
        ]
        atomic_write_json(
            output_path,
            {
                "schemaVersion": "s1.4x-result-batch-v1",
                "requestId": request["requestId"],
                "implementation": f"fake-{track}",
                "results": results,
            },
        )
        atomic_write_json(
            runtime_path,
            {
                "track": track,
                "pythonImplementation": "cpython",
                "pythonVersion": "3.12.13",
                "numpyVersion": "2.5.1",
            },
        )
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")


def test_partition_rejects_unknown_and_duplicate_cases() -> None:
    duplicate = _request()
    duplicate["cases"][1]["fixtureId"] = "production-case"
    with pytest.raises(OracleContractError, match="duplicate"):
        _partition_request(duplicate)

    unknown = _request()
    unknown["cases"][1]["functionId"] = "not_frozen"
    with pytest.raises(OracleContractError, match="unknown frozen"):
        _partition_request(unknown)


def test_capture_uses_two_separate_pinned_subprocesses_and_restores_order(
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "request.json"
    output_path = tmp_path / "result.json"
    report_path = tmp_path / "report.json"
    fixture_root = tmp_path / "fixtures"
    production = tmp_path / "production"
    research = tmp_path / "research"
    uv_bin = tmp_path / "uv"
    for directory in (fixture_root, production, research):
        directory.mkdir()
    uv_bin.write_text("fake", encoding="utf-8")
    atomic_write_json(request_path, _request())
    runner = FakeRunner()

    result = capture(
        request_path=request_path,
        output_path=output_path,
        fixture_root=fixture_root,
        production_project=production,
        research_project=research,
        uv_bin=uv_bin,
        scratch_root=tmp_path / "scratch",
        check=False,
        capture_report_path=report_path,
        runner=runner,
    )

    run_commands = [command for command in runner.commands if "run" in command]
    run_environment_values = [
        environment
        for command, environment in zip(
            runner.commands, runner.uv_python_values, strict=True
        )
        if "run" in command
    ]
    assert len(run_commands) == 2
    assert [command[command.index("--track") + 1] for command in run_commands] == [
        "s1.4",
        "s1.4r",
    ]
    assert all(
        command[command.index("--python") + 1] == "3.12.13"
        for command in run_commands
    )
    assert run_environment_values == ["3.12.13", "3.12.13"]
    assert [item["fixtureId"] for item in result["results"]] == [
        "production-case",
        "research-case",
    ]
    assert strict_json_load(report_path)["processCount"] == 2

    capture(
        request_path=request_path,
        output_path=output_path,
        fixture_root=fixture_root,
        production_project=production,
        research_project=research,
        uv_bin=uv_bin,
        scratch_root=tmp_path / "scratch",
        check=True,
        capture_report_path=None,
        runner=FakeRunner(),
    )


def test_capture_fails_closed_on_uv_version_or_expected_byte_drift(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    uv_bin = tmp_path / "uv"
    uv_bin.write_text("fake", encoding="utf-8")
    atomic_write_json(request_path, _request())
    for name in ("fixtures", "production", "research"):
        (tmp_path / name).mkdir()
    common: dict[str, Any] = {
        "request_path": request_path,
        "output_path": tmp_path / "result.json",
        "fixture_root": tmp_path / "fixtures",
        "production_project": tmp_path / "production",
        "research_project": tmp_path / "research",
        "uv_bin": uv_bin,
        "scratch_root": tmp_path / "scratch",
        "capture_report_path": None,
    }

    with pytest.raises(OracleContractError, match="uv version mismatch"):
        capture(check=False, runner=FakeRunner(uv_version=b"uv 0.11.260\n"), **common)

    (tmp_path / "result.json").write_bytes(b"{}\n")
    with pytest.raises(OracleContractError, match="regeneration drift"):
        capture(check=True, runner=FakeRunner(), **common)


def test_capture_accepts_exact_uv_version_with_platform_suffix(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    uv_bin = tmp_path / "uv"
    uv_bin.write_text("fake", encoding="utf-8")
    atomic_write_json(request_path, _request())
    for name in ("fixtures", "production", "research"):
        (tmp_path / name).mkdir()

    result = capture(
        request_path=request_path,
        output_path=tmp_path / "result.json",
        fixture_root=tmp_path / "fixtures",
        production_project=tmp_path / "production",
        research_project=tmp_path / "research",
        uv_bin=uv_bin,
        scratch_root=tmp_path / "scratch",
        check=False,
        capture_report_path=None,
        runner=FakeRunner(uv_version=b"uv 0.11.26 (x86_64-unknown-linux-gnu)\n"),
    )

    assert len(result["results"]) == 2
