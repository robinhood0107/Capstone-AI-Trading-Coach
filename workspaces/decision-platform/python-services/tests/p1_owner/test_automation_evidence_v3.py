from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd
import pytest

from app.p1_owner.automation import (
    AiCandidateVerdict,
    AiJudgement,
    AutomationEngine,
    AutomationInputs,
    AutomationPolicySnapshot,
    AutomationStore,
    CandidateScreening,
    EvidenceSpan,
    FixtureAutomationTransport,
    NewsScreeningBatch,
    Quote,
    SignalCandidate,
    _candidate_set_sha256,
)
from app.p1_owner.automation_atr import CompletedDailyBar

_SESSION = date(2026, 8, 26)
_NOW = datetime(2026, 8, 26, 9, 30, tzinfo=ZoneInfo("Asia/Seoul"))
_RUN = "auto_run_evidence_fixture_0001"


def _inputs(*signals: SignalCandidate) -> AutomationInputs:
    calendar = xcals.get_calendar("XKRX")
    anchor = calendar.previous_session(pd.Timestamp(_SESSION))
    expected = tuple(item.date() for item in calendar.sessions_window(anchor, -100))
    bars = tuple(CompletedDailyBar(item, 70_000, 71_000, 69_000, 70_000) for item in expected[-23:])
    return AutomationInputs(
        session_date=_SESSION,
        policy=AutomationPolicySnapshot.from_v3_preset(
            policy_id="auto_pol_" + "b" * 32,
            version=1,
            capital_limit_krw=10_000_000,
            preset="BALANCED",
        ),
        signals=signals,
        atr_histories={item.symbol: bars for item in signals},
        atr_expected_sessions=expected,
        ai_judgement_enabled=True,
        ai_judgement_provider_bound=True,
        ai_settings_sha256="c" * 64,
    )


def _store() -> AutomationStore:
    store = AutomationStore(
        "acct_fixture_0001",
        "KIS_MOCK",
        "prc_fixture_0001",
        "strategy_fixture_0001",
        "a" * 64,
    )
    store.create_run(run_id=_RUN, session_date=_SESSION, now=_NOW)
    return store


def _drive(
    store: AutomationStore, transport: FixtureAutomationTransport, inputs: AutomationInputs
) -> str:
    for index in range(1, 20):
        result = AutomationEngine(store).tick(
            run_id=_RUN,
            tick_id=f"evidence_{index}",
            now=_NOW + timedelta(seconds=index),
            inputs=inputs,
            transport=transport,
        )
        if result["state"] in {
            "COMPLETED",
            # 공시 근거 거부권이 닫는 종단 상태다. V141 이 이것을 terminal 로 취급한다.
            "NEWS_VETOED",
            "SKIPPED_NO_ACTION",
            "SKIPPED_DATA_UNAVAILABLE",
            "HALTED",
        }:
            return str(result["state"])
    raise AssertionError("run did not terminate")


def _candidate(symbol: str, expected: float) -> SignalCandidate:
    return SignalCandidate(symbol, "BUY", "BUY", expected)


def test_zero_evidence_skips_judge_and_preserves_rule_rank() -> None:
    candidates = (_candidate("000001", 0.04), _candidate("000002", 0.03))
    screening = NewsScreeningBatch(
        tuple(
            CandidateScreening(item.symbol, "AVAILABLE", "NO_VETO", 5_000, "NO_EVIDENCE")
            for item in candidates
        ),
        1,
        2,
    )
    transport = FixtureAutomationTransport(
        quotes={item.symbol: Quote(item.symbol, 75_000, 52_500, 97_500) for item in candidates},
        screening_batch=screening,
    )
    store = _store()
    assert _drive(store, transport, _inputs(*candidates)) == "COMPLETED"
    assert store.runs[_RUN].selected_symbol == "000001"
    assert transport.screen_calls == 1
    assert transport.judge_calls == 0
    assert transport.vertex_calls == 0


def test_verified_veto_with_no_surviving_evidence_skips_judge_and_selects_once() -> None:
    first = _candidate("000001", 0.05)
    second = _candidate("000002", 0.04)
    span = EvidenceSpan(
        "000001",
        "cit_fixture_000001",
        "src_official_dart",
        "OFFICIAL_PRIMARY",
        None,
        False,
        "d" * 64,
        "verified adverse fixture",
        "e" * 64,
    )
    screening = NewsScreeningBatch(
        (
            CandidateScreening("000001", "AVAILABLE", "VETO_BUY", 2_000, "VERIFIED", (span,)),
            CandidateScreening("000002", "AVAILABLE", "NO_VETO", 5_000, "NO_EVIDENCE"),
        ),
        1,
        1,
    )
    transport = FixtureAutomationTransport(
        quotes={
            item.symbol: Quote(item.symbol, 75_000, 52_500, 97_500) for item in (first, second)
        },
        screening_batch=screening,
    )
    store = _store()
    assert _drive(store, transport, _inputs(first, second)) == "COMPLETED"
    assert store.runs[_RUN].selected_symbol == "000002"
    assert transport.judge_calls == 0


