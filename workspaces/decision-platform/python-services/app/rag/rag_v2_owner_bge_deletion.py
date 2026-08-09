from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import psycopg

_ADMIN_ROLE = "decision_rag_admin"
_OWNER_ID = re.compile(r"^usr_[a-z0-9][a-z0-9_-]{2,95}$")
_DOCUMENT_ID = re.compile(r"^doc_[a-z0-9][a-z0-9_-]{10,95}$")
_DELETE_TICKET_ID = re.compile(r"^rtd_[0-9a-f]{32}$")
_ACTIVATION_RECEIPT_ID = re.compile(r"^rgr_act_[0-9a-f]{32}$")
_DELETION_RECEIPT_ID = re.compile(r"^rgr_del_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DELETE_FUNCTION = (
    "public.delete_rag_v2_immutable_owner_document_with_ticket(text,text,text,text,text,text)"
)
_LEGACY_DELETE_FUNCTIONS = (
    "public.delete_rag_v2_immutable_owner_document(text,text,text,text,bigint,text,text,text)",
    "public.replace_and_delete_rag_v2_immutable_owner_document(text,text,text,text,text)",
)
_ADMIN_FORBIDDEN_TABLES = (
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
    "rag_v2_immutable_public_bundle_pointers",
    "rag_v2_immutable_bundles",
    "rag_v2_immutable_owner_bundle_pointers",
    "rag_v2_immutable_import_tickets",
    "rag_v2_immutable_owner_delete_tickets",
    "rag_v2_immutable_activation_receipts",
    "rag_v2_immutable_deletion_receipts",
    "rag_v2_immutable_owner_document_deletion_tombstones",
)


class OwnerBgeDeletionError(ValueError):
    """owner-private deletion이 bounded admin function 또는 local control 경계에서 실패했음을 나타낸다."""


@dataclass(frozen=True, slots=True)
class RagV2OwnerBgeDeletionReceipt:
    """CLI가 private identity 없이 표시하는 owner document deletion terminal state다."""

    state: Literal["DELETED", "ABSENT"]


class PsycopgRagV2OwnerBgeDeletionRepository:
    """admin DSN로 ticket-bound immutable owner delete wrapper만 실행하는 local adapter다.

    직접 table DML/read와 V25/V33 direct execute는 attestation에서 거부한다. V44 wrapper가 ticket
    consume, staged delete, active replacement/CAS, hard-delete를 하나의 DB transaction으로 묶는다.
    """

    def __init__(self, *, database_dsn: str) -> None:
        self._database_dsn = database_dsn

    def delete(
        self,
        *,
        owner_user_id: str,
        document_id: str,
        delete_ticket_id: str,
    ) -> RagV2OwnerBgeDeletionReceipt:
        """owner control record의 document를 replacement activation 뒤 hard-delete한다.

        Python은 active/staged state를 읽거나 두 direct delete function을 호출하지 않는다. opaque
        delete ticket은 owner/document bind를 DB wrapper가 lock/recheck한 뒤 one atomic operation으로
        소비하며, replay는 stored terminal result만 반환한다.
        """

        if (
            not _OWNER_ID.fullmatch(owner_user_id)
            or not _DOCUMENT_ID.fullmatch(document_id)
            or not _DELETE_TICKET_ID.fullmatch(delete_ticket_id)
        ):
            raise OwnerBgeDeletionError("OWNER_BGE_DELETE_ARGUMENT")
        deletion_receipt_id = f"rgr_del_{uuid.uuid4().hex}"
        activation_receipt_id = f"rgr_act_{uuid.uuid4().hex}"
        reason_hash = hashlib.sha256(
            f"rag-v2-owner-local-delete-v2|{delete_ticket_id}|{document_id}".encode("utf-8")
        ).hexdigest()
        if (
            not _DELETION_RECEIPT_ID.fullmatch(deletion_receipt_id)
            or not _ACTIVATION_RECEIPT_ID.fullmatch(activation_receipt_id)
            or not _SHA256.fullmatch(reason_hash)
        ):
            raise OwnerBgeDeletionError("OWNER_BGE_DELETE_ARGUMENT")
        try:
            with psycopg.connect(
                self._database_dsn,
                autocommit=False,
                connect_timeout=2,
            ) as connection:
                _attest_admin_connection(connection)
                with connection.transaction():
                    _set_delete_timeouts(connection)
                    row = _delete_document_with_ticket(
                        connection,
                        owner_user_id=owner_user_id,
                        document_id=document_id,
                        delete_ticket_id=delete_ticket_id,
                        activation_receipt_id=activation_receipt_id,
                        deletion_receipt_id=deletion_receipt_id,
                        reason_hash=reason_hash,
                    )
        except OwnerBgeDeletionError:
            raise
        except psycopg.Error as error:
            # SQL message에는 document/owner state가 들어갈 수 있으므로 CLI/API로 전달하지 않는다.
            code = (
                "OWNER_BGE_DELETE_BLOCKED"
                if error.sqlstate == "23514"
                else "OWNER_BGE_DELETE_REJECTED"
            )
            raise OwnerBgeDeletionError(code) from error

        if row is None or len(row) != 1 or type(row[0]) is not bool:
            raise OwnerBgeDeletionError("OWNER_BGE_DELETE_RECEIPT")
        return RagV2OwnerBgeDeletionReceipt(state="DELETED" if row[0] else "ABSENT")


