from datetime import date
import json
from pathlib import Path

import pytest

from app.data.kis.backfill_cli import _build_client, main
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

    monkeypatch.setattr(
        "app.data.kis.backfill_cli._build_client",
        lambda settings, accounting: FakeClient(),
    )

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


def test_online_backfill_skips_non_trading_day_before_building_client(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KIS_MODE", "mock")
    monkeypatch.setenv("KIS_OFFLINE", "0")
    monkeypatch.setenv("KIS_MOCK_APP_KEY", "mock-key")
    monkeypatch.setenv("KIS_MOCK_APP_SECRET", "mock-secret")
    monkeypatch.setenv("KIS_DATA_DIR", str(tmp_path))
    report_path = tmp_path / "reports" / "kis_s1_1_report.md"
    build_calls = 0

    def fail_if_called(settings, accounting) -> object:
        nonlocal build_calls
        build_calls += 1
        raise AssertionError("online non-trading day must skip before building KIS client")

    monkeypatch.setattr("app.data.kis.backfill_cli._build_client", fail_if_called)

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
            "--report-path",
            str(report_path),
        ]
    )

    assert exit_code == 0
    assert build_calls == 0
    assert not (tmp_path / "daily" / "005930.parquet").exists()
    report = report_path.read_text(encoding="utf-8")
    assert "Market closed / skipped" in report
    assert "Requested end date: `2026-07-11`" in report
    assert "Previous XKRX trading day: `2026-07-10`" in report


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

    monkeypatch.setattr(
        "app.data.kis.backfill_cli._build_client",
        lambda settings, accounting: FakeClient(),
    )

    exit_code = main(["--from", "2026-07-08", "--to", "2026-07-08", "--data-dir", str(tmp_path)])

    assert exit_code == 0
    assert captured == ["000660"]
    summaries = list(tmp_path.glob("collection-runs/*/*/*/*/summary.json"))
    assert len(summaries) == 1
    latest = json.loads(
        (tmp_path / "datasets" / "latest-success-manifest.json").read_text(encoding="utf-8")
    )
    assert latest["datasetManifest"]["identifier"].endswith("/manifest.json")


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

    monkeypatch.setattr(
        "app.data.kis.backfill_cli._build_client",
        lambda settings, accounting: FakeClient(),
    )

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


def test_backfill_cli_does_not_refetch_daily_range_already_in_parquet(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("KIS_OFFLINE", "1")
    daily_calls: list[tuple[date, date]] = []

    class FakeClient:
        def current_price(self, symbol: str) -> CurrentPrice:
            return CurrentPrice(symbol, 1, 1, 1, 1, 1, 1, 0, 0)

        def daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]:
            daily_calls.append((start, end))
            return [
                DailyBar(symbol, date(2026, 7, 7), 1, 1, 1, 1, 1),
                DailyBar(symbol, date(2026, 7, 8), 1, 1, 1, 1, 1),
            ]

        def holidays(self, base_date: date) -> list:
            return []

    monkeypatch.setattr(
        "app.data.kis.backfill_cli._build_client",
        lambda settings, accounting: FakeClient(),
    )
    args = [
        "--symbols",
        "005930",
        "--from",
        "2026-07-07",
        "--to",
        "2026-07-08",
        "--data-dir",
        str(tmp_path),
    ]

    assert main(args) == 0
    assert main(args) == 0

    assert daily_calls == [(date(2026, 7, 7), date(2026, 7, 8))]


def test_online_client_wires_shared_rest_and_tokenp_quota_scopes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.data.kis.settings import KISSettings

    closed: list[str] = []

    class FakeRedis:
        def close(self) -> None:
            closed.append("redis")

    redis_client = FakeRedis()
    reservations: list[tuple[str, float]] = []

    class RecordingLimiter:
        def __init__(
            self,
            client: object,
            *,
            key: str,
            interval_seconds: float,
            max_wait_seconds: float,
            io_budget_seconds: float = 0.0,
        ) -> None:
            assert client is redis_client
            assert max_wait_seconds == 10.0
            assert io_budget_seconds == 8.0
            reservations.append((key, interval_seconds))

        def acquire(self) -> None:
            return None

    class FakeIssuer:
        def __init__(self, settings: object, *, rate_limiter: object) -> None:
            self.rate_limiter = rate_limiter

        def issue(self) -> dict[str, object]:
            return {"access_token": "validation-dummy-token", "expires_in": 86400}

        def close(self) -> None:
            closed.append("issuer")

    monkeypatch.setattr("app.data.kis.http_client._build_redis_client", lambda: redis_client)
    monkeypatch.setattr(
        "app.data.kis.http_client._provider_scope",
        lambda mode: ("a" if mode == "live" else "b") * 64,
    )
    monkeypatch.setattr("app.data.kis.http_client.RedisIntervalLimiter", RecordingLimiter)
    monkeypatch.setattr("app.data.kis.http_client._TokenIssuer", FakeIssuer)
    for mode in ("live", "mock"):
        settings = KISSettings(
            kis_mode=mode,
            kis_offline=False,
            kis_data_dir=tmp_path,
            _env_file=None,
        )
        client = _build_client(settings)
        client.close()

    assert reservations == [
        (f"kis:rest:v3:{'a' * 64}", 0.12),
        ("kis:tokenp:v3:deployment", 1.0),
        (f"kis:rest:v3:{'b' * 64}", 1.0),
        ("kis:tokenp:v3:deployment", 1.0),
    ]
    assert closed == ["issuer", "redis", "issuer", "redis"]


def test_backfill_cli_closes_runtime_client_on_success(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KIS_OFFLINE", "1")
    closed: list[str] = []

    class FakeClient:
        def current_price(self, symbol: str) -> CurrentPrice:
            return CurrentPrice(symbol, 1, 1, 1, 1, 1, 1, 0, 0)

        def daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]:
            return [DailyBar(symbol, end, 1, 1, 1, 1, 1)]

        def holidays(self, base_date: date) -> list:
            return []

        def close(self) -> None:
            closed.append("closed")

    monkeypatch.setattr(
        "app.data.kis.backfill_cli._build_client",
        lambda settings, accounting: FakeClient(),
    )

    assert (
        main(
            [
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
    assert closed == ["closed"]


def test_backfill_cli_closes_runtime_client_on_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KIS_OFFLINE", "1")
    closed: list[str] = []

    class FailingClient:
        def current_price(self, symbol: str) -> CurrentPrice:
            raise RuntimeError("synthetic failure")

        def close(self) -> None:
            closed.append("closed")

    monkeypatch.setattr(
        "app.data.kis.backfill_cli._build_client",
        lambda settings, accounting: FailingClient(),
    )

    with pytest.raises(RuntimeError, match="synthetic failure"):
        main(
            [
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

    assert closed == ["closed"]
    summaries = list(tmp_path.glob("collection-runs/*/*/*/*/summary.json"))
    assert len(summaries) == 1
    assert json.loads(summaries[0].read_text(encoding="utf-8"))["status"] == "FAILED"
    assert not (tmp_path / "datasets" / "latest-success-manifest.json").exists()
