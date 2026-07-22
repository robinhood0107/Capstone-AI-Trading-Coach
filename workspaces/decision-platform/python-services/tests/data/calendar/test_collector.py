from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

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
    PriorityDeferred,
    ProviderQuotaExhausted,
    RunLimitExceeded,
)
from app.data.calendar.models import QuotaUsage
from app.data.calendar.settings import OpenDARTQuotaConfig
from app.data.opendart.parsers import OpenDARTQuotaExceededError


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


def test_each_transport_retry_gets_a_fresh_charged_reservation_and_handoff() -> None:
    events: list[str] = []
    executor = OpenDARTAttemptExecutor(
        quota_repository=_Quota(events),
        config=_config(calls=3),
        ledger=CollectorRunLedger(max_calls=3),
        now=lambda: datetime(2026, 7, 22, 0, 0, tzinfo=UTC),
    )

    for attempt in range(1, 4):
        events.append("limiter")
        executor.before_send("/api/list.json")
        executor.record_http_handoff()
        events.append(f"send-{attempt}")

    assert events == [
        "limiter",
        "reserve-1",
        "send-1",
        "limiter",
        "reserve-2",
        "send-2",
        "limiter",
        "reserve-3",
        "send-3",
    ]
    assert executor.ledger.charged_reservations == 3
    assert executor.ledger.actual_http_sends == 3


def test_status_020_marks_the_last_reserved_kst_day_exhausted() -> None:
    events: list[str] = []
    quota = _Quota(events)
    executor = OpenDARTAttemptExecutor(
        quota_repository=quota,
        config=_config(calls=3),
        ledger=CollectorRunLedger(max_calls=3),
        now=lambda: datetime(2026, 7, 22, 0, 0, tzinfo=UTC),
    )

    executor.before_send("/api/list.json")
    executor.record_http_handoff()
    executor.mark_provider_exhausted()

    assert quota.exhausted is True


def test_attempt_executor_returns_success_and_translates_both_020_shapes() -> None:
    events: list[str] = []
    quota = _Quota(events)
    executor = OpenDARTAttemptExecutor(
        quota_repository=quota,
        config=_config(calls=3),
        ledger=CollectorRunLedger(max_calls=3),
        now=lambda: datetime(2026, 7, 22, 0, 0, tzinfo=UTC),
    )

    executor.before_send("/api/list.json")
    assert executor.execute("list", lambda: {"status": "000"}) == {"status": "000"}

    executor.before_send("/api/list.json")
    with pytest.raises(ProviderQuotaExhausted):
        executor.execute("list", lambda: {"status": "020"})
    assert quota.exhausted is True

    executor.before_send("/api/list.json")

    def raise_quota() -> object:
        raise OpenDARTQuotaExceededError("020", "fixture quota exhausted")

    with pytest.raises(ProviderQuotaExhausted):
        executor.execute("list", raise_quota)


def test_attempt_executor_caps_reservations_and_rejects_unmatched_handoff() -> None:
    events: list[str] = []
    quota = _Quota(events)
    executor = OpenDARTAttemptExecutor(
        quota_repository=quota,
        config=_config(calls=1),
        ledger=CollectorRunLedger(max_calls=1),
        now=lambda: datetime(2026, 7, 22, 0, 0, tzinfo=UTC),
    )

    with pytest.raises(RuntimeError, match="charged reservation"):
        executor.record_http_handoff()
    with pytest.raises(RunLimitExceeded, match="no charged reservation"):
        executor.mark_provider_exhausted()

    executor.before_send("/api/list.json")
    with pytest.raises(RunLimitExceeded, match="per-run"):
        executor.before_send("/api/list.json")
    assert quota.reservations == 1
    assert executor.ledger.actual_http_sends == 0


def test_real_attempt_executor_020_stops_remaining_collector_queue() -> None:
    quota_events: list[str] = []
    provider_sends: list[str] = []
    quota = _Quota(quota_events)
    executor = OpenDARTAttemptExecutor(
        quota_repository=quota,
        config=_config(calls=3),
        ledger=CollectorRunLedger(max_calls=3),
        now=lambda: datetime(2026, 7, 22, 0, 0, tzinfo=UTC),
    )

    def send(subject: str, status: str) -> object:
        executor.before_send("/api/list.json")
        executor.record_http_handoff()
        provider_sends.append(subject)
        return {"status": status}

    collector = CalendarCollector(
        lock=_Lock(acquired=True),
        executor=executor,
        config=_config(calls=3),
    )
    tasks = [
        CollectionTask(
            operation="list",
            subject="000660",
            page=1,
            send=lambda: send("000660", "020"),
        ),
        CollectionTask(
            operation="list",
            subject="005930",
            page=1,
            send=lambda: send("005930", "000"),
        ),
    ]

    with pytest.raises(ProviderQuotaExhausted):
        collector.run(tasks)

    assert provider_sends == ["000660"]
    assert executor.ledger.charged_reservations == 1
    assert executor.ledger.actual_http_sends == 1
    assert quota.exhausted is True


