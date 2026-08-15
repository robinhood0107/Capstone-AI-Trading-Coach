"""S5.1 prior-month-end point-in-time top-30 universe selection."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable

from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError


FIXED_ETF_SYMBOL = "132030"
MAX_UNION_SYMBOLS = 180


@dataclass(frozen=True)
class UniverseObservation:
    """영구 instrument identity에 결합된 한 session의 PIT 유동성·시총 evidence."""

    instrument_id: str
    symbol: str
    session_date: date
    trading_value: float
    market_cap: float
    market: str
    security_type: str
    common_share: bool
    listed: bool
    available_at: str
    source_revision: str
    source_sha256: str


@dataclass(frozen=True)
class MonthlyUniverse:
    """selection session에서 정해 다음 달 전체에 교체 없이 적용할 symbol 집합."""

    selection_session: date
    effective_month: str
    instrument_ids: tuple[str, ...]
    symbols: tuple[str, ...]


def select_monthly_universe(
    observations: Iterable[UniverseObservation],
    *,
    selection_session: date,
    trailing_sessions: tuple[date, ...],
    effective_month: str,
) -> MonthlyUniverse:
    """trailing-20 evidence와 selection-date 시총으로 PIT top 30 및 고정 ETF를 선택한다."""

    if len(trailing_sessions) != 20 or trailing_sessions[-1] != selection_session:
        raise LightGbmContractError("universe selection requires exact trailing 20 sessions")
    allowed_sessions = set(trailing_sessions)
    by_identity: dict[str, list[UniverseObservation]] = defaultdict(list)
    for observation in observations:
        if observation.session_date in allowed_sessions:
            by_identity[observation.instrument_id].append(observation)

    ranked: list[tuple[float, float, str, str]] = []
    fixed_etf_identity: str | None = None
    for identity, rows in by_identity.items():
        selection_rows = [row for row in rows if row.session_date == selection_session]
        if len(selection_rows) != 1:
            continue
        current = selection_rows[0]
        if current.symbol == FIXED_ETF_SYMBOL:
            if current.listed and current.security_type == "ETF":
                fixed_etf_identity = identity
            continue
        if not _eligible_common_stock(current):
            continue
        positive = [row.trading_value for row in rows if row.trading_value > 0]
        if len(positive) < 18 or current.market_cap <= 0:
            continue
        mean_trading_value = sum(positive) / len(positive)
        ranked.append((-mean_trading_value, -current.market_cap, current.symbol, identity))

    ranked.sort()
    selected = ranked[:30]
    if not selected:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: no eligible PIT common-stock universe")
    identities = [row[3] for row in selected]
    symbols = [row[2] for row in selected]
    if fixed_etf_identity is None:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: fixed ETF 132030 evidence is missing")
    identities.append(fixed_etf_identity)
    symbols.append(FIXED_ETF_SYMBOL)
    return MonthlyUniverse(
        selection_session=selection_session,
        effective_month=effective_month,
        instrument_ids=tuple(identities),
        symbols=tuple(symbols),
    )


def validate_horizon_union(universes: Iterable[MonthlyUniverse]) -> tuple[str, ...]:
    """training horizon의 permanent identity union이 180을 넘으면 fit 전에 거부한다."""

    identities = sorted(
        {identity for universe in universes for identity in universe.instrument_ids}
    )
    if len(identities) > MAX_UNION_SYMBOLS:
        raise LightGbmContractError("PIT universe union exceeds 180 instruments")
    return tuple(identities)


def _eligible_common_stock(observation: UniverseObservation) -> bool:
    return (
        observation.listed
        and observation.market in {"KOSPI", "KOSDAQ"}
        and len(observation.symbol) == 6
        and observation.symbol.isdigit()
        and observation.common_share
        and observation.security_type == "COMMON_STOCK"
    )
