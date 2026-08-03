from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

import numpy as np

from app.rag.authorized_retrieval import ALLOWED_RAG_TOPICS
from app.rag.rag_v2_bge_materializer import RagV2BgeMaterializedPublicDocument

_BGE_PROFILE_ID = "bge_m3_local_1024_v1"
_SCOPE_COUNTS = {"EXACT30": 30, "OA112": 112}
_COMPONENT_SCOPE = Literal["EXACT30", "OA112"]
_GENERATION_ID = re.compile(r"^rgr_[0-9a-f]{32}$")
_RUN_ID = re.compile(r"^rgr_run_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ID = re.compile(r"^src_[a-z0-9][a-z0-9_-]{2,95}$")
_SOURCE_REVISION_ID = re.compile(r"^srv_[a-z0-9][a-z0-9_-]{2,95}$")
_DOCUMENT_ID = re.compile(r"^doc_[a-z0-9][a-z0-9_-]{10,95}$")
_CHUNK_ID = re.compile(r"^rag_v2_chk_[0-9a-f]{32}$")
_PATH_KEYS = frozenset(("originalPath", "rawPath", "absolutePath", "filePath"))
_OA_CARD_REQUIRED_KEYS = frozenset(
    {
        "accessEvidence",
        "activeOa112Eligible",
        "authors",
        "canonicalUrl",
        "canonicalUrlSha256",
        "contractId",
        "identifier",
        "licenseEvidenceDigest",
        "mimeType",
        "permissions",
        "rawContentSha256",
        "revision",
        "revisionDate",
        "schemaVersion",
        "sourceId",
        "sourceKind",
        "title",
    }
)


class RagV2PublicBgeStagingError(ValueError):
    """public immutable BGE staging payload 또는 component identity가 drift했음을 나타낸다."""


@dataclass(frozen=True, slots=True)
class PublicBgeSourceMetadata:
    """public source의 content-free citation/rights metadata다.

    canonical HTTPS locator와 OA source-card는 DB immutable evidence로만 전달한다. raw cache path,
    provider response, 원문 bytes는 이 metadata와 receipt 어디에도 들어가지 않는다.
    """

    citation_title: str
    retrieval_topics: tuple[str, ...]
    canonical_https_url: str
    oa_track_id: str | None
    source_card: Mapping[str, object] | None
    license_evidence_sha256: str | None
    access_evidence_sha256: str | None
    machine_fetch_allowed: bool
    local_processing_allowed: bool
    external_embedding_allowed: bool
    external_generation_allowed: bool


@dataclass(frozen=True, slots=True)
class RagV2PublicBgeComponentContext:
    """한 immutable public BGE component의 deterministic generation/run identity다."""

    component_scope: _COMPONENT_SCOPE
    component_generation_id: str
    materialization_run_id: str
    generation_hash: str
    manifest_hash: str
    expected_source_count: int
    expected_chunk_count: int
    embedding_profile_id: str
    member_digests: tuple[str, ...]


def build_public_bge_component_context(
    records: Sequence[tuple[RagV2BgeMaterializedPublicDocument, PublicBgeSourceMetadata]],
) -> RagV2PublicBgeComponentContext:
    """exact-30 또는 OA112 전체 materialization으로 deterministic component identity를 만든다.

    partial component는 stage/activation 후보가 될 수 없다. context는 exact member digest와 total
    chunk count를 bind하므로 later per-source resumable write도 source addition/subtraction을 감지한다.
    """

    selected = tuple(records)
    if not selected:
        raise RagV2PublicBgeStagingError("PUBLIC_BGE_COMPONENT_MEMBERSHIP")
    scope = selected[0][0].document.source_scope
    if scope not in _SCOPE_COUNTS:
        raise RagV2PublicBgeStagingError("PUBLIC_BGE_COMPONENT_SCOPE")
    expected_source_count = _SCOPE_COUNTS[scope]
    if len(selected) != expected_source_count:
        raise RagV2PublicBgeStagingError("PUBLIC_BGE_COMPONENT_MEMBERSHIP")

    validated: list[tuple[RagV2BgeMaterializedPublicDocument, PublicBgeSourceMetadata, str]] = []
    for materialized, metadata in selected:
        if materialized.document.source_scope != scope:
            raise RagV2PublicBgeStagingError("PUBLIC_BGE_COMPONENT_SCOPE")
        digest = _validate_public_record(materialized, metadata, scope=scope)
        validated.append((materialized, metadata, digest))
    ordered = tuple(sorted(validated, key=lambda value: value[0].document.source_id.encode("utf-8")))
    source_ids = tuple(value[0].document.source_id for value in ordered)
    source_revisions = tuple(value[0].document.source_revision_id for value in ordered)
    document_ids = tuple(value[0].document.document_id for value in ordered)
    member_digests = tuple(value[2] for value in ordered)
    if (
        len(set(source_ids)) != expected_source_count
        or len(set(source_revisions)) != expected_source_count
        or len(set(document_ids)) != expected_source_count
        or len(set(member_digests)) != expected_source_count
    ):
        raise RagV2PublicBgeStagingError("PUBLIC_BGE_COMPONENT_IDENTITY")

    expected_chunk_count = sum(len(value[0].document.chunks) for value in ordered)
    if expected_chunk_count < expected_source_count:
        raise RagV2PublicBgeStagingError("PUBLIC_BGE_COMPONENT_CHUNKS")
    manifest_hash = _canonical_hash(
        {
            "componentScope": scope,
            "embeddingProfileId": _BGE_PROFILE_ID,
            "members": list(member_digests),
            "schemaVersion": 1,
        }
    )
    generation_hash = _canonical_hash(
        {
            "componentScope": scope,
            "embeddingProfileId": _BGE_PROFILE_ID,
            "expectedChunkCount": expected_chunk_count,
            "expectedSourceCount": expected_source_count,
            "manifestHash": manifest_hash,
            "schemaVersion": 1,
        }
    )
    component_generation_id = f"rgr_{generation_hash[:32]}"
    materialization_run_id = "rgr_run_" + hashlib.sha256(
        f"rag-v2-public-bge-run|{component_generation_id}|{manifest_hash}".encode("utf-8")
    ).hexdigest()[:32]
    return RagV2PublicBgeComponentContext(
        component_scope=cast(_COMPONENT_SCOPE, scope),
        component_generation_id=component_generation_id,
        materialization_run_id=materialization_run_id,
        generation_hash=generation_hash,
        manifest_hash=manifest_hash,
        expected_source_count=expected_source_count,
        expected_chunk_count=expected_chunk_count,
        embedding_profile_id=_BGE_PROFILE_ID,
        member_digests=member_digests,
    )


def build_public_bge_staging_payload(
    record: tuple[RagV2BgeMaterializedPublicDocument, PublicBgeSourceMetadata],
    *,
    context: RagV2PublicBgeComponentContext,
) -> dict[str, object]:
    """one public source의 transient IR/text/vector를 writer function payload로 만든다.

    writer payload는 per-source 16 MiB DB boundary보다 앞에서 closed shape, unit vector, source card
    matching, path-free IR를 다시 검증한다. caller는 이 object를 process-local transaction에만 넘겨야
    하며 log/CLI/API/history로 serialize하면 안 된다.
    """

    materialized, metadata = record
    if (
        context.component_scope != materialized.document.source_scope
        or context.embedding_profile_id != _BGE_PROFILE_ID
        or _GENERATION_ID.fullmatch(context.component_generation_id) is None
        or _RUN_ID.fullmatch(context.materialization_run_id) is None
        or not _is_sha256(context.generation_hash)
        or not _is_sha256(context.manifest_hash)
        or context.expected_source_count != _SCOPE_COUNTS.get(context.component_scope)
        or context.expected_chunk_count < context.expected_source_count
    ):
        raise RagV2PublicBgeStagingError("PUBLIC_BGE_COMPONENT_CONTEXT")
    member_digest = _validate_public_record(
        materialized,
        metadata,
        scope=context.component_scope,
    )
    if member_digest not in set(context.member_digests):
        raise RagV2PublicBgeStagingError("PUBLIC_BGE_COMPONENT_MEMBERSHIP")

    document = materialized.document
    ordered_chunks = tuple(sorted(document.chunks, key=lambda value: value.sequence))
    embeddings_by_chunk = {embedding.chunk_id: embedding for embedding in materialized.embeddings}
    if (
        len(ordered_chunks) != len(embeddings_by_chunk)
        or tuple(chunk.sequence for chunk in ordered_chunks) != tuple(range(1, len(ordered_chunks) + 1))
    ):
        raise RagV2PublicBgeStagingError("PUBLIC_BGE_SOURCE_CHUNKS")
    chunks: list[dict[str, object]] = []
    embeddings: list[dict[str, object]] = []
    for chunk in ordered_chunks:
        embedding = embeddings_by_chunk.get(chunk.chunk_id)
        if embedding is None or embedding.context_set_hash is not None or not _is_sha256(embedding.embedding_input_hash):
            raise RagV2PublicBgeStagingError("PUBLIC_BGE_SOURCE_EMBEDDING")
        vector = np.asarray(embedding.embedding, dtype=np.float32)
        if (
            vector.shape != (1024,)
            or not np.isfinite(vector).all()
            or not math.isclose(float(np.linalg.norm(vector)), 1.0, rel_tol=0.0, abs_tol=1e-5)
        ):
            raise RagV2PublicBgeStagingError("PUBLIC_BGE_SOURCE_VECTOR")
        chunks.append(
            {
                "canonicalText": chunk.canonical_text,
                "canonicalTextSha256": chunk.canonical_text_sha256,
                "chunkId": chunk.chunk_id,
                "chunkOrdinal": chunk.sequence,
                "containsTable": chunk.contains_table,
                "headingPath": list(chunk.heading_path),
                "locator": dict(chunk.locator),
                "tokenCount": chunk.token_count,
            }
        )
        embeddings.append(
            {
                "chunkId": chunk.chunk_id,
                "embedding": [float(value) for value in vector],
                "embeddingInputHash": embedding.embedding_input_hash,
            }
        )
    canonical_text = "\n\n".join(chunk.canonical_text for chunk in ordered_chunks)
    canonical_text_sha256 = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
    document_ir = _copy_document_ir(materialized.document_ir)
    source = {
        "accessEvidenceSha256": metadata.access_evidence_sha256,
        "canonicalHttpsUrl": metadata.canonical_https_url,
        "canonicalText": canonical_text,
        "canonicalTextSha256": canonical_text_sha256,
        "chunks": chunks,
        "citationTitle": metadata.citation_title,
        "documentId": document.document_id,
        "documentIr": document_ir,
        "embeddings": embeddings,
        "externalEmbeddingAllowed": metadata.external_embedding_allowed,
        "externalGenerationAllowed": metadata.external_generation_allowed,
        "externalProcessingEligible": document.external_processing_eligible,
        "licenseEvidenceSha256": metadata.license_evidence_sha256,
        "localProcessingAllowed": metadata.local_processing_allowed,
        "machineFetchAllowed": metadata.machine_fetch_allowed,
        "mimeType": _required_ir_text(document_ir, "mimeType", maximum=128),
        "oaSourceCard": _copy_source_card(metadata.source_card),
        "oaTrackId": metadata.oa_track_id,
        "parserVersion": _parser_version(document_ir),
        "rawContentSha256": document.raw_content_sha256,
        "retrievalTopics": list(metadata.retrieval_topics),
        "sourceId": document.source_id,
        "sourceLocator": dict(ordered_chunks[0].locator),
        "sourceRevisionId": document.source_revision_id,
        "sourceRevisionSha256": materialized.source_revision_sha256,
        "tokenizerVersion": "bge-m3-5617a9f-tokenizer-400-600-v1",
    }
    payload = {
        "componentGenerationId": context.component_generation_id,
        "componentScope": context.component_scope,
        "embeddingProfileId": context.embedding_profile_id,
        "expectedChunkCount": context.expected_chunk_count,
        "expectedSourceCount": context.expected_source_count,
        "generationHash": context.generation_hash,
        "manifestHash": context.manifest_hash,
        "materializationRunId": context.materialization_run_id,
        "schemaVersion": 1,
        "source": source,
    }
    _assert_path_free(payload)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) > 16 * 1024 * 1024:
        raise RagV2PublicBgeStagingError("PUBLIC_BGE_SOURCE_PAYLOAD_BOUND")
    return payload


