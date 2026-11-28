"""Spring API와 Python worker를 하나의 기능 모듈 수명주기로 관리한다."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


_STOP_TIMEOUT_SECONDS = 15
_RETENTION_RUNNING = Path("/tmp/decision-platform-retention-running")


def _terminate(processes: tuple[subprocess.Popen[bytes], ...]) -> None:
    """남은 자식에 TERM을 보내고 종료 시한을 넘기면 KILL한다."""

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


_SPRING_READY_TIMEOUT_SECONDS = 300
# 컨테이너 healthcheck(`decision-platform-health.py`)가 쓰는 것과 같은 판정을 쓴다.
# 두 곳이 서로 다른 URL 을 보면 "healthy 인데 자동운용은 못 붙는" 상태가 생긴다.
_SPRING_READY_URL = "http://127.0.0.1:8080/actuator/health"


def _wait_for_spring(spring: subprocess.Popen[bytes]) -> bool:
    """Spring API 가 UP 을 낼 때까지 기다린다. Spring 이 먼저 죽으면 기다리지 않는다."""

    deadline = time.monotonic() + _SPRING_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if spring.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(_SPRING_READY_URL, timeout=2) as response:
                if response.status == 200 and json.load(response).get("status") == "UP":
                    return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def _validate_demo_runtime(environment: dict[str, str]) -> None:
    """Reject any background/account capability in the anonymous demo."""

    forbidden = (
        "ASYNC_WORKER_ENABLED",
        "ASYNC_POLLING_ENABLED",
        "BROKERAGE_GRPC_ENABLED",
        "KIS_MOCK_BROKERAGE_ONLINE_ENABLED",
        "P1_AUTOMATION_RUNTIME_ENABLED",
        "RAG_V2_GRPC_ENABLED",
        "WORLD_NEWS_RETENTION_ENABLED",
    )
    if any(environment.get(key, "false").lower() == "true" for key in forbidden):
        raise RuntimeError("public demo forbids owner/background capabilities")
    if environment.get("S4_9_STRONG_LLM_ENABLED", "false").lower() != "true":
        raise RuntimeError("public demo requires the bounded Agent provider")


def main() -> int:
    """두 runtime을 시작하고 signal·failure를 컨테이너 단위로 전파한다."""

    demo = os.environ.get("MARS_PUBLIC_SURFACE_MODE", "LOCAL") == "DEMO"
    if demo:
        # The public demo has one bounded Agent call path. Starting the owner
        # worker, inference, brokerage, or automation here would reintroduce
        # account capabilities even if the HTTP gate rejected their routes.
        _validate_demo_runtime(dict(os.environ))
    async_worker_enabled = os.environ.get("ASYNC_WORKER_ENABLED", "true").lower() == "true"
    worker = (
        subprocess.Popen(
            ["python", "-m", "app.async_worker.grpc_server"],
            close_fds=True,
        )
        if not demo and async_worker_enabled
        else None
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
    inference = (
        None
        if demo
        else subprocess.Popen(
            ["python", "-m", "app.p1_owner.inference_grpc_server"],
            close_fds=True,
        )
    )
    brokerage: subprocess.Popen[bytes] | None = None
    brokerage_enabled = (
        os.environ.get("KIS_MOCK_BROKERAGE_ONLINE_ENABLED", "false").lower() == "true"
    )
    automation_enabled = os.environ.get("P1_AUTOMATION_RUNTIME_ENABLED", "false").lower() == "true"
    if automation_enabled and not brokerage_enabled:
        raise RuntimeError("automation runtime requires explicit KIS_MOCK online mode")
    if brokerage_enabled:
        brokerage = subprocess.Popen(
            ["python", "-m", "app.brokerage.brokerage_grpc_server"],
            close_fds=True,
        )
    # RAG v2는 자체 설정 오류로 종료할 수 있다. 조회 실패는 RAG 경로에 남긴다.
    rag_v2: subprocess.Popen[bytes] | None = None
    if os.environ.get("RAG_V2_GRPC_ENABLED", "false").lower() == "true":
        rag_v2 = subprocess.Popen(
            ["python", "-m", "app.rag.rag_v2_grpc_server"],
            close_fds=True,
        )
    # Strong LLM 판단 프로세스. 이것이 없으면 Kotlin host의 gRPC 어댑터가 부를 상대가 없다.
    # 켜져 있는데 provider 설정이나 shared secret이 없으면 스스로 기동에서 닫힌다.
    strong_llm: subprocess.Popen[bytes] | None = None
    if os.environ.get("S4_9_STRONG_LLM_ENABLED", "false").lower() == "true":
        strong_llm = subprocess.Popen(
            ["python", "-m", "app.strong_llm.grpc_server"],
            close_fds=True,
        )
    # 30일이 지나 더 쓰이지 않는 세계 뉴스 버전을 실제로 지운다. DB 함수와 어댑터는
    # V157 부터 있었지만 부르는 곳이 없어 코퍼스가 무한히 자랐다(2026-09-14 하루 15만 행).
    # `decision_worker` DSN 은 이미 이 컨테이너에 있으므로 새 권한이 생기지 않는다.
    _RETENTION_RUNNING.unlink(missing_ok=True)
    retention: subprocess.Popen[bytes] | None = None
    if os.environ.get("WORLD_NEWS_RETENTION_ENABLED", "false").lower() == "true":
        retention = subprocess.Popen(
            ["python", "-m", "app.data.news.retention_cli", "--loop"],
            close_fds=True,
        )
        _RETENTION_RUNNING.touch()
    # Start automation after Spring is ready to avoid restart loops during an overdue claim.
    automation: subprocess.Popen[bytes] | None = None
    if automation_enabled:
        if not _wait_for_spring(spring):
            _terminate(
                tuple(
                    process
                    for process in (
                        worker,
                        inference,
                        spring,
                        brokerage,
                        rag_v2,
                        strong_llm,
                        retention,
                    )
                    if process is not None
                )
            )
            _RETENTION_RUNNING.unlink(missing_ok=True)
            print(
                "decision-platform: spring did not become ready for automation",
                file=sys.stderr,
            )
            return 1
        automation = subprocess.Popen(
            ["python", "-m", "app.p1_owner.automation_runtime"],
            close_fds=True,
        )
    processes = tuple(
        process
        for process in (
            worker,
            inference,
            spring,
            brokerage,
            automation,
            strong_llm,
        )
        if process is not None
    )
    optional = {
        name: process
        for name, process in (("rag-v2", rag_v2), ("world-news-retention", retention))
        if process is not None
    }
    all_processes = processes + tuple(optional.values())
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
            for name, process in tuple(optional.items()):
                status = process.poll()
                if status is not None:
                    del optional[name]
                    if name == "world-news-retention":
                        _RETENTION_RUNNING.unlink(missing_ok=True)
                    print(
                        f"decision-platform: {name}=DEGRADED exit={status}",
                        file=sys.stderr,
                        flush=True,
                    )
            time.sleep(0.2)
        return 0
    finally:
        _RETENTION_RUNNING.unlink(missing_ok=True)
        _terminate(all_processes)


if __name__ == "__main__":
    sys.exit(main())
