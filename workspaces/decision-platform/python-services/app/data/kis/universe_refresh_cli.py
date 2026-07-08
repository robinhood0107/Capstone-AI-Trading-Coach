from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from app.data.kis.universe import refresh_universe_from_krx_export, write_universe_markdown_report


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    # universe refresh는 로컬 KRX export만 읽으므로 KIS secret 검증과 네트워크 설정을 요구하지 않는다.
    # 이 CLI를 별도 경로로 둬 S1.1 market-data client에 KRX 수집/네트워크 책임이 섞이지 않게 한다.
    data_dir = Path(args.data_dir) if args.data_dir else Path("data/kis")
    as_of = date.fromisoformat(args.as_of)
    manifest_path = data_dir / "universe_manifest.json"
    manifest = refresh_universe_from_krx_export(
        Path(args.krx_export),
        as_of=as_of,
        limit=args.limit,
        manifest_path=manifest_path,
    )
    # manifest는 machine-readable 입력이고 report는 사람이 검토하는 증거다.
    # 둘 다 ignored data path에 두는 전제로 raw KRX export를 Git에 올리지 않는다.
    report_path = Path(args.report_path) if args.report_path else data_dir / "reports" / "universe_refresh.md"
    write_universe_markdown_report(report_path, manifest)
    print(f"KIS S1.1b universe manifest written to {manifest_path}")
    print(f"KIS S1.1b universe report written to {report_path}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh KIS S1.1b read-only universe from KRX export")
    parser.add_argument("--krx-export", required=True, help="KRX CSV/TSV/TXT export with market cap and trading value")
    parser.add_argument("--as-of", required=True, help="Universe ranking base date in YYYY-MM-DD format")
    parser.add_argument("--data-dir")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--report-path")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