def _validate_public_record(
    materialized: RagV2BgeMaterializedPublicDocument,
    metadata: PublicBgeSourceMetadata,
    *,
    scope: str,
) -> str:
    document = materialized.document
    if (
        document.source_scope != scope
        or scope not in _SCOPE_COUNTS
        or not _DOCUMENT_ID.fullmatch(document.document_id)
        or not _SOURCE_ID.fullmatch(document.source_id)
        or not _SOURCE_REVISION_ID.fullmatch(document.source_revision_id)
        or not _is_sha256(document.raw_content_sha256)
        or not _is_sha256(document.normalized_content_sha256)
        or not _is_sha256(materialized.source_revision_sha256)
        or not document.chunks
        or len(document.chunks) != len(materialized.embeddings)
    ):
        raise RagV2PublicBgeStagingError("PUBLIC_BGE_SOURCE_IDENTITY")
    document_ir = _copy_document_ir(materialized.document_ir)
    if (
        document_ir.get("sourceId") != document.source_id
        or document_ir.get("sourceRevisionId") != document.source_revision_id
        or document_ir.get("rawContentSha256") != document.raw_content_sha256
        or document_ir.get("normalizedContentSha256") != document.normalized_content_sha256
        or _canonical_hash(document_ir) != materialized.source_revision_sha256
    ):
        raise RagV2PublicBgeStagingError("PUBLIC_BGE_SOURCE_IDENTITY")
    _required_ir_text(document_ir, "mimeType", maximum=128)
    _parser_version(document_ir)
    _validate_metadata(materialized, metadata, scope=scope, document_ir=document_ir)
    chunk_projection = []
    for chunk in sorted(document.chunks, key=lambda value: value.sequence):
        if (
            _CHUNK_ID.fullmatch(chunk.chunk_id) is None
            or not _is_sha256(chunk.canonical_text_sha256)
            or hashlib.sha256(chunk.canonical_text.encode("utf-8")).hexdigest() != chunk.canonical_text_sha256
            or not 1 <= chunk.token_count <= 600
        ):
            raise RagV2PublicBgeStagingError("PUBLIC_BGE_SOURCE_CHUNKS")
        chunk_projection.append(
            {
                "canonicalTextSha256": chunk.canonical_text_sha256,
                "chunkId": chunk.chunk_id,
                "chunkOrdinal": chunk.sequence,
            }
        )
    return _canonical_hash(
        {
            "canonicalTextSha256": hashlib.sha256(
                "\n\n".join(chunk.canonical_text for chunk in sorted(document.chunks, key=lambda value: value.sequence)).encode("utf-8")
            ).hexdigest(),
            "chunks": chunk_projection,
            "documentId": document.document_id,
            "rawContentSha256": document.raw_content_sha256,
            "sourceId": document.source_id,
            "sourceRevisionId": document.source_revision_id,
            "sourceRevisionSha256": materialized.source_revision_sha256,
        }
    )


