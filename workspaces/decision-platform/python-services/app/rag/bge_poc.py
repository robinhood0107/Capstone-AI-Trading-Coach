from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Sequence, cast
from urllib.parse import urlsplit

import numpy as np
import psycopg
from numpy.typing import NDArray

from app.rag.bge_artifact import BgeVerifiedPacket
from app.rag.bge_runtime import validate_embedding_batch
from app.rag.ingest_pipeline import (
    RagCanonicalChunk,
    RagEmbeddingInput,
    RagTokenizer,
    build_canonical_chunks,
    build_embedding_inputs,
    parse_markdown_document,
)
from app.rag.official_evidence import OfficialEvidenceReceipt
from app.rag.source_card import RagSourceCard

_PROFILE_ID = "bge_m3_local_1024_v1"
_MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
_TOKENIZER_SHA256 = "6710678b12670bc442b99edc952c4d996ae309a7020c1fa0096dd245c2faf790"
_PARSER_VERSION = "rag-markdown-heading-v2"
_CANONICALIZER_VERSION = "utf8-nfc-lf-source-card-body-v1"
_CHUNKER_VERSION = "bge-tokenizer-heading-400-600-v1"
_INPUT_STRATEGY_VERSION = "adjacent-7.5pct-per-side-no-reallocation-v1"
_EXPECTED_ARTIFACT_FILE_COUNT = 10
_EXPECTED_ARTIFACT_BYTES = 2_289_781_803
_EXPECTED_WRITER_ROLE = "decision_rag_writer"
_ALLOWED_TARGETS = frozenset({"local", "offline", "test", "testcontainers"})
_TARGET_ENV = "RAG_SOURCE_REGISTER_TARGET"
APPROVED_FIVE_CARD_IDENTITIES = frozenset(
    {
        (
            "src_project_ecos_pit_availability_001",
            "card_ecos_pit_availability_001",
        ),
        (
            "src_project_gold_futures_etf_132030_001",
            "card_gold_futures_etf_132030_001",
        ),
        (
            "src_project_kis_adjusted_price_001",
            "card_kis_adjusted_price_001",
        ),
        (
            "src_project_krx_service_coverage_001",
            "card_krx_service_coverage_001",
        ),
        (
            "src_project_opendart_status_quota_001",
            "card_opendart_status_quota_001",
        ),
    }
)


class BgePocError(ValueError):
    """5-card PoC의 membership·embedding·DB 최소권한 위반을 bounded marker로 보고한다."""


@dataclass(frozen=True)
class BgePocItem:
    """한 source card의 immutable revision/ingest/chunk/input 계획."""

    card: RagSourceCard
    source_revision_id: str
    ingest_run_id: str
    chunk: RagCanonicalChunk
    embedding_input: RagEmbeddingInput


@dataclass(frozen=True)
class BgePocPlan:
    """artifact와 exact 5-card corpus를 결합한 deterministic PoC generation identity."""

    generation_id: str
    generation_hash: str
    corpus_hash: str
    materialization_run_id: str
    embedding_profile_id: str
    model_revision: str
    artifact_manifest_sha256: str
    tokenizer_sha256: str
    parser_version: str
    canonicalizer_version: str
    chunker_version: str
    input_strategy_version: str
    items: tuple[BgePocItem, ...]


@dataclass(frozen=True)
class BgeStagedEmbedding:
    """COPY staging 한 행의 application-level immutable receipt."""

    generation_id: str
    materialization_run_id: str
    chunk_revision_id: str
    embedding_profile_id: str
    embedding_input_hash: str
    context_set_hash: str | None
    embedding: NDArray[np.float32]
    staging_row_hash: str


@dataclass(frozen=True)
class BgePocDatabaseReceipt:
    """bounded finalizer 이후 공개 가능한 DB 상태."""

    generation_id: str
    materialization_run_id: str
    final_row_count: int
    status: Literal["EVAL_PASSED"]
    active_pointer_changed: bool


class BgeEmbeddingPort(Protocol):
    def embed(self, texts: tuple[str, ...]) -> NDArray[np.float32]:
        """network 없이 canonical BGE input 묶음을 1024-d unit vector로 변환한다."""


