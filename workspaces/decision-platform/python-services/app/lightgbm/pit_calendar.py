"""S5.1의 exact XKRX cutoff와 1,072/1,007 session arithmetic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from typing import Any, cast

import exchange_calendars as xcals
import pandas as pd

from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError


RAW_SESSION_COUNT = 1_072
WARMUP_SESSIONS = 59
LABEL_TAIL_SESSIONS = 6
ELIGIBLE_SESSION_COUNT = 1_007


@dataclass(frozen=True)
class PitSessionWindow:
    """cutoff까지 완료된 raw와 labelable XKRX session의 불변 목록."""

    cutoff: datetime
    latest_completed: date
    raw_sessions: tuple[date, ...]
    eligible_sessions: tuple[date, ...]


@lru_cache(maxsize=1)
def _calendar() -> Any:
    return xcals.get_calendar("XKRX")


def latest_completed_session(cutoff: datetime) -> date:
    """timezone-aware cutoff 이전에 close가 완료된 가장 최신 XKRX 정규 session을 반환한다."""

    if cutoff.tzinfo is None:
        raise LightGbmContractError("S5 cutoff must be timezone aware")
    calendar = _calendar()
    cutoff_utc = pd.Timestamp(cutoff.astimezone(UTC))
    # exchange_calendars의 date API는 timezone-naive exchange-local calendar date를 요구한다.
    candidate = calendar.date_to_session(pd.Timestamp(cutoff.date()), direction="previous")
    if calendar.session_close(candidate) > cutoff_utc:
        candidate = calendar.previous_session(candidate)
    return cast(date, candidate.date())


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
