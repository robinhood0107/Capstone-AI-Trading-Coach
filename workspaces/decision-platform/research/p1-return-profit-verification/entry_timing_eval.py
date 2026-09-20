"""진입가가 앵커(전일 종가)에서 멀어질수록 성과가 나빠지는가 - 26년 PIT 평가.

실행:
    cd workspaces/decision-platform/python-services
    uv run --frozen python ../research/p1-return-profit-verification/entry_timing_eval.py

판정축은 `reports/entry-timing-protocol.v1.json` 에 **실행 전** 동결돼 있다. 외부 호출 0.

왜 이 질문인가
--------------
`forecast_close = prev_close * (1 + blendedExpectedReturn)` 는 전일 종가에 고정되는데
(`ridge_returns.py:76-88`, SQL `V153:96`) 진입 게이트의 분모는 실시간 호가다
(`automation.py:2514-2522`). 그래서 장중에 오를수록 `remaining_expected_return` 이
기계적으로 깎인다. 2026-09-15 실측에서 066570 은 예측종가 199,414 원인데 현재가가
203,500 원이라 잔여 -235.8bps 로 막혔다 - 모델이 방향을 맞췄는데 그래서 못 산 것이다.

무엇을 답할 수 없는가
---------------------
장중 데이터가 없다. 09:45 / 11:00 / 14:00 의 구분은 일봉으로 **불가능하다**.
`OPEN_T1` 과 `CLOSE_T1` 이 그 셋을 괄호 안에 넣을 뿐이다. 사다리가 단조로 나오면
진입을 앞당길 근거가 되고, 아니면 현행 유지다. 귀무 결과도 결과다.

기존 하니스와의 차이
--------------------
`multitrack_backtest.simulate` 의 진입 게이트는 `score <= 2*cost` 이고, 이는
`buy_limit_price == prev_close` 일 때만 프로덕션 게이트와 같다. 측정 대상 기구가 정확히
그 차이에 살기 때문에 여기서는 프로덕션 형태를 쓴다. 그 파일은 수정하지 않는다
(`multitrack-result.v1.json` 이 무효가 된다).
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Callable

import numpy as np
import pandas as pd

from app.p1_owner.automation_atr import (
    AtrHistoryError,
    CompletedDailyBar,
    advance_trailing_stop,
    wilder_atr,
)

HERE = pathlib.Path(__file__).resolve().parent
REPORTS = HERE / "reports"
PROTOCOL = REPORTS / "entry-timing-protocol.v1.json"
RESULT = REPORTS / "entry-timing-result.v1.json"


def _load(name: str, filename: str) -> Any:
    """연구 디렉터리는 패키지가 아니다. `multitrack_backtest.py:51` 과 같은 방식으로 읽는다."""

    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{filename} 을 불러올 수 없다")
    module = importlib.util.module_from_spec(spec)
    # dataclass 가 자기 모듈을 sys.modules 에서 되찾으므로 exec 전에 등록해야 한다.
    # 등록하지 않으면  가 None.__dict__ 로 죽는다.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_MULTI = _load("_multitrack", "multitrack_backtest.py")
_CONSENSUS = _load("_consensus", "consensus_eval.py")

Track = _MULTI.Track
Position = _MULTI.Position
TrackResult = _MULTI.TrackResult
load_panel: Callable[[], pd.DataFrame] = _MULTI.load_panel
_bars_for = _MULTI._bars_for
ROUND_TRIP_BPS: float = _MULTI.ROUND_TRIP_BPS
BEAR_YEARS: tuple[int, ...] = _MULTI.BEAR_YEARS
describe = _MULTI.describe
deflated_sharpe = _MULTI.deflated_sharpe
simple_returns = _MULTI.simple_returns

fama_macbeth = _CONSENSUS.fama_macbeth
newey_west_t = _CONSENSUS.newey_west_t

#: 프로덕션 게이트의 비용(`automation.py:_ROUND_TRIP_COST_BPS`)과 같은 값.
GATE_COST = ROUND_TRIP_BPS / 10_000.0
#: 한 레그당 비용.
LEG_COST = ROUND_TRIP_BPS / 2.0 / 10_000.0
#: 프로덕션 `_MAX_ORDERS_PER_SESSION`/후보 상한과 같은 5.
TOP_K = 5
#: 횡단면 층의 고정 보유 기간.
FORWARD_SESSIONS = 5


@dataclass(frozen=True, slots=True)
class Arm:
    name: str
    attainable: bool

    def entry_price(self, day: pd.DataFrame) -> pd.Series:
        raise NotImplementedError


def _entry_prices(day: pd.DataFrame, arm: str) -> pd.Series:
    """arm 별 진입가. 앵커(`prev_close`)는 모든 arm 에서 같다."""

    if arm == "CLOSE_T0":
        return day["prev_close"]
    if arm == "OPEN_T1":
        return day["open"]
    if arm == "MID_T1":
        return (day["open"] + day["close"]) / 2.0
    if arm == "TYPICAL_T1":
        return (day["high"] + day["low"] + day["close"]) / 3.0
    if arm == "CLOSE_T1":
        return day["close"]
    raise ValueError(f"unknown arm {arm}")


ARMS: tuple[str, ...] = ("CLOSE_T0", "OPEN_T1", "MID_T1", "TYPICAL_T1", "CLOSE_T1")
BASELINE = "TYPICAL_T1"

CORE = Track(
    name="CORE",
    slots=10,
    max_holding_sessions=60,
    stop_loss_bps=500,
    take_profit_bps=1000,
    atr_period=22,
    atr_multiplier_milli=3000,
)


def build_panel() -> pd.DataFrame:
    """`load_panel` 에 앵커(전일 종가)와 arm 별 진입가를 더한다."""

    panel = load_panel()
    panel = panel.sort_values(["ticker", "date"]).reset_index(drop=True)
    panel["prev_close"] = panel.groupby("ticker")["close"].shift(1)
    panel = panel.dropna(subset=["prev_close"]).reset_index(drop=True)
    # 프로덕션 forecast_close 와 같은 형태. 앵커는 어떤 arm 에서도 바뀌지 않는다.
    panel["forecast_close"] = panel["prev_close"] * (1.0 + panel["score"])
    for arm in ARMS:
        price = _entry_prices(panel, arm)
        panel[f"entry_{arm}"] = price
        # 프로덕션 게이트: forecast_close / buy_limit_price - 1 - cost
        panel[f"remaining_{arm}"] = panel["forecast_close"] / price - 1.0 - GATE_COST
    return panel.sort_values(["date", "ticker"]).reset_index(drop=True)


def split_gap_diagnostic(panel: pd.DataFrame) -> dict[str, float]:
    """분할 추정 갭. `auto_adjust=False` 라 분할일이 시가에 갭을 만든다."""

    ratio = (panel["open"] / panel["prev_close"] - 1.0).abs()
    suspicious = int((ratio > 0.25).sum())
    return {
        "rows": int(len(panel)),
        "suspiciousOpenGaps": suspicious,
        "suspiciousShare": float(suspicious / max(1, len(panel))),
    }


def _atr_value(bars: list[CompletedDailyBar], period: int, as_of: date) -> Decimal | None:
    try:
        return wilder_atr(tuple(bars), period=period, as_of_session=as_of).value_krw
    except (AtrHistoryError, ValueError):
        return None


def simulate_with_entry_price(panel: pd.DataFrame, arm: str) -> TrackResult:
    """`multitrack_backtest.simulate` 와 같은 청산 규칙. 진입가와 게이트만 다르다.

    청산 우선순위·당일청산 금지·ATR 커널 호출·비용 적용은 글자 그대로 같다.
    """

    sessions = sorted(panel["date"].unique())
    by_symbol = {
        symbol: group.sort_values("date").reset_index(drop=True)
        for symbol, group in panel.groupby("ticker", sort=True)
    }
    bar_cache = {symbol: _bars_for(frame) for symbol, frame in by_symbol.items()}
    index_of = {
        symbol: {stamp: i for i, stamp in enumerate(frame["date"])}
        for symbol, frame in by_symbol.items()
    }
    entry_column = f"entry_{arm}"
    remaining_column = f"remaining_{arm}"

    equity = 1.0
    curve: list[float] = []
    open_positions: list[Any] = []
    result = TrackResult(pd.Series(dtype=float))
    track = CORE

    for step, stamp in enumerate(sessions):
        day = panel[panel["date"] == stamp]
        price_of = dict(zip(day["ticker"], day["close"], strict=True))
        session_day = pd.Timestamp(stamp).date()

        survivors: list[Any] = []
        for position in open_positions:
            if position.entry_index == step:
                survivors.append(position)
                continue
            price = price_of.get(position.symbol)
            if price is None:
                survivors.append(position)
                continue
            gross = price / position.entry_price - 1.0
            net_bps = (gross - 2 * LEG_COST) * 10_000.0
            reason: str | None = None
            if net_bps <= -track.stop_loss_bps:
                reason = "STOP_LOSS"
            else:
                bars = bar_cache[position.symbol]
                cut = index_of[position.symbol].get(stamp)
                atr = (
                    _atr_value(bars[: cut + 1], track.atr_period, session_day)
                    if cut is not None
                    else None
                )
                if atr is not None:
                    try:
                        trailing = advance_trailing_stop(
                            previous_peak_price_krw=max(1, int(position.peak_price)),
                            completed_high_price_krw=max(1, int(position.peak_price)),
                            current_quote_price_krw=max(1, int(price)),
                            atr_value_krw=atr,
                            atr_multiplier_milli=track.atr_multiplier_milli,
                            previous_trailing_stop_krw=(
                                int(position.trailing_stop) if position.trailing_stop else None
                            ),
                        )
                    except AtrHistoryError:
                        trailing = None
                    if trailing is not None:
                        position.peak_price = float(trailing.peak_price_krw)
                        position.trailing_stop = float(trailing.trailing_stop_krw)
                        if price <= trailing.trailing_stop_krw:
                            reason = "ATR_TRAILING"
                if reason is None and net_bps >= track.take_profit_bps:
                    reason = "TAKE_PROFIT"
                if reason is None and step - position.entry_index >= track.max_holding_sessions:
                    reason = "MAX_HOLDING_SESSIONS"
            if reason is None:
                survivors.append(position)
                continue
            equity += position.shares * price * (1.0 - LEG_COST)
            result.exits_by_reason[reason] = result.exits_by_reason.get(reason, 0) + 1
        open_positions = survivors

        # 빈 슬롯 채우기. **순위는 score 가 아니라 remaining** - 프로덕션이 하는 그대로.
        held = {position.symbol for position in open_positions}
        ranked = day.sort_values(remaining_column, ascending=False)
        free = track.slots - len(open_positions)
        budget_per_slot = equity / track.slots
        for row in ranked.itertuples():
            if free <= 0:
                break
            remaining = getattr(row, remaining_column)
            if row.ticker in held or not np.isfinite(remaining) or remaining <= 0:
                continue
            price = float(getattr(row, entry_column))
            if price <= 0 or budget_per_slot <= 0 or equity < budget_per_slot:
                continue
            shares = budget_per_slot / price
            equity -= shares * price * (1.0 + LEG_COST)
            open_positions.append(
                Position(
                    symbol=row.ticker,
                    track=track.name,
                    entry_index=step,
                    entry_price=price,
                    shares=shares,
                    peak_price=price,
                )
            )
            held.add(row.ticker)
            result.trades += 1
            free -= 1

        held_value = sum(
            item.shares * price_of.get(item.symbol, item.entry_price) for item in open_positions
        )
        curve.append(equity + held_value)

    result.equity = pd.Series(curve, index=pd.DatetimeIndex(sessions), name="equity")
    return result


def cross_section_spread(panel: pd.DataFrame, arm: str) -> pd.Series:
    """층 A - 고정 5세션 순수익 위에서 (선택 - 유니버스) 일별 스프레드."""

    frame = panel.copy()
    frame["forward_close"] = frame.groupby("ticker")["close"].shift(-FORWARD_SESSIONS)
    frame = frame.dropna(subset=["forward_close"])
    entry = frame[f"entry_{arm}"]
    frame["forward"] = frame["forward_close"] / entry - 1.0 - 2.0 * LEG_COST

    remaining = frame[f"remaining_{arm}"]
    eligible = remaining.gt(0) & np.isfinite(remaining)
    rank = (
        frame[eligible]
        .groupby("date")[f"remaining_{arm}"]
        .rank(ascending=False, method="first")
    )
    mask = pd.Series(False, index=frame.index)
    mask.loc[rank[rank <= TOP_K].index] = True

    spread, _ = fama_macbeth(frame, mask)
    return spread.fillna(0.0)


def break_even_entry_penalty_bps(
    fast: pd.Series, slow: pd.Series, *, step: float = 1.0, cap: float = 200.0
) -> float:
    """빠른 arm 의 Sharpe 우위를 지우는 데 필요한 추가 진입 비용(bps).

    개장 스프레드가 더 넓다는 사실을 임의 상수 없이 표현한다. 이미 계산된 equity 에서
    유도하므로 새 trial 이 아니고 DSR trials 를 늘리지 않는다.
    """

    slow_sharpe = float(describe(slow, "slow")["sharpe"])
    penalty = 0.0
    while penalty <= cap:
        adjusted = fast * (1.0 - penalty / 10_000.0)
        if float(describe(adjusted, "fast")["sharpe"]) <= slow_sharpe:
            return penalty
        penalty += step
    return cap


def main() -> int:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    assert protocol["trials"] == len(ARMS), "protocol trials must match the frozen arm list"
    assert protocol["baselineArm"] == BASELINE

    panel = build_panel()
    diagnostic = split_gap_diagnostic(panel)
    print(
        f"panel rows={diagnostic['rows']:,} "
        f"suspicious open gaps={diagnostic['suspiciousOpenGaps']} "
        f"({diagnostic['suspiciousShare']:.4%})",
        flush=True,
    )

    performance: dict[str, dict[str, Any]] = {}
    equities: dict[str, pd.Series] = {}
    for arm in ARMS:
        result = simulate_with_entry_price(panel, arm)
        summary = dict(describe(result.equity, arm))
        summary["trades"] = result.trades
        summary["exitsByReason"] = dict(result.exits_by_reason)
        rets = pd.Series(simple_returns(result.equity.to_numpy()), index=result.equity.index[1:])
        summary["bearYearMean"] = (
            float(rets[rets.index.year.isin(BEAR_YEARS)].mean())
            if rets.index.year.isin(BEAR_YEARS).any()
            else float("nan")
        )
        performance[arm] = summary
        equities[arm] = result.equity
        print(f"{arm:12} sharpe={summary['sharpe']:+.4f} trades={result.trades}", flush=True)

    sharpes = np.array([float(performance[arm]["sharpe"]) for arm in ARMS], dtype=float)
    trial_std = float(np.std(sharpes, ddof=1))
    for arm in ARMS:
        rets = simple_returns(equities[arm].to_numpy())
        performance[arm]["deflatedSharpe"] = deflated_sharpe(
            float(performance[arm]["sharpe"]),
            rets,
            trials=len(ARMS),
            trial_sharpe_std=trial_std,
        )

    spreads = {arm: cross_section_spread(panel, arm) for arm in ARMS}
    cross_section: dict[str, dict[str, float]] = {}
    for label, (left, right) in {
        "OPEN_T1 - TYPICAL_T1": ("OPEN_T1", "TYPICAL_T1"),
        "OPEN_T1 - CLOSE_T1": ("OPEN_T1", "CLOSE_T1"),
    }.items():
        diff = (spreads[left] - spreads[right]).dropna()
        cross_section[label] = {
            "observations": int(len(diff)),
            "meanDailyBps": float(diff.mean() * 10_000.0),
            "neweyWestT": float(newey_west_t(diff, lags=5)),
        }

    penalty = break_even_entry_penalty_bps(equities["OPEN_T1"], equities[BASELINE])

    fast = performance["OPEN_T1"]
    base = performance[BASELINE]
    clauses = {
        "sharpeBeatsBaseline": float(fast["sharpe"]) > float(base["sharpe"]),
        "deflatedSharpeSignificant": float(fast["deflatedSharpe"]["pValue"]) < 0.05,
        "neweyWestTAboveTwo": cross_section["OPEN_T1 - TYPICAL_T1"]["neweyWestT"] > 2.0,
        "bearYearNotWorse": float(fast["bearYearMean"]) >= float(base["bearYearMean"]),
        "drawdownNotWorse": float(fast["mdd"]) >= float(base["mdd"]),
        "ladderMonotone": (
            float(performance["OPEN_T1"]["sharpe"])
            >= float(performance["MID_T1"]["sharpe"])
            >= float(performance["CLOSE_T1"]["sharpe"])
        ),
        "breakEvenPenaltyAtLeastTen": penalty >= 10.0,
    }
    inconclusive = diagnostic["suspiciousShare"] > 0.001
    verdict = (
        "INCONCLUSIVE" if inconclusive else ("PASS" if all(clauses.values()) else "FAIL")
    )

    RESULT.write_text(
        json.dumps(
            {
                "protocolId": protocol["protocolId"],
                "verdict": verdict,
                "clauses": clauses,
                "failingClauses": [name for name, ok in clauses.items() if not ok],
                "performance": performance,
                "crossSection": cross_section,
                "breakEvenEntryPenaltyBps": penalty,
                "splitGapDiagnostic": diagnostic,
                "decisionOnFailure": protocol["decisionRule"]["defaultOnFailure"],
                "decisionOnPass": protocol["decisionRule"]["onPass"],
                "providerCalls": 0,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=float,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nverdict={verdict} failing={[n for n, ok in clauses.items() if not ok]}", flush=True)
    print(f"wrote {RESULT.name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
