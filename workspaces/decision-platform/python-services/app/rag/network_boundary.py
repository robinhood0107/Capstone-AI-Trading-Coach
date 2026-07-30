from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from typing import Mapping, Protocol

from app.rag.source_registry import (
    RagSourceLocator,
    RagSourceRegistryError,
    validate_resolved_addresses,
)

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_ALLOWED_MIME_TYPES = frozenset(
    {
        "application/json",
        "application/pdf",
        "text/html",
        "text/markdown",
        "text/plain",
    }
)


class RagNetworkBoundaryError(ValueError):
    """RAG source transport fixture가 SSRF·redirect·response bound를 위반할 때 발생한다."""


@dataclass(frozen=True)
class RagTransportPolicy:
    """실제 network adapter가 나중에 증명해야 할 고정 transport 속성."""

    trust_env: bool = False
    follow_redirects: bool = False
    timeout_seconds: float = 2.0
    maximum_response_bytes: int = 1_048_576


@dataclass(frozen=True)
class RagFixtureHttpResponse:
    """credential·socket이 없는 injected fixture response."""

    status_code: int
    headers: Mapping[str, str]
    body: bytes
    peer_ip: str


@dataclass(frozen=True)
class RagCheckedResponse:
    """raw body를 저장하지 않는 source-check ledger 입력."""

    status_code: int
    bytes_read: int
    content_sha256: str
    mime_type: str


class RagResolver(Protocol):
    def resolve(self, hostname: str) -> list[str]:
        """hostname의 A/AAAA fixture를 반환한다."""


class RagFixtureTransport(Protocol):
    fixture_only: bool

    def request(
        self,
        *,
        hostname: str,
        pinned_ip: str,
        path_and_query: str,
        headers: Mapping[str, str],
        policy: RagTransportPolicy,
    ) -> RagFixtureHttpResponse:
        """socket 대신 test fixture response를 반환한다."""


def check_source_with_fixtures(
    *,
    locator: RagSourceLocator,
    resolver: RagResolver,
    transport: RagFixtureTransport,
    policy: RagTransportPolicy = RagTransportPolicy(),
) -> RagCheckedResponse:
    """두 번의 DNS 검증·IP pin·peer 확인·bounded response를 fixture로만 검증한다.

    S4.1에서는 `fixture_only=true` transport만 허용해 이 함수 호출이 실제 인터넷 연결을
    만들 수 없다. runtime network activation은 별도 승인과 adapter 증명이 필요하다.
    """

    if not getattr(transport, "fixture_only", False):
        raise RagNetworkBoundaryError("RAG source network transport remains disabled.")
    if policy.trust_env or policy.follow_redirects:
        raise RagNetworkBoundaryError("RAG source transport must ignore env and redirects.")
    if policy.timeout_seconds <= 0 or policy.timeout_seconds > 10:
        raise RagNetworkBoundaryError("RAG source timeout is outside the approved bound.")
    if policy.maximum_response_bytes <= 0 or policy.maximum_response_bytes > 8_388_608:
        raise RagNetworkBoundaryError("RAG source response byte bound is invalid.")

    hostname, path_and_query = _split_approved_locator(locator)
    first_addresses = resolver.resolve(hostname)
    try:
        validate_resolved_addresses(hostname, first_addresses)
    except RagSourceRegistryError as error:
        raise RagNetworkBoundaryError("RAG source DNS policy rejected the address set.") from error
    pinned_ip = first_addresses[0]

    second_addresses = resolver.resolve(hostname)
    try:
        validate_resolved_addresses(hostname, second_addresses)
    except RagSourceRegistryError as error:
        raise RagNetworkBoundaryError("RAG source DNS rebinding check failed.") from error
    if set(first_addresses) != set(second_addresses):
        raise RagNetworkBoundaryError("RAG source DNS rebinding changed the validated set.")

    try:
        response = transport.request(
            hostname=hostname,
            pinned_ip=pinned_ip,
            path_and_query=path_and_query,
            headers={
                "Accept": "application/json, application/pdf, text/html, text/markdown, text/plain",
                "Accept-Encoding": "identity",
                "Host": hostname,
            },
            policy=policy,
        )
    except TimeoutError as error:
        raise RagNetworkBoundaryError("RAG source transport timed out.") from error

    _validate_peer(response.peer_ip, first_addresses)
    return _validate_response(response, policy)


