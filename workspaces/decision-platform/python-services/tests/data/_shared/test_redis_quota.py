from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.data._shared.redis_quota import (
    REDIS_QUOTA_LUA,
    QuotaDeniedError,
    QuotaUnavailableError,
    QuotaWindow,
    RedisQuotaPolicy,
    RedisQuotaReservation,
)


@dataclass
class FakeRedis:
    result: object = [1, 0, 1]
    calls: list[tuple[str, int, tuple[object, ...]]] | None = None

    def eval(self, script: str, key_count: int, *args: object) -> object:
        if self.calls is None:
            self.calls = []
        self.calls.append((script, key_count, args))
        return self.result


def test_lua_uses_redis_time_and_atomic_window_primitives() -> None:
    assert "redis.call('TIME')" in REDIS_QUOTA_LUA
    assert "ZREMRANGEBYSCORE" in REDIS_QUOTA_LUA
    assert "ZADD" in REDIS_QUOTA_LUA
    assert "PEXPIRE" in REDIS_QUOTA_LUA
    assert "cooldown" in REDIS_QUOTA_LUA


def test_reservation_uses_only_an_opaque_deployment_slot() -> None:
    redis = FakeRedis()
    policy = RedisQuotaPolicy(
        version="ecos-v1",
        windows=(QuotaWindow(limit=270, seconds=1800),),
        min_interval_ms=0,
        cooldown_seconds=1800,
        max_calls_per_run=8,
    )
    quota = RedisQuotaReservation(redis, key="s1.3:quota:ecos:ecos:primary", policy=policy)

    quota.reserve(attempt_id="00000000-0000-4000-8000-000000000001")

    assert redis.calls is not None
    _, key_count, args = redis.calls[0]
    assert key_count == 1
    rendered = " ".join(str(arg) for arg in args)
    assert "credential" not in rendered.lower()
    assert "http" not in rendered.lower()


def test_denial_and_redis_failure_are_fail_closed() -> None:
    policy = RedisQuotaPolicy(
        version="naver-v1",
        windows=(QuotaWindow(limit=2000, seconds=86400),),
        min_interval_ms=250,
        cooldown_seconds=60,
        max_calls_per_run=8,
    )
    denied = RedisQuotaReservation(
        FakeRedis(result=[0, 250, 2000]),
        key="s1.3:quota:naver:naver-legacy:primary",
        policy=policy,
    )
    with pytest.raises(QuotaDeniedError):
        denied.reserve(attempt_id="00000000-0000-4000-8000-000000000001")

    class BrokenRedis:
        def eval(self, script: str, key_count: int, *args: object) -> object:
            raise RuntimeError("redis unavailable")

    unavailable = RedisQuotaReservation(
        BrokenRedis(),
        key="s1.3:quota:naver:naver-legacy:primary",
        policy=policy,
    )
    with pytest.raises(QuotaUnavailableError):
        unavailable.reserve(attempt_id="00000000-0000-4000-8000-000000000001")
