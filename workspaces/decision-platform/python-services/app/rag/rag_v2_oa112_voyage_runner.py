"""OA112를 Voyage full-bundle contextual embedding input으로 prepare/materialize한다.

This module never opens a provider socket. It closes the 14x8 registry, approved raw-cache IR, and pinned
tokenizer inputs before a caller combines them with EXACT30 and the explicit empty OWNER_PRIVATE sentinel.
"""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

from app.rag.ingest_pipeline import RagEmbeddingInput, RagTokenizer
from app.rag.oa112_active_registry import Oa112ActiveRegistry, Oa112RegistryEntry
from app.rag.oa_release_manifest import OA_TRACK_IDS
from app.rag.rag_v2_bge_materializer import (
    ApprovedDocumentParser,
    RagV2BgeMaterializationError,
    RagV2PreparedPublicDocument,
    prepare_public_document_for_embedding,
)
from app.rag.rag_v2_external_exact30_voyage_runner import (
    RagV2VoyageDocumentEmbedding,
    RagV2VoyageMaterializedPublicDocument,
    VoyagePreChunkedChunk,
    VoyagePreChunkedDocumentGroup,
    validate_voyage_document_vectors,
)
from app.rag.rag_v2_voyage_types import PublicVoyageSourceMetadata
from app.rag.rag_v2_oa112_bge_runner import (
    oa112_public_document_request,
    validate_oa112_active_entries,
)
from app.rag.rag_v2_voyage_checkpoint import (
    RagV2VoyageCheckpointError,
    load_optional_public_voyage_checkpoint,
    write_public_voyage_checkpoint,
)

_VOYAGE_PROFILE_ID = "voyage_context_4_1024_v1"
_COMPONENT_SCOPE: Literal["OA112"] = "OA112"
_SHA256_HEX = frozenset("0123456789abcdef")


class RagV2Oa112VoyageRunnerError(ValueError):
    """OA112 full-bundle Voyage input, vector, or immutable generation identity가 drift했다."""


@dataclass(frozen=True, slots=True)
class Oa112PreparedVoyageDocument:
    """one OA source의 path-free IR/input/group pairing이며 provider 전 process-local로만 유지된다."""

    entry: Oa112RegistryEntry
    prepared: RagV2PreparedPublicDocument
    group: VoyagePreChunkedDocumentGroup
    metadata: PublicVoyageSourceMetadata
    checkpoint_reused: bool
    checkpoint_written: bool


@dataclass(frozen=True, slots=True)
class Oa112PublicVoyagePreparation:
    """full OA112 component one-shot input을 가진 transient preparation이다."""

    prepared_documents: tuple[Oa112PreparedVoyageDocument, ...]
    registry_id: str
    registry_digest: str
    checkpoint_reused_count: int
    checkpoint_written_count: int

    @property
    def groups(self) -> tuple[VoyagePreChunkedDocumentGroup, ...]:
        """full bundle canonical source order를 그대로 반환한다."""

        return tuple(item.group for item in self.prepared_documents)


@dataclass(frozen=True, slots=True)
class RagV2Oa112VoyageComponentContext:
    """OA112 Voyage component의 deterministic stage/evaluation identity다."""

    component_scope: Literal["OA112"]
    component_generation_id: str
    materialization_run_id: str
    generation_hash: str
    manifest_hash: str
    expected_source_count: int
    expected_chunk_count: int
    embedding_profile_id: Literal["voyage_context_4_1024_v1"]
    member_digests: tuple[str, ...]
    registry_id: str
    registry_digest: str


@dataclass(frozen=True, slots=True)
class Oa112PublicVoyageMaterialization:
    """OA112 IR/chunk/Voyage vector의 transient writer input이다."""

    records: tuple[RagV2VoyageMaterializedPublicDocument, ...]
    context: RagV2Oa112VoyageComponentContext

    def content_free_receipt(self) -> dict[str, object]:
        """raw cache path, canonical text, vector, provider response를 제외한 run receipt다."""

        return {
            "chunkCount": self.context.expected_chunk_count,
            "componentGenerationId": self.context.component_generation_id,
            "componentScope": self.context.component_scope,
            "embeddingProfileId": self.context.embedding_profile_id,
            "generationHash": self.context.generation_hash,
            "manifestHash": self.context.manifest_hash,
            "materializationRunId": self.context.materialization_run_id,
            "registryDigest": self.context.registry_digest,
            "registryId": self.context.registry_id,
            "sourceCount": self.context.expected_source_count,
        }


