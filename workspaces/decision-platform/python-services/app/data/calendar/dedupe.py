from __future__ import annotations

from collections.abc import Iterable

from app.data.calendar.models import CalendarObservation


def dedupe_observations(observations: Iterable[CalendarObservation]) -> list[CalendarObservation]:
    """같은 source/mapping/sanitized hash 복제는 confidence agreement로 세지 않고 한 건만 유지한다."""
    unique: dict[tuple[object, ...], CalendarObservation] = {}
    for observation in observations:
        key = (
            observation.source_id,
            observation.capability,
            observation.effective_from,
            observation.effective_to,
            observation.mapping_version,
            observation.sanitized_payload_hash,
        )
        existing = unique.get(key)
        if existing is None or observation.observation_id < existing.observation_id:
            # 동일 DB dedupe key의 ID가 달라도 입력 순서와 무관하게 하나를 선택한다.
            unique[key] = observation
    return sorted(unique.values(), key=lambda item: (item.source_id, item.observation_id))
