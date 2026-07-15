from __future__ import annotations

import json
import ssl
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.data._shared.redis_quota import QuotaUnavailableError
from app.data.ecos import _credential_transport, http_client as ecos_http_client
from app.data.ecos._credential_transport import (
    ECOSCredentialError,
    _CredentialTransport,
    _canonical_client_headers,
    _canonical_request_headers,
)
from app.data.ecos.errors import ECOSApplicationError
from app.data.ecos.http_client import ECOSHttpClient, ECOSHttpError, _build_tls_context
from app.data.ecos.policy import build_keyless_service_path
from app.data.ecos.series_registry import CANDIDATE_SERIES
from app.data.ecos.settings import ECOSSettings


class _RecordingQuota:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def reserve(self, *, attempt_id: str) -> None:
        assert attempt_id
        self.events.append("quota")


class _CloseFailureStream(httpx.SyncByteStream):
    def __iter__(self):
        yield b'{"StatisticSearch":{"list_total_count":0,"row":[]}}'

    def close(self) -> None:
        raise RuntimeError("synthetic-ecos-key must not escape")


class _RecordingStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.iterated = False

    def __iter__(self) -> Iterator[bytes]:
        self.iterated = True
        yield from self.chunks


class _ReadTimeoutStream(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield b'{"partial":'
        raise httpx.ReadTimeout("synthetic-ecos-key https://ecos.bok.or.kr/private body timeout")


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _DeadlineDripStream(httpx.SyncByteStream):
    def __init__(self, clock: _FakeClock, *, advance_seconds: float) -> None:
        self.clock = clock
        self.advance_seconds = advance_seconds

    def __iter__(self) -> Iterator[bytes]:
        self.clock.advance(self.advance_seconds)
        yield b'{"ok":true}'


class _NonMockTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"non-mock transport must not run: {request.method}")


def _path() -> str:
    return build_keyless_service_path(
        service="StatisticSearch",
        start_index=1,
        end_index=200,
        arguments=("722Y001", "D", "20260701", "20260714", "0101000"),
    )


def _table_list_payload(name: str, *, unicode_escape: bool) -> bytes:
    payload = {
        "StatisticTableList": {
            "list_total_count": 1,
            "row": [
                {
                    "STAT_CODE": "722Y001",
                    "STAT_NAME": name,
                    "CYCLE": "D",
                    "SRCH_YN": "Y",
                }
            ],
        }
    }
    rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if unicode_escape:
        escaped = "".join(f"\\u{ord(character):04x}" for character in name)
        rendered = rendered.replace(name, escaped)
    return rendered.encode()


def test_statistic_search_uses_exact_item_code1_trailing_slash_raw_path() -> None:
    observed_raw_paths: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_raw_paths.append(request.url.raw_path)
        assert request.url.query == b""
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"RESULT": {"CODE": "INFO-200", "MESSAGE": "synthetic empty"}},
        )

    client = ECOSHttpClient._for_tests(
        ECOSSettings(_env_file=None, ECOS_MAX_ATTEMPTS_PER_REQUEST=1),
        transport=httpx.MockTransport(handler),
        quota=_RecordingQuota([]),
        credential=SecretStr("synthetic-key"),
    )
    try:
        page = client.statistic_search(
            series=CANDIDATE_SERIES[0],
            start=date(2026, 7, 1),
            end=date(2026, 7, 14),
            page_start=1,
            page_end=200,
        )
    finally:
        client.close()

    assert page.status == "empty"
    assert len(observed_raw_paths) == 1
    raw_path = observed_raw_paths[0]
    assert raw_path.endswith(b"/722Y001/D/20260701/20260714/0101000/")
    assert b"?" not in raw_path
    assert b"%3F" not in raw_path.upper()
    assert b"//" not in raw_path


