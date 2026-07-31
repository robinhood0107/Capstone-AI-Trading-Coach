from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Never, cast

from app.data._shared.bounded_json import (
    BoundedJsonError,
    BoundedJsonLimits,
    parse_bounded_json_bytes,
)
from app.data.gdelt.errors import GdeltAggregateError
from app.data.gdelt.policy import QueryDefinition
from app.data.gdelt.transport import ALLOWED_MODES, GDELT_ORIGIN, GDELT_PATH

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_HEAD_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_PACKET_KEYS = {
    "schemaVersion",
    "headSha",
    "origin",
    "path",
    "format",
    "modes",
    "queryRegistryId",
    "queryDefinitionHash",
    "windowStart",
    "windowEnd",
    "physicalCap",
    "retryCount",
    "persistRaw",
    "attribution",
    "operatorPurpose",
    "expiresAt",
}
_LIMITS = BoundedJsonLimits(
    max_bytes=64 * 1024,
    max_depth=3,
    max_list_items=2,
    max_object_keys=len(_PACKET_KEYS),
    max_text_codepoints=512,
    max_text_bytes=2048,
    max_number_characters=4,
)


@dataclass(frozen=True)
class ValidatedApprovalPacket:
    """exact GDELT query/window와 물리 상한에 결속된 online 승인 결과다."""

    packet_sha256: str
    physical_cap: int
    retry_count: int
    persist_raw: bool
    window_start: datetime
    window_end: datetime


def validate_approval_packet(
    *,
    content: bytes,
    expected_sha256: str,
    expected_head_sha: str,
    query: QueryDefinition,
    now: datetime,
) -> ValidatedApprovalPacket:
    """packet hash·HEAD·query·window·expiry·zero-retry를 실제 provider 호출 전에 검증한다.

    packet 내용은 로그나 artifact로 전달하지 않으며 이 검증 성공만으로 online transport가
    활성화되지는 않는다.
    """

    if _SHA256_PATTERN.fullmatch(expected_sha256) is None:
        _reject()
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != expected_sha256 or _HEAD_PATTERN.fullmatch(expected_head_sha) is None:
        _reject()
    try:
        payload = parse_bounded_json_bytes(content, limits=_LIMITS)
    except BoundedJsonError:
        _reject()
    if not isinstance(payload, dict) or set(payload) != _PACKET_KEYS:
        _reject()
    mapping = cast(dict[str, object], payload)
    canonical = json.dumps(
        mapping,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if content != canonical:
        _reject()
    if (
        mapping.get("schemaVersion") != "1"
        or mapping.get("headSha") != expected_head_sha
        or mapping.get("origin") != GDELT_ORIGIN
        or mapping.get("path") != GDELT_PATH
        or mapping.get("format") != "json"
        or mapping.get("modes") != list(ALLOWED_MODES)
        or mapping.get("queryRegistryId") != query.query_registry_id
        or mapping.get("queryDefinitionHash") != query.definition_hash
        or mapping.get("physicalCap") != 1
        or mapping.get("retryCount") != 0
        or mapping.get("persistRaw") is not False
        or mapping.get("attribution") != "The GDELT Project"
    ):
        _reject()
    purpose = mapping.get("operatorPurpose")
    if not isinstance(purpose, str) or not 1 <= len(purpose) <= 200:
        _reject()
    window_start = _parse_utc(mapping.get("windowStart"))
    window_end = _parse_utc(mapping.get("windowEnd"))
    expires_at = _parse_utc(mapping.get("expiresAt"))
    now_utc = _aware_utc(now)
    if not window_start < window_end or now_utc >= expires_at:
        _reject()
    return ValidatedApprovalPacket(
        packet_sha256=actual_sha256,
        physical_cap=1,
        retry_count=0,
        persist_raw=False,
        window_start=window_start,
        window_end=window_end,
    )


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str):
        _reject()
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        _reject()
    return parsed


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        _reject()
    return value.astimezone(UTC)


def _reject() -> Never:
    raise GdeltAggregateError("PROVIDER_DISABLED", "approval packet is invalid or drifted")
