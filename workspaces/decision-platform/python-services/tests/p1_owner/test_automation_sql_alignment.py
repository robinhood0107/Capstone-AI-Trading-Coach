"""엔진과 마이그레이션이 같은 상태기계를 말하는지 확인한다.

DB whitelist가 엔진보다 좁으면 tick이 CAS 충돌(40001)로 죽고, 넓으면 잘못된 전이가 durable
하게 남는다. V91까지 실제로 좁았고 NEWS_CHECKING에서 나가는 전이 두 개가 거부됐다.
"""

from __future__ import annotations

import re

from app.data._shared.repository_root import repository_root
from app.p1_owner.automation import _LEGAL_TRANSITIONS
from app.p1_owner.automation_runtime import (
    _BUY_SUBMIT_DEADLINE,
    _CANCEL_BOUNDARY,
    _DECISION_TIMES,
    _SELL_SUBMIT_DEADLINE,
)

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


_BEGIN_EXECUTION = "p1_begin_automation_portfolio_execution_v2"


def _begin_execution_body(body: str) -> str:
    """`CREATE ... FUNCTION` 부터 `END $begin$;` 까지만 잘라낸다."""

    start = body.index("FUNCTION public." + _BEGIN_EXECUTION + "(")
    start = body.rindex("CREATE", 0, start)
    end = body.index("END $begin$;", start) + len("END $begin$;")
    return body[start:end]


def test_the_buy_submit_window_in_sql_matches_the_python_decision_schedule() -> None:
    """DB 의 매수 마감과 Python 의 결정 시점이 어긋나면 주문이 한 건도 안 나간다.

    2026-09-15 에 실제로 그랬다. SQL 은 09:40 이후 BUY 를 거부했는데 Python 은
    09:45/11:00/14:00 에 진입했다 - **세 시점 전부 마감 이후**라 포트폴리오 경로의 매수가
    구조적으로 불가능했다. 두 값을 여기서 묶는다.
    """

    version, body = _latest_definition(_BEGIN_EXECUTION)
    assert version >= 172, f"expected the realigned definition, found V{version}"

    buy = _BUY_SUBMIT_DEADLINE.strftime("%H:%M")
    sell = _SELL_SUBMIT_DEADLINE.strftime("%H:%M")
    expected = f"THEN time '{buy}' ELSE time '{sell}' END"
    assert expected in body, f"missing {expected!r}"
    assert "time '09:40'" not in body, "the dead 09:40 cutoff is still in the live definition"

    assert all(item < _BUY_SUBMIT_DEADLINE for item in _DECISION_TIMES)
    assert _BUY_SUBMIT_DEADLINE < _CANCEL_BOUNDARY


def test_the_replaced_begin_function_kept_every_other_eligibility_guard() -> None:
    """본문은 시간 리터럴 한 줄만 달라야 한다.

    이 레포는 V167 을 기억으로 재구성했다가 인증 검사·claim 조건·편입 baseline 을 통째로
    날린 적이 있다. 그 실패 방식을 직접 막는다 - 나머지 일곱 가드(역할/ordinal/멱등키,
    claim FOR UPDATE 창, control ARMED, owner_stop_active, risk_kill_switch, 직전 ordinal
    미terminal, 23505 멱등 drift 와 NO_OP 단락)가 글자 그대로 남아 있어야 한다.
    """

    original = _begin_execution_body(
        (_MIGRATIONS / "V163__automation_portfolio_news_operational_closure.sql").read_text(
            encoding="utf-8"
        )
    )
    _, latest_body = _latest_definition(_BEGIN_EXECUTION)
    latest = _begin_execution_body(latest_body)

    before = original.splitlines()
    after = latest.splitlines()
    assert len(before) == len(after), "the function body gained or lost lines"
    differing = [index for index, pair in enumerate(zip(before, after)) if pair[0] != pair[1]]
    # 0 번째 줄은 CREATE -> CREATE OR REPLACE 접두사다. 나머지 둘은 V172 의 시간 리터럴과
    # V175 의 ordinal 상한이고, 각 줄의 **내용**까지 확인한다.
    assert len(differing) == 3, f"unexpected differing lines: {differing}"
    assert differing[0] == 0
    assert "CREATE OR REPLACE FUNCTION" in after[0]

    guard, cutoff = differing[1], differing[2]
    assert "p_ordinal NOT BETWEEN 1 AND 3" in before[guard]
    assert "p_ordinal NOT BETWEEN 1 AND 5" in after[guard]
    assert "time '09:40'" in before[cutoff]
    assert _BUY_SUBMIT_DEADLINE.strftime("%H:%M") in after[cutoff]


