from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from app.data.kis.parsers import CurrentPrice, HolidayRow
from app.data.kis.settings import KISSettings
from app.data.kis.storage import UpsertResult
from app.data.kis.universe import UniverseManifest


@dataclass(frozen=True)
class SymbolRunResult:
    symbol: str
    current_price: CurrentPrice
    upsert: UpsertResult
    fetched_rows: int


def write_markdown_report(
    path: Path,
    settings: KISSettings,
    symbols: list[str],
    results: list[SymbolRunResult],
    start: date,
    end: date,
    universe_source: str,
    holiday_rows: list[HolidayRow],
    universe_manifest: UniverseManifest | None = None,
    generated_at: datetime | None = None,
    skipped_reason: str | None = None,
    requested_end: date | None = None,
    previous_trading_day: date | None = None,
) -> Path:
    # markdown report는 자동 테스트가 놓치기 쉬운 운영 증거다.
    # secret/raw response 없이 mode, 범위, 신규 행, skip 사유만 남겨 PR/시연 검증에 재사용한다.
    generated_at = generated_at or datetime.now(UTC)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _render_report(
            settings=settings,
            symbols=symbols,
            results=results,
            start=start,
            end=end,
            universe_source=universe_source,
            universe_manifest=universe_manifest,
            holiday_rows=holiday_rows,
            generated_at=generated_at,
            skipped_reason=skipped_reason,
            requested_end=requested_end,
            previous_trading_day=previous_trading_day,
        ),
        encoding="utf-8",
    )
    return path


def _render_report(
    settings: KISSettings,
    symbols: list[str],
    results: list[SymbolRunResult],
    start: date,
    end: date,
    universe_source: str,
    universe_manifest: UniverseManifest | None,
    holiday_rows: list[HolidayRow],
    generated_at: datetime,
    skipped_reason: str | None,
    requested_end: date | None,
    previous_trading_day: date | None,
) -> str:
    source_label = "offline fixture" if settings.offline else f"KIS {settings.mode} read-only API"
    total_inserted_rows = sum(result.upsert.inserted_rows for result in results)
    lines = [
        "# KIS S1.1 Market Data Report",
        "",
        f"- Generated at: `{generated_at.isoformat()}`",
        f"- Mode: `{settings.mode}`",
        f"- Source: `{source_label}`",
        f"- Date window: `{start.isoformat()}` to `{end.isoformat()}`",
        f"- Symbols requested: `{', '.join(symbols)}`",
        f"- Universe source: {universe_source}",
        f"- New rows this run: `{total_inserted_rows}`",
        "",
        "## Easy Summary",
        "",
        f"This run collected read-only market data for {len(results)} symbol(s).",
        "No order, balance-changing, correction, or cancellation API is called by this S1.1 script.",
    ]
    if skipped_reason:
        # 휴장일 skip은 성공 종료지만 "데이터를 안 받은 이유"가 리포트에 남아야 DoD 검토가 가능하다.
        lines.extend(
            [
                f"Market data collection status: {skipped_reason}.",
                f"Requested end date: `{(requested_end or end).isoformat()}`",
                f"Previous XKRX trading day: `{(previous_trading_day or end).isoformat()}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Symbol Results",
            "",
            "| Symbol | Current Price | Fetched Daily Rows | Stored Rows | New Rows | Coverage |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for result in results:
        coverage = "-"
        if result.upsert.min_date and result.upsert.max_date:
            coverage = f"{result.upsert.min_date.isoformat()} ~ {result.upsert.max_date.isoformat()}"
        lines.append(
            "| "
            f"{result.symbol} | {result.current_price.price} | {result.fetched_rows} | "
            f"{result.upsert.total_rows} | {result.upsert.inserted_rows} | {coverage} |"
        )
    if universe_manifest is not None:
        # manifest 해시와 ranking rule을 함께 싣는다. CSV 원본을 커밋하지 않아도 어떤 기준의 universe였는지
        # 사후에 대조할 수 있게 하기 위한 감사 trail이다.
        lines.extend(
            [
                "",
                "## Universe Manifest",
                "",
                f"- As-of date: `{universe_manifest.as_of_date.isoformat()}`",
                f"- Source: `{universe_manifest.source}`",
                f"- Ranking rule: {universe_manifest.ranking_rule}",
                f"- Source SHA-256: `{universe_manifest.source_sha256}`",
                "",
                "| Rank | Symbol | Name | Market | Market Cap | Trading Value |",
                "|---:|---|---|---|---:|---:|",
            ]
        )
        for item in universe_manifest.symbols:
            lines.append(
                "| "
                f"{item.rank} | {item.symbol} | {item.name} | {item.market} | "
                f"{item.market_cap} | {item.trading_value} |"
            )
    lines.extend(["", "## Holiday Check", ""])
    if holiday_rows:
        lines.extend(["| Date | Trading Day |", "|---|---|"])
        for row in holiday_rows:
            lines.append(f"| {row.date.isoformat()} | {'Y' if row.is_trading_day else 'N'} |")
    else:
        lines.append("Holiday check was skipped or unsupported for the selected mode.")
    lines.extend(
        [
            "",
            "## Safety Notes",
            "",
            # report 자체가 사용자에게 공유될 수 있으므로 S1.1의 금지 경계를 사람이 읽는 문장으로 반복한다.
            "- Secrets, tokens, account numbers, raw response headers, CSV, JSONL, and parquet files must stay out of git.",
            "- `KIS_MODE=live` means live read-only market data in S1.1, not live trading.",
            "- This S1.1 run calls no order, balance-changing, correction, cancellation, or live trading APIs.",
            "- Pass `--symbols-file` with an audited KRX market-cap export when exact current ranking is required.",
            "",
        ]
    )
    return "\n".join(lines)
