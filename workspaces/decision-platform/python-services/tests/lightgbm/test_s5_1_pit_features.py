from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError
from app.lightgbm.feature_artifact import (
    FeatureArtifact,
    FeatureBundleProvenance,
    build_feature_manifest,
    feature_table_from_rows,
    logical_dataset_hash,
    logical_pit_input_sha256,
    logical_training_dataset_hash,
    logical_universe_schedule_sha256,
    optional_group_is_eligible,
    read_feature_bundle,
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
from app.lightgbm.pit_calendar import (
    S5_ADHOC_CLOSED_SESSIONS,
    MonthlyUniverseSchedule,
    build_pit_session_window,
    derive_monthly_universe_schedule,
    latest_completed_session,
)
from app.lightgbm.production_policy import APPROVED_HORIZON_UNION_SIZE
from app.lightgbm.universe import (
    MonthlyUniverse,
    UniverseObservation,
    select_monthly_universe,
    validate_horizon_union,
)

KST = ZoneInfo("Asia/Seoul")


def test_calendar_regression_has_exact_1072_and_1007_sessions() -> None:
    cutoff = datetime(2026, 8, 15, 8, 10, tzinfo=timezone(timedelta(hours=9)))
    window = build_pit_session_window(cutoff)

    assert window.latest_completed == date(2026, 8, 14)
    assert (window.raw_sessions[0], window.raw_sessions[-1], len(window.raw_sessions)) == (
        date(2022, 3, 30),
        date(2026, 8, 14),
        1_072,
    )
    assert (
        window.eligible_sessions[0],
        window.eligible_sessions[-1],
        len(window.eligible_sessions),
    ) == (
        date(2022, 6, 24),
        date(2026, 8, 6),
        1_007,
    )
    # 승인된 correction은 어떤 경계에도 남아 있으면 안 된다. 새 correction이 추가되면
    # 위 경계는 정확히 correction 수만큼 앞으로 밀린다.
    for closed in S5_ADHOC_CLOSED_SESSIONS:
        assert closed not in window.raw_sessions
        assert closed not in window.eligible_sessions
    # eligible은 label이 성숙한 raw의 연속 구간이며 raw 꼬리가 아니다.
    start = window.raw_sessions.index(window.eligible_sessions[0])
    assert (
        window.eligible_sessions
        == window.raw_sessions[start : start + len(window.eligible_sessions)]
    )


def _universe_schedule(effective_month: str = "2026-07") -> MonthlyUniverseSchedule:
    return derive_monthly_universe_schedule(
        effective_month,
        dataset_cutoff=datetime(2026, 8, 15, 8, 10, tzinfo=KST),
    )


def _universe_observations(
    schedule: MonthlyUniverseSchedule,
) -> list[UniverseObservation]:
    sessions = schedule.trailing_sessions
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
                    available_at=datetime.combine(session, time(16), tzinfo=KST),
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
                    available_at=datetime.combine(session, time(16), tzinfo=KST),
                    source_revision="r1",
                    source_sha256="b" * 64,
                )
            )
    return rows


def test_monthly_schedule_derives_holiday_year_boundary_and_kst_cutoff() -> None:
    march = _universe_schedule("2026-03")
    assert march.first_effective_session == date(2026, 3, 3)
    assert march.selection_session == date(2026, 2, 27)
    assert march.evidence_cutoff == datetime(2026, 3, 3, 8, 10, tzinfo=KST)
    assert len(march.trailing_sessions) == 20

    january = derive_monthly_universe_schedule(
        "2027-01",
        dataset_cutoff=datetime(2027, 2, 1, 8, 10, tzinfo=KST),
    )
    assert january.first_effective_session == date(2027, 1, 4)
    assert january.selection_session == date(2026, 12, 30)

    instant = datetime(2026, 8, 13, 23, 10, tzinfo=UTC)
    assert latest_completed_session(instant) == latest_completed_session(instant.astimezone(KST))


