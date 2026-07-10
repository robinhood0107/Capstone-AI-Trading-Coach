import logging
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from tenacity import wait_none

from app.data.opendart import _credential_transport
from app.data.opendart.http_client import (
    OpenDARTCredentialError,
    OpenDARTHttpClient,
    OpenDARTHttpError,
    OpenDARTTransportError,
    TokenBucket,
)
from app.data.opendart.settings import OpenDARTSettings


def _settings(
    tmp_path: Path,
    *,
    offline: bool = True,
    retry_attempts: int = 3,
) -> OpenDARTSettings:
    return OpenDARTSettings(
        opendart_offline=offline,
        opendart_retry_attempts=retry_attempts,
        opendart_data_dir=tmp_path,
        _env_file=None,
    )


def _stub_credential(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(_credential_transport, "_read_credential", lambda: SecretStr(value))


def test_private_credential_settings_hide_value_from_repr_and_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "fixture-auth-value"
    monkeypatch.setenv("OPENDART_API_KEY", marker)

    settings = _credential_transport._CredentialSettings(_env_file=None)  # type: ignore[call-arg]

    assert marker not in repr(settings)
    assert marker not in settings.model_dump_json()
    assert settings.model_dump() == {}


def test_get_json_retries_retryable_status(tmp_path: Path) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(500, json={"status": "900", "message": "temporary"})
        return httpx.Response(200, json={"status": "000", "list": []})

    client = OpenDARTHttpClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(rate_per_second=1000),
        retry_wait=wait_none(),
    )

    assert client.get_json("/api/list.json", params={}) == {"status": "000", "list": []}
    assert attempts == 2


def test_get_bytes_uses_env_credential_only_at_transport_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "fixture-auth-value"
    captured_requests: list[httpx.Request] = []
    _stub_credential(monkeypatch, marker)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "opendart.fss.or.kr"
        assert request.url.scheme == "https"
        assert request.url.params["crtfc_key"] == marker
        captured_requests.append(request)
        return httpx.Response(200, content=b"zip-bytes")

    client = OpenDARTHttpClient(
        _settings(tmp_path, offline=False),
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(rate_per_second=1000),
    )

    assert client.get_bytes("/api/corpCode.xml", params={}) == b"zip-bytes"
    assert "crtfc_key" not in captured_requests[0].url.params
    assert marker not in repr(vars(client))


def test_get_json_scrubs_credential_echo_and_does_not_mutate_caller_params(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "fixture-auth-value"
    params = {"corp_code": "00126380"}
    _stub_credential(monkeypatch, marker)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["crtfc_key"] == marker
        assert request.url.params["corp_code"] == "00126380"
        return httpx.Response(
            200,
            headers={"x-upstream-debug": marker},
            json={
                "status": "000",
                "echo": marker,
                "crtfc_key": marker,
                "list": [{"account_nm": "자본총계"}],
            },
        )

    client = OpenDARTHttpClient(
        _settings(tmp_path, offline=False),
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(rate_per_second=1000),
    )

    assert client.get_json("/api/company.json", params=params) == {
        "status": "000",
        "echo": "[redacted]",
        "list": [{"account_nm": "자본총계"}],
    }
    assert params == {"corp_code": "00126380"}


def test_transport_scrubs_response_extensions_before_returning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "fixture-auth-value"
    _stub_credential(monkeypatch, marker)

    inner = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={"status": "000"},
            extensions={"http_version": b"HTTP/1.1", "debug": marker.encode()},
        )
    )
    transport = _credential_transport._CredentialTransport(inner, enabled=True)
    response = transport.handle_request(httpx.Request("GET", "https://opendart.fss.or.kr/api/company.json"))

    assert response.extensions == {"http_version": b"HTTP/1.1"}
    assert marker not in repr(response.extensions)


def test_transport_boundary_does_not_emit_credential_to_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "fixture-auth-value"
    _stub_credential(monkeypatch, marker)
    caplog.set_level(logging.DEBUG)

    client = OpenDARTHttpClient(
        _settings(tmp_path, offline=False),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"status": "000"})),
        rate_limiter=TokenBucket(rate_per_second=1000),
    )

    assert client.get_json("/api/company.json", params={}) == {"status": "000"}
    assert marker not in caplog.text
    assert "crtfc_key" not in caplog.text


def test_offline_mode_never_loads_or_attaches_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called() -> SecretStr:
        raise AssertionError("offline transport must not load authentication material")

    monkeypatch.setattr(_credential_transport, "_read_credential", fail_if_called)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "crtfc_key" not in request.url.params
        return httpx.Response(200, json={"status": "000"})

    client = OpenDARTHttpClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(rate_per_second=1000),
    )

    assert client.get_json("/api/company.json", params={}) == {"status": "000"}


def test_http_client_rejects_caller_supplied_authentication_key(tmp_path: Path) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"status": "000"})

    client = OpenDARTHttpClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(rate_per_second=1000),
    )

    with pytest.raises(ValueError, match="reserved request parameter") as exc_info:
        client.get_json("/api/company.json", params={"crtfc_key": "caller-value"})

    assert "caller-value" not in str(exc_info.value)
    assert "crtfc_key" not in str(exc_info.value)
    assert attempts == 0


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "https://attacker.invalid/api/company.json",
        "//attacker.invalid/api/company.json",
        "/api/company.json?redirect=https://attacker.invalid",
        "/api/company.json#fragment",
    ],
)
def test_http_client_rejects_any_path_that_can_escape_fixed_origin(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"status": "000"})

    client = OpenDARTHttpClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(rate_per_second=1000),
    )

    with pytest.raises(ValueError, match="relative OpenDART API path"):
        client.get_json(unsafe_path, params={})

    assert attempts == 0


def test_transport_error_drops_request_and_credential_from_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "fixture-auth-value"
    attempts = 0
    captured_requests: list[httpx.Request] = []
    _stub_credential(monkeypatch, marker)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        captured_requests.append(request)
        raise httpx.ConnectError(f"failed request: {request.url}", request=request)

    client = OpenDARTHttpClient(
        _settings(tmp_path, offline=False),
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(rate_per_second=1000),
        retry_wait=wait_none(),
    )

    with pytest.raises(OpenDARTTransportError) as exc_info:
        client.get_json("/api/company.json", params={"corp_code": "00126380"})

    error_text = f"{exc_info.value!r} {exc_info.value}"
    assert marker not in error_text
    assert "crtfc_key" not in error_text
    assert "00126380" not in error_text
    assert exc_info.value.__cause__ is None
    assert all("crtfc_key" not in request.url.params for request in captured_requests)
    assert attempts == 3


def test_missing_credential_fails_closed_before_outbound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def unavailable() -> SecretStr:
        raise OpenDARTCredentialError("OpenDART authentication is unavailable")

    monkeypatch.setattr(_credential_transport, "_read_credential", unavailable)

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"status": "000"})

    client = OpenDARTHttpClient(
        _settings(tmp_path, offline=False),
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(rate_per_second=1000),
    )

    with pytest.raises(OpenDARTCredentialError) as exc_info:
        client.get_json("/api/company.json", params={})

    assert "OPENDART_API_KEY" not in str(exc_info.value)
    assert attempts == 0


def test_get_json_rejects_non_object_response(tmp_path: Path) -> None:
    client = OpenDARTHttpClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[])),
        rate_limiter=TokenBucket(rate_per_second=1000),
    )

    with pytest.raises(OpenDARTHttpError):
        client.get_json("/api/list.json", params={})
