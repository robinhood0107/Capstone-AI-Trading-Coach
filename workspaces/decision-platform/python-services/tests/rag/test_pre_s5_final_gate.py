from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from app.rag.pre_s5_final_gate import (
    FinalGateError,
    ReleaseDatabaseSnapshot,
    WindowBManifest,
    author_kis_quote_manifest,
    author_window_b_manifest,
    derive_kis_mock_limit_price,
    load_kis_quote_manifest,
    load_kis_quote_receipt,
    load_window_b_manifest,
    require_window_b_child,
    verify_release_ledger,
    write_kis_quote_receipt,
    write_release_evidence_receipt,
)


@pytest.mark.parametrize(
    ("current_price", "previous_close", "expected"),
    (
        (73_500, 72_700, 51_000),
        (18_000, 17_950, 12_580),
        (1_500, 1_490, 1_044),
    ),
)
def test_kis_mock_limit_price_uses_previous_close_seventy_percent_plus_one_tick(
    current_price: int,
    previous_close: int,
    expected: int,
) -> None:
    assert derive_kis_mock_limit_price(current_price, previous_close) == expected


def test_kis_mock_limit_price_rejects_price_not_below_current() -> None:
    with pytest.raises(FinalGateError, match="PRE_S5_KIS_MOCK_LIMIT_PRICE_INVALID"):
        derive_kis_mock_limit_price(1, 1)


def test_kis_quote_manifest_and_receipt_are_exact_and_content_free(tmp_path: Path) -> None:
    manifest_path = tmp_path / "kis-mock-quote-manifest.v1.json"
    manifest_sha256 = author_kis_quote_manifest(
        output_path=manifest_path,
        head_commit="1" * 40,
        tree_object="2" * 40,
        ci_digest="3" * 64,
        security_digest="4" * 64,
    )
    os.environ["PRE_S5_KIS_MOCK_QUOTE_MANIFEST_SHA256"] = manifest_sha256
    try:
        manifest = load_kis_quote_manifest(manifest_path)
    finally:
        os.environ.pop("PRE_S5_KIS_MOCK_QUOTE_MANIFEST_SHA256", None)
    receipt_path = tmp_path / "kis-mock-quote-receipt.v1.json"
    receipt_sha256 = write_kis_quote_receipt(
        output_path=receipt_path,
        manifest_sha256=manifest.manifest_sha256,
        quote_projection_sha256="5" * 64,
        current_price=73_500,
        previous_diff=800,
        token_physical_calls=1,
        brokerage_physical_calls=1,
    )
    receipt = load_kis_quote_receipt(receipt_path)

    assert len(receipt_sha256) == 64
    assert receipt.limit_price == 51_000
    assert receipt.previous_close == 72_700
    assert receipt.token_physical_calls == 1
    assert receipt.brokerage_physical_calls == 1
    raw = receipt_path.read_text(encoding="utf-8")
    assert "appkey" not in raw.lower()
    assert "authorization" not in raw.lower()
    assert receipt_path.stat().st_mode & 0o777 == 0o600


def test_window_b_manifest_binds_all_child_packets_and_no_retry(tmp_path: Path) -> None:
    output = tmp_path / "window-b-final-manifest.v1.json"
    manifest_sha256 = author_window_b_manifest(
        output_path=output,
        head_commit="1" * 40,
        tree_object="2" * 40,
        ci_digest="3" * 64,
        security_digest="4" * 64,
        voyage_query_packet_sha256="5" * 64,
        vertex_packet_sha256="6" * 64,
        kis_mock_packet_sha256="7" * 64,
        kis_quote_receipt_sha256="8" * 64,
    )
    os.environ["PRE_S5_WINDOW_B_FINAL_MANIFEST_SHA256"] = manifest_sha256
    try:
        manifest = load_window_b_manifest(output)
    finally:
        os.environ.pop("PRE_S5_WINDOW_B_FINAL_MANIFEST_SHA256", None)

    assert manifest.voyage_query_packet_sha256 == "5" * 64
    assert manifest.vertex_packet_sha256 == "6" * 64
    assert manifest.kis_mock_packet_sha256 == "7" * 64
    value = json.loads(output.read_text(encoding="utf-8"))
    assert value["physicalCaps"] == {
        "kisMockBrokerage": 7,
        "kisMockTokenP": 1,
        "vertexGenerateContent": 1,
        "vertexToken": 1,
        "voyageQuery": 1,
    }
    assert value["retryCount"] == 0
    assert value["rawArtifactCount"] == 0
    assert isinstance(value["expiresAt"], str)
    assert output.stat().st_mode & 0o777 == 0o600


