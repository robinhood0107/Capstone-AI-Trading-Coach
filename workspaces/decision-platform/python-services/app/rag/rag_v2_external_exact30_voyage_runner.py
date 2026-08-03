from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol

import numpy as np
from numpy.typing import NDArray

from app.rag.document_ir_materializer import (
    DocumentIrMaterializationError,
    RagV2CanonicalDocumentChunk,
    RagV2DocumentMaterialization,
    RagV2DocumentMaterializationRequest,
    materialize_document_ir,
)
from app.rag.external_exact30_source_card_parser import (
    EXTERNAL_EXACT30_LANGUAGE_TAGS,
    ExternalExact30SourceCardDocumentParser,
    ExternalExact30SourceCardParserError,
    external_exact30_document_id,
    external_exact30_source_revision_id,
)
from app.rag.external_processing_corpus import (
    S4_7C_PROFILE_ID,
    S4_7C_SOURCE_CARD_ROOT,
    ExternalProcessingCorpusError,
    load_external_processing_corpus,
)
from app.rag.ingest_pipeline import (
    RagCanonicalChunk,
    RagEmbeddingInput,
    RagIngestError,
    RagTokenizer,
    build_embedding_inputs,
)
from app.rag.source_card_corpus import (
    PUBLIC_TOPICS_BY_SOURCE_ID,
    FrozenSourceCard,
    FrozenSourceCardCorpus,
)

_VOYAGE_PROFILE_ID: Final = "voyage_context_4_1024_v1"
_COMPONENT_SCOPE: Final[Literal["EXACT30"]] = "EXACT30"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RagV2ExternalExact30VoyageRunnerError(ValueError):
    """external-safe exact-30 Voyage input 또는 provider result가 immutable contract를 벗어났다."""


@dataclass(frozen=True, slots=True)
class VoyagePreChunkedChunk:
    """Voyage에 한 번만 전달할 canonical pre-chunked document input이다.

    chunk text는 caller process에서 transport에만 전달하며 receipt, log, DB control plane에는 직렬화하지 않는다.
    """

    chunk_id: str
    canonical_text: str
    canonical_text_sha256: str
    embedding_input_hash: str


@dataclass(frozen=True, slots=True)
class VoyagePreChunkedDocumentGroup:
    """한 source revision의 ordered contextual embedding group이다."""

    source_id: str
    source_revision_id: str
    context_set_hash: str
    chunks: tuple[VoyagePreChunkedChunk, ...]


class VoyageDocumentEmbedder(Protocol):
    """fixed Voyage transport가 ordered pre-chunked document groups를 full-call로 embedding하는 port다."""

    def embed_document_groups(
        self,
        *,
        groups: tuple[VoyagePreChunkedDocumentGroup, ...],
    ) -> NDArray[np.float32]: ...


@dataclass(frozen=True, slots=True)
class RagV2VoyageDocumentEmbedding:
    """transient Voyage vector row이며 raw provider response를 보유하지 않는다."""

    chunk_id: str
    embedding_input_hash: str
    context_set_hash: str
    embedding: NDArray[np.float32]


@dataclass(frozen=True, slots=True)
class PublicVoyageSourceMetadata:
    """external-safe exact-30 citation/right projection만 DB staging에 전달하는 metadata다."""

    citation_title: str
    retrieval_topics: tuple[str, ...]
    canonical_https_url: str
    source_card_sha256: str
    machine_fetch_allowed: bool
    local_processing_allowed: bool
    external_embedding_allowed: bool
    external_generation_allowed: bool


@dataclass(frozen=True, slots=True)
class RagV2VoyageMaterializedPublicDocument:
    """public external-safe IR/chunk/Voyage vector의 process-local staging unit이다."""

    document: RagV2DocumentMaterialization
    embeddings: tuple[RagV2VoyageDocumentEmbedding, ...]
    source_revision_sha256: str
    document_ir: dict[str, object]
    metadata: PublicVoyageSourceMetadata


@dataclass(frozen=True, slots=True)
class RagV2PublicVoyageComponentContext:
    """one external-safe exact-30 Voyage component의 deterministic generation identity다."""

    component_scope: str
    component_generation_id: str
    materialization_run_id: str
    generation_hash: str
    manifest_hash: str
    expected_source_count: int
    expected_chunk_count: int
    embedding_profile_id: str
    member_digests: tuple[str, ...]
    source_card_corpus_manifest_sha256: str


