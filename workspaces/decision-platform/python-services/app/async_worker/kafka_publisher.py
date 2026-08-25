from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

import psycopg
from confluent_kafka import KafkaError, Producer
from psycopg.conninfo import conninfo_to_dict

from app.async_worker.kafka_security import (
    CONTRACT,
    KafkaEnvelopeSecurityError,
    KafkaEnvelopeSigner,
    deterministic_partition,
)
from app.async_worker.kafka_topics import BASE_TOPICS
from app.data._shared.bounded_json import (
    BoundedJsonError,
    BoundedJsonLimits,
    parse_bounded_json_bytes,
)

_WORKER_ID = "p1-kafka-outbox-publisher"
_INPUT_TOPICS = {
    "artifact.ingest-requested.v1",
    "rag.index-requested.v1",
    "model.eval-requested.v1",
}
_PARTITION_KEY = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_MAX_ENVELOPE_BYTES = 65_536
_REFERENCES_LIMITS = BoundedJsonLimits(
    max_bytes=32_768,
    max_depth=8,
    max_list_items=32,
    max_object_keys=64,
    max_text_codepoints=2_048,
    max_text_bytes=2_048,
    max_number_characters=64,
)


@dataclass(frozen=True, slots=True)
class ClaimedKafkaEvent:
    storage_event_id: str
    event_id: str
    event_type: str
    aggregate_id: str
    partition_key: str
    payload_json: bytes
    occurred_at: datetime
    schema_version: int
    topic_name: str
    claim_token: uuid.UUID
    attempt: int
    dlq: bool


class KafkaOutboxQueuePort(Protocol):
    def quarantine_unknown(self, limit: int) -> int: ...
    def claim(self, worker: str, limit: int, *, dlq: bool) -> list[ClaimedKafkaEvent]: ...
    def bind_payload_hash(self, event: ClaimedKafkaEvent, payload_hash: str) -> bool: ...
    def complete(self, event: ClaimedKafkaEvent) -> bool: ...
    def fail(self, event: ClaimedKafkaEvent, code: str) -> bool: ...
    def quarantine(self, event: ClaimedKafkaEvent, code: str) -> bool: ...


class KafkaProducerPort(Protocol):
    def produce(self, topic: str, **kwargs: Any) -> None: ...
    def flush(self, timeout: float | None = None) -> int: ...


