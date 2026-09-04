"""Phase 5-A 2단계: 포트폴리오 시뮬레이션과 성과 판정. (decision-platform venv)

지표는 S1.4 app.financial_engineering 을 그대로 쓴다. 새로 구현하지 않는다.
Deflated Sharpe Ratio 로 다중검정을 보정한다 (Bailey & Lopez de Prado).
"""

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
ROUND_TRIP_BPS = 35.0
TOP_K = 5
HOLDING_DAYS = (5, 20, 60)
BEAR_YEARS = (2008, 2011, 2015, 2018, 2020, 2022)


# --- Deflated Sharpe Ratio -------------------------------------------------
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Acklam 근사. DSR 의 기대 최대 Sharpe 항에만 쓴다."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0,1)")
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def deflated_sharpe(
    observed_sharpe: float, returns: np.ndarray, trials: int, periods_per_year: int = 252
) -> dict[str, float]:
    """DSR: 관측 Sharpe 가 시행 수를 감안해도 0보다 큰가의 확률.

    Bailey & Lopez de Prado. 여러 변형을 시도해 최고를 고르면 모든 후보가 노이즈여도
    최대 Sharpe 가 부풀려진다. 기대 최대값을 빼고 비정규성을 보정한다.
    """
    n = len(returns)
    if n < 3 or trials < 1:
        return {"expectedMaxSharpe": 0.0, "deflatedSharpe": 0.0, "pValue": 1.0}

    # 비연환산 Sharpe 로 변환
    sr = observed_sharpe / math.sqrt(periods_per_year)
    mean = float(np.mean(returns))
    std = float(np.std(returns, ddof=1))
    if std <= 0:
        return {"expectedMaxSharpe": 0.0, "deflatedSharpe": 0.0, "pValue": 1.0}
    centered = (returns - mean) / std
    skew = float(np.mean(centered**3))
    kurt = float(np.mean(centered**4))

    # sigma(SR-hat): Sharpe 추정량의 표준편차. 비정규성(skew, kurtosis)을 반영한다.
    sigma_sr = math.sqrt(
        max(1e-12, (1 - skew * sr + (kurt - 1) / 4.0 * sr**2) / (n - 1))
    )

    # 기대 최대 Sharpe 는 sigma(SR-hat) 단위의 z 값이므로 반드시 sigma 를 곱해야 한다.
    # 이 계수를 빼먹으면 Sharpe 와 z 를 직접 비교해 불가능한 값이 나온다.
    euler = 0.5772156649015329
    if trials == 1:
        expected_max_z = 0.0
    else:
        expected_max_z = (1 - euler) * _norm_ppf(1 - 1.0 / trials) + euler * _norm_ppf(
            1 - 1.0 / (trials * math.e)
        )
    threshold = sigma_sr * expected_max_z

    z = (sr - threshold) / sigma_sr
    return {
        "expectedMaxSharpe": threshold * math.sqrt(periods_per_year),
        "sigmaSharpe": sigma_sr * math.sqrt(periods_per_year),
        "deflatedSharpe": z,
        "pValue": 1.0 - _norm_cdf(z),
    }