def test_the_begin_function_is_replaced_not_dropped() -> None:
    """DROP 은 V163 의 OWNER/GRANT 를 조용히 버려 첫 제출부터 42501 을 만든다."""

    _, body = _latest_definition(_BEGIN_EXECUTION)
    assert f"DROP FUNCTION public.{_BEGIN_EXECUTION}" not in body
    assert f"CREATE OR REPLACE FUNCTION public.{_BEGIN_EXECUTION}(" in body


_FINISH_EXECUTION = "p1_finish_automation_portfolio_execution_v2"


def _finish_execution_body(body: str) -> str:
    """`CREATE ... FUNCTION` 부터 `END $finish$;` 까지만 잘라낸다."""

    start = body.index("FUNCTION public." + _FINISH_EXECUTION + "(")
    start = body.rindex("CREATE", 0, start)
    end = body.index("END $finish$;", start) + len("END $finish$;")
    return body[start:end]


def test_the_replaced_finish_function_kept_every_other_settlement_guard() -> None:
    """부분체결 적재는 네 줄만 바꾼다. 나머지 정산 가드는 글자 그대로 남아야 한다.

    이 레포는 V167 을 기억으로 재구성했다가 인증 검사·claim 조건·편입 baseline 을 통째로
    날린 적이 있다. begin 함수에 건 것과 같은 고정을 finish 에도 건다.
    """

    original = _finish_execution_body(
        (_MIGRATIONS / "V163__automation_portfolio_news_operational_closure.sql").read_text(
            encoding="utf-8"
        )
    )
    version, latest_body = _latest_definition(_FINISH_EXECUTION)
    assert version >= 173, f"expected the partial-fill definition, found V{version}"
    latest = _finish_execution_body(latest_body)

    before = original.splitlines()
    after = latest.splitlines()
    assert len(before) == len(after), "the function body gained or lost lines"
    differing = [index for index, pair in enumerate(zip(before, after)) if pair[0] != pair[1]]
    assert len(differing) == 6, f"expected 6 differing lines, got {differing}"
    assert differing[0] == 0
    assert "CREATE OR REPLACE FUNCTION" in after[0]

    # 다섯 곳 각각의 **내용**까지 확인한다. 줄 수만 세면 엉뚱한 줄이 바뀌어도 통과한다.
    # 첫째는 V175 의 ordinal 상한이고 나머지 넷은 V173 의 부분체결 적재다.
    markers = [
        ("p_ordinal NOT BETWEEN 1 AND 3", "p_ordinal NOT BETWEEN 1 AND 5"),
        ("IF p_state='PENDING_RECONCILIATION' THEN", "applied_filled_quantity"),
        ("IF p_state='FILLED' AND", "p_filled_quantity+p_leaves_quantity<>execution.quantity"),
        (
            "entry_ordered_quantity=entry_ordered_quantity+delta_quantity",
            "entry_unfilled_quantity=",
        ),
        ("status=CASE WHEN p_state='FILLED'", "'PARTIALLY_FILLED'"),
    ]
    for index, (old_marker, new_marker) in zip(differing[1:], markers):
        assert old_marker in before[index], f"line {index}: V163 lost {old_marker!r}"
        assert new_marker in after[index], f"line {index}: V173 missing {new_marker!r}"


def test_the_finish_function_is_replaced_not_dropped() -> None:
    """DROP 은 V163 의 OWNER/GRANT 를 조용히 버려 첫 정산부터 42501 을 만든다."""

    _, body = _latest_definition(_FINISH_EXECUTION)
    statements = [line for line in body.splitlines() if not line.lstrip().startswith("--")]
    assert not any(f"DROP FUNCTION public.{_FINISH_EXECUTION}" in line for line in statements)
    assert f"CREATE OR REPLACE FUNCTION public.{_FINISH_EXECUTION}(" in body


