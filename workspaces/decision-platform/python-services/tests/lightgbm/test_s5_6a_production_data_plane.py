from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import stat
from zoneinfo import ZoneInfo

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.ecos.models import ECOSObservation
from app.data.ecos.policy import ECOS_MAX_ROWS_PER_REQUEST, build_keyless_service_path
from app.data.ecos.series_registry import CANDIDATE_SERIES
from app.data.ecos.settings import ECOSS5ProductionSettings
from app.data.kis.parsers import DailyBar, KISResponseError, parse_daily_bars
from app.data.krx.production_parsers import (
    S5_PRODUCTION_PROJECTION_FIELDS,
    parse_s5_production_response,
)
from app.lightgbm.errors import CalendarDivergenceSuspected, DatasetUnavailable, LightGbmContractError
from app.lightgbm.bootstrap_control import BootstrapLedger, BootstrapPhase
from app.lightgbm import bootstrap_executor
from app.lightgbm.diagnostics import read_diagnostics, record_diagnostic
from app.lightgbm.outcomes import BootstrapEvidenceGap, OutcomeClass
from app.lightgbm.bootstrap_executor import (
    DIVERGENCE_CANDIDATES_FILENAME,
    BootstrapAcquisition,
    _fetch_kis_symbol,
    execute_bootstrap_acquisition,
    materialize_production_feature_bundle,
    provider_query_sha256,
)
from app.lightgbm.bootstrap_calendar_recovery import (
    KrxQuery,
    _validate_reusable_chunk,
    assess_bootstrap_calendar_recovery,
    materialize_recovery_adoption,
    validate_recovery_receipt,
    validate_recovery_execution_authority,
)
from app.lightgbm.bootstrap_fresh_authority import (
    fresh_bootstrap_authority_exists,
    FRESH_AUTHORITY_FILENAME,
    publish_fresh_bootstrap_authority,
    read_fresh_bootstrap_authority,
)
from app.lightgbm.bootstrap_journal import (
    JOURNAL_FILENAME,
    MAX_JOURNAL_BYTES,
    BootstrapJournal,
    JournalAttempt,
    SUPERSEDED_CONSUMED,
    build_recovery_journal_bytes,
    build_resume_packet,
    validate_resume_packet,
)
from app.lightgbm.bootstrap_packet import (
    _author_bootstrap_packet,
    author_bootstrap_packet,
    author_recovery_bootstrap_packet,
    latest_publishable_bootstrap_cutoff,
    validate_bootstrap_packet,
)
from app.lightgbm import (
    bootstrap_calendar_recovery,
    bootstrap_execute_cli,
    bootstrap_packet_cli,
)
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
from app.lightgbm.pit_calendar import (
    previous_xkrx_session,
    S5_ADHOC_CLOSED_SESSIONS,
    S5_CALENDAR_CORRECTION_SET_SHA256,
    S5_SUPERSEDED_CORRECTION_SETS,
    calendar_for_corrections,
    correction_set_sha256,
    corrections_for_sha256,
    S5_CALENDAR_POLICY_VERSION,
    PitSessionWindow,
    base_calendar,
    build_pit_session_window,
    corrected_calendar,
)
from app.lightgbm.private_root import (
    acquire_bootstrap_root_lock,
    acquire_run_lock,
    release_run_lock,
    require_private_root,
)
from app.lightgbm.production_policy import (
    APPROVED_KIS_MAX_GET,
    MAX_KIS_SUPERSEDED_ALLOWANCE,
    MAX_KIS_TOKEN_SUPERSEDED_ALLOWANCE,
    MAX_KRX_SUPERSEDED_ALLOWANCE,
    BootstrapBudget,
    SecurityClassification,
    author_bootstrap_budget,
    author_recovery_bootstrap_budget,
    align_macro_observations,
    corporate_action_sensitivity_pass,
    classify_krx_security,
    is_spac_name,
    macro_timing_sensitivity_pass,
    require_standard_stock_identity,
)
from app.lightgbm.runtime_inputs import (
    resolve_bootstrap_packet_sha256,
    resolve_code_provenance,
    resolve_repository_root,
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
    next_xkrx_evidence_clock,
    require_receipt_eligible,
)
from app.lightgbm.universe import (
    MonthlyUniverse,
    ProductionUniverseObservation,
    select_production_monthly_universe,
)
from app.rag.safe_io import write_approved_new_file


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


def test_bootstrap_root_lock_serializes_different_packet_runs(tmp_path: Path) -> None:
    root = tmp_path / "s5-source"
    root.mkdir(mode=0o700)
    first = acquire_bootstrap_root_lock(root)
    try:
        with pytest.raises(LightGbmContractError, match="root is already active"):
            acquire_bootstrap_root_lock(root)
    finally:
        release_run_lock(first)

    second = acquire_bootstrap_root_lock(root)
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
    assert packet.budget.total == 7_436
    assert packet.packet_version == "s5-production-bootstrap-packet-v2"
    assert packet.lineage_mode == "FRESH"
    assert packet.recovery_binding_sha256 is None
    assert packet.calendar_policy_version == S5_CALENDAR_POLICY_VERSION
    assert packet.calendar_correction_set_sha256 == S5_CALENDAR_CORRECTION_SET_SHA256
    assert packet.window.raw_sessions[0] == date(2022, 3, 30)
    assert date(2026, 6, 3) not in packet.window.raw_sessions
    assert b'"strictProviderPITClaim":false' in packet.content
    assert validate_bootstrap_packet(packet.content, expected_sha256=packet.sha256) == packet
    with pytest.raises(LightGbmContractError, match="trust anchor"):
        validate_bootstrap_packet(packet.content, expected_sha256="0" * 64)
    settings = ECOSS5ProductionSettings()
    assert (settings.max_calls_per_run, settings.max_attempts_per_request) == (24, 1)
    with pytest.raises(LightGbmContractError, match="label maturity"):
        author_bootstrap_packet(cutoff=cutoff - timedelta(seconds=1))


def test_s5_calendar_applies_kis_authority_to_all_feature_and_label_clocks() -> None:
    packet = author_bootstrap_packet(cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC))
    payload = json.loads(packet.content)

    assert packet.window.raw_sessions[0] == date(2022, 3, 29)
    for closed in S5_ADHOC_CLOSED_SESSIONS:
        assert closed not in packet.window.raw_sessions
    assert payload["calendarPolicyVersion"] == S5_CALENDAR_POLICY_VERSION
    assert payload["calendarCorrectionSetSha256"] == S5_CALENDAR_CORRECTION_SET_SHA256
    assert next_session_evidence_clock(date(2026, 6, 2)) == datetime(
        2026, 6, 4, 8, 10, tzinfo=next_session_evidence_clock(date(2026, 6, 2)).tzinfo
    )
    assert next_xkrx_evidence_clock(date(2026, 6, 3)).date() == date(2026, 6, 4)
    # 2026-07-17 제헌절도 같은 권위로 닫히므로 직전 session의 다음 증거 시각은 07-20이다.
    assert next_session_evidence_clock(date(2026, 7, 16)) == datetime(
        2026, 7, 20, 8, 10, tzinfo=next_session_evidence_clock(date(2026, 7, 16)).tzinfo
    )
    assert next_xkrx_evidence_clock(date(2026, 7, 17)).date() == date(2026, 7, 20)

    catalog = json.loads(
        (
            Path(__file__).resolve().parents[5]
            / "contracts/catalogs/s5-bootstrap-calendar-recovery-lock.v1.json"
        ).read_text(encoding="utf-8")
    )
    assert catalog["calendar"]["policyVersion"] == S5_CALENDAR_POLICY_VERSION
    assert (
        catalog["calendar"]["correctionSetSha256"]
        == S5_CALENDAR_CORRECTION_SET_SHA256
    )


def test_calendar_recovery_grants_exact_superseded_allowance_and_closes_shortfall(
    tmp_path: Path,
) -> None:
    root = tmp_path / "s5-source"
    root.mkdir(mode=0o700)
    legacy = _author_bootstrap_packet(
        cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC),
        calendar=base_calendar(),
        packet_version="s5-production-bootstrap-packet-v1",
    )
    packet_result = write_approved_new_file(
        approved_root=root,
        relative_path=f"bootstrap-{legacy.sha256}.json",
        content=legacy.content,
        max_bytes=1 * 1024 * 1024,
    )
    os.chmod(packet_result.absolute_path, 0o600)
    run_root = root / f"run-{legacy.sha256}"
    source_root = run_root / "source"
    run_root.mkdir(mode=0o700)
    source_root.mkdir(mode=0o700)
    (source_root / "chunks").mkdir(mode=0o700)
    query_hash = provider_query_sha256(
        {"service": "stk_bydd_trd", "basDd": "20260603"}
    )
    journal = BootstrapJournal(source_root, policy_corrections=())
    for _ in range(2):
        ordinal = journal.begin(
            provider="KRX",
            operation_id="stk_bydd_trd",
            query_sha256=query_hash,
        )
        journal.finish(
            ordinal=ordinal,
            provider="KRX",
            operation_id="stk_bydd_trd",
            query_sha256=query_hash,
            success=False,
            chunk=None,
        )

    recovery = assess_bootstrap_calendar_recovery(
        approved_root=root,
        prior_packet_sha256=legacy.sha256,
    )
    receipt = json.loads(recovery.content)
    assert recovery.status == "READY_TO_SUPERSEDE"
    assert recovery.corrected_packet.lineage_mode == "CALENDAR_RECOVERY"
    assert (
        recovery.corrected_packet.recovery_binding_sha256
        == recovery.recovery_binding_sha256
    )
    assert recovery.missing_krx_queries == 4_441
    assert recovery.projected_krx_physical_calls == 4_443
    # Allowance는 증명된 superseded consumed call 수와 정확히 같고 논리 query를 늘리지 않는다.
    assert len(recovery.superseded_attempts) == 2
    assert recovery.corrected_packet.budget.krx_superseded_allowance == 2
    assert recovery.corrected_packet.budget.krx_get == 4_443
    assert recovery.corrected_packet.budget.total == 7_438
    assert recovery.krx_shortfall == 0
    assert receipt["krxSupersededAllowance"] == 2
    assert receipt["approvedKrxMaxGet"] == 4_443
    assert receipt["providerCallsDuringRecovery"] == 0
    assert receipt["failedSessionDate"] == "2026-06-03"
    assert receipt["calendarCorrectionSetSha256"] == S5_CALENDAR_CORRECTION_SET_SHA256

    validated = validate_recovery_receipt(
        recovery.content,
        corrected_packet=recovery.corrected_packet,
    )
    assert validated["recoveryBindingSha256"] == recovery.recovery_binding_sha256
    tampered = dict(receipt)
    tampered["priorProgressSha256"] = "f" * 64
    with pytest.raises(LightGbmContractError, match="binding preimage"):
        validate_recovery_receipt(
            canonical_json_bytes(tampered),
            corrected_packet=recovery.corrected_packet,
        )

    with pytest.raises(LightGbmContractError, match="version is not approved"):
        validate_bootstrap_packet(legacy.content, expected_sha256=legacy.sha256)
    assert (
        validate_bootstrap_packet(
            legacy.content,
            expected_sha256=legacy.sha256,
            allow_historical_v1=True,
        )
        == legacy
    )


