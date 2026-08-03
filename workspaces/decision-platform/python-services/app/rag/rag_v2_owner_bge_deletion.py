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
_ACTIVATION_RECEIPT_ID = re.compile(r"^rgr_act_[0-9a-f]{32}$")
_DELETION_RECEIPT_ID = re.compile(r"^rgr_del_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DELETE_FUNCTION = (
    "public.delete_rag_v2_immutable_owner_document(text,text,text,text,bigint,text,text,text)"
)
_REPLACEMENT_DELETE_FUNCTION = (
    "public.replace_and_delete_rag_v2_immutable_owner_document(text,text,text,text,text)"
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
    """admin DSN로 V25 hard-delete function 하나만 실행하는 local owner deletion adapter다.

    직접 table DML/read 권한은 connection attestation에서 거부한다. unreferenced staging document는
    V25 hard-delete만 호출하고, active bundle 때문에 거절된 경우에만 V33이 immutable replacement
    assembly·CAS activation·hard-delete를 한 transaction으로 수행한다.
    """

    def __init__(self, *, database_dsn: str) -> None:
        self._database_dsn = database_dsn

    def delete(
        self,
        *,
        owner_user_id: str,
        document_id: str,
    ) -> RagV2OwnerBgeDeletionReceipt:
        """owner control record의 document를 replacement activation 뒤 hard-delete한다.

        active 여부를 Python이 raw table query로 추측하지 않는다. 먼저 least-capability V25 staged
        delete를 시도하고 only `23514` gate일 때 V33 atomic replacement path로 재시도한다.
        """

        if not _OWNER_ID.fullmatch(owner_user_id) or not _DOCUMENT_ID.fullmatch(document_id):
            raise OwnerBgeDeletionError("OWNER_BGE_DELETE_ARGUMENT")
        deletion_receipt_id = f"rgr_del_{uuid.uuid4().hex}"
        activation_receipt_id = f"rgr_act_{uuid.uuid4().hex}"
        reason_hash = hashlib.sha256(
            f"rag-v2-owner-local-delete-v1|{owner_user_id}|{document_id}".encode("utf-8")
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
                try:
                    with connection.transaction():
                        _set_delete_timeouts(connection)
                        row = _delete_unreferenced_document(
                            connection,
                            owner_user_id=owner_user_id,
                            document_id=document_id,
                            deletion_receipt_id=deletion_receipt_id,
                            reason_hash=reason_hash,
                        )
                except psycopg.Error as error:
                    if error.sqlstate != "23514":
                        raise
                    # V25의 23514는 active/complete graph가 remaining source graph를 보호한
                    # fail-closed marker다. Nested transaction은 savepoint를 rollback한 뒤에만
                    # V33 replacement operation을 시작하게 해 partial deletion을 만들지 않는다.
                    with connection.transaction():
                        _set_delete_timeouts(connection)
                        row = _delete_active_document_with_replacement(
                            connection,
                            owner_user_id=owner_user_id,
                            document_id=document_id,
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


def _delete_unreferenced_document(
    connection: psycopg.Connection[Any],
    *,
    owner_user_id: str,
    document_id: str,
    deletion_receipt_id: str,
    reason_hash: str,
) -> tuple[object, ...] | None:
    """V25 staged-only delete가 active graph 보호를 거부하는지를 DB 안에서 판정한다."""

    return connection.execute(
        """
        SELECT public.delete_rag_v2_immutable_owner_document(
          %s,
          %s,
          NULL::text,
          NULL::text,
          NULL::bigint,
          NULL::text,
          %s,
          %s
        )
        """,
        (owner_user_id, document_id, deletion_receipt_id, reason_hash),
    ).fetchone()


def _delete_active_document_with_replacement(
    connection: psycopg.Connection[Any],
    *,
    owner_user_id: str,
    document_id: str,
    activation_receipt_id: str,
    deletion_receipt_id: str,
    reason_hash: str,
) -> tuple[object, ...] | None:
    """V33만 호출해 replacement ready pointer와 old source hard-delete의 atomicity를 보장한다."""

    return connection.execute(
        """
        SELECT public.replace_and_delete_rag_v2_immutable_owner_document(
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
            activation_receipt_id,
            deletion_receipt_id,
            reason_hash,
        ),
    ).fetchone()


def _attest_admin_connection(connection: psycopg.Connection[Any]) -> None:
    """admin DSN가 raw graph table access 없이 exact hard-delete function만 실행하는지 검증한다."""

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
    for signature in (_DELETE_FUNCTION, _REPLACEMENT_DELETE_FUNCTION):
        row = connection.execute(
            "SELECT has_function_privilege(current_user, %s, 'EXECUTE')",
            (signature,),
        ).fetchone()
        if row is None or row[0] is not True:
            raise OwnerBgeDeletionError("OWNER_BGE_DELETE_ADMIN_PRIVILEGE")
