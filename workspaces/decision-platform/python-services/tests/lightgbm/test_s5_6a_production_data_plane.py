from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import hashlib
from io import BytesIO
import os
from pathlib import Path
import stat

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.data.ecos.models import ECOSObservation
from app.data.ecos.series_registry import CANDIDATE_SERIES
from app.data.ecos.settings import ECOSS5ProductionSettings
from app.data.kis.parsers import DailyBar, KISResponseError, parse_daily_bars
from app.data.krx.production_parsers import (
    S5_PRODUCTION_PROJECTION_FIELDS,
    parse_s5_production_response,
)
from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError
from app.lightgbm.bootstrap_control import BootstrapLedger, BootstrapPhase
from app.lightgbm.bootstrap_executor import (
    BootstrapAcquisition,
    execute_bootstrap_acquisition,
    materialize_production_feature_bundle,
)
from app.lightgbm.bootstrap_journal import (
    BootstrapJournal,
    build_resume_packet,
    validate_resume_packet,
)
from app.lightgbm.bootstrap_packet import author_bootstrap_packet, validate_bootstrap_packet
from app.lightgbm.feature_artifact import (
    FeatureArtifact,
    FeatureBundleProvenance,
    ProductionFeatureBundleProvenance,
    build_feature_manifest,
    build_production_feature_manifest,
    feature_table_from_rows,
    logical_dataset_hash,
    read_production_feature_bundle,
    write_feature_parquet,
)
from app.lightgbm.features import (
    CORE_FEATURE_COLUMNS,
    IndexEvidence,
    MacroObservation,
    ProductionPriceEvidence,
    build_production_core_feature_rows,
)
from app.lightgbm.labels import build_production_exact_labels
from app.lightgbm.pit_calendar import PitSessionWindow, build_pit_session_window
from app.lightgbm.private_root import acquire_run_lock, release_run_lock, require_private_root
from app.lightgbm.production_policy import (
    BootstrapBudget,
    SecurityClassification,
    author_bootstrap_budget,
    align_macro_observations,
    corporate_action_sensitivity_pass,
    classify_krx_security,
    is_spac_name,
    macro_timing_sensitivity_pass,
    require_standard_stock_identity,
)
from app.lightgbm.source_bundle import (
    SourceBundle,
    SourceChunkReceipt,
    build_source_manifest,
    parse_source_chunk_receipt,
    read_source_bundle,
)
from app.lightgbm.temporal import (
    AvailabilityBasis,
    RevisionBasis,
    TemporalQuality,
    TemporalReceipt,
    collapse_or_reject_snapshots,
    feature_as_of,
    label_as_of,
    next_session_evidence_clock,
    require_receipt_eligible,
)
from app.lightgbm.universe import (
    MonthlyUniverse,
    ProductionUniverseObservation,
    select_production_monthly_universe,
)


def _receipt(digest: str = "1" * 64) -> TemporalReceipt:
    observation = date(2026, 8, 14)
    return TemporalReceipt(
        source_id="KIS",
        operation_id="FHKST03010100",
        observation_date=observation,
        retrieved_at=datetime(2026, 8, 16, tzinfo=UTC),
        availability_basis=AvailabilityBasis.PROJECT_FIXED_LAG,
        revision_basis=RevisionBasis.CONTENT_SNAPSHOT,
        request_sha256="2" * 64,
        snapshot_sha256=digest,
        temporal_quality=TemporalQuality.RECONSTRUCTED_FIXED_LAG,
        policy_effective_at=next_session_evidence_clock(observation),
    )


def _krx_receipt(day: date, operation: str, digest: str) -> TemporalReceipt:
    return TemporalReceipt(
        source_id="KRX",
        operation_id=operation,
        observation_date=day,
        retrieved_at=datetime(2026, 8, 16, tzinfo=UTC),
        availability_basis=AvailabilityBasis.PROVIDER_AS_OF_SCHEDULE,
        revision_basis=RevisionBasis.CONTENT_SNAPSHOT,
        request_sha256="3" * 64,
        snapshot_sha256=digest,
        temporal_quality=TemporalQuality.PROVIDER_AS_OF_NO_VINTAGE,
        policy_effective_at=next_session_evidence_clock(day),
    )


def test_temporal_receipt_uses_row_clock_and_never_fabricates_provider_fields() -> None:
    receipt = _receipt()
    assert receipt.provider_available_at is None
    assert receipt.provider_revision is None
    assert feature_as_of(date(2026, 8, 14)) == datetime.fromisoformat(
        "2026-08-18T08:10:00+09:00"
    )
    assert label_as_of(date(2026, 8, 14)) == feature_as_of(date(2026, 8, 14))
    require_receipt_eligible(
        receipt,
        row_clock=feature_as_of(date(2026, 8, 14)),
        dataset_cutoff=datetime(2026, 8, 18, tzinfo=UTC),
    )
    with pytest.raises(DatasetUnavailable, match="row clock"):
        require_receipt_eligible(
            receipt,
            row_clock=datetime(2026, 8, 14, tzinfo=UTC),
            dataset_cutoff=datetime(2026, 8, 18, tzinfo=UTC),
        )

    with pytest.raises(LightGbmContractError, match="provider revision"):
        replace(receipt, provider_revision="hash-is-not-revision")
    with pytest.raises(LightGbmContractError, match="UTC"):
        replace(receipt, retrieved_at=datetime(2026, 8, 16, 9, tzinfo=feature_as_of(date(2026, 8, 14)).tzinfo))


