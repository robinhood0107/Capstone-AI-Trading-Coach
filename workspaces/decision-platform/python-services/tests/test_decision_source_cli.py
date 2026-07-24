from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from app import decision_source_cli


@pytest.mark.parametrize(
    ("entrypoint", "writer_attribute", "dsn_environment", "label"),
    [
        (
            decision_source_cli.market_quote_main,
            "append_market_quote_fixture",
            "DECISION_MARKET_WRITER_DATABASE_DSN",
            "market_quote",
        ),
        (
            decision_source_cli.instrument_catalog_main,
            "append_instrument_catalog_fixture",
            "DECISION_MARKET_WRITER_DATABASE_DSN",
            "instrument_catalog",
        ),
        (
            decision_source_cli.kis_mock_portfolio_main,
            "append_kis_mock_portfolio_fixture",
            "DECISION_PORTFOLIO_WRITER_DATABASE_DSN",
            "kis_mock_portfolio",
        ),
        (
            decision_source_cli.deterministic_metrics_main,
            "append_deterministic_metric_fixture",
            "DECISION_RISK_WRITER_DATABASE_DSN",
            "deterministic_metrics",
        ),
        (
            decision_source_cli.corporation_registry_main,
            "append_corporation_registry_fixture",
            "DECISION_COLLECTOR_DATABASE_DSN",
            "corporation_registry",
        ),
    ],
)
def test_writer_clis_use_only_fixture_and_role_specific_dsn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    entrypoint: Callable[[], None],
    writer_attribute: str,
    dsn_environment: str,
    label: str,
) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def writer(path: Path, *, database_dsn: str) -> int:
        captured.update(path=path, database_dsn=database_dsn)
        return 2

    monkeypatch.setattr(decision_source_cli, writer_attribute, writer)
    monkeypatch.setenv(dsn_environment, "postgresql://sanitized-role")
    monkeypatch.setattr(sys, "argv", [f"decision-{label}-append", str(fixture)])

    entrypoint()

    assert captured == {
        "path": fixture,
        "database_dsn": "postgresql://sanitized-role",
    }
    assert json.loads(capsys.readouterr().out) == {
        "inserted": 2,
        "source": label,
    }


@pytest.mark.parametrize(
    ("entrypoint", "dsn_environment"),
    [
        (decision_source_cli.market_quote_main, "DECISION_MARKET_WRITER_DATABASE_DSN"),
        (decision_source_cli.instrument_catalog_main, "DECISION_MARKET_WRITER_DATABASE_DSN"),
        (decision_source_cli.kis_mock_portfolio_main, "DECISION_PORTFOLIO_WRITER_DATABASE_DSN"),
        (decision_source_cli.deterministic_metrics_main, "DECISION_RISK_WRITER_DATABASE_DSN"),
        (decision_source_cli.corporation_registry_main, "DECISION_COLLECTOR_DATABASE_DSN"),
    ],
)
def test_writer_cli_fails_closed_without_role_dsn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    entrypoint: Callable[[], None],
    dsn_environment: str,
) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text("{}", encoding="utf-8")
    monkeypatch.delenv(dsn_environment, raising=False)
    monkeypatch.setattr(sys, "argv", ["decision-source-append", str(fixture)])

    with pytest.raises(ValueError, match=dsn_environment):
        entrypoint()