def _split_approved_locator(locator: RagSourceLocator) -> tuple[str, str]:
    origin_prefix = "https://"
    if not locator.allowed_origin.startswith(origin_prefix):
        raise RagNetworkBoundaryError("RAG source origin is not approved HTTPS.")
    authority = locator.allowed_origin.removeprefix(origin_prefix)
    hostname = authority.removesuffix(":443")
    if not hostname or locator.allowed_path.startswith("//") or not locator.allowed_path.startswith("/"):
        raise RagNetworkBoundaryError("RAG source locator shape is invalid.")
    expected = locator.allowed_origin + locator.allowed_path
    if expected != locator.canonical_url:
        raise RagNetworkBoundaryError("RAG source locator no longer matches its exact allowlist.")
    return hostname, locator.allowed_path


def _validate_peer(peer_ip: str, validated_addresses: list[str]) -> None:
    try:
        peer = ipaddress.ip_address(peer_ip)
    except ValueError as error:
        raise RagNetworkBoundaryError("RAG source peer address is invalid.") from error
    if (
        not peer.is_global
        or peer.is_loopback
        or peer.is_private
        or peer.is_link_local
        or peer.is_multicast
        or peer.is_reserved
        or peer.is_unspecified
        or peer_ip not in validated_addresses
    ):
        raise RagNetworkBoundaryError("RAG source peer address is outside the pinned set.")


def _validate_response(
    response: RagFixtureHttpResponse,
    policy: RagTransportPolicy,
) -> RagCheckedResponse:
    headers = {key.lower(): value.strip() for key, value in response.headers.items()}
    if response.status_code in _REDIRECT_STATUSES or "location" in headers:
        raise RagNetworkBoundaryError("RAG source redirects are forbidden.")
    if response.status_code < 200 or response.status_code >= 300:
        raise RagNetworkBoundaryError("RAG source response status is not successful.")
    if headers.get("content-encoding", "identity").lower() != "identity":
        raise RagNetworkBoundaryError("RAG source compressed response is forbidden.")
    if len(response.body) == 0 or len(response.body) > policy.maximum_response_bytes:
        raise RagNetworkBoundaryError("RAG source response exceeds the byte bound.")
    raw_content_length = headers.get("content-length")
    if raw_content_length is not None:
        try:
            content_length = int(raw_content_length)
        except ValueError as error:
            raise RagNetworkBoundaryError("RAG source Content-Length is invalid.") from error
        if content_length != len(response.body) or content_length > policy.maximum_response_bytes:
            raise RagNetworkBoundaryError("RAG source Content-Length violates the byte bound.")

    mime_type = headers.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
    if mime_type not in _ALLOWED_MIME_TYPES:
        raise RagNetworkBoundaryError("RAG source MIME type is not allowlisted.")
    _validate_magic(mime_type, response.body)
    return RagCheckedResponse(
        status_code=response.status_code,
        bytes_read=len(response.body),
        content_sha256=hashlib.sha256(response.body).hexdigest(),
        mime_type=mime_type,
    )


def _validate_magic(mime_type: str, body: bytes) -> None:
    stripped = body.lstrip()
    if mime_type == "application/pdf" and not body.startswith(b"%PDF-"):
        raise RagNetworkBoundaryError("RAG source PDF magic does not match MIME.")
    if mime_type == "application/json" and not stripped.startswith((b"{", b"[")):
        raise RagNetworkBoundaryError("RAG source JSON magic does not match MIME.")
    if mime_type == "text/html" and not stripped.lower().startswith(
        (b"<!doctype html", b"<html")
    ):
        raise RagNetworkBoundaryError("RAG source HTML magic does not match MIME.")
    if mime_type in {"text/plain", "text/markdown"}:
        try:
            body.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise RagNetworkBoundaryError("RAG source text must be strict UTF-8.") from error
