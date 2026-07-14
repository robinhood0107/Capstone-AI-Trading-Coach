from __future__ import annotations

import gzip
from collections.abc import Iterator

import httpx
import pytest

from app.data._shared.bounded_json import (
    BoundedJsonError,
    BoundedJsonLimits,
    parse_bounded_json_response,
)


def _limits(**overrides: int) -> BoundedJsonLimits:
    values = {
        "max_bytes": 128,
        "max_depth": 4,
        "max_list_items": 4,
        "max_object_keys": 4,
        "max_text_codepoints": 32,
        "max_text_bytes": 64,
        "max_number_characters": 16,
    }
    values.update(overrides)
    return BoundedJsonLimits(**values)


class _RecordingStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.iterated = False

    def __iter__(self) -> Iterator[bytes]:
        self.iterated = True
        yield from self.chunks


def test_content_length_over_limit_is_rejected_before_stream_read() -> None:
    stream = _RecordingStream([b'{"ok":true}'])
    response = httpx.Response(
        200,
        headers={"content-type": "application/json", "content-length": "129"},
        stream=stream,
    )

    with pytest.raises(BoundedJsonError, match="byte limit"):
        parse_bounded_json_response(response, limits=_limits(max_bytes=128))

    assert stream.iterated is False


def test_decompressed_stream_bytes_are_bounded() -> None:
    raw = b'{"value":"' + (b"x" * 80) + b'"}'
    response = httpx.Response(
        200,
        headers={"content-type": "application/json", "content-encoding": "gzip"},
        stream=_RecordingStream([gzip.compress(raw)]),
    )

    with pytest.raises(BoundedJsonError, match="byte limit"):
        parse_bounded_json_response(response, limits=_limits(max_bytes=64))


def test_valid_json_object_is_returned_after_bounded_stream_read() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "application/json; charset=utf-8"},
        stream=_RecordingStream([b'{"items":[1,2],', b'"name":"synthetic"}']),
    )

    assert parse_bounded_json_response(response, limits=_limits()) == {
        "items": [1, 2],
        "name": "synthetic",
    }


@pytest.mark.parametrize(
    ("payload", "limits", "message"),
    [
        (b'{"a":{"b":{"c":1}}}', _limits(max_depth=2), "depth"),
        (b'{"items":[1,2,3]}', _limits(max_list_items=2), "list"),
        (b'{"a":1,"b":2}', _limits(max_object_keys=1), "object"),
        ('{"value":"가나다"}'.encode(), _limits(max_text_codepoints=2), "text"),
        ('{"value":"가나다"}'.encode(), _limits(max_text_bytes=8), "text"),
        (b'{"value":123456}', _limits(max_number_characters=5), "number"),
    ],
)
def test_structure_and_scalar_limits_are_enforced(
    payload: bytes,
    limits: BoundedJsonLimits,
    message: str,
) -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        content=payload,
    )

    with pytest.raises(BoundedJsonError, match=message):
        parse_bounded_json_response(response, limits=limits)


@pytest.mark.parametrize(
    ("content_type", "payload"),
    [
        ("text/html", b'{"ok":true}'),
        ("application/json", b'{"broken"'),
        ("application/json", b'{"value":NaN}'),
        ("application/json", b'{"value":1e999}'),
    ],
)
def test_non_json_or_non_finite_payload_is_rejected(content_type: str, payload: bytes) -> None:
    response = httpx.Response(200, headers={"content-type": content_type}, content=payload)

    with pytest.raises(BoundedJsonError):
        parse_bounded_json_response(response, limits=_limits())
