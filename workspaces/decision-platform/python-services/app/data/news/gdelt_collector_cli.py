"""승인된 물리 상한 안에서 GQG/GEMG 최신 또는 bounded 복구 target을 한 번 처리한다."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime, timedelta
from itertools import chain
from typing import Iterable

from app.data.news.gdelt_collector import (
    GdeltCycleReceipt,
    GdeltFileTarget,
    backfill_targets,
    latest_targets,
    run_cycle,
)
from app.data.news.gdelt_transport import GdeltHttpClient
from app.data.news.repository import PostgresWorldNewsRepository


#: 이 시간이 지난 뒤에도 404 인 분은 앞으로도 생기지 않는다고 본다.
#: 공식 문서의 생성 지연이 "2~5분"이므로 넉넉히 잡는다.
_PUBLICATION_SETTLE = timedelta(minutes=30)


def main() -> int:
    parser = argparse.ArgumentParser(description="bounded GDELT world-news minute collector")
    parser.add_argument("--lookback-hours", type=int, default=0)
    parser.add_argument("--max-files", type=int, default=2)
    parser.add_argument("--physical-cap", type=int, default=6)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=60)
    args = parser.parse_args()
    if os.environ.get("GDELT_WORLD_NEWS_ENABLED", "false").lower() != "true":
        raise SystemExit("GDELT_WORLD_NEWS_DISABLED")
    if args.lookback_hours not in range(0, 24 * 30 + 1):
        raise SystemExit("GDELT_LOOKBACK_INVALID")
    if (
        args.max_files not in range(1, 2_881)
        or args.physical_cap not in range(1, 8_641)
        or args.interval_seconds not in range(30, 3_601)
        or args.physical_cap > args.max_files * 3
    ):
        raise SystemExit("GDELT_EXECUTION_CAP_INVALID")
    dsn = os.environ.get("MARKET_DATA_WRITER_DSN", "")
    repository = PostgresWorldNewsRepository(dsn)
    next_tick = time.monotonic()
    while True:
        now = datetime.now(UTC)
        targets = _select_targets(
            repository,
            now=now,
            lookback_hours=args.lookback_hours,
            max_files=args.max_files,
        )
        receipts = _run_datasets(
            repository,
            targets=targets,
            started_at=now,
            physical_cap=args.physical_cap,
        )
        projection = _receipt_projection(receipts)
        print(json.dumps(projection, sort_keys=True), flush=True)
        if not args.loop:
            return 1 if _has_actionable_failure(receipts) else 0
        # 실행시간을 sleep에 더하지 않고 최초 monotonic cadence에 맞춰 다음 tick을 잡는다.
        next_tick += args.interval_seconds
        time.sleep(max(0.0, next_tick - time.monotonic()))


def _select_targets(
    repository: PostgresWorldNewsRepository,
    *,
    now: datetime,
    lookback_hours: int,
    max_files: int,
) -> tuple[GdeltFileTarget, ...]:
    """latest 두 dataset을 먼저 확보하고 완료 cursor를 건너뛴 뒤에만 bounded recovery를 자른다."""

    selected: list[GdeltFileTarget] = []
    seen: set[tuple[str, str]] = set()
    source: Iterable[GdeltFileTarget] = latest_targets(now)
    if lookback_hours:
        source = chain(
            source,
            backfill_targets(now, lookback=timedelta(hours=lookback_hours)),
        )
    candidates = [target for target in source if _first_seen(target, seen)]
    # GQG/GEMG 는 15분 heartbeat 라 대부분의 분에 파일이 없다. 게시 유예가 지난 뒤에도
    # 404 인 분은 앞으로도 생기지 않으므로 다시 묻지 않는다. 이것을 거르지 않으면 같은
    # 빈 분들이 매 실행 예산을 먹어 공백 구간을 건널 호출이 남지 않는다.
    settled_before = now.astimezone(UTC) - _PUBLICATION_SETTLE
    settled: dict[str, frozenset[str]] = {}
    lookup = getattr(repository, "unpublished_cursors", None)
    if callable(lookup):
        for provider in {target.provider for target in candidates}:
            cursors = tuple(
                target.cursor_sha256
                for target in candidates
                if target.provider == provider and target.minute < settled_before
            )
            settled[provider] = lookup(provider=provider, cursors=cursors)
    for target in candidates:
        if target.cursor_sha256 in settled.get(target.provider, frozenset()):
            continue
        if repository.was_completed(provider=target.provider, cursor_sha256=target.cursor_sha256):
            continue
        selected.append(target)
        if len(selected) == max_files:
            break
    return tuple(selected)


def _first_seen(target: GdeltFileTarget, seen: set[tuple[str, str]]) -> bool:
    identity = (target.provider, target.cursor_sha256)
    if identity in seen:
        return False
    seen.add(identity)
    return True


def _run_datasets(
    repository: PostgresWorldNewsRepository,
    *,
    targets: tuple[GdeltFileTarget, ...],
    started_at: datetime,
    physical_cap: int,
) -> tuple[GdeltCycleReceipt, ...]:
    """GQG terminal 실패가 GEMG의 독립 예산과 cursor를 굶기지 않게 dataset별로 실행한다."""

    groups = tuple(
        tuple(target for target in targets if target.dataset == dataset)
        for dataset in ("GQG", "GEMG")
    )
    non_empty = tuple(group for group in groups if group)
    if not non_empty:
        return ()
    base, remainder = divmod(physical_cap, len(non_empty))
    receipts: list[GdeltCycleReceipt] = []
    for index, group in enumerate(non_empty):
        dataset_cap = min(len(group) * 3, base + int(index < remainder))
        if dataset_cap < 1:
            continue
        with GdeltHttpClient(network_enabled=True, physical_call_cap=dataset_cap) as client:
            receipts.append(
                run_cycle(
                    targets=group,
                    client=client,
                    repository=repository,
                    started_at=started_at,
                    stop_after_failure=True,
                )
            )
    return tuple(receipts)


def _receipt_projection(receipts: tuple[GdeltCycleReceipt, ...]) -> dict[str, object]:
    fields = (
        "attempted_files",
        "completed_files",
        "excluded_rows",
        "failed_files",
        "physical_calls",
        "received_raw_bytes",
        "reused_files",
        "rows_seen",
        "stored_documents",
    )
    result: dict[str, object] = {
        _camel(field): sum(getattr(receipt, field) for receipt in receipts) for field in fields
    }
    result["failureCodes"] = [code for receipt in receipts for code in receipt.failure_codes]
    result["stoppedAfterFailure"] = any(receipt.stopped_after_failure for receipt in receipts)
    return result


def _has_actionable_failure(receipts: tuple[GdeltCycleReceipt, ...]) -> bool:
    """15분 heartbeat의 미게시 분은 기록하되 one-shot 실패로 세지 않는다."""

    return any(
        code != "GDELT_NOT_PUBLISHED" for receipt in receipts for code in receipt.failure_codes
    )


def _camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(item.title() for item in tail)


if __name__ == "__main__":
    raise SystemExit(main())
