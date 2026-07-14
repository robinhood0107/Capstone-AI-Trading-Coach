from __future__ import annotations

import pytest

from app.data.naver.policy import bounded_json_limits, request_policy_for, validate_news_query
from app.data.naver.settings import NaverSettings


def test_request_policy_is_news_only_and_profile_fixed() -> None:
    legacy = request_policy_for("naver-legacy")
    hub = request_policy_for("naver-api-hub")

    assert (legacy.method, legacy.origin, legacy.path) == (
        "GET",
        "https://openapi.naver.com",
        "/v1/search/news.json",
    )
    assert (hub.method, hub.origin, hub.path) == (
        "GET",
        "https://naverapihub.apigw.ntruss.com",
        "/search/v1/news",
    )
    assert legacy.allowed_query_keys == frozenset({"query", "display", "start", "sort"})
    assert hub.allowed_query_keys == frozenset({"query", "display", "start", "sort", "format"})
    assert legacy.static_query == {}
    assert hub.static_query == {"format": "json"}
    assert legacy.auth_headers == ("X-Naver-Client-Id", "X-Naver-Client-Secret")
    assert hub.auth_headers == ("X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY")
    assert set(legacy.auth_headers).isdisjoint(hub.auth_headers)
    assert legacy.documentation_url == (
        "https://developers.naver.com/docs/serviceapi/search/news/news.md"
    )
    assert legacy.policy_url == "https://developers.naver.com/products/terms/"
    assert hub.documentation_url == "https://api.ncloud-docs.com/docs/naver-api-hub-search-news"
    assert hub.policy_url == "https://www.ncloud.com/policy/terms/svc"


def test_policy_rejects_unknown_profile_instead_of_accepting_caller_origin() -> None:
    with pytest.raises(ValueError, match="profile"):
        request_policy_for("https://attacker.invalid/search")


def test_bounded_json_policy_uses_lower_only_settings_and_fixed_structure_caps() -> None:
    limits = bounded_json_limits(
        NaverSettings(
            NAVER_RESPONSE_MAX_BYTES=256 * 1024,
            NAVER_JSON_MAX_DEPTH=5,
            _env_file=None,
        )
    )

    assert limits.max_bytes == 256 * 1024
    assert limits.max_depth == 5
    assert limits.max_list_items == 20
    assert limits.max_object_keys == 16
    assert limits.max_text_codepoints == 2_048
    assert limits.max_text_bytes == 8_192
    assert limits.max_number_characters == 10


def test_news_retry_policy_allows_only_one_retry() -> None:
    for profile in ("naver-legacy", "naver-api-hub"):
        policy = request_policy_for(profile)
        assert policy.max_attempts == 2
        assert policy.retryable_statuses == frozenset(range(500, 600))


def test_news_query_is_bounded_by_codepoints_and_utf8_bytes() -> None:
    assert validate_news_query("합성회사") == "합성회사"
    assert validate_news_query("가" * 128) == "가" * 128
    assert validate_news_query("😀" * 128) == "😀" * 128
    assert len(("😀" * 128).encode("utf-8")) == 512

    with pytest.raises(ValueError, match="query"):
        validate_news_query("x" * 129)
    with pytest.raises(ValueError, match="query"):
        validate_news_query("\ud800")
