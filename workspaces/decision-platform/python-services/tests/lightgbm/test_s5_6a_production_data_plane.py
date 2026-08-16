from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
import hashlib
from pathlib import Path

import numpy as np
import pytest

from app.data.kis.parsers import KISResponseError, parse_daily_bars
from app.data.krx.production_parsers import parse_s5_production_response
from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError
from app.lightgbm.bootstrap_control import BootstrapLedger, BootstrapPhase
from app.lightgbm.bootstrap_packet import author_bootstrap_packet
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
from app.lightgbm.pit_calendar import build_pit_session_window
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
    SourceChunkReceipt,
    build_source_manifest,
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
    content = b"PAR1synthetic-projection-PAR1"
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
    manifest = build_source_manifest(
        created_at=datetime(2026, 8, 16, tzinfo=UTC),
        dataset_cutoff=datetime(2026, 8, 18, tzinfo=UTC),
        chunks=[chunk],
    )
    (tmp_path / "manifest.json").write_bytes(manifest)
    bundle = read_source_bundle(
        approved_root=tmp_path,
        expected_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
    )
    assert bundle.chunks == (chunk,)

    with pytest.raises(LightGbmContractError, match="trust anchor"):
        read_source_bundle(approved_root=tmp_path, expected_manifest_sha256="0" * 64)


def test_bootstrap_failure_stops_remaining_calls_and_resume_targets_failed_chunk() -> None:
    ledger = BootstrapLedger(BootstrapBudget(krx_get=2, kis_get=1, kis_token=1, ecos_get=1))
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
    packet = author_bootstrap_packet(cutoff=datetime(2026, 8, 16, 8, 10, tzinfo=UTC))
    assert len(packet.window.raw_sessions) == 1_072
    assert len(packet.window.eligible_sessions) == 1_007
    assert len(packet.schedules) == 51
    assert packet.budget.krx_get == 4_441
    assert packet.budget.total == 6_446
    assert b'"strictProviderPITClaim":false' in packet.content


def test_production_feature_and_label_use_row_specific_clocks() -> None:
    packet = author_bootstrap_packet(cutoff=datetime(2026, 8, 16, 8, 10, tzinfo=UTC))
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