def prepare_oa112_public_voyage_component(
    *,
    tokenizer: RagTokenizer,
    registry: Oa112ActiveRegistry,
    local_cache_root: Path,
    parser: ApprovedDocumentParser | None = None,
    checkpoint_local_corpus_root: Path | None = None,
    parser_version: str = "1.1.0",
    tokenizer_version: str = "bge-m3-sentencepiece-v1",
    max_workers: int = 4,
) -> Oa112PublicVoyagePreparation:
    """112 approved OA sources를 one full bundle call 전 canonical Voyage groups로 prepare한다.

    The caller supplies only the packet-bound private raw cache root. A malformed source stops preparation before
    any later source can be handed to a provider transport; this function performs neither embedding nor staging.
    """

    entries = tuple(sorted(registry.active_entries, key=lambda entry: entry.source_id.encode("utf-8")))
    validate_oa112_active_entries(entries)
    if not local_cache_root.is_absolute() or not _is_sha256(registry.registry_digest):
        raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_PREPARATION_CONTEXT")
    approved_root = local_cache_root / "oa-raw"
    if checkpoint_local_corpus_root is not None and (
        not checkpoint_local_corpus_root.is_absolute() or ".." in checkpoint_local_corpus_root.parts
    ):
        raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_PREPARATION_CONTEXT")
    if type(max_workers) is not int or not 1 <= max_workers <= 4:
        raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_PREPARATION_CONTEXT")

    worker_state = threading.local()

    def prepare_entry(entry: Oa112RegistryEntry) -> Oa112PreparedVoyageDocument:
        expected_metadata = _metadata(entry)
        checkpoint_reused = False
        checkpoint_written = False
        try:
            checkpoint = None
            if checkpoint_local_corpus_root is not None:
                checkpoint = load_optional_public_voyage_checkpoint(
                    local_corpus_root=checkpoint_local_corpus_root,
                    component_scope="OA112",
                    expected_raw_content_sha256=entry.raw_content_sha256,
                    expected_source_revision_id=entry.source_revision_id,
                    parser_version=parser_version,
                    tokenizer_version=tokenizer_version,
                )
            if checkpoint is not None:
                prepared = checkpoint.prepared
                checkpoint_reused = True
                if checkpoint.metadata != expected_metadata:
                    raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_CHECKPOINT_DRIFT")
            else:
                active_parser = parser
                if active_parser is None:
                    from app.rag.local_document_parser import LocalDocumentParser

                    # worker별 parser 하나만 만들어 PDF backend의 mutable state를 thread 사이에 공유하지 않는다.
                    active_parser = getattr(worker_state, "parser", None)
                    if active_parser is None:
                        active_parser = LocalDocumentParser(strip_inert_pdf_attachments=True)
                        worker_state.parser = active_parser
                prepared = prepare_public_document_for_embedding(
                    parser=active_parser,
                    tokenizer=tokenizer,
                    request=oa112_public_document_request(
                        entry,
                        approved_root=approved_root,
                        embedding_profile_id=_VOYAGE_PROFILE_ID,
                    ),
                )
                if checkpoint_local_corpus_root is not None:
                    write_public_voyage_checkpoint(
                        local_corpus_root=checkpoint_local_corpus_root,
                        parser_version=parser_version,
                        tokenizer_version=tokenizer_version,
                        prepared=prepared,
                        metadata=expected_metadata,
                    )
                    checkpoint_written = True
        except (RagV2BgeMaterializationError, RagV2VoyageCheckpointError) as error:
            raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_MATERIALIZATION") from error
        group = _group_from_prepared(entry=entry, prepared=prepared)
        return Oa112PreparedVoyageDocument(
            entry=entry,
            prepared=prepared,
            group=group,
            metadata=expected_metadata,
            checkpoint_reused=checkpoint_reused,
            checkpoint_written=checkpoint_written,
        )

    # fixture parser는 test double일 수 있으므로 공유하지 않고, production parser만 4 worker로 제한한다.
    worker_count = max_workers if parser is None else 1
    if worker_count == 1:
        selected = tuple(prepare_entry(entry) for entry in entries)
    else:
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="oa112-voyage-ir") as executor:
            selected = tuple(executor.map(prepare_entry, entries))
    if (
        len(selected) != 112
        or tuple(item.group.source_id for item in selected)
        != tuple(sorted((item.group.source_id for item in selected), key=lambda value: value.encode("utf-8")))
        or len({item.group.source_revision_id for item in selected}) != 112
    ):
        raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_PREPARATION_CONTEXT")
    return Oa112PublicVoyagePreparation(
        prepared_documents=selected,
        registry_id=registry.registry_id,
        registry_digest=registry.registry_digest,
        checkpoint_reused_count=sum(item.checkpoint_reused for item in selected),
        checkpoint_written_count=sum(item.checkpoint_written for item in selected),
    )


