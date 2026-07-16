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


def quota_policy_for(
    provider_profile: str,
    *,
    max_calls_per_run: int | None = None,
) -> RedisQuotaPolicy:
    """고정 window/version을 유지하며 실행 cap만 source hard cap 아래로 낮춘다."""
    if provider_profile == _LEGACY_PROFILE:
        base = _LEGACY_POLICY
    elif provider_profile == _API_HUB_PROFILE:
        base = _API_HUB_POLICY
    else:
        raise ValueError("Naver quota profile is invalid")
    if max_calls_per_run is None:
        return base
    if (
        isinstance(max_calls_per_run, bool)
        or not isinstance(max_calls_per_run, int)
        or not 1 <= max_calls_per_run <= base.max_calls_per_run
    ):
        raise ValueError("Naver quota run cap is invalid")
    return RedisQuotaPolicy(
        version=base.version,
        windows=base.windows,
        min_interval_ms=base.min_interval_ms,
        cooldown_seconds=base.cooldown_seconds,
        max_calls_per_run=max_calls_per_run,
    )


def quota_key_for(provider_profile: str) -> str:
    """credential·URL을 포함하지 않는 profile 전용 opaque Redis scope를 반환한다."""
    if provider_profile not in {_LEGACY_PROFILE, _API_HUB_PROFILE}:
        raise ValueError("Naver quota profile is invalid")
    return f"s1.3:quota:naver:{provider_profile}:primary"
