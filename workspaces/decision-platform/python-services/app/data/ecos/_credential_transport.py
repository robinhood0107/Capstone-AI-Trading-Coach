from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, quote_plus
from uuid import uuid4

import httpx
from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.data.ecos.policy import (
    ECOS_KEY_SENTINEL,
    ECOS_ORIGIN,
    validate_keyless_service_path,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[6]
_ROOT_ENV_FILE = _REPOSITORY_ROOT / ".env"
_DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_DECODED_JSON_NODES = 100_000
_ECOS_DEADLINE_EXTENSION = "ecos_deadline_monotonic"
_ECOS_SAFE_RESPONSE_EXTENSION = "s1.3.ecos.safe_response"
_CANONICAL_CLIENT_HEADER_ITEMS = (
    ("Accept", "application/json"),
    ("Accept-Encoding", "identity"),
    ("Connection", "keep-alive"),
    ("User-Agent", "capstone-ai-trading-coach-s1.3"),
)


class _Reservation(Protocol):
    def reserve(self, *, attempt_id: str) -> None: ...


class ECOSCredentialError(RuntimeError):
    """credential-bearing request·cause를 버리고 stable ECOS transport code만 전달한다."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class _CredentialSettings(BaseSettings):
    """API key 평문은 physical send attempt 안에서만 일시적으로 materialize한다."""

    model_config = SettingsConfigDict(
        env_file=(_ROOT_ENV_FILE,),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ecos_api_key: SecretStr = Field(repr=False, exclude=True)

    @field_validator("ecos_api_key")
    @classmethod
    def _validate_key(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if (
            not raw
            or raw != raw.strip()
            or len(raw) > 30
            or len(raw.encode("utf-8")) > 120
            or any(character in raw for character in ("/", "\\", "?", "#"))
        ):
            raise ValueError("invalid ECOS authentication material")
        return value


def _read_credential() -> SecretStr:
    """ignored root env에서 ECOS API key를 읽되 값·변수명·파일 경로를 오류에 남기지 않는다."""
    settings: _CredentialSettings | None = None
    try:
        settings = _CredentialSettings()  # type: ignore[call-arg]
    except ValidationError:
        pass
    if settings is None:
        raise ECOSCredentialError("authentication_unavailable")
    return settings.ecos_api_key


class _CredentialTransport(httpx.BaseTransport):
    """quota 예약 뒤 고정 origin의 send 순간에만 path key를 삽입하고 항상 원복한다."""

    def __init__(
        self,
        inner: httpx.BaseTransport,
        *,
        quota: _Reservation,
        credential_reader: Callable[[], SecretStr] | None = None,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_response_bytes <= 0 or max_response_bytes > _DEFAULT_MAX_RESPONSE_BYTES:
            raise ValueError("ECOS response byte limit is out of bounds")
        self._inner = inner
        self._quota = quota
        self._credential_reader = credential_reader
        self._max_response_bytes = max_response_bytes
        self._monotonic = monotonic
        self._physical_attempt_count = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        _validate_outbound_request(request)
        deadline = _request_deadline(request)

        # 실패한 attempt도 환불하지 않도록 credential을 읽기 전에 원자 quota slot부터 예약한다.
        self._quota.reserve(attempt_id=str(uuid4()))
        # Redis 왕복 중 deadline이 끝났다면 credential·physical send 단계로 넘기지 않는다.
        _ensure_before_deadline(deadline, monotonic=self._monotonic)

        reader = self._credential_reader or _read_credential
        credential: SecretStr | None = None
        try:
            credential = reader()
        except ECOSCredentialError:
            raise
        except Exception:
            pass
        if not isinstance(credential, SecretStr):
            raise ECOSCredentialError("authentication_unavailable")

        original_url = request.url
        value = ""
        provider_response: httpx.Response | None = None
        sanitized_response: httpx.Response | None = None
        transport_failed = False
        sanitization_failure_code = ""
        try:
            value = _materialize_runtime_key(credential)
            request.url = _credential_url(original_url, value)
            # credential store·URL 준비 중 만료된 요청은 실제 provider socket으로 넘기지 않는다.
            _ensure_before_deadline(deadline, monotonic=self._monotonic)
            self._physical_attempt_count += 1
            try:
                provider_response = self._inner.handle_request(request)
            except Exception:
                # inner exception은 credential-bearing Request/URL을 보존할 수 있어 cause까지 폐기한다.
                transport_failed = True
            if provider_response is not None:
                try:
                    sanitized_response = _scrub_response(
                        provider_response,
                        credential=value,
                        max_response_bytes=self._max_response_bytes,
                        deadline=deadline,
                        monotonic=self._monotonic,
                    )
                except (httpx.TimeoutException, httpx.TransportError):
                    transport_failed = True
                except ECOSCredentialError as error:
                    sanitization_failure_code = error.code
                except Exception:
                    sanitization_failure_code = "response_unavailable"
        finally:
            try:
                if provider_response is not None:
                    try:
                        provider_response.close()
                    except Exception:
                        # close 오류에도 keyless URL 복원과 secret 참조 제거를 반드시 계속한다.
                        if not transport_failed and not sanitization_failure_code:
                            sanitization_failure_code = "response_unavailable"
            finally:
                provider_response = None
                try:
                    request.url = original_url
                finally:
                    value = ""
                    credential = None

        if transport_failed:
            raise ECOSCredentialError("transport_unavailable", retryable=True) from None
        if sanitization_failure_code or sanitized_response is None:
            raise ECOSCredentialError(sanitization_failure_code or "response_unavailable") from None
        return sanitized_response

    @property
    def physical_attempt_count(self) -> int:
        """credential 준비를 마치고 provider transport에 전달한 physical attempt 수를 반환한다."""
        return self._physical_attempt_count

    def close(self) -> None:
        self._inner.close()


def _validate_outbound_request(request: httpx.Request) -> None:
    origin = httpx.URL(ECOS_ORIGIN)
    url = request.url
    expected_host = origin.host
    if (
        url.scheme != origin.scheme
        or url.host != origin.host
        or url.port != origin.port
        or url.userinfo
        or request.headers.get_list("host") != [expected_host]
    ):
        raise ECOSCredentialError("origin_not_allowed")
    if (
        request.method != "GET"
        or url.query
        or url.fragment
        or not _has_exact_canonical_request_headers(request.headers, expected_host=expected_host)
        or any(name not in {"timeout", _ECOS_DEADLINE_EXTENSION} for name in request.extensions)
    ):
        raise ECOSCredentialError("request_not_allowed")
    try:
        validate_keyless_service_path(url.path)
    except ValueError:
        raise ECOSCredentialError("path_not_allowed") from None


def _request_deadline(request: httpx.Request) -> float | None:
    value = request.extensions.get(_ECOS_DEADLINE_EXTENSION)
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ECOSCredentialError("request_not_allowed")
    return float(value)


def _canonical_client_headers() -> dict[str, str]:
    """HTTPX version과 무관한 source-controlled provider request header를 반환한다."""
    return dict(_CANONICAL_CLIENT_HEADER_ITEMS)


def _canonical_request_headers() -> dict[str, str]:
    """direct transport test도 production client와 동일한 canonical header를 사용하게 한다."""
    headers = {"Host": httpx.URL(ECOS_ORIGIN).host}
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


def _validate_runtime_key(value: str) -> None:
    encoded_length: int | None = None
    try:
        encoded_length = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        pass
    if (
        not value
        or value != value.strip()
        or len(value) > 30
        or encoded_length is None
        or encoded_length > 120
        or any(character in value for character in ("/", "\\", "?", "#"))
    ):
        raise ECOSCredentialError("authentication_unavailable")


def _materialize_runtime_key(credential: SecretStr) -> str:
    value: str | None = None
    try:
        value = credential.get_secret_value()
    except Exception:
        pass
    if value is None:
        raise ECOSCredentialError("authentication_unavailable")
    _validate_runtime_key(value)
    return value


def _credential_url(original: httpx.URL, credential: str) -> httpx.URL:
    raw_path = original.raw_path.decode("ascii")
    if raw_path.count(ECOS_KEY_SENTINEL) != 1:
        raise ECOSCredentialError("path_not_allowed")
    encoded = quote(credential, safe="")
    return original.copy_with(raw_path=raw_path.replace(ECOS_KEY_SENTINEL, encoded).encode("ascii"))


def _scrub_response(
    response: httpx.Response,
    *,
    credential: str,
    max_response_bytes: int,
    deadline: float | None,
    monotonic: Callable[[], float],
) -> httpx.Response:
    _ensure_before_deadline(deadline, monotonic=monotonic)
    _ensure_declared_length(response, max_response_bytes=max_response_bytes)
    content = _read_limited(
        response,
        max_response_bytes=max_response_bytes,
        deadline=deadline,
        monotonic=monotonic,
    )
    response_bytes = len(content)
    content_type_class = _content_type_class(response.headers)
    candidates = tuple(
        sorted(
            {
                candidate
                for candidate in {credential, quote(credential, safe=""), quote_plus(credential)}
                if candidate
            },
            key=lambda candidate: (-len(candidate), candidate),
        )
    )
    scan_content = content
    for candidate in candidates:
        encoded = candidate.encode()
        content = content.replace(encoded, b"[redacted]")
        # literal echo는 기존처럼 redaction하고, 별도 copy에서 제거해 escaped echo만 검출한다.
        scan_content = scan_content.replace(encoded, b"")
    if _decoded_json_contains_candidate(scan_content, candidates=candidates):
        raise ECOSCredentialError("response_unavailable")
    _ensure_before_deadline(deadline, monotonic=monotonic)

    headers = _canonical_json_response_headers(content_type_class)
    # downstream에는 원 header가 아니라 allowlist scalar 세 개만 synthetic extension으로 전달한다.
    extensions: dict[str, object] = {
        _ECOS_SAFE_RESPONSE_EXTENSION: {
            "httpStatus": response.status_code,
            "contentTypeClass": content_type_class,
            "responseBytes": response_bytes,
        }
    }

    sanitized_response = httpx.Response(
        response.status_code,
        headers=headers,
        content=content,
        extensions=extensions,
    )
    _ensure_before_deadline(deadline, monotonic=monotonic)
    return sanitized_response


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


def _canonical_json_response_headers(content_type_class: str) -> list[tuple[str, str]]:
    if content_type_class == "application_json":
        return [("content-type", "application/json")]
    if content_type_class == "structured_json":
        return [("content-type", "application/problem+json")]
    return []


def _decoded_json_contains_candidate(content: bytes, *, candidates: tuple[str, ...]) -> bool:
    """JSON escape가 decode된 뒤에도 credential 표현이 남으면 parser 이전에 fail-closed한다."""
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
        if isinstance(value, str):
            if any(candidate in value for candidate in candidates):
                return True
        elif isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, dict):
            stack.extend(value.keys())
            stack.extend(value.values())
    return False


def _read_limited(
    response: httpx.Response,
    *,
    max_response_bytes: int,
    deadline: float | None,
    monotonic: Callable[[], float],
) -> bytes:
    content: list[bytes] = []
    size = 0
    _ensure_before_deadline(deadline, monotonic=monotonic)
    for chunk in response.iter_bytes():
        _ensure_before_deadline(deadline, monotonic=monotonic)
        size += len(chunk)
        if size > max_response_bytes:
            raise ECOSCredentialError("response_too_large")
        content.append(chunk)
    return b"".join(content)


def _ensure_before_deadline(
    deadline: float | None,
    *,
    monotonic: Callable[[], float],
) -> None:
    if deadline is not None and monotonic() >= deadline:
        raise ECOSCredentialError("logical_deadline_exceeded")


def _ensure_declared_length(response: httpx.Response, *, max_response_bytes: int) -> None:
    declared = response.headers.get("content-length")
    if declared is None:
        return
    try:
        size = int(declared)
    except ValueError:
        raise ECOSCredentialError("response_unavailable") from None
    if size < 0:
        raise ECOSCredentialError("response_unavailable")
    if size > max_response_bytes:
        raise ECOSCredentialError("response_too_large")
