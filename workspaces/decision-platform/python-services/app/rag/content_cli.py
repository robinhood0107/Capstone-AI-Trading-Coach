from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from app.rag.bge_acquisition import DEFAULT_MODEL_ROOT
from app.rag.bge_runtime import BgeRuntimeError, BgeStaticTokenizer, load_bge_onnx_embedder
from app.rag.local_document_parser import DocumentParseError, LocalDocumentParser
from app.rag.oa112_downloader import Oa112DownloadError, load_oa112_execution_binding
from app.rag.owner_file_io import OwnerFileIoError, read_owner_regular_file
from app.rag.pre_s5_provider_control import (
    PreS5ProviderActivationError,
    PreS5ProviderBinding,
    load_pre_s5_voyage_document_batch_activation,
    resolve_voyage_api_key,
)
from app.rag.pre_s5_voyage_tokenizer import (
    LocalPreS5VoyageContext4Tokenizer,
    PreS5VoyageTokenizerError,
)
from app.rag.pre_s5_voyage_transport import (
    PreS5VoyageContext4Transport,
    PreS5VoyageTransportError,
    UrllibPreS5VoyageHttpSender,
)
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
    prepare_owner_document_for_embedding,
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
from app.rag.rag_v2_owner_voyage_import import (
    OwnerVoyageAttemptLease,
    OwnerVoyageImportError,
    OwnerVoyageImportPlan,
    OwnerVoyageImportReceipt,
    PsycopgOwnerVoyageRepository,
    RagV2OwnerVoyageImportExecutor,
    build_owner_voyage_import_item,
    build_owner_voyage_import_plan,
)
from app.rag.rag_v2_owner_overlay import (
    OwnerOverlayError,
    PsycopgRagV2OwnerOverlayRepository,
    RagV2OwnerOverlayReceipt,
)


_IMPORT_COMMANDS: Final = {
    "import-auto",
    "import-cpu",
    "import-intel-gpu",
    "import-nvidia-gpu",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OWNER_VOYAGE_MANIFEST_RELATIVE_PATH = "control/owner-voyage-manifest.v1.json"


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
    """user-selected profile만 실행하고 provider 간 자동 fallback은 만들지 않는다."""

    try:
        control = load_pending_owner_import_control(local_root=_local_root())
        writer_database_dsn = os.environ.get("CAPSTONE_RAG_WRITER_DATABASE_DSN", "").strip()
        if not writer_database_dsn:
            raise OwnerBgeStagingError("OWNER_BGE_STAGE_DATABASE_DSN")
        admin_database_dsn = os.environ.get("CAPSTONE_RAG_ADMIN_DATABASE_DSN", "").strip()
        if not admin_database_dsn:
            raise OwnerOverlayError("OWNER_OVERLAY_DATABASE_DSN")
        if control.embedding_profile_id == "bge_m3_local_1024_v1":
            materialized = _materialize_owner_import(control=control)
            bge_staging_receipt = PsycopgRagV2OwnerBgeStagingRepository(
                database_dsn=writer_database_dsn
            ).stage(
                owner_user_id=control.owner_user_id,
                import_ticket_id=control.import_ticket_id,
                materialized=materialized,
                metadata=OwnerBgeStagingMetadata(
                    sanitized_display_name=control.sanitized_display_name,
                    retrieval_topics=control.retrieval_topics,
                ),
            )
            bge_overlay_receipt = PsycopgRagV2OwnerOverlayRepository(
                database_dsn=admin_database_dsn
            ).prepare_and_activate(owner_user_id=control.owner_user_id)
            embedding_profile_id = bge_staging_receipt.embedding_profile_id
            overlay_receipt = bge_overlay_receipt
        elif control.embedding_profile_id == "voyage_context_4_1024_v1":
            voyage_staging_receipt, voyage_overlay_receipt = _execute_owner_voyage_import(
                control=control
            )
            embedding_profile_id = voyage_staging_receipt.embedding_profile_id
            overlay_receipt = voyage_overlay_receipt
        else:  # pragma: no cover - closed local-control decoder가 먼저 거부한다.
            raise RagV2LocalImportControlError("LOCAL_IMPORT_CONTROL_INVALID")
    except RagV2LocalImportControlError:
        return _failure("LOCAL_IMPORT_CONTROL_REQUIRED")
    except (BgeRuntimeError, DocumentParseError, RagV2BgeMaterializationError):
        return _failure("OWNER_DOCUMENT_MATERIALIZATION_FAILED")
    except (
        Oa112DownloadError,
        OwnerBgeStagingError,
        OwnerFileIoError,
        OwnerVoyageImportError,
        PreS5ProviderActivationError,
        PreS5VoyageTokenizerError,
        PreS5VoyageTransportError,
    ):
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
            "embeddingProfileId": embedding_profile_id,
            "state": overlay_receipt.state,
        }
    )
    return 0


