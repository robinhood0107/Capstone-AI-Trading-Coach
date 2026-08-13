"""사용자 선택형 owner Voyage import의 one-request, retry-zero 경계다.

이 모듈은 packet이나 API key를 직접 읽지 않는다. caller가 exact activation에 묶인 transport와
writer repository를 주입해야 하며, canonical text와 vector는 completion transaction까지만 유지한다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

import numpy as np
import psycopg
from psycopg.types.json import Jsonb

from app.rag.pre_s5_voyage_transport import (
    PreS5VoyageDocumentBatchResult,
    PreS5VoyageTransportError,
)
from app.rag.rag_v2_bge_materializer import RagV2PreparedOwnerDocument
from app.rag.rag_v2_external_exact30_voyage_runner import (
    VoyagePreChunkedChunk,
    VoyagePreChunkedDocumentGroup,
)
from app.rag.rag_v2_owner_bge_staging import (
    OwnerBgeStagingMetadata,
    build_owner_voyage_staging_payload,
)
from app.rag.rag_v2_voyage_batching import VoyageContextSegment, VoyageDocumentBatch

_PROFILE_ID = "voyage_context_4_1024_v1"
_REQUEST_TOKEN_CAP = 55_000
_CONTEXT_TOKEN_CAP = 32_000
_RESPONSE_HEADROOM_BYTES = 256 * 1024
_RESPONSE_BYTES_PER_CHUNK = 24 * 1024
_TICKET_ID = re.compile(r"^rti_[0-9a-f]{32}$")
_OWNER_ID = re.compile(r"^usr_[a-z0-9][a-z0-9_-]{2,95}$")
_WRITER_ROLE = "decision_rag_writer"
_OWNER_VOYAGE_FUNCTIONS = (
    "public.reserve_rag_v2_owner_voyage_import(text,text,text,text,text,text,text[],integer,integer,integer)",
    "public.complete_rag_v2_owner_voyage_import(text,text,text,jsonb,integer,integer,bigint)",
    "public.fail_rag_v2_owner_voyage_import_unknown_billing(text,text,text,text)",
)
_WRITER_FORBIDDEN_TABLES = (
    "rag_v2_owner_voyage_import_attempts",
    "rag_v2_immutable_import_tickets",
    "rag_v2_immutable_source_revisions",
    "rag_v2_immutable_chunks",
    "rag_v2_immutable_component_generations",
    "rag_v2_immutable_generation_memberships",
    "rag_v2_immutable_generation_embeddings",
)


class OwnerVoyageImportError(ValueError):
    """owner Voyage plan 또는 atomic completion이 closed contract를 위반했다."""


class OwnerVoyageTokenCounter(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def tokenizer_sha256(self) -> str: ...

    def count_texts(self, *, texts: tuple[str, ...], token_cap: int) -> int: ...


class OwnerVoyageTransport(Protocol):
    def embed_document_batch(
        self,
        *,
        batch_plan_sha256: str,
        batch: VoyageDocumentBatch,
    ) -> PreS5VoyageDocumentBatchResult: ...


class OwnerVoyageRepository(Protocol):
    def complete(self, **values: object) -> Mapping[str, object]: ...


class OwnerVoyageAttemptRepository(OwnerVoyageRepository, Protocol):
    def claim_attempt(self, **values: object) -> None: ...

    def mark_unknown_billing(self, **values: object) -> None: ...


@dataclass(frozen=True, slots=True)
class OwnerVoyageImportItem:
    """one ticket, one safe owner Document IR payload, one ordered contextual group다."""

    import_ticket_id: str
    group: VoyagePreChunkedDocumentGroup
    staging_payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class OwnerVoyageImportPlan:
    """한 owner library import를 정확히 one physical request에 묶는다."""

    owner_user_id: str
    owner_scope_sha256: str
    plan_sha256: str
    ticket_set_sha256: str
    tokenizer_sha256: str
    items: tuple[OwnerVoyageImportItem, ...]
    batch: VoyageDocumentBatch

    def content_free_receipt(self) -> dict[str, object]:
        """owner/ticket/text/vector를 제외한 manifest authoring projection만 반환한다."""

        return {
            "batch": self.batch.content_free_receipt(),
            "documentCount": len(self.items),
            "embeddingProfileId": _PROFILE_ID,
            "ownerScopeSha256": self.owner_scope_sha256,
            "planSha256": self.plan_sha256,
            "ticketSetSha256": self.ticket_set_sha256,
            "tokenizerSha256": self.tokenizer_sha256,
        }


@dataclass(frozen=True, slots=True)
class OwnerVoyageImportReceipt:
    """atomic completion 뒤 raw payload를 제외한 owner staging 결과다."""

    component_generation_id: str
    document_count: int
    chunk_count: int
    embedding_profile_id: str
    state: str


def build_owner_voyage_import_item(
    *,
    import_ticket_id: str,
    prepared: RagV2PreparedOwnerDocument,
    metadata: OwnerBgeStagingMetadata,
    tokenizer_version: str,
) -> OwnerVoyageImportItem:
    """one parsed safe document를 transient group과 atomic staging skeleton에 한 번 결합한다."""

    if _TICKET_ID.fullmatch(import_ticket_id) is None:
        raise OwnerVoyageImportError("OWNER_VOYAGE_IMPORT_INVALID")
    payload = build_owner_voyage_staging_payload(
        prepared,
        metadata=metadata,
        tokenizer_version=tokenizer_version,
    )
    inputs = {item.chunk_revision_id: item for item in prepared.embedding_inputs}
    chunks: list[VoyagePreChunkedChunk] = []
    context_hashes: set[str] = set()
    for chunk in sorted(prepared.document.chunks, key=lambda item: item.sequence):
        embedding_input = inputs.get(chunk.chunk_id)
        if embedding_input is None or embedding_input.context_set_hash is None:
            raise OwnerVoyageImportError("OWNER_VOYAGE_IMPORT_INVALID")
        context_hashes.add(embedding_input.context_set_hash)
        chunks.append(
            VoyagePreChunkedChunk(
                chunk_id=chunk.chunk_id,
                canonical_text=chunk.canonical_text,
                canonical_text_sha256=chunk.canonical_text_sha256,
                embedding_input_hash=embedding_input.embedding_input_hash,
                token_count=chunk.token_count,
            )
        )
    if len(context_hashes) != 1 or not chunks:
        raise OwnerVoyageImportError("OWNER_VOYAGE_IMPORT_INVALID")
    return OwnerVoyageImportItem(
        import_ticket_id=import_ticket_id,
        group=VoyagePreChunkedDocumentGroup(
            source_id=prepared.document.source_id,
            source_revision_id=prepared.document.source_revision_id,
            context_set_hash=context_hashes.pop(),
            chunks=tuple(chunks),
        ),
        staging_payload=payload,
    )


class PsycopgOwnerVoyageRepository:
    """writer role의 세 capability만 사용해 attempt와 atomic staging을 기록한다."""

    def __init__(self, *, database_dsn: str) -> None:
        if not isinstance(database_dsn, str) or not 1 <= len(database_dsn) <= 4_096:
            raise OwnerVoyageImportError("OWNER_VOYAGE_DATABASE_DSN")
        self._database_dsn = database_dsn

    def claim_attempt(self, **values: object) -> None:
        """packet/manifest/nonce/ticket-set 결속을 provider socket 전에 원자 선점한다."""

        expected = {
            "approval_manifest_sha256",
            "chunk_count",
            "document_count",
            "expected_input_tokens",
            "nonce_sha256",
            "owner_user_id",
            "packet_sha256",
            "plan_sha256",
            "ticket_ids",
            "ticket_set_sha256",
        }
        ticket_ids = values.get("ticket_ids")
        if (
            set(values) != expected
            or not isinstance(ticket_ids, tuple)
            or not ticket_ids
            or any(not isinstance(value, str) or _TICKET_ID.fullmatch(value) is None for value in ticket_ids)
            or len(set(ticket_ids)) != len(ticket_ids)
        ):
            raise OwnerVoyageImportError("OWNER_VOYAGE_ATTEMPT_INVALID")
        self._execute_boolean(
            """
            SELECT public.reserve_rag_v2_owner_voyage_import(
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                values["owner_user_id"],
                values["plan_sha256"],
                values["packet_sha256"],
                values["approval_manifest_sha256"],
                values["nonce_sha256"],
                values["ticket_set_sha256"],
                list(ticket_ids),
                values["document_count"],
                values["chunk_count"],
                values["expected_input_tokens"],
            ),
            code="OWNER_VOYAGE_ATTEMPT_REJECTED",
        )

    def complete(self, **values: object) -> Mapping[str, object]:
        """usage commit과 모든 ticket/vector staging을 DB function 한 번으로 완료한다."""

        expected = {
            "actual_cost_microusd",
            "expected_input_tokens",
            "items",
            "owner_user_id",
            "packet_sha256",
            "plan_sha256",
            "provider_total_tokens",
        }
        if set(values) != expected or not isinstance(values.get("items"), tuple):
            raise OwnerVoyageImportError("OWNER_VOYAGE_COMPLETION_INVALID")
        try:
            with psycopg.connect(
                self._database_dsn,
                autocommit=False,
                connect_timeout=2,
            ) as connection:
                _attest_writer_connection(connection)
                with connection.transaction():
                    _set_transaction_timeouts(connection, statement_seconds=60)
                    row = connection.execute(
                        """
                        SELECT component_generation_id, document_count, chunk_count, state
                        FROM public.complete_rag_v2_owner_voyage_import(
                          %s, %s, %s, %s::jsonb, %s, %s, %s
                        )
                        """,
                        (
                            values["owner_user_id"],
                            values["plan_sha256"],
                            values["packet_sha256"],
                            Jsonb(list(cast(tuple[object, ...], values["items"]))),
                            values["expected_input_tokens"],
                            values["provider_total_tokens"],
                            values["actual_cost_microusd"],
                        ),
                    ).fetchone()
        except OwnerVoyageImportError:
            raise
        except psycopg.Error as error:
            raise OwnerVoyageImportError("OWNER_VOYAGE_COMPLETION_REJECTED") from error
        if (
            row is None
            or len(row) != 4
            or not isinstance(row[0], str)
            or type(row[1]) is not int
            or type(row[2]) is not int
            or row[3] != "STAGED"
        ):
            raise OwnerVoyageImportError("OWNER_VOYAGE_COMPLETION_INVALID")
        return {
            "componentGenerationId": row[0],
            "documentCount": row[1],
            "chunkCount": row[2],
            "state": row[3],
        }

    def mark_unknown_billing(self, **values: object) -> None:
        """raw response 없이 optional exact validation leaf만 terminal attempt에 남긴다."""

        expected = {
            "owner_user_id",
            "packet_sha256",
            "plan_sha256",
            "response_validation_leaf",
        }
        if set(values) != expected:
            raise OwnerVoyageImportError("OWNER_VOYAGE_UNKNOWN_BILLING_INVALID")
        self._execute_boolean(
            """
            SELECT public.fail_rag_v2_owner_voyage_import_unknown_billing(%s, %s, %s, %s)
            """,
            (
                values["owner_user_id"],
                values["plan_sha256"],
                values["packet_sha256"],
                values["response_validation_leaf"],
            ),
            code="OWNER_VOYAGE_UNKNOWN_BILLING_REJECTED",
        )

    def _execute_boolean(
        self,
        sql: str,
        parameters: tuple[object, ...],
        *,
        code: str,
    ) -> None:
        try:
            with psycopg.connect(
                self._database_dsn,
                autocommit=False,
                connect_timeout=2,
            ) as connection:
                _attest_writer_connection(connection)
                with connection.transaction():
                    _set_transaction_timeouts(connection, statement_seconds=5)
                    row = connection.execute(sql, parameters).fetchone()
        except OwnerVoyageImportError:
            raise
        except psycopg.Error as error:
            raise OwnerVoyageImportError(code) from error
        if row != (True,):
            raise OwnerVoyageImportError(code)


