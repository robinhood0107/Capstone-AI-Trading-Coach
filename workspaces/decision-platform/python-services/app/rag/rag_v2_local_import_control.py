from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Mapping, cast

from app.rag.benchmark_receipt_io import (
    BenchmarkReceiptIoError,
    write_benchmark_receipt,
)
from app.rag.owner_file_io import OwnerFileIoError, read_owner_regular_file

_CONTROL_DIRECTORY = "control"
_CONTROL_FILENAME = "owner-import.json"
_CONTROL_RELATIVE_PATH = f"{_CONTROL_DIRECTORY}/{_CONTROL_FILENAME}"
_MAX_CONTROL_BYTES = 16 * 1024
_OWNER_ID = re.compile(r"^usr_[a-z0-9][a-z0-9_-]{2,95}$")
_TICKET_ID = re.compile(r"^rti_[0-9a-f]{32}$")
_DOCUMENT_ID = re.compile(r"^doc_[a-z0-9][a-z0-9_-]{10,95}$")
_SOURCE_ID = re.compile(r"^src_[a-z0-9][a-z0-9_-]{2,95}$")
_SOURCE_REVISION_ID = re.compile(r"^srv_[a-z0-9][a-z0-9_-]{2,95}$")
_LANGUAGE_TAG = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
_CONTROL_FIELDS = frozenset(
    {
        "approvedRoot",
        "contractId",
        "documentId",
        "expiresAt",
        "issuedAt",
        "languageTags",
        "ownerUserId",
        "relativePath",
        "schemaVersion",
        "sourceId",
        "sourceRevisionId",
        "ticketId",
    }
)


class RagV2LocalImportControlError(ValueError):
    """local import control record가 private filesystem/ticket contract를 위반했음을 나타낸다."""


@dataclass(frozen=True, slots=True)
class RagV2OwnerImportControl:
    """authenticated local UI가 만든 one-time owner import selector다.

    owner identity, ticket, source root/path는 local 0600 record에서만 소비한다. CLI argv, stdout,
    history, database receipt에는 이 값을 복사하지 않는다.
    """

    owner_user_id: str
    import_ticket_id: str
    approved_root: Path
    relative_path: str
    document_id: str
    source_id: str
    source_revision_id: str
    language_tags: tuple[str, ...]
    issued_at: datetime
    expires_at: datetime

    def content_free_summary(self) -> dict[str, object]:
        """BAT/CLI status에 노출 가능한 control plane 상태만 투영한다."""

        return {
            "code": "OWNER_IMPORT_CONTROL_READY",
            "expiresAt": _format_instant(self.expires_at),
            "state": "PENDING",
        }


def write_pending_owner_import_control(
    *,
    local_root: Path,
    control: RagV2OwnerImportControl,
) -> None:
    """trusted local UI가 owner selection과 server-issued ticket을 0600 control record로 publish한다.

    이 helper는 CLI entrypoint가 아니다. raw path/ticket을 argv로 전달하지 않는 desktop/UI path만
    호출해야 하며, same-directory atomic replace는 이전 stale ticket을 남기지 않는다.
    """

    payload = _encode_control(control)
    try:
        write_benchmark_receipt(
            approved_root=local_root,
            relative_directory=_CONTROL_DIRECTORY,
            filename=_CONTROL_FILENAME,
            payload=payload,
        )
    except BenchmarkReceiptIoError as error:
        raise RagV2LocalImportControlError("LOCAL_IMPORT_CONTROL_BOUNDARY") from error
    _assert_control_record_boundary(local_root)


def load_pending_owner_import_control(
    *,
    local_root: Path,
    now: datetime | None = None,
) -> RagV2OwnerImportControl:
    """fixed local-root control record만 read해 one-time import selector를 반환한다."""

    before = _assert_control_record_boundary(local_root)
    try:
        raw = read_owner_regular_file(
            approved_root=local_root,
            relative_path=_CONTROL_RELATIVE_PATH,
            max_bytes=_MAX_CONTROL_BYTES,
        ).content
    except OwnerFileIoError as error:
        raise RagV2LocalImportControlError("LOCAL_IMPORT_CONTROL_BOUNDARY") from error
    after = _assert_control_record_boundary(local_root)
    if before != after or len(raw) != before.st_size:
        raise RagV2LocalImportControlError("LOCAL_IMPORT_CONTROL_BOUNDARY")
    try:
        payload = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RagV2LocalImportControlError("LOCAL_IMPORT_CONTROL_INVALID") from error
    control = _decode_control(payload)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if current >= control.expires_at:
        raise RagV2LocalImportControlError("LOCAL_IMPORT_CONTROL_EXPIRED")
    return control