def _materialize_owner_import(
    *,
    control: RagV2OwnerImportControl,
) -> RagV2BgeMaterializedOwnerDocument:
    """명시적으로 BGE를 고른 import만 pinned local runtime에서 처리한다."""

    if control.embedding_profile_id != "bge_m3_local_1024_v1":
        raise RagV2BgeMaterializationError("BGE_PROFILE_REQUIRED")

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
            embedding_profile_id=control.embedding_profile_id,
        ),
    )


def _execute_owner_voyage_import(
    *,
    control: RagV2OwnerImportControl,
) -> tuple[OwnerVoyageImportReceipt, RagV2OwnerOverlayReceipt]:
    """exact local manifest와 DB lease가 일치할 때만 one-shot owner Voyage import를 실행한다."""

    if control.embedding_profile_id != "voyage_context_4_1024_v1":
        raise OwnerVoyageImportError("OWNER_VOYAGE_PROFILE_REQUIRED")
    local_root = _local_root()
    tokenizer_root = _voyage_tokenizer_root()
    tokenizer_sha256 = _required_sha256_environment(
        "CAPSTONE_RAG_VOYAGE_TOKENIZER_SHA256"
    )
    voyage_tokenizer = LocalPreS5VoyageContext4Tokenizer.from_local_root(
        local_root=tokenizer_root,
        expected_sha256=tokenizer_sha256,
    )
    canonical_tokenizer = BgeStaticTokenizer.from_file(
        _bge_packet_root() / "onnx" / "tokenizer.json"
    )
    prepared = prepare_owner_document_for_embedding(
        parser=LocalDocumentParser(),
        tokenizer=canonical_tokenizer,
        request=RagV2OwnerDocumentRequest(
            approved_root=control.approved_root,
            relative_path=control.relative_path,
            document_id=control.document_id,
            source_id=control.source_id,
            source_revision_id=control.source_revision_id,
            language_tags=control.language_tags,
            embedding_profile_id=control.embedding_profile_id,
        ),
        # DB attempt reservation rechecks the current v2 consent immediately before outbound.
        external_processing_authorized=True,
    )
    item = build_owner_voyage_import_item(
        import_ticket_id=control.import_ticket_id,
        prepared=prepared,
        metadata=OwnerBgeStagingMetadata(
            sanitized_display_name=control.sanitized_display_name,
            retrieval_topics=control.retrieval_topics,
        ),
        tokenizer_version=f"voyage-context-4-{tokenizer_sha256[:16]}",
    )
    plan = build_owner_voyage_import_plan(
        owner_user_id=control.owner_user_id,
        items=(item,),
        token_counter=voyage_tokenizer,
    )
    binding = _owner_voyage_execution_binding(local_root=local_root)
    manifest_sha256, manifest = _load_owner_voyage_manifest(
        local_root=local_root,
        plan=plan,
        binding=binding,
    )
    activation = load_pre_s5_voyage_document_batch_activation(
        local_root=local_root,
        binding=binding,
        batch_plan_sha256=plan.plan_sha256,
        batch_id=plan.batch.batch_id,
        batch_manifest_sha256=plan.batch.batch_manifest_sha256,
        batch_ordinal=plan.batch.batch_ordinal,
        batch_count=plan.batch.batch_count,
        token_count=plan.batch.token_count,
        chunk_count=plan.batch.chunk_count,
        group_count=plan.batch.group_count,
        estimated_response_bytes=plan.batch.estimated_response_bytes,
    )
    if manifest.get("packetSha256") != activation.packet_sha256:
        raise OwnerVoyageImportError("OWNER_VOYAGE_MANIFEST_BINDING")
    writer_database_dsn = os.environ.get("CAPSTONE_RAG_WRITER_DATABASE_DSN", "").strip()
    repository = PsycopgOwnerVoyageRepository(database_dsn=writer_database_dsn)
    lease = OwnerVoyageAttemptLease(
        repository=repository,
        plan=plan,
        packet_sha256=activation.packet_sha256,
        approval_manifest_sha256=manifest_sha256,
        nonce_sha256=activation.nonce_sha256,
    )
    transport = PreS5VoyageContext4Transport(
        activation=activation,
        api_key=resolve_voyage_api_key(os.environ),
        lease=lease,
        token_counter=voyage_tokenizer,
        sender=UrllibPreS5VoyageHttpSender(),
    )
    receipt = RagV2OwnerVoyageImportExecutor(repository=lease).execute(
        plan=plan,
        transport=transport,
    )
    admin_database_dsn = os.environ.get("CAPSTONE_RAG_ADMIN_DATABASE_DSN", "").strip()
    overlay = PsycopgRagV2OwnerOverlayRepository(
        database_dsn=admin_database_dsn
    ).prepare_and_activate(owner_user_id=control.owner_user_id)
    return receipt, overlay


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
            delete_ticket_id=control.delete_ticket_id,
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


