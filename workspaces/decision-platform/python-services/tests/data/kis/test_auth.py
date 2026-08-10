from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Lock
import time
from typing import Any
from uuid import UUID

import fakeredis
import httpx
import pytest
from pydantic import SecretStr

from app.data.kis import _credential_transport
from app.data.kis.accounting import (
    CollectionRunRecorder,
    CollectionRunStatus,
    PhysicalChannel,
)
from app.data.kis._credential_transport import (
    KISCredentialError,
    _Credentials,
    _build_redis_client,
    _provider_scope,
    _TokenIssuer,
)
from app.data.kis.auth import KISTokenCacheError, KISTokenManager, _token_cache_key
from app.data.kis.rate_limiter import TokenBucket
from app.data.kis.settings import KISSettings


def _assert_traceback_locals_do_not_contain(error: BaseException, marker: str) -> None:
    traceback = error.__traceback__
    while traceback is not None:
        if "/app/data/kis/" in traceback.tb_frame.f_code.co_filename:
            for value in traceback.tb_frame.f_locals.values():
                assert marker not in repr(value)
        traceback = traceback.tb_next


def test_token_issuer_injects_credentials_only_inside_private_fixed_origin_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_key = "validation-dummy-key"
    app_secret = "validation-dummy-secret"
    events: list[str] = []

    def read_credentials(_: str) -> _Credentials:
        events.append("credentials")
        return _Credentials(app_key=SecretStr(app_key), app_secret=SecretStr(app_secret))

    monkeypatch.setattr(_credential_transport, "_read_credentials", read_credentials)

    def handler(request: httpx.Request) -> httpx.Response:
        events.append("send")
        assert request.url == "https://openapivts.koreainvestment.com:29443/oauth2/tokenP"
        assert request.read().decode().find(app_key) >= 0
        assert request.read().decode().find(app_secret) >= 0
        return httpx.Response(
            200,
            json={"access_token": "validation-dummy-token", "expires_in": 86400, "appsecret": app_secret},
        )

    class RecordingLimiter:
        def acquire(self) -> None:
            events.append("quota")

    issuer = _TokenIssuer(
        KISSettings(kis_mode="mock", kis_offline=False, _env_file=None),
        transport=httpx.MockTransport(handler),
        rate_limiter=RecordingLimiter(),
    )

    assert issuer.issue() == {"access_token": "validation-dummy-token", "expires_in": 86400}
    assert events == ["quota", "credentials", "send"]
    assert app_key not in repr(vars(issuer))
    assert app_secret not in repr(vars(issuer))


def test_token_issuer_accounting_is_separate_from_market_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _credential_transport,
        "_read_credentials",
        lambda _: _Credentials(
            app_key=SecretStr("validation-dummy-key"),
            app_secret=SecretStr("validation-dummy-secret"),
        ),
    )
    recorder = CollectionRunRecorder(
        run_id=UUID("123e4567-e89b-42d3-a456-426614174000"),
        started_at=datetime(2026, 7, 21, 1, 0, tzinfo=UTC),
    )
    issuer = _TokenIssuer(
        KISSettings(kis_mode="mock", kis_offline=False, _env_file=None),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"access_token": "validation-dummy-token", "expires_in": 86400},
            )
        ),
        rate_limiter=TokenBucket(rate_per_second=1000),
        accounting=recorder,
    )

    issuer.issue()
    summary = recorder.snapshot(
        completed_at=datetime(2026, 7, 21, 1, 1, tzinfo=UTC),
        status=CollectionRunStatus.SUCCESS,
    )
    attempts = {item.channel: item for item in summary.physical_attempts}
    assert attempts[PhysicalChannel.TOKEN_P].attempts == 1
    assert attempts[PhysicalChannel.TOKEN_P].successes == 1
    assert attempts[PhysicalChannel.MARKET_DATA].attempts == 0


