from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ECOS_ORIGIN = "https://ecos.bok.or.kr"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[6]


class ECOSSettings(BaseSettings):
    """credential을 제외하고 hard ceiling보다 낮출 수만 있는 ECOS 수집 설정이다."""

    # API key는 private transport만 읽으며 이 모델의 repr/model_dump에는 필드 자체를 두지 않는다.
    model_config = SettingsConfigDict(
        env_file=(_REPOSITORY_ROOT / ".env",),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    response_max_bytes: int = Field(
        default=512 * 1024,
        gt=0,
        le=1024 * 1024,
        validation_alias="ECOS_RESPONSE_MAX_BYTES",
    )
    json_max_depth: int = Field(
        default=8,
        ge=1,
        le=12,
        validation_alias="ECOS_JSON_MAX_DEPTH",
    )
    json_max_object_keys: int = Field(
        default=24,
        ge=1,
        le=32,
        validation_alias="ECOS_JSON_MAX_OBJECT_KEYS",
    )
    max_calls_per_run: int = Field(
        default=8,
        ge=1,
        le=8,
        validation_alias="ECOS_MAX_CALLS_PER_RUN",
    )
    connect_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        le=3.0,
        validation_alias="ECOS_CONNECT_TIMEOUT_SECONDS",
    )
    read_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=8.0,
        validation_alias="ECOS_READ_TIMEOUT_SECONDS",
    )
    write_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        le=3.0,
        validation_alias="ECOS_WRITE_TIMEOUT_SECONDS",
    )
    pool_timeout_seconds: float = Field(
        default=1.0,
        gt=0,
        le=2.0,
        validation_alias="ECOS_POOL_TIMEOUT_SECONDS",
    )
    logical_deadline_seconds: float = Field(
        default=12.0,
        gt=0,
        le=20.0,
        validation_alias="ECOS_LOGICAL_DEADLINE_SECONDS",
    )

    @property
    def origin(self) -> str:
        """credential 전송 대상을 배포 설정으로 바꿀 수 없는 공식 HTTPS origin으로 고정한다."""
        return ECOS_ORIGIN
