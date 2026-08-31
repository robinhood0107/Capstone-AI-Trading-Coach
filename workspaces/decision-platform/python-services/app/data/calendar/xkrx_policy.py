"""Pinned XKRX calendar with hash-bound KIS closure corrections.

This data-layer module is the shared calendar authority for operational market
data, Automation replay, and the retained research pipeline.  It owns no model,
provider, account, or order capability.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import date
from functools import cache
from importlib.metadata import version
from typing import Any

import exchange_calendars as xcals
import pandas as pd
from exchange_calendars.exchange_calendar_xkrx import XKRXExchangeCalendar

from app.data._shared.canonical_json import canonical_json_bytes

PINNED_CALENDAR_VERSION = "4.13.2"
XKRX_CALENDAR_POLICY_VERSION = "xkrx-4.13.2-kis-corrections-v1"
XKRX_ADHOC_CLOSED_SESSIONS = (date(2026, 6, 3), date(2026, 7, 17))
XKRX_SUPERSEDED_CORRECTION_SETS: tuple[tuple[date, ...], ...] = (
    (),
    (date(2026, 6, 3),),
)


class XkrxCalendarPolicyError(ValueError):
    """Pinned version or approved correction generation is invalid."""


def correction_set_sha256(corrections: Sequence[date]) -> str:
    return hashlib.sha256(
        b"s5-xkrx-calendar-corrections-v1\x00"
        + canonical_json_bytes([day.isoformat() for day in corrections])
    ).hexdigest()


XKRX_CALENDAR_CORRECTION_SET_SHA256 = correction_set_sha256(XKRX_ADHOC_CLOSED_SESSIONS)


class _CorrectedXkrxCalendar(XKRXExchangeCalendar):  # type: ignore[misc]
    _corrections: tuple[date, ...] = ()

    @property
    def adhoc_holidays(self) -> list[pd.Timestamp]:
        values = {*super().adhoc_holidays}
        values.update(pd.Timestamp(day) for day in self._corrections)
        return sorted(values)


def _require_pinned_version() -> None:
    if version("exchange-calendars") != PINNED_CALENDAR_VERSION:
        raise XkrxCalendarPolicyError("exchange-calendars version drifted from XKRX policy")


@cache
def calendar_for_corrections(corrections: tuple[date, ...]) -> Any:
    _require_pinned_version()
    if not corrections:
        return xcals.get_calendar("XKRX")
    generation = type(
        "_CorrectedXkrxCalendarGeneration",
        (_CorrectedXkrxCalendar,),
        {"_corrections": tuple(corrections)},
    )
    return generation()


def base_calendar() -> Any:
    return calendar_for_corrections(())


def corrected_calendar() -> Any:
    return calendar_for_corrections(XKRX_ADHOC_CLOSED_SESSIONS)


def corrections_for_sha256(digest: str) -> tuple[date, ...]:
    for corrections in (XKRX_ADHOC_CLOSED_SESSIONS, *XKRX_SUPERSEDED_CORRECTION_SETS):
        if correction_set_sha256(corrections) == digest:
            return tuple(corrections)
    raise XkrxCalendarPolicyError("calendar correction set generation is not approved")
