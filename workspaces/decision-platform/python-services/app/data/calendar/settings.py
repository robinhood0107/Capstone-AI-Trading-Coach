from __future__ import annotations

from dataclasses import dataclass

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class OpenDARTQuotaConfig:
    """운영자가 확인한 account limit에서 lower-only로 확정한 non-secret quota 상한이다."""

    daily_call_limit: int
    daily_call_budget: int
    max_calls_per_run: int
    max_symbols_per_run: int

    def __post_init__(self) -> None:
        values = (
            self.daily_call_limit,
            self.daily_call_budget,
            self.max_calls_per_run,
            self.max_symbols_per_run,
        )
        if any(type(value) is not int for value in values):
            raise ValueError("quota values must be integers")
        if self.daily_call_limit <= 0:
            raise ValueError("daily limit must be positive")
        max_budget = min(17_500, self.daily_call_limit * 7 // 8)
        if self.daily_call_budget <= 0 or self.daily_call_budget > max_budget:
            raise ValueError("daily budget exceeds the project safety cap")
        max_per_run = min(8_000, self.daily_call_budget)
        if self.max_calls_per_run <= 0 or self.max_calls_per_run > max_per_run:
            raise ValueError("per-run cap exceeds the project safety cap")
        if self.max_symbols_per_run <= 0:
            raise ValueError("max symbols must be positive")


class OpenDARTQuotaSettings(BaseSettings):
    """네 필수 online 환경값에 코드 기본값을 두지 않고 누락 시 collector를 닫는다."""

    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    daily_call_limit: int = Field(gt=0, alias="OPENDART_DAILY_CALL_LIMIT")
    daily_call_budget: int = Field(gt=0, alias="OPENDART_DAILY_CALL_BUDGET")
    max_calls_per_run: int = Field(gt=0, alias="OPENDART_MAX_CALLS_PER_RUN")
    max_symbols_per_run: int = Field(gt=0, alias="OPENDART_MAX_SYMBOLS_PER_RUN")

    @model_validator(mode="after")
    def _validate_joint_caps(self) -> OpenDARTQuotaSettings:
        self.to_config()
        return self

    def to_config(self) -> OpenDARTQuotaConfig:
        """validated settings를 business 계층에 비밀값 없는 immutable 값으로 넘긴다."""
        return OpenDARTQuotaConfig(
            daily_call_limit=self.daily_call_limit,
            daily_call_budget=self.daily_call_budget,
            max_calls_per_run=self.max_calls_per_run,
            max_symbols_per_run=self.max_symbols_per_run,
        )
