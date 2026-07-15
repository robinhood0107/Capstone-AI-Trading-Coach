from __future__ import annotations

import json
import ssl
from collections.abc import Iterator
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.data._shared.redis_quota import (
    QuotaDeniedError,
    QuotaUnavailableError,
    QuotaWaitError,
    RedisQuotaPolicy,
)
from app.data.naver import _credential_transport, http_client as http_client_module
from app.data.naver._credential_transport import (
    NaverCredentialError,
    _Credentials,
    _CredentialTransport,
    _canonical_client_headers,
    _canonical_request_headers,
)
from app.data.naver.errors import NaverResponseError
from app.data.naver.http_client import (
    NaverHttpClient,
    _build_httpx_client,
    build_tls_context,
)
from app.data.naver.profiles import API_HUB_PROFILE, LEGACY_PROFILE, NaverProfile
from app.data.naver.settings import NaverSettings


_RETRIEVED_AT = datetime(2026, 7, 14, 1, 0, tzinfo=UTC)


@dataclass
class _RecordingQuota:
    reservations: list[str] = field(default_factory=list)
    cooldowns: list[int] = field(default_factory=list)

    def reserve(self, *, attempt_id: str) -> None:
        self.reservations.append(attempt_id)

    def activate_cooldown(self, *, seconds: int) -> None:
        self.cooldowns.append(seconds)


def _success_payload(title: str = "합성 뉴스") -> dict[str, object]:
    return {
        "lastBuildDate": "Tue, 14 Jul 2026 10:00:00 +0900",
        "total": 1,
        "start": 1,
        "display": 1,
        "items": [
            {
                "title": title,
                "originallink": "https://news.example.test/article/1",
                "link": "https://n.news.naver.com/article/001/1",
                "description": "합성 fixture 설명",
                "pubDate": "Tue, 14 Jul 2026 09:30:00 +0900",
            }
        ],
    }


def _stub_credentials(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    identifier = "validation-dummy-id"
    secret = "validation-dummy-secret"
    monkeypatch.setattr(
        _credential_transport,
        "_read_credentials",
        lambda _: _Credentials(identifier=SecretStr(identifier), secret=SecretStr(secret)),
    )
    return identifier, secret


def _test_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    profile: NaverProfile,
    handler: httpx.MockTransport,
    quota: _RecordingQuota | None = None,
) -> tuple[NaverHttpClient, _RecordingQuota]:
    _stub_credentials(monkeypatch)
    selected_quota = quota or _RecordingQuota()
    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None),
        profile=profile,
        transport=handler,
        quota=selected_quota,
        retry_delay=lambda _: 0.0,
    )
    return client, selected_quota


def test_test_constructor_accepts_only_mock_transport_and_canonical_profiles() -> None:
    class ArbitraryTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, request=request, json=_success_payload())

    settings = NaverSettings(_env_file=None)
    quota = _RecordingQuota()
    cloned_profile = dataclass_replace(LEGACY_PROFILE)

    with pytest.raises(ValueError, match="MockTransport"):
        NaverHttpClient._for_test(
            settings=settings,
            profile=LEGACY_PROFILE,
            transport=ArbitraryTransport(),
            quota=quota,
        )
    with pytest.raises(ValueError, match="canonical"):
        NaverHttpClient._for_test(
            settings=settings,
            profile=cloned_profile,
            transport=httpx.MockTransport(lambda _: httpx.Response(200)),
            quota=quota,
        )
    with pytest.raises(ValueError, match="canonical"):
        _CredentialTransport(
            httpx.MockTransport(lambda _: httpx.Response(200)),
            profile=cloned_profile,
            quota=quota,
        )


def test_credential_reader_rejects_cloned_profile_before_reading_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cloned_profile = dataclass_replace(LEGACY_PROFILE)
    monkeypatch.setattr(
        _credential_transport,
        "_CredentialSettings",
        lambda: pytest.fail("cloned profile reached credential settings"),
    )

    with pytest.raises(NaverCredentialError, match="profile_invalid"):
        _credential_transport._read_credentials(cloned_profile)


def test_production_rejects_clone_and_lifecycle_block_before_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis_builds = 0

    def fail_redis() -> object:
        nonlocal redis_builds
        redis_builds += 1
        raise AssertionError("profile gate must run before Redis")

    monkeypatch.setattr(http_client_module, "_build_redis_client", fail_redis)
    settings = NaverSettings(_env_file=None)
    with pytest.raises(ValueError, match="canonical"):
        NaverHttpClient(settings=settings, profile=dataclass_replace(LEGACY_PROFILE))

    def hard_stopped(_: str) -> NaverProfile:
        raise ValueError("legacy_hard_stop")

    monkeypatch.setattr(http_client_module, "profile_for", hard_stopped)
    with pytest.raises(ValueError, match="legacy_hard_stop"):
        NaverHttpClient(settings=settings, profile=LEGACY_PROFILE)

    assert redis_builds == 0


def test_transport_rechecks_lifecycle_before_each_physical_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_credentials(monkeypatch)
    outbound = 0
    quota = _RecordingQuota()

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json=_success_payload())

    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        profile=LEGACY_PROFILE,
        quota=quota,
    )
    params = {"query": "합성회사", "display": "10", "start": "1", "sort": "date"}
    with httpx.Client(
        transport=transport,
        headers=_canonical_client_headers(),
        trust_env=False,
        follow_redirects=False,
    ) as client:
        assert (
            client.get(
                f"{LEGACY_PROFILE.origin}{LEGACY_PROFILE.path}",
                params=params,
            ).status_code
            == 200
        )

        def blocked(_: str) -> NaverProfile:
            raise ValueError("legacy_hard_stop")

        monkeypatch.setattr(_credential_transport, "profile_for", blocked)
        with pytest.raises(NaverCredentialError, match="profile_unavailable"):
            client.get(
                f"{LEGACY_PROFILE.origin}{LEGACY_PROFILE.path}",
                params=params,
            )

    assert outbound == 1
    assert len(quota.reservations) == 1


