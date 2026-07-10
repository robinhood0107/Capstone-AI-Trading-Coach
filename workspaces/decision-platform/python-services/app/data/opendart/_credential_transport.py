from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, quote_plus

import httpx
from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.data.opendart.settings import OPENDART_ORIGIN

_AUTHENTICATION_PARAMETER = "crtfc_key"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[6]
_ROOT_ENV_FILE = _REPOSITORY_ROOT / ".env"


class OpenDARTCredentialError(RuntimeError):
    """인증정보를 읽을 수 없을 때 값·환경변수명·파일 경로 없이 fail-closed한다."""


class _CredentialSettings(BaseSettings):
    """실제 값은 transport attempt 동안만 존재하고 직렬화·repr 대상에서 제외한다."""

    model_config = SettingsConfigDict(
        env_file=(_ROOT_ENV_FILE,),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    opendart_api_key: SecretStr = Field(repr=False, exclude=True)

    @field_validator("opendart_api_key")
    @classmethod
    def _reject_blank_value(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("blank authentication material")
        return value


def _read_credential() -> SecretStr:
    """루트 env에서 인증정보를 일시 로드하며 호출자에게 평문 설정 객체를 반환하지 않는다."""
    try:
        # 필수값은 init 인자가 아니라 process env/루트 .env에서만 공급되므로 정적 call-arg 검사를 제외한다.
        settings = _CredentialSettings()  # type: ignore[call-arg]
    except ValidationError:
        raise OpenDARTCredentialError("OpenDART authentication is unavailable") from None
    return settings.opendart_api_key


class _CredentialTransport(httpx.BaseTransport):
    """고정 OpenDART origin의 실제 send 구간에서만 인증정보를 요청에 일시 부착한다."""

    def __init__(self, inner: httpx.BaseTransport, *, enabled: bool) -> None:
        self._inner = inner
        self._enabled = enabled

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        _ensure_official_origin(request.url)
        if not self._enabled:
            return self._inner.handle_request(request)

        credential = _read_credential()
        value = credential.get_secret_value()
        original_url = request.url
        try:
            request.url = original_url.copy_set_param(_AUTHENTICATION_PARAMETER, value)
            response = self._inner.handle_request(request)
            return _scrub_response(response, value)
        finally:
            # inner transport가 같은 Request 객체를 예외나 응답에 보관해도 평문 query가 남지 않게 원복한다.
            request.url = original_url
            value = ""
            credential = SecretStr("")

    def close(self) -> None:
        self._inner.close()


def _ensure_official_origin(url: httpx.URL) -> None:
    origin = httpx.URL(OPENDART_ORIGIN)
    if url.scheme != origin.scheme or url.host != origin.host or url.port != origin.port:
        raise OpenDARTCredentialError("OpenDART transport origin is not allowed")


def _scrub_response(response: httpx.Response, credential: str) -> httpx.Response:
    """upstream이 값을 echo해도 parser·예외·raw 계층에 도달하기 전에 제거한다."""
    response.read()
    candidates = {credential, quote(credential, safe=""), quote_plus(credential)}
    content = response.content
    for candidate in candidates:
        if candidate:
            content = content.replace(candidate.encode(), b"[redacted]")

    headers: list[tuple[str, str]] = []
    for name, value in response.headers.multi_items():
        sanitized = value
        for candidate in candidates:
            if candidate:
                sanitized = sanitized.replace(candidate, "[redacted]")
        headers.append((name, sanitized))

    extensions: dict[str, object] = {}
    for name in ("http_version", "reason_phrase", "network_stream"):
        if name not in response.extensions:
            continue
        extension_value = response.extensions[name]
        if isinstance(extension_value, bytes):
            for candidate in candidates:
                if candidate:
                    extension_value = extension_value.replace(candidate.encode(), b"[redacted]")
        elif isinstance(extension_value, str):
            for candidate in candidates:
                if candidate:
                    extension_value = extension_value.replace(candidate, "[redacted]")
        extensions[name] = extension_value

    sanitized_response = httpx.Response(
        response.status_code,
        headers=headers,
        content=content,
        extensions=extensions,
    )
    response.close()
    return sanitized_response
