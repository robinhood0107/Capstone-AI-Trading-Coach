from __future__ import annotations

from datetime import date

from app.data.calendar.normalizer import EventCandidate, build_event_revision, event_series_key


def test_event_series_key_excludes_correctable_event_date() -> None:
    first = event_series_key("kis-ksd-dividend", "005930:CASH:COMMON:DIVIDEND_PAY")
    second = event_series_key("kis-ksd-dividend", "005930:CASH:COMMON:DIVIDEND_PAY")
    assert first == second


def test_same_sanitized_payload_is_idempotent_and_correction_appends_revision() -> None:
    initial = build_event_revision(
        EventCandidate(
            source_id="kis-ksd-dividend-hhkdb669102c0",
            source_event_key="005930:CASH:COMMON:PAY",
            stable_identity="005930:CASH:COMMON:DIVIDEND_PAY",
            source_revision=None,
            event_type="DIVIDEND_PAY",
            symbol="005930",
            event_date=date(2026, 4, 20),
            detail={"kind": "CASH"},
        )
    )
    duplicate = build_event_revision(initial.candidate, previous=initial)
    corrected = build_event_revision(
        EventCandidate(
            **{
                **initial.candidate.as_dict(),
                "event_date": date(2026, 4, 21),
                "source_revision": "2",
            }
        ),
        previous=initial,
    )

    assert duplicate is initial
    assert corrected.event_series_key == initial.event_series_key
    assert corrected.revision_no == 2
    assert corrected.revised_from_event_id == initial.event_id
    assert corrected.canonical_hash != initial.canonical_hash
