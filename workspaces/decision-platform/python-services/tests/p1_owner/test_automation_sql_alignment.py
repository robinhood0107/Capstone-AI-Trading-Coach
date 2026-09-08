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
_TRANSITION_TAG = "$p1_automation_transition_valid_v2$"


def _latest_transition_migration() -> tuple[int, str]:
    """전이 표를 마지막으로 다시 쓴 마이그레이션. 버전이 큰 정의가 런타임의 진실이다.

    한 파일을 이름으로 붙들면 다음에 표를 옮겨 쓸 때 이 대조가 조용히 옛 정의를 본다.
    그러면 엔진과 DB가 어긋나도 초록불이 뜬다.
    """

    candidates: list[tuple[int, str]] = []
    for path in _MIGRATIONS.glob("V*__*.sql"):
        body = path.read_text(encoding="utf-8")
        if "CREATE OR REPLACE FUNCTION public.p1_automation_transition_valid_v2" not in body:
            continue
        version = int(re.match(r"V(\d+)__", path.name).group(1))  # type: ignore[union-attr]
        candidates.append((version, body))
    assert candidates, "transition whitelist migration is missing"
    return max(candidates, key=lambda item: item[0])


def _whitelist_pairs() -> frozenset[tuple[str, str]]:
    _, body = _latest_transition_migration()
    start = body.index(_TRANSITION_TAG)
    end = body.index(f"{_TRANSITION_TAG};", start)
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


_V95 = _MIGRATIONS / "V95__p1_principle_binding_order_sizing.sql"


def test_order_sizing_inputs_are_no_longer_hardcoded_constants() -> None:
    # V91은 min() 다섯 항 중 셋을 상수로 열어 두어 사용자 원칙이 주문 크기에 닿지 못했다.
    body = _V95.read_text(encoding="utf-8")

    assert "'openPositionMarketValueKrw',open_position_value" in body
    assert "'principleMaxSingleOrderKrw',max_single_order" in body
    assert "'principleAssetRemainingKrw',asset_remaining" in body
    assert "'openPositionMarketValueKrw',0" not in body
    assert "'principleMaxSingleOrderKrw',9223372036854775807" not in body


def test_principle_limits_come_from_enabled_rules_only() -> None:
    body = _V95.read_text(encoding="utf-8")

    # 꺼진 규칙은 제한이 아니다. 규칙이 없으면 MAX_BIGINT로 남아 다른 항이 결정한다.
    assert "rule->>'ruleId'='max_single_order_amount' AND (rule->>'enabled')::boolean" in body
    assert "rule->>'ruleId'='max_position_per_asset' AND (rule->>'enabled')::boolean" in body
    assert "COALESCE(min((rule->>'threshold')::bigint),9223372036854775807)" in body


def test_state_exposes_the_classified_symbol_set_for_risk_completeness() -> None:
    body = _V95.read_text(encoding="utf-8")

    assert "'instrumentCatalogSymbols',catalog_symbols" in body
    assert "FROM public.latest_instrument_catalog_observations catalog" in body
    assert "WHERE catalog.completeness='COMPLETE'" in body


def _latest_definition(function: str) -> tuple[int, str]:
    """그 함수를 마지막으로 정의한 마이그레이션. 버전이 큰 정의가 런타임의 진실이다.

    한 파일을 이름으로 붙들면 다음에 함수를 옮겨 쓸 때 이 대조가 조용히 옛 정의를 본다.
    `_latest_transition_migration` 이 전이 표에 대해 하는 일과 같은 이유다.
    """

    candidates: list[tuple[int, str]] = []
    for path in _MIGRATIONS.glob("V*__*.sql"):
        body = path.read_text(encoding="utf-8")
        if f"FUNCTION public.{function}(" not in body:
            continue
        if f"CREATE FUNCTION public.{function}(" not in body and (
            f"CREATE OR REPLACE FUNCTION public.{function}(" not in body
        ):
            continue
        match = re.match(r"V(\d+)__", path.name)
        assert match is not None
        candidates.append((int(match.group(1)), body))
    assert candidates, f"{function} definition migration is missing"
    return max(candidates, key=lambda item: item[0])


def test_the_data_gap_retry_boundary_is_bounded_in_count_and_time() -> None:
    """데이터 공백 재시도가 무한히 늘어나지 않는다.

    이 fallback 은 `SKIPPED_DATA_UNAVAILABLE` 로 닫힌 세션을 되살린다. 상한이 없으면 하루
    종일 같은 세션을 다시 열고, 취소 경계를 넘어 되살리면 장 마감 뒤에 주문을 낸다.

    엔진 쪽 순서(`retry_at` -> 대기 -> 날짜·연결성 재확인 -> `resume_data_gap`)는
    `test_automation_runtime.py` 가 본다. 여기서는 DB 가 그 경계를 실제로 들고 있는지 본다.
    """

    version, body = _latest_definition("p1_automation_data_gap_retry_at_v1")

    assert version >= 142
    # 두 번까지만 되살린다.
    assert "retry.used=0" in body or "retry_count=0" in body
    # 첫 재시도는 2분, 그다음은 5분.
    assert "interval '2 minutes'" in body
    assert "interval '5 minutes'" in body
    # 취소 경계를 넘으면 재시도 시각을 주지 않는다.
    assert "time '15:20'" in body
    assert "RETURN NULL" in body
    # 재시도 횟수는 durable 이벤트에서 센다. 프로세스 메모리가 아니다.
    assert "'SESSION_RESUMED'" in body
    # 상주 런타임 role 만 부를 수 있다.
    assert "TO decision_automation_runtime" in body


def test_the_resume_function_keeps_the_same_bounds_as_the_boundary() -> None:
    """재개 함수와 경계 함수가 같은 상한을 말한다.

    두 곳이 갈라지면 경계가 "지금 재개해도 된다"고 하는데 재개가 거부되거나, 반대로 경계가
    막는 시각에 재개가 통과한다.
    """

    _, body = _latest_definition("p1_resume_automation_data_gap_v1")

    assert "retry_count>=2" in body
    assert "interval '2 minutes'" in body
    assert "interval '5 minutes'" in body
    assert "time '09:30'" in body
    assert "time '15:20'" in body
    assert "'SESSION_RESUMED'" in body


def test_the_resume_path_never_raises_the_provider_call_cap() -> None:
    """재개가 provider 호출 상한을 넓히지 않는다.

    되살린 세션이 새 예산을 얻으면 fallback 이 상한 우회 통로가 된다.
    """

    _, body = _latest_definition("p1_advance_automation_checkpoint_v3")

    # V100 이 못박은 상한이 최신 정의에도 그대로 있다.
    assert "p_provider_call_count NOT BETWEEN 0 AND 16" in body
    # 되돌아가는 카운터도 거부한다.
    assert "p_provider_call_count<checkpoint_row.provider_call_count" in body