class OwnerVoyageAttemptLease:
    """transport claim과 atomic completion이 같은 exact packet identity를 공유하게 한다."""

    def __init__(
        self,
        *,
        repository: OwnerVoyageAttemptRepository,
        plan: OwnerVoyageImportPlan,
        packet_sha256: str,
        approval_manifest_sha256: str,
        nonce_sha256: str,
    ) -> None:
        if (
            not isinstance(plan, OwnerVoyageImportPlan)
            or not all(
                _sha256(value)
                for value in (packet_sha256, approval_manifest_sha256, nonce_sha256)
            )
        ):
            raise OwnerVoyageImportError("OWNER_VOYAGE_LEASE_INVALID")
        self._repository = repository
        self._plan = plan
        self._packet_sha256 = packet_sha256
        self._approval_manifest_sha256 = approval_manifest_sha256
        self._nonce_sha256 = nonce_sha256
        self._ticket_set_sha256 = plan.ticket_set_sha256
        self._ticket_ids = tuple(item.import_ticket_id for item in plan.items)

    def claim_attempt(self, *, now: datetime) -> None:
        """aware instant만 받아 exact content-free attempt를 한 번 선점한다."""

        if not isinstance(now, datetime) or now.tzinfo is None:
            raise OwnerVoyageImportError("OWNER_VOYAGE_LEASE_INVALID")
        self._repository.claim_attempt(
            approval_manifest_sha256=self._approval_manifest_sha256,
            chunk_count=self._plan.batch.chunk_count,
            document_count=len(self._plan.items),
            expected_input_tokens=self._plan.batch.token_count,
            nonce_sha256=self._nonce_sha256,
            owner_user_id=self._plan.owner_user_id,
            packet_sha256=self._packet_sha256,
            plan_sha256=self._plan.plan_sha256,
            ticket_ids=self._ticket_ids,
            ticket_set_sha256=self._ticket_set_sha256,
        )

    def commit(
        self,
        *,
        expected_input_tokens: int,
        total_tokens: int,
        actual_cost_microusd: int,
    ) -> None:
        """document batch usage는 staging과 분리 commit할 수 없다."""

        del expected_input_tokens, total_tokens, actual_cost_microusd
        raise OwnerVoyageImportError("OWNER_VOYAGE_ATOMIC_STAGE_REQUIRED")

    def mark_unknown_billing(self, *, response_validation_leaf: str | None = None) -> None:
        """transport 불확실성은 retry 없이 same attempt에 terminalize한다."""

        self._repository.mark_unknown_billing(
            owner_user_id=self._plan.owner_user_id,
            packet_sha256=self._packet_sha256,
            plan_sha256=self._plan.plan_sha256,
            response_validation_leaf=response_validation_leaf,
        )

    def complete(self, **values: object) -> Mapping[str, object]:
        """executor의 batch identity를 재검증하고 bound packet으로 DB completion을 호출한다."""

        if values.pop("batch_manifest_sha256", None) != self._plan.batch.batch_manifest_sha256:
            raise OwnerVoyageImportError("OWNER_VOYAGE_COMPLETION_INVALID")
        return self._repository.complete(packet_sha256=self._packet_sha256, **values)


