from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.data.kis.universe import (
    KRX_EXPORT_RANKING_RULE,
    UniverseExportError,
    load_universe_manifest,
    parse_symbols,
    refresh_universe_from_krx_export,
    write_universe_markdown_report,
)
from app.data.kis.universe_refresh_cli import main


@pytest.mark.parametrize("value", ["../../escaped", "005930/..", "ABCDEF", "1234567", "１２３４５６"])
def test_parse_symbols_rejects_non_ascii_six_digit_codes(value: str) -> None:
    with pytest.raises(ValueError, match="six digits"):
        parse_symbols(value)


def test_refresh_universe_from_krx_export_ranks_by_market_cap_then_trading_value(
    tmp_path: Path,
) -> None:
    export_path = tmp_path / "krx.csv"
    export_path.write_text(
        "\n".join(
            [
                "종목코드,종목명,시장구분,시가총액,거래대금",
                "000003,High Tie A,KOSPI,\"1,000\",\"90\"",
                "000002,High Tie B,KOSPI,\"1,000\",\"100\"",
                "000001,Highest,KOSPI,\"2,000\",\"1\"",
                "000004,Missing Trading,KOSPI,\"900\",",
                "ABCD,Invalid Code,KOSPI,\"9,999\",\"9,999\"",
            ]
        ),
        encoding="utf-8",
    )

    manifest = refresh_universe_from_krx_export(export_path, as_of=date(2026, 7, 8), limit=3)

    assert manifest.ranking_rule == KRX_EXPORT_RANKING_RULE
    assert [item.symbol for item in manifest.symbols] == ["000001", "000002", "000003"]
    assert [item.rank for item in manifest.symbols] == [1, 2, 3]
    assert manifest.symbols[1].trading_value == 100
    assert manifest.source_sha256


def test_refresh_universe_reads_cp949_krx_export(tmp_path: Path) -> None:
    export_path = tmp_path / "krx_cp949.csv"
    export_path.write_bytes(
        "\n".join(
            [
                "종목코드,종목명,시장구분,시가총액,거래대금",
                "005930,삼성전자,KOSPI,\"500,000\",\"900,000\"",
            ]
        ).encode("cp949")
    )

    manifest = refresh_universe_from_krx_export(export_path, as_of=date(2026, 7, 8), limit=30)

    assert manifest.symbols[0].symbol == "005930"
    assert manifest.symbols[0].name == "삼성전자"


def test_refresh_universe_rejects_xlsx_with_export_guidance(tmp_path: Path) -> None:
    export_path = tmp_path / "krx.xlsx"
    export_path.write_bytes(b"not-a-real-xlsx")

    with pytest.raises(UniverseExportError, match="CSV"):
        refresh_universe_from_krx_export(export_path, as_of=date(2026, 7, 8), limit=30)


def test_universe_manifest_round_trips_schema_and_report(tmp_path: Path) -> None:
    export_path = tmp_path / "krx.tsv"
    export_path.write_text(
        "\n".join(
            [
                "단축코드\t한글 종목명\t시장구분\t시가총액\t거래대금",
                "005930\t삼성전자\tKOSPI\t500000\t900000",
                "000660\tSK하이닉스\tKOSPI\t400000\t800000",
            ]
        ),
        encoding="utf-8-sig",
    )
    manifest_path = tmp_path / "data" / "kis" / "universe_manifest.json"

    manifest = refresh_universe_from_krx_export(
        export_path,
        as_of=date(2026, 7, 8),
        limit=30,
        manifest_path=manifest_path,
    )
    loaded = load_universe_manifest(manifest_path)
    report_path = write_universe_markdown_report(tmp_path / "report.md", manifest)

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 1
    assert payload["asOfDate"] == "2026-07-08"
    assert payload["rankingRule"] == KRX_EXPORT_RANKING_RULE
    assert payload["symbols"][0]["rank"] == 1
    assert loaded.symbols == manifest.symbols
    report = report_path.read_text(encoding="utf-8")
    assert "KIS S1.1b Universe Refresh Report" in report
    assert "005930" in report
    assert "market cap desc" in report


def test_universe_refresh_cli_writes_manifest_and_report_without_kis_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "KIS_OFFLINE",
        "KIS_APP_KEY",
        "KIS_APP_SECRET",
        "KIS_MOCK_APP_KEY",
        "KIS_MOCK_APP_SECRET",
        "KIS_LIVE_APP_KEY",
        "KIS_LIVE_APP_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    export_path = tmp_path / "krx.csv"
    export_path.write_text(
        "\n".join(
            [
                "종목코드,종목명,시장구분,시가총액,거래대금",
                "005930,Samsung Electronics,KOSPI,500000,900000",
            ]
        ),
        encoding="utf-8",
    )
    data_dir = tmp_path / "data" / "kis"
    report_path = tmp_path / "report.md"

    exit_code = main(
        [
            "--krx-export",
            str(export_path),
            "--as-of",
            "2026-07-08",
            "--data-dir",
            str(data_dir),
            "--report-path",
            str(report_path),
        ]
    )

    assert exit_code == 0
    assert (data_dir / "universe_manifest.json").exists()
    assert report_path.exists()
