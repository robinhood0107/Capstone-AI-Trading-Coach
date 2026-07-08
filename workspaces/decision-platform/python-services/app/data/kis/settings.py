from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, PositiveFloat, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

KISMode = Literal["mock", "live"]


class KISSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    kis_mode: KISMode = "mock"
    kis_offline: bool = False
    kis_mock_app_key: str | None = None
    kis_mock_app_secret: str | None = None
    kis_mock_account_no: str | None = None
    kis_live_app_key: str | None = None
    kis_live_app_secret: str | None = None
    kis_live_account_no: str | None = None
    kis_app_key: str | None = None
    kis_app_secret: str | None = None
    kis_account_no: str | None = None
    kis_rate_limit_per_second: PositiveFloat = 1.0
    kis_data_dir: Path = Path("data/kis")
    redis_url: str = "redis://localhost:6379/0"
    kis_timeout_seconds: PositiveFloat = 10.0
    kis_retry_attempts: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def _validate_mode_credentials(self) -> "KISSettings":
        if not self.kis_offline and (not self.app_key or not self.app_secret):
            raise ValueError(f"KIS_{self.kis_mode.upper()}_APP_KEY/SECRET is required outside offline mode")
        return self

    @property
    def mode(self) -> KISMode:
        return self.kis_mode

    @property
    def offline(self) -> bool:
        return self.kis_offline

    @property
    def data_dir(self) -> Path:
        return self.kis_data_dir

    @property
    def rate_limit_per_second(self) -> float:
        return float(self.kis_rate_limit_per_second)

    @property
    def app_key(self) -> str | None:
        if self.kis_mode == "live":
            return self.kis_live_app_key or self.kis_app_key
        return self.kis_mock_app_key or self.kis_app_key

    @property
    def app_secret(self) -> str | None:
        if self.kis_mode == "live":
            return self.kis_live_app_secret or self.kis_app_secret
        return self.kis_mock_app_secret or self.kis_app_secret

    @property
    def account_no(self) -> str | None:
        if self.kis_mode == "live":
            return self.kis_live_account_no or self.kis_account_no
        return self.kis_mock_account_no or self.kis_account_no

    @property
    def base_url(self) -> str:
        if self.kis_mode == "live":
            return "https://openapi.koreainvestment.com:9443"
        return "https://openapivts.koreainvestment.com:29443"

    @property
    def current_price_tr_id(self) -> str:
        return "FHKST01010100"

    @property
    def daily_itemchart_tr_id(self) -> str:
        return "FHKST03010100"

    @property
    def holiday_tr_id(self) -> str:
        return "CTCA0903R"
