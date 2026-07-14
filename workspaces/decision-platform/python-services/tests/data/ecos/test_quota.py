from __future__ import annotations

from app.data.ecos.quota import (
    ECOS_HARD_WINDOW,
    ECOS_OPERATIONAL_WINDOW,
    apply_ecos_application_cooldown,
    build_ecos_quota_policy,
    cooldown_seconds_for_application_code,
    window_allows_next_attempt,
)


def test_operational_270_boundary_allows_270th_and_rejects_271st() -> None:
    assert ECOS_OPERATIONAL_WINDOW.limit == 270
    assert ECOS_OPERATIONAL_WINDOW.seconds == 1_800
    assert window_allows_next_attempt(current_count=269, window=ECOS_OPERATIONAL_WINDOW)
    assert not window_allows_next_attempt(current_count=270, window=ECOS_OPERATIONAL_WINDOW)


def test_hard_299_boundary_allows_299th_and_rejects_300th() -> None:
    assert ECOS_HARD_WINDOW.limit == 299
    assert ECOS_HARD_WINDOW.seconds == 1_800
    assert window_allows_next_attempt(current_count=298, window=ECOS_HARD_WINDOW)
    assert not window_allows_next_attempt(current_count=299, window=ECOS_HARD_WINDOW)


def test_runtime_policy_uses_operational_window_and_eight_call_run_cap() -> None:
    policy = build_ecos_quota_policy()

    assert policy.windows == (ECOS_OPERATIONAL_WINDOW,)
    assert policy.max_calls_per_run == 8
    assert policy.cooldown_seconds == 1_800
    assert policy.min_interval_ms == 0


def test_error_602_applies_1800_second_cooldown_without_retry() -> None:
    assert cooldown_seconds_for_application_code("ERROR-602") == 1_800
    assert cooldown_seconds_for_application_code("ERROR-601") == 0
    assert cooldown_seconds_for_application_code("INFO-200") == 0

    class RecordingQuota:
        def __init__(self) -> None:
            self.cooldowns: list[int] = []

        def activate_cooldown(self, *, seconds: int) -> None:
            self.cooldowns.append(seconds)

    quota = RecordingQuota()
    apply_ecos_application_cooldown(quota, application_code="ERROR-602")
    apply_ecos_application_cooldown(quota, application_code="ERROR-601")

    assert quota.cooldowns == [1_800]
