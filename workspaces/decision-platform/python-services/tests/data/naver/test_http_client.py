from __future__ import annotations

import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from app.data._shared.redis_quota import QuotaUnavailableError
from app.data.naver import _credential_transport
from app.data.naver._credential_transport import (
    NaverCredentialError,
    _Credentials,
    _CredentialTransport,
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
    with httpx.Client(transport=transport, trust_env=False, follow_redirects=False) as client:
        with pytest.raises(NaverCredentialError, match="header"):
            client.get(
                f"{LEGACY_PROFILE.origin}{LEGACY_PROFILE.path}",
                headers={header_name: "caller-controlled"},
            )

    assert outbound == 0
    assert quota.reservations == []


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
        transport.handle_request(httpx.Request("GET", url))

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
    raw_client.close()


def test_production_client_rejects_caller_transport_and_quota_overrides() -> None:
    with pytest.raises(ValueError, match="private"):
        NaverHttpClient(
            settings=NaverSettings(_env_file=None),
            profile=LEGACY_PROFILE,
            transport=httpx.MockTransport(lambda _: httpx.Response(200)),
            quota=_RecordingQuota(),
        )


def test_tls_context_requires_hostname_certificate_and_tls12_or_newer() -> None:
    context = build_tls_context()

    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
