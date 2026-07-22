from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Protocol, TypeGuard

from app.data.calendar.errors import StateTransitionError
from app.data.calendar.normalizer import canonical_hash

StateAction = Literal["OPEN", "CLOSE"]
_ENDPOINT_ACTIONS: dict[str, StateAction] = {
    "bnkMngtPcbg": "OPEN",
    "bnkMngtPcsp": "CLOSE",
}


@dataclass(frozen=True)
class DisclosureStateEvent:
    """approved structured OpenDART endpoint에서만 만들어지는 상태 전이 후보이다."""

    endpoint_id: str
    corp_code: str
    state_type: str
    state_key: str
    transition: str
    source_event_key: str
    source_revision: str | None
    effective_on: date
    observed_at: datetime
    canonical_event_id: str
    mapping_version: str


@dataclass(frozen=True)
class DisclosureStateTransition:
    """과거 row를 수정하지 않고 source correction과 open/close를 append하는 transition이다."""

    transition_id: str
    corp_code: str
    state_type: str
    state_key: str
    transition: StateAction
    revision_no: int
    revised_from_transition_id: str | None
    source_id: str
    source_event_key: str
    source_revision: str | None
    effective_on: date
    observed_at: datetime
    canonical_event_id: str
    mapping_version: str


class ActiveStateRepository(Protocol):
    def load_active_states(self, corp_code: str) -> list[object]: ...


class DisclosureStateMachine:
    """BANK_MANAGEMENT의 duplicate/correction/open/close 규칙을 순수 append-only 상태로 적용한다."""

    def __init__(self) -> None:
        self._transitions: list[DisclosureStateTransition] = []

    @property
    def transitions(self) -> list[DisclosureStateTransition]:
        """caller가 내부 list를 수정하지 못하도록 immutable row의 복사본만 반환한다."""
        return list(self._transitions)

    def apply(self, event: DisclosureStateEvent) -> DisclosureStateTransition:
        """duplicate는 기존 row를 반환하고 correction/open/close만 monotonic revision으로 append한다."""
        action = _validated_action(event)
        exact = next(
            (
                item
                for item in self._transitions
                if item.source_event_key == event.source_event_key
                and item.source_revision == event.source_revision
                and item.transition == action
            ),
            None,
        )
        if exact is not None:
            return exact
        corrected = next(
            (item for item in reversed(self._transitions) if item.source_event_key == event.source_event_key),
            None,
        )
        current = active_state(self._transitions, state_key=event.state_key)
        latest = _latest_transition(self._transitions, state_key=event.state_key)
        if corrected is None:
            if action == "OPEN" and current is not None:
                return current
            if action == "CLOSE" and current is None:
                if latest is not None and latest.transition == "CLOSE":
                    return latest
                raise StateTransitionError("CLOSE_BEFORE_OPEN")
        revision_no = 1 if latest is None else latest.revision_no + 1
        transition_id = canonical_hash(
            {
                "state_key": event.state_key,
                "transition": action,
                "revision_no": revision_no,
                "source_event_key": event.source_event_key,
                "source_revision": event.source_revision,
                "effective_on": event.effective_on,
                "canonical_event_id": event.canonical_event_id,
                "mapping_version": event.mapping_version,
            }
        )
        transition = DisclosureStateTransition(
            transition_id=transition_id,
            corp_code=event.corp_code,
            state_type=event.state_type,
            state_key=event.state_key,
            transition=action,
            revision_no=revision_no,
            revised_from_transition_id=None if corrected is None else corrected.transition_id,
            source_id="opendart-structured-events",
            source_event_key=event.source_event_key,
            source_revision=event.source_revision,
            effective_on=event.effective_on,
            observed_at=event.observed_at,
            canonical_event_id=event.canonical_event_id,
            mapping_version=event.mapping_version,
        )
        self._transitions.append(transition)
        return transition


class DisclosureStateScorer:
    """주문 판단 시 provider를 호출하지 않고 active-state repository 결과만 0/1로 읽는다."""

    def __init__(self, repository: ActiveStateRepository) -> None:
        self._repository = repository

    def score(self, corp_code: str) -> int:
        """BANK_MANAGEMENT active row가 있으면 1, close 뒤에는 0을 반환한다."""
        return 1 if self._repository.load_active_states(corp_code) else 0


def active_state(
    transitions: Sequence[object],
    *,
    state_key: str | None = None,
) -> DisclosureStateTransition | None:
    """monotonic revision이 가장 큰 valid transition이 OPEN일 때만 active로 반환한다."""
    typed = [item for item in transitions if _is_transition(item)]
    if state_key is not None:
        typed = [item for item in typed if item.state_key == state_key]
    latest = max(
        typed,
        key=lambda item: (item.revision_no, item.observed_at, item.transition_id),
        default=None,
    )
    return latest if latest is not None and latest.transition == "OPEN" else None


def _latest_transition(
    transitions: Sequence[DisclosureStateTransition],
    *,
    state_key: str,
) -> DisclosureStateTransition | None:
    candidates = [item for item in transitions if item.state_key == state_key]
    return max(
        candidates,
        key=lambda item: (item.revision_no, item.observed_at, item.transition_id),
        default=None,
    )


def _validated_action(event: DisclosureStateEvent) -> StateAction:
    expected = _ENDPOINT_ACTIONS.get(event.endpoint_id)
    if expected is None or event.transition != expected:
        raise StateTransitionError("UNSUPPORTED_STRUCTURED_ENDPOINT")
    if event.state_type != "BANK_MANAGEMENT":
        raise StateTransitionError("UNSUPPORTED_STATE_TYPE")
    if event.state_key != f"{event.corp_code}:BANK_MANAGEMENT":
        raise StateTransitionError("STATE_KEY_MISMATCH")
    if len(event.corp_code) != 8 or not event.corp_code.isascii() or not event.corp_code.isdigit():
        raise StateTransitionError("CORP_CODE_INVALID")
    return expected


def _is_transition(value: object) -> TypeGuard[DisclosureStateTransition]:
    return isinstance(value, DisclosureStateTransition)
