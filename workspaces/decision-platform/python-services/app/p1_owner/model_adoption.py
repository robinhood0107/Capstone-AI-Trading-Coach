"""연구 후보를 수동 production pointer 전환 전까지 단계적으로 제한한다."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
import re


class AdoptionState(StrEnum):
    RESEARCH_EVALUATED = "RESEARCH_EVALUATED"
    SHADOW_DAILY = "SHADOW_DAILY"
    ACCEPTANCE_REVIEWED = "ACCEPTANCE_REVIEWED"
    CURRENT_MODEL = "CURRENT_MODEL"


class AdoptionGateError(ValueError):
    """후보가 순서, 이중 합격, forward maturity 또는 수동 승인 gate를 넘지 못했다."""


@dataclass(frozen=True, slots=True)
class ShadowObservation:
    source_session: date
    target_session: date
    horizon_sessions: int
    answer_receipt_sha256: str

    def __post_init__(self) -> None:
        if (
            self.target_session <= self.source_session
            or self.horizon_sessions not in {1, 5, 20}
            or re.fullmatch(r"[0-9a-f]{64}", self.answer_receipt_sha256) is None
        ):
            raise AdoptionGateError("MODEL_ADOPTION_OBSERVATION_INVALID")


@dataclass(frozen=True, slots=True)
class ModelAdoption:
    candidate_id: str
    horizon_sessions: int
    prediction_accepted: bool
    performance_accepted: bool
    state: AdoptionState = AdoptionState.RESEARCH_EVALUATED
    observations: tuple[ShadowObservation, ...] = ()
    manual_reviewed: bool = False
    current_pointer: bool = False

    @property
    def shadow_sessions(self) -> int:
        return len({(item.source_session, item.target_session) for item in self.observations})

    @property
    def maturity_status(self) -> str:
        if (
            self.state == AdoptionState.SHADOW_DAILY
            and self.shadow_sessions < self.horizon_sessions
        ):
            return "SHADOW_DAILY_IMPLEMENTED_NOT_YET_MATURE"
        return "MATURE" if self.shadow_sessions >= self.horizon_sessions else "NOT_APPLICABLE"

    @property
    def blocker(self) -> str | None:
        if (
            self.state == AdoptionState.SHADOW_DAILY
            and self.shadow_sessions < self.horizon_sessions
        ):
            return "BLOCKED_FORWARD_EVIDENCE_NOT_MATURE"
        return None

    def start_shadow(self) -> ModelAdoption:
        if self.state != AdoptionState.RESEARCH_EVALUATED:
            raise AdoptionGateError("MODEL_ADOPTION_ORDER_INVALID")
        if not self.prediction_accepted or not self.performance_accepted:
            raise AdoptionGateError("MODEL_ADOPTION_DUAL_ACCEPTANCE_REQUIRED")
        return replace(self, state=AdoptionState.SHADOW_DAILY)

    def observe_shadow_session(self, observation: ShadowObservation) -> ModelAdoption:
        if self.state != AdoptionState.SHADOW_DAILY:
            raise AdoptionGateError("MODEL_ADOPTION_NOT_SHADOW")
        if observation.horizon_sessions != self.horizon_sessions:
            raise AdoptionGateError("MODEL_ADOPTION_HORIZON_MISMATCH")
        identity = (observation.source_session, observation.target_session)
        if any(
            (item.source_session, item.target_session) == identity for item in self.observations
        ):
            if observation in self.observations:
                return self
            raise AdoptionGateError("MODEL_ADOPTION_OBSERVATION_CONFLICT")
        return replace(self, observations=(*self.observations, observation))

    def review(self, *, approved: bool) -> ModelAdoption:
        if self.state != AdoptionState.SHADOW_DAILY or self.shadow_sessions < self.horizon_sessions:
            raise AdoptionGateError("BLOCKED_FORWARD_EVIDENCE_NOT_MATURE")
        if not approved:
            raise AdoptionGateError("MODEL_ADOPTION_REVIEW_REJECTED")
        return replace(self, state=AdoptionState.ACCEPTANCE_REVIEWED, manual_reviewed=True)

    def activate(self, *, manual_activation: bool) -> ModelAdoption:
        if self.state != AdoptionState.ACCEPTANCE_REVIEWED or not self.manual_reviewed:
            raise AdoptionGateError("MODEL_ADOPTION_REVIEW_REQUIRED")
        if not manual_activation:
            raise AdoptionGateError("MODEL_ADOPTION_AUTOMATIC_ACTIVATION_FORBIDDEN")
        return replace(self, state=AdoptionState.CURRENT_MODEL, current_pointer=True)
