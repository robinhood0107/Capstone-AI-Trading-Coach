from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import re
from typing import Any, Protocol

from app.data._shared.bounded_json import BoundedJsonError, BoundedJsonLimits, parse_bounded_json_bytes


_EVENT_TYPES = {
    "rag.index-requested.v1": "RAG_INDEX",
    "artifact.ingest-requested.v1": "ARTIFACT_INGEST",
    "model.eval-requested.v1": "MODEL_EVAL",
}
_JOB_ID = re.compile(r"^job_[A-Za-z0-9_-]{8,96}$")
_EVENT_ID = re.compile(r"^evt_[A-Za-z0-9_-]{8,96}$")
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_TOKEN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_PARTITION_KEY = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_ALLOWED_KEYS = {
    "jobId",
    "ownerRef",
    "sourceId",
    "sourceRevisionId",
    "importTicketId",
    "profileId",
    "artifactId",
    "runId",
    "contentHash",
    "resultRef",
    "replayOf",
}
_PAYLOAD_JSON_LIMITS = BoundedJsonLimits(
    max_bytes=32_768,
    max_depth=8,
    max_list_items=32,
    max_object_keys=64,
    max_text_codepoints=2_048,
    max_text_bytes=2_048,
    max_number_characters=64,
)


class AsyncContractError(ValueError):
    """A poison message that must have zero domain materialization."""


class AsyncPayloadHashConflict(RuntimeError):
    """The same event identity was observed with different bytes."""


class AsyncRetryableError(RuntimeError):
    """A bounded retry may be attempted after the DB transaction rolled back."""


@dataclass(frozen=True)
class AsyncWork:
    event_id: str
    event_type: str
    schema_version: int
    payload_hash: str
    job_id: str
    job_type: str
    payload_json: bytes
    claim_token: str | None
    transport: str
    attempt: int = 1
    source_topic: str | None = None
    source_partition: int | None = None
    source_offset: int | None = None
    partition_key: str | None = None


@dataclass(frozen=True)
class AsyncWorkResult:
    outcome: str
    result_ref: str | None = None
    failure_code: str | None = None


class AsyncWorkRepository(Protocol):
    def commit(self, work: AsyncWork, result_ref: str) -> str: ...

    def fail(self, work: AsyncWork, code: str, error_class: str) -> str: ...

    def quarantine(self, work: AsyncWork, code: str, error_class: str) -> bool: ...


class AsyncPoisonRecorder(Protocol):
    def quarantine(self, work: AsyncWork, code: str, error_class: str) -> bool: ...


class AsyncWorkProcessor:
    def __init__(
        self,
        repository: AsyncWorkRepository,
        poison_recorder: AsyncPoisonRecorder | None = None,
    ) -> None:
        self._repository = repository
        self._poison_recorder = poison_recorder or repository

    def process(self, work: AsyncWork) -> AsyncWorkResult:
        try:
            validate_work(work)
            result_ref = _result_ref(work)
            outcome = self._repository.commit(work, result_ref)
            if outcome == "COMPLETED":
                return AsyncWorkResult("COMPLETED", result_ref=result_ref)
            if outcome == "DUPLICATE":
                return AsyncWorkResult("DUPLICATE", result_ref=result_ref)
            if outcome == "PAYLOAD_CONFLICT":
                raise AsyncPayloadHashConflict
            raise AsyncRetryableError
        except (AsyncContractError, AsyncPayloadHashConflict) as error:
            conflict = isinstance(error, AsyncPayloadHashConflict)
            recorded = self._poison_recorder.quarantine(
                work,
                "PAYLOAD_HASH_CONFLICT" if conflict else "INVALID_EVENT_PAYLOAD",
                "CONTRACT_VIOLATION",
            )
            if not recorded:
                return AsyncWorkResult("FAILED", failure_code="POISON_RECEIPT_UNAVAILABLE")
            return AsyncWorkResult(
                "NEEDS_REVIEW",
                failure_code="PAYLOAD_HASH_CONFLICT" if conflict else "INVALID_EVENT_PAYLOAD",
            )
        except AsyncRetryableError:
            if work.claim_token is not None:
                self._repository.fail(work, "ASYNC_DB_RETRY", "RETRYABLE_TRANSIENT")
            return AsyncWorkResult("FAILED", failure_code="ASYNC_DB_RETRY")