def materialize_prepared_oa112_public_voyage_component(
    *,
    preparation: Oa112PublicVoyagePreparation,
    vectors: object,
) -> Oa112PublicVoyageMaterialization:
    """one full-bundle response의 OA112 vector slice만 assign하고 component context를 만든다."""

    if (
        not isinstance(preparation, Oa112PublicVoyagePreparation)
        or len(preparation.prepared_documents) != 112
        or not _is_sha256(preparation.registry_digest)
        or not preparation.registry_id
    ):
        raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_COMPONENT_CONTEXT")
    expected_rows = sum(len(item.group.chunks) for item in preparation.prepared_documents)
    try:
        validated_vectors = validate_voyage_document_vectors(
            vectors,
            expected_rows=expected_rows,
        )
    except ValueError as error:
        raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_COMPONENT_EMBEDDING") from error
    cursor = 0
    records: list[RagV2VoyageMaterializedPublicDocument] = []
    for item in preparation.prepared_documents:
        count = len(item.prepared.embedding_inputs)
        assigned = validated_vectors[cursor : cursor + count]
        if assigned.shape != (count, 1024):  # pragma: no cover - validated before slicing.
            raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_COMPONENT_EMBEDDING")
        embeddings = tuple(
            RagV2VoyageDocumentEmbedding(
                chunk_id=chunk.chunk_id,
                embedding_input_hash=embedding_input.embedding_input_hash,
                context_set_hash=_required_context_hash(embedding_input),
                embedding=np.array(vector, dtype=np.float32, copy=True),
            )
            for chunk, embedding_input, vector in zip(
                item.prepared.document.chunks,
                item.prepared.embedding_inputs,
                assigned,
                strict=True,
            )
        )
        records.append(
            RagV2VoyageMaterializedPublicDocument(
                document=item.prepared.document,
                embeddings=embeddings,
                source_revision_sha256=item.prepared.source_revision_sha256,
                document_ir=_copy_document_ir(item.prepared.document_ir),
                metadata=item.metadata,
            )
        )
        cursor += count
    if cursor != len(validated_vectors):  # pragma: no cover - expected row invariant above closes this path.
        raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_COMPONENT_EMBEDDING")
    context = build_oa112_public_voyage_component_context(
        records=tuple(records),
        registry_id=preparation.registry_id,
        registry_digest=preparation.registry_digest,
    )
    return Oa112PublicVoyageMaterialization(records=tuple(records), context=context)


