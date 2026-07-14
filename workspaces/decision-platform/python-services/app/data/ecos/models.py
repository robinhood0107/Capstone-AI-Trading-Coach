from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ECOSPageStatus = Literal["complete", "empty"]


class ECOSObservation(BaseModel):
    """ECOS 일별 관측치를 날짜와 exponent 없는 canonical decimal 문자열로 표현한다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    time: str = Field(pattern=r"^[0-9]{8}$")
    value: str = Field(min_length=1, max_length=64)


class StatisticSearchPage(BaseModel):
    """StatisticSearch 한 응답의 allowlist 관측치와 중복 처리 결과를 반환한다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ECOSPageStatus
    total_count: int = Field(ge=0)
    observations: list[ECOSObservation]
    duplicate_count: int = Field(default=0, ge=0)
    retryable: bool = False


class StatisticTableMetadata(BaseModel):
    """StatisticTableList에서 registry 승인에 필요한 공개 필드만 보존한다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stat_code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=256)
    cycle: str = Field(pattern=r"^D$")
    searchable: bool


class StatisticItemMetadata(BaseModel):
    """StatisticItemList에서 series identity와 표시 단위만 보존한다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stat_code: str = Field(min_length=1, max_length=20)
    item_code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=256)
    cycle: str = Field(pattern=r"^D$")
    unit: str = Field(min_length=1, max_length=256)