def test_send_time_path_credential_is_restored_on_request_and_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "synthetic-ecos-key"
    events: list[str] = []
    captured: list[httpx.Request] = []

    def read_credential() -> SecretStr:
        events.append("credential")
        return SecretStr(marker)

    def handler(request: httpx.Request) -> httpx.Response:
        events.append("send")
        assert marker in request.url.path
        captured.append(request)
        return httpx.Response(200, json={"StatisticSearch": {"list_total_count": 0, "row": []}})

    monkeypatch.setattr(_credential_transport, "_read_credential", read_credential)
    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        quota=_RecordingQuota(events),
    )
    with httpx.Client(
        transport=transport,
        headers=_canonical_client_headers(),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        response = client.get(f"https://ecos.bok.or.kr{_path()}")

    assert events == ["quota", "credential", "send"]
    assert transport.physical_attempt_count == 1
    assert marker not in captured[0].url.path
    assert marker not in response.request.url.path
    assert response.request.url.path == _path()


def test_transport_failure_restores_keyless_request_and_drops_secret_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "synthetic-ecos-key"
    captured: list[httpx.Request] = []
    monkeypatch.setattr(_credential_transport, "_read_credential", lambda: SecretStr(marker))

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        raise httpx.ConnectError(f"failed {request.url}", request=request)

    transport = _CredentialTransport(httpx.MockTransport(handler), quota=_RecordingQuota([]))
    request = httpx.Request(
        "GET",
        f"https://ecos.bok.or.kr{_path()}",
        headers=_canonical_request_headers(),
    )

    with pytest.raises(ECOSCredentialError, match="transport_unavailable") as exc_info:
        transport.handle_request(request)

    assert marker not in request.url.path
    assert marker not in captured[0].url.path
    assert marker not in f"{exc_info.value!r} {exc_info.value}"
    assert exc_info.value.__cause__ is None


def test_response_close_failure_cannot_prevent_keyless_restoration_or_stable_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "synthetic-ecos-key"
    captured: list[httpx.Request] = []
    monkeypatch.setattr(_credential_transport, "_read_credential", lambda: SecretStr(marker))

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, stream=_CloseFailureStream())

    transport = _CredentialTransport(httpx.MockTransport(handler), quota=_RecordingQuota([]))
    request = httpx.Request(
        "GET",
        f"https://ecos.bok.or.kr{_path()}",
        headers=_canonical_request_headers(),
    )

    with pytest.raises(ECOSCredentialError, match="response_unavailable") as exc_info:
        transport.handle_request(request)

    assert request.url.path == _path()
    assert captured[0].url.path == _path()
    assert marker not in f"{exc_info.value!r} {exc_info.value}"
    assert exc_info.value.__cause__ is None


def test_unicode_escaped_credential_echo_is_rejected_before_metadata_parse() -> None:
    marker = "synthetic-ecos-key"
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=_table_list_payload(marker, unicode_escape=True),
        )

    client = ECOSHttpClient._for_tests(
        ECOSSettings(_env_file=None),
        transport=httpx.MockTransport(handler),
        quota=_RecordingQuota([]),
        credential=SecretStr(marker),
    )
    try:
        with pytest.raises(ECOSCredentialError, match="response_unavailable") as exc_info:
            client.statistic_table_list(series=CANDIDATE_SERIES[0])
    finally:
        client.close()

    assert attempts == 1
    assert marker not in f"{exc_info.value!r} {exc_info.value}"
    assert exc_info.value.__cause__ is None


def test_literal_credential_echo_keeps_normal_body_redaction() -> None:
    marker = "synthetic-ecos-key"

    client = ECOSHttpClient._for_tests(
        ECOSSettings(_env_file=None),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"content-type": "application/json", "x-provider-echo": marker},
                content=_table_list_payload(marker, unicode_escape=False),
            )
        ),
        quota=_RecordingQuota([]),
        credential=SecretStr(marker),
    )
    try:
        metadata = client.statistic_table_list(series=CANDIDATE_SERIES[0])
    finally:
        client.close()

    assert metadata.name == "[redacted]"
    assert marker not in repr(metadata)


