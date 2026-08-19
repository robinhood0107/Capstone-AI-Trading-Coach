"""후보 session만 실제 CTCA0903R 권위로 확정해 canonical trading session에 적재한다.

이 CLI는 S5 bootstrap이 발행한 divergence 후보(또는 운영자가 지정한 세션)만 조회한다. 전체
window를 전수 조회하지 않으므로 문서의 보수적 호출 정책을 유지하며, bootstrap packet 예산과도
분리돼 있어 승인된 KRX/KIS/ECOS 상한을 소비하지 않는다.

필수 환경변수:
  S5_CALENDAR_RECONCILE_DSN       decision_collector role DSN

선택 환경변수:
  S5_CALENDAR_RECONCILE_SESSIONS  콤마로 구분한 ISO 날짜 목록 (최대 32개)
  S5_SOURCE_ROOT                  bootstrap approved root

session 목록을 주지 않으면 bootstrap이 봉인한 divergence 후보 sidecar에서 직접 읽는다. 사람이
날짜를 옮겨 적는 단계가 없어야 같은 후보를 잘못 입력하는 경로가 사라진다.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psycopg

from app.data.calendar.adapters.xkrx import build_xkrx_sessions
from app.data.calendar.errors import AdapterValidationError
from app.data.calendar.kis_holiday_source import (
    MAX_KIS_HOLIDAY_CALLS,
    KISHolidayAuthority,
)
from app.data.calendar.merger import merge_trading_session
from app.data.calendar.models import SourceProvenance, XKRXSession
from app.data.calendar.repository import CalendarRepository
from app.data.kis.http_client import KISHttpClient
from app.data.kis.market_client import KISMarketClient
from app.data.kis.settings import KISSettings
from app.lightgbm.bootstrap_executor import DIVERGENCE_CANDIDATES_FILENAME
from app.lightgbm.pit_calendar import PINNED_CALENDAR_VERSION

_REQUIRED_ROLE = "decision_collector"


def main() -> int:
    """권한 preflight를 provider socket 앞에 두고 후보 session만 확정한다."""

    dsn = os.environ.get("S5_CALENDAR_RECONCILE_DSN", "")
    raw_sessions = os.environ.get("S5_CALENDAR_RECONCILE_SESSIONS", "")
    source_root = os.environ.get("S5_SOURCE_ROOT", "")
    if not dsn or not (raw_sessions or source_root):
        print("S5_CALENDAR_RECONCILE=AUTHORITY_UNAVAILABLE reason=INPUT_MISSING kisHolidayCalls=0")
        return 2
    try:
        sessions = (
            _parse_sessions(raw_sessions)
            if raw_sessions
            else _divergence_candidate_sessions(Path(source_root))
        )
    except AdapterValidationError:
        print("S5_CALENDAR_RECONCILE=INVALID reason=SESSION_LIST_INVALID kisHolidayCalls=0")
        return 2
    if not sessions:
        print("S5_CALENDAR_RECONCILE=NO_CANDIDATES kisHolidayCalls=0")
        return 0

    try:
        with psycopg.connect(dsn, autocommit=False, connect_timeout=5) as preflight:
            _attest_collector_authority(preflight)
    except (psycopg.Error, AdapterValidationError):
        # DB 권한 실패는 provider socket 앞에서 멈춰 휴장일 호출을 낭비하지 않는다.
        print(
            "S5_CALENDAR_RECONCILE=AUTHORITY_UNAVAILABLE "
            "reason=COLLECTOR_PREFLIGHT_FAILED kisHolidayCalls=0"
        )
        return 2

    market_client: KISMarketClient | None = None
    authority: KISHolidayAuthority | None = None
    try:
        settings = KISSettings(kis_mode="live", kis_offline=False, kis_retry_attempts=1)
        http_client = KISHttpClient(settings)
        market_client = KISMarketClient(settings, http_client)
        authority = KISHolidayAuthority(market_client, max_calls=MAX_KIS_HOLIDAY_CALLS)
        now = datetime.now(UTC)
        confirmed: list[tuple[date, bool, bool]] = []
        for day in sessions:
            observation = authority.observe(day)
            canonical = merge_trading_session(
                _base_session(day),
                kis=observation,
                kasi_reasons=[],
                prior=None,
                now=now,
            )
            with psycopg.connect(dsn, autocommit=False, connect_timeout=5) as connection:
                CalendarRepository(connection).upsert_trading_session(canonical)
            confirmed.append((day, canonical.is_open, canonical.has_conflict))
    except Exception:
        calls = authority.calls if authority is not None else 0
        print(f"S5_CALENDAR_RECONCILE=AUTHORITY_UNAVAILABLE kisHolidayCalls={calls}")
        return 1
    finally:
        if market_client is not None:
            try:
                market_client.close()
            except Exception:
                # cleanup 오류가 이미 확정된 관측 결과를 가리지 않게 한다.
                pass

    closed = sum(1 for _, is_open, _ in confirmed if not is_open)
    conflicts = sum(1 for _, _, has_conflict in confirmed if has_conflict)
    print(
        "S5_CALENDAR_RECONCILE=CONFIRMED "
        f"sessions={len(confirmed)} "
        f"closedSessions={closed} "
        f"openSessions={len(confirmed) - closed} "
        f"conflicts={conflicts} "
        f"kisHolidayCalls={authority.calls}"
    )
    return 0


def _parse_sessions(value: str) -> tuple[date, ...]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    if not items or len(items) > MAX_KIS_HOLIDAY_CALLS:
        raise AdapterValidationError("reconcile session list is outside the approved bound")
    parsed: list[date] = []
    for item in items:
        try:
            parsed.append(date.fromisoformat(item))
        except ValueError:
            raise AdapterValidationError("reconcile session date is invalid") from None
    unique = sorted(set(parsed))
    if len(unique) != len(parsed):
        raise AdapterValidationError("reconcile session list has duplicates")
    return tuple(unique)


def _divergence_candidate_sessions(approved_root: Path) -> tuple[date, ...]:
    """bootstrap이 봉인한 divergence 후보 sidecar에서 확정 대상 session만 읽는다.

    후보는 이미 content-free로 봉인돼 있으므로 provider raw를 다시 열지 않는다. sidecar가 없으면
    확정할 후보가 없다는 뜻이며, 값을 지어내지 않는다.
    """

    if not approved_root.is_dir():
        raise AdapterValidationError("reconcile source root is unavailable")
    days: set[date] = set()
    for run in sorted(approved_root.glob("run-*")):
        block = run / "source" / DIVERGENCE_CANDIDATES_FILENAME
        if not block.is_file():
            continue
        try:
            payload = json.loads(block.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise AdapterValidationError("divergence candidate block is unreadable") from error
        for candidate in payload.get("candidates", ()):
            value = candidate.get("sessionDate") if isinstance(candidate, dict) else None
            if not isinstance(value, str):
                raise AdapterValidationError("divergence candidate session is invalid")
            try:
                days.add(date.fromisoformat(value))
            except ValueError:
                raise AdapterValidationError("divergence candidate session is invalid") from None
    if len(days) > MAX_KIS_HOLIDAY_CALLS:
        raise AdapterValidationError("divergence candidates exceed the approved call bound")
    return tuple(sorted(days))


def _attest_collector_authority(connection: psycopg.Connection[Any]) -> None:
    role = _scalar(connection.execute("select current_user").fetchone())
    if str(role) != _REQUIRED_ROLE:
        raise AdapterValidationError("reconcile DSN must use decision_collector")
    for privilege in ("INSERT", "UPDATE"):
        allowed = bool(
            _scalar(
                connection.execute(
                    "SELECT has_table_privilege(current_user, %s, %s)",
                    ("trading_sessions", privilege),
                ).fetchone()
            )
        )
        if not allowed:
            raise AdapterValidationError("reconcile role lacks canonical session write privilege")


def _scalar(row: tuple[Any, ...] | None) -> Any:
    if row is None or not row:
        raise AdapterValidationError("reconcile preflight query returned no row")
    return row[0]


def _base_session(day: date) -> XKRXSession:
    """pinned XKRX base를 후보 날짜의 tier-2 입력으로 만든다.

    base가 이 날짜를 열지 않으면 open/close 없는 closed base로 표현한다. 운영 권위는 언제나 KIS가
    가지므로 이 값은 비교 기준일 뿐이다.
    """

    for session in build_xkrx_sessions(day.year):
        if session.session_date == day:
            return session
    return XKRXSession(
        session_date=day,
        is_open=False,
        open_at=None,
        close_at=None,
        timezone="Asia/Seoul",
        provenance=SourceProvenance(
            library_name="exchange-calendars",
            library_version=PINNED_CALENDAR_VERSION,
            calendar_name="XKRX",
        ),
    )


if __name__ == "__main__":  # pragma: no cover - entrypoint 위임만 한다
    raise SystemExit(main())
