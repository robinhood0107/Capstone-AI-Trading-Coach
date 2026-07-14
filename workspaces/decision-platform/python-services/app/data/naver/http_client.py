from __future__ import annotations

import os
import random
import ssl
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Self, cast

import httpx
import redis
from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.data._shared.bounded_json import BoundedJsonError, parse_bounded_json_response
from app.data._shared.redis_quota import QuotaUnavailableError, RedisQuotaReservation
from app.data.naver._credential_transport import (
    _LOGICAL_DEADLINE_EXTENSION,
    NaverCredentialError,
    _CredentialTransport,
    _QuotaReservation,
    _canonical_client_headers,
)
from app.data.naver.errors import NaverParseError, NaverResponseError
from app.data.naver.models import NaverNewsPage
from app.data.naver.parsers import parse_news_response, raise_for_naver_error
from app.data.naver.policy import bounded_json_limits, request_policy_for, validate_news_query
from app.data.naver.profiles import NaverProfile, profile_for, require_canonical_profile
from app.data.naver.quota import quota_key_for, quota_policy_for
from app.data.naver.settings import NaverSettings


_REPOSITORY_ROOT = Path(__file__).resolve().parents[6]
_DEFAULT_TIMEOUT = httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=1.0)
_REDIS_TIMEOUT_SECONDS = 2.0


