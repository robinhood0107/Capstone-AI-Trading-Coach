from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.data.naver.profiles import (
    API_HUB_PROFILE,
    LEGACY_PROFILE,
    NAVER_MIGRATION_LIFECYCLE,
    profile_for,
)


def test_legacy_is_active_and_hub_is_disabled_ready_for_operator_cutover() -> None:
    lifecycle = NAVER_MIGRATION_LIFECYCLE

    assert LEGACY_PROFILE.enabled is True
    assert API_HUB_PROFILE.enabled is False
    assert lifecycle.cutover_control == "operator-controlled"
    assert lifecycle.automatic_date_switch is False

    # 목표일은 운영자 의사결정 기준일이며 profile을 자동으로 전환하지 않는다.
    at_target = datetime(2027, 3, 31, tzinfo=timezone.utc)
    assert profile_for("legacy", now=at_target) is LEGACY_PROFILE
    with pytest.raises(ValueError, match="profile_disabled"):
        profile_for("api-hub", now=at_target)


def test_hub_preparation_and_validation_require_the_planned_approval_stages() -> None:
    lifecycle = NAVER_MIGRATION_LIFECYCLE

    assert lifecycle.credential_preparation_period == "2026-Q3"
    assert lifecycle.validation_period == "2026-Q4"
    assert lifecycle.offline_validation_required is True
    assert lifecycle.minimal_online_requires_separate_approval is True


def test_migration_deadlines_and_kst_legacy_hard_stop_share_one_policy() -> None:
    lifecycle = NAVER_MIGRATION_LIFECYCLE

    assert lifecycle.target_cutover.isoformat() == "2027-03-31"
    assert lifecycle.legacy_rollback_removal.isoformat() == "2027-05-31"
    assert lifecycle.legacy_hard_stop.isoformat() == "2027-06-30T00:00:00+09:00"

    before_hard_stop = datetime(2027, 6, 29, 14, 59, 59, tzinfo=timezone.utc)
    at_hard_stop = datetime(2027, 6, 29, 15, 0, 0, tzinfo=timezone.utc)
    assert profile_for("legacy", now=before_hard_stop) is LEGACY_PROFILE
    with pytest.raises(ValueError, match="legacy_hard_stop"):
        profile_for("legacy", now=at_hard_stop)
