from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import hmac
import json
import logging
import os
import re
import time
from typing import Any, Protocol, cast

from confluent_kafka import Consumer, KafkaError, KafkaException, Message, TopicPartition

from app.async_worker.core import AsyncContractError, AsyncWork, AsyncWorkProcessor
from app.async_worker.kafka_security import (
    KafkaEnvelopeSecurityError,
    KafkaEnvelopeVerifier,
)
from app.async_worker.postgres import PostgresAsyncWorkRepository, is_decision_worker_dsn
from app.async_worker.poison_recorder import (
    HttpPoisonRecorderClient,
    PoisonReceipt,
    PoisonReceiptError,
    PoisonRecorderPort,
)
from app.data._shared.bounded_json import (
    BoundedJsonError,
    BoundedJsonLimits,
    parse_bounded_json_bytes,
)


_TOPICS = (
    "artifact.ingest-requested.v1",
    "rag.index-requested.v1",
    "model.eval-requested.v1",
)
_EVENT_ID = re.compile(r"^evt_[A-Za-z0-9_-]{8,96}$")
_PARTITION_KEY = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_ENVELOPE_BYTES = 65_536
_GROUP_ID = "decision-python-async-v1"
_ENVELOPE_JSON_LIMITS = BoundedJsonLimits(
    max_bytes=_MAX_ENVELOPE_BYTES,
    max_depth=8,
    max_list_items=32,
    max_object_keys=64,
    max_text_codepoints=2_048,
    max_text_bytes=2_048,
    max_number_characters=64,
)


class KafkaMessage(Protocol):
    def topic(self) -> str: ...
    def partition(self) -> int: ...
    def offset(self) -> int: ...
    def key(self) -> bytes | None: ...
    def value(self) -> bytes | None: ...
    def headers(self) -> list[tuple[str, bytes | None]] | None: ...


class KafkaConsumerPort(Protocol):
    def commit(self, message: KafkaMessage, asynchronous: bool) -> Any: ...


class KafkaPoisonAdapter:
    def __init__(self, recorder: PoisonRecorderPort) -> None:
        self._recorder = recorder

    def quarantine(self, work: AsyncWork, code: str, _error_class: str) -> bool:
        if (
            work.source_topic is None
            or work.source_partition is None
            or work.source_offset is None
            or work.partition_key is None
        ):
            return False
        return self._recorder.record(
            PoisonReceipt(
                event_id=work.event_id,
                event_type=work.event_type,
                payload_hash=work.payload_hash,
                source_topic=work.source_topic,
                source_partition=work.source_partition,
                source_offset=work.source_offset,
                attempt=work.attempt,
                failure_code=code,
                partition_key=work.partition_key,
                job_id=work.job_id if work.claim_token is not None else None,
                claim_token=work.claim_token,
            )
        )


class KafkaAsyncMessageHandler:
    def __init__(
        self,
        repository: PostgresAsyncWorkRepository,
        consumer: KafkaConsumerPort,
        verifier: KafkaEnvelopeVerifier,
        poison_recorder: PoisonRecorderPort,
        poison_partition_key: bytes,
    ) -> None:
        if not 32 <= len(poison_partition_key) <= 128:
            raise ValueError("Kafka poison partition key is invalid")
        self._repository = repository
        self._consumer = consumer
        self._verifier = verifier
        self._poison_recorder = poison_recorder
        self._poison_partition_key = poison_partition_key
        self._processor = AsyncWorkProcessor(repository, KafkaPoisonAdapter(poison_recorder))

    def handle(self, message: KafkaMessage) -> str:
        raw = message.value() or b""
        try:
            work = decode_message(message, self._verifier)
            claim_token = self._repository.claim_job(work, _GROUP_ID)
            result = self._processor.process(replace(work, claim_token=claim_token))
            if result.outcome == "FAILED":
                return "RETRY"
            self._consumer.commit(message=message, asynchronous=False)
            return result.outcome
        except PoisonReceiptError:
            return "RETRY"
        except (
            AsyncContractError,
            KafkaEnvelopeSecurityError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
        ) as error:
            event_type = message.topic()
            if (
                event_type not in _TOPICS
                or message.partition() not in range(3)
                or message.offset() < 0
            ):
                raise KafkaRetryStop(
                    "record outside the exact topic catalog must not be acknowledged"
                )
            raw_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
            identity_hash = hashlib.sha256(
                f"{message.topic()}|{message.partition()}|{message.offset()}|{raw_hash}".encode()
            ).hexdigest()
            partition_key = (
                "hmac-sha256:"
                + hmac.new(
                    self._poison_partition_key,
                    f"poison:{identity_hash}".encode(),
                    hashlib.sha256,
                ).hexdigest()
            )
            receipt = PoisonReceipt(
                event_id=f"evt_poison_{identity_hash[:32]}",
                event_type=event_type,
                payload_hash=raw_hash,
                source_topic=event_type,
                source_partition=message.partition(),
                source_offset=message.offset(),
                attempt=_safe_attempt(message.headers()),
                failure_code="INVALID_EVENT_SIGNATURE"
                if isinstance(error, KafkaEnvelopeSecurityError)
                else "INVALID_EVENT_PAYLOAD",
                partition_key=partition_key,
            )
            try:
                if not self._poison_recorder.record(receipt):
                    return "RETRY"
            except PoisonReceiptError:
                return "RETRY"
            self._consumer.commit(message=message, asynchronous=False)
            return "NEEDS_REVIEW"


