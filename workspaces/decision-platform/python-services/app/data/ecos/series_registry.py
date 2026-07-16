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


# preflight identity 상수 이름은 유지하고, 승인 timestamp와 verified flag로 활성 상태를 구분한다.
CANDIDATE_SERIES: tuple[ECOSSeries, ...] = (
    ECOSSeries(
        series_id="policy-rate",
        stat_code="722Y001",
        item_code1="0101000",
        cycle="D",
        name="한국은행 기준금리",
        unit="연%",
        registry_verified_at=datetime(2026, 7, 15, 6, 2, 19, 299552, tzinfo=UTC),
        verified=True,
    ),
    ECOSSeries(
        series_id="krw-usd-rate",
        stat_code="731Y001",
        item_code1="0000001",
        cycle="D",
        name="원/미국달러(매매기준율)",
        unit="원",
        registry_verified_at=datetime(2026, 7, 15, 6, 2, 19, 299552, tzinfo=UTC),
        verified=True,
    ),
)


def verified_series(entries: Sequence[ECOSSeries]) -> tuple[ECOSSeries, ...]:
    """A4 의미 승인과 exact 일치하는 source-controlled registry만 전달한다."""
    result = tuple(entries)
    if (
        not result
        or any(
            not entry.verified
            or entry.registry_verified_at is None
            or entry.name is None
            or entry.unit is None
            for entry in result
        )
        or result != CANDIDATE_SERIES
    ):
        raise RegistryNotVerifiedError("registry_not_verified")
    return result
