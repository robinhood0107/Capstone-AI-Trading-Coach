from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from typing import Any

import exchange_calendars as xcals
import pandas as pd


@lru_cache(maxsize=1)
def _xkrx_calendar() -> Any:
    return xcals.get_calendar("XKRX")


def is_xkrx_trading_day(day: date) -> bool:
    return bool(_xkrx_calendar().is_session(pd.Timestamp(day)))


def previous_xkrx_trading_day(day: date) -> date:
    cursor = day
    while not is_xkrx_trading_day(cursor):
        cursor -= timedelta(days=1)
    return cursor
