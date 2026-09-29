#!/usr/bin/env python3
"""Build the public DEMO price fixture from an explicitly licensed source bundle.

The input parquet remains outside Git. The deterministic output is the only
market-data artifact checked into the repository and included in the DEMO image.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd


START_DATE = "2026-08-18"
END_DATE = "2026-09-18"
INITIAL_CASH_KRW = 10_000_000
COMMISSION_BPS = 1.5
SELL_TAX_BPS = 20.0
DIVIDEND_WITHHOLDING_TAX_BPS = 1_400.0
SLIPPAGE_BPS = 10.0
SHORT_NAMES = {
    "000660.KS": "SK하이닉스",
    "006400.KS": "삼성SDI",
    "005930.KS": "삼성전자",
    "069500.KS": "KODEX 200",
    "^KS11": "KOSPI 종합지수",
    "^KQ11": "KOSDAQ 종합지수",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def as_int(value: Any) -> int:
    if pd.isna(value):
        return 0
    return int(round(float(value)))


def as_float(value: Any) -> float:
    if pd.isna(value):
        return 0.0
    return float(value)


def round_krw(value: float) -> int:
    return int(math.floor(value + 0.5))


def fee(amount: int, bps: float) -> int:
    return round_krw(amount * bps / 10_000)


def load_calendar(path: Path) -> dict[str, Any]:
    calendar = json.loads(path.read_text(encoding="utf-8"))
    if calendar.get("schemaVersion") != "mars-demo.krx-calendar.v1":
        raise ValueError("calendar schemaVersion")
    sessions = calendar.get("sessions")
    if not isinstance(sessions, list):
        raise ValueError("calendar sessions")
    return calendar


def source_rows(frame: pd.DataFrame, tickers: set[str]) -> dict[str, list[dict[str, Any]]]:
    selected = frame[frame["ticker"].isin(tickers)].copy()
    selected["Date"] = pd.to_datetime(selected["Date"]).dt.strftime("%Y-%m-%d")
    selected = selected[(selected["Date"] >= START_DATE) & (selected["Date"] <= END_DATE)]
    result: dict[str, list[dict[str, Any]]] = {}
    for ticker, group in selected.sort_values(["ticker", "Date"]).groupby("ticker"):
        result[str(ticker)] = [
            {
                "date": str(row["Date"]),
                "open": as_int(row["Open"]),
                "high": as_int(row["High"]),
                "low": as_int(row["Low"]),
                "close": as_int(row["Close"]),
                "adjustedClose": as_int(row["Adj Close"]),
                "volume": as_int(row["Volume"]),
                "dividendPerShare": as_float(row["Dividends"]),
                "splitRatio": as_float(row["Stock Splits"]),
            }
            for _, row in group.iterrows()
        ]
    return result


def sma(series: list[float], width: int, index: int) -> float | None:
    end = index - 1
    start = end - width + 1
    if start < 0:
        return None
    return sum(series[start : end + 1]) / width


def net_fill_price(reference: int, side: str) -> int:
    factor = SLIPPAGE_BPS / 10_000
    return round_krw(reference * (1 + factor if side == "BUY" else 1 - factor))


def hindsight_portfolio(
    bars: dict[str, list[dict[str, Any]]],
    symbols: list[str],
    calendar: dict[str, Any],
) -> dict[str, Any]:
    close_return: list[tuple[str, float]] = []
    for ticker in symbols:
        rows = bars.get(ticker, [])
        if len(rows) < 2 or rows[0]["date"] != START_DATE or rows[-1]["date"] != END_DATE:
            raise ValueError(f"incomplete historical rows for {ticker}")
        close_return.append((ticker, rows[-1]["close"] / rows[0]["close"] - 1))
    # This is intentionally a post-hoc showcase, never the strategy backtest.
    selected = [ticker for ticker, _ in sorted(close_return, key=lambda value: (-value[1], value[0]))[:2]]
    daily_dates = [
        day["date"] for day in calendar["sessions"] if START_DATE <= day["date"] <= END_DATE
    ]
    if any([row["date"] for row in bars[ticker]] != daily_dates for ticker in selected):
        raise ValueError("selected price bars do not match the pinned XKRX calendar")

    events: list[dict[str, Any]] = []
    selected_holdings = {ticker: 0 for ticker in selected}
    cash = INITIAL_CASH_KRW
    positions: dict[str, dict[str, int]] = {}

    for index, ticker in enumerate(selected):
        row = bars[ticker][0]
        allocation = INITIAL_CASH_KRW * 0.4
        price = net_fill_price(row["open"], "BUY")
        quantity = int(allocation // price)
        if quantity <= 0:
            raise ValueError(f"allocation cannot buy one share of {ticker}")
        gross = quantity * price
        commission = fee(gross, COMMISSION_BPS)
        if gross + commission > cash:
            raise ValueError("showcase entry exceeds available cash")
        cash -= gross + commission
        selected_holdings[ticker] = quantity
        positions[ticker] = {"quantity": quantity, "averageCost": gross + commission}
        events.extend(
            [
                {
                    "id": f"showcase-order-{index + 1}",
                    "atKst": f"{START_DATE}T09:00:00+09:00",
                    "type": "ORDER_CREATED",
                    "symbol": ticker,
                    "side": "BUY",
                    "quantity": quantity,
                    "status": "SIMULATED_ACCEPTED",
                    "reason": "사후 구성 가상 사례의 고정 진입 규칙",
                },
                {
                    "id": f"showcase-fill-{index + 1}",
                    "atKst": f"{START_DATE}T09:01:00+09:00",
                    "type": "FILL",
                    "symbol": ticker,
                    "orderId": f"showcase-order-{index + 1}",
                    "side": "BUY",
                    "quantity": quantity,
                    "price": price,
                    "referencePrice": row["open"],
                    "grossAmount": gross,
                    "commission": commission,
                    "transactionTax": 0,
                    "sourceDate": START_DATE,
                    "priceField": "daily open plus 10bp assumed slippage",
                    "simulationOnly": True,
                },
            ]
        )

    daily: list[dict[str, Any]] = []
    realized_pnl = 0
    marks: dict[str, int] = {}
    dividend_events = []
    per_day = {ticker: {row["date"]: row for row in rows} for ticker, rows in bars.items()}

    for day_index, day in enumerate(daily_dates):
        for ticker in selected:
            row = per_day[ticker][day]
            ratio = row["splitRatio"]
            if ratio > 0 and selected_holdings[ticker] > 0:
                prior_quantity = selected_holdings[ticker]
                next_quantity = round_krw(prior_quantity * ratio)
                selected_holdings[ticker] = next_quantity
                events.append(
                    {
                        "id": f"split-{ticker}-{day}",
                        "atKst": f"{day}T08:30:00+09:00",
                        "type": "SPLIT",
                        "symbol": ticker,
                        "ratio": ratio,
                        "quantityBefore": prior_quantity,
                        "quantityAfter": next_quantity,
                        "simulationOnly": True,
                    }
                )
            dividend = row["dividendPerShare"]
            if dividend > 0 and selected_holdings[ticker] > 0:
                gross_dividend = round_krw(dividend * selected_holdings[ticker])
                withholding = fee(gross_dividend, DIVIDEND_WITHHOLDING_TAX_BPS)
                amount = gross_dividend - withholding
                cash += amount
                dividend_events.append(
                    {
                        "id": f"dividend-{ticker}-{day}",
                        "atKst": f"{day}T09:00:00+09:00",
                        "type": "DIVIDEND",
                        "symbol": ticker,
                        "quantity": selected_holdings[ticker],
                        "cashPerShare": dividend,
                        "grossAmount": gross_dividend,
                        "withholdingTax": withholding,
                        "cashAmount": amount,
                        "sourceDate": day,
                        "simulationOnly": True,
                    }
                )
                events.append(dividend_events[-1])
            marks[ticker] = row["close"]

        # The lower-ranked hindsight pick is closed at the published end-date close.
        if day == END_DATE:
            ticker = selected[1]
            quantity = selected_holdings[ticker]
            reference = per_day[ticker][day]["close"]
            price = net_fill_price(reference, "SELL")
            gross = quantity * price
            commission = fee(gross, COMMISSION_BPS)
            tax = fee(gross, SELL_TAX_BPS)
            net = gross - commission - tax
            cost_basis = positions[ticker]["averageCost"]
            cash += net
            realized_pnl += net - cost_basis
            selected_holdings[ticker] = 0
            del positions[ticker]
            events.extend(
                [
                    {
                        "id": "showcase-order-exit",
                        "atKst": f"{day}T15:20:00+09:00",
                        "type": "ORDER_CREATED",
                        "symbol": ticker,
                        "side": "SELL",
                        "quantity": quantity,
                        "status": "SIMULATED_ACCEPTED",
                        "reason": "사후 구성 사례의 기간 종료 규칙",
                    },
                    {
                        "id": "showcase-fill-exit",
                        "atKst": f"{day}T15:29:00+09:00",
                        "type": "FILL",
                        "symbol": ticker,
                        "orderId": "showcase-order-exit",
                        "side": "SELL",
                        "quantity": quantity,
                        "price": price,
                        "referencePrice": reference,
                        "grossAmount": gross,
                        "commission": commission,
                        "transactionTax": tax,
                        "realizedPnl": net - cost_basis,
                        "sourceDate": day,
                        "priceField": "daily close less 10bp assumed slippage",
                        "simulationOnly": True,
                    },
                ]
            )

        equity = cash + sum(selected_holdings[ticker] * marks[ticker] for ticker in selected)
        events.append(
            {
                "id": f"market-mark-{day}",
                "atKst": f"{day}T15:30:00+09:00",
                "type": "MARKET_MARK",
                "prices": {ticker: marks[ticker] for ticker in selected},
                "sourceDate": day,
                "simulationOnly": True,
            }
        )
        daily.append(
            {
                "date": day,
                "cash": cash,
                "equity": equity,
                "realizedPnl": realized_pnl,
                "holdings": {
                    ticker: {"quantity": selected_holdings[ticker], "close": marks[ticker]}
                    for ticker in selected
                    if selected_holdings[ticker] > 0
                },
                "actionCount": sum(
                    1 for event in events if event.get("type") in {"FILL", "DIVIDEND"} and event["atKst"][:10] == day
                ),
            }
        )

    # Keep a single source event stream. Daily snapshots are a verification receipt,
    # not an independent portfolio input.
    events.sort(key=lambda event: (event["atKst"], event["id"]))
    final = daily[-1]
    return {
        "id": "posthoc-showcase-2026-08-18-to-2026-09-18",
        "label": "사후 구성 가상 사례",
        "sourceRange": {"start": START_DATE, "end": END_DATE},
        "selectionRule": "해당 기간의 종가 수익률 상위 2개 종목을 결과 확인 후 선택; 사전 예측력이나 운용 성과가 아님",
        "initialCapital": INITIAL_CASH_KRW,
        "externalNetFlows": 0,
        "selectedSymbols": selected,
        "events": events,
        "dailyReceipt": daily,
        "final": {
            "cash": final["cash"],
            "equity": final["equity"],
            "returnBps": round_krw((final["equity"] / INITIAL_CASH_KRW - 1) * 10_000),
            "realizedPnl": final["realizedPnl"],
            "unrealizedPnl": final["equity"] - final["cash"] - sum(
                positions[ticker]["averageCost"] for ticker in positions
            ),
            "dividendCash": sum(event["cashAmount"] for event in dividend_events),
            "dividendGross": sum(event["grossAmount"] for event in dividend_events),
            "dividendWithholding": sum(event["withholdingTax"] for event in dividend_events),
            "openPositions": final["holdings"],
        },
        "benchmark": {
            "ticker": "^KS11",
            "startClose": per_day["^KS11"][START_DATE]["close"],
            "endClose": per_day["^KS11"][END_DATE]["close"],
            "returnBps": round_krw(
                (per_day["^KS11"][END_DATE]["close"] / per_day["^KS11"][START_DATE]["close"] - 1)
                * 10_000
            ),
        },
        "assumptions": {
            "commissionBpsPerSide": COMMISSION_BPS,
            "kospiSaleTaxBps": SELL_TAX_BPS,
            "dividendWithholdingBps": DIVIDEND_WITHHOLDING_TAX_BPS,
            "slippageBpsPerSide": SLIPPAGE_BPS,
            "dividendTreatment": "Yahoo Finance Dividends is credited net of a 14% national withholding assumption on ex-date for the held whole-share quantity; it is not reinvested. Individual final/local tax liability is not modeled.",
            "splitTreatment": "Yahoo Finance Stock Splits ratios adjust held share quantities on the recorded date; source OHLC is the provider's split-adjusted history.",
            "intradayOrder": "Not inferred. The intraday display times are virtual workflow timestamps; price references use only the source daily open/close fields.",
            "rounding": "whole shares; KRW amounts rounded to nearest won; no leverage; external flows 0",
        },
    }


def moving_average_backtest(
    frame: pd.DataFrame,
    bars: dict[str, list[dict[str, Any]]],
    symbols: list[str],
    calendar: dict[str, Any],
) -> dict[str, Any]:
    history = frame[frame["ticker"].isin(symbols)].copy()
    history["Date"] = pd.to_datetime(history["Date"]).dt.strftime("%Y-%m-%d")
    history = history.sort_values(["ticker", "Date"])
    capital = INITIAL_CASH_KRW
    slot_budget = capital / len(symbols)
    cash = capital
    holdings: dict[str, int] = {ticker: 0 for ticker in symbols}
    marks: dict[str, int] = {}
    close_history: dict[str, list[float]] = {}
    date_rows: dict[str, list[dict[str, Any]]] = {}
    for ticker, group in history.groupby("ticker"):
        close_history[str(ticker)] = [as_float(value) for value in group["Close"].tolist()]
        date_rows[str(ticker)] = [
            {
                "date": str(day),
                "open": as_int(opening),
                "close": as_int(closing),
                "dividend": as_float(dividend),
                "split": as_float(split),
            }
            for day, opening, closing, dividend, split in zip(
                group["Date"], group["Open"], group["Close"], group["Dividends"], group["Stock Splits"]
            )
        ]
    dates = [day["date"] for day in calendar["sessions"] if START_DATE <= day["date"] <= END_DATE]
    events: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    equity_curve: list[int] = []
    no_action_days = 0
    active_days = 0

    def prior_signal(ticker: str, date_index: int) -> tuple[bool, str | None]:
        rows = date_rows[ticker]
        row_index = next(i for i, row in enumerate(rows) if row["date"] == dates[date_index])
        short = sma(close_history[ticker], 20, row_index)
        long = sma(close_history[ticker], 50, row_index)
        signal_date = rows[row_index - 1]["date"] if row_index > 0 else None
        return short is not None and long is not None and short > long, signal_date

    for date_index, day in enumerate(dates):
        actions_today = 0
        for ticker in sorted(symbols):
            rows = date_rows[ticker]
            row = next(item for item in rows if item["date"] == day)
            hold_signal, signal_date = prior_signal(ticker, date_index)
            if hold_signal and holdings[ticker] == 0:
                price = net_fill_price(row["open"], "BUY")
                quantity = int(slot_budget // price)
                gross = quantity * price
                commission = fee(gross, COMMISSION_BPS)
                if quantity > 0 and gross + commission <= cash:
                    cash -= gross + commission
                    holdings[ticker] = quantity
                    events.append(
                        {
                            "id": f"bt-buy-{ticker}-{day}",
                            "date": day,
                            "type": "FILL",
                            "symbol": ticker,
                            "side": "BUY",
                            "quantity": quantity,
                            "price": price,
                            "referencePrice": row["open"],
                            "commission": commission,
                            "transactionTax": 0,
                            "signalAsOf": signal_date,
                            "execution": "next XKRX session open after prior-close signal",
                        }
                    )
                    actions_today += 1
            elif not hold_signal and holdings[ticker] > 0:
                quantity = holdings[ticker]
                price = net_fill_price(row["open"], "SELL")
                gross = quantity * price
                commission = fee(gross, COMMISSION_BPS)
                tax = fee(gross, SELL_TAX_BPS)
                cash += gross - commission - tax
                holdings[ticker] = 0
                events.append(
                    {
                        "id": f"bt-sell-{ticker}-{day}",
                        "date": day,
                        "type": "FILL",
                        "symbol": ticker,
                        "side": "SELL",
                        "quantity": quantity,
                        "price": price,
                        "referencePrice": row["open"],
                        "commission": commission,
                        "transactionTax": tax,
                        "signalAsOf": signal_date,
                        "execution": "next XKRX session open after prior-close signal",
                    }
                )
                actions_today += 1

            if row["split"] > 0 and holdings[ticker] > 0:
                before = holdings[ticker]
                holdings[ticker] = round_krw(before * row["split"])
                events.append(
                    {"id": f"bt-split-{ticker}-{day}", "date": day, "type": "SPLIT", "symbol": ticker, "ratio": row["split"], "quantityBefore": before, "quantityAfter": holdings[ticker]}
                )
            if row["dividend"] > 0 and holdings[ticker] > 0:
                gross_dividend = round_krw(holdings[ticker] * row["dividend"])
                withholding = fee(gross_dividend, DIVIDEND_WITHHOLDING_TAX_BPS)
                amount = gross_dividend - withholding
                cash += amount
                events.append(
                    {"id": f"bt-dividend-{ticker}-{day}", "date": day, "type": "DIVIDEND", "symbol": ticker, "quantity": holdings[ticker], "cashPerShare": row["dividend"], "grossAmount": gross_dividend, "withholdingTax": withholding, "cashAmount": amount}
                )
            marks[ticker] = row["close"]

        if actions_today:
            active_days += 1
        else:
            no_action_days += 1
            events.append(
                {
                    "id": f"bt-no-action-{day}",
                    "date": day,
                    "type": "NO_ACTION",
                    "reason": "이전 종가 기준 20일 평균이 50일 평균 위로 올라서지 않거나 포지션 변화 신호가 없음",
                }
            )
        equity = cash + sum(holdings[ticker] * marks.get(ticker, 0) for ticker in symbols)
        equity_curve.append(equity)
        daily.append(
            {
                "date": day,
                "equity": equity,
                "cash": cash,
                "returnBps": round_krw((equity / capital - 1) * 10_000),
                "actionCount": actions_today,
                "openPositions": sum(1 for quantity in holdings.values() if quantity > 0),
            }
        )

    peak = capital
    max_drawdown_bps = 0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak:
            max_drawdown_bps = min(max_drawdown_bps, round_krw((equity / peak - 1) * 10_000))
    final_equity = equity_curve[-1] if equity_curve else capital
    return {
        "id": "prior-close-sma20-sma50-next-open-v1",
        "label": "20/50일 단순이동평균 예시",
        "testRange": {"start": START_DATE, "end": END_DATE},
        "signal": "각 거래일 이전 종가까지의 SMA20 > SMA50이면 보유; 반대면 현금",
        "execution": "신호 다음 XKRX 세션 시가에 실행; 소스 일별 시가 기준 + 슬리피지",
        "universe": symbols,
        "universeLimitation": "현재 고정된 31종목 목록으로 survivorship bias가 남으며, 전체 시장 투자전략 성과를 뜻하지 않음",
        "initialCapital": capital,
        "finalEquity": final_equity,
        "returnBps": round_krw((final_equity / capital - 1) * 10_000),
        "maxDrawdownBps": max_drawdown_bps,
        "activeDays": active_days,
        "noActionDays": no_action_days,
        "observedDays": len(dates),
        "events": sorted(events, key=lambda event: (event["date"], event["id"])),
        "daily": daily,
        "usesFutureData": False,
        "selectionMethod": "규칙·구성 종목·거래비용은 평가 구간을 보기 전 고정된 예시. 날짜별 신호는 직전 XKRX 종가만 사용.",
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    source_path = args.prices.resolve()
    receipt_path = args.data_receipt.resolve()
    universe_path = args.universe.resolve()
    calendar_path = args.calendar.resolve()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    observed_hash = sha256(source_path)
    if observed_hash != receipt.get("sha256"):
        raise ValueError("source parquet hash does not match data receipt")
    if receipt.get("source") != "Yahoo Finance via yfinance":
        raise ValueError("unexpected source provider")
    calendar = load_calendar(calendar_path)
    universe = json.loads(universe_path.read_text(encoding="utf-8"))
    symbols = sorted(item["yfinanceTicker"] for item in universe["symbols"])
    if len(symbols) != 31:
        raise ValueError("expected fixed 31-symbol universe")
    market_tickers = symbols + ["^KS11", "^KQ11", "069500.KS"]

    frame = pd.read_parquet(source_path)
    required_columns = {"Date", "Open", "High", "Low", "Close", "Adj Close", "Volume", "Dividends", "Stock Splits", "ticker"}
    if not required_columns.issubset(frame.columns):
        raise ValueError("source columns")
    bars = source_rows(frame, set(market_tickers))
    session_dates = [
        day["date"] for day in calendar["sessions"] if START_DATE <= day["date"] <= END_DATE
    ]
    for ticker in market_tickers:
        if [row["date"] for row in bars.get(ticker, [])] != session_dates:
            raise ValueError(f"{ticker} does not match the pinned XKRX session set")

    portfolio = hindsight_portfolio(bars, symbols, calendar)
    backtest = moving_average_backtest(frame, bars, symbols, calendar)
    return {
        "schemaVersion": "mars-demo.scenario.v1",
        "seedVersion": "mars-demo-2026-09-29.1",
        "source": {
            "provider": "Yahoo Finance via yfinance",
            "providerVersion": receipt["yfinanceVersion"],
            "sourceFile": "Yahoo Finance historical daily price extract (original local file excluded)",
            "sourceSha256": observed_hash,
            "sourceCollectedAt": args.source_collected_at,
            "requestedRange": {"start": receipt["startRequested"], "endExclusive": receipt["endExclusive"]},
            "scenarioRange": {"start": START_DATE, "end": END_DATE},
            "downloadUrlPattern": "https://finance.yahoo.com/quote/{ticker}/history/",
            "userAttribution": "Yahoo Finance historical daily OHLCV and corporate-action fields; yfinance 0.2.66, auto_adjust=false, actions=true, repair=true.",
            "knownLimits": receipt["limitations"],
            "permissionAttestation": {
                "date": args.permission_attested_on,
                "scope": [
                    "public display of licensed historical price bars",
                    "derived virtual portfolio and backtest outputs",
                    "inclusion of licensed raw or derived market data in the pjjpjj111/mars-demo image",
                ],
                "evidenceKind": "user confirmation in implementation session; provider contract was not included in the local source bundle",
            },
        },
        "calendar": {
            "schemaVersion": calendar["schemaVersion"],
            "policyVersion": calendar["policyVersion"],
            "sourceSha256": calendar["sha256"],
            "sourceUrl": calendar["sourceUrl"],
            "timezone": "Asia/Seoul",
        },
        "assumptions": {
            "commissionBpsPerSide": COMMISSION_BPS,
            "kospiSellTaxBps": SELL_TAX_BPS,
            "dividendWithholdingBps": DIVIDEND_WITHHOLDING_TAX_BPS,
            "slippageBpsPerSide": SLIPPAGE_BPS,
            "rounding": "whole shares; KRW amounts rounded to nearest won; no leverage",
            "corporateActions": "Yahoo Finance Dividends credited net of a 14% national withholding assumption on ex-date; Stock Splits apply their recorded ratio to held shares; no reinvestment; individual final/local tax liability is not modeled",
            "intraday": "source is daily OHLCV. No intraday path or fill order is inferred from high/low touch. Virtual event times are illustrative only.",
        },
        "bars": [
            {"symbol": ticker, "displayName": SHORT_NAMES.get(ticker, ticker.replace(".KS", "")), **row}
            for ticker in market_tickers
            for row in bars[ticker]
        ],
        "showcasePortfolio": portfolio,
        "backtest": backtest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", required=True, type=Path)
    parser.add_argument("--data-receipt", required=True, type=Path)
    parser.add_argument("--universe", required=True, type=Path)
    parser.add_argument("--calendar", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-collected-at", required=True)
    parser.add_argument("--permission-attested-on", required=True)
    args = parser.parse_args()
    output = build(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"MARS_DEMO_FIXTURE=BUILT seed={output['seedVersion']} source_sha256={output['source']['sourceSha256']}")
    print(f"SHOWCASE_RETURN_BPS={output['showcasePortfolio']['final']['returnBps']}")
    print(f"BACKTEST_RETURN_BPS={output['backtest']['returnBps']} NO_ACTION_DAYS={output['backtest']['noActionDays']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
