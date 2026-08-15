from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
import hashlib
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError
from app.lightgbm.feature_artifact import (
    feature_table_from_rows,
    logical_dataset_hash,
    logical_training_dataset_hash,
    optional_group_is_eligible,
    read_feature_artifact,
    require_source_rows,
    write_feature_parquet,
)
from app.lightgbm.features import (
    CORE_FEATURE_COLUMNS,
    MarketEvidence,
    PriceEvidence,
    build_core_feature_rows,
    reject_forbidden_columns,
    select_pit_market_vintages,
    select_pit_price_vintages,
)
from app.lightgbm.pit_calendar import build_pit_session_window
from app.lightgbm.universe import (
    MonthlyUniverse,
    UniverseObservation,
    select_monthly_universe,
    validate_horizon_union,
)


def test_calendar_regression_has_exact_1072_and_1007_sessions() -> None:
    cutoff = datetime(2026, 8, 15, 8, 10, tzinfo=timezone(timedelta(hours=9)))
    window = build_pit_session_window(cutoff)

    assert window.latest_completed == date(2026, 8, 14)
    assert (window.raw_sessions[0], window.raw_sessions[-1], len(window.raw_sessions)) == (
        date(2022, 4, 1),
        date(2026, 8, 14),
        1_072,
    )
    assert (
        window.eligible_sessions[0],
        window.eligible_sessions[-1],
        len(window.eligible_sessions),
    ) == (
        date(2022, 6, 28),
        date(2026, 8, 6),
        1_007,
    )


def _universe_observations() -> tuple[list[UniverseObservation], tuple[date, ...]]:
    sessions = tuple(date(2026, 6, 1) + timedelta(days=index) for index in range(20))
    rows: list[UniverseObservation] = []
    symbols = [f"{index:06d}" for index in range(1, 33)]
    for rank, symbol in enumerate(symbols):
        for session in sessions:
            rows.append(
                UniverseObservation(
                    instrument_id=f"instrument-{symbol}",
                    symbol=symbol,
                    session_date=session,
                    trading_value=1_000.0 - rank,
                    market_cap=10_000.0 - rank,
                    market="KOSPI" if rank % 2 == 0 else "KOSDAQ",
                    security_type="COMMON_STOCK",
                    common_share=True,
                    listed=True,
                    available_at=datetime(2026, 6, 20, 6, 30, tzinfo=UTC),
                    source_revision="r1",
                    source_sha256="a" * 64,
                )
            )
    for symbol, security_type in (("132030", "ETF"), ("580001", "ETN")):
        for session in sessions:
            rows.append(
                UniverseObservation(
                    instrument_id=f"instrument-{symbol}",
                    symbol=symbol,
                    session_date=session,
                    trading_value=1_000_000.0,
                    market_cap=1_000_000.0,
                    market="KOSPI",
                    security_type=security_type,
                    common_share=False,
                    listed=True,
                    available_at=datetime(2026, 6, 20, 6, 30, tzinfo=UTC),
                    source_revision="r1",
                    source_sha256="b" * 64,
                )
            )
    return rows, sessions


def test_month_end_top30_tie_order_fixed_etf_and_no_etn() -> None:
    rows, sessions = _universe_observations()
    universe = select_monthly_universe(
        rows,
        selection_session=sessions[-1],
        trailing_sessions=sessions,
        effective_month="2026-07",
        cutoff=datetime(2026, 6, 20, 8, tzinfo=UTC),
    )

    assert len(universe.symbols) == 31
    assert universe.symbols[:3] == ("000001", "000002", "000003")
    assert universe.symbols[-1] == "132030"
    assert "580001" not in universe.symbols


def test_monthly_universe_does_not_replace_and_union_181_fails() -> None:
    universes = [
        MonthlyUniverse(date(2026, 1, 30), "2026-02", (f"id-{index}",), (f"{index:06d}",))
        for index in range(181)
    ]
    with pytest.raises(LightGbmContractError, match="180"):
        validate_horizon_union(universes)


class _ExplodingCrossMarketReader:
    calls = 0

    def read(self, symbol: str, session_date: date) -> object:
        self.calls += 1
        raise AssertionError((symbol, session_date))


def _feature_evidence() -> tuple[list[PriceEvidence], list[MarketEvidence], datetime]:
    cutoff = datetime(2026, 8, 15, tzinfo=UTC)
    prices: list[PriceEvidence] = []
    market: list[MarketEvidence] = []
    for index in range(66):
        session = date(2026, 1, 1) + timedelta(days=index)
        available = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=index, hours=8)
        prices.append(
            PriceEvidence(
                instrument_id="instrument-005930",
                symbol="005930",
                session_date=session,
                adjusted_open=100.0,
                adjusted_close=100.0,
                volume=1_000.0,
                available_at=available,
                source_revision="r1",
                source_sha256="a" * 64,
            )
        )
        market.append(
            MarketEvidence(
                session_date=session,
                market="KOSPI",
                market_adjusted_close=200.0,
                base_rate=2.5,
                usdkrw=1_300.0,
                available_at=available,
                source_revision="r1",
                source_sha256="b" * 64,
            )
        )
    return prices, market, cutoff


def test_feature_golden_constant_series_and_cross_market_zero_calls() -> None:
    prices, market, cutoff = _feature_evidence()
    reader = _ExplodingCrossMarketReader()
    rows = build_core_feature_rows(
        prices,
        market,
        listing_market="KOSPI",
        cutoff=cutoff,
        cross_market_reader=reader,
    )

    assert len(rows) == 7
    assert reader.calls == 0
    values = dict(zip(CORE_FEATURE_COLUMNS, rows[0].values, strict=True))
    assert values["rsi14_wilder"] == np.float32(50.0)
    for name, value in values.items():
        if name not in {"rsi14_wilder", "base_rate_level"}:
            assert value == np.float32(0.0), name
    assert values["base_rate_level"] == np.float32(2.5)


