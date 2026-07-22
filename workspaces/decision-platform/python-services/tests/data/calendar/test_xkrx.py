from __future__ import annotations

from datetime import date, time

from app.data.calendar.adapters.xkrx import build_xkrx_sessions


def test_pinned_xkrx_builds_deterministic_full_2026_schedule() -> None:
    first = build_xkrx_sessions(2026)
    second = build_xkrx_sessions(2026)

    assert first == second
    assert len(first) == 246
    assert first[0].session_date == date(2026, 1, 2)
    assert first[-1].session_date == date(2026, 12, 30)
    assert all(session.timezone == "Asia/Seoul" for session in first)
    assert all(session.provenance.library_version == "4.13.2" for session in first)
    assert all(session.provenance.calendar_name == "XKRX" for session in first)


def test_xkrx_preserves_special_open_and_known_2026_boundaries() -> None:
    sessions = {session.session_date: session for session in build_xkrx_sessions(2026)}

    assert sessions[date(2026, 1, 2)].open_at.timetz().replace(tzinfo=None) == time(10, 0)
    assert sessions[date(2026, 1, 2)].close_at.timetz().replace(tzinfo=None) == time(15, 30)
    assert date(2026, 1, 1) not in sessions
    assert date(2026, 5, 1) not in sessions
    # 고정 라이브러리는 2026 지방선거 임시휴장을 반영하지 못하므로 KIS primary가 이 값을 교정한다.
    assert date(2026, 6, 3) in sessions
    assert date(2026, 7, 11) not in sessions
    assert date(2026, 12, 31) not in sessions
