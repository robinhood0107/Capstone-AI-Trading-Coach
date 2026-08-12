"""Pre-S5 Voyage packet/nonce hash의 DB-backed one-shot usage lease다.

이 module은 provider socket과 raw corpus를 다루지 않는다. restricted `decision_rag_writer` role은
V38 SECURITY DEFINER functions만 호출해 reservation, attempt, sanitized outcome을 append-only로
기록한다. DB detail, DSN, packet 원문은 exception 또는 receipt에 노출하지 않는다.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

import psycopg

from app.rag.pre_s5_provider_control import (
    PreS5VoyageActivation,
    PreS5VoyageDocumentBatchActivation,
)
from app.rag.pre_s5_voyage_transport import (
    PreS5VoyageAttemptLease,
    PreS5VoyageFullBundle,
    PreS5VoyageTransportError,
    build_pre_s5_voyage_full_bundle,
)
from app.rag.rag_v2_voyage_batching import PublicVoyageBatchPlan, VoyageDocumentBatch

_WRITER_ROLE = "decision_rag_writer"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_USAGE_EVENT_ID = re.compile(r"^rgr_vou_[0-9a-f]{32}$")
_DOCUMENT_BATCH_MAX_BYTE_CAP = 16 * 1024 * 1024
_RESERVE_FUNCTION = (
    "public.reserve_rag_v2_immutable_voyage_usage_with_tokenizer("
    "text,text,text,text,text,text,timestamptz,integer,integer,bigint,bigint)"
)
_DOCUMENT_BATCH_RESERVE_FUNCTION = (
    "public.reserve_rag_v2_immutable_voyage_document_batch_usage("
    "text,text,text,text,text,text,timestamptz,integer,integer,bigint,bigint)"
)
_DOCUMENT_BATCH_CLAIM_FUNCTION = (
    "public.claim_rag_v2_immutable_voyage_document_batch_attempt(text,text,text,text)"
)
_DOCUMENT_BATCH_UNKNOWN_FUNCTION = (
    "public.mark_rag_v2_immutable_voyage_document_batch_unknown_billing(text,text,text)"
)
_CLAIM_FUNCTION = "public.claim_rag_v2_immutable_voyage_usage_attempt(text)"
_COMMIT_FUNCTION = (
    "public.commit_rag_v2_immutable_voyage_usage_with_tokenizer(text,integer,integer,bigint)"
)
_UNKNOWN_FUNCTION = "public.mark_rag_v2_immutable_voyage_usage_unknown_billing(text)"
_WRITER_FORBIDDEN_TABLES = (
    "rag_v2_immutable_voyage_usage_reservations",
    "rag_v2_immutable_voyage_usage_attempts",
    "rag_v2_immutable_voyage_usage_outcomes",
)


class PreS5VoyageUsageRepositoryError(ValueError):
    """Voyage DB lease capability 또는 sanitized outcome transition 실패다."""


class PsycopgPreS5VoyageUsageRepository:
    """V38 restricted DB capability로 verified full-bundle packet reservation만 만든다."""

    def __init__(self, *, database_dsn: str) -> None:
        if not isinstance(database_dsn, str) or not 1 <= len(database_dsn) <= 4_096:
            raise PreS5VoyageUsageRepositoryError("PRE_S5_VOYAGE_LEASE_DATABASE_DSN")
        self._database_dsn = database_dsn

    def reserve(
        self,
        *,
        activation: PreS5VoyageActivation,
        bundle: PreS5VoyageFullBundle,
    ) -> PreS5VoyageAttemptLease:
        """packet/full-bundle binding을 재검증하고 one physical attempt lease를 DB에 reserve한다."""

        _validate_activation_and_bundle(activation=activation, bundle=bundle)
        usage_event_id = _usage_event_id(activation)
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
                        SELECT usage_event_id, expires_at
                        FROM public.reserve_rag_v2_immutable_voyage_usage_with_tokenizer(
                          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            usage_event_id,
                            activation.packet_sha256,
                            activation.nonce_sha256,
                            activation.bundle_manifest_sha256,
                            activation.rate_evidence_sha256,
                            activation.tokenizer_sha256,
                            activation.expires_at,
                            activation.token_cap,
                            activation.byte_cap,
                            activation.cost_cap_microusd,
                            activation.input_microusd_per_token,
                        ),
                    ).fetchone()
        except PreS5VoyageUsageRepositoryError:
            raise
        except psycopg.Error:
            raise PreS5VoyageUsageRepositoryError(
                "PRE_S5_VOYAGE_LEASE_RESERVATION_REJECTED"
            ) from None
        if (
            row is None
            or len(row) != 2
            or row[0] != usage_event_id
            or not isinstance(row[1], datetime)
            or row[1].tzinfo is None
            or row[1].astimezone(UTC) != activation.expires_at.astimezone(UTC)
        ):
            raise PreS5VoyageUsageRepositoryError("PRE_S5_VOYAGE_LEASE_RESERVATION_REJECTED")
        return PsycopgPreS5VoyageUsageLease(
            database_dsn=self._database_dsn,
            usage_event_id=usage_event_id,
            expires_at=activation.expires_at,
        )

    def reserve_document_batch(
        self,
        *,
        activation: PreS5VoyageDocumentBatchActivation,
        plan: PublicVoyageBatchPlan,
        batch: VoyageDocumentBatch,
    ) -> PsycopgPreS5VoyageDocumentBatchUsageLease:
        """exact plan/batch packet 하나를 기존 append-only Voyage usage ledger에 reserve한다."""

        _validate_batch_activation(activation=activation, plan=plan, batch=batch)
        usage_event_id = _batch_usage_event_id(activation)
        try:
            with psycopg.connect(self._database_dsn, autocommit=False, connect_timeout=2) as connection:
                _attest_writer_connection(connection)
                with connection.transaction():
                    _set_transaction_timeouts(connection)
                    row = connection.execute(
                        """
                        SELECT usage_event_id, expires_at
                        FROM public.reserve_rag_v2_immutable_voyage_document_batch_usage(
                          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            usage_event_id,
                            activation.packet_sha256,
                            activation.nonce_sha256,
                            activation.batch_manifest_sha256,
                            activation.rate_evidence_sha256,
                            activation.tokenizer_sha256,
                            activation.expires_at,
                            activation.token_cap,
                            activation.byte_cap,
                            activation.cost_cap_microusd,
                            activation.input_microusd_per_token,
                        ),
                    ).fetchone()
        except PreS5VoyageUsageRepositoryError:
            raise
        except psycopg.Error:
            raise PreS5VoyageUsageRepositoryError("PRE_S5_VOYAGE_LEASE_RESERVATION_REJECTED") from None
        if (
            row is None
            or len(row) != 2
            or row[0] != usage_event_id
            or not isinstance(row[1], datetime)
            or row[1].tzinfo is None
            or row[1].astimezone(UTC) != activation.expires_at.astimezone(UTC)
        ):
            raise PreS5VoyageUsageRepositoryError("PRE_S5_VOYAGE_LEASE_RESERVATION_REJECTED")
        return PsycopgPreS5VoyageDocumentBatchUsageLease(
            database_dsn=self._database_dsn,
            usage_event_id=usage_event_id,
            expires_at=activation.expires_at,
            batch_plan_sha256=plan.plan_sha256,
            batch_id=batch.batch_id,
            batch_manifest_sha256=batch.batch_manifest_sha256,
        )


