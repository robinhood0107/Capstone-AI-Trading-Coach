from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.data.calendar.models import NormalizedCalendarEvent
from app.data.calendar.privacy import assert_sanitized_payload


CALENDAR_EVENT_TYPES = frozenset(
    {
        "EARNINGS_EXPECTED",
        "EARNINGS_ACTUAL",
        "DIVIDEND_EX",
        "DIVIDEND_RECORD",
        "DIVIDEND_PAY",
        "SPLIT",
        "RIGHTS_ISSUE",
        "BONUS_ISSUE",
        "IPO_SUBSCRIPTION",
        "IPO_LISTING",
        "SHAREHOLDER_MEETING",
        "MERGER_SPLIT",
        "CAPITAL_REDUCTION",
        "DISCLOSURE",
        "MACRO_RELEASE",
    }
)


def canonical_json(value: object) -> str:
    """collection 순서와 locale에 무관한 UTF-8 canonical JSON을 만든다."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def canonical_hash(value: object) -> str:
    """sanitized canonical projection만 SHA-256으로 식별하고 provider raw hash는 받지 않는다."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def event_series_key(source_id: str, stable_identity: str) -> str:
    """correctable event date를 제외한 source identity와 stable identity로 series를 만든다."""
    if not source_id or not stable_identity:
        raise ValueError("source_id and stable_identity are required")
    return canonical_hash({"source_id": source_id, "stable_identity": stable_identity})


@dataclass(frozen=True)
class EventCandidate:
    """새 event revision을 만들기 전의 allowlisted canonical 후보다."""

    source_id: str
    source_event_key: str
    stable_identity: str
    source_revision: str | None
    event_type: str
    symbol: str | None
    exchange_mic: str
    event_date: date
    detail: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """테스트/mapper가 field 이름을 재사용하되 immutable instance 자체는 바꾸지 않게 한다."""
        return {
            "source_id": self.source_id,
            "source_event_key": self.source_event_key,
            "stable_identity": self.stable_identity,
            "source_revision": self.source_revision,
            "event_type": self.event_type,
            "symbol": self.symbol,
            "exchange_mic": self.exchange_mic,
            "event_date": self.event_date,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class EventRevision:
    """series 안에서 append-only로 증가하는 immutable event revision이다."""

    event_id: str
    event_series_key: str
    revision_no: int
    revised_from_event_id: str | None
    canonical_hash: str
    candidate: EventCandidate


def event_candidate_from_normalized(event: NormalizedCalendarEvent) -> EventCandidate:
    """adapter output을 privacy scan한 뒤 immutable repository candidate로 변환한다."""
    assert_sanitized_payload(event.detail)
    return EventCandidate(
        source_id=event.source_id,
        source_event_key=event.source_event_key,
        stable_identity=event.stable_identity,
        source_revision=event.source_revision,
        event_type=event.event_type,
        symbol=event.symbol,
        exchange_mic=event.exchange_mic,
        event_date=event.event_date,
        detail=dict(event.detail),
    )


def build_event_revision(
    candidate: EventCandidate,
    *,
    previous: EventRevision | None = None,
) -> EventRevision:
    """동일 sanitized payload는 기존 revision을 재사용하고 correction만 새 row로 만든다."""
    assert_sanitized_payload(candidate.detail)
    if candidate.event_type not in CALENDAR_EVENT_TYPES:
        raise ValueError("calendar event type is outside the frozen v1 enum")
    if (
        len(candidate.exchange_mic) != 4
        or not candidate.exchange_mic.isascii()
        or not candidate.exchange_mic.isalnum()
        or candidate.exchange_mic != candidate.exchange_mic.upper()
    ):
        raise ValueError("calendar event exchange MIC is invalid")
    series_key = event_series_key(candidate.source_id, candidate.stable_identity)
    payload_hash = canonical_hash(
        {
            "source_id": candidate.source_id,
            "source_event_key": candidate.source_event_key,
            "event_type": candidate.event_type,
            "symbol": candidate.symbol,
            "exchange_mic": candidate.exchange_mic,
            "event_date": candidate.event_date,
            "detail": candidate.detail,
        }
    )
    if previous is not None:
        if previous.event_series_key != series_key:
            raise ValueError("previous event belongs to another series")
        if previous.canonical_hash == payload_hash:
            return previous
        revision_no = previous.revision_no + 1
        revised_from = previous.event_id
    else:
        revision_no = 1
        revised_from = None
    event_id = canonical_hash(
        {"event_series_key": series_key, "revision_no": revision_no, "canonical_hash": payload_hash}
    )
    return EventRevision(
        event_id=event_id,
        event_series_key=series_key,
        revision_no=revision_no,
        revised_from_event_id=revised_from,
        canonical_hash=payload_hash,
        candidate=candidate,
    )


def _json_default(value: object) -> str | int:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"unsupported canonical value type: {type(value).__name__}")
