from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.data._shared.redis_quota import (
    REDIS_COOLDOWN_LUA,
    REDIS_QUOTA_LUA,
    QuotaDeniedError,
    QuotaUnavailableError,
    QuotaWaitError,
    QuotaWindow,
    RedisQuotaPolicy,
    RedisQuotaReservation,
)


@dataclass
class FakeRedis:
    result: object = field(default_factory=lambda: [1, 0, 1])
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
    assert "redis.call('TIME')" in REDIS_COOLDOWN_LUA
    assert "redis.call('SET', cooldown_key" in REDIS_COOLDOWN_LUA
    assert "return {2, min_interval_ms - elapsed" in REDIS_QUOTA_LUA


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
    assert args[3] == 0


def test_min_interval_returns_wait_without_sleep_or_consuming_local_run_cap() -> None:
    redis = FakeRedis(result=[2, 250, 1])
    policy = RedisQuotaPolicy(
        version="naver-v1",
        windows=(QuotaWindow(limit=2_000, seconds=86_400),),
        min_interval_ms=250,
        cooldown_seconds=60,
        max_calls_per_run=8,
    )
    quota = RedisQuotaReservation(
        redis,
        key="s1.3:quota:naver:naver-legacy:primary",
        policy=policy,
    )

    with pytest.raises(QuotaWaitError) as exc_info:
        quota.reserve(attempt_id="00000000-0000-4000-8000-000000000001")

    assert exc_info.value.retry_after_ms == 250
    assert exc_info.value.observed_count == 1
    assert quota.granted_in_run == 0
    assert redis.calls is not None and len(redis.calls) == 1


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


@pytest.mark.parametrize("invalid_status", [-1, 3, "allow", True])
def test_unknown_or_ambiguous_lua_decision_is_unavailable(invalid_status: object) -> None:
    policy = RedisQuotaPolicy(
        version="ecos-v1",
        windows=(QuotaWindow(limit=270, seconds=1_800),),
        min_interval_ms=0,
        cooldown_seconds=1_800,
        max_calls_per_run=8,
    )
    quota = RedisQuotaReservation(
        FakeRedis(result=[invalid_status, 0, 0]),
        key="s1.3:quota:ecos:ecos:primary",
        policy=policy,
    )

    with pytest.raises(QuotaUnavailableError, match="invalid"):
        quota.reserve(attempt_id="00000000-0000-4000-8000-000000000001")


def test_local_run_cap_remains_a_denial_without_an_extra_redis_call() -> None:
    redis = FakeRedis(result=[1, 0, 1])
    policy = RedisQuotaPolicy(
        version="ecos-v1",
        windows=(QuotaWindow(limit=270, seconds=1_800),),
        min_interval_ms=0,
        cooldown_seconds=1_800,
        max_calls_per_run=1,
    )
    quota = RedisQuotaReservation(
        redis,
        key="s1.3:quota:ecos:ecos:primary",
        policy=policy,
    )

    quota.reserve(attempt_id="00000000-0000-4000-8000-000000000001")
    with pytest.raises(QuotaDeniedError) as exc_info:
        quota.reserve(attempt_id="00000000-0000-4000-8000-000000000002")

    assert exc_info.value.retry_after_ms == 0
    assert exc_info.value.observed_count == 1
    assert redis.calls is not None and len(redis.calls) == 1


def test_provider_cooldown_is_atomic_and_bounded_by_policy() -> None:
    redis = FakeRedis(result=60_000)
    policy = RedisQuotaPolicy(
        version="naver-v1",
        windows=(QuotaWindow(limit=2_000, seconds=86_400),),
        min_interval_ms=250,
        cooldown_seconds=60,
        max_calls_per_run=8,
    )
    quota = RedisQuotaReservation(
        redis,
        key="s1.3:quota:naver:naver-legacy:primary",
        policy=policy,
    )

    quota.activate_cooldown(seconds=60)

    assert redis.calls is not None
    script, key_count, args = redis.calls[0]
    assert script == REDIS_COOLDOWN_LUA
    assert key_count == 1
    assert args == ("s1.3:quota:naver:naver-legacy:primary", 60_000)
    with pytest.raises(ValueError, match="bounds"):
        quota.activate_cooldown(seconds=61)
