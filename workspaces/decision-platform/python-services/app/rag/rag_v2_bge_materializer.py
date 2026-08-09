from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

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
from app.rag.ingest_pipeline import (
    RagCanonicalChunk,
    RagEmbeddingInput,
    RagIngestError,
    RagTokenizer,
    build_embedding_inputs,
)
from app.rag.local_document_parser import DocumentParseError

_BGE_PROFILE_ID = "bge_m3_local_1024_v1"
_VOYAGE_PROFILE_ID = "voyage_context_4_1024_v1"


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


class ApprovedDocumentParser(Protocol):
    """owner/OA local cache 공통의 path-free Document IR parser boundary다."""

    def parse_approved_document(
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
class RagV2PublicDocumentRequest:
    """exact-30/OA112 local source의 profile-bound generation identity다.

    filesystem locator는 parser invocation에만 쓰고 receipt나 DB writer projection에는 넣지 않는다.
    expected raw hash와 MIME은 registry/source-card contract에서 온 값이라, cache drift는 embed 전에
    fail-closed한다.
    """

    approved_root: Path
    relative_path: str
    document_id: str
    source_scope: Literal["EXACT30", "OA112"]
    source_id: str
    source_revision_id: str
    language_tags: tuple[str, ...]
    expected_raw_content_sha256: str
    expected_mime_type: str
    local_processing_allowed: bool
    external_embedding_allowed: bool
    external_generation_allowed: bool
    embedding_profile_id: str


@dataclass(frozen=True, slots=True)
class RagV2PreparedPublicDocument:
    """external embedding 전에도 complete하게 검증된 public Document IR/chunk input이다.

    parser 경로와 raw cache leaf는 이 object에 보관하지 않는다. 반환값은 process-local로만 유지하며,
    caller는 one-shot profile transport 결과를 결합한 뒤에만 writer transaction으로 넘겨야 한다.
    """

    document: RagV2DocumentMaterialization
    embedding_inputs: tuple[RagEmbeddingInput, ...]
    source_revision_sha256: str
    document_ir: dict[str, object]


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


@dataclass(frozen=True, slots=True)
class RagV2BgeMaterializedPublicDocument:
    """public exact-30/OA112 IR/chunk/vector의 transient local staging unit이다."""

    document: RagV2DocumentMaterialization
    embeddings: tuple[RagV2BgeDocumentEmbedding, ...]
    source_revision_sha256: str
    document_ir: dict[str, object]

    def content_free_receipt(self) -> dict[str, object]:
        """raw path/text/vector 없이 materialization identity와 count만 반환한다."""

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
            _canonical_chunks(
                document.chunks,
                source_id=request.source_id,
                source_revision_id=request.source_revision_id,
            ),
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


def materialize_public_bge_document(
    *,
    parser: ApprovedDocumentParser,
    tokenizer: RagTokenizer,
    embedder: BgeDocumentEmbedder,
    request: RagV2PublicDocumentRequest,
) -> RagV2BgeMaterializedPublicDocument:
    """approved exact-30/OA112 local source를 parser→canonical chunks→pinned BGE로 materialize한다.

    이 경계는 provider transport를 만들지 않는다. source registry/source-card의 raw hash와 MIME을
    parser 결과에 다시 bind해 cache drift 또는 filename/MIME spoof를 vector generation 전에 닫는다.
    """

    if request.embedding_profile_id != _BGE_PROFILE_ID:
        raise RagV2BgeMaterializationError("BGE_PROFILE_REQUIRED")
    prepared = prepare_public_document_for_embedding(
        parser=parser,
        tokenizer=tokenizer,
        request=request,
    )
    try:
        vectors = validate_embedding_batch(
            embedder.embed(tuple(item.text for item in prepared.embedding_inputs)),
            expected_rows=len(prepared.embedding_inputs),
        )
    except RagV2BgeMaterializationError:
        raise
    except BgeRuntimeError as error:
        raise RagV2BgeMaterializationError(str(error)) from error

    if len(prepared.embedding_inputs) != len(prepared.document.chunks):  # pragma: no cover - closed helper contract.
        raise RagV2BgeMaterializationError("BGE_INPUT_CHUNK_CARDINALITY")
    embeddings = tuple(
        RagV2BgeDocumentEmbedding(
            chunk_id=chunk.chunk_id,
            embedding_input_hash=embedding_input.embedding_input_hash,
            context_set_hash=None,
            embedding=np.array(vector, dtype=np.float32, copy=True),
        )
        for chunk, embedding_input, vector in zip(
            prepared.document.chunks,
            prepared.embedding_inputs,
            vectors,
            strict=True,
        )
    )
    return RagV2BgeMaterializedPublicDocument(
        document=prepared.document,
        embeddings=embeddings,
        source_revision_sha256=prepared.source_revision_sha256,
        document_ir=_copy_document_ir(prepared.document_ir),
    )


def prepare_public_document_for_embedding(
    *,
    parser: ApprovedDocumentParser,
    tokenizer: RagTokenizer,
    request: RagV2PublicDocumentRequest,
) -> RagV2PreparedPublicDocument:
    """approved public source를 profile-neutral canonical chunks로 prepare한다.

    BGE와 Voyage는 같은 pinned tokenizer/Document IR을 쓰지만 vector space는 절대 섞지 않는다.
    따라서 이 helper는 source rights, MIME, raw hash와 input-hash를 provider socket 전 완결하고
    embedding은 호출하지 않는다.
    """

    if request.embedding_profile_id not in {_BGE_PROFILE_ID, _VOYAGE_PROFILE_ID}:
        raise RagV2BgeMaterializationError("PUBLIC_DOCUMENT_PROFILE")
    if request.source_scope not in {"EXACT30", "OA112"}:
        raise RagV2BgeMaterializationError("PUBLIC_DOCUMENT_SCOPE")
    if not _is_sha256(request.expected_raw_content_sha256):
        raise RagV2BgeMaterializationError("PUBLIC_DOCUMENT_RAW_DRIFT")
    if request.expected_mime_type not in {
        "application/pdf",
        "text/html",
        "text/plain",
        "text/markdown",
    }:
        raise RagV2BgeMaterializationError("PUBLIC_DOCUMENT_MIME_DRIFT")
    try:
        document_ir = parser.parse_approved_document(
            approved_root=request.approved_root,
            relative_path=request.relative_path,
            source_id=request.source_id,
            source_revision_id=request.source_revision_id,
            language_tags=request.language_tags,
        )
        if document_ir.get("rawContentSha256") != request.expected_raw_content_sha256:
            raise RagV2BgeMaterializationError("PUBLIC_DOCUMENT_RAW_DRIFT")
        if document_ir.get("mimeType") != request.expected_mime_type:
            raise RagV2BgeMaterializationError("PUBLIC_DOCUMENT_MIME_DRIFT")
        document = materialize_document_ir(
            document_ir=document_ir,
            request=RagV2DocumentMaterializationRequest(
                document_id=request.document_id,
                source_scope=request.source_scope,
                source_id=request.source_id,
                source_revision_id=request.source_revision_id,
                local_processing_allowed=request.local_processing_allowed,
                external_embedding_allowed=request.external_embedding_allowed,
                external_generation_allowed=request.external_generation_allowed,
            ),
            tokenizer=tokenizer,
        )
        inputs = build_embedding_inputs(
            _canonical_chunks(
                document.chunks,
                source_id=request.source_id,
                source_revision_id=request.source_revision_id,
            ),
            embedding_profile_id=request.embedding_profile_id,
            tokenizer=tokenizer,
        )
    except RagV2BgeMaterializationError:
        raise
    except (DocumentIrMaterializationError, DocumentParseError, RagIngestError) as error:
        raise RagV2BgeMaterializationError(str(error)) from error
    if len(inputs) != len(document.chunks) or not inputs:
        raise RagV2BgeMaterializationError("PUBLIC_DOCUMENT_INPUT_CHUNK_CARDINALITY")
    return RagV2PreparedPublicDocument(
        document=document,
        embedding_inputs=inputs,
        source_revision_sha256=_source_revision_sha256(document_ir),
        document_ir=_copy_document_ir(document_ir),
    )


def _canonical_chunks(
    chunks: tuple[RagV2CanonicalDocumentChunk, ...],
    *,
    source_id: str,
    source_revision_id: str,
) -> tuple[RagCanonicalChunk, ...]:
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


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


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
