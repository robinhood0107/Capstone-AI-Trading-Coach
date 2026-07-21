from __future__ import annotations

from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class LogicalOperation(StrEnum):
    CURRENT_PRICE = "currentPrice"
    DAILY_BARS = "dailyBars"
    HOLIDAY = "holiday"


class PhysicalChannel(StrEnum):
    MARKET_DATA = "marketData"
    TOKEN_P = "tokenP"


class FailureCode(StrEnum):
    CREDENTIAL_BLOCKED = "CREDENTIAL_BLOCKED"
    LIMITER_BLOCKED = "LIMITER_BLOCKED"
    VALIDATION_BLOCKED = "VALIDATION_BLOCKED"
    TRANSPORT_UNAVAILABLE = "TRANSPORT_UNAVAILABLE"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    HTTP_RETRYABLE = "HTTP_RETRYABLE"
    HTTP_ERROR = "HTTP_ERROR"
    PROVIDER_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
    PROVIDER_ROUTING = "PROVIDER_ROUTING"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    PARSER_CONTRACT = "PARSER_CONTRACT"
    INGEST_CONFLICT = "INGEST_CONFLICT"
    UNKNOWN_INTERNAL = "UNKNOWN_INTERNAL"


class SkipCode(StrEnum):
    OFFLINE_FIXTURE = "OFFLINE_FIXTURE"
    DATASET_RANGE_PRESENT = "DATASET_RANGE_PRESENT"
    NON_TRADING_DAY = "NON_TRADING_DAY"
    MOCK_HOLIDAY_UNSUPPORTED = "MOCK_HOLIDAY_UNSUPPORTED"
    TOKEN_CACHE_HIT = "TOKEN_CACHE_HIT"


class CollectionRunStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class FailureCount(_FrozenModel):
    code: FailureCode
    count: StrictInt = Field(ge=1)


class LogicalOperationSummary(_FrozenModel):
    operation: LogicalOperation
    started: StrictInt = Field(ge=0)
    succeeded: StrictInt = Field(ge=0)
    terminal_failures: StrictInt = Field(alias="terminalFailures", ge=0)
    failure_codes: tuple[FailureCount, ...] = Field(alias="failureCodes")

    @model_validator(mode="after")
    def _validate_terminal_count(self) -> "LogicalOperationSummary":
        if self.started != self.succeeded + self.terminal_failures:
            raise ValueError("logical operation counts must balance")
        if self.terminal_failures != sum(item.count for item in self.failure_codes):
            raise ValueError("logical failure codes must balance")
        _require_canonical_failure_inventory(self.failure_codes, label="logical")
        return self


class PhysicalAttemptSummary(_FrozenModel):
    channel: PhysicalChannel
    attempts: StrictInt = Field(ge=0)
    successes: StrictInt = Field(ge=0)
    failures: StrictInt = Field(ge=0)
    recovered_failures: StrictInt = Field(alias="recoveredFailures", ge=0)
    failure_codes: tuple[FailureCount, ...] = Field(alias="failureCodes")

    @model_validator(mode="after")
    def _validate_attempt_count(self) -> "PhysicalAttemptSummary":
        if self.attempts != self.successes + self.failures:
            raise ValueError("physical attempt counts must balance")
        if self.failures != sum(item.count for item in self.failure_codes):
            raise ValueError("physical failure codes must balance")
        if self.recovered_failures > self.failures:
            raise ValueError("recovered failures cannot exceed failures")
        _require_canonical_failure_inventory(self.failure_codes, label="physical")
        return self


class SkipCount(_FrozenModel):
    code: SkipCode
    count: StrictInt = Field(ge=1)


class IngestDuplicateSummary(_FrozenModel):
    exact_rows: StrictInt = Field(alias="exactRows", ge=0)
    conflicting_groups: StrictInt = Field(alias="conflictingGroups", ge=0)


