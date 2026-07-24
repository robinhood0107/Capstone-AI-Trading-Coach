from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app import decision_source_cli


def test_market_writer_cli_uses_only_fixture_and_role_specific_dsn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def writer(path: Path, *, database_dsn: str) -> int:
        captured.update(path=path, database_dsn=database_dsn)
        return 2

    monkeypatch.setattr(decision_source_cli, "append_market_quote_fixture", writer)
    monkeypatch.setenv("DECISION_MARKET_WRITER_DATABASE_DSN", "postgresql://sanitized-role")
    monkeypatch.setattr(sys, "argv", ["decision-market-quote-append", str(fixture)])

    decision_source_cli.market_quote_main()

    assert captured == {
        "path": fixture,
        "database_dsn": "postgresql://sanitized-role",
    }
    assert json.loads(capsys.readouterr().out) == {
        "inserted": 2,
        "source": "market_quote",
    }


def test_writer_cli_fails_closed_without_role_dsn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("DECISION_MARKET_WRITER_DATABASE_DSN", raising=False)
    monkeypatch.setattr(sys, "argv", ["decision-market-quote-append", str(fixture)])

    with pytest.raises(ValueError, match="DECISION_MARKET_WRITER_DATABASE_DSN"):
        decision_source_cli.market_quote_main()