def test_legacy_journal_receipt_is_read_only_and_requires_temporal_rebinding() -> None:
    receipt = SourceChunkReceipt(
        source_id="KRX",
        operation_id="stk_bydd_trd",
        query_key="stk_bydd_trd:2026-06-02",
        content_sha256="a" * 64,
        row_count=1,
        byte_count=1,
        temporal=TemporalReceipt(
            source_id="KRX",
            operation_id="stk_bydd_trd",
            observation_date=date(2026, 6, 2),
            retrieved_at=datetime(2026, 8, 17, tzinfo=UTC),
            availability_basis=AvailabilityBasis.PROVIDER_AS_OF_SCHEDULE,
            revision_basis=RevisionBasis.CONTENT_SNAPSHOT,
            request_sha256="b" * 64,
            snapshot_sha256="a" * 64,
            temporal_quality=TemporalQuality.PROVIDER_AS_OF_NO_VINTAGE,
            policy_effective_at=datetime(
                2026, 6, 3, 8, 10, tzinfo=ZoneInfo("Asia/Seoul")
            ),
        ),
    )

    with pytest.raises(LightGbmContractError, match="fixed-lag clock"):
        parse_source_chunk_receipt(receipt.as_dict())
    assert (
        parse_source_chunk_receipt(
            receipt.as_dict(),
            policy_corrections=(),
        )
        == receipt
    )


def test_recovery_rejects_krx_chunk_whose_parquet_rows_belong_to_another_date(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir(mode=0o700)
    (source_root / "chunks").mkdir(mode=0o700)
    query_date = date(2026, 6, 2)
    query_sha256 = provider_query_sha256(
        {"service": "stk_bydd_trd", "basDd": query_date.strftime("%Y%m%d")}
    )
    fields = sorted(S5_PRODUCTION_PROJECTION_FIELDS["stk_bydd_trd"])
    table = pa.Table.from_pylist(
        [
            {
                field: ("20260604" if field == "BAS_DD" else "1")
                for field in fields
            }
        ],
        schema=pa.schema(
            [pa.field(field, pa.string(), nullable=False) for field in fields]
        ),
    )
    sink = BytesIO()
    pq.write_table(
        table,
        sink,
        version="2.6",
        compression="zstd",
        use_dictionary=False,
    )
    content = sink.getvalue()
    content_sha256 = hashlib.sha256(content).hexdigest()
    chunk = SourceChunkReceipt(
        source_id="KRX",
        operation_id="stk_bydd_trd",
        query_key="stk_bydd_trd:2026-06-02",
        content_sha256=content_sha256,
        row_count=1,
        byte_count=len(content),
        temporal=TemporalReceipt(
            source_id="KRX",
            operation_id="stk_bydd_trd",
            observation_date=query_date,
            retrieved_at=datetime(2026, 8, 17, tzinfo=UTC),
            availability_basis=AvailabilityBasis.PROVIDER_AS_OF_SCHEDULE,
            revision_basis=RevisionBasis.CONTENT_SNAPSHOT,
            request_sha256=query_sha256,
            snapshot_sha256=content_sha256,
            temporal_quality=TemporalQuality.PROVIDER_AS_OF_NO_VINTAGE,
            policy_effective_at=datetime(
                2026, 6, 3, 8, 10, tzinfo=ZoneInfo("Asia/Seoul")
            ),
        ),
    )
    written = write_approved_new_file(
        approved_root=source_root,
        relative_path=chunk.relative_path,
        content=content,
        max_bytes=4 * 1024 * 1024,
    )
    os.chmod(written.absolute_path, 0o600)

    with pytest.raises(LightGbmContractError, match="Parquet row date"):
        _validate_reusable_chunk(
            source_root=source_root,
            attempt=JournalAttempt(
                ordinal=1,
                provider="KRX",
                operation_id="stk_bydd_trd",
                query_sha256=query_sha256,
                state="SUCCEEDED",
                chunk=chunk,
            ),
            query=KrxQuery("stk_bydd_trd", query_date, query_sha256),
        )


def test_recovery_authority_and_adoption_stop_before_provider_client_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "s5-source"
    root.mkdir(mode=0o700)
    legacy = _author_bootstrap_packet(
        cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC),
        calendar=base_calendar(),
        packet_version="s5-production-bootstrap-packet-v1",
    )
    prior_file = write_approved_new_file(
        approved_root=root,
        relative_path=f"bootstrap-{legacy.sha256}.json",
        content=legacy.content,
        max_bytes=1 * 1024 * 1024,
    )
    os.chmod(prior_file.absolute_path, 0o600)
    source_root = root / f"run-{legacy.sha256}" / "source"
    source_root.parent.mkdir(mode=0o700)
    source_root.mkdir(mode=0o700)
    (source_root / "chunks").mkdir(mode=0o700)
    reusable_query_hash = provider_query_sha256(
        {"service": "stk_bydd_trd", "basDd": "20260602"}
    )
    fields = sorted(S5_PRODUCTION_PROJECTION_FIELDS["stk_bydd_trd"])
    table = pa.Table.from_pylist(
        [{field: ("20260602" if field == "BAS_DD" else "1") for field in fields}],
        schema=pa.schema(
            [pa.field(field, pa.string(), nullable=False) for field in fields]
        ),
    )
    sink = BytesIO()
    pq.write_table(table, sink, version="2.6", compression="zstd", use_dictionary=False)
    chunk_content = sink.getvalue()
    chunk_sha256 = hashlib.sha256(chunk_content).hexdigest()
    reusable_chunk = SourceChunkReceipt(
        source_id="KRX",
        operation_id="stk_bydd_trd",
        query_key="stk_bydd_trd:2026-06-02",
        content_sha256=chunk_sha256,
        row_count=1,
        byte_count=len(chunk_content),
        temporal=TemporalReceipt(
            source_id="KRX",
            operation_id="stk_bydd_trd",
            observation_date=date(2026, 6, 2),
            retrieved_at=datetime(2026, 8, 17, tzinfo=UTC),
            availability_basis=AvailabilityBasis.PROVIDER_AS_OF_SCHEDULE,
            revision_basis=RevisionBasis.CONTENT_SNAPSHOT,
            request_sha256=reusable_query_hash,
            snapshot_sha256=chunk_sha256,
            temporal_quality=TemporalQuality.PROVIDER_AS_OF_NO_VINTAGE,
            policy_effective_at=datetime(
                2026, 6, 3, 8, 10, tzinfo=ZoneInfo("Asia/Seoul")
            ),
        ),
    )
    chunk_file = write_approved_new_file(
        approved_root=source_root,
        relative_path=reusable_chunk.relative_path,
        content=chunk_content,
        max_bytes=4 * 1024 * 1024,
    )
    os.chmod(chunk_file.absolute_path, 0o600)
    failed_hash = provider_query_sha256(
        {"service": "stk_bydd_trd", "basDd": "20260603"}
    )
    journal = BootstrapJournal(source_root, policy_corrections=())
    reusable_ordinal = journal.begin(
        provider="KRX",
        operation_id="stk_bydd_trd",
        query_sha256=reusable_query_hash,
    )
    journal.finish(
        ordinal=reusable_ordinal,
        provider="KRX",
        operation_id="stk_bydd_trd",
        query_sha256=reusable_query_hash,
        success=True,
        chunk=reusable_chunk,
    )
    for _ in range(2):
        ordinal = journal.begin(
            provider="KRX", operation_id="stk_bydd_trd", query_sha256=failed_hash
        )
        journal.finish(
            ordinal=ordinal,
            provider="KRX",
            operation_id="stk_bydd_trd",
            query_sha256=failed_hash,
            success=False,
            chunk=None,
        )
    recovery = assess_bootstrap_calendar_recovery(
        approved_root=root,
        prior_packet_sha256=legacy.sha256,
    )
    written = write_approved_new_file(
        approved_root=root,
        relative_path=f"bootstrap-{recovery.corrected_packet.sha256}.json",
        content=recovery.corrected_packet.content,
        max_bytes=1 * 1024 * 1024,
    )
    os.chmod(written.absolute_path, 0o600)
    lineage = materialize_recovery_adoption(approved_root=root, recovery=recovery)
    assert lineage["providerCallsDuringAdoption"] == 0
    assert lineage["adoptedSuccessfulChunks"] == 1
    adopted_journal = BootstrapJournal(
        root / f"run-{recovery.corrected_packet.sha256}" / "source"
    )
    adopted_chunk = adopted_journal.completed_chunk(reusable_query_hash)
    assert adopted_chunk is not None
    assert adopted_chunk.temporal.policy_effective_at == datetime(
        2026, 6, 4, 8, 10, tzinfo=ZoneInfo("Asia/Seoul")
    )
    monkeypatch.setenv("S5_SOURCE_ROOT", str(root))
    monkeypatch.setenv(
        "S5_BOOTSTRAP_PACKET_SHA256", recovery.corrected_packet.sha256
    )

    def forbidden_client(*args: object, **kwargs: object) -> None:
        raise AssertionError("provider client must not be created")

    monkeypatch.setattr(bootstrap_execute_cli, "KrxOpenApiClient", forbidden_client)
    monkeypatch.setattr(bootstrap_execute_cli, "KISHttpClient", forbidden_client)
    monkeypatch.setattr(bootstrap_execute_cli, "ECOSHttpClient", forbidden_client)
    assert bootstrap_execute_cli.main() == 2
    assert capsys.readouterr().out == "S5_BOOTSTRAP=RESEARCH_ONLY\n"

    receipt = write_approved_new_file(
        approved_root=root,
        relative_path=(
            "calendar-recovery-binding-"
            f"{recovery.recovery_binding_sha256}.json"
        ),
        content=recovery.content,
        max_bytes=64 * 1024,
    )
    os.chmod(receipt.absolute_path, 0o600)
    assert (
        validate_recovery_execution_authority(
            approved_root=root,
            packet=recovery.corrected_packet,
        )
        == "READY_TO_SUPERSEDE"
    )
    # Allowance를 위조해 상한을 넓히려 하면 packet 재생성 자체가 거부된다.
    forged = dict(json.loads(recovery.content))
    forged["krxSupersededAllowance"] = 8
    with pytest.raises(LightGbmContractError):
        validate_recovery_receipt(
            canonical_json_bytes(forged),
            corrected_packet=recovery.corrected_packet,
        )