class CollectionRunSummary(_FrozenModel):
    """KIS 수집 실행의 logical/physical 집계만 보존하며 provider 원문 ledger는 만들지 않는다."""

    schema_version: StrictInt = Field(default=1, alias="schemaVersion", ge=1, le=1)
    sanitization_version: Literal["s1-5-kis-collection-accounting-v1"] = Field(
        default="s1-5-kis-collection-accounting-v1",
        alias="sanitizationVersion",
    )
    collection_run_id: UUID = Field(alias="collectionRunId")
    started_at: datetime = Field(alias="startedAt")
    completed_at: datetime = Field(alias="completedAt")
    status: CollectionRunStatus
    logical_operations: tuple[LogicalOperationSummary, ...] = Field(alias="logicalOperations")
    physical_attempts: tuple[PhysicalAttemptSummary, ...] = Field(alias="physicalAttempts")
    skips: tuple[SkipCount, ...]
    ingest_duplicates: IngestDuplicateSummary = Field(alias="ingestDuplicates")

    @model_validator(mode="after")
    def _validate_time_and_status(self) -> "CollectionRunSummary":
        if self.started_at.tzinfo is None or self.completed_at.tzinfo is None:
            raise ValueError("collection run timestamps must be timezone-aware")
        if self.completed_at < self.started_at:
            raise ValueError("collection run completion cannot precede start")
        if tuple(item.operation for item in self.logical_operations) != tuple(
            LogicalOperation
        ):
            raise ValueError("logical operation inventory must be canonical")
        if tuple(item.channel for item in self.physical_attempts) != tuple(
            PhysicalChannel
        ):
            raise ValueError("physical attempt inventory must be canonical")
        skip_codes = tuple(item.code for item in self.skips)
        canonical_skip_codes = tuple(code for code in SkipCode if code in skip_codes)
        if len(set(skip_codes)) != len(skip_codes) or skip_codes != canonical_skip_codes:
            raise ValueError("skip inventory must be unique and canonical")
        if (
            self.status == CollectionRunStatus.SUCCESS
            and self.ingest_duplicates.conflicting_groups
        ):
            raise ValueError("conflicting duplicates cannot produce a successful run")
        return self


@dataclass(frozen=True)
class LogicalOperationToken:
    token_id: int
    operation: LogicalOperation


@dataclass
class _LogicalState:
    started: int = 0
    succeeded: int = 0
    terminal_failures: int = 0
    failure_codes: Counter[FailureCode] | None = None

    def __post_init__(self) -> None:
        if self.failure_codes is None:
            self.failure_codes = Counter()


@dataclass
class _PhysicalState:
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    recovered_failures: int = 0
    unresolved: int = 0
    failure_codes: Counter[FailureCode] | None = None

    def __post_init__(self) -> None:
        if self.failure_codes is None:
            self.failure_codes = Counter()


