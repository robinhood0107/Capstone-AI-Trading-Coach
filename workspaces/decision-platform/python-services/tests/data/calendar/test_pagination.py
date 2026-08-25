from __future__ import annotations

from datetime import date

import pytest

from app.data.calendar.errors import AdapterValidationError, ProviderQuotaExhausted
from app.data.calendar.models import CollectionCursor
from app.data.calendar.pagination import advance_opendart_cursor, parse_opendart_page


def test_ds001_page_count_is_exactly_100_and_advances_durable_cursor() -> None:
    page = parse_opendart_page(
        {
            "status": "000",
            "page_no": 1,
            "page_count": 100,
            "total_count": 101,
            "total_page": 2,
            "list": [{"rcept_no": "20260722000001"}],
        },
        expected_page=1,
    )
    cursor = CollectionCursor(
        source_id="opendart-structured-events",
        operation="list",
        subject="00126380",
        window_from=date(2026, 7, 1),
        window_to=date(2026, 7, 22),
        mapping_version="s1.6-opendart-v1",
        next_page=1,
        continuation=None,
        completed=False,
    )

    advanced = advance_opendart_cursor(cursor, page)

    assert advanced.next_page == 2
    assert advanced.completed is False


def test_final_or_empty_ds001_page_marks_cursor_complete() -> None:
    cursor = CollectionCursor(
        source_id="opendart-structured-events",
        operation="list",
        subject="00126380",
        window_from=date(2026, 7, 1),
        window_to=date(2026, 7, 22),
        mapping_version="s1.6-opendart-v1",
        next_page=1,
        continuation=None,
        completed=False,
    )
    empty = parse_opendart_page(
        {
            "status": "013",
            "page_no": 1,
            "page_count": 100,
            "total_count": 0,
            "total_page": 0,
            "list": [],
        },
        expected_page=1,
    )

    assert advance_opendart_cursor(cursor, empty).completed is True


@pytest.mark.parametrize(
    "response, expected",
    [
        (
            {
                "status": "000",
                "page_no": 1,
                "page_count": 99,
                "total_count": 1,
                "total_page": 1,
                "list": [{}],
            },
            "page_count",
        ),
        (
            {
                "status": "000",
                "page_no": 2,
                "page_count": 100,
                "total_count": 1,
                "total_page": 1,
                "list": [{}],
            },
            "page number",
        ),
        (
            {
                "status": "000",
                "page_no": 1,
                "page_count": 100,
                "total_count": 101,
                "total_page": 1,
                "list": [{}],
            },
            "total_page",
        ),
        (
            {
                "status": "000",
                "page_no": 1,
                "page_count": 100,
                "total_count": 1,
                "total_page": 1,
                "list": "raw",
            },
            "list",
        ),
    ],
)
def test_ds001_pagination_metadata_mismatch_fails_before_publish(
    response: dict[str, object],
    expected: str,
) -> None:
    with pytest.raises(AdapterValidationError, match=expected):
        parse_opendart_page(response, expected_page=1)


def test_ds001_quota_empty_and_cursor_error_paths_fail_closed() -> None:
    with pytest.raises(ProviderQuotaExhausted):
        parse_opendart_page({"status": "020"}, expected_page=1)

    with pytest.raises(AdapterValidationError, match="empty"):
        parse_opendart_page(
            {
                "status": "013",
                "page_no": 1,
                "page_count": 100,
                "total_count": 1,
                "total_page": 1,
                "list": [{}],
            },
            expected_page=1,
        )

    page = parse_opendart_page(
        {
            "status": "000",
            "page_no": 1,
            "page_count": 100,
            "total_count": 1,
            "total_page": 1,
            "list": [{}],
        },
        expected_page=1,
    )
    wrong_cursor = CollectionCursor(
        source_id="opendart-structured-events",
        operation="majorstock",
        subject="00126380",
        window_from=date(2026, 7, 1),
        window_to=date(2026, 7, 22),
        mapping_version="s1.6-opendart-v1",
        next_page=1,
        continuation=None,
        completed=False,
    )
    with pytest.raises(AdapterValidationError, match="cursor"):
        advance_opendart_cursor(wrong_cursor, page)
