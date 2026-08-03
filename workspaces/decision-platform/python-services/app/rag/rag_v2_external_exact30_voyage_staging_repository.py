from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

import psycopg
from psycopg.types.json import Jsonb

from app.rag.rag_v2_external_exact30_voyage_runner import (
    RagV2PublicVoyageComponentContext,
    RagV2VoyageMaterializedPublicDocument,
)
from app.rag.rag_v2_external_exact30_voyage_staging import (
    build_external_exact30_voyage_staging_payload,
)

_WRITER_ROLE = "decision_rag_writer"
_COMPONENT_SCOPE = "EXACT30"
_VOYAGE_PROFILE_ID = "voyage_context_4_1024_v1"
_GENERATION_ID = re.compile(r"^rgr_[0-9a-f]{32}$")
_RUN_ID = re.compile(r"^rgr_run_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STAGE_FUNCTION = "public.stage_rag_v2_immutable_external_exact30_voyage_document(jsonb)"
_WRITER_FORBIDDEN_TABLES = (
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
    "rag_v2_immutable_external_exact30_source_allowlist",
    "rag_v2_immutable_external_exact30_voyage_component_manifests",
)

ExternalExact30VoyageRecord: TypeAlias = RagV2VoyageMaterializedPublicDocument


class ExternalExact30VoyageStagingRepositoryError(ValueError):
    """external-safe exact-30 Voyage staging capability 또는 receipt가 drift했다."""


@dataclass(frozen=True, slots=True)
class ExternalExact30VoyageStagingReceipt:
    """source 한 건의 content-free, resumable Voyage staging 결과다."""

    component_generation_id: str
    materialization_run_id: str
    component_scope: Literal["EXACT30"]
    embedding_profile_id: Literal["voyage_context_4_1024_v1"]
    state: Literal["STAGING", "STAGED"]
    source_reused: bool
    source_count: int
    chunk_count: int


class PsycopgExternalExact30VoyageStagingRepository:
    """`decision_rag_writer`로 V37 restricted writer capability만 호출한다.

    source별 독립 transaction은 local process crash 후 stage를 resume하게 하지만, raw card body,
    vector, DSN은 transaction parameter 밖의 receipt, logger, command output에 복사하지 않는다.
    """

    def __init__(self, *, database_dsn: str) -> None:
        if not database_dsn or len(database_dsn) > 4_096:
            raise ExternalExact30VoyageStagingRepositoryError("EXTERNAL_EXACT30_VOYAGE_STAGE_DATABASE_DSN")
        self._database_dsn = database_dsn

    def stage(
        self,
        *,
        record: ExternalExact30VoyageRecord,
        context: RagV2PublicVoyageComponentContext,
    ) -> ExternalExact30VoyageStagingReceipt:
        """one external-safe source를 immutable component에 stage한다.

        DB definer function은 source-card allowlist, full manifest, vector/context identity를 다시
        검증한다. 이 call은 provider transport, evaluation, public bundle activation을 만들지 않는다.
        """

        payload = build_external_exact30_voyage_staging_payload(record, context=context)
        try:
            with psycopg.connect(
                self._database_dsn,
                autocommit=False,
                connect_timeout=2,
            ) as connection:
                _attest_writer_connection(connection)
                with connection.transaction():
                    _set_transaction_timeouts(connection)
                    row = connection.execute(
                        """
                        SELECT component_generation_id, materialization_run_id, state, source_reused,
                               source_count, chunk_count
                        FROM public.stage_rag_v2_immutable_external_exact30_voyage_document(%s::jsonb)
                        """,
                        (Jsonb(payload),),
                    ).fetchone()
        except ExternalExact30VoyageStagingRepositoryError:
            raise
        except psycopg.Error:
            # Server detail can contain source metadata; callers receive only a stable resumable marker.
            raise ExternalExact30VoyageStagingRepositoryError(
                "EXTERNAL_EXACT30_VOYAGE_STAGE_REJECTED"
            ) from None
        return _staging_receipt(row, context=context)

    def stage_component(
        self,
        *,
        records: Sequence[ExternalExact30VoyageRecord],
        context: RagV2PublicVoyageComponentContext,
    ) -> tuple[ExternalExact30VoyageStagingReceipt, ...]:
        """complete exact-30 component를 canonical source order와 source transaction으로 stage한다."""

        payloads = _component_payloads(records=records, context=context)
        receipts: list[ExternalExact30VoyageStagingReceipt] = []
        try:
            with psycopg.connect(
                self._database_dsn,
                autocommit=True,
                connect_timeout=2,
            ) as connection:
                _attest_writer_connection(connection)
                for payload in payloads:
                    with connection.transaction():
                        _set_transaction_timeouts(connection)
                        row = connection.execute(
                            """
                            SELECT component_generation_id, materialization_run_id, state, source_reused,
                                   source_count, chunk_count
                            FROM public.stage_rag_v2_immutable_external_exact30_voyage_document(%s::jsonb)
                            """,
                            (Jsonb(payload),),
                        ).fetchone()
                    receipts.append(_staging_receipt(row, context=context))
        except ExternalExact30VoyageStagingRepositoryError:
            raise
        except psycopg.Error:
            raise ExternalExact30VoyageStagingRepositoryError(
                "EXTERNAL_EXACT30_VOYAGE_STAGE_REJECTED"
            ) from None
        return tuple(receipts)


def _component_payloads(
    *,
    records: Sequence[ExternalExact30VoyageRecord],
    context: RagV2PublicVoyageComponentContext,
) -> tuple[dict[str, object], ...]:
    """full component identity를 process boundary에서 먼저 닫아 partial write를 차단한다."""

    _validate_context(context)
    if len(records) != context.expected_source_count:
        raise ExternalExact30VoyageStagingRepositoryError(
            "EXTERNAL_EXACT30_VOYAGE_STAGE_COMPONENT_MEMBERSHIP"
        )
    payloads = tuple(
        build_external_exact30_voyage_staging_payload(record, context=context) for record in records
    )
    source_ids: list[str] = []
    source_revision_ids: list[str] = []
    chunk_count = 0
    for payload in payloads:
        source = payload.get("source")
        if not isinstance(source, dict):
            raise ExternalExact30VoyageStagingRepositoryError(
                "EXTERNAL_EXACT30_VOYAGE_STAGE_COMPONENT_MEMBERSHIP"
            )
        source_id = source.get("sourceId")
        source_revision_id = source.get("sourceRevisionId")
        chunks = source.get("chunks")
        if (
            not isinstance(source_id, str)
            or not isinstance(source_revision_id, str)
            or not isinstance(chunks, list)
            or not chunks
        ):
            raise ExternalExact30VoyageStagingRepositoryError(
                "EXTERNAL_EXACT30_VOYAGE_STAGE_COMPONENT_MEMBERSHIP"
            )
        source_ids.append(source_id)
        source_revision_ids.append(source_revision_id)
        chunk_count += len(chunks)
    if (
        len(set(source_ids)) != context.expected_source_count
        or len(set(source_revision_ids)) != context.expected_source_count
        or chunk_count != context.expected_chunk_count
    ):
        raise ExternalExact30VoyageStagingRepositoryError(
            "EXTERNAL_EXACT30_VOYAGE_STAGE_COMPONENT_MEMBERSHIP"
        )
    return tuple(sorted(payloads, key=_payload_source_id_for_order))


def _payload_source_id_for_order(payload: dict[str, object]) -> bytes:
    """component-wide membership ordinal은 canonical UTF-8 source ID order만 사용한다."""

    source = payload.get("source")
    if not isinstance(source, dict):
        raise ExternalExact30VoyageStagingRepositoryError(
            "EXTERNAL_EXACT30_VOYAGE_STAGE_COMPONENT_MEMBERSHIP"
        )
    source_id = source.get("sourceId")
    if not isinstance(source_id, str):
        raise ExternalExact30VoyageStagingRepositoryError(
            "EXTERNAL_EXACT30_VOYAGE_STAGE_COMPONENT_MEMBERSHIP"
        )
    return source_id.encode("utf-8")


def _validate_context(context: RagV2PublicVoyageComponentContext) -> None:
    """repository도 source module과 같은 S4.7C full-component envelope를 강제한다."""

    if (
        context.component_scope != _COMPONENT_SCOPE
        or context.embedding_profile_id != _VOYAGE_PROFILE_ID
        or context.expected_source_count != 30
        or not 30 <= context.expected_chunk_count <= 100_000
        or _GENERATION_ID.fullmatch(context.component_generation_id) is None
        or _RUN_ID.fullmatch(context.materialization_run_id) is None
        or _SHA256.fullmatch(context.generation_hash) is None
        or _SHA256.fullmatch(context.manifest_hash) is None
        or _SHA256.fullmatch(context.source_card_corpus_manifest_sha256) is None
        or len(context.member_digests) != 30
        or len(set(context.member_digests)) != 30
        or any(_SHA256.fullmatch(member_digest) is None for member_digest in context.member_digests)
    ):
        raise ExternalExact30VoyageStagingRepositoryError(
            "EXTERNAL_EXACT30_VOYAGE_STAGE_COMPONENT_CONTEXT"
        )


def _set_transaction_timeouts(connection: psycopg.Connection[Any]) -> None:
    """advisory lock 재개를 bounded하게 유지해 DB wait가 provider retry로 확장되지 않게 한다."""

    connection.execute("SET LOCAL statement_timeout = '60s'")
    connection.execute("SET LOCAL lock_timeout = '10s'")
    connection.execute("SET LOCAL idle_in_transaction_session_timeout = '75s'")


def _staging_receipt(
    row: tuple[object, ...] | None,
    *,
    context: RagV2PublicVoyageComponentContext,
) -> ExternalExact30VoyageStagingReceipt:
    """definer result도 closed receipt로 재검증해 malformed DB output을 fail-close한다."""

    _validate_context(context)
    if (
        row is None
        or len(row) != 6
        or not isinstance(row[0], str)
        or row[0] != context.component_generation_id
        or not isinstance(row[1], str)
        or row[1] != context.materialization_run_id
        or row[2] not in {"STAGING", "STAGED"}
        or type(row[3]) is not bool
        or type(row[4]) is not int
        or type(row[5]) is not int
        or not 0 < row[4] <= context.expected_source_count
        or not 0 < row[5] <= context.expected_chunk_count
        or row[5] < row[4]
        or (
            row[2] == "STAGED"
            and (row[4] != context.expected_source_count or row[5] != context.expected_chunk_count)
        )
    ):
        raise ExternalExact30VoyageStagingRepositoryError(
            "EXTERNAL_EXACT30_VOYAGE_STAGE_RECEIPT"
        )
    return ExternalExact30VoyageStagingReceipt(
        component_generation_id=row[0],
        materialization_run_id=row[1],
        component_scope="EXACT30",
        embedding_profile_id="voyage_context_4_1024_v1",
        state=row[2],
        source_reused=row[3],
        source_count=row[4],
        chunk_count=row[5],
    )


def _attest_writer_connection(connection: psycopg.Connection[Any]) -> None:
    """writer DSN가 V37 definer 한 개와 무권한 table boundary만 가진지 확인한다."""

    if connection.execute("SELECT current_user").fetchone() != (_WRITER_ROLE,):
        raise ExternalExact30VoyageStagingRepositoryError(
            "EXTERNAL_EXACT30_VOYAGE_STAGE_WRITER_ROLE"
        )
    for table in _WRITER_FORBIDDEN_TABLES:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            row = connection.execute(
                "SELECT has_table_privilege(current_user, %s, %s)",
                (f"public.{table}", privilege),
            ).fetchone()
            if row is not None and row[0] is True:
                raise ExternalExact30VoyageStagingRepositoryError(
                    "EXTERNAL_EXACT30_VOYAGE_STAGE_WRITER_PRIVILEGE"
                )
    row = connection.execute(
        "SELECT has_function_privilege(current_user, %s, 'EXECUTE')",
        (_STAGE_FUNCTION,),
    ).fetchone()
    if row is None or row[0] is not True:
        raise ExternalExact30VoyageStagingRepositoryError(
            "EXTERNAL_EXACT30_VOYAGE_STAGE_WRITER_PRIVILEGE"
        )
