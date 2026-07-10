import logging
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.data.kis import _credential_transport
from app.data.kis._credential_transport import (
    KISCredentialError,
    KISResponseTooLargeError,
    _CredentialSettings,
    _CredentialTransport,
    _Credentials,
)
from app.data.kis.http_client import (
    KISHttpClient,
    KISHttpError,
    KISProviderRateLimitError,
)
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


def _private_transport_client(
    tmp_path: Path,
    *,
    handler: httpx.MockTransport,
    token_provider: object,
    rate_limiter: object,
    max_json_depth: int = 64,
) -> httpx.Client:
    transport = _CredentialTransport(
        handler,
        settings=_settings(tmp_path, offline=False),
        token_provider=token_provider,  # type: ignore[arg-type]
        rate_limiter=rate_limiter,  # type: ignore[arg-type]
        max_json_depth=max_json_depth,
    )
    return httpx.Client(transport=transport, follow_redirects=False, trust_env=False)


def _private_transport_get(client: httpx.Client) -> httpx.Response:
    return client.get(
        "https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/quotations/inquire-price",
        headers={_credential_transport._INTERNAL_TR_ID_HEADER: "FHKST01010100"},
    )


def _assert_traceback_locals_do_not_contain(error: BaseException, marker: str) -> None:
    traceback = error.__traceback__
    while traceback is not None:
        if "/app/data/kis/" in traceback.tb_frame.f_code.co_filename:
            for value in traceback.tb_frame.f_locals.values():
                assert marker not in repr(value)
        traceback = traceback.tb_next


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
    quota_reservations = 0

    class RecordingLimiter:
        def acquire(self) -> None:
            nonlocal quota_reservations
            quota_reservations += 1

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(500, json={"rt_cd": "1"})
        return httpx.Response(200, json={"rt_cd": "0", "output": {"ok": "yes"}})

    client = KISHttpClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(handler),
        rate_limiter=RecordingLimiter(),
        retry_delay=lambda _: 0.0,
    )

    assert client.request("GET", CURRENT_PRICE_PATH, tr_id="FHKST01010100", params={})["output"] == {"ok": "yes"}
    assert attempts == 2
    assert quota_reservations == 2


def test_get_market_data_retries_timeout_once_then_succeeds(tmp_path: Path) -> None:
    attempts = 0
    quota_reservations = 0

    class RecordingLimiter:
        def acquire(self) -> None:
            nonlocal quota_reservations
            quota_reservations += 1

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectTimeout("network stalled")
        return httpx.Response(200, json={"rt_cd": "0", "output": {"ok": "yes"}})

    client = KISHttpClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(handler),
        rate_limiter=RecordingLimiter(),
        retry_delay=lambda _: 0.0,
    )

    assert client.request("GET", CURRENT_PRICE_PATH, tr_id="FHKST01010100", params={})["output"] == {"ok": "yes"}
    assert attempts == 2
    assert quota_reservations == 2


def test_online_http_client_rejects_caller_transport_and_limiter_overrides(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="private dependencies"):
        KISHttpClient(
            _settings(tmp_path, offline=False),
            token_provider=lambda: "validation-dummy-token",
            transport=httpx.MockTransport(lambda _: httpx.Response(200)),
            rate_limiter=TokenBucket(rate_per_second=1000),
        )


def test_online_http_client_rejects_caller_token_provider_override(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="private dependencies"):
        KISHttpClient(
            _settings(tmp_path, offline=False),
            token_provider=lambda: "validation-dummy-token",
        )


def test_credentials_and_token_exist_only_at_fixed_origin_transport_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_key = "validation-dummy-key"
    app_secret = "validation-dummy-secret"
    token = "validation-dummy-token"
    captured: list[httpx.Request] = []
    events: list[str] = []

    def read_credentials(_: str) -> _Credentials:
        events.append("credentials")
        return _Credentials(app_key=SecretStr(app_key), app_secret=SecretStr(app_secret))

    monkeypatch.setattr(_credential_transport, "_read_credentials", read_credentials)

    class RecordingLimiter:
        def acquire(self) -> None:
            events.append("quota")

    def token_provider() -> str:
        events.append("token")
        return token

    def handler(request: httpx.Request) -> httpx.Response:
        events.append("send")
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

    client = _private_transport_client(
        tmp_path,
        token_provider=token_provider,
        handler=httpx.MockTransport(handler),
        rate_limiter=RecordingLimiter(),
    )

    assert _private_transport_get(client).json() == {
        "rt_cd": "0",
        "echo": "[redacted]",
    }
    assert all(secret not in repr(vars(client)) for secret in (app_key, app_secret, token))
    assert all(name not in captured[0].headers for name in ("appkey", "appsecret", "authorization"))
    assert events == ["token", "quota", "credentials", "send"]


def test_market_quota_failure_does_not_read_static_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def read_credentials(_: str) -> _Credentials:
        events.append("credentials")
        return _Credentials(
            app_key=SecretStr("validation-dummy-key"),
            app_secret=SecretStr("validation-dummy-secret"),
        )

    class RejectingLimiter:
        def acquire(self) -> None:
            events.append("quota")
            raise RuntimeError("quota unavailable")

    monkeypatch.setattr(_credential_transport, "_read_credentials", read_credentials)
    client = _private_transport_client(
        tmp_path,
        handler=httpx.MockTransport(lambda _: httpx.Response(200)),
        token_provider=lambda: events.append("token") or "validation-dummy-token",
        rate_limiter=RejectingLimiter(),
    )

    with pytest.raises(RuntimeError, match="quota unavailable"):
        _private_transport_get(client)

    assert events == ["token", "quota"]


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

    client = _private_transport_client(
        tmp_path,
        token_provider=lambda: "dummy-token",
        handler=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(rate_per_second=1000),
    )

    with pytest.raises(KISCredentialError) as exc_info:
        _private_transport_get(client)

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

    client = _private_transport_client(
        tmp_path,
        token_provider=lambda: "validation-dummy-token",
        handler=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(rate_per_second=1000),
    )

    with pytest.raises(httpx.ConnectError) as exc_info:
        _private_transport_get(client)

    assert marker not in f"{exc_info.value!r} {exc_info.value}"
    assert all(name not in exc_info.value.request.headers for name in ("appkey", "appsecret", "authorization"))
    _assert_traceback_locals_do_not_contain(exc_info.value, marker)


