from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from app.rag.bge_acquisition import DEFAULT_MODEL_ROOT
from app.rag.bge_runtime import BgeRuntimeError, BgeStaticTokenizer, load_bge_onnx_embedder
from app.rag.local_document_parser import DocumentParseError, LocalDocumentParser
from app.rag.rag_v2_local_cache import RagV2LocalCacheError, clean_local_rag_cache
from app.rag.oa_release_manifest import (
    OaReleaseManifestError,
    load_oa_release_manifest,
)
from app.rag.rag_v2_bge_materializer import (
    RagV2BgeMaterializationError,
    RagV2BgeMaterializedOwnerDocument,
    RagV2OwnerDocumentRequest,
    materialize_owner_bge_document,
)
from app.rag.rag_v2_local_import_control import (
    RagV2LocalDeleteControlError,
    RagV2LocalImportControlError,
    RagV2OwnerImportControl,
    load_pending_owner_delete_control,
    load_pending_owner_import_control,
)
from app.rag.rag_v2_owner_bge_deletion import (
    OwnerBgeDeletionError,
    PsycopgRagV2OwnerBgeDeletionRepository,
)
from app.rag.rag_v2_owner_bge_staging import (
    OwnerBgeStagingError,
    OwnerBgeStagingMetadata,
    PsycopgRagV2OwnerBgeStagingRepository,
)
from app.rag.rag_v2_owner_overlay import (
    OwnerOverlayError,
    PsycopgRagV2OwnerOverlayRepository,
)


_IMPORT_COMMANDS: Final = {
    "import-auto",
    "import-cpu",
    "import-intel-gpu",
    "import-nvidia-gpu",
}


def main(argv: Sequence[str] | None = None) -> int:
    """Windows BAT가 호출하는 content command를 stable JSON으로 중계한다.

    setup은 public OA release manifest의 bounded metadata만 검증한다. owner import는 argv가 아닌
    fixed local 0600 control record에서 ticket/path/owner identity를 읽어 local BGE staging과
    immutable owner bundle activation을 수행한다. public OA download와 provider transport는 여전히
    별도 gate이며, active public BGE base가 없으면 owner bundle도 fail-closed한다.
    """

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if not arguments:
        return _failure("CONTENT_COMMAND_INVALID")
    command = arguments[0]
    if command == "status" and len(arguments) == 1:
        return _status()
    if command == "setup" and len(arguments) == 1:
        return _setup_public_oa_release()
    if command in _IMPORT_COMMANDS:
        if len(arguments) != 1:
            return _failure("CONTENT_COMMAND_INVALID")
        return _import_owner_document()
    if command == "remove-document":
        if len(arguments) != 1:
            return _failure("CONTENT_COMMAND_INVALID")
        return _remove_owner_document()
    if command == "cache-clean" and len(arguments) == 1:
        return _clean_local_cache()
    return _failure("CONTENT_COMMAND_INVALID")


def _setup_public_oa_release() -> int:
    try:
        release = load_oa_release_manifest(path=_operator_manifest_path())
    except OaReleaseManifestError:
        return _failure("CONTENT_RELEASE_NOT_INSTALLED")
    _emit(
        {
            "code": "OA_RELEASE_MANIFEST_VERIFIED",
            "progressPercent": 1,
            "publicCorpusVersion": release.public_corpus_version,
            "sourceCount": release.source_count,
            "state": "BUILDING",
        }
    )
    return 0


def _status() -> int:
    try:
        release = load_oa_release_manifest(path=_operator_manifest_path())
    except OaReleaseManifestError:
        _emit(
            {
                "code": "CONTENT_SETUP_REQUIRED",
                "progressPercent": 0,
                "state": "BUILDING",
            }
        )
        return 0
    _emit(
        {
            "code": "OA_RELEASE_MANIFEST_AVAILABLE",
            "progressPercent": 0,
            "publicCorpusVersion": release.public_corpus_version,
            "sourceCount": release.source_count,
            "state": "CORE_READY",
        }
    )
    return 0


def _operator_manifest_path() -> Path | None:
    value = os.environ.get("CAPSTONE_RAG_OA_MANIFEST_PATH")
    if not value:
        return None
    return Path(value)


