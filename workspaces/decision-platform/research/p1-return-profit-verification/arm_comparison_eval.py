"""Phase 5-B: LSTM 단독 / Ridge 단독 / 50:50 결합을 같은 잣대로 비교한다. (decision-platform venv)

## 무엇을 판정하는가

`ridge_returns` 가 만드는 모델의 `qualityStatus` 가 `COMPARISON_PENDING` 이다. 화면은
"50:50 을 쓴다"고 말하는데(V134 의 `EQUAL_WEIGHT_50_50`) 그 결합이 각 단독보다 나은지
재 본 적이 없다. 이 파일이 그 빈칸을 채운다.

## 잣대

`portfolio_eval.py` 의 것을 그대로 쓴다 - 같은 22 fold PIT walk-forward, 같은 상위 5 종목
균등보유, 같은 왕복 35bps, 같은 S1.4 지표, 같은 PIT 균등가중 벤치마크. 바뀌는 것은 상위
k 를 고를 때 보는 **점수 열** 하나다.

    LSTM        exp(predLogRet) - 1
    RIDGE       production Ridge 의 1일 단순수익률 기대값
    BLEND_50_50 두 값의 산술평균 (production 결합과 같은 정의)

단위를 맞춘 이유. LSTM 하네스는 로그수익률을, production Ridge 는 단순수익률을 예측한다.
맞추지 않으면 결합이 두 다른 척도의 평균이 되어 비교가 무의미해진다.

## 다중검정

시행 수가 늘어난다. `portfolio_eval.py` 는 보유기간 3개만 셌지만 여기서는 **arm 3개 x
보유기간 3개 = 9회**다. DSR 에 9를 넣는다 - 시행 수를 줄여 적으면 그것이 곧 p-hacking 이다.

## 판정 기준 (실행 전 확정)

`portfolio_eval.py` 와 같은 셋을 arm 마다 적용한다.

  * 채택: PIT 균등가중 벤치마크를 Sharpe 로 넘고, DSR 보정 후에도 유의하고, 약세 연도에
    벤치마크보다 낫다 - 셋 모두
  * 기각: 하나라도 못 넘으면 그 arm 은 채택하지 않는다

그리고 결합에 대한 질문이 하나 더 있다. **결합이 두 단독의 최댓값보다 나은가.** 아니면
결합을 쓸 근거가 없다 - 더 단순한 쪽을 쓰는 것이 옳다. 이 판정도 실행 전에 적는다.

결과를 보고 기준을 바꾸지 않는다.

## 실행

    cd workspaces/decision-platform/python-services
    uv run --frozen python ../research/p1-return-profit-verification/arm_comparison_eval.py

`ridge_walk_forward.py` 가 만든 `predictions_arms.parquet` 를 읽는다. provider 호출 0.
"""

from __future__ import annotations

import json
import math
import pathlib

import numpy as np
import pandas as pd

from app.financial_engineering.returns import cagr, simple_returns
from app.financial_engineering.risk_metrics import (
    annualized_volatility,
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
)