@pytest.mark.parametrize("profile", [LEGACY_PROFILE, API_HUB_PROFILE])
def test_profile_credentials_exist_only_during_fixed_news_send(
    monkeypatch: pytest.MonkeyPatch,
    profile: NaverProfile,
) -> None:
    identifier, secret = _stub_credentials(monkeypatch)
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.scheme == "https"
        assert request.url.host == httpx.URL(profile.origin).host
        assert request.url.path == profile.path
        expected_query = {
            "query": "합성회사",
            "display": "10",
            "start": "1",
            "sort": "date",
        }
        if profile is API_HUB_PROFILE:
            expected_query["format"] = "json"
        assert dict(request.url.params) == expected_query
        assert request.headers[profile.auth_headers[0]] == identifier
        assert request.headers[profile.auth_headers[1]] == secret
        other_headers = (
            API_HUB_PROFILE.auth_headers
            if profile is LEGACY_PROFILE
            else LEGACY_PROFILE.auth_headers
        )
        assert all(header not in request.headers for header in other_headers)
        captured.append(request)
        return httpx.Response(200, json=_success_payload())

    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None),
        profile=profile,
        transport=httpx.MockTransport(handler),
        quota=_RecordingQuota(),
        retry_delay=lambda _: 0.0,
    )

    page = client.search_news(
        "합성회사",
        retrieved_at=_RETRIEVED_AT,
        requested_display=10,
    )

    assert page.accepted_count == 1
    assert all(header not in captured[0].headers for header in profile.auth_headers)
    client.close()


def test_set_cookie_and_provider_headers_are_dropped_without_next_request_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_credentials(monkeypatch)
    attempts = 0
    quota = _RecordingQuota()

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
        return httpx.Response(200, json=_success_payload(), headers=headers)

    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        profile=LEGACY_PROFILE,
        quota=quota,
    )
    params = {"query": "합성회사", "display": "10", "start": "1", "sort": "date"}
    with httpx.Client(
        transport=transport,
        headers=_canonical_client_headers(),
        trust_env=False,
        follow_redirects=False,
    ) as client:
        first = client.get(
            f"{LEGACY_PROFILE.origin}{LEGACY_PROFILE.path}",
            params=params,
        )
        second = client.get(
            f"{LEGACY_PROFILE.origin}{LEGACY_PROFILE.path}",
            params=params,
        )

    assert first.status_code == second.status_code == 200
    assert first.headers["content-type"] == "application/json"
    assert "set-cookie" not in first.headers
    assert "x-provider-state" not in first.headers
    assert attempts == 2
    assert len(quota.reservations) == 2


@pytest.mark.parametrize(
    "header_name",
    [*LEGACY_PROFILE.auth_headers, *API_HUB_PROFILE.auth_headers],
)
def test_caller_auth_and_mixed_profile_headers_are_rejected_before_outbound(
    monkeypatch: pytest.MonkeyPatch,
    header_name: str,
) -> None:
    _stub_credentials(monkeypatch)
    outbound = 0
    quota = _RecordingQuota()

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json=_success_payload())

    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        profile=LEGACY_PROFILE,
        quota=quota,
    )
    with httpx.Client(
        transport=transport,
        headers=_canonical_client_headers(),
        trust_env=False,
        follow_redirects=False,
    ) as client:
        with pytest.raises(NaverCredentialError, match="header"):
            client.get(
                f"{LEGACY_PROFILE.origin}{LEGACY_PROFILE.path}",
                headers={header_name: "caller-controlled"},
            )

    assert outbound == 0
    assert quota.reservations == []


@pytest.mark.parametrize(
    "header_name",
    ["Authorization", "Cookie", "Proxy-Authorization", "X-Api-Key", "X-Access-Token"],
)
def test_generic_sensitive_caller_headers_are_rejected_before_quota_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
    header_name: str,
) -> None:
    credential_reads = 0
    outbound = 0
    quota = _RecordingQuota()

    def read_credentials(_: NaverProfile) -> _Credentials:
        nonlocal credential_reads
        credential_reads += 1
        return _Credentials(
            identifier=SecretStr("validation-dummy-id"),
            secret=SecretStr("validation-dummy-secret"),
        )

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json=_success_payload())

    monkeypatch.setattr(_credential_transport, "_read_credentials", read_credentials)
    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        profile=LEGACY_PROFILE,
        quota=quota,
    )
    headers = _canonical_request_headers(LEGACY_PROFILE.origin)
    headers[header_name] = "caller-controlled"
    request = httpx.Request(
        "GET",
        f"{LEGACY_PROFILE.origin}{LEGACY_PROFILE.path}",
        params={"query": "합성회사", "display": "10", "start": "1", "sort": "date"},
        headers=headers,
    )

    with pytest.raises(NaverCredentialError, match="caller_auth_header_not_allowed"):
        transport.handle_request(request)

    assert credential_reads == 0
    assert quota.reservations == []
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
def test_arbitrary_caller_header_is_rejected_before_quota_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
    header_name: str,
) -> None:
    credential_reads = 0
    outbound = 0
    quota = _RecordingQuota()

    def read_credentials(_: NaverProfile) -> _Credentials:
        nonlocal credential_reads
        credential_reads += 1
        return _Credentials(
            identifier=SecretStr("validation-dummy-id"),
            secret=SecretStr("validation-dummy-secret"),
        )

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json=_success_payload())

    monkeypatch.setattr(_credential_transport, "_read_credentials", read_credentials)
    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        profile=LEGACY_PROFILE,
        quota=quota,
    )
    headers = _canonical_request_headers(LEGACY_PROFILE.origin)
    headers[header_name] = "caller-controlled"
    request = httpx.Request(
        "GET",
        f"{LEGACY_PROFILE.origin}{LEGACY_PROFILE.path}",
        params={"query": "합성회사", "display": "10", "start": "1", "sort": "date"},
        headers=headers,
    )

    with pytest.raises(NaverCredentialError, match="request_not_allowed"):
        transport.handle_request(request)

    assert credential_reads == 0
    assert quota.reservations == []
    assert outbound == 0


