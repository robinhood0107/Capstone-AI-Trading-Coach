from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from app.data.kis.accounting import (
    CollectionRunRecorder,
    CollectionRunStatus,
    LogicalOperation,
    PhysicalChannel,
    SkipCode,
)
from app.data.kis.calendar import previous_xkrx_trading_day
from app.data.kis.http_client import KISHttpClient
from app.data.kis.market_client import KISMarketClient
from app.data.kis.report import SymbolRunResult, write_markdown_report
from app.data.kis.run_artifacts import (
    build_dataset_manifest,
    inventory_daily_dataset,
    publish_collection_summary,
    publish_successful_dataset_manifest,
    reference_input_artifact,
)
from app.data.kis.settings import KISSettings
from app.data.kis.storage import (
    KISConflictingDuplicateError,
    dataset_lock,
    missing_daily_ranges,
    upsert_daily_bars,
)
from app.data.kis.universe import (
    DEFAULT_UNIVERSE_SOURCE,
    UniverseManifest,
    load_symbols_file,
    load_universe_manifest,
    parse_symbols,
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = KISSettings(kis_data_dir=Path(args.data_dir)) if args.data_dir else KISSettings()
    run_id = uuid4()
    logical_caps, physical_caps = _call_caps(args)
    recorder = CollectionRunRecorder(
        run_id=run_id,
        started_at=datetime.now(UTC),
        logical_caps=logical_caps,
        physical_caps=physical_caps,
    )
    symbol_resolution = _resolve_symbols(args, settings.data_dir)
    symbols = symbol_resolution.symbols
    requested_end = _parse_date(args.to) if args.to else date.today()
    # S1.1은 다중 소스 캘린더 집계를 구현하지 않고, 로컬 XKRX 판정으로 KIS 호출 여부만 결정한다.
    # 온라인 휴장일에는 client 생성 전 종료해 OAuth/holiday/current/daily endpoint 모두 호출되지 않게 한다.
    end = previous_xkrx_trading_day(requested_end)
    start = _parse_date(args.start) if args.start else _years_before(end, args.years)
    report_path = Path(args.report_path) if args.report_path else settings.data_dir / "reports" / "kis_s1_1_report.md"
    if not settings.offline and requested_end != end:
        # 운영 모드에서는 휴장일에 KIS endpoint를 호출하지 않고 감사 가능한 skipped report만 남긴다.
        recorder.record_skip(SkipCode.NON_TRADING_DAY)
        write_markdown_report(
            report_path,
            settings=settings,
            symbols=symbols,
            results=[],
            start=start,
            end=end,
            universe_source=symbol_resolution.source,
            universe_manifest=symbol_resolution.manifest,
            holiday_rows=[],
            skipped_reason="Market closed / skipped",
            requested_end=requested_end,
            previous_trading_day=end,
        )
        publish_collection_summary(
            settings.data_dir,
            recorder.snapshot(
                completed_at=datetime.now(UTC),
                status=CollectionRunStatus.SKIPPED,
            ),
        )
        print(f"KIS S1.1 report written to {report_path}")
        return 0
    if not settings.offline and (logical_caps is None or physical_caps is None):
        # provider 호출이 가능한 실행은 승인 packet의 다섯 cap을 전부 명시해야 한다.
        raise ValueError("online KIS backfill requires explicit logical and physical call caps")
    client = _build_client(settings, recorder)
    summary_published = False
    try:
        with dataset_lock(settings.data_dir, exclusive=True):
            results: list[SymbolRunResult] = []
            for symbol in symbols:
                # current/daily 수집부터 manifest-last까지 같은 exclusive lock 안에 두어 S1.5 reader가
                # mutable per-symbol parquet의 mixed snapshot을 관측하지 못하게 한다.
                current_price = client.current_price(symbol)
                ranges = missing_daily_ranges(settings.data_dir, symbol, start, end)
                if not ranges:
                    recorder.record_skip(SkipCode.DATASET_RANGE_PRESENT)
                bars = [
                    bar
                    for range_start, range_end in ranges
                    for bar in client.daily_bars(symbol, range_start, range_end)
                ]
                try:
                    upsert = upsert_daily_bars(settings.data_dir, symbol, bars)
                except KISConflictingDuplicateError as error:
                    recorder.record_ingest_duplicates(
                        exact_rows=error.exact_duplicate_rows,
                        conflicting_groups=error.conflicting_groups,
                    )
                    raise
                recorder.record_ingest_duplicates(
                    exact_rows=upsert.exact_duplicate_rows,
                    conflicting_groups=upsert.conflicting_duplicate_groups,
                )
                results.append(
                    SymbolRunResult(
                        symbol=symbol,
                        current_price=current_price,
                        upsert=upsert,
                        fetched_rows=len(bars),
                    )
                )
            # chk-holiday는 live domain supporting read라서 기본 실행에서 빼고, 명시 옵션일 때만 보수적으로 확인한다.
            holiday_rows = client.holidays(requested_end) if args.check_holiday else []
            write_markdown_report(
                report_path,
                settings=settings,
                symbols=symbols,
                results=results,
                start=start,
                end=end,
                universe_source=symbol_resolution.source,
                universe_manifest=symbol_resolution.manifest,
                holiday_rows=holiday_rows,
                requested_end=requested_end,
            )
            completed_at = datetime.now(UTC)
            collection = publish_collection_summary(
                settings.data_dir,
                recorder.snapshot(
                    completed_at=completed_at,
                    status=CollectionRunStatus.SUCCESS,
                ),
            )
            summary_published = True
            if symbol_resolution.manifest_path is not None:
                universe_identifier = _relative_input_identifier(
                    settings.data_dir,
                    symbol_resolution.manifest_path,
                )
                universe_reference = reference_input_artifact(
                    settings.data_dir,
                    universe_identifier,
                )
                files = inventory_daily_dataset(settings.data_dir, tuple(symbols))
                dataset_manifest = build_dataset_manifest(
                    dataset_manifest_id=run_id,
                    created_at=completed_at,
                    adjustment_mode="ADJUSTED",
                    universe_manifest=universe_reference,
                    collection_run=collection.reference,
                    files=files,
                )
                publish_successful_dataset_manifest(settings.data_dir, dataset_manifest)
    except Exception:
        if not summary_published:
            publish_collection_summary(
                settings.data_dir,
                recorder.snapshot(
                    completed_at=datetime.now(UTC),
                    status=CollectionRunStatus.FAILED,
                ),
            )
        raise
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    print(f"KIS S1.1 report written to {report_path}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill KIS S1.1 read-only market data")
    parser.add_argument("--symbols", help="Comma or space separated stock codes. Defaults to KOSPI large-cap seed.")
    parser.add_argument("--symbols-file", help="Text/CSV file. First column is treated as stock code.")
    parser.add_argument("--universe-manifest", help="Universe manifest JSON generated by kis-universe-refresh.")
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--from", dest="start")
    parser.add_argument("--to")
    parser.add_argument("--data-dir")
    parser.add_argument("--report-path")
    parser.add_argument("--check-holiday", action="store_true")
    parser.add_argument("--current-price-logical-cap", type=_non_negative_int)
    parser.add_argument("--daily-bars-logical-cap", type=_non_negative_int)
    parser.add_argument("--holiday-logical-cap", type=_non_negative_int)
    parser.add_argument("--market-data-physical-cap", type=_non_negative_int)
    parser.add_argument("--token-p-physical-cap", type=_non_negative_int)
    return parser.parse_args(argv)


def _call_caps(
    args: argparse.Namespace,
) -> tuple[
    dict[LogicalOperation, int] | None,
    dict[PhysicalChannel, int] | None,
]:
    """승인 packet의 다섯 cap을 all-or-none으로 고정해 부분 보호 실행을 거부한다."""
    values = (
        args.current_price_logical_cap,
        args.daily_bars_logical_cap,
        args.holiday_logical_cap,
        args.market_data_physical_cap,
        args.token_p_physical_cap,
    )
    if all(value is None for value in values):
        return None, None
    if any(value is None for value in values):
        raise ValueError("all KIS call caps must be provided together")
    current_price, daily_bars, holiday, market_data, token_p = values
    assert current_price is not None
    assert daily_bars is not None
    assert holiday is not None
    assert market_data is not None
    assert token_p is not None
    return (
        {
            LogicalOperation.CURRENT_PRICE: current_price,
            LogicalOperation.DAILY_BARS: daily_bars,
            LogicalOperation.HOLIDAY: holiday,
        },
        {
            PhysicalChannel.MARKET_DATA: market_data,
            PhysicalChannel.TOKEN_P: token_p,
        },
    )


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("call cap must be a non-negative integer") from None
    if parsed < 0:
        raise argparse.ArgumentTypeError("call cap must be a non-negative integer")
    return parsed


class _SymbolResolution:
    def __init__(
        self,
        symbols: list[str],
        source: str,
        manifest: UniverseManifest | None = None,
        manifest_path: Path | None = None,
    ) -> None:
        self.symbols = symbols
        self.source = source
        self.manifest = manifest
        self.manifest_path = manifest_path


def _resolve_symbols(args: argparse.Namespace, data_dir: Path) -> _SymbolResolution:
    # 명시 입력은 smoke/debug 의도를 보존하기 위해 항상 최우선이다.
    # 자동 실행은 감사 가능한 universe manifest를 seed보다 먼저 써서 30종목 기준이 리포트에 남게 한다.
    if args.symbols:
        return _SymbolResolution(parse_symbols(args.symbols), "CLI --symbols")
    if args.symbols_file:
        return _SymbolResolution(load_symbols_file(Path(args.symbols_file)), str(args.symbols_file))
    manifest_path = Path(args.universe_manifest) if args.universe_manifest else data_dir / "universe_manifest.json"
    if manifest_path.exists():
        manifest = load_universe_manifest(manifest_path)
        return _SymbolResolution(
            manifest.symbol_codes,
            manifest.source_label,
            manifest,
            manifest_path,
        )
    return _SymbolResolution(parse_symbols(None), DEFAULT_UNIVERSE_SOURCE)


def _build_client(
    settings: KISSettings,
    accounting: CollectionRunRecorder | None = None,
) -> KISMarketClient:
    # online transport/Redis/token limiter wiring은 KISHttpClient private runtime 경계만 소유한다.
    return KISMarketClient(
        settings,
        http_client=KISHttpClient(settings, accounting=accounting),
        accounting=accounting,
    )


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _years_before(end: date, years: int) -> date:
    try:
        return end.replace(year=end.year - years)
    except ValueError:
        # 2월 29일 기준 backfill도 CLI가 중단되지 않도록 같은 월의 마지막 안전한 날짜로 낮춘다.
        return end.replace(year=end.year - years, day=28)


def _relative_input_identifier(data_dir: Path, path: Path) -> str:
    root = data_dir.expanduser()
    root = root if root.is_absolute() else Path.cwd() / root
    candidate = path.expanduser()
    candidate = candidate if candidate.is_absolute() else Path.cwd() / candidate
    if ".." in root.parts or ".." in candidate.parts:
        raise ValueError("KIS manifest path must be inside the data root")
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        raise ValueError("KIS manifest path must be inside the data root") from None
    identifier = relative.as_posix()
    if not identifier or identifier.startswith("/") or ".." in relative.parts:
        raise ValueError("KIS manifest path must be inside the data root")
    return identifier


if __name__ == "__main__":
    raise SystemExit(main())
