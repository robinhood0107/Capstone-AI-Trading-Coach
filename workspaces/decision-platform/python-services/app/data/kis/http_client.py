from __future__ import annotations

import random
import re
import time
from collections.abc import Callable
from typing import Any, cast

import httpx

from app.data.kis.accounting import (
    CollectionRunRecorder,
    FailureCode,
    PhysicalChannel,
)
from app.data.kis._credential_transport import (
    KISCredentialError,
    _CredentialTransport,
    _TokenIssuer,
    _build_redis_client,
    _provider_scope,
)
from app.data.kis.auth import KISTokenManager
from app.data.kis.rate_limiter import RateLimiter, RedisIntervalLimiter, TokenBucket
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


class KISDistributionRetryableStatus(KISHttpError):
    """KIS gateway 분산/라우팅 실패는 안전한 GET을 다음 quota slot에서 한 번만 재호출한다."""


class KISProviderRateLimitError(KISHttpError):
    """provider가 유량 초과를 반환하면 자동 retry storm 없이 현재 호출을 중단한다."""


class KISTransportError(RuntimeError):
    """credential-bearing request와 원본 httpx exception을 외부 예외에 보존하지 않는다."""


class KISHttpClient:
    """공식 KIS read-only endpoint만 호출하며 API credential과 token을 보관하지 않는다."""

    def __init__(
        self,
        settings: KISSettings,
        token_provider: Callable[[], str] | None = None,
        transport: httpx.BaseTransport | None = None,
        rate_limiter: RateLimiter | None = None,
        retry_delay: Callable[[int], float] | None = None,
        retry_sleeper: Callable[[float], None] = time.sleep,
        accounting: CollectionRunRecorder | None = None,
        require_cached_token: bool = False,
    ) -> None:
        if not settings.offline and any(
            dependency is not None for dependency in (token_provider, transport, rate_limiter)
        ):
            # online dependency는 이 모듈의 private runtime wiring만 만들며 caller transport/no-op limiter를 거부한다.
            raise ValueError("KIS online private dependencies cannot be overridden")

        self._token_issuer: _TokenIssuer | None = None
        self._token_manager: KISTokenManager | None = None
        self._redis_client: Any | None = None
        self._closed = False
        if settings.offline:
            request_limiter = rate_limiter or TokenBucket(
                rate_per_second=1 / settings.request_interval_seconds
            )
            inner_transport = transport or httpx.HTTPTransport(verify=True, retries=0)
        else:
            redis_client = _build_redis_client()
            token_issuer: _TokenIssuer | None = None
            try:
                scope = _provider_scope(settings.mode)
                request_limiter = RedisIntervalLimiter(
                    redis_client,
                    key=f"kis:rest:v3:{scope}",
                    interval_seconds=settings.request_interval_seconds,
                    max_wait_seconds=float(settings.kis_rate_limit_max_wait_seconds),
                    io_budget_seconds=8.0,
                )
                # 발급 제한 단위가 공개 공지에 없으므로 mock/live를 합친 deployment-global 1/s로 보수 적용한다.
                token_limiter = RedisIntervalLimiter(
                    redis_client,
                    key="kis:tokenp:v3:deployment",
                    interval_seconds=1.0,
                    max_wait_seconds=float(settings.kis_rate_limit_max_wait_seconds),
                    io_budget_seconds=8.0,
                )
                token_issuer = _TokenIssuer(
                    settings,
                    rate_limiter=token_limiter,
                    accounting=accounting,
                )
                token_manager = KISTokenManager(
                    mode=settings.mode,
                    offline=False,
                    redis_client=redis_client,
                    issuer=token_issuer.issue,
                    scope=scope,
                    accounting=accounting,
                    cache_only=require_cached_token,
                )
                token_provider = token_manager.get_access_token
                inner_transport = httpx.HTTPTransport(verify=True, retries=0)
            except Exception:
                if token_issuer is not None:
                    try:
                        token_issuer.close()
                    finally:
                        redis_client.close()
                else:
                    redis_client.close()
                raise
            self._token_issuer = token_issuer
            self._token_manager = token_manager
            self._redis_client = redis_client

        try:
            self._http = httpx.Client(
                transport=_CredentialTransport(
                    inner_transport,
                    settings=settings,
                    token_provider=token_provider,
                    rate_limiter=request_limiter,
                    accounting=accounting,
                ),
                timeout=settings.kis_timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            )
        except Exception:
            inner_transport.close()
            if self._token_issuer is not None:
                self._token_issuer.close()
            if self._redis_client is not None:
                self._redis_client.close()
            raise
        self._origin = settings.base_url
        self._retry_attempts = settings.kis_retry_attempts
        self._retry_delay = retry_delay or _default_retry_delay
        self._retry_sleeper = retry_sleeper
        self._accounting = accounting

    def require_cached_access_token(self) -> None:
        """Core 6 probe가 OAuth 발급 없이 cached token만으로 one data handoff를 열 수 있는지 확인한다.

        이 method는 token을 반환·저장하지 않고 cache-only manager의 typed result만 사용한다. offline
        fixture client나 일반 backfill은 이 preflight를 호출하지 않아 기존 token refresh behavior를 유지한다.
        """

        if self._closed or self._token_manager is None:
            raise KISCredentialError("KIS cached token is unavailable")
        self._token_manager.get_access_token()

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
        attempt_number = 1
        distribution_retry_used = False
        while True:
            try:
                return self._send_once(path, tr_id, safe_params)
            except KISDistributionRetryableStatus:
                if distribution_retry_used or attempt_number >= self._retry_attempts:
                    raise
                # 공지의 "즉시 재호출"은 backoff만 생략한다. 실제 send는 같은 limiter를 다시 통과한다.
                distribution_retry_used = True
            except (KISRetryableStatus, KISTransportError):
                if attempt_number >= self._retry_attempts:
                    raise
                self._retry_sleeper(max(0.0, self._retry_delay(attempt_number)))
            attempt_number += 1

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._http.close()
        finally:
            try:
                if self._token_issuer is not None:
                    self._token_issuer.close()
            finally:
                if self._redis_client is not None:
                    self._redis_client.close()

    def _send_once(self, path: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
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
            if response.status_code == 429:
                self._record_physical_failure(FailureCode.PROVIDER_RATE_LIMIT)
                raise KISProviderRateLimitError(response.status_code, message)
            if response.status_code in {408, 500, 502, 503, 504}:
                self._record_physical_failure(FailureCode.HTTP_RETRYABLE)
                raise KISRetryableStatus(response.status_code, message)
            self._record_physical_failure(FailureCode.HTTP_ERROR)
            raise KISHttpError(response.status_code, message)
        try:
            data: object = response.json()
        except ValueError:
            self._record_physical_failure(FailureCode.PROVIDER_ERROR)
            raise KISHttpError(response.status_code, "KIS response was not valid JSON") from None
        if not isinstance(data, dict):
            self._record_physical_failure(FailureCode.PROVIDER_ERROR)
            raise KISHttpError(response.status_code, "KIS response was not a JSON object")
        payload = cast(dict[str, Any], data)
        if payload.get("rt_cd") not in (None, "0", 0):
            message_code = str(payload.get("msg_cd") or "")
            if message_code == "EGW00201":
                self._record_physical_failure(FailureCode.PROVIDER_RATE_LIMIT)
                raise KISProviderRateLimitError(429, "upstream request exceeded its rate limit")
            if message_code in {"EGW00001", "EGW00002", "EGW00202", "EGW00203", "EGW00300"}:
                self._record_physical_failure(FailureCode.PROVIDER_ROUTING)
                raise KISDistributionRetryableStatus(503, "upstream routing failed")
            self._record_physical_failure(FailureCode.PROVIDER_ERROR)
            return payload
        self._record_physical_success()
        return payload

    @staticmethod
    def _validated_params(params: dict[str, str]) -> dict[str, str]:
        if any(_is_reserved_parameter(name) for name in params):
            raise ValueError("reserved KIS request parameter is not allowed")
        return dict(params)

    def _record_physical_success(self) -> None:
        if self._accounting is not None:
            self._accounting.record_physical_success(PhysicalChannel.MARKET_DATA)

    def _record_physical_failure(self, code: FailureCode) -> None:
        if self._accounting is not None:
            self._accounting.record_physical_failure(PhysicalChannel.MARKET_DATA, code)


def _is_reserved_parameter(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    return any(marker in normalized for marker in ("key", "secret", "token", "auth", "credential", "account"))


def _default_retry_delay(attempt_number: int) -> float:
    # full jitter로 동일 장애 시 여러 worker의 재시도 시점을 흩뜨린다. 분산정책 즉시 재호출에는 적용하지 않는다.
    ceiling = min(2.0, 0.2 * (2 ** max(0, attempt_number - 1)))
    return random.uniform(0.0, ceiling)
