from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import psycopg

_ADMIN_ROLE = "decision_rag_admin"
_OWNER_ID = re.compile(r"^usr_[a-z0-9][a-z0-9_-]{2,95}$")
_BUNDLE_ID = re.compile(r"^rgb_[0-9a-f]{32}$")
_GENERATION_ID = re.compile(r"^rgr_[0-9a-f]{32}$")
_ACTIVATION_RECEIPT_ID = re.compile(r"^rgr_act_[0-9a-f]{32}$")
_PREPARE_FUNCTION = "public.prepare_rag_v2_immutable_owner_overlay(text,text)"
_ACTIVATE_FUNCTION = (
    "public.activate_rag_v2_immutable_owner_bundle(text,text,text,bigint,text,text)"
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
    "rag_v2_immutable_consent_events",
    "rag_v2_immutable_import_tickets",
    "rag_v2_retrieval_scope_claims",
    "rag_v2_immutable_activation_receipts",
    "rag_v2_immutable_deletion_receipts",
    "rag_v2_immutable_owner_document_deletion_tombstones",
)


class OwnerOverlayError(ValueError):
    """owner-private overlay의 immutable assembly 또는 CAS activation 실패를 나타낸다."""


@dataclass(frozen=True, slots=True)
class RagV2OwnerOverlayReceipt:
    """owner identity, raw text, ticket 없이 import 후 사용할 active bundle 경계를 나타낸다."""

    bundle_id: str
    component_generation_id: str
    source_count: int
    chunk_count: int
    state: Literal["READY"]


@dataclass(frozen=True, slots=True)
class _PreparedOwnerOverlay:
    """security-definer prepare 함수의 strict receipt를 activation 입력으로 고정한다."""

    bundle_id: str
    component_generation_id: str
    expected_active_bundle_id: str | None
    expected_bundle_version: int
    source_count: int
    chunk_count: int


class PsycopgRagV2OwnerOverlayRepository:
    """admin DSN로 V31 assembly와 V25 CAS activation만 실행하는 owner overlay adapter다.

    직접 source/chunk/vector table은 읽거나 쓰지 않는다. local BGE staging이 끝난 뒤의 complete
    immutable graph만 DB security-definer function 안에서 조립하고, public base와 같은 profile을
    가진 bundle이 atomically READY pointer가 되기 전에는 성공 receipt를 반환하지 않는다.
    """

    def __init__(self, *, database_dsn: str) -> None:
        if not database_dsn or len(database_dsn) > 4_096:
            raise OwnerOverlayError("OWNER_OVERLAY_DATABASE_DSN")
        self._database_dsn = database_dsn

    def prepare_and_activate(self, *, owner_user_id: str) -> RagV2OwnerOverlayReceipt:
        """현재 owner의 complete staged documents를 one immutable bundle로 전환한다.

        caller는 owner ID만 제공하고 document/path/text/vector/ticket을 넘기지 않는다. public BGE
        base가 없거나 source가 아직 complete하지 않으면 function의 fail-closed state를 sanitized
        marker로 반환해 same local control record의 resumable 재실행만 허용한다.
        """

        if not _OWNER_ID.fullmatch(owner_user_id):
            raise OwnerOverlayError("OWNER_OVERLAY_ARGUMENT")
        activation_receipt_id = f"rgr_act_{uuid.uuid4().hex}"
        if not _ACTIVATION_RECEIPT_ID.fullmatch(activation_receipt_id):
            raise OwnerOverlayError("OWNER_OVERLAY_ARGUMENT")
        try:
            with psycopg.connect(
                self._database_dsn,
                autocommit=False,
                connect_timeout=2,
            ) as connection:
                _attest_admin_connection(connection)
                with connection.transaction():
                    connection.execute("SET LOCAL statement_timeout = '60s'")
                    connection.execute("SET LOCAL lock_timeout = '1s'")
                    connection.execute("SET LOCAL idle_in_transaction_session_timeout = '75s'")
                    prepared = _prepared_overlay_receipt(
                        connection.execute(
                            """
                            SELECT
                              bundle_id,
                              owner_private_generation_id,
                              expected_active_bundle_id,
                              expected_bundle_version,
                              source_count,
                              chunk_count
                            FROM public.prepare_rag_v2_immutable_owner_overlay(%s, NULL::text)
                            """,
                            (owner_user_id,),
                        ).fetchone()
                    )
                    if prepared.expected_active_bundle_id != prepared.bundle_id:
                        activated_version = connection.execute(
                            """
                            SELECT public.activate_rag_v2_immutable_owner_bundle(
                              %s,
                              %s,
                              %s,
                              %s,
                              %s,
                              'OWNER_BUNDLE'
                            )
                            """,
                            (
                                owner_user_id,
                                prepared.bundle_id,
                                prepared.expected_active_bundle_id,
                                prepared.expected_bundle_version,
                                activation_receipt_id,
                            ),
                        ).fetchone()
                        _validate_activated_version(
                            activated_version,
                            expected=prepared.expected_bundle_version + 1,
                        )
        except OwnerOverlayError:
            raise
        except psycopg.Error as error:
            # DB exception에는 owner/source state가 섞일 수 있으므로 outer CLI/API에는 SQL text를
            # 노출하지 않는다. 55000/23514는 public base나 staged source의 legitimate resume gate다.
            if error.sqlstate in {"23514", "55000"}:
                code = "OWNER_OVERLAY_NOT_READY"
            elif error.sqlstate == "40001":
                code = "OWNER_OVERLAY_CONFLICT"
            else:
                code = "OWNER_OVERLAY_REJECTED"
            raise OwnerOverlayError(code) from error

        return RagV2OwnerOverlayReceipt(
            bundle_id=prepared.bundle_id,
            component_generation_id=prepared.component_generation_id,
            source_count=prepared.source_count,
            chunk_count=prepared.chunk_count,
            state="READY",
        )


