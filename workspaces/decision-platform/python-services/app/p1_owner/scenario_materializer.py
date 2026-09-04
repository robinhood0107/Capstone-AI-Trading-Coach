"""Build owner-side Baseline, Guide, and Strict dashboard projections."""

from __future__ import annotations

import argparse
import hashlib
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final, cast
from zoneinfo import ZoneInfo

import numpy as np
import psycopg

from app.data._shared.canonical_json import canonical_json_bytes
from app.p1_owner.assets import GOLDEN_MANIFEST, _validate_repository_schema
from app.p1_owner.daily_inference import DailyInferenceError, _features_and_rule
from app.p1_owner.importer import validate_artifact_bundle
from app.p1_owner.inference import ReturnInferenceError, ReturnInferenceModel

_OWNER: Final = "usr_demo_user"
_INITIAL_CAPITAL: Final = 10_000_000
_SYMBOL_CAPITAL: Final = _INITIAL_CAPITAL // 31
_UNALLOCATED_CASH: Final = _INITIAL_CAPITAL - (_SYMBOL_CAPITAL * 31)
_ROUND_TRIP_COST: Final = 0.0035
_PERIODS_PER_YEAR: Final = 252
_KST: Final = ZoneInfo("Asia/Seoul")
_GOLD_SYMBOLS: Final = frozenset({"132030"})
_IMPLEMENTATION_ID: Final = "owner-scenario-replay.v1.2"


class ScenarioMaterializationError(RuntimeError):
    """The bounded owner replay could not produce a complete projection."""


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    entry_session: date
    exit_session: date
    quantity: int
    entry_price: int
    exit_price: int


@dataclass(frozen=True, slots=True)
class ReplayResult:
    curve: list[tuple[date, float]]
    trades: list[tuple[float, float]]
    violation_count: int = 0


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _load_database_input(dsn: str) -> dict[str, Any]:
    try:
        with psycopg.connect(dsn, connect_timeout=5) as connection, connection.cursor() as cursor:
            cursor.execute("select read_owner_scenario_materialization_inputs_v1(%s)", (_OWNER,))
            row = cursor.fetchone()
    except psycopg.Error as error:
        raise ScenarioMaterializationError("SCENARIO_INPUT_UNAVAILABLE") from error
    if row is None or not isinstance(row[0], dict):
        raise ScenarioMaterializationError("SCENARIO_INPUT_INVALID")
    value = cast(dict[str, Any], row[0])
    if value.get("contractId") != "owner-scenario-materialization-input.v1":
        raise ScenarioMaterializationError("SCENARIO_INPUT_INVALID")
    return value


def _bars_by_symbol(value: dict[str, Any]) -> tuple[list[date], dict[str, list[dict[str, Any]]]]:
    raw_sessions = value.get("sessions")
    raw_bars = value.get("bars")
    if not isinstance(raw_sessions, list) or not isinstance(raw_bars, list):
        raise ScenarioMaterializationError("SCENARIO_BARS_INVALID")
    sessions = [date.fromisoformat(str(item)) for item in raw_sessions]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in raw_bars:
        if not isinstance(raw, dict):
            raise ScenarioMaterializationError("SCENARIO_BARS_INVALID")
        item = cast(dict[str, Any], raw)
        symbol = str(item.get("symbol", ""))
        grouped[symbol].append(
            {
                "sessionDate": str(item["sessionDate"]),
                "open": int(item["open"]),
                "high": int(item["high"]),
                "low": int(item["low"]),
                "close": int(item["close"]),
                "volume": int(item["volume"]),
            }
        )
    if (
        len(sessions) != 104
        or sessions != sorted(set(sessions))
        or len(grouped) != 31
        or any(len(rows) != 104 for rows in grouped.values())
    ):
        raise ScenarioMaterializationError("SCENARIO_BARS_NOT_EXACT_31_BY_104")
    expected_sessions = [session.isoformat() for session in sessions]
    for rows in grouped.values():
        rows.sort(key=lambda item: item["sessionDate"])
        if [str(item["sessionDate"]) for item in rows] != expected_sessions:
            raise ScenarioMaterializationError("SCENARIO_BARS_SESSION_MISMATCH")
    return sessions, dict(grouped)


