from __future__ import annotations

import pytest

from app.data._shared.redis_quota import QuotaWindow
from app.data.naver.quota import quota_key_for, quota_policy_for


def test_legacy_quota_is_2000_per_rolling_day_with_250ms_no_burst() -> None:
    policy = quota_policy_for("naver-legacy")

    assert policy.windows == (QuotaWindow(limit=2_000, seconds=86_400),)
    assert policy.min_interval_ms == 250
    assert policy.max_calls_per_run == 8
    assert policy.cooldown_seconds == 60


def test_api_hub_quota_applies_daily_and_30_day_windows_together() -> None:
    policy = quota_policy_for("naver-api-hub")

    assert policy.windows == (
        QuotaWindow(limit=2_000, seconds=86_400),
        QuotaWindow(limit=60_000, seconds=30 * 86_400),
    )
    assert policy.min_interval_ms == 250
    assert policy.max_calls_per_run == 8
    assert policy.cooldown_seconds == 60


def test_quota_keys_are_opaque_profile_scopes_only() -> None:
    assert quota_key_for("naver-legacy") == "s1.3:quota:naver:naver-legacy:primary"
    assert quota_key_for("naver-api-hub") == "s1.3:quota:naver:naver-api-hub:primary"

    with pytest.raises(ValueError, match="profile"):
        quota_key_for("credential-or-url")


def test_runtime_run_cap_can_only_lower_without_changing_version_or_windows() -> None:
    base = quota_policy_for("naver-legacy")
    lowered = quota_policy_for("naver-legacy", max_calls_per_run=3)

    assert lowered.max_calls_per_run == 3
    assert lowered.version == base.version
    assert lowered.windows == base.windows
    assert lowered.min_interval_ms == base.min_interval_ms
    assert lowered.cooldown_seconds == base.cooldown_seconds

    with pytest.raises(ValueError, match="run cap"):
        quota_policy_for("naver-legacy", max_calls_per_run=9)
