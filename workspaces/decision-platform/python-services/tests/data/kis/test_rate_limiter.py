from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock

import pytest

from app.data.kis.rate_limiter import (
    KISRateLimitUnavailable,
    KISRateLimitWaitExceeded,
    RedisIntervalLimiter,
    TokenBucket,
)


@dataclass
class _Entry:
    value: str
    expires_at_ms: int


class _FakeRedisClock:
    def __init__(self) -> None:
        self.now_ms = 0
        self.entries: dict[str, _Entry] = {}
        self._lock = Lock()

    def set(
        self,
        name: str,
        value: str,
        *,
        nx: bool = False,
        px: int | None = None,
    ) -> bool | None:
        with self._lock:
            self._expire(name)
            if nx and name in self.entries:
                return None
            assert px is not None
            self.entries[name] = _Entry(value=value, expires_at_ms=self.now_ms + px)
            return True

    def pttl(self, name: str) -> int:
        with self._lock:
            self._expire(name)
            entry = self.entries.get(name)
            return -2 if entry is None else entry.expires_at_ms - self.now_ms

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.now_ms += round(seconds * 1000)

    def monotonic(self) -> float:
        with self._lock:
            return self.now_ms / 1000

    def _expire(self, name: str) -> None:
        entry = self.entries.get(name)
        if entry is not None and entry.expires_at_ms <= self.now_ms:
            self.entries.pop(name)


def test_local_token_bucket_never_bursts_the_whole_second_budget() -> None:
    now = [0.0]

    def sleeper(seconds: float) -> None:
        now[0] += seconds

    bucket = TokenBucket(18, clock=lambda: now[0], sleeper=sleeper)

    for _ in range(18):
        bucket.acquire()

    assert now[0] == pytest.approx(17 / 18)


def test_local_token_bucket_rejects_multi_request_burst_capacity() -> None:
    with pytest.raises(ValueError, match="burst capacity"):
        TokenBucket(18, capacity=18)


def test_redis_interval_limiter_is_shared_by_independent_clients() -> None:
    redis_client = _FakeRedisClock()
    first = RedisIntervalLimiter(
        redis_client,
        key="opaque-provider-scope",
        interval_seconds=1.0,
        max_wait_seconds=2.0,
        clock=redis_client.monotonic,
        sleeper=redis_client.sleep,
    )
    second = RedisIntervalLimiter(
        redis_client,
        key="opaque-provider-scope",
        interval_seconds=1.0,
        max_wait_seconds=2.0,
        clock=redis_client.monotonic,
        sleeper=redis_client.sleep,
    )

    first.acquire()
    second.acquire()

    assert redis_client.now_ms == 1000


def test_concurrent_clients_cannot_reserve_the_same_redis_interval() -> None:
    redis_client = _FakeRedisClock()
    clients = [
        RedisIntervalLimiter(
            redis_client,
            key="opaque-provider-scope",
            interval_seconds=1.0,
            max_wait_seconds=2.0,
            clock=redis_client.monotonic,
            sleeper=redis_client.sleep,
        )
        for _ in range(2)
    ]

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda limiter: limiter.acquire(), clients))

    assert redis_client.now_ms == 1000


def test_redis_interval_limiter_fails_closed_when_reservation_store_is_unavailable() -> None:
    class BrokenRedis:
        def set(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("redis unavailable")

        def pttl(self, name: str) -> int:
            return -2

    limiter = RedisIntervalLimiter(
        BrokenRedis(),
        key="opaque-provider-scope",
        interval_seconds=1.0,
        max_wait_seconds=1.0,
    )

    with pytest.raises(KISRateLimitUnavailable, match="unavailable"):
        limiter.acquire()


def test_redis_interval_limiter_fails_before_waiting_past_bounded_deadline() -> None:
    redis_client = _FakeRedisClock()
    redis_client.set("opaque-provider-scope", "reserved", nx=True, px=1_000)
    limiter = RedisIntervalLimiter(
        redis_client,
        key="opaque-provider-scope",
        interval_seconds=1.0,
        max_wait_seconds=0.5,
        clock=redis_client.monotonic,
        sleeper=redis_client.sleep,
    )

    with pytest.raises(KISRateLimitWaitExceeded, match="wait exceeded"):
        limiter.acquire()

    # 남은 TTL이 deadline보다 길면 불필요하게 sleep하지 않고 outbound 전에 실패한다.
    assert redis_client.now_ms == 0