def _validate_metadata(
    materialized: RagV2BgeMaterializedPublicDocument,
    metadata: PublicBgeSourceMetadata,
    *,
    scope: str,
    document_ir: Mapping[str, object],
) -> None:
    if (
        not isinstance(metadata.citation_title, str)
        or not 1 <= len(metadata.citation_title) <= 500
        or not metadata.citation_title.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in metadata.citation_title)
        or not isinstance(metadata.canonical_https_url, str)
        or not metadata.canonical_https_url.startswith("https://")
        or any(character.isspace() or character in "\\\r\n" for character in metadata.canonical_https_url)
        or not isinstance(metadata.retrieval_topics, tuple)
        or not 1 <= len(metadata.retrieval_topics) <= len(ALLOWED_RAG_TOPICS)
        or len(set(metadata.retrieval_topics)) != len(metadata.retrieval_topics)
        or not set(metadata.retrieval_topics) <= ALLOWED_RAG_TOPICS
        or tuple(sorted(metadata.retrieval_topics, key=lambda value: value.encode("utf-8"))) != metadata.retrieval_topics
        or any(
            type(value) is not bool
            for value in (
                metadata.machine_fetch_allowed,
                metadata.local_processing_allowed,
                metadata.external_embedding_allowed,
                metadata.external_generation_allowed,
            )
        )
    ):
        raise RagV2PublicBgeStagingError("PUBLIC_BGE_SOURCE_METADATA")
    if scope == "EXACT30":
        if (
            metadata.oa_track_id is not None
            or metadata.source_card is not None
            or metadata.license_evidence_sha256 is not None
            or metadata.access_evidence_sha256 is not None
            or metadata.machine_fetch_allowed
            or not metadata.local_processing_allowed
            or metadata.external_embedding_allowed
            or metadata.external_generation_allowed
            or materialized.document.external_processing_eligible
        ):
            raise RagV2PublicBgeStagingError("PUBLIC_BGE_EXACT30_METADATA")
        return
    if (
        not isinstance(metadata.oa_track_id, str)
        or not metadata.oa_track_id
        or metadata.source_card is None
        or not _is_sha256(metadata.license_evidence_sha256)
        or not _is_sha256(metadata.access_evidence_sha256)
        or not all(
            (
                metadata.machine_fetch_allowed,
                metadata.local_processing_allowed,
                metadata.external_embedding_allowed,
                metadata.external_generation_allowed,
            )
        )
    ):
        raise RagV2PublicBgeStagingError("PUBLIC_BGE_OA112_METADATA")
    source_card = _copy_source_card(metadata.source_card)
    assert source_card is not None
    permissions = source_card.get("permissions")
    access = source_card.get("accessEvidence")
    if (
        set(source_card) != _OA_CARD_REQUIRED_KEYS
        or source_card.get("contractId") != "rag-source-card-v4"
        or source_card.get("schemaVersion") != 4
        or source_card.get("sourceKind") != "OPEN_ACCESS_DOCUMENT"
        or source_card.get("sourceId") != materialized.document.source_id
        or source_card.get("canonicalUrl") != metadata.canonical_https_url
        or source_card.get("rawContentSha256") != materialized.document.raw_content_sha256
        or source_card.get("mimeType") != document_ir.get("mimeType")
        or source_card.get("licenseEvidenceDigest") != metadata.license_evidence_sha256
        or not isinstance(access, Mapping)
        or access.get("accessEvidenceDigest") != metadata.access_evidence_sha256
        or access.get("verificationState") != "VERIFIED"
        or not isinstance(permissions, Mapping)
        or any(
            permissions.get(key) is not True
            for key in (
                "machineFetchAllowed",
                "localProcessingAllowed",
                "externalEmbeddingAllowed",
                "externalGenerationAllowed",
            )
        )
    ):
        raise RagV2PublicBgeStagingError("PUBLIC_BGE_OA112_SOURCE_CARD")


