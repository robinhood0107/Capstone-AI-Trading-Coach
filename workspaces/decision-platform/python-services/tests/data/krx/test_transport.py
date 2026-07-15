from __future__ import annotations

import ssl
from collections.abc import Iterator

import httpx
import pytest
from pydantic import SecretStr

from app.data._shared.redis_quota import QuotaUnavailableError
from app.data.krx import _credential_transport
from app.data.krx._credential_transport import (
    KrxCredentialError,
    _CredentialTransport,
    _canonical_client_headers,
    _canonical_request_headers,
)
from app.data.krx.catalog import KOSPI_DAILY
from app.data.krx.client import _build_tls_context
from app.data.krx.settings import KrxOpenApiSettings


_AS_OF_QUERY = {"basDd": "20260715"}


class _RecordingQuota:
    def __init__(self) -> None:
        self.reservations: list[str] = []

    def reserve(self, *, attempt_id: str) -> None:
        assert attempt_id
        self.reservations.append(attempt_id)


class _UnavailableQuota:
    def reserve(self, *, attempt_id: str) -> None:
        assert attempt_id
        raise QuotaUnavailableError("synthetic Redis failure")


class _DripStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    def __iter__(self) -> Iterator[bytes]:
        yield from self._chunks


def _origin() -> str:
    return KrxOpenApiSettings(_env_file=None).origin


def _request(*, headers: dict[str, str] | None = None) -> httpx.Request:
    return httpx.Request(
        "GET",
        f"{_origin()}{KOSPI_DAILY.path}",
        params=_AS_OF_QUERY,
        headers=headers or _canonical_request_headers(),
    )


def test_auth_key_exists_only_during_physical_send_and_is_removed_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "synthetic-krx-auth-key"
    quota = _RecordingQuota()
    captured: list[httpx.Request] = []
    monkeypatch.setattr(
        _credential_transport,
        "_read_credential",
        lambda: SecretStr(marker),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["AUTH_KEY"] == marker
        captured.append(request)
        return httpx.Response(200, json={"OutBlock_1": []})

    transport = _CredentialTransport(httpx.MockTransport(handler), quota=quota)
    request = _request()

    response = transport.handle_request(request)

    assert response.status_code == 200
    assert quota.reservations and len(quota.reservations) == 1
    assert transport.physical_attempt_count == 1
    assert "AUTH_KEY" not in request.headers
    assert "AUTH_KEY" not in captured[0].headers
    assert marker not in repr(request)
    assert marker not in repr(response.request)


def test_auth_key_is_removed_and_secret_cause_is_dropped_after_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "synthetic-krx-auth-key"
    captured: list[httpx.Request] = []
    monkeypatch.setattr(
        _credential_transport,
        "_read_credential",
        lambda: SecretStr(marker),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["AUTH_KEY"] == marker
        captured.append(request)
        raise httpx.ConnectError(f"synthetic failure {marker}", request=request)

    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        quota=_RecordingQuota(),
    )
    request = _request()

    with pytest.raises(KrxCredentialError, match="transport_unavailable") as exc_info:
        transport.handle_request(request)

    rendered = f"{exc_info.value!r} {exc_info.value}"
    assert transport.physical_attempt_count == 1
    assert "AUTH_KEY" not in request.headers
    assert "AUTH_KEY" not in captured[0].headers
    assert marker not in rendered
    assert exc_info.value.__cause__ is None


def test_fixed_origin_path_query_method_and_canonical_headers_reach_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "synthetic-krx-auth-key"
    observed = 0
    monkeypatch.setattr(
        _credential_transport,
        "_read_credential",
        lambda: SecretStr(marker),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed
        observed += 1
        expected_origin = httpx.URL(_origin())
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == expected_origin.host
        assert request.url.port == expected_origin.port
        assert request.url.path == KOSPI_DAILY.path
        assert dict(request.url.params) == _AS_OF_QUERY
        expected_headers = _canonical_request_headers()
        expected_headers["AUTH_KEY"] = marker
        assert sorted(request.headers.multi_items()) == sorted(
            httpx.Headers(expected_headers).multi_items()
        )
        return httpx.Response(200, json={"OutBlock_1": []})

    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        quota=_RecordingQuota(),
    )

    transport.handle_request(_request())

    assert observed == 1


@pytest.mark.parametrize(
    ("method", "url", "headers", "expected_code"),
    [
        (
            "POST",
            f"{_origin()}{KOSPI_DAILY.path}?basDd=20260715",
            _canonical_request_headers(),
            "request_not_allowed",
        ),
        (
            "GET",
            "https://attacker.invalid/svc/apis/sto/stk_bydd_trd?basDd=20260715",
            _canonical_request_headers(),
            "origin_not_allowed",
        ),
        (
            "GET",
            f"{_origin()}/svc/apis/sto/not-enabled?basDd=20260715",
            _canonical_request_headers(),
            "path_not_allowed",
        ),
        (
            "GET",
            f"{_origin()}{KOSPI_DAILY.path}?basDd=20260715&extra=1",
            _canonical_request_headers(),
            "query_not_allowed",
        ),
        (
            "GET",
            f"{_origin()}{KOSPI_DAILY.path}?basDd=2026-07-15",
            _canonical_request_headers(),
            "query_not_allowed",
        ),
    ],
)
def test_noncanonical_outbound_request_is_rejected_before_quota_or_send(
    method: str,
    url: str,
    headers: dict[str, str],
    expected_code: str,
) -> None:
    quota = _RecordingQuota()
    outbound = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json={"OutBlock_1": []})

    transport = _CredentialTransport(httpx.MockTransport(handler), quota=quota)
    request = httpx.Request(method, url, headers=headers)

    with pytest.raises(KrxCredentialError, match=expected_code):
        transport.handle_request(request)

    assert quota.reservations == []
    assert outbound == 0


