from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, quote_plus
from uuid import uuid4

import httpx
from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.data.naver.errors import NaverError
from app.data.naver.policy import request_policy_for, validate_news_query
from app.data.naver.profiles import API_HUB_PROFILE, LEGACY_PROFILE, NaverProfile


_REPOSITORY_ROOT = Path(__file__).resolve().parents[6]
_ALL_AUTH_HEADERS = frozenset(
    header.lower()
    for profile in (LEGACY_PROFILE, API_HUB_PROFILE)
    for header in profile.auth_headers
)
_DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024


class NaverCredentialError(NaverError):
    """Credential·request·provider 원문을 노출하지 않는 고정 transport 오류다."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class _Credentials:
    identifier: SecretStr
    secret: SecretStr


class _QuotaReservation(Protocol):
    def reserve(self, *, attempt_id: str) -> None: ...

    def activate_cooldown(self, *, seconds: int) -> None: ...


class _CredentialSettings(BaseSettings):
    """Naver credential은 최종 send transport 내부에서만 읽고 직렬화하지 않는다."""

    model_config = SettingsConfigDict(
        env_file=(_REPOSITORY_ROOT / ".env",),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    naver_client_id: SecretStr | None = Field(default=None, repr=False, exclude=True)
    naver_client_secret: SecretStr | None = Field(default=None, repr=False, exclude=True)
    naver_api_hub_api_key_id: SecretStr | None = Field(default=None, repr=False, exclude=True)
    naver_api_hub_api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)


def _read_credentials(profile: NaverProfile) -> _Credentials:
    try:
        settings = _CredentialSettings()
    except ValidationError:
        raise NaverCredentialError("authentication_unavailable") from None
    if profile is LEGACY_PROFILE or profile.name == "legacy":
        identifier = settings.naver_client_id
        secret = settings.naver_client_secret
    elif profile is API_HUB_PROFILE or profile.name == "api-hub":
        identifier = settings.naver_api_hub_api_key_id
        secret = settings.naver_api_hub_api_key
    else:
        raise NaverCredentialError("profile_invalid")
    if identifier is None or secret is None:
        raise NaverCredentialError("authentication_unavailable")
    if not identifier.get_secret_value().strip() or not secret.get_secret_value().strip():
        raise NaverCredentialError("authentication_unavailable")
    return _Credentials(identifier=identifier, secret=secret)


class _CredentialTransport(httpx.BaseTransport):
    """고정 News request의 실제 send 동안에만 profile 인증 header를 부착한다."""

    def __init__(
        self,
        inner: httpx.BaseTransport,
        *,
        profile: NaverProfile,
        quota: _QuotaReservation,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if max_response_bytes <= 0 or max_response_bytes > _DEFAULT_MAX_RESPONSE_BYTES:
            raise ValueError("Naver response byte limit is out of bounds")
        self._inner = inner
        self._profile = profile
        self._origin = httpx.URL(profile.origin)
        self._quota = quota
        self._max_response_bytes = max_response_bytes
        self._physical_attempt_count = 0

    @property
    def quota(self) -> _QuotaReservation:
        """429 cooldown을 동일 opaque quota scope에 기록할 private runtime 경계를 반환한다."""
        return self._quota

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """request를 검증한 뒤 quota→credential→send하고 모든 exit에서 header를 복원한다."""
        self._validate_request(request)
        original_headers = httpx.Headers(request.headers)
        credentials: _Credentials | None = None
        identifier = ""
        secret = ""
        response: httpx.Response | None = None
        sanitized_response: httpx.Response | None = None
        transport_failed = False
        sanitization_failed = False
        try:
            self._quota.reserve(attempt_id=str(uuid4()))
            self._physical_attempt_count += 1
            credentials = _read_credentials(self._profile)
            identifier = credentials.identifier.get_secret_value()
            secret = credentials.secret.get_secret_value()
            request.headers[self._profile.auth_headers[0]] = identifier
            request.headers[self._profile.auth_headers[1]] = secret
            try:
                response = self._inner.handle_request(request)
            except (httpx.TimeoutException, httpx.TransportError, OSError):
                transport_failed = True
            if response is not None:
                try:
                    sanitized_response = _scrub_response(
                        response,
                        candidates=(identifier, secret),
                        max_response_bytes=self._max_response_bytes,
                    )
                except Exception:
                    sanitization_failed = True
        finally:
            if response is not None:
                response.close()
            response = None
            request.headers.clear()
            request.headers.update(original_headers)
            credentials = None
            identifier = ""
            secret = ""

        if transport_failed:
            raise NaverCredentialError("transport_unavailable", retryable=True) from None
        if sanitization_failed or sanitized_response is None:
            raise NaverCredentialError("response_unavailable") from None
        return sanitized_response

    @property
    def physical_attempt_count(self) -> int:
        """quota 예약에 성공해 credential 단계까지 진입한 attempt 누계를 반환한다."""
        return self._physical_attempt_count

    def close(self) -> None:
        self._inner.close()

    def _validate_request(self, request: httpx.Request) -> None:
        url = request.url
        if (
            url.scheme != self._origin.scheme
            or url.host != self._origin.host
            or url.port != self._origin.port
        ):
            raise NaverCredentialError("origin_not_allowed")
        if request.method != "GET" or url.path != self._profile.path:
            raise NaverCredentialError("path_not_allowed")
        if any(header.lower() in _ALL_AUTH_HEADERS for header in request.headers):
            raise NaverCredentialError("caller_auth_header_not_allowed")
        policy = request_policy_for(self._profile.provider_profile)
        pairs = url.params.multi_items()
        if (
            url.fragment
            or len(pairs) != len(policy.allowed_query_keys)
            or {name for name, _ in pairs} != policy.allowed_query_keys
            or url.params.get("start") != "1"
            or url.params.get("sort") != "date"
            or any(url.params.get(name) != value for name, value in policy.static_query.items())
        ):
            raise NaverCredentialError("query_not_allowed")
        try:
            validate_news_query(url.params["query"])
            display = int(url.params["display"])
        except (KeyError, TypeError, ValueError):
            raise NaverCredentialError("query_not_allowed") from None
        if str(display) != url.params["display"] or not 1 <= display <= 20:
            raise NaverCredentialError("query_not_allowed")


def _scrub_response(
    response: httpx.Response,
    *,
    candidates: tuple[str, str],
    max_response_bytes: int,
) -> httpx.Response:
    content = _read_limited(response, max_response_bytes=max_response_bytes)
    encoded_candidates = tuple(
        candidate
        for value in candidates
        for candidate in {value, quote(value, safe=""), quote_plus(value)}
        if candidate
    )
    for candidate in encoded_candidates:
        content = content.replace(candidate.encode(), b"[redacted]")

    headers: list[tuple[str, str]] = []
    for name, value in response.headers.multi_items():
        if name.lower() in {"content-encoding", "content-length", "transfer-encoding"}:
            continue
        sanitized = value
        for candidate in encoded_candidates:
            sanitized = sanitized.replace(candidate, "[redacted]")
        headers.append((name, sanitized))
    return httpx.Response(response.status_code, headers=headers, content=content)


def _read_limited(response: httpx.Response, *, max_response_bytes: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > max_response_bytes:
            raise NaverCredentialError("response_too_large")
        chunks.append(chunk)
    return b"".join(chunks)
