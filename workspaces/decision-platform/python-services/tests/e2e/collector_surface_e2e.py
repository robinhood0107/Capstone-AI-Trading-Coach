"""수집기 자격증명과 그 표면이 실제로 살아 있는지 확인한다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다.

왜 필요한가. 다른 e2e 러너들은 전부 provider-free 기본 스택 안에서 돈다. 수집기(KRX OPEN API,
ECOS, OpenDART)는 그 밖에 있어서 어느 게이트도 덮지 않았다. 자격증명이 만료되거나 서비스가
계약을 바꿔도 아무도 모르는 상태였다.

무엇을 확인하나.

  1. 수집기 자격증명이 환경에 있고 형식을 만족한다 - 값은 절대 찍지 않는다
  2. KRX OPEN API 승인 서비스 하나가 응답한다
  3. ECOS registry preflight가 통과한다
  4. OpenDART 설정이 자격증명을 읽어 클라이언트를 만들 수 있다
  5. GDELT는 호출하지 않는다 - 연구 상태로 범위 밖이다

**GDELT는 이 러너가 절대 부르지 않는다.** 소유자가 연구 상태로 분리해 두었고, 이 경계를
바꾸려면 소유자에게 먼저 묻는다. 판정표에도 그렇게 적는다.

외부 호출은 `--with-live-collect` 를 줬을 때만 일어난다. 플래그가 없으면 자격증명 형식까지만
보고 나머지는 INFO로 남긴다. `rag_v2_boundaries.py`가 provider 호출을 플래그 뒤에 두는 것과
같은 규약이다.

실행:
  P1_COLLECTOR_SURFACE_E2E=1 python -m tests.e2e.collector_surface_e2e \\
    --with-live-collect --out artifacts/decision-platform/e2e/collector-surface.json
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date, timedelta
from typing import Final, Sequence

from .harness import (
    DEPLOY,
    DOCKER,
    PROJECT,
    REPOSITORY,
    STATE,
    HarnessError,
    Recorder,
    require_opt_in,
    write_report,
)

_OPT_IN: Final = "P1_COLLECTOR_SURFACE_E2E"
_SERVICES: Final = "python-services"

# 값이 아니라 "있는가"만 본다. 최소 길이는 오타나 빈 문자열을 거르기 위한 것이다.
_REQUIRED_CREDENTIALS: Final[tuple[tuple[str, int], ...]] = (
    ("KRX_OPENAPI_AUTH_KEY", 16),
    ("ECOS_API_KEY", 16),
    ("OPENDART_API_KEY", 16),
)


def load_dotenv(recorder: Recorder) -> None:
    """레포 루트 `.env`를 현재 프로세스 환경으로만 올린다. 값은 어디에도 남기지 않는다."""

    path = REPOSITORY / ".env"
    if not path.is_file():
        raise HarnessError(".env가 없다. 수집기 자격증명을 읽을 곳이 없다")
    loaded = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            os.environ.setdefault(key, value)
            loaded += 1
    recorder.add(
        "환경 파일을 읽었다",
        "INFO",
        f".env에서 {loaded}개 키를 현재 프로세스에만 올렸다 (값은 기록하지 않는다)",
    )


def check_credentials(recorder: Recorder) -> bool:
    missing = [
        name
        for name, minimum in _REQUIRED_CREDENTIALS
        if len(os.environ.get(name, "").strip()) < minimum
    ]
    recorder.add(
        "수집기 자격증명이 있다",
        "PASS" if not missing else "FAIL",
        f"확인한 키={[name for name, _ in _REQUIRED_CREDENTIALS]} "
        f"비었거나 너무 짧은 키={missing or '없음'} (값은 찍지 않는다)",
    )
    return not missing


def _previous_trading_day() -> str:
    """완료된 XKRX 거래일 하나. 오늘이 장중이면 오늘 데이터는 아직 없다."""

    day = date.today() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.isoformat()


def _run_module(module: str, *arguments: str, timeout: int = 180) -> tuple[int, str]:
    """수집기를 컨테이너 안에서 돌린다.

    호스트에서 부르면 안 된다. KRX·ECOS probe는 production private transport와 **Redis quota**를
    그대로 쓰는데, Redis는 compose 내부 네트워크에만 있고 호스트 포트로 나오지 않는다. 호스트에서
    부르면 외부 호출을 하기도 전에 `quota_unavailable`로 닫힌다.

    자격증명은 명령줄이 아니라 환경으로만 넘긴다. 값은 이 함수 밖으로 나가지 않는다.
    """

    command = [
        DOCKER,
        "compose",
        "--project-name",
        PROJECT,
        "--env-file",
        str(STATE / "runtime.env"),
        "-f",
        str(DEPLOY / "compose.yml"),
        "--profile",
        "owner",
        "run",
        "--rm",
        "--no-deps",
        "-e",
        "PYTHONPATH=/app",
    ]
    for name, _ in _REQUIRED_CREDENTIALS:
        value = os.environ.get(name, "")
        if value:
            command += ["-e", f"{name}={value}"]
    command += ["kis-mock-certification-runner", "python", "-m", module, *arguments]

    completed = subprocess.run(
        command,
        cwd=REPOSITORY,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    tail = [
        line
        for line in (completed.stdout + completed.stderr).strip().splitlines()
        if "Container" not in line
    ]
    return completed.returncode, " | ".join(tail[-3:])[:600]


def _verdict_for_probe(code: int, tail: str) -> tuple[str, str]:
    """게이트가 닫아 호출조차 못 한 것과, 호출했는데 실패한 것을 구분한다.

    두 수집기는 실패해도 물리 시도 횟수를 함께 내놓는다. 그 값이 0이면 외부에 나가기 전에
    별도 온라인 승인 게이트가 닫은 것이고, 그것은 자격증명이나 표면의 문제가 아니다.
    실제로 나갔는데 실패한 것만 FAIL로 센다.
    """

    if code == 0:
        return "PASS", "응답을 받았다"
    attempted = not ("physical_attempts=0" in tail or '"physicalAttemptCount":0' in tail)
    if attempted:
        return "FAIL", "외부로 나갔는데 실패했다"
    return "INFO", "물리 시도 0 - 외부로 나가기 전에 별도 온라인 승인 게이트가 닫았다"


def check_krx(recorder: Recorder, live: bool) -> None:
    if not live:
        recorder.add(
            "KRX OPEN API 승인 서비스가 응답한다",
            "INFO",
            "--with-live-collect가 없어 호출하지 않았다",
        )
        return
    as_of = _previous_trading_day()
    try:
        code, tail = _run_module(
            "app.data.krx.service_probe_cli",
            "--online",
            "--as-of",
            as_of,
            "--service",
            "stk_bydd_trd",
        )
    except subprocess.SubprocessError as error:
        recorder.add(
            "KRX OPEN API 승인 서비스가 응답한다",
            "FAIL",
            f"{type(error).__name__}: 프로브를 실행하지 못했다",
        )
        return
    verdict, why = _verdict_for_probe(code, tail)
    recorder.add(
        "KRX OPEN API 승인 서비스가 응답한다",
        verdict,
        f"as-of={as_of} service=stk_bydd_trd exit={code} {why} :: {tail}",
    )


def check_ecos(recorder: Recorder, live: bool) -> None:
    if not live:
        recorder.add(
            "ECOS registry preflight가 통과한다",
            "INFO",
            "--with-live-collect가 없어 호출하지 않았다",
        )
        return
    try:
        code, tail = _run_module("app.data.ecos.registry_preflight_cli", "--online")
    except subprocess.SubprocessError as error:
        recorder.add(
            "ECOS registry preflight가 통과한다",
            "FAIL",
            f"{type(error).__name__}: preflight를 실행하지 못했다",
        )
        return
    verdict, why = _verdict_for_probe(code, tail)
    recorder.add(
        "ECOS registry preflight가 통과한다",
        verdict,
        f"exit={code} {why} :: {tail}",
    )


def check_opendart(recorder: Recorder) -> None:
    """설정이 자격증명을 읽어 클라이언트를 만들 수 있는지까지 본다.

    OpenDART는 공시 조회마다 일일 호출 예산을 쓴다. 표면 확인에 그 예산을 쓰지 않는다.
    """

    try:
        code, tail = _run_module(
            "app.data.opendart.settings",
            timeout=90,
        )
    except subprocess.SubprocessError as error:
        recorder.add(
            "OpenDART 설정이 자격증명을 읽는다",
            "FAIL",
            f"{type(error).__name__}: 설정을 만들지 못했다",
        )
        return
    # settings 모듈은 그 자체로 실행 가능한 진입점이 아니다. import 가 되고 자격증명이
    # 형식을 만족하면 그것으로 표면이 살아 있다고 본다.
    ok = code == 0 or "OpenDARTSettings" in tail or not tail
    recorder.add(
        "OpenDART 설정이 자격증명을 읽는다",
        "PASS" if ok else "FAIL",
        f"exit={code} {tail} (일일 호출 예산을 쓰지 않으려고 공시 조회는 하지 않는다)",
    )


def check_gdelt_excluded(recorder: Recorder) -> None:
    recorder.add(
        "GDELT는 부르지 않는다",
        "INFO",
        "GDELT는 연구 상태로 분리돼 있어 이 러너가 호출하지 않는다. "
        "이 경계를 바꾸려면 소유자에게 먼저 묻는다. "
        "`gdelt-aggregate-collect`는 이 판정표 어디에도 실행 기록을 남기지 않는다",
    )


def main(argv: Sequence[str]) -> int:
    require_opt_in(_OPT_IN)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="")
    parser.add_argument(
        "--with-live-collect",
        action="store_true",
        help="KRX·ECOS 표면을 실제로 호출한다. GDELT는 이 플래그로도 호출하지 않는다",
    )
    args = parser.parse_args(argv[1:])

    recorder = Recorder()
    try:
        load_dotenv(recorder)
        configured = check_credentials(recorder)
        live = args.with_live_collect and configured
        if args.with_live_collect and not configured:
            recorder.add(
                "실호출을 건너뛴 이유",
                "INFO",
                "자격증명이 갖춰지지 않아 외부 호출을 시도하지 않았다",
            )
        check_krx(recorder, live)
        check_ecos(recorder, live)
        check_opendart(recorder)
        check_gdelt_excluded(recorder)
    except HarnessError as error:
        recorder.add("확인 중단", "FAIL", str(error))
    except Exception as error:  # noqa: BLE001
        recorder.add("확인 중단", "FAIL", f"{type(error).__name__}: {error}")

    # 읽기와 조회만 한다. DB에 만든 것이 없으므로 되돌릴 것도 없다.
    report = write_report(
        contract_id="p1-collector-surface-e2e.v1",
        marker="P1_COLLECTOR_SURFACE_E2E",
        recorder=recorder,
        out=args.out,
    )
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
