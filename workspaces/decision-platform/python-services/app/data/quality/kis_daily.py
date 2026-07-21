from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import exchange_calendars as xcals
import pandas as pd


@dataclass(frozen=True)
class CompletedSessions:
    sessions: tuple[date, ...]
    expected_last_completed_session: date


def completed_xkrx_sessions(
    *,
    window_start: date,
    window_end: date,
    evaluated_at: datetime,
) -> CompletedSessions:
    """evaluatedAt까지 실제 close가 끝난 XKRX session만 양 끝 포함 window로 반환한다."""
    if window_start > window_end:
        raise ValueError("analysis window is invalid")
    if evaluated_at.tzinfo is None:
        raise ValueError("evaluatedAt must be timezone-aware")
    calendar: Any = xcals.get_calendar("XKRX")
    labels = calendar.sessions_in_range(pd.Timestamp(window_start), pd.Timestamp(window_end))
    completed = tuple(
        label.date()
        for label in labels
        if calendar.session_close(label).to_pydatetime() <= evaluated_at
    )
    if not completed:
        raise ValueError("analysis window contains no completed XKRX session")
    return CompletedSessions(
        sessions=completed,
        expected_last_completed_session=completed[-1],
    )
