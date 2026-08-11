from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping

import numpy as np

from app.rag.authorized_retrieval import ALLOWED_RAG_TOPICS
from app.rag.rag_v2_external_exact30_voyage_runner import (
    RagV2PublicVoyageComponentContext,
    RagV2VoyageMaterializedPublicDocument,
    external_exact30_voyage_source_member_digest,
)
from app.rag.rag_v2_voyage_types import PublicVoyageSourceMetadata

_VOYAGE_PROFILE_ID = "voyage_context_4_1024_v1"
_COMPONENT_SCOPE = "EXACT30"
_GENERATION_ID = re.compile(r"^rgr_[0-9a-f]{32}$")
_RUN_ID = re.compile(r"^rgr_run_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ID = re.compile(r"^src_[a-z0-9][a-z0-9_-]{2,95}$")
_SOURCE_REVISION_ID = re.compile(r"^srv_[a-z0-9][a-z0-9_-]{2,95}$")
_DOCUMENT_ID = re.compile(r"^doc_[a-z0-9][a-z0-9_-]{10,95}$")
_CHUNK_ID = re.compile(r"^rag_v2_chk_[0-9a-f]{32}$")
_PATH_KEYS = frozenset(("originalPath", "rawPath", "absolutePath", "filePath"))


class ExternalExact30VoyageStagingError(ValueError):
    """external exact-30 Voyage writer payload 또는 component identity drift다."""


def build_external_exact30_voyage_staging_payload(
    record: RagV2VoyageMaterializedPublicDocument,
    *,
    context: RagV2PublicVoyageComponentContext,
) -> dict[str, object]:
    """one external-safe exact-30 source의 transient IR/text/vector writer payload를 만든다.

    payload는 local writer transaction에만 전달한다. source card body, vector, local path와 provider
    response를 receipt/API/history에 저장하지 않도록 caller가 이 dict를 log 또는 file로 내보내면 안 된다.
    """

    _validate_context(context)
    member_digest = external_exact30_voyage_source_member_digest(record)
    if member_digest not in context.member_digests:
        raise ExternalExact30VoyageStagingError("EXTERNAL_EXACT30_VOYAGE_MEMBER_MANIFEST")

    document = record.document
    metadata = record.metadata
    document_ir = _copy_document_ir(record.document_ir)
    if (
        document.source_scope != _COMPONENT_SCOPE
        or document_ir.get("sourceId") != document.source_id
        or document_ir.get("sourceRevisionId") != document.source_revision_id
        or document_ir.get("rawContentSha256") != document.raw_content_sha256
        or document_ir.get("normalizedContentSha256") != document.normalized_content_sha256
        or _canonical_hash(document_ir) != record.source_revision_sha256
    ):
        raise ExternalExact30VoyageStagingError("EXTERNAL_EXACT30_VOYAGE_SOURCE_IDENTITY")
    _validate_metadata(metadata, document_external_processing_eligible=document.external_processing_eligible)

    chunks = tuple(sorted(document.chunks, key=lambda value: value.sequence))
    embeddings_by_chunk = {embedding.chunk_id: embedding for embedding in record.embeddings}
    if (
        not chunks
        or len(chunks) != len(embeddings_by_chunk)
        or tuple(chunk.sequence for chunk in chunks) != tuple(range(1, len(chunks) + 1))
    ):
        raise ExternalExact30VoyageStagingError("EXTERNAL_EXACT30_VOYAGE_SOURCE_CHUNKS")
    chunk_payloads: list[dict[str, object]] = []
    embedding_payloads: list[dict[str, object]] = []
    for chunk in chunks:
        embedding = embeddings_by_chunk.get(chunk.chunk_id)
        if (
            embedding is None
            or not _CHUNK_ID.fullmatch(chunk.chunk_id)
            or not _is_sha256(chunk.canonical_text_sha256)
            or hashlib.sha256(chunk.canonical_text.encode("utf-8")).hexdigest()
            != chunk.canonical_text_sha256
            or not 1 <= chunk.token_count <= 600
            or not _is_sha256(embedding.embedding_input_hash)
            or not _is_sha256(embedding.context_set_hash)
            or not _is_unit_vector(embedding.embedding)
        ):
            raise ExternalExact30VoyageStagingError("EXTERNAL_EXACT30_VOYAGE_SOURCE_CHUNKS")
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
    canonical_text = "\n\n".join(chunk.canonical_text for chunk in chunks)
    source = {
        "accessEvidenceSha256": None,
        "canonicalHttpsUrl": metadata.canonical_https_url,
        "canonicalText": canonical_text,
        "canonicalTextSha256": hashlib.sha256(canonical_text.encode("utf-8")).hexdigest(),
        "chunks": chunk_payloads,
        "citationTitle": metadata.citation_title,
        "documentId": document.document_id,
        "documentIr": document_ir,
        "embeddings": embedding_payloads,
        "externalEmbeddingAllowed": True,
        "externalGenerationAllowed": True,
        "externalProcessingEligible": True,
        "licenseEvidenceSha256": None,
        "localProcessingAllowed": True,
        "machineFetchAllowed": False,
        "mimeType": _required_ir_text(document_ir, "mimeType", maximum=128),
        "oaSourceCard": None,
        "oaTrackId": None,
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
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) > 16 * 1024 * 1024:
        raise ExternalExact30VoyageStagingError("EXTERNAL_EXACT30_VOYAGE_SOURCE_PAYLOAD_BOUND")
    return payload


def _validate_context(context: RagV2PublicVoyageComponentContext) -> None:
    if (
        context.component_scope != _COMPONENT_SCOPE
        or context.embedding_profile_id != _VOYAGE_PROFILE_ID
        or context.expected_source_count != 30
        or context.expected_chunk_count < 30
        or _GENERATION_ID.fullmatch(context.component_generation_id) is None
        or _RUN_ID.fullmatch(context.materialization_run_id) is None
        or not _is_sha256(context.generation_hash)
        or not _is_sha256(context.manifest_hash)
        or not _is_sha256(context.source_card_corpus_manifest_sha256)
        or len(context.member_digests) != 30
        or len(set(context.member_digests)) != 30
        or any(not _is_sha256(digest) for digest in context.member_digests)
    ):
        raise ExternalExact30VoyageStagingError("EXTERNAL_EXACT30_VOYAGE_COMPONENT_CONTEXT")


def _validate_metadata(
    metadata: PublicVoyageSourceMetadata,
    *,
    document_external_processing_eligible: bool,
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
        or tuple(sorted(metadata.retrieval_topics, key=lambda value: value.encode("utf-8")))
        != metadata.retrieval_topics
        or not _is_sha256(metadata.source_card_sha256)
        or metadata.machine_fetch_allowed
        or not metadata.local_processing_allowed
        or not metadata.external_embedding_allowed
        or not metadata.external_generation_allowed
        or not document_external_processing_eligible
    ):
        raise ExternalExact30VoyageStagingError("EXTERNAL_EXACT30_VOYAGE_SOURCE_METADATA")


def _required_ir_text(document_ir: Mapping[str, object], key: str, *, maximum: int) -> str:
    value = document_ir.get(key)
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise ExternalExact30VoyageStagingError("EXTERNAL_EXACT30_VOYAGE_SOURCE_IDENTITY")
    return value


def _parser_version(document_ir: Mapping[str, object]) -> str:
    parser_evidence = document_ir.get("parserEvidence")
    if not isinstance(parser_evidence, Mapping):
        raise ExternalExact30VoyageStagingError("EXTERNAL_EXACT30_VOYAGE_SOURCE_IDENTITY")
    value = parser_evidence.get("parserVersion")
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ExternalExact30VoyageStagingError("EXTERNAL_EXACT30_VOYAGE_SOURCE_IDENTITY")
    return value


def _copy_document_ir(document_ir: Mapping[str, object]) -> dict[str, object]:
    copied = json.loads(json.dumps(document_ir, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    if not isinstance(copied, dict):  # pragma: no cover - runner already closes the IR shape.
        raise ExternalExact30VoyageStagingError("EXTERNAL_EXACT30_VOYAGE_SOURCE_IDENTITY")
    return copied


def _assert_path_free(value: object) -> None:
    if isinstance(value, Mapping):
        if _PATH_KEYS.intersection(value):
            raise ExternalExact30VoyageStagingError("EXTERNAL_EXACT30_VOYAGE_PATH_LEAK")
        for item in value.values():
            _assert_path_free(item)
    elif isinstance(value, list):
        for item in value:
            _assert_path_free(item)
    elif isinstance(value, str) and (
        value.startswith(("/", "\\\\", "file:")) or re.match(r"^[A-Za-z]:[\\/]", value)
    ):
        raise ExternalExact30VoyageStagingError("EXTERNAL_EXACT30_VOYAGE_PATH_LEAK")


def _is_unit_vector(vector: object) -> bool:
    return (
        isinstance(vector, np.ndarray)
        and vector.dtype == np.float32
        and vector.shape == (1024,)
        and bool(np.isfinite(vector).all())
        and math.isclose(float(np.linalg.norm(vector)), 1.0, abs_tol=1e-5, rel_tol=0.0)
    )


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None
