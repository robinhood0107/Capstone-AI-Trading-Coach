from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
import hashlib
from zoneinfo import ZoneInfo

import numpy as np
import pyarrow as pa

from app.lightgbm.calibration import fit_ovr_platt
from app.lightgbm.export import (
    SignalArtifactIdentity,
    export_signal_artifact,
    signal_semantic_hash,
)
from app.lightgbm.feature_artifact import (
    FeatureArtifact,
    FeatureBundleProvenance,
    build_feature_manifest,
    feature_table_from_rows,
    logical_dataset_hash,
    logical_pit_input_sha256,
    logical_training_dataset_hash,
    logical_universe_schedule_sha256,
    write_feature_parquet,
)
from app.lightgbm.features import (
    CORE_FEATURE_COLUMNS,
    MarketEvidence,
    PriceEvidence,
    build_core_feature_rows,
)
from app.lightgbm.pit_calendar import (
    build_pit_session_window,
    derive_monthly_universe_schedule,
)
from app.lightgbm.training import (
    exact_grid,
    fit_lightgbm_reproducible,
    model_manifest_bytes,
    raw_margins,
)
from app.lightgbm.universe import MonthlyUniverse


KST = ZoneInfo("Asia/Seoul")


@dataclass
class _StrictCrossMarketReader:
    stored_snapshot: dict[str, object]
    calls: int = 0

    def read(self, symbol: str, session_date: date) -> object:
        self.calls += 1
        raise AssertionError((symbol, session_date, self.stored_snapshot))


@dataclass(frozen=True)
class _IsolationReceipt:
    reader_calls: int
    table: pa.Table
    logical_dataset_sha256: str
    logical_training_sha256: str
    feature_manifest: bytes
    feature_manifest_sha256: str
    model_text: bytes
    model_sha256: str
    calibrator: bytes
    calibrator_sha256: str
    signal_sha256: str


