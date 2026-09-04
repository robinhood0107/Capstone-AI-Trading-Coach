"""Spring API와 Python worker를 하나의 기능 모듈 수명주기로 관리한다."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request


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
    # RAG v2 실검색 프로세스. 켜져 있는데 로컬 루트나 credential이 없으면 스스로 기동에서
    # 닫히고, 그 실패가 컨테이너 실패로 올라온다. 조용히 fixture로 되돌아가지 않는다.
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
    # 자동운용은 맨 마지막이다. 같은 컨테이너의 Spring API 로 로그인한 뒤에야 판단을 진행할 수
    # 있는데, 둘을 동시에 띄우면 예약 시각이 이미 지난 상태에서 재기동할 때 런타임이 기동 즉시
    # claim 해서 AI_JUDGING 까지 갔다가 아직 듣지 않는 8080 에 붙지 못하고 죽고, 수퍼바이저가
    # 그것을 컨테이너 실패로 올려 재기동 루프가 된다. 실측으로 그 루프에 빠졌다.
    #
    # 평소에는 run_at 이 미래라 우연히 시간이 맞아 문제가 드러나지 않았을 뿐이다. 장중
    # 재기동에서도 같은 일이 나므로 여기서 순서를 고정한다. 대기는 다른 프로세스를 전부 띄운
    # 뒤에 한다 - Spring health 가 그들 중 하나에 의존하면 앞에서 기다리다 교착되기 때문이다.
    automation: subprocess.Popen[bytes] | None = None
    if automation_enabled:
        if not _wait_for_spring(spring):
            _terminate(
                tuple(
                    process
                    for process in (worker, inference, spring, brokerage, rag_v2, strong_llm)
                    if process is not None
                )
            )
            print("decision-platform: spring did not become ready for automation", file=sys.stderr)
            return 1
        automation = subprocess.Popen(
            ["python", "-m", "app.p1_owner.automation_runtime"],
            close_fds=True,
        )
    processes = tuple(
        process
        for process in (worker, inference, spring, brokerage, automation, rag_v2, strong_llm)
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
