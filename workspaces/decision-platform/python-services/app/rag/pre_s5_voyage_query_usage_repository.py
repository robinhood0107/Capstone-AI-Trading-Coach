"""DB-backed one-shot ledger for packet-bound Voyage query embedding attempts.

The writer role can reserve/claim/commit only through V46 security-definer functions.  It never has
direct table access and never writes question text, scope claim text, response payload, or vectors;
the ledger stores SHA-256 projections and sanitized numeric usage only.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, cast

import numpy as np
import psycopg
from numpy.typing import NDArray
from psycopg.types.json import Jsonb

from app.rag.pre_s5_provider_control import (
    PreS5VoyageEvaluationBatchActivation,
    PreS5VoyageQueryActivation,
)
from app.rag.pre_s5_voyage_transport import PreS5VoyageAttemptLease

_WRITER_ROLE = "decision_rag_writer"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_USAGE_EVENT_ID = re.compile(r"^rgr_vqu_[0-9a-f]{32}$")
_RESERVE_FUNCTION = (
    "public.reserve_rag_v2_immutable_voyage_query_usage_with_tokenizer("
    "text,text,text,text,text,text,text,text,timestamptz,integer,integer,bigint,bigint)"
)
_CLAIM_FUNCTION = "public.claim_rag_v2_immutable_voyage_query_usage_attempt(text)"
_COMMIT_FUNCTION = (
    "public.commit_rag_v2_immutable_voyage_query_usage_with_tokenizer(text,integer,integer,bigint)"
)
_UNKNOWN_FUNCTION = "public.mark_rag_v2_immutable_voyage_query_usage_unknown_billing(text)"
_EVALUATION_RESERVE_FUNCTION = (
    "public.reserve_rag_v2_immutable_voyage_evaluation_batch_usage("
    "text,text,text,text,text,text,text,text,timestamptz,integer,integer,bigint,bigint)"
)
_EVALUATION_CLAIM_FUNCTION = (
    "public.claim_rag_v2_immutable_voyage_evaluation_batch_attempt(text,text,text,text,text)"
)
_EVALUATION_UNKNOWN_FUNCTION = (
    "public.mark_rag_v2_immutable_voyage_evaluation_batch_unknown_billing(text,text,text)"
)
_EVALUATION_STAGE_FUNCTION = (
    "public.commit_and_stage_rag_v2_immutable_voyage_evaluation_batch(jsonb)"
)
_EVALUATION_LOAD_FUNCTION = (
    "public.load_rag_v2_immutable_voyage_evaluation_batch_vectors(text,text,text)"
)
_WRITER_FORBIDDEN_TABLES = (
    "rag_v2_immutable_voyage_query_usage_reservations",
    "rag_v2_immutable_voyage_query_usage_attempts",
    "rag_v2_immutable_voyage_query_usage_outcomes",
    "rag_v2_immutable_voyage_evaluation_batch_attempts",
    "rag_v2_immutable_voyage_evaluation_batch_vectors",
)


class PreS5VoyageQueryUsageRepositoryError(ValueError):
    """Query usage reservation or append-only state transition failed without exposing DB detail."""


class PsycopgPreS5VoyageQueryUsageRepository:
    """Create a writer-role lease for one exact query/scope packet before a fixed-origin provider call."""

    def __init__(self, *, database_dsn: str) -> None:
        if not isinstance(database_dsn, str) or not 1 <= len(database_dsn) <= 4_096:
            raise PreS5VoyageQueryUsageRepositoryError("PRE_S5_VOYAGE_QUERY_LEASE_DATABASE_DSN")
        self._database_dsn = database_dsn

    def reserve(
        self,
        *,
        activation: PreS5VoyageQueryActivation | PreS5VoyageEvaluationBatchActivation,
        evaluation_component_scope: Literal["EXACT30", "OA112"] | None = None,
    ) -> PreS5VoyageAttemptLease:
        """Reserve one packet; only a closed public-component label may accompany evaluation traffic.

        The normal ask path supplies no label and is stored as ``RUNTIME``.  The label lets V47 prove
        that a public generation's 10/112 reported evaluation attempts really reached the one-shot ledger,
        without ever storing a question, owner, or opaque scope plaintext.
        """

        _validate_activation(activation)
        stored_scope = evaluation_component_scope or "RUNTIME"
        if stored_scope not in {"RUNTIME", "EXACT30", "OA112"}:
            raise PreS5VoyageQueryUsageRepositoryError("PRE_S5_VOYAGE_QUERY_LEASE_ARGUMENT")
        if isinstance(activation, PreS5VoyageEvaluationBatchActivation) and (
            stored_scope != activation.component_scope or stored_scope == "RUNTIME"
        ):
            raise PreS5VoyageQueryUsageRepositoryError("PRE_S5_VOYAGE_QUERY_LEASE_ARGUMENT")
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
                    if isinstance(activation, PreS5VoyageEvaluationBatchActivation):
                        row = connection.execute(
                            """
                            SELECT usage_event_id, expires_at
                            FROM public.reserve_rag_v2_immutable_voyage_evaluation_batch_usage(
                              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                            )
                            """,
                            (
                                usage_event_id,
                                activation.packet_sha256,
                                activation.nonce_sha256,
                                activation.query_manifest_sha256,
                                activation.scope_claim_sha256,
                                activation.rate_evidence_sha256,
                                activation.tokenizer_sha256,
                                stored_scope,
                                activation.expires_at,
                                activation.token_cap,
                                activation.byte_cap,
                                activation.cost_cap_microusd,
                                activation.input_microusd_per_token,
                            ),
                        ).fetchone()
                    else:
                        row = connection.execute(
                            """
                            SELECT usage_event_id, expires_at
                            FROM public.reserve_rag_v2_immutable_voyage_query_usage_with_tokenizer(
                              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                            )
                            """,
                            (
                                usage_event_id,
                                activation.packet_sha256,
                                activation.nonce_sha256,
                                activation.query_sha256,
                                activation.scope_claim_sha256,
                                activation.rate_evidence_sha256,
                                activation.tokenizer_sha256,
                                stored_scope,
                                activation.expires_at,
                                activation.token_cap,
                                activation.byte_cap,
                                activation.cost_cap_microusd,
                                activation.input_microusd_per_token,
                            ),
                        ).fetchone()
        except PreS5VoyageQueryUsageRepositoryError:
            raise
        except psycopg.Error:
            raise PreS5VoyageQueryUsageRepositoryError(
                "PRE_S5_VOYAGE_QUERY_LEASE_RESERVATION_REJECTED"
            ) from None
        if (
            row is None
            or len(row) != 2
            or row[0] != usage_event_id
            or not isinstance(row[1], datetime)
            or row[1].tzinfo is None
            or row[1].astimezone(UTC) != activation.expires_at.astimezone(UTC)
        ):
            raise PreS5VoyageQueryUsageRepositoryError(
                "PRE_S5_VOYAGE_QUERY_LEASE_RESERVATION_REJECTED"
            )
        if isinstance(activation, PreS5VoyageEvaluationBatchActivation):
            return PsycopgPreS5VoyageEvaluationBatchUsageLease(
                database_dsn=self._database_dsn,
                usage_event_id=usage_event_id,
                expires_at=activation.expires_at,
                scope_claim_sha256=activation.scope_claim_sha256,
                component_scope=cast(Literal["EXACT30", "OA112"], activation.component_scope),
                query_manifest_sha256=activation.query_manifest_sha256,
                packet_sha256=activation.packet_sha256,
            )
        return PsycopgPreS5VoyageQueryUsageLease(
            database_dsn=self._database_dsn,
            usage_event_id=usage_event_id,
            expires_at=activation.expires_at,
        )

    def resume_evaluation_batch(
        self,
        *,
        scope_claim_sha256: str,
        component_scope: Literal["EXACT30", "OA112"],
        query_manifest_sha256: str,
        expected_query_sha256s: Sequence[str],
    ) -> Mapping[str, tuple[float, ...]] | None:
        """완료된 component vector set만 반환하고 claimed/unknown/drift state는 fail-closed한다."""

        expected = tuple(expected_query_sha256s)
        expected_count = 10 if component_scope == "EXACT30" else 112
        if (
            not _is_sha256(scope_claim_sha256)
            or not _is_sha256(query_manifest_sha256)
            or len(expected) != expected_count
            or len(set(expected)) != expected_count
            or any(not _is_sha256(value) for value in expected)
        ):
            raise PreS5VoyageQueryUsageRepositoryError("PRE_S5_VOYAGE_EVALUATION_RESUME_ARGUMENT")
        try:
            with psycopg.connect(self._database_dsn, autocommit=False, connect_timeout=2) as connection:
                _attest_writer_connection(connection)
                with connection.transaction():
                    _set_transaction_timeouts(connection)
                    rows = connection.execute(
                        """
                        SELECT query_sha256, embedding
                        FROM public.load_rag_v2_immutable_voyage_evaluation_batch_vectors(%s, %s, %s)
                        """,
                        (scope_claim_sha256, component_scope, query_manifest_sha256),
                    ).fetchall()
        except PreS5VoyageQueryUsageRepositoryError:
            raise
        except psycopg.Error:
            raise PreS5VoyageQueryUsageRepositoryError(
                "PRE_S5_VOYAGE_EVALUATION_RESUME_REJECTED"
            ) from None
        if not rows:
            return None
        vectors = {str(row[0]): _parse_vector(row[1]) for row in rows if len(row) == 2}
        if len(vectors) != expected_count or set(vectors) != set(expected):
            raise PreS5VoyageQueryUsageRepositoryError(
                "PRE_S5_VOYAGE_EVALUATION_RESUME_RECEIPT"
            )
        return vectors

    def stage_evaluation_batch(
        self,
        *,
        activation: PreS5VoyageEvaluationBatchActivation,
        lease: PreS5VoyageAttemptLease,
        vectors_by_query_sha256: Mapping[str, Sequence[float]],
        expected_input_tokens: int,
        total_tokens: int,
        actual_cost_microusd: int,
    ) -> None:
        """provider usage outcome과 normalized query vectors를 한 transaction으로 commit한다."""

        if not isinstance(lease, PsycopgPreS5VoyageEvaluationBatchUsageLease):
            raise PreS5VoyageQueryUsageRepositoryError("PRE_S5_VOYAGE_EVALUATION_STAGE_ARGUMENT")
        rows = _evaluation_vector_rows(vectors_by_query_sha256)
        expected_count = 10 if activation.component_scope == "EXACT30" else 112
        if (
            lease.scope_claim_sha256 != activation.scope_claim_sha256
            or lease.component_scope != activation.component_scope
            or lease.query_manifest_sha256 != activation.query_manifest_sha256
            or lease.packet_sha256 != activation.packet_sha256
            or len(rows) != expected_count
        ):
            raise PreS5VoyageQueryUsageRepositoryError("PRE_S5_VOYAGE_EVALUATION_STAGE_ARGUMENT")
        payload = {
            "actualCostMicrousd": actual_cost_microusd,
            "componentScope": activation.component_scope,
            "expectedInputTokens": expected_input_tokens,
            "packetSha256": activation.packet_sha256,
            "providerTotalTokens": total_tokens,
            "queryManifestSha256": activation.query_manifest_sha256,
            "schemaVersion": "pre-s5-voyage-evaluation-batch-stage/v1",
            "scopeClaimSha256": activation.scope_claim_sha256,
            "usageEventId": lease.usage_event_id,
            "vectors": rows,
        }
        try:
            with psycopg.connect(self._database_dsn, autocommit=False, connect_timeout=2) as connection:
                _attest_writer_connection(connection)
                with connection.transaction():
                    _set_transaction_timeouts(connection)
                    row = connection.execute(
                        """
                        SELECT component_scope, staged_vector_count, batch_reused
                        FROM public.commit_and_stage_rag_v2_immutable_voyage_evaluation_batch(%s::jsonb)
                        """,
                        (Jsonb(payload),),
                    ).fetchone()
        except PreS5VoyageQueryUsageRepositoryError:
            raise
        except psycopg.Error:
            raise PreS5VoyageQueryUsageRepositoryError(
                "PRE_S5_VOYAGE_EVALUATION_STAGE_REJECTED"
            ) from None
        if row != (activation.component_scope, expected_count, False):
            raise PreS5VoyageQueryUsageRepositoryError(
                "PRE_S5_VOYAGE_EVALUATION_STAGE_RECEIPT"
            )


class PsycopgPreS5VoyageQueryUsageLease:
    """Expose exactly the one reservation's claim/commit/unknown transitions to the HTTP transport."""

    def __init__(self, *, database_dsn: str, usage_event_id: str, expires_at: datetime) -> None:
        if (
            not isinstance(database_dsn, str)
            or _USAGE_EVENT_ID.fullmatch(usage_event_id) is None
            or not isinstance(expires_at, datetime)
            or expires_at.tzinfo is None
        ):
            raise PreS5VoyageQueryUsageRepositoryError("PRE_S5_VOYAGE_QUERY_LEASE_ARGUMENT")
        self._database_dsn = database_dsn
        self._usage_event_id = usage_event_id
        self._expires_at = expires_at.astimezone(UTC)

    @property
    def usage_event_id(self) -> str:
        """atomic staging이 plaintext 없이 동일 usage row를 결속하도록 opaque id만 노출한다."""

        return self._usage_event_id

    def claim_attempt(self, *, now: datetime) -> None:
        """Append the physical-attempt row immediately before bytes may be sent to Voyage."""

        if not isinstance(now, datetime) or now.tzinfo is None or now.astimezone(UTC) >= self._expires_at:
            raise PreS5VoyageQueryUsageRepositoryError("PRE_S5_VOYAGE_QUERY_LEASE_CLAIM_REJECTED")
        self._execute_transition(
            sql="SELECT public.claim_rag_v2_immutable_voyage_query_usage_attempt(%s)",
            parameters=(self._usage_event_id,),
            code="PRE_S5_VOYAGE_QUERY_LEASE_CLAIM_REJECTED",
        )

    def commit(
        self,
        *,
        expected_input_tokens: int,
        total_tokens: int,
        actual_cost_microusd: int,
    ) -> None:
        """Persist local official count and actual billed usage, never question or response text."""

        if (
            type(expected_input_tokens) is not int
            or type(total_tokens) is not int
            or type(actual_cost_microusd) is not int
            or not 1 <= expected_input_tokens <= 8_192
            or not 0 <= total_tokens <= 8_192
            or not 0 <= actual_cost_microusd <= 1_000_000_000
        ):
            raise PreS5VoyageQueryUsageRepositoryError("PRE_S5_VOYAGE_QUERY_LEASE_COMMIT_REJECTED")
        self._execute_transition(
            sql=(
                "SELECT public.commit_rag_v2_immutable_voyage_query_usage_with_tokenizer("
                "%s, %s, %s, %s)"
            ),
            parameters=(
                self._usage_event_id,
                expected_input_tokens,
                total_tokens,
                actual_cost_microusd,
            ),
            code="PRE_S5_VOYAGE_QUERY_LEASE_COMMIT_REJECTED",
        )

    def mark_unknown_billing(self) -> None:
        """Record a bounded unknown-billing outcome after a claimed send cannot yield a valid response."""

        self._execute_transition(
            sql="SELECT public.mark_rag_v2_immutable_voyage_query_usage_unknown_billing(%s)",
            parameters=(self._usage_event_id,),
            code="PRE_S5_VOYAGE_QUERY_LEASE_UNKNOWN_REJECTED",
        )

    def _execute_transition(
        self,
        *,
        sql: str,
        parameters: tuple[object, ...],
        code: str,
    ) -> None:
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
        except (psycopg.Error, PreS5VoyageQueryUsageRepositoryError):
            failed = True
        if failed:
            raise PreS5VoyageQueryUsageRepositoryError(code)