def test_online_token_issuer_rejects_missing_shared_rate_limiter() -> None:
    with pytest.raises(KISCredentialError, match="shared token rate limiter"):
        _TokenIssuer(
            KISSettings(kis_mode="mock", kis_offline=False, _env_file=None),
            transport=httpx.MockTransport(lambda _: httpx.Response(200)),
        )


def test_token_quota_failure_does_not_read_static_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def read_credentials(_: str) -> _Credentials:
        events.append("credentials")
        return _Credentials(
            app_key=SecretStr("validation-dummy-key"),
            app_secret=SecretStr("validation-dummy-secret"),
        )

    class RejectingLimiter:
        def acquire(self) -> None:
            events.append("quota")
            raise RuntimeError("quota unavailable")

    monkeypatch.setattr(_credential_transport, "_read_credentials", read_credentials)
    issuer = _TokenIssuer(
        KISSettings(kis_mode="mock", kis_offline=False, _env_file=None),
        transport=httpx.MockTransport(lambda _: httpx.Response(200)),
        rate_limiter=RejectingLimiter(),
    )

    with pytest.raises(RuntimeError, match="quota unavailable"):
        issuer.issue()

    assert events == ["quota"]


def test_provider_scope_is_opaque_and_mode_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    app_key = "validation-dummy-key"
    app_secret = "validation-dummy-secret"
    monkeypatch.setattr(
        _credential_transport,
        "_read_credentials",
        lambda _: _Credentials(app_key=SecretStr(app_key), app_secret=SecretStr(app_secret)),
    )

    mock_scope = _provider_scope("mock")
    live_scope = _provider_scope("live")

    assert mock_scope != live_scope
    assert len(mock_scope) == 64
    assert all(value not in mock_scope + live_scope for value in (app_key, app_secret, "mock", "live"))


def test_private_redis_client_uses_bounded_socket_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "validation-dummy-redis-password"
    captured: dict[str, object] = {}
    client = object()

    class FakeRedisSettings:
        redis_host = "127.0.0.1"
        redis_port = 6379
        redis_db = 0
        redis_password = SecretStr(marker)

    def build_redis(**kwargs: object) -> object:
        captured.update(kwargs)
        return client

    monkeypatch.setattr(_credential_transport, "_RedisCredentialSettings", FakeRedisSettings)
    monkeypatch.setattr(_credential_transport.redis, "Redis", build_redis)

    assert _build_redis_client() is client
    assert captured["socket_connect_timeout"] == 2.0
    assert captured["socket_timeout"] == 2.0
    assert captured["retry_on_timeout"] is False
    assert captured["password"] == marker


def test_token_issuer_drops_provider_error_body_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "validation-dummy-secret"
    monkeypatch.setattr(
        _credential_transport,
        "_read_credentials",
        lambda _: _Credentials(app_key=SecretStr("validation-dummy-key"), app_secret=SecretStr(marker)),
    )
    issuer = _TokenIssuer(
        KISSettings(kis_mode="mock", kis_offline=False, _env_file=None),
        transport=httpx.MockTransport(lambda _: httpx.Response(400, text=f"echo {marker}")),
        rate_limiter=TokenBucket(rate_per_second=1000),
    )

    with pytest.raises(KISCredentialError) as exc_info:
        issuer.issue()

    assert marker not in f"{exc_info.value!r} {exc_info.value}"


