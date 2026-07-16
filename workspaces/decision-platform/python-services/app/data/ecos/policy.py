from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

ECOS_ORIGIN: Final = "https://ecos.bok.or.kr"
ECOS_KEY_SENTINEL: Final = "__KEYLESS__"

_SERVICE_ARGUMENT_COUNTS: Final = {
    "StatisticSearch": 5,
    "StatisticTableList": 1,
    "StatisticItemList": 1,
}
_SAFE_SEGMENT = re.compile(r"[A-Za-z0-9_-]+")
_KEYLESS_PATH = re.compile(
    rf"/api/(?P<service>[A-Za-z]+?)/{re.escape(ECOS_KEY_SENTINEL)}/json/kr/"
    r"(?P<start>[0-9]+)/(?P<end>[0-9]+)(?P<arguments>(?:/[A-Za-z0-9_-]+)+)(?P<trailing>/?)"
)
_RETRYABLE_FAILURES: Final = frozenset(
    {
        "connect_timeout",
        "read_timeout",
        "ERROR-500",
        "ERROR-600",
        "ERROR-601",
    }
    | {f"http_{status}" for status in range(500, 600)}
)


def build_keyless_service_path(
    *,
    service: str,
    start_index: int,
    end_index: int,
    arguments: Sequence[str],
) -> str:
    """승인된 ECOS GET 서비스의 credential 없는 상대 경로만 만든다.

    반환 경로의 sentinel은 private transport가 실제 send 직전에만 API key로 교체한다.
    """
    expected_arguments = _SERVICE_ARGUMENT_COUNTS.get(service)
    if expected_arguments is None:
        raise ValueError("ECOS service path is not allowed")
    _validate_page(service=service, start_index=start_index, end_index=end_index)
    values = tuple(arguments)
    if len(values) != expected_arguments:
        raise ValueError("ECOS service path arguments are invalid")
    for segment in values:
        _validate_segment(segment)
    suffix = "/".join(values)
    trailing = "/" if service == "StatisticSearch" else ""
    return (
        f"/api/{service}/{ECOS_KEY_SENTINEL}/json/kr/{start_index}/{end_index}/{suffix}{trailing}"
    )


def validate_keyless_service_path(path: str) -> str:
    """absolute URL·encoded separator·dot segment를 거부하고 canonical keyless 경로를 반환한다."""
    if (
        not isinstance(path, str)
        or not path.startswith("/api/")
        or "//" in path
        or "%" in path
        or "\\" in path
        or "?" in path
        or "#" in path
    ):
        raise ValueError("ECOS keyless path is invalid")
    match = _KEYLESS_PATH.fullmatch(path)
    if match is None:
        raise ValueError("ECOS keyless path is invalid")
    service = match.group("service")
    arguments = tuple(match.group("arguments").removeprefix("/").split("/"))
    start_index = int(match.group("start"))
    end_index = int(match.group("end"))
    canonical = build_keyless_service_path(
        service=service,
        start_index=start_index,
        end_index=end_index,
        arguments=arguments,
    )
    if canonical != path:
        raise ValueError("ECOS keyless path is not canonical")
    return path


def should_retry_ecos_failure(failure: str) -> bool:
    """재시도 가능한 transport·HTTP·application failure의 명시적 allowlist를 판정한다."""
    return failure in _RETRYABLE_FAILURES


def _validate_page(*, service: str, start_index: int, end_index: int) -> None:
    if (
        isinstance(start_index, bool)
        or isinstance(end_index, bool)
        or start_index < 1
        or end_index < start_index
        or end_index - start_index >= 200
    ):
        raise ValueError("ECOS service page is invalid")
    hard_end = 400 if service == "StatisticSearch" else 200
    if end_index > hard_end:
        raise ValueError("ECOS service page is out of bounds")


def _validate_segment(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or len(value) > 64
        or len(value.encode("utf-8")) > 256
        or _SAFE_SEGMENT.fullmatch(value) is None
    ):
        raise ValueError("ECOS path segment is invalid")