def test_set_cookie_and_provider_headers_are_dropped_without_next_request_replay() -> None:
    attempts = 0
    events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        assert "cookie" not in request.headers
        headers = {"content-type": "Application/JSON; charset=UTF-8"}
        if attempts == 1:
            headers.update(
                {
                    "set-cookie": "provider-state=must-not-replay; Path=/; Secure",
                    "x-provider-state": "must-not-reach-downstream",
                }
            )
        return httpx.Response(200, json={"ok": True}, headers=headers)

    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        quota=_RecordingQuota(events),
        credential_reader=lambda: SecretStr("synthetic-key"),
    )
    with httpx.Client(
        transport=transport,
        headers=_canonical_client_headers(),
        trust_env=False,
        follow_redirects=False,
    ) as client:
        first = client.get(f"https://ecos.bok.or.kr{_path()}")
        second = client.get(f"https://ecos.bok.or.kr{_path()}")

    assert first.status_code == second.status_code == 200
    assert first.headers["content-type"] == "application/json"
    assert "set-cookie" not in first.headers
    assert "x-provider-state" not in first.headers
    assert attempts == 2
    assert events == ["quota", "quota"]


def test_lone_surrogate_runtime_key_fails_stably_without_secret_context() -> None:
    invalid_key = "\ud800synthetic-secret"
    outbound = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json={"ok": True})

    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        quota=_RecordingQuota([]),
        credential_reader=lambda: SecretStr(invalid_key),
    )

    with pytest.raises(ECOSCredentialError, match="authentication_unavailable") as exc_info:
        transport.handle_request(
            httpx.Request(
                "GET",
                f"https://ecos.bok.or.kr{_path()}",
                headers=_canonical_request_headers(),
            )
        )

    rendered = f"{exc_info.value!r} {exc_info.value}"
    assert outbound == 0
    assert "synthetic-secret" not in rendered
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None


def test_declared_oversize_is_rejected_before_provider_stream_read() -> None:
    stream = _RecordingStream([b'{"ok":true}'])
    transport = _CredentialTransport(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"content-type": "application/json", "content-length": "65"},
                stream=stream,
            )
        ),
        quota=_RecordingQuota([]),
        credential_reader=lambda: SecretStr("synthetic-key"),
        max_response_bytes=64,
    )

    with pytest.raises(ECOSCredentialError, match="response_too_large"):
        transport.handle_request(
            httpx.Request(
                "GET",
                f"https://ecos.bok.or.kr{_path()}",
                headers=_canonical_request_headers(),
            )
        )

    assert stream.iterated is False


def test_provider_body_read_timeout_is_retryable_and_reserves_each_attempt() -> None:
    attempts = 0
    events: list[str] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                stream=_ReadTimeoutStream(),
            )
        return httpx.Response(200, json={"ok": True})

    client = ECOSHttpClient._for_tests(
        ECOSSettings(_env_file=None),
        transport=httpx.MockTransport(handler),
        quota=_RecordingQuota(events),
        credential=SecretStr("synthetic-key"),
    )
    try:
        result = client.get_json(_path())
    finally:
        client.close()

    assert result == {"ok": True}
    assert attempts == 2
    assert events == ["quota", "quota"]


def test_wrong_origin_is_rejected_before_quota_or_credential_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        _credential_transport,
        "_read_credential",
        lambda: events.append("credential") or SecretStr("synthetic-key"),
    )
    transport = _CredentialTransport(
        httpx.MockTransport(lambda _: httpx.Response(200)),
        quota=_RecordingQuota(events),
    )

    with pytest.raises(ECOSCredentialError, match="origin"):
        transport.handle_request(
            httpx.Request(
                "GET",
                f"https://attacker.invalid{_path()}",
                headers=_canonical_request_headers(),
            )
        )

    assert events == []


