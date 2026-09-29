#!/usr/bin/env python3
"""Generate the bounded XKRX calendar fixture from the pinned project policy."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from importlib.metadata import version
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd
from exchange_calendars.exchange_calendar_xkrx import XKRXExchangeCalendar


PINNED_VERSION = "4.13.2"
POLICY_VERSION = "xkrx-4.13.2-kis-corrections-v1"
CORRECTIONS = (date(2026, 6, 3), date(2026, 7, 17))
START = date(2026, 1, 1)
END = date(2027, 9, 29)
SOURCE_URL = "https://global.krx.co.kr/contents/GLB/06/0602/0602020204/GLB0602020204T1.jsp"


class CorrectedXkrxCalendar(XKRXExchangeCalendar):  # type: ignore[misc]
    @property
    def adhoc_holidays(self) -> list[pd.Timestamp]:
        values = {*super().adhoc_holidays}
        values.update(pd.Timestamp(day) for day in CORRECTIONS)
        return sorted(values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    installed = version("exchange-calendars")
    if installed != PINNED_VERSION:
        raise SystemExit(f"exchange-calendars pin mismatch: {installed}")
    canonical = json.dumps([day.isoformat() for day in CORRECTIONS], separators=(",", ":"), ensure_ascii=False).encode()
    correction_hash = hashlib.sha256(b"s5-xkrx-calendar-corrections-v1\x00" + canonical).hexdigest()
    calendar = type(
        "PinnedXkrxCalendar",
        (CorrectedXkrxCalendar,),
        {"name": "XKRX"},
    )()
    labels = calendar.sessions_in_range(pd.Timestamp(START), pd.Timestamp(END))
    sessions = []
    for label in labels:
        row = calendar.schedule.loc[label]
        opened = row["open"].to_pydatetime().astimezone(pd.Timestamp.now(tz="Asia/Seoul").tzinfo)
        closed = row["close"].to_pydatetime().astimezone(pd.Timestamp.now(tz="Asia/Seoul").tzinfo)
        sessions.append(
            {
                "date": label.date().isoformat(),
                "openKst": opened.isoformat(),
                "closeKst": closed.isoformat(),
            }
        )
    document = {
        "schemaVersion": "mars-demo.krx-calendar.v1",
        "policyVersion": POLICY_VERSION,
        "exchangeCalendar": "XKRX",
        "library": "exchange-calendars",
        "libraryVersion": installed,
        "range": {"start": START.isoformat(), "end": END.isoformat()},
        "timezone": "Asia/Seoul",
        "regularMarketHoursKst": {"open": "09:00", "close": "15:30"},
        "corrections": [day.isoformat() for day in CORRECTIONS],
        "correctionSetSha256": correction_hash,
        "sourceUrl": SOURCE_URL,
        "sessions": sessions,
    }
    encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    document["sha256"] = hashlib.sha256(encoded).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"MARS_DEMO_CALENDAR=BUILT policy={POLICY_VERSION} sessions={len(sessions)} sha256={document['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
