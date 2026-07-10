from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, quote_plus

import httpx
import redis
from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.data.kis.settings import KISMode, KISSettings

_REPOSITORY_ROOT = Path(__file__).resolve().parents[6]
_ROOT_ENV_FILE = _REPOSITORY_ROOT / ".env"
_INTERNAL_TR_ID_HEADER = "x-kis-internal-tr-id"
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
_MAX_JSON_DEPTH = 64
_MAX_ACCESS_TOKEN_CHARS = 8_192
_MAX_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60
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
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
        max_json_depth: int = _MAX_JSON_DEPTH,
    ) -> None:
        self._inner = inner
        self._mode = settings.mode
        self._origin = httpx.URL(settings.base_url)
        self._enabled = not settings.offline
        self._token_provider = token_provider
        self._max_response_bytes = max_response_bytes
        self._max_json_depth = max_json_depth

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        _ensure_origin(request.url, self._origin)
        tr_id = request.headers.get(_INTERNAL_TR_ID_HEADER)
        if not tr_id:
            raise KISCredentialError("KIS transport metadata is unavailable")

        original_headers = httpx.Headers(request.headers)
        candidates: tuple[str, ...] = ()
        try:
            request.headers.pop(_INTERNAL_TR_ID_HEADER, None)
            request.headers["tr_id"] = tr_id
            request.headers["custtype"] = "P"
            if self._enabled:
                credentials = _read_credentials(self._mode)
                if self._token_provider is None:
                    raise KISCredentialError("KIS authentication is unavailable")
                token = str(self._token_provider())
                app_key = credentials.app_key.get_secret_value()
                app_secret = credentials.app_secret.get_secret_value()
                candidates = (app_key, app_secret, token)
                request.headers["authorization"] = f"Bearer {token}"
                request.headers["appkey"] = app_key
                request.headers["appsecret"] = app_secret
            response = self._inner.handle_request(request)
            return _scrub_response(
                response,
                candidates,
                max_response_bytes=self._max_response_bytes,
                max_json_depth=self._max_json_depth,
            )
        finally:
            request.headers.clear()
            request.headers.update(original_headers)
            candidates = ()

    def close(self) -> None:
        self._inner.close()


class _TokenIssuer:
    """OAuth body credential은 고정 token endpoint request를 만드는 순간에만 평문으로 존재한다."""

    def __init__(self, settings: KISSettings, transport: httpx.BaseTransport | None = None) -> None:
        self._mode = settings.mode
        self._url = f"{settings.base_url}/oauth2/tokenP"
        self._http = httpx.Client(
            transport=transport or httpx.HTTPTransport(),
            timeout=settings.kis_timeout_seconds,
            follow_redirects=False,
        )

    def issue(self) -> dict[str, Any]:
        credentials = _read_credentials(self._mode)
        app_key = credentials.app_key.get_secret_value()
        app_secret = credentials.app_secret.get_secret_value()
        body: dict[str, str] = {
            "grant_type": "client_credentials",
            "appkey": app_key,
            "appsecret": app_secret,
        }
        try:
            response = self._http.post(self._url, json=body)
        except (httpx.TimeoutException, httpx.TransportError):
            raise KISCredentialError("KIS token transport is unavailable") from None
        finally:
            body.clear()
        try:
            if response.status_code >= 400:
                raise KISCredentialError("KIS token issue failed")
            content = _read_limited(response, _MAX_RESPONSE_BYTES)
            try:
                payload: object = json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise KISCredentialError("KIS token response was invalid") from None
            except RecursionError:
                raise KISCredentialError("KIS token response was invalid") from None
            if not isinstance(payload, dict):
                raise KISCredentialError("KIS token response was invalid")
            sanitized = _sanitize_token_payload(payload, (app_key, app_secret))
            return cast(dict[str, Any], sanitized)
        finally:
            response.close()
            app_key = ""
            app_secret = ""

    def close(self) -> None:
        self._http.close()


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


def _drop_authentication_fields(content: bytes, *, candidates: set[str], max_json_depth: int) -> bytes:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return content
    except RecursionError:
        raise KISResponseTooLargeError("KIS response structure exceeded the safety limit") from None
    sanitized = _sanitize_json_value(payload, candidates=candidates, depth=0, max_depth=max_json_depth)
    return json.dumps(sanitized, ensure_ascii=False, separators=(",", ":")).encode()


def _sanitize_json_value(value: object, *, candidates: set[str], depth: int, max_depth: int) -> object:
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
    if isinstance(value, str):
        if _contains_authentication_marker(value) or any(candidate in value for candidate in candidates):
            return "[redacted]"
    return value


def _sanitize_token_payload(value: dict[object, object], candidates: tuple[str, ...]) -> dict[str, object]:
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
    return any(marker in normalized for marker in ("appkey", "appsecret", "token", "authorization", "credential"))


def _contains_authentication_marker(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in ("appkey", "appsecret", "token", "authorization", "credential"))


def _normalized(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())
