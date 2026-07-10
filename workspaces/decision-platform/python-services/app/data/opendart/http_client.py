from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any, cast

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter
from tenacity.wait import wait_base

from app.data.opendart._credential_transport import (
    OpenDARTCredentialError,
    _CredentialTransport,
)
from app.data.opendart.settings import OPENDART_ORIGIN, OpenDARTSettings


class OpenDARTHttpError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"OpenDART HTTP {status_code}: {message}")
        self.status_code = status_code


class OpenDARTRetryableStatus(OpenDARTHttpError):
    pass


class OpenDARTTransportError(RuntimeError):
    """request URL과 원본 transport exception을 보존하지 않는 retryable 오류다."""


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
    """고정 OpenDART origin의 GET, retry, rate limit만 제공하며 인증정보는 보관하지 않는다."""

    def __init__(
        self,
        settings: OpenDARTSettings,
        transport: httpx.BaseTransport | None = None,
        rate_limiter: TokenBucket | None = None,
        retry_wait: wait_base | None = None,
    ) -> None:
        """비밀값 없는 운영 설정과 테스트용 transport/rate limiter만 주입받는다."""
        inner_transport = transport or httpx.HTTPTransport()
        self._http = httpx.Client(
            transport=_CredentialTransport(inner_transport, enabled=not settings.offline),
            timeout=settings.opendart_timeout_seconds,
            follow_redirects=False,
        )
        self._retry_attempts = settings.opendart_retry_attempts
        self._rate_limiter = rate_limiter or TokenBucket(settings.rate_limit_per_second)
        self._retry_wait = retry_wait or wait_exponential_jitter(initial=0.2, max=2.0)

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
        self._http.close()

    def __enter__(self) -> "OpenDARTHttpClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get_with_retry(self, path: str, params: dict[str, str], *, expect_json: bool) -> dict[str, Any] | bytes:
        retrying = Retrying(
            stop=stop_after_attempt(self._retry_attempts),
            wait=self._retry_wait,
            retry=retry_if_exception_type((OpenDARTRetryableStatus, OpenDARTTransportError)),
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                return self._send_once(path, params, expect_json=expect_json)
        raise RuntimeError("unreachable retry state")

    def _send_once(self, path: str, params: dict[str, str], *, expect_json: bool) -> dict[str, Any] | bytes:
        url = self._url(path)
        safe_params = self._validated_params(params)
        self._rate_limiter.acquire()
        try:
            response = self._http.request("GET", url, params=safe_params)
        except OpenDARTCredentialError:
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            # httpx exception은 query가 포함된 Request를 보관할 수 있으므로 원본을 chain하지 않는다.
            raise OpenDARTTransportError("OpenDART transport is unavailable") from None
        if response.status_code >= 400:
            message = "upstream request failed"
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

    def _validated_params(self, params: dict[str, str]) -> dict[str, str]:
        # 상위 계층은 어떤 이름으로도 인증정보를 query에 주입할 수 없다.
        if any(_is_reserved_parameter(name) for name in params):
            raise ValueError("reserved request parameter is not allowed")
        return dict(params)

    def _url(self, path: str) -> str:
        if not re.fullmatch(r"/api/[A-Za-z][A-Za-z0-9]*\.(?:json|xml)", path):
            raise ValueError("relative OpenDART API path is required")
        return f"{OPENDART_ORIGIN}{path}"


def _is_reserved_parameter(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    return any(marker in normalized for marker in ("key", "secret", "token", "auth", "credential"))
