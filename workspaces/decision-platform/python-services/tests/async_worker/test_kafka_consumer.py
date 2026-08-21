from __future__ import annotations

import hashlib
import json
from typing import Any

from app.async_worker.core import AsyncWork
from app.async_worker.kafka_consumer import KafkaAsyncMessageHandler, decode_message


class FakeMessage:
    def __init__(self, value: bytes, *, headers: list[tuple[str, bytes | None]] | None = None) -> None:
        self._value = value
        self._headers = headers or [
            ("event-type", b"artifact.ingest-requested.v1"),
            ("schema-version", b"1"),
            ("attempt", b"1"),
        ]

    def topic(self) -> str:
        return "artifact.ingest-requested.v1"

    def partition(self) -> int:
        return 1

    def offset(self) -> int:
        return 7

    def key(self) -> bytes:
        return ("hmac-sha256:" + "c" * 64).encode()

    def value(self) -> bytes:
        return self._value

    def headers(self) -> list[tuple[str, bytes | None]]:
        return self._headers


class FakeRepository:
    def __init__(self) -> None:
        self.commits: list[AsyncWork] = []
        self.poisons: list[dict[str, Any]] = []

    def claim_job(self, job_id: str, worker_name: str) -> str:
        assert job_id == "job_fixture_00000001"
        assert worker_name == "decision-python-async-v1"
        return "123e4567-e89b-42d3-a456-426614174000"

    def commit(self, work: AsyncWork, result_ref: str) -> str:
        self.commits.append(work)
        assert result_ref.startswith("async_result_")
        return "COMPLETED"

    def fail(self, work: AsyncWork, code: str, error_class: str) -> str:
        raise AssertionError((work, code, error_class))

    def quarantine(self, work: AsyncWork, code: str, error_class: str) -> bool:
        raise AssertionError((work, code, error_class))

    def record_poison(self, **values: Any) -> bool:
        self.poisons.append(values)
        return True


class FakeConsumer:
    def __init__(self) -> None:
        self.committed: list[FakeMessage] = []

    def commit(self, message: FakeMessage, asynchronous: bool) -> None:
        assert asynchronous is False
        self.committed.append(message)


class CrashBeforeDbCommitRepository(FakeRepository):
    def commit(self, work: AsyncWork, result_ref: str) -> str:
        raise RuntimeError("injected crash before DB commit")


class CrashAfterDbCommitConsumer(FakeConsumer):
    def commit(self, message: FakeMessage, asynchronous: bool) -> None:
        raise RuntimeError("injected crash after DB commit before offset ack")


def envelope() -> bytes:
    references = {
        "artifactId": "artifact_fixture_00000001",
        "contentHash": "sha256:" + "a" * 64,
        "jobId": "job_fixture_00000001",
        "ownerRef": "usr_demo_user",
    }
    payload = json.dumps(references, ensure_ascii=False, separators=(",", ":")).encode()
    return json.dumps(
        {
            "eventId": "evt_fixture_00000001",
            "eventType": "artifact.ingest-requested.v1",
            "schemaVersion": 1,
            "occurredAt": "2026-08-22T00:00:00Z",
            "partitionKey": "hmac-sha256:" + "c" * 64,
            "payloadHash": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "references": references,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def test_decode_and_manual_commit_after_domain_commit() -> None:
    message = FakeMessage(envelope())
    decoded = decode_message(message)
    assert decoded.transport == "KAFKA"
    assert decoded.claim_token is None

    repository = FakeRepository()
    consumer = FakeConsumer()
    assert KafkaAsyncMessageHandler(repository, consumer).handle(message) == "COMPLETED"  # type: ignore[arg-type]
    assert len(repository.commits) == 1
    assert consumer.committed == [message]


def test_poison_records_only_sanitized_identity_and_commits_offset() -> None:
    message = FakeMessage(b'{"token":"must-not-be-forwarded"}')
    repository = FakeRepository()
    consumer = FakeConsumer()
    assert KafkaAsyncMessageHandler(repository, consumer).handle(message) == "NEEDS_REVIEW"  # type: ignore[arg-type]
    assert consumer.committed == [message]
    assert len(repository.poisons) == 1
    poison = repository.poisons[0]
    assert set(poison) == {
        "event_id",
        "event_type",
        "payload_hash",
        "source_topic",
        "attempt",
        "failure_code",
    }
    assert "must-not-be-forwarded" not in json.dumps(poison)


def test_crash_before_db_commit_leaves_offset_unacked_for_redelivery() -> None:
    message = FakeMessage(envelope())
    repository = CrashBeforeDbCommitRepository()
    consumer = FakeConsumer()
    try:
        KafkaAsyncMessageHandler(repository, consumer).handle(message)  # type: ignore[arg-type]
    except RuntimeError as error:
        assert str(error) == "injected crash before DB commit"
    else:
        raise AssertionError("crash injection did not escape the handler")
    assert consumer.committed == []


def test_crash_after_db_commit_is_redelivered_and_idempotency_absorbs_duplicate() -> None:
    message = FakeMessage(envelope())
    repository = FakeRepository()
    try:
        KafkaAsyncMessageHandler(repository, CrashAfterDbCommitConsumer()).handle(message)  # type: ignore[arg-type]
    except RuntimeError as error:
        assert str(error) == "injected crash after DB commit before offset ack"
    else:
        raise AssertionError("ack crash injection did not escape the handler")
    assert len(repository.commits) == 1

    class DuplicateRepository(FakeRepository):
        def commit(self, work: AsyncWork, result_ref: str) -> str:
            self.commits.append(work)
            return "DUPLICATE"

    duplicate_repository = DuplicateRepository()
    recovered_consumer = FakeConsumer()
    assert KafkaAsyncMessageHandler(duplicate_repository, recovered_consumer).handle(message) == "DUPLICATE"  # type: ignore[arg-type]
    assert recovered_consumer.committed == [message]
