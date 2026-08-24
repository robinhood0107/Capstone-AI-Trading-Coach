from __future__ import annotations

import re
from dataclasses import dataclass
from threading import Lock
from typing import Literal, Protocol, cast
from uuid import UUID

REDIS_QUOTA_LUA = r"""
local key = KEYS[1]
local now_parts = redis.call('TIME')
local now_ms = (tonumber(now_parts[1]) * 1000) + math.floor(tonumber(now_parts[2]) / 1000)
local attempt_id = ARGV[1]
local min_interval_ms = tonumber(ARGV[3])
local cooldown_ms = tonumber(ARGV[4])
local window_count = tonumber(ARGV[6])
local cooldown_key = key .. ':cooldown'
local cooldown_until = tonumber(redis.call('GET', cooldown_key) or '0')

if cooldown_until > now_ms then
    return {0, cooldown_until - now_ms, 0}
end

local longest_window_ms = 1
for index = 0, window_count - 1 do
    local window_ms = tonumber(ARGV[8 + (index * 2)])
    if window_ms > longest_window_ms then
        longest_window_ms = window_ms
    end
end
redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - longest_window_ms)

local latest = redis.call('ZREVRANGE', key, 0, 0, 'WITHSCORES')
if min_interval_ms > 0 and #latest == 2 then
    local elapsed = now_ms - tonumber(latest[2])
    if elapsed < min_interval_ms then
        return {2, min_interval_ms - elapsed, redis.call('ZCARD', key)}
    end
end

for index = 0, window_count - 1 do
    local limit = tonumber(ARGV[7 + (index * 2)])
    local window_ms = tonumber(ARGV[8 + (index * 2)])
    local count = redis.call('ZCOUNT', key, now_ms - window_ms, '+inf')
    if count >= limit then
        return {0, window_ms, count}
    end
end

local inserted = redis.call('ZADD', key, 'NX', now_ms, attempt_id)
if inserted ~= 1 then
    return {0, min_interval_ms, redis.call('ZCARD', key)}
end
redis.call('PEXPIRE', key, longest_window_ms + cooldown_ms)
return {1, 0, redis.call('ZCARD', key)}
"""

REDIS_COOLDOWN_LUA = r"""
local key = KEYS[1]
local cooldown_key = key .. ':cooldown'
local duration_ms = tonumber(ARGV[1])
local now_parts = redis.call('TIME')
local now_ms = (tonumber(now_parts[1]) * 1000) + math.floor(tonumber(now_parts[2]) / 1000)
local requested_until = now_ms + duration_ms
local current_until = tonumber(redis.call('GET', cooldown_key) or '0')

if current_until >= requested_until then
    return current_until - now_ms
end

redis.call('SET', cooldown_key, requested_until, 'PX', duration_ms)
return duration_ms
"""

_OPAQUE_KEY = re.compile(r"s1\.3:quota:[a-z0-9][a-z0-9-]{0,31}:[a-z0-9][a-z0-9-]{0,31}:primary")


class RedisEvalLike(Protocol):
    def eval(self, script: str, key_count: int, *args: object) -> object: ...


class QuotaDeniedError(RuntimeError):
    """provider 호출 슬롯을 예약하지 못한 상태를 secret/key 없이 전달한다."""

    def __init__(self, *, retry_after_ms: int, observed_count: int) -> None:
        super().__init__("source quota reservation was denied")
        self.retry_after_ms = retry_after_ms
        self.observed_count = observed_count


class QuotaWaitError(RuntimeError):
    """최소 호출 간격이 남은 상태를 외부 scheduler가 처리하도록 전달한다."""

    def __init__(self, *, retry_after_ms: int, observed_count: int) -> None:
        super().__init__("source quota reservation must wait")
        self.retry_after_ms = retry_after_ms
        self.observed_count = observed_count


class QuotaUnavailableError(RuntimeError):
    """Redis 응답을 안전하게 확인할 수 없으면 outbound를 fail-closed한다."""


@dataclass(frozen=True)
class QuotaWindow:
    limit: int
    seconds: int

    def __post_init__(self) -> None:
        if self.limit <= 0 or self.seconds <= 0:
            raise ValueError("quota window limit and seconds must be positive")


@dataclass(frozen=True)
class RedisQuotaPolicy:
    version: str
    windows: tuple[QuotaWindow, ...]
    min_interval_ms: int
    cooldown_seconds: int
    max_calls_per_run: int

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", self.version):
            raise ValueError("opaque quota policy version is required")
        if not self.windows:
            raise ValueError("at least one quota window is required")
        if self.min_interval_ms < 0 or self.cooldown_seconds < 0:
            raise ValueError("quota intervals must be non-negative")
        if self.max_calls_per_run <= 0:
            raise ValueError("quota run cap must be positive")


