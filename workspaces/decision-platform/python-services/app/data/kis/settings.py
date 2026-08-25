from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, PositiveFloat, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

KISMode = Literal["mock", "live"]

KIS_REST_HARD_LIMIT_PER_SECOND: dict[KISMode, float] = {"mock": 1.0, "live": 18.0}
KIS_DEFAULT_REQUEST_INTERVAL_MILLISECONDS: dict[KISMode, int] = {"mock": 1_000, "live": 120}
KIS_MIN_REQUEST_INTERVAL_MILLISECONDS: dict[KISMode, int] = {"mock": 1_000, "live": 100}


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
    kis_rate_limit_per_second: PositiveFloat | None = None
    kis_request_interval_milliseconds: int | None = Field(default=None, ge=1)
    kis_rate_limit_max_wait_seconds: float = Field(default=10.0, gt=8.0, le=10.0)
    kis_data_dir: Path = Path("data/kis")
    kis_timeout_seconds: float = Field(default=10.0, gt=0, le=10.0)
    kis_retry_attempts: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def _validate_provider_rate_contract(self) -> KISSettings:
        hard_limit = KIS_REST_HARD_LIMIT_PER_SECOND[self.kis_mode]
        configured_rate = float(self.kis_rate_limit_per_second or hard_limit)
        if configured_rate > hard_limit:
            raise ValueError(
                f"KIS {self.kis_mode} rate exceeds the official REST limit of {hard_limit:g}/s"
            )

        minimum_interval = KIS_MIN_REQUEST_INTERVAL_MILLISECONDS[self.kis_mode]
        configured_interval = self.kis_request_interval_milliseconds
        if configured_interval is not None and configured_interval < minimum_interval:
            raise ValueError(
                f"KIS {self.kis_mode} minimum request interval is {minimum_interval}ms"
            )
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
        return float(
            self.kis_rate_limit_per_second or KIS_REST_HARD_LIMIT_PER_SECOND[self.kis_mode]
        )

    @property
    def request_interval_seconds(self) -> float:
        """운영자가 낮춘 초당 목표와 KIS의 mode별 최소 호출 간격 중 더 보수적인 값을 쓴다."""
        interval_ms = (
            self.kis_request_interval_milliseconds
            or KIS_DEFAULT_REQUEST_INTERVAL_MILLISECONDS[self.kis_mode]
        )
        return max(interval_ms / 1_000, 1 / self.rate_limit_per_second)

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
