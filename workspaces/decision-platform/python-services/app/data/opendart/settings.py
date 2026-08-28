from __future__ import annotations

from pathlib import Path

from app.data._shared.repository_root import repository_root

from pydantic import Field, PositiveFloat
from pydantic_settings import BaseSettings, SettingsConfigDict

OPENDART_ORIGIN = "https://opendart.fss.or.kr"
_REPOSITORY_ROOT = repository_root(__file__, 6)


class OpenDARTSettings(BaseSettings):
    """인증정보를 제외한 OpenDART read-only 수집 설정만 제공한다."""

    # 인증정보는 이 모델에 선언하지 않아 repr/model_dump와 상위 business client로 전파되지 않는다.
    model_config = SettingsConfigDict(
        env_file=(_REPOSITORY_ROOT / ".env",),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    opendart_offline: bool = False
    opendart_rate_limit_per_second: PositiveFloat = 1.0
    opendart_timeout_seconds: PositiveFloat = 10.0
    opendart_retry_attempts: int = Field(default=3, ge=1, le=3)
    opendart_data_dir: Path = Path("data/opendart")

    @property
    def offline(self) -> bool:
        return self.opendart_offline

    @property
    def base_url(self) -> str:
        # 인증정보가 다른 origin으로 전송되지 않도록 배포 설정으로도 변경할 수 없게 고정한다.
        return OPENDART_ORIGIN

    @property
    def data_dir(self) -> Path:
        return self.opendart_data_dir

    @property
    def rate_limit_per_second(self) -> float:
        return float(self.opendart_rate_limit_per_second)
