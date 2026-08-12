from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from app.rag.document_ir_materializer import RagV2CanonicalDocumentChunk, RagV2DocumentMaterialization
from app.rag.ingest_pipeline import RagEmbeddingInput
from app.rag.rag_v2_bge_materializer import RagV2PreparedPublicDocument
from app.rag.rag_v2_external_exact30_voyage_runner import PublicVoyageSourceMetadata
from app.rag.rag_v2_voyage_checkpoint import (
    RagV2VoyageCheckpointError,
    load_optional_public_voyage_checkpoint,
    load_public_voyage_checkpoint,
    write_public_voyage_checkpoint,
)


def test_optional_checkpoint_rejects_broken_derived_directory_symlink(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    os.symlink(tmp_path / "missing", root / "derived-ir")

    with pytest.raises(RagV2VoyageCheckpointError, match="VOYAGE_CHECKPOINT_BOUNDARY"):
        load_optional_public_voyage_checkpoint(
            local_corpus_root=root,
            component_scope="OA112",
            expected_raw_content_sha256=_prepared().document.raw_content_sha256,
            expected_source_revision_id=_prepared().document.source_revision_id,
            parser_version="1.1.0",
            tokenizer_version="bge-m3-sentencepiece-v1",
        )


def test_optional_checkpoint_returns_none_when_other_scope_created_derived_root(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    derived = root / "derived-ir"
    derived.mkdir(mode=0o700)
    (derived / ".tmp").mkdir(mode=0o700)
    (derived / "exact30").mkdir(mode=0o700)

    loaded = load_optional_public_voyage_checkpoint(
        local_corpus_root=root,
        component_scope="OA112",
        expected_raw_content_sha256=_prepared().document.raw_content_sha256,
        expected_source_revision_id=_prepared().document.source_revision_id,
        parser_version="1.1.0",
        tokenizer_version="bge-m3-sentencepiece-v1",
    )

    assert loaded is None


def test_checkpoint_round_trip_is_0600_profile_neutral_and_reusable(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    prepared = _prepared()
    metadata = _metadata()

    written = write_public_voyage_checkpoint(
        local_corpus_root=root,
        parser_version="1.1.0",
        tokenizer_version="bge-m3-sentencepiece-v1",
        prepared=prepared,
        metadata=metadata,
    )
    loaded = load_public_voyage_checkpoint(
        local_corpus_root=root,
        component_scope="OA112",
        expected_raw_content_sha256=prepared.document.raw_content_sha256,
        expected_source_revision_id=prepared.document.source_revision_id,
        parser_version="1.1.0",
        tokenizer_version="bge-m3-sentencepiece-v1",
    )

    assert written.reused is False
    assert loaded.reused is True
    assert loaded.checkpoint_key == written.checkpoint_key
    assert loaded.prepared == prepared
    assert loaded.metadata == metadata
    assert loaded.provider_call_count == 0
    assert loaded.vector_count == 0
    assert oct(loaded.path.stat().st_mode & 0o777) == "0o600"
    assert loaded.path.parent.name == "oa112"
    payload = loaded.path.read_text(encoding="utf-8")
    decoded = json.loads(payload)
    assert decoded["schemaVersion"] == "pre-s5-public-voyage-checkpoint/v2"
    assert decoded["identity"]["schemaVersion"] == 2
    assert decoded["identity"]["sanitizerVersion"] == "public-pii-v2-rechunk-v2"
    assert '"vectors"' not in payload
    assert "providerResponse" not in payload
    assert "/" not in json.loads(payload)["identity"]["sourceRevisionId"]


def test_checkpoint_rejects_profile_neutral_chunk_over_600_before_write(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    prepared = _prepared()
    oversized_chunk = replace(prepared.document.chunks[0], token_count=602)
    oversized = replace(
        prepared,
        document=replace(prepared.document, chunks=(oversized_chunk,)),
    )

    with pytest.raises(RagV2VoyageCheckpointError, match="VOYAGE_CHECKPOINT_PAYLOAD"):
        write_public_voyage_checkpoint(
            local_corpus_root=root,
            parser_version="1.1.0",
            tokenizer_version="bge-m3-sentencepiece-v1",
            prepared=oversized,
            metadata=_metadata(),
        )

    assert not (root / "derived-ir").exists()


def test_optional_checkpoint_does_not_reuse_v1_identity_after_sanitizer_rotation(
    tmp_path: Path,
) -> None:
    root = _private_root(tmp_path)
    prepared = _prepared()
    old_identity = {
        "componentScope": "OA112",
        "parserVersion": "1.1.0",
        "rawContentSha256": prepared.document.raw_content_sha256,
        "schemaVersion": 1,
        "sourceRevisionId": prepared.document.source_revision_id,
        "tokenizerVersion": "bge-m3-sentencepiece-v1",
    }
    old_key = hashlib.sha256(
        json.dumps(old_identity, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    derived = root / "derived-ir"
    derived.mkdir(mode=0o700)
    (derived / ".tmp").mkdir(mode=0o700)
    scope = derived / "oa112"
    scope.mkdir(mode=0o700)
    old_leaf = scope / f"{old_key}.json"
    old_leaf.write_text("{}", encoding="utf-8")
    old_leaf.chmod(0o600)

    loaded = load_optional_public_voyage_checkpoint(
        local_corpus_root=root,
        component_scope="OA112",
        expected_raw_content_sha256=prepared.document.raw_content_sha256,
        expected_source_revision_id=prepared.document.source_revision_id,
        parser_version="1.1.0",
        tokenizer_version="bge-m3-sentencepiece-v1",
    )

    assert loaded is None


def test_optional_checkpoint_does_not_reuse_previous_v2_sanitizer_identity(
    tmp_path: Path,
) -> None:
    root = _private_root(tmp_path)
    prepared = _prepared()
    old_identity = {
        "componentScope": "OA112",
        "parserVersion": "1.1.0",
        "rawContentSha256": prepared.document.raw_content_sha256,
        "sanitizerVersion": "public-pii-v2-rechunk",
        "schemaVersion": 2,
        "sourceRevisionId": prepared.document.source_revision_id,
        "tokenizerVersion": "bge-m3-sentencepiece-v1",
    }
    old_key = hashlib.sha256(
        json.dumps(old_identity, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    derived = root / "derived-ir"
    derived.mkdir(mode=0o700)
    (derived / ".tmp").mkdir(mode=0o700)
    scope = derived / "oa112"
    scope.mkdir(mode=0o700)
    old_leaf = scope / f"{old_key}.json"
    old_leaf.write_text("{}", encoding="utf-8")
    old_leaf.chmod(0o600)

    loaded = load_optional_public_voyage_checkpoint(
        local_corpus_root=root,
        component_scope="OA112",
        expected_raw_content_sha256=prepared.document.raw_content_sha256,
        expected_source_revision_id=prepared.document.source_revision_id,
        parser_version="1.1.0",
        tokenizer_version="bge-m3-sentencepiece-v1",
    )

    assert loaded is None


def test_checkpoint_writer_reuses_identical_leaf_without_rewriting(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    first = write_public_voyage_checkpoint(
        local_corpus_root=root,
        parser_version="1.1.0",
        tokenizer_version="bge-m3-sentencepiece-v1",
        prepared=_prepared(),
        metadata=_metadata(),
    )
    before = first.path.stat().st_mtime_ns

    second = write_public_voyage_checkpoint(
        local_corpus_root=root,
        parser_version="1.1.0",
        tokenizer_version="bge-m3-sentencepiece-v1",
        prepared=_prepared(),
        metadata=_metadata(),
    )

    assert second.reused is True
    assert second.path.stat().st_mtime_ns == before


def test_checkpoint_scan_is_not_blocked_by_private_atomic_write_temp_leaf(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    written = write_public_voyage_checkpoint(
        local_corpus_root=root,
        parser_version="1.1.0",
        tokenizer_version="bge-m3-sentencepiece-v1",
        prepared=_prepared(),
        metadata=_metadata(),
    )
    temp_root = written.path.parent.parent / ".tmp"
    stale = temp_root / ".checkpoint-stale"
    stale.write_bytes(b"partial")
    stale.chmod(0o600)

    loaded = load_optional_public_voyage_checkpoint(
        local_corpus_root=root,
        component_scope="OA112",
        expected_raw_content_sha256=_prepared().document.raw_content_sha256,
        expected_source_revision_id=_prepared().document.source_revision_id,
        parser_version="1.1.0",
        tokenizer_version="bge-m3-sentencepiece-v1",
    )

    assert loaded is not None
    assert loaded.checkpoint_key == written.checkpoint_key
    assert oct(temp_root.stat().st_mode & 0o777) == "0o700"


def test_optional_checkpoint_uses_direct_key_without_scanning_unrelated_leaf(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    written = write_public_voyage_checkpoint(
        local_corpus_root=root,
        parser_version="1.1.0",
        tokenizer_version="bge-m3-sentencepiece-v1",
        prepared=_prepared(),
        metadata=_metadata(),
    )
    unrelated = written.path.parent / ("9" * 64 + ".json")
    unrelated.write_bytes(b"not-json")
    unrelated.chmod(0o600)

    loaded = load_optional_public_voyage_checkpoint(
        local_corpus_root=root,
        component_scope="OA112",
        expected_raw_content_sha256=_prepared().document.raw_content_sha256,
        expected_source_revision_id=_prepared().document.source_revision_id,
        parser_version="1.1.0",
        tokenizer_version="bge-m3-sentencepiece-v1",
    )

    assert loaded is not None
    assert loaded.checkpoint_key == written.checkpoint_key


def test_checkpoint_rejects_tamper_symlink_and_identity_drift(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    receipt = write_public_voyage_checkpoint(
        local_corpus_root=root,
        parser_version="1.1.0",
        tokenizer_version="bge-m3-sentencepiece-v1",
        prepared=_prepared(),
        metadata=_metadata(),
    )
    receipt.path.chmod(0o600)
    decoded = json.loads(receipt.path.read_text(encoding="utf-8"))
    decoded["payload"]["document"]["chunks"][0]["canonicalText"] = "tampered"
    receipt.path.write_text(json.dumps(decoded), encoding="utf-8")
    receipt.path.chmod(0o600)

    with pytest.raises(RagV2VoyageCheckpointError, match="VOYAGE_CHECKPOINT_DIGEST"):
        load_public_voyage_checkpoint(
            local_corpus_root=root,
            component_scope="OA112",
            expected_raw_content_sha256=_prepared().document.raw_content_sha256,
            expected_source_revision_id=_prepared().document.source_revision_id,
            parser_version="1.1.0",
            tokenizer_version="bge-m3-sentencepiece-v1",
        )

    receipt.path.unlink()
    os.symlink(tmp_path / "elsewhere", receipt.path)
    with pytest.raises(RagV2VoyageCheckpointError, match="VOYAGE_CHECKPOINT_BOUNDARY"):
        load_public_voyage_checkpoint(
            local_corpus_root=root,
            component_scope="OA112",
            expected_raw_content_sha256=_prepared().document.raw_content_sha256,
            expected_source_revision_id=_prepared().document.source_revision_id,
            parser_version="1.1.0",
            tokenizer_version="bge-m3-sentencepiece-v1",
        )


def _private_root(tmp_path: Path) -> Path:
    root = tmp_path / "local-corpus"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return root


def _prepared() -> RagV2PreparedPublicDocument:
    text = "checkpoint canonical text"
    text_sha = hashlib.sha256(text.encode()).hexdigest()
    chunk = RagV2CanonicalDocumentChunk(
        chunk_id="rag_v2_chk_" + "1" * 32,
        document_id="doc_oa_checkpoint_001",
        sequence=1,
        heading_path=("Heading",),
        locator={"page": 1},
        canonical_text=text,
        canonical_text_sha256=text_sha,
        token_count=4,
        contains_table=False,
    )
    document = RagV2DocumentMaterialization(
        document_id=chunk.document_id,
        source_scope="OA112",
        source_id="src_oa_checkpoint_001",
        source_revision_id="srv_oa_checkpoint_001",
        raw_content_sha256="a" * 64,
        normalized_content_sha256="b" * 64,
        external_processing_eligible=True,
        chunks=(chunk,),
    )
    return RagV2PreparedPublicDocument(
        document=document,
        embedding_inputs=(
            RagEmbeddingInput(
                chunk_revision_id=chunk.chunk_id,
                embedding_profile_id="voyage_context_4_1024_v1",
                text=text,
                embedding_input_hash="c" * 64,
                context_set_hash="d" * 64,
            ),
        ),
        source_revision_sha256="e" * 64,
        document_ir={
            "parserEvidence": {"parserVersion": "1.1.0"},
            "sourceId": document.source_id,
            "sourceRevisionId": document.source_revision_id,
        },
    )


def _metadata() -> PublicVoyageSourceMetadata:
    return PublicVoyageSourceMetadata(
        citation_title="Fixture",
        retrieval_topics=("topic",),
        canonical_https_url="https://example.com/fixture.pdf",
        source_card_sha256=None,
        machine_fetch_allowed=True,
        local_processing_allowed=True,
        external_embedding_allowed=True,
        external_generation_allowed=True,
        oa_track_id="portfolio_theory",
        oa_source_card={"sourceId": "src_oa_checkpoint_001"},
        license_evidence_sha256="f" * 64,
        access_evidence_sha256="1" * 64,
    )
