from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Protocol

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.gdelt.errors import GdeltAggregateError
from app.data.gdelt.parser import parse_aggregate_modes
from app.data.gdelt.policy import QueryDefinition
from app.data.gdelt.transport import (
    ALLOWED_MODES,
    FixtureResponse,
    validate_fixture_response,
)

_ATTRIBUTION = {
    "provider": "GDELT",
    "citation": "The GDELT Project",
    "projectUrl": "https://www.gdeltproject.org/",
    "termsUrl": "https://www.gdeltproject.org/about.html",
}


class AggregateFixtureTransport(Protocol):
    physical_attempt_count: int

    def fetch(self, mode: str) -> FixtureResponse: ...


class GdeltCollector:
    """allowlisted synthetic aggregate를 AVAILABLE 또는 ABSTAIN observation으로 변환한다.

    external source는 future GDELT DOC API지만 현재 transport 계약은 physical call이 항상 0인
    fixture만 허용하며 raw response와 기사 metadata는 결과에 포함하지 않는다.
    """

    def __init__(self, *, transport: AggregateFixtureTransport) -> None:
        if transport.physical_attempt_count != 0:
            raise GdeltAggregateError("PROVIDER_DISABLED", "fixture transport attempted network")
        self._transport = transport

    def collect(
        self,
        *,
        query: QueryDefinition,
        window_start: datetime,
        window_end: datetime,
        observed_at: datetime,
        received_at: datetime,
        available_at: datetime,
    ) -> dict[str, object]:
        """bounded query definition과 deterministic 시각을 canonical observation으로 만든다."""

        times = tuple(
            _utc(value)
            for value in (window_start, window_end, observed_at, received_at, available_at)
        )
        start, end, observed, received, available = times
        if not start < end <= observed <= received <= available:
            raise GdeltAggregateError("INVALID_RESPONSE", "observation times are invalid")
        base = _observation_base(
            query=query,
            window_start=start,
            window_end=end,
            observed_at=observed,
            received_at=received,
            available_at=available,
        )
        try:
            tone = validate_fixture_response(self._transport.fetch(ALLOWED_MODES[0]))
            volume = validate_fixture_response(self._transport.fetch(ALLOWED_MODES[1]))
            points = parse_aggregate_modes(
                tone_bytes=tone,
                volume_bytes=volume,
                window_start=start,
                window_end=end,
            )
        except GdeltAggregateError as error:
            completeness = {
                "EMPTY_WINDOW": "EMPTY",
                "INCOMPLETE_SOURCE": "PARTIAL",
                "PROVIDER_DISABLED": "UNAVAILABLE",
            }.get(error.code, "MALFORMED")
            reason = (
                error.code
                if error.code
                in {
                    "EMPTY_WINDOW",
                    "INCOMPLETE_SOURCE",
                    "MAPPING_AMBIGUOUS",
                    "NORM_ZERO",
                    "PROVIDER_DISABLED",
                }
                else "INVALID_RESPONSE"
            )
            return _with_artifact_hash(
                {
                    **base,
                    "status": "ABSTAIN",
                    "sourceCompleteness": completeness,
                    "abstainReason": reason,
                }
            )
        if self._transport.physical_attempt_count != 0:
            raise GdeltAggregateError("PROVIDER_DISABLED", "fixture transport attempted network")
        return _with_artifact_hash(
            {
                **base,
                "status": "AVAILABLE",
                "sourceCompleteness": "COMPLETE",
                "points": points,
            }
        )


def _observation_base(
    *,
    query: QueryDefinition,
    window_start: datetime,
    window_end: datetime,
    observed_at: datetime,
    received_at: datetime,
    available_at: datetime,
) -> dict[str, object]:
    suffix = window_end.strftime("%Y%m%d")
    return {
        "schemaVersion": "1",
        "observationId": f"gdelt_obs_{query.query_registry_id.removesuffix('_v1')}_{suffix}",
        "queryRegistryId": query.query_registry_id,
        "decisionAuthority": "NONE",
        "collectionMode": "OFFLINE_FIXTURE",
        "physicalAttemptCount": 0,
        "approvalPacketHash": None,
        "windowStart": _format_utc(window_start),
        "windowEnd": _format_utc(window_end),
        "observedAt": _format_utc(observed_at),
        "receivedAt": _format_utc(received_at),
        "availableAt": _format_utc(available_at),
        "modes": list(ALLOWED_MODES),
        "rawProviderDataStored": False,
        "articleMetadataStored": False,
        "attribution": dict(_ATTRIBUTION),
    }


def _with_artifact_hash(value: dict[str, object]) -> dict[str, object]:
    identity = dict(value)
    digest = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    return {**identity, "artifactHash": digest}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise GdeltAggregateError("INVALID_RESPONSE", "observation time must be timezone aware")
    return value.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