def _baseline_replay(sessions: list[date], bars: dict[str, list[dict[str, Any]]]) -> ReplayResult:
    aggregate = {session: float(_UNALLOCATED_CASH) for session in sessions}
    realized: list[tuple[float, float]] = []
    for symbol in sorted(bars):
        cash = float(_SYMBOL_CAPITAL)
        shares = 0
        entry_price: float | None = None
        rows = bars[symbol]
        for index, row in enumerate(rows):
            price = float(row["close"])
            signal = "HOLD"
            try:
                _, signal = _features_and_rule(rows[: index + 1], str(row["sessionDate"]))
            except DailyInferenceError as error:
                if not str(error).startswith("DAILY_INFERENCE_HISTORY_TOO_SHORT"):
                    raise ScenarioMaterializationError("SCENARIO_BASELINE_SIGNAL_INVALID") from error
            if signal == "BUY" and shares == 0 and index < len(rows) - 1:
                quantity = int(cash // (price * (1.0 + _ROUND_TRIP_COST / 2.0)))
                if quantity > 0:
                    cash -= quantity * price * (1.0 + _ROUND_TRIP_COST / 2.0)
                    shares = quantity
                    entry_price = price
            elif signal == "SELL" and shares > 0:
                cash += shares * price * (1.0 - _ROUND_TRIP_COST / 2.0)
                if entry_price is not None:
                    realized.append((entry_price, price))
                shares = 0
                entry_price = None
            if index == len(rows) - 1 and shares > 0:
                cash += shares * price * (1.0 - _ROUND_TRIP_COST / 2.0)
                if entry_price is not None:
                    realized.append((entry_price, price))
                shares = 0
                entry_price = None
            aggregate[sessions[index]] += cash + shares * price
    return ReplayResult(sorted(aggregate.items()), realized)


def _guide_replay(
    sessions: list[date],
    bars: dict[str, list[dict[str, Any]]],
    model: ReturnInferenceModel,
) -> tuple[ReplayResult, list[Trade]]:
    if set(model.symbols) != set(bars):
        raise ScenarioMaterializationError("SCENARIO_GUIDE_SYMBOLS_INVALID")
    aggregate = {session: float(_UNALLOCATED_CASH) for session in sessions}
    trades: list[Trade] = []
    for symbol in sorted(bars):
        cash = float(_SYMBOL_CAPITAL)
        shares = 0
        entry_session: date | None = None
        entry_price: int | None = None
        rows = bars[symbol]
        for index, row in enumerate(rows):
            price = int(row["close"])
            signal = "HOLD"
            try:
                features, rule_signal = _features_and_rule(rows[: index + 1], str(row["sessionDate"]))
                prediction = model._infer_symbol(symbol, float(price), np.asarray(features, dtype=np.float64))
                if rule_signal == "BUY" and prediction.signal != "SELL":
                    signal = "BUY"
                elif rule_signal == "SELL" and prediction.signal == "SELL":
                    signal = "SELL"
            except DailyInferenceError as error:
                if not str(error).startswith("DAILY_INFERENCE_HISTORY_TOO_SHORT"):
                    raise ScenarioMaterializationError("SCENARIO_GUIDE_SIGNAL_INVALID") from error
            except ReturnInferenceError as error:
                raise ScenarioMaterializationError("SCENARIO_GUIDE_INFERENCE_INVALID") from error
            if signal == "BUY" and shares == 0 and index < len(rows) - 1:
                quantity = int(cash // (price * (1.0 + _ROUND_TRIP_COST / 2.0)))
                if quantity > 0:
                    cash -= quantity * price * (1.0 + _ROUND_TRIP_COST / 2.0)
                    shares = quantity
                    entry_session = sessions[index]
                    entry_price = price
            elif signal == "SELL" and shares > 0 and entry_session is not None and entry_price is not None:
                cash += shares * price * (1.0 - _ROUND_TRIP_COST / 2.0)
                trades.append(Trade(symbol, entry_session, sessions[index], shares, entry_price, price))
                shares = 0
                entry_session = None
                entry_price = None
            if index == len(rows) - 1 and shares > 0 and entry_session is not None and entry_price is not None:
                cash += shares * price * (1.0 - _ROUND_TRIP_COST / 2.0)
                trades.append(Trade(symbol, entry_session, sessions[index], shares, entry_price, price))
                shares = 0
                entry_session = None
                entry_price = None
            aggregate[sessions[index]] += cash + shares * price
    return ReplayResult(
        sorted(aggregate.items()),
        [(float(item.entry_price), float(item.exit_price)) for item in trades],
    ), trades


def _thresholds(rules: object) -> dict[str, float]:
    if not isinstance(rules, list):
        raise ScenarioMaterializationError("SCENARIO_RULES_INVALID")
    values: dict[str, float] = {}
    for raw in rules:
        if not isinstance(raw, dict) or raw.get("enabled") is not True:
            continue
        rule_id = str(raw.get("ruleId", ""))
        threshold = raw.get("threshold")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise ScenarioMaterializationError("SCENARIO_RULES_INVALID")
        values[rule_id] = float(threshold)
    required = {
        "max_position_per_asset",
        "max_gold_etf_etn_weight",
        "max_single_order_amount",
        "daily_loss_guard",
        "mdd_guard",
        "max_daily_orders",
    }
    if not required.issubset(values):
        raise ScenarioMaterializationError("SCENARIO_RULES_INCOMPLETE")
    return values


def _strict_replay(
    sessions: list[date],
    bars: dict[str, list[dict[str, Any]]],
    trades: list[Trade],
    rules: object,
) -> ReplayResult:
    thresholds = _thresholds(rules)
    price = {
        (symbol, date.fromisoformat(str(row["sessionDate"]))): float(row["close"])
        for symbol, rows in bars.items()
        for row in rows
    }
    entries: dict[date, list[Trade]] = defaultdict(list)
    exits: dict[date, list[Trade]] = defaultdict(list)
    for trade in trades:
        entries[trade.entry_session].append(trade)
        exits[trade.exit_session].append(trade)
    cash = float(_INITIAL_CAPITAL)
    holdings: dict[str, tuple[int, float]] = {}
    accepted: set[tuple[str, date]] = set()
    curve: list[tuple[date, float]] = []
    realized: list[tuple[float, float]] = []
    peak = float(_INITIAL_CAPITAL)
    previous_equity = float(_INITIAL_CAPITAL)
    violations = 0

    def equity_on(session: date) -> float:
        return cash + sum(quantity * price[(symbol, session)] for symbol, (quantity, _) in holdings.items())

    for session in sessions:
        for trade in sorted(exits.get(session, []), key=lambda item: item.symbol):
            key = (trade.symbol, trade.entry_session)
            if key not in accepted or trade.symbol not in holdings:
                continue
            quantity, entry_price = holdings.pop(trade.symbol)
            cash += quantity * trade.exit_price * (1.0 - _ROUND_TRIP_COST / 2.0)
            realized.append((entry_price, float(trade.exit_price)))
        daily_entries = 0
        for trade in sorted(entries.get(session, []), key=lambda item: item.symbol):
            current_equity = equity_on(session)
            drawdown = current_equity / peak - 1.0 if peak > 0 else -1.0
            daily_return = current_equity / previous_equity - 1.0 if previous_equity > 0 else -1.0
            notional = float(trade.quantity * trade.entry_price)
            projected_gold = sum(
                quantity * price[(symbol, session)]
                for symbol, (quantity, _) in holdings.items()
                if symbol in _GOLD_SYMBOLS
            ) + (notional if trade.symbol in _GOLD_SYMBOLS else 0.0)
            blocked = (
                notional > thresholds["max_single_order_amount"]
                or notional / current_equity > thresholds["max_position_per_asset"]
                or projected_gold / current_equity > thresholds["max_gold_etf_etn_weight"]
                or daily_return < thresholds["daily_loss_guard"]
                or drawdown < thresholds["mdd_guard"]
                or daily_entries >= int(thresholds["max_daily_orders"])
                or trade.symbol in holdings
                or cash < notional * (1.0 + _ROUND_TRIP_COST / 2.0)
            )
            if blocked:
                violations += 1
                continue
            cash -= notional * (1.0 + _ROUND_TRIP_COST / 2.0)
            holdings[trade.symbol] = (trade.quantity, float(trade.entry_price))
            accepted.add((trade.symbol, trade.entry_session))
            daily_entries += 1
        equity = equity_on(session)
        peak = max(peak, equity)
        previous_equity = equity
        curve.append((session, equity))
    return ReplayResult(curve, realized, violations)


def _metrics(result: ReplayResult) -> dict[str, float | None]:
    values = np.asarray([value for _, value in result.curve], dtype=np.float64)
    if values.size < 2 or np.any(~np.isfinite(values)) or np.any(values <= 0):
        raise ScenarioMaterializationError("SCENARIO_CURVE_INVALID")
    returns = values[1:] / values[:-1] - 1.0
    peaks = np.maximum.accumulate(values)
    std = float(np.std(returns, ddof=1))
    downside = np.minimum(returns, 0.0)
    downside_deviation = float(np.sqrt(np.mean(np.square(downside))))
    var95 = float(np.quantile(returns, 0.05, method="linear"))
    tail = returns[returns <= var95]
    years = (values.size - 1) / _PERIODS_PER_YEAR
    wins = sum(exit_price > entry_price for entry_price, exit_price in result.trades)
    return {
        "cagr": float(math.expm1(math.log(values[-1] / values[0]) / years)) if years > 0 else None,
        "mdd": float(np.min(values / peaks - 1.0)),
        "sharpe": float(np.mean(returns) / std * math.sqrt(_PERIODS_PER_YEAR)) if std > 0 else None,
        "sortino": (
            float(np.mean(returns) / downside_deviation * math.sqrt(_PERIODS_PER_YEAR))
            if downside_deviation > 0
            else None
        ),
        "var95": var95,
        "cvar95": float(np.mean(tail)) if tail.size else None,
        "netReturn": float(values[-1] / values[0] - 1.0),
        "tradeCount": float(len(result.trades)),
        "winRate": float(wins / len(result.trades)) if result.trades else None,
        "principleViolationCount": float(result.violation_count),
    }


def _curve_rows(result: ReplayResult) -> list[dict[str, object]]:
    return [
        {"at": datetime.combine(session, time.min, UTC).isoformat().replace("+00:00", "Z"), "value": value}
        for session, value in result.curve
    ]


def _heatmap(result: ReplayResult) -> list[dict[str, object]]:
    by_month: dict[str, list[float]] = defaultdict(list)
    for session, value in result.curve:
        by_month[session.strftime("%Y-%m")].append(value)
    return [
        {"month": month, "return": values[-1] / values[0] - 1.0}
        for month, values in sorted(by_month.items())
        if len(values) >= 2
    ]


def _envelope(request_id: str, as_of: datetime, view: dict[str, Any]) -> dict[str, Any]:
    stamp = as_of.astimezone(UTC).isoformat().replace("+00:00", "Z")
    fresh = (as_of + timedelta(days=1)).astimezone(UTC).isoformat().replace("+00:00", "Z")
    return {
        "success": True,
        "requestId": request_id,
        "data": {
            "viewState": "READY",
            "asOf": stamp,
            "freshUntil": fresh,
            "evidenceMode": "REAL_ARTIFACT",
            "performanceClaimAllowed": False,
            "view": view,
        },
        "warnings": [],
        "error": None,
    }


def materialize(bundle_root: Path, dsn: str) -> dict[str, object]:
    manifest_bytes = (bundle_root / GOLDEN_MANIFEST).read_bytes()
    validated = validate_artifact_bundle(
        bundle_root=bundle_root,
        expected_manifest_sha256=_sha(manifest_bytes),
    )
    db_input = _load_database_input(dsn)
    if db_input.get("bundleSha256") != validated.bundle_sha256:
        raise ScenarioMaterializationError("SCENARIO_BUNDLE_POINTER_MISMATCH")
    sessions, bars = _bars_by_symbol(db_input)
    inference_model = ReturnInferenceModel.load(
        bundle_root=bundle_root,
        manifest_sha256=validated.manifest_sha256,
        allow_synthetic=False,
    )
    baseline = _baseline_replay(sessions, bars)
    guide, guide_trades = _guide_replay(sessions, bars, inference_model)
    strict = _strict_replay(sessions, bars, guide_trades, db_input.get("rules"))
    results = {"Baseline": baseline, "Guide": guide, "Strict": strict}
    metrics = {name: _metrics(result) for name, result in results.items()}
    identity = _sha(
        canonical_json_bytes(
            {
                "contractId": "owner-scenario-replay.v1",
                "implementationId": _IMPLEMENTATION_ID,
                "bundleSha256": validated.bundle_sha256,
                "bars": db_input["bars"],
                "rules": db_input["rules"],
                "costBps": 35,
            }
        )
    )
    run_id = f"run_owner_{identity[:24]}"
    artifact_id = f"artifact_owner_{identity[:24]}"
    request_id = f"req_owner_{identity[:24]}"
    latest_session = sessions[-1]
    as_of = datetime.combine(latest_session, time(15, 30), _KST).astimezone(UTC)
    dashboard_metrics = {key: metrics[key] for key in results}
    model_view = {
        "runId": run_id,
        "models": [
            {"modelId": "BASELINE", "status": "AVAILABLE", "metrics": {key: metrics["Baseline"][key] for key in ("cagr", "mdd", "sharpe", "sortino", "var95", "cvar95")}},
            {"modelId": "LSTM", "status": "AVAILABLE", "metrics": {key: metrics["Guide"][key] for key in ("cagr", "mdd", "sharpe", "sortino", "var95", "cvar95")}},
        ],
        "timeline": _curve_rows(guide),
        "sourceRunIds": [validated.run_id],
    }
    strategies = [
        {
            "strategy": name,
            "metrics": {key: dashboard_metrics[name][key] for key in ("cagr", "mdd", "sharpe", "sortino", "var95", "cvar95")},
            "curve": _curve_rows(results[name]),
        }
        for name in ("Baseline", "Guide", "Strict")
    ]
    metric_cards = [
        {"metric": f"{name}.{metric}", "value": metrics[name][metric]}
        for name in ("Baseline", "Guide", "Strict")
        for metric in ("netReturn", "tradeCount", "winRate")
    ] + [{"metric": "Strict.principleViolationCount", "value": metrics["Strict"]["principleViolationCount"]}]
    backtest_view = {
        "runId": run_id,
        "fixtureClass": "REAL_ARTIFACT",
        "strategies": strategies,
        "heatmap": _heatmap(strict),
        "metricCards": metric_cards,
        "projectionHash": f"sha256:{identity}",
    }
    model_projection = _envelope(request_id, as_of, model_view)
    backtest = _envelope(request_id, as_of, backtest_view)
    _validate_repository_schema("contracts/schemas/dashboard-model-evaluation.v1.schema.json", model_projection)
    _validate_repository_schema("contracts/schemas/dashboard-backtest.v1.schema.json", backtest)
    model_text = canonical_json_bytes(model_projection).decode()
    backtest_text = canonical_json_bytes(backtest).decode()
    try:
        with psycopg.connect(dsn, connect_timeout=5) as connection, connection.cursor() as cursor:
            cursor.execute(
                "select publish_owner_scenario_dashboard_v1(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    _OWNER,
                    validated.bundle_sha256,
                    artifact_id,
                    run_id,
                    model_text,
                    f"sha256:{_sha(model_text.encode())}",
                    backtest_text,
                    f"sha256:{_sha(backtest_text.encode())}",
                    as_of,
                    as_of + timedelta(days=1),
                ),
            )
            row = cursor.fetchone()
            connection.commit()
    except psycopg.Error as error:
        raise ScenarioMaterializationError("SCENARIO_PUBLISH_FAILED") from error
    return {
        "status": str(row[0]) if row else "UNKNOWN",
        "runId": run_id,
        "sessions": len(sessions),
        "symbols": len(bars),
        "providerCalls": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    args = parser.parse_args(argv)
    dsn = os.environ.get("P1_ARTIFACT_IMPORT_DATABASE_DSN", "").strip()
    if not dsn:
        raise SystemExit("OWNER_SCENARIO_MATERIALIZATION=FAILED_DSN")
    try:
        result = materialize(args.bundle_root, dsn)
    except (ScenarioMaterializationError, OSError, ValueError) as error:
        print(f"OWNER_SCENARIO_MATERIALIZATION=FAILED_{error}")
        return 1
    print(canonical_json_bytes(result).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
