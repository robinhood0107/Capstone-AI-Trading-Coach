from __future__ import annotations

import ctypes
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final


class BoundedProcessError(RuntimeError):
    """격리 parser/OCR process가 자원·명령·종료 계약을 위반했음을 stable code로 알린다."""


_ENVIRONMENT_NAME: Final = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_FORBIDDEN_ENVIRONMENT_NAME: Final = re.compile(
    r"(?:SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|API_KEY|PRIVATE_KEY)",
    re.I,
)
_WINDOWS_INHERITED_ENVIRONMENT: Final = (
    "COMSPEC",
    "LOCALAPPDATA",
    "PATH",
    "PATHEXT",
    "PROGRAMDATA",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "WINDIR",
)


@dataclass(frozen=True, slots=True)
class BoundedProcessLimits:
    """child 하나에 허용되는 wall clock, output, address-space, CPU 상한이다."""

    timeout_seconds: float
    max_stdout_bytes: int
    max_stderr_bytes: int
    max_memory_bytes: int
    max_cpu_seconds: int
    max_stdin_bytes: int = 128 * 1024 * 1024

    def validate(self) -> None:
        if (
            self.timeout_seconds <= 0
            or self.max_stdout_bytes <= 0
            or self.max_stderr_bytes <= 0
            or self.max_memory_bytes < 64 * 1024 * 1024
            or self.max_cpu_seconds <= 0
            or self.max_stdin_bytes <= 0
        ):
            raise BoundedProcessError("PARSER_PROCESS_LIMIT_INVALID")


@dataclass(frozen=True, slots=True)
class BoundedProcessResult:
    """성공한 child의 상한 내 stdout/stderr와 elapsed time만 반환한다."""

    return_code: int
    stdout: bytes
    stderr: bytes
    elapsed_seconds: float


def run_bounded_process(
    *,
    executable: Path,
    arguments: tuple[str, ...],
    working_directory: Path,
    environment: Mapping[str, str],
    limits: BoundedProcessLimits,
    stdin: bytes | None = None,
) -> BoundedProcessResult:
    """shell 없이 최소 환경의 child를 실행하고 timeout/OOM/output 초과를 fail-closed한다.

    전달 환경은 allowlisted 이름만 허용하며 credential 계열 이름은 거부한다. POSIX는
    rlimit/process group, Windows는 Job Object/process group으로 자식 트리까지 묶는다.
    """

    limits.validate()
    _validate_command(executable, arguments, working_directory)
    if stdin is not None and len(stdin) > limits.max_stdin_bytes:
        raise BoundedProcessError("PARSER_PROCESS_INPUT_BOUND_EXCEEDED")
    child_environment = _minimal_environment(environment)
    command = (str(executable), *arguments)
    started = time.monotonic()
    job_handle: int | None = None
    try:
        if os.name == "nt":
            process = subprocess.Popen(
                command,
                cwd=str(working_directory),
                env=child_environment,
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=0x00000200,  # CREATE_NEW_PROCESS_GROUP
            )
        else:
            process = subprocess.Popen(
                command,
                cwd=str(working_directory),
                env=child_environment,
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
                preexec_fn=_posix_limits(limits),
            )
    except (OSError, ValueError) as error:
        raise BoundedProcessError("PARSER_PROCESS_START_FAILED") from error
    try:
        if os.name == "nt":
            job_handle = _assign_windows_job(process, limits)
        assert process.stdout is not None
        assert process.stderr is not None
        stdout = bytearray()
        stderr = bytearray()
        output_exceeded = threading.Event()
        stdout_thread = threading.Thread(
            target=_read_bounded,
            args=(process.stdout, stdout, limits.max_stdout_bytes, output_exceeded),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_read_bounded,
            args=(process.stderr, stderr, limits.max_stderr_bytes, output_exceeded),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        stdin_thread: threading.Thread | None = None
        if stdin is not None:
            assert process.stdin is not None
            stdin_thread = threading.Thread(
                target=_write_stdin,
                args=(process.stdin, stdin),
                daemon=True,
            )
            stdin_thread.start()
        deadline = started + limits.timeout_seconds
        while process.poll() is None:
            if output_exceeded.is_set():
                _terminate_process_tree(process, job_handle)
                raise BoundedProcessError("PARSER_PROCESS_OUTPUT_BOUND_EXCEEDED")
            if time.monotonic() >= deadline:
                _terminate_process_tree(process, job_handle)
                raise BoundedProcessError("PARSER_PROCESS_TIMEOUT")
            time.sleep(0.01)
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)
        if stdin_thread is not None:
            stdin_thread.join(timeout=1)
        if output_exceeded.is_set():
            raise BoundedProcessError("PARSER_PROCESS_OUTPUT_BOUND_EXCEEDED")
        if stdout_thread.is_alive() or stderr_thread.is_alive():
            _terminate_process_tree(process, job_handle)
            raise BoundedProcessError("PARSER_PROCESS_OUTPUT_DRAIN_FAILED")
        if stdin_thread is not None and stdin_thread.is_alive():
            _terminate_process_tree(process, job_handle)
            raise BoundedProcessError("PARSER_PROCESS_INPUT_DRAIN_FAILED")
        if process.returncode != 0:
            raise BoundedProcessError("PARSER_PROCESS_FAILED")
        return BoundedProcessResult(
            return_code=process.returncode,
            stdout=bytes(stdout),
            stderr=bytes(stderr),
            elapsed_seconds=time.monotonic() - started,
        )
    finally:
        if process.poll() is None:
            _terminate_process_tree(process, job_handle)
        if job_handle is not None:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(job_handle)  # type: ignore[attr-defined]
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        if process.stdin is not None:
            process.stdin.close()


