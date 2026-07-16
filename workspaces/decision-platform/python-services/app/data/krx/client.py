from __future__ import annotations

import os
import ssl
import time
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any, Self, cast

import httpx
import redis
from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.data._shared.bounded_json import (
    BoundedJsonError,
    BoundedJsonLimits,
    parse_bounded_json_response,
)
from app.data._shared.redis_quota import QuotaUnavailableError, RedisQuotaReservation
from app.data.kis.calendar import is_xkrx_trading_day
from app.data.krx._credential_transport import (
    _LOGICAL_DEADLINE_EXTENSION,
    _SAFE_RESPONSE_METADATA_EXTENSION,
    KrxCredentialError,
    _CredentialTransport,
    _QuotaReservation,
    _canonical_client_headers,
    _suppress_dependency_http_logs,
)
from app.data.krx.catalog import (
    ENABLED_UNIVERSE_ENDPOINTS,
    KRX_OPEN_API_FIRST_AVAILABLE_DATE,
    KrxEndpoint,
)
from app.data.krx.errors import (
    KrxError,
    KrxParseError,
    KrxSafeResponseMetadata,
    KrxValidationDiagnostic,
)
from app.data.krx.parsers import KrxDailyRow, parse_daily_response
from app.data.krx.quota import quota_key, quota_policy
from app.data.krx.settings import KrxOpenApiSettings


_REPOSITORY_ROOT = Path(__file__).resolve().parents[6]
_REDIS_TIMEOUT_SECONDS = 2.0
_TLS_ENVIRONMENT_OVERRIDES = ("SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE")