class BgePocRepositoryPort(Protocol):
    def materialize(
        self,
        *,
        plan: BgePocPlan,
        rows: tuple[BgeStagedEmbedding, ...],
        staging_hash: str,
    ) -> BgePocDatabaseReceipt:
        """staging COPY와 bounded finalize를 한 transaction으로 수행한다."""


def prepare_bge_poc(
    *,
    cards: Sequence[RagSourceCard],
    tokenizer: RagTokenizer,
    artifact: BgeVerifiedPacket,
    official_evidence: OfficialEvidenceReceipt,
) -> BgePocPlan:
    """manifest에 결합된 exact 공식 5-card를 한 chunk씩 동결해 PoC plan을 만든다."""

    identities = {(card.source_id, card.card_id) for card in cards}
    if (
        len(cards) != 5
        or len(identities) != 5
        or identities != APPROVED_FIVE_CARD_IDENTITIES
        or any(
            card.status != "VERIFIED"
            or card.external_processing_allowed
            or not card.canonical_body.endswith("\n")
            for card in cards
        )
    ):
        raise BgePocError("FIVE_CARD_MEMBERSHIP")
    if (
        len(official_evidence.source_ids) != 5
        or len(set(official_evidence.source_ids)) != 5
        or len(official_evidence.evidence_sha256) != 5
        or len(official_evidence.source_card_content_sha256) != 5
    ):
        raise BgePocError("OFFICIAL_EVIDENCE_RECEIPT")
    evidence_by_source_id = {
        source_id: (evidence_sha256, card_content_sha256)
        for source_id, evidence_sha256, card_content_sha256 in zip(
            official_evidence.source_ids,
            official_evidence.evidence_sha256,
            official_evidence.source_card_content_sha256,
            strict=True,
        )
    }
    if (
        set(evidence_by_source_id) != {card.source_id for card in cards}
        or any(
            evidence_by_source_id[card.source_id]
            != (card.evidence_content_sha256, card.content_sha256)
            for card in cards
        )
    ):
        # identity만 유지한 card drift도 DB materialization 전에 manifest receipt에서 차단한다.
        raise BgePocError("OFFICIAL_EVIDENCE_CARD_BINDING")
    if (
        artifact.revision != _MODEL_REVISION
        or artifact.file_count != _EXPECTED_ARTIFACT_FILE_COUNT
        or artifact.total_bytes != _EXPECTED_ARTIFACT_BYTES
        or len(artifact.file_manifest_sha256) != 64
    ):
        raise BgePocError("BGE_ARTIFACT_RECEIPT")

    items: list[BgePocItem] = []
    for card in sorted(cards, key=lambda value: value.source_id.encode("utf-8")):
        source_revision_id = f"src_rev_{_hash_parts(card.source_id, card.content_sha256)[:32]}"
        ingest_run_id = (
            f"rag_ing_"
            f"{_hash_parts(source_revision_id, _PARSER_VERSION, _CANONICALIZER_VERSION)[:32]}"
        )
        chunks = build_canonical_chunks(
            source_id=card.source_id,
            source_revision_id=source_revision_id,
            blocks=parse_markdown_document(card.canonical_body),
            tokenizer=tokenizer,
            min_tokens=400,
            max_tokens=600,
            atomic_document=True,
        )
        if len(chunks) != 1:
            raise BgePocError("ONE_CARD_ONE_CHUNK")
        embedding_inputs = build_embedding_inputs(
            chunks,
            embedding_profile_id=_PROFILE_ID,
            tokenizer=tokenizer,
        )
        if len(embedding_inputs) != 1:
            raise BgePocError("ONE_CARD_ONE_EMBEDDING_INPUT")
        items.append(
            BgePocItem(
                card=card,
                source_revision_id=source_revision_id,
                ingest_run_id=ingest_run_id,
                chunk=chunks[0],
                embedding_input=embedding_inputs[0],
            )
        )

    corpus_payload = {
        "items": [
            {
                "cardContentSha256": item.card.content_sha256,
                "cardId": item.card.card_id,
                "canonicalContentHash": item.chunk.content_hash,
                "chunkRevisionId": item.chunk.chunk_revision_id,
                "embeddingInputHash": item.embedding_input.embedding_input_hash,
                "sourceId": item.card.source_id,
            }
            for item in items
        ],
        "membership": "S4.2A_EXACT_OFFICIAL_FIVE",
    }
    corpus_hash = _canonical_json_hash(corpus_payload)
    generation_payload = {
        "artifactManifestSha256": artifact.file_manifest_sha256,
        "canonicalizerVersion": _CANONICALIZER_VERSION,
        "chunkerVersion": _CHUNKER_VERSION,
        "corpusHash": corpus_hash,
        "embeddingProfileId": _PROFILE_ID,
        "inputStrategyVersion": _INPUT_STRATEGY_VERSION,
        "modelRevision": artifact.revision,
        "parserVersion": _PARSER_VERSION,
        "tokenizerSha256": _TOKENIZER_SHA256,
    }
    generation_hash = _canonical_json_hash(generation_payload)
    generation_id = f"rag_gen_{generation_hash[:32]}"
    materialization_run_id = (
        f"rag_mat_{_hash_parts(generation_id, artifact.file_manifest_sha256)[:32]}"
    )
    return BgePocPlan(
        generation_id=generation_id,
        generation_hash=generation_hash,
        corpus_hash=corpus_hash,
        materialization_run_id=materialization_run_id,
        embedding_profile_id=_PROFILE_ID,
        model_revision=artifact.revision,
        artifact_manifest_sha256=artifact.file_manifest_sha256,
        tokenizer_sha256=_TOKENIZER_SHA256,
        parser_version=_PARSER_VERSION,
        canonicalizer_version=_CANONICALIZER_VERSION,
        chunker_version=_CHUNKER_VERSION,
        input_strategy_version=_INPUT_STRATEGY_VERSION,
        items=tuple(items),
    )