@pytest.mark.parametrize("mutation", ["accept-override", "user-agent-override", "accept-duplicate"])
def test_canonical_header_value_override_or_duplicate_is_rejected_before_quota(
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    quota = _RecordingQuota()
    headers = list(_canonical_request_headers(LEGACY_PROFILE.origin).items())
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

    monkeypatch.setattr(
        _credential_transport,
        "_read_credentials",
        lambda _: pytest.fail("noncanonical headers reached credential store"),
    )
    transport = _CredentialTransport(
        httpx.MockTransport(lambda _: pytest.fail("noncanonical headers reached outbound")),
        profile=LEGACY_PROFILE,
        quota=quota,
    )
    request = httpx.Request(
        "GET",
        f"{LEGACY_PROFILE.origin}{LEGACY_PROFILE.path}",
        params={"query": "합성회사", "display": "10", "start": "1", "sort": "date"},
        headers=headers,
    )

    with pytest.raises(NaverCredentialError, match="request_not_allowed"):
        transport.handle_request(request)

    assert quota.reservations == []


def test_url_userinfo_is_rejected_before_quota_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_reads = 0
    outbound = 0
    quota = _RecordingQuota()

    def read_credentials(_: NaverProfile) -> _Credentials:
        nonlocal credential_reads
        credential_reads += 1
        return _Credentials(
            identifier=SecretStr("validation-dummy-id"),
            secret=SecretStr("validation-dummy-secret"),
        )

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json=_success_payload())

    monkeypatch.setattr(_credential_transport, "_read_credentials", read_credentials)
    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        profile=LEGACY_PROFILE,
        quota=quota,
    )
    request = httpx.Request(
        "GET",
        "https://caller:secret@openapi.naver.com/v1/search/news.json",
        params={"query": "합성회사", "display": "10", "start": "1", "sort": "date"},
        headers=_canonical_request_headers(LEGACY_PROFILE.origin),
    )

    with pytest.raises(NaverCredentialError, match="origin_not_allowed"):
        transport.handle_request(request)

    assert credential_reads == 0
    assert quota.reservations == []
    assert outbound == 0


@pytest.mark.parametrize(
    "host_header",
    ["attacker.invalid", "openapi.naver.com:443", "OPENAPI.NAVER.COM"],
)
def test_noncanonical_host_header_is_rejected_before_quota_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
    host_header: str,
) -> None:
    credential_reads = 0
    outbound = 0
    quota = _RecordingQuota()

    def read_credentials(_: NaverProfile) -> _Credentials:
        nonlocal credential_reads
        credential_reads += 1
        return _Credentials(
            identifier=SecretStr("validation-dummy-id"),
            secret=SecretStr("validation-dummy-secret"),
        )

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json=_success_payload())

    monkeypatch.setattr(_credential_transport, "_read_credentials", read_credentials)
    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        profile=LEGACY_PROFILE,
        quota=quota,
    )
    headers = _canonical_request_headers(LEGACY_PROFILE.origin)
    headers["Host"] = host_header
    request = httpx.Request(
        "GET",
        f"{LEGACY_PROFILE.origin}{LEGACY_PROFILE.path}",
        params={"query": "합성회사", "display": "10", "start": "1", "sort": "date"},
        headers=headers,
    )

    with pytest.raises(NaverCredentialError, match="origin_not_allowed"):
        transport.handle_request(request)

    assert credential_reads == 0
    assert quota.reservations == []
    assert outbound == 0


def test_unapproved_request_extension_is_rejected_before_quota_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_reads = 0
    outbound = 0
    quota = _RecordingQuota()

    def read_credentials(_: NaverProfile) -> _Credentials:
        nonlocal credential_reads
        credential_reads += 1
        return _Credentials(
            identifier=SecretStr("validation-dummy-id"),
            secret=SecretStr("validation-dummy-secret"),
        )

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json=_success_payload())

    monkeypatch.setattr(_credential_transport, "_read_credentials", read_credentials)
    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        profile=LEGACY_PROFILE,
        quota=quota,
    )
    request = httpx.Request(
        "GET",
        f"{LEGACY_PROFILE.origin}{LEGACY_PROFILE.path}",
        params={"query": "합성회사", "display": "10", "start": "1", "sort": "date"},
        headers=_canonical_request_headers(LEGACY_PROFILE.origin),
        extensions={"caller-controlled": "validation-dummy-secret"},
    )

    with pytest.raises(NaverCredentialError, match="request_not_allowed") as exc_info:
        transport.handle_request(request)

    assert "validation-dummy-secret" not in f"{exc_info.value!r} {exc_info.value}"
    assert credential_reads == 0
    assert quota.reservations == []
    assert outbound == 0


@pytest.mark.parametrize(
    "url",
    [
        "https://attacker.invalid/v1/search/news.json",
        "https://openapi.naver.com/v1/search/blog.json",
    ],
)
def test_wrong_origin_or_non_news_path_is_rejected_before_quota_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    credential_reads = 0
    quota = _RecordingQuota()

    def read_credentials(_: NaverProfile) -> _Credentials:
        nonlocal credential_reads
        credential_reads += 1
        return _Credentials(
            identifier=SecretStr("validation-dummy-id"),
            secret=SecretStr("validation-dummy-secret"),
        )

    monkeypatch.setattr(_credential_transport, "_read_credentials", read_credentials)
    transport = _CredentialTransport(
        httpx.MockTransport(lambda _: pytest.fail("unsafe request reached outbound")),
        profile=LEGACY_PROFILE,
        quota=quota,
    )

    with pytest.raises(NaverCredentialError, match="origin|path"):
        transport.handle_request(
            httpx.Request(
                "GET",
                url,
                headers=_canonical_request_headers(LEGACY_PROFILE.origin),
            )
        )

    assert credential_reads == 0
    assert quota.reservations == []


def test_missing_credentials_fail_after_reservation_but_before_outbound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outbound = 0
    quota = _RecordingQuota()

    def unavailable(_: NaverProfile) -> _Credentials:
        raise NaverCredentialError("naver authentication unavailable")

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json=_success_payload())

    monkeypatch.setattr(_credential_transport, "_read_credentials", unavailable)
    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None),
        profile=LEGACY_PROFILE,
        transport=httpx.MockTransport(handler),
        quota=quota,
        retry_delay=lambda _: 0.0,
    )

    with pytest.raises(NaverCredentialError):
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    # quota reservation 뒤에는 실패하더라도 refund하지 않는다.
    assert len(quota.reservations) == 1
    assert client.physical_attempt_count == 0
    assert outbound == 0
    client.close()


