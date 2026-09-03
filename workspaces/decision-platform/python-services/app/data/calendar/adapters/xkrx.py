from __future__ import annotations

from datetime import date
from importlib.metadata import version
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from app.data.calendar.models import SourceProvenance, XKRXSession

_KST = ZoneInfo("Asia/Seoul")
_PINNED_VERSION = "4.13.2"


def xkrx_calendar_bounds() -> tuple[date, date]:
    """pinned 달력이 실제로 덮는 첫 세션과 마지막 세션을 돌려준다.

    달력은 유한하다(실측 상한 2027-09-03). 연도 단위로 요구하면 마지막 해가 통째로 거부되므로
    호출자가 범위를 잘라 쓸 수 있게 경계를 노출한다.
    """
    installed = version("exchange-calendars")
    if installed != _PINNED_VERSION:
        raise RuntimeError("exchange-calendars version drifted from the approved pin")
    calendar = xcals.get_calendar("XKRX")
    return calendar.first_session.date(), calendar.last_session.date()


def build_xkrx_sessions_in_range(start_date: date, end_date: date) -> list[XKRXSession]:
    """지정 범위의 XKRX open sessions를 만든다.

    build_xkrx_sessions(year)와 같은 provenance를 쓴다. 범위가 달력 경계를 넘으면 여전히
    거부한다 - 잘라 쓰는 판단은 호출자가 하고 이 함수가 조용히 좁히지 않는다.
    """
    if end_date < start_date:
        raise ValueError("calendar range end precedes start")
    return _build(start_date, end_date)


def build_xkrx_sessions(year: int) -> list[XKRXSession]:
    """pinned exchange-calendars의 XKRX open sessions를 KST provenance와 함께 만든다.

    이 결과는 네트워크 없는 base/fallback이며 미래 임시휴장의 공식 권위를 주장하지 않는다.
    """
    if year < 1990 or year > 2100:
        raise ValueError("year is outside the supported calendar range")
    return _build(date(year, 1, 1), date(year, 12, 31))


def _build(start_date: date, end_date: date) -> list[XKRXSession]:
    installed = version("exchange-calendars")
    if installed != _PINNED_VERSION:
        raise RuntimeError("exchange-calendars version drifted from the approved pin")
    calendar = xcals.get_calendar("XKRX")
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
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
