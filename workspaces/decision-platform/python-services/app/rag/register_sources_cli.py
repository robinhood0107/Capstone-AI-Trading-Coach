from __future__ import annotations

import argparse
import hashlib
import json
import os
from typing import Any, Sequence

import psycopg

from app.rag.source_registry import RagSourceDefinition
from app.rag.source_registry import (
    RagSourceRegistryError,
    RagSourceRegistry,
    load_default_source_registry,
)

_DATABASE_DSN_ENV = "RAG_SOURCE_WRITER_DATABASE_DSN"
_REGISTER_TARGET_ENV = "RAG_SOURCE_REGISTER_TARGET"
_ALLOWED_REGISTER_TARGETS = {"local", "offline", "test", "testcontainers"}
_EXPECTED_ROLE = "decision_rag_writer"
_RETIRE_RELOCATION_FUNCTION = "public.retire_rag_source_for_relocation(text,text)"
_WRITER_OWNED_TABLES = (
    "rag_sources",
    "rag_source_revisions",
    "rag_source_checks",
    "rag_ingest_runs",
    "rag_chunk_revisions",
    "rag_corpus_generations",
    "rag_generation_chunks",
)
_FORBIDDEN_TABLES = (
    "rag_embedding_policy_state",
    "rag_embedding_policy_transitions",
    "rag_sources_v2_legacy",
    "rag_chunks_v2_legacy",
    "rag_answers_v2_legacy",
    "rag_citations_v2_legacy",
    "rag_answer_feedback_v2_legacy",
    "users",
    "principles",
    "orders",
    "decisions",
    "flyway_schema_history",
)


def main(argv: Sequence[str] | None = None) -> int:
    """S4.1 source seed를 검증하고, 명시된 경우 전용 writer role로 DB에 등록한다.

    CLI는 임의 URL 인자를 받지 않고 승인된 manifest만 처리해 registration entrypoint가
    operator convenience로 SSRF fetcher가 되는 일을 막는다.
    """

    parser = argparse.ArgumentParser(description="Validate the S4.1 RAG source seed manifest.")
    parser.add_argument("--json", action="store_true", help="Emit deterministic JSON summary.")
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--register-db",
        action="store_true",
        help=f"Register the validated seed using the {_DATABASE_DSN_ENV} env-only DSN.",
    )
    action.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the code-owned manifest without opening a database connection.",
    )
    args = parser.parse_args(argv)
    try:
        registry = load_default_source_registry()
    except RagSourceRegistryError as error:
        print(f"RAG_SOURCE_REGISTRY_INVALID {error}")
        return 2
    registration: dict[str, int] | None = None
    if args.register_db:
        database_dsn = os.environ.get(_DATABASE_DSN_ENV, "").strip()
        if not database_dsn:
            print(f"RAG_SOURCE_REGISTRY_DB_INVALID {_DATABASE_DSN_ENV} is required")
            return 3
        try:
            registration = register_source_registry(registry, database_dsn=database_dsn)
        except (RagSourceRegistryError, ValueError) as error:
            print(f"RAG_SOURCE_REGISTRY_DB_INVALID {error}")
            return 3
        except psycopg.Error:
            # libpq 오류에는 host/DSN context가 섞일 수 있어 public CLI에는 typed class만 남긴다.
            print("RAG_SOURCE_REGISTRY_DB_INVALID DATABASE_OPERATION_FAILED")
            return 3
    summary = {
        "manifestSha256": registry.seed_sha256,
        "registryVersion": registry.registry_version,
        "schemaVersion": registry.schema_version,
        "sourceCount": len(registry.sources),
        "sourceIds": list(registry.sources),
    }
    if registration is not None:
        summary.update(registration)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    else:
        if registration is None:
            print(f"RAG_SOURCE_REGISTRY_VERIFIED {summary['sourceCount']} sources")
        else:
            print(
                "RAG_SOURCE_REGISTRY_REGISTERED "
                f"{summary['sourceCount']} sources "
                f"insertedSources={registration['insertedSources']} "
                f"insertedRevisions={registration['insertedRevisions']} "
                f"retiredSources={registration['retiredSources']}"
            )
    return 0