@dataclass(frozen=True, slots=True)
class ExternalExact30PublicVoyageMaterialization:
    """full S4.7C exact-30 Voyage component와 content-free immutable context다."""

    records: tuple[RagV2VoyageMaterializedPublicDocument, ...]
    context: RagV2PublicVoyageComponentContext

    def content_free_receipt(self) -> dict[str, object]:
        """card body, source path, provider payload, vector를 제외한 local run receipt를 만든다."""

        return {
            "chunkCount": self.context.expected_chunk_count,
            "componentGenerationId": self.context.component_generation_id,
            "componentScope": self.context.component_scope,
            "embeddingProfileId": self.context.embedding_profile_id,
            "generationHash": self.context.generation_hash,
            "manifestHash": self.context.manifest_hash,
            "materializationRunId": self.context.materialization_run_id,
            "sourceCardCorpusManifestSha256": self.context.source_card_corpus_manifest_sha256,
            "sourceCount": self.context.expected_source_count,
        }


def materialize_external_exact30_public_voyage_component(
    *,
    tokenizer: RagTokenizer,
    embedder: VoyageDocumentEmbedder,
    corpus: FrozenSourceCardCorpus | None = None,
) -> ExternalExact30PublicVoyageMaterialization:
    """all S4.7C cards를 one ordered Voyage document-group call로 materialize한다.

    This function makes no provider transport itself. A caller must supply a hard-gated transport that performs
    exactly one bounded full-component call; partial group submission and per-document fallback are rejected.
    """

    try:
        selected_corpus = corpus or load_external_processing_corpus()
    except ExternalProcessingCorpusError as error:
        raise RagV2ExternalExact30VoyageRunnerError("EXTERNAL_EXACT30_SOURCE_CARD_CORPUS") from error
    cards = tuple(sorted(selected_corpus.cards, key=lambda card: card.source_id.encode("utf-8")))
    if (
        selected_corpus.manifest.get("profileId") != S4_7C_PROFILE_ID
        or len(cards) != 30
        or len({card.source_id for card in cards}) != 30
    ):
        raise RagV2ExternalExact30VoyageRunnerError("EXTERNAL_EXACT30_SOURCE_CARD_MEMBERSHIP")

    parser = ExternalExact30SourceCardDocumentParser(corpus=selected_corpus)
    provisional: list[_PreparedExternalExact30Document] = []
    for card in cards:
        provisional.append(_prepare_document(card=card, parser=parser, tokenizer=tokenizer))
    groups = tuple(item.group for item in provisional)
    try:
        vectors = _validate_voyage_vectors(
            embedder.embed_document_groups(groups=groups),
            expected_rows=sum(len(group.chunks) for group in groups),
        )
    except Exception as error:
        if isinstance(error, RagV2ExternalExact30VoyageRunnerError):
            raise
        raise RagV2ExternalExact30VoyageRunnerError("VOYAGE_COMPONENT_EMBEDDING") from error

    cursor = 0
    records: list[RagV2VoyageMaterializedPublicDocument] = []
    for prepared in provisional:
        count = len(prepared.embedding_inputs)
        next_cursor = cursor + count
        assigned = vectors[cursor:next_cursor]
        if assigned.shape != (count, 1024):  # pragma: no cover - validated before slicing.
            raise RagV2ExternalExact30VoyageRunnerError("VOYAGE_COMPONENT_EMBEDDING")
        embeddings = tuple(
            RagV2VoyageDocumentEmbedding(
                chunk_id=chunk.chunk_id,
                embedding_input_hash=embedding_input.embedding_input_hash,
                context_set_hash=_required_context_hash(embedding_input.context_set_hash),
                embedding=np.array(vector, dtype=np.float32, copy=True),
            )
            for chunk, embedding_input, vector in zip(
                prepared.document.chunks,
                prepared.embedding_inputs,
                assigned,
                strict=True,
            )
        )
        records.append(
            RagV2VoyageMaterializedPublicDocument(
                document=prepared.document,
                embeddings=embeddings,
                source_revision_sha256=_canonical_hash(prepared.document_ir),
                document_ir=_copy_document_ir(prepared.document_ir),
                metadata=_source_metadata(prepared.card),
            )
        )
        cursor = next_cursor
    if cursor != len(vectors):  # pragma: no cover - expected row invariant above closes this path.
        raise RagV2ExternalExact30VoyageRunnerError("VOYAGE_COMPONENT_EMBEDDING")

    context = build_external_exact30_public_voyage_component_context(
        records=tuple(records),
        source_card_corpus_manifest_sha256=selected_corpus.corpus_manifest_sha256,
    )
    return ExternalExact30PublicVoyageMaterialization(records=tuple(records), context=context)