def decode_message(message: KafkaMessage, verifier: KafkaEnvelopeVerifier) -> AsyncWork:
    topic = message.topic()
    raw = message.value()
    if topic not in _TOPICS or raw is None or not 1 <= len(raw) <= _MAX_ENVELOPE_BYTES:
        raise AsyncContractError
    key = (message.key() or b"").decode("ascii", errors="strict")
    headers = _closed_headers(message.headers())
    if set(headers) != {"event-type", "schema-version", "attempt"}:
        raise AsyncContractError
    try:
        envelope = parse_bounded_json_bytes(raw, limits=_ENVELOPE_JSON_LIMITS)
    except BoundedJsonError as error:
        raise AsyncContractError from error
    if not isinstance(envelope, dict) or set(envelope) != {
        "eventId",
        "eventType",
        "schemaVersion",
        "occurredAt",
        "partitionKey",
        "payloadHash",
        "references",
        "transport",
    }:
        raise AsyncContractError
    event_id = envelope.get("eventId")
    event_type = envelope.get("eventType")
    partition_key = envelope.get("partitionKey")
    payload_hash = envelope.get("payloadHash")
    references = envelope.get("references")
    attempt = _parse_attempt(headers["attempt"])
    if (
        not isinstance(event_id, str)
        or _EVENT_ID.fullmatch(event_id) is None
        or event_type != topic
        or headers["event-type"] != topic
        or envelope.get("schemaVersion") != 1
        or headers["schema-version"] != "1"
        or not isinstance(partition_key, str)
        or _PARTITION_KEY.fullmatch(partition_key) is None
        or key != partition_key
        or not isinstance(payload_hash, str)
        or _HASH.fullmatch(payload_hash) is None
        or not isinstance(references, dict)
    ):
        raise AsyncContractError
    verifier.verify(
        envelope,
        actual_topic=topic,
        actual_key=key,
        actual_partition=message.partition(),
    )
    payload = json.dumps(references, ensure_ascii=False, separators=(",", ":")).encode()
    job_id = references.get("jobId")
    if not isinstance(job_id, str):
        raise AsyncContractError
    job_type = {
        "artifact.ingest-requested.v1": "ARTIFACT_INGEST",
        "rag.index-requested.v1": "RAG_INDEX",
        "model.eval-requested.v1": "MODEL_EVAL",
    }[event_type]
    return AsyncWork(
        event_id=event_id,
        event_type=event_type,
        schema_version=1,
        payload_hash=payload_hash,
        job_id=job_id,
        job_type=job_type,
        payload_json=payload,
        claim_token=None,
        transport="KAFKA",
        attempt=attempt,
        source_topic=topic,
        source_partition=message.partition(),
        source_offset=message.offset(),
        partition_key=partition_key,
    )


def run() -> None:
    settings = _settings_from_env()
    consumer = Consumer(settings["consumer"])
    repository = PostgresAsyncWorkRepository(settings["database_dsn"], settings["partition_key"])
    handler = KafkaAsyncMessageHandler(
        repository,
        cast(KafkaConsumerPort, consumer),
        settings["verifier"],
        settings["poison_recorder"],
        settings["partition_key"],
    )
    retries = PartitionRetryController()
    consumer.subscribe(list(_TOPICS))
    try:
        while True:
            retries.resume_due(consumer)
            message: Message | None = consumer.poll(1.0)
            if message is None:
                continue
            error = message.error()
            if error is not None:
                if error.code() == KafkaError._PARTITION_EOF:
                    continue
                raise KafkaException(error)
            try:
                _handle_or_stop(handler, cast(KafkaMessage, message))
            except KafkaRetryStop:
                retries.pause(consumer, cast(KafkaMessage, message))
    finally:
        consumer.close()


