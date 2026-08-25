"""S5.1의 exact XKRX cutoff와 1,072/1,007 session arithmetic."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from functools import cache
from importlib.metadata import version
from typing import Any, cast
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd
from exchange_calendars.exchange_calendar_xkrx import XKRXExchangeCalendar

from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError

RAW_SESSION_COUNT = 1_072
WARMUP_SESSIONS = 59
LABEL_TAIL_SESSIONS = 6
# raw에서 앞쪽 warm-up과 뒤쪽 label tail을 뺀 값이다. 세 수가 어긋날 수 없게 유도한다.
ELIGIBLE_SESSION_COUNT = RAW_SESSION_COUNT - WARMUP_SESSIONS - LABEL_TAIL_SESSIONS
# 월별 universe 선정이 보는 직전 거래일 수다. 이름 없이 두면 의미를 잃는다.
MONTHLY_TRAILING_SESSIONS = 20
KST = ZoneInfo("Asia/Seoul")
_EFFECTIVE_MONTH_PATTERN = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")
PINNED_CALENDAR_VERSION = "4.13.2"
_PINNED_CALENDAR_VERSION = PINNED_CALENDAR_VERSION
S5_CALENDAR_POLICY_VERSION = "xkrx-4.13.2-kis-corrections-v1"

# CTCA0903R가 XKRX base보다 우선한다는 기존 S1.6 authority를 S5에도 적용한다.
# 고정 라이브러리에 없는 임시휴장만 contract-change로 추가하며 주말/법정휴일은 중복 기재하지 않는다.
S5_ADHOC_CLOSED_SESSIONS = (date(2026, 6, 3), date(2026, 7, 17))


def correction_set_sha256(corrections: Sequence[date]) -> str:
    """correction 세대 하나를 식별하는 결정적 해시다."""

    return hashlib.sha256(
        b"s5-xkrx-calendar-corrections-v1\x00"
        + canonical_json_bytes([day.isoformat() for day in corrections])
    ).hexdigest()


S5_CALENDAR_CORRECTION_SET_SHA256 = correction_set_sha256(S5_ADHOC_CLOSED_SESSIONS)

# 이미 소비한 packet/journal을 read-only로 검증할 때만 쓰는 이전 correction 세대다.
# 새 correction이 확정되면 직전 현재 세대를 여기 append하며, 과거 세대는 삭제하지 않는다.
# production 실행은 언제나 현재 세대만 사용한다.
S5_SUPERSEDED_CORRECTION_SETS: tuple[tuple[date, ...], ...] = (
    (),
    (date(2026, 6, 3),),
)


@dataclass(frozen=True)
class PitSessionWindow:
    """cutoff까지 완료된 raw와 labelable XKRX session의 불변 목록."""

    cutoff: datetime
    latest_completed: date
    raw_sessions: tuple[date, ...]
    eligible_sessions: tuple[date, ...]


@dataclass(frozen=True)
class MonthlyUniverseSchedule:
    """적용 월에서 XKRX가 직접 파생한 selection/evidence schedule."""

    effective_month: str
    first_effective_session: date
    evidence_cutoff: datetime
    selection_session: date
    trailing_sessions: tuple[date, ...]


class _S5XKRXCalendar(XKRXExchangeCalendar):  # type: ignore[misc]
    """Pinned XKRX에 승인된 KIS 임시휴장 correction만 덧붙인 결정적 calendar다."""

    _s5_corrections: tuple[date, ...] = ()

    @property
    def adhoc_holidays(self) -> list[pd.Timestamp]:
        values = {*super().adhoc_holidays}
        values.update(pd.Timestamp(day) for day in self._s5_corrections)
        return sorted(values)


def _require_pinned_calendar_version() -> None:
    if version("exchange-calendars") != _PINNED_CALENDAR_VERSION:
        raise LightGbmContractError("exchange-calendars version drifted from S5 policy")


@cache
def calendar_for_corrections(corrections: tuple[date, ...]) -> Any:
    """correction 세대 하나에 대응하는 결정적 calendar를 만든다.

    빈 세대는 수정 전 pinned base이며 historical packet v1 검증에만 쓴다.
    """

    _require_pinned_calendar_version()
    if not corrections:
        return xcals.get_calendar("XKRX")
    generation = type(
        "_S5XKRXCalendar",
        (_S5XKRXCalendar,),
        {"_s5_corrections": tuple(corrections)},
    )
    return generation()


def base_calendar() -> Any:
    """Historical packet v1 byte validation에만 쓰는 수정 전 pinned XKRX base다."""

    return calendar_for_corrections(())


def corrected_calendar() -> Any:
    """S5 feature/label/daily clock 전체가 공유하는 authoritative corrected XKRX다."""

    return calendar_for_corrections(S5_ADHOC_CLOSED_SESSIONS)


def corrections_for_sha256(digest: str) -> tuple[date, ...]:
    """packet이 선언한 correction set 해시를 승인된 세대로만 되돌린다.

    현재 세대와 명시적으로 보존한 이전 세대만 인정하며, 그 밖의 해시는 거부한다.
    """

    for corrections in (S5_ADHOC_CLOSED_SESSIONS, *S5_SUPERSEDED_CORRECTION_SETS):
        if correction_set_sha256(corrections) == digest:
            return tuple(corrections)
    raise LightGbmContractError("calendar correction set generation is not approved")


def latest_completed_session(cutoff: datetime) -> date:
    """timezone-aware cutoff 이전에 close가 완료된 가장 최신 XKRX 정규 session을 반환한다."""

    return _latest_completed_session(cutoff, calendar=corrected_calendar())


def _latest_completed_session(cutoff: datetime, *, calendar: Any) -> date:
    """현재/legacy calendar를 명시적으로 받아 hidden policy 전환을 막는다."""

    if cutoff.tzinfo is None:
        raise LightGbmContractError("S5 cutoff must be timezone aware")
    cutoff_kst = cutoff.astimezone(KST)
    cutoff_utc = pd.Timestamp(cutoff_kst.astimezone(UTC))
    # exchange_calendars의 date API는 timezone-naive exchange-local calendar date를 요구한다.
    candidate = calendar.date_to_session(pd.Timestamp(cutoff_kst.date()), direction="previous")
    if calendar.session_close(candidate) > cutoff_utc:
        candidate = calendar.previous_session(candidate)
    return cast(date, candidate.date())


def previous_xkrx_session(session_date: date) -> date:
    """주말·휴일을 건너뛴 직전 XKRX session을 sensitivity alignment에 제공한다."""

    return _previous_xkrx_session(session_date, calendar=corrected_calendar())


def _previous_xkrx_session(session_date: date, *, calendar: Any) -> date:
    """Packet v1 검증은 base, 현재 실행은 corrected calendar를 명시적으로 선택한다."""

    session = calendar.date_to_session(pd.Timestamp(session_date), direction="none")
    return cast(date, calendar.previous_session(session).date())


def derive_monthly_universe_schedule(
    effective_month: str,
    *,
    dataset_cutoff: datetime,
) -> MonthlyUniverseSchedule:
    """적용 월만으로 previous-session top-30 selection schedule을 파생한다.

    dataset cutoff은 historical evidence cutoff를 대신하지 않고, 파생된 월별 cutoff가
    아직 도달하지 않은 미래인지 거부하는 상한으로만 사용한다.
    """

    return derive_monthly_universe_schedule_for(
        effective_month,
        dataset_cutoff=dataset_cutoff,
        calendar=corrected_calendar(),
    )


def derive_monthly_universe_schedule_for(
    effective_month: str,
    *,
    dataset_cutoff: datetime,
    calendar: Any,
) -> MonthlyUniverseSchedule:
    """Calendar policy가 packet regeneration 동안 바뀌지 않게 explicit instance를 사용한다."""

    if not _EFFECTIVE_MONTH_PATTERN.fullmatch(effective_month):
        raise LightGbmContractError("effective month must use strict YYYY-MM")
    if dataset_cutoff.tzinfo is None:
        raise LightGbmContractError("S5 dataset cutoff must be timezone aware")
    year, month = (int(value) for value in effective_month.split("-"))
    first_day = date(year, month, 1)
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last_day = date.fromordinal(next_month.toordinal() - 1)

    sessions = calendar.sessions_in_range(pd.Timestamp(first_day), pd.Timestamp(last_day))
    if len(sessions) == 0:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: effective month has no XKRX session")
    first = sessions[0]
    selection = calendar.previous_session(first)
    trailing_first = calendar.session_offset(selection, -(MONTHLY_TRAILING_SESSIONS - 1))
    trailing_index = calendar.sessions_in_range(trailing_first, selection)
    trailing = tuple(session.date() for session in trailing_index)
    evidence_cutoff = datetime.combine(first.date(), time(8, 10), tzinfo=KST)

    previous_month = 12 if month == 1 else month - 1
    previous_year = year - 1 if month == 1 else year
    if (
        len(trailing) != MONTHLY_TRAILING_SESSIONS
        or trailing[-1] != selection.date()
        or (selection.year, selection.month) != (previous_year, previous_month)
    ):
        raise DatasetUnavailable("DATASET_UNAVAILABLE: monthly XKRX schedule is incomplete")
    if evidence_cutoff > dataset_cutoff.astimezone(KST):
        raise DatasetUnavailable("DATASET_UNAVAILABLE: effective month is beyond dataset cutoff")
    return MonthlyUniverseSchedule(
        effective_month=effective_month,
        first_effective_session=first.date(),
        evidence_cutoff=evidence_cutoff,
        selection_session=selection.date(),
        trailing_sessions=trailing,
    )


def build_pit_session_window(cutoff: datetime) -> PitSessionWindow:
    """exact 1,072 raw sessions와 t-59..t warm-up 뒤 1,007 labelable sessions를 만든다."""

    return build_pit_session_window_for(cutoff, calendar=corrected_calendar())


def build_pit_session_window_for(cutoff: datetime, *, calendar: Any) -> PitSessionWindow:
    """현재와 historical packet validator가 같은 arithmetic을 calendar별로 재사용한다."""

    latest = pd.Timestamp(_latest_completed_session(cutoff, calendar=calendar))
    first = calendar.session_offset(latest, -(RAW_SESSION_COUNT - 1))
    raw_index = calendar.sessions_in_range(first, latest)
    raw = tuple(session.date() for session in raw_index)
    if len(raw) != RAW_SESSION_COUNT:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: exact 1,072 XKRX sessions are unavailable")
    eligible = raw[WARMUP_SESSIONS:-LABEL_TAIL_SESSIONS]
    if len(eligible) != ELIGIBLE_SESSION_COUNT:
        raise DatasetUnavailable(
            "DATASET_UNAVAILABLE: exact 1,007 labelable sessions are unavailable"
        )
    return PitSessionWindow(cutoff, latest.date(), raw, eligible)
