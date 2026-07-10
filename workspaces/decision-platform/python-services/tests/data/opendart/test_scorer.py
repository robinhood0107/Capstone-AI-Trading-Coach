import logging
from datetime import date, timedelta

import pytest

from app.data.opendart.models import DisclosureRiskEvent
from app.data.opendart.risk_mapping import load_default_risk_mapping
from app.data.opendart.scorer import MAX_EVENTS_PER_SCORE, score_disclosure_risk


AS_OF = date(2026, 7, 9)


def test_representative_five_symbols_have_deterministic_disclosure_risk_scores() -> None:
    mapping = load_default_risk_mapping()
    fixtures = {
        "005930": [_event("005930", "00126380", "OPENDART:piicDecsn", AS_OF - timedelta(days=3))],
        "000660": [_event("000660", "00164742", "OPENDART:cvbdIsDecsn", AS_OF - timedelta(days=5))],
        "005380": [_event("005380", "00164779", "OPENDART:lwstLg", AS_OF - timedelta(days=8))],
        "035420": [
            _event(
                "035420",
                "00266961",
                "OPENDART:accnutAdtorNmNdAdtOpinion",
                AS_OF - timedelta(days=6),
                attributes={"adt_opinion": "적정"},
            )
        ],
        "105560": [],
    }

    first = {
        symbol: score_disclosure_risk(symbol, events, as_of=AS_OF, mapping=mapping)
        for symbol, events in fixtures.items()
    }
    second = {
        symbol: score_disclosure_risk(symbol, events, as_of=AS_OF, mapping=mapping)
        for symbol, events in fixtures.items()
    }

    assert {symbol: result.score for symbol, result in first.items()} == {
        "005930": 0.6,
        "000660": 0.6,
        "005380": 0.4,
        "035420": 0.0,
        "105560": 0.0,
    }
    assert first == second


def test_high_severity_distress_events_score_deterministically() -> None:
    mapping = load_default_risk_mapping()
    cases = {
        "OPENDART:dfOcr": 1.0,
        "OPENDART:ctrcvsBgrq": 1.0,
        "OPENDART:dsRsOcr": 1.0,
        "OPENDART:bnkMngtPcbg": 1.0,
        "OPENDART:bsnSp": 0.8,
        "OPENDART:crDecsn": 0.8,
    }
    for event_code, expected in cases.items():
        events = [_event("900000", "00999999", event_code, AS_OF - timedelta(days=2))]
        first = score_disclosure_risk("900000", events, as_of=AS_OF, mapping=mapping)
        second = score_disclosure_risk("900000", events, as_of=AS_OF, mapping=mapping)

        assert first.score == expected
        assert first.events[0].event_code == event_code
        assert first == second


def test_distress_event_dominates_lower_severity_events_via_max_score() -> None:
    result = score_disclosure_risk(
        "900000",
        [
            _event("900000", "00999999", "OPENDART:lwstLg", AS_OF - timedelta(days=1)),
            _event("900000", "00999999", "OPENDART:dfOcr", AS_OF - timedelta(days=3)),
            _event("900000", "00999999", "OPENDART:crDecsn", AS_OF - timedelta(days=5)),
        ],
        as_of=AS_OF,
        mapping=load_default_risk_mapping(),
    )

    assert result.score == 1.0
    assert result.events[0].event_code == "OPENDART:dfOcr"


def test_persisting_distress_still_scores_beyond_thirty_days() -> None:
    # 상태 지속형: 부도/회생/비적정 감사의견은 31일이 지나도 여전히 위험 상태이므로 점수가 유지되어야 한다.
    mapping = load_default_risk_mapping()
    old = AS_OF - timedelta(days=200)

    bankruptcy = score_disclosure_risk(
        "900000",
        [_event("900000", "00999999", "OPENDART:dfOcr", old)],
        as_of=AS_OF,
        mapping=mapping,
    )
    audit = score_disclosure_risk(
        "105560",
        [_event("105560", "00999999", "OPENDART:accnutAdtorNmNdAdtOpinion", old, attributes={"adt_opinion": "의견거절"})],
        as_of=AS_OF,
        mapping=mapping,
    )

    assert bankruptcy.score == 1.0
    assert audit.score == 1.0