def build_external_exact30_public_voyage_component_context(
    *,
    records: Sequence[RagV2VoyageMaterializedPublicDocument],
    source_card_corpus_manifest_sha256: str,
) -> RagV2PublicVoyageComponentContext:
    """full external exact-30 records의 source-order manifest와 generation identity를 만든다."""

    selected = tuple(records)
    if len(selected) != 30 or not _is_sha256(source_card_corpus_manifest_sha256):
        raise RagV2ExternalExact30VoyageRunnerError("VOYAGE_COMPONENT_CONTEXT")
    ordered = tuple(sorted(selected, key=lambda record: record.document.source_id.encode("utf-8")))
    member_digests = tuple(_member_digest(record) for record in ordered)
    source_ids = tuple(record.document.source_id for record in ordered)
    source_revisions = tuple(record.document.source_revision_id for record in ordered)
    document_ids = tuple(record.document.document_id for record in ordered)
    if (
        len(set(source_ids)) != 30
        or len(set(source_revisions)) != 30
        or len(set(document_ids)) != 30
        or len(set(member_digests)) != 30
    ):
        raise RagV2ExternalExact30VoyageRunnerError("VOYAGE_COMPONENT_CONTEXT")
    expected_chunk_count = sum(len(record.document.chunks) for record in ordered)
    if expected_chunk_count < 30:
        raise RagV2ExternalExact30VoyageRunnerError("VOYAGE_COMPONENT_CONTEXT")
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
            "expectedSourceCount": 30,
            "manifestHash": manifest_hash,
            "schemaVersion": 1,
        }
    )
    component_generation_id = f"rgr_{generation_hash[:32]}"
    materialization_run_id = "rgr_run_" + hashlib.sha256(
        f"rag-v2-external-exact30-voyage-run|{component_generation_id}|{manifest_hash}".encode("utf-8")
    ).hexdigest()[:32]
    return RagV2PublicVoyageComponentContext(
        component_scope=_COMPONENT_SCOPE,
        component_generation_id=component_generation_id,
        materialization_run_id=materialization_run_id,
        generation_hash=generation_hash,
        manifest_hash=manifest_hash,
        expected_source_count=30,
        expected_chunk_count=expected_chunk_count,
        embedding_profile_id=_VOYAGE_PROFILE_ID,
        member_digests=member_digests,
        source_card_corpus_manifest_sha256=source_card_corpus_manifest_sha256,
    )


@dataclass(frozen=True, slots=True)
class _PreparedExternalExact30Document:
    """provider call 전까지 process-local로만 유지하는 parsed document/group pairing이다."""

    card: FrozenSourceCard
    document_ir: dict[str, object]
    document: RagV2DocumentMaterialization
    embedding_inputs: tuple[RagEmbeddingInput, ...]
    group: VoyagePreChunkedDocumentGroup


