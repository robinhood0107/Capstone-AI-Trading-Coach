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
                                   달력 경계를 넘는 부분은 잘라서 채운다.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from typing import Any

import psycopg

from app.data.calendar.adapters.xkrx import (
    build_xkrx_sessions_in_range,
    xkrx_calendar_bounds,
)
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
    # pinned 달력은 유한하다(실측 2006-09-04 ~ 2027-09-03). 연도 전체를 요구하면 마지막 해가
    # 통째로 거부되어 캘린더가 연말에서 끊기고 그 뒤 arm 이 다음 세션을 못 찾는다. 그래서
    # 요청 범위를 달력 경계까지 잘라서 채운다 - 부분 연도도 유효한 base 다.
    try:
        calendar_first, calendar_last = xkrx_calendar_bounds()
    except RuntimeError:
        print("P1_CALENDAR_OFFLINE_SEED=INVALID reason=CALENDAR_PIN_DRIFT providerCalls=0")
        return 2

    sessions: list[XKRXSession] = []
    clamped: list[str] = []
    for year in years:
        start = max(date(year, 1, 1), calendar_first)
        end = min(date(year, 12, 31), calendar_last)
        if end < start:
            clamped.append(f"{year}:outside")
            continue
        if (start, end) != (date(year, 1, 1), date(year, 12, 31)):
            clamped.append(f"{year}:{start.isoformat()}~{end.isoformat()}")
        try:
            built = build_xkrx_sessions_in_range(start, end)
        except (ValueError, RuntimeError) as error:
            clamped.append(f"{year}:{type(error).__name__}")
            continue
        sessions.extend(
            session for session in built if session.is_open and session.session_date not in existing
        )
    clamped_marker = ",".join(clamped) if clamped else "none"
    if not sessions:
        print(
            "P1_CALENDAR_OFFLINE_SEED=NO_SESSIONS seeded=0 "
            f"alreadyPresent={len(existing)} "
            f"clampedYears={clamped_marker} providerCalls=0"
        )
        # clamp 는 정보다. 1년치를 요청하면 마지막 연도는 달력 핀 경계에 항상 걸리므로
        # 캘린더가 한 번 채워진 뒤의 모든 실행이 여기로 온다 - 그것이 정상 정상상태다.
        # 이미 clampedYears 로 적고 있으니 종료코드로 또 말하지 않는다.
        #
        # 실패로 남기는 경우는 하나다. 요청한 연도가 전부 달력 밖이어서 어떤 세션도 얻을
        # 수 없는 것 - 그건 입력이 틀린 것이고 조용히 성공으로 둘 수 없다.
        if clamped and all(entry.endswith(":outside") for entry in clamped):
            return 2
        return 0

    sessions.sort(key=lambda item: item.session_date)
    now = datetime.now(UTC)
    try:
        with psycopg.connect(dsn, autocommit=False, connect_timeout=5) as connection:
            repository = CalendarRepository(connection)
            for session in sessions:
                repository.upsert_trading_session(
                    merge_trading_session(session, kis=None, kasi_reasons=[], prior=None, now=now)
                )
    except (psycopg.Error, AdapterValidationError, ValueError):
        print("P1_CALENDAR_OFFLINE_SEED=WRITE_FAILED providerCalls=0")
        return 1

    print(
        "P1_CALENDAR_OFFLINE_SEED=SEEDED "
        f"seeded={len(sessions)} "
        f"firstSession={sessions[0].session_date.isoformat()} "
        f"lastSession={sessions[-1].session_date.isoformat()} "
        f"clampedYears={clamped_marker} "
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
