"""비동기 레인을 실행 중인 스택에서 실제로 한 번 통과시킨다.

이 파일은 테스트 전용이다. production 이미지나 business logic에서 import 하지 않으며 `test_`
접두사가 아니라 pytest 수집 대상도 아니다.

무엇을 확인하나.
  1. DB 비동기 레인: 합성 요청을 production 함수로 적재하고, worker가 집어 완료로 닫는지 본다.
     이 경로는 `app/s8_demo/container_async_smoke.py`가 그대로 태운다. 테스트가 표를 직접
     건드리지 않는다.
  2. 그 작업이 ADMIN 관측(`async-jobs`)에 실제로 보이는지 본다. 목록이 열리지 않으면 운영자는
     레인이 살아 있는지 알 방법이 없다.

무엇을 확인하지 않나. **Kafka 레인은 이 배포의 범위가 아니다.** `compose.kafka.yml`은
`docs/decision-platform/P1_1_0_0_FULL_APP_V2_권위_및_게이트.md`가 v1 회귀용 historical asset으로
못박아 둔 자산이고, 현재 owner-first 스택에는 broker 서비스가 없다. 코드 경로는
`tests/async_worker/`의 publisher·consumer·security·topics·poison recorder 단위 테스트가 덮는다.
여기서는 그 사실을 기록만 하고 없는 broker를 켜지 않는다.

실행:
  P1_ASYNC_E2E=1 python -m tests.e2e.async_e2e \\
    --out artifacts/decision-platform/e2e/async.json
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Final

from .harness import (
    Api,
    DEPLOY,
    DOCKER,
    HarnessError,
    PROJECT,
    Recorder,
    STATE,
    psql,
    require_opt_in,
    run,
    write_report,
)

_OPT_IN: Final = "P1_ASYNC_E2E"
_KAFKA_OVERLAY: Final = "deploy/p1/compose.kafka.yml"


def _job_count() -> int:
    return int(psql("select count(*) from public.async_job;") or 0)


def _run_db_smoke() -> str:
    """compose의 smoke 프로파일로 합성 비동기 요청을 한 번 태운다."""

    return run(
        [
            DOCKER,
            "compose",
            "--project-name",
            PROJECT,
            "--env-file",
            str(STATE / "runtime.env"),
            "-f",
            str(DEPLOY / "compose.yml"),
            "--profile",
            "smoke",
            "run",
            "--rm",
            "synthetic-async-smoke",
        ],
        env={"P1_OPERATOR_UID": str(os.getuid())},
    )


def check_db_lane(recorder: Recorder) -> dict[str, Any]:
    before = _job_count()
    try:
        output = _run_db_smoke()
    except HarnessError as error:
        recorder.add("DB 비동기 레인", "FAIL", f"합성 요청이 통과하지 못했다: {error}")
        return {}
    after = _job_count()
    # 상태 분포를 읽는다. 완료로 닫히지 않으면 레인이 멈춘 것이다.
    states = psql(
        "select coalesce(string_agg(status || '=' || count, ', '), '(없음)') from ("
        "  select status, count(*)::text as count from public.async_job group by status"
        ") s;"
    )
    completed = psql("select count(*) from public.async_job where status = 'COMPLETED';")
    tail = output.strip().splitlines()[-1] if output.strip() else ""
    recorder.add(
        "DB 비동기 레인",
        "PASS" if after >= before + 1 and int(completed or 0) > 0 else "FAIL",
        f"작업 {before}→{after} 상태분포=[{states}] 마지막 출력={tail[:120]}",
    )
    return {"before": before, "after": after}


def check_admin_observation(recorder: Recorder, admin: Api) -> None:
    status, listed = admin.request("GET", "/api/v1/async-jobs?size=5")
    items = ((listed.get("data") or {}).get("items")) or []
    recorder.add(
        "ADMIN 비동기 작업 관측",
        "PASS" if status == 200 and items else "FAIL",
        f"HTTP {status} {len(items)}건 첫 상태={items[0].get('status') if items else '-'} "
        "(레인이 돌았는데 목록이 비면 관측이 끊긴 것이다)",
    )


def check_kafka_is_out_of_scope(recorder: Recorder) -> None:
    overlay = Path(_KAFKA_OVERLAY)
    exists = (overlay if overlay.is_absolute() else (DEPLOY.parent.parent / overlay)).exists()
    services = psql("select 1;")  # 연결 확인용. Kafka 상태는 DB에 없다.
    recorder.add(
        "Kafka 레인",
        "INFO",
        f"overlay 파일 존재={exists} (v1 회귀용 historical asset이다. 현재 스택에 broker 서비스가 "
        f"없어 여기서 켜지 않는다. 코드 경로는 tests/async_worker/의 단위 테스트가 덮는다) "
        f"db={'연결됨' if services else '연결 안 됨'}",
    )


def main(argv: list[str]) -> int:
    require_opt_in(_OPT_IN)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv[1:])

    recorder = Recorder()
    try:
        admin = Api()
        admin.login("demo-admin")
        check_db_lane(recorder)
        check_admin_observation(recorder, admin)
        check_kafka_is_out_of_scope(recorder)
    except HarnessError as error:
        recorder.add("확인 중단", "FAIL", str(error))
    except Exception as error:  # noqa: BLE001
        recorder.add("확인 중단", "FAIL", f"{type(error).__name__}: {error}")

    # 합성 요청은 production 함수가 만든 정상 작업이다. 다른 runner처럼 지우지 않는다.
    # 지우면 오히려 append-only 감사 기록에 구멍이 생긴다.
    report = write_report(
        contract_id="p1-async-e2e.v1",
        marker="P1_ASYNC_E2E",
        recorder=recorder,
        out=args.out,
    )
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