def test_token_transport_failure_scrubs_credential_traceback_locals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "validation-dummy-secret"
    monkeypatch.setattr(
        _credential_transport,
        "_read_credentials",
        lambda _: _Credentials(app_key=SecretStr("validation-dummy-key"), app_secret=SecretStr(marker)),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("synthetic timeout", request=request)

    issuer = _TokenIssuer(
        KISSettings(kis_mode="mock", kis_offline=False, _env_file=None),
        transport=httpx.MockTransport(handler),
        rate_limiter=TokenBucket(rate_per_second=1000),
    )

    with pytest.raises(KISCredentialError) as exc_info:
        issuer.issue()

    assert exc_info.value.__context__ is None
    _assert_traceback_locals_do_not_contain(exc_info.value, marker)


def test_token_issuer_allowlists_fields_and_drops_embedded_or_deep_credential_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = 'validation-dummy-"secret"'
    monkeypatch.setattr(
        _credential_transport,
        "_read_credentials",
        lambda _: _Credentials(app_key=SecretStr("validation-dummy-key"), app_secret=SecretStr(marker)),
    )
    nested: object = f"echo::{marker}::end"
    for _ in range(80):
        nested = {"child": nested}
    issuer = _TokenIssuer(
        KISSettings(kis_mode="mock", kis_offline=False, _env_file=None),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "access_token": "validation-dummy-token",
                    "expires_in": 86400,
                    "message": f"echo::{marker}::end",
                    "debug": nested,
                },
            )
        ),
        rate_limiter=TokenBucket(rate_per_second=1000),
    )

    result = issuer.issue()

    assert result == {"access_token": "validation-dummy-token", "expires_in": 86400}
    assert marker not in repr(result)


def test_token_issuer_rejects_credential_echo_in_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = "validation-dummy-secret"
    monkeypatch.setattr(
        _credential_transport,
        "_read_credentials",
        lambda _: _Credentials(app_key=SecretStr("validation-dummy-key"), app_secret=SecretStr(marker)),
    )
    issuer = _TokenIssuer(
        KISSettings(kis_mode="mock", kis_offline=False, _env_file=None),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"access_token": f"prefix-{marker}-suffix", "expires_in": 86400})
        ),
        rate_limiter=TokenBucket(rate_per_second=1000),
    )

    with pytest.raises(KISCredentialError, match="invalid"):
        issuer.issue()


def test_token_issuer_rejects_calendar_invalid_expiry_without_token_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "validation-dummy-access-token"
    monkeypatch.setattr(
        _credential_transport,
        "_read_credentials",
        lambda _: _Credentials(
            app_key=SecretStr("validation-dummy-key"),
            app_secret=SecretStr("validation-dummy-secret"),
        ),
    )
    issuer = _TokenIssuer(
        KISSettings(kis_mode="mock", kis_offline=False, _env_file=None),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "access_token": token,
                    "access_token_token_expired": "2026-99-99 99:99:99",
                },
            )
        ),
        rate_limiter=TokenBucket(rate_per_second=1000),
    )

    with pytest.raises(KISCredentialError, match="invalid") as exc_info:
        issuer.issue()

    _assert_traceback_locals_do_not_contain(exc_info.value, token)


def test_token_manager_maps_invalid_expiry_to_stable_tokenless_error() -> None:
    token = "validation-dummy-access-token"
    manager = KISTokenManager(
        mode="mock",
        offline=False,
        redis_client=fakeredis.FakeStrictRedis(),
        issuer=lambda: {
            "access_token": token,
            "access_token_token_expired": "2026-99-99 99:99:99",
        },
        scope="test-scope",
    )

    with pytest.raises(KISTokenCacheError, match="invalid") as exc_info:
        manager.get_access_token()

    assert exc_info.value.__context__ is None
    _assert_traceback_locals_do_not_contain(exc_info.value, token)


def test_token_cache_miss_issues_and_stores_with_refresh_skew() -> None:
    redis_client = fakeredis.FakeStrictRedis()
    now = datetime(2026, 7, 8, 9, 0, tzinfo=UTC)
    calls: list[str] = []

    def issuer() -> dict[str, Any]:
        calls.append("issued")
        return {"access_token": "sensitive-token", "expires_in": 86400}

    manager = KISTokenManager(
        mode="mock",
        offline=False,
        redis_client=redis_client,
        issuer=issuer,
        scope="test-scope",
        now=lambda: now,
    )

    assert manager.get_access_token() == "sensitive-token"
    assert calls == ["issued"]
    assert 86090 <= redis_client.ttl(_token_cache_key("test-scope")) <= 86100


