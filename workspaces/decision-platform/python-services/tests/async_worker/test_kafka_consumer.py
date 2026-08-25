from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from app.async_worker.core import AsyncWork
from app.async_worker.kafka_consumer import (
    KafkaAsyncMessageHandler,
    KafkaRetryStop,
    _handle_or_stop,
    _is_single_loopback_endpoint,
    decode_message,
)
from app.async_worker.kafka_security import (
    CONTRACT,
    KafkaEnvelopeSigner,
    KafkaEnvelopeVerifier,
    deterministic_partition,
)
from app.async_worker.poison_recorder import PoisonReceipt, PoisonReceiptError

TOPIC = "artifact.ingest-requested.v1"
PARTITION_KEY = "hmac-sha256:" + "c" * 64
POISON_KEY = b"poison-partition-key-fixture-0000000001"


def _encoded(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _keys() -> tuple[KafkaEnvelopeSigner, KafkaEnvelopeVerifier]:
    private = Ed25519PrivateKey.generate()
    return (
        KafkaEnvelopeSigner.from_base64url(
            _encoded(private.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption()))
        ),
        KafkaEnvelopeVerifier.from_base64url(
            _encoded(
                private.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
            )
        ),
    )


SIGNER, VERIFIER = _keys()


class FakeMessage:
    def __init__(
        self,
        value: bytes,
        *,
        partition: int | None = None,
        headers: list[tuple[str, bytes | None]] | None = None,
    ) -> None:
        self._value = value
        self._partition = (
            deterministic_partition(TOPIC, PARTITION_KEY) if partition is None else partition
        )
        self._headers = headers or [
            ("event-type", b"artifact.ingest-requested.v1"),
            ("schema-version", b"1"),
            ("attempt", b"1"),
        ]

    def topic(self) -> str:
        return TOPIC

    def partition(self) -> int:
        return self._partition

    def offset(self) -> int:
        return 7

    def key(self) -> bytes:
        return PARTITION_KEY.encode()

    def value(self) -> bytes:
        return self._value

    def headers(self) -> list[tuple[str, bytes | None]]:
        return self._headers


class FakeRepository:
    def __init__(self) -> None:
        self.commits: list[AsyncWork] = []

    def claim_job(self, work: AsyncWork, worker_name: str) -> str:
        assert work.job_id == "job_fixture_00000001"
        assert work.event_id == "evt_fixture_00000001"
        assert work.partition_key == "hmac-sha256:" + "c" * 64
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


class FakeRecorder:
    def __init__(self, *, result: bool = True, raises: bool = False) -> None:
        self.result = result
        self.raises = raises
        self.receipts: list[PoisonReceipt] = []

    def record(self, receipt: PoisonReceipt) -> bool:
        receipt.validate()
        self.receipts.append(receipt)
        if self.raises:
            raise PoisonReceiptError
        return self.result


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


class RetryRepository(FakeRepository):
    def commit(self, work: AsyncWork, result_ref: str) -> str:
        return "CONFLICT"

    def fail(self, work: AsyncWork, code: str, error_class: str) -> str:
        return "FAILED"


