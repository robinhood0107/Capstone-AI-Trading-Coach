from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from enum import StrEnum
import re
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from app.data.kis.accounting import CollectionRunSummary
from app.data.quality.policy import (
    MAX_FILES,
    MAX_ROWS,
    MAX_SAMPLES,
    MAX_SAMPLES_PER_RULE,
    MAX_SESSIONS,
    MAX_SYMBOLS,
    METRIC_IDS,
)


_RELATIVE_IDENTIFIER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._/-]{0,238}[A-Za-z0-9])?")
_SAMPLE_RULE = re.compile(r"[A-Z][A-Z0-9_]{2,63}")
_SYMBOL = re.compile(r"[0-9]{6}")
_REVISION = re.compile(r"[a-f0-9]{7,64}")


class MetricStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    NOT_EVALUATED = "NOT_EVALUATED"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class QualityStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    NOT_EVALUATED = "NOT_EVALUATED"


class EvidenceCompleteness(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class ManifestReference(_FrozenModel):
    """로컬 절대경로 대신 root-relative ID와 검증된 SHA-256만 core로 전달한다."""

    identifier: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("identifier")
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        if (
            _RELATIVE_IDENTIFIER.fullmatch(value) is None
            or value.startswith("/")
            or "//" in value
            or any(item in {".", ".."} for item in value.split("/"))
        ):
            raise ValueError("manifest identifier must be a canonical relative path")
        return value


class SymbolDataset(_FrozenModel):
    """한 symbol file의 필요한 canonical 열만 immutable tuple로 metric core에 전달한다."""

    symbol: str
    columns: tuple[str, ...]
    rows: tuple[Mapping[str, object], ...]


class AnalysisContext(_FrozenModel):
    """orchestration이 검증한 시각·revision·manifest provenance를 순수 core에 주입한다."""

    evaluated_at: datetime
    software_revision: str
    window_start: date
    window_end: date
    expected_last_completed_xkrx_session: date
    sessions: tuple[date, ...]
    universe_symbols: tuple[str, ...]
    universe_manifest: ManifestReference
    dataset_manifest: ManifestReference
    collection_run: ManifestReference | None
    collection_summary: CollectionRunSummary | None
    dataset_file_count: StrictInt = Field(ge=0, le=MAX_FILES)

    @field_validator("evaluated_at")
    @classmethod
    def _normalize_evaluated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("evaluatedAt must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("software_revision")
    @classmethod
    def _validate_revision(cls, value: str) -> str:
        if _REVISION.fullmatch(value) is None:
            raise ValueError("software revision is invalid")
        return value

    @model_validator(mode="after")
    def _validate_context(self) -> "AnalysisContext":
        if self.window_start > self.window_end:
            raise ValueError("analysis window is invalid")
        if len(self.sessions) > MAX_SESSIONS or tuple(sorted(set(self.sessions))) != self.sessions:
            raise ValueError("sessions must be unique and sorted within the cap")
        if any(day < self.window_start or day > self.window_end for day in self.sessions):
            raise ValueError("sessions must remain inside the analysis window")
        if len(self.universe_symbols) > MAX_SYMBOLS:
            raise ValueError("universe symbol cap exceeded")
        if len(set(self.universe_symbols)) != len(self.universe_symbols) or any(
            _SYMBOL.fullmatch(symbol) is None for symbol in self.universe_symbols
        ):
            raise ValueError("universe symbols must be unique six-digit identifiers")
        if (self.collection_run is None) != (self.collection_summary is None):
            raise ValueError("collection reference and summary must be provided together")
        return self


class CalendarSummary(_FrozenModel):
    name: Literal["XKRX"] = "XKRX"
    timezone: Literal["Asia/Seoul"] = "Asia/Seoul"
    window_start: date = Field(alias="windowStart")
    window_end: date = Field(alias="windowEnd")
    expected_last_completed_xkrx_session: date = Field(
        alias="expectedLastCompletedXkrxSession"
    )


class InputProvenance(_FrozenModel):
    universe_manifest: ManifestReference = Field(alias="universeManifest")
    dataset_manifest: ManifestReference = Field(alias="datasetManifest")
    collection_run: ManifestReference | None = Field(alias="collectionRun")


class ReportCounts(_FrozenModel):
    symbols: StrictInt = Field(ge=0, le=MAX_SYMBOLS)
    sessions: StrictInt = Field(ge=0, le=MAX_SESSIONS)
    files: StrictInt = Field(ge=0, le=MAX_FILES)
    rows: StrictInt = Field(ge=0, le=MAX_ROWS)
    samples: StrictInt = Field(ge=0, le=MAX_SAMPLES)


class ReportStatus(_FrozenModel):
    execution_status: Literal["SUCCESS"] = Field(default="SUCCESS", alias="executionStatus")
    evidence_completeness: EvidenceCompleteness = Field(alias="evidenceCompleteness")
    quality_status: QualityStatus = Field(alias="qualityStatus")


class RateMetric(_FrozenModel):
    metric_id: str = Field(alias="metricId")
    status: MetricStatus
    numerator: StrictInt | None = Field(ge=0, le=MAX_ROWS)
    denominator: StrictInt | None = Field(ge=0, le=MAX_ROWS)
    rate_ppm: StrictInt | None = Field(alias="ratePpm", ge=0, le=1_000_000)
    sample_count: StrictInt = Field(alias="sampleCount", ge=0, le=MAX_SAMPLES_PER_RULE)

    @model_validator(mode="after")
    def _validate_rate_state(self) -> "RateMetric":
        if self.metric_id not in METRIC_IDS:
            raise ValueError("metricId is not allowlisted")
        if self.status in {MetricStatus.NOT_AVAILABLE, MetricStatus.NOT_APPLICABLE}:
            if any(value is not None for value in (self.numerator, self.denominator, self.rate_ppm)):
                raise ValueError("unavailable metric counts must be null")
            return self
        if self.status == MetricStatus.NOT_EVALUATED:
            if (self.numerator, self.denominator, self.rate_ppm) != (0, 0, None):
                raise ValueError("not-evaluated metric must use 0/0/null")
            return self
        if (
            self.numerator is None
            or self.denominator is None
            or self.denominator < 1
            or self.numerator > self.denominator
            or self.rate_ppm is None
        ):
            raise ValueError("evaluated metric requires bounded counts and rate")
        return self


class BoundedSample(_FrozenModel):
    rule_code: str = Field(alias="ruleCode")
    symbol: str
    session_date: date = Field(alias="sessionDate")
    derived: dict[str, StrictInt]

    @field_validator("rule_code")
    @classmethod
    def _validate_rule(cls, value: str) -> str:
        if _SAMPLE_RULE.fullmatch(value) is None:
            raise ValueError("sample ruleCode is invalid")
        return value

    @field_validator("symbol")
    @classmethod
    def _validate_symbol(cls, value: str) -> str:
        if _SYMBOL.fullmatch(value) is None:
            raise ValueError("sample symbol is invalid")
        return value

    @field_validator("derived")
    @classmethod
    def _validate_derived(cls, value: dict[str, StrictInt]) -> dict[str, StrictInt]:
        limits = {
            "lagSessions": (0, 3_000),
            "modifiedZMilli": (-1_000_000, 1_000_000),
            "returnPpm": (-1_000_000, 1_000_000),
            "occurrenceCount": (1, MAX_ROWS),
        }
        if not value or any(key not in limits for key in value):
            raise ValueError("sample derived fields are invalid")
        for key, number in value.items():
            lower, upper = limits[key]
            if isinstance(number, bool) or number < lower or number > upper:
                raise ValueError("sample derived value is outside its bound")
        return value


class DataClassification(_FrozenModel):
    classification: Literal["INTERNAL_SANITIZED_AGGREGATE"] = (
        "INTERNAL_SANITIZED_AGGREGATE"
    )
    raw_ohlcv_included: Literal[False] = Field(default=False, alias="rawOhlcvIncluded")
    provider_payload_included: Literal[False] = Field(
        default=False, alias="providerPayloadIncluded"
    )
    sensitive_data_included: Literal[False] = Field(default=False, alias="sensitiveDataIncluded")


class RetentionMetadata(_FrozenModel):
    owner: Literal["decision-platform:python-data-quality"] = (
        "decision-platform:python-data-quality"
    )
    policy_id: Literal["s1-5-quality-report-v1"] = Field(
        default="s1-5-quality-report-v1", alias="policyId"
    )
    ordinary_retention_days_after_evaluation_event: Literal[28] = Field(
        default=28, alias="ordinaryRetentionDaysAfterEvaluationEvent"
    )
    pinned: bool = False
    hold_reason: Literal[
        "HOLD_UNTIL_EVENT_DATE_CONFIGURED",
        "PINNED_UNTIL_FINAL_SUBMISSION_COMPLETE",
    ] | None = Field(default="HOLD_UNTIL_EVENT_DATE_CONFIGURED", alias="holdReason")


class KISDataQualityReport(_FrozenModel):
    """공개 원문 없이 aggregate와 bounded derived sample만 직렬화하는 S1.5 report다."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    metric_policy_version: Literal["s1-5-quality-report-v1"] = Field(
        default="s1-5-quality-report-v1",
        alias="metricPolicyVersion",
    )
    report_id: UUID = Field(alias="reportId")
    analysis_fingerprint: str = Field(alias="analysisFingerprint", pattern=r"^[a-f0-9]{64}$")
    evaluated_at: datetime = Field(alias="evaluatedAt")
    software_revision: str = Field(alias="softwareRevision", pattern=r"^[a-f0-9]{7,64}$")
    calendar: CalendarSummary
    input_provenance: InputProvenance = Field(alias="inputProvenance")
    counts: ReportCounts
    status: ReportStatus
    metrics: tuple[RateMetric, ...]
    bounded_samples: tuple[BoundedSample, ...] = Field(alias="boundedSamples")
    data_classification: DataClassification = Field(
        default_factory=DataClassification,
        alias="dataClassification",
    )
    retention: RetentionMetadata = Field(default_factory=RetentionMetadata)

    @field_validator("report_id")
    @classmethod
    def _require_uuid5(cls, value: UUID) -> UUID:
        if value.version != 5:
            raise ValueError("reportId must be UUIDv5")
        return value

    @field_validator("evaluated_at")
    @classmethod
    def _require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("evaluatedAt must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _validate_report_shape(self) -> "KISDataQualityReport":
        if tuple(item.metric_id for item in self.metrics) != METRIC_IDS:
            raise ValueError("report metrics must use the canonical order")
        if len(self.bounded_samples) > MAX_SAMPLES:
            raise ValueError("sample cap exceeded")
        if self.counts.samples != len(self.bounded_samples):
            raise ValueError("sample count must match boundedSamples")
        return self