def build_owner_voyage_import_plan(
    *,
    owner_user_id: str,
    items: Sequence[OwnerVoyageImportItem],
    token_counter: OwnerVoyageTokenCounter,
) -> OwnerVoyageImportPlan:
    """safe owner groups를 32K/55K cap 아래 one-request plan으로 고정한다."""

    selected = tuple(items)
    if (
        _OWNER_ID.fullmatch(owner_user_id) is None
        or not 1 <= len(selected) <= 1_000
        or getattr(token_counter, "model", None) != "voyage-context-4"
        or not _sha256(getattr(token_counter, "tokenizer_sha256", None))
    ):
        raise OwnerVoyageImportError("OWNER_VOYAGE_IMPORT_INVALID")

    ticket_ids: set[str] = set()
    source_ids: set[str] = set()
    segments: list[VoyageContextSegment] = []
    total_tokens = 0
    total_chunks = 0
    for ordinal, item in enumerate(selected, 1):
        if (
            not isinstance(item, OwnerVoyageImportItem)
            or _TICKET_ID.fullmatch(item.import_ticket_id) is None
            or item.import_ticket_id in ticket_ids
            or not isinstance(item.staging_payload, Mapping)
            or item.staging_payload.get("embeddingProfileId") != _PROFILE_ID
            or item.staging_payload.get("schemaVersion") != 3
            or not item.group.chunks
            or item.group.source_id in source_ids
        ):
            raise OwnerVoyageImportError("OWNER_VOYAGE_IMPORT_INVALID")
        texts = tuple(chunk.canonical_text for chunk in item.group.chunks)
        try:
            group_tokens = token_counter.count_texts(
                texts=texts,
                token_cap=_REQUEST_TOKEN_CAP,
            )
        except (TypeError, ValueError) as error:
            raise OwnerVoyageImportError("OWNER_VOYAGE_IMPORT_TOO_LARGE") from error
        if not 1 <= group_tokens <= _CONTEXT_TOKEN_CAP:
            raise OwnerVoyageImportError("OWNER_VOYAGE_IMPORT_TOO_LARGE")
        total_tokens += group_tokens
        total_chunks += len(item.group.chunks)
        if total_tokens > _REQUEST_TOKEN_CAP or total_chunks > 16_000:
            raise OwnerVoyageImportError("OWNER_VOYAGE_IMPORT_TOO_LARGE")
        segment_projection = {
            "chunkHashes": [chunk.canonical_text_sha256 for chunk in item.group.chunks],
            "contextSetHash": item.group.context_set_hash,
            "ordinal": ordinal,
            "sourceId": item.group.source_id,
            "sourceRevisionId": item.group.source_revision_id,
            "tokenCount": group_tokens,
        }
        segments.append(
            VoyageContextSegment(
                component_scope="OWNER_PRIVATE",
                source_id=item.group.source_id,
                source_revision_id=item.group.source_revision_id,
                segment_ordinal=1,
                segment_count=1,
                token_count=group_tokens,
                group=item.group,
                segment_manifest_sha256=_canonical_hash(segment_projection),
            )
        )
        ticket_ids.add(item.import_ticket_id)
        source_ids.add(item.group.source_id)

    owner_scope_sha256 = hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()
    ticket_set_sha256 = _ticket_set_sha256(selected)
    plan_projection = {
        "documentCount": len(selected),
        "embeddingProfileId": _PROFILE_ID,
        "groupManifests": [segment.segment_manifest_sha256 for segment in segments],
        "ownerScopeSha256": owner_scope_sha256,
        "schemaVersion": 1,
        "ticketSetSha256": ticket_set_sha256,
        "tokenCount": total_tokens,
        "tokenizerSha256": token_counter.tokenizer_sha256,
    }
    plan_sha256 = _canonical_hash(plan_projection)
    batch_projection = {
        "batchCount": 1,
        "batchOrdinal": 1,
        "chunkCount": total_chunks,
        "groupCount": len(segments),
        "planSha256": plan_sha256,
        "segments": [segment.segment_manifest_sha256 for segment in segments],
        "tokenCount": total_tokens,
    }
    batch_manifest_sha256 = _canonical_hash(batch_projection)
    batch = VoyageDocumentBatch(
        batch_id=f"ps5_voyage_doc_0001_{batch_manifest_sha256[:16]}",
        batch_ordinal=1,
        batch_count=1,
        token_count=total_tokens,
        chunk_count=total_chunks,
        group_count=len(segments),
        estimated_response_bytes=(
            _RESPONSE_HEADROOM_BYTES + total_chunks * _RESPONSE_BYTES_PER_CHUNK
        ),
        segments=tuple(segments),
        batch_manifest_sha256=batch_manifest_sha256,
    )
    return OwnerVoyageImportPlan(
        owner_user_id=owner_user_id,
        owner_scope_sha256=owner_scope_sha256,
        plan_sha256=plan_sha256,
        ticket_set_sha256=ticket_set_sha256,
        tokenizer_sha256=token_counter.tokenizer_sha256,
        items=selected,
        batch=batch,
    )


