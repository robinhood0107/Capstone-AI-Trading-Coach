from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
import re
from typing import Any, Protocol, cast

from confluent_kafka import Consumer, KafkaError, KafkaException, Message

from app.async_worker.core import AsyncContractError, AsyncWork, AsyncWorkProcessor
from app.async_worker.postgres import PostgresAsyncWorkRepository, is_decision_worker_dsn


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


class KafkaMessage(Protocol):
    def topic(self) -> str: ...
    def partition(self) -> int: ...
    def offset(self) -> int: ...
    def key(self) -> bytes | None: ...
    def value(self) -> bytes | None: ...
    def headers(self) -> list[tuple[str, bytes | None]] | None: ...


class KafkaConsumerPort(Protocol):
    def commit(self, message: KafkaMessage, asynchronous: bool) -> Any: ...


class KafkaAsyncMessageHandler:
    def __init__(
        self,
        repository: PostgresAsyncWorkRepository,
        consumer: KafkaConsumerPort,
    ) -> None:
        self._repository = repository
        self._consumer = consumer
        self._processor = AsyncWorkProcessor(repository)

    def handle(self, message: KafkaMessage) -> str:
        raw = message.value() or b""
        try:
            work = decode_message(message)
            claim_token = self._repository.claim_job(work, _GROUP_ID)
            result = self._processor.process(replace(work, claim_token=claim_token))
            if result.outcome == "FAILED":
                return "RETRY"
            self._consumer.commit(message=message, asynchronous=False)
            return result.outcome
        except (AsyncContractError, UnicodeError, json.JSONDecodeError, ValueError):
            event_type = message.topic()
            if event_type not in _TOPICS:
                raise KafkaRetryStop("record outside the exact topic catalog must not be acknowledged")
            raw_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
            identity_hash = hashlib.sha256(
                f"{message.topic()}|{message.partition()}|{message.offset()}|{raw_hash}".encode()
            ).hexdigest()
            self._repository.record_poison(
                event_id=f"evt_poison_{identity_hash[:32]}",
                event_type=event_type,
                payload_hash=raw_hash,
                source_topic=event_type,
                source_partition=message.partition(),
                source_offset=message.offset(),
                attempt=_safe_attempt(message.headers()),
                failure_code="INVALID_EVENT_PAYLOAD",
            )
            self._consumer.commit(message=message, asynchronous=False)
            return "NEEDS_REVIEW"


def decode_message(message: KafkaMessage) -> AsyncWork:
    topic = message.topic()
    raw = message.value()
    if topic not in _TOPICS or raw is None or not 1 <= len(raw) <= _MAX_ENVELOPE_BYTES:
        raise AsyncContractError
    key = (message.key() or b"").decode("ascii", errors="strict")
    headers = _closed_headers(message.headers())
    if set(headers) != {"event-type", "schema-version", "attempt"}:
        raise AsyncContractError
    try:
        envelope = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_closed_object)
    except (UnicodeError, json.JSONDecodeError, AsyncContractError) as error:
        raise AsyncContractError from error
    if not isinstance(envelope, dict) or set(envelope) != {
        "eventId",
        "eventType",
        "schemaVersion",
        "occurredAt",
        "partitionKey",
        "payloadHash",
        "references",
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
    handler = KafkaAsyncMessageHandler(repository, cast(KafkaConsumerPort, consumer))
    consumer.subscribe(list(_TOPICS))
    try:
        while True:
            message: Message | None = consumer.poll(1.0)
            if message is None:
                continue
            error = message.error()
            if error is not None:
                if error.code() == KafkaError._PARTITION_EOF:
                    continue
                raise KafkaException(error)
            _handle_or_stop(handler, cast(KafkaMessage, message))
    finally:
        consumer.close()


def _settings_from_env() -> dict[str, Any]:
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "").strip()
    database_dsn = os.environ.get("ASYNC_WORKER_DATABASE_DSN", "").strip()
    partition_key = os.environ.get("ASYNC_PARTITION_HMAC_KEY", "").encode()
    if not bootstrap or not is_decision_worker_dsn(database_dsn) or not 32 <= len(partition_key) <= 128:
        raise ValueError("Kafka async worker configuration is incomplete")
    security = os.environ.get("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT").strip()
    if security != "PLAINTEXT" or not _is_single_loopback_endpoint(bootstrap):
        raise ValueError("this worker build supports only loopback PLAINTEXT Kafka")
    return {
        "database_dsn": database_dsn,
        "partition_key": partition_key,
        "consumer": {
            "bootstrap.servers": bootstrap,
            "group.id": _GROUP_ID,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
            "security.protocol": security,
            "max.partition.fetch.bytes": _MAX_ENVELOPE_BYTES,
            "fetch.message.max.bytes": _MAX_ENVELOPE_BYTES,
        },
    }


def _is_single_loopback_endpoint(value: str) -> bool:
    match = re.fullmatch(r"(?:127\.0\.0\.1|\[::1\]):([1-9][0-9]{0,4})", value)
    return match is not None and int(match.group(1)) <= 65_535


class KafkaRetryStop(RuntimeError):
    """Stops the consumer before a later same-partition offset can be committed."""


def _handle_or_stop(handler: KafkaAsyncMessageHandler, message: KafkaMessage) -> str:
    outcome = handler.handle(message)
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


if __name__ == "__main__":
    run()