def envelope(*, signer: KafkaEnvelopeSigner = SIGNER) -> bytes:
    references = {
        "artifactId": "artifact_fixture_00000001",
        "contentHash": "sha256:" + "a" * 64,
        "jobId": "job_fixture_00000001",
    }
    payload = json.dumps(references, ensure_ascii=False, separators=(",", ":")).encode()
    payload_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
    partition = deterministic_partition(TOPIC, PARTITION_KEY)
    signature = signer.sign(
        topic=TOPIC,
        key=PARTITION_KEY,
        event_type=TOPIC,
        payload_hash=payload_hash,
        partition=partition,
    )
    return json.dumps(
        {
            "eventId": "evt_fixture_00000001",
            "eventType": TOPIC,
            "schemaVersion": 1,
            "occurredAt": "2026-08-22T00:00:00Z",
            "partitionKey": PARTITION_KEY,
            "payloadHash": payload_hash,
            "references": references,
            "transport": {
                "contract": CONTRACT,
                "topic": TOPIC,
                "key": PARTITION_KEY,
                "partition": partition,
                "signature": signature,
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


def handler(
    repository: FakeRepository,
    consumer: FakeConsumer,
    recorder: FakeRecorder | None = None,
    verifier: KafkaEnvelopeVerifier = VERIFIER,
) -> KafkaAsyncMessageHandler:
    return KafkaAsyncMessageHandler(
        repository,  # type: ignore[arg-type]
        consumer,  # type: ignore[arg-type]
        verifier,
        recorder or FakeRecorder(),
        POISON_KEY,
    )


def test_decode_and_manual_commit_after_domain_commit() -> None:
    message = FakeMessage(envelope())
    decoded = decode_message(message, VERIFIER)
    assert decoded.transport == "KAFKA"
    assert decoded.claim_token is None

    repository = FakeRepository()
    consumer = FakeConsumer()
    assert handler(repository, consumer).handle(message) == "COMPLETED"
    assert len(repository.commits) == 1
    assert consumer.committed == [message]


def test_poison_records_only_sanitized_identity_and_commits_offset() -> None:
    message = FakeMessage(b'{"token":"must-not-be-forwarded"}')
    recorder = FakeRecorder()
    consumer = FakeConsumer()
    assert handler(FakeRepository(), consumer, recorder).handle(message) == "NEEDS_REVIEW"
    assert consumer.committed == [message]
    assert len(recorder.receipts) == 1
    assert "must-not-be-forwarded" not in repr(recorder.receipts[0])


def test_forged_signature_is_poison_before_db_claim() -> None:
    wrong_signer, _ = _keys()
    message = FakeMessage(envelope(signer=wrong_signer))
    recorder = FakeRecorder()
    consumer = FakeConsumer()
    assert handler(FakeRepository(), consumer, recorder).handle(message) == "NEEDS_REVIEW"
    assert recorder.receipts[0].failure_code == "INVALID_EVENT_SIGNATURE"
    assert consumer.committed == [message]


def test_actual_partition_mismatch_is_poison_before_db_claim() -> None:
    signed = deterministic_partition(TOPIC, PARTITION_KEY)
    message = FakeMessage(envelope(), partition=(signed + 1) % 3)
    recorder = FakeRecorder()
    consumer = FakeConsumer()
    assert handler(FakeRepository(), consumer, recorder).handle(message) == "NEEDS_REVIEW"
    assert recorder.receipts[0].failure_code == "INVALID_EVENT_SIGNATURE"
    assert consumer.committed == [message]


@pytest.mark.parametrize("recorder", (FakeRecorder(result=False), FakeRecorder(raises=True)))
def test_recorder_failure_leaves_poison_offset_uncommitted(recorder: FakeRecorder) -> None:
    message = FakeMessage(b"not-json")
    consumer = FakeConsumer()
    assert handler(FakeRepository(), consumer, recorder).handle(message) == "RETRY"
    assert consumer.committed == []


def test_excessive_json_depth_is_quarantined_before_job_claim() -> None:
    nested: Any = "value"
    for _ in range(10):
        nested = [nested]
    message = FakeMessage(
        json.dumps(
            {
                "eventId": "evt_fixture_00000001",
                "eventType": "artifact.ingest-requested.v1",
                "schemaVersion": 1,
                "occurredAt": "2026-08-22T00:00:00Z",
                "partitionKey": "hmac-sha256:" + "c" * 64,
                "payloadHash": "sha256:" + "a" * 64,
                "references": {"jobId": "job_fixture_00000001", "nested": nested},
            },
            separators=(",", ":"),
        ).encode()
    )
    repository = FakeRepository()
    recorder = FakeRecorder()
    consumer = FakeConsumer()

    assert handler(repository, consumer, recorder).handle(message) == "NEEDS_REVIEW"
    assert repository.commits == []
    assert len(recorder.receipts) == 1
    assert consumer.committed == [message]


def test_crash_before_db_commit_leaves_offset_unacked_for_redelivery() -> None:
    message = FakeMessage(envelope())
    repository = CrashBeforeDbCommitRepository()
    consumer = FakeConsumer()
    with pytest.raises(KafkaRetryStop, match="retryable Kafka processing failure"):
        _handle_or_stop(handler(repository, consumer), message)
    assert consumer.committed == []


@pytest.mark.parametrize(
    "value",
    (
        "127.0.0.1:9092,evil.example:9092",
        "[::1]:9092,127.0.0.1:9092",
        "localhost:9092",
        "127.0.0.1:0",
        "127.0.0.1:65536",
        "PLAINTEXT://127.0.0.1:9092",
    ),
)
def test_loopback_bootstrap_rejects_endpoint_smuggling(value: str) -> None:
    assert not _is_single_loopback_endpoint(value)


def test_loopback_bootstrap_accepts_one_literal_endpoint() -> None:
    assert _is_single_loopback_endpoint("127.0.0.1:9092")
    assert _is_single_loopback_endpoint("[::1]:9092")


def test_crash_after_db_commit_is_redelivered_and_idempotency_absorbs_duplicate() -> None:
    message = FakeMessage(envelope())
    repository = FakeRepository()
    with pytest.raises(KafkaRetryStop, match="retryable Kafka processing failure"):
        _handle_or_stop(handler(repository, CrashAfterDbCommitConsumer()), message)
    assert len(repository.commits) == 1

    class DuplicateRepository(FakeRepository):
        def commit(self, work: AsyncWork, result_ref: str) -> str:
            self.commits.append(work)
            return "DUPLICATE"

    duplicate_repository = DuplicateRepository()
    recovered_consumer = FakeConsumer()
    assert handler(duplicate_repository, recovered_consumer).handle(message) == "DUPLICATE"
    assert recovered_consumer.committed == [message]


def test_retryable_record_stops_before_any_later_offset_can_be_committed() -> None:
    message = FakeMessage(envelope())
    consumer = FakeConsumer()
    retry_handler = handler(RetryRepository(), consumer)
    with pytest.raises(KafkaRetryStop, match="must be redelivered"):
        _handle_or_stop(retry_handler, message)
    assert consumer.committed == []