def test_publishable_bootstrap_cutoff_honors_holiday_chain_and_exact_clock() -> None:
    before_open = datetime(2026, 8, 17, 23, 9, 59, tzinfo=UTC)
    at_open = datetime(2026, 8, 17, 23, 10, tzinfo=UTC)

    assert latest_publishable_bootstrap_cutoff(cutoff=before_open) == datetime(
        2026, 8, 13, 23, 10, tzinfo=UTC
    )
    assert latest_publishable_bootstrap_cutoff(cutoff=at_open) == datetime(
        2026, 8, 17, 23, 10, tzinfo=UTC
    )
    with pytest.raises(LightGbmContractError, match="timezone aware"):
        latest_publishable_bootstrap_cutoff(cutoff=datetime(2026, 8, 17, 23, 10))
    with pytest.raises(LightGbmContractError, match="calendar bounds"):
        latest_publishable_bootstrap_cutoff(
            cutoff=datetime(1990, 1, 1, tzinfo=UTC)
        )


def test_bootstrap_packet_cli_uses_latest_publishable_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "s5-source"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("S5_SOURCE_ROOT", str(root))

    class FixedDatetime:
        @staticmethod
        def now(tz: object = None) -> datetime:  # noqa: ANN401
            return datetime(2026, 8, 17, 3, 0, tzinfo=UTC if tz is not None else None)

    cutoff = datetime(2026, 8, 17, 3, 0, tzinfo=UTC)
    assert latest_publishable_bootstrap_cutoff(cutoff=cutoff) == datetime(
        2026, 8, 13, 23, 10, tzinfo=UTC
    )
    monkeypatch.setattr(bootstrap_packet_cli, "datetime", FixedDatetime)

    assert bootstrap_packet_cli.main() == 0
    output = capsys.readouterr().out
    assert output.startswith("S5_BOOTSTRAP_PACKET=AUTHORED sha256=")
    packet_sha = output.rsplit("sha256=", 1)[1].strip()
    packet_path = root / f"bootstrap-{packet_sha}.json"
    packet = validate_bootstrap_packet(packet_path.read_bytes(), expected_sha256=packet_sha)
    assert packet.window.latest_completed == date(2026, 8, 13)
    assert packet.window.raw_sessions[-1] == date(2026, 8, 13)
    assert packet.window.eligible_sessions[-1] == date(2026, 8, 5)
    regenerated = author_bootstrap_packet(
        cutoff=latest_publishable_bootstrap_cutoff(cutoff=cutoff)
    )
    assert regenerated.content == packet.content
    assert regenerated.sha256 == packet.sha256
    selected = read_fresh_bootstrap_authority(approved_root=root)
    assert selected.packet == packet
    assert stat.S_IMODE((root / FRESH_AUTHORITY_FILENAME).stat().st_mode) == 0o600

    class LaterDatetime:
        @staticmethod
        def now(tz: object = None) -> datetime:  # noqa: ANN401
            return datetime(
                2026,
                8,
                17,
                23,
                10,
                tzinfo=UTC if tz is not None else None,
            )

    monkeypatch.setattr(bootstrap_packet_cli, "datetime", LaterDatetime)
    assert bootstrap_packet_cli.main() == 0
    assert capsys.readouterr().out == (
        f"S5_BOOTSTRAP_PACKET=SELECTED sha256={packet_sha}\n"
    )
    assert sorted(root.glob("bootstrap-*.json")) == [packet_path]

    (root / f"run-{packet_sha}").mkdir(mode=0o700)
    assert bootstrap_packet_cli.main() == 1
    assert capsys.readouterr().out == "S5_BOOTSTRAP_PACKET=RECOVERY_REQUIRED\n"


def test_fresh_authority_cas_rejects_another_packet_and_run_before_clients(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "s5-source"
    root.mkdir(mode=0o700)
    selected_packet = author_bootstrap_packet(
        cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC)
    )
    other_packet = author_bootstrap_packet(
        cutoff=datetime(2026, 8, 17, 23, 10, tzinfo=UTC)
    )
    root_lock = acquire_bootstrap_root_lock(root)
    try:
        selected = publish_fresh_bootstrap_authority(
            approved_root=root,
            packet=selected_packet,
        )
        assert selected.packet.sha256 == selected_packet.sha256
        with pytest.raises(LightGbmContractError, match="another packet"):
            publish_fresh_bootstrap_authority(
                approved_root=root,
                packet=other_packet,
            )
    finally:
        release_run_lock(root_lock)

    other_file = write_approved_new_file(
        approved_root=root,
        relative_path=f"bootstrap-{other_packet.sha256}.json",
        content=other_packet.content,
        max_bytes=1 * 1024 * 1024,
    )
    os.chmod(other_file.absolute_path, 0o600)
    monkeypatch.setenv("S5_SOURCE_ROOT", str(root))
    monkeypatch.setenv("S5_BOOTSTRAP_PACKET_SHA256", other_packet.sha256)

    def forbidden_client(*args: object, **kwargs: object) -> None:
        raise AssertionError("provider client must not be created")

    monkeypatch.setattr(bootstrap_execute_cli, "KrxOpenApiClient", forbidden_client)
    monkeypatch.setattr(bootstrap_execute_cli, "KISHttpClient", forbidden_client)
    monkeypatch.setattr(bootstrap_execute_cli, "ECOSHttpClient", forbidden_client)
    assert bootstrap_execute_cli.main() == 2
    assert capsys.readouterr().out == "S5_BOOTSTRAP=RESEARCH_ONLY\n"
    assert not (root / f"run-{other_packet.sha256}").exists()


def test_active_root_lock_rejects_selected_execution_before_clients(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "s5-source"
    root.mkdir(mode=0o700)
    packet = author_bootstrap_packet(
        cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC)
    )
    root_lock = acquire_bootstrap_root_lock(root)
    try:
        publish_fresh_bootstrap_authority(approved_root=root, packet=packet)
    finally:
        release_run_lock(root_lock)
    monkeypatch.setenv("S5_SOURCE_ROOT", str(root))
    monkeypatch.setenv("S5_BOOTSTRAP_PACKET_SHA256", packet.sha256)

    def forbidden_client(*args: object, **kwargs: object) -> None:
        raise AssertionError("provider client must not be created")

    monkeypatch.setattr(bootstrap_execute_cli, "KrxOpenApiClient", forbidden_client)
    monkeypatch.setattr(bootstrap_execute_cli, "KISHttpClient", forbidden_client)
    monkeypatch.setattr(bootstrap_execute_cli, "ECOSHttpClient", forbidden_client)
    held_root_lock = acquire_bootstrap_root_lock(root)
    try:
        assert bootstrap_execute_cli.main() == 2
    finally:
        release_run_lock(held_root_lock)
    assert capsys.readouterr().out == "S5_BOOTSTRAP=RESEARCH_ONLY\n"
    assert not (root / f"run-{packet.sha256}").exists()


def test_bootstrap_packet_cli_reports_unavailable_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "s5-source"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("S5_SOURCE_ROOT", str(root))

    def unavailable(*, cutoff: datetime) -> None:
        raise LightGbmContractError("bootstrap cutoff is unavailable")

    monkeypatch.setattr(
        bootstrap_packet_cli,
        "latest_publishable_bootstrap_cutoff",
        unavailable,
    )

    assert bootstrap_packet_cli.main() == 1
    assert capsys.readouterr().out == "S5_BOOTSTRAP_PACKET=DATASET_UNAVAILABLE\n"


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
        budget=BootstrapBudget(krx_get=243, kis_get=31, kis_token=1, ecos_get=4),
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
    assert result.budgeted_calls == 279
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
    assert calls[-4:] == ["ECOS:page"] * 4
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
        budgeted_calls=0,
    )
    bundle = materialize_production_feature_bundle(
        packet=packet,
        acquisition=acquisition,
        feature_root=tmp_path / "feature",
    )
    assert bundle.artifact.table.num_rows == 1_007
    assert bundle.provenance.temporal_quality == "RECONSTRUCTED_FIXED_LAG"
    assert stat.S_IMODE(os.stat(tmp_path / "feature" / "manifest.json").st_mode) == 0o600
def test_fresh_bootstrap_lineage_can_never_carry_superseded_allowance() -> None:
    fresh = author_bootstrap_packet(cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC))
    assert fresh.budget.krx_superseded_allowance == 0
    assert fresh.budget.krx_get == 4_441
    assert fresh.budget.total == 7_436
    assert b"krxSupersededAllowance" in fresh.content

    # Fresh 유도식은 allowance 인자를 아예 받지 않는다.
    with pytest.raises(TypeError):
        author_bootstrap_budget(  # type: ignore[call-arg]
            monthly_schedule_count=51,
            union_size=180,
            superseded_allowance=2,
        )
    with pytest.raises(LightGbmContractError, match="calendar recovery lineage"):
        _author_bootstrap_packet(
            cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC),
            calendar=corrected_calendar(),
            packet_version="s5-production-bootstrap-packet-v2",
            lineage_mode="FRESH",
            superseded_allowance=1,
        )
    # 승인 상한을 allowance 없이 넘기면 budget 자체가 거부된다.
    with pytest.raises(LightGbmContractError, match="approved provider budget exceeded"):
        BootstrapBudget(krx_get=4_442, kis_get=APPROVED_KIS_MAX_GET, kis_token=1, ecos_get=24)
    with pytest.raises(LightGbmContractError, match="superseded allowance exceeds"):
        BootstrapBudget(
            krx_get=4_441,
            kis_get=APPROVED_KIS_MAX_GET,
            kis_token=1,
            ecos_get=24,
            krx_superseded_allowance=MAX_KRX_SUPERSEDED_ALLOWANCE + 1,
        )
    with pytest.raises(LightGbmContractError, match="superseded allowance is invalid"):
        author_recovery_bootstrap_budget(
            monthly_schedule_count=51,
            union_size=180,
            superseded_allowance=MAX_KRX_SUPERSEDED_ALLOWANCE + 1,
        )
    # 과거 v1 packet bytes는 allowance 필드 도입 뒤에도 동결 상태를 유지한다.
    legacy = _author_bootstrap_packet(
        cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC),
        calendar=base_calendar(),
        packet_version="s5-production-bootstrap-packet-v1",
    )
    assert b"krxSupersededAllowance" not in legacy.content
    assert (
        validate_bootstrap_packet(
            legacy.content,
            expected_sha256=legacy.sha256,
            allow_historical_v1=True,
        )
        == legacy
    )


