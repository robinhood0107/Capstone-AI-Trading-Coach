from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.data._shared.repository_root import repository_root
from typing import Any, cast
from urllib.parse import quote, quote_plus

import httpx
import redis
from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.data.kis.accounting import (
    CollectionRunRecorder,
    FailureCode,
    KISCallBudgetExceeded,
    PhysicalChannel,
)
from app.data.kis.rate_limiter import RateLimiter
from app.data.kis.settings import KISMode, KISSettings

_REPOSITORY_ROOT = repository_root(__file__, 6)
_ROOT_ENV_FILE = _REPOSITORY_ROOT / ".env"
_INTERNAL_TR_ID_HEADER = "x-kis-internal-tr-id"
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
_MAX_JSON_DEPTH = 64
_MAX_ACCESS_TOKEN_CHARS = 8_192
_MAX_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60
_REDIS_SOCKET_TIMEOUT_SECONDS = 2.0
_TOKEN_EXPIRY_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}")


class KISCredentialError(RuntimeError):
    """인증정보의 값·환경변수명·경로를 공개하지 않고 KIS 호출을 fail-closed한다."""


class KISResponseTooLargeError(RuntimeError):
    """provider 응답이 parser 안전 상한을 넘으면 비재시도로 중단한다."""


@dataclass(frozen=True, repr=False)
class _Credentials:
    app_key: SecretStr
    app_secret: SecretStr