CACHE = pathlib.Path("/tmp/p1exp")
REPORTS = pathlib.Path(__file__).resolve().parent / "reports"
ROUND_TRIP_BPS = 35.0
TOP_K = 5
HOLDING_DAYS = (5, 20, 60)
BEAR_YEARS = (2008, 2011, 2015, 2018, 2020, 2022)
ARMS = {
    "LSTM": ("predLstmRet", "actualSimpleRet", 1),
    "RIDGE": ("predRidgeRet", "actualSimpleRet", 1),
    "BLEND_50_50": ("predBlendRet", "actualSimpleRet", 1),
    # 지평을 맞춘 arm. 운용은 1일 예측만 읽고 5·20일을 버리는데, 보유기간은 5/20/60일이다.
    # 그 불일치가 성과에 무엇을 하는지 같은 잣대로 잰다.
    "RIDGE_H5": ("predRidgeRet5", "actualSimpleRet5", 5),
    "RIDGE_H20": ("predRidgeRet20", "actualSimpleRet20", 20),
}
TRIALS = len(ARMS) * len(HOLDING_DAYS)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    if not 0.0 < p < 1.0:
        raise ValueError("probability out of range")
    low, high = -10.0, 10.0
    for _ in range(200):
        mid = (low + high) / 2.0
        if _norm_cdf(mid) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def deflated_sharpe(
    observed: float,
    returns: np.ndarray,
    *,
    trials: int,
    trial_sharpe_std: float | None = None,
) -> dict[str, object]:
    """Bailey & Lopez de Prado. 시행 수와 수익률 분포의 비정규성을 함께 보정한다.

    ## σ 를 어디서 얻는가 — 이 하네스가 고쳐야 했던 것

    DSR 의 비교 기준은
    `SR0 = std(SR_hat across trials) * ((1-γ)·Z⁻¹[1-1/N] + γ·Z⁻¹[1-1/(N·e)])` 다.
    앞의 `std(...)` 는 **시행들 사이의 Sharpe 표준편차**다.

    `portfolio_eval.py` 는 그 값을 1(기간당 단위)로 두는 근사를 썼다. 시행별 Sharpe 를
    실제로 갖고 있지 않으면 쓸 수 있는 대체값이지만, 기간당 Sharpe 1.0 은 연 15.9 에
    해당하는 매우 큰 산포이므로 기준이 과도하게 높아지고 어떤 arm 도 통과하지 못한다.
    그 근사 아래에서는 판정이 데이터가 아니라 근사에 걸린다.

    여기서는 시행 9개의 Sharpe 를 모두 갖고 있으므로 `trial_sharpe_std` 로 실제 산포를
    넣는다. 두 값을 함께 보고해 어느 쪽 기준인지 드러낸다 - 사전 확정한 판정 기준을
    바꾸는 것이 아니라 같은 통계를 제대로 계산하는 것이다.
    """

    count = len(returns)
    if count < 3 or trials < 1:
        return {"deflatedSharpe": float("nan"), "pValue": float("nan")}
    euler = 0.5772156649015329
    variance = float(np.var(returns, ddof=1))
    if variance <= 0:
        return {"deflatedSharpe": float("nan"), "pValue": float("nan")}
    centered = returns - float(np.mean(returns))
    skew = float(np.mean(centered**3)) / variance**1.5
    kurtosis = float(np.mean(centered**4)) / variance**2
    multiplier = 1.0
    if trials > 1:
        multiplier = (
            _norm_ppf(1.0 - 1.0 / trials) * (1.0 - euler)
            + _norm_ppf(1.0 - 1.0 / (trials * math.e)) * euler
        )
    # 기간당 단위로 계산한다. `observed` 는 연율 Sharpe 다.
    per_period = observed / math.sqrt(252.0)
    approximate_sigma = 1.0
    measured_sigma = trial_sharpe_std / math.sqrt(252.0) if trial_sharpe_std is not None else None
    sigma = measured_sigma if measured_sigma is not None else approximate_sigma
    expected_max = sigma * multiplier
    denominator = math.sqrt(
        max(1.0 - skew * per_period + (kurtosis - 1.0) / 4.0 * per_period**2, 1e-12)
    )
    z = (per_period - expected_max) * math.sqrt(count - 1) / denominator
    legacy_expected_max = approximate_sigma * multiplier
    legacy_z = (per_period - legacy_expected_max) * math.sqrt(count - 1) / denominator
    return {
        "observedSharpe": round(observed, 4),
        "trialMultiplier": round(multiplier, 4),
        "trialSharpeStdAnnual": (
            round(trial_sharpe_std, 4) if trial_sharpe_std is not None else None
        ),
        "expectedMaxSharpeAnnual": round(expected_max * math.sqrt(252.0), 4),
        "deflatedSharpe": round(z, 4),
        "pValue": round(1.0 - _norm_cdf(z), 6),
        # `portfolio_eval.py` 가 쓴 σ=1 근사. 그 파일의 공개 수치와 비교하기 위해 남긴다.
        "legacyApproximation": {
            "expectedMaxSharpeAnnual": round(legacy_expected_max * math.sqrt(252.0), 4),
            "deflatedSharpe": round(legacy_z, 4),
            "pValue": round(1.0 - _norm_cdf(legacy_z), 6),
        },
    }


