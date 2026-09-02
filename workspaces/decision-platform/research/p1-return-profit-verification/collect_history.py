"""exact-31 + 벤치마크의 최대 이력을 수집해 캐시한다. (return-engine venv)

벤치마크 둘은 상장폐지 종목을 구성상 포함하므로 생존편향이 없다 -
^KS11 (KOSPI 지수), 069500.KS (KODEX 200 ETF).
"""

import json
import pathlib
import time

import pandas as pd
import yfinance as yf

REPO = pathlib.Path("/home/pjjpj/projects/Capstone-AI-Trading-Coach")
OUT = pathlib.Path("/tmp/p1exp")
OUT.mkdir(exist_ok=True)

catalog = json.loads((REPO / "contracts/catalogs/p1-return-universe.v1.json").read_text())
tickers = [item["yfinanceTicker"] for item in catalog["symbols"]]
benchmarks = ["^KS11", "069500.KS"]

frames = []
started = time.time()
for ticker in tickers + benchmarks:
    frame = yf.download(
        ticker, period="max", auto_adjust=False, progress=False, threads=False
    )
    if frame is None or frame.empty:
        print(f"  FAIL {ticker}")
        continue
    if hasattr(frame.columns, "get_level_values"):
        frame.columns = frame.columns.get_level_values(0)
    frame = frame.reset_index()[["Date", "Open", "High", "Low", "Close", "Volume"]]
    frame["ticker"] = ticker
    frames.append(frame)
    print(f"  OK {ticker:12s} {len(frame):5d}행 {frame['Date'].iloc[0].date()}~{frame['Date'].iloc[-1].date()}")

merged = pd.concat(frames, ignore_index=True)
merged["Date"] = pd.to_datetime(merged["Date"]).dt.tz_localize(None)
merged = merged.sort_values(["ticker", "Date"]).reset_index(drop=True)
path = OUT / "long_history.parquet"
merged.to_parquet(path, index=False)

print()
print(f"저장: {path}")
print(f"총 {len(merged):,}행 / {merged['ticker'].nunique()}티커")
print(f"기간 {merged['Date'].min().date()} ~ {merged['Date'].max().date()}")
print(f"수집 {time.time() - started:.0f}초")
print()
print("=== 연도별 상장 종목 수 (PIT 유니버스 크기) ===")
uni = merged[merged["ticker"].isin(tickers)]
for year in range(2000, 2027, 2):
    n = uni[uni["Date"].dt.year == year]["ticker"].nunique()
    print(f"  {year}: {n:2d}종목")
