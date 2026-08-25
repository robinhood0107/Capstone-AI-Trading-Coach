from copy import deepcopy
from datetime import UTC, datetime
from threading import Barrier, Lock, Thread
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.data.kis.accounting import (
    CollectionRunRecorder,
    CollectionRunStatus,
    CollectionRunSummary,
    FailureCode,
    KISCallBudgetExceeded,
    LogicalOperation,
    PhysicalChannel,
    SkipCode,
    stable_failure_code,
)

RUN_ID = UUID("123e4567-e89b-42d3-a456-426614174000")
STARTED_AT = datetime(2026, 7, 21, 1, 0, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 7, 21, 1, 1, tzinfo=UTC)


def _logical(summary, operation: LogicalOperation):
    return next(item for item in summary.logical_operations if item.operation == operation)


def _physical(summary, channel: PhysicalChannel):
    return next(item for item in summary.physical_attempts if item.channel == channel)


def test_recorder_separates_logical_market_and_token_attempts_with_retry_recovery() -> None:
    recorder = CollectionRunRecorder(run_id=RUN_ID, started_at=STARTED_AT)
    operation = recorder.start_logical(LogicalOperation.DAILY_BARS)
    recorder.record_physical_attempt(PhysicalChannel.TOKEN_P)
    recorder.record_physical_success(PhysicalChannel.TOKEN_P)
    recorder.record_physical_attempt(PhysicalChannel.MARKET_DATA)
    recorder.record_physical_failure(
        PhysicalChannel.MARKET_DATA,
        FailureCode.HTTP_RETRYABLE,
    )
    recorder.record_physical_attempt(PhysicalChannel.MARKET_DATA)
    recorder.record_physical_success(PhysicalChannel.MARKET_DATA)
    recorder.succeed_logical(operation)

    summary = recorder.snapshot(
        completed_at=COMPLETED_AT,
        status=CollectionRunStatus.SUCCESS,
    )

    logical = _logical(summary, LogicalOperation.DAILY_BARS)
    market = _physical(summary, PhysicalChannel.MARKET_DATA)
    token = _physical(summary, PhysicalChannel.TOKEN_P)
    assert (logical.started, logical.succeeded, logical.terminal_failures) == (1, 1, 0)
    assert (market.attempts, market.successes, market.failures, market.recovered_failures) == (
        2,
        1,
        1,
        1,
    )
    assert (token.attempts, token.successes, token.failures) == (1, 1, 0)


def test_recorder_call_caps_fail_closed_without_incrementing_denominators() -> None:
    recorder = CollectionRunRecorder(
        run_id=RUN_ID,
        started_at=STARTED_AT,
        logical_caps={LogicalOperation.CURRENT_PRICE: 1},
        physical_caps={PhysicalChannel.MARKET_DATA: 1},
    )
    operation = recorder.start_logical(LogicalOperation.CURRENT_PRICE)
    recorder.record_physical_attempt(PhysicalChannel.MARKET_DATA)
    recorder.record_physical_success(PhysicalChannel.MARKET_DATA)
    recorder.succeed_logical(operation)

    with pytest.raises(KISCallBudgetExceeded, match="currentPrice") as logical_error:
        recorder.start_logical(LogicalOperation.CURRENT_PRICE)
    with pytest.raises(KISCallBudgetExceeded, match="marketData") as physical_error:
        recorder.record_physical_attempt(PhysicalChannel.MARKET_DATA)

    summary = recorder.snapshot(
        completed_at=COMPLETED_AT,
        status=CollectionRunStatus.SUCCESS,
    )
    assert _logical(summary, LogicalOperation.CURRENT_PRICE).started == 1
    assert _physical(summary, PhysicalChannel.MARKET_DATA).attempts == 1
    assert stable_failure_code(logical_error.value) == FailureCode.LIMITER_BLOCKED
    assert stable_failure_code(physical_error.value) == FailureCode.LIMITER_BLOCKED