def _attest_admin_connection(connection: psycopg.Connection[Any]) -> None:
    """admin DSN가 raw graph table capability 없이 exact overlay functions만 갖는지 확인한다."""

    if connection.execute("SELECT current_user").fetchone() != (_ADMIN_ROLE,):
        raise OwnerOverlayError("OWNER_OVERLAY_ADMIN_ROLE")
    for table in _ADMIN_FORBIDDEN_TABLES:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            row = connection.execute(
                "SELECT has_table_privilege(current_user, %s, %s)",
                (f"public.{table}", privilege),
            ).fetchone()
            if row is not None and row[0] is True:
                raise OwnerOverlayError("OWNER_OVERLAY_ADMIN_PRIVILEGE")
    for signature in (_PREPARE_FUNCTION, _ACTIVATE_FUNCTION):
        row = connection.execute(
            "SELECT has_function_privilege(current_user, %s, 'EXECUTE')",
            (signature,),
        ).fetchone()
        if row is None or row[0] is not True:
            raise OwnerOverlayError("OWNER_OVERLAY_ADMIN_PRIVILEGE")


def _prepared_overlay_receipt(row: tuple[object, ...] | None) -> _PreparedOwnerOverlay:
    """DB receipt를 parse해 malformed ID/count가 activation CAS에 닿지 않게 막는다."""

    if (
        row is None
        or len(row) != 6
        or not isinstance(row[0], str)
        or not _BUNDLE_ID.fullmatch(row[0])
        or not isinstance(row[1], str)
        or not _GENERATION_ID.fullmatch(row[1])
        or (row[2] is not None and (not isinstance(row[2], str) or not _BUNDLE_ID.fullmatch(row[2])))
        or type(row[3]) is not int
        or row[3] < 0
        or type(row[4]) is not int
        or row[4] < 0
        or type(row[5]) is not int
        or row[5] < 0
        or (row[4] == 0 and row[5] != 0)
        or (row[4] > 0 and row[5] < row[4])
    ):
        raise OwnerOverlayError("OWNER_OVERLAY_RECEIPT")
    return _PreparedOwnerOverlay(
        bundle_id=row[0],
        component_generation_id=row[1],
        expected_active_bundle_id=row[2],
        expected_bundle_version=row[3],
        source_count=row[4],
        chunk_count=row[5],
    )


def _validate_activated_version(row: tuple[object, ...] | None, *, expected: int) -> None:
    """activation function의 CAS version receipt를 exact next version으로 확인한다."""

    if row is None or len(row) != 1 or type(row[0]) is not int or row[0] != expected:
        raise OwnerOverlayError("OWNER_OVERLAY_ACTIVATION_RECEIPT")
