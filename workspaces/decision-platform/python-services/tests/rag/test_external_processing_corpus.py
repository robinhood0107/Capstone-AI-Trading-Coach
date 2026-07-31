from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from app.rag.bge_artifact import BgeVerifiedPacket
from app.rag.bge_full_generation import (
    BgeBatchBenchmarkReceipt,
    BgeGenerationDatabaseReceipt,
    execute_bge_full_generation,
    prepare_bge_full_generation,
)
from app.rag.corpus_profiles import load_source_card_corpus
from app.rag.external_processing_corpus import (
    S4_7C_APPROVAL_ID,
    S4_7C_CORPUS_MANIFEST_PATH,
    S4_7C_SOURCE_CARD_ROOT,
    ExternalContextCandidate,
    ExternalProcessingCorpusError,
    project_provider_context,
)
from app.rag.source_card_corpus import (
    S4_7B_CORPUS_MANIFEST_PATH,
    S4_7B_SOURCE_CARD_ROOT,
)


REPO_ROOT = Path(__file__).resolve().parents[5]
GENERATOR = REPO_ROOT / "capstone-rag/generate_s4_7c_external_corpus.py"
GENERATION_REPORT = REPO_ROOT / "capstone-rag/reports/s4-7c-external-generation.v1.json"
OLD_MANIFEST_FILE_SHA256 = "d772ab9a54c5477afeccfd41cd41e496645967dee59dd50c0bcc304ae3c95558"
OLD_CORPUS_MANIFEST_SHA256 = "7f2b4d72dcbaccf57cbe49a980973b17b4a9bfd85bec4694fd66fd7fd2a9decd"


class _WhitespaceTokenizer:
    def token_spans(self, text: str) -> tuple[tuple[int, int], ...]:
        spans: list[tuple[int, int]] = []
        cursor = 0
        for token in text.split():
            start = text.index(token, cursor)
            end = start + len(token)
            spans.append((start, end))
            cursor = end
        return tuple(spans)


class _FixtureEmbedder:
    def embed(self, texts: tuple[str, ...]) -> NDArray[np.float32]:
        rows: list[NDArray[np.float32]] = []
        for text in texts:
            vector = np.zeros(1024, dtype=np.float32)
            vector[int(hashlib.sha256(text.encode()).hexdigest()[:8], 16) % 1024] = 1
            rows.append(vector)
        return np.stack(rows)


class _RecordingWriter:
    def materialize(
        self,
        *,
        plan: object,
        rows: tuple[object, ...],
        aggregate_row_hash: str,
        generation_vector_hash: str,
    ) -> BgeGenerationDatabaseReceipt:
        generation_id = str(getattr(plan, "generation_id"))
        materialization_run_id = str(getattr(plan, "materialization_run_id"))
        return BgeGenerationDatabaseReceipt(
            generation_id=generation_id,
            materialization_run_id=materialization_run_id,
            final_row_count=len(rows),
            status="MATERIALIZED",
            aggregate_row_hash=aggregate_row_hash,
            generation_vector_hash=generation_vector_hash,
            active_pointer_changed=False,
        )


def test_old_s4_7b_bytes_and_manifest_identity_remain_immutable() -> None:
    assert hashlib.sha256(S4_7B_CORPUS_MANIFEST_PATH.read_bytes()).hexdigest() == (
        OLD_MANIFEST_FILE_SHA256
    )
    old = load_source_card_corpus(profile_id="s4_7b_internal_v1")
    assert old.corpus_manifest_sha256 == OLD_CORPUS_MANIFEST_SHA256
    assert len(tuple(S4_7B_SOURCE_CARD_ROOT.glob("*.md"))) == 30


