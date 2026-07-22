from __future__ import annotations

from dataclasses import dataclass, replace

from app.data.calendar.errors import AdapterValidationError, ProviderQuotaExhausted
from app.data.calendar.models import CollectionCursor

OPENDART_PAGE_COUNT = 100


@dataclass(frozen=True)
class OpenDARTPage:
    """DS001 pagination metadata와 bounded row list를 cursor 갱신 전에 검증한 page다."""

    page_no: int
    page_count: int
    total_count: int
    total_page: int
    rows: tuple[dict[str, object], ...]


def parse_opendart_page(response: dict[str, object], *, expected_page: int) -> OpenDARTPage:
    """DS001 page_count=100과 total metadata 정합성을 exact 검증한다."""
    status = str(response.get("status"))
    if status == "020":
        raise ProviderQuotaExhausted()
    if status not in {"000", "0", "013"}:
        raise AdapterValidationError("OpenDART page status is invalid")
    page_no = _nonnegative_int(response.get("page_no"), "page number")
    page_count = _nonnegative_int(response.get("page_count"), "page_count")
    total_count = _nonnegative_int(response.get("total_count"), "total_count")
    total_page = _nonnegative_int(response.get("total_page"), "total_page")
    if expected_page <= 0 or page_no != expected_page:
        raise AdapterValidationError("OpenDART page number mismatch")
    if page_count != OPENDART_PAGE_COUNT:
        raise AdapterValidationError("OpenDART page_count must equal 100")
    calculated_pages = (total_count + page_count - 1) // page_count
    if total_page != calculated_pages:
        raise AdapterValidationError("OpenDART total_page mismatch")
    rows_value = response.get("list")
    if not isinstance(rows_value, list) or len(rows_value) > page_count:
        raise AdapterValidationError("OpenDART page list is invalid")
    if any(not isinstance(row, dict) for row in rows_value):
        raise AdapterValidationError("OpenDART page list is invalid")
    rows = tuple(dict(row) for row in rows_value)
    if status == "013" and (total_count != 0 or rows):
        raise AdapterValidationError("OpenDART empty page metadata is inconsistent")
    if total_count == 0:
        if page_no != 1 or rows:
            raise AdapterValidationError("OpenDART empty page metadata is inconsistent")
    elif page_no > total_page:
        raise AdapterValidationError("OpenDART page number exceeds total_page")
    return OpenDARTPage(
        page_no=page_no,
        page_count=page_count,
        total_count=total_count,
        total_page=total_page,
        rows=rows,
    )


def advance_opendart_cursor(cursor: CollectionCursor, page: OpenDARTPage) -> CollectionCursor:
    """성공적으로 canonical publish할 page와 같은 transaction에 넣을 다음 cursor를 계산한다."""
    if cursor.operation != "list" or cursor.next_page != page.page_no or cursor.completed:
        raise AdapterValidationError("OpenDART cursor does not match the collected page")
    completed = page.total_page == 0 or page.page_no >= page.total_page
    return replace(
        cursor,
        next_page=page.page_no + 1,
        continuation=None,
        completed=completed,
    )


def _nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise AdapterValidationError(f"OpenDART {label} is invalid")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.isascii() and value.isdigit():
        parsed = int(value)
    else:
        raise AdapterValidationError(f"OpenDART {label} is invalid")
    if parsed < 0:
        raise AdapterValidationError(f"OpenDART {label} is invalid")
    return parsed