class _RedisSettings(BaseSettings):
    """Redis connection credential을 public Naver settings와 분리해 private wiring에서만 읽는다."""

    model_config = SettingsConfigDict(
        env_file=(_REPOSITORY_ROOT / ".env",),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: SecretStr = Field(repr=False, exclude=True)

    @field_validator("redis_password")
    @classmethod
    def _require_password(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("blank Redis authentication material")
        return value


def build_tls_context() -> ssl.SSLContext:
    """System trust store·hostname 검증을 유지하고 TLS 1.2 미만 연결을 거부한다."""
    if any(os.environ.get(name) for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE")):
        raise ValueError("Naver ambient TLS override is not allowed")
    context = ssl.create_default_context()
    if hasattr(context, "keylog_filename"):
        setattr(context, "keylog_filename", None)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def _build_httpx_client(
    *,
    transport: httpx.BaseTransport,
    timeout: httpx.Timeout = _DEFAULT_TIMEOUT,
) -> httpx.Client:
    return httpx.Client(
        transport=transport,
        timeout=timeout,
        headers=_canonical_client_headers(),
        follow_redirects=False,
        trust_env=False,
    )


def _build_redis_client() -> Any:
    try:
        settings = _RedisSettings()  # type: ignore[call-arg]
    except ValidationError:
        raise QuotaUnavailableError("source quota authentication is unavailable") from None
    password = settings.redis_password.get_secret_value()
    try:
        return redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=password,
            socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
            socket_timeout=_REDIS_TIMEOUT_SECONDS,
            retry_on_timeout=False,
        )
    finally:
        password = ""


class NaverHttpClient:
    """고정 Naver News GET만 bounded parse하며 credential과 raw payload를 보관하지 않는다."""

    def __init__(
        self,
        *,
        settings: NaverSettings,
        profile: NaverProfile,
        transport: httpx.BaseTransport | None = None,
        quota: _QuotaReservation | None = None,
        retry_delay: Callable[[int], float] | None = None,
    ) -> None:
        if transport is not None or quota is not None:
            raise ValueError("Naver production private dependencies cannot be overridden")
        canonical_profile = require_canonical_profile(profile)
        if settings.search_profile != canonical_profile.name:
            raise ValueError("Naver production profile is not enabled")
        try:
            active_profile = profile_for(settings.search_profile)
        except ValueError as error:
            raise ValueError(str(error)) from None
        if active_profile is not canonical_profile:
            raise ValueError("Naver production profile is not canonical")

        tls_context = build_tls_context()
        redis_client = _build_redis_client()
        try:
            inner = httpx.HTTPTransport(verify=tls_context, retries=0)
            reservation = cast(
                _QuotaReservation,
                RedisQuotaReservation(
                    redis_client,
                    key=quota_key_for(profile.provider_profile),
                    policy=quota_policy_for(
                        profile.provider_profile,
                        max_calls_per_run=settings.max_calls_per_run,
                    ),
                ),
            )
            try:
                self._initialize(
                    settings=settings,
                    profile=profile,
                    transport=inner,
                    quota=reservation,
                    retry_delay=retry_delay,
                    enforce_lifecycle=True,
                    quota_wait_sleep=time.sleep,
                )
            except Exception:
                inner.close()
                raise
        except Exception:
            redis_client.close()
            raise
        self._redis_client: Any | None = redis_client

    @classmethod
    def _for_test(
        cls,
        *,
        settings: NaverSettings,
        profile: NaverProfile,
        transport: httpx.BaseTransport,
        quota: _QuotaReservation,
        retry_delay: Callable[[int], float] | None = None,
        quota_wait_sleep: Callable[[float], None] | None = None,
    ) -> Self:
        """MockTransport만 주입하는 offline test 전용 constructor다."""
        if not isinstance(transport, httpx.MockTransport):
            raise ValueError("Naver test transport must be httpx.MockTransport")
        require_canonical_profile(profile)
        instance = cls.__new__(cls)
        instance._initialize(
            settings=settings,
            profile=profile,
            transport=transport,
            quota=quota,
            retry_delay=retry_delay,
            enforce_lifecycle=False,
            quota_wait_sleep=quota_wait_sleep or (lambda _: None),
        )
        instance._redis_client = None
        return instance

    def _initialize(
        self,
        *,
        settings: NaverSettings,
        profile: NaverProfile,
        transport: httpx.BaseTransport,
        quota: _QuotaReservation,
        retry_delay: Callable[[int], float] | None,
        enforce_lifecycle: bool,
        quota_wait_sleep: Callable[[float], None],
    ) -> None:
        credential_transport = _CredentialTransport(
            transport,
            profile=profile,
            quota=quota,
            max_response_bytes=settings.response_max_bytes,
            enforce_lifecycle=enforce_lifecycle,
            quota_wait_sleep=quota_wait_sleep,
        )
        self._http = _build_httpx_client(
            transport=credential_transport,
            timeout=httpx.Timeout(
                connect=settings.connect_timeout_seconds,
                read=settings.read_timeout_seconds,
                write=settings.write_timeout_seconds,
                pool=settings.pool_timeout_seconds,
            ),
        )
        self._settings = settings
        self._profile = profile
        self._policy = request_policy_for(profile.provider_profile)
        self._quota = quota
        self._transport = credential_transport
        self._retry_delay = retry_delay or _default_retry_delay
        self._closed = False

    def search_news(
        self,
        query: str,
        *,
        retrieved_at: datetime,
        requested_display: int,
    ) -> NaverNewsPage:
        """감사된 종목명 하나를 고정 News endpoint에서 조회해 sanitize된 page만 반환한다."""
        safe_query = validate_news_query(query)
        if (
            isinstance(requested_display, bool)
            or not isinstance(requested_display, int)
            or not 1 <= requested_display <= 20
        ):
            raise ValueError("Naver display is invalid")
        params = {
            "query": safe_query,
            "display": str(requested_display),
            "start": "1",
            "sort": "date",
            **self._policy.static_query,
        }

        attempt = 1
        started_at = time.monotonic()
        deadline = started_at + self._settings.logical_deadline_seconds
        while True:
            try:
                return self._send_once(
                    params=params,
                    retrieved_at=retrieved_at,
                    requested_display=requested_display,
                    deadline=deadline,
                )
            except NaverResponseError as error:
                if not error.retryable or attempt >= self._policy.max_attempts:
                    raise
            except NaverCredentialError as error:
                if not error.retryable or attempt >= self._policy.max_attempts:
                    raise
            delay = min(0.5, max(0.0, self._retry_delay(attempt)))
            if time.monotonic() + delay >= deadline:
                raise NaverCredentialError("logical_deadline_exceeded") from None
            time.sleep(delay)
            attempt += 1

    def _send_once(
        self,
        *,
        params: dict[str, str],
        retrieved_at: datetime,
        requested_display: int,
        deadline: float,
    ) -> NaverNewsPage:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise NaverCredentialError("logical_deadline_exceeded")
        response = self._http.request(
            "GET",
            f"{self._policy.origin}{self._policy.path}",
            params=params,
            timeout=httpx.Timeout(
                connect=min(self._settings.connect_timeout_seconds, remaining),
                read=min(self._settings.read_timeout_seconds, remaining),
                write=min(self._settings.write_timeout_seconds, remaining),
                pool=min(self._settings.pool_timeout_seconds, remaining),
            ),
            extensions={_LOGICAL_DEADLINE_EXTENSION: deadline},
        )
        if time.monotonic() >= deadline:
            raise NaverCredentialError("logical_deadline_exceeded")
        status = response.status_code
        if 300 <= status < 400:
            response.close()
            raise NaverResponseError("redirect_rejected", retryable=False)
        try:
            value = parse_bounded_json_response(
                response,
                limits=bounded_json_limits(self._settings),
            )
        except BoundedJsonError:
            if status in {401, 403}:
                raise NaverResponseError("authentication_failed", retryable=False) from None
            if status == 429:
                raise NaverResponseError("rate_limited", retryable=False) from None
            if status >= 500:
                raise NaverResponseError("provider_unavailable", retryable=True) from None
            raise NaverParseError() from None
        if time.monotonic() >= deadline:
            raise NaverCredentialError("logical_deadline_exceeded")
        if not isinstance(value, Mapping):
            if status in {401, 403}:
                raise NaverResponseError("authentication_failed", retryable=False) from None
            if status == 429:
                raise NaverResponseError("rate_limited", retryable=False) from None
            if status >= 500:
                raise NaverResponseError("provider_unavailable", retryable=True) from None
            raise NaverParseError() from None
        payload = cast(Mapping[str, object], value)
        try:
            raise_for_naver_error(status, payload, profile=self._profile.provider_profile)
        except NaverResponseError as error:
            # API gateway가 2xx body에 감싼 429도 다음 outbound 전에 동일 cooldown을 공유한다.
            if status != 429 and error.code == "rate_limited":
                self._quota.activate_cooldown(seconds=60)
            raise
        page = parse_news_response(
            payload,
            profile=self._profile.provider_profile,
            retrieved_at=retrieved_at,
            requested_display=requested_display,
        )
        if time.monotonic() >= deadline:
            raise NaverCredentialError("logical_deadline_exceeded")
        return page

    def close(self) -> None:
        """HTTP transport와 production Redis connection을 idempotent하게 닫는다."""
        if self._closed:
            return
        self._closed = True
        try:
            self._http.close()
        finally:
            if self._redis_client is not None:
                self._redis_client.close()

    @property
    def physical_attempt_count(self) -> int:
        """quota 예약에 성공한 query physical attempt 누계를 반환한다."""
        return self._transport.physical_attempt_count


def _default_retry_delay(_: int) -> float:
    # 한 번뿐인 retry를 0..500ms full jitter로 분산해 동시 장애의 재집중을 줄인다.
    return random.uniform(0.0, 0.5)
