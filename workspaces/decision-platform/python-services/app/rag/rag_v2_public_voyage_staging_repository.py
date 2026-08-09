from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias, cast

import psycopg
from psycopg.types.json import Jsonb

from app.rag.rag_v2_external_exact30_voyage_runner import RagV2VoyageMaterializedPublicDocument
from app.rag.rag_v2_public_voyage_staging import (
    PublicVoyageComponentContext,
    build_public_voyage_staging_payload,
)

_WRITER_ROLE = "decision_rag_writer"
_VOYAGE_PROFILE_ID = "voyage_context_4_1024_v1"
_GENERATION_ID = re.compile(r"^rgr_[0-9a-f]{32}$")
_RUN_ID = re.compile(r"^rgr_run_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STAGE_FUNCTION = "public.stage_rag_v2_immutable_public_voyage_document(jsonb)"
_EVALUATE_FUNCTION = "public.evaluate_rag_v2_immutable_public_voyage_component(text,jsonb)"
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
    "rag_v2_immutable_public_voyage_component_evaluations",
    "rag_v2_immutable_public_voyage_component_manifests",
    "rag_v2_immutable_exact30_source_allowlist",
    "rag_v2_immutable_import_tickets",
)

PublicVoyageRecord: TypeAlias = RagV2VoyageMaterializedPublicDocument
PublicVoyageComponentScope: TypeAlias = Literal["EXACT30", "OA112"]


class PublicVoyageStagingRepositoryError(ValueError):
    """public Voyage staging/evaluation의 capability 또는 immutable receipt 경계 실패다."""


@dataclass(frozen=True, slots=True)
class RagV2PublicVoyageStagingReceipt:
    """public source 한 건의 content-free resumable staging receipt다."""

    component_generation_id: str
    materialization_run_id: str
    component_scope: PublicVoyageComponentScope
    embedding_profile_id: Literal["voyage_context_4_1024_v1"]
    state: Literal["STAGING", "STAGED"]
    source_reused: bool
    source_count: int
    chunk_count: int


@dataclass(frozen=True, slots=True)
class PublicVoyageEvaluationEvidence:
    """local-only evaluator가 만든 aggregate acceptance metric만 writer에 bind한다.

    원문, query, citation text, owner identifier, provider response는 이 record에 넣지 않는다. DB는
    threshold와 generation cardinality를 다시 검증하므로 이 dataclass는 activation 허가가 아니다.
    """

    evaluation_digest: str
    evaluation_scope_claim_sha256: str
    exact_top5_hit_rate: float
    track_recall_at5: float
    citation_coverage: float
    direct_advice_block_rate: float
    cross_owner_leak_count: int
    mixed_profile_row_count: int
    owner_delete_residual_row_count: int
    warm_p95_millis: float
    provider_physical_call_count: int

    def __post_init__(self) -> None:
        ratios = (
            self.exact_top5_hit_rate,
            self.track_recall_at5,
            self.citation_coverage,
            self.direct_advice_block_rate,
        )
        counts = (
            self.cross_owner_leak_count,
            self.mixed_profile_row_count,
            self.owner_delete_residual_row_count,
            self.provider_physical_call_count,
        )
        if (
            _SHA256.fullmatch(self.evaluation_digest) is None
            or _SHA256.fullmatch(self.evaluation_scope_claim_sha256) is None
            or any(type(value) is not float or not math.isfinite(value) or not 0 <= value <= 1 for value in ratios)
            or any(type(value) is not int or value < 0 for value in counts)
            or type(self.warm_p95_millis) is not float
            or not math.isfinite(self.warm_p95_millis)
            or self.warm_p95_millis <= 0
        ):
            raise PublicVoyageStagingRepositoryError("PUBLIC_VOYAGE_EVALUATION_ARGUMENT")

    def as_payload(self) -> dict[str, float | int | str]:
        """DB function의 closed JSON shape를 생성한다."""

        return {
            "citationCoverage": self.citation_coverage,
            "crossOwnerLeakCount": self.cross_owner_leak_count,
            "directAdviceBlockRate": self.direct_advice_block_rate,
            "evaluationDigest": self.evaluation_digest,
            "evaluationScopeClaimSha256": self.evaluation_scope_claim_sha256,
            "exactTop5HitRate": self.exact_top5_hit_rate,
            "mixedProfileRowCount": self.mixed_profile_row_count,
            "ownerDeleteResidualRowCount": self.owner_delete_residual_row_count,
            "providerPhysicalCallCount": self.provider_physical_call_count,
            "schemaVersion": 1,
            "trackRecallAt5": self.track_recall_at5,
            "warmP95Millis": self.warm_p95_millis,
        }


