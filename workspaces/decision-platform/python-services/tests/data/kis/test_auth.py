from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis
import httpx
import pytest
from pydantic import SecretStr

from app.data.kis import _credential_transport
from app.data.kis._credential_transport import KISCredentialError, _Credentials, _TokenIssuer
from app.data.kis.auth import KISTokenManager
from app.data.kis.settings import KISSettings


def test_token_issuer_injects_credentials_only_inside_private_fixed_origin_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_key = "validation-dummy-key"
    app_secret = "validation-dummy-secret"
    monkeypatch.setattr(
        _credential_transport,
        "_read_credentials",
        lambda _: _Credentials(app_key=SecretStr(app_key), app_secret=SecretStr(app_secret)),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://openapivts.koreainvestment.com:29443/oauth2/tokenP"
        assert request.read().decode().find(app_key) >= 0
        assert request.read().decode().find(app_secret) >= 0
        return httpx.Response(
            200,
            json={"access_token": "validation-dummy-token", "expires_in": 86400, "appsecret": app_secret},
        )

    issuer = _TokenIssuer(
        KISSettings(kis_mode="mock", kis_offline=False, _env_file=None),
        transport=httpx.MockTransport(handler),
    )

    assert issuer.issue() == {"access_token": "validation-dummy-token", "expires_in": 86400}
    assert app_key not in repr(vars(issuer))
    assert app_secret not in repr(vars(issuer))


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
    )

    with pytest.raises(KISCredentialError) as exc_info:
        issuer.issue()

    assert marker not in f"{exc_info.value!r} {exc_info.value}"


def test_token_cache_miss_issues_and_stores_with_refresh_skew() -> None:
    redis_client = fakeredis.FakeStrictRedis()
    now = datetime(2026, 7, 8, 9, 0, tzinfo=UTC)
    calls: list[str] = []

    def issuer() -> dict[str, Any]:
        calls.append("issued")
        return {"access_token": "sensitive-token", "expires_in": 86400}

    manager = KISTokenManager(mode="mock", offline=False, redis_client=redis_client, issuer=issuer, now=lambda: now)

    assert manager.get_access_token() == "sensitive-token"
    assert calls == ["issued"]
    assert 86090 <= redis_client.ttl("kis:token") <= 86100


def test_token_cache_hit_does_not_issue_again() -> None:
    redis_client = fakeredis.FakeStrictRedis()
    now = datetime(2026, 7, 8, 9, 0, tzinfo=UTC)
    calls = 0

    def issuer() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"access_token": f"token-{calls}", "expires_in": 86400}

    manager = KISTokenManager(mode="mock", offline=False, redis_client=redis_client, issuer=issuer, now=lambda: now)

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

    manager = KISTokenManager(mode="mock", offline=False, redis_client=redis_client, issuer=issuer, now=lambda: current)
    assert manager.get_access_token() == "token-1"

    current = current + timedelta(minutes=6)
    assert manager.get_access_token() == "token-2"
    assert calls == 2


def test_token_refreshes_when_cached_mode_differs() -> None:
    redis_client = fakeredis.FakeStrictRedis()
    now = datetime(2026, 7, 8, 9, 0, tzinfo=UTC)
    redis_client.set(
        "kis:token",
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

    manager = KISTokenManager(mode="live", offline=False, redis_client=redis_client, issuer=issuer, now=lambda: now)

    assert manager.get_access_token() == "live-token"
