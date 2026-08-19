"""S5 correction set이 저장된 CTCA0903R 권위와 어긋나면 통과하지 않음을 실제 DB로 고정한다."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import psycopg

from app.data.calendar.correction_attestation import (
    ATTESTATION_CONFIRMED,
    ATTESTATION_CONFLICT,
    ATTESTATION_UNVERIFIED,
    attest_correction_set,
)
from app.data.calendar.merger import merge_trading_session
from app.data.calendar.models import KISHolidayObservation, XKRXSession
from app.data.calendar.repository import CalendarRepository
from app.lightgbm.pit_calendar import S5_ADHOC_CLOSED_SESSIONS
from tests.data.calendar.conftest import PostgresTestCluster

_KST = ZoneInfo("Asia/Seoul")
_WINDOW_START = date(2026, 6, 1)
_WINDOW_END = date(2026, 6, 30)
_CORRECTION = date(2026, 6, 3)


def _observation(day: date, *, is_open: bool) -> KISHolidayObservation:
    return KISHolidayObservation(
        day=day,
        is_open=is_open,
        business_day_flag=None,
        trading_day_flag=None,
        settlement_day_flag=None,
        source_id="kis-holiday-ctca0903r",
        origin_group="kis",
        tier=1,
        tr_id="CTCA0903R",
    )


def _open_base(day: date) -> XKRXSession:
    return XKRXSession.fixture(
        session_date=day,
        is_open=True,
        open_at=datetime(day.year, day.month, day.day, 9, 0, tzinfo=_KST),
        close_at=datetime(day.year, day.month, day.day, 15, 30, tzinfo=_KST),
    )


def _publish(dsn: str, day: date, *, is_open: bool) -> None:
    canonical = merge_trading_session(
        _open_base(day),
        kis=_observation(day, is_open=is_open),
        kasi_reasons=[],
        prior=None,
        now=datetime(2026, 8, 19, tzinfo=UTC),
    )
    with psycopg.connect(dsn, autocommit=False) as connection:
        CalendarRepository(connection).upsert_trading_session(canonical)


def _attest(dsn: str) -> str:
    with psycopg.connect(dsn, autocommit=False) as connection:
        return attest_correction_set(
            connection,
            window_start=_WINDOW_START,
            window_end=_WINDOW_END,
            corrections=S5_ADHOC_CLOSED_SESSIONS,
        ).status


def test_correction_set_requires_observed_kis_authority(
    isolated_postgres_cluster: PostgresTestCluster,
) -> None:
    dsn = isolated_postgres_cluster["collector_dsn"]
    assert _CORRECTION in S5_ADHOC_CLOSED_SESSIONS

    # 관측이 없으면 통과가 아니라 미검증이다.
    assert _attest(dsn) == ATTESTATION_UNVERIFIED

    # correction 날짜가 실제 CTCA0903R에서 휴장으로 확인되면 확정된다.
    _publish(dsn, _CORRECTION, is_open=True)
    assert _attest(dsn) == ATTESTATION_CONFLICT

    _publish(dsn, _CORRECTION, is_open=False)
    assert _attest(dsn) == ATTESTATION_CONFIRMED


def test_unexpected_closed_session_is_reported_as_calendar_conflict(
    isolated_postgres_cluster: PostgresTestCluster,
) -> None:
    dsn = isolated_postgres_cluster["collector_dsn"]
    _publish(dsn, _CORRECTION, is_open=False)
    assert _attest(dsn) == ATTESTATION_CONFIRMED

    # correction set에 없는 날짜가 휴장으로 확인되면 상수가 현실과 갈라진 것이다.
    _publish(dsn, date(2026, 6, 17), is_open=False)
    with psycopg.connect(dsn, autocommit=False) as connection:
        result = attest_correction_set(
            connection,
            window_start=_WINDOW_START,
            window_end=_WINDOW_END,
            corrections=S5_ADHOC_CLOSED_SESSIONS,
        )
    assert result.status == ATTESTATION_CONFLICT
    assert result.unexpected_closed == (date(2026, 6, 17),)
    assert result.confirmed_closed == (_CORRECTION,)
