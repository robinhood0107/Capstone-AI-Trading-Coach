"""Shared XKRX evidence clocks for production data and artifact consumers."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from app.data.calendar.xkrx_policy import corrected_calendar

KST = ZoneInfo("Asia/Seoul")


class EvidenceClockError(ValueError):
    """The requested evidence clock is outside the approved XKRX calendar."""


def next_session_evidence_clock(observation_date: date, *, extra_sessions: int = 0) -> datetime:
    if type(observation_date) is not date or isinstance(extra_sessions, bool) or extra_sessions < 0:
        raise EvidenceClockError("evidence clock input is invalid")
    calendar = corrected_calendar()
    try:
        session = calendar.date_to_session(pd.Timestamp(observation_date), direction="none")
    except Exception:
        raise EvidenceClockError("observationDate must be an XKRX session") from None
    target = session
    for _ in range(extra_sessions + 1):
        target = calendar.next_session(target)
    return datetime.combine(target.date(), time(8, 10), tzinfo=KST)
