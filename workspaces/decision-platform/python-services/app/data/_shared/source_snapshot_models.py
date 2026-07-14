from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

_SNAPSHOT_PATH_PATTERN = re.compile(
    r"(?P<source>ecos|naver)/(?P<year>[0-9]{4})/(?P<month>[0-9]{2})/"
    r"(?P<day>[0-9]{2})/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}/snapshot\.json"
)


class _SnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class EcosCountBreakdown(_SnapshotModel):
    series_count: int = Field(alias="seriesCount", ge=0)
    observation_count: int = Field(alias="observationCount", ge=0)
    duplicate_count: int = Field(alias="duplicateCount", ge=0)


class NaverCountBreakdown(_SnapshotModel):
    query_count: int = Field(alias="queryCount", ge=0)
    accepted_item_count: int = Field(alias="acceptedItemCount", ge=0)
    filtered_item_count: int = Field(alias="filteredItemCount", ge=0)
    redacted_url_count: int = Field(alias="redactedUrlCount", ge=0)


class SourceProvenance(_SnapshotModel):
    documentation_url: HttpUrl = Field(alias="documentationUrl")
    policy_url: HttpUrl = Field(alias="policyUrl")


class SourceSnapshotManifest(_SnapshotModel):
    """source snapshot의 provenance·hash·보존·부분수집 상태를 strict DTO로 고정한다."""

    schema_version: Literal[1] = Field(alias="schemaVersion")
    source: Literal["ecos", "naver"]
    provider_profile: str = Field(alias="providerProfile", min_length=1, max_length=64)
    operation: str = Field(min_length=1, max_length=64)
    generated_at: datetime = Field(alias="generatedAt")
    as_of: date = Field(alias="asOf")
    snapshot_path: str = Field(alias="snapshotPath")
    snapshot_sha256: str = Field(alias="snapshotSha256", pattern=r"^[0-9a-f]{64}$")
    record_count: int = Field(alias="recordCount", ge=0)
    count_breakdown: EcosCountBreakdown | NaverCountBreakdown = Field(alias="countBreakdown")
    partial: bool
    coverage: Literal["complete", "partial", "empty"]
    deferred_queries: int = Field(alias="deferredQueries", ge=0)
    physical_attempt_count: int = Field(alias="physicalAttemptCount", ge=0)
    quota_policy_version: str = Field(alias="quotaPolicyVersion", min_length=1, max_length=64)
    provenance: SourceProvenance
    sanitization_version: str = Field(alias="sanitizationVersion", min_length=1, max_length=64)
    retention_days: int = Field(alias="retentionDays", gt=0)
    delete_owner: Literal["decision-platform:source-snapshot-retention"] = Field(
        alias="deleteOwner"
    )

    @field_validator("generated_at")
    @classmethod
    def _require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("generatedAt must be an aware UTC timestamp")
        return value

    @field_validator("snapshot_path")
    @classmethod
    def _require_canonical_snapshot_path(cls, value: str) -> str:
        if _SNAPSHOT_PATH_PATTERN.fullmatch(value) is None:
            raise ValueError("snapshotPath must be a canonical source snapshot path")
        return value

    @model_validator(mode="after")
    def _validate_source_contract(self) -> "SourceSnapshotManifest":
        match = _SNAPSHOT_PATH_PATTERN.fullmatch(self.snapshot_path)
        if match is None:
            raise ValueError("snapshotPath must be a canonical source snapshot path")
        partition = date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
        if match.group("source") != self.source or partition != self.as_of:
            raise ValueError("snapshotPath source/date partition must match the manifest")
        if self.partial != (self.coverage == "partial"):
            raise ValueError("partial and coverage must agree")
        if self.coverage == "empty" and self.record_count != 0:
            raise ValueError("empty coverage requires zero records")
        if self.source == "ecos":
            if not isinstance(self.count_breakdown, EcosCountBreakdown):
                raise ValueError("ECOS requires an ECOS count breakdown")
            if self.record_count != self.count_breakdown.observation_count:
                raise ValueError("ECOS recordCount must equal observationCount")
            if self.deferred_queries != 0:
                raise ValueError("ECOS does not support deferred queries")
        else:
            if not isinstance(self.count_breakdown, NaverCountBreakdown):
                raise ValueError("Naver requires a Naver count breakdown")
            if self.record_count != self.count_breakdown.accepted_item_count:
                raise ValueError("Naver recordCount must equal acceptedItemCount")
        return self
