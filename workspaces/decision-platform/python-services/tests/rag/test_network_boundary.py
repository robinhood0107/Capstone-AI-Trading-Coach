from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pytest

from app.rag.network_boundary import (
    RagFixtureHttpResponse,
    RagNetworkBoundaryError,
    RagTransportPolicy,
    check_source_with_fixtures,
)
from app.rag.source_registry import RagSourceLocator

LOCATOR = RagSourceLocator(
    canonical_url="https://official.example.com/guide?id=1",
    allowed_origin="https://official.example.com",
    allowed_path="/guide?id=1",
)


@dataclass
class FixtureResolver:
    answers: list[list[str]]
    calls: int = 0

    def resolve(self, hostname: str) -> list[str]:
        assert hostname == "official.example.com"
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return answer


@dataclass
class FixtureTransport:
    response: RagFixtureHttpResponse | None = None
    timeout: bool = False
    fixture_only: bool = True
    calls: int = 0

    def request(
        self,
        *,
        hostname: str,
        pinned_ip: str,
        path_and_query: str,
        headers: Mapping[str, str],
        policy: RagTransportPolicy,
    ) -> RagFixtureHttpResponse:
        self.calls += 1
        assert hostname == "official.example.com"
        assert pinned_ip == "8.8.8.8"
        assert path_and_query == "/guide?id=1"
        assert headers["Accept-Encoding"] == "identity"
        assert headers["Host"] == hostname
        assert policy.trust_env is False
        assert policy.follow_redirects is False
        if self.timeout:
            raise TimeoutError
        assert self.response is not None
        return self.response


def test_fixture_source_check_pins_dns_peer_and_hashes_bounded_body() -> None:
    body = b"<!doctype html><html><body>official fixture</body></html>"
    resolver = FixtureResolver([["8.8.8.8"], ["8.8.8.8"]])
    transport = FixtureTransport(
        RagFixtureHttpResponse(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8", "Content-Length": str(len(body))},
            body=body,
            peer_ip="8.8.8.8",
        )
    )

    result = check_source_with_fixtures(
        locator=LOCATOR,
        resolver=resolver,
        transport=transport,
    )

    assert resolver.calls == 2
    assert transport.calls == 1
    assert result.status_code == 200
    assert result.bytes_read == len(body)
    assert len(result.content_sha256) == 64


@pytest.mark.parametrize(
    ("answers", "peer"),
    [
        ([["127.0.0.1"], ["127.0.0.1"]], "127.0.0.1"),
        ([["10.0.0.1"], ["10.0.0.1"]], "10.0.0.1"),
        ([["169.254.1.1"], ["169.254.1.1"]], "169.254.1.1"),
        ([["::1"], ["::1"]], "::1"),
        ([["fe80::1"], ["fe80::1"]], "fe80::1"),
        ([["0.0.0.0"], ["0.0.0.0"]], "0.0.0.0"),
        ([["224.0.0.1"], ["224.0.0.1"]], "224.0.0.1"),
        ([["192.0.2.1"], ["192.0.2.1"]], "192.0.2.1"),
        ([["8.8.8.8", "10.0.0.1"], ["8.8.8.8", "10.0.0.1"]], "8.8.8.8"),
    ],
)
def test_fixture_source_check_rejects_non_global_or_mixed_dns(
    answers: list[list[str]],
    peer: str,
) -> None:
    transport = FixtureTransport(_html_response(peer_ip=peer))
    with pytest.raises(RagNetworkBoundaryError):
        check_source_with_fixtures(
            locator=LOCATOR,
            resolver=FixtureResolver(answers),
            transport=transport,
        )
    assert transport.calls == 0


def test_fixture_source_check_rejects_rebinding_and_peer_mismatch() -> None:
    with pytest.raises(RagNetworkBoundaryError):
        check_source_with_fixtures(
            locator=LOCATOR,
            resolver=FixtureResolver([["8.8.8.8"], ["1.1.1.1"]]),
            transport=FixtureTransport(_html_response()),
        )

    with pytest.raises(RagNetworkBoundaryError):
        check_source_with_fixtures(
            locator=LOCATOR,
            resolver=FixtureResolver([["8.8.8.8"], ["8.8.8.8"]]),
            transport=FixtureTransport(_html_response(peer_ip="1.1.1.1")),
        )


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_fixture_source_check_rejects_every_redirect_status(status: int) -> None:
    response = _html_response(status=status, headers={"Location": "https://other.example/"})
    with pytest.raises(RagNetworkBoundaryError):
        _check(response)


def test_fixture_source_check_rejects_oversize_mime_magic_compression_and_timeout() -> None:
    invalid_responses = [
        _html_response(body=b"x" * 33, headers={"Content-Type": "text/html"}),
        _html_response(body=b"not html"),
        RagFixtureHttpResponse(200, {"Content-Type": "application/pdf"}, b"not-pdf", "8.8.8.8"),
        _html_response(headers={"Content-Encoding": "gzip"}),
        _html_response(headers={"Content-Type": "application/octet-stream"}),
    ]
    for response in invalid_responses:
        with pytest.raises(RagNetworkBoundaryError):
            _check(response, maximum_bytes=32)

    with pytest.raises(RagNetworkBoundaryError):
        check_source_with_fixtures(
            locator=LOCATOR,
            resolver=FixtureResolver([["8.8.8.8"], ["8.8.8.8"]]),
            transport=FixtureTransport(timeout=True),
        )


