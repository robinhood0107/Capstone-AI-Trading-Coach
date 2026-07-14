from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import cast

import httpx


class BoundedJsonError(ValueError):
    """응답 JSON이 합의된 byte·구조 상한을 벗어나면 raw 내용을 노출하지 않고 실패한다."""


@dataclass(frozen=True)
class BoundedJsonLimits:
    """provider 응답에 적용할 decompressed byte와 JSON 구조 상한이다."""

    max_bytes: int
    max_depth: int
    max_list_items: int
    max_object_keys: int
    max_text_codepoints: int
    max_text_bytes: int
    max_number_characters: int

    def __post_init__(self) -> None:
        values = (
            self.max_bytes,
            self.max_list_items,
            self.max_object_keys,
            self.max_text_codepoints,
            self.max_text_bytes,
            self.max_number_characters,
        )
        if any(value <= 0 for value in values) or self.max_depth < 0:
            raise ValueError("bounded JSON limits must be positive")


def parse_bounded_json_response(
    response: httpx.Response,
    *,
    limits: BoundedJsonLimits,
) -> object:
    """HTTP 응답을 decompression 이후 byte 수로 제한하고 bounded JSON 값으로 반환한다.

    provider raw body는 오류에 포함하지 않으며, Content-Length는 stream을 읽기 전 빠른 거부에만
    사용한다. 실제 보안 상한은 `iter_bytes()`가 내놓는 decompressed bytes를 다시 누적해 적용한다.
    """
    try:
        _ensure_json_content_type(response)
        _ensure_declared_length(response, limits.max_bytes)
        content = _read_decompressed_bytes(response, limits.max_bytes)
    finally:
        response.close()

    try:
        text = content.decode("utf-8")
        payload = json.loads(
            text,
            parse_int=lambda value: _parse_int(value, limits.max_number_characters),
            parse_float=lambda value: _parse_float(value, limits.max_number_characters),
            parse_constant=_reject_non_finite_constant,
        )
    except BoundedJsonError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError, OverflowError):
        raise BoundedJsonError("bounded JSON payload was invalid") from None

    _validate_value(payload, limits=limits, depth=0)
    return payload


def _ensure_json_content_type(response: httpx.Response) -> None:
    media_type = response.headers.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        raise BoundedJsonError("bounded JSON response content type was not JSON")


def _ensure_declared_length(response: httpx.Response, max_bytes: int) -> None:
    declared = response.headers.get("content-length")
    if declared is None:
        return
    try:
        declared_bytes = int(declared)
    except ValueError:
        raise BoundedJsonError("bounded JSON Content-Length was invalid") from None
    if declared_bytes < 0:
        raise BoundedJsonError("bounded JSON Content-Length was invalid")
    if declared_bytes > max_bytes:
        raise BoundedJsonError("bounded JSON response exceeded the byte limit")


def _read_decompressed_bytes(response: httpx.Response, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    try:
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise BoundedJsonError("bounded JSON response exceeded the byte limit")
            chunks.append(chunk)
    except BoundedJsonError:
        raise
    except (httpx.HTTPError, OSError):
        raise BoundedJsonError("bounded JSON response stream was unavailable") from None
    return b"".join(chunks)


def _parse_int(value: str, max_characters: int) -> int:
    _ensure_number_length(value, max_characters)
    return int(value)


def _parse_float(value: str, max_characters: int) -> float:
    _ensure_number_length(value, max_characters)
    parsed = float(value)
    if not math.isfinite(parsed):
        raise BoundedJsonError("bounded JSON number must be finite")
    return parsed


def _ensure_number_length(value: str, max_characters: int) -> None:
    if len(value) > max_characters:
        raise BoundedJsonError("bounded JSON number exceeded the number limit")


def _reject_non_finite_constant(_: str) -> object:
    raise BoundedJsonError("bounded JSON number must be finite")


def _validate_value(value: object, *, limits: BoundedJsonLimits, depth: int) -> None:
    if depth > limits.max_depth:
        raise BoundedJsonError("bounded JSON value exceeded the depth limit")
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        if len(mapping) > limits.max_object_keys:
            raise BoundedJsonError("bounded JSON object exceeded the object key limit")
        for key, child in mapping.items():
            if not isinstance(key, str):
                raise BoundedJsonError("bounded JSON object key was invalid")
            _validate_text(key, limits)
            _validate_value(child, limits=limits, depth=depth + 1)
        return
    if isinstance(value, list):
        items = cast(list[object], value)
        if len(items) > limits.max_list_items:
            raise BoundedJsonError("bounded JSON list exceeded the list item limit")
        for child in items:
            _validate_value(child, limits=limits, depth=depth + 1)
        return
    if isinstance(value, str):
        _validate_text(value, limits)


def _validate_text(value: str, limits: BoundedJsonLimits) -> None:
    if (
        len(value) > limits.max_text_codepoints
        or len(value.encode("utf-8")) > limits.max_text_bytes
    ):
        raise BoundedJsonError("bounded JSON text exceeded the text limit")
