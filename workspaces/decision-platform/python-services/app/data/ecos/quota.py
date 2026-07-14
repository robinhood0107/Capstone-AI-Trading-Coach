from __future__ import annotations

from pathlib import Path
from typing import Final, Protocol, cast

import redis
from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.data._shared.redis_quota import (
    QuotaUnavailableError,
    QuotaWindow,
    RedisEvalLike,
    RedisQuotaPolicy,
    RedisQuotaReservation,
)

ECOS_OPERATIONAL_WINDOW: Final = QuotaWindow(limit=270, seconds=1_800)
ECOS_HARD_WINDOW: Final = QuotaWindow(limit=299, seconds=1_800)
ECOS_QUOTA_KEY: Final = "s1.3:quota:ecos:ecos:primary"
ECOS_QUOTA_POLICY_VERSION: Final = "s1.3-ecos-quota-v1"

_REPOSITORY_ROOT = Path(__file__).resolve().parents[6]
_ROOT_ENV_FILE = _REPOSITORY_ROOT / ".env"


class ECOSQuota(Protocol):
    """모든 ECOS physical attempt와 provider cooldown이 공유하는 quota 경계다."""

    def reserve(self, *, attempt_id: str) -> None: ...

    def activate_cooldown(self, *, seconds: int) -> None: ...


class _CooldownQuota(Protocol):
    def activate_cooldown(self, *, seconds: int) -> None: ...


class _RedisSettings(BaseSettings):
    """Redis credential은 quota client 생성 구간에만 로드하고 공개 ECOS 설정에서 제외한다."""

    model_config = SettingsConfigDict(
        env_file=(_ROOT_ENV_FILE,),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    redis_host: str = "127.0.0.1"
    redis_port: int = Field(default=6379, ge=1, le=65_535)
    redis_db: int = Field(default=0, ge=0)
    redis_password: SecretStr = Field(repr=False, exclude=True)

    @field_validator("redis_password")
    @classmethod
    def _require_password(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("blank Redis authentication material")
        return value


def build_ecos_quota_policy(*, max_calls_per_run: int = 8) -> RedisQuotaPolicy:
    """운영 270/30분과 run cap을 적용한 ECOS Redis quota 정책을 만든다.

    299/30분 hard window는 독립 경계 검증에 남기며 더 엄격한 운영 window가 runtime을 지배한다.
    """
    if max_calls_per_run < 1 or max_calls_per_run > 8:
        raise ValueError("ECOS max calls per run is out of bounds")
    return RedisQuotaPolicy(
        version=ECOS_QUOTA_POLICY_VERSION,
        windows=(ECOS_OPERATIONAL_WINDOW,),
        min_interval_ms=0,
        cooldown_seconds=1_800,
        max_calls_per_run=max_calls_per_run,
    )


def build_ecos_quota_reservation(
    redis_client: object,
    *,
    max_calls_per_run: int = 8,
) -> RedisQuotaReservation:
    """credential·URL이 없는 opaque deployment key로 ECOS quota reservation을 만든다."""
    return RedisQuotaReservation(
        cast(RedisEvalLike, redis_client),
        key=ECOS_QUOTA_KEY,
        policy=build_ecos_quota_policy(max_calls_per_run=max_calls_per_run),
    )


def window_allows_next_attempt(*, current_count: int, window: QuotaWindow) -> bool:
    """현재 count 다음 physical attempt가 지정 window limit 안인지 판정한다."""
    if isinstance(current_count, bool) or current_count < 0:
        raise ValueError("quota count must be a non-negative integer")
    return current_count < window.limit


def cooldown_seconds_for_application_code(application_code: str) -> int:
    """ECOS ERROR-602에만 30분 project cooldown을 반환한다."""
    return 1_800 if application_code == "ERROR-602" else 0


def apply_ecos_application_cooldown(
    quota: _CooldownQuota,
    *,
    application_code: str,
) -> None:
    """ERROR-602 응답을 재시도하지 않고 공유 Redis cooldown으로 즉시 반영한다."""
    seconds = cooldown_seconds_for_application_code(application_code)
    if seconds:
        quota.activate_cooldown(seconds=seconds)


def _build_redis_client() -> redis.Redis:
    """ignored env의 Redis password를 private 경계에서 읽어 fail-closed client를 만든다."""
    try:
        settings = _RedisSettings()  # type: ignore[call-arg]
    except ValidationError:
        raise QuotaUnavailableError("source quota authentication is unavailable") from None
    password = settings.redis_password.get_secret_value()
    try:
        return redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=password,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
            retry_on_timeout=False,
        )
    finally:
        password = ""
