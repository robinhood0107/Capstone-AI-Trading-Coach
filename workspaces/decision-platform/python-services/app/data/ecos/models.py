from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ECOSPageStatus = Literal["complete", "empty"]
ECOSSeriesStatus = Literal["complete", "empty", "failed"]
ECOSCoverage = Literal["complete", "partial", "empty"]

_CANONICAL_DECIMAL = r"^(?:0|-?(?:[1-9][0-9]*(?:\.[0-9]*[1-9])?|0\.[0-9]*[1-9]))$"


class ECOSObservation(BaseModel):
    """ECOS 일별 관측치를 날짜와 exponent 없는 canonical decimal 문자열로 표현한다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    time: str = Field(pattern=r"^[0-9]{8}$")
    value: str = Field(min_length=1, max_length=64, pattern=_CANONICAL_DECIMAL)

    @field_validator("time")
    @classmethod
    def _require_calendar_day(cls, value: str) -> str:
        _calendar_day(value)
        return value


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


class _ECOSSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class ECOSSeriesSnapshot(_ECOSSnapshotModel):
    """한 approved series의 요청 범위·상태·sanitized 관측치 계약을 표현한다."""

    series_id: str = Field(
        alias="seriesId",
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    stat_code: str = Field(
        alias="statCode",
        min_length=1,
        max_length=20,
        pattern=r"^[A-Za-z0-9]+$",
    )
    item_code1: str = Field(
        alias="itemCode1",
        min_length=1,
        max_length=20,
        pattern=r"^[A-Za-z0-9]+$",
    )
    cycle: Literal["D"]
    name: str = Field(min_length=1, max_length=256)
    unit: str = Field(min_length=1, max_length=256)
    requested_from: str = Field(alias="requestedFrom", pattern=r"^[0-9]{8}$")
    requested_to: str = Field(alias="requestedTo", pattern=r"^[0-9]{8}$")
    status: ECOSSeriesStatus
    observations: tuple[ECOSObservation, ...] = Field(max_length=400)

    @model_validator(mode="after")
    def _validate_series_contract(self) -> ECOSSeriesSnapshot:
        requested_from = _calendar_day(self.requested_from)
        requested_to = _calendar_day(self.requested_to)
        if requested_from > requested_to:
            raise ValueError("ECOS requested range is invalid")
        if self.status == "complete" and not self.observations:
            raise ValueError("complete ECOS series requires observations")
        if self.status != "complete" and self.observations:
            raise ValueError("non-complete ECOS series cannot contain observations")
        times = [observation.time for observation in self.observations]
        if times != sorted(times) or len(times) != len(set(times)):
            raise ValueError("ECOS observations must be sorted and unique")
        if any(not (self.requested_from <= value <= self.requested_to) for value in times):
            raise ValueError("ECOS observation is outside the requested range")
        return self


class ECOSMacroSnapshot(_ECOSSnapshotModel):
    """consumer schema와 동일한 두-series ECOS canonical snapshot DTO다."""

    schema_version: Literal[1] = Field(alias="schemaVersion")
    source: Literal["ecos"]
    as_of: date = Field(alias="asOf")
    retrieved_at: datetime = Field(alias="retrievedAt")
    registry_version: str = Field(
        alias="registryVersion",
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    registry_verified_at: datetime = Field(alias="registryVerifiedAt")
    series: tuple[ECOSSeriesSnapshot, ...] = Field(min_length=2, max_length=2)
    partial: bool
    coverage: ECOSCoverage

    @field_validator("retrieved_at", "registry_verified_at")
    @classmethod
    def _require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("ECOS snapshot timestamps must be UTC")
        return value

    @model_validator(mode="after")
    def _validate_snapshot_contract(self) -> ECOSMacroSnapshot:
        identities = [(entry.series_id, entry.stat_code, entry.item_code1) for entry in self.series]
        if len(set(identities)) != 2:
            raise ValueError("ECOS snapshot series must be unique")
        statuses = [entry.status for entry in self.series]
        failures = statuses.count("failed")
        if failures == len(statuses):
            raise ValueError("ECOS snapshot requires one successful series")
        expected_coverage: ECOSCoverage
        if failures:
            expected_coverage = "partial"
        elif all(status == "empty" for status in statuses):
            expected_coverage = "empty"
        else:
            expected_coverage = "complete"
        if self.coverage != expected_coverage or self.partial != (failures > 0):
            raise ValueError("ECOS partial and coverage fields do not match series status")
        return self


class ECOSCollectionResult(_ECOSSnapshotModel):
    """collector가 publisher와 CLI에 전달하는 snapshot·coverage·attempt 결과다."""

    snapshot: ECOSMacroSnapshot
    series_results: tuple[ECOSSeriesSnapshot, ...]
    partial: bool
    coverage: ECOSCoverage
    physical_attempt_count: int = Field(ge=0, le=8)
    duplicate_count: int = Field(default=0, ge=0, le=800)

    @model_validator(mode="after")
    def _match_snapshot(self) -> ECOSCollectionResult:
        if (
            self.series_results != self.snapshot.series
            or self.partial != self.snapshot.partial
            or self.coverage != self.snapshot.coverage
        ):
            raise ValueError("ECOS collection result does not match its snapshot")
        return self


def _calendar_day(value: str) -> date:
    try:
        parsed = datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise ValueError("ECOS date must be a valid calendar day") from None
    if parsed.strftime("%Y%m%d") != value:
        raise ValueError("ECOS date must be a valid calendar day")
    return parsed
