from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from typing import Any

import exchange_calendars as xcals
import pandas as pd


@lru_cache(maxsize=1)
def _xkrx_calendar() -> Any:
    # S1.1 캘린더 경계는 네트워크 없는 로컬 판정 하나로 제한한다.
    # 다중 소스 휴장일 집계는 S1.6 계획으로 남겨 scope creep을 막는다.
    return xcals.get_calendar("XKRX")


def is_xkrx_trading_day(day: date) -> bool:
    return bool(_xkrx_calendar().is_session(pd.Timestamp(day)))


def previous_xkrx_trading_day(day: date) -> date:
    # 휴장일 입력을 이전 세션으로 낮추되, 온라인 CLI는 이 값을 이용해 호출 자체를 skip한다.
    # offline smoke는 같은 로직으로 fixture 날짜 범위를 맞춰 네트워크 없이도 재현 가능하게 둔다.
    cursor = day
    while not is_xkrx_trading_day(cursor):
        cursor -= timedelta(days=1)
    return cursor