class _RecordingJudgeTransport(FixtureAutomationTransport):
    judged_symbols: tuple[str, ...] = ()

    def judge(
        self,
        candidates: tuple[SignalCandidate, ...],
        candidate_set_sha256: str,
    ) -> AiJudgement | None:
        self.judged_symbols = tuple(item.symbol for item in candidates)
        return super().judge(candidates, candidate_set_sha256)


def test_judge_receives_only_post_screening_survivors() -> None:
    first = _candidate("000001", 0.05)
    second = _candidate("000002", 0.04)
    third = _candidate("000003", 0.03)
    veto_span = EvidenceSpan(
        "000001",
        "cit_fixture_000001",
        "src_official_dart",
        "OFFICIAL_PRIMARY",
        None,
        False,
        "d" * 64,
        "verified adverse fixture",
        "e" * 64,
    )
    surviving_span = EvidenceSpan(
        "000002",
        "cit_fixture_000002",
        "src_official_dart",
        "OFFICIAL_PRIMARY",
        None,
        False,
        "f" * 64,
        "verified neutral fixture",
        "a" * 64,
    )
    transport = _RecordingJudgeTransport(
        quotes={
            item.symbol: Quote(item.symbol, 75_000, 52_500, 97_500)
            for item in (first, second, third)
        },
        screening_batch=NewsScreeningBatch(
            (
                CandidateScreening(
                    "000001", "AVAILABLE", "VETO_BUY", 2_000, "VERIFIED", (veto_span,)
                ),
                CandidateScreening(
                    "000002", "AVAILABLE", "NO_VETO", 5_000, "VERIFIED", (surviving_span,)
                ),
                CandidateScreening("000003", "AVAILABLE", "NO_VETO", 5_000, "NO_EVIDENCE"),
            ),
            1,
            1,
        ),
        ai_judgement=AiJudgement(
            (
                AiCandidateVerdict(
                    "000002",
                    0.6,
                    False,
                    "supported",
                    ((surviving_span.citation_id, surviving_span.bounded_quote),),
                ),
                AiCandidateVerdict("000003", 0.5, False, "neutral"),
            ),
            "fixture",
        ),
    )
    store = _store()

    assert _drive(store, transport, _inputs(first, second, third)) == "COMPLETED"
    assert transport.judged_symbols == ("000002", "000003")
    assert store.runs[_RUN].selected_symbol == "000002"


def test_prompt_injection_abstains_one_candidate_and_batch_failure_buys_nothing() -> None:
    first = _candidate("000001", 0.05)
    second = _candidate("000002", 0.04)
    injection = NewsScreeningBatch(
        (
            CandidateScreening("000001", "ABSTAIN", "NO_VETO", 5_000, "PROMPT_INJECTION"),
            CandidateScreening("000002", "AVAILABLE", "NO_VETO", 5_000, "NO_EVIDENCE"),
        ),
        1,
        1,
    )
    transport = FixtureAutomationTransport(
        quotes={
            item.symbol: Quote(item.symbol, 75_000, 52_500, 97_500) for item in (first, second)
        },
        screening_batch=injection,
    )
    store = _store()
    assert _drive(store, transport, _inputs(first, second)) == "COMPLETED"
    assert store.runs[_RUN].selected_symbol == "000002"

    failed = FixtureAutomationTransport(
        quotes={
            item.symbol: Quote(item.symbol, 75_000, 52_500, 97_500) for item in (first, second)
        },
        screening_batch=NewsScreeningBatch((), 0, 0, failed=True),
    )
    failed_store = _store()
    assert _drive(failed_store, failed, _inputs(first, second)) == "SKIPPED_DATA_UNAVAILABLE"
    assert failed.submit_calls == 0


@pytest.mark.parametrize(
    ("temp_stop", "management", "liquidation", "expected"),
    [
        ("N", "00", "N", "COMPLETED"),
        ("Y", "00", "N", "SKIPPED_NO_ACTION"),
        ("N", "01", "N", "SKIPPED_NO_ACTION"),
        ("N", "00", "Y", "SKIPPED_NO_ACTION"),
        ("", "", "", "SKIPPED_NO_ACTION"),
        ("X", "UNKNOWN", "X", "SKIPPED_NO_ACTION"),
    ],
)
def test_hard_eligibility_requires_all_three_known_normal_codes(
    temp_stop: str,
    management: str,
    liquidation: str,
    expected: str,
) -> None:
    candidate = _candidate("000001", 0.05)
    transport = FixtureAutomationTransport(
        quotes={
            candidate.symbol: Quote(
                candidate.symbol,
                75_000,
                52_500,
                97_500,
                temp_stop_yn=temp_stop,
                management_issue_code=management,
                liquidation_trading_yn=liquidation,
            )
        }
    )
    store = _store()

    assert _drive(store, transport, _inputs(candidate)) == expected
    if expected != "COMPLETED":
        assert transport.screen_calls == 0
        assert transport.submit_calls == 0