def test_journal_bounds_superseded_attempts_and_blocks_when_allowance_is_short(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "s5-source"
    root.mkdir(mode=0o700)
    legacy = _author_bootstrap_packet(
        cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC),
        calendar=base_calendar(),
        packet_version="s5-production-bootstrap-packet-v1",
    )
    prior = write_approved_new_file(
        approved_root=root,
        relative_path=f"bootstrap-{legacy.sha256}.json",
        content=legacy.content,
        max_bytes=1 * 1024 * 1024,
    )
    os.chmod(prior.absolute_path, 0o600)
    source_root = root / f"run-{legacy.sha256}" / "source"
    source_root.parent.mkdir(mode=0o700)
    source_root.mkdir(mode=0o700)
    (source_root / "chunks").mkdir(mode=0o700)
    failed_hash = provider_query_sha256(
        {"service": "stk_bydd_trd", "basDd": "20260603"}
    )
    journal = BootstrapJournal(source_root, policy_corrections=())
    for _ in range(2):
        ordinal = journal.begin(
            provider="KRX", operation_id="stk_bydd_trd", query_sha256=failed_hash
        )
        journal.finish(
            ordinal=ordinal,
            provider="KRX",
            operation_id="stk_bydd_trd",
            query_sha256=failed_hash,
            success=False,
            chunk=None,
        )
    # 소비 가능한 물리 시도 자체가 query당 2회로 묶여 있어 allowance가 무한히 커질 수 없다.
    with pytest.raises(LightGbmContractError, match="resume attempt is unavailable"):
        journal.begin(
            provider="KRX", operation_id="stk_bydd_trd", query_sha256=failed_hash
        )

    # 승인 bound가 증명된 superseded 수보다 작으면 allowance를 주지 않고 fail-closed 한다.
    monkeypatch.setattr(
        bootstrap_calendar_recovery, "MAX_KRX_SUPERSEDED_ALLOWANCE", 1
    )
    recovery = assess_bootstrap_calendar_recovery(
        approved_root=root,
        prior_packet_sha256=legacy.sha256,
    )
    assert recovery.status == "CAPACITY_EXHAUSTED"
    assert len(recovery.superseded_attempts) == 2
    assert recovery.corrected_packet.budget.krx_superseded_allowance == 0
    assert recovery.corrected_packet.budget.krx_get == 4_441
    assert recovery.krx_shortfall == 2
    assert json.loads(recovery.content)["providerCallsDuringRecovery"] == 0

    written = write_approved_new_file(
        approved_root=root,
        relative_path=f"bootstrap-{recovery.corrected_packet.sha256}.json",
        content=recovery.corrected_packet.content,
        max_bytes=1 * 1024 * 1024,
    )
    os.chmod(written.absolute_path, 0o600)
    receipt = write_approved_new_file(
        approved_root=root,
        relative_path=(
            f"calendar-recovery-binding-{recovery.recovery_binding_sha256}.json"
        ),
        content=recovery.content,
        max_bytes=64 * 1024,
    )
    os.chmod(receipt.absolute_path, 0o600)
    materialize_recovery_adoption(approved_root=root, recovery=recovery)
    monkeypatch.setenv("S5_SOURCE_ROOT", str(root))
    monkeypatch.setenv("S5_BOOTSTRAP_PACKET_SHA256", recovery.corrected_packet.sha256)

    def forbidden_client(*args: object, **kwargs: object) -> None:
        raise AssertionError("provider client must not be created")

    monkeypatch.setattr(bootstrap_execute_cli, "KrxOpenApiClient", forbidden_client)
    monkeypatch.setattr(bootstrap_execute_cli, "KISHttpClient", forbidden_client)
    monkeypatch.setattr(bootstrap_execute_cli, "ECOSHttpClient", forbidden_client)
    assert bootstrap_execute_cli.main() == 2
    assert capsys.readouterr().out == "S5_BOOTSTRAP=RESEARCH_ONLY\n"


def test_empty_daily_projection_becomes_calendar_divergence_and_stops_further_calls(
    tmp_path: Path,
) -> None:
    original = author_bootstrap_packet(cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC))
    raw = original.window.raw_sessions[-4:]
    packet = replace(
        original,
        window=PitSessionWindow(
            cutoff=original.window.cutoff,
            latest_completed=raw[-1],
            raw_sessions=raw,
            eligible_sessions=raw[-1:],
        ),
        schedules=(original.schedules[-1],),
        budget=BootstrapBudget(krx_get=19, kis_get=31, kis_token=1, ecos_get=3),
    )
    blocked_session = raw[2]
    calls: list[str] = []

    class Krx:
        def fetch(
            self, *, service: str, session_date: date
        ) -> tuple[dict[str, str], ...]:
            calls.append(f"{service}:{session_date.isoformat()}")
            if session_date == blocked_session and service == "stk_bydd_trd":
                # 휴장일에 provider가 구조적으로 유효한 빈 응답을 돌려주는 실제 경로다.
                return ()
            return (
                {
                    field: ("1" if field != "BAS_DD" else session_date.strftime("%Y%m%d"))
                    for field in S5_PRODUCTION_PROJECTION_FIELDS[service]
                },
            )

    class Forbidden:
        def __getattr__(self, name: str) -> object:
            raise AssertionError("KRX 이후 provider는 열리지 않아야 한다")

    source_root = tmp_path / "source"
    with pytest.raises(CalendarDivergenceSuspected):
        execute_bootstrap_acquisition(
            packet=packet,
            source_root=source_root,
            krx=Krx(),
            kis=Forbidden(),
            ecos=Forbidden(),
            ecos_series=CANDIDATE_SERIES,
            clock=lambda: datetime(2026, 8, 20, tzinfo=UTC),
        )
    block = json.loads(
        (source_root / DIVERGENCE_CANDIDATES_FILENAME).read_bytes()
    )
    assert block["candidates"] == [
        {
            "evidence": "EMPTY_DAILY_PROJECTION",
            "operationId": "stk_bydd_trd",
            "provider": "KRX",
            "sessionDate": blocked_session.isoformat(),
        }
    ]
    assert block["providerCallsDuringBlock"] == 0
    assert block["calendarCorrectionSetSha256"] == S5_CALENDAR_CORRECTION_SET_SHA256
    # 후보가 발견된 세션 이후의 KRX query는 열리지 않는다.
    assert calls[-1] == f"stk_bydd_trd:{blocked_session.isoformat()}"
    consumed = tuple(calls)

    # 해소되지 않은 block이 남아 있으면 재개 자체가 provider 앞에서 멈춘다.
    with pytest.raises(CalendarDivergenceSuspected, match="unresolved"):
        execute_bootstrap_acquisition(
            packet=packet,
            source_root=source_root,
            krx=Krx(),
            kis=Forbidden(),
            ecos=Forbidden(),
            ecos_series=CANDIDATE_SERIES,
            clock=lambda: datetime(2026, 8, 20, tzinfo=UTC),
            resume=True,
        )
    assert tuple(calls) == consumed
def test_superseded_generation_residue_never_opens_a_second_fresh_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """실제 복구 root에는 이전 세대 packet과 block sidecar가 증거로 남는다.

    그 잔여물이 있어도 authoring은 새 FRESH packet을 만들지 않고 recovery 경로를 요구해야 한다.
    """

    root = tmp_path / "s5-source"
    root.mkdir(mode=0o700)
    stale = author_bootstrap_packet(cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC))
    for relative, content in (
        (f"bootstrap-{stale.sha256}.json", stale.content),
        (f"blocked-bootstrap-{stale.sha256}.json", b"{}"),
        (f"calendar-recovery-{'0' * 64}.json", b"{}"),
    ):
        written = write_approved_new_file(
            approved_root=root,
            relative_path=relative,
            content=content,
            max_bytes=1 * 1024 * 1024,
        )
        os.chmod(written.absolute_path, 0o600)
    (root / f"run-{stale.sha256}").mkdir(mode=0o700)

    monkeypatch.setenv("S5_SOURCE_ROOT", str(root))
    assert bootstrap_packet_cli.main() == 1
    assert capsys.readouterr().out == "S5_BOOTSTRAP_PACKET=RECOVERY_REQUIRED\n"
    assert not fresh_bootstrap_authority_exists(approved_root=root)
    assert not any(
        name.startswith("fresh-bootstrap-authority") for name in os.listdir(root)
    )
def test_corrected_calendar_fixture_matches_the_live_calendar() -> None:
    """계약 fixture의 session 경계가 실제 correction-set 달력과 어긋나지 않게 고정한다.

    generator는 app.lightgbm을 import할 수 없는 root 환경에서 돌아 경계를 리터럴로 둔다. 그
    리터럴이 조용히 낡는 것을 막는 유일한 지점이 여기다.
    """

    fixture = json.loads(
        (
            Path(__file__).resolve().parents[5]
            / "contracts/examples/s5-feature-bundle-v2.corrected-calendar.valid.json"
        ).read_text(encoding="utf-8")
    )
    provenance = fixture["provenance"]
    cutoff = datetime.fromisoformat(provenance["datasetCutoff"].replace("Z", "+00:00"))
    window = build_pit_session_window(cutoff)

    assert provenance["rawSessionStart"] == window.raw_sessions[0].isoformat()
    assert provenance["rawSessionEnd"] == window.raw_sessions[-1].isoformat()
    assert provenance["rawSessionCount"] == len(window.raw_sessions)
    assert provenance["eligibleSessionStart"] == window.eligible_sessions[0].isoformat()
    assert provenance["eligibleSessionEnd"] == window.eligible_sessions[-1].isoformat()
    assert provenance["eligibleSessionCount"] == len(window.eligible_sessions)
    for closed in S5_ADHOC_CLOSED_SESSIONS:
        assert closed not in window.raw_sessions
