from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from app.async_worker.kafka_publisher import ClaimedKafkaEvent, KafkaOutboxPublisher
from app.async_worker.kafka_security import (
    KafkaEnvelopeSigner,
    KafkaEnvelopeVerifier,
    deterministic_partition,
)


class FakeQueue:
    def __init__(self, event: ClaimedKafkaEvent) -> None:
        self.event = event
        self.bound: list[str] = []
        self.completed = 0
        self.failed = 0
        self.quarantined = 0

    def quarantine_unknown(self, limit: int) -> int:
        assert limit == 100
        return 0

    def claim(self, worker: str, limit: int, *, dlq: bool) -> list[ClaimedKafkaEvent]:
        assert worker == "p1-kafka-outbox-publisher" and limit == 100
        return [self.event] if not dlq else []

    def bind_payload_hash(self, event: ClaimedKafkaEvent, payload_hash: str) -> bool:
        assert event is self.event
        self.bound.append(payload_hash)
        return True

    def complete(self, event: ClaimedKafkaEvent) -> bool:
        assert event is self.event
        self.completed += 1
        return True

    def fail(self, event: ClaimedKafkaEvent, code: str) -> bool:
        assert event is self.event and code == "KAFKA_PUBLISH_FAILED"
        self.failed += 1
        return True

    def quarantine(self, event: ClaimedKafkaEvent, code: str) -> bool:
        assert event is self.event and code == "INVALID_EVENT_PAYLOAD"
        self.quarantined += 1
        return True


class FakeProducer:
    def __init__(self, *, delivery_error: object | None = None) -> None:
        self.records: list[tuple[str, dict[str, Any]]] = []
        self.delivery_error = delivery_error

    def produce(self, topic: str, **kwargs: Any) -> None:
        self.records.append((topic, kwargs))
        kwargs["on_delivery"](self.delivery_error, object())

    def flush(self, timeout: float | None = None) -> int:
        assert timeout == 5.0
        return 0


def _keys() -> tuple[KafkaEnvelopeSigner, KafkaEnvelopeVerifier]:
    private = Ed25519PrivateKey.generate()
    encode = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()  # noqa: E731
    return (
        KafkaEnvelopeSigner.from_base64url(
            encode(private.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption()))
        ),
        KafkaEnvelopeVerifier.from_base64url(
            encode(
                private.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
            )
        ),
    )


def _event(*, topic: str = "artifact.ingest-requested.v1") -> ClaimedKafkaEvent:
    return ClaimedKafkaEvent(
        storage_event_id="evt_fixture_00000001",
        event_id="evt_fixture_00000001",
        event_type="artifact.ingest-requested.v1",
        aggregate_id="job_fixture_00000001",
        partition_key="hmac-sha256:" + "c" * 64,
        payload_json=json.dumps(
            {
                "jobId": "job_fixture_00000001",
                "artifactId": "artifact_fixture_00000001",
                "contentHash": "sha256:" + "a" * 64,
                "ownerRef": "must-not-cross-kafka",
            },
            separators=(",", ":"),
        ).encode(),
        occurred_at=datetime(2026, 8, 24, tzinfo=UTC),
        schema_version=1,
        topic_name=topic,
        claim_token=uuid.UUID("123e4567-e89b-42d3-a456-426614174000"),
        attempt=1,
        dlq=False,
    )


def test_publisher_signs_exact_explicit_partition_and_completes_after_ack() -> None:
    signer, verifier = _keys()
    event = _event()
    queue = FakeQueue(event)
    producer = FakeProducer()
    KafkaOutboxPublisher(queue, producer, signer).poll_once()

    assert queue.completed == 1 and queue.failed == 0 and queue.quarantined == 0
    assert len(queue.bound) == 1 and len(producer.records) == 1
    topic, record = producer.records[0]
    envelope = json.loads(record["value"])
    expected_partition = deterministic_partition(topic, event.partition_key)
    assert record["partition"] == expected_partition
    assert b"must-not-cross-kafka" not in record["value"]
    verifier.verify(
        envelope,
        actual_topic=topic,
        actual_key=record["key"].decode(),
        actual_partition=record["partition"],
    )


def test_wrong_topic_is_quarantined_without_publish_or_completion() -> None:
    signer, _ = _keys()
    event = _event(topic="artifact.ingest-requested.retry.v1")
    queue = FakeQueue(event)
    producer = FakeProducer()
    KafkaOutboxPublisher(queue, producer, signer).poll_once()
    assert queue.quarantined == 1 and queue.completed == 0 and producer.records == []


def test_delivery_failure_never_completes_outbox() -> None:
    signer, _ = _keys()
    event = _event()
    queue = FakeQueue(event)
    producer = FakeProducer(delivery_error=object())
    KafkaOutboxPublisher(queue, producer, signer).poll_once()
    assert queue.failed == 1 and queue.completed == 0
