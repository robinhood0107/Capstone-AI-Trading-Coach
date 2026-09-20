"""30일이 지나 앞으로 쓰일 일이 없는 세계 뉴스 버전을 실제로 지운다.

`PostgresWorldNewsRetention` 과 DB 함수 `p1_world_news_retention_v1` 은 V157 부터
있었지만 **프로덕션에서 아무도 부르지 않았다.** 그래서 톰스톤이 0 건이었고 코퍼스는
유입 속도 그대로 무한히 자랐다 - 2026-09-14 하루에 15 만 행이 쌓였다.

지우는 대상은 `first_seen_at` 이 30일보다 오래됐고 **살아 있는 인용 핀이 없는** 버전뿐이다.
답변에 인용된 근거는 핀이 만료되기 전까지 보호되고, 지운 뒤에도 톰스톤에 출처 해시가
남아 "있었는데 보존기간이 지나 지웠다"를 구분할 수 있다.

capability 경계는 그대로다 - 이 함수는 `decision_worker` 로만 실행되고, 그 DSN 은
이미 이 컨테이너에 있다. 새 secret 도 새 권한도 만들지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime

from app.data.news.repository import PostgresWorldNewsRetention

#: 한 번에 지우는 최대 버전 수. DB 함수의 상한(1000)과 같다. 한 트랜잭션을 길게 잡으면
#: 수집 쓰기와 오래 부딪히므로 작은 배치를 자주 도는 편을 택한다.
_BATCH_LIMIT = 1_000

#: 배치 사이 간격. 지울 것이 남아 있으면 곧바로 다음 배치를 돌되, 다 지웠으면 이 주기로
#: 쉰다. 보존은 30일 경계라 분 단위 정확도가 필요 없다.
_IDLE_INTERVAL_SECONDS = 3_600

#: 한 cycle 이 연속으로 도는 최대 배치 수. 밀린 양이 많아도 다른 작업을 굶기지 않는다.
_MAX_BATCHES_PER_CYCLE = 20


def _run_cycle(retention: PostgresWorldNewsRetention, *, apply: bool) -> dict[str, int]:
    totals = {"scanned": 0, "eligible": 0, "pinned": 0, "deleted": 0, "batches": 0}
    for _ in range(_MAX_BATCHES_PER_CYCLE):
        receipt = retention.run(apply=apply, now=datetime.now(UTC), limit=_BATCH_LIMIT)
        totals["batches"] += 1
        totals["scanned"] = receipt["scanned"]
        totals["pinned"] = receipt["pinned"]
        totals["eligible"] += receipt["eligible"]
        totals["deleted"] += receipt["deleted"]
        if not apply or receipt["deleted"] == 0:
            break
    return totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=_IDLE_INTERVAL_SECONDS)
    args = parser.parse_args(argv)
    if args.interval_seconds not in range(60, 86_401):
        print("WORLD_NEWS_RETENTION=INVALID_INTERVAL", file=sys.stderr)
        return 2

    dsn = os.environ.get("ASYNC_WORKER_DATABASE_DSN", "")
    if not dsn.strip():
        print("WORLD_NEWS_RETENTION=MISSING_DSN", file=sys.stderr)
        return 2

    retention = PostgresWorldNewsRetention(dsn)
    while True:
        try:
            totals = _run_cycle(retention, apply=not args.dry_run)
            print(json.dumps({"worldNewsRetention": totals}, sort_keys=True), flush=True)
        except (ValueError, OSError) as error:
            # 보존은 운용을 막지 않는다. 사유만 남기고 다음 주기에 다시 시도한다.
            print(
                f"WORLD_NEWS_RETENTION=FAILED error={type(error).__name__}",
                file=sys.stderr,
                flush=True,
            )
        if not args.loop:
            return 0
        time.sleep(args.interval_seconds)


if __name__ == "__main__":  # pragma: no cover - 컨테이너 진입점
    raise SystemExit(main())
