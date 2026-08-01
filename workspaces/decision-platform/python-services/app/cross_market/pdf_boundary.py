from __future__ import annotations

import errno
import hashlib
import ipaddress
import json
import os
import re
import stat
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol
from urllib.parse import urlsplit


class PdfBoundaryError(ValueError):
    """analyst PDF metadata/local ephemeral 경계가 승인이나 자원 제한을 위반했음을 나타낸다."""


APPROVED_PDF_SECTIONS: Final[tuple[str, ...]] = (
    "투자포인트",
    "실적전망",
    "Valuation",
    "목표주가",
    "위험요인",
    "Disclaimer",
)
_APPROVED_TAGS: Final[frozenset[str]] = frozenset(
    {"CHANGE", "DISCLAIMER", "EARNINGS", "INVESTMENT_POINT", "RISK", "TARGET_PRICE", "VALUATION"}
)
_APPROVAL_ID = re.compile(r"^AUTH_LICENSED_EPHEMERAL_LOCAL_[A-Z0-9_]{8,96}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_MAX_FILE_BYTES = 10 * 1024 * 1024
_MAX_PAGES = 100
_MAX_DECOMPRESSED_BYTES = 32 * 1024 * 1024
_MAX_PARSE_MILLIS = 2_000
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
_SHARED_WRITE_BITS = stat.S_IWGRP | stat.S_IWOTH


@dataclass(frozen=True, slots=True)
class ManualAnalystReportLink:
    title: str
    broker: str
    published_at: datetime
    url: str


@dataclass(frozen=True, slots=True)
class EphemeralPdfApproval:
    approval_id: str
    approved_root: Path
    relative_path: str
    expected_sha256: str
    derived_data_allowed: bool
    user_confirmed_tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BoundedPdfParseResult:
    section_names: tuple[str, ...]
    page_count: int
    decompressed_bytes: int


class LocalPdfParserPort(Protocol):
    """network/외부 LLM 없이 이미 삭제된 파일의 in-memory bytes만 처리한다."""

    def parse(self, payload: memoryview) -> BoundedPdfParseResult: ...


@dataclass(frozen=True, slots=True)
class EphemeralPdfReceipt:
    input_sha256: str
    relative_path_hash: str
    deletion_receipt_hash: str
    derived_data_stored: bool
    normalized_tags: tuple[str, ...]
    section_names: tuple[str, ...]
    page_count: int | None
    raw_text_stored: bool = False
    quote_stored: bool = False
    external_llm_calls: int = 0


def build_manual_link_projection(value: ManualAnalystReportLink) -> dict[str, object]:
    """title/broker/time/URL만 반환하며 URL을 resolve하거나 다운로드하지 않는다."""

    if (
        not _bounded_text(value.title, 300)
        or not _bounded_text(value.broker, 128)
        or value.published_at.tzinfo is None
        or not _safe_manual_https_url(value.url)
    ):
        raise PdfBoundaryError("MANUAL_LINK_URL_INVALID")
    return {
        "automaticDownloadCount": 0,
        "broker": value.broker,
        "contentStored": False,
        "embedded": False,
        "externalLlmCalls": 0,
        "mode": "MANUAL_LINK_ONLY",
        "publishedAt": _instant(value.published_at),
        "title": value.title,
        "url": value.url,
    }


def process_licensed_ephemeral_pdf(
    approval: EphemeralPdfApproval,
    parser: LocalPdfParserPort,
) -> EphemeralPdfReceipt:
    """승인된 regular PDF를 bounded read하고 삭제를 확인한 뒤 local parser를 호출한다.

    삭제가 실패하면 parser, 저장, outbound는 모두 0이다. derivedDataAllowed=false이면 parser
    결과와 user tag를 반환하지 않고 hash-only deletion receipt만 남긴다.
    """

    _validate_approval(approval)
    payload = bytearray()
    try:
        payload, deletion_receipt_hash = _read_delete_ephemeral(approval)
        started = time.monotonic_ns()
        view = memoryview(payload)
        try:
            parsed = parser.parse(view)
        except Exception as error:
            raise PdfBoundaryError("EPHEMERAL_PARSE_FAILED") from error
        finally:
            view.release()
        elapsed_millis = (time.monotonic_ns() - started) // 1_000_000
        _validate_parse_result(parsed, elapsed_millis)
        if approval.derived_data_allowed:
            tags = tuple(sorted(approval.user_confirmed_tags, key=lambda item: item.encode("utf-8")))
            sections = tuple(section for section in APPROVED_PDF_SECTIONS if section in parsed.section_names)
            page_count = parsed.page_count
        else:
            tags = ()
            sections = ()
            page_count = None
        return EphemeralPdfReceipt(
            input_sha256=approval.expected_sha256,
            relative_path_hash=hashlib.sha256(approval.relative_path.encode("utf-8")).hexdigest(),
            deletion_receipt_hash=deletion_receipt_hash,
            derived_data_stored=bool(approval.derived_data_allowed and (tags or sections)),
            normalized_tags=tags,
            section_names=sections,
            page_count=page_count,
        )
    finally:
        payload[:] = b"\x00" * len(payload)


def _validate_approval(value: EphemeralPdfApproval) -> None:
    root = value.approved_root
    if (
        _APPROVAL_ID.fullmatch(value.approval_id) is None
        or not root.is_absolute()
        or ".." in root.parts
        or not value.relative_path
        or "/" in value.relative_path
        or "\\" in value.relative_path
        or value.relative_path in {".", ".."}
        or not value.relative_path.lower().endswith(".pdf")
        or _HASH.fullmatch(value.expected_sha256) is None
        or len(value.user_confirmed_tags) > 12
        or len(set(value.user_confirmed_tags)) != len(value.user_confirmed_tags)
        or any(tag not in _APPROVED_TAGS for tag in value.user_confirmed_tags)
    ):
        raise PdfBoundaryError("EPHEMERAL_APPROVAL_INVALID")


def _read_delete_ephemeral(value: EphemeralPdfApproval) -> tuple[bytearray, str]:
    root_fd = -1
    file_fd = -1
    payload = bytearray()
    try:
        root_fd = os.open(value.approved_root, _DIRECTORY_FLAGS)
        root_metadata = os.fstat(root_fd)
        if (
            not stat.S_ISDIR(root_metadata.st_mode)
            or root_metadata.st_uid != os.geteuid()
            or root_metadata.st_mode & _SHARED_WRITE_BITS
        ):
            raise PdfBoundaryError("EPHEMERAL_ROOT_INVALID")
        file_fd = os.open(value.relative_path, _FILE_FLAGS, dir_fd=root_fd)
        before = os.fstat(file_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_mode & _SHARED_WRITE_BITS
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > _MAX_FILE_BYTES
        ):
            raise PdfBoundaryError("EPHEMERAL_FILE_INVALID")
        while len(payload) <= _MAX_FILE_BYTES:
            chunk = os.read(file_fd, min(65_536, _MAX_FILE_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(file_fd)
        if _stable_metadata(before) != _stable_metadata(after) or len(payload) != before.st_size:
            raise PdfBoundaryError("EPHEMERAL_FILE_RACE")
        if hashlib.sha256(payload).hexdigest() != value.expected_sha256:
            raise PdfBoundaryError("EPHEMERAL_HASH_MISMATCH")
        if not payload.startswith(b"%PDF-") or b"%%EOF" not in payload[-1024:]:
            raise PdfBoundaryError("EPHEMERAL_MIME_INVALID")

        current = os.stat(value.relative_path, dir_fd=root_fd, follow_symlinks=False)
        if _stable_metadata(before) != _stable_metadata(current):
            raise PdfBoundaryError("EPHEMERAL_FILE_RACE")
        try:
            _unlink_ephemeral_leaf(root_fd, value.relative_path)
            os.fsync(root_fd)
        except OSError as error:
            raise PdfBoundaryError("EPHEMERAL_DELETE_FAILED") from error
        try:
            os.stat(value.relative_path, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise PdfBoundaryError("EPHEMERAL_DELETE_FAILED")
        receipt = {
            "approvalId": value.approval_id,
            "deletedDevice": before.st_dev,
            "deletedInode": before.st_ino,
            "inputSha256": value.expected_sha256,
            "relativePathHash": hashlib.sha256(value.relative_path.encode("utf-8")).hexdigest(),
        }
        receipt_hash = hashlib.sha256(
            json.dumps(receipt, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        return payload, receipt_hash
    except PdfBoundaryError:
        payload[:] = b"\x00" * len(payload)
        raise
    except OSError as error:
        payload[:] = b"\x00" * len(payload)
        if error.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
            raise PdfBoundaryError("EPHEMERAL_FILE_INVALID") from None
        raise PdfBoundaryError("EPHEMERAL_IO_FAILED") from error
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if root_fd >= 0:
            os.close(root_fd)


def _unlink_ephemeral_leaf(root_fd: int, relative_path: str) -> None:
    os.unlink(relative_path, dir_fd=root_fd)


def _validate_parse_result(value: BoundedPdfParseResult, elapsed_millis: int) -> None:
    if (
        not isinstance(value, BoundedPdfParseResult)
        or not 1 <= len(value.section_names) <= len(APPROVED_PDF_SECTIONS)
        or len(set(value.section_names)) != len(value.section_names)
        or any(section not in APPROVED_PDF_SECTIONS for section in value.section_names)
        or not 1 <= value.page_count <= _MAX_PAGES
        or not 1 <= value.decompressed_bytes <= _MAX_DECOMPRESSED_BYTES
        or elapsed_millis > _MAX_PARSE_MILLIS
    ):
        raise PdfBoundaryError("EPHEMERAL_PARSE_BOUNDARY")


def _safe_manual_https_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
        or len(value) > 2048
    ):
        return False
    normalized_host = host.rstrip(".").casefold()
    if normalized_host == "localhost" or normalized_host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _bounded_text(value: str, maximum: int) -> bool:
    return bool(value.strip()) and len(value) <= maximum and "\x00" not in value and "\n" not in value


def _stable_metadata(value: os.stat_result) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_uid,
        value.st_gid,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
