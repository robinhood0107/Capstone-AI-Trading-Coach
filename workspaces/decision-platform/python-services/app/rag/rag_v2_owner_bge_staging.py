from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from app.rag.rag_v2_bge_materializer import RagV2BgeMaterializedOwnerDocument

_BGE_PROFILE_ID = "bge_m3_local_1024_v1"
_TOKENIZER_VERSION = "bge-m3-5617a9f-tokenizer-400-600-v1"
_WRITER_ROLE = "decision_rag_writer"
_OWNER_ID = re.compile(r"^usr_[a-z0-9][a-z0-9_-]{2,95}$")
_IMPORT_TICKET_ID = re.compile(r"^rti_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STAGE_FUNCTION = "public.stage_rag_v2_immutable_owner_bge_document(text,text,jsonb)"
_WRITER_FORBIDDEN_TABLES = (
    "rag_v2_immutable_oa_track_catalog",
    "rag_v2_immutable_oa_source_cards",
    "rag_v2_immutable_source_revisions",
    "rag_v2_immutable_chunks",
    "rag_v2_immutable_component_generations",
    "rag_v2_immutable_generation_memberships",
    "rag_v2_immutable_generation_embeddings",
    "rag_v2_immutable_embedding_cache",
    "rag_v2_immutable_materialization_runs",
    "rag_v2_immutable_source_receipts",
    "rag_v2_immutable_chunk_receipts",
    "rag_v2_immutable_embedding_receipts",
    "rag_v2_immutable_import_tickets",
)
_PATH_KEYS = frozenset(("originalPath", "rawPath", "absolutePath", "filePath", "url"))


class OwnerBgeStagingError(ValueError):
    """owner-private BGE staging이 ticket·RLS·DB receipt 경계에서 실패했음을 나타낸다."""


@dataclass(frozen=True, slots=True)
class RagV2OwnerBgeStagingReceipt:
    """raw text/vector/ticket 없이 caller가 status와 resume에 쓰는 staging 결과다."""

    owner_user_id: str
    component_generation_id: str
    materialization_run_id: str
    component_scope: str
    embedding_profile_id: str
    state: str
    source_count: int
    chunk_count: int


def build_owner_bge_staging_payload(
    materialized: RagV2BgeMaterializedOwnerDocument,
) -> dict[str, object]:
    """transient local IR/text/vector를 V28 writer에만 전달할 closed payload로 만든다.

    approved root, relative path, filename, raw bytes는 payload에 넣지 않는다. 이 payload는 DB
    transaction 직전에만 생성하며 log, receipt, history, command-line으로 직렬화하면 안 된다.
    """

    document = materialized.document
    if (
        document.source_scope != "OWNER_PRIVATE"
        or not document.chunks
        or len(document.chunks) != len(materialized.embeddings)
        or not _SHA256.fullmatch(materialized.source_revision_sha256)
    ):
        raise OwnerBgeStagingError("OWNER_BGE_STAGE_MATERIALIZATION_INVALID")
    document_ir = _validated_document_ir(materialized, document_id=document.document_id)
    ordered_chunks = tuple(sorted(document.chunks, key=lambda item: item.sequence))
    if tuple(chunk.sequence for chunk in ordered_chunks) != tuple(
        range(1, len(ordered_chunks) + 1)
    ):
        raise OwnerBgeStagingError("OWNER_BGE_STAGE_CHUNK_ORDINAL")
    embeddings_by_chunk = {item.chunk_id: item for item in materialized.embeddings}
    if len(embeddings_by_chunk) != len(materialized.embeddings):
        raise OwnerBgeStagingError("OWNER_BGE_STAGE_EMBEDDING_IDENTITY")

    chunks: list[dict[str, object]] = []
    embeddings: list[dict[str, object]] = []
    for chunk in ordered_chunks:
        embedding = embeddings_by_chunk.get(chunk.chunk_id)
        if embedding is None or embedding.context_set_hash is not None:
            raise OwnerBgeStagingError("OWNER_BGE_STAGE_EMBEDDING_IDENTITY")
        if not _SHA256.fullmatch(embedding.embedding_input_hash):
            raise OwnerBgeStagingError("OWNER_BGE_STAGE_EMBEDDING_HASH")
        vector = np.asarray(embedding.embedding, dtype=np.float32)
        if (
            vector.shape != (1024,)
            or not np.isfinite(vector).all()
            or not math.isclose(float(np.linalg.norm(vector)), 1.0, rel_tol=0.0, abs_tol=1e-5)
        ):
            raise OwnerBgeStagingError("OWNER_BGE_STAGE_VECTOR")
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
    payload = {
        "canonicalText": canonical_text,
        "canonicalTextSha256": canonical_text_sha256,
        "chunks": chunks,
        "documentId": document.document_id,
        "documentIr": document_ir,
        "embeddingProfileId": _BGE_PROFILE_ID,
        "embeddings": embeddings,
        "mimeType": _required_document_ir_text(document_ir, "mimeType"),
        "normalizedDocumentIrSha256": document.normalized_content_sha256,
        "parserVersion": _parser_version(document_ir),
        "rawContentSha256": document.raw_content_sha256,
        "schemaVersion": 1,
        "sourceId": document.source_id,
        "sourceLocator": dict(ordered_chunks[0].locator),
        "sourceRevisionId": document.source_revision_id,
        "sourceRevisionSha256": materialized.source_revision_sha256,
        "tokenizerVersion": _TOKENIZER_VERSION,
    }
    _assert_payload_is_path_free(payload)
    return payload


class PsycopgRagV2OwnerBgeStagingRepository:
    """`decision_rag_writer` 전용 DSN으로 one-time ticket staging 함수만 호출한다."""

    def __init__(self, *, database_dsn: str) -> None:
        if not database_dsn or len(database_dsn) > 4_096:
            raise OwnerBgeStagingError("OWNER_BGE_STAGE_DATABASE_DSN")
        self._database_dsn = database_dsn

    def stage(
        self,
        *,
        owner_user_id: str,
        import_ticket_id: str,
        materialized: RagV2BgeMaterializedOwnerDocument,
    ) -> RagV2OwnerBgeStagingReceipt:
        """local-only materialization을 owner ticket에 bind해 immutable STAGED graph로 append한다.

        DB credential, owner identity, ticket, canonical text, vector는 모두 process-local 입력이며
        successful return에는 content-free identifiers/counts만 남긴다.
        """

        if not _OWNER_ID.fullmatch(owner_user_id) or not _IMPORT_TICKET_ID.fullmatch(import_ticket_id):
            raise OwnerBgeStagingError("OWNER_BGE_STAGE_ARGUMENT")
        payload = build_owner_bge_staging_payload(materialized)
        try:
            with psycopg.connect(
                self._database_dsn,
                autocommit=False,
                connect_timeout=2,
            ) as connection:
                _attest_writer_connection(connection)
                with connection.transaction():
                    connection.execute("SET LOCAL statement_timeout = '60s'")
                    connection.execute("SET LOCAL lock_timeout = '1s'")
                    connection.execute("SET LOCAL idle_in_transaction_session_timeout = '75s'")
                    row = connection.execute(
                        """
                        SELECT component_generation_id, materialization_run_id, state, source_count, chunk_count
                        FROM public.stage_rag_v2_immutable_owner_bge_document(%s, %s, %s::jsonb)
                        """,
                        (owner_user_id, import_ticket_id, Jsonb(payload)),
                    ).fetchone()
        except OwnerBgeStagingError:
            raise
        except psycopg.Error as error:
            # libpq/DB 오류에는 DSN, source text, vector, ticket이 섞일 수 있어 public path에는
            # SQLSTATE/exception text를 출력하지 않는다.
            raise OwnerBgeStagingError("OWNER_BGE_STAGE_REJECTED") from error

        if (
            row is None
            or len(row) != 5
            or not isinstance(row[0], str)
            or not isinstance(row[1], str)
            or row[2] != "STAGED"
            or type(row[3]) is not int
            or type(row[4]) is not int
            or row[3] != 1
            or row[4] != len(cast(list[object], payload["chunks"]))
        ):
            raise OwnerBgeStagingError("OWNER_BGE_STAGE_RECEIPT")
        return RagV2OwnerBgeStagingReceipt(
            owner_user_id=owner_user_id,
            component_generation_id=row[0],
            materialization_run_id=row[1],
            component_scope="OWNER_PRIVATE",
            embedding_profile_id=_BGE_PROFILE_ID,
            state="STAGED",
            source_count=row[3],
            chunk_count=row[4],
        )


def _validated_document_ir(
    materialized: RagV2BgeMaterializedOwnerDocument,
    *,
    document_id: str,
) -> dict[str, object]:
    """parser snapshot의 identity/hash/path-free 조건을 DB round-trip 전에 다시 닫는다."""

    copied = json.loads(
        json.dumps(
            materialized.document_ir,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    if not isinstance(copied, dict):
        raise OwnerBgeStagingError("OWNER_BGE_STAGE_DOCUMENT_IR")
    source = materialized.document
    expected_source_revision_sha256 = hashlib.sha256(
        json.dumps(
            copied,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    if (
        copied.get("sourceId") != source.source_id
        or copied.get("sourceRevisionId") != source.source_revision_id
        or copied.get("rawContentSha256") != source.raw_content_sha256
        or copied.get("normalizedContentSha256") != source.normalized_content_sha256
        or expected_source_revision_sha256 != materialized.source_revision_sha256
        or any(key in copied for key in _PATH_KEYS)
        or document_id != source.document_id
    ):
        raise OwnerBgeStagingError("OWNER_BGE_STAGE_DOCUMENT_IR")
    for key in ("rawContentSha256", "normalizedContentSha256"):
        value = copied.get(key)
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise OwnerBgeStagingError("OWNER_BGE_STAGE_DOCUMENT_IR")
    _required_document_ir_text(copied, "mimeType")
    _parser_version(copied)
    return cast(dict[str, object], copied)


def _parser_version(document_ir: Mapping[str, object]) -> str:
    evidence = document_ir.get("parserEvidence")
    if not isinstance(evidence, Mapping):
        raise OwnerBgeStagingError("OWNER_BGE_STAGE_DOCUMENT_IR")
    version = evidence.get("parserVersion")
    artifact_hash = evidence.get("parserArtifactSha256")
    if (
        not isinstance(version, str)
        or not version
        or len(version) > 128
        or not isinstance(artifact_hash, str)
        or not _SHA256.fullmatch(artifact_hash)
    ):
        raise OwnerBgeStagingError("OWNER_BGE_STAGE_DOCUMENT_IR")
    return version


def _required_document_ir_text(document_ir: Mapping[str, object], field: str) -> str:
    value = document_ir.get(field)
    if not isinstance(value, str) or not value or len(value) > 128:
        raise OwnerBgeStagingError("OWNER_BGE_STAGE_DOCUMENT_IR")
    return value


def _assert_payload_is_path_free(payload: Mapping[str, object]) -> None:
    """writer input은 raw-derived text/vector를 가질 수 있어도 filesystem locator를 가질 수 없다."""

    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if any(f'"{key}"' in encoded for key in _PATH_KEYS):
        raise OwnerBgeStagingError("OWNER_BGE_STAGE_PATH_LEAK")


def _attest_writer_connection(connection: psycopg.Connection[Any]) -> None:
    """writer DSN가 direct raw-table capability 없이 V28 function 하나만 갖는지 확인한다."""

    if connection.execute("SELECT current_user").fetchone() != (_WRITER_ROLE,):
        raise OwnerBgeStagingError("OWNER_BGE_STAGE_WRITER_ROLE")
    for table in _WRITER_FORBIDDEN_TABLES:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            row = connection.execute(
                "SELECT has_table_privilege(current_user, %s, %s)",
                (f"public.{table}", privilege),
            ).fetchone()
            if row is not None and row[0] is True:
                raise OwnerBgeStagingError("OWNER_BGE_STAGE_WRITER_PRIVILEGE")
    row = connection.execute(
        "SELECT has_function_privilege(current_user, %s, 'EXECUTE')",
        (_STAGE_FUNCTION,),
    ).fetchone()
    if row is None or row[0] is not True:
        raise OwnerBgeStagingError("OWNER_BGE_STAGE_WRITER_PRIVILEGE")
