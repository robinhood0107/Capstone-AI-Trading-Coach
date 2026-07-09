import logging
from datetime import date, timedelta

from app.data.opendart.models import DisclosureRiskEvent
from app.data.opendart.risk_mapping import load_default_risk_mapping
from app.data.opendart.scorer import score_disclosure_risk


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