def _import_owner_document() -> int:
    """one-time local control record를 BGE writer와 admin-only overlay activation에만 전달한다."""

    try:
        control = load_pending_owner_import_control(local_root=_local_root())
        writer_database_dsn = os.environ.get("CAPSTONE_RAG_WRITER_DATABASE_DSN", "").strip()
        if not writer_database_dsn:
            raise OwnerBgeStagingError("OWNER_BGE_STAGE_DATABASE_DSN")
        admin_database_dsn = os.environ.get("CAPSTONE_RAG_ADMIN_DATABASE_DSN", "").strip()
        if not admin_database_dsn:
            raise OwnerOverlayError("OWNER_OVERLAY_DATABASE_DSN")
        materialized = _materialize_owner_import(control=control)
        staging_receipt = PsycopgRagV2OwnerBgeStagingRepository(database_dsn=writer_database_dsn).stage(
            owner_user_id=control.owner_user_id,
            import_ticket_id=control.import_ticket_id,
            materialized=materialized,
            metadata=OwnerBgeStagingMetadata(
                sanitized_display_name=control.sanitized_display_name,
                retrieval_topics=control.retrieval_topics,
            ),
        )
        overlay_receipt = PsycopgRagV2OwnerOverlayRepository(
            database_dsn=admin_database_dsn
        ).prepare_and_activate(owner_user_id=control.owner_user_id)
    except RagV2LocalImportControlError:
        return _failure("LOCAL_IMPORT_CONTROL_REQUIRED")
    except (BgeRuntimeError, DocumentParseError, RagV2BgeMaterializationError):
        return _failure("OWNER_DOCUMENT_MATERIALIZATION_FAILED")
    except OwnerBgeStagingError:
        return _failure("OWNER_DOCUMENT_STAGING_FAILED")
    except OwnerOverlayError as error:
        if str(error) in {"OWNER_OVERLAY_NOT_READY", "OWNER_OVERLAY_CONFLICT"}:
            return _failure("OWNER_DOCUMENT_OVERLAY_PENDING")
        return _failure("OWNER_DOCUMENT_OVERLAY_UNAVAILABLE")

    _emit(
        {
            "bundleId": overlay_receipt.bundle_id,
            "chunkCount": overlay_receipt.chunk_count,
            "code": "OWNER_DOCUMENT_READY",
            "componentGenerationId": overlay_receipt.component_generation_id,
            "embeddingProfileId": staging_receipt.embedding_profile_id,
            "state": overlay_receipt.state,
        }
    )
    return 0


def _materialize_owner_import(
    *,
    control: RagV2OwnerImportControl,
) -> RagV2BgeMaterializedOwnerDocument:
    """exact local BGE packet과 safe parser만 사용하고 network/provider transport를 만들지 않는다."""

    packet_root = _bge_packet_root()
    tokenizer = BgeStaticTokenizer.from_file(packet_root / "onnx" / "tokenizer.json")
    embedder = load_bge_onnx_embedder(packet_root)
    return materialize_owner_bge_document(
        parser=LocalDocumentParser(),
        tokenizer=tokenizer,
        embedder=embedder,
        request=RagV2OwnerDocumentRequest(
            approved_root=control.approved_root,
            relative_path=control.relative_path,
            document_id=control.document_id,
            source_id=control.source_id,
            source_revision_id=control.source_revision_id,
            language_tags=control.language_tags,
            embedding_profile_id="bge_m3_local_1024_v1",
        ),
    )


def _remove_owner_document() -> int:
    """one-time local deletion record의 staged owner document만 hard-delete function에 전달한다."""

    try:
        control = load_pending_owner_delete_control(local_root=_local_root())
        database_dsn = os.environ.get("CAPSTONE_RAG_ADMIN_DATABASE_DSN", "").strip()
        if not database_dsn:
            raise OwnerBgeDeletionError("OWNER_BGE_DELETE_DATABASE_DSN")
        receipt = PsycopgRagV2OwnerBgeDeletionRepository(database_dsn=database_dsn).delete(
            owner_user_id=control.owner_user_id,
            document_id=control.document_id,
        )
    except RagV2LocalDeleteControlError:
        return _failure("LOCAL_DELETE_CONTROL_REQUIRED")
    except RagV2LocalImportControlError:
        return _failure("LOCAL_DELETE_CONTROL_REQUIRED")
    except OwnerBgeDeletionError as error:
        if str(error) == "OWNER_BGE_DELETE_BLOCKED":
            return _failure("OWNER_DOCUMENT_DELETE_BLOCKED")
        return _failure("OWNER_DOCUMENT_DELETE_UNAVAILABLE")

    _emit(
        {
            "code": (
                "OWNER_DOCUMENT_DELETED"
                if receipt.state == "DELETED"
                else "OWNER_DOCUMENT_ALREADY_ABSENT"
            ),
            "state": receipt.state,
        }
    )
    return 0


def _clean_local_cache() -> int:
    """fixed local cache set만 삭제하고 control record나 approved owner source는 보존한다."""

    try:
        receipt = clean_local_rag_cache(local_root=_local_root())
    except (RagV2LocalImportControlError, RagV2LocalCacheError):
        return _failure("LOCAL_CACHE_CLEAN_UNAVAILABLE")
    _emit(
        {
            "code": "LOCAL_CACHE_CLEARED",
            "removedEntries": receipt.removed_entries,
            "state": "READY",
        }
    )
    return 0


def _local_root() -> Path:
    value = os.environ.get("CAPSTONE_RAG_LOCAL_ROOT", "").strip()
    if not value:
        raise RagV2LocalImportControlError("LOCAL_IMPORT_CONTROL_BOUNDARY")
    root = Path(value)
    if not root.is_absolute():
        raise RagV2LocalImportControlError("LOCAL_IMPORT_CONTROL_BOUNDARY")
    return root


def _bge_packet_root() -> Path:
    value = os.environ.get("CAPSTONE_RAG_BGE_PACKET_ROOT", "").strip()
    if not value:
        return DEFAULT_MODEL_ROOT
    root = Path(value)
    if not root.is_absolute():
        raise BgeRuntimeError("BGE_PACKET_VERIFICATION_FAILED")
    return root


def _failure(code: str) -> int:
    _emit({"code": code, "state": "FAILED"})
    return 2


def _emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
