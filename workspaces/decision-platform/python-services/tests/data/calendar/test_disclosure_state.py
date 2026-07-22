from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.data.calendar.disclosure_state import (
    DisclosureStateEvent,
    DisclosureStateMachine,
    DisclosureStateScorer,
    active_state,
)
from app.data.calendar.errors import StateTransitionError


def test_start_duplicate_stop_duplicate_and_closed_score_zero() -> None:
    machine = DisclosureStateMachine()
    opened = _event("bnkMngtPcbg", "OPEN", "receipt-open", source_revision="1")
    closed = _event("bnkMngtPcsp", "CLOSE", "receipt-close", source_revision="1")

    machine.apply(opened)
    machine.apply(opened)
    assert len(machine.transitions) == 1
    assert active_state(machine.transitions) is not None

    machine.apply(closed)
    machine.apply(closed)
    assert len(machine.transitions) == 2
    assert active_state(machine.transitions) is None
    assert DisclosureStateScorer(_Repository(machine.transitions)).score("00126380") == 0


def test_stop_before_start_is_stable_validation_error() -> None:
    machine = DisclosureStateMachine()
    with pytest.raises(StateTransitionError) as exc_info:
        machine.apply(_event("bnkMngtPcsp", "CLOSE", "receipt-close", source_revision="1"))
    assert exc_info.value.code == "CLOSE_BEFORE_OPEN"
    assert "receipt" not in str(exc_info.value)


def test_correction_appends_revision_without_mutating_previous_transition() -> None:
    machine = DisclosureStateMachine()
    first = machine.apply(_event("bnkMngtPcbg", "OPEN", "receipt-open", source_revision="1"))
    corrected = machine.apply(
        _event(
            "bnkMngtPcbg",
            "OPEN",
            "receipt-open",
            source_revision="2",
            effective_on=date(2026, 7, 21),
        )
    )

    assert first.revision_no == 1
    assert corrected.revision_no == 2
    assert corrected.revised_from_transition_id == first.transition_id
    assert machine.transitions[0] == first
    assert active_state(machine.transitions) == corrected


def test_correction_of_an_open_transition_after_close_cannot_reopen_state() -> None:
    machine = DisclosureStateMachine()
    machine.apply(_event("bnkMngtPcbg", "OPEN", "receipt-open", source_revision="1"))
    machine.apply(_event("bnkMngtPcsp", "CLOSE", "receipt-close", source_revision="1"))

    with pytest.raises(StateTransitionError) as exc_info:
        machine.apply(
            _event(
                "bnkMngtPcbg",
                "OPEN",
                "receipt-open",
                source_revision="2",
                effective_on=date(2026, 7, 21),
            )
        )

    assert exc_info.value.code == "OPEN_CORRECTION_AFTER_CLOSE"
    assert active_state(machine.transitions) is None


def test_scorer_reads_active_state_repository_without_provider_http() -> None:
    provider_calls = 0

    class Repository:
        def load_active_states(self, corp_code: str) -> list[object]:
            assert corp_code == "00126380"
            return [object()]

    def provider() -> None:
        nonlocal provider_calls
        provider_calls += 1

    scorer = DisclosureStateScorer(Repository())
    assert scorer.score("00126380") == 1
    assert provider_calls == 0
    assert provider is not None


def _event(
    endpoint_id: str,
    transition: str,
    source_event_key: str,
    *,
    source_revision: str,
    effective_on: date = date(2026, 7, 22),
) -> DisclosureStateEvent:
    return DisclosureStateEvent(
        endpoint_id=endpoint_id,
        corp_code="00126380",
        state_type="BANK_MANAGEMENT",
        state_key="00126380:BANK_MANAGEMENT",
        transition=transition,
        source_event_key=source_event_key,
        source_revision=source_revision,
        effective_on=effective_on,
        observed_at=datetime(2026, 7, 22, 1, 0, tzinfo=UTC),
        canonical_event_id=f"event-{source_event_key}",
        mapping_version="s1.6-v1",
    )


class _Repository:
    def __init__(self, transitions: list[object]) -> None:
        self._transitions = transitions

    def load_active_states(self, _: str) -> list[object]:
        state = active_state(self._transitions)
        return [] if state is None else [state]