def _prepare_document(
    *,
    card: FrozenSourceCard,
    parser: ExternalExact30SourceCardDocumentParser,
    tokenizer: RagTokenizer,
) -> _PreparedExternalExact30Document:
    """one S4.7C card를 provider transport 전 canonical source/revision/group로 close한다."""

    source_revision_id = external_exact30_source_revision_id(card)
    try:
        document_ir = parser.parse_approved_document(
            approved_root=S4_7C_SOURCE_CARD_ROOT,
            relative_path=Path(card.relative_path).name,
            source_id=card.source_id,
            source_revision_id=source_revision_id,
            language_tags=EXTERNAL_EXACT30_LANGUAGE_TAGS,
        )
        document = materialize_document_ir(
            document_ir=document_ir,
            request=RagV2DocumentMaterializationRequest(
                document_id=external_exact30_document_id(card),
                source_scope=_COMPONENT_SCOPE,
                source_id=card.source_id,
                source_revision_id=source_revision_id,
                local_processing_allowed=True,
                external_embedding_allowed=True,
                external_generation_allowed=True,
            ),
            tokenizer=tokenizer,
        )
        embedding_inputs = build_embedding_inputs(
            _canonical_chunks(document.chunks, source_id=card.source_id, source_revision_id=source_revision_id),
            embedding_profile_id=_VOYAGE_PROFILE_ID,
        )
    except (
        DocumentIrMaterializationError,
        ExternalExact30SourceCardParserError,
        RagIngestError,
    ) as error:
        raise RagV2ExternalExact30VoyageRunnerError("EXTERNAL_EXACT30_DOCUMENT_MATERIALIZATION") from error
    if (
        not document.external_processing_eligible
        or len(document.chunks) != len(embedding_inputs)
        or not embedding_inputs
    ):
        raise RagV2ExternalExact30VoyageRunnerError("EXTERNAL_EXACT30_DOCUMENT_MATERIALIZATION")
    context_hash = _required_context_hash(embedding_inputs[0].context_set_hash)
    if any(
        embedding_input.embedding_profile_id != _VOYAGE_PROFILE_ID
        or embedding_input.context_set_hash != context_hash
        or not _is_sha256(embedding_input.embedding_input_hash)
        for embedding_input in embedding_inputs
    ):
        raise RagV2ExternalExact30VoyageRunnerError("EXTERNAL_EXACT30_DOCUMENT_MATERIALIZATION")
    chunks = tuple(
        VoyagePreChunkedChunk(
            chunk_id=chunk.chunk_id,
            canonical_text=chunk.canonical_text,
            canonical_text_sha256=chunk.canonical_text_sha256,
            embedding_input_hash=embedding_input.embedding_input_hash,
        )
        for chunk, embedding_input in zip(document.chunks, embedding_inputs, strict=True)
    )
    return _PreparedExternalExact30Document(
        card=card,
        document_ir=document_ir,
        document=document,
        embedding_inputs=embedding_inputs,
        group=VoyagePreChunkedDocumentGroup(
            source_id=card.source_id,
            source_revision_id=source_revision_id,
            context_set_hash=context_hash,
            chunks=chunks,
        ),
    )


def _canonical_chunks(
    chunks: tuple[RagV2CanonicalDocumentChunk, ...],
    *,
    source_id: str,
    source_revision_id: str,
) -> tuple[RagCanonicalChunk, ...]:
    """canonical document chunks를 profile input builder가 요구하는 immutable projection으로 바꾼다."""

    return tuple(
        RagCanonicalChunk(
            source_id=source_id,
            source_revision_id=source_revision_id,
            chunk_revision_id=chunk.chunk_id,
            sequence=chunk.sequence,
            heading_path=chunk.heading_path,
            text=chunk.canonical_text,
            content_hash=chunk.canonical_text_sha256,
            token_count=chunk.token_count,
            contains_table=chunk.contains_table,
        )
        for chunk in chunks
    )


def _source_metadata(card: FrozenSourceCard) -> PublicVoyageSourceMetadata:
    """S4.7C card의 external-safe citation/right projection만 stage boundary로 전달한다."""

    title = card.front_matter.get("title")
    canonical_url = card.front_matter.get("canonicalUrl")
    topics = PUBLIC_TOPICS_BY_SOURCE_ID.get(card.source_id)
    if (
        not isinstance(title, str)
        or not isinstance(canonical_url, str)
        or not topics
        or card.front_matter.get("externalProcessingAllowed") is not True
        or card.front_matter.get("externalProcessingGate") != "LICENSE_AND_CONSENT_VERIFIED"
    ):
        raise RagV2ExternalExact30VoyageRunnerError("EXTERNAL_EXACT30_SOURCE_CARD_METADATA")
    return PublicVoyageSourceMetadata(
        citation_title=title,
        retrieval_topics=tuple(topics),
        canonical_https_url=canonical_url,
        source_card_sha256=card.card_sha256,
        machine_fetch_allowed=False,
        local_processing_allowed=True,
        external_embedding_allowed=True,
        external_generation_allowed=True,
    )