def build_oa112_public_voyage_component_context(
    *,
    records: tuple[RagV2VoyageMaterializedPublicDocument, ...],
    registry_id: str,
    registry_digest: str,
) -> RagV2Oa112VoyageComponentContext:
    """all 112 OA records의 registry-bound manifest/generation identity를 만든다."""

    if len(records) != 112 or not registry_id or not _is_sha256(registry_digest):
        raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_COMPONENT_CONTEXT")
    ordered = tuple(sorted(records, key=lambda record: record.document.source_id.encode("utf-8")))
    member_digests = tuple(oa112_voyage_source_member_digest(record) for record in ordered)
    if (
        len({record.document.source_id for record in ordered}) != 112
        or len({record.document.source_revision_id for record in ordered}) != 112
        or len({record.document.document_id for record in ordered}) != 112
        or len(set(member_digests)) != 112
    ):
        raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_COMPONENT_CONTEXT")
    expected_chunk_count = sum(len(record.document.chunks) for record in ordered)
    if expected_chunk_count < 112:
        raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_COMPONENT_CONTEXT")
    manifest_hash = _canonical_hash(
        {
            "componentScope": _COMPONENT_SCOPE,
            "embeddingProfileId": _VOYAGE_PROFILE_ID,
            "members": list(member_digests),
            "schemaVersion": 1,
        }
    )
    generation_hash = _canonical_hash(
        {
            "componentScope": _COMPONENT_SCOPE,
            "embeddingProfileId": _VOYAGE_PROFILE_ID,
            "expectedChunkCount": expected_chunk_count,
            "expectedSourceCount": 112,
            "manifestHash": manifest_hash,
            "schemaVersion": 1,
        }
    )
    component_generation_id = f"rgr_{generation_hash[:32]}"
    materialization_run_id = "rgr_run_" + hashlib.sha256(
        f"rag-v2-oa112-voyage-run|{component_generation_id}|{manifest_hash}".encode("utf-8")
    ).hexdigest()[:32]
    return RagV2Oa112VoyageComponentContext(
        component_scope="OA112",
        component_generation_id=component_generation_id,
        materialization_run_id=materialization_run_id,
        generation_hash=generation_hash,
        manifest_hash=manifest_hash,
        expected_source_count=112,
        expected_chunk_count=expected_chunk_count,
        embedding_profile_id="voyage_context_4_1024_v1",
        member_digests=member_digests,
        registry_id=registry_id,
        registry_digest=registry_digest,
    )


def oa112_voyage_source_member_digest(record: RagV2VoyageMaterializedPublicDocument) -> str:
    """OA registry rights/evidence와 contextual chunk identity를 vector-free member digest로 닫는다."""

    document = record.document
    metadata = record.metadata
    if (
        document.source_scope != _COMPONENT_SCOPE
        or not document.external_processing_eligible
        or len(document.chunks) != len(record.embeddings)
        or not _is_sha256(document.raw_content_sha256)
        or not _is_sha256(document.normalized_content_sha256)
        or not _is_sha256(record.source_revision_sha256)
        or metadata.source_card_sha256 is not None
        or metadata.oa_track_id not in OA_TRACK_IDS
        or not isinstance(metadata.oa_source_card, dict)
        or not _is_sha256(metadata.license_evidence_sha256)
        or not _is_sha256(metadata.access_evidence_sha256)
        or not metadata.machine_fetch_allowed
        or not metadata.local_processing_allowed
        or not metadata.external_embedding_allowed
        or not metadata.external_generation_allowed
    ):
        raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_COMPONENT_CONTEXT")
    source_card = _copy_document_ir(metadata.oa_source_card)
    if (
        source_card.get("sourceId") != document.source_id
        or source_card.get("rawContentSha256") != document.raw_content_sha256
        or source_card.get("canonicalUrl") != metadata.canonical_https_url
    ):
        raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_COMPONENT_CONTEXT")
    embeddings = {embedding.chunk_id: embedding for embedding in record.embeddings}
    if len(embeddings) != len(record.embeddings):
        raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_COMPONENT_CONTEXT")
    chunks: list[dict[str, object]] = []
    for chunk in sorted(document.chunks, key=lambda value: value.sequence):
        embedding = embeddings.get(chunk.chunk_id)
        if (
            embedding is None
            or not _is_sha256(embedding.context_set_hash)
            or not _is_sha256(embedding.embedding_input_hash)
            or not _is_unit_vector(embedding.embedding)
        ):
            raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_COMPONENT_CONTEXT")
        chunks.append(
            {
                "canonicalTextSha256": chunk.canonical_text_sha256,
                "chunkId": chunk.chunk_id,
                "chunkOrdinal": chunk.sequence,
                "contextSetHash": embedding.context_set_hash,
                "embeddingInputHash": embedding.embedding_input_hash,
            }
        )
    return _canonical_hash(
        {
            "accessEvidenceSha256": metadata.access_evidence_sha256,
            "canonicalTextSha256": hashlib.sha256(
                "\n\n".join(
                    chunk.canonical_text for chunk in sorted(document.chunks, key=lambda value: value.sequence)
                ).encode("utf-8")
            ).hexdigest(),
            "chunks": chunks,
            "documentId": document.document_id,
            "licenseEvidenceSha256": metadata.license_evidence_sha256,
            "oaTrackId": metadata.oa_track_id,
            "rawContentSha256": document.raw_content_sha256,
            "sourceId": document.source_id,
            "sourceRevisionId": document.source_revision_id,
            "sourceRevisionSha256": record.source_revision_sha256,
        }
    )