def _settings_from_env() -> dict[str, Any]:
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "").strip()
    database_dsn = os.environ.get("ASYNC_WORKER_DATABASE_DSN", "").strip()
    partition_key = os.environ.get("ASYNC_PARTITION_HMAC_KEY", "").encode()
    username = os.environ.get("KAFKA_SASL_USERNAME", "").strip()
    password = os.environ.get("KAFKA_SASL_PASSWORD", "").strip()
    if (
        not bootstrap
        or not is_decision_worker_dsn(database_dsn)
        or not 32 <= len(partition_key) <= 128
        or username != "p1_async_worker"
        or not 32 <= len(password.encode()) <= 128
    ):
        raise ValueError("Kafka async worker configuration is incomplete")
    security = os.environ.get("KAFKA_SECURITY_PROTOCOL", "SASL_PLAINTEXT").strip()
    if security != "SASL_PLAINTEXT" or not _is_single_loopback_endpoint(bootstrap):
        raise ValueError("Kafka worker requires loopback SASL_PLAINTEXT")
    verifier = KafkaEnvelopeVerifier.from_base64url(
        os.environ.get("KAFKA_ENVELOPE_PUBLIC_KEY", "").strip()
    )
    poison_recorder = HttpPoisonRecorderClient(
        os.environ.get("POISON_RECORDER_URL", "").strip(),
        os.environ.get("POISON_RECORDER_SHARED_SECRET", "").strip(),
    )
    return {
        "database_dsn": database_dsn,
        "partition_key": partition_key,
        "verifier": verifier,
        "poison_recorder": poison_recorder,
        "consumer": {
            "bootstrap.servers": bootstrap,
            "group.id": _GROUP_ID,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
            "security.protocol": security,
            "sasl.mechanism": "PLAIN",
            "sasl.username": username,
            "sasl.password": password,
            "max.partition.fetch.bytes": _MAX_ENVELOPE_BYTES,
            "fetch.message.max.bytes": _MAX_ENVELOPE_BYTES,
        },
    }


def _is_single_loopback_endpoint(value: str) -> bool:
    match = re.fullmatch(r"(?:127\.0\.0\.1|\[::1\]):([1-9][0-9]{0,4})", value)
    return match is not None and int(match.group(1)) <= 65_535


class KafkaRetryStop(RuntimeError):
    """Stops the consumer before a later same-partition offset can be committed."""


@dataclass(slots=True)
class _PausedPartition:
    offset: int
    resume_at: float
    failures: int


class PartitionRetryController:
    def __init__(self) -> None:
        self._paused: dict[tuple[str, int], _PausedPartition] = {}

    def pause(self, consumer: Consumer, message: KafkaMessage) -> None:
        identity = (message.topic(), message.partition())
        previous = self._paused.get(identity)
        failures = 1 if previous is None else min(previous.failures + 1, 3)
        delay = (1.0, 5.0, 10.0)[failures - 1]
        partition = TopicPartition(message.topic(), message.partition(), message.offset())
        consumer.pause([partition])
        consumer.seek(partition)
        self._paused[identity] = _PausedPartition(
            message.offset(), time.monotonic() + delay, failures
        )
        logger.warning("Kafka partition paused after retryable processing failure.")

    def resume_due(self, consumer: Consumer) -> None:
        now = time.monotonic()
        for identity, state in list(self._paused.items()):
            if state.resume_at > now:
                continue
            partition = TopicPartition(identity[0], identity[1], state.offset)
            consumer.seek(partition)
            consumer.resume([TopicPartition(identity[0], identity[1])])
            del self._paused[identity]


def _handle_or_stop(handler: KafkaAsyncMessageHandler, message: KafkaMessage) -> str:
    try:
        outcome = handler.handle(message)
    except (PoisonReceiptError, RuntimeError) as error:
        raise KafkaRetryStop("retryable Kafka processing failure") from error
    if outcome == "RETRY":
        raise KafkaRetryStop("retryable Kafka record must be redelivered before later offsets")
    return outcome


def _closed_headers(headers: list[tuple[str, bytes | None]] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, raw in headers or []:
        if key in result or raw is None or len(raw) > 128:
            raise AsyncContractError
        result[key] = raw.decode("ascii", errors="strict")
    return result


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AsyncContractError
        result[key] = value
    return result


def _parse_attempt(raw: str) -> int:
    if raw not in {"1", "2", "3"}:
        raise AsyncContractError
    return int(raw)


def _safe_attempt(headers: list[tuple[str, bytes | None]] | None) -> int:
    try:
        return _parse_attempt(_closed_headers(headers).get("attempt", "1"))
    except (AsyncContractError, UnicodeError):
        return 1


logger = logging.getLogger(__name__)


if __name__ == "__main__":
    run()