class CollectionRunRecorder:
    """한 backfill run에 명시적으로 주입되는 aggregate recorder다.

    global singleton을 사용하지 않고 allowlisted enum과 정수 count만 받아 provider message, URL,
    credential, account 값이 summary model로 들어올 통로를 제거한다.
    """

    def __init__(self, *, run_id: UUID, started_at: datetime) -> None:
        if started_at.tzinfo is None:
            raise ValueError("collection run start must be timezone-aware")
        self._run_id = run_id
        self._started_at = started_at.astimezone(UTC)
        self._logical = {operation: _LogicalState() for operation in LogicalOperation}
        self._physical = {channel: _PhysicalState() for channel in PhysicalChannel}
        self._skips: Counter[SkipCode] = Counter()
        self._exact_duplicate_rows = 0
        self._conflicting_duplicate_groups = 0
        self._active: dict[int, LogicalOperationToken] = {}
        self._active_physical_failures: dict[int, Counter[PhysicalChannel]] = {}
        self._logical_context: ContextVar[tuple[int, ...]] = ContextVar(
            f"kis_collection_run_{run_id}",
            default=(),
        )
        self._next_token = 1
        self._lock = Lock()

    def start_logical(self, operation: LogicalOperation) -> LogicalOperationToken:
        if not isinstance(operation, LogicalOperation):
            raise ValueError("logical operation must be allowlisted")
        with self._lock:
            token = LogicalOperationToken(
                token_id=self._next_token,
                operation=operation,
            )
            self._next_token += 1
            self._active[token.token_id] = token
            self._active_physical_failures[token.token_id] = Counter()
            self._logical_context.set((*self._logical_context.get(), token.token_id))
            self._logical[operation].started += 1
            return token

    def succeed_logical(self, token: LogicalOperationToken) -> None:
        with self._lock:
            active, recovered_failures = self._pop_active(token)
            self._logical[active.operation].succeeded += 1
            for channel, recovered in recovered_failures.items():
                self._physical[channel].recovered_failures += recovered

    def fail_logical(
        self,
        token: LogicalOperationToken,
        code: FailureCode,
    ) -> None:
        if not isinstance(code, FailureCode):
            raise ValueError("logical failure code must be allowlisted")
        with self._lock:
            active, _ = self._pop_active(token)
            state = self._logical[active.operation]
            state.terminal_failures += 1
            assert state.failure_codes is not None
            state.failure_codes[code] += 1

    def record_physical_attempt(self, channel: PhysicalChannel) -> None:
        if not isinstance(channel, PhysicalChannel):
            raise ValueError("physical channel must be allowlisted")
        with self._lock:
            state = self._physical[channel]
            state.attempts += 1
            state.unresolved += 1

    def record_physical_success(self, channel: PhysicalChannel) -> None:
        with self._lock:
            state = self._physical[channel]
            self._require_unresolved(state)
            state.unresolved -= 1
            state.successes += 1

    def record_physical_failure(
        self,
        channel: PhysicalChannel,
        code: FailureCode,
    ) -> None:
        if not isinstance(code, FailureCode):
            raise ValueError("physical failure code must be allowlisted")
        with self._lock:
            state = self._physical[channel]
            self._require_unresolved(state)
            state.unresolved -= 1
            state.failures += 1
            assert state.failure_codes is not None
            state.failure_codes[code] += 1
            logical_stack = self._logical_context.get()
            if logical_stack and logical_stack[-1] in self._active_physical_failures:
                self._active_physical_failures[logical_stack[-1]][channel] += 1

    def record_skip(self, code: SkipCode) -> None:
        if not isinstance(code, SkipCode):
            raise ValueError("skip code must be allowlisted")
        with self._lock:
            self._skips[code] += 1

    def record_ingest_duplicates(self, *, exact_rows: int, conflicting_groups: int) -> None:
        if exact_rows < 0 or conflicting_groups < 0:
            raise ValueError("ingest duplicate counts must be non-negative")
        with self._lock:
            self._exact_duplicate_rows += exact_rows
            self._conflicting_duplicate_groups += conflicting_groups

    def snapshot(
        self,
        *,
        completed_at: datetime,
        status: CollectionRunStatus,
    ) -> CollectionRunSummary:
        if not isinstance(status, CollectionRunStatus):
            raise ValueError("collection run status must be allowlisted")
        with self._lock:
            if self._active:
                raise ValueError("logical operations must finish before snapshot")
            if any(state.unresolved for state in self._physical.values()):
                raise ValueError("physical attempts must finish before snapshot")
            return CollectionRunSummary(
                collectionRunId=self._run_id,
                startedAt=self._started_at,
                completedAt=completed_at.astimezone(UTC),
                status=status,
                logicalOperations=tuple(
                    self._logical_summary(operation) for operation in LogicalOperation
                ),
                physicalAttempts=tuple(
                    self._physical_summary(channel) for channel in PhysicalChannel
                ),
                skips=tuple(
                    SkipCount(code=code, count=self._skips[code])
                    for code in SkipCode
                    if self._skips[code]
                ),
                ingestDuplicates=IngestDuplicateSummary(
                    exactRows=self._exact_duplicate_rows,
                    conflictingGroups=self._conflicting_duplicate_groups,
                ),
            )

    def _pop_active(
        self,
        token: LogicalOperationToken,
    ) -> tuple[LogicalOperationToken, Counter[PhysicalChannel]]:
        active = self._active.get(token.token_id)
        if active != token:
            raise ValueError("logical operation token is not active")
        logical_stack = self._logical_context.get()
        if not logical_stack or logical_stack[-1] != token.token_id:
            raise ValueError("logical operation token is not current in this execution context")
        self._logical_context.set(logical_stack[:-1])
        del self._active[token.token_id]
        failures = self._active_physical_failures.pop(token.token_id)
        return active, failures

    @staticmethod
    def _require_unresolved(state: _PhysicalState) -> None:
        if state.unresolved <= 0:
            raise ValueError("physical outcome requires an unresolved attempt")

    def _logical_summary(self, operation: LogicalOperation) -> LogicalOperationSummary:
        state = self._logical[operation]
        assert state.failure_codes is not None
        return LogicalOperationSummary(
            operation=operation,
            started=state.started,
            succeeded=state.succeeded,
            terminalFailures=state.terminal_failures,
            failureCodes=_failure_counts(state.failure_codes),
        )

    def _physical_summary(self, channel: PhysicalChannel) -> PhysicalAttemptSummary:
        state = self._physical[channel]
        assert state.failure_codes is not None
        return PhysicalAttemptSummary(
            channel=channel,
            attempts=state.attempts,
            successes=state.successes,
            failures=state.failures,
            recoveredFailures=state.recovered_failures,
            failureCodes=_failure_counts(state.failure_codes),
        )


