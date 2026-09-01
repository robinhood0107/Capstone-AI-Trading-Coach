"""AI 판단이 자동매매를 실제로 바꾸는지, 그리고 그 바꿈이 경계를 넘지 않는지 확인한다.

이 파일이 지키는 것은 하나다. 모델은 점수와 차단만 내고, 무엇을 얼마나 살지는 엔진이
결정론적으로 계산한다. 그래서 같은 판단에 같은 주문이 나오고 그 계산은 감사 가능하다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

from app.p1_owner.automation import (
    AiCandidateVerdict,
    AiJudgement,
    AutomationEngine,
    AutomationError,
    AutomationInputs,
    AutomationStore,
    FixtureAutomationTransport,
    Quote,
    SignalCandidate,
    _apply_judgement,
)

_KST = ZoneInfo("Asia/Seoul")
_SESSION = date(2026, 8, 26)
_NOW = datetime(2026, 8, 26, 9, 30, tzinfo=_KST)
_RUN_ID = "auto_run_fixture_0001"


def _store() -> AutomationStore:
    return AutomationStore(
        account_id="acct_fixture_0001",
        brokerage_mode="KIS_MOCK",
        principle_id="prc_fixture_0001",
        strategy_id="strategy_fixture_0001",
        baseline_account_digest="a" * 64,
    )


def _buy(symbol: str, expected_return: float) -> SignalCandidate:
    return SignalCandidate(symbol, "BUY", "BUY", expected_return)


def _transport(judgement: AiJudgement | None) -> FixtureAutomationTransport:
    quotes = {
        symbol: Quote(symbol, 75_000, 52_500, 97_500)
        for symbol in ("005930", "000001", "000002", "000003")
    }
    return FixtureAutomationTransport(quotes=quotes, ai_judgement=judgement)


def _inputs(*signals: SignalCandidate, **overrides: object) -> AutomationInputs:
    values: dict[str, object] = {
        "session_date": _SESSION,
        "signals": tuple(signals),
        "ai_judgement_provider_bound": True,
        "buyable_quantity": 10,
        "buyable_amount_krw": 100_000_000,
        "risk_allow": True,
    }
    values.update(overrides)
    return AutomationInputs(**cast(Any, values))


def _drive(
    store: AutomationStore,
    transport: FixtureAutomationTransport,
    inputs: AutomationInputs,
    *,
    ticks: int = 12,
) -> list[str]:
    store.create_run(run_id=_RUN_ID, session_date=_NOW.date(), now=_NOW)
    states: list[str] = []
    for index in range(1, ticks + 1):
        result = AutomationEngine(store).tick(
            run_id=_RUN_ID,
            tick_id=f"tick_{index:03d}",
            now=_NOW + timedelta(seconds=index),
            inputs=inputs,
            transport=transport,
        )
        states.append(cast(str, result["state"]))
        if states[-1] in {"COMPLETED", "SKIPPED_NO_ACTION", "NEWS_VETOED", "HALTED"}:
            break
    return states


def _verdict(symbol: str, score: float, *, veto: bool = False) -> AiCandidateVerdict:
    return AiCandidateVerdict(symbol, score, veto, "테스트 사유")


# ------------------------------------------------------------------ 재순위


def test_ai_score_moves_a_lower_expected_return_candidate_to_the_front() -> None:
    # 규칙만으로는 기대수익 1등인 005930을 산다. AI가 그 후보를 낮게 보면 순위가 바뀐다.
    judgement = AiJudgement((_verdict("005930", 0.2), _verdict("000001", 0.9)), "요약")
    store = _store()
    transport = _transport(judgement)

    states = _drive(store, transport, _inputs(_buy("005930", 0.05), _buy("000001", 0.01)))

    run = store.runs[_RUN_ID]
    assert "AI_JUDGING" in states
    assert run.ai_baseline_symbol == "005930"
    assert run.selected_symbol == "000001"
    assert run.ai_participation == "APPLIED"


def test_the_rule_ranking_stands_when_the_model_agrees_with_it() -> None:
    judgement = AiJudgement((_verdict("005930", 0.9), _verdict("000001", 0.2)), "요약")
    store = _store()

    _drive(store, _transport(judgement), _inputs(_buy("005930", 0.05), _buy("000001", 0.01)))

    run = store.runs[_RUN_ID]
    assert (run.ai_baseline_symbol, run.selected_symbol) == ("005930", "005930")


def test_a_candidate_the_model_did_not_score_falls_back_to_neutral_not_to_last() -> None:
    # 답을 못 받은 것이 나쁜 후보라는 뜻은 아니다. 중립 0.5보다 낮은 점수만 뒤로 밀린다.
    scored = _buy("005930", 0.05)
    unscored = _buy("000001", 0.01)
    judgement = AiJudgement((_verdict("005930", 0.2),), "요약")

    ranked = _apply_judgement((scored, unscored), judgement)

    assert [item.symbol for item in ranked] == ["000001", "005930"]


# ------------------------------------------------------------------ 거부권


def test_a_vetoed_candidate_is_not_bought_and_the_next_one_is() -> None:
    judgement = AiJudgement((_verdict("005930", 0.9, veto=True), _verdict("000001", 0.3)), "요약")
    store = _store()

    _drive(store, _transport(judgement), _inputs(_buy("005930", 0.05), _buy("000001", 0.01)))

    run = store.runs[_RUN_ID]
    assert run.ai_vetoed_symbols == ("005930",)
    assert run.selected_symbol == "000001"


def test_vetoing_every_candidate_stops_the_run_without_inventing_a_symbol() -> None:
    judgement = AiJudgement(
        (_verdict("005930", 0.9, veto=True), _verdict("000001", 0.9, veto=True)), "요약"
    )
    store = _store()

    states = _drive(
        store, _transport(judgement), _inputs(_buy("005930", 0.05), _buy("000001", 0.01))
    )

    run = store.runs[_RUN_ID]
    assert states[-1] == "SKIPPED_NO_ACTION"
    assert run.selected_symbol is None
    assert set(run.ai_vetoed_symbols) == {"005930", "000001"}


def test_a_symbol_outside_the_candidate_set_cannot_enter_the_order() -> None:
    # 모델이 후보에 없는 종목을 답해도 그것은 순위에도 주문에도 닿지 않는다.
    candidates = (_buy("005930", 0.05),)
    judgement = AiJudgement((_verdict("000009", 0.99),), "요약")

    ranked = _apply_judgement(candidates, judgement)

    assert [item.symbol for item in ranked] == ["005930"]


# ------------------------------------------------------------------ 수량


def test_ai_judgement_has_no_quantity_authority() -> None:
    store = _store()
    judgement = AiJudgement((_verdict("005930", 0.9),), "요약")

    _drive(store, _transport(judgement), _inputs(_buy("005930", 0.05)))

    run = store.runs[_RUN_ID]
    assert run.reservation is not None
    assert run.reservation.quantity == 10
    assert not hasattr(run, "ai_confidence")
    assert not hasattr(run, "ai_quantity_before")


# ------------------------------------------------------------------ AI 없이도 돈다


def test_the_run_completes_and_says_so_when_no_provider_is_bound() -> None:
    store = _store()

    states = _drive(
        store,
        _transport(None),
        _inputs(_buy("005930", 0.05), ai_judgement_provider_bound=False),
    )

    run = store.runs[_RUN_ID]
    assert states[-1] == "COMPLETED"
    assert run.ai_participation == "NOT_PARTICIPATED"
    assert run.ai_judge_call_count == 0
    assert run.selected_symbol == "005930"


def test_the_run_completes_when_both_providers_failed_to_answer() -> None:
    store = _store()
    transport = _transport(None)

    states = _drive(store, transport, _inputs(_buy("005930", 0.05)))

    run = store.runs[_RUN_ID]
    assert states[-1] == "COMPLETED"
    assert run.ai_participation == "NOT_PARTICIPATED"
    # 물어보기는 했다. 물어본 적이 없는 것과 답을 못 받은 것은 다르다.
    assert (run.ai_judge_call_count, transport.judge_calls) == (1, 1)


# ------------------------------------------------------------------ 값 자체의 경계


@pytest.mark.parametrize(
    "verdicts",
    [
        (("005930", 1.5, False),),
        (("005930", -0.1, False),),
        (("005930", float("nan"), False),),
    ],
)
def test_a_score_outside_zero_to_one_is_refused_at_the_boundary(
    verdicts: tuple[tuple[str, float, bool], ...],
) -> None:
    with pytest.raises(AutomationError):
        AiCandidateVerdict(verdicts[0][0], verdicts[0][1], verdicts[0][2], "사유")


def test_an_empty_reason_is_refused_because_an_unexplained_score_cannot_be_audited() -> None:
    with pytest.raises(AutomationError):
        AiCandidateVerdict("005930", 0.5, False, "")


def test_the_same_symbol_cannot_be_judged_twice_in_one_answer() -> None:
    with pytest.raises(AutomationError):
        AiJudgement((_verdict("005930", 0.2), _verdict("005930", 0.9)), "요약")
