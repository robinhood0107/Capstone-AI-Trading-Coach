from __future__ import annotations

import pytest

from app.data._shared.redis_quota import QuotaWindow
from app.data.krx.quota import quota_key, quota_policy


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
