from __future__ import annotations

import pytest

from app.data.calendar.confidence import confidence_bps


@pytest.mark.parametrize("tier, expected", [(1, 9000), (2, 7000), (3, 5000), (4, 3000)])
def test_confidence_uses_exact_integer_tier_base(tier: int, expected: int) -> None:
    assert confidence_bps(tier=tier, agreeing_origin_groups=set(), has_conflict=False) == expected


def test_confidence_counts_each_independent_origin_once_and_penalizes_conflict_once() -> None:
    assert (
        confidence_bps(
            tier=2,
            agreeing_origin_groups={"origin-a", "origin-b"},
            has_conflict=True,
        )
        == 6000
    )


def test_confidence_clamps_to_9900_and_never_uses_float() -> None:
    result = confidence_bps(
        tier=1,
        agreeing_origin_groups={f"origin-{index}" for index in range(20)},
        has_conflict=False,
    )
    assert result == 9900
    assert isinstance(result, int)