def validate_work(work: AsyncWork) -> dict[str, str]:
    if (
        not _EVENT_ID.fullmatch(work.event_id)
        or work.event_type not in _EVENT_TYPES
        or work.schema_version != 1
        or not _HASH.fullmatch(work.payload_hash)
        or not _JOB_ID.fullmatch(work.job_id)
        or work.job_type != _EVENT_TYPES[work.event_type]
        or work.transport not in {"DB", "KAFKA"}
        or work.attempt not in {1, 2, 3}
        or (work.source_topic is not None and work.source_topic != work.event_type)
        or (work.transport == "KAFKA" and (
            work.source_partition is None
            or work.source_partition not in range(0, 1024)
            or work.source_offset is None
            or work.source_offset < 0
        ))
        or work.partition_key is None
        or _PARTITION_KEY.fullmatch(work.partition_key) is None
        or len(work.payload_json) > 32_768
        or (work.transport == "DB" and (work.claim_token is None or not _TOKEN.fullmatch(work.claim_token)))
        or (work.claim_token is not None and not _TOKEN.fullmatch(work.claim_token))
    ):
        raise AsyncContractError
    actual_hash = "sha256:" + hashlib.sha256(work.payload_json).hexdigest()
    if not hmac.compare_digest(actual_hash, work.payload_hash):
        raise AsyncContractError
    try:
        payload = parse_bounded_json_bytes(work.payload_json, limits=_PAYLOAD_JSON_LIMITS)
    except BoundedJsonError as error:
        raise AsyncContractError from error
    _validate_json(payload, depth=1, counts={"keys": 0})
    if not isinstance(payload, dict) or payload.get("jobId") != work.job_id:
        raise AsyncContractError
    if set(payload) - _ALLOWED_KEYS:
        raise AsyncContractError
    required = {
        "RAG_INDEX": {"sourceId", "sourceRevisionId", "importTicketId", "profileId"},
        "ARTIFACT_INGEST": {"artifactId", "contentHash"},
        "MODEL_EVAL": {"runId", "contentHash"},
    }[work.job_type]
    if work.transport == "DB" and work.job_type == "RAG_INDEX":
        required.add("ownerRef")
    if work.transport == "KAFKA" and "ownerRef" in payload:
        raise AsyncContractError
    if not required.issubset(payload):
        raise AsyncContractError
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in payload.items()):
        raise AsyncContractError
    return payload


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AsyncContractError
        result[key] = value
    return result


def _validate_json(value: Any, *, depth: int, counts: dict[str, int]) -> None:
    if depth > 8:
        raise AsyncContractError
    if isinstance(value, dict):
        counts["keys"] += len(value)
        if counts["keys"] > 64:
            raise AsyncContractError
        for key, child in value.items():
            if len(key.encode()) > 2_048:
                raise AsyncContractError
            _validate_json(child, depth=depth + 1, counts=counts)
    elif isinstance(value, list):
        if len(value) > 32:
            raise AsyncContractError
        for child in value:
            _validate_json(child, depth=depth + 1, counts=counts)
    elif isinstance(value, str):
        if len(value.encode()) > 2_048:
            raise AsyncContractError
    elif value is None or isinstance(value, (bool, int, float)):
        return
    else:
        raise AsyncContractError


def _result_ref(work: AsyncWork) -> str:
    digest = hashlib.sha256(f"{work.event_id}|{work.job_id}|{work.payload_hash}".encode()).hexdigest()
    return f"async_result_{digest[:32]}"
