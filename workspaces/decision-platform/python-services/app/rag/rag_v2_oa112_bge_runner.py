from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.rag.ingest_pipeline import RagTokenizer
from app.rag.local_document_parser import LocalDocumentParser
from app.rag.oa112_active_registry import Oa112ActiveRegistry, Oa112RegistryEntry
from app.rag.oa112_downloader import oa112_raw_cache_filename
from app.rag.oa_release_manifest import OA_TRACK_IDS
from app.rag.rag_v2_bge_materializer import (
    ApprovedDocumentParser,
    BgeDocumentEmbedder,
    RagV2BgeMaterializationError,
    RagV2PublicDocumentRequest,
    materialize_public_bge_document,
)
from app.rag.rag_v2_public_bge_staging import (
    PublicBgeSourceMetadata,
    RagV2PublicBgeComponentContext,
    RagV2PublicBgeStagingError,
    build_public_bge_component_context,
)
from app.rag.rag_v2_public_bge_staging_repository import PublicBgeRecord

_BGE_PROFILE_ID = "bge_m3_local_1024_v1"


class RagV2Oa112BgeRunnerError(ValueError):
    """OA112 local cache→BGE component 경계가 registry/rights/IR 계약을 위반했다."""


@dataclass(frozen=True, slots=True)
class Oa112PublicBgeMaterialization:
    """in-memory OA112 component와 immutable writer context다.

    records는 raw text/vector를 포함하므로 writer transaction 외에 serialize하거나 local cache에 복사하지
    않는다. receipt는 registry/generation identity와 count만 노출한다.
    """

    records: tuple[PublicBgeRecord, ...]
    context: RagV2PublicBgeComponentContext
    registry_id: str
    registry_digest: str

    def content_free_receipt(self) -> dict[str, object]:
        """raw cache path, OA text, card body, vector를 제외한 component receipt를 반환한다."""

        return {
            "chunkCount": self.context.expected_chunk_count,
            "componentGenerationId": self.context.component_generation_id,
            "componentScope": self.context.component_scope,
            "embeddingProfileId": self.context.embedding_profile_id,
            "generationHash": self.context.generation_hash,
            "manifestHash": self.context.manifest_hash,
            "materializationRunId": self.context.materialization_run_id,
            "registryDigest": self.registry_digest,
            "registryId": self.registry_id,
            "sourceCount": self.context.expected_source_count,
        }


def materialize_oa112_public_bge_component(
    *,
    tokenizer: RagTokenizer,
    embedder: BgeDocumentEmbedder,
    registry: Oa112ActiveRegistry,
    local_cache_root: Path,
    parser: ApprovedDocumentParser | None = None,
) -> Oa112PublicBgeMaterialization:
    """exactly 112 approved cached OA sources를 local parser/BGE component로 materialize한다.

    caller는 packet-bound downloader가 완성한 private `oa-raw` cache root만 지정한다. 이 함수는
    registry download, external embedding/generation, active public pointer activation을 만들지 않으며,
    one source라도 raw hash/MIME/safety drift면 remaining source materialization을 시작하지 않고 닫는다.
    """

    entries = tuple(registry.active_entries)
    validate_oa112_active_entries(entries)
    if not local_cache_root.is_absolute():
        raise RagV2Oa112BgeRunnerError("OA112_LOCAL_CACHE_ROOT")
    approved_root = local_cache_root / "oa-raw"
    active_parser = parser or LocalDocumentParser()
    records: list[PublicBgeRecord] = []
    for entry in entries:
        try:
            materialized = materialize_public_bge_document(
                parser=active_parser,
                tokenizer=tokenizer,
                embedder=embedder,
                request=oa112_public_document_request(
                    entry,
                    approved_root=approved_root,
                    embedding_profile_id=_BGE_PROFILE_ID,
                ),
            )
            records.append((materialized, oa112_public_source_metadata(entry)))
        except RagV2BgeMaterializationError as error:
            raise RagV2Oa112BgeRunnerError("OA112_BGE_MATERIALIZATION") from error

    try:
        context = build_public_bge_component_context(tuple(records))
    except RagV2PublicBgeStagingError as error:
        raise RagV2Oa112BgeRunnerError("OA112_BGE_COMPONENT_CONTEXT") from error
    if context.component_scope != "OA112" or context.expected_source_count != 112:
        raise RagV2Oa112BgeRunnerError("OA112_BGE_COMPONENT_CONTEXT")
    return Oa112PublicBgeMaterialization(
        records=tuple(records),
        context=context,
        registry_id=registry.registry_id,
        registry_digest=registry.registry_digest,
    )