@pytest.mark.parametrize(
    "unsafe_material",
    ["synthetic\nheader", "합성-secret", "x" * 513],
)
def test_credential_header_material_is_bounded_printable_ascii(
    monkeypatch: pytest.MonkeyPatch,
    unsafe_material: str,
) -> None:
    outbound = 0
    quota = _RecordingQuota()

    monkeypatch.setattr(
        _credential_transport,
        "_read_credentials",
        lambda _: _Credentials(
            identifier=SecretStr("validation-dummy-id"),
            secret=SecretStr(unsafe_material),
        ),
    )

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json=_success_payload())

    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None),
        profile=LEGACY_PROFILE,
        transport=httpx.MockTransport(handler),
        quota=quota,
    )

    with pytest.raises(NaverCredentialError, match="authentication_unavailable") as exc_info:
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    assert unsafe_material not in f"{exc_info.value!r} {exc_info.value}"
    assert exc_info.value.__cause__ is None
    assert len(quota.reservations) == 1
    assert client.physical_attempt_count == 0
    assert outbound == 0
    client.close()


def test_header_setup_exception_is_mapped_without_secret_or_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier, secret = _stub_credentials(monkeypatch)
    outbound = 0
    real_setitem = httpx.Headers.__setitem__

    def fail_auth_header(self: httpx.Headers, key: str, value: str) -> None:
        if key.lower() in {header.lower() for header in LEGACY_PROFILE.auth_headers}:
            raise RuntimeError(f"synthetic header setup {identifier} {secret}")
        real_setitem(self, key, value)

    monkeypatch.setattr(httpx.Headers, "__setitem__", fail_auth_header)

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json=_success_payload())

    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None),
        profile=LEGACY_PROFILE,
        transport=httpx.MockTransport(handler),
        quota=_RecordingQuota(),
    )

    with pytest.raises(NaverCredentialError, match="authentication_unavailable") as exc_info:
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    rendered = f"{exc_info.value!r} {exc_info.value}"
    assert identifier not in rendered
    assert secret not in rendered
    assert exc_info.value.__cause__ is None
    assert client.physical_attempt_count == 0
    assert outbound == 0
    client.close()


def test_quota_failure_does_not_read_credentials_or_attempt_outbound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_reads = 0
    outbound = 0

    def read_credentials(_: NaverProfile) -> _Credentials:
        nonlocal credential_reads
        credential_reads += 1
        return _Credentials(
            identifier=SecretStr("validation-dummy-id"),
            secret=SecretStr("validation-dummy-secret"),
        )

    class RejectingQuota:
        def reserve(self, *, attempt_id: str) -> None:
            raise QuotaUnavailableError("quota unavailable")

        def activate_cooldown(self, *, seconds: int) -> None:
            raise AssertionError("a failed reservation cannot activate cooldown")

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json=_success_payload())

    monkeypatch.setattr(_credential_transport, "_read_credentials", read_credentials)
    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None),
        profile=LEGACY_PROFILE,
        transport=httpx.MockTransport(handler),
        quota=RejectingQuota(),
        retry_delay=lambda _: 0.0,
    )

    with pytest.raises(QuotaUnavailableError):
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    assert credential_reads == 0
    assert outbound == 0
    client.close()


def test_transport_exception_restores_request_headers_and_hides_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier, secret = _stub_credentials(monkeypatch)
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        raise httpx.ConnectError(
            f"synthetic connection failure {identifier} {secret}",
            request=request,
        )

    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None),
        profile=LEGACY_PROFILE,
        transport=httpx.MockTransport(handler),
        quota=_RecordingQuota(),
        retry_delay=lambda _: 0.0,
    )

    with pytest.raises(NaverCredentialError, match="transport_unavailable") as exc_info:
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    assert identifier not in f"{exc_info.value!r} {exc_info.value}"
    assert secret not in f"{exc_info.value!r} {exc_info.value}"
    assert exc_info.value.__cause__ is None
    assert len(captured) == 2
    assert all(
        header not in request.headers
        for request in captured
        for header in LEGACY_PROFILE.auth_headers
    )
    client.close()


def test_inner_transport_exception_counts_exactly_one_provider_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_credentials(monkeypatch)
    outbound = 0
    quota = _RecordingQuota()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        raise httpx.ConnectError("synthetic connection failure", request=request)

    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        profile=LEGACY_PROFILE,
        quota=quota,
    )
    request = httpx.Request(
        "GET",
        f"{LEGACY_PROFILE.origin}{LEGACY_PROFILE.path}",
        params={"query": "합성회사", "display": "10", "start": "1", "sort": "date"},
        headers=_canonical_request_headers(LEGACY_PROFILE.origin),
    )

    with pytest.raises(NaverCredentialError, match="transport_unavailable") as exc_info:
        transport.handle_request(request)

    assert len(quota.reservations) == 1
    assert transport.physical_attempt_count == 1
    assert outbound == 1
    assert all(header not in request.headers for header in LEGACY_PROFILE.auth_headers)
    assert exc_info.value.__cause__ is None


def test_unexpected_inner_exception_is_mapped_without_credential_or_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier, secret = _stub_credentials(monkeypatch)
    outbound = 0
    captured: list[httpx.Request] = []
    quota = _RecordingQuota()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        captured.append(request)
        raise RuntimeError(f"synthetic inner failure {identifier} {secret}")

    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None, NAVER_MAX_ATTEMPTS_PER_QUERY=1),
        profile=LEGACY_PROFILE,
        transport=httpx.MockTransport(handler),
        quota=quota,
        retry_delay=lambda _: 0.0,
    )

    with pytest.raises(NaverCredentialError, match="transport_unavailable") as exc_info:
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    rendered = f"{exc_info.value!r} {exc_info.value}"
    assert identifier not in rendered
    assert secret not in rendered
    assert exc_info.value.retryable is True
    assert exc_info.value.__cause__ is None
    assert len(quota.reservations) == 1
    assert client.physical_attempt_count == 1
    assert outbound == 1
    assert all(
        header not in request.headers
        for request in captured
        for header in LEGACY_PROFILE.auth_headers
    )
    client.close()


def test_response_stream_timeout_is_retryable_transport_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier, secret = _stub_credentials(monkeypatch)
    attempts = 0

    class TimeoutStream(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            raise httpx.ReadTimeout(f"synthetic read timeout {identifier} {secret}")
            yield b""  # pragma: no cover

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, stream=TimeoutStream())

    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None),
        profile=LEGACY_PROFILE,
        transport=httpx.MockTransport(handler),
        quota=_RecordingQuota(),
        retry_delay=lambda _: 0.0,
    )

    with pytest.raises(NaverCredentialError, match="transport_unavailable") as exc_info:
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    assert exc_info.value.retryable is True
    assert attempts == 2
    assert identifier not in f"{exc_info.value!r} {exc_info.value}"
    assert secret not in f"{exc_info.value!r} {exc_info.value}"
    assert exc_info.value.__cause__ is None
    client.close()