def test_single_session_query_failure_records_a_divergence_candidate(tmp_path: Path) -> None:
    """앞선 session이 정상인데 한 session만 실패하면 후보 증거를 남기고 resume은 허용한다.

    2026-07-17 제헌절은 provider가 빈 projection이 아니라 오류로 응답해 일반 실패로 분류됐고,
    그 때문에 진단에 KRX 예산이 한 번 더 들었다. 이 회귀가 그 경로를 닫는다.
    """

    original = author_bootstrap_packet(cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC))
    raw = original.window.raw_sessions[-4:]
    packet = replace(
        original,
        window=PitSessionWindow(
            cutoff=original.window.cutoff,
            latest_completed=raw[-1],
            raw_sessions=raw,
            eligible_sessions=raw[-1:],
        ),
        schedules=(original.schedules[-1],),
        budget=BootstrapBudget(krx_get=19, kis_get=31, kis_token=1, ecos_get=3),
    )
    blocked_session = raw[2]
    calls: list[str] = []

    class Krx:
        def fetch(
            self, *, service: str, session_date: date
        ) -> tuple[dict[str, str], ...]:
            calls.append(f"{service}:{session_date.isoformat()}")
            if session_date == blocked_session and service == "stk_bydd_trd":
                raise RuntimeError("provider rejected the request")
            return (
                {
                    field: ("1" if field != "BAS_DD" else session_date.strftime("%Y%m%d"))
                    for field in S5_PRODUCTION_PROJECTION_FIELDS[service]
                },
            )

    class Forbidden:
        def __getattr__(self, name: str) -> object:
            raise AssertionError("KRX 이후 provider는 열리지 않아야 한다")

    source_root = tmp_path / "source"
    with pytest.raises(RuntimeError, match="provider rejected"):
        execute_bootstrap_acquisition(
            packet=packet,
            source_root=source_root,
            krx=Krx(),
            kis=Forbidden(),
            ecos=Forbidden(),
            ecos_series=CANDIDATE_SERIES,
            clock=lambda: datetime(2026, 8, 20, tzinfo=UTC),
        )

    block = json.loads((source_root / DIVERGENCE_CANDIDATES_FILENAME).read_bytes())
    assert block["candidates"] == [
        {
            "evidence": "SINGLE_SESSION_QUERY_FAILURE",
            "operationId": "stk_bydd_trd",
            "provider": "KRX",
            "sessionDate": blocked_session.isoformat(),
        }
    ]
    assert block["providerCallsDuringBlock"] == 0

    # 단일 실패 후보는 진단 증거일 뿐이므로 계약이 허용한 resume을 막지 않는다.
    consumed = tuple(calls)
    with pytest.raises(RuntimeError, match="provider rejected"):
        execute_bootstrap_acquisition(
            packet=packet,
            source_root=source_root,
            krx=Krx(),
            kis=Forbidden(),
            ecos=Forbidden(),
            ecos_series=CANDIDATE_SERIES,
            clock=lambda: datetime(2026, 8, 20, tzinfo=UTC),
            resume=True,
        )
    assert len(calls) > len(consumed)
def test_correction_generation_authority_is_closed() -> None:
    """승인된 correction 세대만 해시로 되돌릴 수 있어야 한다."""

    current = correction_set_sha256(S5_ADHOC_CLOSED_SESSIONS)
    assert current == S5_CALENDAR_CORRECTION_SET_SHA256
    assert corrections_for_sha256(current) == S5_ADHOC_CLOSED_SESSIONS

    # 이전 세대는 read-only 검증용으로 보존되며 삭제되지 않는다.
    assert S5_SUPERSEDED_CORRECTION_SETS[0] == ()
    for generation in S5_SUPERSEDED_CORRECTION_SETS:
        digest = correction_set_sha256(generation)
        assert corrections_for_sha256(digest) == tuple(generation)
        assert digest != current

    with pytest.raises(LightGbmContractError, match="generation is not approved"):
        corrections_for_sha256("f" * 64)

    # 세대별 달력은 그 세대의 correction만 닫는다.
    first = calendar_for_corrections(S5_SUPERSEDED_CORRECTION_SETS[1])
    assert not first.is_session("2026-06-03")
    assert first.is_session("2026-07-17")
    assert not corrected_calendar().is_session("2026-07-17")


def test_superseded_generation_packet_is_read_only_for_recovery() -> None:
    """이전 세대 packet은 production 실행에서 거부되고 recovery 검증에서만 열린다."""

    superseded = _author_bootstrap_packet(
        cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC),
        calendar=calendar_for_corrections(S5_SUPERSEDED_CORRECTION_SETS[1]),
        packet_version="s5-production-bootstrap-packet-v2",
        lineage_mode="CALENDAR_RECOVERY",
        recovery_binding_sha256="b" * 64,
        superseded_allowance=2,
        corrections=S5_SUPERSEDED_CORRECTION_SETS[1],
    )
    assert superseded.calendar_correction_set_sha256 == correction_set_sha256(
        S5_SUPERSEDED_CORRECTION_SETS[1]
    )
    assert superseded.calendar_correction_set_sha256 != S5_CALENDAR_CORRECTION_SET_SHA256

    with pytest.raises(LightGbmContractError, match="current calendar policy"):
        validate_bootstrap_packet(
            superseded.content, expected_sha256=superseded.sha256
        )
    assert (
        validate_bootstrap_packet(
            superseded.content,
            expected_sha256=superseded.sha256,
            allow_superseded_corrections=True,
        )
        == superseded
    )

    # 현재 세대 packet은 flag 없이도 그대로 검증된다.
    current = author_bootstrap_packet(cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC))
    assert (
        validate_bootstrap_packet(current.content, expected_sha256=current.sha256)
        == current
    )


def test_recovery_refuses_a_fresh_packet_as_prior(tmp_path: Path) -> None:
    """fresh packet은 prior가 될 수 없다.

    prior는 수정 전 historical v1이거나 이미 소비된 recovery packet뿐이다. fresh packet을 prior로
    받으면 아직 소비되지 않은 예산을 supersede 대상으로 취급하게 된다.
    """

    root = tmp_path / "s5-source"
    root.mkdir(mode=0o700)
    current = author_bootstrap_packet(cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC))
    written = write_approved_new_file(
        approved_root=root,
        relative_path=f"bootstrap-{current.sha256}.json",
        content=current.content,
        max_bytes=1 * 1024 * 1024,
    )
    os.chmod(written.absolute_path, 0o600)
    source_root = root / f"run-{current.sha256}" / "source"
    source_root.parent.mkdir(mode=0o700)
    source_root.mkdir(mode=0o700)
    (source_root / "chunks").mkdir(mode=0o700)

    query_hash = provider_query_sha256(
        {"service": "stk_bydd_trd", "basDd": "20260603"}
    )
    journal = BootstrapJournal(source_root)
    ordinal = journal.begin(
        provider="KRX", operation_id="stk_bydd_trd", query_sha256=query_hash
    )
    journal.finish(
        ordinal=ordinal,
        provider="KRX",
        operation_id="stk_bydd_trd",
        query_sha256=query_hash,
        success=False,
        chunk=None,
    )

    with pytest.raises(LightGbmContractError, match="prior lineage is invalid"):
        assess_bootstrap_calendar_recovery(
            approved_root=root, prior_packet_sha256=current.sha256
        )