class RagV2OwnerVoyageImportExecutor:
    """one transport result를 ticket consumption, vector stage, usage commit 한 transaction에 넘긴다."""

    def __init__(self, *, repository: OwnerVoyageRepository) -> None:
        self._repository = repository

    def execute(
        self,
        *,
        plan: OwnerVoyageImportPlan,
        transport: OwnerVoyageTransport,
    ) -> OwnerVoyageImportReceipt:
        """retry/fallback 없이 정확히 한 provider call과 한 atomic completion만 수행한다."""

        try:
            result = transport.embed_document_batch(
                batch_plan_sha256=plan.plan_sha256,
                batch=plan.batch,
            )
        except PreS5VoyageTransportError:
            # transport가 UNKNOWN_BILLING을 먼저 기록한 뒤에도 exact parser leaf가 있으면 같은
            # content-free attempt를 한 번 보강한다. raw body/header/text/vector는 읽지 않는다.
            summary_reader = getattr(transport, "content_free_summary", None)
            marker = getattr(self._repository, "mark_unknown_billing", None)
            if callable(summary_reader) and callable(marker):
                summary = summary_reader()
                leaf = summary.get("responseValidationLeaf") if isinstance(summary, Mapping) else None
                if isinstance(leaf, str):
                    marker(response_validation_leaf=leaf)
            raise
        vectors = np.asarray(result.vectors, dtype=np.float32)
        if vectors.shape != (plan.batch.chunk_count, 1024) or not np.isfinite(vectors).all():
            raise OwnerVoyageImportError("OWNER_VOYAGE_RESPONSE_INVALID")
        completion_items: list[dict[str, object]] = []
        vector_index = 0
        for item in plan.items:
            payload = dict(item.staging_payload)
            embeddings: list[dict[str, object]] = []
            for chunk in item.group.chunks:
                embeddings.append(
                    {
                        "chunkId": chunk.chunk_id,
                        "contextSetHash": item.group.context_set_hash,
                        "embeddingInputHash": chunk.embedding_input_hash,
                        "embedding": [float(value) for value in vectors[vector_index]],
                    }
                )
                vector_index += 1
            payload["embeddings"] = embeddings
            completion_items.append(
                {"importTicketId": item.import_ticket_id, "stagingPayload": payload}
            )
        try:
            row = self._repository.complete(
                owner_user_id=plan.owner_user_id,
                plan_sha256=plan.plan_sha256,
                batch_manifest_sha256=plan.batch.batch_manifest_sha256,
                expected_input_tokens=result.expected_input_tokens,
                provider_total_tokens=result.provider_total_tokens,
                actual_cost_microusd=result.actual_cost_microusd,
                items=tuple(completion_items),
            )
        except Exception:
            # provider response가 검증된 뒤 DB commit이 실패하면 재호출하지 않고 같은 attempt를
            # UNKNOWN_BILLING으로 닫는다. 별도 terminalization 실패도 원래 exception을 가리지 않는다.
            marker = getattr(self._repository, "mark_unknown_billing", None)
            if callable(marker):
                try:
                    marker(response_validation_leaf=None)
                except Exception:
                    pass
            raise
        try:
            component_generation_id_value = row["componentGenerationId"]
            document_count_value = row["documentCount"]
            chunk_count_value = row["chunkCount"]
            state_value = row["state"]
        except KeyError as error:
            raise OwnerVoyageImportError("OWNER_VOYAGE_COMPLETION_INVALID") from error
        if (
            not isinstance(component_generation_id_value, str)
            or type(document_count_value) is not int
            or type(chunk_count_value) is not int
            or not isinstance(state_value, str)
        ):
            raise OwnerVoyageImportError("OWNER_VOYAGE_COMPLETION_INVALID")
        component_generation_id = component_generation_id_value
        document_count = document_count_value
        chunk_count = chunk_count_value
        state = state_value
        if document_count != len(plan.items) or chunk_count != plan.batch.chunk_count or state != "STAGED":
            raise OwnerVoyageImportError("OWNER_VOYAGE_COMPLETION_INVALID")
        return OwnerVoyageImportReceipt(
            component_generation_id=component_generation_id,
            document_count=document_count,
            chunk_count=chunk_count,
            embedding_profile_id=_PROFILE_ID,
            state=state,
        )