class PsycopgPreS5VoyageEvaluationBatchUsageLease(PsycopgPreS5VoyageQueryUsageLease):
    """평가 component의 global one-attempt claim과 atomic vector stage를 결속한다."""

    def __init__(
        self,
        *,
        database_dsn: str,
        usage_event_id: str,
        expires_at: datetime,
        scope_claim_sha256: str,
        component_scope: Literal["EXACT30", "OA112"],
        query_manifest_sha256: str,
        packet_sha256: str,
    ) -> None:
        super().__init__(
            database_dsn=database_dsn,
            usage_event_id=usage_event_id,
            expires_at=expires_at,
        )
        if (
            not _is_sha256(scope_claim_sha256)
            or component_scope not in {"EXACT30", "OA112"}
            or not _is_sha256(query_manifest_sha256)
            or not _is_sha256(packet_sha256)
        ):
            raise PreS5VoyageQueryUsageRepositoryError("PRE_S5_VOYAGE_QUERY_LEASE_ARGUMENT")
        self.scope_claim_sha256 = scope_claim_sha256
        self.component_scope = component_scope
        self.query_manifest_sha256 = query_manifest_sha256
        self.packet_sha256 = packet_sha256

    def claim_attempt(self, *, now: datetime) -> None:
        if not isinstance(now, datetime) or now.tzinfo is None or now.astimezone(UTC) >= self._expires_at:
            raise PreS5VoyageQueryUsageRepositoryError("PRE_S5_VOYAGE_QUERY_LEASE_CLAIM_REJECTED")
        self._execute_transition(
            sql=(
                "SELECT public.claim_rag_v2_immutable_voyage_evaluation_batch_attempt("
                "%s, %s, %s, %s, %s)"
            ),
            parameters=(
                self.usage_event_id,
                self.scope_claim_sha256,
                self.component_scope,
                self.query_manifest_sha256,
                self.packet_sha256,
            ),
            code="PRE_S5_VOYAGE_QUERY_LEASE_CLAIM_REJECTED",
        )

    def commit(
        self,
        *,
        expected_input_tokens: int,
        total_tokens: int,
        actual_cost_microusd: int,
    ) -> None:
        del expected_input_tokens, total_tokens, actual_cost_microusd
        raise PreS5VoyageQueryUsageRepositoryError("PRE_S5_VOYAGE_EVALUATION_ATOMIC_STAGE_REQUIRED")

    def mark_unknown_billing(self) -> None:
        self._execute_transition(
            sql=(
                "SELECT public.mark_rag_v2_immutable_voyage_evaluation_batch_unknown_billing("
                "%s, %s, %s)"
            ),
            parameters=(self.usage_event_id, self.scope_claim_sha256, self.component_scope),
            code="PRE_S5_VOYAGE_QUERY_LEASE_UNKNOWN_REJECTED",
        )


