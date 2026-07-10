from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, PositiveFloat
from pydantic_settings import BaseSettings, SettingsConfigDict

KISMode = Literal["mock", "live"]


class KISSettings(BaseSettings):
    # 서비스 디렉터리 실행과 repo root 실행을 모두 지원한다. extra ignore는 다른 workspace env가 섞여도
    # S1.1 설정 로딩이 깨지지 않게 하려는 완충 장치다.
    model_config = SettingsConfigDict(
        env_file=(".env", "../../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    kis_mode: KISMode = "mock"
    kis_offline: bool = False
    kis_rate_limit_per_second: PositiveFloat = 1.0
    kis_data_dir: Path = Path("data/kis")
    kis_timeout_seconds: PositiveFloat = 10.0
    kis_retry_attempts: int = Field(default=3, ge=1, le=5)

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
    def base_url(self) -> str:
        # live는 실전 domain의 읽기 전용 시장데이터 조회를 뜻한다. live trading 활성화와 연결하지 않는다.
        if self.kis_mode == "live":
            return "https://openapi.koreainvestment.com:9443"
        return "https://openapivts.koreainvestment.com:29443"

    @property
    def current_price_tr_id(self) -> str:
        # 현재가/기간별시세 TR은 mock/live에서 동일한 read-only 국내주식 조회 계약으로만 사용한다.
        return "FHKST01010100"

    @property
    def daily_itemchart_tr_id(self) -> str:
        return "FHKST03010100"

    @property
    def holiday_tr_id(self) -> str:
        return "CTCA0903R"
