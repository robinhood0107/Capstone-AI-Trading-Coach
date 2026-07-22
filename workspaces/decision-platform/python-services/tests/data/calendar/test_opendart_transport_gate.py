from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from pydantic import SecretStr
from tenacity import wait_none

from app.data.opendart import _credential_transport
from app.data.opendart.http_client import (
    OpenDARTCredentialError,
    OpenDARTHttpClient,
    OpenDARTHttpError,
    TokenBucket,
)
from app.data.opendart.settings import OpenDARTSettings


def test_reservation_hook_runs_after_limiter_and_immediately_before_transport(tmp_path: Path) -> None:
    events: list[str] = []

    class Limiter:
        def acquire(self) -> None:
            events.append("limiter")

    def reserve(path: str) -> None:
        events.append(f"reservation:{path}")

    def handoff() -> None:
        events.append("handoff")

    def handler(_: httpx.Request) -> httpx.Response:
        events.append("transport")
        return httpx.Response(200, json={"status": "000"})

    client = OpenDARTHttpClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(handler),
        rate_limiter=Limiter(),  # type: ignore[arg-type]
        before_send=reserve,
        on_handoff=handoff,
    )

    assert client.get_json("/api/list.json", {}) == {"status": "000"}
    assert events == ["limiter", "reservation:/api/list.json", "handoff", "transport"]


def test_reservation_failure_is_fail_closed_with_zero_transport_attempts(tmp_path: Path) -> None:
    attempts = 0

    def reserve(_: str) -> None:
        raise RuntimeError("ambiguous database result")

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"status": "000"})

    client = OpenDARTHttpClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(1000),
        before_send=reserve,
        on_handoff=lambda: None,
    )
    with pytest.raises(RuntimeError, match="ambiguous"):
        client.get_json("/api/list.json", {})
    assert attempts == 0


def test_each_http_retry_reenters_reservation_and_handoff_hooks(tmp_path: Path) -> None:
    events: list[str] = []
    attempts = 0

    class Limiter:
        def acquire(self) -> None:
            events.append("limiter")

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        events.append("transport")
        if attempts == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"status": "000"})

    client = OpenDARTHttpClient(
        _settings(tmp_path, retry_attempts=2),
        transport=httpx.MockTransport(handler),
        rate_limiter=Limiter(),  # type: ignore[arg-type]
        retry_wait=wait_none(),
        before_send=lambda path: events.append(f"reservation:{path}"),
        on_handoff=lambda: events.append("handoff"),
    )

    assert client.get_json("/api/list.json", {}) == {"status": "000"}
    assert events == [
        "limiter",
        "reservation:/api/list.json",
        "handoff",
        "transport",
        "limiter",
        "reservation:/api/list.json",
        "handoff",
        "transport",
    ]


def test_missing_online_credential_keeps_charged_reservation_but_records_no_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    attempts = 0

    def unavailable() -> SecretStr:
        raise OpenDARTCredentialError("authentication unavailable")

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"status": "000"})

    monkeypatch.setattr(_credential_transport, "_read_credential", unavailable)
    client = OpenDARTHttpClient._for_online_test(
        OpenDARTSettings(
            opendart_offline=False,
            opendart_data_dir=tmp_path,
            opendart_timeout_seconds=1.0,
            opendart_retry_attempts=1,
            opendart_rate_limit_per_second=1,
        ),
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(1000),
        before_send=lambda path: events.append(f"reservation:{path}"),
        on_handoff=lambda: events.append("handoff"),
    )

    with pytest.raises(OpenDARTCredentialError):
        client.get_json("/api/list.json", {})

    assert events == ["reservation:/api/list.json"]
    assert attempts == 0


def test_http_429_is_non_retryable(tmp_path: Path) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(429)

    client = OpenDARTHttpClient(
        _settings(tmp_path, retry_attempts=3),
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(1000),
        retry_wait=wait_none(),
    )
    with pytest.raises(OpenDARTHttpError) as exc_info:
        client.get_json("/api/list.json", {})
    assert exc_info.value.status_code == 429
    assert attempts == 1


def test_http_408_is_non_retryable(tmp_path: Path) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(408)

    client = OpenDARTHttpClient(
        _settings(tmp_path, retry_attempts=3),
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(1000),
        retry_wait=wait_none(),
    )
    with pytest.raises(OpenDARTHttpError) as exc_info:
        client.get_json("/api/list.json", {})
    assert exc_info.value.status_code == 408
    assert attempts == 1


