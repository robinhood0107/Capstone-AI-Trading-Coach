from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from app.async_worker.core import AsyncWork, AsyncWorkProcessor, AsyncWorkRepository


class FakeRepository(AsyncWorkRepository):
    def __init__(self, commit_outcome: str = "COMPLETED") -> None:
        self.commit_outcome = commit_outcome
        self.commits = 0
        self.failures: list[str] = []
        self.quarantines: list[str] = []

    def commit(self, work: AsyncWork, result_ref: str) -> str:
        self.commits += 1
        return self.commit_outcome

    def fail(self, work: AsyncWork, code: str, error_class: str) -> str:
        self.failures.append(code)
        return "FAILED"

    def quarantine(self, work: AsyncWork, code: str, error_class: str) -> bool:
        self.quarantines.append(code)
        return True


def _work(payload: dict[str, object] | None = None) -> AsyncWork:
    body = json.dumps(
        payload
        or {
            "jobId": "job_fixture_00000001",
            "artifactId": "artifact_fixture_00000001",
            "contentHash": "sha256:" + "a" * 64,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return AsyncWork(
        event_id="evt_fixture_00000001",
        event_type="artifact.ingest-requested.v1",
        schema_version=1,
        payload_hash="sha256:" + hashlib.sha256(body).hexdigest(),
        job_id="job_fixture_00000001",
        job_type="ARTIFACT_INGEST",
        payload_json=body,
        claim_token="00000000-0000-4000-8000-000000000001",
        transport="DB",
    )


def test_completed_and_duplicate_are_idempotent_outcomes() -> None:
    completed = FakeRepository("COMPLETED")
    first = AsyncWorkProcessor(completed).process(_work())
    assert first.outcome == "COMPLETED"
    assert first.result_ref is not None
    assert completed.commits == 1

    duplicate = FakeRepository("DUPLICATE")
    replay = AsyncWorkProcessor(duplicate).process(_work())
    assert replay.outcome == "DUPLICATE"
    assert replay.result_ref == first.result_ref


def test_same_event_different_hash_is_quarantined_without_domain_success() -> None:
    repository = FakeRepository("PAYLOAD_CONFLICT")
    result = AsyncWorkProcessor(repository).process(_work())
    assert result.outcome == "NEEDS_REVIEW"
    assert result.failure_code == "PAYLOAD_HASH_CONFLICT"
    assert repository.quarantines == ["PAYLOAD_HASH_CONFLICT"]


def test_schema_enum_hash_and_poison_bounds_fail_closed() -> None:
    cases = [
        replace(_work(), schema_version=2),
        replace(_work(), job_type="UNKNOWN"),
        replace(_work(), payload_hash="sha256:" + "0" * 64),
        _work({"jobId": "job_fixture_00000001", "unknown": "x"}),
    ]
    for work in cases:
        repository = FakeRepository()
        result = AsyncWorkProcessor(repository).process(work)
        assert result.outcome == "NEEDS_REVIEW"
        assert repository.commits == 0
        assert repository.quarantines == ["INVALID_EVENT_PAYLOAD"]


def test_retryable_repository_outcome_uses_one_bounded_failure_transition() -> None:
    repository = FakeRepository("CONFLICT")
    result = AsyncWorkProcessor(repository).process(_work())
    assert result.outcome == "FAILED"
    assert result.failure_code == "ASYNC_DB_RETRY"
    assert repository.failures == ["ASYNC_DB_RETRY"]
