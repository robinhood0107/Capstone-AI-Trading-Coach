#!/usr/bin/env python3
"""Validate public DEMO fixture provenance and event-ledger reconciliation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def fail(message: str) -> None:
    raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def round_krw(value: float) -> int:
    import math

    return int(math.floor(value + 0.5))


def verify(path: Path) -> dict[str, Any]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    if fixture.get("schemaVersion") != "mars-demo.scenario.v1":
        fail("fixture schema")
    if fixture.get("seedVersion") != "mars-demo-2026-09-29.1":
        fail("fixture seed version")
    source = fixture.get("source", {})
    if source.get("provider") != "Yahoo Finance via yfinance":
        fail("fixture source")
    if len(source.get("sourceSha256", "")) != 64:
        fail("source hash missing")
    attestation = source.get("permissionAttestation", {})
    expected_scope = {
        "public display of licensed historical price bars",
        "derived virtual portfolio and backtest outputs",
        "inclusion of licensed raw or derived market data in the pjjpjj111/mars-demo image",
    }
    if attestation.get("date") != "2026-09-29" or set(attestation.get("scope", [])) != expected_scope:
        fail("public/image data-use attestation scope")
    if "provider contract was not included" not in attestation.get("evidenceKind", ""):
        fail("permission evidence statement")

    calendar = fixture.get("calendar", {})
    if calendar.get("policyVersion") != "xkrx-4.13.2-kis-corrections-v1":
        fail("calendar policy")
    if len(calendar.get("sourceSha256", "")) != 64:
        fail("calendar source hash")

    bars = fixture.get("bars", [])
    if not bars:
        fail("empty bars")
    bar_keys: set[tuple[str, str]] = set()
    for bar in bars:
        key = (bar.get("symbol"), bar.get("date"))
        if key in bar_keys:
            fail(f"duplicate bar {key}")
        bar_keys.add(key)
        if not ("2026-08-18" <= bar["date"] <= "2026-09-18"):
            fail("bar outside approved public fixture window")
        if min(bar["open"], bar["high"], bar["low"], bar["close"]) <= 0:
            fail("non-positive OHLC")
        if bar["low"] > min(bar["open"], bar["close"]) or bar["high"] < max(bar["open"], bar["close"]):
            fail("OHLC ordering")

    portfolio = fixture.get("showcasePortfolio", {})
    events = portfolio.get("events", [])
    order_remaining: dict[str, int] = {}
    cash = int(portfolio.get("initialCapital", 0))
    positions: dict[str, int] = {}
    basis: dict[str, int] = {}
    marks: dict[str, int] = {}
    realized = 0
    dividend_cash = 0
    dividend_gross = 0
    dividend_withholding = 0
    snapshots: dict[str, dict[str, Any]] = {}
    previous_key: tuple[str, str] | None = None
    for event in events:
        event_type = event.get("type")
        date = event.get("atKst", "")[:10]
        if not date or date > "2026-09-18":
            fail("portfolio event outside scenario period")
        key = (event["atKst"], event.get("id", ""))
        if previous_key is not None and key < previous_key:
            fail("event ordering")
        previous_key = key
        if event_type == "ORDER_CREATED":
            if event.get("quantity", 0) <= 0 or event.get("status") != "SIMULATED_ACCEPTED":
                fail("virtual order contract")
            order_remaining[event["id"]] = event["quantity"]
        elif event_type == "FILL":
            order_id = event.get("orderId")
            quantity = event.get("quantity", 0)
            if order_id not in order_remaining or quantity <= 0 or quantity > order_remaining[order_id]:
                fail("fill quantity exceeds virtual order")
            order_remaining[order_id] -= quantity
            symbol = event["symbol"]
            gross = event["grossAmount"]
            fees = event.get("commission", 0) + event.get("transactionTax", 0)
            if event["side"] == "BUY":
                cash -= gross + fees
                positions[symbol] = positions.get(symbol, 0) + quantity
                basis[symbol] = basis.get(symbol, 0) + gross + fees
            elif event["side"] == "SELL":
                if quantity > positions.get(symbol, 0):
                    fail("virtual sale exceeds position")
                cash += gross - fees
                prior_quantity = positions[symbol]
                cost = round_krw(basis[symbol] * quantity / prior_quantity)
                basis[symbol] -= cost
                positions[symbol] -= quantity
                realized += gross - fees - cost
            else:
                fail("unknown fill side")
        elif event_type == "DIVIDEND":
            amount = event.get("cashAmount", -1)
            if amount < 0 or positions.get(event.get("symbol"), 0) < event.get("quantity", 0):
                fail("invalid dividend entitlement")
            gross = event.get("grossAmount", -1)
            withholding = event.get("withholdingTax", -1)
            if gross < 0 or withholding < 0 or gross - withholding != amount:
                fail("dividend net calculation")
            cash += amount
            dividend_cash += amount
            dividend_gross += gross
            dividend_withholding += withholding
        elif event_type == "SPLIT":
            symbol = event["symbol"]
            if positions.get(symbol, 0) != event.get("quantityBefore"):
                fail("split quantity does not match ledger")
            if event.get("quantityAfter", 0) <= 0:
                fail("split quantity")
            positions[symbol] = event["quantityAfter"]
        elif event_type == "MARKET_MARK":
            prices = event.get("prices")
            if not isinstance(prices, dict) or not prices:
                fail("market mark prices")
            marks.update(prices)
            equity = cash + sum(positions.get(ticker, 0) * marks.get(ticker, 0) for ticker in positions)
            snapshots[date] = {
                "date": date,
                "cash": cash,
                "equity": equity,
                "realizedPnl": realized,
            }
        else:
            fail(f"unsupported portfolio event: {event_type}")
        if cash < 0:
            fail("negative cash")
        if any(quantity < 0 for quantity in positions.values()):
            fail("negative position")

    if any(order_remaining.values()):
        fail("unfilled quantity left without an explicit state")
    daily = portfolio.get("dailyReceipt", [])
    if len(daily) != 24 or len(snapshots) != len(daily):
        fail("daily ledger coverage")
    for row in daily:
        actual = snapshots.get(row["date"])
        if not actual or any(actual[field] != row[field] for field in ("cash", "equity", "realizedPnl")):
            fail(f"daily receipt differs from event log on {row['date']}")
    final = portfolio.get("final", {})
    final_equity = cash + sum(positions.get(ticker, 0) * marks.get(ticker, 0) for ticker in positions)
    if final_equity != final.get("equity") or cash != final.get("cash"):
        fail("final cash/equity reconciliation")
    if realized != final.get("realizedPnl") or dividend_cash != final.get("dividendCash"):
        fail("final realized/dividend reconciliation")
    if dividend_gross != final.get("dividendGross") or dividend_withholding != final.get("dividendWithholding"):
        fail("dividend gross/withholding reconciliation")
    if any(quantity < 0 for quantity in positions.values()):
        fail("final negative position")

    backtest = fixture.get("backtest", {})
    backtest_daily = backtest.get("daily", [])
    if backtest.get("usesFutureData") is not False:
        fail("backtest future-data contract")
    if len(backtest_daily) != 24 or backtest.get("observedDays") != len(backtest_daily):
        fail("backtest date coverage")
    if backtest.get("activeDays", 0) + backtest.get("noActionDays", 0) != len(backtest_daily):
        fail("backtest action/no-action coverage")
    if backtest_daily[-1]["equity"] != backtest.get("finalEquity"):
        fail("backtest final equity")
    if backtest_daily[-1]["returnBps"] != backtest.get("returnBps"):
        fail("backtest final return")

    return {
        "seedVersion": fixture["seedVersion"],
        "sourceSha256": source["sourceSha256"],
        "barCount": len(bars),
        "portfolioEventCount": len(events),
        "backtestEventCount": len(backtest.get("events", [])),
        "portfolioSessions": len(daily),
        "showcaseReturnBps": final["returnBps"],
        "backtestReturnBps": backtest["returnBps"],
        "backtestNoActionDays": backtest["noActionDays"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    summary = verify(args.fixture)
    print("MARS_DEMO_FIXTURE_VALID=" + json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