# --- 포트폴리오 시뮬레이션 ---------------------------------------------------
def simulate(
    predictions: pd.DataFrame, holding_days: int, *, use_prediction: bool
) -> pd.Series:
    """holding_days 마다 상위 k 를 고르고 그 기간을 균등 보유한다.

    use_prediction=False 면 예측을 쓰지 않고 그날의 PIT 유니버스 전체를 균등 보유한다
    (벤치마크). 회전에 왕복 35bps 를 적용한다.
    """
    daily = predictions.sort_values("date")
    dates = sorted(daily["date"].unique())
    weights: dict[str, float] = {}
    equity = [1.0]
    rebalance_cost = ROUND_TRIP_BPS / 10_000.0

    for index, day in enumerate(dates):
        rows = daily[daily["date"] == day]
        if index % holding_days == 0:
            if use_prediction:
                chosen = rows.nlargest(min(TOP_K, len(rows)), "predLogRet")["ticker"].tolist()
            else:
                chosen = rows["ticker"].tolist()
            new_weights = {t: 1.0 / len(chosen) for t in chosen} if chosen else {}
            turnover = sum(
                abs(new_weights.get(t, 0.0) - weights.get(t, 0.0))
                for t in set(new_weights) | set(weights)
            )
            cost = turnover / 2.0 * rebalance_cost
            weights = new_weights
        else:
            cost = 0.0

        realized = {t: float(np.expm1(r)) for t, r in zip(rows["ticker"], rows["actualLogRet"], strict=True)}
        gain = sum(w * realized.get(t, 0.0) for t, w in weights.items())
        equity.append(equity[-1] * (1.0 + gain - cost))

    return pd.Series(equity[1:], index=pd.to_datetime(dates))


def describe(equity: pd.Series, label: str) -> dict[str, object]:
    rets = simple_returns(equity.to_numpy())
    years = len(equity) / 252.0
    # VaR/CVaR 는 대시보드 모델 비교가 요구하는 지표다(dashboard-model-evaluation.v1).
    # 여기서 같은 일별 수익률 계열로 계산해 화면과 연구가 같은 숫자를 보게 한다. 부호는
    # 손실을 음수로 두는 이 레포의 관례(mdd 와 같은 방향)를 따른다.
    losses = np.sort(np.asarray(rets, dtype=float))
    var95 = float(np.quantile(losses, 0.05)) if losses.size else float("nan")
    tail = losses[losses <= var95]
    cvar95 = float(tail.mean()) if tail.size else var95
    return {
        "label": label,
        "years": round(years, 1),
        "sessions": len(equity),
        "cagr": round(float(cagr(equity.to_numpy())), 4),
        "annualVol": round(float(annualized_volatility(rets)), 4),
        "sharpe": round(float(sharpe_ratio(rets)), 4),
        "sortino": round(float(sortino_ratio(rets)), 4),
        "mdd": round(float(max_drawdown(equity.to_numpy())), 4),
        "var95": round(var95, 6),
        "cvar95": round(cvar95, 6),
        "finalEquity": round(float(equity.iloc[-1]), 4),
    }