def test_recorder_rejects_negative_boolean_and_unknown_call_caps() -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        CollectionRunRecorder(
            run_id=RUN_ID,
            started_at=STARTED_AT,
            logical_caps={LogicalOperation.CURRENT_PRICE: -1},
        )
    with pytest.raises(ValueError, match="non-negative integer"):
        CollectionRunRecorder(
            run_id=RUN_ID,
            started_at=STARTED_AT,
            physical_caps={PhysicalChannel.TOKEN_P: True},  # type: ignore[dict-item]
        )
    with pytest.raises(ValueError, match="allowlisted"):
        CollectionRunRecorder(
            run_id=RUN_ID,
            started_at=STARTED_AT,
            logical_caps={"currentPrice": 1},  # type: ignore[dict-item]
        )


def test_parser_failure_is_logical_terminal_failure_after_physical_success() -> None:
    recorder = CollectionRunRecorder(run_id=RUN_ID, started_at=STARTED_AT)
    operation = recorder.start_logical(LogicalOperation.CURRENT_PRICE)
    recorder.record_physical_attempt(PhysicalChannel.MARKET_DATA)
    recorder.record_physical_success(PhysicalChannel.MARKET_DATA)
    recorder.fail_logical(operation, FailureCode.PARSER_CONTRACT)

    summary = recorder.snapshot(
        completed_at=COMPLETED_AT,
        status=CollectionRunStatus.FAILED,
    )

    logical = _logical(summary, LogicalOperation.CURRENT_PRICE)
    market = _physical(summary, PhysicalChannel.MARKET_DATA)
    assert logical.terminal_failures == 1
    assert logical.failure_codes[0].code == FailureCode.PARSER_CONTRACT
    assert market.attempts == 1
    assert market.failures == 0


def test_presend_block_and_skip_leave_physical_denominator_zero() -> None:
    recorder = CollectionRunRecorder(run_id=RUN_ID, started_at=STARTED_AT)
    operation = recorder.start_logical(LogicalOperation.CURRENT_PRICE)
    recorder.fail_logical(operation, FailureCode.CREDENTIAL_BLOCKED)
    recorder.record_skip(SkipCode.OFFLINE_FIXTURE)
    recorder.record_skip(SkipCode.DATASET_RANGE_PRESENT)

    summary = recorder.snapshot(
        completed_at=COMPLETED_AT,
        status=CollectionRunStatus.FAILED,
    )

    market = _physical(summary, PhysicalChannel.MARKET_DATA)
    token = _physical(summary, PhysicalChannel.TOKEN_P)
    assert market.attempts == market.successes == market.failures == 0
    assert token.attempts == token.successes == token.failures == 0
    assert [(item.code, item.count) for item in summary.skips] == [
        (SkipCode.OFFLINE_FIXTURE, 1),
        (SkipCode.DATASET_RANGE_PRESENT, 1),
    ]


def test_summary_is_frozen_and_rejects_arbitrary_failure_messages() -> None:
    recorder = CollectionRunRecorder(run_id=RUN_ID, started_at=STARTED_AT)
    operation = recorder.start_logical(LogicalOperation.HOLIDAY)
    with pytest.raises(ValueError, match="allowlisted"):
        recorder.fail_logical(operation, "provider says secret-canary")  # type: ignore[arg-type]
    recorder.fail_logical(operation, FailureCode.PROVIDER_ERROR)
    summary = recorder.snapshot(
        completed_at=COMPLETED_AT,
        status=CollectionRunStatus.FAILED,
    )

    with pytest.raises(ValidationError):
        summary.status = CollectionRunStatus.SUCCESS  # type: ignore[misc]
    serialized = summary.model_dump_json(by_alias=True)
    assert "secret-canary" not in serialized
    assert "msg1" not in serialized


