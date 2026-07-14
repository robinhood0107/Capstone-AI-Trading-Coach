from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.data.ecos.errors import RegistryNotVerifiedError


class ECOSSeries(BaseModel):
    """ECOS metadata preflight로 검증해야 하는 일별 시계열 식별자를 표현한다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    series_id: str = Field(min_length=1, max_length=120)
    stat_code: str = Field(min_length=1, max_length=20)
    item_code1: str = Field(min_length=1, max_length=20)
    cycle: str = Field(pattern=r"^D$")
    name: str | None = Field(default=None, min_length=1, max_length=256)
    unit: str | None = Field(default=None, min_length=1, max_length=256)
    registry_verified_at: datetime | None = None
    verified: bool = False

    @field_validator("registry_verified_at")
    @classmethod
    def _require_utc_evidence(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value)
        ):
            raise ValueError("ECOS registry evidence timestamp must be UTC")
        return value


CANDIDATE_SERIES: tuple[ECOSSeries, ...] = (
    ECOSSeries(
        series_id="policy-rate",
        stat_code="722Y001",
        item_code1="0101000",
        cycle="D",
        verified=False,
    ),
    ECOSSeries(
        series_id="krw-usd-rate",
        stat_code="731Y001",
        item_code1="0000001",
        cycle="D",
        verified=False,
    ),
)


def verified_series(entries: Sequence[ECOSSeries]) -> tuple[ECOSSeries, ...]:
    """timestamp·표시 metadata까지 승인된 series만 network 경계로 전달한다."""
    result = tuple(entries)
    if not result or any(
        not entry.verified
        or entry.registry_verified_at is None
        or entry.name is None
        or entry.unit is None
        for entry in result
    ):
        raise RegistryNotVerifiedError("registry_not_verified")
    return result