def execute_bge_poc(
    *,
    plan: BgePocPlan,
    embedder: BgeEmbeddingPort,
    repository: BgePocRepositoryPort,
) -> BgePocDatabaseReceipt:
    """CPU embedding을 검증한 뒤 staging/finalize port로 넘기고 EVAL_PASSED만 수용한다."""

    texts = tuple(item.embedding_input.text for item in plan.items)
    embedding_batch = validate_embedding_batch(
        embedder.embed(texts),
        expected_rows=len(texts),
    )
    rows = tuple(
        _build_staging_row(plan=plan, item=item, embedding=embedding_batch[index])
        for index, item in enumerate(plan.items)
    )
    staging_hash = hashlib.sha256(
        "".join(
            row.staging_row_hash
            for row in sorted(rows, key=lambda value: value.chunk_revision_id.encode("utf-8"))
        ).encode("ascii")
    ).hexdigest()
    receipt = repository.materialize(
        plan=plan,
        rows=rows,
        staging_hash=staging_hash,
    )
    if (
        receipt.generation_id != plan.generation_id
        or receipt.materialization_run_id != plan.materialization_run_id
        or receipt.final_row_count != len(plan.items)
        or receipt.status != "EVAL_PASSED"
        or receipt.active_pointer_changed
    ):
        raise BgePocError("POC_DATABASE_RECEIPT")
    return receipt


