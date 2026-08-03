from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from app.rag.bge_runtime import BgeRuntimeError, validate_embedding_batch
from app.rag.document_ir_materializer import (
    DocumentIrMaterializationError,
    RagV2CanonicalDocumentChunk,
    RagV2DocumentMaterialization,
    RagV2DocumentMaterializationRequest,
    materialize_document_ir,
)
from app.rag.ingest_pipeline import RagCanonicalChunk, RagIngestError, RagTokenizer, build_embedding_inputs
from app.rag.local_document_parser import DocumentParseError

_BGE_PROFILE_ID = "bge_m3_local_1024_v1"


class RagV2BgeMaterializationError(ValueError):
    """owner-private local BGE materialization이 외부 전송 없이 실패했음을 나타낸다."""


class OwnerDocumentParser(Protocol):
    """원본 복사 없이 안전한 owner read를 Document IR로 변환하는 parser boundary다."""

    def parse_owner_document(
        self,
        *,
        approved_root: Path,
        relative_path: str,
        source_id: str,
        source_revision_id: str,
        language_tags: tuple[str, ...],
    ) -> dict[str, object]: ...


class BgeDocumentEmbedder(Protocol):
    """pinned local BGE packet만 사용해 ordered document inputs를 embedding하는 boundary다."""

    def embed(self, texts: tuple[str, ...]) -> NDArray[np.float32]: ...


@dataclass(frozen=True, slots=True)
class RagV2OwnerDocumentRequest:
    """BAT argv가 아닌 local control record에서 읽는 owner import identity다.

    `approved_root`와 `relative_path`는 parser에만 전달되고, receipt/API/history에 직렬화하지 않는다.
    """

    approved_root: Path
    relative_path: str
    document_id: str
    source_id: str
    source_revision_id: str
    language_tags: tuple[str, ...]
    embedding_profile_id: str


@dataclass(frozen=True, slots=True)
class RagV2BgeDocumentEmbedding:
    """DB staging 직전의 bounded local vector row이며 raw file bytes를 보유하지 않는다."""

    chunk_id: str
    embedding_input_hash: str
    context_set_hash: None
    embedding: NDArray[np.float32]


@dataclass(frozen=True, slots=True)
class RagV2BgeMaterializedOwnerDocument:
    """owner IR/chunk/vector를 하나의 local-only staging unit으로 묶는다."""

    document: RagV2DocumentMaterialization
    embeddings: tuple[RagV2BgeDocumentEmbedding, ...]
    source_revision_sha256: str
    document_ir: dict[str, object]

    def content_free_receipt(self) -> dict[str, object]:
        """원본 경로·bytes·canonical text·vector를 제외한 reusable receipt만 반환한다."""

        receipt = dict(self.document.content_free_receipt())
        receipt.update(
            {
                "embeddingCount": len(self.embeddings),
                "embeddingInputHashes": [item.embedding_input_hash for item in self.embeddings],
                "embeddingProfileId": _BGE_PROFILE_ID,
                "sourceRevisionSha256": self.source_revision_sha256,
            }
        )
        return receipt


def materialize_owner_bge_document(
    *,
    parser: OwnerDocumentParser,
    tokenizer: RagTokenizer,
    embedder: BgeDocumentEmbedder,
    request: RagV2OwnerDocumentRequest,
) -> RagV2BgeMaterializedOwnerDocument:
    """owner 문서를 local parser→canonical chunk→pinned BGE vector 순서로 materialize한다.

    이 경계는 network/provider transport를 생성하지 않는다. external-LLM eligibility와 무관하게
    local BGE만 쓰며, caller는 반환된 transient text/vector를 owner-RLS staging transaction으로만
    전달해야 한다.
    """

    if request.embedding_profile_id != _BGE_PROFILE_ID:
        raise RagV2BgeMaterializationError("BGE_PROFILE_REQUIRED")
    try:
        document_ir = parser.parse_owner_document(
            approved_root=request.approved_root,
            relative_path=request.relative_path,
            source_id=request.source_id,
            source_revision_id=request.source_revision_id,
            language_tags=request.language_tags,
        )
        document = materialize_document_ir(
            document_ir=document_ir,
            request=RagV2DocumentMaterializationRequest(
                document_id=request.document_id,
                source_scope="OWNER_PRIVATE",
                source_id=request.source_id,
                source_revision_id=request.source_revision_id,
                local_processing_allowed=True,
                external_embedding_allowed=False,
                external_generation_allowed=False,
            ),
            tokenizer=tokenizer,
        )
        inputs = build_embedding_inputs(
            _canonical_chunks(document.chunks, request),
            embedding_profile_id=_BGE_PROFILE_ID,
            tokenizer=tokenizer,
        )
        vectors = validate_embedding_batch(
            embedder.embed(tuple(item.text for item in inputs)),
            expected_rows=len(inputs),
        )
    except (
        BgeRuntimeError,
        DocumentIrMaterializationError,
        DocumentParseError,
        RagIngestError,
    ) as error:
        raise RagV2BgeMaterializationError(str(error)) from error

    if len(inputs) != len(document.chunks):  # pragma: no cover - closed helper contract.
        raise RagV2BgeMaterializationError("BGE_INPUT_CHUNK_CARDINALITY")
    embeddings = tuple(
        RagV2BgeDocumentEmbedding(
            chunk_id=chunk.chunk_id,
            embedding_input_hash=embedding_input.embedding_input_hash,
            context_set_hash=None,
            embedding=np.array(vector, dtype=np.float32, copy=True),
        )
        for chunk, embedding_input, vector in zip(document.chunks, inputs, vectors, strict=True)
    )
    return RagV2BgeMaterializedOwnerDocument(
        document=document,
        embeddings=embeddings,
        source_revision_sha256=_source_revision_sha256(document_ir),
        # parser 반환값을 caller가 나중에 mutate해 staged DB graph의 identity가 바뀌지 않게
        # canonical JSON round-trip으로 독립 snapshot만 넘긴다. 이 값은 local writer에만 전달한다.
        document_ir=_copy_document_ir(document_ir),
    )


def _canonical_chunks(
    chunks: tuple[RagV2CanonicalDocumentChunk, ...],
    request: RagV2OwnerDocumentRequest,
) -> tuple[RagCanonicalChunk, ...]:
    return tuple(
        RagCanonicalChunk(
            source_id=request.source_id,
            source_revision_id=request.source_revision_id,
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


def _source_revision_sha256(document_ir: Mapping[str, object]) -> str:
    """safe parser가 만든 closed Document IR의 canonical bytes만 source revision receipt로 사용한다."""

    encoded = json.dumps(
        document_ir,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _copy_document_ir(document_ir: Mapping[str, object]) -> dict[str, object]:
    """path/raw bytes 없는 parser Document IR만 immutable local staging input으로 복제한다."""

    copied = json.loads(
        json.dumps(
            document_ir,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    if not isinstance(copied, dict):  # pragma: no cover - parser contract가 이미 mapping을 보장한다.
        raise RagV2BgeMaterializationError("DOCUMENT_IR_COPY_INVALID")
    return copied
