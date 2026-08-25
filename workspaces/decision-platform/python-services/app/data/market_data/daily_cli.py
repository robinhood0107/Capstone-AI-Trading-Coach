"""Manual S5.7C sealed-replay CLI with no live provider adapter."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, cast

import psycopg

from app.data.market_data.daily_runtime import (
    AcceptedDailyShard,
    DailyReplayPacket,
    DailyShardSink,
    SealedDirectoryReplay,
    load_packet,
    run_offline_daily,
)
from app.data.market_data.repository import (
    ConnectionLike,
    require_previous_accepted_head,
    stage_daily_shard,
)


class _PostgresDailySink(DailyShardSink):
    def __init__(self, database_dsn: str) -> None:
        self._database_dsn = database_dsn

    def preflight(self, packet: DailyReplayPacket) -> None:
        """Verify the least-privilege writer before opening replay evidence."""

        with psycopg.connect(self._database_dsn, autocommit=False, connect_timeout=2) as connection:
            probe = AcceptedDailyShard(
                payload={
                    "manifestSha256": "0" * 64,
                    "previousAcceptedManifestSha256": packet.previous_accepted_manifest_sha256,
                    "sessionDate": packet.session_date.isoformat(),
                },
                universe_rows=(),
            )
            head = require_previous_accepted_head(
                connection=cast(ConnectionLike, connection), accepted=probe
            )
            if head.session_date != packet.previous_session_date:
                raise RuntimeError(
                    "NEEDS_HUMAN: previous accepted market-data session is not the packet predecessor"
                )
            connection.rollback()

    def adopt(self, accepted: AcceptedDailyShard) -> str:
        result = stage_daily_shard(
            database_dsn=self._database_dsn,
            accepted=accepted,
            expected_manifest_sha256=accepted.manifest_sha256,
        )
        return result.outcome


def main(argv: list[str] | None = None) -> int:
    """Run one explicit offline replay packet; network provider authority is absent."""

    parser = argparse.ArgumentParser(description="replay one sealed S5.7C market-data session")
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args(argv)
    database_dsn = os.environ.get("MARKET_DATA_WRITER_DSN")
    if not database_dsn:
        parser.error("MARKET_DATA_WRITER_DSN is required")

    packet = load_packet(args.packet)
    sink = _PostgresDailySink(database_dsn)
    result = run_offline_daily(
        packet=packet,
        run_root=args.run_root,
        replay_factory=lambda: SealedDirectoryReplay(args.replay_root),
        sink=sink,
    )
    output: dict[str, Any] = {
        "providerPhysicalCalls": result.provider_physical_calls,
        "replayReads": result.replay_reads,
        "status": result.status,
    }
    if result.accepted is not None:
        output["manifestSha256"] = result.accepted.manifest_sha256
    print(json.dumps(cast(object, output), separators=(",", ":"), sort_keys=True))
    return 0 if result.status in {"ACCEPTED", "NO_NEW_SESSION", "WAITING_FOR_EVIDENCE_CLOCK"} else 1
