from __future__ import annotations

_BASE_BY_TIER = {1: 9000, 2: 7000, 3: 5000, 4: 3000}


def confidence_bps(
    *,
    tier: int,
    agreeing_origin_groups: set[str],
    has_conflict: bool,
) -> int:
    """authority 선택 뒤 독립 upstream agreement와 conflict를 정수 basis point로 설명한다."""
    try:
        base = _BASE_BY_TIER[tier]
    except KeyError:
        raise ValueError("tier must be between 1 and 4") from None
    agreement_bonus = 500 * len({group for group in agreeing_origin_groups if group})
    conflict_penalty = 2000 if has_conflict else 0
    return min(9900, max(0, base + agreement_bonus - conflict_penalty))