def _group_from_prepared(
    *,
    entry: Oa112RegistryEntry,
    prepared: RagV2PreparedPublicDocument,
) -> VoyagePreChunkedDocumentGroup:
    """profile-bound input hashes와 canonical chunks를 transport-only ordered group으로 투영한다."""

    inputs = prepared.embedding_inputs
    if (
        not inputs
        or len(inputs) != len(prepared.document.chunks)
        or any(input.embedding_profile_id != _VOYAGE_PROFILE_ID for input in inputs)
    ):
        raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_MATERIALIZATION")
    context_hash = _required_context_hash(inputs[0])
    if any(_required_context_hash(value) != context_hash for value in inputs):
        raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_MATERIALIZATION")
    return VoyagePreChunkedDocumentGroup(
        source_id=entry.source_id,
        source_revision_id=entry.source_revision_id,
        context_set_hash=context_hash,
        chunks=tuple(
            VoyagePreChunkedChunk(
                chunk_id=chunk.chunk_id,
                canonical_text=chunk.canonical_text,
                canonical_text_sha256=chunk.canonical_text_sha256,
                embedding_input_hash=embedding_input.embedding_input_hash,
                token_count=chunk.token_count,
            )
            for chunk, embedding_input in zip(prepared.document.chunks, inputs, strict=True)
        ),
    )


def _metadata(entry: Oa112RegistryEntry) -> PublicVoyageSourceMetadata:
    """all-four rights evidence를 Voyage staging data shape에 copy한다; local path is excluded."""

    return PublicVoyageSourceMetadata(
        citation_title=entry.title,
        retrieval_topics=entry.retrieval_topics,
        canonical_https_url=entry.canonical_url,
        source_card_sha256=None,
        machine_fetch_allowed=entry.machine_fetch_allowed,
        local_processing_allowed=entry.local_processing_allowed,
        external_embedding_allowed=entry.external_embedding_allowed,
        external_generation_allowed=entry.external_generation_allowed,
        oa_track_id=entry.track_id,
        oa_source_card=_copy_document_ir(entry.source_card),
        license_evidence_sha256=entry.license_evidence_sha256,
        access_evidence_sha256=entry.access_evidence_sha256,
    )


def _required_context_hash(value: RagEmbeddingInput) -> str:
    context_hash = value.context_set_hash
    if not isinstance(context_hash, str) or not _is_sha256(context_hash):
        raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_MATERIALIZATION")
    return context_hash


def _copy_document_ir(value: dict[str, object]) -> dict[str, object]:
    copied = json.loads(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    if not isinstance(copied, dict):  # pragma: no cover - dataclass/static source card contracts keep a dict.
        raise RagV2Oa112VoyageRunnerError("OA112_VOYAGE_COMPONENT_CONTEXT")
    return copied


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _SHA256_HEX for character in value)
    )


def _is_unit_vector(value: object) -> bool:
    if not isinstance(value, np.ndarray) or value.dtype != np.float32 or value.shape != (1024,):
        return False
    if not bool(np.isfinite(value).all()):
        return False
    return bool(np.isclose(float(np.linalg.norm(value)), 1.0, rtol=0.0, atol=1e-5))