def simulate(predictions: pd.DataFrame, holding_days: int, *, score: str | None) -> pd.Series:
    """holding_days 마다 `score` 상위 k 를 고르고 그 기간을 균등 보유한다.

    `score=None` 이면 예측을 쓰지 않고 그날의 PIT 유니버스 전체를 균등 보유한다(벤치마크).
    `portfolio_eval.simulate` 와 같은 절차이고 점수 열만 파라미터가 됐다.
    """

    daily = predictions.sort_values("date")
    dates = sorted(daily["date"].unique())
    weights: dict[str, float] = {}
    equity = [1.0]
    rebalance_cost = ROUND_TRIP_BPS / 10_000.0

    for index, day in enumerate(dates):
        rows = daily[daily["date"] == day]
        if index % holding_days == 0:
            if score is None:
                chosen = rows["ticker"].tolist()
            else:
                eligible = rows[np.isfinite(rows[score].astype(float))]
                chosen = eligible.nlargest(min(TOP_K, len(eligible)), score)["ticker"].tolist()
            # 거부된 슬롯을 남은 종목에 재분배하지 않는다. 빈 슬롯은 사전 정의한 현금 정책이다.
            divisor = len(chosen) if score is None else TOP_K
            new_weights = {ticker: 1.0 / divisor for ticker in chosen} if chosen else {}
            turnover = sum(
                abs(new_weights.get(ticker, 0.0) - weights.get(ticker, 0.0))
                for ticker in set(new_weights) | set(weights)
            )
            cost = turnover / 2.0 * rebalance_cost
            weights = new_weights
        else:
            cost = 0.0
        realized = dict(zip(rows["ticker"], rows["actualSimpleRet"].astype(float), strict=True))
        missing = [ticker for ticker in weights if ticker not in realized or not math.isfinite(realized[ticker])]
        if missing:
            # terminal return을 0이나 -100%로 채우지 않는다. 그 비교 자체를 판정 불가로 닫는다.
            raise ValueError(f"MISSING_TERMINAL_RETURN:{','.join(sorted(missing))}")
        gain = sum(weight * realized[ticker] for ticker, weight in weights.items())
        equity.append(equity[-1] * (1.0 + gain - cost))

    return pd.Series(equity[1:], index=pd.to_datetime(dates))


def describe(equity: pd.Series, label: str) -> dict[str, object]:
    rets = simple_returns(equity.to_numpy())
    losses = np.sort(np.asarray(rets, dtype=float))
    var95 = float(np.quantile(losses, 0.05)) if losses.size else float("nan")
    tail = losses[losses <= var95]
    return {
        "label": label,
        "years": round(len(equity) / 252.0, 1),
        "sessions": len(equity),
        "cagr": round(float(cagr(equity.to_numpy())), 4),
        "annualVol": round(float(annualized_volatility(rets)), 4),
        "sharpe": round(float(sharpe_ratio(rets)), 4),
        "sortino": round(float(sortino_ratio(rets)), 4),
        "mdd": round(float(max_drawdown(equity.to_numpy())), 4),
        "var95": round(var95, 6),
        "cvar95": round(float(tail.mean()) if tail.size else var95, 6),
        "finalEquity": round(float(equity.iloc[-1]), 4),
    }