def test_the_finish_function_keeps_pgcrypto_on_search_path() -> None:
    """`CREATE OR REPLACE` 는 SET 절을 새 헤더로 **교체한다**.

    V165 가 pgcrypto(digest()) 때문에 넣은 `search_path TO pg_catalog,public` 을 다시 발행하지
    않으면 조용히 다시 깨진다. 헤더에 인라인이든 뒤따르는 ALTER 든 둘 중 하나는 있어야 한다.
    """

    _, body = _latest_definition(_FINISH_EXECUTION)
    inline = "SET search_path=pg_catalog,public" in body
    reissued = "SET search_path TO pg_catalog,public" in body
    assert inline or reissued, "pgcrypto search_path is not restored after CREATE OR REPLACE"


_STAGE_PLAN = "p1_stage_automation_portfolio_plan_v1"


def test_the_sql_order_ceiling_matches_the_table_check_and_python() -> None:
    """상한이 SQL 에서만 3 이면 정책을 4 로 올리는 순간 그 세션이 22023 으로 죽는다.

    V167 이 `max_orders_per_session` 정책 CHECK 와 ordinal 테이블 CHECK 를 1..5 로 올렸는데
    계획·제출·정산 함수 셋은 3 에 남아 있었다. 정책을 올리는 것은 CHECK 가 **명시적으로
    허용**하므로, 이것은 언젠가 반드시 밟는 지뢰였다. V172 의 09:40 과 같은 종류다.
    """

    from app.p1_owner.automation_portfolio import _MAX_ORDERS_PER_SESSION

    # 표가 허용하는 상한이 진실이고 나머지가 그것을 따른다.
    table = (_MIGRATIONS / "V167__automation_same_session_exit_guard.sql").read_text(
        encoding="utf-8"
    )
    assert "CHECK (ordinal BETWEEN 1 AND 5)" in table
    assert "CHECK (max_orders_per_session BETWEEN 1 AND 5)" in table
    assert _MAX_ORDERS_PER_SESSION == 5

    for function, guard in (
        (_STAGE_PLAN, "jsonb_array_length(p_orders) NOT BETWEEN 1 AND 5"),
        (_BEGIN_EXECUTION, "p_ordinal NOT BETWEEN 1 AND 5"),
        (_FINISH_EXECUTION, "p_ordinal NOT BETWEEN 1 AND 5"),
    ):
        version, body = _latest_definition(function)
        assert version >= 175, f"{function}: expected the realigned definition, found V{version}"
        assert guard in body, f"{function}: missing {guard!r}"
        stale = guard.replace("AND 5", "AND 3")
        assert stale not in body, f"{function}: the 1..3 ceiling is still live"


def test_the_stage_function_is_replaced_not_dropped() -> None:
    """DROP 은 V161:483 OWNER 와 :490 GRANT 를 조용히 버려 첫 계획부터 42501 을 만든다."""

    _, body = _latest_definition(_STAGE_PLAN)
    statements = [line for line in body.splitlines() if not line.lstrip().startswith("--")]
    assert not any(f"DROP FUNCTION public.{_STAGE_PLAN}" in line for line in statements)
    assert f"CREATE OR REPLACE FUNCTION public.{_STAGE_PLAN}(" in body


def test_the_replaced_stage_function_kept_every_other_plan_guard() -> None:
    """본문은 상한 한 줄만 달라야 한다 - 자본·배분·해시 검증이 그대로 남아야 한다."""

    def _stage_body(body: str) -> str:
        start = body.index("FUNCTION public." + _STAGE_PLAN + "(")
        start = body.rindex("CREATE", 0, start)
        end = body.index("END $stage$;", start) + len("END $stage$;")
        return body[start:end]

    original = _stage_body(
        (_MIGRATIONS / "V161__automation_capital_policy_and_order_executions.sql").read_text(
            encoding="utf-8"
        )
    ).replace("CREATE FUNCTION public.", "CREATE OR REPLACE FUNCTION public.", 1)
    _, latest_body = _latest_definition(_STAGE_PLAN)
    latest = _stage_body(latest_body)

    before = original.splitlines()
    after = latest.splitlines()
    assert len(before) == len(after), "the function body gained or lost lines"
    differing = [index for index, pair in enumerate(zip(before, after)) if pair[0] != pair[1]]
    assert len(differing) == 1, f"unexpected differing lines: {differing}"
    assert "jsonb_array_length(p_orders) NOT BETWEEN 1 AND 3" in before[differing[0]]
    assert "jsonb_array_length(p_orders) NOT BETWEEN 1 AND 5" in after[differing[0]]