def test_announcement_effect_event_expires_after_its_short_window() -> None:
    # 공시효과형: 유상증자는 30일 유효기간이라 31일 전 이벤트는 window 밖으로 점수 0.
    mapping = load_default_risk_mapping()

    fresh = score_disclosure_risk(
        "005930",
        [_event("005930", "00126380", "OPENDART:piicDecsn", AS_OF - timedelta(days=29))],
        as_of=AS_OF,
        mapping=mapping,
    )
    stale = score_disclosure_risk(
        "005930",
        [_event("005930", "00126380", "OPENDART:piicDecsn", AS_OF - timedelta(days=31))],
        as_of=AS_OF,
        mapping=mapping,
    )

    assert fresh.score == 0.6
    assert stale.score == 0.0
    # 같은 31일 전이라도 상태 지속형은 살아있어 유형별 window가 실제로 다르게 동작함을 대비로 확인한다.
    distress_same_age = score_disclosure_risk(
        "005930",
        [_event("005930", "00126380", "OPENDART:bsnSp", AS_OF - timedelta(days=31))],
        as_of=AS_OF,
        mapping=mapping,
    )
    assert distress_same_age.score == 0.8


def test_s1_2b_events_score_deterministically() -> None:
    mapping = load_default_risk_mapping()
    codes = [
        "OPENDART:bdwtIsDecsn",
        "OPENDART:exbdIsDecsn",
        "OPENDART:cmpMgDecsn",
        "OPENDART:cmpDvDecsn",
        "OPENDART:cmpDvmgDecsn",
        "OPENDART:bsnTrfDecsn",
    ]
    for code in codes:
        events = [_event("900000", "00999999", code, AS_OF - timedelta(days=2))]
        first = score_disclosure_risk("900000", events, as_of=AS_OF, mapping=mapping)
        second = score_disclosure_risk("900000", events, as_of=AS_OF, mapping=mapping)

        assert first.score == 0.6
        assert first.events[0].event_code == code
        assert first == second


def test_s1_2b_bw_eb_expire_after_thirty_days() -> None:
    # BW/EB는 공시효과형이라 30일 window. 29일은 살아있고 31일은 0.
    mapping = load_default_risk_mapping()
    for code in ("OPENDART:bdwtIsDecsn", "OPENDART:exbdIsDecsn"):
        fresh = score_disclosure_risk(
            "900000", [_event("900000", "00999999", code, AS_OF - timedelta(days=29))], as_of=AS_OF, mapping=mapping
        )
        stale = score_disclosure_risk(
            "900000", [_event("900000", "00999999", code, AS_OF - timedelta(days=31))], as_of=AS_OF, mapping=mapping
        )
        assert fresh.score == 0.6
        assert stale.score == 0.0


def test_s1_2b_reorg_events_persist_within_ninety_days() -> None:
    # 합병·분할·분할합병·영업양도는 reorg라 90일 window. 90일은 살아있고 91일은 0.
    mapping = load_default_risk_mapping()
    for code in ("OPENDART:cmpMgDecsn", "OPENDART:cmpDvDecsn", "OPENDART:cmpDvmgDecsn", "OPENDART:bsnTrfDecsn"):
        within = score_disclosure_risk(
            "900000", [_event("900000", "00999999", code, AS_OF - timedelta(days=90))], as_of=AS_OF, mapping=mapping
        )
        beyond = score_disclosure_risk(
            "900000", [_event("900000", "00999999", code, AS_OF - timedelta(days=91))], as_of=AS_OF, mapping=mapping
        )
        assert within.score == 0.6
        assert beyond.score == 0.0


def test_s1_2b_event_does_not_override_higher_distress_max_score() -> None:
    # 복수 이벤트 max score 규칙 유지: 합병(0.6)과 부도(1.0)가 함께면 1.0.
    result = score_disclosure_risk(
        "900000",
        [
            _event("900000", "00999999", "OPENDART:cmpMgDecsn", AS_OF - timedelta(days=3)),
            _event("900000", "00999999", "OPENDART:dfOcr", AS_OF - timedelta(days=5)),
        ],
        as_of=AS_OF,
        mapping=load_default_risk_mapping(),
    )

    assert result.score == 1.0
    assert result.events[0].event_code == "OPENDART:dfOcr"


