from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, quote_plus
from uuid import uuid4

import httpx
from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.data._shared.redis_quota import QuotaWaitError
from app.data.naver.errors import NaverError
from app.data.naver.policy import request_policy_for, validate_news_query
from app.data.naver.profiles import (
    API_HUB_PROFILE,
    LEGACY_PROFILE,
    NaverProfile,
    profile_for,
    require_canonical_profile,
)
from app.data.naver.sanitizer import NaverSanitizationError, sanitize_news_text
from app.data.naver.url_metadata import normalize_metadata_url


_REPOSITORY_ROOT = Path(__file__).resolve().parents[6]
_ALL_AUTH_HEADERS = frozenset(
    header.lower()
    for profile in (LEGACY_PROFILE, API_HUB_PROFILE)
    for header in profile.auth_headers
)
_DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_CREDENTIAL_HEADER_BYTES = 512
_MAX_DECODED_JSON_NODES = 100_000
_LOGICAL_DEADLINE_EXTENSION = "s1.3.naver.logical_deadline"
_CANONICAL_CLIENT_HEADER_ITEMS = (
    ("Accept", "application/json"),
    ("Accept-Encoding", "identity"),
    ("Connection", "keep-alive"),
    ("User-Agent", "capstone-ai-trading-coach-s1.3"),
)


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
        require_canonical_profile(profile)
    except ValueError:
        raise NaverCredentialError("profile_invalid") from None
    try:
        settings = _CredentialSettings()
    except ValidationError:
        raise NaverCredentialError("authentication_unavailable") from None
    if profile is LEGACY_PROFILE:
        identifier = settings.naver_client_id
        secret = settings.naver_client_secret
    elif profile is API_HUB_PROFILE:
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
        enforce_lifecycle: bool = True,
        quota_wait_sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_response_bytes <= 0 or max_response_bytes > _DEFAULT_MAX_RESPONSE_BYTES:
            raise ValueError("Naver response byte limit is out of bounds")
        require_canonical_profile(profile)
        self._inner = inner
        self._profile = profile
        self._origin = httpx.URL(profile.origin)
        self._quota = quota
        self._max_response_bytes = max_response_bytes
        self._enforce_lifecycle = enforce_lifecycle
        self._quota_wait_sleep = quota_wait_sleep
        self._physical_attempt_count = 0

    @property
    def quota(self) -> _QuotaReservation:
        """429 cooldown을 동일 opaque quota scope에 기록할 private runtime 경계를 반환한다."""
        return self._quota

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """request를 검증한 뒤 quota→credential→send하고 모든 exit에서 header를 복원한다."""
        self._validate_request(request)
        self._require_active_profile()
        deadline = _request_deadline(request)
        _require_deadline_remaining(deadline)
        original_headers = httpx.Headers(request.headers)
        credentials: _Credentials | None = None
        identifier = ""
        secret = ""
        response: httpx.Response | None = None
        sanitized_response: httpx.Response | None = None
        transport_failed = False
        sanitization_failed = False
        sanitization_error_code: str | None = None
        response_cleanup_failed = False
        try:
            self._reserve_quota(deadline=deadline)
            try:
                credentials = _read_credentials(self._profile)
                identifier = _credential_header_value(credentials.identifier)
                secret = _credential_header_value(credentials.secret)
                request.headers[self._profile.auth_headers[0]] = identifier
                request.headers[self._profile.auth_headers[1]] = secret
            except NaverCredentialError:
                raise
            except Exception:
                raise NaverCredentialError("authentication_unavailable") from None
            try:
                # credential store·header 준비 중 만료된 요청은 실제 provider socket으로 넘기지 않는다.
                _require_deadline_remaining(deadline)
                # quota 예약은 refund하지 않지만 physical attempt는 provider transport handoff 직전에만 기록한다.
                self._physical_attempt_count += 1
                response = self._inner.handle_request(request)
            except (httpx.TimeoutException, httpx.TransportError, OSError):
                transport_failed = True
            if response is not None:
                if response.status_code == 429:
                    # body read 결과와 무관하게 provider 429 획득 즉시 deployment cooldown을 공유한다.
                    self._quota.activate_cooldown(seconds=60)
                    # 이미 taxonomy가 확정됐으므로 untrusted 429 body/header는 읽지 않고 폐기한다.
                    sanitized_response = httpx.Response(429, content=b"{}")
                else:
                    try:
                        sanitized_response = _scrub_response(
                            response,
                            candidates=(identifier, secret),
                            max_response_bytes=self._max_response_bytes,
                            deadline=deadline,
                        )
                    except (httpx.TimeoutException, httpx.TransportError, OSError):
                        transport_failed = True
                    except NaverCredentialError as error:
                        sanitization_error_code = error.code
                    except Exception:
                        sanitization_failed = True
        finally:
            try:
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        response_cleanup_failed = True
            finally:
                response = None
                request.headers.clear()
                request.headers.update(original_headers)
                credentials = None
                identifier = ""
                secret = ""

        if transport_failed:
            raise NaverCredentialError("transport_unavailable", retryable=True) from None
        if sanitization_error_code is not None:
            raise NaverCredentialError(sanitization_error_code) from None
        if response_cleanup_failed or sanitization_failed or sanitized_response is None:
            raise NaverCredentialError("response_unavailable") from None
        return sanitized_response

    @property
    def physical_attempt_count(self) -> int:
        """provider transport에 실제로 handoff한 physical attempt 누계를 반환한다."""
        return self._physical_attempt_count

    def close(self) -> None:
        self._inner.close()

    def _reserve_quota(self, *, deadline: float | None) -> None:
        attempt_id = str(uuid4())
        while True:
            self._require_active_profile()
            _require_deadline_remaining(deadline)
            try:
                self._quota.reserve(attempt_id=attempt_id)
                # Redis 왕복 중 deadline이 끝났다면 credential·physical send 단계로 넘기지 않는다.
                _require_deadline_remaining(deadline)
                return
            except QuotaWaitError as error:
                wait_seconds = error.retry_after_ms / 1_000
                if deadline is not None and time.monotonic() + wait_seconds >= deadline:
                    raise NaverCredentialError("logical_deadline_exceeded") from None
                try:
                    self._quota_wait_sleep(wait_seconds)
                except Exception:
                    raise NaverCredentialError("quota_wait_unavailable") from None

    def _require_active_profile(self) -> None:
        if not self._enforce_lifecycle:
            return
        try:
            active_profile = profile_for(self._profile.name)
        except ValueError:
            raise NaverCredentialError("profile_unavailable") from None
        if active_profile is not self._profile:
            raise NaverCredentialError("profile_unavailable")

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
            raise NaverCredentialError("origin_not_allowed")
        if request.method != "GET" or url.path != self._profile.path:
            raise NaverCredentialError("path_not_allowed")
        if _has_sensitive_caller_header(request.headers):
            raise NaverCredentialError("caller_auth_header_not_allowed")
        if not _has_exact_canonical_request_headers(request.headers, expected_host=expected_host):
            raise NaverCredentialError("request_not_allowed")
        if any(name not in {"timeout", _LOGICAL_DEADLINE_EXTENSION} for name in request.extensions):
            raise NaverCredentialError("request_not_allowed")
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


