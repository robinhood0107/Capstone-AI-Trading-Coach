import logging
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from tenacity import wait_none

from app.data.kis import _credential_transport
from app.data.kis._credential_transport import KISCredentialError, _CredentialSettings, _Credentials
from app.data.kis.http_client import KISHttpClient, KISHttpError, KISTransportError
from app.data.kis.market_client import CURRENT_PRICE_PATH
from app.data.kis.rate_limiter import TokenBucket
from app.data.kis.settings import KISSettings


def _settings(tmp_path: Path, *, offline: bool = True) -> KISSettings:
    return KISSettings(kis_offline=offline, kis_data_dir=tmp_path, _env_file=None)


def _stub_credentials(monkeypatch: pytest.MonkeyPatch, app_key: str, app_secret: str) -> None:
    monkeypatch.setattr(
        _credential_transport,
        "_read_credentials",
        lambda _: _Credentials(app_key=SecretStr(app_key), app_secret=SecretStr(app_secret)),
    )


def test_private_credential_settings_hide_values_from_repr_and_serialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "validation-dummy-secret"
    monkeypatch.setenv("KIS_MOCK_APP_KEY", marker)
    monkeypatch.setenv("KIS_MOCK_APP_SECRET", marker)

    settings = _CredentialSettings(_env_file=None)  # type: ignore[call-arg]

    assert marker not in repr(settings)
    assert marker not in settings.model_dump_json()
    assert settings.model_dump() == {}


def test_get_market_data_retries_retryable_status(tmp_path: Path) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(500, json={"rt_cd": "1"})
        return httpx.Response(200, json={"rt_cd": "0", "output": {"ok": "yes"}})

    client = KISHttpClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(rate_per_second=1000),
        retry_wait=wait_none(),
    )

    assert client.request("GET", CURRENT_PRICE_PATH, tr_id="FHKST01010100", params={})["output"] == {"ok": "yes"}
    assert attempts == 2


def test_get_market_data_retries_timeout_once_then_succeeds(tmp_path: Path) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectTimeout("network stalled")
        return httpx.Response(200, json={"rt_cd": "0", "output": {"ok": "yes"}})

    client = KISHttpClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(rate_per_second=1000),
        retry_wait=wait_none(),
    )

    assert client.request("GET", CURRENT_PRICE_PATH, tr_id="FHKST01010100", params={})["output"] == {"ok": "yes"}
    assert attempts == 2


def test_credentials_and_token_exist_only_at_fixed_origin_transport_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_key = "validation-dummy-key"
    app_secret = "validation-dummy-secret"
    token = "validation-dummy-token"
    captured: list[httpx.Request] = []
    _stub_credentials(monkeypatch, app_key, app_secret)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.scheme == "https"
        assert request.url.host == "openapivts.koreainvestment.com"
        assert request.headers["appkey"] == app_key
        assert request.headers["appsecret"] == app_secret
        assert request.headers["authorization"] == f"Bearer {token}"
        captured.append(request)
        return httpx.Response(
            200,
            headers={"x-provider-debug": app_secret},
            json={"rt_cd": "0", "echo": token, "appsecret": app_secret},
        )

    client = KISHttpClient(
        _settings(tmp_path, offline=False),
        token_provider=lambda: token,
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(rate_per_second=1000),
    )

    assert client.request("GET", CURRENT_PRICE_PATH, tr_id="FHKST01010100", params={}) == {
        "rt_cd": "0",
        "echo": "[redacted]",
    }
    assert all(secret not in repr(vars(client)) for secret in (app_key, app_secret, token))
    assert all(name not in captured[0].headers for name in ("appkey", "appsecret", "authorization"))


@pytest.mark.parametrize(
    "unsafe_path",
    ["https://attacker.invalid/collect", "//attacker.invalid/collect", "/uapi/test?redirect=x", "/uapi/test#x"],
)
def test_http_client_rejects_paths_outside_fixed_endpoint_allowlist(tmp_path: Path, unsafe_path: str) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"rt_cd": "0"})

    client = KISHttpClient(_settings(tmp_path), transport=httpx.MockTransport(handler))

    with pytest.raises(ValueError, match="approved KIS endpoint"):
        client.request("GET", unsafe_path, tr_id="FHKST01010100", params={})

    assert attempts == 0


def test_missing_credentials_fail_closed_before_outbound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def unavailable(_: str) -> _Credentials:
        raise KISCredentialError("KIS authentication is unavailable")

    monkeypatch.setattr(_credential_transport, "_read_credentials", unavailable)

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"rt_cd": "0"})

    client = KISHttpClient(
        _settings(tmp_path, offline=False),
        token_provider=lambda: "dummy-token",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(KISCredentialError) as exc_info:
        client.request("GET", CURRENT_PRICE_PATH, tr_id="FHKST01010100", params={})

    assert "KIS_MOCK_APP_KEY" not in str(exc_info.value)
    assert attempts == 0


def test_transport_error_drops_request_and_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "validation-dummy-secret"
    _stub_credentials(monkeypatch, "validation-dummy-key", marker)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed {request.url}", request=request)

    client = KISHttpClient(
        _settings(tmp_path, offline=False),
        token_provider=lambda: "validation-dummy-token",
        transport=httpx.MockTransport(handler),
        retry_wait=wait_none(),
    )

    with pytest.raises(KISTransportError) as exc_info:
        client.request("GET", CURRENT_PRICE_PATH, tr_id="FHKST01010100", params={"FID_INPUT_ISCD": "005930"})

    assert marker not in f"{exc_info.value!r} {exc_info.value}"
    assert "005930" not in f"{exc_info.value!r} {exc_info.value}"
    assert exc_info.value.__cause__ is None


def test_transport_boundary_does_not_log_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "validation-dummy-secret"
    _stub_credentials(monkeypatch, "validation-dummy-key", marker)
    caplog.set_level(logging.DEBUG)
    client = KISHttpClient(
        _settings(tmp_path, offline=False),
        token_provider=lambda: "validation-dummy-token",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"rt_cd": "0"})),
    )

    assert client.request("GET", CURRENT_PRICE_PATH, tr_id="FHKST01010100", params={}) == {"rt_cd": "0"}
    assert marker not in caplog.text
    assert "appsecret" not in caplog.text.lower()


def test_http_client_rejects_non_get_method(tmp_path: Path) -> None:
    client = KISHttpClient(_settings(tmp_path), transport=httpx.MockTransport(lambda _: httpx.Response(200)))

    with pytest.raises(KISHttpError, match="read-only GET"):
        client.request("POST", CURRENT_PRICE_PATH, tr_id="FHKST01010100", params={})
