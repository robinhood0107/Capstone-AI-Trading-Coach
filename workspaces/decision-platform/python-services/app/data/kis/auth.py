from __future__ import annotations

import json
import re
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from redis.exceptions import WatchError

from app.data.kis.accounting import CollectionRunRecorder, SkipCode
from app.data.kis.settings import KISMode

REFRESH_SKEW_SECONDS = 300
TOKEN_ISSUE_LOCK_SECONDS = 30
TOKEN_ISSUE_WAIT_SECONDS = 10.0
TOKEN_ISSUE_POLL_SECONDS = 0.01
MAX_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60
_OPAQUE_SCOPE_PATTERN = re.compile(r"[A-Za-z0-9_-]{8,128}")


class PipelineLike(Protocol):
    def __enter__(self) -> "PipelineLike": ...

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None: ...

    def watch(self, *names: str) -> object: ...

    def get(self, name: str) -> bytes | str | None: ...

    def unwatch(self) -> object: ...

    def multi(self) -> object: ...

    def delete(self, *names: str) -> object: ...

    def execute(self) -> object: ...


class RedisLike(Protocol):
    def get(self, name: str) -> bytes | str | None: ...

    def set(
        self,
        name: str,
        value: str,
        ex: int | timedelta | None = None,
        *,
        nx: bool = False,
    ) -> object: ...

    def pipeline(self) -> PipelineLike: ...


class KISTokenCacheError(RuntimeError):
    """토큰 cache/singleflight 상태를 안전하게 확인할 수 없으면 발급을 중단한다."""


class KISTokenManager:
    """Bearer token은 private transport용 provider에만 반환하고 평문 API credential은 전혀 받지 않는다."""

    def __init__(
        self,
        *,
        mode: KISMode,
        offline: bool,
        redis_client: RedisLike,
        issuer: Callable[[], dict[str, Any]],
        scope: str,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        accounting: CollectionRunRecorder | None = None,
    ) -> None:
        if not _OPAQUE_SCOPE_PATTERN.fullmatch(scope):
            raise ValueError("opaque KIS token scope is required")
        self._mode = mode
        self._offline = offline
        self._redis = redis_client
        self._issuer = issuer
        self._cache_key = _token_cache_key(scope)
        self._lock_key = _token_lock_key(scope)
        self._now = now or (lambda: datetime.now(UTC))
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._accounting = accounting

    def get_access_token(self) -> str:
        if self._offline:
            # fixture 모드는 network/Redis credential을 읽지 않고 private transport도 실제 send를 만들지 않는다.
            self._record_cache_skip()
            return "offline-token"
        cached = self._load_cached()
        if cached is not None:
            self._record_cache_skip()
            return cached
        owner = secrets.token_hex(16)
        self._acquire_issue_lock(owner)
        token_response: dict[str, Any] = {}
        token = ""
        payload: dict[str, object] = {}
        try:
            # lock 대기 사이 다른 process가 발급했을 수 있으므로 provider 호출 직전에 다시 확인한다.
            cached = self._load_cached()
            if cached is not None:
                self._record_cache_skip()
                return cached
            token_response = self._issuer()
            token, expires_in = self._parse_token_response(token_response)
            expires_at = self._now() + timedelta(seconds=expires_in)
            payload.update(
                {
                "mode": self._mode,
                "access_token": token,
                "expires_at": expires_at.isoformat(),
                }
            )
            ttl = max(1, expires_in - REFRESH_SKEW_SECONDS)
            try:
                stored = self._redis.set(self._cache_key, json.dumps(payload), ex=ttl)
            except Exception:
                raise KISTokenCacheError("KIS token cache is unavailable") from None
            if not stored:
                raise KISTokenCacheError("KIS token cache is unavailable")
            return token
        finally:
            token_response.clear()
            payload.clear()
            token = ""
            self._release_issue_lock(owner)

    def _record_cache_skip(self) -> None:
        if self._accounting is not None:
            self._accounting.record_skip(SkipCode.TOKEN_CACHE_HIT)

    def _load_cached(self) -> str | None:
        try:
            cached = self._redis.get(self._cache_key)
        except Exception:
            raise KISTokenCacheError("KIS token cache is unavailable") from None
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

    def _acquire_issue_lock(self, owner: str) -> None:
        deadline = self._monotonic() + TOKEN_ISSUE_WAIT_SECONDS
        while True:
            cached = self._load_cached()
            if cached is not None:
                return
            try:
                acquired = self._redis.set(
                    self._lock_key,
                    owner,
                    ex=TOKEN_ISSUE_LOCK_SECONDS,
                    nx=True,
                )
            except Exception:
                raise KISTokenCacheError("KIS token singleflight is unavailable") from None
            if acquired:
                return
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise KISTokenCacheError("KIS token issue wait exceeded")
            self._sleeper(min(TOKEN_ISSUE_POLL_SECONDS, remaining))

    def _release_issue_lock(self, owner: str) -> None:
        # TTL 만료 뒤 새 owner가 lock을 잡은 경우 기존 owner가 지우지 못하게 WATCH로 fencing한다.
        while True:
            try:
                with self._redis.pipeline() as pipeline:
                    pipeline.watch(self._lock_key)
                    current = pipeline.get(self._lock_key)
                    current_text = current.decode() if isinstance(current, bytes) else current
                    if current_text != owner:
                        pipeline.unwatch()
                        return
                    pipeline.multi()
                    pipeline.delete(self._lock_key)
                    pipeline.execute()
                    return
            except WatchError:
                continue
            except Exception:
                # lock은 짧은 TTL로 반드시 만료되므로 성공한 token 발급 결과까지 폐기하지 않는다.
                return

    def _parse_token_response(self, response: dict[str, Any]) -> tuple[str, int]:
        token = ""
        raw_token: object = None
        expires_at_text = ""
        result: tuple[str, int] | None = None
        invalid = False
        try:
            raw_token = response.get("access_token")
            if not isinstance(raw_token, str) or not raw_token:
                invalid = True
            else:
                token = raw_token
                expires_in = response.get("expires_in")
                if isinstance(expires_in, str) and expires_in.isdigit():
                    ttl = int(expires_in)
                elif isinstance(expires_in, int) and not isinstance(expires_in, bool):
                    ttl = expires_in
                else:
                    raw_expires_at = response.get("access_token_token_expired")
                    if isinstance(raw_expires_at, str) and raw_expires_at:
                        expires_at_text = raw_expires_at
                        try:
                            expires_at = datetime.strptime(
                                expires_at_text,
                                "%Y-%m-%d %H:%M:%S",
                            ).replace(tzinfo=ZoneInfo("Asia/Seoul"))
                            ttl = int((expires_at.astimezone(UTC) - self._now()).total_seconds())
                        except ValueError:
                            invalid = True
                            ttl = 0
                    else:
                        ttl = 86400
                if not invalid and 1 <= ttl <= MAX_TOKEN_TTL_SECONDS:
                    result = (token, ttl)
                else:
                    invalid = True
        finally:
            response.clear()
            token = ""
            raw_token = None
            expires_at_text = ""

        if invalid or result is None:
            raise KISTokenCacheError("KIS token response is invalid") from None
        return result


def _token_cache_key(scope: str) -> str:
    return f"kis:token:v2:{scope}"


def _token_lock_key(scope: str) -> str:
    return f"kis:token-issue:v2:{scope}"