def test_runtime_inputs_derive_execution_target_from_sealed_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실행 대상 packet과 provenance를 사람이 옮겨 적지 않고 봉인된 증거에서 유도한다."""

    monkeypatch.delenv("S5_BOOTSTRAP_PACKET_SHA256", raising=False)
    root = tmp_path / "s5-source"
    root.mkdir(mode=0o700)

    # fresh authority가 있으면 그 packet이 유일한 실행 대상이다.
    packet = author_bootstrap_packet(cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC))
    publish_fresh_bootstrap_authority(approved_root=root, packet=packet)
    assert resolve_bootstrap_packet_sha256(approved_root=root) == packet.sha256

    # 명시값이 있으면 그대로 쓰되 형식은 강제한다.
    monkeypatch.setenv("S5_BOOTSTRAP_PACKET_SHA256", "a" * 64)
    assert resolve_bootstrap_packet_sha256(approved_root=root) == "a" * 64
    monkeypatch.setenv("S5_BOOTSTRAP_PACKET_SHA256", "not-a-digest")
    with pytest.raises(LightGbmContractError, match="digest is invalid"):
        resolve_bootstrap_packet_sha256(approved_root=root)
    monkeypatch.delenv("S5_BOOTSTRAP_PACKET_SHA256", raising=False)

    # 후보가 없는 root에서는 값을 지어내지 않는다.
    empty = tmp_path / "empty-root"
    empty.mkdir(mode=0o700)
    with pytest.raises(LightGbmContractError, match="head is unavailable"):
        resolve_bootstrap_packet_sha256(approved_root=empty)


def test_code_provenance_rejects_values_that_do_not_match_the_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """형식만 맞는 임의 provenance가 release manifest에 봉인되는 경로를 닫는다."""

    for name in ("S5_CODE_HEAD_SHA", "S5_CODE_TREE_SHA", "S5_UV_LOCK_SHA256"):
        monkeypatch.delenv(name, raising=False)
    repository_root = resolve_repository_root()
    head, tree, lock = resolve_code_provenance(repository_root=repository_root)
    assert len(head) == 40 and len(tree) == 40 and len(lock) == 64

    # 유도값과 같으면 통과한다.
    monkeypatch.setenv("S5_CODE_HEAD_SHA", head)
    monkeypatch.setenv("S5_CODE_TREE_SHA", tree)
    monkeypatch.setenv("S5_UV_LOCK_SHA256", lock)
    assert resolve_code_provenance(repository_root=repository_root) == (head, tree, lock)

    # 형식은 맞지만 저장소 상태와 다른 값은 거부한다.
    monkeypatch.setenv("S5_CODE_HEAD_SHA", "0" * 40)
    with pytest.raises(LightGbmContractError, match="does not match the repository"):
        resolve_code_provenance(repository_root=repository_root)
def test_recovery_receipt_carries_every_superseded_query_identity(tmp_path: Path) -> None:
    """receipt는 실패 query 하나가 아니라 superseded query 집합 전체를 담아야 한다.

    체인이 길어지면 superseded 항목은 직전 세대보다 앞선 세대의 query일 수 있다. 단수 필드로는
    그 항목을 대조할 수 없어 실행 권위가 정당한 체인을 거부했다.
    """

    root = tmp_path / "s5-source"
    root.mkdir(mode=0o700)
    legacy = _author_bootstrap_packet(
        cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC),
        calendar=base_calendar(),
        packet_version="s5-production-bootstrap-packet-v1",
    )
    written = write_approved_new_file(
        approved_root=root,
        relative_path=f"bootstrap-{legacy.sha256}.json",
        content=legacy.content,
        max_bytes=1 * 1024 * 1024,
    )
    os.chmod(written.absolute_path, 0o600)
    source_root = root / f"run-{legacy.sha256}" / "source"
    source_root.parent.mkdir(mode=0o700)
    source_root.mkdir(mode=0o700)
    (source_root / "chunks").mkdir(mode=0o700)

    failed_hash = provider_query_sha256(
        {"service": "stk_bydd_trd", "basDd": "20260603"}
    )
    journal = BootstrapJournal(source_root, policy_corrections=())
    for _ in range(2):
        ordinal = journal.begin(
            provider="KRX", operation_id="stk_bydd_trd", query_sha256=failed_hash
        )
        journal.finish(
            ordinal=ordinal,
            provider="KRX",
            operation_id="stk_bydd_trd",
            query_sha256=failed_hash,
            success=False,
            chunk=None,
        )

    recovery = assess_bootstrap_calendar_recovery(
        approved_root=root, prior_packet_sha256=legacy.sha256
    )
    receipt = validate_recovery_receipt(
        recovery.content, corrected_packet=recovery.corrected_packet
    )
    identities = receipt["supersededQueries"]
    assert isinstance(identities, list) and identities
    assert all(
        set(item) == {"operationId", "querySha256", "sessionDate"} for item in identities
    )
    # 집합은 query 해시 순으로 정렬되고 중복이 없어야 결정적이다.
    digests = [str(item["querySha256"]) for item in identities]
    assert digests == sorted(digests)
    assert len(set(digests)) == len(digests)
    # 새로 실패한 query는 반드시 집합에 포함된다.
    assert str(receipt["failedQuerySha256"]) in digests
    assert {str(item["sessionDate"]) for item in identities} <= {
        day.isoformat() for day in S5_ADHOC_CLOSED_SESSIONS
    }

    # 집합을 위조하면 binding preimage 재계산에서 거부된다.
    forged = dict(json.loads(recovery.content))
    forged["supersededQueries"] = [
        {"operationId": "stk_bydd_trd", "querySha256": "0" * 64, "sessionDate": "2026-06-03"}
    ]
    with pytest.raises(LightGbmContractError):
        validate_recovery_receipt(
            canonical_json_bytes(forged), corrected_packet=recovery.corrected_packet
        )
def test_adopted_prefix_survives_further_provider_records(tmp_path: Path) -> None:
    """채택 이후 append된 물리 호출 기록이 실행 권위를 깨뜨리지 않아야 한다.

    lineage가 journal 전체 digest를 고정하면 실행이 시작된 run은 영구히 재검증 불가가 되고,
    계약이 허용한 resume이 막힌다. 채택 prefix만 대조해 불변성과 append를 함께 지킨다.
    """

    root = tmp_path / "s5-source"
    root.mkdir(mode=0o700)
    legacy = _author_bootstrap_packet(
        cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC),
        calendar=base_calendar(),
        packet_version="s5-production-bootstrap-packet-v1",
    )
    prior = write_approved_new_file(
        approved_root=root,
        relative_path=f"bootstrap-{legacy.sha256}.json",
        content=legacy.content,
        max_bytes=1 * 1024 * 1024,
    )
    os.chmod(prior.absolute_path, 0o600)
    prior_source = root / f"run-{legacy.sha256}" / "source"
    prior_source.parent.mkdir(mode=0o700)
    prior_source.mkdir(mode=0o700)
    (prior_source / "chunks").mkdir(mode=0o700)

    failed_hash = provider_query_sha256(
        {"service": "stk_bydd_trd", "basDd": "20260603"}
    )
    journal = BootstrapJournal(prior_source, policy_corrections=())
    for _ in range(2):
        ordinal = journal.begin(
            provider="KRX", operation_id="stk_bydd_trd", query_sha256=failed_hash
        )
        journal.finish(
            ordinal=ordinal,
            provider="KRX",
            operation_id="stk_bydd_trd",
            query_sha256=failed_hash,
            success=False,
            chunk=None,
        )

    recovery = assess_bootstrap_calendar_recovery(
        approved_root=root, prior_packet_sha256=legacy.sha256
    )
    written = write_approved_new_file(
        approved_root=root,
        relative_path=f"bootstrap-{recovery.corrected_packet.sha256}.json",
        content=recovery.corrected_packet.content,
        max_bytes=1 * 1024 * 1024,
    )
    os.chmod(written.absolute_path, 0o600)
    receipt = write_approved_new_file(
        approved_root=root,
        relative_path=(
            f"calendar-recovery-binding-{recovery.recovery_binding_sha256}.json"
        ),
        content=recovery.content,
        max_bytes=64 * 1024,
    )
    os.chmod(receipt.absolute_path, 0o600)
    materialize_recovery_adoption(approved_root=root, recovery=recovery)

    adopted_source = root / f"run-{recovery.corrected_packet.sha256}" / "source"
    assert (
        validate_recovery_execution_authority(
            approved_root=root, packet=recovery.corrected_packet
        )
        == "READY_TO_SUPERSEDE"
    )

    # 채택 이후 실행이 새 물리 호출을 기록해도 권위는 유지된다.
    adopted_journal = BootstrapJournal(adopted_source)
    token_hash = provider_query_sha256({"operation": "oauth2/tokenP"})
    ordinal = adopted_journal.begin(
        provider="KIS", operation_id="oauth2/tokenP", query_sha256=token_hash
    )
    adopted_journal.finish(
        ordinal=ordinal,
        provider="KIS",
        operation_id="oauth2/tokenP",
        query_sha256=token_hash,
        success=True,
        chunk=None,
    )
    assert (
        validate_recovery_execution_authority(
            approved_root=root, packet=recovery.corrected_packet
        )
        == "READY_TO_SUPERSEDE"
    )

    # 채택 구간 자체가 훼손되면 여전히 거부한다.
    raw = (adopted_source / "progress.jsonl").read_bytes().splitlines(keepends=True)
    tampered = b"".join(raw[1:])
    (adopted_source / "progress.jsonl").write_bytes(tampered)
    with pytest.raises(LightGbmContractError):
        validate_recovery_execution_authority(
            approved_root=root, packet=recovery.corrected_packet
        )
def test_superseded_attempt_does_not_consume_this_generation_retry_budget(
    tmp_path: Path,
) -> None:
    """이관된 소비 원장이 새 세대의 재시도 자격을 먹으면 이관 자체가 무의미해진다.

    실제로 KIS 일별시세 query 하나가 이전 세대에서 시도 2회를 모두 소진했다. 파서 결함을 고친 뒤
    세대를 이관했는데도 journal이 그 query를 열어주지 않아 packet이 완주 불가였다. 누적 예산은
    ledger가 SUPERSEDED_CONSUMED까지 세므로 상한은 그대로 지켜진다.
    """

    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    query = "7" * 64
    journal = BootstrapJournal(root)
    for _ in range(2):
        ordinal = journal.begin(
            provider="KIS", operation_id="FHKST03010100", query_sha256=query
        )
        journal.finish(
            ordinal=ordinal,
            provider="KIS",
            operation_id="FHKST03010100",
            query_sha256=query,
            success=False,
            chunk=None,
        )
    # 같은 세대 안에서는 두 번째 실패로 자격이 닫힌다.
    with pytest.raises(LightGbmContractError, match="resume attempt"):
        BootstrapJournal(root).begin(
            provider="KIS", operation_id="FHKST03010100", query_sha256=query
        )

    # 세대를 이관하면 그 시도들은 SUPERSEDED_CONSUMED 원장이 되고 자격이 열린다.
    carried = tmp_path / "carried"
    carried.mkdir(mode=0o700)
    content = build_recovery_journal_bytes(
        adopted=(),
        superseded=BootstrapJournal(root).attempts,
    )
    written = write_approved_new_file(
        approved_root=carried,
        relative_path=JOURNAL_FILENAME,
        content=content,
        max_bytes=MAX_JOURNAL_BYTES,
    )
    os.chmod(written.absolute_path, 0o600)
    next_generation = BootstrapJournal(carried)
    assert len(next_generation.attempts) == 2
    assert all(
        attempt.state == SUPERSEDED_CONSUMED for attempt in next_generation.attempts
    )
    ordinal = next_generation.begin(
        provider="KIS", operation_id="FHKST03010100", query_sha256=query
    )
    assert ordinal == 3
    next_generation.finish(
        ordinal=ordinal,
        provider="KIS",
        operation_id="FHKST03010100",
        query_sha256=query,
        success=False,
        chunk=None,
    )
    # 새 세대의 자격도 2회로 닫힌다. 이관이 상한을 무한히 열지 않는다.
    reopened = BootstrapJournal(carried)
    second = reopened.begin(
        provider="KIS", operation_id="FHKST03010100", query_sha256=query
    )
    reopened.finish(
        ordinal=second,
        provider="KIS",
        operation_id="FHKST03010100",
        query_sha256=query,
        success=False,
        chunk=None,
    )
    with pytest.raises(LightGbmContractError, match="resume attempt"):
        BootstrapJournal(carried).begin(
            provider="KIS", operation_id="FHKST03010100", query_sha256=query
        )


def test_adopted_success_can_never_be_called_again_across_generations(
    tmp_path: Path,
) -> None:
    """채택된 성공은 결과가 그대로 있으므로 재호출이 승인 호출만 태운다."""

    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    (root / "chunks").mkdir(mode=0o700)
    query = "8" * 64
    journal = BootstrapJournal(root)
    ordinal = journal.begin(
        provider="KIS", operation_id="oauth2/tokenP", query_sha256=query
    )
    journal.finish(
        ordinal=ordinal,
        provider="KIS",
        operation_id="oauth2/tokenP",
        query_sha256=query,
        success=True,
        chunk=None,
    )
    with pytest.raises(LightGbmContractError, match="cannot be called again"):
        BootstrapJournal(root).begin(
            provider="KIS", operation_id="oauth2/tokenP", query_sha256=query
        )


def test_kis_token_success_cannot_be_adopted_because_its_value_is_not_preserved(
    tmp_path: Path,
) -> None:
    """Access token 성공은 값이 남지 않아 재사용할 수 없다. superseded로만 이관된다.

    성공으로 채택하면 새 run은 token 상한을 이미 쓴 상태로 시작해 KIS 호출을 한 건도 못 한다.
    """

    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    journal = BootstrapJournal(root)
    ordinal = journal.begin(
        provider="KIS", operation_id="oauth2/tokenP", query_sha256="9" * 64
    )
    journal.finish(
        ordinal=ordinal,
        provider="KIS",
        operation_id="oauth2/tokenP",
        query_sha256="9" * 64,
        success=True,
        chunk=None,
    )
    attempts = BootstrapJournal(root).attempts
    with pytest.raises(LightGbmContractError, match="adopted bootstrap attempt"):
        build_recovery_journal_bytes(adopted=attempts, superseded=())
    # superseded 경로는 chunk 없는 성공도 소비 원장으로 받아들인다.
    content = build_recovery_journal_bytes(adopted=(), superseded=attempts)
    assert content.count(b"SUPERSEDED_CONSUMED") == 1


def test_fresh_bootstrap_lineage_can_never_carry_kis_allowance() -> None:
    """FRESH 경로는 어떤 provider의 allowance도 가질 수 없다."""

    with pytest.raises(LightGbmContractError, match="calendar recovery lineage"):
        _author_bootstrap_packet(
            cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC),
            calendar=corrected_calendar(),
            packet_version="s5-production-bootstrap-packet-v2",
            lineage_mode="FRESH",
            kis_superseded_allowance=1,
        )
    with pytest.raises(LightGbmContractError, match="calendar recovery lineage"):
        _author_bootstrap_packet(
            cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC),
            calendar=corrected_calendar(),
            packet_version="s5-production-bootstrap-packet-v2",
            lineage_mode="FRESH",
            kis_token_superseded_allowance=1,
        )
    fresh = author_bootstrap_packet(cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC))
    assert fresh.budget.kis_superseded_allowance == 0
    assert fresh.budget.kis_token_superseded_allowance == 0
    assert fresh.budget.kis_get == APPROVED_KIS_MAX_GET
    assert fresh.budget.kis_token == 1


def test_kis_allowance_is_absent_from_packet_bytes_when_it_is_zero() -> None:
    """0인 allowance가 bytes에 나타나면 이미 봉인된 packet이 전부 무효가 된다.

    상한 회계를 provider별로 나눌 때마다 과거 세대를 다시 검증할 수 없게 되는 일을 막는다.
    """

    recovery = author_recovery_bootstrap_packet(
        cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC),
        recovery_binding_sha256="a" * 64,
        superseded_allowance=3,
    )
    limits = json.loads(recovery.content)["limits"]
    assert limits["krxSupersededAllowance"] == 3
    assert "kisSupersededAllowance" not in limits
    assert "kisTokenSupersededAllowance" not in limits
    assert validate_bootstrap_packet(
        recovery.content, expected_sha256=recovery.sha256
    ) == recovery

    widened = author_recovery_bootstrap_packet(
        cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC),
        recovery_binding_sha256="a" * 64,
        superseded_allowance=3,
        kis_superseded_allowance=3,
        kis_token_superseded_allowance=1,
    )
    widened_limits = json.loads(widened.content)["limits"]
    assert widened_limits["kisSupersededAllowance"] == 3
    assert widened_limits["kisTokenSupersededAllowance"] == 1
    assert widened.budget.kis_get == APPROVED_KIS_MAX_GET + 3
    assert widened.budget.kis_token == 2
    assert widened.sha256 != recovery.sha256
    assert validate_bootstrap_packet(
        widened.content, expected_sha256=widened.sha256
    ) == widened


def test_kis_allowance_cannot_exceed_the_approved_bound() -> None:
    """Allowance는 증명된 소비량만 복원하며 무한 확장을 허용하지 않는다."""

    for kwargs in (
        {"kis_superseded_allowance": MAX_KIS_SUPERSEDED_ALLOWANCE + 1},
        {"kis_token_superseded_allowance": MAX_KIS_TOKEN_SUPERSEDED_ALLOWANCE + 1},
    ):
        with pytest.raises(LightGbmContractError, match="allowance is invalid"):
            author_recovery_bootstrap_packet(
                cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC),
                recovery_binding_sha256="a" * 64,
                superseded_allowance=0,
                **kwargs,
            )


def test_recovery_that_changes_nothing_is_refused(tmp_path: Path) -> None:
    """Supersede는 packet 신원을 바꿔야 한다.

    같은 packet을 prior로 삼으면 체인이 자기 자신을 가리켜 head 유도가 무너지고 같은 세대를
    무한히 재발행할 수 있다.
    """

    root = tmp_path / "s5-source"
    root.mkdir(mode=0o700)
    binding = "b" * 64
    packet = author_recovery_bootstrap_packet(
        cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC),
        recovery_binding_sha256=binding,
        superseded_allowance=0,
    )
    written = write_approved_new_file(
        approved_root=root,
        relative_path=f"bootstrap-{packet.sha256}.json",
        content=packet.content,
        max_bytes=1 * 1024 * 1024,
    )
    os.chmod(written.absolute_path, 0o600)
    source_root = root / f"run-{packet.sha256}" / "source"
    source_root.parent.mkdir(mode=0o700)
    source_root.mkdir(mode=0o700)
    (source_root / "chunks").mkdir(mode=0o700)
    journal = BootstrapJournal(source_root)
    ordinal = journal.begin(
        provider="KRX", operation_id="stk_bydd_trd", query_sha256="c" * 64
    )
    journal.finish(
        ordinal=ordinal,
        provider="KRX",
        operation_id="stk_bydd_trd",
        query_sha256="c" * 64,
        success=False,
        chunk=None,
    )
    with pytest.raises(LightGbmContractError):
        assess_bootstrap_calendar_recovery(
            approved_root=root, prior_packet_sha256=packet.sha256
        )
def _kis_symbol_fetch(
    *,
    tmp_path: Path,
    raw_sessions: tuple[date, ...],
    expected_sessions: tuple[date, ...],
    available: tuple[date, ...],
    on_fetch: object = None,
) -> tuple[tuple[ProductionPriceEvidence, ...], tuple[SourceChunkReceipt, ...]]:
    """고정된 응답 집합으로 한 종목의 KIS paging을 돌린다. 네트워크는 없다."""

    tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    source_root = tmp_path / "source"
    source_root.mkdir(mode=0o700)
    (source_root / "chunks").mkdir(mode=0o700)

    class Kis:
        def prepare_access_token(self) -> None:  # pragma: no cover - 호출되지 않는다
            raise AssertionError("token handoff is out of scope")

        def require_cached_token_only(self) -> None:
            return None

        def fetch_page(
            self, *, symbol: str, start: date, end: date
        ) -> tuple[DailyBar, ...]:
            if on_fetch is not None:
                on_fetch(start, end)
            window = [day for day in available if start <= day <= end]
            # provider는 최신 100개만 돌려주고 caller가 cursor를 뒤로 옮긴다.
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
                for day in window[-100:]
            )

    return _fetch_kis_symbol(
        ledger=BootstrapLedger(
            BootstrapBudget(krx_get=0, kis_get=64, kis_token=1, ecos_get=0),
            phase=BootstrapPhase.KIS,
        ),
        source_root=source_root,
        provider=Kis(),
        identity="KR7000001003",
        symbol="000001",
        raw_sessions=raw_sessions,
        expected_sessions=expected_sessions,
        clock=lambda: datetime(2026, 8, 19, tzinfo=UTC),
        journal=BootstrapJournal(source_root),
    )


def test_kis_coverage_is_bound_to_krx_trading_evidence(tmp_path: Path) -> None:
    """union 종목 전원에게 전수 커버리지를 요구하면 상장폐지 종목에서 충족 불가가 된다.

    실측: 010620은 KRX와 KIS 양쪽에서 정확히 910 session(2022-03-29..2025-12-12)이고 그 뒤 거래
    증거가 없다. 두 provider가 정확히 일치하므로 불일치가 아니라 상장폐지다. horizon union은 전
    구간 monthly universe의 합집합이라 초기 유동성으로 선정된 종목이 cutoff 전에 상장폐지될 수
    있고, 그 종목에 1,072 session 전수를 요구하면 수집이 끝까지 갈 수 없다.

    이미 수집한 KRX 일별 projection이 "그 session에 그 종목이 거래됐는가"의 권위다. 느슨하게
    허용하는 것이 아니라 요구 대상을 정확히 그 증거로 바꾼다.
    """

    packet = author_bootstrap_packet(cutoff=datetime(2026, 8, 19, 0, 0, tzinfo=UTC))
    raw = packet.window.raw_sessions[-120:]
    # 마지막 40 session 전에 상장폐지된 종목을 모사한다.
    traded = raw[:-40]

    prices, receipts = _kis_symbol_fetch(
        tmp_path=tmp_path / "delisted",
        raw_sessions=raw,
        expected_sessions=traded,
        available=traded,
    )
    assert tuple(row.session_date for row in prices) == traded
    assert receipts and all(chunk.source_id == "KIS" for chunk in receipts)

    # 증거보다 짧은 역사는 여전히 거부된다. 상장폐지 허용이 잘린 역사 허용은 아니다.
    with pytest.raises(DatasetUnavailable, match="KIS_HISTORY_UNAVAILABLE"):
        _kis_symbol_fetch(
            tmp_path=tmp_path / "truncated",
            raw_sessions=raw,
            expected_sessions=traded,
            available=traded[10:],
        )

    # KRX 증거에 없는 session을 KIS가 주면 provider 불일치로 거부된다.
    with pytest.raises(DatasetUnavailable, match="KIS_HISTORY_UNAVAILABLE"):
        _kis_symbol_fetch(
            tmp_path=tmp_path / "extra",
            raw_sessions=raw,
            expected_sessions=traded[:-1],
            available=traded,
        )


def test_kis_symbol_without_any_krx_trading_evidence_is_refused(tmp_path: Path) -> None:
    """union 소속은 거래 관측에서 나오므로 증거가 0인 종목은 있을 수 없다."""

    packet = author_bootstrap_packet(cutoff=datetime(2026, 8, 19, 0, 0, tzinfo=UTC))
    raw = packet.window.raw_sessions[-10:]
    with pytest.raises(DatasetUnavailable, match="coverage expectation is absent"):
        _kis_symbol_fetch(
            tmp_path=tmp_path / "absent",
            raw_sessions=raw,
            expected_sessions=(),
            available=raw,
        )
def test_kis_coverage_divergence_names_the_symbol_and_survives_resume(
    tmp_path: Path,
) -> None:
    """커버리지 결손이 나면 어떤 종목에서 몇 개가 어긋났는지 실행 하나로 알 수 있어야 한다.

    이전에는 KIS_HISTORY_UNAVAILABLE만 남아 원인을 찾는 데 별도 진단 스크립트가 필요했고, 그
    비용은 발생할 때마다 반복된다. 이제 예외가 단위 신원과 측정값을 직접 들고 나오고 호출자가
    진단 원장에 남긴다. 분류와 신원만 담고 provider 응답은 담지 않는다.

    진단 원장을 추가하면서 source root의 정확한 allowlist에 등록하지 않으면 다음 resume이
    "production bundle root must be empty"로 죽는다. 그 경로도 함께 고정한다.
    """

    packet = author_bootstrap_packet(cutoff=datetime(2026, 8, 19, 0, 0, tzinfo=UTC))
    raw = packet.window.raw_sessions[-120:]
    root = tmp_path / "diverged"

    with pytest.raises(BootstrapEvidenceGap) as caught:
        _kis_symbol_fetch(
            tmp_path=root,
            raw_sessions=raw,
            expected_sessions=raw,
            available=raw[:-40],
        )

    gap = caught.value
    assert gap.unit.provider == "KIS"
    assert gap.unit.label == "000001"
    assert gap.measured["missingSessions"] == 40
    assert gap.measured["extraSessions"] == 0
    assert gap.measured["firstMissingSession"] == raw[-40].isoformat()
    assert gap.measured["lastMissingSession"] == raw[-1].isoformat()

    # 호출자가 원장에 남기는 경로를 그대로 재현한다.
    source_root = root / "source"
    record_diagnostic(
        source_root=source_root,
        phase="COLLECTING_KIS",
        outcome=OutcomeClass.EVIDENCE_GAP,
        unit=gap.unit,
        measured=gap.measured,
    )
    event = read_diagnostics(source_root=source_root)[-1]
    assert event["outcome"] == "EVIDENCE_GAP"
    assert event["unit"]["label"] == "000001"  # type: ignore[index]
    assert event["measured"]["missingSessions"] == 40  # type: ignore[index]

    # 원장이 있어도 같은 run root를 다시 열 수 있어야 한다. allowlist 누락이면 여기서 죽는다.
    bootstrap_executor._prepare_private_bundle_root(
        source_root, chunks=True, resume=True
    )


def test_traded_session_evidence_must_be_contiguous(tmp_path: Path) -> None:
    """rolling window는 종목 자기 행에 대한 위치 기반이라 중간 결손이 의미를 바꾼다.

    수집된 KRX 증거 3,036 종목 전수 측정에서 중간 결손은 0이었다(전 구간 2,301 / 앞부분만 194 /
    뒷부분만 541). 지금 성립하는 성질을 요구로 못 박아, 미래 데이터에서 깨지면 조용히 틀린
    feature를 만드는 대신 fail-closed 한다. 상장/폐지로 끝이 잘리는 것은 그대로 허용한다.
    """

    packet = author_bootstrap_packet(cutoff=datetime(2026, 8, 19, 0, 0, tzinfo=UTC))
    raw = packet.window.raw_sessions[-8:]
    source_root = tmp_path / "source"
    source_root.mkdir(mode=0o700, parents=True)
    (source_root / "chunks").mkdir(mode=0o700)

    def seal(session: date, service: str, codes: tuple[str, ...]) -> SourceChunkReceipt:
        rows = tuple(
            {
                field: ("1" if field != "ISU_CD" else code)
                for field in S5_PRODUCTION_PROJECTION_FIELDS[service]
            }
            for code in codes
        )
        payload = bootstrap_executor._string_rows_parquet(rows)
        return bootstrap_executor._seal_projection(
            source_root=source_root,
            source="KRX",
            operation=service,
            query_key=f"{service}:{session.isoformat()}",
            rows=len(rows),
            payload=payload,
            temporal=bootstrap_executor._temporal_receipt(
                source="KRX",
                operation=service,
                observation_date=session,
                retrieved_at=datetime(2026, 8, 19, tzinfo=UTC),
                request_sha256="0" * 64,
                snapshot_sha256=hashlib.sha256(payload).hexdigest(),
            ),
        )

    def build(present: object) -> dict[
        tuple[str, date], SourceChunkReceipt
    ]:
        chunks: dict[tuple[str, date], SourceChunkReceipt] = {}
        for session in raw:
            chunks[("stk_bydd_trd", session)] = seal(
                session, "stk_bydd_trd", present(session)  # type: ignore[operator]
            )
            chunks[("ksq_bydd_trd", session)] = seal(session, "ksq_bydd_trd", ("999999",))
        return chunks

    window = replace(
        packet.window,
        raw_sessions=raw,
        latest_completed=raw[-1],
        eligible_sessions=raw[-1:],
    )
    scoped = replace(packet, window=window)

    # 끝이 잘린 상장폐지는 허용된다.
    delisted = bootstrap_executor._derive_traded_sessions(
        packet=scoped,
        source_root=source_root,
        chunks=build(
            lambda session: ("000001",) if session <= raw[4] else ("888888",)
        ),
        symbols=frozenset({"000001"}),
    )
    assert delisted["000001"] == tuple(raw[:5])

    # 중간 결손은 거부된다.
    with pytest.raises(DatasetUnavailable, match="not contiguous"):
        bootstrap_executor._derive_traded_sessions(
            packet=scoped,
            source_root=source_root,
            chunks=build(
                lambda session: ("000001",) if session != raw[3] else ("888888",)
            ),
            symbols=frozenset({"000001"}),
        )
def test_paging_stops_when_evidence_is_satisfied_on_a_page_boundary(
    tmp_path: Path,
) -> None:
    """종료 판정이 응답 모양에만 의존하면 100의 배수 역사에서 여분 호출을 태운다.

    실측: 419530은 KRX 증거 900 session(2022-12-06 상장)이고 KIS도 900 session을 9페이지로 이미
    다 받았다(결손 0, 초과 0). 그런데 9페이지가 정확히 100행이라 start 도달도 100행 미만도
    성립하지 않아 상장 전 구간을 한 번 더 요청했고, 0행 응답이 하드 실패가 되면서 승인 호출 2건을
    태우고 packet을 완주 불가로 만들었다.

    커버리지 권위는 이미 KRX 거래 증거다. 그 증거를 다 받았으면 더 요청할 것이 없다.
    """

    packet = author_bootstrap_packet(cutoff=datetime(2026, 8, 19, 0, 0, tzinfo=UTC))
    raw = packet.window.raw_sessions[-300:]
    # 역사가 정확히 페이지 경계에서 끝나고 start에는 닿지 않는 신규상장 종목이다.
    traded = raw[-200:]
    assert len(traded) % 100 == 0 and traded[0] != raw[0]

    calls: list[tuple[date, date]] = []

    def record(start: date, end: date) -> None:
        calls.append((start, end))

    prices, receipts = _kis_symbol_fetch(
        tmp_path=tmp_path / "boundary",
        raw_sessions=raw,
        expected_sessions=traded,
        available=traded,
        on_fetch=record,
    )
    assert tuple(row.session_date for row in prices) == traded
    # 두 페이지로 끝나야 한다. 세 번째 요청은 상장 전 구간이라 0행이고 예산만 태운다.
    assert len(receipts) == 2
    assert len(calls) == 2
def test_ecos_page_range_is_valid_without_a_provider_call() -> None:
    """provider가 요청하는 page 범위는 네트워크 전에 정책을 통과해야 한다.

    실측: bootstrap/daily provider가 page 1..400을 요청했지만 정책은 요청당 span 200행을
    강제한다(end - start >= 200 이면 거부). ECOS 첫 호출은 네트워크로 나가기도 전에 분류되지 않은
    ValueError로 죽었고, 그 예외가 실패 시도로 기록되면서 승인 호출 2건을 태우고서야 원인을 알 수
    있었다. 범위 유효성은 호출 없이 판정되므로 회귀로 닫는다.
    """

    arguments = ("722Y001", "D", "20220329", "20221114", "0101000")
    path = build_keyless_service_path(
        service="StatisticSearch",
        start_index=1,
        end_index=ECOS_MAX_ROWS_PER_REQUEST,
        arguments=arguments,
    )
    assert f"/1/{ECOS_MAX_ROWS_PER_REQUEST}/" in path

    # 상한을 한 행이라도 넘으면 거부된다. 이것이 page 1..400이 죽은 이유다.
    with pytest.raises(ValueError, match="page is invalid"):
        build_keyless_service_path(
            service="StatisticSearch",
            start_index=1,
            end_index=ECOS_MAX_ROWS_PER_REQUEST + 1,
            arguments=arguments,
        )


def test_ecos_chunk_length_cannot_exceed_the_request_row_cap() -> None:
    """chunk 달력 길이가 행 상한을 넘으면 한 요청이 상한을 넘길 수 있다.

    달력 길이를 상한 이하로 두면 발행 밀도와 무관하게 안전하다. 영업일 기준이라 실제로는 더
    적지만 그 가정에 의존하지 않는 것이 요점이다. 두 상수가 다시 어긋나면 여기서 걸린다.
    """

    assert bootstrap_executor._ECOS_CHUNK_DAYS <= ECOS_MAX_ROWS_PER_REQUEST

    # 승인 상한 안에 들어가는 논리 query 수인지도 함께 고정한다.
    packet = author_bootstrap_packet(cutoff=datetime(2026, 8, 13, 23, 10, tzinfo=UTC))
    raw_start = packet.window.raw_sessions[0]
    raw_end = packet.window.raw_sessions[-1]
    total = 0
    for series in CANDIDATE_SERIES:
        start = (
            raw_start - timedelta(days=366)
            if series.series_id == "policy-rate"
            else previous_xkrx_session(raw_start)
        )
        cursor = start
        while cursor <= raw_end:
            cursor = min(
                cursor + timedelta(days=bootstrap_executor._ECOS_CHUNK_DAYS - 1), raw_end
            ) + timedelta(days=1)
            total += 1
    assert total <= packet.budget.ecos_get
