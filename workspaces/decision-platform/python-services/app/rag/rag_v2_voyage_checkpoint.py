"""Public Voyage 준비 결과를 ignored local corpus에 source별로 안전하게 checkpoint한다.

체크포인트는 canonical Document IR/chunk와 profile-specific input identity만 보존한다. raw 파일 경로,
provider credential/response, vector는 기록하지 않으며 동일 identity leaf는 byte digest가 일치할 때만
재사용한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from app.rag.document_ir_materializer import (
    RagV2CanonicalDocumentChunk,
    RagV2DocumentMaterialization,
)
from app.rag.ingest_pipeline import RagEmbeddingInput
from app.rag.rag_v2_bge_materializer import (
    PUBLIC_VOYAGE_SANITIZER_VERSION,
    RagV2PreparedPublicDocument,
)
from app.rag.rag_v2_voyage_types import PublicVoyageSourceMetadata

PublicScope = Literal["EXACT30", "OA112"]

_DERIVED_DIRECTORY = "derived-ir"
_TEMP_DIRECTORY = ".tmp"
_SCHEMA_VERSION = "pre-s5-public-voyage-checkpoint/v2"
_MAX_CHECKPOINT_BYTES = 64 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_SOURCE_REVISION = re.compile(r"^srv_[a-z0-9][a-z0-9_-]{2,95}$")


class RagV2VoyageCheckpointError(ValueError):
    """checkpoint filesystem boundary, identity, digest 또는 payload가 fail-closed 했다."""


@dataclass(frozen=True, slots=True)
class PublicVoyageCheckpoint:
    """재사용 가능한 prepared document와 content-free checkpoint receipt다."""

    checkpoint_key: str
    path: Path
    prepared: RagV2PreparedPublicDocument
    metadata: PublicVoyageSourceMetadata
    reused: bool
    provider_call_count: Literal[0] = 0
    vector_count: Literal[0] = 0

    def content_free_receipt(self) -> dict[str, object]:
        """canonical text, path, Document IR를 제외한 reuse 결과만 반환한다."""

        return {
            "checkpointKey": self.checkpoint_key,
            "componentScope": self.prepared.document.source_scope,
            "providerCallCount": self.provider_call_count,
            "reused": self.reused,
            "sourceId": self.prepared.document.source_id,
            "sourceRevisionId": self.prepared.document.source_revision_id,
            "vectorCount": self.vector_count,
        }


def write_public_voyage_checkpoint(
    *,
    local_corpus_root: Path,
    parser_version: str,
    tokenizer_version: str,
    prepared: RagV2PreparedPublicDocument,
    metadata: PublicVoyageSourceMetadata,
) -> PublicVoyageCheckpoint:
    """prepared source를 0600 leaf에 atomic write하고 동일 leaf는 검증 후 reuse한다."""

    _validate_versions(parser_version=parser_version, tokenizer_version=tokenizer_version)
    _validate_prepared(prepared=prepared, metadata=metadata, parser_version=parser_version)
    scope = cast(PublicScope, prepared.document.source_scope)
    identity = _identity(
        scope=scope,
        raw_content_sha256=prepared.document.raw_content_sha256,
        source_revision_id=prepared.document.source_revision_id,
        parser_version=parser_version,
        tokenizer_version=tokenizer_version,
    )
    checkpoint_key = _canonical_hash(identity)
    scope_root = _ensure_checkpoint_directories(local_corpus_root, scope=scope)
    target = scope_root / f"{checkpoint_key}.json"
    payload = _serialize_payload(prepared=prepared, metadata=metadata)
    envelope = {
        "identity": identity,
        "payload": payload,
        "payloadSha256": _canonical_hash(payload),
        "schemaVersion": _SCHEMA_VERSION,
    }
    encoded = json.dumps(
        envelope,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if not 1 <= len(encoded) <= _MAX_CHECKPOINT_BYTES:
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_SIZE")
    try:
        target.lstat()
    except FileNotFoundError:
        _atomic_write(target=target, encoded=encoded)
        reused = False
    except OSError as error:
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_BOUNDARY") from error
    else:
        existing = _read_secure_file(target)
        if existing != encoded:
            raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_DIGEST")
        reused = True
    loaded = _load_exact(
        path=target,
        expected_identity=identity,
        checkpoint_key=checkpoint_key,
        reused=reused,
        expected_source_revision_sha256=prepared.source_revision_sha256,
    )
    return loaded


def load_public_voyage_checkpoint(
    *,
    local_corpus_root: Path,
    component_scope: PublicScope,
    expected_raw_content_sha256: str,
    expected_source_revision_id: str,
    parser_version: str,
    tokenizer_version: str,
    expected_source_revision_sha256: str | None = None,
) -> PublicVoyageCheckpoint:
    """expected source identity에서만 leaf 이름을 계산하고 안전한 existing checkpoint를 읽는다."""

    _validate_versions(parser_version=parser_version, tokenizer_version=tokenizer_version)
    if (
        component_scope not in {"EXACT30", "OA112"}
        or _SHA256.fullmatch(expected_raw_content_sha256) is None
        or _SOURCE_REVISION.fullmatch(expected_source_revision_id) is None
        or (
            expected_source_revision_sha256 is not None
            and _SHA256.fullmatch(expected_source_revision_sha256) is None
        )
    ):
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_IDENTITY")
    scope_root = _require_checkpoint_directories(local_corpus_root, scope=component_scope)
    identity = _identity(
        scope=component_scope,
        raw_content_sha256=expected_raw_content_sha256,
        source_revision_id=expected_source_revision_id,
        parser_version=parser_version,
        tokenizer_version=tokenizer_version,
    )
    checkpoint_key = _canonical_hash(identity)
    target = scope_root / f"{checkpoint_key}.json"
    return _load_exact(
        path=target,
        expected_identity=identity,
        checkpoint_key=checkpoint_key,
        reused=True,
        expected_source_revision_sha256=expected_source_revision_sha256,
    )


def load_optional_public_voyage_checkpoint(
    *,
    local_corpus_root: Path,
    component_scope: PublicScope,
    expected_raw_content_sha256: str,
    expected_source_revision_id: str,
    parser_version: str,
    tokenizer_version: str,
) -> PublicVoyageCheckpoint | None:
    """첫 preparation에서는 None, exact reusable leaf가 있으면 verified checkpoint를 반환한다."""

    _validate_versions(parser_version=parser_version, tokenizer_version=tokenizer_version)
    if (
        component_scope not in {"EXACT30", "OA112"}
        or _SHA256.fullmatch(expected_raw_content_sha256) is None
        or _SOURCE_REVISION.fullmatch(expected_source_revision_id) is None
    ):
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_IDENTITY")
    derived = local_corpus_root / _DERIVED_DIRECTORY
    scope_root = derived / component_scope.lower()
    try:
        derived.lstat()
    except FileNotFoundError:
        _secure_directory(local_corpus_root)
        return None
    except OSError as error:
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_BOUNDARY") from error
    _secure_directory(derived)
    try:
        scope_root.lstat()
    except FileNotFoundError:
        # 다른 component가 derived root를 먼저 만들었어도 현재 scope의 최초 조회는 정상 cache miss다.
        return None
    except OSError as error:
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_BOUNDARY") from error
    scope_root = _require_checkpoint_directories(local_corpus_root, scope=component_scope)
    identity = _identity(
        scope=component_scope,
        raw_content_sha256=expected_raw_content_sha256,
        source_revision_id=expected_source_revision_id,
        parser_version=parser_version,
        tokenizer_version=tokenizer_version,
    )
    checkpoint_key = _canonical_hash(identity)
    target = scope_root / f"{checkpoint_key}.json"
    try:
        target.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_BOUNDARY") from error
    return _load_exact(
        path=target,
        expected_identity=identity,
        checkpoint_key=checkpoint_key,
        reused=True,
    )


def _load_exact(
    *,
    path: Path,
    expected_identity: dict[str, object],
    checkpoint_key: str,
    reused: bool,
    expected_source_revision_sha256: str | None = None,
) -> PublicVoyageCheckpoint:
    decoded = _decode_envelope(_read_secure_file(path))
    if (
        decoded.get("schemaVersion") != _SCHEMA_VERSION
        or decoded.get("identity") != expected_identity
    ):
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_IDENTITY")
    payload = decoded.get("payload")
    if not isinstance(payload, dict) or decoded.get("payloadSha256") != _canonical_hash(payload):
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_DIGEST")
    prepared, metadata = _deserialize_payload(payload)
    _validate_prepared(
        prepared=prepared,
        metadata=metadata,
        parser_version=str(expected_identity["parserVersion"]),
    )
    if (
        expected_source_revision_sha256 is not None
        and prepared.source_revision_sha256 != expected_source_revision_sha256
    ):
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_IDENTITY")
    return PublicVoyageCheckpoint(
        checkpoint_key=checkpoint_key,
        path=path,
        prepared=prepared,
        metadata=metadata,
        reused=reused,
    )


def _identity(
    *,
    scope: PublicScope,
    raw_content_sha256: str,
    source_revision_id: str,
    parser_version: str,
    tokenizer_version: str,
) -> dict[str, object]:
    return {
        "componentScope": scope,
        "parserVersion": parser_version,
        "rawContentSha256": raw_content_sha256,
        "sanitizerVersion": PUBLIC_VOYAGE_SANITIZER_VERSION,
        "schemaVersion": 2,
        "sourceRevisionId": source_revision_id,
        "tokenizerVersion": tokenizer_version,
    }


def _serialize_payload(
    *,
    prepared: RagV2PreparedPublicDocument,
    metadata: PublicVoyageSourceMetadata,
) -> dict[str, object]:
    document = prepared.document
    return {
        "document": {
            "chunks": [
                {
                    "canonicalText": chunk.canonical_text,
                    "canonicalTextSha256": chunk.canonical_text_sha256,
                    "chunkId": chunk.chunk_id,
                    "containsTable": chunk.contains_table,
                    "documentId": chunk.document_id,
                    "headingPath": list(chunk.heading_path),
                    "locator": chunk.locator,
                    "sequence": chunk.sequence,
                    "tokenCount": chunk.token_count,
                }
                for chunk in document.chunks
            ],
            "documentId": document.document_id,
            "externalProcessingEligible": document.external_processing_eligible,
            "normalizedContentSha256": document.normalized_content_sha256,
            "rawContentSha256": document.raw_content_sha256,
            "sourceId": document.source_id,
            "sourceRevisionId": document.source_revision_id,
            "sourceScope": document.source_scope,
        },
        "documentIr": prepared.document_ir,
        "embeddingInputs": [
            {
                "chunkRevisionId": item.chunk_revision_id,
                "contextSetHash": item.context_set_hash,
                "embeddingInputHash": item.embedding_input_hash,
                "embeddingProfileId": item.embedding_profile_id,
                "text": item.text,
            }
            for item in prepared.embedding_inputs
        ],
        "metadata": {
            "accessEvidenceSha256": metadata.access_evidence_sha256,
            "canonicalHttpsUrl": metadata.canonical_https_url,
            "citationTitle": metadata.citation_title,
            "externalEmbeddingAllowed": metadata.external_embedding_allowed,
            "externalGenerationAllowed": metadata.external_generation_allowed,
            "licenseEvidenceSha256": metadata.license_evidence_sha256,
            "localProcessingAllowed": metadata.local_processing_allowed,
            "machineFetchAllowed": metadata.machine_fetch_allowed,
            "oaSourceCard": metadata.oa_source_card,
            "oaTrackId": metadata.oa_track_id,
            "retrievalTopics": list(metadata.retrieval_topics),
            "sourceCardSha256": metadata.source_card_sha256,
        },
        "sourceRevisionSha256": prepared.source_revision_sha256,
    }


def _deserialize_payload(
    payload: dict[str, object],
) -> tuple[RagV2PreparedPublicDocument, PublicVoyageSourceMetadata]:
    try:
        raw_document = _dict(payload["document"])
        raw_chunks = _list(raw_document["chunks"])
        chunks = tuple(
            RagV2CanonicalDocumentChunk(
                chunk_id=_str(_dict(item)["chunkId"]),
                document_id=_str(_dict(item)["documentId"]),
                sequence=_int(_dict(item)["sequence"]),
                heading_path=tuple(_str(value) for value in _list(_dict(item)["headingPath"])),
                locator=_dict(_dict(item)["locator"]),
                canonical_text=_str(_dict(item)["canonicalText"]),
                canonical_text_sha256=_str(_dict(item)["canonicalTextSha256"]),
                token_count=_int(_dict(item)["tokenCount"]),
                contains_table=_bool(_dict(item)["containsTable"]),
            )
            for item in raw_chunks
        )
        scope = _str(raw_document["sourceScope"])
        if scope not in {"EXACT30", "OA112"}:
            raise ValueError("scope")
        document = RagV2DocumentMaterialization(
            document_id=_str(raw_document["documentId"]),
            source_scope=cast(PublicScope, scope),
            source_id=_str(raw_document["sourceId"]),
            source_revision_id=_str(raw_document["sourceRevisionId"]),
            raw_content_sha256=_str(raw_document["rawContentSha256"]),
            normalized_content_sha256=_str(raw_document["normalizedContentSha256"]),
            external_processing_eligible=_bool(raw_document["externalProcessingEligible"]),
            chunks=chunks,
        )
        inputs = tuple(
            RagEmbeddingInput(
                chunk_revision_id=_str(_dict(item)["chunkRevisionId"]),
                embedding_profile_id=_str(_dict(item)["embeddingProfileId"]),
                text=_str(_dict(item)["text"]),
                embedding_input_hash=_str(_dict(item)["embeddingInputHash"]),
                context_set_hash=_optional_str(_dict(item)["contextSetHash"]),
            )
            for item in _list(payload["embeddingInputs"])
        )
        raw_metadata = _dict(payload["metadata"])
        metadata = PublicVoyageSourceMetadata(
            citation_title=_str(raw_metadata["citationTitle"]),
            retrieval_topics=tuple(_str(value) for value in _list(raw_metadata["retrievalTopics"])),
            canonical_https_url=_str(raw_metadata["canonicalHttpsUrl"]),
            source_card_sha256=_optional_str(raw_metadata["sourceCardSha256"]),
            machine_fetch_allowed=_bool(raw_metadata["machineFetchAllowed"]),
            local_processing_allowed=_bool(raw_metadata["localProcessingAllowed"]),
            external_embedding_allowed=_bool(raw_metadata["externalEmbeddingAllowed"]),
            external_generation_allowed=_bool(raw_metadata["externalGenerationAllowed"]),
            oa_track_id=_optional_str(raw_metadata["oaTrackId"]),
            oa_source_card=_optional_dict(raw_metadata["oaSourceCard"]),
            license_evidence_sha256=_optional_str(raw_metadata["licenseEvidenceSha256"]),
            access_evidence_sha256=_optional_str(raw_metadata["accessEvidenceSha256"]),
        )
        prepared = RagV2PreparedPublicDocument(
            document=document,
            embedding_inputs=inputs,
            source_revision_sha256=_str(payload["sourceRevisionSha256"]),
            document_ir=_dict(payload["documentIr"]),
        )
    except (KeyError, TypeError, ValueError):
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_PAYLOAD") from None
    return prepared, metadata


def _validate_prepared(
    *,
    prepared: object,
    metadata: object,
    parser_version: str,
) -> None:
    if not isinstance(prepared, RagV2PreparedPublicDocument) or not isinstance(
        metadata, PublicVoyageSourceMetadata
    ):
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_PAYLOAD")
    document = prepared.document
    evidence = prepared.document_ir.get("parserEvidence")
    if not isinstance(evidence, dict):
        # exact-30 source-card parser도 같은 parserVersion projection을 제공해야 한다.
        evidence = prepared.document_ir.get("parser")
    actual_parser_version = evidence.get("parserVersion") if isinstance(evidence, dict) else None
    if (
        document.source_scope not in {"EXACT30", "OA112"}
        or not document.external_processing_eligible
        or actual_parser_version != parser_version
        or not _SHA256.fullmatch(document.raw_content_sha256)
        or not _SHA256.fullmatch(document.normalized_content_sha256)
        or not _SHA256.fullmatch(prepared.source_revision_sha256)
        or not document.chunks
        or len(document.chunks) != len(prepared.embedding_inputs)
        or metadata.local_processing_allowed is not True
        or metadata.external_embedding_allowed is not True
        or metadata.external_generation_allowed is not True
    ):
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_PAYLOAD")
    for chunk, embedding_input in zip(document.chunks, prepared.embedding_inputs, strict=True):
        if (
            chunk.document_id != document.document_id
            or type(chunk.token_count) is not int
            or not 1 <= chunk.token_count <= 600
            or hashlib.sha256(chunk.canonical_text.encode("utf-8")).hexdigest()
            != chunk.canonical_text_sha256
            or embedding_input.chunk_revision_id != chunk.chunk_id
            or embedding_input.text != chunk.canonical_text
            or embedding_input.embedding_profile_id != "voyage_context_4_1024_v1"
            or not _SHA256.fullmatch(embedding_input.embedding_input_hash)
            or not isinstance(embedding_input.context_set_hash, str)
            or not _SHA256.fullmatch(embedding_input.context_set_hash)
        ):
            raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_PAYLOAD")


def _ensure_checkpoint_directories(local_corpus_root: Path, *, scope: PublicScope) -> Path:
    _secure_directory(local_corpus_root)
    derived = local_corpus_root / _DERIVED_DIRECTORY
    scope_root = derived / scope.lower()
    temp_root = derived / _TEMP_DIRECTORY
    for path in (derived, temp_root, scope_root):
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as error:
            raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_BOUNDARY") from error
        # 기존 leaf에는 chmod를 호출하지 않는다. symlink swap이 외부 target mode를 바꾸는 것을 막고
        # lstat 기반 exact-mode 검증이 안전한 directory만 통과시킨다.
        _secure_directory(path)
    return scope_root


def _require_checkpoint_directories(local_corpus_root: Path, *, scope: PublicScope) -> Path:
    _secure_directory(local_corpus_root)
    derived = local_corpus_root / _DERIVED_DIRECTORY
    scope_root = derived / scope.lower()
    _secure_directory(derived)
    _secure_directory(scope_root)
    return scope_root


def _atomic_write(*, target: Path, encoded: bytes) -> None:
    descriptor = -1
    temporary = ""
    try:
        temp_root = target.parent.parent / _TEMP_DIRECTORY
        _secure_directory(temp_root)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".checkpoint-{target.parent.name}-",
            dir=temp_root,
        )
        os.fchmod(descriptor, 0o600)
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, target)
        temporary = ""
        _secure_file(target)
    except OSError as error:
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_BOUNDARY") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _secure_directory(path: Path) -> None:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or ".." in path.parts
        or os.name == "nt"
    ):
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_BOUNDARY")
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_BOUNDARY") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_BOUNDARY")


def _secure_file(path: Path) -> int:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_BOUNDARY") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or not 1 <= metadata.st_size <= _MAX_CHECKPOINT_BYTES
    ):
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_BOUNDARY")
    return metadata.st_size


def _read_secure_file(path: Path) -> bytes:
    expected_size = _secure_file(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_BOUNDARY") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            metadata.st_size != expected_size
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_BOUNDARY")
        chunks = bytearray()
        while len(chunks) <= _MAX_CHECKPOINT_BYTES:
            piece = os.read(descriptor, min(1024 * 1024, _MAX_CHECKPOINT_BYTES + 1 - len(chunks)))
            if not piece:
                break
            chunks.extend(piece)
        if len(chunks) != expected_size:
            raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_BOUNDARY")
        return bytes(chunks)
    finally:
        os.close(descriptor)


def _decode_envelope(raw: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_DIGEST") from None
    if not isinstance(decoded, dict):
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_DIGEST")
    return decoded


def _validate_versions(*, parser_version: str, tokenizer_version: str) -> None:
    if _VERSION.fullmatch(parser_version) is None or _VERSION.fullmatch(tokenizer_version) is None:
        raise RagV2VoyageCheckpointError("VOYAGE_CHECKPOINT_IDENTITY")


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("dict")
    return value


def _optional_dict(value: object) -> dict[str, object] | None:
    return None if value is None else _dict(value)


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("list")
    return value


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("str")
    return value


def _optional_str(value: object) -> str | None:
    return None if value is None else _str(value)


def _int(value: object) -> int:
    if type(value) is not int:
        raise ValueError("int")
    return value


def _bool(value: object) -> bool:
    if type(value) is not bool:
        raise ValueError("bool")
    return value
