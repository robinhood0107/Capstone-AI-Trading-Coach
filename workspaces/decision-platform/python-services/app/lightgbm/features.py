"""S5.1 point-in-time core feature 공식과 cross-market 격리."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Mapping, Protocol, Sequence

import numpy as np

from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError
from app.lightgbm.pit_calendar import previous_xkrx_session
from app.lightgbm.temporal import TemporalReceipt, feature_as_of, require_receipt_eligible


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
class ProductionPriceEvidence:
    """KIS adjusted OHLCV와 adjustment metadata를 TemporalReceipt에 결속한다."""

    instrument_id: str
    symbol: str
    session_date: date
    adjusted_open: float | None
    adjusted_close: float
    volume: float
    flng_cls_code: str
    prtt_rate: float
    mod_yn: str
    revl_issu_reas: str
    receipt: TemporalReceipt


@dataclass(frozen=True)
class IndexEvidence:
    """KRX listing-market index close의 source-specific receipt."""

    session_date: date
    market: str
    adjusted_close: float
    receipt: TemporalReceipt


@dataclass(frozen=True)
class MacroObservation:
    """ECOS TIME은 observation_date일 뿐이며 availability는 receipt가 소유한다."""

    series_id: str
    observation_date: date
    value: float
    receipt: TemporalReceipt


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


def select_pit_market_vintages(
    rows: Sequence[MarketEvidence],
    *,
    cutoff: datetime,
) -> tuple[MarketEvidence, ...]:
    """cutoff 이전 각 market-session의 최신 macro/index vintage만 선택한다."""

    selected: dict[tuple[str, date], MarketEvidence] = {}
    for row in rows:
        _validate_provenance(row.available_at, row.source_revision, row.source_sha256, cutoff)
        if row.available_at > cutoff:
            continue
        key = (row.market, row.session_date)
        previous = selected.get(key)
        if previous is None or (row.available_at, row.source_revision, row.source_sha256) > (
            previous.available_at,
            previous.source_revision,
            previous.source_sha256,
        ):
            selected[key] = row
    return tuple(sorted(selected.values(), key=lambda row: (row.session_date, row.market)))


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

    return _calculate_core_feature_rows(
        ordered=ordered,
        close=close,
        volume=volume,
        market_close=market_close,
        base_rate=base_rate,
        usdkrw=usdkrw,
    )


def build_production_core_feature_rows(
    prices: Sequence[ProductionPriceEvidence],
    indices: Sequence[IndexEvidence],
    macro: Sequence[MacroObservation],
    *,
    listing_market: str | None = None,
    listing_market_by_session: Mapping[date, str] | None = None,
    cutoff: datetime,
    cross_market_reader: CrossMarketReader | None = None,
    macro_delay_sessions: int = 0,
) -> tuple[CoreFeatureRow, ...]:
    """TemporalReceipt와 row-specific clock을 강제한 production feature projection."""

    del cross_market_reader
    if cutoff.tzinfo is None:
        raise LightGbmContractError("production dataset cutoff must be timezone aware")
    if macro_delay_sessions not in {0, 1}:
        raise LightGbmContractError("macro timing sensitivity delay must be zero or one session")
    ordered = sorted(prices, key=lambda row: row.session_date)
    if (
        not ordered
        or len({row.symbol for row in ordered}) != 1
        or len({row.instrument_id for row in ordered}) != 1
        or len({row.session_date for row in ordered}) != len(ordered)
    ):
        raise LightGbmContractError("production price evidence identity or sessions are invalid")
    if (listing_market is None) == (listing_market_by_session is None):
        raise LightGbmContractError("exactly one production listing-market source is required")
    index_map = {(row.market, row.session_date): row for row in indices}
    if len(index_map) != len(indices):
        raise LightGbmContractError("production index evidence has duplicate sessions")
    rate_rows = sorted(
        (row for row in macro if row.series_id == "policy-rate"),
        key=lambda row: row.observation_date,
    )
    fx_map = {row.observation_date: row for row in macro if row.series_id == "krw-usd-rate"}
    if len(fx_map) != len([row for row in macro if row.series_id == "krw-usd-rate"]):
        raise LightGbmContractError("production FX evidence has duplicate dates")
    current_rate: MacroObservation | None = None
    rate_index = 0
    market_values: list[float] = []
    rate_values: list[float] = []
    fx_values: list[float] = []
    for price in ordered:
        row_clock = feature_as_of(price.session_date)
        macro_session = (
            price.session_date
            if macro_delay_sessions == 0
            else previous_xkrx_session(price.session_date)
        )
        require_receipt_eligible(price.receipt, row_clock=row_clock, dataset_cutoff=cutoff)
        has_adjustment = (
            price.flng_cls_code not in {"", "00"}
            or price.prtt_rate > 0
            or bool(price.revl_issu_reas)
        )
        if (
            not math.isfinite(price.adjusted_close)
            or price.adjusted_close <= 0
            or not math.isfinite(price.volume)
            or price.volume < 0
            or not math.isfinite(price.prtt_rate)
            or price.prtt_rate < 0
            or price.mod_yn not in {"Y", "N"}
            or (price.mod_yn == "Y") != has_adjustment
            or len(price.flng_cls_code) > 32
            or len(price.revl_issu_reas) > 256
        ):
            raise DatasetUnavailable("DATASET_UNAVAILABLE: production price evidence is invalid")
        market = (
            listing_market
            if listing_market_by_session is None
            else listing_market_by_session.get(price.session_date)
        )
        if market not in {"KOSPI", "KOSDAQ"}:
            raise DatasetUnavailable("DATASET_UNAVAILABLE: listing market evidence is missing")
        index = index_map.get((market, price.session_date))
        fx = fx_map.get(macro_session)
        while rate_index < len(rate_rows) and (
            rate_rows[rate_index].observation_date <= macro_session
        ):
            current_rate = rate_rows[rate_index]
            rate_index += 1
        if index is None or fx is None or current_rate is None:
            raise DatasetUnavailable("DATASET_UNAVAILABLE: aligned production evidence is missing")
        for receipt in (index.receipt, fx.receipt, current_rate.receipt):
            require_receipt_eligible(receipt, row_clock=row_clock, dataset_cutoff=cutoff)
        market_values.append(index.adjusted_close)
        rate_values.append(current_rate.value)
        fx_values.append(fx.value)
    close = np.asarray([row.adjusted_close for row in ordered], dtype=np.float64)
    volume = np.asarray([row.volume for row in ordered], dtype=np.float64)
    market_close = np.asarray(market_values, dtype=np.float64)
    base_rate = np.asarray(rate_values, dtype=np.float64)
    usdkrw = np.asarray(fx_values, dtype=np.float64)
    if not all(np.isfinite(value).all() for value in (market_close, base_rate, usdkrw)):
        raise DatasetUnavailable("DATASET_UNAVAILABLE: production market evidence is non-finite")
    if (market_close <= 0).any() or (usdkrw <= 0).any():
        raise DatasetUnavailable("DATASET_UNAVAILABLE: production market level is invalid")
    return _calculate_core_feature_rows(
        ordered=ordered,
        close=close,
        volume=volume,
        market_close=market_close,
        base_rate=base_rate,
        usdkrw=usdkrw,
    )


def _calculate_core_feature_rows(
    *,
    ordered: Sequence[PriceEvidence] | Sequence[ProductionPriceEvidence],
    close: np.ndarray,
    volume: np.ndarray,
    market_close: np.ndarray,
    base_rate: np.ndarray,
    usdkrw: np.ndarray,
) -> tuple[CoreFeatureRow, ...]:
    """v1/v2 provenance validation 뒤 공유하는 exact numeric kernel."""

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