def _voyage_tokenizer_root() -> Path:
    value = os.environ.get("CAPSTONE_RAG_VOYAGE_TOKENIZER_ROOT", "").strip()
    root = Path(value) if value else _local_root()
    if not root.is_absolute() or ".." in root.parts:
        raise OwnerVoyageImportError("OWNER_VOYAGE_TOKENIZER_ROOT")
    return root


def _required_sha256_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if _SHA256.fullmatch(value) is None:
        raise OwnerVoyageImportError("OWNER_VOYAGE_ENVIRONMENT")
    return value


def _owner_voyage_execution_binding(*, local_root: Path) -> PreS5ProviderBinding:
    evidence_path = os.environ.get(
        "CAPSTONE_RAG_OWNER_VOYAGE_EXECUTION_EVIDENCE",
        "pre-s5-voyage-execution-evidence.v1.json",
    ).strip()
    evidence = load_oa112_execution_binding(
        approved_root=local_root,
        relative_path=evidence_path,
        repository_root=Path(__file__).resolve().parents[5],
    )
    return PreS5ProviderBinding(
        head_commit=evidence.head_sha,
        tree_object=evidence.tree_sha256,
        ci_digest=evidence.ci_digest,
        security_digest=evidence.security_digest,
    )


def _load_owner_voyage_manifest(
    *,
    local_root: Path,
    plan: OwnerVoyageImportPlan,
    binding: PreS5ProviderBinding,
) -> tuple[str, dict[str, object]]:
    """approved manifest는 content-free exact plan/packet/binding만 포함하고 TTL 안에서 한 번 읽는다."""

    result = read_owner_regular_file(
        approved_root=local_root,
        relative_path=_OWNER_VOYAGE_MANIFEST_RELATIVE_PATH,
        max_bytes=64 * 1024,
    )
    try:
        value = json.loads(result.content.decode("utf-8", errors="strict"))
        if not isinstance(value, dict):
            raise OwnerVoyageImportError("OWNER_VOYAGE_MANIFEST_INVALID")
        expires_at = datetime.fromisoformat(
            str(value.get("expiresAt", "")).replace("Z", "+00:00")
        )
    except OwnerVoyageImportError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise OwnerVoyageImportError("OWNER_VOYAGE_MANIFEST_INVALID") from error
    expected_approval = os.environ.get(
        "PRE_S5_OWNER_VOYAGE_SYNTHETIC_MANIFEST_SHA256", ""
    ).strip()
    if (
        set(value)
        != {
            "approvalScope",
            "batchManifestSha256",
            "binding",
            "chunkCount",
            "documentCount",
            "embeddingProfileId",
            "expiresAt",
            "operation",
            "ownerScopeSha256",
            "packetSha256",
            "physicalCallCap",
            "planSha256",
            "rawArtifactCount",
            "retryCount",
            "schemaVersion",
            "ticketSetSha256",
            "tokenCount",
            "tokenizerSha256",
        }
        or expected_approval != result.content_sha256
        or expires_at.tzinfo is None
        or datetime.now(UTC) >= expires_at.astimezone(UTC)
        or value.get("schemaVersion") != 1
        or value.get("approvalScope") != "PRE_S5_OWNER_VOYAGE_SYNTHETIC_ONE_SHOT"
        or value.get("operation") != "OWNER_VOYAGE_DOCUMENT_IMPORT"
        or value.get("embeddingProfileId") != "voyage_context_4_1024_v1"
        or value.get("planSha256") != plan.plan_sha256
        or value.get("batchManifestSha256") != plan.batch.batch_manifest_sha256
        or value.get("ownerScopeSha256") != plan.owner_scope_sha256
        or value.get("ticketSetSha256") != plan.ticket_set_sha256
        or value.get("tokenizerSha256") != plan.tokenizer_sha256
        or value.get("documentCount") != len(plan.items)
        or value.get("chunkCount") != plan.batch.chunk_count
        or value.get("tokenCount") != plan.batch.token_count
        or value.get("physicalCallCap") != 1
        or value.get("retryCount") != 0
        or value.get("rawArtifactCount") != 0
        or value.get("binding")
        != {
            "ciDigest": binding.ci_digest,
            "headCommit": binding.head_commit,
            "securityDigest": binding.security_digest,
            "treeObject": binding.tree_object,
        }
        or _SHA256.fullmatch(str(value.get("packetSha256", ""))) is None
    ):
        raise OwnerVoyageImportError("OWNER_VOYAGE_MANIFEST_BINDING")
    return result.content_sha256, value


def _failure(code: str) -> int:
    _emit({"code": code, "state": "FAILED"})
    return 2


def _emit(value: dict[str, object]) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
