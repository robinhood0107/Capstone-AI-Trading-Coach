from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, cast

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter
from tenacity.wait import wait_base

from app.data.opendart.settings import OpenDARTSettings


class OpenDARTHttpError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"OpenDART HTTP {status_code}: {message}")
        self.status_code = status_code


class OpenDARTRetryableStatus(OpenDARTHttpError):
    pass


class TokenBucket:
    def __init__(
        self,
        rate_per_second: float,
        capacity: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        # OpenDART 일일/초당 제한을 batch retry가 우회하지 않게 attempt마다 같은 bucket을 통과시킨다.
        self.rate_per_second = float(rate_per_second)
        self.capacity = float(capacity or max(1.0, rate_per_second))
        self._tokens = self.capacity
        self._clock = clock
        self._sleeper = sleeper
        self._updated_at = self._clock()

    def acquire(self) -> None:
        while True:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            self._sleeper((1.0 - self._tokens) / self.rate_per_second)

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._updated_at)
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_second)
        self._updated_at = now


class OpenDARTHttpClient:
    """OpenDART GET 전용 client로 retry, rate limit, secret masking 경계를 담당한다."""

    def __init__(
        self,
        settings: OpenDARTSettings,
        http_client: httpx.Client | None = None,
        rate_limiter: TokenBucket | None = None,
        retry_wait: wait_base | None = None,
    ) -> None:
        """운영 설정과 테스트용 transport/rate limiter를 분리해 offline 검증을 쉽게 한다."""
        self.settings = settings
        self.http_client = http_client or httpx.Client(timeout=settings.opendart_timeout_seconds)
        self.rate_limiter = rate_limiter or TokenBucket(settings.rate_limit_per_second)
        self.retry_wait = retry_wait or wait_exponential_jitter(initial=0.2, max=2.0)

    def get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        """JSON endpoint 응답이 객체가 아니면 parser 전에 실패시켜 raw shape 오류를 드러낸다."""
        data = self._get_with_retry(path, params, expect_json=True)
        if not isinstance(data, dict):
            raise OpenDARTHttpError(200, "OpenDART response was not a JSON object")
        return data

    def get_bytes(self, path: str, params: dict[str, str]) -> bytes:
        """corpCode.xml처럼 ZIP/XML byte 응답을 받는 공식 endpoint에 사용한다."""
        data = self._get_with_retry(path, params, expect_json=False)
        if not isinstance(data, bytes):
            raise OpenDARTHttpError(200, "OpenDART response was not bytes")
        return data

    def close(self) -> None:
        """장기 batch 실행 후 httpx connection pool을 명시적으로 닫는다."""
        self.http_client.close()

    def _get_with_retry(self, path: str, params: dict[str, str], *, expect_json: bool) -> dict[str, Any] | bytes:
        retrying = Retrying(
            stop=stop_after_attempt(self.settings.opendart_retry_attempts),
            wait=self.retry_wait,
            retry=retry_if_exception_type(
                (OpenDARTRetryableStatus, httpx.TimeoutException, httpx.TransportError)
            ),
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                return self._send_once(path, params, expect_json=expect_json)
        raise RuntimeError("unreachable retry state")

    def _send_once(self, path: str, params: dict[str, str], *, expect_json: bool) -> dict[str, Any] | bytes:
        self.rate_limiter.acquire()
        response = self.http_client.request("GET", self._url(path), params=self._authenticated_params(params))
        if response.status_code >= 400:
            message = _mask_text(response.text[:300], [self.settings.api_key])
            if response.status_code in {408, 429, 500, 502, 503, 504}:
                raise OpenDARTRetryableStatus(response.status_code, message)
            raise OpenDARTHttpError(response.status_code, message)
        if not expect_json:
            return response.content
        try:
            data: object = response.json()
        except ValueError as exc:
            raise OpenDARTHttpError(response.status_code, "OpenDART response was not valid JSON") from exc
        if not isinstance(data, dict):
            raise OpenDARTHttpError(response.status_code, "OpenDART response was not a JSON object")
        return cast(dict[str, Any], data)

    def _authenticated_params(self, params: dict[str, str]) -> dict[str, str]:
        # 인증키는 transport 경계에서만 붙여 사용자별 key 주입과 상위 계층의 secret 전파를 막는다.
        if "crtfc_key" in params:
            raise ValueError("crtfc_key is managed by OpenDARTHttpClient")
        return {**params, "crtfc_key": self.settings.api_key or ""}

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.settings.base_url}/{path.lstrip('/')}"


def _mask_text(text: str, secrets: list[str | None]) -> str:
    masked = text
    for secret in secrets:
        if secret:
            masked = masked.replace(secret, "***")
    return masked