_CLAIM_WINDOW = "AND (claim_state='ACTIVE' OR (claim_state='RELEASED'"


def test_the_order_authorizing_functions_keep_the_tight_claim_window() -> None:
    """주문을 **인가**하는 함수는 claim 창을 좁게 유지해야 한다.

    `ACTIVE` 이거나, `RELEASED` 라면 **같은 세션일 + 15:20 이전**일 때만 통과한다.
    이것이 이 서브시스템의 인가 경계다. 느슨해지면 지난 세션의 토큰으로 주문을 낼 수 있다.
    """

    for function in (
        "p1_begin_automation_portfolio_execution_v2",
        "p1_stage_automation_portfolio_plan_v2",
        "p1_read_automation_portfolio_sources_v1",
        "p1_record_automation_buyable_receipt_v1",
    ):
        _, migration = _latest_definition(function)
        start = migration.index("FUNCTION public." + function + "(")
        start = migration.rindex("CREATE", 0, start)
        body = migration[start : migration.index("END $", start)]
        assert _CLAIM_WINDOW in body, f"{function}: the claim window was widened"
        assert "time '15:20'" in body, f"{function}: the 15:20 bound is gone"


def test_the_settlement_function_deliberately_has_no_claim_window() -> None:
    """`finish` 에는 창이 **없다.** 이것은 누락이 아니라 설계다.

    체결은 우리가 결정하는 것이 아니라 브로커가 통보하는 사실이다. 장 마감 뒤에 도착한
    체결도 반드시 장부에 들어가야 한다. 여기에 `begin` 과 같은 창을 달면 15:20 이후의
    적재가 42501 로 막히고 **체결된 주식이 장부에서 사라진다.**

    나란히 놓인 두 함수라 "정리"하고 싶어지는 자리다. 그래서 못박는다.
    """

    # V175 는 한 파일에 세 함수를 담는다. 파일 전체에서 찾으면 맨 앞 함수의 claim 읽기를
    # 잡는다 - 실제로 처음에 그렇게 틀렸다. 반드시 finish 본문으로 좁힌다.
    _, migration = _latest_definition(_FINISH_EXECUTION)
    body = _finish_execution_body(migration)
    claim_read = "SELECT * INTO claim FROM public.automation_runtime_claim\n"
    index = body.index(claim_read) + len(claim_read)
    predicate = body[index : body.index(";", index)]

    assert "run_id=p_run_id" in predicate
    assert "claim_token_hash=p_claim_token_hash" in predicate
    assert "FOR UPDATE" in predicate
    assert "claim_state" not in predicate, "finish gained a claim-state window"
    assert "15:20" not in predicate, "finish gained a time bound"


def test_the_read_window_is_what_strands_an_execution_after_the_close() -> None:
    """마감 sweep 이 아직 열려 있는 이유를 코드에 붙여 둔다.

    `finish` 는 언제든 적재할 수 있지만, 그 앞에서 실행을 **찾는**
    `p1_read_automation_portfolio_execution_v1` 은 창을 가진다. claim 이 `ACTIVE` 인 동안은
    시각 제한이 없어 세션 중에는 문제가 없다. 그러나 claim 이 `RELEASED` 로 바뀐 뒤
    15:20 이 지나면 읽기가 42501 로 막혀 미terminal 실행을 더는 볼 수 없다.

    즉 sweep 에 필요한 것은 **세 함수가 아니라 이 읽기 하나**다. 2026-09-15 에 그 범위를
    잘못 적었고, 실측으로 바로잡았다. 이 테스트는 그 사실을 다음 사람에게 넘긴다.
    """

    name = "p1_read_automation_portfolio_execution_v1"
    _, migration = _latest_definition(name)
    start = migration.index("FUNCTION public." + name + "(")
    start = migration.rindex("CREATE", 0, start)
    body = migration[start : migration.index("END $", start)]

    assert _CLAIM_WINDOW in body
    assert "time '15:20'" in body
    # ACTIVE 에는 시각 조건이 붙지 않는다 - 세션 중 대사가 마감을 넘겨도 계속된다.
    active_clause = body[body.index(_CLAIM_WINDOW) : body.index("time '15:20'")]
    assert active_clause.count("claim_state='ACTIVE'") == 1
    assert "session_date=" in active_clause


