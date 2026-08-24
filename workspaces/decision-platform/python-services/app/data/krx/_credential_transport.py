from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from threading import Lock
from typing import Protocol
from urllib.parse import quote, quote_plus
from uuid import uuid4

import httpx
from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.data._shared.redis_quota import QuotaWaitError
from app.data.krx.catalog import (
    ENABLED_UNIVERSE_ENDPOINTS,
    KRX_OPEN_API_ORIGIN,
    S5_PRODUCTION_ENDPOINTS,
)
from app.data.krx.errors import KrxSafeResponseMetadata

_REPOSITORY_ROOT = Path(__file__).resolve().parents[6]
_AUTH_HEADER = "AUTH_KEY"
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_CREDENTIAL_HEADER_BYTES = 512
# 공식 최대 5,000행 × (row dict 1 + 15 key/value pair 30) + envelope 3개를 모두 검사한다.
_MAX_DECODED_JSON_NODES = 5_000 * (1 + 2 * 15) + 3
_LOGICAL_DEADLINE_EXTENSION = "s1.3.krx.logical_deadline"
_SAFE_RESPONSE_METADATA_EXTENSION = "s1.3.krx.safe_response_metadata"
_CANONICAL_CLIENT_HEADER_ITEMS = (
    ("Accept", "application/json"),
    ("Accept-Encoding", "identity"),
    ("Connection", "keep-alive"),
    ("User-Agent", "capstone-ai-trading-coach-s1.3"),
)
_ALLOWED_PATHS = frozenset(endpoint.path for endpoint in ENABLED_UNIVERSE_ENDPOINTS) | frozenset(
    endpoint.path for endpoint in S5_PRODUCTION_ENDPOINTS.values()
)
_DEPENDENCY_HTTP_LOGGER_NAMES = (
    "httpx",
    "httpcore.connection",
    "httpcore.http11",
)
_DEPENDENCY_HTTP_LOG_GUARD = ContextVar("krx_dependency_http_log_guard", default=False)
_DEPENDENCY_HTTP_LOG_FILTER_LOCK = Lock()


class _QuotaReservation(Protocol):
    def reserve(self, *, attempt_id: str) -> None: ...


class _DependencyHttpLogFilter(logging.Filter):
    """KRX 요청 context에서 dependency raw HTTP record가 handler로 전달되지 않게 한다."""

    def filter(self, _record: logging.LogRecord) -> bool:
        return not _DEPENDENCY_HTTP_LOG_GUARD.get()


_DEPENDENCY_HTTP_LOG_FILTER = _DependencyHttpLogFilter()


@contextmanager
def _suppress_dependency_http_logs() -> Iterator[None]:
    """현재 KRX request 동안에만 HTTPX/HTTPCore 원문 로그를 source logger에서 차단한다."""
    with _DEPENDENCY_HTTP_LOG_FILTER_LOCK:
        for name in _DEPENDENCY_HTTP_LOGGER_NAMES:
            logging.getLogger(name).addFilter(_DEPENDENCY_HTTP_LOG_FILTER)
    token = _DEPENDENCY_HTTP_LOG_GUARD.set(True)
    try:
        yield
    finally:
        _DEPENDENCY_HTTP_LOG_GUARD.reset(token)


