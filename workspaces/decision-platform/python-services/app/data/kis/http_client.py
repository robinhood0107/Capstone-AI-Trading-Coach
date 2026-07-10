from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, cast

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter
from tenacity.wait import wait_base

from app.data.kis._credential_transport import KISCredentialError, _CredentialTransport
from app.data.kis.rate_limiter import TokenBucket
from app.data.kis.settings import KISSettings

CURRENT_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
DAILY_ITEMCHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
HOLIDAY_PATH = "/uapi/domestic-stock/v1/quotations/chk-holiday"

_APPROVED_ENDPOINTS = {
    (CURRENT_PRICE_PATH, "FHKST01010100"),
    (DAILY_ITEMCHART_PATH, "FHKST03010100"),
    (HOLIDAY_PATH, "CTCA0903R"),
}
_INTERNAL_TR_ID_HEADER = "x-kis-internal-tr-id"


class KISHttpError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"KIS HTTP {status_code}: {message}")
        self.status_code = status_code


class KISRetryableStatus(KISHttpError):
    pass


class KISTransportError(RuntimeError):
    """credential-bearing request와 원본 httpx exception을 외부 예외에 보존하지 않는다."""


class KISHttpClient:
    """공식 KIS read-only endpoint만 호출하며 API credential과 token을 보관하지 않는다."""

    def __init__(
        self,
        settings: KISSettings,
        token_provider: Callable[[], str] | None = None,
        transport: httpx.BaseTransport | None = None,
        rate_limiter: TokenBucket | None = None,
        retry_wait: wait_base | None = None,
    ) -> None:
        inner_transport = transport or httpx.HTTPTransport()
        self._http = httpx.Client(
            transport=_CredentialTransport(
                inner_transport,
                settings=settings,
                token_provider=token_provider,
            ),
            timeout=settings.kis_timeout_seconds,
            follow_redirects=False,
        )
        self._origin = settings.base_url
        self._retry_attempts = settings.kis_retry_attempts
        self._rate_limiter = rate_limiter or TokenBucket(settings.rate_limit_per_second)
        self._retry_wait = retry_wait or wait_exponential_jitter(initial=0.2, max=2.0)

    def request(
        self,
        method: str,
        path: str,
        tr_id: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """KIS 시장데이터 GET만 허용하고 인증 header는 private transport가 마지막 순간에 만든다."""
        if method.upper() != "GET":
            raise KISHttpError(405, "KIS client supports read-only GET requests")
        if (path, tr_id) not in _APPROVED_ENDPOINTS:
            raise ValueError("approved KIS endpoint and transaction id are required")
        safe_params = self._validated_params(params or {})
        retrying = Retrying(
            stop=stop_after_attempt(self._retry_attempts),
            wait=self._retry_wait,
            retry=retry_if_exception_type((KISRetryableStatus, KISTransportError)),
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                return self._send_once(path, tr_id, safe_params)
        raise RuntimeError("unreachable retry state")

    def close(self) -> None:
        self._http.close()

    def _send_once(self, path: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        self._rate_limiter.acquire()
        try:
            response = self._http.request(
                "GET",
                f"{self._origin}{path}",
                headers={_INTERNAL_TR_ID_HEADER: tr_id},
                params=params,
            )
        except KISCredentialError:
            raise
        except (httpx.TimeoutException, httpx.TransportError):
            raise KISTransportError("KIS transport is unavailable") from None
        if response.status_code >= 400:
            message = "upstream request failed"
            if response.status_code in {408, 429, 500, 502, 503, 504}:
                raise KISRetryableStatus(response.status_code, message)
            raise KISHttpError(response.status_code, message)
        try:
            data: object = response.json()
        except ValueError:
            raise KISHttpError(response.status_code, "KIS response was not valid JSON") from None
        if not isinstance(data, dict):
            raise KISHttpError(response.status_code, "KIS response was not a JSON object")
        return cast(dict[str, Any], data)

    @staticmethod
    def _validated_params(params: dict[str, str]) -> dict[str, str]:
        if any(_is_reserved_parameter(name) for name in params):
            raise ValueError("reserved KIS request parameter is not allowed")
        return dict(params)


def _is_reserved_parameter(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    return any(marker in normalized for marker in ("key", "secret", "token", "auth", "credential", "account"))