def test_window_b_manifest_rejects_wrong_exact_approval(tmp_path: Path) -> None:
    output = tmp_path / "window-b-final-manifest.v1.json"
    author_window_b_manifest(
        output_path=output,
        head_commit="1" * 40,
        tree_object="2" * 40,
        ci_digest="3" * 64,
        security_digest="4" * 64,
        voyage_query_packet_sha256="5" * 64,
        vertex_packet_sha256="6" * 64,
        kis_mock_packet_sha256="7" * 64,
        kis_quote_receipt_sha256="8" * 64,
    )
    os.environ["PRE_S5_WINDOW_B_FINAL_MANIFEST_SHA256"] = "f" * 64
    try:
        with pytest.raises(FinalGateError, match="PRE_S5_WINDOW_B_MANIFEST_APPROVAL"):
            load_window_b_manifest(output)
    finally:
        os.environ.pop("PRE_S5_WINDOW_B_FINAL_MANIFEST_SHA256", None)


def test_window_b_manifest_rejects_mismatched_child_before_runtime() -> None:
    manifest = WindowBManifest(
        manifest_sha256="4" * 64,
        voyage_query_packet_sha256="5" * 64,
        vertex_packet_sha256="6" * 64,
        kis_mock_packet_sha256="7" * 64,
        kis_quote_receipt_sha256="8" * 64,
        head_commit="1" * 40,
        tree_object="2" * 40,
        ci_digest="3" * 64,
        security_digest="9" * 64,
    )

    with pytest.raises(FinalGateError, match="PRE_S5_WINDOW_B_CHILD_BINDING"):
        require_window_b_child(
            manifest,
            runtime="VERTEX",
            packet_sha256="f" * 64,
        )


def test_final_gate_writer_rejects_symlinked_control_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    os.symlink(outside, tmp_path / "control")

    with pytest.raises(FinalGateError, match="PRE_S5_FINAL_GATE_BOUNDARY"):
        author_kis_quote_manifest(
            output_path=tmp_path / "control" / "kis-mock-quote-manifest.v1.json",
            head_commit="1" * 40,
            tree_object="2" * 40,
            ci_digest="3" * 64,
            security_digest="4" * 64,
        )
    assert not (outside / "kis-mock-quote-manifest.v1.json").exists()


def test_release_ledger_rejects_self_asserted_markers_without_bound_receipts(tmp_path: Path) -> None:
    ledger = {
        "binding": _release_binding(),
        "markers": _release_markers(),
        "schemaVersion": "pre-s5-release-ledger/v2",
    }

    with pytest.raises(FinalGateError, match="PRE_S5_RELEASE_LEDGER_INVALID"):
        verify_release_ledger(
            local_root=tmp_path,
            binding=("1" * 40, "2" * 40, "3" * 64, "4" * 64),
            ledger=ledger,
            database_snapshot=_release_database_snapshot(),
        )


def test_release_ledger_rejects_receipt_digest_or_database_drift(tmp_path: Path) -> None:
    ledger = _write_release_receipts_and_ledger(tmp_path)
    ledger["receipts"]["kisMockV3"]["sha256"] = "f" * 64

    with pytest.raises(FinalGateError, match="PRE_S5_RELEASE_RECEIPT_INVALID"):
        verify_release_ledger(
            local_root=tmp_path,
            binding=("1" * 40, "2" * 40, "3" * 64, "4" * 64),
            ledger=ledger,
            database_snapshot=_release_database_snapshot(),
        )

    ledger = _write_release_receipts_and_ledger(tmp_path)
    with pytest.raises(FinalGateError, match="PRE_S5_RELEASE_DATABASE_DRIFT"):
        verify_release_ledger(
            local_root=tmp_path,
            binding=("1" * 40, "2" * 40, "3" * 64, "4" * 64),
            ledger=ledger,
            database_snapshot=replace(_release_database_snapshot(), public_chunk_count=7_870),
        )


