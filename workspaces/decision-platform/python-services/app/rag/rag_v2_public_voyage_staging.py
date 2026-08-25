"""Public Voyage component을 V45 restricted writer payload로 닫는다.

EXACT30 and OA112 share one contextual profile but keep their own immutable generation/run identities. This module
only creates an in-memory payload for the writer capability; it never opens a provider connection or persists raw
cache paths, source text, or vectors outside the transaction caller.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Literal, TypeAlias

import numpy as np

from app.rag.authorized_retrieval import ALLOWED_RAG_TOPICS
from app.rag.oa_release_manifest import OA_TRACK_IDS
from app.rag.rag_v2_external_exact30_voyage_runner import (
    RagV2PublicVoyageComponentContext,
    RagV2VoyageMaterializedPublicDocument,
    external_exact30_voyage_source_member_digest,
)
from app.rag.rag_v2_oa112_voyage_runner import (
    RagV2Oa112VoyageComponentContext,
    oa112_voyage_source_member_digest,
)
from app.rag.rag_v2_voyage_types import PublicVoyageSourceMetadata

_VOYAGE_PROFILE_ID = "voyage_context_4_1024_v1"
_SCOPE_COUNTS = {"EXACT30": 30, "OA112": 112}
_GENERATION_ID = re.compile(r"^rgr_[0-9a-f]{32}$")
_RUN_ID = re.compile(r"^rgr_run_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ID = re.compile(r"^src_[a-z0-9][a-z0-9_-]{2,95}$")
_SOURCE_REVISION_ID = re.compile(r"^srv_[a-z0-9][a-z0-9_-]{2,95}$")
_DOCUMENT_ID = re.compile(r"^doc_[a-z0-9][a-z0-9_-]{10,95}$")
_CHUNK_ID = re.compile(r"^rag_v2_chk_[0-9a-f]{32}$")
_PATH_KEYS = frozenset(("originalPath", "rawPath", "absolutePath", "filePath"))

PublicVoyageComponentContext: TypeAlias = (
    RagV2PublicVoyageComponentContext | RagV2Oa112VoyageComponentContext
)


class RagV2PublicVoyageStagingError(ValueError):
    """V45 public Voyage writer payload 또는 component binding이 drift했다."""


def build_public_voyage_staging_payload(
    record: RagV2VoyageMaterializedPublicDocument,
    *,
    context: PublicVoyageComponentContext,
) -> dict[str, object]:
    """one public contextual record를 V45에만 전달할 path-free payload로 만든다.

    `context` must describe the whole 30- or 112-source component. The writer recomputes the same member digest
    after staging, so this constructor rejects an unbound source before a database connection is attempted.
    """

    _validate_context(context)
    document = record.document
    metadata = record.metadata
    if context.component_scope == "EXACT30":
        scope: Literal["EXACT30", "OA112"] = "EXACT30"
    elif context.component_scope == "OA112":
        scope = "OA112"
    else:  # pragma: no cover - _validate_context closes the public scopes first.
        raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_SCOPE")
    if document.source_scope != scope:
        raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_SCOPE")
    member_digest = _member_digest(record, scope=scope)
    if member_digest not in context.member_digests:
        raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_MEMBER_MANIFEST")

    document_ir = _copy_document_ir(record.document_ir)
    if (
        _DOCUMENT_ID.fullmatch(document.document_id) is None
        or _SOURCE_ID.fullmatch(document.source_id) is None
        or _SOURCE_REVISION_ID.fullmatch(document.source_revision_id) is None
        or not _is_sha256(document.raw_content_sha256)
        or not _is_sha256(document.normalized_content_sha256)
        or not _is_sha256(record.source_revision_sha256)
        or document_ir.get("sourceId") != document.source_id
        or document_ir.get("sourceRevisionId") != document.source_revision_id
        or document_ir.get("rawContentSha256") != document.raw_content_sha256
        or document_ir.get("normalizedContentSha256") != document.normalized_content_sha256
        or _canonical_hash(document_ir) != record.source_revision_sha256
    ):
        raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_SOURCE_IDENTITY")
    _validate_metadata(
        metadata,
        scope=scope,
        document_external_processing_eligible=document.external_processing_eligible,
    )

    chunks = tuple(sorted(document.chunks, key=lambda value: value.sequence))
    embeddings_by_chunk = {embedding.chunk_id: embedding for embedding in record.embeddings}
    if (
        not chunks
        or len(chunks) != len(embeddings_by_chunk)
        or tuple(chunk.sequence for chunk in chunks) != tuple(range(1, len(chunks) + 1))
    ):
        raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_SOURCE_CHUNKS")
    chunk_payloads: list[dict[str, object]] = []
    embedding_payloads: list[dict[str, object]] = []
    context_set_hash: str | None = None
    for chunk in chunks:
        embedding = embeddings_by_chunk.get(chunk.chunk_id)
        if (
            embedding is None
            or _CHUNK_ID.fullmatch(chunk.chunk_id) is None
            or not _is_sha256(chunk.canonical_text_sha256)
            or hashlib.sha256(chunk.canonical_text.encode("utf-8")).hexdigest()
            != chunk.canonical_text_sha256
            or not 1 <= chunk.token_count <= 600
            or not _is_sha256(embedding.embedding_input_hash)
            or not _is_sha256(embedding.context_set_hash)
            or not _is_unit_vector(embedding.embedding)
        ):
            raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_SOURCE_CHUNKS")
        if context_set_hash is None:
            context_set_hash = embedding.context_set_hash
        elif embedding.context_set_hash != context_set_hash:
            raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_CONTEXT_GROUP")
        chunk_payloads.append(
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
        embedding_payloads.append(
            {
                "chunkId": chunk.chunk_id,
                "contextSetHash": embedding.context_set_hash,
                "embedding": [float(value) for value in embedding.embedding],
                "embeddingInputHash": embedding.embedding_input_hash,
            }
        )
    if context_set_hash is None:  # pragma: no cover - non-empty chunks are checked above.
        raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_CONTEXT_GROUP")

    source = {
        "accessEvidenceSha256": metadata.access_evidence_sha256,
        "canonicalHttpsUrl": metadata.canonical_https_url,
        "canonicalText": "\n\n".join(chunk.canonical_text for chunk in chunks),
        "canonicalTextSha256": hashlib.sha256(
            "\n\n".join(chunk.canonical_text for chunk in chunks).encode("utf-8")
        ).hexdigest(),
        "chunks": chunk_payloads,
        "citationTitle": metadata.citation_title,
        "documentId": document.document_id,
        "documentIr": document_ir,
        "embeddings": embedding_payloads,
        "externalEmbeddingAllowed": metadata.external_embedding_allowed,
        "externalGenerationAllowed": metadata.external_generation_allowed,
        "externalProcessingEligible": document.external_processing_eligible,
        "licenseEvidenceSha256": metadata.license_evidence_sha256,
        "localProcessingAllowed": metadata.local_processing_allowed,
        "machineFetchAllowed": metadata.machine_fetch_allowed,
        "mimeType": _required_ir_text(document_ir, "mimeType", maximum=128),
        "oaSourceCard": _copy_optional_source_card(metadata.oa_source_card),
        "oaTrackId": metadata.oa_track_id,
        "parserVersion": _parser_version(document_ir),
        "rawContentSha256": document.raw_content_sha256,
        "retrievalTopics": list(metadata.retrieval_topics),
        "sourceCardSha256": metadata.source_card_sha256,
        "sourceId": document.source_id,
        "sourceLocator": dict(chunks[0].locator),
        "sourceRevisionId": document.source_revision_id,
        "sourceRevisionSha256": record.source_revision_sha256,
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
        "memberDigests": list(context.member_digests),
        "schemaVersion": 1,
        "source": source,
    }
    _assert_path_free(payload)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    if len(encoded) > 16 * 1024 * 1024:
        raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_SOURCE_PAYLOAD_BOUND")
    return payload


def _validate_context(context: PublicVoyageComponentContext) -> None:
    scope = context.component_scope
    expected_source_count = _SCOPE_COUNTS.get(scope)
    if expected_source_count is None:
        raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_COMPONENT_CONTEXT")
    run_prefix = (
        "rag-v2-external-exact30-voyage-run" if scope == "EXACT30" else "rag-v2-oa112-voyage-run"
    )
    expected_run_id = (
        "rgr_run_"
        + hashlib.sha256(
            f"{run_prefix}|{context.component_generation_id}|{context.manifest_hash}".encode()
        ).hexdigest()[:32]
    )
    if (
        context.embedding_profile_id != _VOYAGE_PROFILE_ID
        or context.expected_source_count != expected_source_count
        or context.expected_chunk_count < expected_source_count
        or _GENERATION_ID.fullmatch(context.component_generation_id) is None
        or _RUN_ID.fullmatch(context.materialization_run_id) is None
        or context.materialization_run_id != expected_run_id
        or not _is_sha256(context.generation_hash)
        or not _is_sha256(context.manifest_hash)
        or context.component_generation_id != f"rgr_{context.generation_hash[:32]}"
        or len(context.member_digests) != expected_source_count
        or len(set(context.member_digests)) != expected_source_count
        or any(not _is_sha256(value) for value in context.member_digests)
    ):
        raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_COMPONENT_CONTEXT")
    if scope == "EXACT30":
        if not isinstance(context, RagV2PublicVoyageComponentContext) or not _is_sha256(
            context.source_card_corpus_manifest_sha256
        ):
            raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_COMPONENT_CONTEXT")
    elif (
        not isinstance(context, RagV2Oa112VoyageComponentContext)
        or not context.registry_id
        or not _is_sha256(context.registry_digest)
    ):
        raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_COMPONENT_CONTEXT")


def _member_digest(record: RagV2VoyageMaterializedPublicDocument, *, scope: str) -> str:
    if scope == "EXACT30":
        return external_exact30_voyage_source_member_digest(record)
    if scope == "OA112":
        return oa112_voyage_source_member_digest(record)
    raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_SCOPE")


def _validate_metadata(
    metadata: PublicVoyageSourceMetadata,
    *,
    scope: Literal["EXACT30", "OA112"],
    document_external_processing_eligible: bool,
) -> None:
    if (
        not isinstance(metadata.citation_title, str)
        or not 1 <= len(metadata.citation_title) <= 500
        or not metadata.citation_title.strip()
        or any(
            ord(character) < 32 or ord(character) == 127 for character in metadata.citation_title
        )
        or not isinstance(metadata.canonical_https_url, str)
        or not metadata.canonical_https_url.startswith("https://")
        or any(
            character.isspace() or character in "\\\r\n"
            for character in metadata.canonical_https_url
        )
        or not isinstance(metadata.retrieval_topics, tuple)
        or not 1 <= len(metadata.retrieval_topics) <= len(ALLOWED_RAG_TOPICS)
        or len(set(metadata.retrieval_topics)) != len(metadata.retrieval_topics)
        or not set(metadata.retrieval_topics) <= ALLOWED_RAG_TOPICS
        or tuple(sorted(metadata.retrieval_topics, key=lambda value: value.encode("utf-8")))
        != metadata.retrieval_topics
        or not document_external_processing_eligible
    ):
        raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_SOURCE_METADATA")
    if scope == "EXACT30":
        valid = (
            _is_sha256(metadata.source_card_sha256)
            and metadata.oa_track_id is None
            and metadata.oa_source_card is None
            and metadata.license_evidence_sha256 is None
            and metadata.access_evidence_sha256 is None
            and metadata.machine_fetch_allowed is False
            and metadata.local_processing_allowed is True
            and metadata.external_embedding_allowed is True
            and metadata.external_generation_allowed is True
        )
    else:
        card = metadata.oa_source_card
        access_evidence = card.get("accessEvidence") if isinstance(card, dict) else None
        valid = (
            metadata.source_card_sha256 is None
            and metadata.oa_track_id in OA_TRACK_IDS
            and isinstance(card, dict)
            and _is_sha256(metadata.license_evidence_sha256)
            and _is_sha256(metadata.access_evidence_sha256)
            and metadata.machine_fetch_allowed is True
            and metadata.local_processing_allowed is True
            and metadata.external_embedding_allowed is True
            and metadata.external_generation_allowed is True
            and card.get("sourceId") is not None
            and card.get("canonicalUrl") == metadata.canonical_https_url
            and card.get("licenseEvidenceDigest") == metadata.license_evidence_sha256
            and isinstance(access_evidence, Mapping)
            and access_evidence.get("accessEvidenceDigest") == metadata.access_evidence_sha256
        )
    if not valid:
        raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_SOURCE_METADATA")


def _required_ir_text(document_ir: Mapping[str, object], key: str, *, maximum: int) -> str:
    value = document_ir.get(key)
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_SOURCE_IDENTITY")
    return value


def _parser_version(document_ir: Mapping[str, object]) -> str:
    evidence = document_ir.get("parserEvidence")
    if not isinstance(evidence, Mapping):
        raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_SOURCE_IDENTITY")
    value = evidence.get("parserVersion")
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_SOURCE_IDENTITY")
    return value


def _copy_document_ir(value: Mapping[str, object]) -> dict[str, object]:
    copied = json.loads(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
    if not isinstance(
        copied, dict
    ):  # pragma: no cover - source runner already closes this boundary.
        raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_SOURCE_IDENTITY")
    return copied


def _copy_optional_source_card(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_SOURCE_METADATA")
    copied = json.loads(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
    if not isinstance(copied, dict):  # pragma: no cover - Mapping input is normalized above.
        raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_SOURCE_METADATA")
    return copied


def _assert_path_free(value: object) -> None:
    """로컬 경로를 운반하는 구조 필드를 거부하되 문서 원문 자체를 경로로 해석하지 않는다."""

    if isinstance(value, Mapping):
        if _PATH_KEYS.intersection(value):
            raise RagV2PublicVoyageStagingError("PUBLIC_VOYAGE_PATH_LEAK")
        for item in value.values():
            _assert_path_free(item)
    elif isinstance(value, list):
        for item in value:
            _assert_path_free(item)


def _is_unit_vector(value: object) -> bool:
    return (
        isinstance(value, np.ndarray)
        and value.dtype == np.float32
        and value.shape == (1024,)
        and bool(np.isfinite(value).all())
        and math.isclose(float(np.linalg.norm(value)), 1.0, abs_tol=1e-5, rel_tol=0.0)
    )


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None