def stable_failure_code(error: BaseException) -> FailureCode:
    """예외 type만 allowlist에 매핑하고 provider/exception message는 읽거나 보존하지 않는다."""
    name = type(error).__name__
    if name in {"KISCredentialError", "KISTokenCacheError"}:
        return FailureCode.CREDENTIAL_BLOCKED
    if name in {"KISRateLimitUnavailable", "KISRateLimitWaitExceeded"}:
        return FailureCode.LIMITER_BLOCKED
    if name == "KISResponseTooLargeError":
        return FailureCode.RESPONSE_TOO_LARGE
    if name == "KISTransportError":
        return FailureCode.TRANSPORT_UNAVAILABLE
    if name == "KISProviderRateLimitError":
        return FailureCode.PROVIDER_RATE_LIMIT
    if name == "KISDistributionRetryableStatus":
        return FailureCode.PROVIDER_ROUTING
    if name == "KISRetryableStatus":
        return FailureCode.HTTP_RETRYABLE
    if name == "KISHttpError":
        return FailureCode.HTTP_ERROR
    if name == "KISResponseError":
        return FailureCode.PARSER_CONTRACT
    if name == "KISConflictingDuplicateError":
        return FailureCode.INGEST_CONFLICT
    if isinstance(error, ValueError):
        return FailureCode.VALIDATION_BLOCKED
    return FailureCode.UNKNOWN_INTERNAL


def _failure_counts(counts: Counter[FailureCode]) -> tuple[FailureCount, ...]:
    return tuple(
        FailureCount(code=code, count=counts[code])
        for code in FailureCode
        if counts[code]
    )


def _require_canonical_failure_inventory(
    counts: tuple[FailureCount, ...],
    *,
    label: str,
) -> None:
    observed = tuple(item.code for item in counts)
    canonical = tuple(code for code in FailureCode if code in observed)
    if len(set(observed)) != len(observed) or observed != canonical:
        raise ValueError(f"{label} failure code inventory must be unique and canonical")
