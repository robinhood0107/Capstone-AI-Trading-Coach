from pathlib import Path

import httpx
import pytest
from tenacity import wait_none

from app.data.kis.http_client import KISHttpClient, KISHttpError
from app.data.kis.rate_limiter import TokenBucket
from app.data.kis.settings import KISSettings


def _settings(tmp_path: Path) -> KISSettings:
    return KISSettings(kis_offline=True, kis_data_dir=tmp_path)


def test_get_market_data_retries_retryable_status(tmp_path: Path) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(500, json={"rt_cd": "1"})
        return httpx.Response(200, json={"rt_cd": "0", "output": {"ok": "yes"}})

    client = KISHttpClient(
        _settings(tmp_path),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        rate_limiter=TokenBucket(rate_per_second=1000),
        retry_wait=wait_none(),
    )

    assert client.request("GET", "/uapi/test", headers={}, params={})["output"] == {"ok": "yes"}
    assert attempts == 2


def test_post_is_not_retried_even_when_server_fails(tmp_path: Path) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500, json={"rt_cd": "1"})

    client = KISHttpClient(
        _settings(tmp_path),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        rate_limiter=TokenBucket(rate_per_second=1000),
        retry_wait=wait_none(),
    )

    with pytest.raises(KISHttpError):
        client.request("POST", "/oauth2/tokenP", headers={}, json_body={"grant_type": "client_credentials"})
    assert attempts == 1
