#!/usr/bin/env python3
"""Extend a licensed local Yahoo source bundle without changing its original files."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf


REQUIRED_COLUMNS = {
    "Date", "Open", "High", "Low", "Close", "Adj Close", "Volume",
    "Dividends", "Stock Splits", "ticker",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_one(ticker: str, start: str, end_exclusive: str, expected_dates: list[str]) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            frame = yf.download(
                ticker,
                start=start,
                end=end_exclusive,
                auto_adjust=False,
                actions=True,
                repair=True,
                progress=False,
                threads=False,
                timeout=20,
            )
            if frame.empty:
                raise ValueError(f"empty source download for {ticker}")
            frame.columns = frame.columns.get_level_values(0)
            frame = frame.reset_index()
            frame["ticker"] = ticker
            frame["Date"] = pd.to_datetime(frame["Date"]).dt.tz_localize(None)
            if not REQUIRED_COLUMNS.issubset(frame.columns):
                raise ValueError(f"missing source columns for {ticker}")
            dates = frame["Date"].dt.strftime("%Y-%m-%d").tolist()
            if dates != expected_dates:
                raise ValueError(f"source dates differ from pinned KRX sessions for {ticker}: {dates}")
            return frame
        except Exception as error:
            last_error = error
            if attempt < 2:
                time.sleep(attempt + 1)
    raise ValueError(f"failed source download for {ticker}") from last_error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-parquet", required=True, type=Path)
    parser.add_argument("--base-receipt", required=True, type=Path)
    parser.add_argument("--universe", required=True, type=Path)
    parser.add_argument("--calendar", required=True, type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end-exclusive", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    base_receipt = json.loads(args.base_receipt.read_text(encoding="utf-8"))
    base_hash = sha256(args.base_parquet)
    if base_hash != base_receipt.get("sha256") or base_receipt.get("source") != "Yahoo Finance via yfinance":
        raise ValueError("original source receipt does not match input")
    if base_receipt.get("endExclusive") != args.start:
        raise ValueError("increment must begin at original exclusive end")
    if yf.__version__ != base_receipt.get("yfinanceVersion"):
        raise ValueError("provider library version differs from original source")

    calendar = json.loads(args.calendar.read_text(encoding="utf-8"))
    expected_dates = [
        session["date"] for session in calendar["sessions"]
        if args.start <= session["date"] < args.end_exclusive
    ]
    if not expected_dates:
        raise ValueError("no pinned trading sessions in increment")
    universe = json.loads(args.universe.read_text(encoding="utf-8"))
    tickers = sorted({item["yfinanceTicker"] for item in universe["symbols"]} | {"^KS11", "^KQ11", "069500.KS"})
    if len(tickers) != 34:
        raise ValueError("expected 31 fixed symbols plus two benchmarks and KODEX 200")

    increments = []
    for ticker in tickers:
        frame = collect_one(ticker, args.start, args.end_exclusive, expected_dates)
        increments.append(frame)
        print(f"SOURCE_INCREMENT {ticker} {len(frame)} {expected_dates[0]}..{expected_dates[-1]}", flush=True)
    incremental = pd.concat(increments, ignore_index=True).sort_values(["ticker", "Date"])
    base = pd.read_parquet(args.base_parquet)
    if not REQUIRED_COLUMNS.issubset(base.columns) or set(base["ticker"]) != set(tickers):
        raise ValueError("original source universe or columns differ")
    if pd.to_datetime(base["Date"]).max().strftime("%Y-%m-%d") >= args.start:
        raise ValueError("original source overlaps increment")
    combined = pd.concat([base, incremental], ignore_index=True).sort_values(["ticker", "Date"])
    if combined.duplicated(["ticker", "Date"]).any():
        raise ValueError("combined source contains a duplicate ticker/date")

    args.output_dir.mkdir(parents=True, exist_ok=False)
    increment_path = args.output_dir / "increment.parquet"
    combined_path = args.output_dir / "long_history.parquet"
    incremental.to_parquet(increment_path, index=False)
    combined.to_parquet(combined_path, index=False)
    receipt = {
        "source": "Yahoo Finance via yfinance",
        "yfinanceVersion": yf.__version__,
        "sourceCollectedAt": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds"),
        "startRequested": base_receipt["startRequested"],
        "endExclusive": args.end_exclusive,
        "incrementStart": args.start,
        "incrementSessions": expected_dates,
        "baseSourceSha256": base_hash,
        "incrementSha256": sha256(increment_path),
        "sha256": sha256(combined_path),
        "rows": len(combined),
        "tickers": len(tickers),
        "failures": [],
        "limitations": base_receipt["limitations"],
        "providerOptions": "auto_adjust=false, actions=true, repair=true, threads=false",
    }
    (args.output_dir / "data-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"SOURCE_EXTENDED sha256={receipt['sha256']} rows={receipt['rows']} sessions={len(expected_dates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
