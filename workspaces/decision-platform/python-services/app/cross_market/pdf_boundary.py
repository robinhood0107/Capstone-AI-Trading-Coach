from __future__ import annotations

import ipaddress
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol
from urllib.parse import urlsplit

from app.rag.owner_file_io import OwnerFileIoError, read_owner_regular_file


class PdfBoundaryError(ValueError):
    """analyst PDF metadata/local parse 경계가 형식이나 자원 제한을 위반했음을 나타낸다."""


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
_DOCUMENT_ID = re.compile(r"^doc_[a-z0-9][a-z0-9_-]{2,95}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_MAX_FILE_BYTES = 10 * 1024 * 1024
_MAX_PAGES = 100
_MAX_DECOMPRESSED_BYTES = 32 * 1024 * 1024
_MAX_PARSE_MILLIS = 2_000


@dataclass(frozen=True, slots=True)
class ManualAnalystReportLink:
    title: str
    broker: str
    published_at: datetime
    url: str


@dataclass(frozen=True, slots=True)
class LocalPdfRequest:
    """owner가 보유한 PDF의 read-only local parse 요청이며 파일별 승인 packet은 없다."""

    document_id: str
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
    """network/외부 LLM 없이 in-memory owner bytes만 처리하는 bounded parser port다."""

    def parse(self, payload: memoryview) -> BoundedPdfParseResult: ...


@dataclass(frozen=True, slots=True)
class LocalPdfReceipt:
    document_id: str
    input_sha256: str
    processing_mode: str
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


def process_local_ephemeral_pdf(
    request: LocalPdfRequest,
    parser: LocalPdfParserPort,
) -> LocalPdfReceipt:
    """owner regular PDF를 변경 없이 bounded read하고 local-only parser에 전달한다.

    파일별 approval ID, nonce, TTL은 사용하지 않는다. 입력은 디스크에 복제하지 않으며
    경로 대신 opaque document ID만 receipt에 남긴다.
    """

    _validate_request(request)
    try:
        read_result = read_owner_regular_file(
            approved_root=request.approved_root,
            relative_path=request.relative_path,
            max_bytes=_MAX_FILE_BYTES,
        )
    except OwnerFileIoError as error:
        raise PdfBoundaryError("LOCAL_PDF_FILE_INVALID") from error
    payload = bytearray(read_result.content)
    try:
        if read_result.content_sha256 != request.expected_sha256:
            raise PdfBoundaryError("LOCAL_PDF_HASH_MISMATCH")
        if not payload.startswith(b"%PDF-") or b"%%EOF" not in payload[-1024:]:
            raise PdfBoundaryError("LOCAL_PDF_MIME_INVALID")
        started = time.monotonic_ns()
        view = memoryview(payload)
        try:
            parsed = parser.parse(view)
        except Exception as error:
            raise PdfBoundaryError("LOCAL_PDF_PARSE_FAILED") from error
        finally:
            view.release()
        elapsed_millis = (time.monotonic_ns() - started) // 1_000_000
        _validate_parse_result(parsed, elapsed_millis)
        if request.derived_data_allowed:
            tags = tuple(sorted(request.user_confirmed_tags, key=lambda item: item.encode("utf-8")))
            sections = tuple(
                section for section in APPROVED_PDF_SECTIONS if section in parsed.section_names
            )
            page_count = parsed.page_count
        else:
            tags = ()
            sections = ()
            page_count = None
        return LocalPdfReceipt(
            document_id=request.document_id,
            input_sha256=request.expected_sha256,
            processing_mode="LOCAL_EPHEMERAL_PARSE",
            derived_data_stored=bool(request.derived_data_allowed and (tags or sections)),
            normalized_tags=tags,
            section_names=sections,
            page_count=page_count,
        )
    finally:
        payload[:] = b"\x00" * len(payload)


def _validate_request(value: LocalPdfRequest) -> None:
    if (
        _DOCUMENT_ID.fullmatch(value.document_id) is None
        or not value.approved_root.is_absolute()
        or not value.relative_path
        or not value.relative_path.lower().endswith(".pdf")
        or _HASH.fullmatch(value.expected_sha256) is None
        or len(value.user_confirmed_tags) > 12
        or len(set(value.user_confirmed_tags)) != len(value.user_confirmed_tags)
        or any(tag not in _APPROVED_TAGS for tag in value.user_confirmed_tags)
    ):
        raise PdfBoundaryError("LOCAL_PDF_REQUEST_INVALID")


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
        raise PdfBoundaryError("LOCAL_PDF_PARSE_BOUNDARY")


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


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