def test_logical_deadline_bounds_drip_stream_elapsed_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_credentials(monkeypatch)
    now = 0.0
    chunks = 0
    attempts = 0

    def monotonic() -> float:
        return now

    class DripStream(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            nonlocal now, chunks
            for _ in range(10):
                now += 1.1
                chunks += 1
                yield b" "

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, stream=DripStream())

    monkeypatch.setattr(http_client_module.time, "monotonic", monotonic)
    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None, NAVER_LOGICAL_DEADLINE_SECONDS=3.0),
        profile=LEGACY_PROFILE,
        transport=httpx.MockTransport(handler),
        quota=_RecordingQuota(),
        retry_delay=lambda _: 0.0,
    )

    with pytest.raises(NaverCredentialError, match="logical_deadline_exceeded"):
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    assert attempts == 1
    assert chunks == 3
    client.close()


def test_oversized_content_length_is_rejected_before_stream_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_credentials(monkeypatch)
    iterated = False

    class RecordingStream(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            nonlocal iterated
            iterated = True
            yield b"{}"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-length": str(512 * 1024 + 1)},
            stream=RecordingStream(),
        )

    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None),
        profile=LEGACY_PROFILE,
        transport=httpx.MockTransport(handler),
        quota=_RecordingQuota(),
    )

    with pytest.raises(NaverCredentialError, match="response_too_large"):
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    assert iterated is False
    client.close()


def test_response_close_exception_restores_headers_and_raises_stable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier, secret = _stub_credentials(monkeypatch)
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        response = httpx.Response(200, json=_success_payload())

        def fail_close() -> None:
            raise RuntimeError(f"synthetic close failure {identifier} {secret}")

        monkeypatch.setattr(response, "close", fail_close)
        return response

    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None),
        profile=LEGACY_PROFILE,
        transport=httpx.MockTransport(handler),
        quota=_RecordingQuota(),
        retry_delay=lambda _: 0.0,
    )

    with pytest.raises(NaverCredentialError, match="response_unavailable") as exc_info:
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    rendered = f"{exc_info.value!r} {exc_info.value}"
    assert "synthetic close failure" not in rendered
    assert identifier not in rendered
    assert secret not in rendered
    assert exc_info.value.__cause__ is None
    assert len(captured) == 1
    assert all(header not in captured[0].headers for header in LEGACY_PROFILE.auth_headers)
    client.close()


def test_provider_echo_cannot_reach_normalized_page_or_response_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier, secret = _stub_credentials(monkeypatch)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_success_payload(title=f"{identifier} {secret}"),
            headers={"x-provider-echo": secret},
        )

    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None),
        profile=LEGACY_PROFILE,
        transport=httpx.MockTransport(handler),
        quota=_RecordingQuota(),
        retry_delay=lambda _: 0.0,
    )
    page = client.search_news(
        "합성회사",
        retrieved_at=_RETRIEVED_AT,
        requested_display=10,
    )

    rendered = repr(page)
    assert identifier not in rendered
    assert secret not in rendered
    client.close()


def test_overlapping_credential_echo_redacts_the_longest_value_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = "validation-prefix"
    secret_suffix = "-sensitive-secret-suffix"
    secret = f"{identifier}{secret_suffix}"
    monkeypatch.setattr(
        _credential_transport,
        "_read_credentials",
        lambda _: _Credentials(
            identifier=SecretStr(identifier),
            secret=SecretStr(secret),
        ),
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_payload(title=secret))

    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None),
        profile=LEGACY_PROFILE,
        transport=httpx.MockTransport(handler),
        quota=_RecordingQuota(),
        retry_delay=lambda _: 0.0,
    )
    page = client.search_news(
        "합성회사",
        retrieved_at=_RETRIEVED_AT,
        requested_display=10,
    )

    rendered = repr(page)
    assert identifier not in rendered
    assert secret not in rendered
    assert secret_suffix not in rendered
    client.close()


def test_unicode_escaped_credential_echo_fails_before_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier, secret = _stub_credentials(monkeypatch)

    def handler(_: httpx.Request) -> httpx.Response:
        raw = json.dumps(
            _success_payload(title=f"{identifier} {secret}"),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        for candidate in (identifier, secret):
            escaped = "".join(f"\\u{ord(character):04x}" for character in candidate)
            raw = raw.replace(candidate, escaped)
        return httpx.Response(
            200,
            content=raw.encode("utf-8"),
            headers={"content-type": "application/json"},
        )

    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None),
        profile=LEGACY_PROFILE,
        transport=httpx.MockTransport(handler),
        quota=_RecordingQuota(),
    )

    with pytest.raises(NaverCredentialError, match="response_unavailable") as exc_info:
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    rendered = f"{exc_info.value!r} {exc_info.value}"
    assert identifier not in rendered
    assert secret not in rendered
    assert exc_info.value.__cause__ is None
    client.close()


@pytest.mark.parametrize("echo_form", ["html-entity", "control-removal", "url-percent"])
def test_normalized_credential_echo_forms_fail_before_persistence(
    monkeypatch: pytest.MonkeyPatch,
    echo_form: str,
) -> None:
    secret = "abc"
    monkeypatch.setattr(
        _credential_transport,
        "_read_credentials",
        lambda _: _Credentials(
            identifier=SecretStr("validation-dummy-id"),
            secret=SecretStr(secret),
        ),
    )

    def handler(_: httpx.Request) -> httpx.Response:
        payload = _success_payload()
        item = payload["items"][0]
        if echo_form == "html-entity":
            item["title"] = "a&#98;c"
        elif echo_form == "control-removal":
            item["title"] = "a\u0000bc"
        else:
            item["originallink"] = "https://news.example.test/article?q=a%62c"
        return httpx.Response(200, json=payload)

    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None),
        profile=LEGACY_PROFILE,
        transport=httpx.MockTransport(handler),
        quota=_RecordingQuota(),
    )

    with pytest.raises(NaverCredentialError, match="response_unavailable"):
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    client.close()


