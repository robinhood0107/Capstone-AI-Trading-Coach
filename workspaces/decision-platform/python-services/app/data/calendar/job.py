from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")


def kst_usage_date(now: datetime) -> date:
    """OpenDART reset timezone evidence가 KST로 승인된 경우에만 사용할 ledger 날짜를 계산한다."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("quota clock must be timezone-aware")
    return now.astimezone(_KST).date()
