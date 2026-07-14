from __future__ import annotations

from typing import Final

from app.data._shared.redis_quota import QuotaWindow, RedisQuotaPolicy

_LEGACY_PROFILE: Final = "naver-legacy"
_API_HUB_PROFILE: Final = "naver-api-hub"

_LEGACY_POLICY: Final = RedisQuotaPolicy(
    version="s1.3-naver-legacy-quota-v1",
    windows=(QuotaWindow(limit=2_000, seconds=86_400),),
    min_interval_ms=250,
    cooldown_seconds=60,
    max_calls_per_run=8,
)

_API_HUB_POLICY: Final = RedisQuotaPolicy(
    version="s1.3-naver-api-hub-quota-v1",
    windows=(
        QuotaWindow(limit=2_000, seconds=86_400),
        QuotaWindow(limit=60_000, seconds=30 * 86_400),
    ),
    min_interval_ms=250,
    cooldown_seconds=60,
    max_calls_per_run=8,
)


def quota_policy_for(provider_profile: str) -> RedisQuotaPolicy:
    """Naver profile별 rolling window·no-burst·run cap의 고정 정책을 반환한다."""
    if provider_profile == _LEGACY_PROFILE:
        return _LEGACY_POLICY
    if provider_profile == _API_HUB_PROFILE:
        return _API_HUB_POLICY
    raise ValueError("Naver quota profile is invalid")


def quota_key_for(provider_profile: str) -> str:
    """credential·URL을 포함하지 않는 profile 전용 opaque Redis scope를 반환한다."""
    if provider_profile not in {_LEGACY_PROFILE, _API_HUB_PROFILE}:
        raise ValueError("Naver quota profile is invalid")
    return f"s1.3:quota:naver:{provider_profile}:primary"
