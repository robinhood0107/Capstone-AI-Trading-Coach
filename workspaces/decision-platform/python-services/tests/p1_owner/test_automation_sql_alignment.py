"""엔진과 마이그레이션이 같은 상태기계를 말하는지 확인한다.

DB whitelist가 엔진보다 좁으면 tick이 CAS 충돌(40001)로 죽고, 넓으면 잘못된 전이가 durable
하게 남는다. V91까지 실제로 좁았고 NEWS_CHECKING에서 나가는 전이 두 개가 거부됐다.
"""

from __future__ import annotations

import re

from app.data._shared.repository_root import repository_root
from app.p1_owner.automation import _LEGAL_TRANSITIONS

_MIGRATIONS = (
    repository_root(__file__, 5)
    / "workspaces/decision-platform/spring-api/src/main/resources/db/migration"
)
_V93 = _MIGRATIONS / "V93__p1_automation_pipeline_continuity.sql"


def _whitelist_pairs() -> frozenset[tuple[str, str]]:
    body = _V93.read_text(encoding="utf-8")
    start = body.index("$p1_automation_transition_valid_v2$")
    end = body.index("$p1_automation_transition_valid_v2$;", start)
    return frozenset(
        (current, following)
        for current, following in re.findall(r"\('([A-Z_]+)','([A-Z_]+)'\)", body[start:end])
    )


def test_db_transition_whitelist_matches_the_engine_exactly() -> None:
    assert _whitelist_pairs() == _LEGAL_TRANSITIONS


def test_every_active_state_may_halt_and_terminal_states_never_leave() -> None:
    from app.p1_owner.automation import _ACTIVE_STATES, _TERMINAL_STATES

    for state in _ACTIVE_STATES:
        assert (state, "HALTED") in _LEGAL_TRANSITIONS
    for state in _TERMINAL_STATES:
        assert not [pair for pair in _LEGAL_TRANSITIONS if pair[0] == state]


def test_readiness_no_longer_blocks_forever_on_the_bots_own_filled_order() -> None:
    body = _V93.read_text(encoding="utf-8")
    # 사람이 낸 미결 주문만 봇을 막고, 봇 예약에 연결된 주문은 판정에서 빠진다.
    assert "public.p1_automation_open_work_clear_v3" in body
    assert "FROM public.automation_order_reservations reservation" in body
    for function in ("p1_automation_runtime_readiness_v1", "p1_read_automation_runtime_state_v1"):
        start = body.index(f"${function}$")
        end = body.index(f"${function}$;", start)
        assert "FROM public.orders item" not in body[start:end]


def test_sql_realized_pnl_uses_the_same_integer_35bp_round_trip_cost() -> None:
    body = _V93.read_text(encoding="utf-8")
    start = body.index("realized_delta:=\n")
    formula = body[start : body.index("END IF;", start)]
    assert "*35+19999" in formula.replace(" ", "").replace("\n", "")
    assert "/20000" in formula
