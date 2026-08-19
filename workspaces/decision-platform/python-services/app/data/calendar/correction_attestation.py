"""S5 correction set이 실제 CTCA0903R 관측과 어긋나지 않는지 확인하는 read-only 검증기다.

S5 packet 해시는 결정적이어야 하므로 correction set 자체는 정적 상수로 남는다. 대신 이 검증기가
저장된 tier-1 관측과 상수를 대조해, 상수가 현실과 갈라진 상태로 provider 호출이 열리는 것을 막는다.
provider 호출과 쓰기는 하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

import psycopg

KIS_HOLIDAY_SOURCE_ID = "kis-holiday-ctca0903r"

ATTESTATION_CONFIRMED = "API_CONFIRMED"
ATTESTATION_UNVERIFIED = "CALENDAR_AUTHORITY_UNVERIFIED"
ATTESTATION_CONFLICT = "CALENDAR_AUTHORITY_CONFLICT"


@dataclass(frozen=True, slots=True)
class CorrectionAttestation:
    """상수 correction set과 저장된 KIS 권위의 대조 결과다."""

    status: str
    observed_sessions: int
    confirmed_closed: tuple[date, ...]
    unobserved_corrections: tuple[date, ...]
    unexpected_closed: tuple[date, ...]
    contradicted_corrections: tuple[date, ...]


def attest_correction_set(
    connection: psycopg.Connection[Any],
    *,
    window_start: date,
    window_end: date,
    corrections: Sequence[date],
) -> CorrectionAttestation:
    """Window 안의 tier-1 관측만 읽어 correction set과의 정합을 판정한다.

    `API_CONFIRMED`는 window 안 모든 correction이 KIS 관측으로 휴장 확인됐고, KIS가 휴장이라고
    본 다른 날짜가 없을 때만 반환한다. 관측이 없으면 통과가 아니라 `CALENDAR_AUTHORITY_UNVERIFIED`다.
    """

    if window_end < window_start:
        raise ValueError("attestation window is inverted")
    expected = tuple(
        sorted({day for day in corrections if window_start <= day <= window_end})
    )
    rows = connection.execute(
        """
        SELECT session_date, is_open
        FROM trading_sessions
        WHERE exchange_mic = 'XKRX'
          AND chosen_source_id = %s
          AND session_date BETWEEN %s AND %s
        ORDER BY session_date
        """,
        (KIS_HOLIDAY_SOURCE_ID, window_start, window_end),
    ).fetchall()
    observed = {row[0]: bool(row[1]) for row in rows}
    observed_closed = {day for day, is_open in observed.items() if not is_open}

    unobserved = tuple(day for day in expected if day not in observed)
    contradicted = tuple(day for day in expected if observed.get(day) is True)
    unexpected = tuple(sorted(observed_closed.difference(expected)))

    if contradicted or unexpected:
        status = ATTESTATION_CONFLICT
    elif unobserved:
        status = ATTESTATION_UNVERIFIED
    elif not observed:
        status = ATTESTATION_UNVERIFIED
    else:
        status = ATTESTATION_CONFIRMED
    return CorrectionAttestation(
        status=status,
        observed_sessions=len(observed),
        confirmed_closed=tuple(sorted(observed_closed.intersection(expected))),
        unobserved_corrections=unobserved,
        unexpected_closed=unexpected,
        contradicted_corrections=contradicted,
    )
