"""S5.6 physical handoff의 append-only progress와 bounded resume packet."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Mapping, cast

from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.bootstrap_control import BootstrapCallReceipt
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.source_bundle import SourceChunkReceipt, parse_source_chunk_receipt


JOURNAL_FILENAME = "progress.jsonl"
MAX_JOURNAL_BYTES = 16 * 1024 * 1024
RESUME_PACKET_VERSION = "s5-production-bootstrap-resume-v1"
_EVENT_FIELDS = frozenset(
    {"eventVersion", "ordinal", "state", "provider", "operationId", "querySha256", "chunk"}
)


@dataclass(frozen=True, slots=True)
class JournalAttempt:
    ordinal: int
    provider: str
    operation_id: str
    query_sha256: str
    state: str
    chunk: SourceChunkReceipt | None


@dataclass(frozen=True, slots=True)
class ResumePacket:
    content: bytes
    sha256: str
    bootstrap_packet_sha256: str
    failed_query_sha256: str


class BootstrapJournal:
    """한 run root의 intent/terminal event를 fsync append하고 재개 상태를 검증한다."""

    def __init__(self, root: Path) -> None:
        self._path = root / JOURNAL_FILENAME
        self._attempts = _read_attempts(self._path) if self._path.exists() else ()

    @property
    def consumed_receipts(self) -> list[BootstrapCallReceipt]:
        return [
            BootstrapCallReceipt(
                ordinal=attempt.ordinal,
                provider=attempt.provider,
                operation_id=attempt.operation_id,
                query_key_sha256=attempt.query_sha256,
                success=attempt.state == "SUCCEEDED",
            )
            for attempt in self._attempts
        ]

    @property
    def failed_attempt(self) -> JournalAttempt | None:
        failures = [attempt for attempt in self._attempts if attempt.state == "FAILED"]
        if not failures:
            return None
        succeeded = {attempt.query_sha256 for attempt in self._attempts if attempt.state == "SUCCEEDED"}
        unresolved = [attempt for attempt in failures if attempt.query_sha256 not in succeeded]
        return unresolved[-1] if unresolved else None

    def completed_chunk(self, query_sha256: str) -> SourceChunkReceipt | None:
        matches = [
            attempt.chunk
            for attempt in self._attempts
            if attempt.query_sha256 == query_sha256 and attempt.state == "SUCCEEDED"
        ]
        return matches[-1] if matches else None

    def token_completed(self, query_sha256: str) -> bool:
        return any(
            attempt.query_sha256 == query_sha256
            and attempt.state == "SUCCEEDED"
            and attempt.chunk is None
            for attempt in self._attempts
        )

    def begin(self, *, provider: str, operation_id: str, query_sha256: str) -> int:
        prior = [attempt for attempt in self._attempts if attempt.query_sha256 == query_sha256]
        if any(attempt.state == "SUCCEEDED" for attempt in prior):
            raise LightGbmContractError("successful bootstrap query cannot be called again")
        if len(prior) >= 2 or (prior and prior[-1].state != "FAILED"):
            raise LightGbmContractError("bootstrap resume attempt is unavailable")
        ordinal = len(self._attempts) + 1
        self._append(
            {
                "eventVersion": "s5-bootstrap-progress-v1",
                "ordinal": ordinal,
                "state": "INTENT",
                "provider": provider,
                "operationId": operation_id,
                "querySha256": query_sha256,
            },
            allow_incomplete=True,
        )
        return ordinal

    def finish(
        self,
        *,
        ordinal: int,
        provider: str,
        operation_id: str,
        query_sha256: str,
        success: bool,
        chunk: SourceChunkReceipt | None,
    ) -> None:
        self._append(
            {
                "eventVersion": "s5-bootstrap-progress-v1",
                "ordinal": ordinal,
                "state": "SUCCEEDED" if success else "FAILED",
                "provider": provider,
                "operationId": operation_id,
                "querySha256": query_sha256,
                **({"chunk": chunk.as_dict()} if chunk is not None else {}),
            }
        )

    def _append(
        self, event: Mapping[str, object], *, allow_incomplete: bool = False
    ) -> None:
        line = canonical_json_bytes(dict(event)).removesuffix(b"\n") + b"\n"
        descriptor = os.open(
            self._path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or metadata.st_size + len(line) > MAX_JOURNAL_BYTES
            ):
                raise LightGbmContractError("bootstrap progress journal boundary is invalid")
            offset = 0
            while offset < len(line):
                written = os.write(descriptor, line[offset:])
                if written <= 0:
                    raise LightGbmContractError(
                        "bootstrap progress journal write made no progress"
                    )
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if not allow_incomplete:
            self._attempts = _read_attempts(self._path)


def build_resume_packet(
    *, bootstrap_packet_sha256: str, journal: BootstrapJournal, total_cap: int
) -> ResumePacket:
    """정확히 한 unresolved failure와 remaining cumulative budget만 결속한다."""

    failed = journal.failed_attempt
    consumed = len(journal.consumed_receipts)
    if failed is None or consumed >= total_cap:
        raise LightGbmContractError("bootstrap failure has no bounded resume authority")
    payload = {
        "resumePacketVersion": RESUME_PACKET_VERSION,
        "bootstrapPacketSha256": bootstrap_packet_sha256,
        "failedQuerySha256": failed.query_sha256,
        "provider": failed.provider,
        "operationId": failed.operation_id,
        "consumedPhysicalCalls": consumed,
        "remainingPhysicalCalls": total_cap - consumed,
        "retryOrdinal": 1,
    }
    content = canonical_json_bytes(payload)
    return ResumePacket(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        bootstrap_packet_sha256=bootstrap_packet_sha256,
        failed_query_sha256=failed.query_sha256,
    )


def validate_resume_packet(
    content: bytes,
    *,
    expected_sha256: str,
    bootstrap_packet_sha256: str,
    journal: BootstrapJournal,
    total_cap: int,
) -> ResumePacket:
    """Canonical packet을 현재 journal에서 재생성해 stale/forged resume를 거부한다."""

    expected = build_resume_packet(
        bootstrap_packet_sha256=bootstrap_packet_sha256,
        journal=journal,
        total_cap=total_cap,
    )
    if expected.sha256 != expected_sha256 or expected.content != content:
        raise LightGbmContractError("bootstrap resume packet trust anchor mismatch")
    return expected


def _read_attempts(path: Path) -> tuple[JournalAttempt, ...]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return ()
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_size > MAX_JOURNAL_BYTES
        ):
            raise LightGbmContractError("bootstrap progress journal boundary is invalid")
        content = os.read(descriptor, MAX_JOURNAL_BYTES + 1)
        after = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino, metadata.st_size) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
        ):
            raise LightGbmContractError("bootstrap progress journal changed during read")
    finally:
        os.close(descriptor)
    if content and not content.endswith(b"\n"):
        raise LightGbmContractError("bootstrap progress journal is truncated")
    events: list[dict[str, object]] = []
    for raw_line in content.splitlines():
        try:
            value = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise LightGbmContractError("bootstrap progress journal JSON is invalid") from None
        if (
            not isinstance(value, dict)
            or canonical_json_bytes(value).removesuffix(b"\n") != raw_line
            or not set(value).issubset(_EVENT_FIELDS)
            or not (_EVENT_FIELDS - {"chunk"}).issubset(value)
        ):
            raise LightGbmContractError("bootstrap progress journal event is not closed")
        events.append(cast(dict[str, object], value))
    attempts: list[JournalAttempt] = []
    index = 0
    ordinal = 1
    while index < len(events):
        intent = events[index]
        if intent["state"] != "INTENT" or intent["ordinal"] != ordinal or "chunk" in intent:
            raise LightGbmContractError("bootstrap progress journal sequence is invalid")
        if index + 1 >= len(events):
            raise LightGbmContractError("bootstrap provider handoff outcome is ambiguous")
        terminal = events[index + 1]
        if any(
            terminal[field] != intent[field]
            for field in ("ordinal", "provider", "operationId", "querySha256")
        ) or terminal["state"] not in {"SUCCEEDED", "FAILED"}:
            raise LightGbmContractError("bootstrap progress journal terminal is invalid")
        chunk_value = terminal.get("chunk")
        chunk = parse_source_chunk_receipt(chunk_value) if chunk_value is not None else None
        if terminal["state"] == "FAILED" and chunk is not None:
            raise LightGbmContractError("failed bootstrap attempt cannot bind a chunk")
        attempts.append(
            JournalAttempt(
                ordinal=ordinal,
                provider=_text(intent["provider"]),
                operation_id=_text(intent["operationId"]),
                query_sha256=_sha(intent["querySha256"]),
                state=_text(terminal["state"]),
                chunk=chunk,
            )
        )
        ordinal += 1
        index += 2
    return tuple(attempts)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise LightGbmContractError("bootstrap progress text is invalid")
    return value


def _sha(value: object) -> str:
    text = _text(value)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise LightGbmContractError("bootstrap progress SHA-256 is invalid")
    return text
