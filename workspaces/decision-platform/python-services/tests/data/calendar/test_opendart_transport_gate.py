from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from tenacity import wait_none

from app.data.opendart.http_client import (
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


def _settings(tmp_path: Path, *, retry_attempts: int = 3) -> OpenDARTSettings:
    return OpenDARTSettings(
        opendart_offline=True,
        opendart_data_dir=tmp_path,
        opendart_timeout_seconds=1.0,
        opendart_retry_attempts=retry_attempts,
        opendart_rate_limit_per_second=1000,
    )
