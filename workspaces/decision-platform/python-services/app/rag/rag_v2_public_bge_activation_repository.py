"""Immutable public BGE base의 admin-only CAS activation adapter다.

이 모듈은 이미 writer가 평가한 exact-30/OA112 component만 같은 DB transaction에서
prepare→activate한다. 직접 immutable graph를 읽거나 raw corpus를 취급하지 않으며, caller가
pointer version이나 activation receipt ID를 공급할 수 없게 해 stale CAS와 receipt 재사용을 줄인다.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import psycopg

from app.rag.rag_v2_public_bge_staging import RagV2PublicBgeComponentContext

_ADMIN_ROLE = "decision_rag_admin"
_BGE_PROFILE_ID = "bge_m3_local_1024_v1"
_GENERATION_ID = re.compile(r"^rgr_[0-9a-f]{32}$")
_RUN_ID = re.compile(r"^rgr_run_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ACTIVATION_RECEIPT_ID = re.compile(r"^rgr_act_[0-9a-f]{32}$")
_PREPARE_FUNCTION = "public.prepare_rag_v2_immutable_public_base_activation(text,text)"
_ACTIVATE_FUNCTION = "public.activate_rag_v2_immutable_public_base(text,text,bigint,text)"
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
    "rag_v2_immutable_public_component_evaluations",
    "rag_v2_immutable_public_component_manifests",
    "rag_v2_immutable_exact30_source_allowlist",
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


class PublicBgeActivationError(ValueError):
    """public BGE CAS activation이 안전하게 완료되지 않았음을 나타낸다."""


@dataclass(frozen=True, slots=True)
class PublicBgeActivationRequest:
    """같은 local BGE profile의 complete exact-30/OA112 context pair다.

    context는 materializer가 만든 deterministic identity만 담는다. evaluation 결과, pointer version,
    raw source 또는 admin receipt는 이 request를 통해 주입할 수 없다.
    """

    exact30: RagV2PublicBgeComponentContext
    oa112: RagV2PublicBgeComponentContext

    def __post_init__(self) -> None:
        _validate_context_pair(self.exact30, self.oa112)


@dataclass(frozen=True, slots=True)
class RagV2PublicBgeActivationReceipt:
    """content-free public base pointer activation 결과다."""

    exact30_generation_id: str
    oa112_generation_id: str
    embedding_profile_id: Literal["bge_m3_local_1024_v1"]
    previous_pointer_version: int
    new_pointer_version: int
    state: Literal["ACTIVE"]


@dataclass(frozen=True, slots=True)
class _PreparedPublicBaseActivation:
    """definer prepare 함수가 lock 보유 transaction 안에서 반환하는 최소 CAS input이다."""

    expected_pointer_version: int
    activation_required: bool


class PsycopgRagV2PublicBgeActivationRepository:
    """`decision_rag_admin` DSN로 public pointer의 prepare/CAS만 호출한다.

    prepare와 activate는 같은 explicit transaction에서 실행된다. prepare가 held advisory/pointer lock을
    유지하는 동안 V25 CAS를 호출하므로 caller가 pointer version을 직접 read하거나 hard-code할 수 없다.
    """

    def __init__(self, *, database_dsn: str) -> None:
        if not isinstance(database_dsn, str) or not database_dsn or len(database_dsn) > 4_096:
            raise PublicBgeActivationError("PUBLIC_BGE_ACTIVATION_DATABASE_DSN")
        self._database_dsn = database_dsn

    def activate(
        self,
        *,
        request: PublicBgeActivationRequest,
    ) -> RagV2PublicBgeActivationReceipt:
        """evaluated public pair를 idempotently ACTIVE pointer로 전환한다.

        V43 prepare가 이미 같은 pair가 ACTIVE임을 확인하면 V25의 duplicate error를 호출하지 않고
        current pointer version을 receipt로 반환한다. 다른 pointer write와 경쟁하면 sanitized conflict로
        fail-close하여 operator가 whole current pair를 재평가하도록 한다.
        """

        _validate_context_pair(request.exact30, request.oa112)
        activation_receipt_id = f"rgr_act_{uuid.uuid4().hex}"
        if _ACTIVATION_RECEIPT_ID.fullmatch(activation_receipt_id) is None:
            raise PublicBgeActivationError("PUBLIC_BGE_ACTIVATION_ARGUMENT")
        try:
            with psycopg.connect(
                self._database_dsn,
                autocommit=False,
                connect_timeout=2,
            ) as connection:
                _attest_admin_connection(connection)
                with connection.transaction():
                    _set_transaction_timeouts(connection)
                    prepared = _prepared_receipt(
                        connection.execute(
                            """
                            SELECT expected_pointer_version, activation_required
                            FROM public.prepare_rag_v2_immutable_public_base_activation(%s, %s)
                            """,
                            (
                                request.exact30.component_generation_id,
                                request.oa112.component_generation_id,
                            ),
                        ).fetchone()
                    )
                    if not prepared.activation_required:
                        return _receipt(
                            request=request,
                            previous_pointer_version=prepared.expected_pointer_version,
                            new_pointer_version=prepared.expected_pointer_version,
                        )
                    activated = connection.execute(
                        """
                        SELECT public.activate_rag_v2_immutable_public_base(%s, %s, %s, %s)
                        """,
                        (
                            request.exact30.component_generation_id,
                            request.oa112.component_generation_id,
                            prepared.expected_pointer_version,
                            activation_receipt_id,
                        ),
                    ).fetchone()
                    new_pointer_version = _activated_pointer_version(
                        activated,
                        expected=prepared.expected_pointer_version + 1,
                    )
        except PublicBgeActivationError:
            raise
        except psycopg.Error as error:
            # SQL text/DSN/role state는 local operator stdout/API/history에 leak하지 않는다.
            if error.sqlstate == "40001":
                code = "PUBLIC_BGE_ACTIVATION_CONFLICT"
            elif error.sqlstate in {"23514", "55000"}:
                code = "PUBLIC_BGE_ACTIVATION_NOT_READY"
            else:
                code = "PUBLIC_BGE_ACTIVATION_REJECTED"
            raise PublicBgeActivationError(code) from None
        return _receipt(
            request=request,
            previous_pointer_version=prepared.expected_pointer_version,
            new_pointer_version=new_pointer_version,
        )


def _validate_context_pair(
    exact30: RagV2PublicBgeComponentContext,
    oa112: RagV2PublicBgeComponentContext,
) -> None:
    """activation input이 staged writer와 같은 deterministic full-component contract인지 확인한다."""

    _validate_context(exact30, scope="EXACT30", expected_source_count=30)
    _validate_context(oa112, scope="OA112", expected_source_count=112)
    if exact30.component_generation_id == oa112.component_generation_id:
        raise PublicBgeActivationError("PUBLIC_BGE_ACTIVATION_ARGUMENT")


def _validate_context(
    context: RagV2PublicBgeComponentContext,
    *,
    scope: Literal["EXACT30", "OA112"],
    expected_source_count: int,
) -> None:
    """caller-generated context의 count/hash/run binding을 fail-close한다."""

    expected_run_id = (
        "rgr_run_"
        + hashlib.sha256(
            (
                f"rag-v2-public-bge-run|{context.component_generation_id}|{context.manifest_hash}"
            ).encode()
        ).hexdigest()[:32]
    )
    if (
        context.component_scope != scope
        or context.expected_source_count != expected_source_count
        or context.expected_chunk_count < expected_source_count
        or context.embedding_profile_id != _BGE_PROFILE_ID
        or _GENERATION_ID.fullmatch(context.component_generation_id) is None
        or _RUN_ID.fullmatch(context.materialization_run_id) is None
        or _SHA256.fullmatch(context.generation_hash) is None
        or _SHA256.fullmatch(context.manifest_hash) is None
        or context.component_generation_id != f"rgr_{context.generation_hash[:32]}"
        or context.materialization_run_id != expected_run_id
        or len(context.member_digests) != expected_source_count
        or len(set(context.member_digests)) != expected_source_count
        or any(_SHA256.fullmatch(digest) is None for digest in context.member_digests)
    ):
        raise PublicBgeActivationError("PUBLIC_BGE_ACTIVATION_ARGUMENT")


def _set_transaction_timeouts(connection: psycopg.Connection[Any]) -> None:
    """CAS lock ownership을 bounded하게 유지해 UI/BAT operator를 indefinite wait에서 보호한다."""

    connection.execute("SET LOCAL statement_timeout = '60s'")
    connection.execute("SET LOCAL lock_timeout = '10s'")
    connection.execute("SET LOCAL idle_in_transaction_session_timeout = '75s'")


def _attest_admin_connection(connection: psycopg.Connection[Any]) -> None:
    """admin DSN가 direct immutable graph grant 없이 typed activation 함수만 갖는지 확인한다."""

    if connection.execute("SELECT current_user").fetchone() != (_ADMIN_ROLE,):
        raise PublicBgeActivationError("PUBLIC_BGE_ACTIVATION_ADMIN_ROLE")
    for table in _ADMIN_FORBIDDEN_TABLES:
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
                raise PublicBgeActivationError("PUBLIC_BGE_ACTIVATION_ADMIN_PRIVILEGE")
    for signature in (_PREPARE_FUNCTION, _ACTIVATE_FUNCTION):
        row = connection.execute(
            "SELECT has_function_privilege(current_user, %s, 'EXECUTE')",
            (signature,),
        ).fetchone()
        if row is None or row[0] is not True:
            raise PublicBgeActivationError("PUBLIC_BGE_ACTIVATION_ADMIN_PRIVILEGE")


def _prepared_receipt(row: tuple[object, ...] | None) -> _PreparedPublicBaseActivation:
    """definer prepare output을 exact CAS version/bool shape로 닫는다."""

    if (
        row is None
        or len(row) != 2
        or type(row[0]) is not int
        or row[0] < 1
        or type(row[1]) is not bool
    ):
        raise PublicBgeActivationError("PUBLIC_BGE_ACTIVATION_RECEIPT")
    return _PreparedPublicBaseActivation(
        expected_pointer_version=row[0],
        activation_required=row[1],
    )


def _activated_pointer_version(
    row: tuple[object, ...] | None,
    *,
    expected: int,
) -> int:
    """V25 CAS result이 same-transaction next version인지 recheck한다."""

    if row is None or len(row) != 1 or type(row[0]) is not int or row[0] != expected:
        raise PublicBgeActivationError("PUBLIC_BGE_ACTIVATION_RECEIPT")
    return row[0]


def _receipt(
    *,
    request: PublicBgeActivationRequest,
    previous_pointer_version: int,
    new_pointer_version: int,
) -> RagV2PublicBgeActivationReceipt:
    """public operator output에는 IDs/profile/version만 남기고 source details를 배제한다."""

    if previous_pointer_version < 1 or new_pointer_version < previous_pointer_version:
        raise PublicBgeActivationError("PUBLIC_BGE_ACTIVATION_RECEIPT")
    return RagV2PublicBgeActivationReceipt(
        exact30_generation_id=request.exact30.component_generation_id,
        oa112_generation_id=request.oa112.component_generation_id,
        embedding_profile_id="bge_m3_local_1024_v1",
        previous_pointer_version=previous_pointer_version,
        new_pointer_version=new_pointer_version,
        state="ACTIVE",
    )
