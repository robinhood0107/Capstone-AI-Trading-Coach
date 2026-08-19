"""S5.1 prior-month-end point-in-time top-30 universe selection."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError
from app.lightgbm.production_policy import APPROVED_HORIZON_UNION_SIZE
from app.lightgbm.pit_calendar import MonthlyUniverseSchedule, derive_monthly_universe_schedule
from app.lightgbm.temporal import TemporalReceipt, require_receipt_eligible


FIXED_ETF_SYMBOL = "132030"
MAX_UNION_SYMBOLS = APPROVED_HORIZON_UNION_SIZE


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
    available_at: datetime
    source_revision: str
    source_sha256: str


@dataclass(frozen=True)
class MonthlyUniverse:
    """selection session에서 정해 다음 달 전체에 교체 없이 적용할 symbol 집합."""

    selection_session: date
    effective_month: str
    instrument_ids: tuple[str, ...]
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class ProductionUniverseObservation:
    """KRX trading row와 base-info identity receipt를 분리한 monthly PIT observation."""

    instrument_id: str
    symbol: str
    session_date: date
    trading_value: float
    market_cap: float
    market: str
    security_type: str
    common_share: bool
    listed: bool
    trading_receipt: TemporalReceipt
    identity_receipt: TemporalReceipt


def select_monthly_universe(
    observations: Iterable[UniverseObservation],
    *,
    schedule: MonthlyUniverseSchedule,
) -> MonthlyUniverse:
    """trailing-20 evidence와 selection-date 시총으로 PIT top 30 및 고정 ETF를 선택한다."""

    expected_schedule = derive_monthly_universe_schedule(
        schedule.effective_month,
        dataset_cutoff=schedule.evidence_cutoff,
    )
    if schedule != expected_schedule:
        raise LightGbmContractError("universe schedule must be derived from the XKRX calendar")
    allowed_sessions = set(schedule.trailing_sessions)
    selected_vintages: dict[tuple[str, date], UniverseObservation] = {}
    for observation in observations:
        if observation.session_date not in allowed_sessions:
            continue
        _validate_provenance(observation)
        if observation.available_at > schedule.evidence_cutoff:
            continue
        key = (observation.instrument_id, observation.session_date)
        previous = selected_vintages.get(key)
        if previous is None or (
            observation.available_at,
            observation.source_revision,
            observation.source_sha256,
        ) > (previous.available_at, previous.source_revision, previous.source_sha256):
            selected_vintages[key] = observation

    by_identity: dict[str, list[UniverseObservation]] = defaultdict(list)
    for observation in selected_vintages.values():
        by_identity[observation.instrument_id].append(observation)

    ranked: list[tuple[float, float, str, str]] = []
    fixed_etf_identity: str | None = None
    for identity, rows in by_identity.items():
        selection_rows = [row for row in rows if row.session_date == schedule.selection_session]
        if len(selection_rows) != 1:
            continue
        current = selection_rows[0]
        if current.symbol == FIXED_ETF_SYMBOL:
            if len(rows) == 20 and current.listed and current.security_type == "ETF":
                fixed_etf_identity = identity
            continue
        if not _eligible_common_stock(current):
            continue
        if len(rows) != 20 or {row.session_date for row in rows} != allowed_sessions:
            continue
        positive = [row.trading_value for row in rows if row.trading_value > 0]
        if len(positive) < 18 or current.market_cap <= 0:
            continue
        mean_trading_value = sum(row.trading_value for row in rows) / 20.0
        ranked.append((-mean_trading_value, -current.market_cap, current.symbol, identity))

    ranked.sort()
    selected = ranked[:30]
    if len(selected) != 30:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: exact PIT top-30 universe is unavailable")
    identities = [row[3] for row in selected]
    symbols = [row[2] for row in selected]
    if fixed_etf_identity is None:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: fixed ETF 132030 evidence is missing")
    identities.append(fixed_etf_identity)
    symbols.append(FIXED_ETF_SYMBOL)
    return MonthlyUniverse(
        selection_session=schedule.selection_session,
        effective_month=schedule.effective_month,
        instrument_ids=tuple(identities),
        symbols=tuple(symbols),
    )


def select_production_monthly_universe(
    observations: Iterable[ProductionUniverseObservation],
    *,
    schedule: MonthlyUniverseSchedule,
) -> MonthlyUniverse:
    """TemporalReceipt v2로 exact top30+132030을 선택하고 revision hash ordering을 금지한다."""

    expected = derive_monthly_universe_schedule(
        schedule.effective_month, dataset_cutoff=schedule.evidence_cutoff
    )
    if schedule != expected:
        raise LightGbmContractError("production universe schedule must be XKRX-derived")
    allowed_sessions = set(schedule.trailing_sessions)
    selected: dict[tuple[str, date], ProductionUniverseObservation] = {}
    for observation in observations:
        if observation.session_date not in allowed_sessions:
            continue
        for receipt in (observation.trading_receipt, observation.identity_receipt):
            require_receipt_eligible(
                receipt,
                row_clock=schedule.evidence_cutoff,
                dataset_cutoff=schedule.evidence_cutoff,
            )
        key = (observation.instrument_id, observation.session_date)
        previous = selected.get(key)
        if previous is not None:
            if (
                previous.trading_receipt.snapshot_sha256
                != observation.trading_receipt.snapshot_sha256
                or previous.identity_receipt.snapshot_sha256
                != observation.identity_receipt.snapshot_sha256
            ):
                raise LightGbmContractError("SOURCE_SNAPSHOT_CONFLICT")
            continue
        selected[key] = observation
    by_identity: dict[str, list[ProductionUniverseObservation]] = defaultdict(list)
    for observation in selected.values():
        by_identity[observation.instrument_id].append(observation)
    ranked: list[tuple[float, float, str, str]] = []
    fixed_etf: str | None = None
    for identity, rows in by_identity.items():
        current_rows = [row for row in rows if row.session_date == schedule.selection_session]
        if len(current_rows) != 1:
            continue
        current = current_rows[0]
        if current.symbol == FIXED_ETF_SYMBOL:
            if current.security_type == "ETF" and current.listed:
                fixed_etf = identity
            continue
        if not (
            current.listed
            and current.market in {"KOSPI", "KOSDAQ"}
            and current.security_type == "COMMON_STOCK"
            and current.common_share
            and len(current.instrument_id) == 12
        ):
            continue
        if len(rows) != 20 or {row.session_date for row in rows} != allowed_sessions:
            continue
        if len([row for row in rows if row.trading_value > 0]) < 18 or current.market_cap <= 0:
            continue
        ranked.append(
            (
                -sum(row.trading_value for row in rows) / 20.0,
                -current.market_cap,
                current.symbol,
                identity,
            )
        )
    ranked.sort()
    if len(ranked) < 30 or fixed_etf is None:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: production PIT universe is incomplete")
    chosen = ranked[:30]
    return MonthlyUniverse(
        selection_session=schedule.selection_session,
        effective_month=schedule.effective_month,
        instrument_ids=tuple([row[3] for row in chosen] + [fixed_etf]),
        symbols=tuple([row[2] for row in chosen] + [FIXED_ETF_SYMBOL]),
    )


def validate_horizon_union(universes: Iterable[MonthlyUniverse]) -> tuple[str, ...]:
    """training horizon의 permanent identity union이 승인 상한을 넘으면 fit 전에 거부한다."""

    identities = sorted(
        {identity for universe in universes for identity in universe.instrument_ids}
    )
    if len(identities) > MAX_UNION_SYMBOLS:
        raise LightGbmContractError(
            "PIT universe union exceeds the approved instrument bound"
        )
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


def _validate_provenance(observation: UniverseObservation) -> None:
    if observation.available_at.tzinfo is None:
        raise LightGbmContractError("universe evidence timestamp must be timezone aware")
    if (
        not observation.source_revision
        or len(observation.source_sha256) != 64
        or any(character not in "0123456789abcdef" for character in observation.source_sha256)
    ):
        raise LightGbmContractError("universe evidence provenance is invalid")
