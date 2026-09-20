"""보유 호라이즌이 다른 두 트랙(SHORT/CORE)을 함께 굴리면 단독보다 나은지 잰다.

## 왜 이 하니스가 필요했는가

기존 `portfolio_eval.simulate` / `arm_comparison_eval.simulate` 는 "n일마다 목표 비중
재설정"이라는 캘린더 리밸런싱이다. 포지션 객체도, 진입가도, 진입일도, 트레일링 상태도
없다. 그래서 손절·익절·ATR 트레일링·최대보유세션이라는 **실제 청산 규칙을 반영할 수
없고**, 만기가 다른 포지션이 동시에 살아 있는 상태도 표현할 수 없다. 두 트랙 혼합은
정확히 그 상태를 요구한다.

그래서 포지션 상태 기계를 새로 쓴다. 다만 새로 만드는 것은 그 루프뿐이고,
청산 우선순위는 `automation.py` 와 같고 ATR 은 프로덕션 커널(`automation_atr`)을
그대로 호출한다. 지표·DSR·비용·약세연도 기준도 `arm_comparison_eval` 에서 가져온다.

## 판정

`reports/multitrack-protocol.v1.json` 에 실행 전에 고정했다. 요약하면 혼합이 두 단독
트랙보다 Sharpe 가 높고, DSR(trials=4) 이 유의하고, 약세연도에서 열위가 아닐 때만
SHORT 트랙을 채택한다. 미달이면 CORE 단독을 유지하고 임계값을 사후에 풀지 않는다.

실행:
    uv run python ../research/p1-return-profit-verification/multitrack_backtest.py
(python-services 디렉터리에서. `/tmp/p1exp/*.parquet` 이 있어야 한다.)
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd

from app.p1_owner.automation_atr import (
    AtrHistoryError,
    CompletedDailyBar,
    advance_trailing_stop,
    wilder_atr,
)

HERE = pathlib.Path(__file__).resolve().parent
CACHE = pathlib.Path("/tmp/p1exp")
REPORTS = HERE / "reports"
PROTOCOL = REPORTS / "multitrack-protocol.v1.json"


def _load_arm_module() -> object:
    """지표·DSR·비용 상수를 세 번째로 구현하지 않는다. 기존 하니스를 그대로 읽어 쓴다."""

    spec = importlib.util.spec_from_file_location("_arm_eval", HERE / "arm_comparison_eval.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("arm_comparison_eval 을 불러올 수 없다")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ARM = _load_arm_module()
ROUND_TRIP_BPS: float = _ARM.ROUND_TRIP_BPS  # type: ignore[attr-defined]
BEAR_YEARS: tuple[int, ...] = _ARM.BEAR_YEARS  # type: ignore[attr-defined]
describe = _ARM.describe  # type: ignore[attr-defined]
deflated_sharpe = _ARM.deflated_sharpe  # type: ignore[attr-defined]
simple_returns = _ARM.simple_returns  # type: ignore[attr-defined]


@dataclass(frozen=True, slots=True)
class Track:
    """한 트랙의 청산 파라미터. 값은 프로덕션 프리셋과 같은 의미다."""

    name: str
    slots: int
    max_holding_sessions: int
    stop_loss_bps: int
    take_profit_bps: int
    atr_period: int
    atr_multiplier_milli: int


@dataclass(slots=True)
class Position:
    symbol: str
    track: str
    entry_index: int
    entry_price: float
    shares: float
    peak_price: float
    trailing_stop: float | None = None


@dataclass(slots=True)
class TrackResult:
    equity: pd.Series
    trades: int = 0
    exits_by_reason: dict[str, int] = field(default_factory=dict)


def load_panel() -> pd.DataFrame:
    """가격(OHLC)과 예측 점수를 (날짜, 종목)에서 결합한다.

    예측 파일에는 가격이 없다. 손절·ATR 은 가격 점프에 직접 반응하므로 OHLC 가 필요하다.
    """

    history = pd.read_parquet(CACHE / "long_history.parquet")
    predictions = pd.read_parquet(CACHE / "predictions_arms.parquet")
    history = history.rename(columns={"Date": "date", "Open": "open", "High": "high"})
    history = history.rename(columns={"Low": "low", "Close": "close"})
    history["date"] = pd.to_datetime(history["date"])
    predictions["date"] = pd.to_datetime(predictions["date"])
    score = "predBlendRet" if "predBlendRet" in predictions.columns else "predLstmRet"
    merged = history.merge(
        predictions[["date", "ticker", score]].rename(columns={score: "score"}),
        on=["date", "ticker"],
        how="inner",
    )
    merged = merged.dropna(subset=["open", "high", "low", "close", "score"])
    return merged.sort_values(["date", "ticker"]).reset_index(drop=True)


def _bars_for(frame: pd.DataFrame) -> list[CompletedDailyBar]:
    """프로덕션 ATR 커널은 원화 정수 바를 받는다. float 패널에 한 겹 어댑터를 둔다."""

    bars: list[CompletedDailyBar] = []
    for row in frame.itertuples():
        high = max(1, int(row.high))
        low = max(1, int(row.low))
        close = min(max(int(row.close), low), high)
        open_ = min(max(int(row.open), low), high)
        bars.append(
            CompletedDailyBar(row.date.date(), open_, high, low, close)
        )
    return bars


def _atr_value(bars: list[CompletedDailyBar], period: int, as_of: date) -> Decimal | None:
    try:
        return wilder_atr(tuple(bars), period=period, as_of_session=as_of).value_krw
    except (AtrHistoryError, ValueError):
        return None


def simulate(tracks: tuple[Track, ...], panel: pd.DataFrame) -> TrackResult:
    """슬롯을 채우고 프로덕션과 같은 우선순위로 청산한다. 당일 청산은 하지 않는다."""

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
    cost = ROUND_TRIP_BPS / 2.0 / 10_000.0

    equity = 1.0
    curve: list[float] = []
    open_positions: list[Position] = []
    result = TrackResult(pd.Series(dtype=float))
    track_by_name = {track.name: track for track in tracks}

    for step, stamp in enumerate(sessions):
        day = panel[panel["date"] == stamp]
        price_of = dict(zip(day["ticker"], day["close"], strict=True))
        session_day = pd.Timestamp(stamp).date()

        # 1) 청산 - automation.py 와 같은 우선순위. 당일 진입분은 제외한다.
        survivors: list[Position] = []
        for position in open_positions:
            if position.entry_index == step:
                survivors.append(position)
                continue
            price = price_of.get(position.symbol)
            if price is None:
                survivors.append(position)
                continue
            track = track_by_name[position.track]
            gross = price / position.entry_price - 1.0
            net_bps = (gross - 2 * cost) * 10_000.0
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
            equity += position.shares * price * (1.0 - cost)
            result.exits_by_reason[reason] = result.exits_by_reason.get(reason, 0) + 1
        open_positions = survivors

        # 2) 빈 슬롯 채우기 - 트랙별로 상위 점수부터. 이미 보유한 종목은 다시 사지 않는다.
        held = {position.symbol for position in open_positions}
        ranked = day.sort_values("score", ascending=False)
        for track in tracks:
            used = sum(1 for item in open_positions if item.track == track.name)
            free = track.slots - used
            if free <= 0 or track.slots == 0:
                continue
            budget_per_slot = equity / max(1, sum(item.slots for item in tracks))
            for row in ranked.itertuples():
                if free <= 0:
                    break
                if row.ticker in held or row.score <= (2 * cost):
                    continue
                price = float(row.close)
                if price <= 0 or budget_per_slot <= 0 or equity < budget_per_slot:
                    continue
                shares = budget_per_slot / price
                equity -= shares * price * (1.0 + cost)
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


def _bear_year_mean(equity: pd.Series) -> float:
    rets = pd.Series(simple_returns(equity.to_numpy()), index=equity.index[1:])
    mask = rets.index.year.isin(BEAR_YEARS)
    return float(rets[mask].mean()) if mask.any() else float("nan")


def main() -> int:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    specs = protocol["tracks"]
    panel = load_panel()
    print(
        f"패널 {len(panel):,}관측  {panel.ticker.nunique()}종목  "
        f"세션 {panel.date.nunique():,}개  "
        f"{panel.date.min().date()} ~ {panel.date.max().date()}"
    )

    def build(name: str, slots: int) -> Track:
        spec = specs[name]
        return Track(
            name=name,
            slots=slots,
            max_holding_sessions=spec["maxHoldingSessions"],
            stop_loss_bps=spec["stopLossBps"],
            take_profit_bps=spec["takeProfitBps"],
            atr_period=spec["atrPeriod"],
            atr_multiplier_milli=spec["atrMultiplierMilli"],
        )

    rows: list[dict[str, object]] = []
    trials = int(protocol["trials"])
    # 시행별 equity 를 먼저 다 만든다. DSR 의 기준 σ 는 시행들 사이의 Sharpe 산포이므로
    # 그것을 실제로 갖고 있어야 한다(arm_comparison_eval 의 같은 판단).
    outcomes: list[tuple[dict[str, object], TrackResult, np.ndarray]] = []
    for arm in protocol["arms"]:
        tracks = tuple(
            track
            for track in (build("CORE", arm["coreSlots"]), build("SHORT", arm["shortSlots"]))
            if track.slots > 0
        )
        outcome = simulate(tracks, panel)
        stats = dict(describe(outcome.equity, arm["name"]))
        rets = np.asarray(simple_returns(outcome.equity.to_numpy()), dtype=float)
        outcomes.append((stats, outcome, rets))

    sharpe_std = float(np.std([float(item[0]["sharpe"]) for item in outcomes], ddof=1))
    for stats, outcome, rets in outcomes:
        dsr = deflated_sharpe(
            float(stats["sharpe"]), rets, trials=trials, trial_sharpe_std=sharpe_std
        )
        stats["deflatedSharpe"] = dsr.get("deflatedSharpe")
        stats["deflatedSharpePValue"] = dsr.get("pValue")
        stats["trades"] = outcome.trades
        stats["bearYearMean"] = round(_bear_year_mean(outcome.equity), 6)
        stats["exitsByReason"] = outcome.exits_by_reason
        rows.append(stats)
        print(
            f"{str(stats['label']):12s} sharpe={float(stats['sharpe']):+.4f} "
            f"cagr={float(stats['cagr']):+.4f} mdd={float(stats['mdd']):+.4f} "
            f"dsr={float(stats['deflatedSharpe']):+.4f} trades={outcome.trades} "
            f"bear={float(stats['bearYearMean']):+.6f}"
        )
    print(f"시행 간 Sharpe 표준편차 {sharpe_std:.4f} (DSR 기준 σ)")

    by_name = {str(item["label"]): item for item in rows}
    core = by_name["CORE_ONLY"]
    short = by_name["SHORT_ONLY"]
    verdict: dict[str, object] = {"adoptShortTrack": False, "reasons": []}
    for name in ("MIXED_7_3", "MIXED_5_5"):
        mixed = by_name[name]
        checks = {
            "beatsBothSolo": mixed["sharpe"] > core["sharpe"] and mixed["sharpe"] > short["sharpe"],
            "deflatedSharpeSignificant": float(mixed["deflatedSharpe"]) > 0.95,
            "notWorseInBearYears": float(mixed["bearYearMean"]) >= float(core["bearYearMean"]),
        }
        cast_reasons = verdict["reasons"]
        assert isinstance(cast_reasons, list)
        cast_reasons.append({"arm": name, "checks": checks})
        if all(checks.values()):
            verdict["adoptShortTrack"] = True
            verdict["adoptedArm"] = name

    payload = {
        "protocolId": protocol["protocolId"],
        "trials": trials,
        "arms": rows,
        "verdict": verdict,
    }
    out = REPORTS / "multitrack-result.v1.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n판정: SHORT 트랙 채택={verdict['adoptShortTrack']}  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