def test_release_ledger_opens_only_with_exact_receipts_and_database_state(tmp_path: Path) -> None:
    ledger = _write_release_receipts_and_ledger(tmp_path)

    markers = verify_release_ledger(
        local_root=tmp_path,
        binding=("1" * 40, "2" * 40, "3" * 64, "4" * 64),
        ledger=ledger,
        database_snapshot=_release_database_snapshot(),
    )

    assert markers == _release_markers()


def test_release_ledger_distinguishes_approved_after_hours_kis_mock(tmp_path: Path) -> None:
    ledger = _write_release_receipts_and_ledger(tmp_path)
    receipt_path = tmp_path / "evidence/kisMockV3.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["facts"].update(
        {
            "brokeragePhysicalCalls": 0,
            "tokenPhysicalCalls": 0,
            "verificationMode": "AFTER_HOURS_DETERMINISTIC_MOCK",
        }
    )
    encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    receipt_path.write_bytes(encoded)
    receipt_path.chmod(0o600)
    ledger["receipts"]["kisMockV3"]["sha256"] = hashlib.sha256(encoded).hexdigest()
    ledger["markers"]["KIS_MOCK_AFTER_HOURS_RECONCILIATION_VERIFIED"] = True
    ledger["markers"]["KIS_MOCK_FULL_RECONCILIATION_VERIFIED"] = False

    markers = verify_release_ledger(
        local_root=tmp_path,
        binding=("1" * 40, "2" * 40, "3" * 64, "4" * 64),
        ledger=ledger,
        database_snapshot=_release_database_snapshot(),
    )

    assert markers["KIS_MOCK_AFTER_HOURS_RECONCILIATION_VERIFIED"] is True
    assert markers["KIS_MOCK_FULL_RECONCILIATION_VERIFIED"] is False


def test_release_ledger_rejects_boolean_smuggled_as_numeric_evidence(tmp_path: Path) -> None:
    ledger = _write_release_receipts_and_ledger(tmp_path)
    receipt_path = tmp_path / "evidence/ownerBgeLocal.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["facts"]["documentCount"] = True
    encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    receipt_path.write_bytes(encoded)
    receipt_path.chmod(0o600)
    ledger["receipts"]["ownerBgeLocal"]["sha256"] = hashlib.sha256(encoded).hexdigest()

    with pytest.raises(FinalGateError, match="PRE_S5_RELEASE_RECEIPT_INVALID"):
        verify_release_ledger(
            local_root=tmp_path,
            binding=("1" * 40, "2" * 40, "3" * 64, "4" * 64),
            ledger=ledger,
            database_snapshot=_release_database_snapshot(),
        )


def _release_binding() -> dict[str, str]:
    return {
        "ciDigest": "3" * 64,
        "headCommit": "1" * 40,
        "securityDigest": "4" * 64,
        "treeObject": "2" * 40,
    }


def _release_markers() -> dict[str, object]:
    return {
        "BGE_OWNER_EMBEDDING_INFERENCE": "USER_SELECTED_ONLY",
        "BGE_PUBLIC_EMBEDDING_INFERENCE_CALLS": 0,
        "FINAL_SECURITY_COVERAGE_COMPLETE_FINDINGS": 0,
        "FOREIGN_NEWS_MODEL_SELECTION": "ABSTAIN",
        "FOREIGN_NEWS_PROVIDER_CALLS": 0,
        "KIS_MOCK_AFTER_HOURS_RECONCILIATION_VERIFIED": False,
        "KIS_MOCK_FULL_RECONCILIATION_VERIFIED": True,
        "OWNER_PRIVATE_BGE_LOCAL_VERIFIED": True,
        "OWNER_PRIVATE_IMPORT_DELETE_RLS_VERIFIED": True,
        "OWNER_PRIVATE_PROFILE_SELECTION": "USER_EXPLICIT_LIBRARY_LEVEL",
        "OWNER_PRIVATE_VOYAGE_SYNTHETIC_ONE_SHOT_VERIFIED": True,
        "PRE_S5_FRESH_EXECUTION_NAMESPACE_VERIFIED": True,
        "RAG_NEWS_ANALYST_DECISION_SIGNAL_ORDER_AUTHORITY": 0,
        "RAG_V2_ACTIVE_EMBEDDING_PROFILE": "voyage_context_4_1024_v1",
        "RAG_V2_CORPUS_STATE": "FULL_READY",
        "S48_ACCESSIBLE_LANES_TERMINALLY_CLASSIFIED": True,
        "TRACKED_RAW_EXTRACTED_EMBEDDINGS": 0,
        "VERTEX_SERVICE_ACCOUNT_OAUTH_GEMINI_3_5_FLASH_ONE_SHOT_VERIFIED": True,
        "VOYAGE_QUERY_USAGE": "COMMITTED",
    }


