from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.data.naver.errors import NaverResponseError
from app.data.naver.parsers import parse_news_response, raise_for_naver_error


_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "naver"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_legacy_success_preserves_provider_counts_and_sanitizes_metadata() -> None:
    page = parse_news_response(
        _fixture("legacy_news_success.json"),
        profile="naver-legacy",
        retrieved_at=datetime(2026, 7, 14, 1, 0, tzinfo=timezone.utc),
        requested_display=10,
    )

    assert page.provider_total == 2
    assert page.provider_display == 2
    assert page.received_count == 2
    assert page.accepted_count == 2
    assert page.filtered_count == 0
    assert page.redacted_url_count == 1
    assert page.items[0].title == "합성전자 실적 전망"
    assert page.items[0].description == "합성 fixture 설명"
    assert page.items[0].provider_pub_date == "2026-07-14T00:30:00Z"
    assert page.items[1].original_url is None
    assert page.items[1].naver_url is not None


def test_api_hub_success_uses_the_same_normalized_model() -> None:
    page = parse_news_response(
        _fixture("api_hub_news_success.json"),
        profile="naver-api-hub",
        retrieved_at=datetime(2026, 7, 14, 1, 0, tzinfo=timezone.utc),
        requested_display=10,
    )

    assert page.accepted_count == 1
    assert page.items[0].title == "API Hub 합성 뉴스"


@pytest.mark.parametrize("bad_date", ["not-a-date", "Tue, 14 Jul 1969 09:30:00 +0900"])
def test_invalid_or_out_of_range_pubdate_drops_the_item(bad_date: str) -> None:
    payload = _fixture("legacy_news_success.json")
    payload["items"][0]["pubDate"] = bad_date

    page = parse_news_response(
        payload,
        profile="naver-legacy",
        retrieved_at=datetime(2026, 7, 14, 1, 0, tzinfo=timezone.utc),
        requested_display=10,
    )

    assert page.accepted_count == 1
    assert page.filtered_count == 1


def test_provider_errors_map_to_stable_sanitized_taxonomy() -> None:
    for case in _fixture("legacy_news_error_cases.json")["cases"]:
        with pytest.raises(NaverResponseError) as exc_info:
            raise_for_naver_error(case["status"], case["payload"], profile="naver-legacy")

        error = exc_info.value
        assert error.code == case["code"]
        assert error.retryable is case["retryable"]
        assert "synthetic" not in f"{error!r} {error}"
        assert error.__cause__ is None


def test_api_hub_gateway_errors_map_to_stable_sanitized_taxonomy() -> None:
    for case in _fixture("api_hub_gateway_error_cases.json")["cases"]:
        with pytest.raises(NaverResponseError) as exc_info:
            raise_for_naver_error(case["status"], case["payload"], profile="naver-api-hub")

        error = exc_info.value
        assert error.code == case["code"]
        assert error.retryable is case["retryable"]
        assert "synthetic" not in f"{error!r} {error}"
        assert error.__cause__ is None