def test_monthly_schedule_rejects_invalid_or_future_inputs() -> None:
    with pytest.raises(LightGbmContractError, match=r"YYYY-MM"):
        derive_monthly_universe_schedule(
            "2026-7",
            dataset_cutoff=datetime(2026, 8, 15, 8, 10, tzinfo=KST),
        )
    with pytest.raises(LightGbmContractError, match=r"timezone aware"):
        derive_monthly_universe_schedule(
            "2026-07",
            dataset_cutoff=datetime(2026, 8, 15, 8, 10),
        )
    with pytest.raises(DatasetUnavailable, match=r"dataset cutoff"):
        derive_monthly_universe_schedule(
            "2026-07",
            dataset_cutoff=datetime(2026, 7, 1, 8, 9, tzinfo=KST),
        )


def test_month_end_top30_tie_order_fixed_etf_and_no_etn() -> None:
    schedule = _universe_schedule()
    rows = _universe_observations(schedule)
    universe = select_monthly_universe(
        rows,
        schedule=schedule,
    )

    assert len(universe.symbols) == 31
    assert universe.symbols[:3] == ("000001", "000002", "000003")
    assert universe.symbols[-1] == "132030"
    assert "580001" not in universe.symbols

    forged_selection = replace(
        schedule,
        selection_session=schedule.trailing_sessions[-2],
    )
    with pytest.raises(LightGbmContractError, match=r"derived"):
        select_monthly_universe(rows, schedule=forged_selection)

    forged_trailing = replace(
        schedule,
        trailing_sessions=(date(2026, 6, 7), *schedule.trailing_sessions[1:]),
    )
    with pytest.raises(LightGbmContractError, match=r"derived"):
        select_monthly_universe(rows, schedule=forged_trailing)


def test_monthly_universe_does_not_replace_and_union_over_bound_fails() -> None:
    universes = [
        MonthlyUniverse(date(2026, 1, 30), "2026-02", (f"id-{index}",), (f"{index:06d}",))
        for index in range(APPROVED_HORIZON_UNION_SIZE + 1)
    ]
    with pytest.raises(LightGbmContractError, match=r"approved instrument bound"):
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


def test_pit_provenance_hash_binds_authoritative_inputs_only() -> None:
    prices, market, _ = _feature_evidence()
    schedule = _universe_schedule()
    universe = MonthlyUniverse(
        schedule.selection_session,
        schedule.effective_month,
        ("instrument-005930",),
        ("005930",),
    )
    first = logical_pit_input_sha256((universe,), prices, market)
    changed_price = replace(prices[0], adjusted_close=101.0, source_sha256="f" * 64)
    second = logical_pit_input_sha256((universe,), [changed_price, *prices[1:]], market)
    assert first != second
    assert logical_universe_schedule_sha256((schedule,)) == logical_universe_schedule_sha256(
        (schedule,)
    )


