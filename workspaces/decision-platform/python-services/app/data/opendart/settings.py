from __future__ import annotations

from pathlib import Path

from pydantic import Field, PositiveFloat, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class OpenDARTSettings(BaseSettings):
    """OpenDART API key와 read-only 수집 설정을 env에서 안전하게 읽는다."""

    # 다른 workspace의 env가 섞여도 OpenDART read-only 설정만 안정적으로 읽는다.
    model_config = SettingsConfigDict(
        env_file=(".env", "../../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    opendart_api_key: str | None = None
    opendart_offline: bool = False
    opendart_base_url: str = "https://opendart.fss.or.kr"
    opendart_rate_limit_per_second: PositiveFloat = 1.0
    opendart_timeout_seconds: PositiveFloat = 10.0
    opendart_retry_attempts: int = Field(default=3, ge=1, le=5)
    opendart_data_dir: Path = Path("data/opendart")

    @model_validator(mode="after")
    def _validate_api_key(self) -> "OpenDARTSettings":
        if not self.opendart_offline and not self.opendart_api_key:
            # offline fixture는 키 없이 허용하지만, online 조회는 설정 단계에서 실패시켜 원본 요청 로그를 줄인다.
            raise ValueError("OPENDART_API_KEY is required outside offline mode")
        return self

    @property
    def api_key(self) -> str | None:
        return self.opendart_api_key

    @property
    def offline(self) -> bool:
        return self.opendart_offline

    @property
    def base_url(self) -> str:
        return self.opendart_base_url.rstrip("/")

    @property
    def data_dir(self) -> Path:
        return self.opendart_data_dir

    @property
    def rate_limit_per_second(self) -> float:
        return float(self.opendart_rate_limit_per_second)