def _encode_control(control: RagV2OwnerImportControl) -> bytes:
    _validate_control(control)
    payload = {
        "approvedRoot": str(control.approved_root),
        "contractId": "rag-v2-owner-local-import-control-v1",
        "documentId": control.document_id,
        "expiresAt": _format_instant(control.expires_at),
        "issuedAt": _format_instant(control.issued_at),
        "languageTags": list(control.language_tags),
        "ownerUserId": control.owner_user_id,
        "relativePath": control.relative_path,
        "schemaVersion": 1,
        "sourceId": control.source_id,
        "sourceRevisionId": control.source_revision_id,
        "ticketId": control.import_ticket_id,
    }
    encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode(
        "utf-8"
    )
    if not 1 <= len(encoded) <= _MAX_CONTROL_BYTES:
        raise RagV2LocalImportControlError("LOCAL_IMPORT_CONTROL_INVALID")
    return encoded


def _decode_control(value: object) -> RagV2OwnerImportControl:
    if not isinstance(value, Mapping) or set(value) != _CONTROL_FIELDS:
        raise RagV2LocalImportControlError("LOCAL_IMPORT_CONTROL_INVALID")
    approved_root = value.get("approvedRoot")
    relative_path = value.get("relativePath")
    language_tags = value.get("languageTags")
    if (
        value.get("contractId") != "rag-v2-owner-local-import-control-v1"
        or value.get("schemaVersion") != 1
        or not isinstance(approved_root, str)
        or not isinstance(relative_path, str)
        or not isinstance(language_tags, list)
    ):
        raise RagV2LocalImportControlError("LOCAL_IMPORT_CONTROL_INVALID")
    try:
        control = RagV2OwnerImportControl(
            owner_user_id=_require_pattern(value.get("ownerUserId"), _OWNER_ID),
            import_ticket_id=_require_pattern(value.get("ticketId"), _TICKET_ID),
            approved_root=Path(approved_root),
            relative_path=_validate_relative_path(relative_path),
            document_id=_require_pattern(value.get("documentId"), _DOCUMENT_ID),
            source_id=_require_pattern(value.get("sourceId"), _SOURCE_ID),
            source_revision_id=_require_pattern(value.get("sourceRevisionId"), _SOURCE_REVISION_ID),
            language_tags=_validate_language_tags(language_tags),
            issued_at=_parse_instant(value.get("issuedAt")),
            expires_at=_parse_instant(value.get("expiresAt")),
        )
    except (TypeError, ValueError) as error:
        raise RagV2LocalImportControlError("LOCAL_IMPORT_CONTROL_INVALID") from error
    _validate_control(control)
    return control


def _validate_control(control: RagV2OwnerImportControl) -> None:
    if (
        _OWNER_ID.fullmatch(control.owner_user_id) is None
        or _TICKET_ID.fullmatch(control.import_ticket_id) is None
        or _DOCUMENT_ID.fullmatch(control.document_id) is None
        or _SOURCE_ID.fullmatch(control.source_id) is None
        or _SOURCE_REVISION_ID.fullmatch(control.source_revision_id) is None
        or not control.approved_root.is_absolute()
        or ".." in control.approved_root.parts
        or not control.approved_root.is_dir()
        or _validate_relative_path(control.relative_path) != control.relative_path
        or _validate_language_tags(list(control.language_tags)) != control.language_tags
        or control.issued_at.tzinfo is None
        or control.expires_at.tzinfo is None
        or control.expires_at.astimezone(UTC) - control.issued_at.astimezone(UTC) != timedelta(minutes=5)
    ):
        raise RagV2LocalImportControlError("LOCAL_IMPORT_CONTROL_INVALID")


def _assert_control_record_boundary(local_root: Path) -> os.stat_result:
    record = local_root / _CONTROL_DIRECTORY / _CONTROL_FILENAME
    try:
        metadata = record.lstat()
    except OSError as error:
        raise RagV2LocalImportControlError("LOCAL_IMPORT_CONTROL_BOUNDARY") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size <= 0
        or metadata.st_size > _MAX_CONTROL_BYTES
    ):
        raise RagV2LocalImportControlError("LOCAL_IMPORT_CONTROL_BOUNDARY")
    if os.name != "nt" and (
        metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise RagV2LocalImportControlError("LOCAL_IMPORT_CONTROL_BOUNDARY")
    return metadata


def _require_pattern(value: object, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError("pattern")
    return value


def _validate_relative_path(value: str) -> str:
    if (
        not value
        or value.startswith("/")
        or value.endswith("/")
        or "\\" in value
        or "\x00" in value
        or any(part in {"", ".", ".."} or ":" in part for part in PurePosixPath(value).parts)
    ):
        raise ValueError("relative path")
    return PurePosixPath(value).as_posix()


def _validate_language_tags(value: list[object]) -> tuple[str, ...]:
    if (
        not 1 <= len(value) <= 8
        or any(not isinstance(item, str) or _LANGUAGE_TAG.fullmatch(item) is None for item in value)
        or len(set(cast(str, item) for item in value)) != len(value)
    ):
        raise ValueError("language tags")
    return tuple(cast(str, item) for item in value)


def _parse_instant(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("instant")
    parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    if parsed.tzinfo is None:
        raise ValueError("instant")
    return parsed.astimezone(UTC)


def _format_instant(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("instant")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
