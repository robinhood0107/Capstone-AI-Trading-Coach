from __future__ import annotations

import socket

import httpx
import pytest

from app.data.naver.url_metadata import normalize_metadata_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://news.example.test/article?id=7&lang=ko#headline",
            "https://news.example.test/article?id=7&lang=ko",
        ),
        ("http://news.example.test/article", "http://news.example.test/article"),
        (
            "https://news.example.test:8443/a%20b?id=7",
            "https://news.example.test:8443/a%20b?id=7",
        ),
    ],
)
def test_http_and_https_metadata_are_normalized_without_fetch(raw: str, expected: str) -> None:
    assert normalize_metadata_url(raw) == expected


@pytest.mark.parametrize(
    "unsafe",
    [
        "",
        "//news.example.test/article",
        "ftp://news.example.test/article",
        "https:///missing-host",
        "https://fixture-user:fixture-password@news.example.test/article",
        "http://localhost/internal",
        "http://service.local/internal",
        "http://127.0.0.1/internal",
        "http://10.0.0.1/internal",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/internal",
        "http://[fd00::1]/internal",
        "http://2130706433/internal",
        "https://news.example.test/a%0d%0aInjected:yes",
        "https://news.example.test/\u202earticle",
    ],
)
def test_unsafe_scheme_authority_control_and_non_global_hosts_are_redacted(unsafe: str) -> None:
    assert normalize_metadata_url(unsafe) is None


def test_fragment_and_credential_query_parameters_are_removed_case_insensitively() -> None:
    raw = (
        "https://news.example.test/article?id=7&access_token=fixture"
        "&API%5FKEY=fixture&X-Amz-Credential=fixture&signature=fixture&lang=ko#debug"
    )

    normalized = normalize_metadata_url(raw)

    assert normalized == "https://news.example.test/article?id=7&lang=ko"
    assert "fixture" not in normalized
    assert "#" not in normalized


def test_normalization_never_performs_dns_head_or_get(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("metadata URL normalization must not perform network I/O")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    monkeypatch.setattr(httpx, "get", fail)
    monkeypatch.setattr(httpx, "head", fail)

    assert normalize_metadata_url("https://news.example.test/article?id=7") == (
        "https://news.example.test/article?id=7"
    )


def test_url_length_bounds_apply_to_code_points_and_utf8_bytes() -> None:
    assert normalize_metadata_url("https://news.example.test/" + "x" * 2_100) is None
    assert normalize_metadata_url("https://news.example.test/" + "한" * 2_000) is None