def _release_database_snapshot() -> ReleaseDatabaseSnapshot:
    return ReleaseDatabaseSnapshot(
        latest_migration=63,
        public_state="ACTIVE",
        public_embedding_profile_id="voyage_context_4_1024_v1",
        public_source_count=142,
        public_chunk_count=7_871,
        committed_document_batch_count=63,
        public_evaluation_count=2,
        public_evaluation_minimum=1.0,
        public_evaluation_leak_count=0,
        owner_source_count=0,
        owner_chunk_count=0,
        owner_embedding_count=0,
        owner_profile_lock_count=0,
        owner_voyage_committed_document_count=9,
        owner_voyage_committed_chunk_count=9,
        s48_states=(
            ("S48_CORE6_ECOS", "ABSTAIN"),
            ("S48_CORE6_KIS", "AVAILABLE"),
            ("S48_CORE6_KOFIA", "BLOCKED"),
            ("S48_CORE6_KRX", "ABSTAIN"),
            ("S48_CORE6_OPENDART", "ABSTAIN"),
            ("S48_CORE6_SEC_EDGAR", "ABSTAIN"),
            ("S48_OPTIONAL3_FINNHUB", "BLOCKED"),
            ("S48_OPTIONAL3_MASSIVE", "BLOCKED"),
            ("S48_OPTIONAL3_TWELVE_DATA", "BLOCKED"),
        ),
        voyage_query_committed_packet_sha256="5" * 64,
        vertex_committed_packet_sha256="6" * 64,
    )


def _write_release_receipts_and_ledger(tmp_path: Path) -> dict[str, object]:
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700, exist_ok=True)
    binding = _release_binding()
    facts_by_name: dict[str, dict[str, object]] = {
        "ownerBgeLocal": {
            "documentCount": 1,
            "providerPhysicalCalls": 0,
            "residualRows": 0,
        },
        "kisMockV3": {
            "brokeragePhysicalCalls": 7,
            "completedSteps": [
                "preBalance",
                "buyable",
                "submitLimitBuy",
                "cancelFull",
                "executionRead",
                "postBalance",
                "openOrderReconciliation",
            ],
            "liveOrderCalls": 0,
            "openOrderCount": 0,
            "retryCount": 0,
            "tokenPhysicalCalls": 1,
            "verificationMode": "PHYSICAL_MOCK",
        },
        "requiredCi": {
            "checks": {
                "Contracts CI": "SUCCESS",
                "Kotlin Build": "SUCCESS",
                "Python CI": "SUCCESS",
                "Repo Hygiene": "SUCCESS",
            }
        },
        "securityScan": {"coverage": "complete", "validatedFindings": 0},
        "trackedAudit": {
            "aiAttributionCount": 0,
            "credentialCount": 0,
            "placeholderWorkspaceDiffCount": 0,
            "rawTextVectorCount": 0,
        },
    }
    receipts: dict[str, dict[str, str]] = {}
    for name, facts in facts_by_name.items():
        path = evidence / f"{name}.json"
        receipt_sha256 = write_release_evidence_receipt(
            output_path=path,
            binding=("1" * 40, "2" * 40, "3" * 64, "4" * 64),
            kind=name,
            facts=facts,
        )
        receipts[name] = {
            "path": f"evidence/{name}.json",
            "sha256": receipt_sha256,
        }
    return {
        "binding": binding,
        "markers": _release_markers(),
        "receipts": receipts,
        "schemaVersion": "pre-s5-release-ledger/v2",
        "windowB": {
            "kisMockPacketSha256": "7" * 64,
            "manifestSha256": "8" * 64,
            "vertexPacketSha256": "6" * 64,
            "voyageQueryPacketSha256": "5" * 64,
        },
    }
