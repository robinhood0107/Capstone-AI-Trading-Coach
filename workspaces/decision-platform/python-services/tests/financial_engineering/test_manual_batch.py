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

    def publish_all(self, publications: tuple[BatchPublication, ...]) -> tuple[str, ...]:
        self.values.extend(publications)
        return tuple("INSERTED" for _ in publications)


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
    assert result.error_code == "STORED_HISTORY_INSUFFICIENT"
    assert [step.status for step in result.diagnostic_steps] == ["COMPLETE", "NOT_AVAILABLE"]
    assert result.diagnostic_steps[-1].error_code == "STORED_HISTORY_INSUFFICIENT"
    assert publisher.values == []


def test_empty_stored_symbol_set_is_not_available_and_not_published() -> None:
    class EmptyReader(Reader):
        def current_symbols(self) -> tuple[str, ...]:
            return ()

    publisher = Publisher()
    result = ManualFinancialEngineeringBatch(EmptyReader(), publisher, n_paths=1_000).run()
    assert result.status == "NOT_AVAILABLE"
    assert result.error_code == "STORED_SYMBOL_SET_EMPTY"
    assert result.publications == ()
    assert publisher.values == []


def test_multi_symbol_failure_never_calls_atomic_publisher_or_returns_partial_publications() -> None:
    class TwoSymbolReader(Reader):
        def current_symbols(self) -> tuple[str, ...]:
            return ("005930", "000660")

        def read_closes(self, symbol: str, *, limit: int = 253) -> tuple[CloseObservation, ...]:
            if symbol == "000660":
                return super().read_closes(symbol, limit=limit)[:59]
            return super().read_closes(symbol, limit=limit)

    publisher = Publisher()
    result = ManualFinancialEngineeringBatch(TwoSymbolReader(), publisher, n_paths=1_000).run()
    assert result.status == "FAILED"
    assert result.publications == ()
    assert publisher.values == []


def test_local_output_is_promoted_only_after_every_file_is_written(tmp_path: Path) -> None:
    publisher = Publisher()
    result = ManualFinancialEngineeringBatch(Reader(), publisher, n_paths=1_000).run()
    output = tmp_path / "published"
    write_publications(output, result.publications)
    assert output.is_dir()
    assert not list(tmp_path.glob(".published.*.staging"))


def test_local_output_failure_never_promotes_partial_root(tmp_path: Path) -> None:
    publisher = Publisher()
    result = ManualFinancialEngineeringBatch(Reader(), publisher, n_paths=1_000).run()
    duplicated = (result.publications[0], result.publications[0])
    output = tmp_path / "published"
    try:
        write_publications(output, duplicated)
    except FileExistsError:
        pass
    else:
        raise AssertionError("duplicate symbol should fail before promotion")
    assert not output.exists()
    assert not list(tmp_path.glob(".published.*.staging"))


def test_local_output_encodes_symbol_as_one_safe_path_component(tmp_path: Path) -> None:
    publisher = Publisher()
    result = ManualFinancialEngineeringBatch(Reader(), publisher, n_paths=1_000).run()
    original = result.publications[0]
    escaped = BatchPublication(
        {**original.snapshot, "symbol": "../X"},
        original.manifest,
        original.report_markdown,
    )

    output = tmp_path / "published"
    write_publications(output, (escaped,))

    assert (output / "%2E%2E%2FX" / "financial_engineering_report.md").is_file()
    assert not (tmp_path / "X").exists()
