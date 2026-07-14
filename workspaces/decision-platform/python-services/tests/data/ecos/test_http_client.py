from __future__ import annotations

import ssl
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.data._shared.redis_quota import QuotaUnavailableError
from app.data.ecos import _credential_transport
from app.data.ecos._credential_transport import ECOSCredentialError, _CredentialTransport
from app.data.ecos.http_client import ECOSHttpClient, ECOSHttpError, _build_tls_context
from app.data.ecos.policy import build_keyless_service_path
from app.data.ecos.settings import ECOSSettings


class _RecordingQuota:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def reserve(self, *, attempt_id: str) -> None:
        assert attempt_id
        self.events.append("quota")


def _path() -> str:
    return build_keyless_service_path(
        service="StatisticSearch",
        start_index=1,
        end_index=200,
        arguments=("722Y001", "D", "20260701", "20260714", "0101000"),
    )


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
    with httpx.Client(transport=transport, follow_redirects=False, trust_env=False) as client:
        response = client.get(f"https://ecos.bok.or.kr{_path()}")

    assert events == ["quota", "credential", "send"]
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
    request = httpx.Request("GET", f"https://ecos.bok.or.kr{_path()}")

    with pytest.raises(ECOSCredentialError, match="transport_unavailable") as exc_info:
        transport.handle_request(request)

    assert marker not in request.url.path
    assert marker not in captured[0].url.path
    assert marker not in f"{exc_info.value!r} {exc_info.value}"
    assert exc_info.value.__cause__ is None


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
        transport.handle_request(httpx.Request("GET", f"https://attacker.invalid{_path()}"))

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
        transport.handle_request(httpx.Request("GET", f"https://ecos.bok.or.kr{_path()}"))

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
        transport.handle_request(httpx.Request("GET", f"https://ecos.bok.or.kr{_path()}"))

    assert credential_reads == 0
    assert outbound == 0


def test_tls_context_requires_tls12_hostname_and_cert_validation() -> None:
    context = _build_tls_context()

    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_production_client_rejects_caller_transport_and_quota_overrides(tmp_path: Path) -> None:
    settings = ECOSSettings(_env_file=None)

    with pytest.raises(ValueError, match="private"):
        ECOSHttpClient(
            settings,
            transport=httpx.MockTransport(lambda _: httpx.Response(200)),
            quota=_RecordingQuota([]),
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
        with pytest.raises(ECOSHttpError, match="redirect_rejected"):
            client.get_json(_path())
    finally:
        client.close()