def test_external_profile_has_exact_same_membership_and_body_with_30_verified_receipts() -> None:
    old = load_source_card_corpus(profile_id="s4_7b_internal_v1")
    new = load_source_card_corpus(profile_id="s4_7c_external_v1")

    assert len(new.cards) == 30
    assert [(card.source_id, card.card_id) for card in new.cards] == [
        (card.source_id, card.card_id) for card in old.cards
    ]
    assert [card.canonical_body for card in new.cards] == [
        card.canonical_body for card in old.cards
    ]
    assert all(card.front_matter["externalProcessingAllowed"] is True for card in new.cards)
    assert all(
        card.front_matter["externalProcessingGate"] == "LICENSE_AND_CONSENT_VERIFIED"
        for card in new.cards
    )
    assert all(
        card.front_matter["contentClass"] == "PROJECT_AUTHORED_SANITIZED_CARD" for card in new.cards
    )

    receipts = new.manifest["licenseConsentReceipts"]
    assert len(receipts) == 30
    assert new.manifest["approvalId"] == S4_7C_APPROVAL_ID
    assert new.manifest["oldCorpusManifestSha256"] == old.corpus_manifest_sha256
    for receipt, old_card, new_card in zip(receipts, old.cards, new.cards, strict=True):
        assert receipt["sourceId"] == old_card.source_id == new_card.source_id
        assert receipt["cardId"] == old_card.card_id == new_card.card_id
        assert receipt["contentClass"] == "PROJECT_AUTHORED_SANITIZED_CARD"
        assert receipt["accessLevel"] == "PUBLIC"
        assert receipt["projectAuthoredBody"] is True
        assert receipt["rawReferenceCopied"] is False
        assert receipt["providerPayloadCopied"] is False
        assert receipt["longQuotePresent"] is False
        assert receipt["publicLocatorOnly"] is True
        assert receipt["attributionPresent"] is True
        assert receipt["licenseNoteReviewed"] is True
        assert receipt["thirdPartyRestrictionReviewed"] is True
        assert receipt["userConsentDecisionId"] == S4_7C_APPROVAL_ID
        assert receipt["reviewerDecision"] == "PASS"
        assert receipt["oldBodySha256"] == receipt["newBodySha256"]
        assert receipt["oldBodySha256"] == old_card.body_sha256 == new_card.body_sha256


def test_naver_receipt_is_project_boundary_only_and_contains_no_deleted_snapshot() -> None:
    corpus = load_source_card_corpus(profile_id="s4_7c_external_v1")
    receipt = next(
        item
        for item in corpus.manifest["licenseConsentReceipts"]
        if item["sourceId"] == "src_project_naver_news_discovery_boundary_001"
    )

    assert receipt["projectAuthoredPolicyBoundaryOnly"] is True
    assert receipt["naverSearchResultMetadataCount"] == 0
    assert receipt["deletedSnapshotContentCount"] == 0
    assert receipt["externalProviderProcessesNaverApiResult"] is False


def test_profile_loader_rejects_unknown_or_old_new_mix() -> None:
    with pytest.raises(ExternalProcessingCorpusError, match="CORPUS_PROFILE_UNKNOWN"):
        load_source_card_corpus(profile_id="client_selected")
    with pytest.raises(ExternalProcessingCorpusError, match="CORPUS_PROFILE_MIXED"):
        load_source_card_corpus(
            profile_id="s4_7c_external_v1",
            card_root=S4_7B_SOURCE_CARD_ROOT,
            manifest_path=S4_7C_CORPUS_MANIFEST_PATH,
        )


def test_external_manifest_or_single_false_card_drift_is_rejected(posix_tmp_path: Path) -> None:
    copied_root = posix_tmp_path / "cards"
    copied_root.mkdir()
    for source in S4_7C_SOURCE_CARD_ROOT.glob("*.md"):
        (copied_root / source.name).write_bytes(source.read_bytes())
    target = sorted(copied_root.glob("*.md"))[0]
    target.write_text(
        target.read_text(encoding="utf-8")
        .replace(
            "externalProcessingAllowed: true",
            "externalProcessingAllowed: false",
            1,
        )
        .replace(
            "externalProcessingGate: LICENSE_AND_CONSENT_VERIFIED",
            "externalProcessingGate: NOT_GRANTED",
            1,
        ),
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ExternalProcessingCorpusError):
        load_source_card_corpus(
            profile_id="s4_7c_external_v1",
            card_root=copied_root,
            manifest_path=S4_7C_CORPUS_MANIFEST_PATH,
        )


def test_external_profile_produces_append_only_body_equivalent_bge_generation() -> None:
    old_plan = prepare_bge_full_generation(
        corpus=load_source_card_corpus(profile_id="s4_7b_internal_v1"),
        tokenizer=_WhitespaceTokenizer(),
        artifact=_artifact(),
        batch_benchmark=_benchmark(),
    )
    new_plan = prepare_bge_full_generation(
        corpus=load_source_card_corpus(profile_id="s4_7c_external_v1"),
        tokenizer=_WhitespaceTokenizer(),
        artifact=_artifact(),
        batch_benchmark=_benchmark(),
    )

    assert new_plan.generation_id != old_plan.generation_id
    assert new_plan.corpus_hash != old_plan.corpus_hash
    assert (
        old_plan.corpus_profile_id,
        old_plan.source_registry_version,
        old_plan.external_processing_allowed,
    ) == ("s4_7b_internal_v1", "s4-7b-source-card-v2", False)
    assert (
        new_plan.corpus_profile_id,
        new_plan.source_registry_version,
        new_plan.external_processing_allowed,
    ) == ("s4_7c_external_v1", "s4-7c-source-card-v2", True)
    assert [item.embedding_input.text for item in new_plan.items] == [
        item.embedding_input.text for item in old_plan.items
    ]
    materialized = execute_bge_full_generation(
        plan=new_plan,
        embedder=_FixtureEmbedder(),
        repository=_RecordingWriter(),
    )
    assert len(materialized.rows) == 30
    assert all(np.isfinite(row.embedding).all() for row in materialized.rows)
    assert all(
        float(np.linalg.norm(row.embedding)) == pytest.approx(1.0) for row in materialized.rows
    )


