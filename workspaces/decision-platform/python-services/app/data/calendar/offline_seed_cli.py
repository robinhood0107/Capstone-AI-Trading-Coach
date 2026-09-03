"""KIS 호출 없이 pinned XKRX 달력으로 canonical trading session을 적재한다.

reconcile_cli는 후보 세션을 실제 CTCA0903R 권위로 확정하므로 live KIS 소켓을 강제한다
(kis_mode="live"). 그런데 merger는 kis=None을 이미 지원하고 그 경로를 degraded=False로
낸다 - 즉 설계는 KIS 없는 확정을 지원하는데 호출자만 없었다. 이 CLI가 그 호출자다.

_attest_collector_authority는 reconcile_cli의 것을 그대로 쓴다. 같은 패키지의 같은 일이므로
두 번째 구현을 만들지 않는다.

한계: XKRX 패키지 달력이 유일 근거이므로 KRX의 돌발 휴장 공지는 반영하지 못한다.
chosen_source_id에 XKRX가 그대로 기록되어 이 성질이 데이터에 남는다.

필수 환경변수:
  P1_CALENDAR_OFFLINE_SEED_DSN     decision_collector role DSN

선택 환경변수:
  P1_CALENDAR_OFFLINE_SEED_YEARS   콤마로 구분한 연도 (기본: 올해와 내년)
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from typing import Any

import psycopg

from app.data.calendar.adapters.xkrx import build_xkrx_sessions
from app.data.calendar.errors import AdapterValidationError
from app.data.calendar.merger import merge_trading_session
from app.data.calendar.models import XKRXSession
from app.data.calendar.reconcile_cli import _attest_collector_authority
from app.data.calendar.repository import CalendarRepository

_MAX_YEARS = 3


def main() -> int:
    """권한 preflight를 먼저 통과한 뒤 캘린더에 없는 개장일만 적재한다."""

    dsn = os.environ.get("P1_CALENDAR_OFFLINE_SEED_DSN", "")
    if not dsn:
        print("P1_CALENDAR_OFFLINE_SEED=INVALID reason=DSN_MISSING providerCalls=0")
        return 2
    try:
        years = _parse_years(os.environ.get("P1_CALENDAR_OFFLINE_SEED_YEARS", ""))
    except AdapterValidationError:
        print("P1_CALENDAR_OFFLINE_SEED=INVALID reason=YEAR_LIST_INVALID providerCalls=0")
        return 2

    try:
        with psycopg.connect(dsn, autocommit=False, connect_timeout=5) as preflight:
            _attest_collector_authority(preflight)
    except (psycopg.Error, AdapterValidationError):
        print(
            "P1_CALENDAR_OFFLINE_SEED=AUTHORITY_UNAVAILABLE "
            "reason=COLLECTOR_PREFLIGHT_FAILED providerCalls=0"
        )
        return 2

    # 이미 있는 행은 건드리지 않는다. 확정된 이력의 chosen_source_id를 XKRX로 바꾸지 않는
    # 것이 목적이므로, 날짜로 자르는 대신 존재 여부로 가른다. 그래야 과거 공백도 메워진다 -
    # LSTM의 20세션 lookback은 그 구간이 캘린더에 있어야 성립한다.
    try:
        with psycopg.connect(dsn, autocommit=False, connect_timeout=5) as connection:
            existing = _existing_sessions(connection)
    except psycopg.Error:
        print(
            "P1_CALENDAR_OFFLINE_SEED=AUTHORITY_UNAVAILABLE "
            "reason=EXISTING_SESSION_SCAN_FAILED providerCalls=0"
        )
        return 2
    sessions: list[XKRXSession] = []
    skipped: list[str] = []
    for year in years:
        # pinned 달력은 오늘부터 약 1년 뒤에서 끝난다(실측 상한 2027-09-03). 마지막 연도가
        # 그 경계를 넘는 것은 정상이므로, 되는 연도까지 적재하고 건너뛴 연도만 남긴다.
        try:
            built = build_xkrx_sessions(year)
        except (ValueError, RuntimeError) as error:
            skipped.append(f"{year}:{type(error).__name__}")
            continue
        sessions.extend(
            session
            for session in built
            if session.is_open and session.session_date not in existing
        )
    skipped_marker = ",".join(skipped) if skipped else "none"
    if not sessions:
        print(
            "P1_CALENDAR_OFFLINE_SEED=NO_SESSIONS seeded=0 "
            f"alreadyPresent={len(existing)} "
            f"skippedYears={skipped_marker} providerCalls=0"
        )
        # 달력 밖 연도만 요청해 한 세션도 못 얻은 것은 입력 오류다. 조용히 성공으로 두지 않는다.
        return 2 if skipped else 0

    sessions.sort(key=lambda item: item.session_date)
    now = datetime.now(UTC)
    try:
        with psycopg.connect(dsn, autocommit=False, connect_timeout=5) as connection:
            repository = CalendarRepository(connection)
            for session in sessions:
                repository.upsert_trading_session(
                    merge_trading_session(
                        session, kis=None, kasi_reasons=[], prior=None, now=now
                    )
                )
    except (psycopg.Error, AdapterValidationError, ValueError):
        print("P1_CALENDAR_OFFLINE_SEED=WRITE_FAILED providerCalls=0")
        return 1

    print(
        "P1_CALENDAR_OFFLINE_SEED=SEEDED "
        f"seeded={len(sessions)} "
        f"firstSession={sessions[0].session_date.isoformat()} "
        f"lastSession={sessions[-1].session_date.isoformat()} "
        f"skippedYears={skipped_marker} "
        f"alreadyPresent={len(existing)} "
        "chosenSource=XKRX degraded=false providerCalls=0"
    )
    return 0


def _existing_sessions(connection: psycopg.Connection[Any]) -> set[date]:
    """이미 적재된 canonical session 날짜를 읽는다. 그 행은 다시 쓰지 않는다."""

    rows = connection.execute("SELECT session_date FROM public.trading_sessions").fetchall()
    return {row[0] for row in rows}


def _parse_years(value: str) -> tuple[int, ...]:
    """연도 목록을 파싱한다. 비면 올해와 내년으로 둬서 최초 arm과 재arm을 함께 덮는다."""

    if not value.strip():
        current = datetime.now(UTC).date().year
        return (current, current + 1)
    years: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            years.add(int(token))
        except ValueError:
            raise AdapterValidationError("offline seed year is invalid") from None
    if not years or len(years) > _MAX_YEARS:
        raise AdapterValidationError("offline seed year list is out of bounds")
    return tuple(sorted(years))


if __name__ == "__main__":
    raise SystemExit(main())
