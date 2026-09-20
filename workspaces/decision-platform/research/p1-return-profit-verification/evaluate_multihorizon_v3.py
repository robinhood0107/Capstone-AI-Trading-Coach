"""LSTM 5/20 v3 후보의 예측·운용 scorecard를 분리해 계산한다."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from walk_forward import FEATURES
from walk_forward_multihorizon import CACHE, HORIZONS, horizon_frame

ROOT = Path(__file__).parent
REPORTS = ROOT / "reports"
COST = 0.0035
TOP_K = 5
BOOTSTRAPS = 2_000
RANDOM_CONTROLS = 100
SEED = 20260909


def expected_rows(history: pd.DataFrame, horizon: int) -> int:
    count = 0
    target = f"TargetLogRet{horizon}"
    target_date = f"TargetDate{horizon}"
    for test_year in range(2005, int(history["Date"].max().year) + 1):
        start = pd.Timestamp(f"{test_year}-01-01")
        end = pd.Timestamp(f"{test_year + 1}-01-01")
        for _, source in history.groupby("ticker", sort=False):
            featured = horizon_frame(source.copy(), horizon).dropna(
                subset=[*FEATURES, target, target_date]
            )
            count += int(
                (
                    (featured["Date"] >= start)
                    & (featured["Date"] < end)
                    & (featured[target_date] < end)
                ).sum()
            )
    return count


def block_ci(frame: pd.DataFrame, rng: np.random.Generator) -> tuple[float, float]:
    yearly = (
        frame.assign(
            delta=(frame["predictionSimpleReturn"] - frame["actualSimpleReturn"]).abs()
            - frame["actualSimpleReturn"].abs()
        )
        .groupby("testYear")["delta"]
        .mean()
    )
    values = yearly.to_numpy(dtype=float)
    samples = np.asarray(
        [rng.choice(values, size=len(values), replace=True).mean() for _ in range(BOOTSTRAPS)]
    )
    return float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))


def drawdown(returns: np.ndarray) -> float:
    equity = np.cumprod(1 + returns)
    peaks = np.maximum.accumulate(np.r_[1.0, equity])[:-1]
    return float(np.min(equity / peaks - 1)) if len(equity) else 0.0


def ratio(returns: np.ndarray, *, downside: bool = False, periods: float) -> float:
    if len(returns) < 2:
        return 0.0
    denominator = (
        np.sqrt(np.mean(np.minimum(returns, 0) ** 2)) if downside else np.std(returns, ddof=1)
    )
    return float(np.mean(returns) / denominator * np.sqrt(periods)) if denominator > 0 else 0.0


def operation_scorecard(
    frame: pd.DataFrame, horizon: int, rng: np.random.Generator
) -> dict[str, object]:
    dates = sorted(frame["date"].unique())[::horizon]
    selected_returns: list[float] = []
    benchmark_returns: list[float] = []
    selections: list[frozenset[str]] = []
    random_paths = [[] for _ in range(RANDOM_CONTROLS)]
    rank_ics: list[float] = []
    spreads: list[float] = []
    years: list[int] = []
    for current_date in dates:
        cross = frame[frame["date"] == current_date].dropna(
            subset=["predictionSimpleReturn", "actualSimpleReturn"]
        )
        if len(cross) < TOP_K:
            continue
        ranked = cross.sort_values(["predictionSimpleReturn", "ticker"], ascending=[False, True])
        chosen = ranked.head(TOP_K)
        selected_returns.append(float(chosen["actualSimpleReturn"].mean() - COST))
        benchmark_returns.append(float(cross["actualSimpleReturn"].mean() - COST))
        selections.append(frozenset(chosen["ticker"].astype(str)))
        years.append(int(pd.Timestamp(current_date).year))
        rank_ics.append(
            float(
                cross["predictionSimpleReturn"].corr(cross["actualSimpleReturn"], method="spearman")
            )
        )
        spreads.append(
            float(
                chosen["actualSimpleReturn"].mean()
                - ranked.tail(TOP_K)["actualSimpleReturn"].mean()
            )
        )
        actual = cross["actualSimpleReturn"].to_numpy(dtype=float)
        for path in random_paths:
            path.append(float(rng.choice(actual, size=TOP_K, replace=False).mean() - COST))
    returns = np.asarray(selected_returns)
    benchmark = np.asarray(benchmark_returns)
    periods = 252 / horizon
    turnovers = [
        1 - len(before & after) / len(before | after)
        for before, after in zip(selections, selections[1:], strict=False)
        if before | after
    ]
    random_totals = [float(np.prod(1 + np.asarray(path)) - 1) for path in random_paths]
    yearly = pd.DataFrame({"year": years, "strategy": returns, "benchmark": benchmark})
    bear = []
    for year, group in yearly.groupby("year"):
        benchmark_total = float(np.prod(1 + group["benchmark"].to_numpy()) - 1)
        if benchmark_total < 0:
            bear.append(
                {
                    "year": int(year),
                    "excessPositive": bool(
                        np.prod(1 + group["strategy"].to_numpy())
                        > np.prod(1 + group["benchmark"].to_numpy())
                    ),
                }
            )
    net_return = float(np.prod(1 + returns) - 1)
    benchmark_net = float(np.prod(1 + benchmark) - 1)
    sharpe = ratio(returns, periods=periods)
    benchmark_sharpe = ratio(benchmark, periods=periods)
    sortino = ratio(returns, downside=True, periods=periods)
    benchmark_sortino = ratio(benchmark, downside=True, periods=periods)
    mdd = drawdown(returns)
    benchmark_mdd = drawdown(benchmark)
    accepted = (
        net_return > benchmark_net
        and sharpe > benchmark_sharpe
        and sortino >= benchmark_sortino
        and mdd >= benchmark_mdd - 0.05
        and net_return > max(random_totals)
        and (not bear or sum(item["excessPositive"] for item in bear) > len(bear) / 2)
    )
    return {
        "accepted": accepted,
        "bearYears": bear,
        "benchmarkMaxDrawdown": benchmark_mdd,
        "benchmarkNetReturn": benchmark_net,
        "benchmarkSharpe": benchmark_sharpe,
        "benchmarkSortino": benchmark_sortino,
        "maxDrawdown": mdd,
        "meanRankIc": float(np.nanmean(rank_ics)),
        "meanTopBottomSpread": float(np.mean(spreads)),
        "meanTurnover": float(np.mean(turnovers)) if turnovers else 0.0,
        "netReturnAfter35Bps": net_return,
        "randomControlMaximumNetReturn": max(random_totals),
        "rebalanceCount": len(returns),
        "sharpe": sharpe,
        "sortino": sortino,
    }


def main() -> int:
    protocol_path = REPORTS / "evaluation-protocol.v3.json"
    protocol = json.loads(protocol_path.read_text())
    predictions_path = CACHE / "predictions_lstm_h5_h20.v3.parquet"
    predictions = pd.read_parquet(predictions_path)
    history = pd.read_parquet(CACHE / "long_history.parquet")
    rng = np.random.default_rng(SEED)
    candidates: dict[str, object] = {}
    for horizon in HORIZONS:
        frame = predictions[predictions["horizonSessions"] == horizon].copy()
        errors = frame["predictionSimpleReturn"] - frame["actualSimpleReturn"]
        expected = expected_rows(history, horizon)
        ci = block_ci(frame, rng)
        rmse = float(np.sqrt(np.mean(errors**2)))
        mae = float(np.mean(np.abs(errors)))
        zero_rmse = float(np.sqrt(np.mean(frame["actualSimpleReturn"] ** 2)))
        zero_mae = float(np.mean(np.abs(frame["actualSimpleReturn"])))
        coverage = len(frame) / expected if expected else 0.0
        prediction_accepted = (
            coverage >= 0.95
            and rmse < zero_rmse
            and mae < zero_mae
            and ci[1] < 0
            and 1 - coverage <= 0.05
        )
        operation = operation_scorecard(frame, horizon, rng)
        candidates[f"LSTM_H{horizon}"] = {
            "dualAccepted": prediction_accepted and operation["accepted"],
            "operation": operation,
            "prediction": {
                "abstentionRate": 1 - coverage,
                "accepted": prediction_accepted,
                "bias": float(np.mean(errors)),
                "coverage": coverage,
                "eligibleRows": expected,
                "extremeAbsErrorP99": float(np.quantile(np.abs(errors), 0.99)),
                "mae": mae,
                "maeDeltaBlock95": list(ci),
                "rmse": rmse,
                "rows": len(frame),
                "zeroBaselineMae": zero_mae,
                "zeroBaselineRmse": zero_rmse,
            },
        }
    accepted = [name for name, value in candidates.items() if value["dualAccepted"]]
    report = {
        "acceptedCandidates": accepted,
        "candidates": candidates,
        "contractId": "p1-lstm-multihorizon-evaluation.v3",
        "currentModelDisposition": "KEEP_CURRENT_50_50"
        if not accepted
        else "MANUAL_REVIEW_REQUIRED",
        "inputSha256": hashlib.sha256(predictions_path.read_bytes()).hexdigest(),
        "protocolSha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "providerCalls": 0,
        "trialCount": protocol["multipleTesting"]["totalTrialCountAfterRun"],
    }
    output = REPORTS / "lstm-multihorizon.v3.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    lines = [
        "# LSTM 5/20 multi-horizon evaluation v3",
        "",
        f"- accepted candidates: {', '.join(accepted) if accepted else 'none'}",
        f"- disposition: {report['currentModelDisposition']}",
        "- provider calls: 0",
        "",
        "| candidate | prediction | operation | coverage | RMSE | net return | Sharpe | MDD |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for name, value in candidates.items():
        prediction = value["prediction"]
        operation = value["operation"]
        lines.append(
            f"| {name} | {'PASS' if prediction['accepted'] else 'FAIL'} | "
            f"{'PASS' if operation['accepted'] else 'FAIL'} | {prediction['coverage']:.4f} | "
            f"{prediction['rmse']:.6f} | {operation['netReturnAfter35Bps']:.4f} | "
            f"{operation['sharpe']:.4f} | {operation['maxDrawdown']:.4f} |"
        )
    (REPORTS / "lstm-multihorizon.v3.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