class PsycopgPreS5VoyageUsageLease:
    """one reservation의 attempt/outcome transition만 노출하는 DB-backed lease다."""

    def __init__(self, *, database_dsn: str, usage_event_id: str, expires_at: datetime) -> None:
        if (
            not isinstance(database_dsn, str)
            or _USAGE_EVENT_ID.fullmatch(usage_event_id) is None
            or not isinstance(expires_at, datetime)
            or expires_at.tzinfo is None
        ):
            raise PreS5VoyageUsageRepositoryError("PRE_S5_VOYAGE_LEASE_ARGUMENT")
        self._database_dsn = database_dsn
        self._usage_event_id = usage_event_id
        self._expires_at = expires_at.astimezone(UTC)

    def claim_attempt(self, *, now: datetime) -> None:
        """provider socket 직전에 DB attempt row를 exactly once append한다."""

        if not isinstance(now, datetime) or now.tzinfo is None or now.astimezone(UTC) >= self._expires_at:
            raise PreS5VoyageUsageRepositoryError("PRE_S5_VOYAGE_LEASE_CLAIM_REJECTED")
        self._execute_transition(
            sql="SELECT public.claim_rag_v2_immutable_voyage_usage_attempt(%s)",
            parameters=(self._usage_event_id,),
            code="PRE_S5_VOYAGE_LEASE_CLAIM_REJECTED",
        )

    def commit(
        self,
        *,
        expected_input_tokens: int,
        total_tokens: int,
        actual_cost_microusd: int,
    ) -> None:
        """Official preflight count와 provider actual usage만 append하고 raw body/vector는 넣지 않는다."""

        if (
            type(expected_input_tokens) is not int
            or type(total_tokens) is not int
            or type(actual_cost_microusd) is not int
            or not 1 <= expected_input_tokens <= 120_000
            or not 0 <= total_tokens <= 120_000
            or not 0 <= actual_cost_microusd <= 1_000_000_000
        ):
            raise PreS5VoyageUsageRepositoryError("PRE_S5_VOYAGE_LEASE_COMMIT_REJECTED")
        self._execute_transition(
            sql="SELECT public.commit_rag_v2_immutable_voyage_usage_with_tokenizer(%s, %s, %s, %s)",
            parameters=(
                self._usage_event_id,
                expected_input_tokens,
                total_tokens,
                actual_cost_microusd,
            ),
            code="PRE_S5_VOYAGE_LEASE_COMMIT_REJECTED",
        )

    def mark_unknown_billing(self) -> None:
        """provider attempt 뒤 response 검증/DB commit이 실패했을 때 billing 불확실성을 append한다."""

        self._execute_transition(
            sql="SELECT public.mark_rag_v2_immutable_voyage_usage_unknown_billing(%s)",
            parameters=(self._usage_event_id,),
            code="PRE_S5_VOYAGE_LEASE_UNKNOWN_REJECTED",
        )

    def _execute_transition(
        self,
        *,
        sql: str,
        parameters: tuple[object, ...],
        code: str,
    ) -> None:
        """DB detail이 caller/log에 닿지 않도록 one restricted function result만 소비한다."""

        failed = False
        try:
            with psycopg.connect(
                self._database_dsn,
                autocommit=False,
                connect_timeout=2,
            ) as connection:
                _attest_writer_connection(connection)
                with connection.transaction():
                    _set_transaction_timeouts(connection)
                    connection.execute(sql, parameters).fetchone()
        except (psycopg.Error, PreS5VoyageUsageRepositoryError):
            failed = True
        if failed:
            raise PreS5VoyageUsageRepositoryError(code)


