from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import cast

from app.data._shared.bounded_json import (
    BoundedJsonError,
    BoundedJsonLimits,
    parse_bounded_json_bytes,
)
from app.data.gdelt.errors import GdeltAggregateError

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_POINTS = 512
_LIMITS = BoundedJsonLimits(
    max_bytes=MAX_RESPONSE_BYTES,
    max_depth=4,
    max_list_items=MAX_POINTS,
    max_object_keys=4,
    max_text_codepoints=64,
    max_text_bytes=128,
    max_number_characters=32,
)
_RATIO_QUANTUM = Decimal("0.00000001")


def parse_aggregate_modes(
    *,
    tone_bytes: bytes,
    volume_bytes: bytes,
    window_start: datetime,
    window_end: datetime,
) -> list[dict[str, object]]:
    """두 allowlisted mode의 합성 JSON을 strict parsing해 canonical aggregate만 반환한다.

    입력 bytes는 저장·로그에 남기지 않으며 article-shaped field, partial timestamp set, norm 0은
    numeric zero로 바꾸지 않고 typed 실패로 수렴한다.
    """

    start = _utc_datetime(window_start, field="window_start")
    end = _utc_datetime(window_end, field="window_end")
    if start >= end:
        raise GdeltAggregateError("INVALID_RESPONSE", "window is invalid")
    tone = _parse_mode(tone_bytes, mode="tone", window_start=start, window_end=end)
    volume = _parse_mode(volume_bytes, mode="volume", window_start=start, window_end=end)
    if not tone and not volume:
        raise GdeltAggregateError("EMPTY_WINDOW", "aggregate window is empty")
    if set(tone) != set(volume):
        raise GdeltAggregateError("INCOMPLETE_SOURCE", "aggregate modes have different points")

    points: list[dict[str, object]] = []
    for timestamp in sorted(tone):
        average_tone = cast(Decimal, tone[timestamp])
        article_count, norm = cast(tuple[int, int], volume[timestamp])
        if norm == 0:
            raise GdeltAggregateError("NORM_ZERO", "aggregate norm is zero")
        if article_count > norm:
            raise GdeltAggregateError("INVALID_RESPONSE", "count exceeds norm")
        coverage = (Decimal(article_count) / Decimal(norm)).quantize(
            _RATIO_QUANTUM,
            rounding=ROUND_HALF_EVEN,
        )
        points.append(
            {
                "timestamp": _format_utc(timestamp),
                "averageTone": _canonical_number(average_tone),
                "articleCount": article_count,
                "norm": norm,
                "coverageRatio": _canonical_number(coverage),
            }
        )
    return points


def _parse_mode(
    content: bytes,
    *,
    mode: str,
    window_start: datetime,
    window_end: datetime,
) -> dict[datetime, Decimal | tuple[int, int]]:
    try:
        payload = parse_bounded_json_bytes(content, limits=_LIMITS)
    except BoundedJsonError:
        raise GdeltAggregateError("INVALID_RESPONSE", "aggregate JSON is invalid") from None
    if not isinstance(payload, dict) or set(payload) != {"timeline"}:
        raise GdeltAggregateError("INVALID_RESPONSE", "aggregate envelope is invalid")
    timeline = payload.get("timeline")
    if not isinstance(timeline, list):
        raise GdeltAggregateError("INVALID_RESPONSE", "aggregate timeline is invalid")

    result: dict[datetime, Decimal | tuple[int, int]] = {}
    for raw_point in timeline:
        if not isinstance(raw_point, dict):
            raise GdeltAggregateError("INVALID_RESPONSE", "aggregate point is invalid")
        expected = {"date", "value"} if mode == "tone" else {"date", "value", "norm"}
        if set(raw_point) != expected:
            raise GdeltAggregateError("INVALID_RESPONSE", "aggregate point fields are invalid")
        timestamp = _parse_provider_timestamp(raw_point.get("date"))
        if not window_start <= timestamp < window_end or timestamp in result:
            raise GdeltAggregateError("INVALID_RESPONSE", "aggregate timestamp is invalid")
        if mode == "tone":
            tone = _decimal(raw_point.get("value"))
            if not Decimal("-100") <= tone <= Decimal("100"):
                raise GdeltAggregateError("INVALID_RESPONSE", "aggregate tone is invalid")
            result[timestamp] = tone
        else:
            count = _integer(raw_point.get("value"))
            norm = _integer(raw_point.get("norm"))
            if count < 0 or norm < 0 or count > 1_000_000_000 or norm > 10_000_000_000:
                raise GdeltAggregateError("INVALID_RESPONSE", "aggregate count is invalid")
            result[timestamp] = (count, norm)
    return result


def _parse_provider_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise GdeltAggregateError("INVALID_RESPONSE", "aggregate timestamp is invalid")
    try:
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise GdeltAggregateError("INVALID_RESPONSE", "aggregate timestamp is invalid") from None
    return parsed


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise GdeltAggregateError("INVALID_RESPONSE", "aggregate number is invalid")
    try:
        converted = Decimal(str(value))
    except InvalidOperation:
        raise GdeltAggregateError("INVALID_RESPONSE", "aggregate number is invalid") from None
    if not converted.is_finite():
        raise GdeltAggregateError("INVALID_RESPONSE", "aggregate number is invalid")
    return converted


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GdeltAggregateError("INVALID_RESPONSE", "aggregate integer is invalid")
    return value


def _utc_datetime(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise GdeltAggregateError("INVALID_RESPONSE", f"{field} must be timezone aware")
    return value.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value)
