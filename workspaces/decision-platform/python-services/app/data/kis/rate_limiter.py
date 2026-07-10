from __future__ import annotations

import time
from collections.abc import Callable
from threading import Lock
from typing import Protocol


class RateLimiter(Protocol):
    """실제 provider send 직전에 호출 슬롯 하나를 원자 예약한다."""

    def acquire(self) -> None: ...


class RedisIntervalStore(Protocol):
    def set(
        self,
        name: str,
        value: str,
        *,
        nx: bool = False,
        px: int | None = None,
    ) -> object: ...

    def pttl(self, name: str) -> int: ...


class KISRateLimitUnavailable(RuntimeError):
    """공유 호출 슬롯을 안전하게 예약할 수 없으면 온라인 호출을 fail-closed한다."""


class KISRateLimitWaitExceeded(RuntimeError):
    """공유 호출 대기가 bounded deadline을 넘으면 무한 queue 대신 실패한다."""


class TokenBucket:
    def __init__(
        self,
        rate_per_second: float,
        capacity: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        # clock/sleeper를 주입 가능하게 둬 rate limit 테스트가 실제 sleep 없이 예산 계산만 검증하게 한다.
        self.rate_per_second = float(rate_per_second)
        requested_capacity = 1.0 if capacity is None else float(capacity)
        if requested_capacity <= 0 or requested_capacity > 1:
            raise ValueError("burst capacity greater than one is not allowed")
        # 공식 초당 숫자만큼 초기 token을 채우면 시작 순간 burst가 생기므로 항상 한 건만 허용한다.
        self.capacity = requested_capacity
        self._tokens = self.capacity
        self._clock = clock
        self._sleeper = sleeper
        self._updated_at = self._clock()
        self._lock = Lock()

    def acquire(self) -> None:
        # 단일 process 안의 여러 thread도 같은 local fallback 슬롯을 공유한다.
        with self._lock:
            while True:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait_seconds = (1.0 - self._tokens) / self.rate_per_second
                self._sleeper(wait_seconds)

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._updated_at)
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_second)
        self._updated_at = now


class RedisIntervalLimiter:
    """같은 opaque provider scope의 모든 process가 공유하는 no-burst 호출 간격 예약기다."""

    def __init__(
        self,
        redis_client: RedisIntervalStore,
        *,
        key: str,
        interval_seconds: float,
        max_wait_seconds: float,
        io_budget_seconds: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if max_wait_seconds <= 0:
            raise ValueError("max_wait_seconds must be positive")
        if io_budget_seconds < 0 or io_budget_seconds >= max_wait_seconds:
            raise ValueError("io_budget_seconds must be non-negative and below max_wait_seconds")
        self._redis = redis_client
        self._key = key
        self._interval_ms = max(1, round(interval_seconds * 1_000))
        # 마지막 SET/PTTL 두 번의 bounded socket I/O가 전체 wait 상한 안에 들어오도록 미리 예산을 뺀다.
        self._reservation_wait_seconds = float(max_wait_seconds - io_budget_seconds)
        self._clock = clock
        self._sleeper = sleeper

    def acquire(self) -> None:
        deadline = self._clock() + self._reservation_wait_seconds
        while True:
            try:
                granted = self._redis.set(
                    self._key,
                    "1",
                    nx=True,
                    px=self._interval_ms,
                )
                if granted:
                    return
                ttl_ms = self._redis.pttl(self._key)
            except Exception:
                raise KISRateLimitUnavailable("KIS shared rate limiter is unavailable") from None

            remaining = deadline - self._clock()
            if remaining < 0:
                raise KISRateLimitWaitExceeded("KIS shared rate-limit wait exceeded")
            wait_seconds = self._interval_ms / 1_000 if ttl_ms <= 0 else ttl_ms / 1_000
            if wait_seconds > remaining:
                raise KISRateLimitWaitExceeded("KIS shared rate-limit wait exceeded")
            self._sleeper(max(0.001, wait_seconds))