class PsycopgPreS5VoyageDocumentBatchUsageLease(PsycopgPreS5VoyageUsageLease):
    """같은 plan/batch의 다른 packet과 경쟁할 수 없고 stage 전 단독 commit을 노출하지 않는 lease다."""

    def __init__(
        self,
        *,
        database_dsn: str,
        usage_event_id: str,
        expires_at: datetime,
        batch_plan_sha256: str,
        batch_id: str,
        batch_manifest_sha256: str,
    ) -> None:
        super().__init__(
            database_dsn=database_dsn,
            usage_event_id=usage_event_id,
            expires_at=expires_at,
        )
        if (
            not _is_sha256(batch_plan_sha256)
            or not isinstance(batch_id, str)
            or not batch_id.startswith("ps5_voyage_doc_")
            or not _is_sha256(batch_manifest_sha256)
        ):
            raise PreS5VoyageUsageRepositoryError("PRE_S5_VOYAGE_LEASE_ARGUMENT")
        self._batch_plan_sha256 = batch_plan_sha256
        self._batch_id = batch_id
        self._batch_manifest_sha256 = batch_manifest_sha256

    @property
    def usage_event_id(self) -> str:
        """atomic stage payload에만 쓰는 content-free append-only usage identity다."""

        return self._usage_event_id

    def claim_attempt(self, *, now: datetime) -> None:
        """usage attempt와 plan/batch global attempt를 한 transaction에서 함께 선점한다."""

        if not isinstance(now, datetime) or now.tzinfo is None or now.astimezone(UTC) >= self._expires_at:
            raise PreS5VoyageUsageRepositoryError("PRE_S5_VOYAGE_LEASE_CLAIM_REJECTED")
        self._execute_transition(
            sql=(
                "SELECT public.claim_rag_v2_immutable_voyage_document_batch_attempt("
                "%s, %s, %s, %s)"
            ),
            parameters=(
                self._usage_event_id,
                self._batch_plan_sha256,
                self._batch_id,
                self._batch_manifest_sha256,
            ),
            code="PRE_S5_VOYAGE_LEASE_CLAIM_REJECTED",
        )

    def commit(
        self,
        *,
        expected_input_tokens: int,
        total_tokens: int,
        actual_cost_microusd: int,
    ) -> None:
        """document batch 성공은 vector stage와 분리 commit할 수 없다."""

        del expected_input_tokens, total_tokens, actual_cost_microusd
        raise PreS5VoyageUsageRepositoryError("PRE_S5_VOYAGE_ATOMIC_STAGE_REQUIRED")

    def mark_unknown_billing(self) -> None:
        """usage와 plan/batch attempt를 함께 terminal UNKNOWN으로 만든다."""

        self._execute_transition(
            sql=(
                "SELECT public.mark_rag_v2_immutable_voyage_document_batch_unknown_billing("
                "%s, %s, %s)"
            ),
            parameters=(self._usage_event_id, self._batch_plan_sha256, self._batch_id),
            code="PRE_S5_VOYAGE_LEASE_UNKNOWN_REJECTED",
        )