def _build_inputs() -> tuple[
    tuple[PriceEvidence, ...],
    tuple[MarketEvidence, ...],
    MonthlyUniverse,
    datetime,
]:
    cutoff = datetime(2026, 8, 15, 8, 10, tzinfo=KST)
    window = build_pit_session_window(cutoff)
    sessions = window.raw_sessions[-66:]
    markets: list[MarketEvidence] = []
    for index, session in enumerate(sessions):
        available_at = datetime.combine(session, time(18), tzinfo=KST)
        markets.append(
            MarketEvidence(
                session_date=session,
                market="KOSPI",
                market_adjusted_close=2_500.0 + index * 1.5 + (index % 5) * 0.2,
                base_rate=2.5 + (index // 20) * 0.01,
                usdkrw=1_300.0 + index * 0.3 + (index % 7) * 0.1,
                available_at=available_at,
                source_revision="market-r1",
                source_sha256=hashlib.sha256(f"market|{session}".encode()).hexdigest(),
            )
        )

    prices: list[PriceEvidence] = []
    symbols = tuple(f"{index:06d}" for index in range(1, 61))
    instrument_ids = tuple(f"instrument-{symbol}" for symbol in symbols)
    for symbol_index, (instrument_id, symbol) in enumerate(
        zip(instrument_ids, symbols, strict=True)
    ):
        for session_index, session in enumerate(sessions):
            close = (
                90.0
                + symbol_index * 0.7
                + session_index * (0.08 + (symbol_index % 4) * 0.01)
                + ((session_index + symbol_index) % 9) * 0.03
            )
            prices.append(
                PriceEvidence(
                    instrument_id=instrument_id,
                    symbol=symbol,
                    session_date=session,
                    adjusted_open=close - 0.1,
                    adjusted_close=close,
                    volume=1_000.0 + symbol_index * 13 + session_index * 7,
                    available_at=datetime.combine(session, time(18), tzinfo=KST),
                    source_revision="price-r1",
                    source_sha256=hashlib.sha256(
                        f"price|{instrument_id}|{session}".encode()
                    ).hexdigest(),
                )
            )
    schedule = derive_monthly_universe_schedule("2026-08", dataset_cutoff=cutoff)
    universe = MonthlyUniverse(
        selection_session=schedule.selection_session,
        effective_month=schedule.effective_month,
        instrument_ids=instrument_ids,
        symbols=symbols,
    )
    return tuple(prices), tuple(markets), universe, cutoff


def _run_pipeline(stored_snapshot: dict[str, object]) -> _IsolationReceipt:
    prices, markets, universe, cutoff = _build_inputs()
    reader = _StrictCrossMarketReader(stored_snapshot)
    rows = []
    by_instrument: dict[str, list[PriceEvidence]] = {}
    for item in prices:
        by_instrument.setdefault(item.instrument_id, []).append(item)
    for instrument_id in sorted(by_instrument):
        rows.extend(
            build_core_feature_rows(
                by_instrument[instrument_id],
                markets,
                listing_market="KOSPI",
                cutoff=cutoff,
                cross_market_reader=reader,
            )
        )
    rows.sort(key=lambda item: (item.session_date, item.symbol))
    table = feature_table_from_rows([item.as_mapping() for item in rows])
    labels = np.arange(table.num_rows, dtype=np.int64) % 3
    training_sha256 = logical_training_dataset_hash(table, labels.tolist())

    parquet = write_feature_parquet(table)
    artifact = FeatureArtifact(
        table=table,
        parquet_sha256=hashlib.sha256(parquet).hexdigest(),
        logical_dataset_hash=logical_dataset_hash(table),
        physical_bytes=len(parquet),
        decoded_bytes=table.nbytes,
    )
    window = build_pit_session_window(cutoff)
    schedule = derive_monthly_universe_schedule("2026-08", dataset_cutoff=cutoff)
    provenance = FeatureBundleProvenance(
        dataset_cutoff=cutoff,
        raw_session_start=window.raw_sessions[0],
        raw_session_end=window.raw_sessions[-1],
        raw_session_count=len(window.raw_sessions),
        eligible_session_start=window.eligible_sessions[0],
        eligible_session_end=window.eligible_sessions[-1],
        eligible_session_count=len(window.eligible_sessions),
        universe_schedule_sha256=logical_universe_schedule_sha256((schedule,)),
        pit_input_sha256=logical_pit_input_sha256((universe,), prices, markets),
    )
    feature_manifest = build_feature_manifest(artifact, provenance=provenance)

    features = np.column_stack(
        [
            np.asarray(table[name].to_numpy(zero_copy_only=False), dtype=np.float32)
            for name in CORE_FEATURE_COLUMNS
        ]
    )
    candidate = exact_grid()[0]
    model = fit_lightgbm_reproducible(
        features[:300],
        labels[:300],
        features[300:360],
        labels[300:360],
        candidate,
    )
    calibration_margins = raw_margins(model, features[360:420])
    calibrator = fit_ovr_platt(calibration_margins, labels[360:420])
    calibrator_bytes = calibrator.canonical_bytes()
    calibrator_sha256 = hashlib.sha256(calibrator_bytes).hexdigest()
    model_manifest = model_manifest_bytes(model, candidate, calibrator_sha256)
    report_sha256 = hashlib.sha256(model_manifest).hexdigest()

    signal_row = 0
    probabilities = calibrator.transform(calibration_margins[[signal_row]])[0]
    session = table["sessionDate"][360 + signal_row].as_py()
    assert isinstance(session, date)
    exported = export_signal_artifact(
        SignalArtifactIdentity(
            symbol=str(table["symbol"][360 + signal_row].as_py()),
            session_date=session,
            evaluation_id=f"eval-cross-market-isolation-{session.isoformat()}",
            model_version=f"lgbm-v1-{model.model_sha256[:12]}",
            model_report_id=f"mrp-{report_sha256[:12]}",
            dataset_sha256=training_sha256,
            model_sha256=model.model_sha256,
            report_sha256=report_sha256,
            payload_sha256=calibrator_sha256,
            provenance_sha256=hashlib.sha256(feature_manifest).hexdigest(),
            fixture=True,
            provenance_class="FAKE_CONTRACT",
        ),
        as_of=datetime.combine(session, time(18), tzinfo=KST).astimezone(UTC),
        current_completed_session=session,
        calibrated_probabilities=probabilities,
        raw_margins=calibration_margins[signal_row],
    )
    return _IsolationReceipt(
        reader_calls=reader.calls,
        table=table,
        logical_dataset_sha256=artifact.logical_dataset_hash,
        logical_training_sha256=training_sha256,
        feature_manifest=feature_manifest,
        feature_manifest_sha256=hashlib.sha256(feature_manifest).hexdigest(),
        model_text=model.model_text,
        model_sha256=model.model_sha256,
        calibrator=calibrator_bytes,
        calibrator_sha256=calibrator_sha256,
        signal_sha256=signal_semantic_hash(exported.payload),
    )


def test_cross_market_snapshot_mutation_cannot_change_s5_model_or_signal_hash() -> None:
    first = _run_pipeline(
        {
            "mode": "NORMAL",
            "score": 0.1,
            "freshness": "FRESH",
            "evidenceSha256": "1" * 64,
        }
    )
    second = _run_pipeline(
        {
            "mode": "RISK_OFF",
            "score": 99.0,
            "freshness": "STALE",
            "evidenceSha256": "9" * 64,
        }
    )

    assert first.reader_calls == second.reader_calls == 0
    assert first.table.equals(second.table)
    assert first.logical_dataset_sha256 == second.logical_dataset_sha256
    assert first.logical_training_sha256 == second.logical_training_sha256
    assert first.feature_manifest == second.feature_manifest
    assert first.feature_manifest_sha256 == second.feature_manifest_sha256
    assert first.model_text == second.model_text
    assert first.model_sha256 == second.model_sha256
    assert first.calibrator == second.calibrator
    assert first.calibrator_sha256 == second.calibrator_sha256
    assert first.signal_sha256 == second.signal_sha256
