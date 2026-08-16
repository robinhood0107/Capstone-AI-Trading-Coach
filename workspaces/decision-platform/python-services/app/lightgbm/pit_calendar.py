"""S5.1의 exact XKRX cutoff와 1,072/1,007 session arithmetic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from functools import lru_cache
import re
from typing import Any, cast
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError


RAW_SESSION_COUNT = 1_072
WARMUP_SESSIONS = 59
LABEL_TAIL_SESSIONS = 6
ELIGIBLE_SESSION_COUNT = 1_007
KST = ZoneInfo("Asia/Seoul")
_EFFECTIVE_MONTH_PATTERN = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")


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


@lru_cache(maxsize=1)
def _calendar() -> Any:
    return xcals.get_calendar("XKRX")


def latest_completed_session(cutoff: datetime) -> date:
    """timezone-aware cutoff 이전에 close가 완료된 가장 최신 XKRX 정규 session을 반환한다."""

    if cutoff.tzinfo is None:
        raise LightGbmContractError("S5 cutoff must be timezone aware")
    calendar = _calendar()
    cutoff_kst = cutoff.astimezone(KST)
    cutoff_utc = pd.Timestamp(cutoff_kst.astimezone(UTC))
    # exchange_calendars의 date API는 timezone-naive exchange-local calendar date를 요구한다.
    candidate = calendar.date_to_session(pd.Timestamp(cutoff_kst.date()), direction="previous")
    if calendar.session_close(candidate) > cutoff_utc:
        candidate = calendar.previous_session(candidate)
    return cast(date, candidate.date())


def previous_xkrx_session(session_date: date) -> date:
    """주말·휴일을 건너뛴 직전 XKRX session을 sensitivity alignment에 제공한다."""

    calendar = _calendar()
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

    if not _EFFECTIVE_MONTH_PATTERN.fullmatch(effective_month):
        raise LightGbmContractError("effective month must use strict YYYY-MM")
    if dataset_cutoff.tzinfo is None:
        raise LightGbmContractError("S5 dataset cutoff must be timezone aware")
    year, month = (int(value) for value in effective_month.split("-"))
    first_day = date(year, month, 1)
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = date.fromordinal(next_month.toordinal() - 1)

    calendar = _calendar()
    sessions = calendar.sessions_in_range(pd.Timestamp(first_day), pd.Timestamp(last_day))
    if len(sessions) == 0:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: effective month has no XKRX session")
    first = sessions[0]
    selection = calendar.previous_session(first)
    trailing_first = calendar.session_offset(selection, -19)
    trailing_index = calendar.sessions_in_range(trailing_first, selection)
    trailing = tuple(session.date() for session in trailing_index)
    evidence_cutoff = datetime.combine(first.date(), time(8, 10), tzinfo=KST)

    previous_month = 12 if month == 1 else month - 1
    previous_year = year - 1 if month == 1 else year
    if (
        len(trailing) != 20
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

    calendar = _calendar()
    latest = pd.Timestamp(latest_completed_session(cutoff))
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