@pytest.mark.parametrize(
    "host_header",
    ["attacker.invalid", "ecos.bok.or.kr:443", "ECOS.BOK.OR.KR"],
)
def test_noncanonical_host_header_is_rejected_before_quota_or_credential_read(
    monkeypatch: pytest.MonkeyPatch,
    host_header: str,
) -> None:
    events: list[str] = []
    outbound = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200)

    monkeypatch.setattr(
        _credential_transport,
        "_read_credential",
        lambda: events.append("credential") or SecretStr("synthetic-key"),
    )
    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        quota=_RecordingQuota(events),
    )
    headers = _canonical_request_headers()
    headers["Host"] = host_header
    request = httpx.Request(
        "GET",
        f"https://ecos.bok.or.kr{_path()}",
        headers=headers,
    )

    with pytest.raises(ECOSCredentialError, match="origin_not_allowed"):
        transport.handle_request(request)

    assert events == []
    assert outbound == 0


@pytest.mark.parametrize(
    "header_name",
    [
        "Forwarded",
        "X-Forwarded-Host",
        "X-Original-URL",
        "X-HTTP-Method-Override",
        "X-Arbitrary-Caller-State",
    ],
)
def test_arbitrary_caller_header_is_rejected_before_quota_or_credential_read(
    monkeypatch: pytest.MonkeyPatch,
    header_name: str,
) -> None:
    events: list[str] = []
    outbound = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200)

    monkeypatch.setattr(
        _credential_transport,
        "_read_credential",
        lambda: events.append("credential") or SecretStr("synthetic-key"),
    )
    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        quota=_RecordingQuota(events),
    )
    headers = _canonical_request_headers()
    headers[header_name] = "caller-controlled"
    request = httpx.Request(
        "GET",
        f"https://ecos.bok.or.kr{_path()}",
        headers=headers,
    )

    with pytest.raises(ECOSCredentialError, match="request_not_allowed"):
        transport.handle_request(request)

    assert events == []
    assert outbound == 0


@pytest.mark.parametrize("mutation", ["accept-override", "user-agent-override", "accept-duplicate"])
def test_canonical_header_value_override_or_duplicate_is_rejected_before_quota(
    mutation: str,
) -> None:
    events: list[str] = []
    headers = list(_canonical_request_headers().items())
    if mutation == "accept-override":
        headers = [
            (name, "text/plain" if name.lower() == "accept" else value) for name, value in headers
        ]
    elif mutation == "user-agent-override":
        headers = [
            (name, "caller-agent" if name.lower() == "user-agent" else value)
            for name, value in headers
        ]
    else:
        headers.append(("Accept", "application/json"))

    transport = _CredentialTransport(
        httpx.MockTransport(lambda _: pytest.fail("noncanonical headers reached outbound")),
        quota=_RecordingQuota(events),
        credential_reader=lambda: pytest.fail("noncanonical headers reached credential store"),
    )
    request = httpx.Request(
        "GET",
        f"https://ecos.bok.or.kr{_path()}",
        headers=headers,
    )

    with pytest.raises(ECOSCredentialError, match="request_not_allowed"):
        transport.handle_request(request)

    assert events == []


def test_missing_credential_fails_after_reservation_but_before_outbound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    outbound = 0

    def unavailable() -> SecretStr:
        raise ECOSCredentialError("authentication_unavailable")

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200)

    monkeypatch.setattr(_credential_transport, "_read_credential", unavailable)
    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        quota=_RecordingQuota(events),
    )

    with pytest.raises(ECOSCredentialError, match="authentication_unavailable"):
        transport.handle_request(
            httpx.Request(
                "GET",
                f"https://ecos.bok.or.kr{_path()}",
                headers=_canonical_request_headers(),
            )
        )

    assert events == ["quota"]
    assert outbound == 0


def test_quota_failure_does_not_read_credential_or_attempt_outbound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_reads = 0
    outbound = 0

    def read_credential() -> SecretStr:
        nonlocal credential_reads
        credential_reads += 1
        return SecretStr("synthetic-key")

    class RejectingQuota:
        def reserve(self, *, attempt_id: str) -> None:
            raise QuotaUnavailableError("quota unavailable")

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200)

    monkeypatch.setattr(_credential_transport, "_read_credential", read_credential)
    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        quota=RejectingQuota(),
    )

    with pytest.raises(QuotaUnavailableError):
        transport.handle_request(
            httpx.Request(
                "GET",
                f"https://ecos.bok.or.kr{_path()}",
                headers=_canonical_request_headers(),
            )
        )

    assert credential_reads == 0
    assert outbound == 0


