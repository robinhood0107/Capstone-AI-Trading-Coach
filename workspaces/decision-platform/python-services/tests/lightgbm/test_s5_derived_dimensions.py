"""승인 차원에서 유도되는 값이 리터럴로 중복 선언되지 않는지 고정한다.

이번 실물화에서 멈춘 7건 중 3건이 이 부류였다. KIS 행 상한 192,960은 union 180 시절 리터럴이
남은 것이고, ECOS page 400은 요청당 행 상한 200과 한 번도 맞춰지지 않았고, ECOS chunk 365일은 그
상한을 넘겼다. 유도식이 상한의 유일한 정의여야 차원을 바꿀 때 두 곳이 어긋나지 않는다.
"""

from __future__ import annotations

import math

from app.data.ecos.policy import ECOS_MAX_ROWS_PER_REQUEST
from app.lightgbm.bootstrap_executor import _ECOS_CHUNK_DAYS
from app.lightgbm.daily_refresh import (
    DAILY_ECOS_MAX,
    DAILY_KIS_MAX,
    DAILY_KIS_TOKEN_MAX,
    DAILY_KRX_MAX,
    DAILY_TOTAL_MAX,
)
from app.lightgbm.pit_calendar import (
    ELIGIBLE_SESSION_COUNT,
    LABEL_TAIL_SESSIONS,
    RAW_SESSION_COUNT,
    WARMUP_SESSIONS,
)
from app.lightgbm.production_policy import (
    APPROVED_ECOS_MAX_GET,
    APPROVED_HORIZON_UNION_SIZE,
    APPROVED_KIS_MAX_GET,
    APPROVED_KIS_TOKEN_MAX,
    APPROVED_KRX_MAX_GET,
    APPROVED_MONTHLY_SCHEDULE_COUNT,
    APPROVED_TOTAL_MAX_PHYSICAL_CALLS,
)
from app.lightgbm.source_bundle import SOURCE_ROW_CAPS
from app.lightgbm.walk_forward import EXPECTED_SESSIONS


def test_session_counts_are_derived_from_one_another() -> None:
    """eligible을 따로 적으면 warm-up이나 label tail을 바꿀 때 어긋난다."""

    assert ELIGIBLE_SESSION_COUNT == RAW_SESSION_COUNT - WARMUP_SESSIONS - LABEL_TAIL_SESSIONS
    # walk-forward가 같은 수를 다시 선언하지 않는다.
    assert EXPECTED_SESSIONS == ELIGIBLE_SESSION_COUNT
    # 현재 승인 차원의 실제 값도 함께 고정한다.
    assert (RAW_SESSION_COUNT, ELIGIBLE_SESSION_COUNT) == (1_072, 1_007)


def test_approved_provider_caps_are_derived_from_approved_dimensions() -> None:
    """union 크기가 바뀌면 KIS 상한이 따라 움직여야 한다. 리터럴은 그러지 못했다."""

    assert APPROVED_KRX_MAX_GET == RAW_SESSION_COUNT * 4 + APPROVED_MONTHLY_SCHEDULE_COUNT * 3
    assert APPROVED_KIS_MAX_GET == APPROVED_HORIZON_UNION_SIZE * math.ceil(
        RAW_SESSION_COUNT / 100
    )
    assert APPROVED_TOTAL_MAX_PHYSICAL_CALLS == (
        APPROVED_KRX_MAX_GET
        + APPROVED_KIS_MAX_GET
        + APPROVED_KIS_TOKEN_MAX
        + APPROVED_ECOS_MAX_GET
    )
    # 유도식이 현재 승인값과 같아야 한다. 달라지면 승인 범위를 벗어난 변경이다.
    assert (APPROVED_KRX_MAX_GET, APPROVED_KIS_MAX_GET) == (4_441, 2_970)
    assert APPROVED_TOTAL_MAX_PHYSICAL_CALLS == 7_436


def test_source_row_cap_follows_the_union_size() -> None:
    """union 180 시절 리터럴 192,960이 남아 실제 수집 267,788행을 거부했다."""

    assert SOURCE_ROW_CAPS["KIS"] == APPROVED_HORIZON_UNION_SIZE * RAW_SESSION_COUNT
    assert SOURCE_ROW_CAPS["KIS"] > 267_788


def test_ecos_chunk_length_cannot_exceed_the_request_row_cap() -> None:
    """두 상수가 서로를 제약하면 하나에서 유도한다. 맞춰지지 않아 첫 호출이 죽었다."""

    assert _ECOS_CHUNK_DAYS <= ECOS_MAX_ROWS_PER_REQUEST


def test_daily_total_is_the_sum_of_provider_bounds() -> None:
    """따로 적으면 하나를 바꿀 때 다른 하나가 남는다."""

    assert DAILY_TOTAL_MAX == (
        DAILY_KRX_MAX + DAILY_KIS_MAX + DAILY_KIS_TOKEN_MAX + DAILY_ECOS_MAX
    )
    assert DAILY_TOTAL_MAX == 41
