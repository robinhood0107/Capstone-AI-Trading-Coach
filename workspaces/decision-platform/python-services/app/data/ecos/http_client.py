from __future__ import annotations

import os
import random
import ssl
import time
from collections.abc import Callable, Mapping
from datetime import date
from typing import Protocol, TypeVar, cast

import httpx
from pydantic import SecretStr

from app.data._shared.bounded_json import (
    BoundedJsonError,
    BoundedJsonLimits,
    parse_bounded_json_response,
)
from app.data.ecos._credential_transport import (
    _ECOS_DEADLINE_EXTENSION,
    ECOSCredentialError,
    _CredentialTransport,
    _canonical_client_headers,
)
from app.data.ecos.errors import ECOSApplicationError, ECOSError
from app.data.ecos.models import (
    StatisticItemMetadata,
    StatisticSearchPage,
    StatisticTableMetadata,
)
from app.data.ecos.parsers import (
    parse_statistic_item_list,
    parse_statistic_search,
    parse_statistic_table_list,
    raise_for_ecos_application_error,
)
from app.data.ecos.policy import (
    ECOS_ORIGIN,
    build_keyless_service_path,
    should_retry_ecos_failure,
    validate_keyless_service_path,
)
from app.data.ecos.quota import (
    ECOSQuota,
    apply_ecos_application_cooldown,
    build_ecos_quota_reservation,
    _build_redis_client,
)
from app.data.ecos.series_registry import ECOSSeries
from app.data.ecos.settings import ECOSSettings

_ResultT = TypeVar("_ResultT")
_TLS_ENVIRONMENT_OVERRIDES = ("SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE")


class _Quota(Protocol):
    def reserve(self, *, attempt_id: str) -> None: ...


class ECOSHttpError(ECOSError):
    """provider URL·message·raw response를 제외한 stable HTTP 경계 오류다."""

    def __init__(self, code: str, *, status_code: int | None = None) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def _build_tls_context() -> ssl.SSLContext:
    """시스템 trust store와 hostname 검증을 유지하며 TLS 1.2 이상만 허용한다."""
    if any(os.environ.get(name, "") != "" for name in _TLS_ENVIRONMENT_OVERRIDES):
        raise ECOSHttpError("tls_environment_not_allowed")
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    setattr(context, "keylog_filename", None)
    return context