class PsycopgBgePocRepository:
    """전용 writer DSN으로 5-card append/COPY/finalize만 수행하는 PostgreSQL adapter."""

    def __init__(self, *, database_dsn: str) -> None:
        if not database_dsn or len(database_dsn) > 4_096:
            raise BgePocError("POC_DATABASE_DSN")
        self._database_dsn = database_dsn

    def materialize(
        self,
        *,
        plan: BgePocPlan,
        rows: tuple[BgeStagedEmbedding, ...],
        staging_hash: str,
    ) -> BgePocDatabaseReceipt:
        _require_offline_target()
        try:
            with psycopg.connect(
                self._database_dsn,
                autocommit=False,
                connect_timeout=2,
            ) as connection:
                _attest_writer_connection(connection)
                with connection.transaction():
                    connection.execute("set local statement_timeout = '30s'")
                    connection.execute("set local lock_timeout = '1s'")
                    connection.execute(
                        "set local idle_in_transaction_session_timeout = '45s'"
                    )
                    _insert_cards_and_chunks(connection, plan=plan)
                    _insert_generation_membership(connection, plan=plan)
                    _copy_staging_rows(connection, rows=rows)
                    finalized = _required_int(
                        connection.execute(
                            """
                            SELECT public.finalize_rag_embedding_staging(
                              %s, %s, %s, %s, %s
                            )
                            """,
                            (
                                plan.generation_id,
                                plan.materialization_run_id,
                                _EXPECTED_WRITER_ROLE,
                                len(rows),
                                staging_hash,
                            ),
                        ).fetchone()
                    )
                    if finalized != len(rows):
                        raise BgePocError("POC_FINALIZE_COUNT")
                    connection.execute(
                        """
                        UPDATE rag_corpus_generations
                        SET status = 'MATERIALIZED'
                        WHERE corpus_generation_id = %s
                          AND status = 'MATERIALIZING'
                          AND actual_chunk_count = expected_chunk_count
                        """,
                        (plan.generation_id,),
                    )
                    connection.execute(
                        """
                        UPDATE rag_corpus_generations
                        SET status = 'EVAL_PASSED',
                            evaluation_status = 'PASSED',
                            evaluated_at = transaction_timestamp()
                        WHERE corpus_generation_id = %s
                          AND status = 'MATERIALIZED'
                        """,
                        (plan.generation_id,),
                    )
                    status_row = connection.execute(
                        """
                        SELECT status, actual_chunk_count, activated_at IS NOT NULL
                        FROM rag_corpus_generations
                        WHERE corpus_generation_id = %s
                        """,
                        (plan.generation_id,),
                    ).fetchone()
                    if (
                        status_row is None
                        or str(status_row[0]) != "EVAL_PASSED"
                        or _required_int((status_row[1],)) != len(rows)
                        or bool(status_row[2])
                    ):
                        raise BgePocError("POC_GENERATION_STATUS")
        except BgePocError:
            raise
        except psycopg.Error as error:
            raise BgePocError("POC_DATABASE_OPERATION_FAILED") from error
        return BgePocDatabaseReceipt(
            generation_id=plan.generation_id,
            materialization_run_id=plan.materialization_run_id,
            final_row_count=len(rows),
            status="EVAL_PASSED",
            active_pointer_changed=False,
        )


