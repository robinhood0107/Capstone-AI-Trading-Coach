from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


NaverPageStatus = Literal["complete", "empty"]


class _NaverModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class NaverNewsItem(_NaverModel):
    """기사 본문 없이 sanitize된 검색 metadata allowlist만 보존한다."""

    title: str = Field(min_length=1, max_length=512)
    description: str = Field(min_length=1, max_length=2_048)
    original_url: str | None = Field(alias="originalUrl", default=None, max_length=2_048)
    naver_url: str | None = Field(alias="naverUrl", default=None, max_length=2_048)
    provider_pub_date: str = Field(
        alias="providerPubDate",
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
    )

    @model_validator(mode="after")
    def _require_one_safe_url(self) -> Self:
        if self.original_url is None and self.naver_url is None:
            raise ValueError("at least one sanitized metadata URL is required")
        return self


class NaverNewsPage(_NaverModel):
    """검색 응답의 provider count와 sanitize/drop 결과를 분리해 반환한다."""

    status: NaverPageStatus
    provider_total: int = Field(alias="providerTotal", ge=0, le=2_147_483_647)
    requested_display: int = Field(alias="requestedDisplay", ge=1, le=20)
    provider_display: int = Field(alias="providerDisplay", ge=0, le=20)
    received_count: int = Field(alias="receivedCount", ge=0, le=20)
    accepted_count: int = Field(alias="acceptedCount", ge=0, le=20)
    filtered_count: int = Field(alias="filteredCount", ge=0, le=20)
    redacted_url_count: int = Field(alias="redactedUrlCount", ge=0, le=40)
    items: list[NaverNewsItem]

    @model_validator(mode="after")
    def _validate_count_breakdown(self) -> Self:
        if self.accepted_count != len(self.items):
            raise ValueError("accepted count must equal normalized item count")
        if self.received_count != self.accepted_count + self.filtered_count:
            raise ValueError("received count must equal accepted plus filtered")
        if self.redacted_url_count > self.received_count * 2:
            raise ValueError("redacted URL count exceeds received URL fields")
        return self