class PostgresKafkaOutboxQueue:
    def __init__(self, database_dsn: str) -> None:
        if conninfo_to_dict(database_dsn).get("user") != "decision_outbox_publisher":
            raise ValueError("Kafka publisher requires the decision_outbox_publisher DSN")
        self._database_dsn = database_dsn

    def quarantine_unknown(self, limit: int) -> int:
        return int(self._scalar("SELECT p1_quarantine_unknown_kafka_outbox(%s)", (limit,)))

    def claim(self, worker: str, limit: int, *, dlq: bool) -> list[ClaimedKafkaEvent]:
        function = "p1_claim_kafka_dlq_outbox" if dlq else "p1_claim_kafka_outbox"
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {function}(%s,%s)", (worker, limit))
            return [
                ClaimedKafkaEvent(
                    storage_event_id=str(row[0]),
                    event_id=str(row[0]),
                    event_type=str(row[1]),
                    aggregate_id=str(row[3]),
                    partition_key=str(row[4]),
                    payload_json=json.dumps(
                        row[5], ensure_ascii=False, separators=(",", ":")
                    ).encode(),
                    occurred_at=row[6],
                    schema_version=int(row[8]),
                    topic_name=str(row[9]),
                    claim_token=row[10],
                    attempt=int(row[11]),
                    dlq=dlq,
                )
                for row in cursor.fetchall()
            ]

    def bind_payload_hash(self, event: ClaimedKafkaEvent, payload_hash: str) -> bool:
        return bool(
            self._scalar(
                "SELECT p1_bind_kafka_outbox_payload_hash(%s,%s,%s)",
                (event.storage_event_id, event.claim_token, payload_hash),
            )
        )

    def complete(self, event: ClaimedKafkaEvent) -> bool:
        function = "p1_complete_kafka_dlq_outbox" if event.dlq else "p1_complete_kafka_outbox"
        return bool(
            self._scalar(f"SELECT {function}(%s,%s)", (event.storage_event_id, event.claim_token))
        )

    def fail(self, event: ClaimedKafkaEvent, code: str) -> bool:
        function = "p1_fail_kafka_dlq_outbox" if event.dlq else "p1_fail_kafka_outbox"
        return bool(
            self._scalar(
                f"SELECT {function}(%s,%s,%s)", (event.storage_event_id, event.claim_token, code)
            )
        )

    def quarantine(self, event: ClaimedKafkaEvent, code: str) -> bool:
        return bool(
            self._scalar(
                "SELECT p1_quarantine_kafka_outbox(%s,%s,%s)",
                (event.storage_event_id, event.claim_token, code),
            )
        )

    def _scalar(self, query: str, values: tuple[Any, ...]) -> Any:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(query, values)
            row = cursor.fetchone()
            return None if row is None else row[0]

    def _connect(self) -> psycopg.Connection[Any]:
        connection: psycopg.Connection[Any] = psycopg.connect(
            self._database_dsn,
            autocommit=True,
            connect_timeout=2,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_user,session_user")
                if cursor.fetchone() != ("decision_outbox_publisher", "decision_outbox_publisher"):
                    raise ValueError("Kafka publisher effective database role mismatch")
        except Exception:
            connection.close()
            raise
        return connection


class KafkaOutboxPublisher:
    def __init__(
        self,
        queue: KafkaOutboxQueuePort,
        producer: KafkaProducerPort,
        signer: KafkaEnvelopeSigner,
        *,
        publish_timeout: float = 5.0,
        claim_page_size: int = 100,
    ) -> None:
        if not 1.0 <= publish_timeout <= 30.0 or claim_page_size != 100:
            raise ValueError("Kafka publisher bounds are invalid")
        self._queue = queue
        self._producer = producer
        self._signer = signer
        self._publish_timeout = publish_timeout
        self._claim_page_size = claim_page_size

    def poll_once(self) -> None:
        self._queue.quarantine_unknown(self._claim_page_size)
        for dlq in (False, True):
            for event in self._queue.claim(_WORKER_ID, self._claim_page_size, dlq=dlq):
                self._publish(event)

    def _publish(self, event: ClaimedKafkaEvent) -> None:
        try:
            envelope, partition = self._envelope(event)
            delivered: list[KafkaError | None] = []
            self._producer.produce(
                event.topic_name,
                key=event.partition_key.encode("ascii"),
                value=envelope,
                partition=partition,
                headers=[
                    ("event-type", event.event_type.encode("ascii")),
                    ("schema-version", b"1"),
                    ("attempt", str(event.attempt).encode("ascii")),
                ],
                on_delivery=lambda error, _message: delivered.append(error),
            )
            if self._producer.flush(self._publish_timeout) != 0 or delivered != [None]:
                raise RuntimeError("Kafka delivery was not acknowledged")
            if not self._queue.complete(event):
                raise RuntimeError("published outbox completion was fenced")
        except (BoundedJsonError, KafkaEnvelopeSecurityError, UnicodeError, ValueError, TypeError):
            self._queue.quarantine(event, "INVALID_EVENT_PAYLOAD")
        except Exception:
            self._queue.fail(event, "KAFKA_PUBLISH_FAILED")

    def _envelope(self, event: ClaimedKafkaEvent) -> tuple[bytes, int]:
        expected_topic = _topic_for(event.event_type, dlq=event.dlq)
        if (
            event.topic_name != expected_topic
            or event.schema_version != 1
            or event.attempt not in {1, 2, 3}
            or _PARTITION_KEY.fullmatch(event.partition_key) is None
        ):
            raise ValueError("invalid Kafka outbox identity")
        references = parse_bounded_json_bytes(event.payload_json, limits=_REFERENCES_LIMITS)
        if not isinstance(references, dict):
            raise ValueError("Kafka references must be an object")
        references.pop("ownerRef", None)
        canonical_references = json.dumps(
            references, ensure_ascii=False, separators=(",", ":")
        ).encode()
        payload_hash = "sha256:" + hashlib.sha256(canonical_references).hexdigest()
        if not event.dlq and not self._queue.bind_payload_hash(event, payload_hash):
            raise RuntimeError("Kafka payload hash binding was fenced")
        partition = deterministic_partition(event.topic_name, event.partition_key)
        signature = self._signer.sign(
            topic=event.topic_name,
            key=event.partition_key,
            event_type=event.event_type,
            payload_hash=payload_hash,
            partition=partition,
        )
        envelope = {
            "eventId": event.event_id,
            "eventType": event.event_type,
            "schemaVersion": event.schema_version,
            "occurredAt": event.occurred_at.isoformat().replace("+00:00", "Z"),
            "partitionKey": event.partition_key,
            "payloadHash": payload_hash,
            "references": references,
            "transport": {
                "contract": CONTRACT,
                "topic": event.topic_name,
                "key": event.partition_key,
                "partition": partition,
                "signature": signature,
            },
        }
        encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()
        if not 1 <= len(encoded) <= _MAX_ENVELOPE_BYTES:
            raise ValueError("Kafka envelope size is invalid")
        return encoded, partition


def _topic_for(event_type: str, *, dlq: bool) -> str:
    if event_type not in BASE_TOPICS:
        raise ValueError("event type is outside the exact topic catalog")
    return event_type.removesuffix(".v1") + ".dlq.v1" if dlq else event_type


def _producer_from_env() -> Producer:
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "").strip()
    username = os.environ.get("KAFKA_SASL_USERNAME", "").strip()
    password = os.environ.get("KAFKA_SASL_PASSWORD", "").strip()
    if (
        re.fullmatch(r"(?:127\.0\.0\.1|\[::1\]):[1-9][0-9]{0,4}", bootstrap) is None
        or username != "p1_outbox_publisher"
        or not 32 <= len(password.encode()) <= 128
    ):
        raise ValueError("Kafka publisher principal configuration is invalid")
    return Producer(
        {
            "bootstrap.servers": bootstrap,
            "client.id": _WORKER_ID,
            "security.protocol": "SASL_PLAINTEXT",
            "sasl.mechanism": "PLAIN",
            "sasl.username": username,
            "sasl.password": password,
            "enable.idempotence": True,
            "acks": "all",
        }
    )


def run() -> None:
    queue = PostgresKafkaOutboxQueue(os.environ.get("OUTBOX_PUBLISHER_DATABASE_DSN", "").strip())
    signer = KafkaEnvelopeSigner.from_base64url(
        os.environ.get("KAFKA_ENVELOPE_PRIVATE_KEY", "").strip()
    )
    publisher = KafkaOutboxPublisher(
        queue,
        cast(KafkaProducerPort, _producer_from_env()),
        signer,
    )
    while True:
        try:
            publisher.poll_once()
        except (psycopg.Error, RuntimeError, ValueError):
            pass
        time.sleep(5.0)


if __name__ == "__main__":
    run()
