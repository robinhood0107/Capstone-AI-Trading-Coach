"""S5.1 point-in-time core feature 공식과 cross-market 격리."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Protocol, Sequence

import numpy as np

from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError


FORBIDDEN_COLUMN_PREFIXES = (
    "cross_market_",
    "analyst_revision",
    "news_sentiment",
    "cause_",
    "rag_",
    "llm_",
    "risk_score",
    "hmm_",
)
CORE_FEATURE_COLUMNS = (
    "momentum_5",
    "momentum_20",
    "momentum_60",
    "close_sma20_ratio",
    "close_sma60_ratio",
    "rsi14_wilder",
    "macd_signal_spread_ratio",
    "volatility20",
    "log_volume_z20",
    "market_return_5",
    "market_return_20",
    "relative_strength_5",
    "relative_strength_20",
    "base_rate_level",
    "base_rate_change_20",
    "usdkrw_return_5",
    "usdkrw_return_20",
)


class CrossMarketReader(Protocol):
    """S6.6 이전에는 존재만 하며 S5 builder가 호출해서는 안 되는 port."""

    def read(self, symbol: str, session_date: date) -> object: ...


@dataclass(frozen=True)
class PriceEvidence:
    """한 symbol-session의 PIT adjusted price와 provenance."""

    instrument_id: str
    symbol: str
    session_date: date
    adjusted_open: float | None
    adjusted_close: float
    volume: float
    available_at: datetime
    source_revision: str
    source_sha256: str


@dataclass(frozen=True)
class MarketEvidence:
    """listing market index, 기준금리, USDKRW의 session-aligned PIT evidence."""

    session_date: date
    market: str
    market_adjusted_close: float
    base_rate: float
    usdkrw: float
    available_at: datetime
    source_revision: str
    source_sha256: str


@dataclass(frozen=True)
class CoreFeatureRow:
    """key와 nullable float32 core feature만 가진 학습 전 projection."""

    symbol: str
    session_date: date
    values: tuple[np.float32, ...]

    def as_mapping(self) -> dict[str, object]:
        """ordered schema와 동일한 mapping을 반환한다."""

        return {
            "symbol": self.symbol,
            "sessionDate": self.session_date,
            **dict(zip(CORE_FEATURE_COLUMNS, self.values, strict=True)),
        }


def reject_forbidden_columns(columns: Sequence[str]) -> None:
    """Parquet projection 전에 교차시장·인접 권한·HMM column을 이름만으로 거부한다."""

    for column in columns:
        normalized = column.lower()
        if any(
            normalized == prefix or normalized.startswith(prefix)
            for prefix in FORBIDDEN_COLUMN_PREFIXES
        ):
            raise LightGbmContractError(f"forbidden S5 feature column: {column}")


def select_pit_price_vintages(
    rows: Sequence[PriceEvidence],
    *,
    cutoff: datetime,
) -> tuple[PriceEvidence, ...]:
    """cutoff 이후 revision을 배제하고 각 symbol-session의 최신 available vintage만 고른다."""

    selected: dict[tuple[str, date], PriceEvidence] = {}
    for row in rows:
        _validate_provenance(row.available_at, row.source_revision, row.source_sha256, cutoff)
        if row.available_at > cutoff:
            continue
        key = (row.instrument_id, row.session_date)
        previous = selected.get(key)
        if previous is None or (row.available_at, row.source_revision, row.source_sha256) > (
            previous.available_at,
            previous.source_revision,
            previous.source_sha256,
        ):
            selected[key] = row
    return tuple(sorted(selected.values(), key=lambda row: (row.session_date, row.symbol)))


def build_core_feature_rows(
    prices: Sequence[PriceEvidence],
    market_rows: Sequence[MarketEvidence],
    *,
    listing_market: str,
    cutoff: datetime,
    cross_market_reader: CrossMarketReader | None = None,
) -> tuple[CoreFeatureRow, ...]:
    """60-session warm-up이 있는 단일 symbol의 exact core feature를 계산한다.

    `cross_market_reader`는 architecture parity를 위해 주입만 받고 의도적으로 참조하지 않는다.
    """

    del cross_market_reader
    ordered = sorted(prices, key=lambda row: row.session_date)
    if (
        len({row.symbol for row in ordered}) != 1
        or len({row.instrument_id for row in ordered}) != 1
    ):
        raise LightGbmContractError(
            "core feature builder requires one permanent instrument identity"
        )
    if len({row.session_date for row in ordered}) != len(ordered):
        raise LightGbmContractError("price evidence has duplicate sessions")
    market = {row.session_date: row for row in market_rows if row.market == listing_market}
    if len(market) != len([row for row in market_rows if row.market == listing_market]):
        raise LightGbmContractError("market evidence has duplicate sessions")

    for row in ordered:
        _validate_provenance(row.available_at, row.source_revision, row.source_sha256, cutoff)
        if row.available_at > cutoff:
            raise DatasetUnavailable(
                "DATASET_UNAVAILABLE: price vintage was not available at cutoff"
            )
        if not math.isfinite(row.adjusted_close) or row.adjusted_close <= 0:
            raise DatasetUnavailable("DATASET_UNAVAILABLE: adjusted close is missing or invalid")
        if not math.isfinite(row.volume) or row.volume < 0:
            raise DatasetUnavailable("DATASET_UNAVAILABLE: volume is missing or invalid")
        evidence = market.get(row.session_date)
        if evidence is None:
            raise DatasetUnavailable(
                "DATASET_UNAVAILABLE: aligned market or macro evidence is missing"
            )
        _validate_provenance(
            evidence.available_at, evidence.source_revision, evidence.source_sha256, cutoff
        )
        if evidence.available_at > cutoff:
            raise DatasetUnavailable(
                "DATASET_UNAVAILABLE: macro vintage was not available at cutoff"
            )

    close = np.asarray([row.adjusted_close for row in ordered], dtype=np.float64)
    volume = np.asarray([row.volume for row in ordered], dtype=np.float64)
    market_close = np.asarray(
        [market[row.session_date].market_adjusted_close for row in ordered], dtype=np.float64
    )
    base_rate = np.asarray(
        [market[row.session_date].base_rate for row in ordered], dtype=np.float64
    )
    usdkrw = np.asarray([market[row.session_date].usdkrw for row in ordered], dtype=np.float64)
    if not all(np.isfinite(values).all() for values in (market_close, base_rate, usdkrw)):
        raise DatasetUnavailable("DATASET_UNAVAILABLE: market or macro evidence is non-finite")
    if (market_close <= 0).any() or (usdkrw <= 0).any():
        raise DatasetUnavailable("DATASET_UNAVAILABLE: market or FX level is invalid")

    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    macd = ema12 - ema26
    signal = _ema(macd, 9)
    rsi = _wilder_rsi(close, 14)
    output: list[CoreFeatureRow] = []
    for index in range(59, len(ordered)):
        stock_5 = _window_return(close, index, 5)
        stock_20 = _window_return(close, index, 20)
        market_5 = _window_return(market_close, index, 5)
        market_20 = _window_return(market_close, index, 20)
        log_returns = np.diff(np.log(close[index - 20 : index + 1]))
        log_volume = np.log1p(volume[index - 19 : index + 1])
        volume_std = float(log_volume.std(ddof=1))
        values = (
            stock_5,
            stock_20,
            _window_return(close, index, 60),
            close[index] / close[index - 19 : index + 1].mean() - 1.0,
            close[index] / close[index - 59 : index + 1].mean() - 1.0,
            rsi[index],
            (macd[index] - signal[index]) / close[index],
            float(log_returns.std(ddof=1) * math.sqrt(252.0)),
            0.0
            if volume_std <= np.finfo(np.float64).eps * max(1.0, abs(float(log_volume.mean())))
            else float((log_volume[-1] - log_volume.mean()) / volume_std),
            market_5,
            market_20,
            stock_5 - market_5,
            stock_20 - market_20,
            base_rate[index],
            base_rate[index] - base_rate[index - 19],
            _window_return(usdkrw, index, 5),
            _window_return(usdkrw, index, 20),
        )
        if not np.isfinite(np.asarray(values)).all():
            raise DatasetUnavailable("DATASET_UNAVAILABLE: core feature is non-finite")
        output.append(
            CoreFeatureRow(
                symbol=ordered[index].symbol,
                session_date=ordered[index].session_date,
                values=tuple(np.float32(value) for value in values),
            )
        )
    return tuple(output)


def _validate_provenance(
    available_at: datetime,
    source_revision: str,
    source_sha256: str,
    cutoff: datetime,
) -> None:
    if available_at.tzinfo is None or cutoff.tzinfo is None:
        raise LightGbmContractError("PIT timestamps must be timezone aware")
    if (
        not source_revision
        or len(source_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_sha256)
    ):
        raise LightGbmContractError("PIT source provenance is invalid")


def _window_return(values: np.ndarray, index: int, observations: int) -> float:
    return float(values[index] / values[index - observations + 1] - 1.0)


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1.0)
    output = np.empty_like(values)
    output[0] = values[0]
    for index in range(1, len(values)):
        output[index] = alpha * values[index] + (1.0 - alpha) * output[index - 1]
    return output


def _wilder_rsi(close: np.ndarray, period: int) -> np.ndarray:
    output = np.full_like(close, np.nan)
    if len(close) <= period:
        return output
    delta = np.diff(close)
    gain = np.maximum(delta, 0.0)
    loss = np.maximum(-delta, 0.0)
    average_gain = float(gain[:period].mean())
    average_loss = float(loss[:period].mean())
    output[period] = _rsi_value(average_gain, average_loss)
    for index in range(period + 1, len(close)):
        average_gain = ((period - 1) * average_gain + gain[index - 1]) / period
        average_loss = ((period - 1) * average_loss + loss[index - 1]) / period
        output[index] = _rsi_value(average_gain, average_loss)
    return output


def _rsi_value(average_gain: float, average_loss: float) -> float:
    if average_gain == 0 and average_loss == 0:
        return 50.0
    if average_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + average_gain / average_loss))
