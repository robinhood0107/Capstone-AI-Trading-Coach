from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import IntEnum
from typing import Any, Protocol

from app.data.calendar.errors import (
    CollectorAlreadyRunning,
    NonRetryableProviderError,
    ProviderQuotaExhausted,
    RetryableProviderError,
    RunLimitExceeded,
)
from app.data.calendar.job import kst_usage_date
from app.data.calendar.settings import OpenDARTQuotaConfig


class OperationPriority(IntEnum):
    P1 = 1
    P2 = 2
    P3 = 3
    P4 = 4


_OPERATION_PRIORITIES = {
    "list": OperationPriority.P1,
    "corpCode": OperationPriority.P1,
    "bnkMngtPcbg": OperationPriority.P1,
    "bnkMngtPcsp": OperationPriority.P1,
    "piicDecsn": OperationPriority.P2,
    "cvbdIsDecsn": OperationPriority.P2,
    "lwstLg": OperationPriority.P2,
    "dfOcr": OperationPriority.P2,
    "ctrcvsBgrq": OperationPriority.P2,
    "dsRsOcr": OperationPriority.P2,
    "bsnSp": OperationPriority.P2,
    "crDecsn": OperationPriority.P2,
    "bdwtIsDecsn": OperationPriority.P2,
    "exbdIsDecsn": OperationPriority.P2,
    "cmpMgDecsn": OperationPriority.P2,
    "cmpDvDecsn": OperationPriority.P2,
    "cmpDvmgDecsn": OperationPriority.P2,
    "bsnTrfDecsn": OperationPriority.P2,
    "majorstock": OperationPriority.P3,
    "elestock": OperationPriority.P3,
    "company": OperationPriority.P4,
    "fnlttSinglAcnt": OperationPriority.P4,
    "fnlttSinglIndx": OperationPriority.P4,
    "fnlttCmpnyIndx": OperationPriority.P4,
}


class AttemptBucket(Protocol):
    def acquire(self) -> None: ...


class QuotaReservationRepository(Protocol):
    def reserve(self, usage_date: date, config: OpenDARTQuotaConfig, grant_token: str) -> object: ...

    def mark_exhausted(self, usage_date: date, reason: str) -> None: ...


class CollectorLock(Protocol):
    def acquire_collector_lock(self) -> bool: ...

    def release_collector_lock(self) -> None: ...


@dataclass
class CollectorRunLedger:
    """charged reservation과 actual transport handoff를 섞지 않고 실행별로 따로 센다."""

    max_calls: int
    charged_reservations: int = 0
    actual_http_sends: int = 0


@dataclass(frozen=True)
class CollectionTask:
    """subject 한 page의 안전한 GET을 deterministic round-robin으로 실행하는 내부 task다."""

    operation: str
    subject: str
    page: int
    send: Callable[[], object]


class OpenDARTAttemptExecutor:
    """각 physical attempt를 limiter, PostgreSQL reservation, HTTP handoff 순으로 실행한다."""

    def __init__(
        self,
        *,
        bucket: AttemptBucket,
        quota_repository: QuotaReservationRepository,
        config: OpenDARTQuotaConfig,
        ledger: CollectorRunLedger,
        now: Callable[[], datetime],
    ) -> None:
        if ledger.max_calls > config.max_calls_per_run:
            raise ValueError("ledger max calls cannot exceed configured per-run cap")
        self._bucket = bucket
        self._quota_repository = quota_repository
        self._config = config
        self.ledger = ledger
        self._now = now

    def execute(self, operation: str, send: Callable[[], object]) -> object:
        """safe GET만 최대 3 attempts로 실행하며 429/status020/schema 계열은 즉시 중단한다."""
        priority_for_operation(operation)
        for attempt_index in range(3):
            if self.ledger.charged_reservations >= self.ledger.max_calls:
                raise RunLimitExceeded("per-run physical attempt cap reached")
            now = self._now()
            usage_date = kst_usage_date(now)
            self._bucket.acquire()
            token = f"s16-{usage_date.isoformat()}-{uuid.uuid4().hex}"
            try:
                self._quota_repository.reserve(usage_date, self._config, token)
            except Exception:
                # DB 오류와 ambiguous commit 결과는 retry하거나 HTTP로 진행하지 않는다.
                raise RunLimitExceeded("quota reservation failed closed") from None
            self.ledger.charged_reservations += 1
            self.ledger.actual_http_sends += 1
            try:
                result = send()
            except ProviderQuotaExhausted:
                self._quota_repository.mark_exhausted(usage_date, "PROVIDER_STATUS_020")
                raise
            except NonRetryableProviderError:
                raise
            except RetryableProviderError:
                if attempt_index == 2:
                    raise
                continue
            if isinstance(result, dict) and result.get("status") == "020":
                self._quota_repository.mark_exhausted(usage_date, "PROVIDER_STATUS_020")
                raise ProviderQuotaExhausted()
            return result
        raise RuntimeError("unreachable OpenDART retry state")


class CalendarCollector:
    """single-instance lock 아래 page/subject 순서를 고정하는 offline-ready collector shell이다."""

    def __init__(
        self,
        *,
        lock: CollectorLock,
        executor: Any,
        config: OpenDARTQuotaConfig,
    ) -> None:
        self._lock = lock
        self._executor = executor
        self._config = config

    def run(self, tasks: list[CollectionTask]) -> list[object]:
        """두 번째 process는 outbound 0으로 거부하고 첫 status020에서 남은 queue를 실행하지 않는다."""
        if not self._lock.acquire_collector_lock():
            raise CollectorAlreadyRunning("another S1.6 collector owns the advisory lock")
        try:
            results: list[object] = []
            for task in deterministic_round_robin(
                tasks,
                max_symbols=self._config.max_symbols_per_run,
            ):
                results.append(self._executor.execute(task.operation, task.send))
            return results
        finally:
            self._lock.release_collector_lock()


def priority_for_operation(operation: str) -> OperationPriority:
    """source-controlled operation allowlist 밖 endpoint의 online 실행을 client 생성 전에 거부한다."""
    try:
        return _OPERATION_PRIORITIES[operation]
    except KeyError:
        raise ValueError("unmapped OpenDART operation is not allowed online") from None


def degradation_allows(
    priority: OperationPriority,
    *,
    physical_attempts: int,
    daily_budget: int,
) -> bool:
    """70%부터 P4를, 90%부터 P2/P3/P4를 중단하고 budget 소진 뒤에는 모두 막는다."""
    if daily_budget <= 0 or physical_attempts < 0:
        raise ValueError("quota usage values are invalid")
    if physical_attempts >= daily_budget:
        return False
    if physical_attempts * 100 >= daily_budget * 90:
        return priority is OperationPriority.P1
    if physical_attempts * 100 >= daily_budget * 70:
        return priority is not OperationPriority.P4
    return True


def deterministic_round_robin(
    tasks: list[CollectionTask],
    *,
    max_symbols: int,
) -> list[CollectionTask]:
    """corp/symbol 정렬 후 각 subject의 한 page씩 순환하고 cap 밖 subject는 deferred 상태로 남긴다."""
    if max_symbols <= 0:
        raise ValueError("max symbols must be positive")
    subjects = sorted({task.subject for task in tasks})[:max_symbols]
    allowed = set(subjects)
    return sorted(
        (task for task in tasks if task.subject in allowed),
        key=lambda task: (task.page, task.subject, task.operation),
    )
