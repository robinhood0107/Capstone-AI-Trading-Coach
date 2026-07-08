from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx

from app.data.kis.masking import mask_text
from app.data.kis.settings import KISSettings

TOKEN_KEY = "kis:token"
REFRESH_SKEW_SECONDS = 300


class RedisLike(Protocol):
    def get(self, name: str) -> bytes | str | None: ...

    def set(self, name: str, value: str, ex: int | timedelta | None = None) -> object: ...


class KISTokenManager:
    def __init__(
        self,
        settings: KISSettings,
        redis_client: RedisLike,
        issuer: Callable[[], dict[str, Any]] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.redis_client = redis_client
        self.issuer = issuer or self._issue_token
        self.now = now or (lambda: datetime.now(UTC))

    def get_access_token(self) -> str:
        if self.settings.offline:
            return "offline-token"
        cached = self._load_cached()
        if cached is not None:
            return cached
        token_response = self.issuer()
        token, expires_in = self._parse_token_response(token_response)
        expires_at = self.now() + timedelta(seconds=expires_in)
        payload = {
            "mode": self.settings.mode,
            "access_token": token,
            "expires_at": expires_at.isoformat(),
        }
        # KIS 토큰은 만료 직전 장애를 피하려고 Redis TTL도 5분 일찍 끝낸다.
        ttl = max(1, expires_in - REFRESH_SKEW_SECONDS)
        self.redis_client.set(TOKEN_KEY, json.dumps(payload), ex=ttl)
        return token

    def _load_cached(self) -> str | None:
        cached = self.redis_client.get(TOKEN_KEY)
        if cached is None:
            return None
        if isinstance(cached, bytes):
            cached = cached.decode("utf-8")
        payload = json.loads(cached)
        if payload.get("mode") != self.settings.mode:
            return None
        expires_at = datetime.fromisoformat(payload["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at - self.now() <= timedelta(seconds=REFRESH_SKEW_SECONDS):
            return None
        return str(payload["access_token"])

    def _issue_token(self) -> dict[str, Any]:
        body = {
            "grant_type": "client_credentials",
            "appkey": self.settings.app_key,
            "appsecret": self.settings.app_secret,
        }
        with httpx.Client(timeout=self.settings.kis_timeout_seconds) as client:
            response = client.post(f"{self.settings.base_url}/oauth2/tokenP", json=body)
            if response.status_code >= 400:
                message = mask_text(response.text[:300], [self.settings.app_key, self.settings.app_secret])
                raise RuntimeError(f"KIS token issue failed: {message}")
            data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("KIS token response was not a JSON object")
        return data

    def _parse_token_response(self, response: dict[str, Any]) -> tuple[str, int]:
        token = response.get("access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("KIS token response did not include access_token")
        expires_in = response.get("expires_in")
        if isinstance(expires_in, str) and expires_in.isdigit():
            return token, int(expires_in)
        if isinstance(expires_in, int | float):
            return token, int(expires_in)
        expires_at_text = response.get("access_token_token_expired")
        if isinstance(expires_at_text, str) and expires_at_text:
            expires_at = datetime.strptime(expires_at_text, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=ZoneInfo("Asia/Seoul")
            )
            return token, max(1, int((expires_at.astimezone(UTC) - self.now()).total_seconds()))
        return token, 86400
