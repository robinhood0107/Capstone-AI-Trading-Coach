from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from app.data.kis.parsers import DailyBar


@dataclass(frozen=True)
class UpsertResult:
    path: Path
    inserted_rows: int
    total_rows: int
    min_date: date | None
    max_date: date | None


def upsert_daily_bars(data_dir: Path, symbol: str, bars: list[DailyBar]) -> UpsertResult:
    daily_dir = data_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    path = daily_dir / f"{symbol}.parquet"
    existing = _read_existing(path)
    existing_count = len(existing)
    incoming = pd.DataFrame([asdict(bar) for bar in bars])
    if incoming.empty:
        combined = existing
    else:
        incoming["date"] = pd.to_datetime(incoming["date"])
        combined = pd.concat([existing, incoming], ignore_index=True)
    if not combined.empty:
        combined = (
            combined.drop_duplicates(subset=["symbol", "date"], keep="last")
            .sort_values(["symbol", "date"])
            .reset_index(drop=True)
        )
    combined.to_parquet(path, index=False)
    dates = pd.to_datetime(combined["date"]) if not combined.empty else pd.Series(dtype="datetime64[ns]")
    return UpsertResult(
        path=path,
        inserted_rows=max(0, len(combined) - existing_count),
        total_rows=len(combined),
        min_date=dates.min().date() if not dates.empty else None,
        max_date=dates.max().date() if not dates.empty else None,
    )


def _read_existing(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume", "turnover"])
    existing = pd.read_parquet(path)
    if not existing.empty:
        existing["date"] = pd.to_datetime(existing["date"])
    return existing
