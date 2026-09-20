from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.p1_owner.model_adoption import (
    AdoptionGateError,
    AdoptionState,
    ModelAdoption,
    ShadowObservation,
)


def test_candidate_must_pass_both_axes_before_shadow() -> None:
    for prediction, performance in ((True, False), (False, True), (False, False)):
        with pytest.raises(AdoptionGateError, match="DUAL_ACCEPTANCE"):
            ModelAdoption("candidate", 1, prediction, performance).start_shadow()


def test_twenty_session_candidate_stays_forward_blocked_until_mature() -> None:
    candidate = ModelAdoption("candidate-h20", 20, True, True).start_shadow()
    for offset in range(19):
        source = date(2026, 1, 2) + timedelta(days=offset)
        candidate = candidate.observe_shadow_session(
            ShadowObservation(source, source + timedelta(days=28), 20, f"{offset + 1:064x}")
        )
    assert candidate.state == AdoptionState.SHADOW_DAILY
    assert candidate.maturity_status == "SHADOW_DAILY_IMPLEMENTED_NOT_YET_MATURE"
    assert candidate.blocker == "BLOCKED_FORWARD_EVIDENCE_NOT_MATURE"
    with pytest.raises(AdoptionGateError, match="FORWARD_EVIDENCE"):
        candidate.review(approved=True)


def test_activation_is_manual_and_ordered() -> None:
    candidate = (
        ModelAdoption("candidate-h1", 1, True, True)
        .start_shadow()
        .observe_shadow_session(ShadowObservation(date(2026, 9, 7), date(2026, 9, 8), 1, "a" * 64))
    )
    reviewed = candidate.review(approved=True)
    with pytest.raises(AdoptionGateError, match="AUTOMATIC_ACTIVATION"):
        reviewed.activate(manual_activation=False)
    current = reviewed.activate(manual_activation=True)
    assert current.state == AdoptionState.CURRENT_MODEL
    assert current.current_pointer is True


def test_duplicate_shadow_receipt_is_noop_and_conflicting_answer_is_rejected() -> None:
    candidate = ModelAdoption("candidate-h1", 1, True, True).start_shadow()
    observation = ShadowObservation(date(2026, 9, 7), date(2026, 9, 8), 1, "a" * 64)
    observed = candidate.observe_shadow_session(observation)
    assert observed.observe_shadow_session(observation).shadow_sessions == 1
    with pytest.raises(AdoptionGateError, match="OBSERVATION_CONFLICT"):
        observed.observe_shadow_session(
            ShadowObservation(date(2026, 9, 7), date(2026, 9, 8), 1, "b" * 64)
        )