def _copy_document_ir(document_ir: Mapping[str, object]) -> dict[str, object]:
    copied = json.loads(json.dumps(document_ir, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    if not isinstance(copied, dict):  # pragma: no cover - caller type is already a mapping.
        raise RagV2PublicBgeStagingError("PUBLIC_BGE_SOURCE_IDENTITY")
    return cast(dict[str, object], copied)


def _copy_source_card(value: Mapping[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None
    copied = json.loads(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    if not isinstance(copied, dict):
        raise RagV2PublicBgeStagingError("PUBLIC_BGE_OA112_SOURCE_CARD")
    return cast(dict[str, object], copied)


def _parser_version(document_ir: Mapping[str, object]) -> str:
    evidence = document_ir.get("parserEvidence")
    if not isinstance(evidence, Mapping):
        raise RagV2PublicBgeStagingError("PUBLIC_BGE_SOURCE_IDENTITY")
    version = evidence.get("parserVersion")
    artifact_hash = evidence.get("parserArtifactSha256")
    if not isinstance(version, str) or not version or len(version) > 128 or not _is_sha256(artifact_hash):
        raise RagV2PublicBgeStagingError("PUBLIC_BGE_SOURCE_IDENTITY")
    return version


def _required_ir_text(document_ir: Mapping[str, object], key: str, *, maximum: int) -> str:
    value = document_ir.get(key)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise RagV2PublicBgeStagingError("PUBLIC_BGE_SOURCE_IDENTITY")
    return value


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _assert_path_free(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key in _PATH_KEYS:
                raise RagV2PublicBgeStagingError("PUBLIC_BGE_SOURCE_PATH_LEAK")
            _assert_path_free(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_path_free(nested)