class RedisQuotaReservation:
    """Redis TIME 기반 Lua script로 모든 physical attempt의 슬롯을 원자 예약한다."""

    def __init__(self, redis_client: RedisEvalLike, *, key: str, policy: RedisQuotaPolicy) -> None:
        if _OPAQUE_KEY.fullmatch(key) is None:
            raise ValueError("opaque source quota deployment slot is required")
        self._redis = redis_client
        self._key = key
        self._policy = policy
        self._granted_in_run = 0
        self._run_lock = Lock()

    def reserve(self, *, attempt_id: str) -> None:
        """고유 attempt를 예약하며 실패·timeout 뒤에도 환불 API를 제공하지 않는다."""
        _validate_attempt_id(attempt_id)
        with self._run_lock:
            if self._granted_in_run >= self._policy.max_calls_per_run:
                raise QuotaDeniedError(retry_after_ms=0, observed_count=self._granted_in_run)
            arguments: list[object] = [
                self._key,
                attempt_id,
                self._policy.version,
                self._policy.min_interval_ms,
                self._policy.cooldown_seconds * 1000,
                self._policy.max_calls_per_run,
                len(self._policy.windows),
            ]
            for window in self._policy.windows:
                arguments.extend((window.limit, window.seconds * 1000))
            try:
                raw_result = self._redis.eval(REDIS_QUOTA_LUA, 1, *arguments)
                decision, retry_after_ms, observed_count = _parse_result(raw_result)
            except QuotaUnavailableError:
                raise
            except Exception:
                raise QuotaUnavailableError("source quota reservation is unavailable") from None
            if decision == "wait":
                raise QuotaWaitError(
                    retry_after_ms=retry_after_ms,
                    observed_count=observed_count,
                )
            if decision == "deny":
                raise QuotaDeniedError(
                    retry_after_ms=retry_after_ms,
                    observed_count=observed_count,
                )
            self._granted_in_run += 1

    def activate_cooldown(self, *, seconds: int) -> None:
        """provider rate-limit 응답 뒤 Redis TIME 기준 deployment cooldown을 원자 활성화한다."""
        if seconds <= 0 or seconds > self._policy.cooldown_seconds:
            raise ValueError("quota cooldown seconds are out of policy bounds")
        try:
            result = self._redis.eval(
                REDIS_COOLDOWN_LUA,
                1,
                self._key,
                seconds * 1000,
            )
            remaining_ms = int(cast(int | str | bytes, result))
        except (TypeError, ValueError):
            raise QuotaUnavailableError("source quota cooldown response was invalid") from None
        except Exception:
            raise QuotaUnavailableError("source quota cooldown is unavailable") from None
        if remaining_ms < 0:
            raise QuotaUnavailableError("source quota cooldown response was invalid")

    @property
    def granted_in_run(self) -> int:
        """현재 process run에서 예약에 성공한 physical attempt 수를 반환한다."""
        with self._run_lock:
            return self._granted_in_run


def _validate_attempt_id(value: str) -> None:
    try:
        parsed = UUID(value)
    except ValueError:
        raise ValueError("lowercase UUID v4 quota attempt id is required") from None
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("lowercase UUID v4 quota attempt id is required")


QuotaDecision = Literal["deny", "allow", "wait"]


def _parse_result(value: object) -> tuple[QuotaDecision, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise QuotaUnavailableError("source quota reservation response was invalid")
    parts = cast(list[object] | tuple[object, ...], value)
    try:
        status = _parse_redis_integer(parts[0])
        retry_after_ms = _parse_redis_integer(parts[1])
        observed_count = _parse_redis_integer(parts[2])
    except (TypeError, ValueError):
        raise QuotaUnavailableError("source quota reservation response was invalid") from None
    if status not in (0, 1, 2) or retry_after_ms < 0 or observed_count < 0:
        raise QuotaUnavailableError("source quota reservation response was invalid")
    if status == 1:
        if retry_after_ms != 0:
            raise QuotaUnavailableError("source quota reservation response was invalid")
        return "allow", retry_after_ms, observed_count
    if status == 2:
        if retry_after_ms == 0:
            raise QuotaUnavailableError("source quota reservation response was invalid")
        return "wait", retry_after_ms, observed_count
    return "deny", retry_after_ms, observed_count


def _parse_redis_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str, bytes)):
        raise TypeError("Redis quota value is not an integer")
    return int(value)
