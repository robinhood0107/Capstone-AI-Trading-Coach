"""offline-only source fixture가 메모리로 들어오기 전 byte bound를 강제한다."""

import json
import os
from pathlib import Path
from typing import Any


def read_bounded_fixture(path: Path, *, max_bytes: int, label: str) -> bytes:
    """파일 크기를 read 전후로 검증해 sparse/교체 파일도 정해진 메모리 상한을 넘기지 않는다."""
    if max_bytes <= 0:
        raise ValueError("fixture byte bound must be positive")
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        if size <= 0 or size > max_bytes:
            raise ValueError(f"{label} fixture exceeds its byte bound")
        payload = stream.read(max_bytes + 1)
    if len(payload) != size or len(payload) > max_bytes:
        raise ValueError(f"{label} fixture changed while it was being read")
    return payload


def read_json_fixture(path: Path, *, max_bytes: int, label: str) -> tuple[bytes, Any]:
    """byte bound와 duplicate-key 거부를 한 곳에서 적용해 source별 parser drift를 막는다."""
    payload = read_bounded_fixture(path, max_bytes=max_bytes, label=label)
    try:
        return payload, json.loads(payload, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} fixture JSON is invalid") from error


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("fixture JSON contains duplicate object keys")
        result[key] = value
    return result
