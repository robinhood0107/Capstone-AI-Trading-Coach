"""S5.6 physical handoff의 append-only progress와 bounded resume packet."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Literal, Mapping, Sequence, cast

from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.bootstrap_control import BootstrapCallReceipt
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.production_policy import ECOS_OPERATIONS, KIS_OPERATION, KRX_OPERATIONS
from app.lightgbm.source_bundle import SourceChunkReceipt, parse_source_chunk_receipt


JOURNAL_FILENAME = "progress.jsonl"
MAX_JOURNAL_BYTES = 16 * 1024 * 1024
RESUME_PACKET_VERSION = "s5-production-bootstrap-resume-v1"
SUPERSEDED_CONSUMED = "SUPERSEDED_CONSUMED"
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
    failed_query_sha256: str | None


class BootstrapJournal:
    """한 run root의 intent/terminal event를 fsync append하고 재개 상태를 검증한다."""

    def __init__(
        self,
        root: Path,
        *,
        calendar_policy: Literal["current", "legacy-v1"] = "current",
    ) -> None:
        self._root = root
        self._path = root / JOURNAL_FILENAME
        self._calendar_policy = calendar_policy
        self._attempts = (
            _read_attempts(self._path, calendar_policy=calendar_policy)
            if self._path.exists()
            else ()
        )

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

    @property
    def attempts(self) -> tuple[JournalAttempt, ...]:
        """resume packet이 누적 물리 시도와 exact failed query를 결속하도록 불변 snapshot을 준다."""

        return self._attempts

    def query_completed(self, query_sha256: str) -> bool:
        """결과 row가 0인 일일 기준금리 조회까지 포함해 terminal success 여부를 반환한다."""

        return any(
            attempt.query_sha256 == query_sha256 and attempt.state == "SUCCEEDED"
            for attempt in self._attempts
        )

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
        directory_descriptor = os.open(
            self._root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        if not allow_incomplete:
            self._attempts = _read_attempts(
                self._path,
                calendar_policy=self._calendar_policy,
            )


def build_resume_packet(
    *, bootstrap_packet_sha256: str, journal: BootstrapJournal, total_cap: int
) -> ResumePacket:
    """실패 chunk 재시도 또는 provider 재호출 없는 local finalization만 결속한다."""

    failed = journal.failed_attempt
    consumed = len(journal.consumed_receipts)
    if consumed == 0 or consumed > total_cap:
        raise LightGbmContractError("bootstrap journal has no bounded resume authority")
    if failed is not None and sum(
        attempt.query_sha256 == failed.query_sha256 for attempt in journal._attempts
    ) != 1:
        raise LightGbmContractError("bootstrap failed query resume authority is exhausted")
    payload = {
        "resumePacketVersion": RESUME_PACKET_VERSION,
        "bootstrapPacketSha256": bootstrap_packet_sha256,
        "resumeMode": "FAILED_QUERY" if failed is not None else "LOCAL_FINALIZATION",
        "consumedPhysicalCalls": consumed,
        "remainingPhysicalCalls": total_cap - consumed,
        **(
            {
                "failedQuerySha256": failed.query_sha256,
                "provider": failed.provider,
                "operationId": failed.operation_id,
                "retryOrdinal": 1,
            }
            if failed is not None
            else {}
        ),
    }
    content = canonical_json_bytes(payload)
    return ResumePacket(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        bootstrap_packet_sha256=bootstrap_packet_sha256,
        failed_query_sha256=failed.query_sha256 if failed is not None else None,
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


def build_recovery_journal_bytes(
    *,
    adopted: Sequence[JournalAttempt],
    superseded: Sequence[JournalAttempt],
) -> bytes:
    """검증된 old-run receipt만 새 calendar run의 누적 physical ledger로 원자 이관한다."""

    events: list[dict[str, object]] = []
    ordinal = 1
    tagged_attempts = (
        *((attempt, True) for attempt in adopted),
        *((attempt, False) for attempt in superseded),
    )
    for attempt, is_adopted in tagged_attempts:
        if is_adopted and (attempt.state != "SUCCEEDED" or attempt.chunk is None):
            raise LightGbmContractError("adopted bootstrap attempt is invalid")
        terminal_state = "SUCCEEDED" if is_adopted else SUPERSEDED_CONSUMED
        intent = {
            "eventVersion": "s5-bootstrap-progress-v1",
            "ordinal": ordinal,
            "state": "INTENT",
            "provider": attempt.provider,
            "operationId": attempt.operation_id,
            "querySha256": attempt.query_sha256,
        }
        terminal = {
            **intent,
            "state": terminal_state,
            **(
                {"chunk": attempt.chunk.as_dict()}
                if terminal_state == "SUCCEEDED" and attempt.chunk is not None
                else {}
            ),
        }
        events.extend((intent, terminal))
        ordinal += 1
    if not events:
        raise LightGbmContractError("calendar recovery journal cannot be empty")
    return b"".join(
        canonical_json_bytes(event).removesuffix(b"\n") + b"\n" for event in events
    )


def _read_attempts(
    path: Path, *, calendar_policy: Literal["current", "legacy-v1"] = "current"
) -> tuple[JournalAttempt, ...]:
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
        ) or terminal["state"] not in {"SUCCEEDED", "FAILED", SUPERSEDED_CONSUMED}:
            raise LightGbmContractError("bootstrap progress journal terminal is invalid")
        chunk_value = terminal.get("chunk")
        chunk = (
            parse_source_chunk_receipt(
                chunk_value,
                calendar_policy=calendar_policy,
            )
            if chunk_value is not None
            else None
        )
        if terminal["state"] in {"FAILED", SUPERSEDED_CONSUMED} and chunk is not None:
            raise LightGbmContractError("failed bootstrap attempt cannot bind a chunk")
        provider = _text(intent["provider"])
        operation_id = _text(intent["operationId"])
        allowed_operations = {
            "KRX": frozenset(KRX_OPERATIONS),
            "KIS": frozenset({KIS_OPERATION, "oauth2/tokenP"}),
            "ECOS": frozenset(ECOS_OPERATIONS),
        }
        if provider not in allowed_operations or operation_id not in allowed_operations[provider]:
            raise LightGbmContractError("bootstrap progress operation is not allowlisted")
        token_without_projection = provider == "KIS" and operation_id == "oauth2/tokenP"
        empty_daily_policy_rate = provider == "ECOS" and operation_id == "722Y001/0101000/D"
        if terminal["state"] == "SUCCEEDED" and (
            (token_without_projection and chunk is not None)
            or (not token_without_projection and not empty_daily_policy_rate and chunk is None)
        ):
            raise LightGbmContractError("bootstrap progress receipt shape is invalid")
        if terminal["state"] == SUPERSEDED_CONSUMED and provider != "KRX":
            raise LightGbmContractError("only KRX calls may be calendar-superseded")
        if chunk is not None and (
            chunk.source_id != provider or chunk.operation_id != operation_id
        ):
            raise LightGbmContractError("bootstrap progress chunk binding is invalid")
        attempts.append(
            JournalAttempt(
                ordinal=ordinal,
                provider=provider,
                operation_id=operation_id,
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
