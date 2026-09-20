from __future__ import annotations

import gzip
import base64
import hashlib

import httpx

import pytest

from app.data.news.gdelt_transport import (
    GdeltResponseFixture,
    GdeltHttpClient,
    GdeltWorldNewsTransportError,
    validate_gdelt_response,
)


def fixture(content: bytes = b"bounded world-news fixture") -> GdeltResponseFixture:
    body = gzip.compress(content)
    crc32c = _crc32c(body)
    return GdeltResponseFixture(
        requested_url="https://data.gdeltproject.org/gdeltv3/gqg/20260908030100.gqg.json.gz",
        final_url="https://storage.googleapis.com/gdelt-open-data/gdeltv3/gqg/20260908030100.gqg.json.gz",
        redirect_count=1,
        tls_hostname_verified=True,
        dns_public_addresses_verified=True,
        headers=(
            ("Content-Length", str(len(body))),
            ("Content-Encoding", "gzip"),
            ("x-goog-hash", f"crc32c={base64.b64encode(crc32c.to_bytes(4, 'big')).decode()}"),
            (
                "x-goog-hash",
                "md5="
                + base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode(),
            ),
        ),
        body=body,
    )


def test_repeated_x_goog_hash_and_gzip_are_valid() -> None:
    assert validate_gdelt_response(fixture()) == b"bounded world-news fixture"


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"redirect_count": 3}, "REDIRECT_LIMIT"),
        ({"tls_hostname_verified": False}, "DNS_TLS"),
        ({"final_url": "https://evil.example/file.gz"}, "FIXED_ORIGIN"),
    ],
)
def test_target_and_tls_boundary(change: dict[str, object], code: str) -> None:
    value = fixture()
    changed = GdeltResponseFixture(
        **({field: getattr(value, field) for field in value.__dataclass_fields__} | change)
    )
    with pytest.raises(GdeltWorldNewsTransportError, match=code):
        validate_gdelt_response(changed)


def test_content_length_transfer_encoding_conflict_is_rejected() -> None:
    value = fixture()
    changed = GdeltResponseFixture(
        **{
            **{field: getattr(value, field) for field in value.__dataclass_fields__},
            "headers": value.headers + (("Transfer-Encoding", "chunked"),),
        }
    )
    with pytest.raises(GdeltWorldNewsTransportError, match="FRAMING_CONFLICT"):
        validate_gdelt_response(changed)


def test_hash_mismatch_truncation_and_zip_bomb_are_rejected() -> None:
    value = fixture()
    bad_hash = GdeltResponseFixture(
        **{
            **{field: getattr(value, field) for field in value.__dataclass_fields__},
            "headers": (("x-goog-hash", "md5=AAAAAAAAAAAAAAAAAAAAAA=="),),
        }
    )
    with pytest.raises(GdeltWorldNewsTransportError, match="HASH_MISMATCH"):
        validate_gdelt_response(bad_hash)
    truncated = GdeltResponseFixture(
        **{
            **{field: getattr(value, field) for field in value.__dataclass_fields__},
            "body": value.body[:-2],
            "headers": (("Content-Encoding", "gzip"),),
        }
    )
    with pytest.raises(GdeltWorldNewsTransportError, match="CRC_OR_TRUNCATION"):
        validate_gdelt_response(truncated)
    with pytest.raises(GdeltWorldNewsTransportError, match="DECOMPRESSED_SIZE"):
        validate_gdelt_response(fixture(b"x" * 100), decompressed_limit=32)


def test_repeated_or_comma_separated_official_hashes_are_not_lowercased() -> None:
    value = fixture()
    combined = GdeltResponseFixture(
        **{
            **{field: getattr(value, field) for field in value.__dataclass_fields__},
            "headers": (
                ("Content-Length", str(len(value.body))),
                ("Content-Encoding", "gzip"),
                (
                    "x-goog-hash",
                    ", ".join(item[1] for item in value.headers if item[0] == "x-goog-hash"),
                ),
            ),
        }
    )
    assert validate_gdelt_response(combined) == b"bounded world-news fixture"