def test_db_reservation_failure_causes_zero_http_send() -> None:
    events: list[str] = []
    quota = _Quota(events, deny=True)
    executor = OpenDARTAttemptExecutor(
        quota_repository=quota,
        config=_config(calls=1),
        ledger=CollectorRunLedger(max_calls=1),
        now=lambda: datetime(2026, 7, 22, 0, 0, tzinfo=UTC),
    )

    with pytest.raises(RunLimitExceeded, match="reservation"):
        executor.before_send("/api/list.json")
    assert "send" not in events
    assert executor.ledger.actual_http_sends == 0


def test_priority_degradation_is_enforced_before_reservation_and_send() -> None:
    events: list[str] = []
    quota = _Quota(events, used=70, budget=100)
    executor = OpenDARTAttemptExecutor(
        quota_repository=quota,
        config=_config(calls=3),
        ledger=CollectorRunLedger(max_calls=3),
        now=lambda: datetime(2026, 7, 22, 0, 0, tzinfo=UTC),
    )

    with pytest.raises(PriorityDeferred):
        executor.before_send("/api/company.json")
    assert events == []

    executor.before_send("/api/majorstock.json")
    executor.record_http_handoff()
    assert events == ["reserve-1"]

    quota.used = 72
    with pytest.raises(PriorityDeferred):
        executor.before_send("/api/majorstock.json")
    executor.before_send("/api/list.json")
    executor.record_http_handoff()
    assert events == ["reserve-1", "reserve-2"]


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
    executor = _FakeExecutor(calls)
    collector = CalendarCollector(lock=lock, executor=executor, config=_config(calls=5))
    with pytest.raises(ProviderQuotaExhausted):
        collector.run([_task("000660", 1), _task("005930", 1, result={"status": "020"})])
    assert calls == ["000660:1", "005930:1"]
    assert executor.exhausted is True


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


def test_collector_defers_low_priority_task_and_continues_remaining_queue() -> None:
    calls: list[str] = []
    executor = _FakeExecutor(calls, defer_operation="company")
    collector = CalendarCollector(
        lock=_Lock(acquired=True),
        executor=executor,
        config=_config(calls=5),
    )
    tasks = [
        CollectionTask(operation="company", subject="000660", page=1, send=lambda: "deferred"),
        CollectionTask(operation="list", subject="005930", page=1, send=lambda: "p1-result"),
    ]

    assert collector.run(tasks) == ["p1-result"]
    assert calls == ["p1-result"]


def test_successful_page_is_published_once_after_provider_result() -> None:
    calls: list[str] = []
    published: list[object] = []
    collector = CalendarCollector(
        lock=_Lock(acquired=True),
        executor=_FakeExecutor(calls),
        config=_config(calls=5),
    )
    task = CollectionTask(
        operation="list",
        subject="005930",
        page=1,
        send=lambda: {"status": "000", "page_no": 1},
        publish=published.append,
    )

    result = collector.run([task])

    assert result == [{"status": "000", "page_no": 1}]
    assert published == result


class _Quota:
    def __init__(
        self,
        events: list[str],
        *,
        deny: bool = False,
        used: int = 0,
        budget: int = 80,
    ) -> None:
        self.events = events
        self.deny = deny
        self.reservations = 0
        self.exhausted = False
        self.used = used
        self.budget = budget

    def reserve(self, *_: object) -> None:
        self.reservations += 1
        self.events.append(f"reserve-{self.reservations}")
        if self.deny:
            raise RuntimeError("ambiguous reservation")
        self.used += 1

    def get_usage(self, _: object) -> QuotaUsage:
        return QuotaUsage(
            usage_date=datetime(2026, 7, 22, tzinfo=UTC).date(),
            effective_limit=100,
            daily_budget=self.budget,
            physical_attempts=self.used,
            exhausted_at=None,
            exhausted_reason=None,
            last_grant_token=None,
        )

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
    def __init__(self, calls: list[str], *, defer_operation: str | None = None) -> None:
        self.calls = calls
        self.exhausted = False
        self.defer_operation = defer_operation

    def execute(self, operation: str, send: object) -> object:
        if operation == self.defer_operation:
            raise PriorityDeferred("fixture deferred")
        assert callable(send)
        result = send()
        label = result if isinstance(result, str) else self._next_label()
        self.calls.append(str(label))
        if isinstance(result, dict) and result.get("status") == "020":
            self.mark_provider_exhausted()
            raise ProviderQuotaExhausted()
        return result

    def mark_provider_exhausted(self) -> None:
        self.exhausted = True

    def _next_label(self) -> str:
        return "000660:1" if not self.calls else "005930:1"


def _task(subject: str, page: int, *, result: object | None = None) -> CollectionTask:
    identity = f"{subject}:{page}"
    value = identity if result is None else result
    return CollectionTask(
        operation="list",
        subject=subject,
        page=page,
        send=lambda: value,
    )


def _config(*, calls: int) -> OpenDARTQuotaConfig:
    return OpenDARTQuotaConfig(
        daily_call_limit=100,
        daily_call_budget=80,
        max_calls_per_run=calls,
        max_symbols_per_run=2,
    )