class KrxHttpError(KrxError):
    """provider URL·header·body·message를 제외한 KRX HTTP 경계 오류다."""

    def __init__(
        self,
        code: str,
        *,
        status_code: int | None = None,
        validation_diagnostic: KrxValidationDiagnostic | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.validation_diagnostic = validation_diagnostic
        super().__init__(f"krx_http_error:{code}")


class _RedisSettings(BaseSettings):
    """Redis credential을 공개 KRX 설정과 분리해 production wiring에서만 읽는다."""

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


def _build_tls_context() -> ssl.SSLContext:
    """시스템 trust store·hostname 검증을 유지하고 TLS 1.2 미만을 거부한다."""
    if any(os.environ.get(name, "") != "" for name in _TLS_ENVIRONMENT_OVERRIDES):
        raise ValueError("KRX ambient TLS override is not allowed")
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    if hasattr(context, "keylog_filename"):
        setattr(context, "keylog_filename", None)
    return context


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


def _close_without_raising(resource: Any | None) -> None:
    """constructor 실패 시 한 cleanup 예외가 다른 resource 정리를 막지 않게 한다."""
    if resource is None:
        return
    try:
        resource.close()
    except Exception:
        pass


class KrxOpenApiClient:
    """두 국내주식 일별 endpoint만 고정 origin·bounded JSON 경계로 조회한다."""

    def __init__(
        self,
        settings: KrxOpenApiSettings,
        *,
        transport: httpx.BaseTransport | None = None,
        quota: _QuotaReservation | None = None,
    ) -> None:
        """production에서는 transport와 quota의 caller 주입을 거부하고 private wiring만 쓴다."""
        if transport is not None or quota is not None:
            raise ValueError("KRX production private dependencies cannot be overridden")

        tls_context = _build_tls_context()
        redis_client = _build_redis_client()
        inner: httpx.BaseTransport | None = None
        try:
            reservation = cast(
                _QuotaReservation,
                RedisQuotaReservation(
                    redis_client,
                    key=quota_key(),
                    policy=quota_policy(max_calls_per_run=settings.max_calls_per_run),
                ),
            )
            inner = httpx.HTTPTransport(
                verify=tls_context,
                proxy=None,
                http1=True,
                http2=False,
                retries=0,
            )
            self._initialize(
                settings=settings,
                transport=inner,
                quota=reservation,
                quota_wait_sleep=time.sleep,
            )
        except Exception:
            _close_without_raising(inner)
            _close_without_raising(redis_client)
            raise KrxCredentialError("initialization_unavailable") from None
        self._redis_client: Any | None = redis_client

    @classmethod
    def _for_test(
        cls,
        *,
        settings: KrxOpenApiSettings,
        transport: httpx.BaseTransport,
        quota: _QuotaReservation,
        quota_wait_sleep: Callable[[float], None] | None = None,
    ) -> Self:
        """socket·ambient proxy 없이 MockTransport만 주입하는 offline test factory다."""
        if not isinstance(transport, httpx.MockTransport):
            raise ValueError("KRX test factory requires httpx.MockTransport")
        instance = cls.__new__(cls)
        instance._initialize(
            settings=settings,
            transport=transport,
            quota=quota,
            quota_wait_sleep=quota_wait_sleep or (lambda _: None),
        )
        instance._redis_client = None
        return instance

    def _initialize(
        self,
        *,
        settings: KrxOpenApiSettings,
        transport: httpx.BaseTransport,
        quota: _QuotaReservation,
        quota_wait_sleep: Callable[[float], None],
    ) -> None:
        credential_transport = _CredentialTransport(
            transport,
            quota=quota,
            max_response_bytes=settings.response_max_bytes,
            quota_wait_sleep=quota_wait_sleep,
        )
        self._http = httpx.Client(
            transport=credential_transport,
            timeout=httpx.Timeout(
                connect=settings.connect_timeout_seconds,
                read=settings.read_timeout_seconds,
                write=settings.write_timeout_seconds,
                pool=settings.pool_timeout_seconds,
            ),
            headers=_canonical_client_headers(),
            follow_redirects=False,
            trust_env=False,
        )
        self._settings = settings
        self._transport = credential_transport
        self._limits = BoundedJsonLimits(
            max_bytes=settings.response_max_bytes,
            max_depth=settings.json_max_depth,
            max_list_items=settings.json_max_rows,
            max_object_keys=16,
            max_text_codepoints=1_024,
            max_text_bytes=4_096,
            max_number_characters=64,
        )
        self._closed = False

    def fetch_universe_rows(self, as_of: date) -> tuple[KrxDailyRow, ...]:
        """직전 완료 XKRX session의 KOSPI 뒤 KOSDAQ 행을 모두 성공한 경우에만 반환한다."""
        if type(as_of) is not date or as_of < KRX_OPEN_API_FIRST_AVAILABLE_DATE:
            raise ValueError("KRX as-of date is outside the supported range")
        try:
            is_session = is_xkrx_trading_day(as_of)
        except Exception:
            raise ValueError("calendar_unavailable") from None
        if not is_session:
            raise ValueError("KRX as-of date must be an XKRX trading session")
        deadline = time.monotonic() + self._settings.logical_deadline_seconds
        rows: list[KrxDailyRow] = []
        # endpoint 순서는 evidence의 request ordinal과 physical call accounting 계약이다.
        for request_ordinal, endpoint in enumerate(ENABLED_UNIVERSE_ENDPOINTS, start=1):
            rows.extend(
                self._fetch_endpoint(
                    endpoint,
                    as_of=as_of,
                    deadline=deadline,
                    request_ordinal=request_ordinal,
                )
            )
        return tuple(rows)

    def _fetch_endpoint(
        self,
        endpoint: KrxEndpoint,
        *,
        as_of: date,
        deadline: float,
        request_ordinal: int,
    ) -> tuple[KrxDailyRow, ...]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise KrxCredentialError("logical_deadline_exceeded")
        with _suppress_dependency_http_logs():
            response = self._http.request(
                "GET",
                f"{self._settings.origin}{endpoint.path}",
                params={endpoint.request_parameter: as_of.strftime("%Y%m%d")},
                timeout=httpx.Timeout(
                    connect=min(self._settings.connect_timeout_seconds, remaining),
                    read=min(self._settings.read_timeout_seconds, remaining),
                    write=min(self._settings.write_timeout_seconds, remaining),
                    pool=min(self._settings.pool_timeout_seconds, remaining),
                ),
                extensions={_LOGICAL_DEADLINE_EXTENSION: deadline},
            )
        status = response.status_code
        if 300 <= status < 400:
            response.close()
            raise KrxHttpError("redirect_rejected", status_code=status)
        if status != 200:
            response.close()
            raise KrxHttpError("http_status", status_code=status)
        response_metadata = _safe_response_metadata(response)
        try:
            payload = parse_bounded_json_response(response, limits=self._limits)
        except BoundedJsonError as error:
            diagnostic = _bounded_json_diagnostic(
                error,
                response_metadata=response_metadata,
            ).with_context(
                request_ordinal=request_ordinal,
                service=_service_id(endpoint),
                http_status=status,
                response_metadata=response_metadata,
            )
            raise KrxHttpError(
                "parse_invalid_response",
                status_code=status,
                validation_diagnostic=diagnostic,
            ) from None
        if not isinstance(payload, Mapping):
            diagnostic = KrxValidationDiagnostic.for_leaf(
                "payload_not_object",
                top_level_type=_top_level_type(payload),
            ).with_context(
                request_ordinal=request_ordinal,
                service=_service_id(endpoint),
                http_status=status,
                response_metadata=response_metadata,
            )
            raise KrxHttpError(
                "parse_invalid_response",
                status_code=status,
                validation_diagnostic=diagnostic,
            )
        try:
            rows = parse_daily_response(
                cast(Mapping[str, object], payload),
                endpoint=endpoint,
                requested_date=as_of,
            )
        except KrxParseError as error:
            parse_diagnostic = error.diagnostic
            if parse_diagnostic is not None:
                parse_diagnostic = parse_diagnostic.with_context(
                    request_ordinal=request_ordinal,
                    service=_service_id(endpoint),
                    http_status=status,
                    response_metadata=response_metadata,
                )
            raise KrxHttpError(
                "parse_invalid_response",
                status_code=status,
                validation_diagnostic=parse_diagnostic,
            ) from None
        if time.monotonic() >= deadline:
            raise KrxCredentialError("logical_deadline_exceeded")
        return rows

    @property
    def physical_attempt_count(self) -> int:
        """두 endpoint의 provider transport handoff physical attempt 누계를 반환한다."""
        return self._transport.physical_attempt_count

    def close(self) -> None:
        """HTTP pool과 production Redis connection을 idempotent하게 닫는다."""
        if self._closed:
            return
        cleanup_failed = False
        try:
            self._http.close()
        except Exception:
            cleanup_failed = True
        if self._redis_client is not None:
            try:
                self._redis_client.close()
            except Exception:
                cleanup_failed = True
        if cleanup_failed:
            raise KrxCredentialError("cleanup_unavailable") from None
        self._redis_client = None
        self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _safe_response_metadata(response: httpx.Response) -> KrxSafeResponseMetadata | None:
    value = response.extensions.get(_SAFE_RESPONSE_METADATA_EXTENSION)
    if isinstance(value, KrxSafeResponseMetadata):
        return value
    return None


def _bounded_json_diagnostic(
    error: BoundedJsonError,
    *,
    response_metadata: KrxSafeResponseMetadata | None,
) -> KrxValidationDiagnostic:
    leaf = error.code
    if leaf == "content_type_missing" and response_metadata is not None:
        if response_metadata.content_type_class == "multiple":
            leaf = "content_type_multiple"
        elif response_metadata.content_type_class == "other":
            leaf = "content_type_unsupported"
    return KrxValidationDiagnostic.for_leaf(leaf)


def _service_id(endpoint: KrxEndpoint) -> str:
    return endpoint.path.rsplit("/", 1)[-1].removesuffix(".json")


def _top_level_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, list):
        return "array"
    if type(value) is bool:
        return "boolean"
    if isinstance(value, str):
        return "string"
    return "number"