def _member_digest(record: RagV2VoyageMaterializedPublicDocument) -> str:
    """provider vector를 제외한 persisted source/chunk/context projection digest를 계산한다."""

    document = record.document
    metadata = record.metadata
    if (
        document.source_scope != _COMPONENT_SCOPE
        or not document.external_processing_eligible
        or len(document.chunks) != len(record.embeddings)
        or not _is_sha256(document.raw_content_sha256)
        or not _is_sha256(document.normalized_content_sha256)
        or not _is_sha256(record.source_revision_sha256)
        or not _is_sha256(metadata.source_card_sha256)
        or metadata.machine_fetch_allowed
        or not metadata.local_processing_allowed
        or not metadata.external_embedding_allowed
        or not metadata.external_generation_allowed
    ):
        raise RagV2ExternalExact30VoyageRunnerError("VOYAGE_COMPONENT_CONTEXT")
    embeddings = {embedding.chunk_id: embedding for embedding in record.embeddings}
    if len(embeddings) != len(record.embeddings):
        raise RagV2ExternalExact30VoyageRunnerError("VOYAGE_COMPONENT_CONTEXT")
    chunks: list[dict[str, object]] = []
    for chunk in sorted(document.chunks, key=lambda value: value.sequence):
        embedding = embeddings.get(chunk.chunk_id)
        if (
            embedding is None
            or embedding.context_set_hash is None
            or not _is_sha256(embedding.context_set_hash)
            or not _is_sha256(embedding.embedding_input_hash)
            or not _is_unit_vector(embedding.embedding)
        ):
            raise RagV2ExternalExact30VoyageRunnerError("VOYAGE_COMPONENT_CONTEXT")
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
            "canonicalTextSha256": hashlib.sha256(
                "\n\n".join(
                    chunk.canonical_text for chunk in sorted(document.chunks, key=lambda value: value.sequence)
                ).encode("utf-8")
            ).hexdigest(),
            "chunks": chunks,
            "documentId": document.document_id,
            "rawContentSha256": document.raw_content_sha256,
            "sourceCardSha256": metadata.source_card_sha256,
            "sourceId": document.source_id,
            "sourceRevisionId": document.source_revision_id,
            "sourceRevisionSha256": record.source_revision_sha256,
        }
    )


def _validate_voyage_vectors(
    vectors: object,
    *,
    expected_rows: int,
) -> NDArray[np.float32]:
    """provider adapter output의 row count/dimension/finite/unit-norm contract를 fail-close한다."""

    if (
        not isinstance(vectors, np.ndarray)
        or vectors.dtype != np.float32
        or vectors.shape != (expected_rows, 1024)
        or not np.isfinite(vectors).all()
    ):
        raise RagV2ExternalExact30VoyageRunnerError("VOYAGE_COMPONENT_EMBEDDING")
    norms = np.linalg.norm(vectors, axis=1)
    if not np.allclose(norms, 1.0, atol=1e-5, rtol=0.0):
        raise RagV2ExternalExact30VoyageRunnerError("VOYAGE_COMPONENT_EMBEDDING")
    return vectors


def _is_unit_vector(vector: object) -> bool:
    return (
        isinstance(vector, np.ndarray)
        and vector.dtype == np.float32
        and vector.shape == (1024,)
        and bool(np.isfinite(vector).all())
        and math.isclose(float(np.linalg.norm(vector)), 1.0, abs_tol=1e-5, rel_tol=0.0)
    )


def _required_context_hash(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RagV2ExternalExact30VoyageRunnerError("EXTERNAL_EXACT30_DOCUMENT_MATERIALIZATION")
    return value


def _copy_document_ir(document_ir: Mapping[str, object]) -> dict[str, object]:
    copied = json.loads(
        json.dumps(document_ir, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
    if not isinstance(copied, dict):  # pragma: no cover - parser contract keeps IR an object.
        raise RagV2ExternalExact30VoyageRunnerError("EXTERNAL_EXACT30_DOCUMENT_MATERIALIZATION")
    return copied


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None
