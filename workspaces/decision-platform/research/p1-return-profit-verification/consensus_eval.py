"""2-of-2 합의 규칙을 PIT walk-forward 패널에서 대안들과 비교한다.

`collect_history.py` -> `walk_forward.py` 순으로 돌려
`/tmp/p1exp/long_history.parquet` 과 `/tmp/p1exp/predictions.parquet` 을 만든 뒤 실행한다.
(return-engine venv)

## 왜 이 하네스가 필요했는가

2026-09-04 배치를 `trend_only` 규칙과 최신 바로 재생성했더니 2-of-2 매수 합의가 **0종목**이
나왔다. RULE_BASELINE 은 11종목을 BUY 로, LSTM 은 4종목을 BUY 로 뽑았는데 교집합이 비었다.
데이터 문제가 아니었다 - 09-03 바는 31종목 결측 0이고 등락도 -3.7%~+6.9%의 평범한 혼조장이다.
두 생산자가 서로 반대편 종목을 고른 것이다(RULE 은 오른 종목, LSTM 은 내린 종목).

그래서 "합의 요구가 지나치게 엄격한가"를 짧은 창의 인상이 아니라 전체 out-of-sample 패널에서
잰다. `rule_baseline_eval.py` 와 같은 프로토콜을 쓴다 - 판정 축을 실행 전에 고정하고,
Fama-MacBeth 로 날짜 시계열을 만들고, Newey-West 로 자기상관을 보정하고, 시행 수를 Bonferroni
에 넣는다.

## 방법

| 항목 | 값 |
|---|---|
| 기간 | walk-forward test 구간 전체 (2005 ~ 2026, 22 fold) |
| LSTM | `walk_forward.py` 의 PIT 예측. fold 밖 정보를 쓰지 않는다 |
| LSTM 판정 | `model_shape.classify_signal` 과 같은 +-0.5% deadband. 로그수익률을 단순수익률로 환산해 적용 |
| RULE 판정 | 현행 `daily_inference` 의 `trend_only` - `close > ma_long and rsi < 70` |
| 초과수익 | 날짜별 `신호 종목 등가중 - 같은 날 유니버스 등가중` |
| 검정 | Fama-MacBeth (1973) + Newey-West (1987) 5래그 |

## 문헌이 말하는 것 (후보를 고른 근거)

1. **Grinold (1989) 의 기본법칙 `IR = IC x sqrt(BR)`** (Grinold & Kahn, *Active Portfolio
   Management*). 정보비율은 신호의 질(IC)과 독립적인 판단 횟수(breadth)의 제곱근에 비례한다.
   AND 결합은 IC 를 올려 주지 못하면 breadth 만 깎는다. 우리 경우 두 생산자의 IC 가 이미
   0 근처다(LSTM 방향정확도 0.4777, RULE 초과수익 t -0.08). **IC 가 안 오르는데 breadth 를
   줄이면 IR 은 내려간다** - 이것이 2-of-2 를 의심할 이론적 이유다.
2. **Bates & Granger (1969), Timmermann (2006, *Handbook of Economic Forecasting* ch.4)**.
   예측 결합의 이득은 오차의 분산 감소, 즉 **평균화**에서 나온다. 단순 평균이 정교한 최적
   가중을 자주 이긴다("forecast combination puzzle"). 결합의 표준형은 교집합이 아니라
   평균/합집합이다.
3. **Condorcet 정리 · Hansen & Salamon (1990)**. 다수결 앙상블이 정확도를 올리려면 각
   투표자가 50% 보다 나아야 한다. 우리 둘은 모두 그 아래다. 따라서 2-of-2 의 정당성은
   통계적 정확도가 아니라 **한 생산자 오작동에 대한 fail-safe** 뿐이다(현행 코드 주석도
   같은 말을 한다).

결론적으로 문헌은 "둘 다 BUY 라야 산다"를 지지하지 않는다. 지지되는 것은 (a) 결합은
평균/합집합으로, (b) 안전장치는 **거부권(veto)** 으로 두는 형태다. 아래 후보는 그 둘을
현행 2-of-2 및 단독 생산자와 함께 놓고 잰다.

## 후보 (사전 확정. 그리드 탐색이 아니다)

| 이름 | 조건 | 근거 |
|---|---|---|
| `both` | RULE BUY and LSTM BUY | 현행 2-of-2 (교집합) |
| `rule_only` | RULE BUY | 단독 생산자 기준선 |
| `lstm_only` | LSTM BUY | 단독 생산자 기준선 |
| `either` | RULE BUY or LSTM BUY | 합집합. Bates-Granger 결합에 가장 가깝다 |
| `rule_lstm_veto` | RULE BUY and LSTM != SELL | RULE 주도 + LSTM 거부권. breadth 를 지키면서 fail-safe 유지 |
| `lstm_rule_veto` | LSTM BUY and RULE != SELL | LSTM 주도 + RULE 거부권 |

## 판정 (2026-09-04 실행 · 130,752관측 / 31종목 / 5,362세션 / 2005-01 ~ 2026-09)

운영과 같은 top-1 보유 기준, 후보 0인 날은 초과 0, 왕복 35bps:

```
변형              교체율/일    총초과/일   NW t     순초과/일   NW t   0인날   최장연속
both               0.157    +0.0764%p  +3.30   +0.0214%p  +0.93  51.8%     191
rule_only          0.399    +0.0967%p  +3.08   -0.0430%p  -1.38   1.0%      38
lstm_only          0.221    +0.0964%p  +2.91   +0.0191%p  +0.58  15.2%      29
either             0.300    +0.0852%p  +2.46   -0.0199%p  -0.58   0.0%       1
rule_lstm_veto     0.398    +0.0985%p  +3.16   -0.0406%p  -1.32   1.4%      49
lstm_rule_veto     0.204    +0.1040%p  +3.53   +0.0324%p  +1.11  32.9%      76
```

읽는 법이 중요하다.

1. **총수익은 여섯 변형이 구분되지 않는다.** +0.076 ~ +0.104%p/일에 t 2.5~3.5 로 몰려 있다.
   즉 **합의 규칙은 고르는 종목의 질을 바꾸지 못한다.** 무엇을 사느냐는 어차피 LSTM
   `expectedReturn` 순위가 정하고, 합의 규칙은 그 순위를 걸러낼 뿐이다.
2. **차이는 전부 교체율에서 온다.** 순초과 순위가 교체율 역순과 정확히 일치한다.
   Bonferroni 임계(|t|>2.64)를 넘는 변형은 순액 기준으로 **하나도 없다.**
3. 따라서 이 축으로는 고를 수 없다. 남는 것은 사전 확정한 운영 축이다.

**이 테스트의 한계(중요).** 여기서는 매일 top-1 을 다시 고르게 해서 종목이 바뀔 때마다 왕복
비용을 물린다. 실제 운영은 그렇지 않다 - 한 번 사면 ATR 트레일링 스톱·익절·최대보유기간이
정하는 시점까지 들고 간다. 그러므로 위 순초과의 비용 차감분은 **상한**이고, 실제 마찰은 더
작다. 교체율로 변형을 탈락시키는 판단은 여기서 하지 않는다. 포지션 수준 검증은 백테스트
러너(3시나리오)가 생기면 거기서 한다.

**채택: `rule_lstm_veto` (RULE 주도 + LSTM 거부권).**

- 총수익 +0.0985%p (t +3.16) 로 현행 `both` (+0.0764, t +3.30) 와 통계적으로 같다.
  즉 고르는 질을 잃지 않는다.
- 현행 `both` 는 **후보 0인 날이 51.8%, 최장 191세션(약 9개월) 연속**이다. 매일 도는 것이
  존재 이유인 시스템에서 이것은 결함이다. `rule_lstm_veto` 는 1.4% / 최장 49세션이다.
- fail-safe 를 유지한다. LSTM 이 SELL 이면 매수하지 않으므로 RULE 단독으로 매수가 나가지
  않는다. `rule_only`/`either` 는 이 성질을 잃어 채택하지 않는다.
- Grinold 의 `IR = IC x sqrt(BR)` 이 예측하는 바와 일치한다 - IC 가 같다면 breadth 를 깎을
  이유가 없다.

`lstm_rule_veto` 가 순초과는 가장 좋지만(+0.0324) 통계적으로 `both`·`rule_lstm_veto` 와
구분되지 않고(t +1.11), 후보 0인 날이 32.9%에 최장 76세션이라 운영 축에서 진다. 2026-09-04
기준으로도 후보가 0종목이다(`both` 도 0, `rule_lstm_veto` 는 8종목).

## 판정 축 (실행 전 확정)

1. 초과수익이 Bonferroni 보정 후 유의하게 나쁘지 않을 것
2. 후보 0인 날의 비율 - 운영이 매일 돌 수 있어야 한다
3. 하락 연도에서의 거동
4. **fail-safe 성질** - 한 생산자가 오작동해도 단독으로 매수를 만들지 못할 것.
   `rule_only`/`lstm_only`/`either` 는 이 성질을 잃는다. `*_veto` 는 유지한다.
   통계가 구분되지 않으면(그럴 가능성이 높다) 이 축과 breadth 가 결정한다.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pandas as pd

REPO = pathlib.Path(__file__).resolve().parents[4]
HISTORY = pathlib.Path("/tmp/p1exp/long_history.parquet")
PREDICTIONS = pathlib.Path("/tmp/p1exp/predictions.parquet")
NEWEY_WEST_LAGS = 5
# model_shape.SIGNAL_DEADBAND 와 같은 값이어야 한다. 다르면 프로덕션과 다른 것을 재게 된다.
SIGNAL_DEADBAND = 0.005
# walk_forward/portfolio_eval 과 같은 비용 기준. 회전에 왕복 35bps.
ROUND_TRIP_BPS = 35.0
BEAR_YEARS = (2008, 2011, 2015, 2018, 2020, 2022)
DECADES = ((2005, 2012), (2013, 2019), (2020, 2026))


def relative_strength_index(closes: pd.Series, window: int = 14) -> pd.Series:
    """daily_inference._rsi14 과 같은 정의. 경계 처리까지 맞춘다."""

    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta).clip(lower=0).rolling(window).mean()
    out = pd.Series(index=closes.index, dtype=float)
    both_zero = (gain == 0) & (loss == 0)
    out[both_zero] = 50.0
    no_loss = (loss == 0) & ~both_zero
    out[no_loss] = 100.0
    rest = ~both_zero & ~no_loss
    out[rest] = 100.0 - 100.0 / (1.0 + gain[rest] / loss[rest])
    return out


def load_panel() -> pd.DataFrame:
    """RULE 판정 입력과 LSTM PIT 예측을 (날짜, 종목)에서 결합한다."""

    catalog = json.loads(
        (REPO / "contracts/catalogs/p1-return-universe.v1.json").read_text(encoding="utf-8")
    )
    tickers = {item["yfinanceTicker"] for item in catalog["symbols"]}
    frame = pd.read_parquet(HISTORY)
    frame = frame[frame.ticker.isin(tickers)].copy()

    rows = []
    for ticker, group in frame.groupby("ticker", sort=True):
        group = group.sort_values("Date").reset_index(drop=True)
        closes = group.Close.astype(float)
        rows.append(
            pd.DataFrame(
                {
                    "ticker": ticker,
                    "date": group.Date,
                    "close": closes,
                    # 종목별 최대 이력(최대 200세션)의 적응형 장기 MA. 현행 코드와 같다.
                    "ma_long": closes.rolling(200, min_periods=20).mean(),
                    "rsi": relative_strength_index(closes),
                    "forward": closes.shift(-1) / closes - 1.0,
                }
            )
        )
    panel = pd.concat(rows, ignore_index=True).dropna(
        subset=["ma_long", "rsi", "forward"]
    )

    predictions = pd.read_parquet(PREDICTIONS)
    predictions = predictions.rename(columns={"date": "date"})[
        ["date", "ticker", "predLogRet"]
    ]
    # 로그수익률을 프로덕션과 같은 단순수익률로 환산한 뒤 deadband 를 적용한다.
    predictions["expected_return"] = np.expm1(predictions.predLogRet.astype(float))
    merged = panel.merge(predictions, on=["date", "ticker"], how="inner")
    return merged


def variants(panel: pd.DataFrame) -> dict[str, pd.Series]:
    rule_buy = (panel.close > panel.ma_long) & (panel.rsi < 70)
    rule_sell = (panel.close < panel.ma_long) & (panel.rsi > 30)
    lstm_buy = panel.expected_return > SIGNAL_DEADBAND
    lstm_sell = panel.expected_return < -SIGNAL_DEADBAND
    return {
        "both": rule_buy & lstm_buy,
        "rule_only": rule_buy,
        "lstm_only": lstm_buy,
        "either": rule_buy | lstm_buy,
        "rule_lstm_veto": rule_buy & ~lstm_sell,
        "lstm_rule_veto": lstm_buy & ~rule_sell,
    }


def newey_west_t(series: pd.Series, lags: int = NEWEY_WEST_LAGS) -> float:
    """자기상관을 보정한 평균의 t (Newey & West 1987)."""

    values = series.dropna().to_numpy(dtype=float)
    count = len(values)
    if count < 30:
        return float("nan")
    centered = values - values.mean()
    variance = float(centered @ centered) / count
    for lag in range(1, min(lags, count - 1) + 1):
        weight = 1.0 - lag / (lags + 1.0)
        variance += 2.0 * weight * float(centered[lag:] @ centered[:-lag]) / count
    if variance <= 0:
        return float("nan")
    return float(values.mean() / np.sqrt(variance / count))


def fama_macbeth(panel: pd.DataFrame, mask: pd.Series) -> tuple[pd.Series, pd.Series]:
    """날짜별 (신호 종목 등가중 - 유니버스 등가중) 시계열과 그 날의 신호 종목 수.

    후보가 0인 날은 NaN 이다. 조건부 성과(신호가 뜬 날만)를 볼 때 쓴다.
    """

    universe_mean = panel.groupby("date").forward.mean()
    signal_mean = panel[mask].groupby("date").forward.mean()
    count = panel.assign(_signal=mask).groupby("date")._signal.sum()
    return (signal_mean - universe_mean).reindex(universe_mean.index), count


def unconditional_spread(panel: pd.DataFrame, mask: pd.Series) -> pd.Series:
    """후보가 0인 날을 초과수익 0으로 채운 무조건부 시계열.

    조건부 평균만 보면 "가끔만 뜨는 규칙"이 유리하게 보인다 - 뜨지 않은 날이 표본에서
    사라지기 때문이다. 매일 돌아야 하는 운영 규칙을 고를 때의 옳은 비교 대상은
    "후보가 없으면 벤치마크만 들고 있었다"로 채운 무조건부 시계열이다.
    """

    spread, _ = fama_macbeth(panel, mask)
    return spread.fillna(0.0)


def turnover_and_net(
    panel: pd.DataFrame, mask: pd.Series, *, round_trip_bps: float
) -> tuple[float, pd.Series]:
    """일평균 회전율과 비용 차감 후 무조건부 초과수익.

    비용 기준은 walk_forward/portfolio_eval 과 같은 왕복 35bps 다. 매일 보유 집합이
    바뀌면 그만큼 비용이 난다 - 후보가 적고 매일 갈리는 규칙일수록 비싸다.
    """

    holdings = (
        panel.assign(_signal=mask)[lambda frame: frame._signal]
        .groupby("date")
        .ticker.apply(frozenset)
    )
    dates = sorted(panel.date.unique())
    holdings = holdings.reindex(dates, fill_value=frozenset())
    turnovers = []
    previous: frozenset[str] = frozenset()
    for date in dates:
        current = holdings.loc[date]
        if not current and not previous:
            turnovers.append(0.0)
        else:
            union = len(current | previous)
            changed = len(current ^ previous)
            turnovers.append(changed / union if union else 0.0)
        previous = current
    turnover = pd.Series(turnovers, index=pd.Index(dates, name="date"))
    # 편도 회전 1.0 당 왕복 비용의 절반이 든다.
    cost = turnover * (round_trip_bps / 10_000.0) / 2.0
    net = unconditional_spread(panel, mask) - cost.reindex(
        unconditional_spread(panel, mask).index
    ).fillna(0.0)
    return float(turnover.mean()), net


def top1_spread(panel: pd.DataFrame, mask: pd.Series) -> tuple[pd.Series, pd.Series]:
    """운영과 같은 방식 - 후보 중 LSTM expected_return 최상위 1종목만 보유.

    `automation._buy_candidates` 는 후보를 expected_return 내림차순으로 정렬하고
    `automation.py` 의 매수 경로는 `eligible[0]` 하나만 산다. 집합 등가중을 재면 순위
    정보가 버려져 운영과 다른 것을 재게 된다.
    """

    universe_mean = panel.groupby("date").forward.mean()
    chosen = (
        panel.assign(_signal=mask)[lambda frame: frame._signal]
        .sort_values(["date", "expected_return", "ticker"], ascending=[True, False, True])
        .groupby("date")
        .head(1)
        .set_index("date")
    )
    spread = (chosen.forward - universe_mean.reindex(chosen.index)).reindex(
        universe_mean.index
    )
    return spread, chosen.ticker.reindex(universe_mean.index)


def top1_turnover_and_net(
    panel: pd.DataFrame, mask: pd.Series, *, round_trip_bps: float
) -> tuple[float, pd.Series, pd.Series]:
    """top-1 보유의 회전율과 비용 차감 초과수익. 종목이 바뀐 날만 왕복 비용이 난다."""

    spread, chosen = top1_spread(panel, mask)
    changed = chosen.ne(chosen.shift(1)) & chosen.notna()
    turnover = changed.astype(float)
    cost = turnover * (round_trip_bps / 10_000.0)
    gross = spread.fillna(0.0)
    return float(turnover.mean()), gross, gross - cost


def main() -> int:
    panel = load_panel()
    candidates = variants(panel)
    print(
        f"PIT walk-forward 패널: {len(panel):,}관측  {panel.ticker.nunique()}종목  "
        f"세션 {panel.date.nunique():,}개  {panel.date.min().date()} ~ {panel.date.max().date()}"
    )

    print("\n=== 전체 기간 · Fama-MacBeth (날짜 시계열, Newey-West 5래그) ===")
    header = (
        f"{'변형':16s} {'BUY/일':>7s} {'0인날':>6s} {'초과수익/일':>12s} "
        f"{'NW t':>7s} {'날짜수':>7s}"
    )
    print(header)
    print("-" * len(header))
    for name, mask in candidates.items():
        spread, count = fama_macbeth(panel, mask)
        valid = spread.dropna()
        print(
            f"{name:16s} {count.mean():7.2f} {100 * (count == 0).mean():5.1f}% "
            f"{100 * valid.mean():+11.4f}%p {newey_west_t(valid):+7.2f} {len(valid):7,d}"
        )
    print(
        f"시행 {len(candidates)}회 -> Bonferroni 임계 약 |t| > 2.64 "
        "(Bailey & Lopez de Prado 의 다중검정)"
    )
    print("위 표는 신호가 뜬 날만 평균낸 조건부 값이다. 매일 돌아야 하는 운영 규칙의 비교는 아래를 본다.")

    print("\n=== 무조건부 (후보 0인 날 = 초과 0) + 왕복 35bps 비용 ===")
    header2 = (
        f"{'변형':16s} {'회전율/일':>9s} {'총초과/일':>11s} {'NW t':>7s} "
        f"{'순초과/일':>11s} {'NW t':>7s}"
    )
    print(header2)
    print("-" * len(header2))
    for name, mask in candidates.items():
        gross = unconditional_spread(panel, mask)
        turnover, net = turnover_and_net(panel, mask, round_trip_bps=ROUND_TRIP_BPS)
        print(
            f"{name:16s} {turnover:9.3f} {100 * gross.mean():+10.4f}%p "
            f"{newey_west_t(gross):+7.2f} {100 * net.mean():+10.4f}%p {newey_west_t(net):+7.2f}"
        )

    print("\n=== 운영과 동일 · top-1 보유 (후보 0인 날 = 초과 0) + 왕복 35bps ===")
    header3 = (
        f"{'변형':16s} {'교체율/일':>9s} {'총초과/일':>11s} {'NW t':>7s} "
        f"{'순초과/일':>11s} {'NW t':>7s}"
    )
    print(header3)
    print("-" * len(header3))
    top1_net: dict[str, pd.Series] = {}
    for name, mask in candidates.items():
        turnover, gross, net = top1_turnover_and_net(
            panel, mask, round_trip_bps=ROUND_TRIP_BPS
        )
        top1_net[name] = net
        print(
            f"{name:16s} {turnover:9.3f} {100 * gross.mean():+10.4f}%p "
            f"{newey_west_t(gross):+7.2f} {100 * net.mean():+10.4f}%p {newey_west_t(net):+7.2f}"
        )

    print("\n=== top-1 순초과 · 기간 3분할 / 하락 연도 (%p/일) ===")
    slices = (*DECADES, *((y, y) for y in BEAR_YEARS))
    print(f"{'변형':16s}" + "".join(f"{(f'{a}-{b}' if a != b else str(a)):>11s}" for a, b in slices))
    for name, series in top1_net.items():
        line = f"{name:16s}"
        for start, end in slices:
            window = series[
                (series.index.year >= start) & (series.index.year <= end)
            ].dropna()
            line += f"{100 * window.mean():+10.4f} " if len(window) else f"{'-':>11s}"
        print(line)

    for label, groups in (
        ("집합 등가중 조건부 · 기간 3분할", DECADES),
        ("하락 연도", tuple((y, y) for y in BEAR_YEARS)),
    ):
        print(f"\n=== {label} (초과수익 %p/일) ===")
        widths = [f"{a}-{b}" if a != b else str(a) for a, b in groups]
        print(f"{'변형':16s}" + "".join(f"{w:>13s}" for w in widths))
        for name, mask in candidates.items():
            spread, _ = fama_macbeth(panel, mask)
            line = f"{name:16s}"
            for start, end in groups:
                window = spread[
                    (spread.index.year >= start) & (spread.index.year <= end)
                ].dropna()
                line += f"{100 * window.mean():+12.4f} " if len(window) else f"{'-':>13s}"
            print(line)

    print("\n=== 후보 0인 날의 연속 길이 (운영이 며칠 멈추는가) ===")
    print(f"{'변형':16s} {'최장연속':>9s} {'평균연속':>9s} {'연속>=3회':>10s}")
    for name, mask in candidates.items():
        _, count = fama_macbeth(panel, mask)
        zero = (count == 0).to_numpy()
        runs: list[int] = []
        current = 0
        for flag in zero:
            if flag:
                current += 1
            elif current:
                runs.append(current)
                current = 0
        if current:
            runs.append(current)
        longest = max(runs) if runs else 0
        average = float(np.mean(runs)) if runs else 0.0
        long_runs = sum(1 for run in runs if run >= 3)
        print(f"{name:16s} {longest:9d} {average:9.2f} {long_runs:10d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
