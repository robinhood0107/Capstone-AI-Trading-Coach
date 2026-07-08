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
            # S1.1에서 retry가 허용되는 대상은 멱등 read-only 조회뿐이다.
            # POST는 OAuth 같은 발급성 요청일 수 있어 timeout이 나도 중복 시도하지 않는다.
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
        # KIS 장애·점검 시간에는 짧은 지수 backoff가 개발/배치 실패를 줄인다.
        # timeout/transport 오류도 GET 조회에서는 같은 정책으로 재시도해 일시 네트워크 흔들림을 흡수한다.
        retrying = Retrying(
            stop=stop_after_attempt(self.settings.kis_retry_attempts),
            wait=self.retry_wait,
            retry=retry_if_exception_type((KISRetryableStatus, httpx.TimeoutException, httpx.TransportError)),
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
        # limiter는 attempt마다 적용한다. retry burst가 KIS 초당 제한을 우회하지 않게 하려는 선택이다.
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
                # 4xx 중 인증/파라미터 오류는 즉시 실패시키고, 일시성으로 볼 수 있는 상태만 재시도한다.
                raise KISRetryableStatus(response.status_code, message)
            raise KISHttpError(response.status_code, message)
        data = response.json()
        if not isinstance(data, dict):
            # parser는 KIS envelope dict를 전제로 하므로 여기서 비정상 응답 모양을 일찍 끊는다.
            raise KISHttpError(response.status_code, "KIS response was not a JSON object")
        return data

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.settings.base_url.rstrip('/')}/{path.lstrip('/')}"
