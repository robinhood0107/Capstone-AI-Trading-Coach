from __future__ import annotations

import pytest

from app.data._shared.redis_quota import (
    QuotaDeniedError,
    QuotaWindow,
    RedisQuotaReservation,
)
from app.data.krx.quota import quota_key, quota_policy


class _AllowingRedis:
    def __init__(self) -> None:
        self.calls = 0

    def eval(self, _script: str, _key_count: int, *_args: object) -> list[int]:
        self.calls += 1
        return [1, 0, self.calls]


def test_quota_is_9000_per_rolling_day_with_250ms_no_burst_and_two_call_run_cap() -> None:
    policy = quota_policy()

    assert policy.windows == (QuotaWindow(limit=9_000, seconds=86_400),)
    assert policy.min_interval_ms == 250
    assert policy.max_calls_per_run == 2


def test_quota_key_is_the_approved_opaque_deployment_scope() -> None:
    key = quota_key()

    assert key == "s1.3:quota:krx:krx-openapi:primary"
    assert "http" not in key
    assert "auth" not in key


def test_runtime_run_cap_can_only_lower_without_changing_policy_identity() -> None:
    base = quota_policy()
    lowered = quota_policy(max_calls_per_run=1)

    assert lowered.max_calls_per_run == 1
    assert lowered.version == base.version
    assert lowered.windows == base.windows
    assert lowered.min_interval_ms == base.min_interval_ms
    assert lowered.cooldown_seconds == base.cooldown_seconds

    for invalid in (0, 3, True):
        with pytest.raises(ValueError, match="run cap"):
            quota_policy(max_calls_per_run=invalid)


def test_third_krx_reservation_is_denied_without_a_third_redis_call() -> None:
    redis = _AllowingRedis()
    quota = RedisQuotaReservation(redis, key=quota_key(), policy=quota_policy())

    quota.reserve(attempt_id="00000000-0000-4000-8000-000000000001")
    quota.reserve(attempt_id="00000000-0000-4000-8000-000000000002")
    with pytest.raises(QuotaDeniedError) as exc_info:
        quota.reserve(attempt_id="00000000-0000-4000-8000-000000000003")

    assert exc_info.value.retry_after_ms == 0
    assert exc_info.value.observed_count == 2
    assert quota.granted_in_run == 2
    assert redis.calls == 2
