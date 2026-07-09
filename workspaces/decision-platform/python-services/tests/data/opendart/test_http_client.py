from pathlib import Path

import httpx
import pytest
from tenacity import wait_none

from app.data.opendart.http_client import OpenDARTHttpClient, OpenDARTHttpError, TokenBucket
from app.data.opendart.settings import OpenDARTSettings


def _settings(tmp_path: Path) -> OpenDARTSettings:
    return OpenDARTSettings(opendart_offline=True, opendart_data_dir=tmp_path, _env_file=None)


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
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        rate_limiter=TokenBucket(rate_per_second=1000),
        retry_wait=wait_none(),
    )

    assert client.get_json("/api/list.json", params={}) == {"status": "000", "list": []}
    assert attempts == 2


def test_get_bytes_supports_corp_code_zip(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key=masked"
        return httpx.Response(200, content=b"zip-bytes")

    settings = OpenDARTSettings(
        opendart_api_key="masked",
        opendart_data_dir=tmp_path,
        _env_file=None,
    )
    client = OpenDARTHttpClient(
        settings,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        rate_limiter=TokenBucket(rate_per_second=1000),
    )

    assert client.get_bytes("/api/corpCode.xml", params={"crtfc_key": "masked"}) == b"zip-bytes"


def test_get_json_rejects_non_object_response(tmp_path: Path) -> None:
    client = OpenDARTHttpClient(
        _settings(tmp_path),
        http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[]))),
        rate_limiter=TokenBucket(rate_per_second=1000),
    )

    with pytest.raises(OpenDARTHttpError):
        client.get_json("/api/list.json", params={})
