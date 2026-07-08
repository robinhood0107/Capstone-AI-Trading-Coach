from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import fakeredis

from app.data.kis.auth import KISTokenManager
from app.data.kis.settings import KISSettings


def _settings(tmp_path: Path) -> KISSettings:
    return KISSettings(
        kis_mode="mock",
        kis_mock_app_key="mock-key",
        kis_mock_app_secret="mock-secret",
        kis_data_dir=tmp_path,
    )


def test_token_cache_miss_issues_and_stores_with_refresh_skew(tmp_path: Path) -> None:
    redis_client = fakeredis.FakeStrictRedis()
    now = datetime(2026, 7, 8, 9, 0, tzinfo=UTC)
    calls: list[str] = []

    def issuer() -> dict[str, Any]:
        calls.append("issued")
        return {"access_token": "sensitive-token", "expires_in": 86400}

    manager = KISTokenManager(_settings(tmp_path), redis_client, issuer=issuer, now=lambda: now)

    assert manager.get_access_token() == "sensitive-token"
    assert calls == ["issued"]
    assert 86090 <= redis_client.ttl("kis:token") <= 86100


def test_token_cache_hit_does_not_issue_again(tmp_path: Path) -> None:
    redis_client = fakeredis.FakeStrictRedis()
    now = datetime(2026, 7, 8, 9, 0, tzinfo=UTC)
    calls = 0

    def issuer() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"access_token": f"token-{calls}", "expires_in": 86400}

    manager = KISTokenManager(_settings(tmp_path), redis_client, issuer=issuer, now=lambda: now)

    assert manager.get_access_token() == "token-1"
    assert manager.get_access_token() == "token-1"
    assert calls == 1


def test_token_refreshes_when_expiring_within_five_minutes(tmp_path: Path) -> None:
    redis_client = fakeredis.FakeStrictRedis()
    current = datetime(2026, 7, 8, 9, 0, tzinfo=UTC)
    calls = 0

    def issuer() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"access_token": f"token-{calls}", "expires_in": 600}

    manager = KISTokenManager(_settings(tmp_path), redis_client, issuer=issuer, now=lambda: current)
    assert manager.get_access_token() == "token-1"

    current = current + timedelta(minutes=6)
    assert manager.get_access_token() == "token-2"
    assert calls == 2


def test_token_refreshes_when_cached_mode_differs(tmp_path: Path) -> None:
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

    live_settings = KISSettings(
        kis_mode="live",
        kis_live_app_key="live-key",
        kis_live_app_secret="live-secret",
        kis_data_dir=tmp_path,
        _env_file=None,
    )
    manager = KISTokenManager(live_settings, redis_client, issuer=issuer, now=lambda: now)

    assert manager.get_access_token() == "live-token"