class KrxCredentialError(RuntimeError):
    """인증값·provider 원문·credential-bearing request를 제외한 안정적인 오류다."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _CredentialSettings(BaseSettings):
    """KRX 인증키는 physical send 직전에만 private 설정에서 읽는다."""

    model_config = SettingsConfigDict(
        env_file=(_REPOSITORY_ROOT / ".env",),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    krx_openapi_auth_key: SecretStr = Field(repr=False, exclude=True)


def _read_credential() -> SecretStr:
    """ignored root env에서 인증키를 읽고 설정·경로·원문은 오류에 남기지 않는다."""
    try:
        settings = _CredentialSettings()  # type: ignore[call-arg]
    except ValidationError:
        raise KrxCredentialError("authentication_unavailable") from None
    return settings.krx_openapi_auth_key


class _CredentialTransport(httpx.BaseTransport):
    """검증된 KRX GET의 quota 예약 뒤 실제 send 동안에만 AUTH_KEY를 부착한다."""

    def __init__(
        self,
        inner: httpx.BaseTransport,
        *,
        quota: _QuotaReservation,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
        quota_wait_sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_response_bytes <= 0 or max_response_bytes > _MAX_RESPONSE_BYTES:
            raise ValueError("KRX response byte limit is out of bounds")
        self._inner = inner
        self._quota = quota
        self._max_response_bytes = max_response_bytes
        self._quota_wait_sleep = quota_wait_sleep
        self._origin = httpx.URL(KRX_OPEN_API_ORIGIN)
        self._physical_attempt_count = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """request 검증 후 quota→credential→send하며 모든 종료 경로에서 header를 복원한다."""
        self._validate_request(request)
        deadline = _request_deadline(request)
        _require_deadline_remaining(deadline)
        original_headers = httpx.Headers(request.headers)
        credential: SecretStr | None = None
        material = ""
        provider_response: httpx.Response | None = None
        sanitized_response: httpx.Response | None = None
        transport_failure_code = ""
        sanitization_code = ""
        cleanup_failed = False
        try:
            self._reserve_quota(deadline=deadline)
            try:
                credential = _read_credential()
                material = _credential_header_value(credential)
                request.headers[_AUTH_HEADER] = material
            except KrxCredentialError:
                raise
            except Exception:
                raise KrxCredentialError("authentication_unavailable") from None
            # credential 저장소 접근 중 deadline이 끝나면 provider socket으로 넘기지 않는다.
            _require_deadline_remaining(deadline)
            self._physical_attempt_count += 1
            try:
                provider_response = self._inner.handle_request(request)
            except Exception as error:
                # 문자열·request·cause는 폐기하고 HTTPX의 공개 예외 타입만 안정 코드로 축약한다.
                transport_failure_code = _safe_transport_failure_code(error)
            if provider_response is not None:
                try:
                    sanitized_response = _scrub_response(
                        provider_response,
                        request=request,
                        credential=material,
                        max_response_bytes=self._max_response_bytes,
                        deadline=deadline,
                    )
                except KrxCredentialError as error:
                    sanitization_code = error.code
                except Exception:
                    sanitization_code = "response_unavailable"
        finally:
            try:
                if provider_response is not None:
                    try:
                        provider_response.close()
                    except Exception:
                        cleanup_failed = True
            finally:
                provider_response = None
                request.headers.clear()
                request.headers.update(original_headers)
                material = ""
                credential = None

        if transport_failure_code:
            raise KrxCredentialError(transport_failure_code) from None
        if sanitization_code:
            raise KrxCredentialError(sanitization_code) from None
        if cleanup_failed or sanitized_response is None:
            raise KrxCredentialError("response_unavailable") from None
        return sanitized_response

    @property
    def physical_attempt_count(self) -> int:
        """provider transport에 실제 handoff한 physical attempt 누계를 반환한다."""
        return self._physical_attempt_count

    def close(self) -> None:
        self._inner.close()

    def _reserve_quota(self, *, deadline: float | None) -> None:
        attempt_id = str(uuid4())
        while True:
            _require_deadline_remaining(deadline)
            try:
                self._quota.reserve(attempt_id=attempt_id)
                # Redis round trip 중 만료된 attempt는 인증값을 읽지 않는다.
                _require_deadline_remaining(deadline)
                return
            except QuotaWaitError as error:
                wait_seconds = error.retry_after_ms / 1_000
                if deadline is not None and time.monotonic() + wait_seconds >= deadline:
                    raise KrxCredentialError("logical_deadline_exceeded") from None
                try:
                    self._quota_wait_sleep(wait_seconds)
                except Exception:
                    raise KrxCredentialError("quota_wait_unavailable") from None

    def _validate_request(self, request: httpx.Request) -> None:
        url = request.url
        expected_host = self._origin.host
        if (
            url.scheme != self._origin.scheme
            or url.host != self._origin.host
            or url.port != self._origin.port
            or url.userinfo
            or request.headers.get_list("host") != [expected_host]
        ):
            raise KrxCredentialError("origin_not_allowed")
        if request.method != "GET":
            raise KrxCredentialError("request_not_allowed")
        if url.path not in _ALLOWED_PATHS:
            raise KrxCredentialError("path_not_allowed")
        if _has_sensitive_caller_header(request.headers):
            raise KrxCredentialError("caller_auth_header_not_allowed")
        if not _has_exact_canonical_request_headers(
            request.headers,
            expected_host=expected_host,
        ):
            raise KrxCredentialError("request_not_allowed")
        if any(name not in {"timeout", _LOGICAL_DEADLINE_EXTENSION} for name in request.extensions):
            raise KrxCredentialError("request_not_allowed")
        pairs = url.params.multi_items()
        if (
            url.fragment
            or len(pairs) != 1
            or pairs[0][0] != "basDd"
            or not _is_canonical_date(pairs[0][1])
        ):
            raise KrxCredentialError("query_not_allowed")


def _safe_transport_failure_code(error: Exception) -> str:
    """credential-bearing 예외의 문자열·원인 사슬을 읽지 않고 공개 HTTPX 타입만 분류한다."""
    if isinstance(error, httpx.ConnectTimeout):
        return "connect_timeout"
    if isinstance(error, httpx.ReadTimeout):
        return "read_timeout"
    if isinstance(error, httpx.WriteTimeout):
        return "write_timeout"
    if isinstance(error, httpx.PoolTimeout):
        return "pool_timeout"
    if isinstance(error, httpx.ConnectError):
        return "connect_unavailable"
    if isinstance(error, httpx.ReadError):
        return "read_unavailable"
    if isinstance(error, httpx.WriteError):
        return "write_unavailable"
    if isinstance(error, httpx.ProtocolError):
        return "protocol_unavailable"
    return "transport_unavailable"


def _has_sensitive_caller_header(headers: httpx.Headers) -> bool:
    for name in headers:
        normalized = "".join(character for character in name.lower() if character.isalnum())
        if any(
            marker in normalized
            for marker in ("key", "secret", "token", "auth", "credential", "cookie", "proxy")
        ):
            return True
    return False


def _canonical_client_headers() -> dict[str, str]:
    """HTTPX 기본값과 무관한 source-controlled provider header를 반환한다."""
    return dict(_CANONICAL_CLIENT_HEADER_ITEMS)


def _canonical_request_headers() -> dict[str, str]:
    """direct transport 검증도 production client와 같은 canonical header를 사용하게 한다."""
    headers = {"Host": httpx.URL(KRX_OPEN_API_ORIGIN).host}
    headers.update(_canonical_client_headers())
    return headers


def _has_exact_canonical_request_headers(
    headers: httpx.Headers,
    *,
    expected_host: str,
) -> bool:
    expected = [("host", expected_host)] + [
        (name.lower(), value) for name, value in _CANONICAL_CLIENT_HEADER_ITEMS
    ]
    actual = [(name.lower(), value) for name, value in headers.multi_items()]
    return len(actual) == len(expected) and all(actual.count(item) == 1 for item in expected)


def _is_canonical_date(value: str) -> bool:
    if len(value) != 8 or not value.isascii() or not value.isdecimal():
        return False
    try:
        time.strptime(value, "%Y%m%d")
    except ValueError:
        return False
    return True


def _scrub_response(
    response: httpx.Response,
    *,
    request: httpx.Request,
    credential: str,
    max_response_bytes: int,
    deadline: float | None,
) -> httpx.Response:
    content = _read_limited(
        response,
        max_response_bytes=max_response_bytes,
        deadline=deadline,
    )
    if _response_contains_credential(
        content,
        headers=response.headers,
        credential=credential,
    ):
        raise KrxCredentialError("response_unavailable")
    return httpx.Response(
        response.status_code,
        headers=_canonical_json_response_headers(response.headers),
        content=content,
        extensions={
            _SAFE_RESPONSE_METADATA_EXTENSION: _safe_response_metadata(
                content,
                headers=response.headers,
            )
        },
        request=request,
    )


def _read_limited(
    response: httpx.Response,
    *,
    max_response_bytes: int,
    deadline: float | None,
) -> bytes:
    _require_deadline_remaining(deadline)
    declared = response.headers.get("content-length")
    if declared is not None:
        normalized = declared.strip()
        if not normalized.isascii() or not normalized.isdecimal():
            raise KrxCredentialError("response_unavailable")
        if int(normalized) > max_response_bytes:
            raise KrxCredentialError("response_too_large")
    chunks: list[bytes] = []
    size = 0
    try:
        for chunk in response.iter_bytes():
            _require_deadline_remaining(deadline)
            size += len(chunk)
            if size > max_response_bytes:
                raise KrxCredentialError("response_too_large")
            chunks.append(chunk)
    except KrxCredentialError:
        raise
    except Exception as error:
        code = _safe_transport_failure_code(error)
        if code in {"read_timeout", "read_unavailable", "protocol_unavailable"}:
            raise KrxCredentialError(code) from None
        raise KrxCredentialError("response_unavailable") from None
    return b"".join(chunks)


def _canonical_json_response_headers(headers: httpx.Headers) -> list[tuple[str, str]]:
    content_type_class = _content_type_class(headers)
    if content_type_class not in {"application_json", "structured_json"}:
        return []
    return [("content-type", "application/json")]


def _safe_response_metadata(
    content: bytes,
    *,
    headers: httpx.Headers,
) -> KrxSafeResponseMetadata:
    """provider 원문을 보존하지 않고 media/body/encoding 파생 분류만 생성한다."""
    utf8_valid = True
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        utf8_valid = False
    return KrxSafeResponseMetadata(
        content_type_class=_content_type_class(headers),
        body_class=_body_class(content, utf8_valid=utf8_valid),
        body_size_bucket=_body_size_bucket(len(content)),
        utf8_valid=utf8_valid,
        utf8_bom_present=content.startswith(b"\xef\xbb\xbf"),
    )


def _content_type_class(headers: httpx.Headers) -> str:
    values = headers.get_list("content-type")
    if not values:
        return "missing"
    if len(values) != 1:
        return "multiple"
    media_type = values[0].split(";", 1)[0].strip().lower()
    if media_type == "application/json":
        return "application_json"
    if media_type.endswith("+json"):
        return "structured_json"
    return "other"


def _body_class(content: bytes, *, utf8_valid: bool) -> str:
    if not content:
        return "empty"
    probe = content[3:] if content.startswith(b"\xef\xbb\xbf") else content
    probe = probe.lstrip(b" \t\r\n")
    lowered = probe[:64].lower()
    if probe.startswith((b"{", b"[")):
        return "json_candidate"
    if lowered.startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
        return "html_like"
    if utf8_valid:
        return "text_like"
    return "opaque"


def _body_size_bucket(size: int) -> str:
    if size == 0:
        return "empty"
    if size <= 4 * 1024:
        return "1_4k"
    if size <= 64 * 1024:
        return "4k_64k"
    if size <= 1024 * 1024:
        return "64k_1m"
    return "1m_4m"


def _response_contains_credential(
    content: bytes,
    *,
    headers: httpx.Headers,
    credential: str,
) -> bool:
    variants = tuple(
        candidate
        for candidate in (
            credential,
            quote(credential, safe=""),
            quote_plus(credential),
        )
        if candidate
    )
    if any(candidate.encode() in content for candidate in variants):
        return True
    if any(candidate in value for candidate in variants for _, value in headers.multi_items()):
        return True
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
        return False
    stack: list[object] = [payload]
    visited = 0
    while stack:
        visited += 1
        if visited > _MAX_DECODED_JSON_NODES:
            return True
        value = stack.pop()
        if isinstance(value, str) and any(candidate in value for candidate in variants):
            return True
        if isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, dict):
            stack.extend(value.keys())
            stack.extend(value.values())
    return False


def _credential_header_value(value: SecretStr) -> str:
    try:
        material = value.get_secret_value()
    except Exception:
        raise KrxCredentialError("authentication_unavailable") from None
    try:
        encoded_length = len(material.encode("ascii"))
    except UnicodeEncodeError:
        raise KrxCredentialError("authentication_unavailable") from None
    if (
        not material
        or encoded_length > _MAX_CREDENTIAL_HEADER_BYTES
        or any(not 0x21 <= ord(character) <= 0x7E for character in material)
    ):
        raise KrxCredentialError("authentication_unavailable")
    return material


def _request_deadline(request: httpx.Request) -> float | None:
    value = request.extensions.get(_LOGICAL_DEADLINE_EXTENSION)
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise KrxCredentialError("request_not_allowed")
    return float(value)


def _require_deadline_remaining(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise KrxCredentialError("logical_deadline_exceeded")
