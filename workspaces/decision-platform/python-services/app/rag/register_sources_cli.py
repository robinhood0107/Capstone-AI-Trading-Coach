from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Sequence

import psycopg

from app.rag.source_registry import RagSourceDefinition
from app.rag.source_registry import (
    RagSourceRegistryError,
    RagSourceRegistry,
    load_default_source_registry,
    load_source_registry,
)

_DATABASE_DSN_ENV = "RAG_SOURCE_WRITER_DATABASE_DSN"
_REGISTER_TARGET_ENV = "RAG_SOURCE_REGISTER_TARGET"
_ALLOWED_REGISTER_TARGETS = {"local", "offline", "test", "testcontainers"}
_EXPECTED_ROLE = "decision_rag_writer"
_ALLOWED_TABLES = ("rag_sources", "rag_source_revisions")
_FORBIDDEN_TABLES = (
    "rag_source_checks",
    "rag_chunks",
    "rag_answers",
    "rag_citations",
    "rag_answer_feedback",
    "users",
    "principles",
    "decisions",
    "flyway_schema_history",
)


def main(argv: Sequence[str] | None = None) -> int:
    """S4.1 source seed를 검증하고, 명시된 경우 전용 writer role로 DB에 등록한다.

    CLI는 임의 URL 인자를 받지 않고 승인된 manifest만 처리해 registration entrypoint가
    operator convenience로 SSRF fetcher가 되는 일을 막는다.
    """

    parser = argparse.ArgumentParser(description="Validate the S4.1 RAG source seed manifest.")
    parser.add_argument("--seed", type=Path, help="Optional approved seed YAML path.")
    parser.add_argument("--json", action="store_true", help="Emit deterministic JSON summary.")
    parser.add_argument(
        "--register-db",
        action="store_true",
        help=f"Register the validated seed using the {_DATABASE_DSN_ENV} env-only DSN.",
    )
    args = parser.parse_args(argv)
    try:
        registry = load_source_registry(args.seed) if args.seed else load_default_source_registry()
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
        except (RagSourceRegistryError, ValueError, psycopg.Error) as error:
            print(f"RAG_SOURCE_REGISTRY_DB_INVALID {error}")
            return 3
    summary = {
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
                f"insertedRevisions={registration['insertedRevisions']}"
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
            inserted_sources = _insert_sources(connection, registry)
            inserted_revisions = _insert_revisions(connection, registry)
            _verify_registered_sources(connection, registry)
    return {
        "insertedSources": inserted_sources,
        "insertedRevisions": inserted_revisions,
    }


def _require_register_target() -> None:
    target = os.environ.get(_REGISTER_TARGET_ENV, "").strip().lower()
    if target not in _ALLOWED_REGISTER_TARGETS:
        raise ValueError(f"{_REGISTER_TARGET_ENV} must be one of local/offline/test/testcontainers")


def _attest_rag_writer_connection(connection: psycopg.Connection[Any]) -> None:
    current_user = str(_required_scalar(connection.execute("select current_user").fetchone()))
    if current_user != _EXPECTED_ROLE:
        raise ValueError(f"RAG source registry DSN must use {_EXPECTED_ROLE}")
    for table in _ALLOWED_TABLES:
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


def _insert_sources(
    connection: psycopg.Connection[Any],
    registry: RagSourceRegistry,
) -> int:
    rows = [
        (
            source.source_id,
            source.title,
            source.source_type,
            source.tier,
            source.locator.canonical_url,
            source.attribution,
            source.access_level,
            registry.registry_version,
            source.owner,
            source.license_decision,
            source.external_processing_allowed,
            source.initial_processing,
            source.retention.mode,
            source.retention.days,
            source.retention.owner,
            source.locator.canonical_url,
            source.locator.allowed_origin,
            source.locator.allowed_path,
        )
        for source in registry.sources.values()
    ]
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO rag_sources (
              source_id, title, source_type, tier, url, license_note, access_level,
              ingest_status, registry_version, source_owner, license_decision,
              external_processing_allowed, initial_processing, retention_mode,
              retention_days, retention_owner, canonical_url, allowed_origin, allowed_path
            )
            VALUES (
              %s, %s, %s, %s, %s, %s, %s,
              'REGISTERED', %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s, %s
            )
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
              canonical_url, allowed_origin, allowed_path, locator_sha256, metadata_hash
            )
            VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            rows,
        )
        return cursor.rowcount


def _verify_registered_sources(
    connection: psycopg.Connection[Any],
    registry: RagSourceRegistry,
) -> None:
    expected = {
        source.source_id: {
            "access_level": source.access_level,
            "canonical_url": source.locator.canonical_url,
            "external_processing_allowed": source.external_processing_allowed,
            "initial_processing": source.initial_processing,
            "license_decision": source.license_decision,
            "registry_version": registry.registry_version,
            "retention_owner": source.retention.owner,
            "url": source.locator.canonical_url,
        }
        for source in registry.sources.values()
    }
    with connection.cursor() as cursor:
        rows = cursor.execute(
            """
            SELECT source_id, access_level, canonical_url, external_processing_allowed,
                   initial_processing, license_decision, registry_version, retention_owner, url
            FROM rag_sources
            ORDER BY source_id
            """
        ).fetchall()
        actual = {str(row[0]): _row_mapping(row) for row in rows}
        if actual != expected:
            raise RagSourceRegistryError("registered RAG source seed drifted")

        revision_count = int(
            _required_scalar(cursor.execute("SELECT count(*) FROM rag_source_revisions").fetchone())
        )
        if revision_count != len(registry.sources):
            raise RagSourceRegistryError("registered RAG source revision count drifted")


def _row_mapping(row: tuple[Any, ...]) -> dict[str, object]:
    return {
        "access_level": row[1],
        "canonical_url": row[2],
        "external_processing_allowed": row[3],
        "initial_processing": row[4],
        "license_decision": row[5],
        "registry_version": row[6],
        "retention_owner": row[7],
        "url": row[8],
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
        "canonicalUrl": source.locator.canonical_url,
        "externalProcessingAllowed": source.external_processing_allowed,
        "initialProcessing": source.initial_processing,
        "licenseDecision": source.license_decision,
        "registryVersion": registry.registry_version,
        "retentionMode": source.retention.mode,
        "retentionOwner": source.retention.owner,
        "sourceId": source.source_id,
        "sourceType": source.source_type,
        "tier": source.tier,
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


def _required_scalar(row: tuple[Any, ...] | None) -> Any:
    if row is None or len(row) != 1:
        raise ValueError("database privilege attestation query failed")
    return row[0]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
