from __future__ import annotations

from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter
from tenacity.wait import wait_base

from app.data.kis.rate_limiter import TokenBucket
from app.data.kis.settings import KISSettings


class KISHttpError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"KIS HTTP {status_code}: {message}")
        self.status_code = status_code


class KISRetryableStatus(KISHttpError):
    pass


class KISHttpClient:
    def __init__(
        self,
        settings: KISSettings,
        http_client: httpx.Client | None = None,
        rate_limiter: TokenBucket | None = None,
        retry_wait: wait_base | None = None,
    ) -> None:
        self.settings = settings
        self.http_client = http_client or httpx.Client(timeout=settings.kis_timeout_seconds)
        self.rate_limiter = rate_limiter or TokenBucket(settings.rate_limit_per_second)
        self.retry_wait = retry_wait or wait_exponential_jitter(initial=0.2, max=2.0)

    def request(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        method = method.upper()
        if method == "GET":
            return self._request_get_with_retry(method, path, headers, params, json_body)
        return self._send_once(method, path, headers, params, json_body, retryable=False)

    def close(self) -> None:
        self.http_client.close()

    def _request_get_with_retry(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        params: dict[str, str] | None,
        json_body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        retrying = Retrying(
            stop=stop_after_attempt(self.settings.kis_retry_attempts),
            wait=self.retry_wait,
            retry=retry_if_exception_type(KISRetryableStatus),
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                return self._send_once(method, path, headers, params, json_body, retryable=True)
        raise RuntimeError("unreachable retry state")

    def _send_once(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        params: dict[str, str] | None,
        json_body: dict[str, Any] | None,
        retryable: bool,
    ) -> dict[str, Any]:
        self.rate_limiter.acquire()
        response = self.http_client.request(
            method,
            self._url(path),
            headers=headers,
            params=params,
            json=json_body,
        )
        if response.status_code >= 400:
            message = response.text[:300]
            if retryable and response.status_code in {408, 429, 500, 502, 503, 504}:
                raise KISRetryableStatus(response.status_code, message)
            raise KISHttpError(response.status_code, message)
        data = response.json()
        if not isinstance(data, dict):
            raise KISHttpError(response.status_code, "KIS response was not a JSON object")
        return data

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.settings.base_url.rstrip('/')}/{path.lstrip('/')}"
