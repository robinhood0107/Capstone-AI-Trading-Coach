from datetime import date
import json
from pathlib import Path

from app.data.kis.backfill_cli import main
from app.data.kis.parsers import CurrentPrice, DailyBar


def test_offline_backfill_cli_writes_parquet_and_markdown_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KIS_OFFLINE", "1")
    monkeypatch.setenv("KIS_DATA_DIR", str(tmp_path))

    exit_code = main(
        [
            "--symbols",
            "005930",
            "--years",
            "3",
            "--to",
            "2026-07-08",
            "--report-path",
            str(tmp_path / "reports" / "kis_s1_1_report.md"),
        ]
    )

    assert exit_code == 0
    assert (tmp_path / "daily" / "005930.parquet").exists()
    report = (tmp_path / "reports" / "kis_s1_1_report.md").read_text(encoding="utf-8")
    assert "KIS S1.1 Market Data Report" in report
    assert "005930" in report
    assert "offline fixture" in report


def test_backfill_cli_moves_non_trading_end_date_to_previous_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KIS_OFFLINE", "1")
    monkeypatch.setenv("KIS_DATA_DIR", str(tmp_path))
    captured: dict[str, date] = {}

    class FakeClient:
        def current_price(self, symbol: str) -> CurrentPrice:
            return CurrentPrice(symbol, 73500, 72800, 73900, 72400, 12123456, 0, 0, 0)

        def daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]:
            captured["end"] = end
            return [DailyBar(symbol, end, 72800, 73900, 72400, 73500, 12123456)]

        def holidays(self, base_date: date) -> list:
            return []

    monkeypatch.setattr("app.data.kis.backfill_cli._build_client", lambda settings: FakeClient())

    exit_code = main(
        [
            "--symbols",
            "005930",
            "--from",
            "2026-07-01",
            "--to",
            "2026-07-11",
            "--data-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert captured["end"] == date(2026, 7, 10)


def test_backfill_cli_uses_default_manifest_before_fallback_seed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("KIS_OFFLINE", "1")
    monkeypatch.setenv("KIS_DATA_DIR", str(tmp_path))
    manifest_path = tmp_path / "universe_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "generatedAt": "2026-07-08T00:00:00+00:00",
                "asOfDate": "2026-07-08",
                "source": "/tmp/krx.csv",
                "sourceSha256": "abc",
                "rankingRule": "market cap desc, trading value desc, symbol asc",
                "limit": 30,
                "symbols": [
                    {
                        "rank": 1,
                        "symbol": "000660",
                        "name": "SK hynix",
                        "market": "KOSPI",
                        "marketCap": 400000,
                        "tradingValue": 800000,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    captured: list[str] = []

    class FakeClient:
        def current_price(self, symbol: str) -> CurrentPrice:
            captured.append(symbol)
            return CurrentPrice(symbol, 1, 1, 1, 1, 1, 1, 0, 0)

        def daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]:
            return [DailyBar(symbol, end, 1, 1, 1, 1, 1)]

        def holidays(self, base_date: date) -> list:
            return []

    monkeypatch.setattr("app.data.kis.backfill_cli._build_client", lambda settings: FakeClient())

    exit_code = main(["--from", "2026-07-08", "--to", "2026-07-08", "--data-dir", str(tmp_path)])

    assert exit_code == 0
    assert captured == ["000660"]


def test_backfill_cli_symbol_sources_override_manifest_in_precedence_order(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("KIS_OFFLINE", "1")
    monkeypatch.setenv("KIS_DATA_DIR", str(tmp_path))
    manifest_path = tmp_path / "chosen_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "generatedAt": "2026-07-08T00:00:00+00:00",
                "asOfDate": "2026-07-08",
                "source": "/tmp/krx.csv",
                "sourceSha256": "abc",
                "rankingRule": "market cap desc, trading value desc, symbol asc",
                "limit": 30,
                "symbols": [
                    {
                        "rank": 1,
                        "symbol": "000660",
                        "name": "SK hynix",
                        "market": "KOSPI",
                        "marketCap": 400000,
                        "tradingValue": 800000,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    symbols_file = tmp_path / "symbols.txt"
    symbols_file.write_text("035420\n", encoding="utf-8")
    captured: list[str] = []

    class FakeClient:
        def current_price(self, symbol: str) -> CurrentPrice:
            captured.append(symbol)
            return CurrentPrice(symbol, 1, 1, 1, 1, 1, 1, 0, 0)

        def daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]:
            return [DailyBar(symbol, end, 1, 1, 1, 1, 1)]

        def holidays(self, base_date: date) -> list:
            return []

    monkeypatch.setattr("app.data.kis.backfill_cli._build_client", lambda settings: FakeClient())

    assert (
        main(
            [
                "--universe-manifest",
                str(manifest_path),
                "--from",
                "2026-07-08",
                "--to",
                "2026-07-08",
                "--data-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--universe-manifest",
                str(manifest_path),
                "--symbols-file",
                str(symbols_file),
                "--from",
                "2026-07-08",
                "--to",
                "2026-07-08",
                "--data-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--universe-manifest",
                str(manifest_path),
                "--symbols-file",
                str(symbols_file),
                "--symbols",
                "005930",
                "--from",
                "2026-07-08",
                "--to",
                "2026-07-08",
                "--data-dir",
                str(tmp_path),
            ]
        )
        == 0
    )

    assert captured == ["000660", "035420", "005930"]