@dataclass(frozen=True, slots=True)
class RagV2PublicVoyageEvaluationReceipt:
    """evaluation 후 activation-adapter가 사용할 content-free component state다."""

    component_generation_id: str
    component_scope: PublicVoyageComponentScope
    embedding_profile_id: Literal["voyage_context_4_1024_v1"]
    state: Literal["EVALUATED"]
    source_count: int
    chunk_count: int


class PsycopgRagV2PublicVoyageStagingRepository:
    """`decision_rag_writer` DSN로 public Voyage writer capability만 호출한다.

    source마다 독립 transaction을 commit해 parser/model crash 뒤에도 이미 verified source graph를
    resume할 수 있게 한다. 직접 source/chunk/vector/evaluation table access는 attestation에서 거부한다.
    """

    def __init__(self, *, database_dsn: str) -> None:
        if not database_dsn or len(database_dsn) > 4_096:
            raise PublicVoyageStagingRepositoryError("PUBLIC_VOYAGE_STAGE_DATABASE_DSN")
        self._database_dsn = database_dsn

    def stage(
        self,
        *,
        record: PublicVoyageRecord,
        context: PublicVoyageComponentContext,
    ) -> RagV2PublicVoyageStagingReceipt:
        """public source 한 건을 한 transaction으로 immutable graph에 stage한다.

        raw-derived IR/text/vector는 only process-local JSON parameter로 존재하고 receipt, exception,
        logs에는 복사하지 않는다. function이 returned source count를 exact context cap 안에서 검증한다.
        """

        payload = build_public_voyage_staging_payload(record, context=context)
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
                        FROM public.stage_rag_v2_immutable_public_voyage_document(%s::jsonb)
                        """,
                        (Jsonb(payload),),
                    ).fetchone()
        except PublicVoyageStagingRepositoryError:
            raise
        except psycopg.Error:
            # SQLSTATE/message can contain source metadata or DB path details. The caller receives only a
            # stable resumable marker and must not echo a chained database traceback to stdout or history.
            raise PublicVoyageStagingRepositoryError("PUBLIC_VOYAGE_STAGE_REJECTED") from None
        return _staging_receipt(row, context=context)

    def stage_component(
        self,
        *,
        records: Sequence[PublicVoyageRecord],
        context: PublicVoyageComponentContext,
    ) -> tuple[RagV2PublicVoyageStagingReceipt, ...]:
        """full exact-30/OA112 component를 source-transaction 단위로 resumable stage한다.

        connection은 autocommit mode에서 attestation만 공유한다. 각 source는 독립 explicit
        transaction으로 넣으므로, 뒤 source의 실패가 앞 immutable receipt를 지우거나 writer의
        raw-table capability를 넓히지 않는다.
        """

        payloads = _component_payloads(records=records, context=context)
        receipts: list[RagV2PublicVoyageStagingReceipt] = []
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
                            FROM public.stage_rag_v2_immutable_public_voyage_document(%s::jsonb)
                            """,
                            (Jsonb(payload),),
                        ).fetchone()
                    receipts.append(_staging_receipt(row, context=context))
        except PublicVoyageStagingRepositoryError:
            raise
        except psycopg.Error:
            raise PublicVoyageStagingRepositoryError("PUBLIC_VOYAGE_STAGE_REJECTED") from None
        return tuple(receipts)

    def evaluate(
        self,
        *,
        context: PublicVoyageComponentContext,
        evidence: PublicVoyageEvaluationEvidence,
    ) -> RagV2PublicVoyageEvaluationReceipt:
        """complete Voyage component의 aggregate evaluation evidence만 immutable로 기록한다.

        이 호출은 public pointer를 activate하지 않는다. DB가 source/chunk/vector count와 모든
        acceptance threshold를 재검증한 뒤에만 `PENDING`을 `PASSED`로 바꾼다.
        """

        _validate_context(context)
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
                        SELECT component_generation_id, state, source_count, chunk_count
                        FROM public.evaluate_rag_v2_immutable_public_voyage_component(%s, %s::jsonb)
                        """,
                        (context.component_generation_id, Jsonb(evidence.as_payload())),
                    ).fetchone()
        except PublicVoyageStagingRepositoryError:
            raise
        except psycopg.Error:
            raise PublicVoyageStagingRepositoryError("PUBLIC_VOYAGE_EVALUATION_REJECTED") from None
        return _evaluation_receipt(row, context=context)


def _component_payloads(
    *,
    records: Sequence[PublicVoyageRecord],
    context: PublicVoyageComponentContext,
) -> tuple[dict[str, object], ...]:
    """source identity를 deterministic order로 close해 duplicate/partial component writes를 막는다."""

    _validate_context(context)
    if len(records) != context.expected_source_count:
        raise PublicVoyageStagingRepositoryError("PUBLIC_VOYAGE_STAGE_COMPONENT_MEMBERSHIP")
    payloads = tuple(build_public_voyage_staging_payload(record, context=context) for record in records)
    source_ids: list[str] = []
    source_revision_ids: list[str] = []
    chunk_count = 0
    for payload in payloads:
        source = payload.get("source")
        if not isinstance(source, dict):
            raise PublicVoyageStagingRepositoryError("PUBLIC_VOYAGE_STAGE_COMPONENT_MEMBERSHIP")
        source_id = source.get("sourceId")
        source_revision_id = source.get("sourceRevisionId")
        chunks = source.get("chunks")
        if (
            not isinstance(source_id, str)
            or not isinstance(source_revision_id, str)
            or not isinstance(chunks, list)
            or not chunks
        ):
            raise PublicVoyageStagingRepositoryError("PUBLIC_VOYAGE_STAGE_COMPONENT_MEMBERSHIP")
        source_ids.append(source_id)
        source_revision_ids.append(source_revision_id)
        chunk_count += len(chunks)
    if (
        len(set(source_ids)) != context.expected_source_count
        or len(set(source_revision_ids)) != context.expected_source_count
        or chunk_count != context.expected_chunk_count
    ):
        raise PublicVoyageStagingRepositoryError("PUBLIC_VOYAGE_STAGE_COMPONENT_MEMBERSHIP")
    return tuple(
        sorted(
            payloads,
            key=_payload_source_id_for_order,
        )
    )


def _payload_source_id_for_order(payload: dict[str, object]) -> bytes:
    """같은 component의 source stage 순서를 canonical UTF-8 sourceId로 고정한다."""

    source = payload.get("source")
    if not isinstance(source, dict):
        raise PublicVoyageStagingRepositoryError("PUBLIC_VOYAGE_STAGE_COMPONENT_MEMBERSHIP")
    source_id = source.get("sourceId")
    if not isinstance(source_id, str):
        raise PublicVoyageStagingRepositoryError("PUBLIC_VOYAGE_STAGE_COMPONENT_MEMBERSHIP")
    return source_id.encode("utf-8")


def _validate_context(context: PublicVoyageComponentContext) -> None:
    """repository boundary가 strict local payload module과 같은 full-component invariants를 유지한다."""

    expected_source_count = {"EXACT30": 30, "OA112": 112}.get(context.component_scope)
    if (
        expected_source_count is None
        or context.expected_source_count != expected_source_count
        or context.expected_chunk_count < expected_source_count
        or context.embedding_profile_id != _VOYAGE_PROFILE_ID
        or _GENERATION_ID.fullmatch(context.component_generation_id) is None
        or _RUN_ID.fullmatch(context.materialization_run_id) is None
        or _SHA256.fullmatch(context.generation_hash) is None
        or _SHA256.fullmatch(context.manifest_hash) is None
        or len(context.member_digests) != expected_source_count
        or any(_SHA256.fullmatch(member_digest) is None for member_digest in context.member_digests)
        or len(set(context.member_digests)) != expected_source_count
    ):
        raise PublicVoyageStagingRepositoryError("PUBLIC_VOYAGE_STAGE_COMPONENT_CONTEXT")


def _set_transaction_timeouts(connection: psycopg.Connection[Any]) -> None:
    """source advisory lock race를 bounded resume로 처리하되 indefinite DB wait를 만들지 않는다."""

    connection.execute("SET LOCAL statement_timeout = '60s'")
    connection.execute("SET LOCAL lock_timeout = '10s'")
    connection.execute("SET LOCAL idle_in_transaction_session_timeout = '75s'")


def _staging_receipt(
    row: tuple[object, ...] | None,
    *,
    context: PublicVoyageComponentContext,
) -> RagV2PublicVoyageStagingReceipt:
    """DB result을 closed count/state receipt로 validate해 malformed definer output을 fail-close한다."""

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
        or (row[2] == "STAGED" and (row[4] != context.expected_source_count or row[5] != context.expected_chunk_count))
    ):
        raise PublicVoyageStagingRepositoryError("PUBLIC_VOYAGE_STAGE_RECEIPT")
    return RagV2PublicVoyageStagingReceipt(
        component_generation_id=row[0],
        materialization_run_id=row[1],
        component_scope=cast(PublicVoyageComponentScope, context.component_scope),
        embedding_profile_id="voyage_context_4_1024_v1",
        state=row[2],
        source_reused=row[3],
        source_count=row[4],
        chunk_count=row[5],
    )


def _evaluation_receipt(
    row: tuple[object, ...] | None,
    *,
    context: PublicVoyageComponentContext,
) -> RagV2PublicVoyageEvaluationReceipt:
    """evaluation result이 full expected component만 나타내는지 application에서도 재검증한다."""

    _validate_context(context)
    if (
        row is None
        or len(row) != 4
        or not isinstance(row[0], str)
        or row[0] != context.component_generation_id
        or row[1] != "EVALUATED"
        or type(row[2]) is not int
        or type(row[3]) is not int
        or row[2] != context.expected_source_count
        or row[3] != context.expected_chunk_count
    ):
        raise PublicVoyageStagingRepositoryError("PUBLIC_VOYAGE_EVALUATION_RECEIPT")
    return RagV2PublicVoyageEvaluationReceipt(
        component_generation_id=row[0],
        component_scope=cast(PublicVoyageComponentScope, context.component_scope),
        embedding_profile_id="voyage_context_4_1024_v1",
        state="EVALUATED",
        source_count=row[2],
        chunk_count=row[3],
    )


def _attest_writer_connection(connection: psycopg.Connection[Any]) -> None:
    """writer DSN가 direct immutable graph grant 없이 exact two definer calls만 갖는지 확인한다."""

    if connection.execute("SELECT current_user").fetchone() != (_WRITER_ROLE,):
        raise PublicVoyageStagingRepositoryError("PUBLIC_VOYAGE_STAGE_WRITER_ROLE")
    for table in _WRITER_FORBIDDEN_TABLES:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            row = connection.execute(
                "SELECT has_table_privilege(current_user, %s, %s)",
                (f"public.{table}", privilege),
            ).fetchone()
            if row is not None and row[0] is True:
                raise PublicVoyageStagingRepositoryError("PUBLIC_VOYAGE_STAGE_WRITER_PRIVILEGE")
    for signature in (_STAGE_FUNCTION, _EVALUATE_FUNCTION):
        row = connection.execute(
            "SELECT has_function_privilege(current_user, %s, 'EXECUTE')",
            (signature,),
        ).fetchone()
        if row is None or row[0] is not True:
            raise PublicVoyageStagingRepositoryError("PUBLIC_VOYAGE_STAGE_WRITER_PRIVILEGE")
