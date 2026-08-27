"""Spring API와 Python worker를 하나의 기능 모듈 수명주기로 관리한다."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


_STOP_TIMEOUT_SECONDS = 15


def _terminate(processes: tuple[subprocess.Popen[bytes], ...]) -> None:
    """하위 프로세스 하나가 끝나면 나머지도 종료해 부분 정상 상태를 만들지 않는다."""

    for process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + _STOP_TIMEOUT_SECONDS
    for process in processes:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()
    for process in processes:
        process.wait()


def main() -> int:
    """두 runtime을 시작하고 signal·failure를 컨테이너 단위로 전파한다."""

    worker = subprocess.Popen(
        ["python", "-m", "app.async_worker.grpc_server"],
        close_fds=True,
    )
    spring = subprocess.Popen(
        [
            "java",
            "-XX:MaxRAMPercentage=65",
            "-Djava.io.tmpdir=/tmp",
            "-jar",
            "/app/app.jar",
        ],
        close_fds=True,
    )
    inference = subprocess.Popen(
        ["python", "-m", "app.p1_owner.inference_grpc_server"],
        close_fds=True,
    )
    brokerage: subprocess.Popen[bytes] | None = None
    brokerage_enabled = os.environ.get("KIS_MOCK_BROKERAGE_ONLINE_ENABLED", "false").lower() == "true"
    automation_enabled = os.environ.get("P1_AUTOMATION_RUNTIME_ENABLED", "false").lower() == "true"
    if automation_enabled and not brokerage_enabled:
        raise RuntimeError("automation runtime requires explicit KIS_MOCK online mode")
    if brokerage_enabled:
        brokerage = subprocess.Popen(
            ["python", "-m", "app.brokerage.brokerage_grpc_server"],
            close_fds=True,
        )
    automation: subprocess.Popen[bytes] | None = None
    if automation_enabled:
        automation = subprocess.Popen(
            ["python", "-m", "app.p1_owner.automation_runtime"],
            close_fds=True,
        )
    processes = tuple(
        process
        for process in (worker, inference, spring, brokerage, automation)
        if process is not None
    )
    stopping = False

    def stop(_signal: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            for process in processes:
                status = process.poll()
                if status is not None:
                    return status if status != 0 else 1
            time.sleep(0.2)
        return 0
    finally:
        _terminate(processes)


if __name__ == "__main__":
    sys.exit(main())