def test_cache_only_token_manager_rejects_miss_without_issuing() -> None:
    issued = 0

    def issuer() -> dict[str, Any]:
        nonlocal issued
        issued += 1
        return {"access_token": "must-not-be-issued", "expires_in": 86400}

    manager = KISTokenManager(
        mode="mock",
        offline=False,
        redis_client=fakeredis.FakeStrictRedis(),
        issuer=issuer,
        scope="test-scope",
        cache_only=True,
    )

    with pytest.raises(KISTokenCacheError, match="cached token is required"):
        manager.get_access_token()

    assert issued == 0


def test_token_cache_hit_does_not_issue_again() -> None:
    redis_client = fakeredis.FakeStrictRedis()
    now = datetime(2026, 7, 8, 9, 0, tzinfo=UTC)
    calls = 0

    def issuer() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"access_token": f"token-{calls}", "expires_in": 86400}

    manager = KISTokenManager(
        mode="mock",
        offline=False,
        redis_client=redis_client,
        issuer=issuer,
        scope="live-scope",
        now=lambda: now,
    )

    assert manager.get_access_token() == "token-1"
    assert manager.get_access_token() == "token-1"
    assert calls == 1


def test_token_refreshes_when_expiring_within_five_minutes() -> None:
    redis_client = fakeredis.FakeStrictRedis()
    current = datetime(2026, 7, 8, 9, 0, tzinfo=UTC)
    calls = 0

    def issuer() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"access_token": f"token-{calls}", "expires_in": 600}

    manager = KISTokenManager(
        mode="mock",
        offline=False,
        redis_client=redis_client,
        issuer=issuer,
        scope="test-scope",
        now=lambda: current,
    )
    assert manager.get_access_token() == "token-1"

    current = current + timedelta(minutes=6)
    assert manager.get_access_token() == "token-2"
    assert calls == 2


def test_token_refreshes_when_cached_mode_differs() -> None:
    redis_client = fakeredis.FakeStrictRedis()
    now = datetime(2026, 7, 8, 9, 0, tzinfo=UTC)
    redis_client.set(
        _token_cache_key("shared-scope"),
        json.dumps(
            {
                "mode": "mock",
                "access_token": "mock-token",
                "expires_at": (now + timedelta(hours=1)).isoformat(),
            }
        ),
        ex=3600,
    )

    def issuer() -> dict[str, Any]:
        return {"access_token": "live-token", "expires_in": 86400}

    manager = KISTokenManager(
        mode="live",
        offline=False,
        redis_client=redis_client,
        issuer=issuer,
        scope="shared-scope",
        now=lambda: now,
    )

    assert manager.get_access_token() == "live-token"


def test_invalid_utf8_token_cache_is_treated_as_cache_miss() -> None:
    redis_client = fakeredis.FakeStrictRedis()
    redis_client.set(_token_cache_key("test-scope"), b"\xff", ex=3600)
    manager = KISTokenManager(
        mode="mock",
        offline=False,
        redis_client=redis_client,
        issuer=lambda: {"access_token": "replacement-token", "expires_in": 86400},
        scope="test-scope",
    )

    assert manager.get_access_token() == "replacement-token"


def test_concurrent_cache_miss_uses_distributed_singleflight() -> None:
    redis_client = fakeredis.FakeStrictRedis()
    calls = 0
    calls_lock = Lock()

    def issuer() -> dict[str, Any]:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return {"access_token": "shared-token", "expires_in": 86400}

    managers = [
        KISTokenManager(
            mode="mock",
            offline=False,
            redis_client=redis_client,
            issuer=issuer,
            scope="same-opaque-scope",
        )
        for _ in range(2)
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        tokens = list(pool.map(lambda manager: manager.get_access_token(), managers))

    assert tokens == ["shared-token", "shared-token"]
    assert calls == 1