def main() -> int:
    predictions = pd.read_parquet(CACHE / "predictions.parquet")
    predictions["date"] = pd.to_datetime(predictions["date"])
    print(
        f"예측 {len(predictions):,}행 / {predictions['ticker'].nunique()}종목 / "
        f"fold {predictions['testYear'].nunique()}개 "
        f"({predictions['testYear'].min()}~{predictions['testYear'].max()})"
    )
    print()

    # 예측력 자체
    same = np.sign(predictions["predLogRet"]) == np.sign(predictions["actualLogRet"])
    dir_acc = float(same.mean())
    n = len(predictions)
    stderr = math.sqrt(0.25 / n)
    print("=== 예측력 (out-of-sample 전체) ===")
    print(f"  dir_acc        {dir_acc:.4f}")
    print(f"  동전던지기 95% 구간  {0.5 - 1.96 * stderr:.4f} ~ {0.5 + 1.96 * stderr:.4f}  (n={n:,})")
    naive_rmse = float(np.sqrt((predictions["actualLogRet"] ** 2).mean()))
    model_rmse = float(
        np.sqrt(((predictions["predLogRet"] - predictions["actualLogRet"]) ** 2).mean())
    )
    print(f"  RMSE(모델)     {model_rmse:.6f}")
    print(f"  RMSE(naive=0)  {naive_rmse:.6f}   -> 모델이 {'낫다' if model_rmse < naive_rmse else '못하다'}")
    ratio = np.exp(predictions["predLogRet"])
    print(f"  expectedReturn 범위 {(ratio.min()-1)*100:+.2f}% ~ {(ratio.max()-1)*100:+.2f}%")
    print()

    results: list[dict[str, object]] = []
    equities: dict[str, pd.Series] = {}

    benchmark = simulate(predictions, 20, use_prediction=False)
    equities["PIT 균등가중 (벤치마크)"] = benchmark
    results.append(describe(benchmark, "PIT 균등가중 (벤치마크)"))

    for holding in HOLDING_DAYS:
        equity = simulate(predictions, holding, use_prediction=True)
        label = f"LSTM 상위{TOP_K} / {holding}일 보유"
        equities[label] = equity
        results.append(describe(equity, label))

    print("=== 성과 (walk-forward OOS, 왕복 35bps) ===")
    header = f"{'전략':32s} {'연수익':>8s} {'변동성':>8s} {'Sharpe':>8s} {'Sortino':>8s} {'MDD':>8s}"
    print(header)
    print("-" * len(header))
    for row in results:
        print(
            f"{row['label']:32s} {row['cagr']*100:7.1f}% {row['annualVol']*100:7.1f}% "
            f"{row['sharpe']:8.2f} {row['sortino']:8.2f} {row['mdd']*100:7.1f}%"
        )
    print()

    # DSR: 시행 수 = 보유기간 후보 3개
    print(f"=== Deflated Sharpe Ratio (시행 {len(HOLDING_DAYS)}회) ===")
    print("  DSR 은 'Sharpe 가 0보다 큰가'를 시행 수 보정 후 본다. 벤치마크 초과 여부는 아니다.")
    dsr_report: dict[str, object] = {}
    for row in results[1:]:
        equity = equities[str(row["label"])]
        rets = simple_returns(equity.to_numpy())
        dsr = deflated_sharpe(float(row["sharpe"]), rets, trials=len(HOLDING_DAYS))
        dsr_report[str(row["label"])] = dsr
        print(
            f"  {row['label']:32s} 관측 {row['sharpe']:5.2f} / sigma {dsr['sigmaSharpe']:4.2f} / "
            f"기대최대 {dsr['expectedMaxSharpe']:4.2f} / DSR z {dsr['deflatedSharpe']:6.2f} / "
            f"p {dsr['pValue']:.4f}"
        )
    print()

    # 벤치마크 초과분 - 실제로 궁금한 것은 "LSTM 이 무엇을 더하는가"다.
    print("=== 벤치마크 초과분 (LSTM 상위k - PIT 균등가중) ===")
    bench_equity = equities["PIT 균등가중 (벤치마크)"]
    bench_rets = simple_returns(bench_equity.to_numpy())
    excess_report: dict[str, object] = {}
    for row in results[1:]:
        equity = equities[str(row["label"])]
        rets = simple_returns(equity.to_numpy())
        length = min(len(rets), len(bench_rets))
        excess = rets[:length] - bench_rets[:length]
        mean_annual = float(np.mean(excess)) * 252.0
        std_annual = float(np.std(excess, ddof=1)) * math.sqrt(252.0)
        info_ratio = mean_annual / std_annual if std_annual > 0 else 0.0
        t_stat = info_ratio * math.sqrt(length / 252.0)
        dsr = deflated_sharpe(info_ratio, excess, trials=len(HOLDING_DAYS))
        excess_report[str(row["label"])] = {
            "annualExcessPct": round(mean_annual * 100, 2),
            "trackingErrorPct": round(std_annual * 100, 2),
            "informationRatio": round(info_ratio, 4),
            "tStat": round(t_stat, 2),
            "deflatedSharpe": dsr,
        }
        print(
            f"  {row['label']:32s} 초과 {mean_annual*100:+6.2f}%/년 / "
            f"추적오차 {std_annual*100:5.2f}% / IR {info_ratio:+6.3f} / "
            f"t {t_stat:+5.2f} / DSR p {dsr['pValue']:.4f}"
        )
    print("  t 절댓값이 1.96 미만이면 초과분이 0과 구분되지 않는다.")
    print()

    # 연도별 / 약세 연도
    print("=== 연도별 수익 (%) ===")
    labels = list(equities)
    print(f"{'연도':6s}" + "".join(f"{lab[:18]:>20s}" for lab in labels))
    yearly: dict[str, dict[int, float]] = {lab: {} for lab in labels}
    for year in sorted(predictions["testYear"].unique()):
        line = f"{year:<6d}"
        for lab in labels:
            equity = equities[lab]
            window = equity[equity.index.year == year]
            value = (float(window.iloc[-1] / window.iloc[0]) - 1.0) * 100 if len(window) > 1 else float("nan")
            yearly[lab][int(year)] = round(value, 2)
            line += f"{value:19.1f}%"
        mark = "  <- 약세" if year in BEAR_YEARS else ""
        print(line + mark)
    print()

    print("=== 약세 연도 평균 (%) ===")
    for lab in labels:
        vals = [v for y, v in yearly[lab].items() if y in BEAR_YEARS and not math.isnan(v)]
        print(f"  {lab:32s} {sum(vals)/len(vals) if vals else float('nan'):7.2f}%  (연 {len(vals)}개)")
    print()

    verdict_rows = []
    bench = results[0]
    for row in results[1:]:
        dsr = dsr_report[str(row["label"])]
        beats_sharpe = float(row["sharpe"]) > float(bench["sharpe"])
        dsr_positive = float(dsr["pValue"]) < 0.05
        bear_bench = [v for y, v in yearly[str(bench["label"])].items() if y in BEAR_YEARS]
        bear_strat = [v for y, v in yearly[str(row["label"])].items() if y in BEAR_YEARS]
        better_bear = (sum(bear_strat) / len(bear_strat)) > (sum(bear_bench) / len(bear_bench))
        adopted = beats_sharpe and dsr_positive and better_bear
        verdict_rows.append(
            {
                "label": row["label"],
                "beatsBenchmarkSharpe": beats_sharpe,
                "deflatedSharpeSignificant": dsr_positive,
                "betterInBearYears": better_bear,
                "verdict": "ADOPT" if adopted else "REJECT",
            }
        )

    print("=== 사전 확정 판정 기준 적용 ===")
    for row in verdict_rows:
        print(
            f"  {row['label']:32s} Sharpe초과 {str(row['beatsBenchmarkSharpe']):5s} / "
            f"DSR유의 {str(row['deflatedSharpeSignificant']):5s} / "
            f"약세우위 {str(row['betterInBearYears']):5s} -> {row['verdict']}"
        )
    final = "PASS" if any(r["verdict"] == "ADOPT" for r in verdict_rows) else "BELOW_BASELINE"
    print()
    print(f"MODEL_QUALITY={final}")

    (CACHE / "profit_verification.json").write_text(
        json.dumps(
            {
                "predictionRows": int(len(predictions)),
                "symbols": int(predictions["ticker"].nunique()),
                "folds": int(predictions["testYear"].nunique()),
                "firstTestYear": int(predictions["testYear"].min()),
                "lastTestYear": int(predictions["testYear"].max()),
                "directionAccuracy": round(dir_acc, 4),
                "coinFlip95": [
                    round(0.5 - 1.96 * stderr, 4),
                    round(0.5 + 1.96 * stderr, 4),
                ],
                "rmseModel": round(model_rmse, 6),
                "rmseNaiveZero": round(naive_rmse, 6),
                "expectedReturnRangePct": [
                    round(float((ratio.min() - 1) * 100), 2),
                    round(float((ratio.max() - 1) * 100), 2),
                ],
                "roundTripCostBps": ROUND_TRIP_BPS,
                "topK": TOP_K,
                "performance": results,
                "deflatedSharpe": dsr_report,
                "excessOverBenchmark": excess_report,
                "yearly": yearly,
                "verdicts": verdict_rows,
                "modelQuality": final,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"저장: {CACHE / 'profit_verification.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