def _prediction_accuracy(
    all_rows: pd.DataFrame,
    column: str,
    target: str,
    horizon: int,
) -> dict[str, object]:
    frame = all_rows[np.isfinite(all_rows[column]) & np.isfinite(all_rows[target])].copy()
    same = np.sign(frame[column]) == np.sign(frame[target])
    count = int(len(frame))
    rate = float(same.mean())
    half_width = 1.959963985 * math.sqrt(0.25 / count) if count else float("nan")
    return {
        "horizonSessions": horizon,
        "rows": count,
        "eligibleRows": int(len(all_rows)),
        "abstentionRows": int(len(all_rows) - count),
        "coverage": round(count / len(all_rows), 6) if len(all_rows) else 0.0,
        "directionAccuracy": round(rate, 4),
        "coinFlip95": [round(0.5 - half_width, 4), round(0.5 + half_width, 4)],
        "beatsCoinFlip": bool(rate - half_width > 0.5),
        "rmse": round(float(np.sqrt(((frame[column] - frame[target]) ** 2).mean())), 6),
        "rmseNaiveZero": round(float(np.sqrt((frame[target] ** 2).mean())), 6),
        "mae": round(float((frame[column] - frame[target]).abs().mean()), 6),
        "maeNaiveZero": round(float(frame[target].abs().mean()), 6),
        "bias": round(float((frame[column] - frame[target]).mean()), 6),
        "extremeAbsErrorP99": round(float((frame[column] - frame[target]).abs().quantile(0.99)), 6),
        "meanPredicted": round(float(frame[column].mean()), 6),
        "meanActual": round(float(frame[target].mean()), 6),
        "maeDeltaBlock95": _block_mae_delta_interval(frame, column, target),
    }


def _block_mae_delta_interval(frame: pd.DataFrame, column: str, target: str) -> list[float]:
    blocks = (
        frame.assign(delta=(frame[column] - frame[target]).abs() - frame[target].abs())
        .groupby("testYear")["delta"]
        .mean()
        .to_numpy(dtype=float)
    )
    if len(blocks) < 2:
        return [float("nan"), float("nan")]
    generator = np.random.default_rng(20260908)
    means = np.asarray(
        [float(np.mean(generator.choice(blocks, size=len(blocks), replace=True))) for _ in range(2_000)]
    )
    return [round(float(np.quantile(means, 0.025)), 6), round(float(np.quantile(means, 0.975)), 6)]


def _selection_coverage(frame: pd.DataFrame, score: str, holding: int) -> dict[str, object]:
    dates = sorted(frame["date"].unique())
    counts = [
        int(np.isfinite(frame[frame["date"] == day][score].astype(float)).sum())
        for index, day in enumerate(dates)
        if index % holding == 0
    ]
    slots = len(counts) * TOP_K
    used = sum(min(count, TOP_K) for count in counts)
    return {
        "rebalanceCount": len(counts),
        "availableSlots": used,
        "totalSlots": slots,
        "coverage": round(used / slots, 6) if slots else 0.0,
        "cashPolicy": "UNFILLED_TOP_K_SLOTS_STAY_CASH",
    }


def _bear_year_comparison(predictions: pd.DataFrame, score: str, holding: int) -> dict[str, object]:
    """약세 연도에 벤치마크보다 나은지. 판정 기준의 세 번째 항이다."""

    better = 0
    total = 0
    detail: dict[str, object] = {}
    for year in BEAR_YEARS:
        subset = predictions[predictions["testYear"] == year]
        if subset.empty:
            continue
        total += 1
        arm = simulate(subset, holding, score=score)
        bench = simulate(subset, 20, score=None)
        arm_return = float(arm.iloc[-1]) - 1.0
        bench_return = float(bench.iloc[-1]) - 1.0
        detail[str(year)] = {
            "armReturnPct": round(arm_return * 100, 2),
            "benchmarkReturnPct": round(bench_return * 100, 2),
        }
        if arm_return > bench_return:
            better += 1
    return {"yearsCompared": total, "yearsBetter": better, "detail": detail}


