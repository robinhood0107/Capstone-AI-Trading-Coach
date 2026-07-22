from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from app.data.calendar.collector import (
    CalendarCollector,
    CollectionTask,
    CollectorRunLedger,
    OpenDARTAttemptExecutor,
    OperationPriority,
    degradation_allows,
    deterministic_round_robin,
    priority_for_operation,
)
from app.data.calendar.errors import (
    CollectorAlreadyRunning,
    NonRetryableProviderError,
    ProviderQuotaExhausted,
    RetryableProviderError,
    RunLimitExceeded,
)
from app.data.calendar.settings import OpenDARTQuotaConfig


def test_operation_priority_mapping_is_closed_and_exact() -> None:
    assert priority_for_operation("list") is OperationPriority.P1
    assert priority_for_operation("corpCode") is OperationPriority.P1
    assert priority_for_operation("bnkMngtPcbg") is OperationPriority.P1
    assert priority_for_operation("bnkMngtPcsp") is OperationPriority.P1
    assert priority_for_operation("cmpMgDecsn") is OperationPriority.P2
    assert priority_for_operation("majorstock") is OperationPriority.P3
    assert priority_for_operation("company") is OperationPriority.P4
    with pytest.raises(ValueError, match="unmapped"):
        priority_for_operation("unknown-endpoint")


@pytest.mark.parametrize(
    "used, budget, allowed",
    [
        (69, 100, {1, 2, 3, 4}),
        (70, 100, {1, 2, 3}),
        (89, 100, {1, 2, 3}),
        (90, 100, {1}),
        (100, 100, set()),
    ],
)
def test_budget_degradation_uses_exact_70_and_90_percent_boundaries(
    used: int,
    budget: int,
    allowed: set[int],
) -> None:
    actual = {
        priority.value
        for priority in OperationPriority
        if degradation_allows(priority, physical_attempts=used, daily_budget=budget)
    }
    assert actual == allowed


def test_each_retry_acquires_bucket_and_charged_reservation_before_send() -> None:
    events: list[str] = []
    attempts = 0

    def send() -> dict[str, str]:
        nonlocal attempts
        attempts += 1
        events.append(f"send-{attempts}")
        if attempts < 3:
            raise RetryableProviderError(503)
        return {"status": "000"}

    executor = OpenDARTAttemptExecutor(
        bucket=_Bucket(events),
        quota_repository=_Quota(events),
        config=_config(calls=3),
        ledger=CollectorRunLedger(max_calls=3),
        now=lambda: datetime(2026, 7, 22, 0, 0, tzinfo=UTC),
    )

    assert executor.execute("list", send) == {"status": "000"}
    assert events == [
        "bucket",
        "reserve-1",
        "send-1",
        "bucket",
        "reserve-2",
        "send-2",
        "bucket",
        "reserve-3",
        "send-3",
    ]
    assert executor.ledger.charged_reservations == 3
    assert executor.ledger.actual_http_sends == 3


@pytest.mark.parametrize("error", [NonRetryableProviderError(429), ProviderQuotaExhausted()])
def test_429_and_status_020_never_retry(error: Exception) -> None:
    events: list[str] = []
    quota = _Quota(events)

    def send() -> dict[str, Any]:
        events.append("send")
        raise error

    executor = OpenDARTAttemptExecutor(
        bucket=_Bucket(events),
        quota_repository=quota,
        config=_config(calls=3),
        ledger=CollectorRunLedger(max_calls=3),
        now=lambda: datetime(2026, 7, 22, 0, 0, tzinfo=UTC),
    )

    with pytest.raises(type(error)):
        executor.execute("list", send)
    assert events.count("send") == 1
    assert quota.exhausted == isinstance(error, ProviderQuotaExhausted)


def test_db_reservation_failure_causes_zero_http_send() -> None:
    events: list[str] = []
    quota = _Quota(events, deny=True)
    executor = OpenDARTAttemptExecutor(
        bucket=_Bucket(events),
        quota_repository=quota,
        config=_config(calls=1),
        ledger=CollectorRunLedger(max_calls=1),
        now=lambda: datetime(2026, 7, 22, 0, 0, tzinfo=UTC),
    )

    with pytest.raises(RunLimitExceeded, match="reservation"):
        executor.execute("list", lambda: events.append("send"))
    assert "send" not in events
    assert executor.ledger.actual_http_sends == 0


def test_collector_requires_single_advisory_lock_and_status_020_stops_queue() -> None:
    calls: list[str] = []
    lock = _Lock(acquired=False)
    collector = CalendarCollector(lock=lock, executor=_FakeExecutor(calls), config=_config(calls=5))
    tasks = [_task("005930", 1), _task("000660", 1)]

    with pytest.raises(CollectorAlreadyRunning):
        collector.run(tasks)
    assert calls == []

    lock.acquired = True
    calls.clear()
    executor = _FakeExecutor(calls, quota_on="005930:1")
    collector = CalendarCollector(lock=lock, executor=executor, config=_config(calls=5))
    with pytest.raises(ProviderQuotaExhausted):
        collector.run(tasks)
    assert calls == ["000660:1", "005930:1"]


def test_round_robin_is_subject_sorted_one_page_at_a_time_and_caps_symbols() -> None:
    tasks = [
        _task("005930", 2),
        _task("035420", 1),
        _task("000660", 2),
        _task("005930", 1),
        _task("000660", 1),
    ]
    ordered = deterministic_round_robin(tasks, max_symbols=2)
    assert [(task.subject, task.page) for task in ordered] == [
        ("000660", 1),
        ("005930", 1),
        ("000660", 2),
        ("005930", 2),
    ]


@dataclass
class _Bucket:
    events: list[str]

    def acquire(self) -> None:
        self.events.append("bucket")


class _Quota:
    def __init__(self, events: list[str], *, deny: bool = False) -> None:
        self.events = events
        self.deny = deny
        self.reservations = 0
        self.exhausted = False

    def reserve(self, *_: object) -> None:
        self.reservations += 1
        self.events.append(f"reserve-{self.reservations}")
        if self.deny:
            raise RuntimeError("ambiguous reservation")

    def mark_exhausted(self, *_: object) -> None:
        self.exhausted = True


@dataclass
class _Lock:
    acquired: bool

    def acquire_collector_lock(self) -> bool:
        return self.acquired

    def release_collector_lock(self) -> None:
        pass


class _FakeExecutor:
    def __init__(self, calls: list[str], *, quota_on: str | None = None) -> None:
        self.calls = calls
        self.quota_on = quota_on

    def execute(self, _: str, send: object) -> object:
        assert callable(send)
        result = send()
        if result == self.quota_on:
            raise ProviderQuotaExhausted()
        return result


def _task(subject: str, page: int) -> CollectionTask:
    identity = f"{subject}:{page}"
    return CollectionTask(
        operation="list",
        subject=subject,
        page=page,
        send=lambda: identity,
    )


def _config(*, calls: int) -> OpenDARTQuotaConfig:
    return OpenDARTQuotaConfig(
        daily_call_limit=100,
        daily_call_budget=80,
        max_calls_per_run=calls,
        max_symbols_per_run=2,
    )