def test_denied_quota_is_not_counted_as_a_physical_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_credentials(monkeypatch)

    class DeniedQuota:
        def reserve(self, *, attempt_id: str) -> None:
            raise QuotaDeniedError(retry_after_ms=250, observed_count=2_000)

        def activate_cooldown(self, *, seconds: int) -> None:
            raise AssertionError("denied quota cannot activate cooldown")

    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None),
        profile=LEGACY_PROFILE,
        transport=httpx.MockTransport(lambda _: pytest.fail("quota denial reached outbound")),
        quota=DeniedQuota(),
        retry_delay=lambda _: 0.0,
    )

    with pytest.raises(QuotaDeniedError):
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    assert client.physical_attempt_count == 0
    client.close()


def test_min_interval_wait_sleeps_and_rereserves_without_counting_an_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_credentials(monkeypatch)
    reserve_calls: list[str] = []
    sleeps: list[float] = []
    outbound = 0

    class WaitOnceQuota:
        def reserve(self, *, attempt_id: str) -> None:
            reserve_calls.append(attempt_id)
            if len(reserve_calls) == 1:
                raise QuotaWaitError(retry_after_ms=250, observed_count=1)

        def activate_cooldown(self, *, seconds: int) -> None:
            raise AssertionError("successful response cannot activate cooldown")

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json=_success_payload())

    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None),
        profile=LEGACY_PROFILE,
        transport=httpx.MockTransport(handler),
        quota=WaitOnceQuota(),
        quota_wait_sleep=sleeps.append,
    )

    page = client.search_news(
        "합성회사",
        retrieved_at=_RETRIEVED_AT,
        requested_display=10,
    )

    assert page.accepted_count == 1
    assert reserve_calls[0] == reserve_calls[1]
    assert sleeps == [0.25]
    assert client.physical_attempt_count == 1
    assert outbound == 1
    client.close()


def test_min_interval_wait_beyond_deadline_defers_without_sleep_or_outbound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_credentials(monkeypatch)
    sleeps: list[float] = []
    outbound = 0

    class LongWaitQuota:
        def reserve(self, *, attempt_id: str) -> None:
            raise QuotaWaitError(retry_after_ms=4_000, observed_count=1)

        def activate_cooldown(self, *, seconds: int) -> None:
            raise AssertionError("wait cannot activate cooldown")

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json=_success_payload())

    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None, NAVER_LOGICAL_DEADLINE_SECONDS=3.0),
        profile=LEGACY_PROFILE,
        transport=httpx.MockTransport(handler),
        quota=LongWaitQuota(),
        quota_wait_sleep=sleeps.append,
    )

    with pytest.raises(NaverCredentialError, match="logical_deadline_exceeded"):
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    assert sleeps == []
    assert client.physical_attempt_count == 0
    assert outbound == 0
    client.close()


def test_quota_latency_expiring_deadline_stops_before_credentials_or_physical_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 0.0
    credential_reads = 0
    outbound = 0

    def monotonic() -> float:
        return now

    class SlowQuota:
        def reserve(self, *, attempt_id: str) -> None:
            nonlocal now
            assert attempt_id
            now = 1.0

        def activate_cooldown(self, *, seconds: int) -> None:
            raise AssertionError("expired request cannot activate cooldown")

    def read_credentials(_: NaverProfile) -> _Credentials:
        nonlocal credential_reads
        credential_reads += 1
        return _Credentials(
            identifier=SecretStr("validation-dummy-id"),
            secret=SecretStr("validation-dummy-secret"),
        )

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json=_success_payload())

    monkeypatch.setattr(_credential_transport.time, "monotonic", monotonic)
    monkeypatch.setattr(_credential_transport, "_read_credentials", read_credentials)
    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        profile=LEGACY_PROFILE,
        quota=SlowQuota(),
    )
    request = httpx.Request(
        "GET",
        f"{LEGACY_PROFILE.origin}{LEGACY_PROFILE.path}",
        params={"query": "합성회사", "display": "10", "start": "1", "sort": "date"},
        headers=_canonical_request_headers(LEGACY_PROFILE.origin),
        extensions={"s1.3.naver.logical_deadline": 1.0},
    )

    with pytest.raises(NaverCredentialError, match="logical_deadline_exceeded") as exc_info:
        transport.handle_request(request)

    assert credential_reads == 0
    assert transport.physical_attempt_count == 0
    assert outbound == 0
    assert exc_info.value.__cause__ is None


def test_credential_latency_expiring_deadline_stops_immediately_before_outbound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 0.0
    credential_reads = 0
    outbound = 0
    quota = _RecordingQuota()

    def monotonic() -> float:
        return now

    def read_credentials(_: NaverProfile) -> _Credentials:
        nonlocal credential_reads, now
        credential_reads += 1
        now = 1.0
        return _Credentials(
            identifier=SecretStr("validation-dummy-id"),
            secret=SecretStr("validation-dummy-secret"),
        )

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json=_success_payload())

    monkeypatch.setattr(_credential_transport.time, "monotonic", monotonic)
    monkeypatch.setattr(_credential_transport, "_read_credentials", read_credentials)
    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        profile=LEGACY_PROFILE,
        quota=quota,
    )
    request = httpx.Request(
        "GET",
        f"{LEGACY_PROFILE.origin}{LEGACY_PROFILE.path}",
        params={"query": "합성회사", "display": "10", "start": "1", "sort": "date"},
        headers=_canonical_request_headers(LEGACY_PROFILE.origin),
        extensions={"s1.3.naver.logical_deadline": 1.0},
    )

    with pytest.raises(NaverCredentialError, match="logical_deadline_exceeded") as exc_info:
        transport.handle_request(request)

    assert credential_reads == 1
    assert len(quota.reservations) == 1
    assert transport.physical_attempt_count == 0
    assert outbound == 0
    assert all(header not in request.headers for header in LEGACY_PROFILE.auth_headers)
    assert exc_info.value.__cause__ is None


