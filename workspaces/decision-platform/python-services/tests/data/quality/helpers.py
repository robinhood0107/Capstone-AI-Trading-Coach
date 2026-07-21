from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import os
from pathlib import Path
from uuid import UUID

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.kis.accounting import CollectionRunRecorder, CollectionRunStatus
from app.data.kis.parsers import DailyBar
from app.data.kis.run_artifacts import (
    build_dataset_manifest,
    inventory_daily_dataset,
    publish_collection_summary,
    publish_successful_dataset_manifest,
    reference_input_artifact,
)
from app.data.kis.storage import upsert_daily_bars


RUN_ID = UUID("123e4567-e89b-42d3-a456-426614174000")
STARTED_AT = datetime(2026, 7, 21, 1, 0, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 7, 21, 7, 0, tzinfo=UTC)


@dataclass(frozen=True)
class SnapshotIdentifiers:
    universe: str
    dataset: str
    collection: str


def prepare_snapshot(
    root: Path,
    *,
    universe_sessions: tuple[date, ...] = (date(2026, 7, 21),),
    data_sessions: tuple[date, ...] | None = None,
) -> SnapshotIdentifiers:
    sessions = data_sessions if data_sessions is not None else universe_sessions
    universe_identifier = "universe_manifest.json"
    universe_path = root / universe_identifier
    universe_path.parent.mkdir(parents=True, exist_ok=True)
    universe_path.write_bytes(
        canonical_json_bytes(
            {
                "schemaVersion": 1,
                "generatedAt": "2026-07-21T00:00:00Z",
                "asOfDate": "2026-07-21",
                "source": "offline-fixture",
                "sourceSha256": "d" * 64,
                "rankingRule": "symbol asc",
                "limit": 1,
                "symbols": [
                    {
                        "rank": 1,
                        "symbol": "005930",
                        "name": "Offline Fixture",
                        "market": "KOSPI",
                        "marketCap": 1,
                        "tradingValue": 1,
                    }
                ],
            }
        )
    )
    os.chmod(universe_path, 0o600)
    upsert_daily_bars(
        root,
        "005930",
        [
            DailyBar(
                "005930",
                session,
                100 + index,
                105 + index,
                95 + index,
                101 + index,
                1000 + index,
                100_000 + index,
            )
            for index, session in enumerate(sessions)
        ],
    )
    summary = publish_collection_summary(
        root,
        CollectionRunRecorder(run_id=RUN_ID, started_at=STARTED_AT).snapshot(
            completed_at=COMPLETED_AT,
            status=CollectionRunStatus.SUCCESS,
        ),
    )
    files = inventory_daily_dataset(root, ("005930",))
    manifest = build_dataset_manifest(
        dataset_manifest_id=RUN_ID,
        created_at=COMPLETED_AT,
        adjustment_mode="ADJUSTED",
        universe_manifest=reference_input_artifact(root, universe_identifier),
        collection_run=summary.reference,
        files=files,
    )
    dataset = publish_successful_dataset_manifest(root, manifest)
    return SnapshotIdentifiers(
        universe=universe_identifier,
        dataset=dataset.identifier,
        collection=summary.identifier,
    )
