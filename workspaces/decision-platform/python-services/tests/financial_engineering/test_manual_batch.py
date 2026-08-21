from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from app.data.market_data.reader import CloseObservation
from app.financial_engineering.manual_batch import (
    BatchPublication,
    ManualFinancialEngineeringBatch,
    write_publications,
)


class Reader:
    def __init__(self, count: int = 100) -> None:
        self.count = count

    def current_symbols(self) -> tuple[str, ...]:
        return ("005930",)

    def read_closes(self, symbol: str, *, limit: int = 253) -> tuple[CloseObservation, ...]:
        return tuple(
            CloseObservation(
                symbol,
                date(2026, 1, 2) + timedelta(days=index),
                Decimal(str(100 + index * 0.2 + (index % 7) * 0.03)),
                "RECONSTRUCTED_FIXED_LAG",
                f"{index + 1:064x}",
            )
            for index in range(self.count)
        )


class Publisher:
    def __init__(self) -> None:
        self.values: list[BatchPublication] = []

    def publish(self, publication: BatchPublication) -> str:
        self.values.append(publication)
        return "INSERTED"


def test_manual_batch_publishes_only_complete_snapshot_and_report(tmp_path: Path) -> None:
    publisher = Publisher()
    result = ManualFinancialEngineeringBatch(
        Reader(),
        publisher,
        n_paths=1_000,
        clock=lambda: datetime(2026, 8, 21, 0, 10, tzinfo=UTC),
    ).run()

    assert result.status == "COMPLETE"
    assert result.provider_calls == 0
    assert len(publisher.values) == 1
    publication = publisher.values[0]
    assert publication.manifest["complete"] is True
    assert [step["name"] for step in publication.manifest["steps"]] == [
        "STORED_COLLECTION",
        "FEATURE",
        "INFERENCE",
        "SNAPSHOT",
        "REPORT",
    ]
    assert "provider calls: 0" in publication.report_markdown
    assert "reference only" in publication.report_markdown
    repo = Path(__file__).resolve().parents[5]
    for schema_name, payload in (
        ("financial_engineering_snapshot.v1", publication.snapshot),
        ("financial_engineering_report_manifest.v1", publication.manifest),
    ):
        schema = json.loads((repo / f"contracts/schemas/{schema_name}.schema.json").read_text())
        assert list(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload)
        ) == []
    write_publications(tmp_path / "output", result.publications)
    assert (tmp_path / "output/005930/financial_engineering_report.md").is_file()


def test_incomplete_history_is_not_published() -> None:
    publisher = Publisher()
    result = ManualFinancialEngineeringBatch(Reader(59), publisher, n_paths=1_000).run()
    assert result.status == "NOT_AVAILABLE"
    assert result.error_code == "VALUEERROR"
    assert publisher.values == []