def test_quota_latency_expiring_deadline_stops_before_credentials_or_physical_attempt() -> None:
    clock = _FakeClock()
    credential_reads = 0
    outbound = 0

    class SlowQuota:
        def reserve(self, *, attempt_id: str) -> None:
            assert attempt_id
            clock.advance(1.0)

    def read_credential() -> SecretStr:
        nonlocal credential_reads
        credential_reads += 1
        return SecretStr("synthetic-key")

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200)

    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        quota=SlowQuota(),
        credential_reader=read_credential,
        monotonic=clock,
    )
    request = httpx.Request(
        "GET",
        f"https://ecos.bok.or.kr{_path()}",
        headers=_canonical_request_headers(),
        extensions={"ecos_deadline_monotonic": 1.0},
    )

    with pytest.raises(ECOSCredentialError, match="logical_deadline_exceeded") as exc_info:
        transport.handle_request(request)

    assert credential_reads == 0
    assert transport.physical_attempt_count == 0
    assert outbound == 0
    assert exc_info.value.__cause__ is None


def test_credential_latency_expiring_deadline_stops_immediately_before_outbound() -> None:
    clock = _FakeClock()
    credential_reads = 0
    outbound = 0
    events: list[str] = []

    def read_credential() -> SecretStr:
        nonlocal credential_reads
        credential_reads += 1
        clock.advance(1.0)
        return SecretStr("synthetic-key")

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200)

    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        quota=_RecordingQuota(events),
        credential_reader=read_credential,
        monotonic=clock,
    )
    request = httpx.Request(
        "GET",
        f"https://ecos.bok.or.kr{_path()}",
        headers=_canonical_request_headers(),
        extensions={"ecos_deadline_monotonic": 1.0},
    )

    with pytest.raises(ECOSCredentialError, match="logical_deadline_exceeded") as exc_info:
        transport.handle_request(request)

    assert credential_reads == 1
    assert events == ["quota"]
    assert transport.physical_attempt_count == 0
    assert outbound == 0
    assert request.url.path == _path()
    assert exc_info.value.__cause__ is None


def test_tls_context_requires_tls12_hostname_cert_validation_and_no_keylog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE"):
        monkeypatch.delenv(name, raising=False)

    context = _build_tls_context()

    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.keylog_filename is None


@pytest.mark.parametrize("environment_name", ["SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE"])
def test_tls_environment_poisoning_fails_before_context_construction_without_value_echo(
    monkeypatch: pytest.MonkeyPatch,
    environment_name: str,
) -> None:
    marker = "synthetic-tls-secret-path"
    monkeypatch.setenv(environment_name, marker)
    monkeypatch.setattr(
        ssl,
        "create_default_context",
        lambda: pytest.fail("poisoned TLS environment reached context construction"),
    )

    with pytest.raises(ECOSHttpError, match="tls_environment_not_allowed") as exc_info:
        _build_tls_context()

    assert marker not in f"{exc_info.value!r} {exc_info.value}"
    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize("override", ["transport", "quota"])
def test_production_client_rejects_each_private_dependency_override_before_redis(
    monkeypatch: pytest.MonkeyPatch,
    override: str,
) -> None:
    settings = ECOSSettings(_env_file=None)
    monkeypatch.setattr(
        ecos_http_client,
        "_build_redis_client",
        lambda: pytest.fail("caller override must stop before Redis construction"),
    )
    kwargs: dict[str, object] = {}
    if override == "transport":
        kwargs["transport"] = httpx.MockTransport(lambda _: httpx.Response(200))
    else:
        kwargs["quota"] = _RecordingQuota([])

    with pytest.raises(ValueError, match="private"):
        ECOSHttpClient(settings, **kwargs)  # type: ignore[arg-type]


