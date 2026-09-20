"""보존이 실제로 돌고, 지울 것이 없으면 멈추고, 실패해도 죽지 않는지 고정한다.

DB 함수와 어댑터는 V157 부터 있었지만 **프로덕션에서 아무도 부르지 않아** 톰스톤이
0 건이었다. 기능이 "있다"와 "동작한다"는 다르다 - 그 차이를 여기서 잠근다.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.data.news import retention_cli


class _Retention:
    """배치마다 정해진 영수증을 돌려주는 대역."""

    def __init__(self, receipts: list[dict[str, int]]) -> None:
        self.receipts = receipts
        self.calls: list[dict[str, Any]] = []

    def run(self, *, apply: bool = False, now: Any = None, limit: int = 1000) -> dict[str, int]:
        self.calls.append({"apply": apply, "limit": limit})
        if self.receipts:
            return self.receipts.pop(0)
        return {"scanned": 0, "eligible": 0, "pinned": 0, "deleted": 0}


def test_a_cycle_keeps_deleting_until_nothing_is_left() -> None:
    """한 배치는 최대 1000 건이다. 밀린 양이 많으면 이어서 지워야 한다."""

    retention = _Retention(
        [
            {"scanned": 2_500, "eligible": 1_000, "pinned": 3, "deleted": 1_000},
            {"scanned": 1_500, "eligible": 1_000, "pinned": 3, "deleted": 1_000},
            {"scanned": 500, "eligible": 500, "pinned": 3, "deleted": 500},
            {"scanned": 0, "eligible": 0, "pinned": 3, "deleted": 0},
        ]
    )

    totals = retention_cli._run_cycle(retention, apply=True)

    assert totals["deleted"] == 2_500
    assert totals["batches"] == 4
    assert all(call["apply"] is True for call in retention.calls)


def test_a_cycle_stops_at_the_batch_ceiling() -> None:
    """밀린 양이 아무리 많아도 한 cycle 이 무한히 돌지 않는다."""

    retention = _Retention(
        [{"scanned": 10**6, "eligible": 1_000, "pinned": 0, "deleted": 1_000}] * 100
    )

    totals = retention_cli._run_cycle(retention, apply=True)

    assert totals["batches"] == retention_cli._MAX_BATCHES_PER_CYCLE


def test_a_dry_run_never_deletes_and_never_loops() -> None:
    retention = _Retention([{"scanned": 9, "eligible": 9, "pinned": 0, "deleted": 0}])

    totals = retention_cli._run_cycle(retention, apply=False)

    assert totals["deleted"] == 0
    assert totals["batches"] == 1
    assert retention.calls[0]["apply"] is False


def test_a_missing_dsn_fails_loudly_instead_of_pretending_to_run() -> None:
    """조용히 아무것도 안 하는 것이 이 사건의 본질이었다. 사유를 내고 멈춘다."""

    assert retention_cli.main(["--dry-run"]) == 2


def test_a_failing_cycle_does_not_kill_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """보존 실패가 컨테이너를 죽이면 자동 운용까지 같이 멈춘다."""

    monkeypatch.setenv("ASYNC_WORKER_DATABASE_DSN", "postgresql://decision_worker:x@db/p1")

    def _boom(*_args: object, **_kwargs: object) -> dict[str, int]:
        raise ValueError("world-news retention capability is unavailable")

    monkeypatch.setattr(retention_cli, "_run_cycle", _boom)

    assert retention_cli.main([]) == 0