def _set_delete_timeouts(connection: psycopg.Connection[Any]) -> None:
    """replacement assembly와 hard-delete에 동일한 bounded transaction limits를 적용한다."""

    connection.execute("SET LOCAL statement_timeout = '60s'")
    connection.execute("SET LOCAL lock_timeout = '1s'")
    connection.execute("SET LOCAL idle_in_transaction_session_timeout = '75s'")


def _delete_document_with_ticket(
    connection: psycopg.Connection[Any],
    *,
    owner_user_id: str,
    document_id: str,
    delete_ticket_id: str,
    activation_receipt_id: str,
    deletion_receipt_id: str,
    reason_hash: str,
) -> tuple[object, ...] | None:
    """V44 wrapper만 호출해 ticket 소비와 active/staged deletion을 DB에서 원자화한다."""

    return connection.execute(
        """
        SELECT public.delete_rag_v2_immutable_owner_document_with_ticket(
          %s,
          %s,
          %s,
          %s,
          %s,
          %s
        )
        """,
        (
            owner_user_id,
            document_id,
            delete_ticket_id,
            activation_receipt_id,
            deletion_receipt_id,
            reason_hash,
        ),
    ).fetchone()


def _attest_admin_connection(connection: psycopg.Connection[Any]) -> None:
    """admin DSN가 raw graph와 legacy delete execute 없이 V44 wrapper만 갖는지 검증한다."""

    if connection.execute("SELECT current_user").fetchone() != (_ADMIN_ROLE,):
        raise OwnerBgeDeletionError("OWNER_BGE_DELETE_ADMIN_ROLE")
    for table in _ADMIN_FORBIDDEN_TABLES:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            row = connection.execute(
                "SELECT has_table_privilege(current_user, %s, %s)",
                (f"public.{table}", privilege),
            ).fetchone()
            if row is not None and row[0] is True:
                raise OwnerBgeDeletionError("OWNER_BGE_DELETE_ADMIN_PRIVILEGE")
    row = connection.execute(
        "SELECT has_function_privilege(current_user, %s, 'EXECUTE')",
        (_DELETE_FUNCTION,),
    ).fetchone()
    if row is None or row[0] is not True:
        raise OwnerBgeDeletionError("OWNER_BGE_DELETE_ADMIN_PRIVILEGE")
    for signature in _LEGACY_DELETE_FUNCTIONS:
        legacy = connection.execute(
            "SELECT has_function_privilege(current_user, %s, 'EXECUTE')",
            (signature,),
        ).fetchone()
        if legacy is None or legacy[0] is not False:
            raise OwnerBgeDeletionError("OWNER_BGE_DELETE_ADMIN_PRIVILEGE")