def test_conflicting_repeated_hash_and_crc32_ieee_are_rejected() -> None:
    value = fixture()
    conflicting = GdeltResponseFixture(
        **{
            **{field: getattr(value, field) for field in value.__dataclass_fields__},
            "headers": value.headers + (("x-goog-hash", "crc32c=AAAAAA=="),),
        }
    )
    with pytest.raises(GdeltWorldNewsTransportError, match="HASH_AMBIGUOUS"):
        validate_gdelt_response(conflicting)


def _crc32c(value: bytes) -> int:
    checksum = 0xFFFFFFFF
    for byte in value:
        checksum ^= byte
        for _ in range(8):
            checksum = (checksum >> 1) ^ (0x82F63B78 if checksum & 1 else 0)
    return checksum ^ 0xFFFFFFFF


def test_http_client_streams_one_file_and_reuses_allowlisted_redirect() -> None:
    value = fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "data.gdeltproject.org":
            return httpx.Response(
                302,
                headers={
                    "Location": "https://storage.googleapis.com/gdelt-open-data/gdeltv3/gqg/20260908030100.gqg.json.gz"
                },
            )
        return httpx.Response(200, headers=value.headers, stream=httpx.ByteStream(value.body))

    with GdeltHttpClient(
        transport=httpx.MockTransport(handler),
        resolver=lambda _: ("8.8.8.8",),
    ) as client:
        content = client.fetch(value.requested_url)
        assert client.physical_calls == 2
    assert content == b"bounded world-news fixture"


def test_http_client_is_default_off_and_rejects_private_dns_or_unbounded_path() -> None:
    with pytest.raises(ValueError, match="disabled"):
        GdeltHttpClient()
    with GdeltHttpClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404)),
        resolver=lambda _: (),
    ) as client:
        with pytest.raises(GdeltWorldNewsTransportError, match="DNS_TLS"):
            client.fetch("https://data.gdeltproject.org/gdeltv3/gqg/20260908030100.gqg.json.gz")
        with pytest.raises(GdeltWorldNewsTransportError, match="FIXED_ORIGIN"):
            client.fetch("https://data.gdeltproject.org/gdeltv2/latest.csv")


def test_dns_answer_change_is_rejected_even_with_a_mock_response() -> None:
    value = fixture()
    answers = iter((("8.8.8.8",), ("1.1.1.1",)))
    with GdeltHttpClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers=value.headers, stream=httpx.ByteStream(value.body)
            )
        ),
        resolver=lambda _: next(answers),
    ) as client:
        with pytest.raises(GdeltWorldNewsTransportError, match="DNS_REBINDING"):
            client.fetch(value.requested_url)


@pytest.mark.parametrize("header", ["md5=!!!", "crc32c=", "MD5=AAAA"])
def test_malformed_recognized_google_hash_is_rejected(header: str) -> None:
    value = fixture()
    malformed = GdeltResponseFixture(
        **{
            **{field: getattr(value, field) for field in value.__dataclass_fields__},
            "headers": (("x-goog-hash", header),),
        }
    )
    with pytest.raises(GdeltWorldNewsTransportError, match="HASH_INVALID"):
        validate_gdelt_response(malformed)


def test_timeout_after_send_consumes_attempt_budget() -> None:
    attempts: list[httpx.Request] = []

    def timeout(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        raise httpx.ReadTimeout("synthetic", request=request)

    with GdeltHttpClient(
        transport=httpx.MockTransport(timeout),
        resolver=lambda _: ("8.8.8.8",),
        physical_call_cap=1,
    ) as client:
        with pytest.raises(GdeltWorldNewsTransportError, match="NETWORK_ERROR"):
            client.fetch("https://data.gdeltproject.org/gdeltv3/gqg/20260908030100.gqg.json.gz")
        assert client.physical_calls == len(attempts) == 1