def test_test_factory_rejects_every_non_mock_transport() -> None:
    with pytest.raises(ValueError, match="mock transport"):
        ECOSHttpClient._for_tests(
            ECOSSettings(_env_file=None),
            transport=_NonMockTransport(),
            quota=_RecordingQuota([]),
            credential=SecretStr("synthetic-key"),
        )


def test_test_factory_disables_ambient_env_and_rejects_redirects(tmp_path: Path) -> None:
    settings = ECOSSettings(_env_file=None)
    client = ECOSHttpClient._for_tests(
        settings,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(302, headers={"location": "https://x.invalid"})
        ),
        quota=_RecordingQuota([]),
        credential=SecretStr("synthetic-key"),
    )
    try:
        assert client._http.follow_redirects is False
        assert client._http._trust_env is False
        assert (
            client._http.headers.multi_items()
            == httpx.Headers(_canonical_client_headers()).multi_items()
        )
        with pytest.raises(ECOSHttpError, match="redirect_rejected"):
            client.get_json(_path())
    finally:
        client.close()


def test_registry_metadata_requests_never_retry() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, json={"RESULT": {"CODE": "ERROR-500"}})

    client = ECOSHttpClient._for_tests(
        ECOSSettings(_env_file=None, ECOS_MAX_ATTEMPTS_PER_REQUEST=2),
        transport=httpx.MockTransport(handler),
        quota=_RecordingQuota([]),
        credential=SecretStr("synthetic-key"),
    )
    try:
        with pytest.raises(ECOSHttpError, match="http_500"):
            client.statistic_table_list(series=CANDIDATE_SERIES[0])
    finally:
        client.close()

    assert attempts == 1


def test_attempt_setting_one_prevents_retry_send_and_backoff() -> None:
    attempts = 0
    backoffs: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, json={"RESULT": {"CODE": "ERROR-500"}})

    client = ECOSHttpClient._for_tests(
        ECOSSettings(_env_file=None, ECOS_MAX_ATTEMPTS_PER_REQUEST=1),
        transport=httpx.MockTransport(handler),
        quota=_RecordingQuota([]),
        credential=SecretStr("synthetic-key"),
        retry_sleeper=backoffs.append,
    )
    try:
        with pytest.raises(ECOSHttpError, match="http_500"):
            client.get_json(_path())
    finally:
        client.close()

    assert attempts == 1
    assert backoffs == []


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [(302, "redirect_rejected"), (500, "http_500")],
)
def test_sanitized_response_close_failure_cannot_override_stable_status_error(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected_code: str,
) -> None:
    marker = "synthetic-close-secret"
    calls = 0
    client = ECOSHttpClient._for_tests(
        ECOSSettings(_env_file=None),
        transport=httpx.MockTransport(lambda _: pytest.fail("patched get must be used")),
        quota=_RecordingQuota([]),
        credential=SecretStr("synthetic-key"),
    )

    def fake_get(_: str, **__: object) -> httpx.Response:
        nonlocal calls
        calls += 1
        response = httpx.Response(status, json={"RESULT": {"CODE": "ERROR-500"}})

        def fail_close() -> None:
            raise RuntimeError(f"{marker} https://ecos.bok.or.kr/private")

        monkeypatch.setattr(response, "close", fail_close)
        return response

    monkeypatch.setattr(client._http, "get", fake_get)
    try:
        with pytest.raises(ECOSHttpError) as exc_info:
            client.statistic_table_list(series=CANDIDATE_SERIES[0])
    finally:
        client.close()

    assert exc_info.value.code == expected_code
    assert calls == 1
    assert marker not in f"{exc_info.value!r} {exc_info.value}"
    assert exc_info.value.__cause__ is None