def register_source_registry(
    registry: RagSourceRegistry,
    *,
    database_dsn: str,
) -> dict[str, int]:
    """전용 `decision_rag_writer` DSN으로 source/revision seed만 append하고 drift는 실패시킨다."""

    _require_register_target()
    with psycopg.connect(database_dsn, autocommit=False, connect_timeout=2) as connection:
        _attest_rag_writer_connection(connection)
        with connection.transaction():
            connection.execute("set local statement_timeout = '2s'")
            connection.execute("set local lock_timeout = '500ms'")
            connection.execute("set local idle_in_transaction_session_timeout = '5s'")
            _verify_existing_seed_subset(connection, registry)
            inserted_sources = _insert_sources(connection, registry)
            inserted_revisions = _insert_revisions(connection, registry)
            retired_sources = _retire_relocated_sources(connection, registry)
            _verify_registered_sources(connection, registry)
    return {
        "insertedSources": inserted_sources,
        "insertedRevisions": inserted_revisions,
        "retiredSources": retired_sources,
    }


def _require_register_target() -> None:
    target = os.environ.get(_REGISTER_TARGET_ENV, "").strip().lower()
    if target not in _ALLOWED_REGISTER_TARGETS:
        raise ValueError(f"{_REGISTER_TARGET_ENV} must be one of local/offline/test/testcontainers")


def _attest_rag_writer_connection(connection: psycopg.Connection[Any]) -> None:
    current_user = str(_required_scalar(connection.execute("select current_user").fetchone()))
    if current_user != _EXPECTED_ROLE:
        raise ValueError(f"RAG source registry DSN must use {_EXPECTED_ROLE}")
    for table in _WRITER_OWNED_TABLES:
        for privilege in ("SELECT", "INSERT"):
            if not _has_table_privilege(connection, table, privilege):
                raise ValueError("RAG source writer lacks required append privilege")
        for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
            if _has_table_privilege(connection, table, privilege):
                raise ValueError("RAG source writer has unexpected mutation privilege")
    for table in _FORBIDDEN_TABLES:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            if _has_table_privilege(connection, table, privilege):
                raise ValueError("RAG source writer can access a forbidden table")
    if bool(
        _required_scalar(
            connection.execute("select has_schema_privilege(current_user, 'public', 'CREATE')")
            .fetchone()
        )
    ):
        raise ValueError("RAG source writer must not create schema objects")
    if not _has_function_privilege(
        connection,
        _RETIRE_RELOCATION_FUNCTION,
        "EXECUTE",
    ):
        raise ValueError("RAG source writer lacks bounded relocation retirement privilege")


def _insert_sources(
    connection: psycopg.Connection[Any],
    registry: RagSourceRegistry,
) -> int:
    rows = [
        (
            source.source_id,
            source.source_type,
            source.institution,
            source.topic,
            source.owner,
        )
        for source in registry.sources.values()
    ]
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO rag_sources (
              source_id, source_type, institution, topic, owner_identity
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            rows,
        )
        return cursor.rowcount


