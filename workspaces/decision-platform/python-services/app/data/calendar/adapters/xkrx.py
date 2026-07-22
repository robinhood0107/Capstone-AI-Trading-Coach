from __future__ import annotations

from datetime import date
from importlib.metadata import version
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from app.data.calendar.models import SourceProvenance, XKRXSession

_KST = ZoneInfo("Asia/Seoul")
_PINNED_VERSION = "4.13.2"


def build_xkrx_sessions(year: int) -> list[XKRXSession]:
    """pinned exchange-calendars의 XKRX open sessions를 KST provenance와 함께 만든다.

    이 결과는 네트워크 없는 base/fallback이며 미래 임시휴장의 공식 권위를 주장하지 않는다.
    """
    if year < 1990 or year > 2100:
        raise ValueError("year is outside the supported calendar range")
    installed = version("exchange-calendars")
    if installed != _PINNED_VERSION:
        raise RuntimeError("exchange-calendars version drifted from the approved pin")
    calendar = xcals.get_calendar("XKRX")
    start = pd.Timestamp(date(year, 1, 1))
    end = pd.Timestamp(date(year, 12, 31))
    labels = calendar.sessions_in_range(start, end)
    provenance = SourceProvenance(
        library_name="exchange-calendars",
        library_version=installed,
        calendar_name=calendar.name,
    )
    sessions: list[XKRXSession] = []
    for label in labels:
        row = calendar.schedule.loc[label]
        opened = row["open"].to_pydatetime().astimezone(_KST)
        closed = row["close"].to_pydatetime().astimezone(_KST)
        sessions.append(
            XKRXSession(
                session_date=label.date(),
                is_open=True,
                open_at=opened,
                close_at=closed,
                timezone="Asia/Seoul",
                provenance=provenance,
            )
        )
    return sessions
