from __future__ import annotations

from typing import Final

from app.data._shared.redis_quota import QuotaWindow, RedisQuotaPolicy

_KRX_QUOTA_KEY: Final = "s1.3:quota:krx:krx-openapi:primary"
_KRX_QUOTA_POLICY: Final = RedisQuotaPolicy(
    version="s1.3-krx-openapi-quota-v1",
    windows=(QuotaWindow(limit=9_000, seconds=86_400),),
    min_interval_ms=250,
    cooldown_seconds=60,
    max_calls_per_run=2,
)


def quota_policy(*, max_calls_per_run: int | None = None) -> RedisQuotaPolicy:
    """rolling 24시간 9,000건과 250ms no-burst를 유지하며 run cap만 낮춘다."""
    if max_calls_per_run is None:
        return _KRX_QUOTA_POLICY
    if (
        isinstance(max_calls_per_run, bool)
        or not isinstance(max_calls_per_run, int)
        or not 1 <= max_calls_per_run <= _KRX_QUOTA_POLICY.max_calls_per_run
    ):
        raise ValueError("KRX quota run cap is invalid")
    return RedisQuotaPolicy(
        version=_KRX_QUOTA_POLICY.version,
        windows=_KRX_QUOTA_POLICY.windows,
        min_interval_ms=_KRX_QUOTA_POLICY.min_interval_ms,
        cooldown_seconds=_KRX_QUOTA_POLICY.cooldown_seconds,
        max_calls_per_run=max_calls_per_run,
    )


def quota_key() -> str:
    """credential·URL이 없는 KRX OPEN API deployment Redis scope를 반환한다."""
    return _KRX_QUOTA_KEY


def s5_quota_policy(*, max_calls_per_run: int) -> RedisQuotaPolicy:
    """S5.6 approved one-shot cap을 기존 9,000/day와 250ms no-burst 아래에서 연다."""

    if (
        isinstance(max_calls_per_run, bool)
        or not isinstance(max_calls_per_run, int)
        or not 1 <= max_calls_per_run <= 4_441
    ):
        raise ValueError("S5 KRX quota run cap is invalid")
    return RedisQuotaPolicy(
        version="s5.6-krx-openapi-quota-v1",
        windows=_KRX_QUOTA_POLICY.windows,
        min_interval_ms=_KRX_QUOTA_POLICY.min_interval_ms,
        cooldown_seconds=_KRX_QUOTA_POLICY.cooldown_seconds,
        max_calls_per_run=max_calls_per_run,
    )