def _validate_activation_and_bundle(
    *,
    activation: object,
    bundle: object,
) -> None:
    """repository는 transport 외 direct caller도 packet/full membership/cost relation으로 close한다."""

    if not isinstance(activation, PreS5VoyageActivation) or not isinstance(bundle, PreS5VoyageFullBundle):
        raise PreS5VoyageUsageRepositoryError("PRE_S5_VOYAGE_LEASE_ARGUMENT")
    try:
        rebuilt = build_pre_s5_voyage_full_bundle(components=bundle.components)
    except PreS5VoyageTransportError:
        raise PreS5VoyageUsageRepositoryError("PRE_S5_VOYAGE_LEASE_ARGUMENT") from None
    if (
        bundle.manifest_sha256 != rebuilt.manifest_sha256
        or activation.bundle_manifest_sha256 != rebuilt.manifest_sha256
        or not _is_sha256(activation.packet_sha256)
        or not _is_sha256(activation.nonce_sha256)
        or not _is_sha256(activation.rate_evidence_sha256)
        or not _is_sha256(activation.tokenizer_sha256)
        or activation.provider != "VOYAGE"
        or activation.operation != "CONTEXTUALIZED_DOCUMENT_EMBEDDING"
        or activation.origin != "https://api.voyageai.com"
        or activation.endpoint != "/v1/contextualizedembeddings"
        or not isinstance(activation.expires_at, datetime)
        or activation.expires_at.tzinfo is None
        or activation.logical_call_cap != 1
        or activation.physical_call_cap != 1
        or type(activation.token_cap) is not int
        or not 1 <= activation.token_cap <= 120_000
        or type(activation.byte_cap) is not int
        or not 1 <= activation.byte_cap <= 4_194_304
        or type(activation.cost_cap_microusd) is not int
        or not 1 <= activation.cost_cap_microusd <= 1_000_000_000
        or type(activation.input_microusd_per_token) is not int
        or not 1 <= activation.input_microusd_per_token <= 1_000_000
        or activation.token_cap * activation.input_microusd_per_token > activation.cost_cap_microusd
        or activation.retry_count != 0
        or activation.raw_artifact_count != 0
    ):
        raise PreS5VoyageUsageRepositoryError("PRE_S5_VOYAGE_LEASE_ARGUMENT")


def _usage_event_id(activation: PreS5VoyageActivation) -> str:
    """packet/nonce hash pair가 같은 재예약을 identical DB identity로 충돌시키는 deterministic ID다."""

    digest = hashlib.sha256(
        (
            "pre-s5-voyage-usage-v1\0"
            f"{activation.packet_sha256}\0{activation.nonce_sha256}\0{activation.bundle_manifest_sha256}"
        ).encode("utf-8")
    ).hexdigest()
    return f"rgr_vou_{digest[:32]}"


def _batch_usage_event_id(activation: PreS5VoyageDocumentBatchActivation) -> str:
    """각 batch packet/nonce/manifest를 기존 usage-event namespace의 unique identity로 만든다."""

    digest = hashlib.sha256(
        (
            "pre-s5-voyage-document-batch-usage-v1\0"
            f"{activation.packet_sha256}\0{activation.nonce_sha256}\0"
            f"{activation.batch_plan_sha256}\0{activation.batch_manifest_sha256}"
        ).encode("utf-8")
    ).hexdigest()
    return f"rgr_vou_{digest[:32]}"