def test_transport_boundary_does_not_log_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    marker = "validation-dummy-secret"
    _stub_credentials(monkeypatch, "validation-dummy-key", marker)
    caplog.set_level(logging.DEBUG)
    client = _private_transport_client(
        tmp_path,
        token_provider=lambda: "validation-dummy-token",
        handler=httpx.MockTransport(lambda _: httpx.Response(200, json={"rt_cd": "0"})),
        rate_limiter=TokenBucket(rate_per_second=1000),
    )

    assert _private_transport_get(client).json() == {"rt_cd": "0"}
    assert marker not in caplog.text
    assert "appsecret" not in caplog.text.lower()


def test_deep_response_failure_drops_credential_traceback_locals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "validation-dummy-secret"
    _stub_credentials(monkeypatch, "validation-dummy-key", marker)
    client = _private_transport_client(
        tmp_path,
        token_provider=lambda: "validation-dummy-token",
        handler=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"outer": {"inner": {"echo": marker}}})
        ),
        rate_limiter=TokenBucket(rate_per_second=1000),
        max_json_depth=1,
    )

    with pytest.raises(KISResponseTooLargeError) as exc_info:
        _private_transport_get(client)

    _assert_traceback_locals_do_not_contain(exc_info.value, marker)


def test_http_client_rejects_non_get_method(tmp_path: Path) -> None:
    client = KISHttpClient(_settings(tmp_path), transport=httpx.MockTransport(lambda _: httpx.Response(200)))

    with pytest.raises(KISHttpError, match="read-only GET"):
        client.request("POST", CURRENT_PRICE_PATH, tr_id="FHKST01010100", params={})


@pytest.mark.parametrize(
    "routing_code",
    ["EGW00001", "EGW00002", "EGW00202", "EGW00203", "EGW00300"],
)
def test_distribution_routing_failure_is_recalled_once_in_next_quota_slot(
    tmp_path: Path,
    routing_code: str,
) -> None:
    attempts = 0
    quota_reservations = 0
    backoff_calls: list[int] = []

    class RecordingLimiter:
        def acquire(self) -> None:
            nonlocal quota_reservations
            quota_reservations += 1

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(200, json={"rt_cd": "1", "msg_cd": routing_code, "msg1": "routing"})
        return httpx.Response(200, json={"rt_cd": "0", "output": {"ok": "yes"}})

    client = KISHttpClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(handler),
        rate_limiter=RecordingLimiter(),
        retry_delay=lambda attempt: backoff_calls.append(attempt) or 0.0,
    )

    result = client.request("GET", CURRENT_PRICE_PATH, tr_id="FHKST01010100", params={})

    assert result["output"] == {"ok": "yes"}
    assert attempts == 2
    assert quota_reservations == 2
    assert backoff_calls == []


def test_second_distribution_failure_stops_after_one_immediate_retry(tmp_path: Path) -> None:
    attempts = 0
    quota_reservations = 0

    class RecordingLimiter:
        def acquire(self) -> None:
            nonlocal quota_reservations
            quota_reservations += 1

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"rt_cd": "1", "msg_cd": "EGW00202"})

    client = KISHttpClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(handler),
        rate_limiter=RecordingLimiter(),
        retry_delay=lambda _: 0.0,
    )

    with pytest.raises(KISHttpError):
        client.request("GET", CURRENT_PRICE_PATH, tr_id="FHKST01010100", params={})

    assert attempts == 2
    assert quota_reservations == 2


def test_provider_rate_limit_response_fails_without_retry_storm(tmp_path: Path) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            json={"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "rate exceeded"},
        )

    client = KISHttpClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(rate_per_second=1000),
        retry_delay=lambda _: 0.0,
    )

    with pytest.raises(KISProviderRateLimitError):
        client.request("GET", CURRENT_PRICE_PATH, tr_id="FHKST01010100", params={})

    assert attempts == 1


def test_http_429_fails_without_retry_storm(tmp_path: Path) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429, json={"message": "rate exceeded"})

    client = KISHttpClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(rate_per_second=1000),
        retry_delay=lambda _: 0.0,
    )

    with pytest.raises(KISProviderRateLimitError):
        client.request("GET", CURRENT_PRICE_PATH, tr_id="FHKST01010100", params={})

    assert attempts == 1


def test_close_releases_token_and_redis_even_if_market_close_fails(tmp_path: Path) -> None:
    events: list[str] = []

    class FailingMarket:
        def close(self) -> None:
            events.append("market")
            raise RuntimeError("market close failed")

    class ClosingResource:
        def __init__(self, name: str) -> None:
            self.name = name

        def close(self) -> None:
            events.append(self.name)

    client = KISHttpClient(_settings(tmp_path))
    client._http = FailingMarket()  # type: ignore[assignment]
    client._token_issuer = ClosingResource("issuer")  # type: ignore[assignment]
    client._redis_client = ClosingResource("redis")

    with pytest.raises(RuntimeError, match="market close failed"):
        client.close()

    assert events == ["market", "issuer", "redis"]
