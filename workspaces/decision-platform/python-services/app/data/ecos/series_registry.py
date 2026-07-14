from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.data.ecos.errors import RegistryNotVerifiedError


class ECOSSeries(BaseModel):
    """ECOS metadata preflight로 검증해야 하는 일별 시계열 식별자를 표현한다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    series_id: str = Field(min_length=1, max_length=120)
    stat_code: str = Field(min_length=1, max_length=20)
    item_code1: str = Field(min_length=1, max_length=20)
    cycle: str = Field(pattern=r"^D$")
    verified: bool = False


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
    """metadata 승인된 series만 반환하며 하나라도 provisional이면 network 이전에 거부한다."""
    result = tuple(entries)
    if not result or any(not entry.verified for entry in result):
        raise RegistryNotVerifiedError("registry_not_verified")
    return result