def _insert_revisions(
    connection: psycopg.Connection[Any],
    registry: RagSourceRegistry,
) -> int:
    rows = [
        (
            _source_revision_id(registry, source),
            source.source_id,
            registry.registry_version,
            source.title,
            source.tier,
            source.access_level,
            source.license_decision,
            f"P0 reference metadata: {source.license_decision}",
            source.attribution,
            source.retention.mode,
            source.retention.days,
            source.retention.owner,
            source.external_processing_allowed,
            source.initial_processing,
            source.locator.canonical_url,
            source.locator.allowed_origin,
            source.locator.allowed_path,
            _sha256_text(source.locator.canonical_url),
            _metadata_hash(registry, source),
        )
        for source in registry.sources.values()
    ]
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO rag_source_revisions (
              source_revision_id, source_id, revision_seq, registry_version,
              title, tier, access_level, license_decision, license_note, attribution,
              retention_mode, retention_days, retention_owner, external_processing_allowed,
              initial_processing, canonical_url, allowed_origin, allowed_path,
              locator_sha256, metadata_hash
            )
            VALUES (
              %s, %s, 1, %s,
              %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s
            )
            ON CONFLICT DO NOTHING
            """,
            rows,
        )
        return cursor.rowcount


def _retire_relocated_sources(
    connection: psycopg.Connection[Any],
    registry: RagSourceRegistry,
) -> int:
    """sequence가 증가한 manifest row만 DB의 bounded retirement 함수로 원자 전이한다."""

    retired_sources = 0
    for source in registry.sources.values():
        if source.sequence == 1:
            continue
        previous_source_id = (
            f"{source.source_id.rsplit('_', maxsplit=1)[0]}_{source.sequence - 1:03d}"
        )
        retired = _required_scalar(
            connection.execute(
                "select public.retire_rag_source_for_relocation(%s, %s)",
                (previous_source_id, source.source_id),
            ).fetchone()
        )
        if type(retired) is not bool:
            raise ValueError("RAG source relocation retirement returned an invalid receipt")
        retired_sources += int(retired)
    return retired_sources


def _verify_registered_sources(
    connection: psycopg.Connection[Any],
    registry: RagSourceRegistry,
) -> None:
    expected_sources = {
        source.source_id: {
            "institution": source.institution,
            "owner_identity": source.owner,
            "retired_at": None,
            "source_type": source.source_type,
            "topic": source.topic,
        }
        for source in registry.sources.values()
    }
    expected_revisions = {
        source.source_id: _expected_revision_mapping(registry, source)
        for source in registry.sources.values()
    }
    with connection.cursor() as cursor:
        rows = cursor.execute(
            """
            SELECT source_id, institution, owner_identity, source_type, topic, retired_at
            FROM rag_sources
            WHERE source_id = ANY(%s)
            ORDER BY source_id
            """,
            (list(registry.sources),),
        ).fetchall()
        actual_sources = {str(row[0]): _source_row_mapping(row) for row in rows}
        if actual_sources != expected_sources:
            raise RagSourceRegistryError("registered RAG source seed drifted")

        revision_rows = cursor.execute(
            """
            SELECT source_id, source_revision_id, registry_version, title, tier, access_level,
                   license_decision, license_note, attribution, retention_mode, retention_days,
                   retention_owner, external_processing_allowed, initial_processing,
                   canonical_url, allowed_origin, allowed_path, locator_sha256, metadata_hash
            FROM rag_source_revisions
            WHERE source_id = ANY(%s) AND revision_seq = 1
            ORDER BY source_id
            """,
            (list(registry.sources),),
        ).fetchall()
        actual_revisions = {
            str(row[0]): _revision_row_mapping(row) for row in revision_rows
        }
        if actual_revisions != expected_revisions:
            raise RagSourceRegistryError("registered RAG source revision drifted")


def _verify_existing_seed_subset(
    connection: psycopg.Connection[Any],
    registry: RagSourceRegistry,
) -> None:
    """같은 source ID가 이미 있으면 insert 전에 canonical identity drift를 typed conflict로 막는다."""

    expected_sources = {
        source.source_id: {
            "institution": source.institution,
            "owner_identity": source.owner,
            "retired_at": None,
            "source_type": source.source_type,
            "topic": source.topic,
        }
        for source in registry.sources.values()
    }
    expected_revisions = {
        source.source_id: _expected_revision_mapping(registry, source)
        for source in registry.sources.values()
    }
    with connection.cursor() as cursor:
        source_rows = cursor.execute(
            """
            SELECT source_id, institution, owner_identity, source_type, topic, retired_at
            FROM rag_sources
            WHERE source_id = ANY(%s)
            """,
            (list(registry.sources),),
        ).fetchall()
        for row in source_rows:
            source_id = str(row[0])
            if _source_row_mapping(row) != expected_sources[source_id]:
                raise RagSourceRegistryError("RAG source ID is already bound to different metadata")

        revision_rows = cursor.execute(
            """
            SELECT source_id, source_revision_id, registry_version, title, tier, access_level,
                   license_decision, license_note, attribution, retention_mode, retention_days,
                   retention_owner, external_processing_allowed, initial_processing,
                   canonical_url, allowed_origin, allowed_path, locator_sha256, metadata_hash
            FROM rag_source_revisions
            WHERE source_id = ANY(%s) AND revision_seq = 1
            """,
            (list(registry.sources),),
        ).fetchall()
        for row in revision_rows:
            source_id = str(row[0])
            if _revision_row_mapping(row) != expected_revisions[source_id]:
                raise RagSourceRegistryError(
                    "RAG source ID is already bound to a different canonical revision"
                )


def _source_row_mapping(row: tuple[Any, ...]) -> dict[str, object]:
    return {
        "institution": row[1],
        "owner_identity": row[2],
        "source_type": row[3],
        "topic": row[4],
        "retired_at": row[5],
    }


def _expected_revision_mapping(
    registry: RagSourceRegistry,
    source: RagSourceDefinition,
) -> dict[str, object]:
    return {
        "access_level": source.access_level,
        "allowed_origin": source.locator.allowed_origin,
        "allowed_path": source.locator.allowed_path,
        "attribution": source.attribution,
        "canonical_url": source.locator.canonical_url,
        "external_processing_allowed": source.external_processing_allowed,
        "initial_processing": source.initial_processing,
        "license_decision": source.license_decision,
        "license_note": f"P0 reference metadata: {source.license_decision}",
        "locator_sha256": _sha256_text(source.locator.canonical_url),
        "metadata_hash": _metadata_hash(registry, source),
        "registry_version": registry.registry_version,
        "retention_days": source.retention.days,
        "retention_mode": source.retention.mode,
        "retention_owner": source.retention.owner,
        "source_revision_id": _source_revision_id(registry, source),
        "tier": source.tier,
        "title": source.title,
    }


def _revision_row_mapping(row: tuple[Any, ...]) -> dict[str, object]:
    return {
        "source_revision_id": row[1],
        "registry_version": row[2],
        "title": row[3],
        "tier": row[4],
        "access_level": row[5],
        "license_decision": row[6],
        "license_note": row[7],
        "attribution": row[8],
        "retention_mode": row[9],
        "retention_days": row[10],
        "retention_owner": row[11],
        "external_processing_allowed": row[12],
        "initial_processing": row[13],
        "canonical_url": row[14],
        "allowed_origin": row[15],
        "allowed_path": row[16],
        "locator_sha256": row[17],
        "metadata_hash": row[18],
    }


def _source_revision_id(
    registry: RagSourceRegistry,
    source: RagSourceDefinition,
) -> str:
    material = "\x00".join(
        (
            "s4-rag-source-revision-v1",
            registry.registry_version,
            source.source_id,
            source.locator.canonical_url,
        )
    )
    return "src_rev_" + _sha256_text(material)[:32]


def _metadata_hash(
    registry: RagSourceRegistry,
    source: RagSourceDefinition,
) -> str:
    payload = {
        "accessLevel": source.access_level,
        "attribution": source.attribution,
        "canonicalUrl": source.locator.canonical_url,
        "externalProcessingAllowed": source.external_processing_allowed,
        "initialProcessing": source.initial_processing,
        "licenseDecision": source.license_decision,
        "licenseNote": f"P0 reference metadata: {source.license_decision}",
        "registryVersion": registry.registry_version,
        "retentionDays": source.retention.days,
        "retentionMode": source.retention.mode,
        "retentionOwner": source.retention.owner,
        "sourceId": source.source_id,
        "sourceType": source.source_type,
        "tier": source.tier,
        "title": source.title,
    }
    return _sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _has_table_privilege(
    connection: psycopg.Connection[Any],
    table: str,
    privilege: str,
) -> bool:
    return bool(
        _required_scalar(
            connection.execute(
                "select has_table_privilege(current_user, %s, %s)",
                (table, privilege),
            ).fetchone()
        )
    )


def _has_function_privilege(
    connection: psycopg.Connection[Any],
    signature: str,
    privilege: str,
) -> bool:
    return bool(
        _required_scalar(
            connection.execute(
                "select has_function_privilege(current_user, %s, %s)",
                (signature, privilege),
            ).fetchone()
        )
    )


def _required_scalar(row: tuple[Any, ...] | None) -> Any:
    if row is None or len(row) != 1:
        raise ValueError("database privilege attestation query failed")
    return row[0]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