def test_duplicate_snapshot_collapses_and_conflict_fails_without_sha_ordering() -> None:
    first = ("key", _receipt("1" * 64))
    duplicate = ("key", _receipt("1" * 64))
    assert len(
        collapse_or_reject_snapshots(
            [first, duplicate], logical_key=lambda value: value[0], receipt_of=lambda value: value[1]
        )
    ) == 1
    conflict = ("key", _receipt("f" * 64))
    with pytest.raises(LightGbmContractError, match="SOURCE_SNAPSHOT_CONFLICT"):
        collapse_or_reject_snapshots(
            [first, conflict], logical_key=lambda value: value[0], receipt_of=lambda value: value[1]
        )


def test_bootstrap_budget_and_universe_identity_rules() -> None:
    budget = author_bootstrap_budget(monthly_schedule_count=51, union_size=180)
    assert budget == BootstrapBudget(
        krx_get=4_441, kis_get=1_980, kis_token=1, ecos_get=24
    )
    assert budget.total == 6_446


def test_bootstrap_cap_rejects_dimensions_that_do_not_fit() -> None:
    with pytest.raises(LightGbmContractError, match="budget exceeded"):
        author_bootstrap_budget(monthly_schedule_count=52, union_size=180)
    assert is_spac_name(" 미래에셋비전 스팩 1호 ")
    assert is_spac_name("테스트기업인수목적")
    assert not is_spac_name("삼성전자")
    assert require_standard_stock_identity("KR7005930003") == "KR7005930003"
    with pytest.raises(DatasetUnavailable, match="identity"):
        require_standard_stock_identity("005930")
    assert classify_krx_security(
        security_group="주권",
        stock_kind="보통주",
        official_name="삼성전자",
        source_service="stk_isu_base_info",
    ) is SecurityClassification.COMMON_STOCK
    assert classify_krx_security(
        security_group="주권",
        stock_kind="보통주",
        official_name="테스트스팩1호",
        source_service="ksq_isu_base_info",
    ) is SecurityClassification.SPAC


def test_kis_adjustment_fields_are_preserved_and_closed() -> None:
    payload = {
        "rt_cd": "0",
        "output2": [
            {
                "stck_bsop_date": "20260814",
                "stck_oprc": "100",
                "stck_hgpr": "110",
                "stck_lwpr": "90",
                "stck_clpr": "105",
                "acml_vol": "1000",
                "acml_tr_pbmn": "100000",
                "flng_cls_code": "01",
                "prtt_rate": "0.5",
                "mod_yn": "Y",
                "revl_issu_reas": "액면분할",
            }
        ],
    }
    row = parse_daily_bars(payload, "005930")[0]
    assert (row.flng_cls_code, row.prtt_rate, row.mod_yn, row.revl_issu_reas) == (
        "01", Decimal("0.5"), "Y", "액면분할"
    )
    payload["output2"][0]["mod_yn"] = "UNKNOWN"
    with pytest.raises(KISResponseError, match="ADJUSTMENT_FIELD_INVALID"):
        parse_daily_bars(payload, "005930")