def test_online_constructor_rejects_injected_transport_and_rate_limiter(tmp_path: Path) -> None:
    online = OpenDARTSettings(
        opendart_offline=False,
        opendart_data_dir=tmp_path,
        opendart_timeout_seconds=1.0,
        opendart_retry_attempts=1,
        opendart_rate_limit_per_second=1,
    )
    with pytest.raises(ValueError, match="online.*injection"):
        OpenDARTHttpClient(
            online,
            transport=httpx.MockTransport(lambda _: httpx.Response(200)),
            rate_limiter=TokenBucket(1000),
        )


def test_online_constructor_requires_both_reservation_hooks(tmp_path: Path) -> None:
    online = OpenDARTSettings(
        opendart_offline=False,
        opendart_data_dir=tmp_path,
        opendart_timeout_seconds=1.0,
        opendart_retry_attempts=1,
        opendart_rate_limit_per_second=1,
    )

    with pytest.raises(ValueError, match="reservation and handoff hooks"):
        OpenDARTHttpClient(online, on_handoff=lambda: None)
    with pytest.raises(ValueError, match="reservation and handoff hooks"):
        OpenDARTHttpClient(online, before_send=lambda _: None)


def test_online_factory_rejects_offline_settings(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="OPENDART_OFFLINE=false"):
        OpenDARTHttpClient.for_online_collector(
            _settings(tmp_path),
            before_send=lambda _: None,
            on_handoff=lambda: None,
        )


def test_online_factory_ignores_ambient_network_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    netrc = tmp_path / ".netrc"
    netrc.write_text("machine proxy.invalid login fixture password fixture\n", encoding="utf-8")
    for name, value in {
        "HTTP_PROXY": "http://proxy.invalid:8080",
        "HTTPS_PROXY": "http://proxy.invalid:8080",
        "ALL_PROXY": "socks5://proxy.invalid:1080",
        "SSL_CERT_FILE": str(tmp_path / "untrusted.pem"),
        "NETRC": str(netrc),
    }.items():
        monkeypatch.setenv(name, value)

    captured: dict[str, object] = {}
    inner_transport = object()

    def transport_factory(**kwargs: object) -> object:
        captured["transport_kwargs"] = kwargs
        return inner_transport

    class ClientProbe:
        def __init__(self, **kwargs: object) -> None:
            captured["client_kwargs"] = kwargs

        def close(self) -> None:
            pass

    monkeypatch.setattr(httpx, "HTTPTransport", transport_factory)
    monkeypatch.setattr(httpx, "Client", ClientProbe)
    settings = OpenDARTSettings(
        opendart_offline=False,
        opendart_data_dir=tmp_path,
        opendart_timeout_seconds=1.0,
        opendart_retry_attempts=1,
        opendart_rate_limit_per_second=1,
    )

    OpenDARTHttpClient.for_online_collector(
        settings,
        before_send=lambda _: None,
        on_handoff=lambda: None,
    )

    assert captured["transport_kwargs"] == {"verify": True, "retries": 0}
    client_kwargs = cast(dict[str, object], captured["client_kwargs"])
    assert client_kwargs["follow_redirects"] is False
    assert client_kwargs["trust_env"] is False
    wrapped = cast(_credential_transport._CredentialTransport, client_kwargs["transport"])
    assert wrapped._inner is inner_transport


@pytest.mark.parametrize(
    "override",
    [
        {"proxy": "http://proxy.invalid:8080"},
        {"verify": False},
        {"ca_bundle": "/tmp/untrusted.pem"},
    ],
)
def test_online_constructor_rejects_caller_network_overrides(
    tmp_path: Path,
    override: dict[str, object],
) -> None:
    online = OpenDARTSettings(
        opendart_offline=False,
        opendart_data_dir=tmp_path,
        opendart_timeout_seconds=1.0,
        opendart_retry_attempts=1,
        opendart_rate_limit_per_second=1,
    )
    constructor = cast(Any, OpenDARTHttpClient)

    with pytest.raises(TypeError):
        constructor(
            online,
            before_send=lambda _: None,
            on_handoff=lambda: None,
            **override,
        )


def _settings(tmp_path: Path, *, retry_attempts: int = 3) -> OpenDARTSettings:
    return OpenDARTSettings(
        opendart_offline=True,
        opendart_data_dir=tmp_path,
        opendart_timeout_seconds=1.0,
        opendart_retry_attempts=retry_attempts,
        opendart_rate_limit_per_second=1000,
    )
