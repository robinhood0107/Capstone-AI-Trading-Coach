"""Immutable public Voyage base의 admin-only CAS activation adapter다.

EXACT30+OA112 must have been produced by the complete exact manifest-bound document batch set, then separately
staged and evaluated under the same Voyage profile. This adapter exposes only the existing bounded prepare/CAS
functions; it cannot read raw corpus rows, choose a pointer version, or activate a mixed profile.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import psycopg

from app.rag.rag_v2_external_exact30_voyage_runner import RagV2PublicVoyageComponentContext
from app.rag.rag_v2_oa112_voyage_runner import RagV2Oa112VoyageComponentContext

_ADMIN_ROLE = "decision_rag_admin"
_VOYAGE_PROFILE_ID = "voyage_context_4_1024_v1"
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
    "rag_v2_immutable_public_voyage_component_evaluations",
    "rag_v2_immutable_public_voyage_component_manifests",
    "rag_v2_immutable_external_exact30_voyage_component_manifests",
    "rag_v2_immutable_external_exact30_source_allowlist",
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


class PublicVoyageActivationError(ValueError):
    """public Voyage CAS activation이 안전하게 완료되지 않았음을 나타낸다."""


@dataclass(frozen=True, slots=True)
class PublicVoyageActivationRequest:
    """same-profile completed-batch-set exact-30/OA112 context pair다.

    The types deliberately distinguish the external exact-30 consent corpus from the OA112 rights registry.
    A caller cannot attach an owner component or choose a different materialization run to this public pointer.
    """

    exact30: RagV2PublicVoyageComponentContext
    oa112: RagV2Oa112VoyageComponentContext

    def __post_init__(self) -> None:
        _validate_context_pair(self.exact30, self.oa112)


@dataclass(frozen=True, slots=True)
class RagV2PublicVoyageActivationReceipt:
    """content-free public Voyage pointer activation 결과다."""

    exact30_generation_id: str
    oa112_generation_id: str
    embedding_profile_id: Literal["voyage_context_4_1024_v1"]
    previous_pointer_version: int
    new_pointer_version: int
    state: Literal["ACTIVE"]


@dataclass(frozen=True, slots=True)
class _PreparedPublicBaseActivation:
    """definer prepare 함수가 lock-owning transaction 안에서 반환하는 minimal CAS input이다."""

    expected_pointer_version: int
    activation_required: bool


class PsycopgRagV2PublicVoyageActivationRepository:
    """`decision_rag_admin` DSN로 public Voyage pointer의 prepare/CAS만 호출한다.

    V43 prepare와 V25 activation run in one explicit transaction, preventing stale pointer versions and keeping
    all public profile selection in the evaluated component identities rather than a CLI/API argument.
    """

    def __init__(self, *, database_dsn: str) -> None:
        if not isinstance(database_dsn, str) or not database_dsn or len(database_dsn) > 4_096:
            raise PublicVoyageActivationError("PUBLIC_VOYAGE_ACTIVATION_DATABASE_DSN")
        self._database_dsn = database_dsn

    def activate(
        self,
        *,
        request: PublicVoyageActivationRequest,
    ) -> RagV2PublicVoyageActivationReceipt:
        """evaluated contextual public pair를 idempotently ACTIVE pointer로 전환한다.

        The database independently checks source rights, profile homogeneity, evaluated state, and the V45
        contextual-hash manifest. This method neither opens a provider socket nor emits raw corpus/vector data.
        """

        _validate_context_pair(request.exact30, request.oa112)
        activation_receipt_id = f"rgr_act_{uuid.uuid4().hex}"
        if _ACTIVATION_RECEIPT_ID.fullmatch(activation_receipt_id) is None:
            raise PublicVoyageActivationError("PUBLIC_VOYAGE_ACTIVATION_ARGUMENT")
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
        except PublicVoyageActivationError:
            raise
        except psycopg.Error as error:
            # SQL details can include local role and path state. Keep the public/BAT error resumable and opaque.
            if error.sqlstate == "40001":
                code = "PUBLIC_VOYAGE_ACTIVATION_CONFLICT"
            elif error.sqlstate in {"23514", "55000"}:
                code = "PUBLIC_VOYAGE_ACTIVATION_NOT_READY"
            else:
                code = "PUBLIC_VOYAGE_ACTIVATION_REJECTED"
            raise PublicVoyageActivationError(code) from None
        return _receipt(
            request=request,
            previous_pointer_version=prepared.expected_pointer_version,
            new_pointer_version=new_pointer_version,
        )


def _validate_context_pair(
    exact30: RagV2PublicVoyageComponentContext,
    oa112: RagV2Oa112VoyageComponentContext,
) -> None:
    """one transport response의 two public components만 V25 pointer pair에 전달한다."""

    _validate_exact30_context(exact30)
    _validate_oa112_context(oa112)
    if exact30.component_generation_id == oa112.component_generation_id:
        raise PublicVoyageActivationError("PUBLIC_VOYAGE_ACTIVATION_ARGUMENT")


def _validate_exact30_context(context: RagV2PublicVoyageComponentContext) -> None:
    """external exact-30 consent corpus run identity를 packet-independent하게 재검증한다."""

    if not isinstance(context, RagV2PublicVoyageComponentContext):
        raise PublicVoyageActivationError("PUBLIC_VOYAGE_ACTIVATION_ARGUMENT")
    expected_run_id = _run_id(
        prefix="rag-v2-external-exact30-voyage-run",
        component_generation_id=context.component_generation_id,
        manifest_hash=context.manifest_hash,
    )
    if (
        context.component_scope != "EXACT30"
        or context.expected_source_count != 30
        or context.expected_chunk_count < 30
        or context.embedding_profile_id != _VOYAGE_PROFILE_ID
        or _GENERATION_ID.fullmatch(context.component_generation_id) is None
        or _RUN_ID.fullmatch(context.materialization_run_id) is None
        or _SHA256.fullmatch(context.generation_hash) is None
        or _SHA256.fullmatch(context.manifest_hash) is None
        or _SHA256.fullmatch(context.source_card_corpus_manifest_sha256) is None
        or context.component_generation_id != f"rgr_{context.generation_hash[:32]}"
        or context.materialization_run_id != expected_run_id
        or len(context.member_digests) != 30
        or len(set(context.member_digests)) != 30
        or any(_SHA256.fullmatch(digest) is None for digest in context.member_digests)
    ):
        raise PublicVoyageActivationError("PUBLIC_VOYAGE_ACTIVATION_ARGUMENT")


def _validate_oa112_context(context: RagV2Oa112VoyageComponentContext) -> None:
    """OA112 registry-bound run identity와 four-rights registry digest binding을 재검증한다."""

    if not isinstance(context, RagV2Oa112VoyageComponentContext):
        raise PublicVoyageActivationError("PUBLIC_VOYAGE_ACTIVATION_ARGUMENT")
    expected_run_id = _run_id(
        prefix="rag-v2-oa112-voyage-run",
        component_generation_id=context.component_generation_id,
        manifest_hash=context.manifest_hash,
    )
    if (
        context.component_scope != "OA112"
        or context.expected_source_count != 112
        or context.expected_chunk_count < 112
        or context.embedding_profile_id != _VOYAGE_PROFILE_ID
        or _GENERATION_ID.fullmatch(context.component_generation_id) is None
        or _RUN_ID.fullmatch(context.materialization_run_id) is None
        or _SHA256.fullmatch(context.generation_hash) is None
        or _SHA256.fullmatch(context.manifest_hash) is None
        or not context.registry_id
        or _SHA256.fullmatch(context.registry_digest) is None
        or context.component_generation_id != f"rgr_{context.generation_hash[:32]}"
        or context.materialization_run_id != expected_run_id
        or len(context.member_digests) != 112
        or len(set(context.member_digests)) != 112
        or any(_SHA256.fullmatch(digest) is None for digest in context.member_digests)
    ):
        raise PublicVoyageActivationError("PUBLIC_VOYAGE_ACTIVATION_ARGUMENT")


def _run_id(*, prefix: str, component_generation_id: str, manifest_hash: str) -> str:
    """writer/activation이 same deterministic run identity를 share하도록 full prefix를 pin한다."""

    return (
        "rgr_run_"
        + hashlib.sha256(
            f"{prefix}|{component_generation_id}|{manifest_hash}".encode()
        ).hexdigest()[:32]
    )


def _set_transaction_timeouts(connection: psycopg.Connection[Any]) -> None:
    """CAS lock을 bounded하게 유지해 local operator를 indefinite wait에서 보호한다."""

    connection.execute("SET LOCAL statement_timeout = '60s'")
    connection.execute("SET LOCAL lock_timeout = '10s'")
    connection.execute("SET LOCAL idle_in_transaction_session_timeout = '75s'")


def _attest_admin_connection(connection: psycopg.Connection[Any]) -> None:
    """admin DSN가 direct graph/table capability 없이 typed CAS functions만 갖는지 확인한다."""

    if connection.execute("SELECT current_user").fetchone() != (_ADMIN_ROLE,):
        raise PublicVoyageActivationError("PUBLIC_VOYAGE_ACTIVATION_ADMIN_ROLE")
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
                raise PublicVoyageActivationError("PUBLIC_VOYAGE_ACTIVATION_ADMIN_PRIVILEGE")
    for signature in (_PREPARE_FUNCTION, _ACTIVATE_FUNCTION):
        row = connection.execute(
            "SELECT has_function_privilege(current_user, %s, 'EXECUTE')",
            (signature,),
        ).fetchone()
        if row is None or row[0] is not True:
            raise PublicVoyageActivationError("PUBLIC_VOYAGE_ACTIVATION_ADMIN_PRIVILEGE")


def _prepared_receipt(row: tuple[object, ...] | None) -> _PreparedPublicBaseActivation:
    """definer prepare output을 exact CAS version/bool shape로 close한다."""

    if (
        row is None
        or len(row) != 2
        or type(row[0]) is not int
        or row[0] < 1
        or type(row[1]) is not bool
    ):
        raise PublicVoyageActivationError("PUBLIC_VOYAGE_ACTIVATION_RECEIPT")
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
        raise PublicVoyageActivationError("PUBLIC_VOYAGE_ACTIVATION_RECEIPT")
    return row[0]


def _receipt(
    *,
    request: PublicVoyageActivationRequest,
    previous_pointer_version: int,
    new_pointer_version: int,
) -> RagV2PublicVoyageActivationReceipt:
    """operator output에는 generation ID/profile/version만 남기고 provider/source payload를 배제한다."""

    if previous_pointer_version < 1 or new_pointer_version < previous_pointer_version:
        raise PublicVoyageActivationError("PUBLIC_VOYAGE_ACTIVATION_RECEIPT")
    return RagV2PublicVoyageActivationReceipt(
        exact30_generation_id=request.exact30.component_generation_id,
        oa112_generation_id=request.oa112.component_generation_id,
        embedding_profile_id="voyage_context_4_1024_v1",
        previous_pointer_version=previous_pointer_version,
        new_pointer_version=new_pointer_version,
        state="ACTIVE",
    )
