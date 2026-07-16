from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.data.krx.catalog import KRX_OPEN_API_ORIGIN


_REPOSITORY_ROOT = Path(__file__).resolve().parents[6]


class KrxOpenApiSettings(BaseSettings):
    """인증정보를 제외하고 lower-only 안전 상한만 보관하는 KRX 공개 설정이다."""

    # AUTH_KEY는 private transport가 send 직전에만 읽으며 공개 설정에는 필드 자체를 두지 않는다.
    model_config = SettingsConfigDict(
        env_file=(_REPOSITORY_ROOT / ".env",),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    max_calls_per_run: int = Field(
        default=2,
        ge=1,
        le=2,
        validation_alias="KRX_OPENAPI_MAX_CALLS_PER_RUN",
    )
    max_attempts_per_request: int = Field(
        default=1,
        ge=1,
        le=1,
        validation_alias="KRX_OPENAPI_MAX_ATTEMPTS_PER_REQUEST",
    )
    response_max_bytes: int = Field(
        default=4 * 1024 * 1024,
        ge=1,
        le=4 * 1024 * 1024,
        validation_alias="KRX_OPENAPI_RESPONSE_MAX_BYTES",
    )
    json_max_depth: int = Field(
        default=4,
        ge=1,
        le=4,
        validation_alias="KRX_OPENAPI_JSON_MAX_DEPTH",
    )
    json_max_rows: int = Field(
        default=5_000,
        ge=1,
        le=5_000,
        validation_alias="KRX_OPENAPI_JSON_MAX_ROWS",
    )
    connect_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        le=2.0,
        validation_alias="KRX_OPENAPI_CONNECT_TIMEOUT_SECONDS",
    )
    read_timeout_seconds: float = Field(
        default=8.0,
        gt=0,
        le=8.0,
        validation_alias="KRX_OPENAPI_READ_TIMEOUT_SECONDS",
    )
    write_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        le=2.0,
        validation_alias="KRX_OPENAPI_WRITE_TIMEOUT_SECONDS",
    )
    pool_timeout_seconds: float = Field(
        default=1.0,
        gt=0,
        le=1.0,
        validation_alias="KRX_OPENAPI_POOL_TIMEOUT_SECONDS",
    )
    logical_deadline_seconds: float = Field(
        default=20.0,
        gt=0,
        le=20.0,
        validation_alias="KRX_OPENAPI_LOGICAL_DEADLINE_SECONDS",
    )

    @field_validator(
        "max_calls_per_run",
        "max_attempts_per_request",
        "response_max_bytes",
        "json_max_depth",
        "json_max_rows",
        mode="before",
    )
    @classmethod
    def _reject_boolean_limits(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("KRX collection limits must be integers")
        return value

    @property
    def origin(self) -> str:
        """AUTH_KEY 전송 대상을 runtime override가 불가능한 공식 HTTPS origin으로 고정한다."""
        return KRX_OPEN_API_ORIGIN