def test_second_attempt_drip_body_cannot_exceed_logical_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    attempts = 0
    timeout_extensions: list[dict[str, float]] = []
    monkeypatch.setattr(ecos_http_client.random, "uniform", lambda *_: 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        timeout = request.extensions.get("timeout")
        assert isinstance(timeout, dict)
        timeout_extensions.append(timeout)
        if attempts == 1:
            clock.advance(8.0)
            raise httpx.ConnectError("synthetic first attempt", request=request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=_DeadlineDripStream(clock, advance_seconds=5.0),
        )

    client = ECOSHttpClient._for_tests(
        ECOSSettings(_env_file=None),
        transport=httpx.MockTransport(handler),
        quota=_RecordingQuota([]),
        credential=SecretStr("synthetic-key"),
        monotonic=clock,
    )
    try:
        with pytest.raises(ECOSHttpError, match="logical_deadline_exceeded") as exc_info:
            client.get_json(_path())
    finally:
        client.close()

    assert attempts == 2
    assert timeout_extensions[0] == {
        "connect": 2.0,
        "read": 5.0,
        "write": 2.0,
        "pool": 1.0,
    }
    assert timeout_extensions[1] == {
        "connect": 2.0,
        "read": 4.0,
        "write": 2.0,
        "pool": 1.0,
    }
    assert clock.value == 13.0
    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize("operation", ["get_json", "table", "item"])
def test_error_602_activates_cooldown_without_retry_for_every_response_path(
    operation: str,
) -> None:
    attempts = 0
    cooldowns: list[int] = []

    class CooldownQuota(_RecordingQuota):
        def activate_cooldown(self, *, seconds: int) -> None:
            cooldowns.append(seconds)

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            json={
                "RESULT": {
                    "CODE": "ERROR-602",
                    "MESSAGE": "synthetic-provider-message https://ecos.bok.or.kr/private",
                }
            },
        )

    client = ECOSHttpClient._for_tests(
        ECOSSettings(_env_file=None),
        transport=httpx.MockTransport(handler),
        quota=CooldownQuota([]),
        credential=SecretStr("synthetic-key"),
    )
    try:
        with pytest.raises(ECOSApplicationError, match="ERROR-602") as exc_info:
            if operation == "get_json":
                client.get_json(_path())
            elif operation == "table":
                client.statistic_table_list(series=CANDIDATE_SERIES[0])
            else:
                client.statistic_item_list(series=CANDIDATE_SERIES[0])
    finally:
        client.close()

    assert attempts == 1
    assert cooldowns == [1_800]
    rendered = f"{exc_info.value!r} {exc_info.value}"
    assert "synthetic-provider-message" not in rendered
    assert "https://" not in rendered
    assert exc_info.value.__cause__ is None


def test_unicode_escaped_error_602_still_activates_cooldown_without_retry() -> None:
    attempts = 0
    cooldowns: list[int] = []

    class CooldownQuota(_RecordingQuota):
        def activate_cooldown(self, *, seconds: int) -> None:
            cooldowns.append(seconds)

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"RESULT":{"CODE":"ERROR-\\u0036\\u0030\\u0032"}}',
        )

    client = ECOSHttpClient._for_tests(
        ECOSSettings(_env_file=None),
        transport=httpx.MockTransport(handler),
        quota=CooldownQuota([]),
        credential=SecretStr("synthetic-key"),
    )
    try:
        with pytest.raises(ECOSApplicationError, match="ERROR-602"):
            client.get_json(_path())
    finally:
        client.close()

    assert attempts == 1
    assert cooldowns == [1_800]


def test_error_602_cooldown_failure_is_stable_and_never_retried() -> None:
    marker = "synthetic-cooldown-secret"
    attempts = 0

    class FailingCooldownQuota(_RecordingQuota):
        def activate_cooldown(self, *, seconds: int) -> None:
            raise RuntimeError(f"{marker}:{seconds}:https://redis.invalid")

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"RESULT": {"CODE": "ERROR-602"}})

    client = ECOSHttpClient._for_tests(
        ECOSSettings(_env_file=None),
        transport=httpx.MockTransport(handler),
        quota=FailingCooldownQuota([]),
        credential=SecretStr("synthetic-key"),
    )
    try:
        with pytest.raises(ECOSHttpError, match="quota_cooldown_unavailable") as exc_info:
            client.get_json(_path())
    finally:
        client.close()

    assert attempts == 1
    assert marker not in f"{exc_info.value!r} {exc_info.value}"
    assert exc_info.value.__cause__ is None