def test_provider_context_requires_external_profile_question_gate_and_current_generation() -> None:
    external = load_source_card_corpus(profile_id="s4_7c_external_v1")
    card = external.cards[0]
    candidate = ExternalContextCandidate(
        source_id=card.source_id,
        card_id=card.card_id,
        canonical_content=card.canonical_body,
        source_revision_current=True,
        chunk_revision_current=True,
        verified_source_check=True,
        generation_id="rag_gen_" + "1" * 32,
    )

    projected = project_provider_context(
        corpus=external,
        candidates=(candidate,),
        active_generation_id="rag_gen_" + "1" * 32,
        question_authorized=True,
    )
    assert len(projected.items) == 1
    assert projected.items[0]["sourceId"] == card.source_id
    assert len(projected.context_set_hash) == 64

    with pytest.raises(ExternalProcessingCorpusError, match="QUESTION_GATE"):
        project_provider_context(
            corpus=external,
            candidates=(candidate,),
            active_generation_id=candidate.generation_id,
            question_authorized=False,
        )
    with pytest.raises(ExternalProcessingCorpusError, match="GENERATION_DRIFT"):
        project_provider_context(
            corpus=external,
            candidates=(replace(candidate, generation_id="rag_gen_" + "2" * 32),),
            active_generation_id="rag_gen_" + "1" * 32,
            question_authorized=True,
        )
    with pytest.raises(ExternalProcessingCorpusError, match="CARD_NOT_EXTERNAL"):
        project_provider_context(
            corpus=load_source_card_corpus(profile_id="s4_7b_internal_v1"),
            candidates=(candidate,),
            active_generation_id=candidate.generation_id,
            question_authorized=True,
        )


def test_external_generator_is_deterministic_and_current() -> None:
    completed = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "S4_7C_EXTERNAL_PROCESSING_CORPUS_VERIFIED" in completed.stdout


def test_tracked_external_generation_report_proves_complete_atomic_transition() -> None:
    report = json.loads(GENERATION_REPORT.read_text(encoding="utf-8"))
    expected_hash = report.pop("reportSha256")
    actual_hash = hashlib.sha256(
        (
            json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
    ).hexdigest()

    assert expected_hash == actual_hash
    assert report["status"] == "PASS"
    assert report["externalProfileId"] == "s4_7c_external_v1"
    assert report["sourceCount"] == report["chunkCount"] == 30
    assert report["bodyEquivalentCount"] == 30
    assert report["vectorEquivalentCount"] == 30
    assert report["activeGenerationCount"] == 1
    assert report["oldGenerationStatus"] == "DISABLED"
    assert report["newGenerationStatus"] == "ACTIVE"
    assert report["activePointerBefore"] == report["oldGenerationId"]
    assert report["activePointerAfter"] == report["newGenerationId"]
    assert report["retrievalNonRegression"] is True
    assert report["providerPhysicalCalls"] == 0


def _artifact() -> BgeVerifiedPacket:
    return BgeVerifiedPacket(
        revision="5617a9f61b028005a4858fdac845db406aefb181",
        file_count=10,
        total_bytes=2_289_781_803,
        file_manifest_sha256="a0ae6372b2d735b593d806d24c1155cb48dd7188adebe7d6b7619a1622fb71aa",
    )


def _benchmark() -> BgeBatchBenchmarkReceipt:
    return BgeBatchBenchmarkReceipt(
        selected_batch_size=32,
        candidates=(16, 32, 64),
        peak_rss_bytes=((16, 4_000_000_000), (32, 5_000_000_000), (64, 7_000_000_000)),
        elapsed_ms=((16, 1_000.0), (32, 900.0), (64, 850.0)),
        environment_fingerprint_sha256="1" * 64,
        benchmark_sha256="2" * 64,
    )