def test_unknown_code_is_zero_score_with_structured_warning_even_if_title_looks_risky() -> None:
    result = score_disclosure_risk(
        "005930",
        [
            _event(
                "005930",
                "00126380",
                "OPENDART:UNKNOWN",
                AS_OF,
                attributes={"report_nm": "관리종목 지정 및 상장폐지 관련"},
            )
        ],
        as_of=AS_OF,
        mapping=load_default_risk_mapping(),
    )

    assert result.score == 0.0
    assert result.events == []
    assert result.warnings[0].code == "UNMAPPED_DISCLOSURE_RISK_CODE"
    assert result.warnings[0].event_code == "OPENDART:UNKNOWN"


def test_unknown_code_logs_warning_for_batch_observability(caplog) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level(logging.WARNING, logger="app.data.opendart.scorer")

    score_disclosure_risk(
        "005930",
        [_event("005930", "00126380", "OPENDART:UNKNOWN", AS_OF)],
        as_of=AS_OF,
        mapping=load_default_risk_mapping(),
    )

    assert [(record.message, record.event_code) for record in caplog.records] == [
        ("unmapped_disclosure_risk_code", "OPENDART:UNKNOWN")
    ]


def test_events_outside_thirty_day_window_are_excluded() -> None:
    result = score_disclosure_risk(
        "005930",
        [_event("005930", "00126380", "OPENDART:piicDecsn", AS_OF - timedelta(days=31))],
        as_of=AS_OF,
        mapping=load_default_risk_mapping(),
    )

    assert result.score == 0.0
    assert result.events == []


def test_multiple_events_use_max_score() -> None:
    result = score_disclosure_risk(
        "005930",
        [
            _event("005930", "00126380", "OPENDART:lwstLg", AS_OF - timedelta(days=1)),
            _event("005930", "00126380", "OPENDART:piicDecsn", AS_OF - timedelta(days=2)),
        ],
        as_of=AS_OF,
        mapping=load_default_risk_mapping(),
    )

    assert result.score == 0.6
    assert [event.event_code for event in result.events] == ["OPENDART:piicDecsn", "OPENDART:lwstLg"]


def test_audit_opinion_score_requires_structured_non_clean_opinion() -> None:
    mapping = load_default_risk_mapping()

    adverse = score_disclosure_risk(
        "105560",
        [
            _event(
                "105560",
                "00999999",
                "OPENDART:accnutAdtorNmNdAdtOpinion",
                AS_OF,
                attributes={"adt_opinion": "한정"},
            )
        ],
        as_of=AS_OF,
        mapping=mapping,
    )
    clean = score_disclosure_risk(
        "105560",
        [
            _event(
                "105560",
                "00999999",
                "OPENDART:accnutAdtorNmNdAdtOpinion",
                AS_OF,
                attributes={"adt_opinion": "적정"},
            )
        ],
        as_of=AS_OF,
        mapping=mapping,
    )

    assert adverse.score == 1.0
    assert clean.score == 0.0


def test_missing_required_condition_emits_fail_closed_warning() -> None:
    result = score_disclosure_risk(
        "105560",
        [_event("105560", "00999999", "OPENDART:accnutAdtorNmNdAdtOpinion", AS_OF)],
        as_of=AS_OF,
        mapping=load_default_risk_mapping(),
    )

    assert result.score == 0.0
    assert result.events == []
    assert [warning.code for warning in result.warnings] == ["INVALID_DISCLOSURE_RISK_CONDITION"]


def test_scorer_rejects_event_collection_over_safety_limit() -> None:
    event = _event("005930", "00126380", "OPENDART:piicDecsn", AS_OF)

    with pytest.raises(ValueError, match="event limit"):
        score_disclosure_risk(
            "005930",
            [event] * (MAX_EVENTS_PER_SCORE + 1),
            as_of=AS_OF,
            mapping=load_default_risk_mapping(),
        )


def test_scorer_rejects_invalid_default_window() -> None:
    with pytest.raises(ValueError, match="window_days"):
        score_disclosure_risk("005930", [], as_of=AS_OF, window_days=0)


def _event(
    symbol: str,
    corp_code: str,
    event_code: str,
    occurred_on: date,
    *,
    attributes: dict[str, str] | None = None,
) -> DisclosureRiskEvent:
    return DisclosureRiskEvent(
        symbol=symbol,
        corp_code=corp_code,
        event_code=event_code,
        receipt_no=f"{occurred_on:%Y%m%d}000001",
        occurred_on=occurred_on,
        attributes=attributes or {},
    )
