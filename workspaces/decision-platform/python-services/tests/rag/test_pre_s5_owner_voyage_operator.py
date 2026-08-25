from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.rag.pre_s5_owner_voyage_operator import (
    OWNER_VOYAGE_SYNTHETIC_FORMATS,
    OwnerVoyageOperatorError,
    author_owner_voyage_manifest,
    load_owner_voyage_batch_control,
)


def test_owner_batch_control_requires_exact_nine_formats_and_unique_tickets(
    tmp_path: Path,
) -> None:
    payload = _control_payload(tmp_path)
    _write_control(tmp_path, payload)

    control = load_owner_voyage_batch_control(local_root=tmp_path)

    assert (
        tuple(document.format_id for document in control.documents)
        == OWNER_VOYAGE_SYNTHETIC_FORMATS
    )
    assert len({document.import_ticket_id for document in control.documents}) == 9
    assert {document.embedding_profile_id for document in control.documents} == {
        "voyage_context_4_1024_v1"
    }


@pytest.mark.parametrize("mutation", ("missing", "duplicate_ticket", "wrong_profile"))
def test_owner_batch_control_rejects_non_exact_batch_before_provider(
    tmp_path: Path,
    mutation: str,
) -> None:
    payload = _control_payload(tmp_path)
    documents = payload["documents"]
    assert isinstance(documents, list)
    if mutation == "missing":
        documents.pop()
    elif mutation == "duplicate_ticket":
        documents[1]["ticketId"] = documents[0]["ticketId"]
    else:
        documents[0]["embeddingProfileId"] = "bge_m3_local_1024_v1"
    _write_control(tmp_path, payload)

    with pytest.raises(OwnerVoyageOperatorError, match="OWNER_VOYAGE_BATCH_CONTROL_INVALID"):
        load_owner_voyage_batch_control(local_root=tmp_path)


def test_owner_batch_control_rejects_hardlinked_record(tmp_path: Path) -> None:
    payload = _control_payload(tmp_path)
    path = _write_control(tmp_path, payload)
    os.link(path, tmp_path / "control" / "alias.json")

    with pytest.raises(OwnerVoyageOperatorError, match="OWNER_VOYAGE_BATCH_CONTROL_BOUNDARY"):
        load_owner_voyage_batch_control(local_root=tmp_path)


def test_owner_manifest_is_content_free_and_binds_one_physical_call(tmp_path: Path) -> None:
    manifest_path = tmp_path / "control" / "owner-voyage-manifest.v1.json"
    manifest_sha256 = author_owner_voyage_manifest(
        output_path=manifest_path,
        head_commit="1" * 40,
        tree_object="2" * 40,
        ci_digest="3" * 64,
        security_digest="4" * 64,
        plan_sha256="5" * 64,
        batch_manifest_sha256="6" * 64,
        owner_scope_sha256="7" * 64,
        ticket_set_sha256="8" * 64,
        tokenizer_sha256="9" * 64,
        packet_sha256="a" * 64,
        document_count=9,
        chunk_count=9,
        token_count=90,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    raw = manifest_path.read_text(encoding="utf-8")
    value = json.loads(raw)
    assert len(manifest_sha256) == 64
    assert value["documentCount"] == 9
    assert value["physicalCallCap"] == 1
    assert value["retryCount"] == 0
    assert value["rawArtifactCount"] == 0
    assert "usr_" not in raw
    assert "rti_" not in raw
    assert "doc_" not in raw
    assert manifest_path.stat().st_mode & 0o777 == 0o600


def test_owner_manifest_rejects_symlinked_control_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    os.symlink(outside, tmp_path / "control")

    with pytest.raises(OwnerVoyageOperatorError, match="OWNER_VOYAGE_MANIFEST_BOUNDARY"):
        author_owner_voyage_manifest(
            output_path=tmp_path / "control" / "owner-voyage-manifest.v1.json",
            head_commit="1" * 40,
            tree_object="2" * 40,
            ci_digest="3" * 64,
            security_digest="4" * 64,
            plan_sha256="5" * 64,
            batch_manifest_sha256="6" * 64,
            owner_scope_sha256="7" * 64,
            ticket_set_sha256="8" * 64,
            tokenizer_sha256="9" * 64,
            packet_sha256="a" * 64,
            document_count=9,
            chunk_count=9,
            token_count=90,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    assert not (outside / "owner-voyage-manifest.v1.json").exists()


def _control_payload(root: Path) -> dict[str, object]:
    now = datetime.now(UTC)
    documents = []
    extensions = ("pdf", "docx", "pptx", "xlsx", "html", "md", "txt", "png", "jpg")
    for index, (format_id, extension) in enumerate(
        zip(OWNER_VOYAGE_SYNTHETIC_FORMATS, extensions, strict=True),
        start=1,
    ):
        documents.append(
            {
                "approvedRoot": str(root / "documents"),
                "documentId": f"doc_owner_voyage_synthetic_{index:02d}",
                "embeddingProfileId": "voyage_context_4_1024_v1",
                "formatId": format_id,
                "languageTags": ["ko"],
                "relativePath": f"safe-{index:02d}.{extension}",
                "retrievalTopics": ["RISK"],
                "sanitizedDisplayName": f"Synthetic {index:02d}",
                "sourceId": f"src_owner_voyage_synthetic_{index:02d}",
                "sourceRevisionId": f"srv_owner_voyage_synthetic_{index:02d}",
                "ticketId": f"rti_{index:032x}",
            }
        )
    return {
        "contractId": "pre-s5-owner-voyage-batch-control/v1",
        "documents": documents,
        "expiresAt": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "issuedAt": now.isoformat().replace("+00:00", "Z"),
        "ownerUserId": "usr_demo_user",
        "schemaVersion": 1,
    }


def _write_control(root: Path, payload: dict[str, object]) -> Path:
    control = root / "control"
    control.mkdir(mode=0o700)
    os.chmod(control, 0o700)
    path = control / "owner-voyage-batch-control.v1.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    return path