def _validate_activation(activation: object) -> None:
    """Keep a direct repository caller from turning a document packet or broader operation into a query lease."""

    if not isinstance(
        activation, (PreS5VoyageQueryActivation, PreS5VoyageEvaluationBatchActivation)
    ):
        raise PreS5VoyageQueryUsageRepositoryError("PRE_S5_VOYAGE_QUERY_LEASE_ARGUMENT")
    if (
        not _is_sha256(activation.packet_sha256)
        or not _is_sha256(activation.nonce_sha256)
        or not _is_sha256(activation.query_sha256)
        or not _is_sha256(activation.scope_claim_sha256)
        or not _is_sha256(activation.rate_evidence_sha256)
        or not _is_sha256(activation.tokenizer_sha256)
        or activation.provider != "VOYAGE"
        or activation.operation != "CONTEXTUALIZED_QUERY_EMBEDDING"
        or activation.origin != "https://api.voyageai.com"
        or activation.endpoint != "/v1/contextualizedembeddings"
        or not isinstance(activation.expires_at, datetime)
        or activation.expires_at.tzinfo is None
        or activation.logical_call_cap != 1
        or activation.physical_call_cap != 1
        or type(activation.token_cap) is not int
        or not 1 <= activation.token_cap <= 8_192
        or type(activation.byte_cap) is not int
        or not 1 <= activation.byte_cap <= 4_194_304
        or type(activation.cost_cap_microusd) is not int
        or not 1 <= activation.cost_cap_microusd <= 1_000_000_000
        or type(activation.input_microusd_per_token) is not int
        or not 1 <= activation.input_microusd_per_token <= 1_000_000
        or activation.token_cap * activation.input_microusd_per_token
        > activation.cost_cap_microusd
        or activation.retry_count != 0
        or activation.raw_artifact_count != 0
    ):
        raise PreS5VoyageQueryUsageRepositoryError("PRE_S5_VOYAGE_QUERY_LEASE_ARGUMENT")


