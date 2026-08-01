from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from app.rag.bounded_subprocess import (
    BoundedProcessError,
    BoundedProcessLimits,
    run_bounded_process,
)


def _limits(**overrides: int | float) -> BoundedProcessLimits:
    values: dict[str, int | float] = {
        "timeout_seconds": 2.0,
        "max_stdout_bytes": 4096,
        "max_stderr_bytes": 4096,
        "max_memory_bytes": 256 * 1024 * 1024,
        "max_cpu_seconds": 2,
    }
    values.update(overrides)
    return BoundedProcessLimits(**values)  # type: ignore[arg-type]


def test_bounded_process_returns_only_bounded_stdout_and_minimal_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OWNER_PRIVATE_SECRET", "must-not-reach-child")
    result = run_bounded_process(
        executable=Path(sys.executable),
        arguments=(
            "-c",
            (
                "import json, os; "
                "print(json.dumps({'secret': os.getenv('OWNER_PRIVATE_SECRET'), "
                "'marker': os.getenv('CAPSTONE_CHILD_MARKER')}))"
            ),
        ),
        working_directory=tmp_path,
        environment={"CAPSTONE_CHILD_MARKER": "safe"},
        limits=_limits(),
    )

    assert result.return_code == 0
    assert result.stdout == b'{"secret": null, "marker": "safe"}\n'
    assert result.stderr == b""


def test_timeout_kills_the_process_tree_and_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(BoundedProcessError, match="PARSER_PROCESS_TIMEOUT"):
        run_bounded_process(
            executable=Path(sys.executable),
            arguments=("-c", "import time; time.sleep(60)"),
            working_directory=tmp_path,
            environment={},
            limits=_limits(timeout_seconds=0.1),
        )


def test_stdout_stderr_and_nonzero_exit_are_stable_failures(tmp_path: Path) -> None:
    with pytest.raises(BoundedProcessError, match="PARSER_PROCESS_OUTPUT_BOUND_EXCEEDED"):
        run_bounded_process(
            executable=Path(sys.executable),
            arguments=("-c", "print('x' * 8192)"),
            working_directory=tmp_path,
            environment={},
            limits=_limits(max_stdout_bytes=128),
        )

    with pytest.raises(BoundedProcessError, match="PARSER_PROCESS_FAILED"):
        run_bounded_process(
            executable=Path(sys.executable),
            arguments=("-c", "raise SystemExit(7)"),
            working_directory=tmp_path,
            environment={},
            limits=_limits(),
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX RLIMIT_AS receipt")
def test_posix_memory_limit_fails_closed_without_crashing_parent(tmp_path: Path) -> None:
    with pytest.raises(BoundedProcessError, match="PARSER_PROCESS_FAILED"):
        run_bounded_process(
            executable=Path(sys.executable),
            arguments=("-c", "bytearray(512 * 1024 * 1024)"),
            working_directory=tmp_path,
            environment={},
            limits=_limits(max_memory_bytes=128 * 1024 * 1024),
        )


@pytest.mark.parametrize(
    ("executable", "arguments"),
    [
        (Path("python"), ("-V",)),
        (Path(sys.executable), ("contains\x00nul",)),
        (Path(sys.executable), ("",)),
    ],
)
def test_command_must_be_absolute_and_nul_free(
    tmp_path: Path,
    executable: Path,
    arguments: tuple[str, ...],
) -> None:
    with pytest.raises(BoundedProcessError, match="PARSER_PROCESS_COMMAND_INVALID"):
        run_bounded_process(
            executable=executable,
            arguments=arguments,
            working_directory=tmp_path,
            environment={},
            limits=_limits(),
        )