def test_universe_ignores_future_revision_and_requires_exact_thirty() -> None:
    schedule = _universe_schedule()
    rows = _universe_observations(schedule)
    target = next(
        row
        for row in rows
        if row.symbol == "000001" and row.session_date == schedule.selection_session
    )
    rows.append(
        UniverseObservation(
            **{
                **target.__dict__,
                "market_cap": 20_000.0,
                "available_at": schedule.evidence_cutoff,
                "source_revision": "r2",
                "source_sha256": "e" * 64,
            }
        )
    )
    rows.append(
        UniverseObservation(
            **{
                **target.__dict__,
                "market_cap": 0.0,
                "available_at": schedule.evidence_cutoff + timedelta(seconds=1),
                "source_revision": "r3",
                "source_sha256": "f" * 64,
            }
        )
    )
    universe = select_monthly_universe(
        rows,
        schedule=schedule,
    )
    assert universe.symbols[0] == "000001"

    reduced = [row for row in rows if row.symbol not in {"000030", "000031", "000032"}]
    with pytest.raises(DatasetUnavailable, match=r"top-30"):
        select_monthly_universe(
            reduced,
            schedule=schedule,
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
        with pytest.raises(LightGbmContractError, match=r"forbidden"):
            reject_forbidden_columns(["symbol", column])


def _bundle_provenance() -> FeatureBundleProvenance:
    """달력 경계를 하드코딩하지 않고 유도해 correction 추가에도 fixture가 어긋나지 않게 한다."""

    cutoff = datetime(2026, 8, 15, 8, 10, tzinfo=KST)
    window = build_pit_session_window(cutoff)
    return FeatureBundleProvenance(
        dataset_cutoff=cutoff,
        raw_session_start=window.raw_sessions[0],
        raw_session_end=window.raw_sessions[-1],
        raw_session_count=len(window.raw_sessions),
        eligible_session_start=window.eligible_sessions[0],
        eligible_session_end=window.eligible_sessions[-1],
        eligible_session_count=len(window.eligible_sessions),
        universe_schedule_sha256="c" * 64,
        pit_input_sha256="d" * 64,
    )


def _write_feature_bundle(root: Path, table: pa.Table) -> tuple[bytes, bytes]:
    root.mkdir()
    parquet = write_feature_parquet(table)
    artifact = FeatureArtifact(
        table=table,
        parquet_sha256=hashlib.sha256(parquet).hexdigest(),
        logical_dataset_hash=logical_dataset_hash(table),
        physical_bytes=len(parquet),
        decoded_bytes=table.nbytes,
    )
    manifest = build_feature_manifest(artifact, provenance=_bundle_provenance())
    (root / "features.parquet").write_bytes(parquet)
    (root / "manifest.json").write_bytes(manifest)
    return manifest, parquet


def _read_manifest(manifest: bytes) -> dict[str, object]:
    value = json.loads(manifest)
    assert isinstance(value, dict)
    return value


def test_feature_bundle_round_trip_and_logical_hashes(tmp_path: Path) -> None:
    prices, market, cutoff = _feature_evidence()
    rows = build_core_feature_rows(prices, market, listing_market="KOSPI", cutoff=cutoff)
    table = feature_table_from_rows([row.as_mapping() for row in rows])
    root = tmp_path / "bundle"
    manifest, _ = _write_feature_bundle(root, table)
    bundle = read_feature_bundle(
        approved_root=root.resolve(),
        expected_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
    )
    assert bundle.artifact.logical_dataset_hash == logical_dataset_hash(table)
    assert bundle.manifest_bytes == manifest
    assert bundle.provenance == _bundle_provenance()
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
    with pytest.raises(LightGbmContractError, match=r"class bytes"):
        logical_training_dataset_hash(table, [0] * (table.num_rows - 1))


def test_feature_bundle_rejects_manifest_trust_and_shape_mutations(tmp_path: Path) -> None:
    prices, market, cutoff = _feature_evidence()
    rows = build_core_feature_rows(prices, market, listing_market="KOSPI", cutoff=cutoff)
    table = feature_table_from_rows([row.as_mapping() for row in rows])
    root = tmp_path / "bundle"
    manifest, _ = _write_feature_bundle(root, table)

    with pytest.raises(LightGbmContractError, match=r"trust anchor"):
        read_feature_bundle(
            approved_root=root.resolve(),
            expected_manifest_sha256="0" * 64,
        )

    documents: list[tuple[bytes, str]] = []
    duplicate = manifest.replace(
        b'"columnCount":19',
        b'"columnCount":19,"columnCount":19',
        1,
    )
    documents.append((duplicate, "JSON"))
    documents.append((manifest[:-1] + b" \n", "canonical"))

    unknown = _read_manifest(manifest)
    unknown["crossMarketScore"] = 1
    documents.append((canonical_json_bytes(unknown), "unknown"))

    bad_version = _read_manifest(manifest)
    bad_version["manifestVersion"] = "s5-feature-bundle-v2"
    documents.append((canonical_json_bytes(bad_version), "version"))

    bad_provenance = _read_manifest(manifest)
    provenance = bad_provenance["provenance"]
    assert isinstance(provenance, dict)
    provenance["unexpected"] = True
    documents.append((canonical_json_bytes(bad_provenance), "provenance"))

    optional = _read_manifest(manifest)
    optional_provenance = optional["provenance"]
    assert isinstance(optional_provenance, dict)
    optional_provenance["optionalFeatureGroups"] = ["news"]
    documents.append((canonical_json_bytes(optional), "optional"))

    wrong_window = _read_manifest(manifest)
    window_provenance = wrong_window["provenance"]
    assert isinstance(window_provenance, dict)
    window_provenance["rawSessionStart"] = "2022-04-04"
    documents.append((canonical_json_bytes(wrong_window), "session window"))

    for index, (mutated, message) in enumerate(documents):
        case_root = tmp_path / f"manifest-case-{index}"
        case_root.mkdir()
        (case_root / "manifest.json").write_bytes(mutated)
        with pytest.raises(LightGbmContractError, match=message):
            read_feature_bundle(
                approved_root=case_root.resolve(),
                expected_manifest_sha256=hashlib.sha256(mutated).hexdigest(),
            )


def test_feature_bundle_rejects_parquet_schema_and_manifest_mismatch(tmp_path: Path) -> None:
    prices, market, cutoff = _feature_evidence()
    rows = build_core_feature_rows(prices, market, listing_market="KOSPI", cutoff=cutoff)
    table = feature_table_from_rows([row.as_mapping() for row in rows])
    valid_root = tmp_path / "valid"
    manifest, _ = _write_feature_bundle(valid_root, table)

    bad = table.append_column(
        "cross_market_score", pa.array([0.0] * table.num_rows, type=pa.float32())
    )
    bad_root = tmp_path / "bad-schema"
    bad_root.mkdir()
    bad_path = bad_root / "features.parquet"
    pq.write_table(bad, bad_path, compression="zstd")
    bad_manifest = _read_manifest(manifest)
    bad_manifest["parquetSha256"] = hashlib.sha256(bad_path.read_bytes()).hexdigest()
    bad_manifest_bytes = canonical_json_bytes(bad_manifest)
    (bad_root / "manifest.json").write_bytes(bad_manifest_bytes)
    with pytest.raises(LightGbmContractError, match=r"forbidden"):
        read_feature_bundle(
            approved_root=bad_root.resolve(),
            expected_manifest_sha256=hashlib.sha256(bad_manifest_bytes).hexdigest(),
        )

    mismatches = (
        ("rowCount", table.num_rows + 1),
        ("columnCount", table.num_columns + 1),
        ("logicalDatasetHash", "9" * 64),
    )
    for index, (field, value) in enumerate(mismatches):
        case_root = tmp_path / f"mismatch-{index}"
        case_root.mkdir()
        (case_root / "features.parquet").write_bytes((valid_root / "features.parquet").read_bytes())
        document = _read_manifest(manifest)
        document[field] = value
        case_manifest = canonical_json_bytes(document)
        (case_root / "manifest.json").write_bytes(case_manifest)
        with pytest.raises(LightGbmContractError, match=r"count|decoded Parquet"):
            read_feature_bundle(
                approved_root=case_root.resolve(),
                expected_manifest_sha256=hashlib.sha256(case_manifest).hexdigest(),
            )


def test_feature_bundle_symlink_bounds_and_source_absence_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prices, market, cutoff = _feature_evidence()
    rows = build_core_feature_rows(prices, market, listing_market="KOSPI", cutoff=cutoff)
    table = feature_table_from_rows([row.as_mapping() for row in rows])
    regular_root = tmp_path / "regular"
    manifest, parquet = _write_feature_bundle(regular_root, table)

    manifest_link_root = tmp_path / "manifest-link"
    manifest_link_root.mkdir()
    (manifest_link_root / "target.json").write_bytes(manifest)
    (manifest_link_root / "manifest.json").symlink_to(manifest_link_root / "target.json")
    with pytest.raises(LightGbmContractError, match=r"manifest path"):
        read_feature_bundle(
            approved_root=manifest_link_root.resolve(),
            expected_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        )

    parquet_link_root = tmp_path / "parquet-link"
    parquet_link_root.mkdir()
    (parquet_link_root / "target.parquet").write_bytes(parquet)
    (parquet_link_root / "features.parquet").symlink_to(parquet_link_root / "target.parquet")
    (parquet_link_root / "manifest.json").write_bytes(manifest)
    with pytest.raises(LightGbmContractError, match=r"path"):
        read_feature_bundle(
            approved_root=parquet_link_root.resolve(),
            expected_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        )

    hash_root = tmp_path / "hash-mismatch"
    hash_root.mkdir()
    (hash_root / "features.parquet").write_bytes(parquet)
    hash_document = _read_manifest(manifest)
    hash_document["parquetSha256"] = "0" * 64
    hash_manifest = canonical_json_bytes(hash_document)
    (hash_root / "manifest.json").write_bytes(hash_manifest)
    with pytest.raises(LightGbmContractError, match=r"does not match manifest"):
        read_feature_bundle(
            approved_root=hash_root.resolve(),
            expected_manifest_sha256=hashlib.sha256(hash_manifest).hexdigest(),
        )

    monkeypatch.setattr("app.lightgbm.feature_artifact.MAX_MANIFEST_BYTES", len(manifest) - 1)
    with pytest.raises(LightGbmContractError, match=r"manifest path"):
        read_feature_bundle(
            approved_root=regular_root.resolve(),
            expected_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        )
    monkeypatch.setattr("app.lightgbm.feature_artifact.MAX_MANIFEST_BYTES", 1024 * 1024)
    monkeypatch.setattr("app.lightgbm.feature_artifact.MAX_PHYSICAL_BYTES", len(parquet) - 1)
    with pytest.raises(LightGbmContractError, match=r"artifact path"):
        read_feature_bundle(
            approved_root=regular_root.resolve(),
            expected_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        )
    monkeypatch.setattr("app.lightgbm.feature_artifact.MAX_PHYSICAL_BYTES", 256 * 1024 * 1024)
    monkeypatch.setattr("app.lightgbm.feature_artifact.MAX_DECODED_BYTES", 1)
    with pytest.raises(LightGbmContractError, match=r"decoded size"):
        read_feature_bundle(
            approved_root=regular_root.resolve(),
            expected_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        )

    zero_root = tmp_path / "zero"
    zero_root.mkdir()
    zero = _read_manifest(manifest)
    zero["rowCount"] = 0
    zero_manifest = canonical_json_bytes(zero)
    (zero_root / "manifest.json").write_bytes(zero_manifest)
    with pytest.raises(DatasetUnavailable, match=r"DATASET_UNAVAILABLE"):
        read_feature_bundle(
            approved_root=zero_root.resolve(),
            expected_manifest_sha256=hashlib.sha256(zero_manifest).hexdigest(),
        )
    with pytest.raises(DatasetUnavailable, match=r"DATASET_UNAVAILABLE"):
        require_source_rows(0)


def test_optional_groups_require_every_fold_at_98_percent_and_complete() -> None:
    assert optional_group_is_eligible([(98, 100, True), (99, 100, True), (100, 100, True)])
    assert not optional_group_is_eligible([(98, 100, True), (97, 100, True), (100, 100, True)])
    assert not optional_group_is_eligible([(98, 100, True), (99, 100, False), (100, 100, True)])
