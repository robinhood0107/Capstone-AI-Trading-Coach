from __future__ import annotations

from datetime import date

import pytest

from app.data.calendar.errors import PrivacyProjectionError
from app.data.calendar.models import NormalizedCalendarEvent
from app.data.calendar.normalizer import EventCandidate, build_event_revision, event_candidate_from_normalized


def test_normalized_adapter_event_maps_to_repository_candidate_without_semantic_loss() -> None:
    normalized = NormalizedCalendarEvent(
        source_id="opendart-structured-events",
        origin_group="opendart",
        tier=1,
        source_event_key="20260722000001",
        stable_identity="00126380:piicDecsn:20260722000001",
        source_revision=None,
        event_type="DISCLOSURE",
        symbol="005930",
        exchange_mic="XKRX",
        event_date=date(2026, 7, 22),
        detail={"corp_code": "00126380", "endpoint_id": "piicDecsn"},
        operation="/api/piicDecsn.json",
    )

    candidate = event_candidate_from_normalized(normalized)

    assert candidate.source_id == normalized.source_id
    assert candidate.stable_identity == normalized.stable_identity
    assert candidate.exchange_mic == "XKRX"
    assert candidate.detail == normalized.detail


def test_normalized_event_is_privacy_scanned_before_becoming_a_candidate() -> None:
    normalized = NormalizedCalendarEvent(
        source_id="opendart-structured-events",
        origin_group="opendart",
        tier=1,
        source_event_key="20260722000001",
        stable_identity="00126380:majorstock:20260722000001",
        source_revision=None,
        event_type="DISCLOSURE",
        symbol="005930",
        exchange_mic="XKRX",
        event_date=date(2026, 7, 22),
        detail={"reporter_name": "홍길동"},
        operation="/api/majorstock.json",
    )

    with pytest.raises(PrivacyProjectionError, match="forbidden"):
        event_candidate_from_normalized(normalized)


def test_event_revision_rejects_raw_detail_before_hashing() -> None:
    candidate = EventCandidate(
        source_id="opendart-structured-events",
        source_event_key="20260722000001",
        stable_identity="00126380:majorstock:20260722000001",
        source_revision=None,
        event_type="DISCLOSURE",
        symbol="005930",
        exchange_mic="XKRX",
        event_date=date(2026, 7, 22),
        detail={"raw_response": {"reporter_name": "홍길동"}},
    )

    with pytest.raises(PrivacyProjectionError, match="forbidden"):
        build_event_revision(candidate)
