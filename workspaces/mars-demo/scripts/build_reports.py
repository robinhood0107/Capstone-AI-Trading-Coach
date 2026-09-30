#!/usr/bin/env python3
"""Build independent DEMO backtest and model reports from pinned local evidence.

The LSTM predictions were produced by the point-in-time research pipeline. This
script evaluates recorded predictions; it does not train a model or infer a
prediction for a date absent from that artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any

import pandas as pd


INITIAL = 10_000_000
COMMISSION_BPS = 1.5
SLIPPAGE_BPS = 10.0
SELL_TAX_BPS = 20.0
MODEL_END = "2026-09-18"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def won(value: float) -> int:
    return math.floor(value + 0.5)


def fee(amount: int, bps: float) -> int:
    return won(amount * bps / 10_000)


def quantile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    place = (len(ordered) - 1) * percentile
    low = math.floor(place)
    high = math.ceil(place)
    return ordered[low] + (ordered[high] - ordered[low]) * (place - low)


def metrics(equities: list[int]) -> dict[str, float | None]:
    previous = INITIAL
    returns: list[float] = []
    peak = INITIAL
    mdd = 0.0
    for equity in equities:
        returns.append(equity / previous - 1)
        previous = equity
        peak = max(peak, equity)
        mdd = min(mdd, equity / peak - 1)
    if not returns:
        return dict(cagr=None, mdd=None, sharpe=None, sortino=None, var95=None, cvar95=None)
    mean = statistics.fmean(returns)
    deviation = statistics.stdev(returns) if len(returns) > 1 else 0.0
    downside = math.sqrt(statistics.fmean(min(0.0, item) ** 2 for item in returns))
    var95 = quantile(returns, 0.05)
    return {
        "cagr": (equities[-1] / INITIAL) ** (252 / len(returns)) - 1,
        "mdd": mdd,
        "sharpe": math.sqrt(252) * mean / deviation if deviation > 0 else None,
        "sortino": math.sqrt(252) * mean / downside if downside > 0 else None,
        "var95": var95,
        "cvar95": statistics.fmean(item for item in returns if item <= var95),
    }


def history_by_symbol(prices: pd.DataFrame, symbols: list[str]) -> dict[str, list[tuple[str, int]]]:
    selected = prices[prices["ticker"].isin(symbols)].dropna(subset=["Close"]).copy()
    selected["Date"] = pd.to_datetime(selected["Date"]).dt.strftime("%Y-%m-%d")
    result: dict[str, list[tuple[str, int]]] = {}
    for ticker, group in selected.sort_values(["ticker", "Date"]).groupby("ticker"):
        result[str(ticker)] = [(str(row.Date), won(row.Close)) for row in group.itertuples()]
    return result


def previous_scores(history: dict[str, list[tuple[str, int]]]) -> dict[tuple[str, str], tuple[float, float]]:
    scores: dict[tuple[str, str], tuple[float, float]] = {}
    for symbol, rows in history.items():
        closes = [close for _, close in rows]
        for index, (date, _) in enumerate(rows):
            if index < 50:
                continue
            prior20 = statistics.fmean(closes[index - 20:index])
            prior50 = statistics.fmean(closes[index - 50:index])
            five_day_return = closes[index - 1] / closes[index - 6] - 1
            scores[(symbol, date)] = (prior20 / prior50 - 1, five_day_return)
    return scores


def replay_existing_orders(
    fixture: dict[str, Any], scores: dict[tuple[str, str], tuple[float, float]], strict: bool
) -> dict[str, Any]:
    backtest = fixture["backtest"]
    rows = backtest["daily"]
    dates = [row["date"] for row in rows]
    bars = {(bar["symbol"], bar["date"]): bar for bar in fixture["bars"]}
    events_by_day: dict[str, list[dict[str, Any]]] = {}
    for event in backtest["events"]:
        events_by_day.setdefault(event["date"], []).append(event)
    cash = INITIAL
    positions: dict[str, int] = {}
    basis: dict[str, int] = {}
    daily: list[dict[str, Any]] = []
    blocked = trades = wins = losses = 0
    for date in dates:
        for event in events_by_day.get(date, []):
            symbol = event.get("symbol")
            if event["type"] == "FILL" and event["side"] == "BUY":
                if strict and scores.get((symbol, date), (0.0, 0.0))[1] < -0.03:
                    blocked += 1
                    continue
                price = int(event["price"])
                quantity = int(event["quantity"])
                if quantity * price + fee(quantity * price, COMMISSION_BPS) > cash:
                    quantity = max(0, int(cash / (price * (1 + COMMISSION_BPS / 10_000))))
                gross = price * quantity
                commission = fee(gross, COMMISSION_BPS)
                if quantity:
                    cash -= gross + commission
                    positions[symbol] = positions.get(symbol, 0) + quantity
                    basis[symbol] = basis.get(symbol, 0) + gross + commission
                    trades += 1
            elif event["type"] == "FILL" and event["side"] == "SELL":
                quantity = min(positions.get(symbol, 0), int(event["quantity"]))
                if quantity:
                    gross = quantity * int(event["price"])
                    commission = fee(gross, COMMISSION_BPS)
                    tax = fee(gross, SELL_TAX_BPS)
                    original_quantity = positions[symbol]
                    consumed_basis = won(basis[symbol] * quantity / original_quantity)
                    pnl = gross - commission - tax - consumed_basis
                    wins += pnl > 0
                    losses += pnl < 0
                    cash += gross - commission - tax
                    positions[symbol] -= quantity
                    basis[symbol] -= consumed_basis
                    trades += 1
            elif event["type"] == "SPLIT" and positions.get(symbol, 0):
                positions[symbol] = won(positions[symbol] * float(event["ratio"]))
            elif event["type"] == "DIVIDEND" and positions.get(symbol, 0):
                gross = won(positions[symbol] * float(event["cashPerShare"]))
                cash += gross - fee(gross, 1_400)
        equity = cash + sum(quantity * int(bars[(symbol, date)]["close"]) for symbol, quantity in positions.items())
        daily.append({"date": date, "equity": equity, "cash": cash})
        if not strict and equity != int(rows[len(daily) - 1]["equity"]):
            raise ValueError(f"replayed guide ledger differs on {date}: {equity} != {rows[len(daily) - 1]['equity']}")
    return {"daily": daily, "blockedCandidateCount": blocked, "tradeCount": trades, "wins": wins, "losses": losses}


def backtest_report(fixture: dict[str, Any], scores: dict[tuple[str, str], tuple[float, float]]) -> dict[str, Any]:
    guide = replay_existing_orders(fixture, scores, strict=False)
    strict = replay_existing_orders(fixture, scores, strict=True)
    variants = {"Baseline": guide, "Guide": guide, "Strict": strict}
    strategies = []
    metric_cards = []
    for name, variant in variants.items():
        equities = [row["equity"] for row in variant["daily"]]
        strategies.append({
            "strategy": name,
            "metrics": metrics(equities),
            "curve": [{"at": row["date"], "value": row["equity"] / INITIAL} for row in variant["daily"]],
        })
        metric_cards.extend([
            {"metric": f"{name}.netReturn", "value": equities[-1] / INITIAL - 1},
            {"metric": f"{name}.tradeCount", "value": variant["tradeCount"]},
            {"metric": f"{name}.winRate", "value": variant["wins"] / (variant["wins"] + variant["losses"]) if variant["wins"] + variant["losses"] else None},
        ])
    metric_cards.append({"metric": "Strict.principleViolationCount", "value": strict["blockedCandidateCount"]})
    monthly: dict[str, int] = {}
    for row in guide["daily"]:
        monthly[row["date"][:7]] = row["equity"]
    previous = INITIAL
    heatmap = []
    for month, equity in monthly.items():
        heatmap.append({"month": month, "return": equity / previous - 1})
        previous = equity
    return {
        "period": fixture["backtest"]["testRange"],
        "scenarioRule": {
            "Baseline": "SMA20>SMA50 candidate schedule, no principle block",
            "Guide": "same schedule; warnings do not suppress orders",
            "Strict": "same schedule; block buys when prior five-session close return is below -3%",
        },
        "strategies": strategies,
        "heatmap": heatmap,
        "metricCards": metric_cards,
        "strictBlockedCandidateCount": strict["blockedCandidateCount"],
    }


def model_report(
    fixture: dict[str, Any], scores: dict[tuple[str, str], tuple[float, float]], predictions_root: Path
) -> dict[str, Any]:
    universe = sorted(fixture["backtest"]["universe"])
    dates = [row["date"] for row in fixture["backtest"]["daily"] if row["date"] <= MODEL_END]
    bars = {(bar["symbol"], bar["date"]): bar for bar in fixture["bars"]}
    predictions: dict[tuple[str, str], tuple[float, str, str]] = {}
    combined_hash = hashlib.sha256()
    for symbol in universe:
        path = predictions_root / f"{symbol}-2026.parquet"
        combined_hash.update(bytes.fromhex(sha256(path)))
        rows = pd.read_parquet(path)
        for row in rows.itertuples():
            date = str(row.date)[:10]
            if date not in dates:
                continue
            source_date = str(row.sourceDate)[:10]
            trained_through = str(row.trainedThrough)[:10]
            if not (trained_through < source_date < date) or str(row.ticker) != symbol:
                raise ValueError(f"LSTM timing or ticker mismatch: {symbol} {date}")
            prediction = float(row.prediction)
            if not math.isfinite(prediction):
                raise ValueError(f"nonfinite LSTM prediction: {symbol} {date}")
            predictions[(symbol, date)] = (prediction, source_date, trained_through)
    if len(predictions) != len(universe) * len(dates):
        raise ValueError(f"incomplete LSTM window: {len(predictions)} != {len(universe) * len(dates)}")

    model_rows = []
    for model in ("BASELINE", "LSTM"):
        equity = INITIAL
        daily = []
        for date in dates:
            ranked = sorted(
                universe,
                key=lambda symbol: (
                    -(scores[(symbol, date)][0] if model == "BASELINE" else predictions[(symbol, date)][0]),
                    symbol,
                ),
            )[:5]
            cash = equity
            positions: list[tuple[str, int]] = []
            for symbol in ranked:
                opening = int(bars[(symbol, date)]["open"])
                buy_price = won(opening * (1 + SLIPPAGE_BPS / 10_000))
                quantity = max(0, int((equity / 5) / (buy_price * (1 + COMMISSION_BPS / 10_000))))
                gross = quantity * buy_price
                cash -= gross + fee(gross, COMMISSION_BPS)
                positions.append((symbol, quantity))
            for symbol, quantity in positions:
                closing = int(bars[(symbol, date)]["close"])
                sell_price = won(closing * (1 - SLIPPAGE_BPS / 10_000))
                gross = quantity * sell_price
                cash += gross - fee(gross, COMMISSION_BPS) - fee(gross, SELL_TAX_BPS)
            equity = cash
            daily.append({"date": date, "equity": equity, "selectedSymbols": ranked})
        model_rows.append({"modelId": model, "status": "AVAILABLE", "metrics": metrics([row["equity"] for row in daily]), "daily": daily})

    return {
        "period": {"start": dates[0], "end": dates[-1]},
        "predictionSha256": combined_hash.hexdigest(),
        "predictionArtifact": "w756-quarterly point-in-time LSTM; train-only scaler; trainedThrough <= 2026-06-30",
        "execution": "prior-close signal; next-session open buy, same-session close sell; five equal budget slots; whole shares and modeled costs",
        "models": model_rows,
        "sourceRunIds": [f"lstm_w756_quarterly_{combined_hash.hexdigest()[:16]}"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--predictions-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    fixture = json.loads(args.scenario.read_text(encoding="utf-8"))
    if sha256(args.prices) != fixture["source"]["sourceSha256"]:
        raise ValueError("report price parquet differs from the scenario's licensed source hash")
    prices = pd.read_parquet(args.prices)
    scores = previous_scores(history_by_symbol(prices, fixture["backtest"]["universe"]))
    output = {
        "schemaVersion": "mars-demo.reports.v1",
        "seedVersion": fixture["seedVersion"],
        "scenarioSha256": sha256(args.scenario),
        "sourcePriceSha256": fixture["source"]["sourceSha256"],
        "assumptions": fixture["assumptions"],
        "backtestReport": backtest_report(fixture, scores),
        "modelEvaluation": model_report(fixture, scores, args.predictions_root),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({
        "scenarioSha256": output["scenarioSha256"],
        "modelPredictionSha256": output["modelEvaluation"]["predictionSha256"],
        "strictBlockedCandidateCount": output["backtestReport"]["strictBlockedCandidateCount"],
        "baselineModelReturn": output["modelEvaluation"]["models"][0]["daily"][-1]["equity"] / INITIAL - 1,
        "lstmModelReturn": output["modelEvaluation"]["models"][1]["daily"][-1]["equity"] / INITIAL - 1,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