def validate_oa112_active_entries(entries: tuple[Oa112RegistryEntry, ...]) -> None:
    """materialization 전 14×8 membership와 all-four permission을 locally fail-close한다."""

    if len(entries) != 112 or len({entry.source_id for entry in entries}) != 112:
        raise RagV2Oa112BgeRunnerError("OA112_ACTIVE_MEMBERSHIP")
    for track_id in OA_TRACK_IDS:
        if sum(entry.track_id == track_id for entry in entries) != 8:
            raise RagV2Oa112BgeRunnerError("OA112_ACTIVE_TRACK_DISTRIBUTION")
    if any(
        not all(
            (
                entry.machine_fetch_allowed,
                entry.local_processing_allowed,
                entry.external_embedding_allowed,
                entry.external_generation_allowed,
            )
        )
        for entry in entries
    ):
        raise RagV2Oa112BgeRunnerError("OA112_RIGHTS_REQUIRED")


def oa112_public_document_request(
    entry: Oa112RegistryEntry,
    *,
    approved_root: Path,
    embedding_profile_id: str,
) -> RagV2PublicDocumentRequest:
    """registry entry와 fixed raw-cache leaf를 requested full-profile input으로 연결한다."""

    return RagV2PublicDocumentRequest(
        approved_root=approved_root,
        relative_path=oa112_raw_cache_filename(entry),
        document_id=entry.document_id,
        source_scope="OA112",
        source_id=entry.source_id,
        source_revision_id=entry.source_revision_id,
        language_tags=entry.language_tags,
        expected_raw_content_sha256=entry.raw_content_sha256,
        expected_mime_type=entry.mime_type,
        local_processing_allowed=entry.local_processing_allowed,
        external_embedding_allowed=entry.external_embedding_allowed,
        external_generation_allowed=entry.external_generation_allowed,
        embedding_profile_id=embedding_profile_id,
    )


def oa112_public_source_metadata(entry: Oa112RegistryEntry) -> PublicBgeSourceMetadata:
    """registry's verified OA card and all-four permission proof만 staging metadata로 투영한다."""

    return PublicBgeSourceMetadata(
        citation_title=entry.title,
        retrieval_topics=entry.retrieval_topics,
        canonical_https_url=entry.canonical_url,
        source_card_sha256=None,
        oa_track_id=entry.track_id,
        source_card=_copy_source_card(entry.source_card),
        license_evidence_sha256=entry.license_evidence_sha256,
        access_evidence_sha256=entry.access_evidence_sha256,
        machine_fetch_allowed=entry.machine_fetch_allowed,
        local_processing_allowed=entry.local_processing_allowed,
        external_embedding_allowed=entry.external_embedding_allowed,
        external_generation_allowed=entry.external_generation_allowed,
    )


def _copy_source_card(source_card: dict[str, object]) -> dict[str, object]:
    """registry object mutation이 vector materialization 뒤 rights projection을 바꾸지 못하게 복제한다."""

    copied = json.loads(json.dumps(source_card, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    if not isinstance(copied, dict):  # pragma: no cover - registry dataclass contract keeps it a dict.
        raise RagV2Oa112BgeRunnerError("OA112_SOURCE_CARD_METADATA")
    return copied