def _validate_batch_activation(
    *,
    activation: object,
    plan: object,
    batch: object,
) -> None:
    """usage reservation도 transport와 같은 exact batch membership/cap을 독립적으로 검증한다."""

    if (
        not isinstance(activation, PreS5VoyageDocumentBatchActivation)
        or not isinstance(plan, PublicVoyageBatchPlan)
        or not isinstance(batch, VoyageDocumentBatch)
        or batch not in plan.batches
        or activation.batch_plan_sha256 != plan.plan_sha256
        or activation.batch_id != batch.batch_id
        or activation.batch_manifest_sha256 != batch.batch_manifest_sha256
        or activation.batch_ordinal != batch.batch_ordinal
        or activation.batch_count != batch.batch_count
        or activation.expected_token_count != batch.token_count
        or activation.expected_chunk_count != batch.chunk_count
        or activation.expected_group_count != batch.group_count
        or activation.tokenizer_sha256 != plan.tokenizer_sha256
        or not _is_sha256(activation.packet_sha256)
        or not _is_sha256(activation.nonce_sha256)
        or not _is_sha256(activation.rate_evidence_sha256)
        or activation.provider != "VOYAGE"
        or activation.operation != "CONTEXTUALIZED_DOCUMENT_EMBEDDING"
        or activation.logical_call_cap != 1
        or activation.physical_call_cap != 1
        # batch 본문은 110K 이하로 계획하지만 packet capability는 provider가 추가하는
        # input_type=document accounting까지 포함하므로 공식 요청 상한 120K를 허용한다.
        or not 1 <= activation.token_cap <= 120_000
        or activation.token_cap < batch.token_count
        or type(activation.byte_cap) is not int
        or not 1 <= activation.byte_cap <= _DOCUMENT_BATCH_MAX_BYTE_CAP
        or activation.byte_cap < batch.estimated_response_bytes
        or type(activation.cost_cap_microusd) is not int
        or not 1 <= activation.cost_cap_microusd <= 1_000_000_000
        or type(activation.input_microusd_per_token) is not int
        or not 1 <= activation.input_microusd_per_token <= 1_000_000
        or activation.token_cap * activation.input_microusd_per_token
        > activation.cost_cap_microusd
        or activation.retry_count != 0
        or activation.raw_artifact_count != 0
    ):
        raise PreS5VoyageUsageRepositoryError("PRE_S5_VOYAGE_LEASE_ARGUMENT")


def _set_transaction_timeouts(connection: psycopg.Connection[Any]) -> None:
    """lease DB wait가 provider retry나 indefinite operator wait로 확대되지 않게 bounded하게 닫는다."""

    connection.execute("SET LOCAL statement_timeout = '5s'")
    connection.execute("SET LOCAL lock_timeout = '500ms'")
    connection.execute("SET LOCAL idle_in_transaction_session_timeout = '6s'")


def _attest_writer_connection(connection: psycopg.Connection[Any]) -> None:
    """writer DSN가 V38 functions 외 raw ledger table privilege를 받지 않았는지 확인한다."""

    if connection.execute("SELECT current_user").fetchone() != (_WRITER_ROLE,):
        raise PreS5VoyageUsageRepositoryError("PRE_S5_VOYAGE_LEASE_WRITER_ROLE")
    for table in _WRITER_FORBIDDEN_TABLES:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            row = connection.execute(
                "SELECT has_table_privilege(current_user, %s, %s)",
                (f"public.{table}", privilege),
            ).fetchone()
            if row is not None and row[0] is True:
                raise PreS5VoyageUsageRepositoryError("PRE_S5_VOYAGE_LEASE_WRITER_PRIVILEGE")
    for function in (
        _RESERVE_FUNCTION,
        _DOCUMENT_BATCH_RESERVE_FUNCTION,
        _DOCUMENT_BATCH_CLAIM_FUNCTION,
        _DOCUMENT_BATCH_UNKNOWN_FUNCTION,
        _CLAIM_FUNCTION,
        _COMMIT_FUNCTION,
        _UNKNOWN_FUNCTION,
    ):
        row = connection.execute(
            "SELECT has_function_privilege(current_user, %s, 'EXECUTE')",
            (function,),
        ).fetchone()
        if row is None or row[0] is not True:
            raise PreS5VoyageUsageRepositoryError("PRE_S5_VOYAGE_LEASE_WRITER_PRIVILEGE")


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None