def test_header_construction_expiring_deadline_is_not_a_provider_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 0.0
    outbound = 0
    auth_header_writes = 0
    quota = _RecordingQuota()
    _stub_credentials(monkeypatch)

    def monotonic() -> float:
        return now

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal outbound
        outbound += 1
        return httpx.Response(200, json=_success_payload())

    transport = _CredentialTransport(
        httpx.MockTransport(handler),
        profile=LEGACY_PROFILE,
        quota=quota,
    )
    request = httpx.Request(
        "GET",
        f"{LEGACY_PROFILE.origin}{LEGACY_PROFILE.path}",
        params={"query": "합성회사", "display": "10", "start": "1", "sort": "date"},
        headers=_canonical_request_headers(LEGACY_PROFILE.origin),
        extensions={"s1.3.naver.logical_deadline": 1.0},
    )
    real_setitem = httpx.Headers.__setitem__

    def delayed_auth_header(self: httpx.Headers, key: str, value: str) -> None:
        nonlocal auth_header_writes, now
        real_setitem(self, key, value)
        if key.lower() in {header.lower() for header in LEGACY_PROFILE.auth_headers}:
            auth_header_writes += 1
            if auth_header_writes == len(LEGACY_PROFILE.auth_headers):
                now = 1.0

    monkeypatch.setattr(_credential_transport.time, "monotonic", monotonic)
    monkeypatch.setattr(httpx.Headers, "__setitem__", delayed_auth_header)

    with pytest.raises(NaverCredentialError, match="logical_deadline_exceeded") as exc_info:
        transport.handle_request(request)

    assert auth_header_writes == 2
    assert len(quota.reservations) == 1
    assert transport.physical_attempt_count == 0
    assert outbound == 0
    assert all(header not in request.headers for header in LEGACY_PROFILE.auth_headers)
    assert exc_info.value.__cause__ is None


def test_each_retry_reserves_a_physical_attempt_without_refund(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    quota = _RecordingQuota()

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(500, json={"errorCode": "SE99"})
        return httpx.Response(200, json=_success_payload())

    client, _ = _test_client(
        monkeypatch,
        profile=LEGACY_PROFILE,
        handler=httpx.MockTransport(handler),
        quota=quota,
    )

    page = client.search_news(
        "합성회사",
        retrieved_at=_RETRIEVED_AT,
        requested_display=10,
    )

    assert page.accepted_count == 1
    assert attempts == 2
    assert len(quota.reservations) == 2
    assert len(set(quota.reservations)) == 2
    client.close()


def test_attempt_setting_one_prevents_retry_send_and_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    backoff_calls: list[int] = []
    quota = _RecordingQuota()
    _stub_credentials(monkeypatch)

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, json={"errorCode": "SE99"})

    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None, NAVER_MAX_ATTEMPTS_PER_QUERY=1),
        profile=LEGACY_PROFILE,
        transport=httpx.MockTransport(handler),
        quota=quota,
        retry_delay=lambda attempt: backoff_calls.append(attempt) or 0.0,
    )
    try:
        with pytest.raises(NaverResponseError) as exc_info:
            client.search_news(
                "합성회사",
                retrieved_at=_RETRIEVED_AT,
                requested_display=10,
            )
    finally:
        client.close()

    assert exc_info.value.code == "provider_unavailable"
    assert attempts == 1
    assert quota.reservations != []
    assert len(quota.reservations) == 1
    assert backoff_calls == []


def test_429_is_not_retried_and_activates_60_second_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    quota = _RecordingQuota()

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429, json={"errorCode": "SE99"})

    client, _ = _test_client(
        monkeypatch,
        profile=LEGACY_PROFILE,
        handler=httpx.MockTransport(handler),
        quota=quota,
    )

    with pytest.raises(NaverResponseError) as exc_info:
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    assert exc_info.value.code == "rate_limited"
    assert attempts == 1
    assert len(quota.reservations) == 1
    assert quota.cooldowns == [60]
    client.close()


def test_429_with_invalid_body_still_activates_cooldown_and_stays_non_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    quota = _RecordingQuota()

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429, content=b"not-json", headers={"content-type": "text/plain"})

    client, _ = _test_client(
        monkeypatch,
        profile=LEGACY_PROFILE,
        handler=httpx.MockTransport(handler),
        quota=quota,
    )

    with pytest.raises(NaverResponseError) as exc_info:
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    assert exc_info.value.code == "rate_limited"
    assert attempts == 1
    assert quota.cooldowns == [60]
    client.close()


def test_direct_429_cools_down_without_reading_oversized_failing_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_credentials(monkeypatch)
    attempts = 0
    iterated = False
    quota = _RecordingQuota()

    class UntrustedStream(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            nonlocal iterated
            iterated = True
            raise httpx.ReadTimeout("synthetic 429 body must not be read")
            yield b""  # pragma: no cover

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            429,
            headers={"content-length": str(1024 * 1024 + 1)},
            stream=UntrustedStream(),
        )

    client = NaverHttpClient._for_test(
        settings=NaverSettings(_env_file=None),
        profile=LEGACY_PROFILE,
        transport=httpx.MockTransport(handler),
        quota=quota,
        retry_delay=lambda _: 0.0,
    )

    with pytest.raises(NaverResponseError) as exc_info:
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    assert exc_info.value.code == "rate_limited"
    assert exc_info.value.retryable is False
    assert attempts == 1
    assert iterated is False
    assert quota.cooldowns == [60]
    client.close()


def test_gateway_embedded_429_activates_one_cooldown_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    quota = _RecordingQuota()

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            json={
                "error": {
                    "statusCode": 429,
                    "code": "TooManyRequests",
                    "message": "synthetic",
                }
            },
        )

    client, _ = _test_client(
        monkeypatch,
        profile=API_HUB_PROFILE,
        handler=httpx.MockTransport(handler),
        quota=quota,
    )

    with pytest.raises(NaverResponseError) as exc_info:
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    assert exc_info.value.code == "rate_limited"
    assert exc_info.value.retryable is False
    assert attempts == 1
    assert len(quota.reservations) == 1
    assert quota.cooldowns == [60]
    client.close()


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("content", [b"not-json", b"[]"])
def test_malformed_auth_response_stops_with_stable_nonretryable_taxonomy(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    content: bytes,
) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status, content=content)

    client, _ = _test_client(
        monkeypatch,
        profile=LEGACY_PROFILE,
        handler=httpx.MockTransport(handler),
    )

    with pytest.raises(NaverResponseError) as exc_info:
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    assert exc_info.value.code == "authentication_failed"
    assert exc_info.value.retryable is False
    assert attempts == 1
    client.close()