def test_runtime_transport_and_env_proxy_remain_disabled() -> None:
    with pytest.raises(RagNetworkBoundaryError):
        check_source_with_fixtures(
            locator=LOCATOR,
            resolver=FixtureResolver([["8.8.8.8"], ["8.8.8.8"]]),
            transport=FixtureTransport(_html_response(), fixture_only=False),
        )
    with pytest.raises(RagNetworkBoundaryError):
        check_source_with_fixtures(
            locator=LOCATOR,
            resolver=FixtureResolver([["8.8.8.8"], ["8.8.8.8"]]),
            transport=FixtureTransport(_html_response()),
            policy=RagTransportPolicy(trust_env=True),
        )


@pytest.mark.parametrize(
    "policy",
    [
        RagTransportPolicy(timeout_seconds=0),
        RagTransportPolicy(timeout_seconds=10.1),
        RagTransportPolicy(maximum_response_bytes=0),
        RagTransportPolicy(maximum_response_bytes=8_388_609),
    ],
)
def test_fixture_source_check_rejects_transport_policy_bounds(
    policy: RagTransportPolicy,
) -> None:
    with pytest.raises(RagNetworkBoundaryError):
        check_source_with_fixtures(
            locator=LOCATOR,
            resolver=FixtureResolver([["8.8.8.8"], ["8.8.8.8"]]),
            transport=FixtureTransport(_html_response()),
            policy=policy,
        )


@pytest.mark.parametrize(
    "locator",
    [
        RagSourceLocator(
            canonical_url=LOCATOR.canonical_url,
            allowed_origin="http://official.example.com",
            allowed_path=LOCATOR.allowed_path,
        ),
        RagSourceLocator(
            canonical_url=LOCATOR.canonical_url,
            allowed_origin=LOCATOR.allowed_origin,
            allowed_path="//guide?id=1",
        ),
        RagSourceLocator(
            canonical_url=LOCATOR.canonical_url,
            allowed_origin=LOCATOR.allowed_origin,
            allowed_path="guide?id=1",
        ),
        RagSourceLocator(
            canonical_url=LOCATOR.canonical_url,
            allowed_origin=LOCATOR.allowed_origin,
            allowed_path="/other",
        ),
    ],
)
def test_fixture_source_check_rejects_locator_drift(
    locator: RagSourceLocator,
) -> None:
    with pytest.raises(RagNetworkBoundaryError):
        check_source_with_fixtures(
            locator=locator,
            resolver=FixtureResolver([["8.8.8.8"], ["8.8.8.8"]]),
            transport=FixtureTransport(_html_response()),
        )


def test_fixture_source_check_rejects_invalid_peer_status_length_and_body_shapes() -> None:
    invalid_responses = [
        _html_response(peer_ip="not-an-ip"),
        _html_response(status=199),
        _html_response(status=400),
        _html_response(body=b""),
        _html_response(headers={"Content-Type": "text/html", "Content-Length": "invalid"}),
        _html_response(headers={"Content-Type": "text/html", "Content-Length": "1"}),
        _html_response(headers={"Content-Type": "text/html", "Location": "/moved"}),
        RagFixtureHttpResponse(
            200,
            {"Content-Type": "text/plain"},
            b"\xff",
            "8.8.8.8",
        ),
        RagFixtureHttpResponse(
            200,
            {"Content-Type": "application/json"},
            b"not-json",
            "8.8.8.8",
        ),
    ]
    for response in invalid_responses:
        with pytest.raises(RagNetworkBoundaryError):
            _check(response)


@pytest.mark.parametrize(
    ("mime_type", "body"),
    [
        ("application/json", b'{"status":"ok"}'),
        ("application/pdf", b"%PDF-1.7 fixture"),
        ("text/html", b"<html><body>fixture</body></html>"),
        ("text/markdown", b"# fixture\n"),
        ("text/plain", b"fixture text\n"),
    ],
)
def test_fixture_source_check_accepts_every_allowlisted_mime(
    mime_type: str,
    body: bytes,
) -> None:
    response = RagFixtureHttpResponse(
        status_code=200,
        headers={"Content-Type": mime_type, "Content-Length": str(len(body))},
        body=body,
        peer_ip="8.8.8.8",
    )

    result = check_source_with_fixtures(
        locator=LOCATOR,
        resolver=FixtureResolver([["8.8.8.8"], ["8.8.8.8"]]),
        transport=FixtureTransport(response),
    )

    assert result.mime_type == mime_type
    assert result.bytes_read == len(body)


def _check(
    response: RagFixtureHttpResponse,
    *,
    maximum_bytes: int = 1_048_576,
) -> None:
    check_source_with_fixtures(
        locator=LOCATOR,
        resolver=FixtureResolver([["8.8.8.8"], ["8.8.8.8"]]),
        transport=FixtureTransport(response),
        policy=RagTransportPolicy(maximum_response_bytes=maximum_bytes),
    )


def _html_response(
    *,
    status: int = 200,
    body: bytes = b"<!doctype html><html></html>",
    peer_ip: str = "8.8.8.8",
    headers: Mapping[str, str] | None = None,
) -> RagFixtureHttpResponse:
    return RagFixtureHttpResponse(
        status_code=status,
        headers=headers or {"Content-Type": "text/html"},
        body=body,
        peer_ip=peer_ip,
    )
