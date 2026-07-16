from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Final, cast

from app.data.naver.errors import NaverErrorCode, NaverParseError, NaverResponseError
from app.data.naver.models import NaverNewsItem, NaverNewsPage
from app.data.naver.sanitizer import NaverSanitizationError, sanitize_news_text
from app.data.naver.url_metadata import normalize_metadata_url


_PROFILES: Final = frozenset({"naver-legacy", "naver-api-hub"})
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_MAX_ITEMS: Final = 20
_MAX_PUB_DATE_CHARS: Final = 128
_LEGACY_VALIDATION_CODES: Final = frozenset({"SE01", "SE02", "SE03", "SE04", "SE05", "SE06"})


def raise_for_naver_error(
    status: int,
    payload: Mapping[str, object],
    *,
    profile: str,
) -> None:
    """Legacy/Hub 오류를 provider message 비노출의 고정 taxonomy로 변환한다.

    401/403/429와 validation 오류는 재시도하지 않고, HTTP 5xx 또는 `SE99`만
    안전한 GET retry 후보로 표시한다.
    """
    _require_profile(profile)
    if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
        raise NaverParseError() from None

    provider_code = _provider_error_code(payload)
    effective_status = status if not 200 <= status < 300 else _embedded_status(payload)
    if 200 <= status < 300 and provider_code is None and effective_status is None:
        return

    code, retryable = _classify_error(effective_status or status, provider_code)
    raise NaverResponseError(code, retryable=retryable) from None


def parse_news_response(
    payload: Mapping[str, object],
    *,
    profile: str,
    retrieved_at: datetime,
    requested_display: int,
) -> NaverNewsPage:
    """Legacy/Hub News 응답을 동일한 sanitize-only page model로 정규화한다.

    provider count는 그대로 보존하되 URL은 field별로 redaction하고, URL 두 개가 모두
    unsafe이거나 text/date 계약이 깨진 항목은 원문 없이 drop count로만 기록한다.
    """
    _require_profile(profile)
    retrieved_at_utc = _aware_utc(retrieved_at)
    if (
        isinstance(requested_display, bool)
        or not isinstance(requested_display, int)
        or not 1 <= requested_display <= _MAX_ITEMS
    ):
        raise NaverParseError() from None

    # 일부 provider 오류는 성공 HTTP status의 JSON body로도 오므로 성공 envelope보다 먼저 거른다.
    if _provider_error_code(payload) is not None or _embedded_status(payload) is not None:
        raise_for_naver_error(200, payload, profile=profile)

    provider_total = _bounded_int(payload.get("total"), upper=2_147_483_647)
    provider_display = _bounded_int(payload.get("display"), upper=_MAX_ITEMS)
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or len(raw_items) > _MAX_ITEMS:
        raise NaverParseError() from None

    normalized_items: list[NaverNewsItem] = []
    filtered_count = 0
    redacted_url_count = 0
    for raw_item in raw_items:
        item, item_redactions = _parse_item(raw_item, retrieved_at=retrieved_at_utc)
        redacted_url_count += item_redactions
        if item is None:
            filtered_count += 1
        else:
            normalized_items.append(item)

    received_count = len(raw_items)
    return NaverNewsPage(
        status="empty" if provider_total == 0 and received_count == 0 else "complete",
        providerTotal=provider_total,
        requestedDisplay=requested_display,
        providerDisplay=provider_display,
        receivedCount=received_count,
        acceptedCount=len(normalized_items),
        filteredCount=filtered_count,
        redactedUrlCount=redacted_url_count,
        items=normalized_items,
    )


def _parse_item(
    value: object,
    *,
    retrieved_at: datetime,
) -> tuple[NaverNewsItem | None, int]:
    if not isinstance(value, Mapping):
        return None, 0
    row = cast(Mapping[str, object], value)

    original_url = _normalized_url(row.get("originallink"))
    naver_url = _normalized_url(row.get("link"))
    redacted_count = int(original_url is None) + int(naver_url is None)
    if original_url is None and naver_url is None:
        return None, redacted_count

    title_value = row.get("title")
    description_value = row.get("description")
    if not isinstance(title_value, str) or not isinstance(description_value, str):
        return None, redacted_count
    try:
        title = sanitize_news_text(
            title_value,
            max_code_points=512,
            max_utf8_bytes=2_048,
        )
        description = sanitize_news_text(
            description_value,
            max_code_points=2_048,
            max_utf8_bytes=8_192,
        )
    except NaverSanitizationError:
        return None, redacted_count

    provider_pub_date = _provider_pub_date(row.get("pubDate"), retrieved_at=retrieved_at)
    if provider_pub_date is None:
        return None, redacted_count
    return (
        NaverNewsItem(
            title=title,
            description=description,
            originalUrl=original_url,
            naverUrl=naver_url,
            providerPubDate=provider_pub_date,
        ),
        redacted_count,
    )


def _provider_pub_date(value: object, *, retrieved_at: datetime) -> str | None:
    if not isinstance(value, str) or not value or len(value) > _MAX_PUB_DATE_CHARS:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    parsed_utc = parsed.astimezone(UTC)
    if parsed_utc < _EPOCH or parsed_utc > retrieved_at + timedelta(hours=24):
        return None
    return parsed_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalized_url(value: object) -> str | None:
    return normalize_metadata_url(value) if isinstance(value, str) else None


def _bounded_int(value: object, *, upper: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= upper:
        raise NaverParseError() from None
    return value


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise NaverParseError() from None
    try:
        normalized = value.astimezone(UTC)
        # pubDate upper-bound 계산이 overflow하지 않는 범위도 입력 계약으로 고정한다.
        _ = normalized + timedelta(hours=24)
    except (OverflowError, ValueError):
        raise NaverParseError() from None
    return normalized


def _require_profile(profile: str) -> None:
    if profile not in _PROFILES:
        raise NaverParseError() from None


def _provider_error_code(payload: Mapping[str, object]) -> str | None:
    direct = _bounded_error_code(payload.get("errorCode"))
    if direct is not None:
        return direct
    for key in ("error", "response"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            code = _bounded_error_code(nested.get("errorCode"))
            if code is None:
                code = _bounded_error_code(nested.get("code"))
            if code is not None:
                return code
    return None


def _embedded_status(payload: Mapping[str, object]) -> int | None:
    for container in (payload, payload.get("error"), payload.get("response")):
        if not isinstance(container, Mapping):
            continue
        for key in ("status", "statusCode", "httpStatus"):
            value = container.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and 400 <= value <= 599:
                return value
    return None


def _bounded_error_code(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    return value


def _classify_error(status: int, provider_code: str | None) -> tuple[NaverErrorCode, bool]:
    if status in {401, 403}:
        return "authentication_failed", False
    if status == 429:
        return "rate_limited", False
    if status >= 500 or provider_code == "SE99":
        return "provider_unavailable", True
    if status >= 400 or provider_code in _LEGACY_VALIDATION_CODES:
        return "invalid_query", False
    return "invalid_response", False
