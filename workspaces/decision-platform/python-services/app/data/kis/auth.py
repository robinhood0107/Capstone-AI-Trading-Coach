from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.data.kis.settings import KISMode

TOKEN_KEY = "kis:token"
REFRESH_SKEW_SECONDS = 300


class RedisLike(Protocol):
    def get(self, name: str) -> bytes | str | None: ...

    def set(self, name: str, value: str, ex: int | timedelta | None = None) -> object: ...


class KISTokenManager:
    """Bearer token은 private transport용 provider에만 반환하고 평문 API credential은 전혀 받지 않는다."""

    def __init__(
        self,
        *,
        mode: KISMode,
        offline: bool,
        redis_client: RedisLike,
        issuer: Callable[[], dict[str, Any]],
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._mode = mode
        self._offline = offline
        self._redis = redis_client
        self._issuer = issuer
        self._now = now or (lambda: datetime.now(UTC))

    def get_access_token(self) -> str:
        if self._offline:
            # fixture 모드는 network/Redis credential을 읽지 않고 private transport도 실제 send를 만들지 않는다.
            return "offline-token"
        cached = self._load_cached()
        if cached is not None:
            return cached
        token_response = self._issuer()
        token, expires_in = self._parse_token_response(token_response)
        expires_at = self._now() + timedelta(seconds=expires_in)
        payload = {
            "mode": self._mode,
            "access_token": token,
            "expires_at": expires_at.isoformat(),
        }
        ttl = max(1, expires_in - REFRESH_SKEW_SECONDS)
        self._redis.set(TOKEN_KEY, json.dumps(payload), ex=ttl)
        return token

    def _load_cached(self) -> str | None:
        cached = self._redis.get(TOKEN_KEY)
        if cached is None:
            return None
        try:
            if isinstance(cached, bytes):
                cached = cached.decode("utf-8")
            payload = json.loads(cached)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("mode") != self._mode:
            return None
        try:
            expires_at = datetime.fromisoformat(str(payload["expires_at"]))
        except (KeyError, ValueError):
            return None
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at - self._now() <= timedelta(seconds=REFRESH_SKEW_SECONDS):
            return None
        token = payload.get("access_token")
        return token if isinstance(token, str) and token else None

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
            return token, max(1, int((expires_at.astimezone(UTC) - self._now()).total_seconds()))
        return token, 86400