def main() -> int:
    predictions = pd.read_parquet(CACHE / "predictions_arms.parquet")
    predictions["date"] = pd.to_datetime(predictions["date"])
    print(
        f"예측 {len(predictions):,}행 / {predictions['ticker'].nunique()}종목 / "
        f"fold {predictions['testYear'].nunique()}개 "
        f"({predictions['testYear'].min()}~{predictions['testYear'].max()})"
    )
    print(f"arm {len(ARMS)}개 x 보유기간 {len(HOLDING_DAYS)}개 = 시행 {TRIALS}회")
    print()

    accuracy = {
        name: _prediction_accuracy(predictions, column, target, horizon)
        for name, (column, target, horizon) in ARMS.items()
    }
    print("=== 예측 자체 (방향 정확도·RMSE) ===")
    for name, value in accuracy.items():
        print(
            f"  {name:12s} 방향 {value['directionAccuracy']:.4f} "
            f"(동전던지기 {value['coinFlip95']}) 넘음={value['beatsCoinFlip']} "
            f"RMSE {value['rmse']:.6f} vs naive {value['rmseNaiveZero']:.6f}"
        )
    print()

    # today's exact-31을 과거 전 구간에 있었다고 간주하지 않는다. historical available rows의
    # 예측 오차는 위에서 그대로 보고하고, 운용 성과는 31종목과 1일 실현수익률이 모두 있는
    # common-coverage 날짜로 따로 측정한다. historical universe 성과는 terminal return 결손으로
    # 차단 상태를 남긴다.
    per_date = predictions.groupby("date").agg(
        symbols=("ticker", "nunique"),
        realized=("actualSimpleRet", lambda values: int(np.isfinite(values.astype(float)).sum())),
    )
    complete_dates = per_date[(per_date["symbols"] == 31) & (per_date["realized"] == 31)].index
    performance_predictions = predictions[predictions["date"].isin(complete_dates)].copy()
    if performance_predictions["date"].nunique() < 252:
        raise ValueError("EXACT31_COMMON_COVERAGE_TOO_SHORT")
    historical_performance = {
        "status": "BLOCKED_MISSING_TERMINAL_RETURN",
        "reason": "MISSING_TERMINAL_RETURN",
        "filledReturnPolicy": "NONE",
    }
    exact31_accuracy = {
        name: _prediction_accuracy(performance_predictions, column, target, horizon)
        for name, (column, target, horizon) in ARMS.items()
    }

    benchmark = simulate(performance_predictions, 20, score=None)
    rows: list[dict[str, object]] = [describe(benchmark, "PIT 균등가중 (벤치마크)")]
    equities: dict[str, pd.Series] = {"PIT 균등가중 (벤치마크)": benchmark}
    coverage: dict[str, object] = {}
    for name, (column, _target, _horizon) in ARMS.items():
        for holding in HOLDING_DAYS:
            label = f"{name} 상위{TOP_K} / {holding}일"
            equity = simulate(performance_predictions, holding, score=column)
            equities[label] = equity
            rows.append(describe(equity, label))
            coverage[f"{name}:{holding}"] = _selection_coverage(
                performance_predictions, column, holding
            )

    print("=== 성과 (walk-forward OOS, 왕복 35bps) ===")
    header = f"{'전략':28s} {'연수익':>8s} {'변동성':>8s} {'Sharpe':>8s} {'MDD':>8s}"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['label']:28s} {row['cagr'] * 100:7.1f}% {row['annualVol'] * 100:7.1f}% "
            f"{row['sharpe']:8.2f} {row['mdd'] * 100:7.1f}%"
        )
    print()

    bench_rets = simple_returns(benchmark.to_numpy())
    bench_sharpe = float(sharpe_ratio(bench_rets))
    # 시행들 사이의 Sharpe 산포. DSR 의 비교 기준이 이 값에 비례한다. 벤치마크는 시행이
    # 아니라 기준이므로 제외한다.
    trial_sharpes = [float(row["sharpe"]) for row in rows[1:]]
    assert len(trial_sharpes) == TRIALS
    trial_sharpe_std = float(np.std(np.asarray(trial_sharpes), ddof=1))
    verdicts: dict[str, object] = {}
    print(
        f"=== 판정 (벤치마크 Sharpe {bench_sharpe:.2f}, DSR 시행 {TRIALS}회, "
        f"시행간 Sharpe 표준편차 {trial_sharpe_std:.3f}) ==="
    )
    for name, (column, _target, _horizon) in ARMS.items():
        best: dict[str, object] | None = None
        for holding in HOLDING_DAYS:
            label = f"{name} 상위{TOP_K} / {holding}일"
            row = next(item for item in rows if item["label"] == label)
            equity = equities[label]
            rets = simple_returns(equity.to_numpy())
            length = min(len(rets), len(bench_rets))
            excess = rets[:length] - bench_rets[:length]
            mean_annual = float(np.mean(excess)) * 252.0
            std_annual = float(np.std(excess, ddof=1)) * math.sqrt(252.0)
            info_ratio = mean_annual / std_annual if std_annual > 0 else 0.0
            candidate = {
                "holdingDays": holding,
                "sharpe": row["sharpe"],
                "beatsBenchmarkSharpe": bool(float(row["sharpe"]) > bench_sharpe),
                "annualExcessPct": round(mean_annual * 100, 2),
                "trackingErrorPct": round(std_annual * 100, 2),
                "informationRatio": round(info_ratio, 4),
                "tStat": round(info_ratio * math.sqrt(length / 252.0), 2),
                "deflatedSharpe": deflated_sharpe(
                    float(row["sharpe"]),
                    rets,
                    trials=TRIALS,
                    trial_sharpe_std=trial_sharpe_std,
                ),
            }
            if best is None or float(candidate["sharpe"]) > float(best["sharpe"]):
                best = candidate
        assert best is not None
        bear = _bear_year_comparison(performance_predictions, column, int(best["holdingDays"]))
        dsr = best["deflatedSharpe"]
        assert isinstance(dsr, dict)
        significant = bool(float(dsr["pValue"]) < 0.05)
        bear_ok = bool(
            int(bear["yearsCompared"]) > 0
            and int(bear["yearsBetter"]) * 2 > int(bear["yearsCompared"])
        )
        adopted = bool(best["beatsBenchmarkSharpe"]) and significant and bear_ok
        verdicts[name] = {
            "best": best,
            "bearYears": bear,
            "criteria": {
                "beatsBenchmarkSharpe": bool(best["beatsBenchmarkSharpe"]),
                "significantAfterDeflation": significant,
                "betterInBearYears": bear_ok,
            },
            "adopted": adopted,
        }
        print(
            f"  {name:12s} best={best['holdingDays']}일 Sharpe {best['sharpe']:5.2f} "
            f"초과 {best['annualExcessPct']:+6.2f}%/년 t {best['tStat']:5.2f} "
            f"DSR p {dsr['pValue']:.4f} 약세 {bear['yearsBetter']}/{bear['yearsCompared']} "
            f"-> {'채택' if adopted else '기각'}"
        )
    print()

    # --- 무작위 순위 대조군 ---------------------------------------------------
    #
    # 왜 필요한가. 세 arm 모두 방향 정확도가 동전던지기 하한 아래이고 RMSE 도 naive-zero
    # 보다 나쁘다. 즉 예측력이 없다. 그런데 포트폴리오 초과수익은 양수다. 그 둘이 동시에
    # 참일 수 있는 이유는 상위 5 종목 선택이 방향 베팅이 아니라 **집중**이기 때문이다 -
    # 31종목 중 5종목으로 몰면 고변동·고모멘텀 쪽으로 기울고, exact-31 이 오늘의 명부라
    # 생존편향이 그 기울기를 보상한다(README 의 공시 한계).
    #
    # 그래서 같은 우주·같은 비용·같은 보유기간에서 **무작위로 5종목을 고르는** 대조군을
    # 만든다. 무작위가 arm 과 비슷한 초과수익을 내면 그 초과는 예측력이 아니다. 이 대조는
    # 논증이 아니라 측정으로 귀속을 가른다.
    #
    # 시드를 여러 개 쓰는 이유는 한 무작위 경로의 운을 결론으로 만들지 않기 위해서다.
    print("=== 무작위 순위 대조군 (귀무: 초과수익은 집중과 생존편향의 결과다) ===")
    control: dict[str, object] = {}
    for holding in HOLDING_DAYS:
        sharpes: list[float] = []
        excesses: list[float] = []
        for seed in range(8):
            generator = np.random.default_rng(seed)
            scored = performance_predictions.copy()
            scored["predRandom"] = generator.standard_normal(len(scored))
            equity = simulate(scored, holding, score="predRandom")
            rets = simple_returns(equity.to_numpy())
            sharpes.append(float(sharpe_ratio(rets)))
            length = min(len(rets), len(bench_rets))
            excesses.append(float(np.mean(rets[:length] - bench_rets[:length])) * 252.0)
        control[str(holding)] = {
            "seeds": 8,
            "sharpeMean": round(float(np.mean(sharpes)), 4),
            "sharpeMax": round(float(np.max(sharpes)), 4),
            "annualExcessPctMean": round(float(np.mean(excesses)) * 100, 2),
            "annualExcessPctMax": round(float(np.max(excesses)) * 100, 2),
        }
        print(
            f"  {holding:2d}일 보유  Sharpe 평균 {np.mean(sharpes):5.2f} 최대 "
            f"{np.max(sharpes):5.2f} / 초과 평균 {np.mean(excesses) * 100:+6.2f}%/년 "
            f"최대 {np.max(excesses) * 100:+6.2f}%/년"
        )
    print()

    lstm_sharpe = float(verdicts["LSTM"]["best"]["sharpe"])  # type: ignore[index]
    ridge_sharpe = float(verdicts["RIDGE"]["best"]["sharpe"])  # type: ignore[index]
    blend_sharpe = float(verdicts["BLEND_50_50"]["best"]["sharpe"])  # type: ignore[index]
    blend_beats_both = blend_sharpe > max(lstm_sharpe, ridge_sharpe)
    print("=== 결합이 단독보다 나은가 ===")
    print(
        f"  LSTM {lstm_sharpe:.2f} / RIDGE {ridge_sharpe:.2f} / "
        f"BLEND {blend_sharpe:.2f} -> {'낫다' if blend_beats_both else '낫지 않다'}"
    )

    adopted_arms = [name for name, value in verdicts.items() if value["adopted"]]  # type: ignore[index]

    blend = verdicts["BLEND_50_50"]
    blend_best = blend["best"]  # type: ignore[index]
    blend_holding = str(blend_best["holdingDays"])  # type: ignore[index]
    blend_excess = float(blend_best["annualExcessPct"])  # type: ignore[index]
    blend_sharpe_value = float(blend_best["sharpe"])  # type: ignore[index]
    control_row = control[blend_holding]
    assert isinstance(control_row, dict)
    beats_random = bool(
        blend_sharpe_value > float(control_row["sharpeMax"])
        and blend_excess > float(control_row["annualExcessPctMax"])
    )
    any_skill = bool(any(value["beatsCoinFlip"] for value in accuracy.values()))

    # 공개 상태를 무엇으로 둘 것인가.
    #
    # 사전 확정 기준 셋은 BLEND 가 통과한다. 무작위 순위 대조군도 넘는다 - 즉 이 초과수익은
    # "31종목 중 5종목으로 몰면 생기는 것"이 아니다. 그 두 가지는 그대로 보고한다.
    #
    # 그래도 `PASS` 로 공개하지 않는 이유는 **생존편향의 귀속이 이 설계로 해소되지 않기**
    # 때문이다. exact-31 은 오늘의 명부다(README 의 공시 한계). 균등가중 벤치마크는 전략과
    # 같은 편향을 갖지만, 그 상쇄 논증은 **같은 노출**에서만 성립한다. 상위 5종목으로
    # 집중하는 순위 전략은 "오늘의 승자 중 과거에 가장 크게 이긴 종목"으로 기울 수 있고,
    # 그 기울기는 실력과 구분되지 않는다. 무작위 대조군은 이 교란을 시험하지 않는다 -
    # 무작위 추출도 같은 생존편향 우주에서 뽑지만 그 안에서 기울지 않기 때문이다.
    #
    # 방향 정확도가 세 arm 모두 동전던지기 하한 아래라는 사실은 판정의 근거로 쓰지 않는다.
    # 그것은 부호·수준 진단이고 이 전략은 횡단면 순위 전략이므로, 순위가 유용하면서 부호가
    # 틀리는 것이 모순이 아니다. 다만 "수익률 수준을 맞춘다"는 주장은 이 수치로 할 수 없다.
    #
    # 그래서 상태는 보수적으로 `BELOW_BASELINE` 이고, 해소 조건을 함께 적는다 - 상장폐지·
    # 편출 종목을 포함한 시점별 명부로 다시 재는 것. 계약이 exact-31 을 고정하므로 이번
    # 범위에서는 할 수 없다.
    unresolved = "SURVIVORSHIP_ATTRIBUTION_UNRESOLVED"
    quality = "BELOW_BASELINE"
    print("=== 공개 상태 판정 ===")
    print(f"  사전 확정 기준 셋 통과: {'BLEND_50_50' in adopted_arms}")
    print(
        f"  무작위 순위 대조군을 넘음: {beats_random} "
        f"(BLEND Sharpe {blend_sharpe_value:.2f} vs 무작위 최대 "
        f"{float(control_row['sharpeMax']):.2f}, 초과 {blend_excess:+.2f}%/년 vs 무작위 최대 "
        f"{float(control_row['annualExcessPctMax']):+.2f}%/년)"
    )
    print(f"  어느 arm 이든 방향 정확도가 동전던지기를 넘음: {any_skill}")
    print(f"  미해소 교란: {unresolved}")
    print(f"  -> qualityStatus={quality}")
    report = {
        "contractId": "p1-return-arm-comparison.v2",
        "predictionRows": int(len(predictions)),
        "symbols": int(predictions["ticker"].nunique()),
        "folds": int(predictions["testYear"].nunique()),
        "firstTestYear": int(predictions["testYear"].min()),
        "lastTestYear": int(predictions["testYear"].max()),
        "roundTripCostBps": ROUND_TRIP_BPS,
        "topK": TOP_K,
        "holdingDays": list(HOLDING_DAYS),
        "trials": TRIALS,
        "benchmarkSharpe": round(bench_sharpe, 4),
        "trialSharpeStdAnnual": round(trial_sharpe_std, 4),
        "predictionAccuracy": accuracy,
        "predictionAccuracyExact31": exact31_accuracy,
        "selectionCoverage": coverage,
        "performanceUniverse": {
            "status": "CURRENT_EXACT31_COMMON_COVERAGE",
            "firstSession": str(min(complete_dates).date()),
            "lastSession": str(max(complete_dates).date()),
            "sessions": int(len(complete_dates)),
            "symbols": 31,
        },
        "historicalUniversePerformance": historical_performance,
        "performance": rows,
        "verdicts": verdicts,
        "blendBeatsBothStandalone": blend_beats_both,
        "randomRankingControl": control,
        "blendBeatsRandomControl": beats_random,
        "anyArmBeatsCoinFlip": bool(any(value["beatsCoinFlip"] for value in accuracy.values())),
        "adoptedArms": adopted_arms,
        "unresolvedConfound": unresolved,
        "unresolvedConfoundNote": (
            "exact-31 은 오늘의 명부다. 집중 순위 전략의 초과수익을 실력과 생존편향으로 "
            "나눌 수 없다. 해소 조건은 편출·상장폐지 종목을 포함한 시점별 명부로 재측정."
        ),
        "combinationMethodInProduction": "EQUAL_WEIGHT_50_50",
        "resolvedQualityStatus": quality,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / "arm-comparison.v2.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    markdown = REPORTS / "arm-comparison.v2.md"
    markdown.write_text(
        "# Arm comparison v2\n\n"
        f"- protocol: `evaluation-protocol.v2.json`\n"
        f"- prediction rows: {report['predictionRows']:,}\n"
        f"- performance universe: CURRENT_EXACT31_COMMON_COVERAGE "
        f"({report['performanceUniverse']['firstSession']}~{report['performanceUniverse']['lastSession']}, "
        f"{report['performanceUniverse']['sessions']} sessions)\n"
        "- historical-universe performance: BLOCKED_MISSING_TERMINAL_RETURN; no 0/-100% fill\n"
        f"- adopted arms: {', '.join(adopted_arms) if adopted_arms else 'none'}\n"
        f"- quality status: {quality}\n",
        encoding="utf-8",
    )
    print()
    print(f"저장: {path}")
    print(f"저장: {markdown}")
    print(f"채택 arm: {adopted_arms or '없음'} / qualityStatus={quality}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