def test_production_root_requires_exact_0700_and_rejects_symlink(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    require_private_root(private)

    private.chmod(0o750)
    with pytest.raises(LightGbmContractError, match="0700"):
        require_private_root(private)

    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(LightGbmContractError, match="symlink"):
        require_private_root(alias)


def test_bootstrap_run_lock_allows_only_one_active_process(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir(mode=0o700)
    first = acquire_run_lock(run_root)
    try:
        with pytest.raises(LightGbmContractError, match="already active"):
            acquire_run_lock(run_root)
    finally:
        release_run_lock(first)

    second = acquire_run_lock(run_root)
    release_run_lock(second)


def test_corporate_action_and_macro_sensitivity_gates() -> None:
    assert corporate_action_sensitivity_pass([0.01] * 1000, [0.01] * 1000, [0.02] * 1000, [0.02] * 1000)
    changed = [0.01] * 998 + [0.02, 0.02]
    assert not corporate_action_sensitivity_pass([0.01] * 1000, changed, [0.02] * 1000, [0.02] * 1000)

    labels = [0, 1, 2] * 10
    primary = np.eye(3, dtype=np.float64)[labels] * 0.8 + 0.2 / 3
    primary /= primary.sum(axis=1, keepdims=True)
    assert macro_timing_sensitivity_pass(
        primary_probabilities=primary,
        delayed_probabilities=primary.copy(),
        labels=labels,
        primary_row_count=len(labels),
    )
    with pytest.raises(DatasetUnavailable, match="98%"):
        macro_timing_sensitivity_pass(
            primary_probabilities=primary[:-1],
            delayed_probabilities=primary[:-1],
            labels=labels[:-1],
            primary_row_count=100,
        )
    sessions = [date(2026, 8, 13), date(2026, 8, 14)]
    assert align_macro_observations(
        sessions=sessions,
        base_rate_observations={date(2026, 8, 1): Decimal("2.5")},
        usdkrw_observations={
            date(2026, 8, 13): Decimal("1380"),
            date(2026, 8, 14): Decimal("1381"),
        },
    ) == ((2.5, 1380.0), (2.5, 1381.0))
    with pytest.raises(DatasetUnavailable, match="exact macro"):
        align_macro_observations(
            sessions=sessions,
            base_rate_observations={date(2026, 8, 1): Decimal("2.5")},
            usdkrw_observations={date(2026, 8, 13): Decimal("1380")},
        )


def test_feature_bundle_v2_is_required_for_production(tmp_path: Path) -> None:
    cutoff = datetime(2026, 8, 15, 8, 10, tzinfo=feature_as_of(date(2026, 8, 14)).tzinfo)
    window = build_pit_session_window(cutoff)
    table = feature_table_from_rows(
        [
            {
                "symbol": "005930",
                "sessionDate": window.eligible_sessions[0],
                **{name: np.float32(0) for name in CORE_FEATURE_COLUMNS},
            }
        ]
    )
    parquet = write_feature_parquet(table)
    artifact = FeatureArtifact(
        table=table,
        parquet_sha256=hashlib.sha256(parquet).hexdigest(),
        logical_dataset_hash=logical_dataset_hash(table),
        physical_bytes=len(parquet),
        decoded_bytes=table.nbytes,
    )
    base = FeatureBundleProvenance(
        dataset_cutoff=cutoff,
        raw_session_start=window.raw_sessions[0],
        raw_session_end=window.raw_sessions[-1],
        raw_session_count=len(window.raw_sessions),
        eligible_session_start=window.eligible_sessions[0],
        eligible_session_end=window.eligible_sessions[-1],
        eligible_session_count=len(window.eligible_sessions),
        universe_schedule_sha256="a" * 64,
        pit_input_sha256="b" * 64,
    )
    production = ProductionFeatureBundleProvenance(
        base=base,
        source_bundle_set_sha256="c" * 64,
        source_policy_set_sha256="d" * 64,
    )
    manifest = build_production_feature_manifest(artifact, provenance=production)
    (tmp_path / "features.parquet").write_bytes(parquet)
    (tmp_path / "manifest.json").write_bytes(manifest)
    (tmp_path / "features.parquet").chmod(0o600)
    (tmp_path / "manifest.json").chmod(0o600)
    bundle = read_production_feature_bundle(
        approved_root=tmp_path,
        expected_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
    )
    assert bundle.artifact.logical_dataset_hash == artifact.logical_dataset_hash

    v1_manifest = build_feature_manifest(artifact, provenance=base)
    (tmp_path / "manifest.json").write_bytes(v1_manifest)
    with pytest.raises(LightGbmContractError, match="version"):
        read_production_feature_bundle(
            approved_root=tmp_path,
            expected_manifest_sha256=hashlib.sha256(v1_manifest).hexdigest(),
        )


def test_source_bundle_uses_manifest_trust_anchor_and_digest_derived_path(tmp_path: Path) -> None:
    fields = (
        "symbol",
        "observationDate",
        "adjustedOpen",
        "adjustedHigh",
        "adjustedLow",
        "adjustedClose",
        "volume",
        "turnover",
        "flngClsCode",
        "prttRate",
        "modYn",
        "revlIssuReas",
    )
    table = pa.Table.from_pylist(
        [{field: "1" for field in fields}],
        schema=pa.schema([pa.field(field, pa.string(), nullable=False) for field in fields]),
    )
    sink = BytesIO()
    pq.write_table(table, sink, version="2.6", compression="zstd", use_dictionary=False)
    content = sink.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    receipt = replace(_receipt(digest), snapshot_sha256=digest)
    chunk = SourceChunkReceipt(
        source_id="KIS",
        operation_id="FHKST03010100",
        query_key="005930:20260814:0",
        content_sha256=digest,
        row_count=1,
        byte_count=len(content),
        temporal=receipt,
    )
    (tmp_path / "chunks").mkdir(mode=0o700)
    (tmp_path / chunk.relative_path).write_bytes(content)
    (tmp_path / chunk.relative_path).chmod(0o600)
    manifest = build_source_manifest(
        created_at=datetime(2026, 8, 16, tzinfo=UTC),
        dataset_cutoff=datetime(2026, 8, 18, tzinfo=UTC),
        chunks=[chunk],
    )
    (tmp_path / "manifest.json").write_bytes(manifest)
    (tmp_path / "manifest.json").chmod(0o600)
    bundle = read_source_bundle(
        approved_root=tmp_path,
        expected_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
    )
    assert bundle.chunks == (chunk,)

    with pytest.raises(LightGbmContractError, match="trust anchor"):
        read_source_bundle(approved_root=tmp_path, expected_manifest_sha256="0" * 64)

    with pytest.raises(LightGbmContractError, match="physical byte cap"):
        build_source_manifest(
            created_at=datetime(2026, 8, 16, tzinfo=UTC),
            dataset_cutoff=datetime(2026, 8, 18, tzinfo=UTC),
            chunks=[replace(chunk, byte_count=10 * 1024**2 + 1)],
        )

    with pytest.raises(LightGbmContractError, match="row cap"):
        build_source_manifest(
            created_at=datetime(2026, 8, 16, tzinfo=UTC),
            dataset_cutoff=datetime(2026, 8, 18, tzinfo=UTC),
            chunks=[replace(chunk, row_count=101)],
        )

    with pytest.raises(LightGbmContractError, match="latest receipt"):
        build_source_manifest(
            created_at=datetime(2026, 8, 16, 0, 0, 1, tzinfo=UTC),
            dataset_cutoff=datetime(2026, 8, 18, tzinfo=UTC),
            chunks=[chunk],
        )

    with pytest.raises(LightGbmContractError, match="after dataset cutoff"):
        build_source_manifest(
            created_at=datetime(2026, 8, 16, tzinfo=UTC),
            dataset_cutoff=next_session_evidence_clock(receipt.observation_date)
            - timedelta(seconds=1),
            chunks=[chunk],
        )

    wrong_policy = replace(
        chunk,
        temporal=replace(
            chunk.temporal,
            availability_basis=AvailabilityBasis.PROVIDER_AS_OF_SCHEDULE,
            temporal_quality=TemporalQuality.PROVIDER_AS_OF_NO_VINTAGE,
        ),
    )
    with pytest.raises(LightGbmContractError, match="provider policy"):
        parse_source_chunk_receipt(wrong_policy.as_dict())


def test_bootstrap_failure_stops_remaining_calls_and_resume_targets_failed_chunk() -> None:
    ledger = BootstrapLedger(BootstrapBudget(krx_get=3, kis_get=1, kis_token=1, ecos_get=1))
    assert ledger.physical_call(
        provider="KRX",
        operation_id="stk_bydd_trd",
        query_key_sha256="1" * 64,
        call=lambda: "ok",
    ) == "ok"
    with pytest.raises(RuntimeError, match="provider failed"):
        ledger.physical_call(
            provider="KRX",
            operation_id="ksq_bydd_trd",
            query_key_sha256="2" * 64,
            call=lambda: (_ for _ in ()).throw(RuntimeError("provider failed")),
        )
    with pytest.raises(LightGbmContractError, match="no longer"):
        ledger.physical_call(
            provider="KRX",
            operation_id="ksq_bydd_trd",
            query_key_sha256="3" * 64,
            call=lambda: "forbidden",
        )
    ledger.resume_failed(query_key_sha256="2" * 64)
    assert ledger.physical_call(
        provider="KRX",
        operation_id="ksq_bydd_trd",
        query_key_sha256="2" * 64,
        call=lambda: "resumed",
    ) == "resumed"
    ledger.advance(BootstrapPhase.KRX)
    assert ledger.phase is BootstrapPhase.KIS


def test_failed_token_call_consumes_the_only_token_budget() -> None:
    ledger = BootstrapLedger(
        BootstrapBudget(krx_get=0, kis_get=1, kis_token=1, ecos_get=0)
    )
    ledger.advance(BootstrapPhase.KRX)
    query = "c" * 64
    with pytest.raises(RuntimeError):
        ledger.physical_call(
            provider="KIS",
            operation_id="oauth2/tokenP",
            query_key_sha256=query,
            call=lambda: (_ for _ in ()).throw(RuntimeError("token failed")),
        )
    ledger.resume_failed(query_key_sha256=query)
    with pytest.raises(LightGbmContractError, match="budget exhausted"):
        ledger.physical_call(
            provider="KIS",
            operation_id="oauth2/tokenP",
            query_key_sha256=query,
            call=lambda: None,
        )


def test_durable_journal_authors_one_bounded_resume_packet(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    journal = BootstrapJournal(root)
    ordinal = journal.begin(
        provider="KRX", operation_id="stk_bydd_trd", query_sha256="1" * 64
    )
    journal.finish(
        ordinal=ordinal,
        provider="KRX",
        operation_id="stk_bydd_trd",
        query_sha256="1" * 64,
        success=False,
        chunk=None,
    )
    packet = build_resume_packet(
        bootstrap_packet_sha256="2" * 64,
        journal=BootstrapJournal(root),
        total_cap=10,
    )
    assert validate_resume_packet(
        packet.content,
        expected_sha256=packet.sha256,
        bootstrap_packet_sha256="2" * 64,
        journal=BootstrapJournal(root),
        total_cap=10,
    ) == packet
    retry = BootstrapJournal(root)
    second = retry.begin(
        provider="KRX", operation_id="stk_bydd_trd", query_sha256="1" * 64
    )
    retry.finish(
        ordinal=second,
        provider="KRX",
        operation_id="stk_bydd_trd",
        query_sha256="1" * 64,
        success=False,
        chunk=None,
    )
    with pytest.raises(LightGbmContractError, match="authority is exhausted"):
        build_resume_packet(
            bootstrap_packet_sha256="2" * 64,
            journal=BootstrapJournal(root),
            total_cap=10,
        )
    with pytest.raises(LightGbmContractError, match="resume attempt"):
        BootstrapJournal(root).begin(
            provider="KRX", operation_id="stk_bydd_trd", query_sha256="1" * 64
        )


def test_durable_journal_allows_provider_free_local_finalization_resume(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    journal = BootstrapJournal(root)
    ordinal = journal.begin(
        provider="KIS", operation_id="oauth2/tokenP", query_sha256="3" * 64
    )
    journal.finish(
        ordinal=ordinal,
        provider="KIS",
        operation_id="oauth2/tokenP",
        query_sha256="3" * 64,
        success=True,
        chunk=None,
    )

    packet = build_resume_packet(
        bootstrap_packet_sha256="4" * 64,
        journal=BootstrapJournal(root),
        total_cap=10,
    )

    assert packet.failed_query_sha256 is None
    assert b'"resumeMode":"LOCAL_FINALIZATION"' in packet.content


def test_durable_journal_rejects_ambiguous_handoff(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    BootstrapJournal(root).begin(
        provider="KIS", operation_id="FHKST03010100", query_sha256="3" * 64
    )
    with pytest.raises(LightGbmContractError, match="ambiguous"):
        BootstrapJournal(root)


def test_krx_production_base_info_is_closed_and_requires_standard_identity() -> None:
    row = {
        "ISU_CD": "KR7005930003",
        "ISU_SRT_CD": "005930",
        "ISU_NM": "삼성전자",
        "ISU_ABBRV": "삼성전자",
        "ISU_ENG_NM": "SamsungElectronics",
        "LIST_DD": "1975/06/11",
        "MKT_TP_NM": "KOSPI",
        "SECUGRP_NM": "주권",
        "SECT_TP_NM": "중견기업부",
        "KIND_STKCERT_TP_NM": "보통주",
        "PARVAL": "100",
        "LIST_SHRS": "5969782550",
    }
    parsed = parse_s5_production_response(
        {"OutBlock_1": [row]},
        service="stk_isu_base_info",
        requested_date=date(2026, 8, 14),
    )
    assert parsed[0]["ISU_CD"] == "KR7005930003"
    unknown = dict(row)
    unknown["UNEXPECTED"] = "x"
    with pytest.raises(LightGbmContractError, match="field set"):
        parse_s5_production_response(
            {"OutBlock_1": [unknown]},
            service="stk_isu_base_info",
            requested_date=date(2026, 8, 14),
        )


def test_bootstrap_packet_has_exact_1072_1007_51_and_6446_caps() -> None:
    cutoff = datetime(2026, 8, 17, 23, 10, tzinfo=UTC)
    packet = author_bootstrap_packet(cutoff=cutoff)
    assert len(packet.window.raw_sessions) == 1_072
    assert len(packet.window.eligible_sessions) == 1_007
    assert len(packet.schedules) == 51
    assert packet.budget.krx_get == 4_441
    assert packet.budget.total == 6_446
    assert b'"strictProviderPITClaim":false' in packet.content
    assert validate_bootstrap_packet(packet.content, expected_sha256=packet.sha256) == packet
    with pytest.raises(LightGbmContractError, match="trust anchor"):
        validate_bootstrap_packet(packet.content, expected_sha256="0" * 64)
    settings = ECOSS5ProductionSettings()
    assert (settings.max_calls_per_run, settings.max_attempts_per_request) == (24, 1)
    with pytest.raises(LightGbmContractError, match="label maturity"):
        author_bootstrap_packet(cutoff=cutoff - timedelta(seconds=1))


def test_production_universe_uses_temporal_receipts_and_exact_31() -> None:
    packet = author_bootstrap_packet(cutoff=datetime(2026, 8, 17, 23, 10, tzinfo=UTC))
    schedule = packet.schedules[-1]
    rows: list[ProductionUniverseObservation] = []
    for symbol_index in range(31):
        symbol = f"{symbol_index + 1:06d}"
        identity = f"KR7{symbol}003"
        for day_index, day in enumerate(schedule.trailing_sessions):
            rows.append(
                ProductionUniverseObservation(
                    instrument_id=identity,
                    symbol=symbol,
                    session_date=day,
                    trading_value=float(100_000 - symbol_index * 100 + day_index),
                    market_cap=float(1_000_000 - symbol_index),
                    market="KOSPI",
                    security_type="COMMON_STOCK",
                    common_share=True,
                    listed=True,
                    trading_receipt=_krx_receipt(day, "stk_bydd_trd", f"{day_index + 1:064x}"),
                    identity_receipt=_krx_receipt(
                        schedule.selection_session,
                        "stk_isu_base_info",
                        "a" * 64,
                    ),
                )
            )
    rows.append(
        ProductionUniverseObservation(
            instrument_id="XKRX:ETF:132030",
            symbol="132030",
            session_date=schedule.selection_session,
            trading_value=1.0,
            market_cap=1.0,
            market="KOSPI",
            security_type="ETF",
            common_share=False,
            listed=True,
            trading_receipt=_krx_receipt(
                schedule.selection_session, "etf_bydd_trd", "b" * 64
            ),
            identity_receipt=_krx_receipt(
                schedule.selection_session, "etf_bydd_trd", "b" * 64
            ),
        )
    )
    universe = select_production_monthly_universe(rows, schedule=schedule)
    assert len(universe.symbols) == 31
    assert universe.symbols[-1] == "132030"
    assert universe.symbols[:3] == ("000001", "000002", "000003")

    conflict = replace(rows[0], trading_receipt=replace(rows[0].trading_receipt, snapshot_sha256="f" * 64))
    with pytest.raises(LightGbmContractError, match="SOURCE_SNAPSHOT_CONFLICT"):
        select_production_monthly_universe([*rows, conflict], schedule=schedule)


def test_production_feature_and_label_use_row_specific_clocks() -> None:
    packet = author_bootstrap_packet(cutoff=datetime(2026, 8, 17, 23, 10, tzinfo=UTC))
    sessions = packet.window.raw_sessions[:66]
    cutoff = datetime(2026, 8, 16, tzinfo=UTC)

    def receipt(source: str, operation: str, day: date, digest: str) -> TemporalReceipt:
        return TemporalReceipt(
            source_id=source,
            operation_id=operation,
            observation_date=day,
            retrieved_at=datetime(2026, 8, 15, tzinfo=UTC),
            availability_basis=(
                AvailabilityBasis.PROVIDER_AS_OF_SCHEDULE
                if source == "KRX"
                else AvailabilityBasis.PROJECT_FIXED_LAG
            ),
            revision_basis=RevisionBasis.CONTENT_SNAPSHOT,
            request_sha256="a" * 64,
            snapshot_sha256=digest,
            temporal_quality=(
                TemporalQuality.PROVIDER_AS_OF_NO_VINTAGE
                if source == "KRX"
                else TemporalQuality.RECONSTRUCTED_FIXED_LAG
            ),
            policy_effective_at=next_session_evidence_clock(day),
        )

    prices = [
        ProductionPriceEvidence(
            instrument_id="KR7005930003",
            symbol="005930",
            session_date=day,
            adjusted_open=100.0 + index,
            adjusted_close=100.0 + index,
            volume=1000.0,
            flng_cls_code="",
            prtt_rate=0.0,
            mod_yn="N",
            revl_issu_reas="",
            receipt=receipt("KIS", "FHKST03010100", day, f"{index + 1:064x}"),
        )
        for index, day in enumerate(sessions)
    ]
    indices = [
        IndexEvidence(
            session_date=day,
            market="KOSPI",
            adjusted_close=2000.0 + index,
            receipt=receipt("KRX", "kospi_dd_trd", day, f"{index + 101:064x}"),
        )
        for index, day in enumerate(sessions)
    ]
    macro = [
        MacroObservation(
            series_id="policy-rate",
            observation_date=sessions[0],
            value=2.5,
            receipt=receipt("ECOS", "722Y001/0101000/D", sessions[0], "e" * 64),
        ),
        *[
            MacroObservation(
                series_id="krw-usd-rate",
                observation_date=day,
                value=1300.0 + index,
                receipt=receipt("ECOS", "731Y001/0000001/D", day, f"{index + 201:064x}"),
            )
            for index, day in enumerate(sessions)
        ],
    ]
    rows = build_production_core_feature_rows(
        prices, indices, macro, listing_market="KOSPI", cutoff=cutoff
    )
    assert len(rows) == 7
    assert len(build_production_exact_labels(prices, dataset_cutoff=cutoff)) == 60
    before_last_maturity = label_as_of(sessions[-1]) - timedelta(seconds=1)
    assert (
        len(
            build_production_exact_labels(
                prices, dataset_cutoff=before_last_maturity
            )
        )
        == 59
    )

    leaked = list(prices)
    leaked[10] = replace(
        leaked[10],
        receipt=replace(
            leaked[10].receipt,
            policy_effective_at=feature_as_of(leaked[10].session_date).replace(year=2027),
        ),
    )
    with pytest.raises(DatasetUnavailable, match="row clock"):
        build_production_core_feature_rows(
            leaked, indices, macro, listing_market="KOSPI", cutoff=cutoff
        )

    invalid_adjustment = list(prices)
    invalid_adjustment[10] = replace(invalid_adjustment[10], prtt_rate=float("nan"))
    with pytest.raises(DatasetUnavailable, match="production price evidence"):
        build_production_core_feature_rows(
            invalid_adjustment, indices, macro, listing_market="KOSPI", cutoff=cutoff
        )


def test_bootstrap_executor_orders_providers_and_seals_private_manifest(tmp_path: Path) -> None:
    original = author_bootstrap_packet(cutoff=datetime(2026, 8, 19, 0, 0, tzinfo=UTC))
    raw = original.window.raw_sessions[-60:]
    schedule = original.schedules[-1]
    packet = replace(
        original,
        window=PitSessionWindow(
            cutoff=original.window.cutoff,
            latest_completed=raw[-1],
            raw_sessions=raw,
            eligible_sessions=raw[59:],
        ),
        schedules=(schedule,),
        budget=BootstrapBudget(krx_get=243, kis_get=31, kis_token=1, ecos_get=3),
    )
    calls: list[str] = []

    def full(service: str, values: dict[str, str]) -> dict[str, str]:
        row = {field: "1" for field in S5_PRODUCTION_PROJECTION_FIELDS[service]}
        row.update(values)
        return row

    class Krx:
        def fetch(self, *, service: str, session_date: date) -> tuple[dict[str, str], ...]:
            del session_date
            calls.append(f"KRX:{service}")
            if service in {"stk_bydd_trd", "ksq_bydd_trd"}:
                offset = 0 if service == "stk_bydd_trd" else 31
                count = 31 if service == "stk_bydd_trd" else 1
                return tuple(
                    full(
                        service,
                        {
                            "ISU_CD": f"{offset + index + 1:06d}",
                            "ACC_TRDVAL": str(1_000_000 - offset - index),
                            "MKTCAP": str(10_000_000 - offset - index),
                        },
                    )
                    for index in range(count)
                )
            if service in {"kospi_dd_trd", "kosdaq_dd_trd"}:
                return (
                    full(
                        service,
                        {
                            "IDX_NM": "코스피" if service == "kospi_dd_trd" else "코스닥",
                            "CLSPRC_IDX": "2500",
                        },
                    ),
                )
            if service in {"stk_isu_base_info", "ksq_isu_base_info"}:
                offset = 0 if service == "stk_isu_base_info" else 31
                count = 31 if service == "stk_isu_base_info" else 1
                market = "KOSPI" if service == "stk_isu_base_info" else "KOSDAQ"
                return tuple(
                    full(
                        service,
                        {
                            "ISU_CD": f"KR7{offset + index + 1:06d}003",
                            "ISU_SRT_CD": f"{offset + index + 1:06d}",
                            "ISU_NM": f"테스트{offset + index + 1}",
                            "SECUGRP_NM": "주권",
                            "KIND_STKCERT_TP_NM": "보통주",
                            "MKT_TP_NM": market,
                        },
                    )
                    for index in range(count)
                )
            return (
                full(
                    service,
                    {"ISU_CD": "132030", "ACC_TRDVAL": "1", "MKTCAP": "1"},
                ),
            )

    class Kis:
        def prepare_access_token(self) -> None:
            calls.append("KIS:token")

        def require_cached_token_only(self) -> None:
            return None

        def fetch_page(
            self, *, symbol: str, start: date, end: date
        ) -> tuple[DailyBar, ...]:
            calls.append("KIS:page")
            return tuple(
                DailyBar(
                    symbol=symbol,
                    date=day,
                    open=100,
                    high=101,
                    low=99,
                    close=100,
                    volume=1000,
                )
                for day in raw
                if start <= day <= end
            )

    class Ecos:
        def fetch(self, *, series: object, start: date, end: date) -> tuple[ECOSObservation, ...]:
            calls.append("ECOS:page")
            series_id = getattr(series, "series_id")
            days = [day for day in raw if start <= day <= end]
            if series_id == "policy-rate" and not days:
                days = [start]
            return tuple(
                ECOSObservation(time=day.strftime("%Y%m%d"), value="2.5") for day in days
            )

    source_root = tmp_path / "source"
    result = execute_bootstrap_acquisition(
        packet=packet,
        source_root=source_root,
        krx=Krx(),
        kis=Kis(),
        ecos=Ecos(),
        ecos_series=CANDIDATE_SERIES,
        clock=lambda: datetime(2026, 8, 19, tzinfo=UTC),
    )
    assert result.physical_calls == 278
    assert calls[:243] == [
        f"KRX:{service}"
        for _ in raw
        for service in ("stk_bydd_trd", "ksq_bydd_trd", "kospi_dd_trd", "kosdaq_dd_trd")
    ] + [
        "KRX:stk_isu_base_info",
        "KRX:ksq_isu_base_info",
        "KRX:etf_bydd_trd",
    ]
    assert calls[243] == "KIS:token"
    assert calls[-3:] == ["ECOS:page", "ECOS:page", "ECOS:page"]
    assert len(result.universes[0].symbols) == 31
    assert stat.S_IMODE(os.stat(source_root / "manifest.json").st_mode) == 0o600
    completed_calls = tuple(calls)
    resumed = execute_bootstrap_acquisition(
        packet=packet,
        source_root=source_root,
        krx=Krx(),
        kis=Kis(),
        ecos=Ecos(),
        ecos_series=CANDIDATE_SERIES,
        clock=lambda: datetime(2026, 8, 20, tzinfo=UTC),
        resume=True,
    )
    assert tuple(calls) == completed_calls
    assert resumed.source_bundle.manifest_sha256 == result.source_bundle.manifest_sha256


def test_materializer_publishes_feature_bundle_v2_from_verified_source(tmp_path: Path) -> None:
    # 최신 raw session label tail이 성숙한 첫 08:10 clock에서 production packet을 연다.
    packet = author_bootstrap_packet(cutoff=datetime(2026, 8, 17, 23, 10, tzinfo=UTC))
    identity = "KR7005930003"

    def receipt(source: str, operation: str, day: date, ordinal: int) -> TemporalReceipt:
        return TemporalReceipt(
            source_id=source,
            operation_id=operation,
            observation_date=day,
            retrieved_at=datetime(2026, 8, 19, tzinfo=UTC),
            availability_basis=(
                AvailabilityBasis.PROVIDER_AS_OF_SCHEDULE
                if source == "KRX"
                else AvailabilityBasis.PROJECT_FIXED_LAG
            ),
            revision_basis=RevisionBasis.CONTENT_SNAPSHOT,
            request_sha256=f"{ordinal + 1:064x}",
            snapshot_sha256=f"{ordinal + 10_000:064x}",
            temporal_quality=(
                TemporalQuality.PROVIDER_AS_OF_NO_VINTAGE
                if source == "KRX"
                else TemporalQuality.RECONSTRUCTED_FIXED_LAG
            ),
            policy_effective_at=next_session_evidence_clock(day),
        )

    prices = tuple(
        ProductionPriceEvidence(
            instrument_id=identity,
            symbol="005930",
            session_date=day,
            adjusted_open=100.0 + index / 100,
            adjusted_close=100.0 + index / 100,
            volume=1000.0 + index,
            flng_cls_code="",
            prtt_rate=0.0,
            mod_yn="N",
            revl_issu_reas="",
            receipt=receipt("KIS", "FHKST03010100", day, index),
        )
        for index, day in enumerate(packet.window.raw_sessions)
    )
    indices = tuple(
        IndexEvidence(
            session_date=day,
            market="KOSPI",
            adjusted_close=2000.0 + index / 10,
            receipt=receipt("KRX", "kospi_dd_trd", day, index + 2_000),
        )
        for index, day in enumerate(packet.window.raw_sessions)
    )
    macro = (
        MacroObservation(
            series_id="policy-rate",
            observation_date=packet.window.raw_sessions[0],
            value=2.5,
            receipt=receipt(
                "ECOS", "722Y001/0101000/D", packet.window.raw_sessions[0], 4_000
            ),
        ),
        *tuple(
            MacroObservation(
                series_id="krw-usd-rate",
                observation_date=day,
                value=1300.0 + index / 10,
                receipt=receipt("ECOS", "731Y001/0000001/D", day, index + 5_000),
            )
            for index, day in enumerate(packet.window.raw_sessions)
        ),
    )
    universes = tuple(
        MonthlyUniverse(
            selection_session=schedule.selection_session,
            effective_month=schedule.effective_month,
            instrument_ids=(identity,),
            symbols=("005930",),
        )
        for schedule in packet.schedules
    )
    source = SourceBundle(
        manifest_sha256="a" * 64,
        manifest_bytes=b"{}",
        dataset_cutoff=packet.window.cutoff,
        chunks=(),
        receipt_set_sha256="b" * 64,
    )
    acquisition = BootstrapAcquisition(
        source_bundle=source,
        universes=universes,
        prices=prices,
        indices=indices,
        macro=macro,
        listing_market_by_membership={
            (identity, schedule.effective_month): "KOSPI" for schedule in packet.schedules
        },
        physical_calls=0,
    )
    bundle = materialize_production_feature_bundle(
        packet=packet,
        acquisition=acquisition,
        feature_root=tmp_path / "feature",
    )
    assert bundle.artifact.table.num_rows == 1_007
    assert bundle.provenance.temporal_quality == "RECONSTRUCTED_FIXED_LAG"
    assert stat.S_IMODE(os.stat(tmp_path / "feature" / "manifest.json").st_mode) == 0o600
