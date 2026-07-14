from __future__ import annotations

import pytest

from app.data.ecos.policy import (
    ECOS_KEY_SENTINEL,
    ECOS_ORIGIN,
    build_keyless_service_path,
    should_retry_ecos_failure,
)


def test_keyless_path_allows_only_the_three_s1_3_services() -> None:
    path = build_keyless_service_path(
        service="StatisticSearch",
        start_index=1,
        end_index=200,
        arguments=("722Y001", "D", "20250714", "20260714", "0101000"),
    )

    assert ECOS_ORIGIN == "https://ecos.bok.or.kr"
    assert path == (
        f"/api/StatisticSearch/{ECOS_KEY_SENTINEL}/json/kr/1/200/"
        "722Y001/D/20250714/20260714/0101000"
    )
    assert "ECOS_API_KEY" not in path


@pytest.mark.parametrize(
    "service",
    ["KeyStatisticList", "StatisticMeta", "StatisticWord", "../StatisticSearch"],
)
def test_out_of_scope_or_unsafe_services_are_rejected(service: str) -> None:
    with pytest.raises(ValueError, match="service|path"):
        build_keyless_service_path(
            service=service,
            start_index=1,
            end_index=1,
            arguments=("722Y001",),
        )


@pytest.mark.parametrize("segment", ["..", "%2fetc", "a/b", "//attacker.invalid"])
def test_path_segments_cannot_escape_the_fixed_origin(segment: str) -> None:
    with pytest.raises(ValueError, match="segment|path"):
        build_keyless_service_path(
            service="StatisticTableList",
            start_index=1,
            end_index=1,
            arguments=(segment,),
        )


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("connect_timeout", True),
        ("read_timeout", True),
        ("http_500", True),
        ("ERROR-500", True),
        ("ERROR-600", True),
        ("ERROR-601", True),
        ("ERROR-602", False),
        ("INFO-200", False),
        ("http_400", False),
        ("authentication", False),
    ],
)
def test_retry_allowlist_is_explicit_and_602_is_never_retried(
    failure: str,
    expected: bool,
) -> None:
    assert should_retry_ecos_failure(failure) is expected