def _has_sensitive_caller_header(headers: httpx.Headers) -> bool:
    for name in headers:
        lowered = name.lower()
        normalized = "".join(character for character in lowered if character.isalnum())
        if lowered in _ALL_AUTH_HEADERS or any(
            marker in normalized
            for marker in ("key", "secret", "token", "auth", "credential", "cookie", "proxy")
        ):
            return True
    return False


def _canonical_client_headers() -> dict[str, str]:
    """HTTPX version과 무관한 source-controlled provider request header를 반환한다."""
    return dict(_CANONICAL_CLIENT_HEADER_ITEMS)


def _canonical_request_headers(origin: str) -> dict[str, str]:
    """direct transport test도 production client와 동일한 canonical header를 사용하게 한다."""
    headers = {"Host": httpx.URL(origin).host}
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


def _scrub_response(
    response: httpx.Response,
    *,
    candidates: tuple[str, str],
    max_response_bytes: int,
    deadline: float | None,
) -> httpx.Response:
    content = _read_limited(
        response,
        max_response_bytes=max_response_bytes,
        deadline=deadline,
    )
    encoded_candidates = tuple(
        candidate
        for value in candidates
        for candidate in {value, quote(value, safe=""), quote_plus(value)}
        if candidate
    )
    for candidate in encoded_candidates:
        content = content.replace(candidate.encode(), b"[redacted]")
    if _decoded_json_contains_candidate(content, candidates=candidates):
        raise NaverCredentialError("response_unavailable")

    headers = _canonical_json_response_headers(response.headers)
    return httpx.Response(response.status_code, headers=headers, content=content)


def _canonical_json_response_headers(headers: httpx.Headers) -> list[tuple[str, str]]:
    values = headers.get_list("content-type")
    if len(values) != 1 or values[0].split(";", 1)[0].strip().lower() != "application/json":
        return []
    return [("content-type", "application/json")]


def _read_limited(
    response: httpx.Response,
    *,
    max_response_bytes: int,
    deadline: float | None,
) -> bytes:
    _require_deadline_remaining(deadline)
    declared_length = response.headers.get("content-length")
    if declared_length is not None:
        normalized_length = declared_length.strip()
        if normalized_length.isascii() and normalized_length.isdecimal():
            if int(normalized_length) > max_response_bytes:
                raise NaverCredentialError("response_too_large")
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        _require_deadline_remaining(deadline)
        size += len(chunk)
        if size > max_response_bytes:
            raise NaverCredentialError("response_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _credential_header_value(value: SecretStr) -> str:
    try:
        material = value.get_secret_value()
    except Exception:
        raise NaverCredentialError("authentication_unavailable") from None
    if (
        not material
        or len(material) > _MAX_CREDENTIAL_HEADER_BYTES
        or any(not 0x21 <= ord(character) <= 0x7E for character in material)
    ):
        raise NaverCredentialError("authentication_unavailable")
    return material


def _decoded_json_contains_candidate(content: bytes, *, candidates: tuple[str, str]) -> bool:
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
            if _normalized_string_contains_candidate(value, candidates=candidates):
                return True
        elif isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, dict):
            stack.extend(value.keys())
            stack.extend(value.values())
    return False


def _normalized_string_contains_candidate(value: str, *, candidates: tuple[str, str]) -> bool:
    variants = [value]
    try:
        variants.append(
            sanitize_news_text(
                value,
                max_code_points=2_048,
                max_utf8_bytes=8_192,
            )
        )
    except (NaverSanitizationError, ValueError):
        pass
    normalized_url = normalize_metadata_url(value)
    if normalized_url is not None:
        variants.append(normalized_url)
    return any(candidate in variant for candidate in candidates for variant in variants)


def _request_deadline(request: httpx.Request) -> float | None:
    value = request.extensions.get(_LOGICAL_DEADLINE_EXTENSION)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise NaverCredentialError("logical_deadline_exceeded")
    return float(value)


def _require_deadline_remaining(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise NaverCredentialError("logical_deadline_exceeded")