def _insert_cards_and_chunks(
    connection: psycopg.Connection[Any],
    *,
    plan: BgePocPlan,
) -> None:
    for item in plan.items:
        card = item.card
        locator = urlsplit(card.canonical_url)
        if locator.scheme != "https" or not locator.hostname or not locator.path:
            raise BgePocError("POC_CARD_LOCATOR")
        allowed_origin = f"https://{locator.hostname}"
        if locator.port not in (None, 443):
            allowed_origin += f":{locator.port}"
        connection.execute(
            """
            INSERT INTO rag_sources (
              source_id, source_type, institution, topic, owner_identity
            )
            VALUES (%s, 'PROJECT_SOURCE_CARD', %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                card.source_id,
                card.institution,
                card.topic,
                card.retention_owner,
            ),
        )
        connection.execute(
            """
            INSERT INTO rag_source_revisions (
              source_revision_id, source_id, revision_seq, registry_version,
              title, tier, access_level, license_decision, license_note, attribution,
              retention_mode, retention_days, retention_owner, external_processing_allowed,
              initial_processing, canonical_url, allowed_origin, allowed_path,
              locator_sha256, metadata_hash
            )
            VALUES (
              %s, %s, 1, 's4-7a-source-card-v1',
              %s, 'PROJECT', 'PUBLIC', 'PROJECT_AUTHORED_PUBLIC', %s, %s,
              'PROJECT_CARD', %s, %s, %s,
              'PROJECT_AUTHORED_CARD', %s, %s, %s,
              %s, %s
            )
            ON CONFLICT DO NOTHING
            """,
            (
                item.source_revision_id,
                card.source_id,
                card.title,
                card.license_note,
                card.attribution,
                card.retention_days,
                card.retention_owner,
                card.external_processing_allowed,
                card.canonical_url,
                allowed_origin,
                locator.path,
                card.canonical_url_sha256,
                card.content_sha256,
            ),
        )
        connection.execute(
            """
            INSERT INTO rag_ingest_runs (
              ingest_run_id, source_revision_id, parser_version, canonicalizer_version,
              card_schema_version, input_content_hash, status, expected_chunk_count
            )
            VALUES (%s, %s, %s, %s, 'rag-source-card-v1', %s, 'PLANNED', 1)
            """,
            (
                item.ingest_run_id,
                item.source_revision_id,
                plan.parser_version,
                plan.canonicalizer_version,
                card.content_sha256,
            ),
        )
        connection.execute(
            """
            UPDATE rag_ingest_runs
            SET status = 'RUNNING', started_at = transaction_timestamp()
            WHERE ingest_run_id = %s AND status = 'PLANNED'
            """,
            (item.ingest_run_id,),
        )
        connection.execute(
            """
            INSERT INTO rag_chunk_revisions (
              chunk_revision_id, ingest_run_id, source_revision_id, chunk_seq,
              heading_path, canonical_content, canonical_content_hash, token_count,
              topic, access_level, tier
            )
            VALUES (%s, %s, %s, 1, %s, %s, %s, %s, %s, 'PUBLIC', 'PROJECT')
            """,
            (
                item.chunk.chunk_revision_id,
                item.ingest_run_id,
                item.source_revision_id,
                list(item.chunk.heading_path),
                item.chunk.text,
                item.chunk.content_hash,
                item.chunk.token_count,
                card.topic,
            ),
        )
        connection.execute(
            """
            UPDATE rag_ingest_runs
            SET status = 'SUCCEEDED',
                actual_chunk_count = 1,
                completed_at = transaction_timestamp()
            WHERE ingest_run_id = %s AND status = 'RUNNING'
            """,
            (item.ingest_run_id,),
        )


def _insert_generation_membership(
    connection: psycopg.Connection[Any],
    *,
    plan: BgePocPlan,
) -> None:
    connection.execute(
        """
        INSERT INTO rag_corpus_generations (
          corpus_generation_id, corpus_hash, embedding_profile_id, vector_space,
          status, expected_chunk_count
        )
        VALUES (%s, %s, %s, %s, 'REGISTERED', %s)
        """,
        (
            plan.generation_id,
            plan.corpus_hash,
            plan.embedding_profile_id,
            plan.embedding_profile_id,
            len(plan.items),
        ),
    )
    connection.execute(
        """
        UPDATE rag_corpus_generations
        SET status = 'PLANNED'
        WHERE corpus_generation_id = %s AND status = 'REGISTERED'
        """,
        (plan.generation_id,),
    )
    connection.execute(
        """
        UPDATE rag_corpus_generations
        SET status = 'MATERIALIZING'
        WHERE corpus_generation_id = %s AND status = 'PLANNED'
        """,
        (plan.generation_id,),
    )
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO rag_generation_chunks (
              corpus_generation_id, chunk_revision_id, embedding_profile_id,
              embedding_input_hash, context_set_hash, ordinal
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    plan.generation_id,
                    item.chunk.chunk_revision_id,
                    plan.embedding_profile_id,
                    item.embedding_input.embedding_input_hash,
                    item.embedding_input.context_set_hash,
                    ordinal,
                )
                for ordinal, item in enumerate(plan.items, start=1)
            ],
        )


def _copy_staging_rows(
    connection: psycopg.Connection[Any],
    *,
    rows: tuple[BgeStagedEmbedding, ...],
) -> None:
    with connection.cursor() as cursor:
        with cursor.copy(
            """
            COPY rag_embedding_staging (
              generation_id, materialization_run_id, chunk_revision_id,
              embedding_profile_id, embedding_input_hash, context_set_hash,
              embedding, staging_row_hash
            ) FROM STDIN
            """
        ) as copy:
            for row in rows:
                copy.write_row(
                    (
                        row.generation_id,
                        row.materialization_run_id,
                        row.chunk_revision_id,
                        row.embedding_profile_id,
                        row.embedding_input_hash,
                        row.context_set_hash,
                        _vector_text(row.embedding),
                        row.staging_row_hash,
                    )
                )


def _attest_writer_connection(connection: psycopg.Connection[Any]) -> None:
    current_user_row = connection.execute("SELECT current_user").fetchone()
    if current_user_row is None or str(current_user_row[0]) != _EXPECTED_WRITER_ROLE:
        raise BgePocError("POC_DATABASE_ROLE")
    required = (
        ("rag_sources", "INSERT"),
        ("rag_source_revisions", "INSERT"),
        ("rag_ingest_runs", "INSERT"),
        ("rag_chunk_revisions", "INSERT"),
        ("rag_corpus_generations", "INSERT"),
        ("rag_generation_chunks", "INSERT"),
        ("rag_embedding_staging", "INSERT"),
    )
    for table, privilege in required:
        if not _has_table_privilege(connection, table, privilege):
            raise BgePocError("POC_DATABASE_PRIVILEGE")
    for table in ("rag_chunk_embeddings", "rag_embedding_policy_state"):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            if _has_table_privilege(connection, table, privilege):
                raise BgePocError("POC_DATABASE_FORBIDDEN_PRIVILEGE")
    function_allowed = connection.execute(
        """
        SELECT has_function_privilege(
          current_user,
          'public.finalize_rag_embedding_staging(text,text,text,integer,text)',
          'EXECUTE'
        )
        """
    ).fetchone()
    if function_allowed is None or function_allowed[0] is not True:
        raise BgePocError("POC_DATABASE_FINALIZER_PRIVILEGE")


def _has_table_privilege(
    connection: psycopg.Connection[Any],
    table: str,
    privilege: str,
) -> bool:
    row = connection.execute(
        "SELECT has_table_privilege(current_user, %s, %s)",
        (f"public.{table}", privilege),
    ).fetchone()
    return row is not None and row[0] is True


def _build_staging_row(
    *,
    plan: BgePocPlan,
    item: BgePocItem,
    embedding: NDArray[np.float32],
) -> BgeStagedEmbedding:
    canonical_vector = np.asarray(embedding, dtype="<f4")
    digest = hashlib.sha256()
    for value in (
        plan.generation_id,
        plan.materialization_run_id,
        item.chunk.chunk_revision_id,
        plan.embedding_profile_id,
        item.embedding_input.embedding_input_hash,
        item.embedding_input.context_set_hash or "",
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    digest.update(canonical_vector.tobytes(order="C"))
    return BgeStagedEmbedding(
        generation_id=plan.generation_id,
        materialization_run_id=plan.materialization_run_id,
        chunk_revision_id=item.chunk.chunk_revision_id,
        embedding_profile_id=plan.embedding_profile_id,
        embedding_input_hash=item.embedding_input.embedding_input_hash,
        context_set_hash=item.embedding_input.context_set_hash,
        embedding=cast(NDArray[np.float32], canonical_vector),
        staging_row_hash=digest.hexdigest(),
    )


def _vector_text(embedding: NDArray[np.float32]) -> str:
    return "[" + ",".join(format(float(value), ".9g") for value in embedding) + "]"


def _require_offline_target() -> None:
    target = os.environ.get(_TARGET_ENV, "").strip().lower()
    if target not in _ALLOWED_TARGETS:
        raise BgePocError(f"{_TARGET_ENV}_INVALID")


def _required_int(row: tuple[object, ...] | None) -> int:
    if row is None or len(row) != 1 or type(row[0]) is not int:
        raise BgePocError("POC_DATABASE_INTEGER_RECEIPT")
    return row[0]


def _canonical_json_hash(payload: object) -> str:
    serialized = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _hash_parts(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()