@pytest.mark.parametrize(
    "header_name",
    [
        "AUTH_KEY",
        "Authorization",
        "Cookie",
        "Proxy-Authorization",
        "X-Api-Key",
        "X-Access-Token",
    ],
)
def test_caller_auth_proxy_and_cookie_headers_are_rejected_before_credential_read(
    monkeypatch: pytest.MonkeyPatch,
    header_name: str,
) -> None:
    credential_reads = 0
    outbound = 0
    quota = _RecordingQuota()

    def read_credential() -> SecretStr:
        nonlocal credential_reads
        credential_reads += 1
        return SecretStr("synthetic-krx-auth-key")

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json={"OutBlock_1": []})

    monkeypatch.setattr(_credential_transport, "_read_credential", read_credential)
    headers = _canonical_request_headers()
    headers[header_name] = "caller-controlled"
    transport = _CredentialTransport(httpx.MockTransport(handler), quota=quota)

    with pytest.raises(KrxCredentialError, match="caller_auth_header_not_allowed"):
        transport.handle_request(_request(headers=headers))

    assert credential_reads == 0
    assert quota.reservations == []
    assert outbound == 0


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=20260715",
        "http://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=20260715",
        "//attacker.invalid/svc/apis/sto/stk_bydd_trd?basDd=20260715",
    ],
)
def test_userinfo_plain_http_and_network_path_reference_are_rejected(url: str) -> None:
    outbound = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json={"OutBlock_1": []})

    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        quota=_RecordingQuota(),
    )
    request = httpx.Request("GET", url, headers=_canonical_request_headers())

    with pytest.raises(KrxCredentialError, match="origin_not_allowed"):
        transport.handle_request(request)

    assert outbound == 0


def test_redis_failure_is_fail_closed_before_credential_or_outbound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_reads = 0
    outbound = 0

    def read_credential() -> SecretStr:
        nonlocal credential_reads
        credential_reads += 1
        return SecretStr("synthetic-krx-auth-key")

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json={"OutBlock_1": []})

    monkeypatch.setattr(_credential_transport, "_read_credential", read_credential)
    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        quota=_UnavailableQuota(),
    )

    with pytest.raises(QuotaUnavailableError):
        transport.handle_request(_request())

    assert credential_reads == 0
    assert outbound == 0
    assert transport.physical_attempt_count == 0


def test_oversized_stream_is_rejected_without_returning_partial_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _credential_transport,
        "_read_credential",
        lambda: SecretStr("synthetic-krx-auth-key"),
    )
    transport = _CredentialTransport(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                stream=_DripStream([b'{"OutBlock_1":["', b"x" * 64, b'"]}']),
            )
        ),
        quota=_RecordingQuota(),
        max_response_bytes=48,
    )

    with pytest.raises(KrxCredentialError, match="response_too_large") as exc_info:
        transport.handle_request(_request())

    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize(
    "body",
    [
        b'{"OutBlock_1":[{"ISU_NM":"synthetic-krx-auth-key"}]}',
        b'{"OutBlock_1":[{"ISU_NM":"synthetic-krx-auth-\\u006bey"}]}',
    ],
)
def test_literal_and_unicode_escaped_credential_echo_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
) -> None:
    marker = "synthetic-krx-auth-key"
    monkeypatch.setattr(
        _credential_transport,
        "_read_credential",
        lambda: SecretStr(marker),
    )
    transport = _CredentialTransport(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=body,
            )
        ),
        quota=_RecordingQuota(),
    )

    with pytest.raises(KrxCredentialError, match="response_unavailable") as exc_info:
        transport.handle_request(_request())

    rendered = f"{exc_info.value!r} {exc_info.value}"
    assert marker not in rendered
    assert exc_info.value.__cause__ is None


def test_tls_context_rejects_ambient_ca_and_keylog_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE"):
        monkeypatch.delenv(name, raising=False)
    context = _build_tls_context()

    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert getattr(context, "keylog_filename", None) is None

    monkeypatch.setenv("SSL_CERT_FILE", "/tmp/caller-ca.pem")
    with pytest.raises(ValueError, match="TLS|tls"):
        _build_tls_context()


def test_canonical_client_headers_do_not_contain_auth_or_ambient_state() -> None:
    headers = httpx.Headers(_canonical_client_headers())

    assert headers["Accept"] == "application/json"
    assert headers["Accept-Encoding"] == "identity"
    assert headers["User-Agent"] == "capstone-ai-trading-coach-s1.3"
    assert "AUTH_KEY" not in headers
    assert "Cookie" not in headers
    assert "Proxy-Authorization" not in headers
