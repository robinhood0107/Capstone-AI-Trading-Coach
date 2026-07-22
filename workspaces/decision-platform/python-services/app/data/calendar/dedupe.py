from __future__ import annotations

from collections.abc import Iterable

from app.data.calendar.models import CalendarObservation


def dedupe_observations(observations: Iterable[CalendarObservation]) -> list[CalendarObservation]:
    """같은 source/mapping/sanitized hash 복제는 confidence agreement로 세지 않고 한 건만 유지한다."""
    unique: dict[tuple[str, str, str], CalendarObservation] = {}
    for observation in observations:
        key = (
            observation.source_id,
            observation.mapping_version,
            observation.sanitized_payload_hash,
        )
        unique.setdefault(key, observation)
    return sorted(unique.values(), key=lambda item: (item.source_id, item.observation_id))