@pytest.mark.parametrize(
    ("status", "expected_code", "retryable", "expected_attempts"),
    [
        (429, "rate_limited", False, 1),
        (500, "provider_unavailable", True, 2),
    ],
)
def test_non_object_json_is_classified_by_http_status(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected_code: str,
    retryable: bool,
    expected_attempts: int,
) -> None:
    attempts = 0
    quota = _RecordingQuota()

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status, json=[])

    client, _ = _test_client(
        monkeypatch,
        profile=LEGACY_PROFILE,
        handler=httpx.MockTransport(handler),
        quota=quota,
    )

    with pytest.raises(NaverResponseError) as exc_info:
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    assert exc_info.value.code == expected_code
    assert exc_info.value.retryable is retryable
    assert attempts == expected_attempts
    assert quota.cooldowns == ([60] if status == 429 else [])
    client.close()


def test_redirect_is_rejected_without_following_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(302, headers={"location": "https://attacker.invalid/article"})

    client, _ = _test_client(
        monkeypatch,
        profile=LEGACY_PROFILE,
        handler=httpx.MockTransport(handler),
    )

    with pytest.raises(NaverResponseError) as exc_info:
        client.search_news(
            "합성회사",
            retrieved_at=_RETRIEVED_AT,
            requested_display=10,
        )

    assert exc_info.value.code == "redirect_rejected"
    assert attempts == 1
    client.close()


def test_httpx_client_disables_environment_and_redirects() -> None:
    raw_client = _build_httpx_client(transport=httpx.MockTransport(lambda _: httpx.Response(200)))

    assert raw_client.follow_redirects is False
    assert getattr(raw_client, "_trust_env") is False
    assert (
        raw_client.headers.multi_items() == httpx.Headers(_canonical_client_headers()).multi_items()
    )
    raw_client.close()


def test_production_client_rejects_caller_transport_and_quota_overrides() -> None:
    with pytest.raises(ValueError, match="private"):
        NaverHttpClient(
            settings=NaverSettings(_env_file=None),
            profile=LEGACY_PROFILE,
            transport=httpx.MockTransport(lambda _: httpx.Response(200)),
            quota=_RecordingQuota(),
        )


def test_production_redis_reservation_uses_lower_runtime_call_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_policies: list[RedisQuotaPolicy] = []

    class FakeRedis:
        def close(self) -> None:
            return

    def build_reservation(
        redis_client: object,
        *,
        key: str,
        policy: RedisQuotaPolicy,
    ) -> _RecordingQuota:
        del redis_client, key
        captured_policies.append(policy)
        return _RecordingQuota()

    monkeypatch.setattr(http_client_module, "_build_redis_client", FakeRedis)
    monkeypatch.setattr(http_client_module, "RedisQuotaReservation", build_reservation)
    monkeypatch.setattr(
        http_client_module.httpx,
        "HTTPTransport",
        lambda **_: httpx.MockTransport(lambda _: httpx.Response(200)),
    )

    client = NaverHttpClient(
        settings=NaverSettings(_env_file=None, NAVER_MAX_CALLS_PER_RUN=3),
        profile=LEGACY_PROFILE,
    )

    assert len(captured_policies) == 1
    policy = captured_policies[0]
    assert policy.max_calls_per_run == 3
    assert policy.version == "s1.3-naver-legacy-quota-v1"
    assert [(window.limit, window.seconds) for window in policy.windows] == [(2_000, 86_400)]
    client.close()


@pytest.mark.parametrize("password", [None, "", " \t\n"])
def test_private_redis_client_rejects_missing_or_blank_password_before_construction(
    monkeypatch: pytest.MonkeyPatch,
    password: str | None,
) -> None:
    original_settings = http_client_module._RedisSettings
    redis_builds = 0

    if password is None:
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("REDIS_PASSWORD", password)
    monkeypatch.setattr(
        http_client_module,
        "_RedisSettings",
        lambda: original_settings(_env_file=None),
    )

    def fail_redis(**_: object) -> object:
        nonlocal redis_builds
        redis_builds += 1
        raise AssertionError("invalid Redis authentication reached client construction")

    monkeypatch.setattr(http_client_module.redis, "Redis", fail_redis)

    with pytest.raises(QuotaUnavailableError) as exc_info:
        http_client_module._build_redis_client()

    assert str(exc_info.value) == "source quota authentication is unavailable"
    assert (
        repr(exc_info.value)
        == "QuotaUnavailableError('source quota authentication is unavailable')"
    )
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
    rendered = f"{exc_info.value!r} {exc_info.value}"
    assert "REDIS_PASSWORD" not in rendered
    assert "redis_password" not in rendered
    assert str(http_client_module._REPOSITORY_ROOT) not in rendered
    assert redis_builds == 0


def test_private_redis_password_is_hidden_and_passed_to_bounded_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "validation-dummy-redis-password"
    original_settings = http_client_module._RedisSettings
    captured: dict[str, object] = {}
    client = object()
    monkeypatch.setenv("REDIS_PASSWORD", marker)
    settings = original_settings(_env_file=None)

    assert marker not in repr(settings)
    assert "redis_password" not in settings.model_dump()

    monkeypatch.setattr(
        http_client_module,
        "_RedisSettings",
        lambda: original_settings(_env_file=None),
    )

    def build_redis(**kwargs: object) -> object:
        captured.update(kwargs)
        return client

    monkeypatch.setattr(http_client_module.redis, "Redis", build_redis)

    assert http_client_module._build_redis_client() is client
    assert captured["password"] == marker
    assert captured["socket_connect_timeout"] == 2.0
    assert captured["socket_timeout"] == 2.0
    assert captured["retry_on_timeout"] is False


def test_private_redis_settings_requires_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    with pytest.raises(ValidationError):
        http_client_module._RedisSettings(_env_file=None)


def test_tls_context_requires_hostname_certificate_and_tls12_or_newer() -> None:
    context = build_tls_context()

    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert context.keylog_filename is None


@pytest.mark.parametrize("name", ["SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE"])
def test_tls_context_rejects_ambient_override_without_echoing_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
) -> None:
    for variable in ("SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE"):
        monkeypatch.delenv(variable, raising=False)
    poison = str(tmp_path / "validation-dummy-tls-override")
    monkeypatch.setenv(name, poison)

    with pytest.raises(ValueError, match="TLS override") as exc_info:
        build_tls_context()

    assert poison not in str(exc_info.value)
    assert not Path(poison).exists()
