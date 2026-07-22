from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal


@dataclass(frozen=True)
class SourceProvenance:
    """로컬 calendar 결과가 어떤 pinned library와 calendar 이름에서 왔는지 보존한다."""

    library_name: str
    library_version: str
    calendar_name: str


@dataclass(frozen=True)
class XKRXSession:
    """네트워크 없는 XKRX base session이며 최신 임시휴장의 운영 권위를 주장하지 않는다."""

    session_date: date
    is_open: bool
    open_at: datetime | None
    close_at: datetime | None
    timezone: str
    provenance: SourceProvenance
    source_id: str = "xkrx-4.13.2"
    origin_group: str = "exchange-calendars"
    tier: int = 2

    @classmethod
    def fixture(
        cls,
        *,
        session_date: date,
        is_open: bool,
        open_at: datetime | None,
        close_at: datetime | None,
    ) -> XKRXSession:
        """테스트가 production provenance와 timezone 계약을 우회하지 않게 고정 fixture를 만든다."""
        return cls(
            session_date=session_date,
            is_open=is_open,
            open_at=open_at,
            close_at=close_at,
            timezone="Asia/Seoul",
            provenance=SourceProvenance(
                library_name="exchange-calendars",
                library_version="4.13.2",
                calendar_name="XKRX",
            ),
        )


@dataclass(frozen=True)
class KISHolidayObservation:
    """CTCA0903R의 opnd_yn과 보조 업무 flag만 보존한 sanitized observation이다."""

    day: date
    is_open: bool
    business_day_flag: bool | None
    trading_day_flag: bool | None
    settlement_day_flag: bool | None
    source_id: str
    origin_group: str
    tier: int
    tr_id: str


@dataclass(frozen=True)
class KASIReason:
    """KASI는 휴일 이름/사유만 보강하며 is_open을 표현하지 않는다."""

    day: date
    reason: str


@dataclass(frozen=True)
class CalendarConflict:
    """낮은 authority의 불일치를 canonical overwrite가 아닌 감사 가능한 충돌로 남긴다."""

    field_name: str
    chosen_value: bool | str
    competing_value: bool | str
    chosen_source_id: str
    competing_source_id: str
    resolution_rule: str


@dataclass(frozen=True)
class CanonicalTradingSession:
    """S1.6 내부 canonical session이며 public API 계약은 별도 contract-change 전까지 제공하지 않는다."""

    exchange_mic: str
    session_date: date
    is_open: bool
    open_at: datetime | None
    close_at: datetime | None
    timezone: str
    reason: str
    chosen_source_id: str
    degraded: bool
    fallback_reason: str | None
    as_of: datetime
    confidence_bps: int
    has_conflict: bool
    conflicts: tuple[CalendarConflict, ...]
    source_refs: tuple[str, ...]
    canonical_hash: str
    canonical_rule_version: str = "s1.6-session-v1"
    confidence_rule_version: str = "s1.6-confidence-v1"


@dataclass(frozen=True)
class PriorCanonicalSession:
    """KIS 실패 때만 사용할 same-date, non-expired prior canonical과 만료시각을 묶는다."""

    session: CanonicalTradingSession
    expires_at: datetime


@dataclass(frozen=True)
class NormalizedCalendarEvent:
    """adapter가 allowlisted structured field만 전달하는 provider-neutral event다."""

    source_id: str
    origin_group: str
    tier: int
    source_event_key: str
    stable_identity: str
    source_revision: str | None
    event_type: str
    symbol: str | None
    event_date: date
    detail: dict[str, Any]
    operation: str
    tr_id: str | None = None
    freshness: str = "UNVERIFIED"

    @property
    def canonical_json(self) -> str:
        """raw/provider 설명을 제외한 deterministic projection만 직렬화한다."""
        from app.data.calendar.normalizer import canonical_json

        return canonical_json(
            {
                "source_id": self.source_id,
                "source_event_key": self.source_event_key,
                "stable_identity": self.stable_identity,
                "event_type": self.event_type,
                "symbol": self.symbol,
                "event_date": self.event_date,
                "detail": self.detail,
                "operation": self.operation,
            }
        )


@dataclass(frozen=True)
class CalendarObservation:
    """provider raw 대신 저장할 immutable sanitized observation 계약이다."""

    observation_id: str
    source_id: str
    origin_group: str
    capability: str
    effective_from: date
    effective_to: date | None
    observed_at: datetime
    ingested_at: datetime
    sanitized_payload: dict[str, Any]
    sanitized_payload_hash: str
    adapter_version: str
    mapping_version: str
    registry_version: str


CursorKey = tuple[str, str, str, date, date, str]


@dataclass(frozen=True)
class CollectionCursor:
    """subject/page 재개 지점을 canonical commit과 같은 transaction에 저장한다."""

    source_id: str
    operation: str
    subject: str
    window_from: date
    window_to: date
    mapping_version: str
    next_page: int
    continuation: str | None
    completed: bool

    @property
    def key(self) -> CursorKey:
        return (
            self.source_id,
            self.operation,
            self.subject,
            self.window_from,
            self.window_to,
            self.mapping_version,
        )


@dataclass(frozen=True)
class QuotaUsage:
    """OpenDART KST 일자별 charged physical-attempt 원장의 sanitized 조회 모델이다."""

    usage_date: date
    effective_limit: int
    daily_budget: int
    physical_attempts: int
    exhausted_at: datetime | None
    exhausted_reason: str | None
    last_grant_token: str | None


TransitionType = Literal["OPEN", "CLOSE"]