def _usage_event_id(
    activation: PreS5VoyageQueryActivation | PreS5VoyageEvaluationBatchActivation,
) -> str:
    """The packet/nonce/query/scope hash tuple is deterministic, making replay conflict before an attempt row exists."""

    digest = hashlib.sha256(
        (
            "pre-s5-voyage-query-usage-v1\0"
            f"{activation.packet_sha256}\0{activation.nonce_sha256}\0"
            f"{activation.query_sha256}\0{activation.scope_claim_sha256}"
        ).encode("utf-8")
    ).hexdigest()
    return f"rgr_vqu_{digest[:32]}"


def _set_transaction_timeouts(connection: psycopg.Connection[Any]) -> None:
    """A local ledger wait must not transform the single-attempt call into an indefinite retry loop."""

    connection.execute("SET LOCAL statement_timeout = '5s'")
    connection.execute("SET LOCAL lock_timeout = '500ms'")
    connection.execute("SET LOCAL idle_in_transaction_session_timeout = '6s'")


def _attest_writer_connection(connection: psycopg.Connection[Any]) -> None:
    """Verify the supplied DSN has only V46 function capability, never direct query-usage tables."""

    if connection.execute("SELECT current_user").fetchone() != (_WRITER_ROLE,):
        raise PreS5VoyageQueryUsageRepositoryError("PRE_S5_VOYAGE_QUERY_LEASE_WRITER_ROLE")
    for table in _WRITER_FORBIDDEN_TABLES:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            row = connection.execute(
                "SELECT has_table_privilege(current_user, %s, %s)",
                (f"public.{table}", privilege),
            ).fetchone()
            if row is not None and row[0] is True:
                raise PreS5VoyageQueryUsageRepositoryError("PRE_S5_VOYAGE_QUERY_LEASE_WRITER_PRIVILEGE")
    for function in (
        _RESERVE_FUNCTION,
        _CLAIM_FUNCTION,
        _COMMIT_FUNCTION,
        _UNKNOWN_FUNCTION,
        _EVALUATION_RESERVE_FUNCTION,
        _EVALUATION_CLAIM_FUNCTION,
        _EVALUATION_UNKNOWN_FUNCTION,
        _EVALUATION_STAGE_FUNCTION,
        _EVALUATION_LOAD_FUNCTION,
    ):
        row = connection.execute(
            "SELECT has_function_privilege(current_user, %s, 'EXECUTE')",
            (function,),
        ).fetchone()
        if row is None or row[0] is not True:
            raise PreS5VoyageQueryUsageRepositoryError("PRE_S5_VOYAGE_QUERY_LEASE_WRITER_PRIVILEGE")


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _evaluation_vector_rows(
    vectors: Mapping[str, Sequence[float]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for query_sha256, raw_vector in sorted(vectors.items()):
        if not _is_sha256(query_sha256):
            raise PreS5VoyageQueryUsageRepositoryError("PRE_S5_VOYAGE_EVALUATION_STAGE_VECTOR")
        try:
            vector = np.asarray(raw_vector, dtype=np.float32)
        except Exception:
            raise PreS5VoyageQueryUsageRepositoryError(
                "PRE_S5_VOYAGE_EVALUATION_STAGE_VECTOR"
            ) from None
        if (
            vector.shape != (1024,)
            or not bool(np.isfinite(vector).all())
            or not math.isclose(float(np.linalg.norm(vector)), 1.0, rel_tol=0.0, abs_tol=1e-5)
        ):
            raise PreS5VoyageQueryUsageRepositoryError("PRE_S5_VOYAGE_EVALUATION_STAGE_VECTOR")
        normalized = cast(NDArray[np.float32], vector)
        rows.append(
            {
                "embedding": [float(value) for value in normalized],
                "querySha256": query_sha256,
                "vectorSha256": hashlib.sha256(
                    normalized.astype("<f4", copy=False).tobytes()
                ).hexdigest(),
            }
        )
    return rows


def _parse_vector(value: object) -> tuple[float, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise PreS5VoyageQueryUsageRepositoryError(
                "PRE_S5_VOYAGE_EVALUATION_RESUME_RECEIPT"
            ) from None
    try:
        vector = np.asarray(value, dtype=np.float32)
    except Exception:
        raise PreS5VoyageQueryUsageRepositoryError(
            "PRE_S5_VOYAGE_EVALUATION_RESUME_RECEIPT"
        ) from None
    if (
        vector.shape != (1024,)
        or not bool(np.isfinite(vector).all())
        or not math.isclose(float(np.linalg.norm(vector)), 1.0, rel_tol=0.0, abs_tol=1e-5)
    ):
        raise PreS5VoyageQueryUsageRepositoryError(
            "PRE_S5_VOYAGE_EVALUATION_RESUME_RECEIPT"
        )
    return tuple(float(item) for item in vector)
