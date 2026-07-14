from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.data.naver.profiles import API_HUB_PROFILE, LEGACY_PROFILE, profile_for


def test_profiles_keep_origin_path_and_auth_headers_separate() -> None:
    assert LEGACY_PROFILE.origin == "https://openapi.naver.com"
    assert LEGACY_PROFILE.path == "/v1/search/news.json"
    assert LEGACY_PROFILE.auth_headers == ("X-Naver-Client-Id", "X-Naver-Client-Secret")
    assert LEGACY_PROFILE.enabled is True

    assert API_HUB_PROFILE.origin == "https://naverapihub.apigw.ntruss.com"
    assert API_HUB_PROFILE.path == "/search/v1/news"
    assert API_HUB_PROFILE.auth_headers == ("X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY")
    assert API_HUB_PROFILE.enabled is False
    assert set(LEGACY_PROFILE.auth_headers).isdisjoint(API_HUB_PROFILE.auth_headers)


def test_hub_is_disabled_ready_until_operator_cutover() -> None:
    with pytest.raises(ValueError, match="profile_disabled"):
        profile_for("api-hub", now=datetime(2026, 12, 1, tzinfo=timezone.utc))


def test_legacy_hard_stops_at_documented_kst_deadline() -> None:
    before = datetime(2027, 6, 29, 14, 59, 59, tzinfo=timezone.utc)
    at_deadline = datetime(2027, 6, 29, 15, 0, 0, tzinfo=timezone.utc)

    assert profile_for("legacy", now=before) is LEGACY_PROFILE
    with pytest.raises(ValueError, match="legacy_hard_stop"):
        profile_for("legacy", now=at_deadline)
