"""S1.4와 S1.4R pinned environment를 분리해 frozen oracle 결과를 capture한다."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from compare_results import compare_reference_regeneration
from oracle_common import (
    OracleContractError,
    atomic_write_json,
    canonical_json_bytes,
    find_repo_root,
    sha256_bytes,
    strict_json_load,
)
from reference_worker import PRODUCTION_FUNCTIONS, RESEARCH_FUNCTIONS

EXPECTED_UV_VERSION = "uv 0.11.26"
EXPECTED_UV_VERSION_PATTERN = re.compile(r"^uv 0\.11\.26(?: \([^)]+\))?$")
EXPECTED_PYTHON_VERSION = "3.12.13"
EXPECTED_NUMPY_VERSION = "2.5.1"

Runner = Callable[..., subprocess.CompletedProcess[bytes]]


def _portable_stderr(stderr: bytes) -> str:
    text = stderr.decode("utf-8", errors="replace").strip()
    if not text:
        return "<empty>"
    return text.splitlines()[-1][:500]


def _run_checked(
    argv: list[str],
    *,
    cwd: Path,
    runner: Runner,
    timeout_seconds: int,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    completed = runner(
        argv,
        cwd=cwd,
        env=dict(environment) if environment is not None else None,
        check=False,
        capture_output=True,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        raise OracleContractError(
            f"pinned subprocess failed: exit={completed.returncode}, "
            f"leaf={_portable_stderr(completed.stderr)}"
        )
    return completed


def _partition_request(request: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if request.get("schemaVersion") != "s1.4x-request-v1":
        raise OracleContractError("reference request schemaVersion mismatch")
    request_id = request.get("requestId")
    cases = request.get("cases")
    if not isinstance(request_id, str) or not request_id or not isinstance(cases, list):
        raise OracleContractError("reference request envelope is invalid")
    partitions: dict[str, list[dict[str, Any]]] = {"s1.4": [], "s1.4r": []}
    fixture_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise OracleContractError("reference request case must be an object")
        fixture_id = case.get("fixtureId")
        function_id = case.get("functionId")
        if (
            not isinstance(fixture_id, str)
            or not fixture_id
            or fixture_id in fixture_ids
        ):
            raise OracleContractError("reference request fixtureId is invalid or duplicate")
        fixture_ids.add(fixture_id)
        if function_id in PRODUCTION_FUNCTIONS:
            partitions["s1.4"].append(case)
        elif function_id in RESEARCH_FUNCTIONS:
            partitions["s1.4r"].append(case)
        else:
            raise OracleContractError(f"unknown frozen functionId: {function_id!r}")
    return {
        track: {
            "schemaVersion": "s1.4x-request-v1",
            "requestId": request_id,
            "cases": track_cases,
        }
        for track, track_cases in partitions.items()
    }


def _validate_worker_result(
    batch: Any,
    *,
    request: Mapping[str, Any],
    track: str,
) -> dict[str, Any]:
    if not isinstance(batch, dict):
        raise OracleContractError(f"{track} worker result must be an object")
    if batch.get("schemaVersion") != "s1.4x-result-batch-v1":
        raise OracleContractError(f"{track} worker result schemaVersion mismatch")
    if batch.get("requestId") != request.get("requestId"):
        raise OracleContractError(f"{track} worker requestId mismatch")
    results = batch.get("results")
    if not isinstance(results, list):
        raise OracleContractError(f"{track} worker results must be an array")
    expected_ids = [
        case.get("fixtureId")
        for case in request.get("cases", [])
        if isinstance(case, dict)
    ]
    actual_ids = [
        result.get("fixtureId")
        for result in results
        if isinstance(result, dict)
    ]
    if len(actual_ids) != len(results) or actual_ids != expected_ids:
        raise OracleContractError(f"{track} worker silently skipped or reordered cases")
    return batch


def _validate_runtime(runtime: Any, *, track: str) -> dict[str, Any]:
    if not isinstance(runtime, dict):
        raise OracleContractError(f"{track} runtime identity must be an object")
    expected = {
        "track": track,
        "pythonImplementation": "cpython",
        "pythonVersion": EXPECTED_PYTHON_VERSION,
        "numpyVersion": EXPECTED_NUMPY_VERSION,
    }
    if runtime != expected:
        raise OracleContractError(
            f"{track} runtime mismatch: expected={expected!r}, actual={runtime!r}"
        )
    return runtime


def capture(
    *,
    request_path: Path,
    output_path: Path,
    fixture_root: Path,
    production_project: Path,
    research_project: Path,
    uv_bin: Path,
    scratch_root: Path,
    check: bool,
    capture_report_path: Path | None,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """두 frozen project를 별도 uv subprocess로 실행해 original case order로 병합한다."""

    request = strict_json_load(request_path)
    if not isinstance(request, dict):
        raise OracleContractError("reference request must be an object")
    partitions = _partition_request(request)
    resolved_uv = uv_bin.resolve(strict=True)
    uv_version = _run_checked(
        [str(resolved_uv), "--version"],
        cwd=production_project,
        runner=runner,
        timeout_seconds=10,
    ).stdout.decode("utf-8", errors="strict").strip()
    if EXPECTED_UV_VERSION_PATTERN.fullmatch(uv_version) is None:
        raise OracleContractError(
            f"uv version mismatch: expected={EXPECTED_UV_VERSION}, actual={uv_version}"
        )
    for project in (production_project, research_project):
        _run_checked(
            [str(resolved_uv), "lock", "--project", str(project), "--check"],
            cwd=project,
            runner=runner,
            timeout_seconds=60,
        )

    scratch_root.mkdir(parents=True, exist_ok=True)
    worker = Path(__file__).resolve().with_name("reference_worker.py")
    runtime_by_track: dict[str, dict[str, Any]] = {}
    results_by_fixture: dict[str, dict[str, Any]] = {}
    project_by_track = {
        "s1.4": production_project,
        "s1.4r": research_project,
    }
    reference_root_by_track = {
        "s1.4": production_project,
        "s1.4r": research_project,
    }
    with tempfile.TemporaryDirectory(prefix="capture-", dir=scratch_root) as directory:
        scratch = Path(directory)
        for track in ("s1.4", "s1.4r"):
            partition_path = scratch / f"{track}-request.json"
            result_path = scratch / f"{track}-result.json"
            runtime_path = scratch / f"{track}-runtime.json"
            atomic_write_json(partition_path, partitions[track])
            project = project_by_track[track]
            environment = dict(os.environ)
            environment["UV_PYTHON"] = EXPECTED_PYTHON_VERSION
            environment.pop("PYTHONPATH", None)
            _run_checked(
                [
                    str(resolved_uv),
                    "run",
                    "--project",
                    str(project),
                    "--frozen",
                    "--python",
                    EXPECTED_PYTHON_VERSION,
                    "python",
                    str(worker),
                    "--track",
                    track,
                    "--reference-root",
                    str(reference_root_by_track[track]),
                    "--fixture-root",
                    str(fixture_root),
                    "--request",
                    str(partition_path),
                    "--output",
                    str(result_path),
                    "--runtime-output",
                    str(runtime_path),
                ],
                cwd=project,
                runner=runner,
                timeout_seconds=120,
                environment=environment,
            )
            batch = _validate_worker_result(
                strict_json_load(result_path),
                request=partitions[track],
                track=track,
            )
            runtime_by_track[track] = _validate_runtime(
                strict_json_load(runtime_path),
                track=track,
            )
            for result in batch["results"]:
                results_by_fixture[result["fixtureId"]] = result

    ordered_results = []
    for case in request["cases"]:
        fixture_id = case["fixtureId"]
        if fixture_id not in results_by_fixture:
            raise OracleContractError(f"reference capture omitted fixture {fixture_id!r}")
        ordered_results.append(results_by_fixture[fixture_id])
    combined = {
        "schemaVersion": "s1.4x-result-batch-v1",
        "requestId": request["requestId"],
        "implementation": "python-frozen-oracle",
        "results": ordered_results,
    }
    payload = canonical_json_bytes(combined)
    if check:
        try:
            expected_payload = output_path.read_bytes()
        except OSError as exc:
            raise OracleContractError("frozen expected output is missing") from exc
        expected = strict_json_load(output_path)
        if canonical_json_bytes(expected) != expected_payload:
            raise OracleContractError(
                "frozen expected output must use canonical JSON UTF-8 bytes"
            )
        mismatches = compare_reference_regeneration(
            expected,
            combined,
            request=request,
        )
        if mismatches:
            first = mismatches[0]
            raise OracleContractError(
                "reference output regeneration drift: "
                f"mismatchCount={len(mismatches)}, "
                f"firstFixtureId={first['fixtureId']}, "
                f"firstPath={first['path']}, "
                f"expectedSha256={sha256_bytes(expected_payload)}, "
                f"actualSha256={sha256_bytes(payload)}"
            )
    else:
        atomic_write_json(output_path, combined)
    if capture_report_path is not None:
        atomic_write_json(
            capture_report_path,
            {
                "schemaVersion": "s1.4x-reference-capture-report-v1",
                "uvVersion": uv_version,
                "processCount": 2,
                "projects": [
                    {"projectId": "S1_4_PRODUCTION", **runtime_by_track["s1.4"]},
                    {"projectId": "S1_4R_RESEARCH", **runtime_by_track["s1.4r"]},
                ],
                "resultSha256": sha256_bytes(payload),
                "status": "PASS",
            },
        )
    return combined


def _default_paths() -> dict[str, Path]:
    repo = find_repo_root()
    research = repo / "workspaces" / "decision-platform" / "research"
    s1_4x = research / "s1-4x-numeric-parity"
    return {
        "request": s1_4x / "contract" / "fixtures" / "small" / "canonical-inputs.v1.json",
        "output": s1_4x
        / "contract"
        / "fixtures"
        / "expected"
        / "canonical-results.v1.json",
        "fixture_root": s1_4x / "contract" / "fixtures",
        "production": repo / "workspaces" / "decision-platform" / "python-services",
        "research": research / "s1-4r-jax-risk",
        "scratch": Path.home() / ".cache" / "s1-4x" / "oracle-capture",
    }


def _parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    defaults = _default_paths()
    parser = argparse.ArgumentParser(
        description=(
            "Capture S1.4 and S1.4R NumPy results in two separate uv --frozen "
            "subprocesses. --check preserves frozen canonical bytes and applies the "
            "frozen typed numeric tolerance to live regeneration without overwriting."
        )
    )
    parser.add_argument("--request", type=Path, default=defaults["request"])
    parser.add_argument("--output", type=Path, default=defaults["output"])
    parser.add_argument("--fixture-root", type=Path, default=defaults["fixture_root"])
    parser.add_argument("--production-project", type=Path, default=defaults["production"])
    parser.add_argument("--research-project", type=Path, default=defaults["research"])
    parser.add_argument("--uv-bin", type=Path, default=Path(shutil.which("uv") or "uv"))
    parser.add_argument("--scratch-root", type=Path, default=defaults["scratch"])
    parser.add_argument("--capture-report", type=Path)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint이며 capture 성공 시 결과 hash만 sanitized stdout에 출력한다."""

    arguments = _parse_arguments(argv)
    try:
        combined = capture(
            request_path=arguments.request.resolve(),
            output_path=arguments.output.resolve(),
            fixture_root=arguments.fixture_root.resolve(),
            production_project=arguments.production_project.resolve(),
            research_project=arguments.research_project.resolve(),
            uv_bin=arguments.uv_bin,
            scratch_root=arguments.scratch_root.resolve(),
            check=arguments.check,
            capture_report_path=(
                arguments.capture_report.resolve()
                if arguments.capture_report is not None
                else None
            ),
        )
    except OracleContractError as exc:
        print(f"REFERENCE_CAPTURE_FAIL {exc}")
        return 1
    print(
        f"REFERENCE_CAPTURE_PASS cases={len(combined['results'])} "
        f"sha256={sha256_bytes(canonical_json_bytes(combined))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