# ---------------------------------------------------------------------------
# 판단 결속의 범위 - 2026-09-16 사고의 회귀
#
# 45주 중 27주가 체결된 주문이 15:20 정산에서 40001 로 막혔다. 다섯 조건 중 걸린 것은
# `valid_until<=statement_timestamp()` 하나였다. Decision 의 유효기간은 약 30초인데
# 정산은 여섯 시간 뒤다 - 이 하루의 사고가 아니라 체결된 주문이면 언제나 닫히지 못하는
# 구조였다. V176 이 가드를 그 주석("주문을 여는 전이에서만")에 맞췄다.
# ---------------------------------------------------------------------------

_CHECKPOINT_V2 = "p1_advance_automation_checkpoint_v2"


def _checkpoint_v2_scope(body: str) -> frozenset[str]:
    """판단 결속을 요구하는 next_state 집합을 정의 본문에서 읽는다."""

    marker = "IF p_decision_id IS NOT NULL AND p_next_state IN ("
    start = body.index(marker) + len(marker)
    end = body.index(") THEN", start)
    return frozenset(re.findall(r"'([A-Z_]+)'", body[start:end]))


def test_the_decision_binding_is_required_only_on_transitions_that_open_an_order() -> None:
    """닫는 전이는 판단 결속을 다시 요구하지 않는다.

    닫는 전이는 무엇도 사지 못한다 - 취소하고 이미 체결된 수량을 기록할 뿐이다. 거기에
    30초짜리 유효기간을 다시 걸면 체결된 주문이 영원히 열린 채로 남고, 그 claim 이
    ACTIVE 로 남아 다음 세션까지 막는다.
    """

    version, body = _latest_definition(_CHECKPOINT_V2)
    assert version >= 176, f"expected the V176 scope, found V{version}"

    scope = _checkpoint_v2_scope(body)
    assert scope == {"ORDER_SUBMITTING", "ORDER_SUBMITTED"}
    for closing in ("PENDING_RECONCILIATION", "COMPLETED", "CANCELLED_UNFILLED"):
        assert closing not in scope, f"{closing} 은 주문을 여는 전이가 아니다"


def test_the_checkpoint_function_is_replaced_not_dropped() -> None:
    """살아있는 정의를 옮겨 적었는지 확인한다. DROP 은 권한과 소유자를 함께 날린다."""

    body = (_MIGRATIONS / "V176__automation_decision_binding_opening_transitions.sql").read_text(
        encoding="utf-8"
    )
    assert f"CREATE OR REPLACE FUNCTION public.{_CHECKPOINT_V2}(" in body
    assert "DROP FUNCTION" not in body
    # `CREATE OR REPLACE` 는 SET 절을 갈아치운다. 살아있던 값이 그대로 다시 적혀 있어야 한다.
    assert "SET search_path TO 'pg_catalog'" in body


def test_the_replaced_checkpoint_function_kept_every_other_binding_guard() -> None:
    """바뀐 것은 상태 목록 하나뿐이다. 나머지 인가 조건은 글자 그대로 남아야 한다."""

    _, body = _latest_definition(_CHECKPOINT_V2)
    for guard in (
        "decision_row.outcome<>'ALLOW'",
        "NOT decision_row.can_submit_order",
        "decision_row.enforcement_action<>'NONE'",
        "decision_row.portfolio_source<>'KIS_MOCK'",
        "decision_row.principle_version_id<>control_row.principle_version_id",
        "decision_row.valid_until<=statement_timestamp()",
        "artifact_row.decision_id IS NULL",
        # 주문 의도 자체의 검증은 시간과 무관하다. 닫는 전이에서도 계속 돈다.
        "automation exact order intent invalid",
        "automation order quantity conservation failed",
    ):
        assert guard in body, f"사라진 가드: {guard}"