def _validate_command(
    executable: Path,
    arguments: tuple[str, ...],
    working_directory: Path,
) -> None:
    if (
        not executable.is_absolute()
        or not executable.is_file()
        or not working_directory.is_absolute()
        or not working_directory.is_dir()
        or not arguments
        or any(not value or "\x00" in value for value in arguments)
    ):
        raise BoundedProcessError("PARSER_PROCESS_COMMAND_INVALID")


def _minimal_environment(values: Mapping[str, str]) -> dict[str, str]:
    result = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    }
    if os.name == "nt":
        for name in _WINDOWS_INHERITED_ENVIRONMENT:
            value = os.environ.get(name)
            if value:
                result[name] = value
    for name, value in values.items():
        if (
            _ENVIRONMENT_NAME.fullmatch(name) is None
            or _FORBIDDEN_ENVIRONMENT_NAME.search(name) is not None
            or "\x00" in value
        ):
            raise BoundedProcessError("PARSER_PROCESS_ENVIRONMENT_INVALID")
        result[name] = value
    return result


def _read_bounded(
    stream: BinaryIO,
    output: bytearray,
    maximum: int,
    exceeded: threading.Event,
) -> None:
    try:
        while chunk := stream.read(65_536):
            remaining = maximum - len(output)
            if len(chunk) > remaining:
                output.extend(chunk[: max(0, remaining)])
                exceeded.set()
                return
            output.extend(chunk)
    except OSError:
        exceeded.set()


def _write_stdin(stream: BinaryIO, payload: bytes) -> None:
    try:
        stream.write(payload)
        stream.flush()
    except (BrokenPipeError, OSError):
        pass
    finally:
        stream.close()


def _posix_limits(limits: BoundedProcessLimits) -> Callable[[], None]:
    def apply_limits() -> None:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (limits.max_memory_bytes, limits.max_memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (limits.max_cpu_seconds, limits.max_cpu_seconds))
        file_limit = limits.max_stdout_bytes + limits.max_stderr_bytes
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))

    return apply_limits


def _terminate_process_tree(process: subprocess.Popen[bytes], job_handle: int | None) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        if job_handle is not None:
            ctypes.WinDLL("kernel32", use_last_error=True).TerminateJobObject(job_handle, 1)  # type: ignore[attr-defined]
        else:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _assign_windows_job(
    process: subprocess.Popen[bytes],
    limits: BoundedProcessLimits,
) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "read_operation_count",
                "write_operation_count",
                "other_operation_count",
                "read_transfer_count",
                "write_transfer_count",
                "other_transfer_count",
            )
        ]

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("per_process_user_time_limit", ctypes.c_longlong),
            ("per_job_user_time_limit", ctypes.c_longlong),
            ("limit_flags", wintypes.DWORD),
            ("minimum_working_set_size", ctypes.c_size_t),
            ("maximum_working_set_size", ctypes.c_size_t),
            ("active_process_limit", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority_class", wintypes.DWORD),
            ("scheduling_class", wintypes.DWORD),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("basic_limit_information", BasicLimitInformation),
            ("io_info", IoCounters),
            ("process_memory_limit", ctypes.c_size_t),
            ("job_memory_limit", ctypes.c_size_t),
            ("peak_process_memory_used", ctypes.c_size_t),
            ("peak_job_memory_used", ctypes.c_size_t),
        ]

    create_job = kernel32.CreateJobObjectW
    create_job.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    create_job.restype = wintypes.HANDLE
    job = create_job(None, None)
    if not job:
        process.kill()
        raise BoundedProcessError("PARSER_PROCESS_JOB_FAILED")
    info = ExtendedLimitInformation()
    info.basic_limit_information.limit_flags = 0x00000100 | 0x00002000
    info.process_memory_limit = limits.max_memory_bytes
    set_information = kernel32.SetInformationJobObject
    set_information.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    set_information.restype = wintypes.BOOL
    assign = kernel32.AssignProcessToJobObject
    assign.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    assign.restype = wintypes.BOOL
    if not set_information(job, 9, ctypes.byref(info), ctypes.sizeof(info)) or not assign(
        job,
        process._handle,  # type: ignore[attr-defined]
    ):
        kernel32.CloseHandle(job)
        process.kill()
        raise BoundedProcessError("PARSER_PROCESS_JOB_FAILED")
    return int(job)