def test_conflicting_ingest_duplicate_forces_failed_summary() -> None:
    recorder = CollectionRunRecorder(run_id=RUN_ID, started_at=STARTED_AT)
    recorder.record_ingest_duplicates(exact_rows=2, conflicting_groups=1)

    with pytest.raises(ValueError, match="conflicting"):
        recorder.snapshot(
            completed_at=COMPLETED_AT,
            status=CollectionRunStatus.SUCCESS,
        )

    summary = recorder.snapshot(
        completed_at=COMPLETED_AT,
        status=CollectionRunStatus.FAILED,
    )
    assert summary.ingest_duplicates.exact_rows == 2
    assert summary.ingest_duplicates.conflicting_groups == 1


def test_overlapping_logical_operations_do_not_double_count_recovery() -> None:
    recorder = CollectionRunRecorder(run_id=RUN_ID, started_at=STARTED_AT)
    started = Barrier(2)
    finish = Barrier(2)
    errors: list[BaseException] = []
    guard = Lock()

    def recovered_operation() -> None:
        try:
            token = recorder.start_logical(LogicalOperation.CURRENT_PRICE)
            started.wait(timeout=2)
            recorder.record_physical_attempt(PhysicalChannel.MARKET_DATA)
            recorder.record_physical_failure(
                PhysicalChannel.MARKET_DATA,
                FailureCode.HTTP_RETRYABLE,
            )
            recorder.record_physical_attempt(PhysicalChannel.MARKET_DATA)
            recorder.record_physical_success(PhysicalChannel.MARKET_DATA)
            finish.wait(timeout=2)
            recorder.succeed_logical(token)
        except BaseException as error:
            with guard:
                errors.append(error)

    def operation_without_attempts() -> None:
        try:
            token = recorder.start_logical(LogicalOperation.DAILY_BARS)
            started.wait(timeout=2)
            finish.wait(timeout=2)
            recorder.succeed_logical(token)
        except BaseException as error:
            with guard:
                errors.append(error)

    threads = [Thread(target=recovered_operation), Thread(target=operation_without_attempts)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert not errors
    summary = recorder.snapshot(completed_at=COMPLETED_AT, status=CollectionRunStatus.SUCCESS)
    market = _physical(summary, PhysicalChannel.MARKET_DATA)
    assert (market.failures, market.recovered_failures) == (1, 1)


def test_summary_rejects_unknown_version_and_noncanonical_enum_inventory() -> None:
    summary = CollectionRunRecorder(run_id=RUN_ID, started_at=STARTED_AT).snapshot(
        completed_at=COMPLETED_AT,
        status=CollectionRunStatus.SUCCESS,
    )
    payload = summary.model_dump(mode="json", by_alias=True)

    unknown_version = dict(payload)
    unknown_version["sanitizationVersion"] = "unknown-accounting-version"
    with pytest.raises(ValidationError):
        CollectionRunSummary.model_validate(unknown_version)

    missing_logical = dict(payload)
    missing_logical["logicalOperations"] = payload["logicalOperations"][:-1]
    with pytest.raises(ValidationError, match="logical operation inventory"):
        CollectionRunSummary.model_validate(missing_logical)

    reversed_physical = dict(payload)
    reversed_physical["physicalAttempts"] = list(reversed(payload["physicalAttempts"]))
    with pytest.raises(ValidationError, match="physical attempt inventory"):
        CollectionRunSummary.model_validate(reversed_physical)

    duplicate_failures = deepcopy(payload)
    duplicate_failures["logicalOperations"][0].update(
        {
            "started": 2,
            "succeeded": 0,
            "terminalFailures": 2,
            "failureCodes": [
                {"code": "HTTP_ERROR", "count": 1},
                {"code": "HTTP_ERROR", "count": 1},
            ],
        }
    )
    with pytest.raises(ValidationError, match="failure code inventory"):
        CollectionRunSummary.model_validate(duplicate_failures)

    duplicate_skips = deepcopy(payload)
    duplicate_skips["skips"] = [
        {"code": "OFFLINE_FIXTURE", "count": 1},
        {"code": "OFFLINE_FIXTURE", "count": 1},
    ]
    with pytest.raises(ValidationError, match="skip inventory"):
        CollectionRunSummary.model_validate(duplicate_skips)