class _CredentialSettings(BaseSettings):
    """KIS API credential은 private final-transport 모듈에서만 일시 로드한다."""

    model_config = SettingsConfigDict(
        env_file=(_ROOT_ENV_FILE,),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    kis_mock_app_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    kis_mock_app_secret: SecretStr | None = Field(default=None, repr=False, exclude=True)
    kis_live_app_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    kis_live_app_secret: SecretStr | None = Field(default=None, repr=False, exclude=True)


class _RedisCredentialSettings(BaseSettings):
    """Redis password도 connection 생성 구간 밖의 공개 설정 객체로 전달하지 않는다."""

    model_config = SettingsConfigDict(
        env_file=(_ROOT_ENV_FILE,),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: SecretStr = Field(repr=False, exclude=True)


def _read_credentials(mode: KISMode) -> _Credentials:
    try:
        settings = _CredentialSettings()
    except ValidationError:
        raise KISCredentialError("KIS authentication is unavailable") from None
    if mode == "live":
        app_key = settings.kis_live_app_key
        app_secret = settings.kis_live_app_secret
    else:
        app_key = settings.kis_mock_app_key
        app_secret = settings.kis_mock_app_secret
    if app_key is None or app_secret is None:
        raise KISCredentialError("KIS authentication is unavailable")
    if not app_key.get_secret_value().strip() or not app_secret.get_secret_value().strip():
        raise KISCredentialError("KIS authentication is unavailable")
    return _Credentials(app_key=app_key, app_secret=app_secret)


def _provider_scope(mode: KISMode) -> str:
    """단일 credential slot의 mode별 cache/REST scope를 비가역 HMAC으로 파생한다.

    실제 key를 Redis나 공개 객체에 두지 않기 위한 private 초기화 예외이며, outbound header/body에는
    각 send 직전에 다시 읽은 credential만 사용한다.
    """
    credentials = _read_credentials(mode)
    app_key = credentials.app_key.get_secret_value()
    app_secret = credentials.app_secret.get_secret_value()
    try:
        message = f"kis-provider-scope/v1:{mode}:{app_key}".encode()
        return hmac.new(app_secret.encode(), message, hashlib.sha256).hexdigest()
    finally:
        app_key = ""
        app_secret = ""


def _build_redis_client() -> redis.Redis:
    """운영 Redis credential을 private connection 경계에서만 읽어 client를 만든다."""
    try:
        settings = _RedisCredentialSettings()  # type: ignore[call-arg]
    except ValidationError:
        raise KISCredentialError("KIS token cache authentication is unavailable") from None
    password = settings.redis_password.get_secret_value()
    try:
        return redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=password,
            socket_connect_timeout=_REDIS_SOCKET_TIMEOUT_SECONDS,
            socket_timeout=_REDIS_SOCKET_TIMEOUT_SECONDS,
            retry_on_timeout=False,
        )
    finally:
        password = ""


class _CredentialTransport(httpx.BaseTransport):
    """공식 KIS origin의 실제 send attempt에만 API credential과 bearer token을 부착한다."""

    def __init__(
        self,
        inner: httpx.BaseTransport,
        *,
        settings: KISSettings,
        token_provider: Callable[[], str] | None,
        rate_limiter: RateLimiter,
        accounting: CollectionRunRecorder | None = None,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
        max_json_depth: int = _MAX_JSON_DEPTH,
        sensitive_values: Callable[[], tuple[str, ...]] | None = None,
        deadline_guard: Callable[[], None] | None = None,
    ) -> None:
        self._inner = inner
        self._mode = settings.mode
        self._origin = httpx.URL(settings.base_url)
        self._enabled = not settings.offline
        self._token_provider = token_provider
        self._rate_limiter = rate_limiter
        self._accounting = accounting
        self._max_response_bytes = max_response_bytes
        self._max_json_depth = max_json_depth
        self._sensitive_values = sensitive_values
        self._deadline_guard = deadline_guard

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        _ensure_origin(request.url, self._origin)
        tr_id = request.headers.get(_INTERNAL_TR_ID_HEADER)
        if not tr_id:
            raise KISCredentialError("KIS transport metadata is unavailable")

        original_headers = httpx.Headers(request.headers)
        candidates: tuple[str, ...] = ()
        credentials: _Credentials | None = None
        token = ""
        app_key = ""
        app_secret = ""
        provider_response: httpx.Response | None = None
        sanitized_response: httpx.Response | None = None
        response_too_large = False
        attempt_recorded = False
        outcome_recorded = False
        call_budget_exceeded = False
        additional: tuple[str, ...] = ()
        try:
            request.headers.pop(_INTERNAL_TR_ID_HEADER, None)
            request.headers["tr_id"] = tr_id
            request.headers["custtype"] = "P"
            additional = self._sensitive_values() if self._sensitive_values is not None else ()
            if self._enabled:
                if self._token_provider is None:
                    raise KISCredentialError("KIS authentication is unavailable")
                self._require_before_handoff()
                token = str(self._token_provider())
                # token cache/발급 대기가 끝난 뒤 실제 market send 슬롯을 먼저 예약한다.
                self._require_before_handoff()
                self._rate_limiter.acquire()
                self._require_before_handoff()
                credentials = _read_credentials(self._mode)
                app_key = credentials.app_key.get_secret_value()
                app_secret = credentials.app_secret.get_secret_value()
                candidates = (app_key, app_secret, token, *additional)
                request.headers["authorization"] = f"Bearer {token}"
                request.headers["appkey"] = app_key
                request.headers["appsecret"] = app_secret
            else:
                self._require_before_handoff()
                self._rate_limiter.acquire()
                candidates = additional
            if self._accounting is not None:
                self._accounting.record_physical_attempt(PhysicalChannel.MARKET_DATA)
                attempt_recorded = True
            try:
                self._require_before_handoff()
                provider_response = self._inner.handle_request(request)
            except KISCallBudgetExceeded:
                call_budget_exceeded = True
            except Exception:
                if self._accounting is not None and attempt_recorded:
                    self._accounting.record_physical_failure(
                        PhysicalChannel.MARKET_DATA,
                        FailureCode.TRANSPORT_UNAVAILABLE,
                    )
                    outcome_recorded = True
                raise
            if provider_response is not None:
                try:
                    sanitized_response = _scrub_response(
                        provider_response,
                        candidates,
                        max_response_bytes=self._max_response_bytes,
                        max_json_depth=self._max_json_depth,
                    )
                except KISResponseTooLargeError:
                    # recursive sanitizer frame의 credential candidates를 새 외부 예외 traceback에 연결하지 않는다.
                    response_too_large = True
                    if self._accounting is not None and attempt_recorded:
                        self._accounting.record_physical_failure(
                            PhysicalChannel.MARKET_DATA,
                            FailureCode.RESPONSE_TOO_LARGE,
                        )
                        outcome_recorded = True
        finally:
            if provider_response is not None:
                provider_response.close()
            provider_response = None
            request.headers.clear()
            request.headers.update(original_headers)
            candidates = ()
            additional = ()
            credentials = None
            token = ""
            app_key = ""
            app_secret = ""
            if (
                self._accounting is not None
                and attempt_recorded
                and not outcome_recorded
                and sanitized_response is None
            ):
                self._accounting.record_physical_failure(
                    PhysicalChannel.MARKET_DATA,
                    FailureCode.UNKNOWN_INTERNAL,
                )

        if call_budget_exceeded:
            raise KISCallBudgetExceeded("KIS physical call budget exhausted") from None
        if response_too_large:
            raise KISResponseTooLargeError("KIS response exceeded the safety limit") from None
        if sanitized_response is None:
            raise KISCredentialError("KIS response sanitization failed")
        return sanitized_response

    def close(self) -> None:
        self._inner.close()

    def _require_before_handoff(self) -> None:
        """approval-bound caller가 준 deadline은 limiter 대기 전후와 socket 직전에 다시 확인한다."""

        if self._deadline_guard is not None:
            self._deadline_guard()


class _TokenIssuer:
    """OAuth body credential은 고정 token endpoint request를 만드는 순간에만 평문으로 존재한다."""

    def __init__(
        self,
        settings: KISSettings,
        transport: httpx.BaseTransport | None = None,
        rate_limiter: RateLimiter | None = None,
        accounting: CollectionRunRecorder | None = None,
        deadline_guard: Callable[[], None] | None = None,
    ) -> None:
        if rate_limiter is None:
            # tokenP도 app process마다 local bucket을 만들면 동시 cache miss에서 공식 1/s를 우회한다.
            raise KISCredentialError("KIS shared token rate limiter is required")
        self._mode = settings.mode
        self._url = f"{settings.base_url}/oauth2/tokenP"
        self._http = httpx.Client(
            transport=transport or httpx.HTTPTransport(verify=True, retries=0),
            timeout=settings.kis_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        self._rate_limiter = rate_limiter
        self._accounting = accounting
        self._deadline_guard = deadline_guard

    def issue(self) -> dict[str, Any]:
        credentials: _Credentials | None = None
        app_key = ""
        app_secret = ""
        body: dict[str, str] = {}
        response: httpx.Response | None = None
        content = b""
        payload: object = None
        result: dict[str, Any] | None = None
        transport_failed = False
        issue_failed = False
        invalid_response = False
        attempt_recorded = False
        try:
            # tokenP global 1/s 슬롯을 확보하기 전에는 static credential을 읽거나 body를 만들지 않는다.
            self._require_before_handoff()
            self._rate_limiter.acquire()
            self._require_before_handoff()
            credentials = _read_credentials(self._mode)
            app_key = credentials.app_key.get_secret_value()
            app_secret = credentials.app_secret.get_secret_value()
            body.update(
                {
                    "grant_type": "client_credentials",
                    "appkey": app_key,
                    "appsecret": app_secret,
                }
            )
            try:
                self._require_before_handoff()
                if self._accounting is not None:
                    self._accounting.record_physical_attempt(PhysicalChannel.TOKEN_P)
                    attempt_recorded = True
                response = self._http.post(self._url, json=body)
            except KISCallBudgetExceeded:
                # 승인 cap은 provider send 전 차단 신호이므로 일반 transport 실패로 축약하지 않는다.
                raise
            except (httpx.TimeoutException, httpx.TransportError):
                # caught exception의 credential-bearing request/context를 새 stable error에 연결하지 않는다.
                transport_failed = True
            except Exception:
                # inner transport handoff 뒤의 임의 예외도 unresolved attempt나 원문 traceback을 남기지 않는다.
                transport_failed = True
            if response is not None:
                if response.status_code >= 400:
                    issue_failed = True
                else:
                    try:
                        content = _read_limited(response, _MAX_RESPONSE_BYTES)
                        payload = json.loads(content)
                    except (
                        KISResponseTooLargeError,
                        UnicodeDecodeError,
                        json.JSONDecodeError,
                        RecursionError,
                    ):
                        invalid_response = True
                    if not invalid_response and isinstance(payload, dict):
                        try:
                            result = cast(
                                dict[str, Any],
                                _sanitize_token_payload(payload, (app_key, app_secret)),
                            )
                        except KISCredentialError:
                            invalid_response = True
                    elif not invalid_response:
                        invalid_response = True
        finally:
            body.clear()
            if response is not None:
                response.close()
            response = None
            credentials = None
            app_key = ""
            app_secret = ""
            content = b""
            payload = None

        if transport_failed:
            if self._accounting is not None and attempt_recorded:
                self._accounting.record_physical_failure(
                    PhysicalChannel.TOKEN_P,
                    FailureCode.TRANSPORT_UNAVAILABLE,
                )
            raise KISCredentialError("KIS token transport is unavailable") from None
        if issue_failed:
            if self._accounting is not None and attempt_recorded:
                self._accounting.record_physical_failure(
                    PhysicalChannel.TOKEN_P,
                    FailureCode.HTTP_ERROR,
                )
            raise KISCredentialError("KIS token issue failed") from None
        if invalid_response or result is None:
            if self._accounting is not None and attempt_recorded:
                self._accounting.record_physical_failure(
                    PhysicalChannel.TOKEN_P,
                    FailureCode.PROVIDER_ERROR,
                )
            raise KISCredentialError("KIS token response was invalid") from None
        if self._accounting is not None and attempt_recorded:
            self._accounting.record_physical_success(PhysicalChannel.TOKEN_P)
        return result

    def close(self) -> None:
        self._http.close()

    def _require_before_handoff(self) -> None:
        """approval-bound tokenP는 limiter wait 뒤에도 expiry를 넘기면 socket 전에 종료한다."""

        if self._deadline_guard is not None:
            self._deadline_guard()


def _ensure_origin(url: httpx.URL, origin: httpx.URL) -> None:
    if url.scheme != origin.scheme or url.host != origin.host or url.port != origin.port:
        raise KISCredentialError("KIS transport origin is not allowed")


def _scrub_response(
    response: httpx.Response,
    candidates: tuple[str, ...],
    *,
    max_response_bytes: int,
    max_json_depth: int,
) -> httpx.Response:
    content = _read_limited(response, max_response_bytes)
    encoded_candidates = _encoded_candidates(candidates)
    for candidate in encoded_candidates:
        content = content.replace(candidate.encode(), b"[redacted]")
    # KIS market endpoints are JSON; 잘못된 content-type이어도 JSON이면 credential field/value를 제거한다.
    content = _drop_authentication_fields(
        content,
        candidates=encoded_candidates,
        max_json_depth=max_json_depth,
    )

    headers: list[tuple[str, str]] = []
    for name, value in response.headers.multi_items():
        if name.lower() in {"content-encoding", "content-length", "transfer-encoding"}:
            continue
        sanitized = value
        for candidate in encoded_candidates:
            sanitized = sanitized.replace(candidate, "[redacted]")
        headers.append((name, sanitized))

    sanitized_response = httpx.Response(response.status_code, headers=headers, content=content)
    response.close()
    return sanitized_response


def _read_limited(response: httpx.Response, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > limit:
            response.close()
            raise KISResponseTooLargeError("KIS response exceeded the safety limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _drop_authentication_fields(
    content: bytes, *, candidates: set[str], max_json_depth: int
) -> bytes:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return content
    except RecursionError:
        raise KISResponseTooLargeError("KIS response structure exceeded the safety limit") from None
    sanitized = _sanitize_json_value(
        payload, candidates=candidates, depth=0, max_depth=max_json_depth
    )
    return json.dumps(sanitized, ensure_ascii=False, separators=(",", ":")).encode()


def _sanitize_json_value(
    value: object, *, candidates: set[str], depth: int, max_depth: int
) -> object:
    if depth > max_depth:
        raise KISResponseTooLargeError("KIS response structure exceeded the safety limit")
    if isinstance(value, dict):
        return {
            str(key): _sanitize_json_value(
                child,
                candidates=candidates,
                depth=depth + 1,
                max_depth=max_depth,
            )
            for key, child in value.items()
            if not _is_authentication_field(str(key))
        }
    if isinstance(value, list):
        return [
            _sanitize_json_value(child, candidates=candidates, depth=depth + 1, max_depth=max_depth)
            for child in value
        ]
    if isinstance(value, str) and (
        _contains_authentication_marker(value)
        or any(candidate in value for candidate in candidates)
    ):
        return "[redacted]"
    return value


def _sanitize_token_payload(
    value: dict[object, object], candidates: tuple[str, ...]
) -> dict[str, object]:
    """OAuth 응답은 token/만료 필드만 허용해 provider debug·echo가 상위 client로 전파되지 않게 한다."""
    encoded_candidates = _encoded_candidates(candidates)
    token = value.get("access_token")
    if not isinstance(token, str) or not token or len(token) > _MAX_ACCESS_TOKEN_CHARS:
        raise KISCredentialError("KIS token response was invalid")
    if any(candidate in token for candidate in encoded_candidates):
        raise KISCredentialError("KIS token response was invalid")

    sanitized: dict[str, object] = {"access_token": token}
    expires_in = value.get("expires_in")
    if expires_in is not None:
        if isinstance(expires_in, bool) or not isinstance(expires_in, (str, int)):
            raise KISCredentialError("KIS token response was invalid")
        try:
            ttl = int(expires_in)
        except (TypeError, ValueError):
            raise KISCredentialError("KIS token response was invalid") from None
        if not 1 <= ttl <= _MAX_TOKEN_TTL_SECONDS:
            raise KISCredentialError("KIS token response was invalid")
        sanitized["expires_in"] = ttl
        return sanitized

    expires_at = value.get("access_token_token_expired")
    if not isinstance(expires_at, str) or not _TOKEN_EXPIRY_PATTERN.fullmatch(expires_at):
        raise KISCredentialError("KIS token response was invalid")
    try:
        datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        raise KISCredentialError("KIS token response was invalid") from None
    sanitized["access_token_token_expired"] = expires_at
    return sanitized


def _encoded_candidates(candidates: tuple[str, ...]) -> set[str]:
    return {
        encoded
        for candidate in candidates
        if candidate
        for encoded in (candidate, quote(candidate, safe=""), quote_plus(candidate))
    }


def _is_authentication_field(name: str) -> bool:
    normalized = _normalized(name)
    return any(
        marker in normalized
        for marker in ("appkey", "appsecret", "token", "authorization", "credential")
    )


def _contains_authentication_marker(value: str) -> bool:
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in ("appkey", "appsecret", "token", "authorization", "credential")
    )


def _normalized(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())
