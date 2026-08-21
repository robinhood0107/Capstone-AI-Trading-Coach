from __future__ import annotations

import json
import resource
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.data.market_data.reader import CloseObservation
from app.financial_engineering.manual_batch import BatchPublication, ManualFinancialEngineeringBatch


class _WorstCaseFixtureReader:
    def current_symbols(self) -> tuple[str, ...]:
        return tuple(f"{index:06d}" for index in range(1, 31)) + ("132030",)

    def read_closes(self, symbol: str, *, limit: int = 253) -> tuple[CloseObservation, ...]:
        return tuple(
            CloseObservation(
                identity=symbol,
                session_date=date(2025, 8, 1) + timedelta(days=index),
                close=Decimal(str(100.0 + index * 0.1 + (index % 11) * 0.017)),
                temporal_quality="RECONSTRUCTED_FIXED_LAG",
                source_receipt_sha256=f"{index + 1:064x}",
            )
            for index in range(limit)
        )


class _ReceiptPublisher:
    def __init__(self) -> None:
        self.publications: list[BatchPublication] = []

    def publish(self, publication: BatchPublication) -> str:
        self.publications.append(publication)
        return "INSERTED"


def main() -> int:
    publisher = _ReceiptPublisher()
    started = time.perf_counter()
    result = ManualFinancialEngineeringBatch(
        _WorstCaseFixtureReader(),
        publisher,
        n_paths=10_000,
        clock=lambda: datetime(2026, 8, 21, 0, 10, tzinfo=UTC),
    ).run()
    elapsed = time.perf_counter() - started
    peak_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    payload = {
        "evidenceMode": "SYNTHETIC_FIXTURE",
        "productionEvidence": False,
        "status": result.status,
        "symbolCount": len(publisher.publications),
        "gbmPathsPerSymbol": 10_000,
        "wallTimeSeconds": round(elapsed, 6),
        "peakResidentBytes": int(peak_kib * 1024),
        "maxSnapshotBytes": max(
            (len(json.dumps(item.snapshot, separators=(",", ":")).encode()) for item in publisher.publications),
            default=0,
        ),
        "maxReportBytes": max(
            (len(item.report_markdown.encode()) for item in publisher.publications),
            default=0,
        ),
        "providerCalls": result.provider_calls,
        "underThirtyMinutes": elapsed < 1_800,
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if result.status == "COMPLETE" and elapsed < 1_800 else 1


if __name__ == "__main__":
    raise SystemExit(main())