def test_pit_vintage_mutation_after_cutoff_does_not_rewrite_history() -> None:
    prices, market, cutoff = _feature_evidence()
    original = prices[0]
    future = PriceEvidence(
        **{
            **original.__dict__,
            "adjusted_close": 999.0,
            "available_at": cutoff + timedelta(seconds=1),
            "source_revision": "r2",
            "source_sha256": "c" * 64,
        }
    )
    selected = select_pit_price_vintages([original, future], cutoff=cutoff)
    assert selected == (original,)

    market_original = market[0]
    market_future = MarketEvidence(
        **{
            **market_original.__dict__,
            "base_rate": 99.0,
            "available_at": cutoff + timedelta(seconds=1),
            "source_revision": "r2",
            "source_sha256": "d" * 64,
        }
    )
    assert select_pit_market_vintages([market_original, market_future], cutoff=cutoff) == (
        market_original,
    )


def test_universe_ignores_future_revision_and_requires_exact_thirty() -> None:
    rows, sessions = _universe_observations()
    cutoff = datetime(2026, 6, 20, 8, tzinfo=UTC)
    target = next(row for row in rows if row.symbol == "000001" and row.session_date == sessions[-1])
    rows.append(
        UniverseObservation(
            **{
                **target.__dict__,
                "market_cap": 0.0,
                "available_at": cutoff + timedelta(seconds=1),
                "source_revision": "r2",
                "source_sha256": "f" * 64,
            }
        )
    )
    universe = select_monthly_universe(
        rows,
        selection_session=sessions[-1],
        trailing_sessions=sessions,
        effective_month="2026-07",
        cutoff=cutoff,
    )
    assert universe.symbols[0] == "000001"

    reduced = [row for row in rows if row.symbol not in {"000030", "000031", "000032"}]
    with pytest.raises(DatasetUnavailable, match="top-30"):
        select_monthly_universe(
            reduced,
            selection_session=sessions[-1],
            trailing_sessions=sessions,
            effective_month="2026-07",
            cutoff=cutoff,
        )


def test_forbidden_columns_fail_before_projection() -> None:
    for column in (
        "cross_market_score",
        "analyst_revision",
        "news_sentiment",
        "cause_code",
        "rag_score",
        "llm_score",
        "risk_score",
        "hmm_state",
    ):
        with pytest.raises(LightGbmContractError, match="forbidden"):
            reject_forbidden_columns(["symbol", column])


def test_parquet_profile_safe_read_hash_and_unknown_column(tmp_path: Path) -> None:
    prices, market, cutoff = _feature_evidence()
    rows = build_core_feature_rows(prices, market, listing_market="KOSPI", cutoff=cutoff)
    table = feature_table_from_rows([row.as_mapping() for row in rows])
    payload = write_feature_parquet(table)
    path = tmp_path / "features.parquet"
    path.write_bytes(payload)
    artifact = read_feature_artifact(
        approved_root=tmp_path.resolve(),
        relative_path="features.parquet",
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        approved_feature_columns=CORE_FEATURE_COLUMNS,
    )
    assert artifact.logical_dataset_hash == logical_dataset_hash(table)
    labels = [index % 3 for index in range(table.num_rows)]
    training_hash = logical_training_dataset_hash(table, labels)
    changed_labels = labels.copy()
    changed_labels[-1] = (changed_labels[-1] + 1) % 3
    assert training_hash != logical_training_dataset_hash(table, changed_labels)

    nullable = table.set_column(
        2,
        table.schema.field(2),
        pa.array([None, *table.column(2).to_pylist()[1:]], type=pa.float32()),
    )
    assert logical_dataset_hash(nullable) != logical_dataset_hash(table)
    with pytest.raises(LightGbmContractError, match="class bytes"):
        logical_training_dataset_hash(table, [0] * (table.num_rows - 1))

    bad = table.append_column(
        "cross_market_score", pa.array([0.0] * table.num_rows, type=pa.float32())
    )
    bad_path = tmp_path / "bad.parquet"
    pq.write_table(bad, bad_path)
    with pytest.raises(LightGbmContractError, match="forbidden"):
        read_feature_artifact(
            approved_root=tmp_path.resolve(),
            relative_path="bad.parquet",
            expected_sha256=hashlib.sha256(bad_path.read_bytes()).hexdigest(),
            approved_feature_columns=CORE_FEATURE_COLUMNS,
        )


def test_symlink_and_source_absence_fail_closed(tmp_path: Path) -> None:
    target = tmp_path / "target.parquet"
    target.write_bytes(b"PAR1")
    link = tmp_path / "link.parquet"
    link.symlink_to(target)
    with pytest.raises(LightGbmContractError, match="path"):
        read_feature_artifact(
            approved_root=tmp_path.resolve(),
            relative_path="link.parquet",
            expected_sha256=hashlib.sha256(b"PAR1").hexdigest(),
            approved_feature_columns=CORE_FEATURE_COLUMNS,
        )
    with pytest.raises(DatasetUnavailable, match="DATASET_UNAVAILABLE"):
        require_source_rows(0)


def test_optional_groups_require_every_fold_at_98_percent_and_complete() -> None:
    assert optional_group_is_eligible([(98, 100, True), (99, 100, True), (100, 100, True)])
    assert not optional_group_is_eligible([(98, 100, True), (97, 100, True), (100, 100, True)])
    assert not optional_group_is_eligible([(98, 100, True), (99, 100, False), (100, 100, True)])
