from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping

from app.data._shared.bounded_json import BoundedJsonLimits
from app.data.naver.settings import NaverSettings

_MAX_QUERY_CODEPOINTS: Final = 128
_MAX_QUERY_UTF8_BYTES: Final = 512
_RETRYABLE_STATUSES: Final = frozenset(range(500, 600))


@dataclass(frozen=True)
class NaverRequestPolicy:
    """Naver News 호출에 허용된 고정 origin·path·query·재시도 계약이다."""

    method: str
    origin: str
    path: str
    allowed_query_keys: frozenset[str]
    static_query: Mapping[str, str]
    auth_headers: tuple[str, str]
    max_attempts: int
    retryable_statuses: frozenset[int]
    documentation_url: str
    policy_url: str
    sanitization_version: str
    quota_policy_version: str


_LEGACY_POLICY: Final = NaverRequestPolicy(
    method="GET",
    origin="https://openapi.naver.com",
    path="/v1/search/news.json",
    allowed_query_keys=frozenset({"query", "display", "start", "sort"}),
    static_query=MappingProxyType({}),
    auth_headers=("X-Naver-Client-Id", "X-Naver-Client-Secret"),
    max_attempts=2,
    retryable_statuses=_RETRYABLE_STATUSES,
    documentation_url="https://developers.naver.com/docs/serviceapi/search/news/news.md",
    policy_url="https://developers.naver.com/products/terms/",
    sanitization_version="s1.3-sanitization-v1",
    quota_policy_version="s1.3-naver-legacy-quota-v1",
)

_API_HUB_POLICY: Final = NaverRequestPolicy(
    method="GET",
    origin="https://naverapihub.apigw.ntruss.com",
    path="/search/v1/news",
    allowed_query_keys=frozenset({"query", "display", "start", "sort", "format"}),
    static_query=MappingProxyType({"format": "json"}),
    auth_headers=("X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY"),
    max_attempts=2,
    retryable_statuses=_RETRYABLE_STATUSES,
    documentation_url="https://api.ncloud-docs.com/docs/naver-api-hub-search-news",
    policy_url="https://www.ncloud.com/policy/terms/svc",
    sanitization_version="s1.3-sanitization-v1",
    quota_policy_version="s1.3-naver-api-hub-quota-v1",
)


def request_policy_for(provider_profile: str) -> NaverRequestPolicy:
    """caller URL을 받지 않고 승인된 provider profile의 News 전용 정책만 반환한다."""
    if provider_profile == "naver-legacy":
        return _LEGACY_POLICY
    if provider_profile == "naver-api-hub":
        return _API_HUB_POLICY
    raise ValueError("Naver request profile is invalid")


def bounded_json_limits(settings: NaverSettings) -> BoundedJsonLimits:
    """설정으로 낮출 수 있는 byte·depth와 고정 구조 상한을 bounded JSON에 연결한다."""
    return BoundedJsonLimits(
        max_bytes=settings.response_max_bytes,
        max_depth=settings.json_max_depth,
        max_list_items=20,
        max_object_keys=16,
        max_text_codepoints=2_048,
        max_text_bytes=8_192,
        max_number_characters=10,
    )


def validate_news_query(query: str) -> str:
    """감사된 종목명을 변형하지 않고 code point·UTF-8 byte 상한 안에서 검증한다."""
    if not isinstance(query, str) or not query or not query.strip():
        raise ValueError("Naver news query is invalid")
    if len(query) > _MAX_QUERY_CODEPOINTS:
        raise ValueError("Naver news query is invalid")
    try:
        encoded = query.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Naver news query is invalid") from None
    if len(encoded) > _MAX_QUERY_UTF8_BYTES or any(
        ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F for character in query
    ):
        raise ValueError("Naver news query is invalid")
    return query
