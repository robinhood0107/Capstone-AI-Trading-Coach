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
    DailyShardSink,
    SealedDirectoryReplay,
    load_packet,
    run_offline_daily,
)
from app.data.market_data.repository import stage_daily_shard


_WRITER_ROLE = "decision_market_writer"


class _PostgresDailySink(DailyShardSink):
    def __init__(self, database_dsn: str) -> None:
        self._database_dsn = database_dsn

    def preflight(self) -> None:
        """Verify the least-privilege writer before opening replay evidence."""

        with psycopg.connect(
            self._database_dsn, autocommit=True, connect_timeout=2
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT session_user, current_user")
                identity = cursor.fetchone()
        if identity != (_WRITER_ROLE, _WRITER_ROLE):
            raise RuntimeError("market-data daily CLI requires the exact writer role")

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
