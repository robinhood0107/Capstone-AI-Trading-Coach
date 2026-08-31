"""Bounded live-read-only transport for the existing manifest-last daily coordinator.

Provider-specific clients normalize their payloads before this boundary.  This
transport adds exact operation identity, one-attempt accounting, and a default-
off activation gate.  It has no account, balance, or order interface.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Protocol

from app.data._shared.canonical_json import canonical_json_sha256
from app.data.market_data.daily_runtime import DailyReplayPacket, ReplayRecord, operation_ids
from app.p1_owner.data_only_collector import P1DailyCollectorError


class SanitizedDailyPayloadSource(Protocol):
    """Return one already-sanitized payload for one approved operation."""

    def read(self, operation_id: str, packet: DailyReplayPacket) -> dict[str, object]: ...


class LiveDailyCollectionTransport:
    """Execute each exact operation at most once, with zero retry and no fallback."""

    def __init__(
        self,
        *,
        packet: DailyReplayPacket,
        source: SanitizedDailyPayloadSource,
        retrieved_at: datetime,
        enabled: bool,
    ) -> None:
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise P1DailyCollectorError("live collector clock must be timezone aware")
        self._packet = packet
        self._source = source
        self._retrieved_at = retrieved_at
        self._enabled = enabled
        self._allowed = frozenset(operation_ids(packet))
        self._attempted: set[str] = set()
        self.logical_calls = 0
        self.physical_calls = 0

    @classmethod
    def from_environment(
        cls,
        *,
        packet: DailyReplayPacket,
        source: SanitizedDailyPayloadSource,
        retrieved_at: datetime,
    ) -> LiveDailyCollectionTransport:
        enabled = os.environ.get("P1_DATA_ONLY_COLLECTOR_ENABLED", "false").lower() == "true"
        return cls(packet=packet, source=source, retrieved_at=retrieved_at, enabled=enabled)

    def collect(self, operation_id: str) -> ReplayRecord:
        self.logical_calls += 1
        if not self._enabled:
            raise P1DailyCollectorError("live data-only collector is disabled")
        if operation_id not in self._allowed or operation_id in self._attempted:
            raise P1DailyCollectorError("live data-only operation is invalid or duplicated")
        if self.physical_calls >= len(self._allowed):
            raise P1DailyCollectorError("live data-only physical cap is exhausted")
        self._attempted.add(operation_id)
        self.physical_calls += 1
        payload = self._source.read(operation_id, self._packet)
        if not isinstance(payload, dict):
            raise P1DailyCollectorError("live data-only source payload is invalid")
        query_sha = canonical_json_sha256(
            {
                "operationId": operation_id,
                "sessionDate": self._packet.session_date.isoformat(),
            }
        )
        return ReplayRecord(
            source_id=operation_id.split("_", maxsplit=1)[0],
            operation_id=operation_id,
            query_sha256=query_sha,
            content_sha256=canonical_json_sha256(payload),
            retrieved_at=self._retrieved_at,
            payload=payload,
        )
