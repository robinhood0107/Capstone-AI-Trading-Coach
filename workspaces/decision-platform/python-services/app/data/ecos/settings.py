from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.data.ecos.policy import ECOS_ORIGIN

_REPOSITORY_ROOT = Path(__file__).resolve().parents[6]
_PYTHON_SERVICE_ROOT = Path(__file__).resolve().parents[3]


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
    max_attempts_per_request: int = Field(
        default=2,
        ge=1,
        le=2,
        validation_alias="ECOS_MAX_ATTEMPTS_PER_REQUEST",
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
    snapshot_root: Path = Field(
        default=_PYTHON_SERVICE_ROOT / "data" / "source_snapshots",
        validation_alias="SOURCE_SNAPSHOT_ROOT",
    )

    @field_validator("max_attempts_per_request", mode="before")
    @classmethod
    def _reject_boolean_attempt_limit(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("ECOS attempt limit must be an integer")
        return value

    @property
    def origin(self) -> str:
        """credential 전송 대상을 배포 설정으로 바꿀 수 없는 공식 HTTPS origin으로 고정한다."""
        return ECOS_ORIGIN


class ECOSS5ProductionSettings(ECOSSettings):
    """S5.6 one-shot 두 series chunk set에만 24-call 상한과 retry 0을 적용한다."""

    max_calls_per_run: int = Field(
        default=24,
        ge=1,
        le=24,
        validation_alias="S5_ECOS_MAX_CALLS_PER_RUN",
    )
    max_attempts_per_request: int = Field(
        default=1,
        ge=1,
        le=1,
        validation_alias="S5_ECOS_MAX_ATTEMPTS_PER_REQUEST",
    )