def _canonical_hash(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _ticket_set_sha256(items: Sequence[OwnerVoyageImportItem]) -> str:
    """ordered plan ticket set은 plaintext를 DB에 저장하지 않고 one-way digest만 남긴다."""

    encoded = "".join(f"{item.import_ticket_id}\n" for item in items).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _set_transaction_timeouts(
    connection: psycopg.Connection[Any],
    *,
    statement_seconds: int,
) -> None:
    connection.execute(f"SET LOCAL statement_timeout = '{statement_seconds}s'")
    connection.execute("SET LOCAL lock_timeout = '1s'")
    connection.execute(
        f"SET LOCAL idle_in_transaction_session_timeout = '{statement_seconds + 15}s'"
    )


def _attest_writer_connection(connection: psycopg.Connection[Any]) -> None:
    """writer DSN가 owner Voyage functions 외 direct table capability를 갖지 않는지 확인한다."""

    if connection.execute("SELECT current_user").fetchone() != (_WRITER_ROLE,):
        raise OwnerVoyageImportError("OWNER_VOYAGE_WRITER_ROLE")
    for table in _WRITER_FORBIDDEN_TABLES:
        for privilege in (
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "TRUNCATE",
            "REFERENCES",
            "TRIGGER",
        ):
            row = connection.execute(
                "SELECT has_table_privilege(current_user, %s, %s)",
                (f"public.{table}", privilege),
            ).fetchone()
            if row is not None and row[0] is True:
                raise OwnerVoyageImportError("OWNER_VOYAGE_WRITER_PRIVILEGE")
    for function in _OWNER_VOYAGE_FUNCTIONS:
        row = connection.execute(
            "SELECT has_function_privilege(current_user, %s, 'EXECUTE')",
            (function,),
        ).fetchone()
        if row is None or row[0] is not True:
            raise OwnerVoyageImportError("OWNER_VOYAGE_WRITER_PRIVILEGE")