def test_candidate_set_hash_seals_pre_eligibility_return_engine_set() -> None:
    first = _candidate("000001", 0.05)
    second = _candidate("000002", 0.04)
    transport = FixtureAutomationTransport(
        quotes={
            "000001": Quote("000001", 75_000, 52_500, 97_500, management_issue_code="UNKNOWN"),
            "000002": Quote("000002", 75_000, 52_500, 97_500),
        }
    )
    store = _store()

    assert _drive(store, transport, _inputs(first, second)) == "COMPLETED"
    assert store.runs[_RUN].selected_symbol == "000002"
    assert store.runs[_RUN].candidate_set_sha256 == _candidate_set_sha256((first, second))


def test_v3_skips_the_disclosure_veto_when_its_provider_is_not_bound() -> None:
    """거부권 provider 가 결속되지 않은 v3 세션은 vertex 를 부르지 않는다.

    결속되지 않으면 transport 가 fail-closed 라 판정이 ABSTAIN 으로 고정된다 - 부르면 호출
    하나를 버리는 것이고, 소유자가 AI 를 끈 세션에서도 provider 경로가 열린다.
    """

    candidate = _candidate("000001", 0.04)
    screening = NewsScreeningBatch(
        (CandidateScreening("000001", "AVAILABLE", "NO_VETO", 5_000, "NO_EVIDENCE"),), 1, 2
    )
    transport = FixtureAutomationTransport(
        quotes={"000001": Quote("000001", 75_000, 52_500, 97_500)},
        screening_batch=screening,
        news_verdict="VETO_BUY",
    )
    store = _store()

    assert _drive(store, transport, _inputs(candidate)) == "COMPLETED"
    assert transport.vertex_calls == 0


def test_v3_lets_the_disclosure_veto_stop_the_buy_when_its_provider_is_bound() -> None:
    """결속된 v3 세션은 공시 근거 거부권을 지나고 `VETO_BUY` 에서 닫힌다.

    v3 는 앞의 `NEWS_SCREENING` 에서 이미 후보 집합에 거부권을 행사하지만 그 판정은 Spring
    bridge 가 자체 grounding 으로 내리고 요청에 근거를 넣을 자리가 없다. `NEWS_CHECKING` 은
    등록 도메인 공시와 host 가 아는 접수일로 만든 인용을 쓰므로 다른 층이다. 두 층은 서로를
    대체하지 않는다.
    """

    candidate = _candidate("000001", 0.04)
    screening = NewsScreeningBatch(
        (CandidateScreening("000001", "AVAILABLE", "NO_VETO", 5_000, "NO_EVIDENCE"),), 1, 2
    )
    transport = FixtureAutomationTransport(
        quotes={"000001": Quote("000001", 75_000, 52_500, 97_500)},
        screening_batch=screening,
        news_verdict="VETO_BUY",
    )
    store = _store()
    inputs = replace(_inputs(candidate), news_veto_provider_bound=True)

    assert _drive(store, transport, inputs) == "NEWS_VETOED"
    assert transport.vertex_calls == 1
    assert transport.submit_calls == 0
    assert store.runs[_RUN].logical_submit_count == 0


def test_v3_passes_the_disclosure_veto_when_evidence_is_absent() -> None:
    """근거가 없어 ABSTAIN 이면 매수를 막지 않는다.

    실측: 2026-09-08 시점 유니버스 29종목의 최근 7일 구조화 공시가 0건이다. 즉 평시의
    정상 경로가 ABSTAIN 이고, 그것으로 매수를 막으면 자동운용이 평시에 멈춘다.
    """

    candidate = _candidate("000001", 0.04)
    screening = NewsScreeningBatch(
        (CandidateScreening("000001", "AVAILABLE", "NO_VETO", 5_000, "NO_EVIDENCE"),), 1, 2
    )
    transport = FixtureAutomationTransport(
        quotes={"000001": Quote("000001", 75_000, 52_500, 97_500)},
        screening_batch=screening,
        news_verdict="ABSTAIN",
    )
    store = _store()
    inputs = replace(_inputs(candidate), news_veto_provider_bound=True)

    assert _drive(store, transport, inputs) == "COMPLETED"
    assert transport.vertex_calls == 1
    assert transport.submit_calls == 1
