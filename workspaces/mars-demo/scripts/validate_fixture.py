#!/usr/bin/env python3
"""Validate public DEMO fixture provenance and event-ledger reconciliation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
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
    if fixture.get("seedVersion") != "mars-demo-2026-09-30.2":
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
        if not ("2026-08-18" <= bar["date"] <= "2026-09-29"):
            fail("bar outside approved public fixture window")
        if min(bar["open"], bar["high"], bar["low"], bar["close"]) <= 0:
            fail("non-positive OHLC")
        if bar["low"] > min(bar["open"], bar["close"]) or bar["high"] < max(bar["open"], bar["close"]):
            fail("OHLC ordering")

    portfolio = fixture.get("showcasePortfolio", {})
    events = portfolio.get("events", [])
    selected = portfolio.get("selectedSymbols", [])
    if len(selected) < 6 or len(selected) != len(set(selected)):
        fail("showcase portfolio must include diversified, unique symbols")
    bar_lookup = {(bar["symbol"], bar["date"]): bar for bar in bars}
    dates = sorted({bar["date"] for bar in bars if bar["symbol"] == selected[0]})
    if len(dates) != 29:
        fail("showcase session coverage")
    if any((symbol, date) not in bar_lookup for symbol in selected for date in dates):
        fail("selected symbol lacks a source bar")

    assumptions = fixture.get("assumptions", {})
    commission_bps = assumptions.get("commissionBpsPerSide")
    slippage_bps = assumptions.get("slippageBpsPerSide")
    sell_tax_bps = assumptions.get("kospiSellTaxBps")
    if (commission_bps, slippage_bps, sell_tax_bps) != (1.5, 10.0, 20.0):
        fail("unexpected transaction cost assumptions")

    order_remaining: dict[str, int] = {}
    order_details: dict[str, dict[str, Any]] = {}
    decisions: dict[str, dict[str, Any]] = {}
    order_dates: set[str] = set()
    no_order_dates: set[str] = set()
    ids: set[str] = set()
    cash = int(portfolio.get("initialCapital", 0))
    positions: dict[str, int] = {}
    basis: dict[str, int] = {}
    gross_basis: dict[str, int] = {}
    marks: dict[str, int] = {}
    realized = 0
    gross_realized = 0
    dividend_cash = 0
    dividend_gross = 0
    dividend_withholding = 0
    commissions = 0
    sell_taxes = 0
    slippage = 0
    fill_count = 0
    sale_count = 0
    winning_sells = 0
    losing_sells = 0
    snapshots: dict[str, dict[str, Any]] = {}
    previous_key: tuple[str, str] | None = None
    for event in events:
        event_type = event.get("type")
        date = event.get("atKst", "")[:10]
        if not date or date not in dates:
            fail("portfolio event outside scenario sessions")
        key = (event["atKst"], event.get("id", ""))
        if previous_key is not None and key < previous_key:
            fail("event ordering")
        previous_key = key
        if not event.get("id") or event["id"] in ids:
            fail("duplicate or missing event id")
        ids.add(event["id"])
        symbol = event.get("symbol")
        if symbol is not None and symbol not in selected:
            fail("event symbol outside selected portfolio")
        if event_type == "DECISION_RECORDED":
            order_id = event.get("orderId")
            if not re.fullmatch(r"dec_[A-Za-z0-9_-]{8,96}", str(event.get("id", ""))):
                fail("scenario decision ID format")
            if not order_id or order_id in decisions or event.get("action") != "ALLOW":
                fail("scenario decision contract")
            if event.get("cashBeforeKrw") != cash or event.get("positionBefore") != positions.get(symbol, 0):
                fail("scenario decision does not match prior ledger state")
            if event.get("quantity", 0) <= 0 or event.get("side") not in {"BUY", "SELL"}:
                fail("scenario decision quantity/side")
            decisions[order_id] = event
        elif event_type == "ORDER_CREATED":
            order_id = event["id"]
            decision = decisions.get(order_id)
            if not decision or event.get("decisionId") != decision["id"]:
                fail("virtual order lacks its recorded decision")
            if any(event.get(field) != decision.get(field) for field in ("symbol", "side", "quantity")):
                fail("order differs from recorded decision")
            if event.get("quantity", 0) <= 0 or event.get("status") != "SIMULATED_ACCEPTED":
                fail("virtual order contract")
            if date in order_dates:
                fail("more than one order in one session")
            order_dates.add(date)
            order_remaining[order_id] = event["quantity"]
            order_details[order_id] = event
        elif event_type == "FILL":
            order_id = event.get("orderId")
            quantity = event.get("quantity", 0)
            if order_id not in order_remaining or quantity <= 0 or quantity > order_remaining[order_id]:
                fail("fill quantity exceeds virtual order")
            order = order_details[order_id]
            if event.get("symbol") != order.get("symbol") or event.get("side") != order.get("side"):
                fail("fill differs from its order")
            order_remaining[order_id] -= quantity
            price_field = event.get("priceField", "")
            source_field = "open" if "daily open" in price_field else "close" if "daily close" in price_field else None
            source_bar = bar_lookup.get((symbol, date))
            if not source_field or not source_bar or event.get("sourceDate") != date:
                fail("fill lacks a dated source field")
            reference = source_bar[source_field]
            side = event["side"]
            expected_price = round_krw(reference * (1 + slippage_bps / 10_000 if side == "BUY" else 1 - slippage_bps / 10_000))
            if event.get("referencePrice") != reference or event.get("price") != expected_price:
                fail("fill price differs from source bar plus assumed slippage")
            gross = quantity * expected_price
            commission = round_krw(gross * commission_bps / 10_000)
            tax = round_krw(gross * sell_tax_bps / 10_000) if side == "SELL" else 0
            if event.get("grossAmount") != gross or event.get("commission") != commission or event.get("transactionTax") != tax:
                fail("fill amount/fee/tax arithmetic")
            if decisions[order_id].get("estimatedAmountKrw") != gross:
                fail("decision amount differs from fill")
            commissions += commission
            sell_taxes += tax
            slippage += abs(gross - reference * quantity)
            fill_count += 1
            if side == "BUY":
                cash -= gross + commission
                positions[symbol] = positions.get(symbol, 0) + quantity
                basis[symbol] = basis.get(symbol, 0) + gross + commission
                gross_basis[symbol] = gross_basis.get(symbol, 0) + gross
            else:
                prior_quantity = positions.get(symbol, 0)
                if quantity > prior_quantity:
                    fail("virtual sale exceeds position")
                sold_cost = basis[symbol] if quantity == prior_quantity else round_krw(basis[symbol] * quantity / prior_quantity)
                sold_gross = gross_basis[symbol] if quantity == prior_quantity else round_krw(gross_basis[symbol] * quantity / prior_quantity)
                net = gross - commission - tax
                pnl = net - sold_cost
                gross_pnl = gross - sold_gross
                if event.get("realizedPnl") != pnl or event.get("realizedGrossPnl") != gross_pnl:
                    fail("sale realized PnL receipt")
                cash += net
                basis[symbol] -= sold_cost
                gross_basis[symbol] -= sold_gross
                positions[symbol] -= quantity
                realized += pnl
                gross_realized += gross_pnl
                sale_count += 1
                winning_sells += pnl > 0
                losing_sells += pnl < 0
        elif event_type == "DIVIDEND":
            amount = event.get("cashAmount", -1)
            if amount < 0 or positions.get(symbol, 0) < event.get("quantity", 0):
                fail("invalid dividend entitlement")
            gross = event.get("grossAmount", -1)
            withholding = event.get("withholdingTax", -1)
            if gross < 0 or withholding < 0 or gross - withholding != amount:
                fail("dividend net calculation")
            source_bar = bar_lookup.get((symbol, date))
            if not source_bar or gross != round_krw(source_bar["dividendPerShare"] * event["quantity"]):
                fail("dividend differs from source corporate action")
            cash += amount
            dividend_cash += amount
            dividend_gross += gross
            dividend_withholding += withholding
        elif event_type == "SPLIT":
            if positions.get(symbol, 0) != event.get("quantityBefore"):
                fail("split quantity does not match ledger")
            source_bar = bar_lookup.get((symbol, date))
            if not source_bar or source_bar["splitRatio"] != event.get("ratio"):
                fail("split differs from source corporate action")
            if event.get("quantityAfter", 0) <= 0:
                fail("split quantity")
            positions[symbol] = event["quantityAfter"]
        elif event_type == "NO_ACTION":
            if date in no_order_dates or not event.get("reason"):
                fail("invalid no-order event")
            no_order_dates.add(date)
        elif event_type == "MARKET_MARK":
            prices = event.get("prices")
            if not isinstance(prices, dict) or set(prices) != set(selected):
                fail("market mark coverage")
            if any(prices[ticker] != bar_lookup[(ticker, date)]["close"] for ticker in selected):
                fail("market mark differs from source close")
            marks.update(prices)
            equity = cash + sum(positions.get(ticker, 0) * marks.get(ticker, 0) for ticker in selected)
            snapshots[date] = {
                "date": date,
                "cash": cash,
                "equity": equity,
                "realizedPnl": realized,
                "holdings": {ticker: {"quantity": positions.get(ticker, 0), "close": marks[ticker]} for ticker in selected if positions.get(ticker, 0) > 0},
            }
        else:
            fail(f"unsupported portfolio event: {event_type}")
        if cash < 0:
            fail("negative cash")
        if any(quantity < 0 for quantity in positions.values()):
            fail("negative position")

    if any(order_remaining.values()) or len(decisions) != len(order_details):
        fail("unfilled order or missing decision")
    if order_dates & no_order_dates or order_dates | no_order_dates != set(dates):
        fail("order/no-order session coverage")
    daily = portfolio.get("dailyReceipt", [])
    if len(daily) != len(dates) or len(snapshots) != len(daily):
        fail("daily ledger coverage")
    for row in daily:
        actual = snapshots.get(row["date"])
        if not actual or any(actual[field] != row[field] for field in ("cash", "equity", "realizedPnl", "holdings")):
            fail(f"daily receipt differs from event log on {row['date']}")
        action_count = sum(event["type"] in {"FILL", "DIVIDEND"} and event["atKst"][:10] == row["date"] for event in events)
        if row.get("actionCount") != action_count:
            fail("daily action count differs from event log")
    final = portfolio.get("final", {})
    final_equity = cash + sum(positions.get(ticker, 0) * marks.get(ticker, 0) for ticker in selected)
    unrealized = sum(positions.get(ticker, 0) * marks.get(ticker, 0) - basis.get(ticker, 0) for ticker in selected if positions.get(ticker, 0) > 0)
    expected = {
        "cash": cash,
        "equity": final_equity,
        "netPnlKrw": final_equity - portfolio["initialCapital"],
        "returnBps": round_krw((final_equity / portfolio["initialCapital"] - 1) * 10_000),
        "realizedPnl": realized,
        "grossRealizedPnl": gross_realized,
        "unrealizedPnl": unrealized,
        "commissionKrw": commissions,
        "sellTaxKrw": sell_taxes,
        "slippageKrw": slippage,
        "dividendCash": dividend_cash,
        "dividendGross": dividend_gross,
        "dividendWithholding": dividend_withholding,
        "orderCount": len(order_details),
        "fillCount": fill_count,
        "winningSaleCount": winning_sells,
        "losingSaleCount": losing_sells,
        "noOrderDays": len(no_order_dates),
        "closedPositionCount": sum(positions.get(ticker, 0) == 0 for ticker in selected),
        "openPositionCount": sum(positions.get(ticker, 0) > 0 for ticker in selected),
        "openPositions": snapshots[dates[-1]]["holdings"],
    }
    if any(final.get(field) != value for field, value in expected.items()):
        fail("final ledger receipt differs from source events")
    if realized + unrealized + dividend_cash != final["netPnlKrw"]:
        fail("net PnL components do not reconcile")
    if len(order_details) < 10 or expected["openPositionCount"] < 4 or sale_count < 4 or winning_sells == 0 or losing_sells == 0:
        fail("showcase record coverage lacks trades, holdings, or mixed outcomes")
    if final["netPnlKrw"] <= 0:
        fail("showcase final portfolio is not positive after modeled costs")

    backtest = fixture.get("backtest", {})
    backtest_daily = backtest.get("daily", [])
    if backtest.get("usesFutureData") is not False:
        fail("backtest future-data contract")
    if len(backtest_daily) != len(dates) or backtest.get("observedDays") != len(backtest_daily):
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
        "openPositionCount": final["openPositionCount"],
        "closedPositionCount": final["closedPositionCount"],
        "orderCount": final["orderCount"],
        "fillCount": final["fillCount"],
        "winningSaleCount": final["winningSaleCount"],
        "losingSaleCount": final["losingSaleCount"],
        "noOrderDays": final["noOrderDays"],
        "netPnlKrw": final["netPnlKrw"],
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
