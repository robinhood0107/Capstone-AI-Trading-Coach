"""RULE_BASELINE 판정 규칙을 26년 PIT 패널에서 문헌 표준 방식으로 비교한다.

`collect_history.py`로 `/tmp/p1exp/long_history.parquet`을 만든 뒤 실행한다. (return-engine venv)

## 왜 이 하네스가 필요했는가

`daily_inference._features_and_rule`의 판정을 바꿀 때 처음에는 DB에 있던 83세션(2026-04~09)
으로 쟀다. 두 가지가 틀렸다.

1. **표본이 짧고 한 국면이었다.** 그 창은 exact-31이 폭등한 구간이다. 이 프로젝트의 평가
   프로토콜(결정 등록부 #13·#14)은 PIT walk-forward와 다중검정 보정을 요구한다.
2. **검정이 틀렸다.** 신호가 뜬 (날짜, 종목) 관측 수십만 개를 pooled로 t검정했다. 같은 날
   종목들은 시장 성분을 공유해 강하게 상관되므로 유효 표본은 관측 수가 아니라 **날짜 수**다.
   pooled t는 크게 부풀려진다.

그래서 이 파일이 표준 방식을 고정한다. 앞으로 판정 규칙을 건드릴 때는 여기서 재고, 짧은 창의
수치로 결정하지 않는다.

## 방법

| 항목 | 값 |
|---|---|
| 기간 | 전체 이력 (2000-02 ~ 2026-09, 6,646세션, 158,336관측) |
| 유니버스 | exact-31. 종목은 상장 시점부터만 등장하므로 point-in-time이 자동이다 |
| 초과수익 | 날짜별 `신호 종목 등가중 − 같은 날 유니버스 등가중`. 시장 성분이 매일 상쇄되므로 국면 편향이 구조적으로 없다 |
| 검정 | Fama & MacBeth (1973) — 날짜별 횡단면 통계의 시계열을 검정. 자기상관은 Newey & West (1987)로 보정 |
| 다중검정 | 시행 횟수를 출력하고 Bonferroni 임계를 함께 본다 (Bailey & López de Prado) |
| 분해 | 10년 단위와 하락 연도(2008/2011/2015/2018/2020/2022)를 따로 본다 |

## 후보 (문헌 근거만. 그리드 탐색이 아니다)

| 이름 | 조건 | 근거 |
|---|---|---|
| `event` | `ma5>ma20 and prior_ma5<=prior_ma20 and rsi<70` | 그 날 골든크로스. Brock·Lakonishok·LeBaron (1992)의 "fixed" 변형에 가깝다 |
| `state` | `ma5>ma20 and rsi<70` | BLL (1992)의 VMA — 빠른 MA가 느린 MA 위에 있는 **상태**. 이동평균 규칙의 표준형 |
| `state_band` | `ma5>ma20*1.01 and rsi<70` | BLL (1992)이 whipsaw 감소로 권하는 밴드 |
| `price_ma20` | `close>ma20 and rsi<70` | Faber (2007) 류 단일 MA 상태 |

## 판정 (2026-09-04 실행)

```
변형             BUY/일    0인날       초과수익/일    NW t     날짜수   양(+)일
event           0.73  55.8%     -0.0751%p   -2.02   2,939   45.5%
state           9.17   1.9%     -0.0143%p   -1.11   6,518   49.9%
state_band      6.82   3.2%     -0.0149%p   -1.00   6,431   49.7%
price_ma20      9.11   1.7%     -0.0044%p   -0.36   6,530   49.7%
```

`event`만 일관되게 음의 방향이다 — 10년 구간 셋 모두, 하락 연도 6개 중 5개에서 음수.
나머지 셋은 다중검정 보정 후 **0과 구분되지 않는다**. 추세 필터에 문헌이 기대하는 역할이
알파가 아니라 위험 통제이므로 중립은 정상이다.

`state`를 채택했다. `price_ma20`과 통계적으로 구분되지 않지만 계약 feature에 `ma5`와 `ma20`이
이미 둘 다 있어 두 MA의 관계를 쓰는 것이 계약과 일치하고, Team B의 `from_baseline`도 두 MA를
쓴다. 밴드는 우리 표본에 맞춘 상수를 하나 더 들여오므로 범용성이 떨어져 기각했다.

**후보 0인 날이 1.9% 남는 것은 결함이 아니다.** 유니버스 전 종목이 하락 추세인 날 매수 후보가
없는 것이 정상이다. 반대로 `event`의 55.8%는 규칙이 드문 사건을 요구해서 생긴 구조적 성질이고
짧은 표본의 산물이 아니다 — 26년에서도 같은 값이다.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pandas as pd

REPO = pathlib.Path(__file__).resolve().parents[4]
CACHE = pathlib.Path("/tmp/p1exp/long_history.parquet")
NEWEY_WEST_LAGS = 5
BEAR_YEARS = (2008, 2011, 2015, 2018, 2020, 2022)
DECADES = ((1997, 2005), (2006, 2015), (2016, 2026))


def load_panel() -> pd.DataFrame:
    """exact-31 만의 PIT 패널. MA5/MA20/RSI14 와 1일 선행 수익률을 붙인다."""

    catalog = json.loads(
        (REPO / "contracts/catalogs/p1-return-universe.v1.json").read_text(encoding="utf-8")
    )
    tickers = {item["yfinanceTicker"] for item in catalog["symbols"]}
    frame = pd.read_parquet(CACHE)
    frame = frame[frame.ticker.isin(tickers)].copy()

    rows = []
    for ticker, group in frame.groupby("ticker", sort=True):
        group = group.sort_values("Date").reset_index(drop=True)
        closes = group.Close.astype(float)
        ma5 = closes.rolling(5).mean()
        ma20 = closes.rolling(20).mean()
        rows.append(
            pd.DataFrame(
                {
                    "ticker": ticker,
                    "date": group.Date,
                    "close": closes,
                    "ma5": ma5,
                    "ma20": ma20,
                    "rsi": relative_strength_index(closes),
                    "prior_ma5": ma5.shift(1),
                    "prior_ma20": ma20.shift(1),
                    "forward": closes.shift(-1) / closes - 1.0,
                }
            )
        )
    panel = pd.concat(rows, ignore_index=True).dropna(
        subset=["ma5", "ma20", "rsi", "prior_ma5", "prior_ma20", "forward"]
    )
    return panel


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


def variants(panel: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "event": (panel.ma5 > panel.ma20)
        & (panel.prior_ma5 <= panel.prior_ma20)
        & (panel.rsi < 70),
        "state": (panel.ma5 > panel.ma20) & (panel.rsi < 70),
        "state_band": (panel.ma5 > panel.ma20 * 1.01) & (panel.rsi < 70),
        "price_ma20": (panel.close > panel.ma20) & (panel.rsi < 70),
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
    """날짜별 (신호 종목 등가중 - 유니버스 등가중) 시계열과 그 날의 신호 종목 수."""

    universe_mean = panel.groupby("date").forward.mean()
    signal_mean = panel[mask].groupby("date").forward.mean()
    count = panel.assign(_signal=mask).groupby("date")._signal.sum()
    return (signal_mean - universe_mean).reindex(universe_mean.index), count


def main() -> int:
    panel = load_panel()
    candidates = variants(panel)
    print(
        f"exact-31 PIT 패널: {len(panel):,}관측  {panel.ticker.nunique()}종목  "
        f"세션 {panel.date.nunique():,}개  {panel.date.min().date()} ~ {panel.date.max().date()}"
    )

    print("\n=== 전체 기간 · Fama-MacBeth (날짜 시계열, Newey-West 5래그) ===")
    header = f"{'변형':12s} {'BUY/일':>7s} {'0인날':>6s} {'초과수익/일':>12s} {'NW t':>7s} {'날짜수':>7s}"
    print(header)
    print("-" * len(header))
    for name, mask in candidates.items():
        spread, count = fama_macbeth(panel, mask)
        valid = spread.dropna()
        print(
            f"{name:12s} {count.mean():7.2f} {100 * (count == 0).mean():5.1f}% "
            f"{100 * valid.mean():+11.4f}%p {newey_west_t(valid):+7.2f} {len(valid):7,d}"
        )
    print(
        f"시행 {len(candidates)}회 -> Bonferroni 임계 약 |t| > 2.50 "
        "(Bailey & Lopez de Prado 의 다중검정)"
    )

    for label, groups in (("10년 단위", DECADES), ("하락 연도", tuple((y, y) for y in BEAR_YEARS))):
        print(f"\n=== {label} (초과수익 %p/일) ===")
        widths = [f"{a}-{b}" if a != b else str(a) for a, b in groups]
        print(f"{'변형':12s}" + "".join(f"{w:>13s}" for w in widths))
        for name, mask in candidates.items():
            line = f"{name:12s}"
            for start, end in groups:
                spread, _ = fama_macbeth(panel, mask)
                window = spread[
                    (spread.index.year >= start) & (spread.index.year <= end)
                ].dropna()
                line += f"{100 * window.mean():+12.4f} " if len(window) else f"{'-':>13s}"
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
