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
SELECTION_END_DATE = "2026-09-18"
END_DATE = "2026-09-29"
SEED_VERSION = "mars-demo-2026-09-30.2"
INITIAL_CASH_KRW = 10_000_000
COMMISSION_BPS = 1.5
SELL_TAX_BPS = 20.0
DIVIDEND_WITHHOLDING_TAX_BPS = 1_400.0
SLIPPAGE_BPS = 10.0
SHORT_NAMES = {
    "005930.KS": "삼성전자",
    "000660.KS": "SK하이닉스",
    "005935.KS": "삼성전자우",
    "402340.KS": "SK스퀘어",
    "009150.KS": "삼성전기",
    "373220.KS": "LG에너지솔루션",
    "005380.KS": "현대차",
    "207940.KS": "삼성바이오로직스",
    "032830.KS": "삼성생명",
    "105560.KS": "KB금융",
    "028260.KS": "삼성물산",
    "012450.KS": "한화에어로스페이스",
    "034020.KS": "두산에너빌리티",
    "055550.KS": "신한지주",
    "000270.KS": "기아",
    "329180.KS": "HD현대중공업",
    "006400.KS": "삼성SDI",
    "068270.KS": "셀트리온",
    "012330.KS": "현대모비스",
    "034730.KS": "SK",
    "086790.KS": "하나금융지주",
    "035420.KS": "NAVER",
    "066570.KS": "LG전자",
    "010120.KS": "LS ELECTRIC",
    "000810.KS": "삼성화재",
    "298040.KS": "효성첨단소재",
    "267260.KS": "HD현대일렉트릭",
    "010130.KS": "고려아연",
    "042660.KS": "한화오션",
    "005490.KS": "POSCO홀딩스",
    "132030.KS": "KODEX 골드선물(H)",
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
    close_returns: list[tuple[str, float]] = []
    for ticker in symbols:
        rows = bars.get(ticker, [])
        if len(rows) < 2 or rows[0]["date"] != START_DATE or rows[-1]["date"] != END_DATE:
            raise ValueError(f"incomplete historical rows for {ticker}")
        selection_close = next((row["close"] for row in rows if row["date"] == SELECTION_END_DATE), None)
        if selection_close is None:
            raise ValueError(f"selection-date close missing for {ticker}")
        close_returns.append((ticker, selection_close / rows[0]["close"] - 1))
    # The selection uses the completed period. It must never be described as a forecast.
    selected = [ticker for ticker, _ in sorted(close_returns, key=lambda value: (-value[1], value[0]))[:6]]
    daily_dates = [
        day["date"] for day in calendar["sessions"] if START_DATE <= day["date"] <= END_DATE
    ]
    if len(daily_dates) != 29 or daily_dates[23] != SELECTION_END_DATE or any([row["date"] for row in bars[ticker]] != daily_dates for ticker in selected):
        raise ValueError("selected price bars do not match the pinned XKRX calendar")
    per_day = {ticker: {row["date"]: row for row in rows} for ticker, rows in bars.items()}

    # One order per chosen session. The completed-period selection is post hoc;
    # the fixed schedule keeps both profitable and losing sales rather than
    # optimizing every exit after the fact.
    buy_sessions = {0: selected[0], 1: selected[1], 2: selected[2], 3: selected[3], 4: selected[4], 13: selected[5]}
    sell_sessions = {
        9: (selected[4], 1.0),
        15: (selected[1], 0.5),
        17: (selected[2], 0.4),
        19: (selected[3], 0.5),
        23: (selected[5], 1.0),
    }
    events: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    holdings = {ticker: 0 for ticker in selected}
    cost_bases = {ticker: 0 for ticker in selected}
    gross_bases = {ticker: 0 for ticker in selected}
    marks: dict[str, int] = {}
    dividend_events: list[dict[str, Any]] = []
    cash = INITIAL_CASH_KRW
    realized_pnl = 0
    trade_number = 0

    def execute_trade(day: str, ticker: str, side: str, quantity: int, price_field: str) -> None:
        nonlocal cash, realized_pnl, trade_number
        if quantity <= 0:
            raise ValueError("showcase trade quantity must be positive")
        reference = per_day[ticker][day][price_field]
        price = net_fill_price(reference, side)
        gross = quantity * price
        commission = fee(gross, COMMISSION_BPS)
        tax = fee(gross, SELL_TAX_BPS) if side == "SELL" else 0
        cash_before = cash
        position_before = holdings[ticker]
        realized = None
        realized_gross = None
        if side == "BUY":
            if gross + commission > cash:
                raise ValueError("showcase entry exceeds available cash")
            cash -= gross + commission
            holdings[ticker] += quantity
            cost_bases[ticker] += gross + commission
            gross_bases[ticker] += gross
        else:
            if quantity > position_before:
                raise ValueError("showcase sale exceeds held shares")
            sold_cost = cost_bases[ticker] if quantity == position_before else round_krw(cost_bases[ticker] * quantity / position_before)
            sold_gross = gross_bases[ticker] if quantity == position_before else round_krw(gross_bases[ticker] * quantity / position_before)
            net = gross - commission - tax
            realized = net - sold_cost
            realized_gross = gross - sold_gross
            cash += net
            realized_pnl += realized
            holdings[ticker] -= quantity
            cost_bases[ticker] -= sold_cost
            gross_bases[ticker] -= sold_gross

        trade_number += 1
        order_id = f"showcase-order-{trade_number:02d}"
        decision_id = f"dec_showcase_{trade_number:08d}"
        decision_time = "15:15:00" if price_field == "close" else "08:50:00"
        order_time = "15:20:00" if price_field == "close" else "09:00:00"
        fill_time = "15:29:00" if price_field == "close" else "09:01:00"
        events.extend([
            {
                "id": decision_id,
                "atKst": f"{day}T{decision_time}+09:00",
                "type": "DECISION_RECORDED",
                "orderId": order_id,
                "symbol": ticker,
                "side": side,
                "quantity": quantity,
                "estimatedAmountKrw": gross,
                "cashBeforeKrw": cash_before,
                "positionBefore": position_before,
                "action": "ALLOW",
                "reason": "기록된 수량과 잔고 범위 안에서 주문",
                "sourceDate": day,
                "simulationOnly": True,
            },
            {
                "id": order_id,
                "atKst": f"{day}T{order_time}+09:00",
                "type": "ORDER_CREATED",
                "decisionId": decision_id,
                "symbol": ticker,
                "side": side,
                "quantity": quantity,
                "status": "SIMULATED_ACCEPTED",
                "reason": "사후 구성 사례의 고정 거래 일정",
                "sourceDate": day,
                "simulationOnly": True,
            },
            {
                "id": f"showcase-fill-{trade_number:02d}",
                "orderId": order_id,
                "atKst": f"{day}T{fill_time}+09:00",
                "type": "FILL",
                "symbol": ticker,
                "side": side,
                "quantity": quantity,
                "price": price,
                "referencePrice": reference,
                "grossAmount": gross,
                "commission": commission,
                "transactionTax": tax,
                "sourceDate": day,
                "priceField": f"daily {price_field} {'plus' if side == 'BUY' else 'less'} 10bp assumed slippage",
                "simulationOnly": True,
                **({"realizedPnl": realized, "realizedGrossPnl": realized_gross} if side == "SELL" else {}),
            },
        ])

    for day_index, day in enumerate(daily_dates):
        for ticker in selected:
            row = per_day[ticker][day]
            ratio = row["splitRatio"]
            if ratio > 0 and holdings[ticker] > 0:
                before = holdings[ticker]
                holdings[ticker] = round_krw(before * ratio)
                events.append({
                    "id": f"split-{ticker}-{day}",
                    "atKst": f"{day}T08:30:00+09:00",
                    "type": "SPLIT",
                    "symbol": ticker,
                    "ratio": ratio,
                    "quantityBefore": before,
                    "quantityAfter": holdings[ticker],
                    "simulationOnly": True,
                })
            dividend = row["dividendPerShare"]
            if dividend > 0 and holdings[ticker] > 0:
                gross_dividend = round_krw(dividend * holdings[ticker])
                withholding = fee(gross_dividend, DIVIDEND_WITHHOLDING_TAX_BPS)
                amount = gross_dividend - withholding
                cash += amount
                dividend_event = {
                    "id": f"dividend-{ticker}-{day}",
                    "atKst": f"{day}T08:45:00+09:00",
                    "type": "DIVIDEND",
                    "symbol": ticker,
                    "quantity": holdings[ticker],
                    "cashPerShare": dividend,
                    "grossAmount": gross_dividend,
                    "withholdingTax": withholding,
                    "cashAmount": amount,
                    "sourceDate": day,
                    "simulationOnly": True,
                }
                dividend_events.append(dividend_event)
                events.append(dividend_event)

        if day_index in buy_sessions:
            ticker = buy_sessions[day_index]
            price = net_fill_price(per_day[ticker][day]["open"], "BUY")
            quantity = max(1, int((INITIAL_CASH_KRW * 0.16) // price))
            execute_trade(day, ticker, "BUY", quantity, "open")
        elif day_index in sell_sessions:
            ticker, fraction = sell_sessions[day_index]
            held = holdings[ticker]
            quantity = held if fraction == 1.0 else max(1, min(held - 1, round_krw(held * fraction)))
            execute_trade(day, ticker, "SELL", quantity, "close" if day == SELECTION_END_DATE else "open")
        else:
            events.append({
                "id": f"showcase-no-order-{day}",
                "atKst": f"{day}T09:30:00+09:00",
                "type": "NO_ACTION",
                "reason": "고정 거래 일정에 주문 없음",
                "sourceDate": day,
                "simulationOnly": True,
            })

        for ticker in selected:
            marks[ticker] = per_day[ticker][day]["close"]
        equity = cash + sum(holdings[ticker] * marks[ticker] for ticker in selected)
        events.append({
            "id": f"market-mark-{day}",
            "atKst": f"{day}T15:30:00+09:00",
            "type": "MARKET_MARK",
            "prices": {ticker: marks[ticker] for ticker in selected},
            "sourceDate": day,
            "simulationOnly": True,
        })
        daily.append({
            "date": day,
            "cash": cash,
            "equity": equity,
            "realizedPnl": realized_pnl,
            "holdings": {
                ticker: {"quantity": holdings[ticker], "close": marks[ticker]}
                for ticker in selected if holdings[ticker] > 0
            },
            "actionCount": sum(
                1 for event in events if event.get("type") in {"FILL", "DIVIDEND"} and event["atKst"][:10] == day
            ),
        })

    events.sort(key=lambda event: (event["atKst"], event["id"]))
    final = daily[-1]
    return {
        "id": "posthoc-showcase-2026-08-18-to-2026-09-29-v3",
        "label": "사후 구성 가상 사례",
        "sourceRange": {"start": START_DATE, "end": END_DATE},
        "selectionAsOf": SELECTION_END_DATE,
        "selectionRule": "2026-08-18~2026-09-18 종가 수익률 상위 6개를 결과 확인 후 선정한 사후 구성 사례. 거래 일정은 9월 18일까지 고정하고 보유분만 9월 29일까지 평가; 사전 예측력·실운용 성과가 아님",
        "initialCapital": INITIAL_CASH_KRW,
        "externalNetFlows": 0,
        "selectedSymbols": selected,
        "events": events,
        "dailyReceipt": daily,
        "final": {
            "cash": final["cash"],
            "equity": final["equity"],
            "returnBps": round_krw((final["equity"] / INITIAL_CASH_KRW - 1) * 10_000),
            "netPnlKrw": final["equity"] - INITIAL_CASH_KRW,
            "realizedPnl": final["realizedPnl"],
            "grossRealizedPnl": sum(event["realizedGrossPnl"] for event in events if event["type"] == "FILL" and event["side"] == "SELL"),
            "unrealizedPnl": final["equity"] - final["cash"] - sum(cost_bases[ticker] for ticker in selected if holdings[ticker] > 0),
            "commissionKrw": sum(event["commission"] for event in events if event["type"] == "FILL"),
            "sellTaxKrw": sum(event["transactionTax"] for event in events if event["type"] == "FILL"),
            "slippageKrw": sum(abs(event["grossAmount"] - event["referencePrice"] * event["quantity"]) for event in events if event["type"] == "FILL"),
            "dividendCash": sum(event["cashAmount"] for event in dividend_events),
            "dividendGross": sum(event["grossAmount"] for event in dividend_events),
            "dividendWithholding": sum(event["withholdingTax"] for event in dividend_events),
            "orderCount": trade_number,
            "fillCount": trade_number,
            "winningSaleCount": sum(event["realizedPnl"] > 0 for event in events if event["type"] == "FILL" and event["side"] == "SELL"),
            "losingSaleCount": sum(event["realizedPnl"] < 0 for event in events if event["type"] == "FILL" and event["side"] == "SELL"),
            "noOrderDays": sum(event["type"] == "NO_ACTION" for event in events),
            "closedPositionCount": sum(holdings[ticker] == 0 for ticker in selected),
            "openPositionCount": sum(holdings[ticker] > 0 for ticker in selected),
            "openPositions": final["holdings"],
        },
        "benchmark": {
            "ticker": "^KS11",
            "startClose": per_day["^KS11"][START_DATE]["close"],
            "endClose": per_day["^KS11"][END_DATE]["close"],
            "returnBps": round_krw((per_day["^KS11"][END_DATE]["close"] / per_day["^KS11"][START_DATE]["close"] - 1) * 10_000),
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
    if receipt.get("sourceCollectedAt") and receipt["sourceCollectedAt"] != args.source_collected_at:
        raise ValueError("source collection timestamp differs from receipt")
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
        "seedVersion": SEED_VERSION,
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
