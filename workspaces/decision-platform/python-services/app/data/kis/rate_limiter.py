from __future__ import annotations

import time
from collections.abc import Callable


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
        # burst를 초당 한도보다 크게 열지 않는다. 개발 중 실수로 빠른 루프가 KIS 제한을 넘는 일을 막는다.
        self.capacity = float(capacity or max(1.0, rate_per_second))
        self._tokens = self.capacity
        self._clock = clock
        self._sleeper = sleeper
        self._updated_at = self._clock()

    def acquire(self) -> None:
        while True:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            # 부족한 token만큼만 기다려 batch 전체가 과도하게 느려지지 않게 한다.
            wait_seconds = (1.0 - self._tokens) / self.rate_per_second
            self._sleeper(wait_seconds)

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._updated_at)
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_second)
        self._updated_at = now