class ECOSHttpClient:
    """세 ECOS GET 서비스만 fixed origin·private transport·bounded JSON으로 호출한다."""

    def __init__(
        self,
        settings: ECOSSettings,
        *,
        transport: httpx.BaseTransport | None = None,
        quota: _Quota | None = None,
    ) -> None:
        """운영 client는 caller transport/quota override를 거부하고 private wiring만 사용한다."""
        if transport is not None or quota is not None:
            raise ValueError("ECOS online private dependencies cannot be overridden")

        redis_client = _build_redis_client()
        inner: httpx.BaseTransport | None = None
        try:
            reservation = build_ecos_quota_reservation(
                redis_client,
                max_calls_per_run=settings.max_calls_per_run,
            )
            inner = httpx.HTTPTransport(verify=_build_tls_context(), retries=0)
            self._initialize(
                settings,
                transport=inner,
                quota=reservation,
                credential_reader=None,
                retry_sleeper=time.sleep,
                redis_client=redis_client,
                monotonic=time.monotonic,
            )
        except Exception:
            if inner is not None:
                inner.close()
            redis_client.close()
            raise

    @classmethod
    def _for_tests(
        cls,
        settings: ECOSSettings,
        *,
        transport: httpx.BaseTransport,
        quota: _Quota,
        credential: SecretStr,
        retry_sleeper: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> "ECOSHttpClient":
        """socket/env 없이 synthetic credential과 MockTransport만 쓰는 비공개 test factory다."""
        if not isinstance(transport, httpx.MockTransport):
            raise ValueError("ECOS test factory requires a private mock transport")
        client = cls.__new__(cls)
        client._initialize(
            settings,
            transport=transport,
            quota=quota,
            credential_reader=lambda: credential,
            retry_sleeper=retry_sleeper or (lambda _: None),
            redis_client=None,
            monotonic=monotonic or time.monotonic,
        )
        return client

    def _initialize(
        self,
        settings: ECOSSettings,
        *,
        transport: httpx.BaseTransport,
        quota: _Quota,
        credential_reader: Callable[[], SecretStr] | None,
        retry_sleeper: Callable[[float], None],
        redis_client: object | None,
        monotonic: Callable[[], float],
    ) -> None:
        credential_transport = _CredentialTransport(
            transport,
            quota=quota,
            credential_reader=credential_reader,
            max_response_bytes=settings.response_max_bytes,
            monotonic=monotonic,
        )
        timeout = httpx.Timeout(
            connect=settings.connect_timeout_seconds,
            read=settings.read_timeout_seconds,
            write=settings.write_timeout_seconds,
            pool=settings.pool_timeout_seconds,
        )
        self._http = httpx.Client(
            transport=credential_transport,
            timeout=timeout,
            headers=_canonical_client_headers(),
            follow_redirects=False,
            trust_env=False,
        )
        self._transport = credential_transport
        self._quota = quota
        self._limits = BoundedJsonLimits(
            max_bytes=settings.response_max_bytes,
            max_depth=settings.json_max_depth,
            max_list_items=400,
            max_object_keys=settings.json_max_object_keys,
            max_text_codepoints=4_096,
            max_text_bytes=16_384,
            max_number_characters=64,
        )
        self._retry_sleeper = retry_sleeper
        self._max_attempts_per_request = min(settings.max_attempts_per_request, 2)
        self._logical_deadline_seconds = settings.logical_deadline_seconds
        self._phase_timeout_seconds = (
            settings.connect_timeout_seconds,
            settings.read_timeout_seconds,
            settings.write_timeout_seconds,
            settings.pool_timeout_seconds,
        )
        self._monotonic = monotonic
        self._redis_client = redis_client
        self._closed = False

    def get_json(self, path: str) -> dict[str, object]:
        """검증된 keyless 상대경로를 lower-only attempt 상한으로 bounded JSON object화한다."""
        validate_keyless_service_path(path)
        return self._execute(path, _identity_payload)

    def statistic_search(
        self,
        *,
        series: ECOSSeries,
        start: date,
        end: date,
        page_start: int,
        page_end: int,
    ) -> StatisticSearchPage:
        """승인 series의 일별 관측 page를 identity 검증된 canonical 값으로 반환한다."""
        path = build_keyless_service_path(
            service="StatisticSearch",
            start_index=page_start,
            end_index=page_end,
            arguments=(
                series.stat_code,
                series.cycle,
                start.strftime("%Y%m%d"),
                end.strftime("%Y%m%d"),
                series.item_code1,
            ),
        )
        return self._execute(
            path,
            lambda payload: parse_statistic_search(
                payload,
                expected_stat_code=series.stat_code,
                expected_item_code1=series.item_code1,
                expected_cycle=series.cycle,
                max_rows=400,
            ),
        )

    def statistic_table_list(self, *, series: ECOSSeries) -> StatisticTableMetadata:
        """registry preflight용 table metadata에서 allowlist 필드만 반환한다."""
        path = build_keyless_service_path(
            service="StatisticTableList",
            start_index=1,
            end_index=200,
            arguments=(series.stat_code,),
        )
        return self._execute_preflight(
            path,
            lambda payload: parse_statistic_table_list(
                payload,
                expected_stat_code=series.stat_code,
            ),
        )

    def statistic_item_list(self, *, series: ECOSSeries) -> StatisticItemMetadata:
        """registry preflight용 item 목록에서 exact series candidate 하나만 반환한다."""
        path = build_keyless_service_path(
            service="StatisticItemList",
            start_index=1,
            end_index=200,
            arguments=(series.stat_code,),
        )
        return self._execute_preflight(
            path,
            lambda payload: parse_statistic_item_list(
                payload,
                expected_stat_code=series.stat_code,
                expected_item_code=series.item_code1,
            ),
        )

    @property
    def physical_attempt_count(self) -> int:
        """manifest audit에 사용할 quota-reserved physical attempt 수를 반환한다."""
        return self._transport.physical_attempt_count

    def close(self) -> None:
        """HTTP pool과 private Redis client를 중복 호출에 안전하게 닫는다."""
        if self._closed:
            return
        self._closed = True
        try:
            self._http.close()
        finally:
            redis_client = self._redis_client
            close = getattr(redis_client, "close", None)
            if callable(close):
                close()

    def __enter__(self) -> "ECOSHttpClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _execute(
        self,
        path: str,
        parser: Callable[[Mapping[str, object]], _ResultT],
    ) -> _ResultT:
        return self._execute_with_attempt_limit(
            path,
            parser,
            max_attempts=self._max_attempts_per_request,
        )

    def _execute_preflight(
        self,
        path: str,
        parser: Callable[[Mapping[str, object]], _ResultT],
    ) -> _ResultT:
        """registry metadata는 data retry 설정과 무관하게 정확히 한 번만 시도한다."""
        return self._execute_with_attempt_limit(path, parser, max_attempts=1)

    def _execute_with_attempt_limit(
        self,
        path: str,
        parser: Callable[[Mapping[str, object]], _ResultT],
        *,
        max_attempts: int,
    ) -> _ResultT:
        if max_attempts not in {1, 2}:
            raise ValueError("ECOS retry attempt count is out of bounds")
        started = self._monotonic()
        deadline = started + self._logical_deadline_seconds
        attempt = 1
        while True:
            failure: Exception | None = None
            try:
                payload = self._send_once(path, deadline=deadline)
                # metadata와 generic JSON 경로도 provider application 오류를 parser 전에 통일한다.
                raise_for_ecos_application_error(payload)
                result = parser(payload)
                if self._monotonic() >= deadline:
                    raise ECOSHttpError("logical_deadline_exceeded")
                return result
            except ECOSApplicationError as error:
                failure = error
                if error.code == "ERROR-602":
                    activate = getattr(self._quota, "activate_cooldown", None)
                    cooldown_failed = not callable(activate)
                    if callable(activate):
                        try:
                            apply_ecos_application_cooldown(
                                cast(ECOSQuota, self._quota),
                                application_code=error.code,
                            )
                        except Exception:
                            cooldown_failed = True
                    if cooldown_failed:
                        raise ECOSHttpError("quota_cooldown_unavailable") from None
                retryable = should_retry_ecos_failure(error.code)
            except ECOSCredentialError as error:
                if error.code == "logical_deadline_exceeded":
                    failure = ECOSHttpError("logical_deadline_exceeded")
                    retryable = False
                else:
                    failure = error
                    retryable = error.retryable
            except ECOSHttpError as error:
                failure = error
                retryable = should_retry_ecos_failure(error.code)

            if attempt >= max_attempts or not retryable:
                if failure is None:
                    raise ECOSHttpError("request_failed")
                raise failure
            delay = random.uniform(0.0, 0.5)
            if self._monotonic() + delay >= deadline:
                raise ECOSHttpError("logical_deadline_exceeded") from None
            self._retry_sleeper(delay)
            if self._monotonic() >= deadline:
                raise ECOSHttpError("logical_deadline_exceeded") from None
            attempt += 1

    def _send_once(self, path: str, *, deadline: float) -> Mapping[str, object]:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise ECOSHttpError("logical_deadline_exceeded")
        connect, read, write, pool = self._phase_timeout_seconds
        timeout = httpx.Timeout(
            connect=min(connect, remaining),
            read=min(read, remaining),
            write=min(write, remaining),
            pool=min(pool, remaining),
        )
        try:
            response = self._http.get(
                f"{ECOS_ORIGIN}{path}",
                timeout=timeout,
                extensions={_ECOS_DEADLINE_EXTENSION: deadline},
            )
        except ECOSCredentialError:
            raise

        status = response.status_code
        if 300 <= status < 400:
            _close_response_without_details(response)
            raise ECOSHttpError("redirect_rejected", status_code=status) from None
        if status >= 400:
            _close_response_without_details(response)
            raise ECOSHttpError(f"http_{status}", status_code=status) from None
        try:
            payload = parse_bounded_json_response(response, limits=self._limits)
        except BoundedJsonError:
            raise ECOSHttpError("response_invalid", status_code=status) from None
        if not isinstance(payload, dict):
            raise ECOSHttpError("response_invalid", status_code=status)
        return cast(dict[str, object], payload)


def _identity_payload(payload: Mapping[str, object]) -> dict[str, object]:
    return dict(payload)


def _close_response_without_details(response: httpx.Response) -> None:
    """status 오류가 sanitized response cleanup 예외에 덮이지 않도록 raw detail을 폐기한다."""
    try:
        response.close()
    except Exception:
        pass
