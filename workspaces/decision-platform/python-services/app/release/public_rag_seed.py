from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import stat
from collections.abc import Buffer, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

FORMAT_VERSION = 1
SOURCE_FLYWAY_SCHEMA_VERSION = "73"
TARGET_FLYWAY_SCHEMA_VERSION = "87"
# The sealed public Seed remains byte-bound to V87. Later additive migrations may
# explicitly declare compatibility without rewriting that historical manifest.
FORWARD_COMPATIBLE_TARGET_SCHEMA_VERSIONS = frozenset(
    {
        "88",
        "89",
        "90",
        "91",
        "92",
        "93",
        "94",
        "95",
        "96",
        "97",
        "98",
        "99",
        "100",
        "101",
        "102",
        "103",
        "104",
        "105",
        "106",
        "107",
        "108",
    }
)
EMBEDDING_DIMENSION = 1024
EXPECTED_SOURCE_COUNT = 142
EXPECTED_CHUNK_COUNT = 7_871
MAX_PART_BYTES = 32 * 1024 * 1024
PROFILE_ID = "voyage_context_4_1024_v1"
# 공개 코퍼스를 만든 Voyage 문서 배치 계획의 출처다. V98이 넣는 값과 같아야 한다.
VOYAGE_DOCUMENT_BATCH_PLAN_SHA256 = (
    "a5ec40010296f0f2a8935bf283e54296972db85963f1db89c7f7d83e5fb5d66c"
)
VOYAGE_OFFICIAL_TOKENIZER_SHA256 = (
    "c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539"
)
EXPECTED_TOKEN_COUNT = 3_243_555
EXPECTED_BATCH_COUNT = 63
MANIFEST_NAME = "public-rag-seed.v1.manifest.json"
PART_PREFIX = "public-rag-seed.v1.jsonl.gz.part-"


class PublicRagSeedError(RuntimeError):
    """Public Seed export/import가 fail-closed한 typed boundary다."""


@dataclass(frozen=True)
class TableSpec:
    name: str
    select_sql: str
    expected_count: int


@dataclass(frozen=True)
class PartReceipt:
    file: str
    ordinal: int
    size_bytes: int
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "file": self.file,
            "ordinal": self.ordinal,
            "sha256": self.sha256,
            "sizeBytes": self.size_bytes,
        }


_POINTER_CTE = """
WITH seed_pointer AS (
  SELECT exact30_generation_id, oa112_generation_id, embedding_profile_id
  FROM public.rag_v2_immutable_public_bundle_pointers
  WHERE state_id = 'default' AND state = 'ACTIVE'
), seed_generations AS (
  SELECT exact30_generation_id AS component_generation_id FROM seed_pointer
  UNION ALL
  SELECT oa112_generation_id FROM seed_pointer
), seed_memberships AS (
  SELECT membership.*
  FROM public.rag_v2_immutable_generation_memberships AS membership
  WHERE membership.component_generation_id IN (
    SELECT component_generation_id FROM seed_generations
  )
)
"""


TABLE_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        "rag_v2_immutable_source_revisions",
        _POINTER_CTE
        + """
SELECT source.*
FROM public.rag_v2_immutable_source_revisions AS source
WHERE source.owner_user_id IS NULL
  AND source.source_scope IN ('EXACT30', 'OA112')
  AND EXISTS (
    SELECT 1 FROM seed_memberships AS membership
    WHERE membership.source_revision_id = source.source_revision_id
      AND membership.component_scope = source.source_scope
  )
ORDER BY source.source_revision_id
""",
        EXPECTED_SOURCE_COUNT,
    ),
    TableSpec(
        "rag_v2_immutable_oa_source_cards",
        _POINTER_CTE
        + """
SELECT card.*
FROM public.rag_v2_immutable_oa_source_cards AS card
WHERE EXISTS (
  SELECT 1 FROM seed_memberships AS membership
  WHERE membership.source_revision_id = card.source_revision_id
    AND membership.component_scope = 'OA112'
)
ORDER BY card.source_revision_id
""",
        112,
    ),
    TableSpec(
        "rag_v2_immutable_chunks",
        _POINTER_CTE
        + """
SELECT chunk.*
FROM public.rag_v2_immutable_chunks AS chunk
WHERE chunk.owner_user_id IS NULL
  AND chunk.source_scope IN ('EXACT30', 'OA112')
  AND EXISTS (
    SELECT 1 FROM seed_memberships AS membership
    WHERE membership.chunk_id = chunk.chunk_id
      AND membership.source_revision_id = chunk.source_revision_id
      AND membership.component_scope = chunk.source_scope
  )
ORDER BY chunk.chunk_id
""",
        EXPECTED_CHUNK_COUNT,
    ),
    TableSpec(
        "rag_v2_immutable_component_generations",
        _POINTER_CTE
        + """
SELECT generation.*
FROM public.rag_v2_immutable_component_generations AS generation
WHERE generation.owner_user_id IS NULL
  AND generation.component_generation_id IN (
    SELECT component_generation_id FROM seed_generations
  )
ORDER BY generation.component_scope, generation.component_generation_id
""",
        2,
    ),
    TableSpec(
        "rag_v2_immutable_generation_memberships",
        _POINTER_CTE
        + """
SELECT membership.*
FROM seed_memberships AS membership
WHERE membership.owner_user_id IS NULL
  AND membership.component_scope IN ('EXACT30', 'OA112')
ORDER BY membership.component_generation_id, membership.ordinal, membership.chunk_id
""",
        EXPECTED_CHUNK_COUNT,
    ),
    TableSpec(
        "rag_v2_immutable_generation_embeddings",
        _POINTER_CTE
        + """
SELECT embedding.*
FROM public.rag_v2_immutable_generation_embeddings AS embedding
WHERE embedding.owner_user_id IS NULL
  AND embedding.component_scope IN ('EXACT30', 'OA112')
  AND embedding.component_generation_id IN (
    SELECT component_generation_id FROM seed_generations
  )
ORDER BY embedding.component_generation_id, embedding.chunk_id
""",
        EXPECTED_CHUNK_COUNT,
    ),
    TableSpec(
        "rag_v2_immutable_public_voyage_component_manifests",
        _POINTER_CTE
        + """
SELECT manifest.*
FROM public.rag_v2_immutable_public_voyage_component_manifests AS manifest
WHERE manifest.component_generation_id IN (
  SELECT component_generation_id FROM seed_generations
)
ORDER BY manifest.component_generation_id
""",
        2,
    ),
    TableSpec(
        "rag_v2_immutable_public_voyage_component_evaluations",
        _POINTER_CTE
        + """
SELECT evaluation.*
FROM public.rag_v2_immutable_public_voyage_component_evaluations AS evaluation
WHERE evaluation.component_generation_id IN (
  SELECT component_generation_id FROM seed_generations
)
ORDER BY evaluation.component_generation_id
""",
        2,
    ),
    TableSpec(
        "rag_v2_immutable_public_bundle_pointers",
        """
SELECT pointer.*
FROM public.rag_v2_immutable_public_bundle_pointers AS pointer
WHERE pointer.state_id = 'default' AND pointer.state = 'ACTIVE'
ORDER BY pointer.state_id
""",
        1,
    ),
)

_SPEC_BY_NAME = {spec.name: spec for spec in TABLE_SPECS}


class _SplitHashWriter(io.RawIOBase):
    def __init__(self, output_dir: Path, *, max_part_bytes: int) -> None:
        super().__init__()
        if max_part_bytes < 1 or max_part_bytes > MAX_PART_BYTES:
            raise PublicRagSeedError("PUBLIC_RAG_SEED_PART_BOUND")
        self._output_dir = output_dir
        self._max_part_bytes = max_part_bytes
        self._archive_hash = hashlib.sha256()
        self._archive_size = 0
        self._parts: list[PartReceipt] = []
        self._part_file: BinaryIO | None = None
        self._part_path: Path | None = None
        self._part_hash = hashlib.sha256()
        self._part_size = 0

    @property
    def archive_sha256(self) -> str:
        return self._archive_hash.hexdigest()

    @property
    def archive_size(self) -> int:
        return self._archive_size

    @property
    def parts(self) -> tuple[PartReceipt, ...]:
        return tuple(self._parts)

    def writable(self) -> bool:
        return True

    def write(self, payload: Buffer) -> int:
        data = bytes(payload)
        offset = 0
        while offset < len(data):
            if self._part_file is None:
                self._open_part()
            remaining = self._max_part_bytes - self._part_size
            chunk = data[offset : offset + remaining]
            assert self._part_file is not None
            self._part_file.write(chunk)
            self._part_hash.update(chunk)
            self._archive_hash.update(chunk)
            self._part_size += len(chunk)
            self._archive_size += len(chunk)
            offset += len(chunk)
            if self._part_size == self._max_part_bytes:
                self._close_part()
        return len(data)

    def close(self) -> None:
        if not self.closed:
            self._close_part()
        super().close()

    def _open_part(self) -> None:
        ordinal = len(self._parts) + 1
        self._part_path = self._output_dir / f"{PART_PREFIX}{ordinal:04d}"
        self._part_file = self._part_path.open("xb")
        self._part_hash = hashlib.sha256()
        self._part_size = 0

    def _close_part(self) -> None:
        if self._part_file is None or self._part_path is None:
            return
        self._part_file.flush()
        os.fsync(self._part_file.fileno())
        self._part_file.close()
        self._parts.append(
            PartReceipt(
                file=self._part_path.name,
                ordinal=len(self._parts) + 1,
                size_bytes=self._part_size,
                sha256=self._part_hash.hexdigest(),
            )
        )
        self._part_file = None
        self._part_path = None


class _PartReader(io.RawIOBase):
    def __init__(self, paths: Sequence[Path]) -> None:
        super().__init__()
        self._paths = tuple(paths)
        self._index = 0
        self._current: BinaryIO | None = None
        self._archive_hash = hashlib.sha256()
        self._archive_size = 0

    @property
    def archive_sha256(self) -> str:
        return self._archive_hash.hexdigest()

    @property
    def archive_size(self) -> int:
        return self._archive_size

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Buffer) -> int:
        view = memoryview(buffer)
        while True:
            if self._current is None:
                if self._index >= len(self._paths):
                    return 0
                path = self._paths[self._index]
                try:
                    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                except OSError as error:
                    raise PublicRagSeedError("PUBLIC_RAG_SEED_PART_OPEN") from error
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_size < 1
                    or metadata.st_size > MAX_PART_BYTES
                ):
                    os.close(descriptor)
                    raise PublicRagSeedError("PUBLIC_RAG_SEED_PART_BOUNDARY")
                self._current = os.fdopen(descriptor, "rb")
                self._index += 1
            data = self._current.read(len(view))
            if data:
                view[: len(data)] = data
                self._archive_hash.update(data)
                self._archive_size += len(data)
                return len(data)
            self._current.close()
            self._current = None

    def close(self) -> None:
        if self._current is not None:
            self._current.close()
            self._current = None
        super().close()


def export_public_seed(
    *,
    database_dsn: str,
    output_dir: Path,
    max_part_bytes: int = MAX_PART_BYTES,
) -> Mapping[str, object]:
    """현재 ACTIVE public graph만 read-only deterministic Seed로 export한다."""

    _validate_dsn(database_dsn)
    _prepare_empty_output_dir(output_dir)
    try:
        with psycopg.connect(database_dsn) as connection:
            with connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute("SET LOCAL statement_timeout = '10min'")
                _require_schema_version(connection, expected=SOURCE_FLYWAY_SCHEMA_VERSION)
                pointer = _read_active_pointer(connection)
                columns = {
                    spec.name: _non_generated_columns(connection, spec.name) for spec in TABLE_SPECS
                }
                manifest = _write_archive(
                    connection=connection,
                    output_dir=output_dir,
                    pointer=pointer,
                    columns=columns,
                    max_part_bytes=max_part_bytes,
                )
    except PublicRagSeedError:
        _remove_generated_files(output_dir)
        raise
    except (OSError, psycopg.Error) as error:
        _remove_generated_files(output_dir)
        raise PublicRagSeedError("PUBLIC_RAG_SEED_EXPORT_FAILED") from error
    _write_manifest(output_dir / MANIFEST_NAME, manifest)
    return manifest


def verify_seed_parts(*, manifest_path: Path) -> Mapping[str, object]:
    manifest = _load_manifest(manifest_path)
    _verified_part_paths(manifest_path=manifest_path, manifest=manifest)
    return manifest


def import_public_seed(*, database_dsn: str, manifest_path: Path) -> str:
    """fresh migrated DB에 Seed를 단일 transaction으로 stage하고 pointer를 마지막에 활성화한다."""

    _validate_dsn(database_dsn)
    manifest = _load_manifest(manifest_path)
    part_paths = _verified_part_paths(manifest_path=manifest_path, manifest=manifest)
    try:
        with psycopg.connect(database_dsn) as connection:
            with connection.transaction():
                connection.execute("SET LOCAL statement_timeout = '10min'")
                connection.execute("SELECT pg_advisory_xact_lock(70710931058731)")
                _require_schema_version(
                    connection,
                    expected=TARGET_FLYWAY_SCHEMA_VERSION,
                    compatible=FORWARD_COMPATIBLE_TARGET_SCHEMA_VERSIONS,
                )
                _set_force_row_level_security(connection, enabled=False)
                _verify_target_columns(connection, manifest)
                if _is_matching_active_seed(connection, manifest):
                    _validate_database_invariants(connection, manifest)
                    _ensure_voyage_document_batch_plan(connection)
                    _set_force_row_level_security(connection, enabled=True)
                    _require_force_row_level_security(connection)
                    return "NOOP_MATCHING_ACTIVE_SEED"
                _require_empty_target(connection)
                _restore_archive(
                    connection=connection,
                    manifest=manifest,
                    part_paths=part_paths,
                )
                _validate_database_invariants(connection, manifest)
                _ensure_voyage_document_batch_plan(connection)
                _set_force_row_level_security(connection, enabled=True)
                _require_force_row_level_security(connection)
    except PublicRagSeedError:
        raise
    except (OSError, psycopg.Error, UnicodeDecodeError, gzip.BadGzipFile) as error:
        raise PublicRagSeedError("PUBLIC_RAG_SEED_IMPORT_FAILED") from error
    return "IMPORTED_FULL_READY"


def _write_archive(
    *,
    connection: psycopg.Connection[Any],
    output_dir: Path,
    pointer: Mapping[str, Any],
    columns: Mapping[str, Sequence[str]],
    max_part_bytes: int,
) -> dict[str, object]:
    split_writer = _SplitHashWriter(output_dir, max_part_bytes=max_part_bytes)
    content_hash = hashlib.sha256()
    table_counts: dict[str, int] = {}
    header = {
        "embeddingDimension": EMBEDDING_DIMENSION,
        "expectedChunkCount": EXPECTED_CHUNK_COUNT,
        "expectedSourceCount": EXPECTED_SOURCE_COUNT,
        "sourceFlywaySchemaVersion": SOURCE_FLYWAY_SCHEMA_VERSION,
        "targetFlywaySchemaVersion": TARGET_FLYWAY_SCHEMA_VERSION,
        "formatVersion": FORMAT_VERSION,
        "pointer": _pointer_identity(pointer),
        "profileId": PROFILE_ID,
        "recordType": "header",
        "tableColumns": {name: list(value) for name, value in columns.items()},
        "tableOrder": [spec.name for spec in TABLE_SPECS],
    }
    with gzip.GzipFile(
        filename="",
        mode="wb",
        fileobj=split_writer,
        compresslevel=9,
        mtime=0,
    ) as archive:
        _write_json_line(archive, header, content_hash=content_hash)
        for spec in TABLE_SPECS:
            count = 0
            generated = _generated_columns(connection, spec.name)
            generated_expression = ""
            if generated:
                generated_expression = (
                    " - ARRAY["
                    + ",".join("'" + value.replace("'", "''") + "'" for value in generated)
                    + "]::text[]"
                )
            statement = (
                "SELECT jsonb_build_object("
                "'recordType', 'row', 'table', %s::text, "
                f"'value', to_jsonb(seed_row){generated_expression})::text "
                f"FROM ({spec.select_sql}) AS seed_row"
            )
            with connection.cursor(name=f"p1_seed_{count}_{spec.name[:32]}") as cursor:
                cursor.itersize = 128
                cursor.execute(statement, (spec.name,))
                for row in cursor:
                    encoded = str(row[0]).encode("utf-8") + b"\n"
                    archive.write(encoded)
                    content_hash.update(encoded)
                    count += 1
            if count != spec.expected_count:
                raise PublicRagSeedError(f"PUBLIC_RAG_SEED_COUNT_{spec.name}")
            table_counts[spec.name] = count
        footer = {
            "contentSha256": content_hash.hexdigest(),
            "recordType": "footer",
            "tableCounts": table_counts,
        }
        _write_json_line(archive, footer, content_hash=None)
    split_writer.close()
    if not split_writer.parts:
        raise PublicRagSeedError("PUBLIC_RAG_SEED_EMPTY_ARCHIVE")
    return {
        "archiveSha256": split_writer.archive_sha256,
        "archiveSizeBytes": split_writer.archive_size,
        "contractId": "p1-public-rag-seed.v1",
        "embeddingDimension": EMBEDDING_DIMENSION,
        "expectedChunkCount": EXPECTED_CHUNK_COUNT,
        "expectedSourceCount": EXPECTED_SOURCE_COUNT,
        "sourceFlywaySchemaVersion": SOURCE_FLYWAY_SCHEMA_VERSION,
        "targetFlywaySchemaVersion": TARGET_FLYWAY_SCHEMA_VERSION,
        "formatVersion": FORMAT_VERSION,
        "maxPartBytes": max_part_bytes,
        "parts": [part.as_dict() for part in split_writer.parts],
        "pointer": _pointer_identity(pointer),
        "profileId": PROFILE_ID,
        "tableColumns": {name: list(value) for name, value in columns.items()},
        "tableCounts": table_counts,
        "tableOrder": [spec.name for spec in TABLE_SPECS],
    }


def _restore_archive(
    *,
    connection: psycopg.Connection[Any],
    manifest: Mapping[str, Any],
    part_paths: Sequence[Path],
) -> None:
    part_reader = _PartReader(part_paths)
    buffered = io.BufferedReader(part_reader, buffer_size=1024 * 1024)
    content_hash = hashlib.sha256()
    observed_counts = {spec.name: 0 for spec in TABLE_SPECS}
    current_table_index = 0
    batches: dict[str, list[Mapping[str, Any]]] = {spec.name: [] for spec in TABLE_SPECS}
    try:
        with gzip.GzipFile(fileobj=buffered, mode="rb") as archive:
            first_line = archive.readline()
            header = _decode_record(first_line)
            _validate_header(header, manifest)
            content_hash.update(first_line)
            footer: Mapping[str, Any] | None = None
            for line in archive:
                record = _decode_record(line)
                record_type = record.get("recordType")
                if record_type == "footer":
                    footer = record
                    break
                if record_type != "row":
                    raise PublicRagSeedError("PUBLIC_RAG_SEED_RECORD_TYPE")
                table_name = record.get("table")
                if not isinstance(table_name, str) or table_name not in _SPEC_BY_NAME:
                    raise PublicRagSeedError("PUBLIC_RAG_SEED_TABLE")
                expected_index = list(_SPEC_BY_NAME).index(table_name)
                if expected_index < current_table_index or expected_index > current_table_index + 1:
                    raise PublicRagSeedError("PUBLIC_RAG_SEED_TABLE_ORDER")
                if expected_index == current_table_index + 1:
                    previous = list(_SPEC_BY_NAME)[current_table_index]
                    _flush_batch(connection, previous, batches[previous], manifest)
                    batches[previous].clear()
                    current_table_index = expected_index
                value = record.get("value")
                if not isinstance(value, Mapping):
                    raise PublicRagSeedError("PUBLIC_RAG_SEED_ROW")
                batches[table_name].append(value)
                observed_counts[table_name] += 1
                content_hash.update(line)
                if len(batches[table_name]) >= 128:
                    _flush_batch(connection, table_name, batches[table_name], manifest)
                    batches[table_name].clear()
            if footer is None or archive.read(1) != b"":
                raise PublicRagSeedError("PUBLIC_RAG_SEED_FOOTER")
        while buffered.read(1024 * 1024):
            pass
    finally:
        buffered.close()
    if part_reader.archive_size != manifest.get(
        "archiveSizeBytes"
    ) or part_reader.archive_sha256 != manifest.get("archiveSha256"):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_CONSUMED_ARCHIVE_HASH")
    for spec in TABLE_SPECS:
        _flush_batch(connection, spec.name, batches[spec.name], manifest)
    expected_counts = manifest.get("tableCounts")
    if (
        footer.get("contentSha256") != content_hash.hexdigest()
        or footer.get("tableCounts") != expected_counts
        or observed_counts != expected_counts
    ):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_CONTENT_HASH")


def _flush_batch(
    connection: psycopg.Connection[Any],
    table_name: str,
    rows: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
) -> None:
    if not rows:
        return
    columns_by_table = manifest.get("tableColumns")
    if not isinstance(columns_by_table, Mapping):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_COLUMNS")
    raw_columns = columns_by_table.get(table_name)
    if not isinstance(raw_columns, list) or not all(
        isinstance(value, str) for value in raw_columns
    ):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_COLUMNS")
    columns = tuple(raw_columns)
    payload = json.dumps(
        rows[0] if table_name == "rag_v2_immutable_public_bundle_pointers" else list(rows),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    table_identifier = sql.Identifier("public", table_name)
    if table_name == "rag_v2_immutable_public_bundle_pointers":
        if len(rows) != 1:
            raise PublicRagSeedError("PUBLIC_RAG_SEED_POINTER_COUNT")
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = restored.{}").format(sql.Identifier(column), sql.Identifier(column))
            for column in columns
            if column != "state_id"
        )
        selected = sql.SQL(", ").join(sql.Identifier(column) for column in columns)
        statement = sql.SQL(
            """
            UPDATE {table} AS target
            SET {assignments}
            FROM (
              SELECT {selected}
              FROM jsonb_populate_record(NULL::{table}, %s::jsonb)
            ) AS restored
            WHERE target.state_id = 'default'
              AND target.state = 'NOT_MATERIALIZED'
              AND target.exact30_generation_id IS NULL
              AND target.oa112_generation_id IS NULL
            """
        ).format(table=table_identifier, assignments=assignments, selected=selected)
    else:
        selected = sql.SQL(", ").join(sql.Identifier(column) for column in columns)
        statement = sql.SQL(
            """
            INSERT INTO {table} ({selected})
            SELECT {selected}
            FROM jsonb_populate_recordset(NULL::{table}, %s::jsonb)
            """
        ).format(table=table_identifier, selected=selected)
    cursor = connection.execute(statement, (payload,))
    if cursor.rowcount != len(rows):
        raise PublicRagSeedError(f"PUBLIC_RAG_SEED_RESTORE_{table_name}")


def _ensure_voyage_document_batch_plan(connection: psycopg.Connection[Any]) -> None:
    """코퍼스를 만든 Voyage 문서 배치의 출처 기록을 함께 남긴다.

    이 행이 없으면 `reserve_s4_9_runtime_voyage_query_usage`가 활성 계획에서 공식 tokenizer
    해시를 읽지 못해 모든 질의 예약이 55000으로 닫힌다. 코퍼스는 적재됐는데 질의 경로만
    구조적으로 막히고, 호출자에게는 엉뚱한 이유로 보인다.

    V98이 같은 행을 넣지만 그것만으로는 부족하다. 새 배포에서는 migration이 seed보다 먼저
    돌아 코퍼스가 아직 없고, RAG 표는 전부 FORCE RLS라 migration 안에서 확인할 방법도 없다.
    그래서 코퍼스를 실제로 들여놓는 이 자리에서 한 번 더 보장한다. 값은 V98과 같고 근거는
    `batch-plans/public-voyage-batches.v1.json`이다. 이미 있으면 아무것도 하지 않는다.
    """

    connection.execute(
        """
        INSERT INTO public.rag_v2_immutable_voyage_document_batch_plans (
          batch_plan_sha256, embedding_profile_id, official_tokenizer_sha256,
          expected_source_count, expected_chunk_count, expected_token_count,
          expected_batch_count, owner_scope_sha256, owner_private_ordered_group_count,
          state, created_at, completed_at
        )
        SELECT
          %s, %s, %s, %s, %s, %s, %s, NULL, 0, 'COMPLETE',
          min(generation.created_at), max(generation.activated_at)
        FROM public.rag_v2_immutable_component_generations AS generation
        WHERE generation.owner_partition_key = '__PUBLIC__'
          AND generation.embedding_profile_id = %s
          AND generation.state = 'ACTIVE'
          AND generation.evaluation_status = 'PASSED'
          AND generation.activated_at IS NOT NULL
        HAVING count(*) = 2
           AND sum(generation.actual_source_count) = %s
           AND sum(generation.actual_chunk_count) = %s
        ON CONFLICT (batch_plan_sha256) DO NOTHING
        """,
        (
            VOYAGE_DOCUMENT_BATCH_PLAN_SHA256,
            PROFILE_ID,
            VOYAGE_OFFICIAL_TOKENIZER_SHA256,
            EXPECTED_SOURCE_COUNT,
            EXPECTED_CHUNK_COUNT,
            EXPECTED_TOKEN_COUNT,
            EXPECTED_BATCH_COUNT,
            PROFILE_ID,
            EXPECTED_SOURCE_COUNT,
            EXPECTED_CHUNK_COUNT,
        ),
    )


def _validate_database_invariants(
    connection: psycopg.Connection[Any], manifest: Mapping[str, Any]
) -> None:
    pointer = _read_active_pointer(connection)
    if _pointer_identity(pointer) != manifest.get("pointer"):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_POINTER_DRIFT")
    counts = _database_counts(connection)
    if counts != manifest.get("tableCounts"):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_DATABASE_COUNTS")
    row = connection.execute(
        """
        WITH p AS (
          SELECT exact30_generation_id AS generation_id
          FROM public.rag_v2_immutable_public_bundle_pointers WHERE state_id='default'
          UNION ALL
          SELECT oa112_generation_id
          FROM public.rag_v2_immutable_public_bundle_pointers WHERE state_id='default'
        )
        SELECT
          count(*) FILTER (WHERE source.owner_user_id IS NOT NULL),
          count(*) FILTER (WHERE source.source_scope NOT IN ('EXACT30', 'OA112')),
          (SELECT count(*) FROM public.rag_v2_immutable_generation_embeddings AS embedding
           WHERE embedding.component_generation_id IN (SELECT generation_id FROM p)
             AND vector_dims(embedding.embedding) <> %s),
          (SELECT count(*) FROM public.rag_v2_immutable_component_generations AS generation
           WHERE generation.component_generation_id IN (SELECT generation_id FROM p)
             AND (generation.state <> 'ACTIVE' OR generation.evaluation_status <> 'PASSED'
                  OR generation.embedding_profile_id <> %s))
        FROM public.rag_v2_immutable_source_revisions AS source
        WHERE EXISTS (
          SELECT 1 FROM public.rag_v2_immutable_generation_memberships AS membership
          WHERE membership.component_generation_id IN (SELECT generation_id FROM p)
            AND membership.source_revision_id = source.source_revision_id
        )
        """,
        (EMBEDDING_DIMENSION, PROFILE_ID),
    ).fetchone()
    if row is None or tuple(row) != (0, 0, 0, 0):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_DATABASE_INVARIANT")


def _database_counts(connection: psycopg.Connection[Any]) -> dict[str, int]:
    pointer = _read_active_pointer(connection)
    generation_ids = (
        str(pointer["exact30_generation_id"]),
        str(pointer["oa112_generation_id"]),
    )
    counts: dict[str, int] = {}
    for spec in TABLE_SPECS:
        statement = f"SELECT count(*) FROM ({spec.select_sql}) AS seed_rows"
        value = connection.execute(statement).fetchone()
        if value is None or type(value[0]) is not int:
            raise PublicRagSeedError("PUBLIC_RAG_SEED_DATABASE_COUNTS")
        counts[spec.name] = int(value[0])
    if not all(generation_ids):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_POINTER")
    return counts


def _require_empty_target(connection: psycopg.Connection[Any]) -> None:
    for spec in TABLE_SPECS:
        if spec.name == "rag_v2_immutable_public_bundle_pointers":
            row = connection.execute(
                """
                SELECT
                  count(*),
                  count(*) FILTER (
                    WHERE state_id = 'default' AND state = 'NOT_MATERIALIZED'
                      AND exact30_generation_id IS NULL AND oa112_generation_id IS NULL
                  )
                FROM public.rag_v2_immutable_public_bundle_pointers
                """
            ).fetchone()
            if row == (0, 0):
                # The squashed B86 baseline intentionally contains schema plus
                # allowlisted static rows, but not the V25 singleton bootstrap
                # row. Recreate that empty state inside this already locked,
                # RLS-disabled import transaction before the archive updates it.
                connection.execute(
                    """
                    INSERT INTO public.rag_v2_immutable_public_bundle_pointers (
                      state_id, state, exact30_generation_id, oa112_generation_id,
                      embedding_profile_id, pointer_version
                    ) VALUES ('default', 'NOT_MATERIALIZED', NULL, NULL, NULL, 1)
                    """
                )
            elif row != (1, 1):
                raise PublicRagSeedError("PUBLIC_RAG_SEED_TARGET_NOT_EMPTY_" + spec.name)
            continue
        row = connection.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("public", spec.name))
        ).fetchone()
        if row != (0,):
            raise PublicRagSeedError("PUBLIC_RAG_SEED_TARGET_NOT_EMPTY_" + spec.name)


def _set_force_row_level_security(connection: psycopg.Connection[Any], *, enabled: bool) -> None:
    """install transaction 안에서만 owner가 RLS를 우회하고 commit 전에 FORCE를 복구한다."""

    mode = sql.SQL("FORCE") if enabled else sql.SQL("NO FORCE")
    for spec in TABLE_SPECS:
        connection.execute(
            sql.SQL("ALTER TABLE {} {} ROW LEVEL SECURITY").format(
                sql.Identifier("public", spec.name), mode
            )
        )


def _require_force_row_level_security(connection: psycopg.Connection[Any]) -> None:
    table_names = [f"public.{spec.name}" for spec in TABLE_SPECS]
    row = connection.execute(
        """
        SELECT count(*) = %s AND bool_and(class.relforcerowsecurity)
        FROM pg_catalog.pg_class AS class
        WHERE class.oid = ANY(%s::regclass[])
        """,
        (len(table_names), table_names),
    ).fetchone()
    if row != (True,):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_FORCE_RLS")


def _is_matching_active_seed(
    connection: psycopg.Connection[Any], manifest: Mapping[str, Any]
) -> bool:
    row = connection.execute(
        """
        SELECT state, exact30_generation_id, oa112_generation_id, embedding_profile_id,
               pointer_version
        FROM public.rag_v2_immutable_public_bundle_pointers
        WHERE state_id = 'default'
        """
    ).fetchone()
    if row is None or row[0] != "ACTIVE":
        return False
    return {
        "embeddingProfileId": row[3],
        "exact30GenerationId": row[1],
        "oa112GenerationId": row[2],
        "pointerVersion": row[4],
        "state": row[0],
        "stateId": "default",
    } == manifest.get("pointer")


def _read_active_pointer(connection: psycopg.Connection[Any]) -> Mapping[str, Any]:
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            """
            SELECT state_id, state, exact30_generation_id, oa112_generation_id,
                   embedding_profile_id, pointer_version
            FROM public.rag_v2_immutable_public_bundle_pointers
            WHERE state_id = 'default' AND state = 'ACTIVE'
            """
        ).fetchone()
    if not isinstance(row, Mapping):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_ACTIVE_POINTER")
    if (
        row.get("embedding_profile_id") != PROFILE_ID
        or not isinstance(row.get("exact30_generation_id"), str)
        or not isinstance(row.get("oa112_generation_id"), str)
        or type(row.get("pointer_version")) is not int
    ):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_ACTIVE_POINTER")
    return row


def _pointer_identity(pointer: Mapping[str, Any]) -> dict[str, object]:
    return {
        "embeddingProfileId": pointer["embedding_profile_id"],
        "exact30GenerationId": pointer["exact30_generation_id"],
        "oa112GenerationId": pointer["oa112_generation_id"],
        "pointerVersion": pointer["pointer_version"],
        "state": pointer["state"],
        "stateId": pointer["state_id"],
    }


def _require_schema_version(
    connection: psycopg.Connection[Any],
    *,
    expected: str,
    compatible: frozenset[str] = frozenset(),
) -> None:
    row = connection.execute(
        """
        SELECT version
        FROM public.flyway_schema_history
        WHERE success
        ORDER BY installed_rank DESC
        LIMIT 1
        """
    ).fetchone()
    if row != (expected,) and (not row or row[0] not in compatible):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_SCHEMA_VERSION")


def _non_generated_columns(connection: psycopg.Connection[Any], table_name: str) -> tuple[str, ...]:
    rows = connection.execute(
        """
        SELECT attribute.attname
        FROM pg_catalog.pg_attribute AS attribute
        WHERE attribute.attrelid = %s::regclass
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
          AND attribute.attgenerated = ''
        ORDER BY attribute.attnum
        """,
        (f"public.{table_name}",),
    ).fetchall()
    columns = tuple(str(row[0]) for row in rows)
    if not columns or len(columns) != len(set(columns)):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_COLUMNS")
    return columns


def _generated_columns(connection: psycopg.Connection[Any], table_name: str) -> tuple[str, ...]:
    rows = connection.execute(
        """
        SELECT attribute.attname
        FROM pg_catalog.pg_attribute AS attribute
        WHERE attribute.attrelid = %s::regclass
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped
          AND attribute.attgenerated <> ''
        ORDER BY attribute.attnum
        """,
        (f"public.{table_name}",),
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _verify_target_columns(
    connection: psycopg.Connection[Any], manifest: Mapping[str, Any]
) -> None:
    columns = manifest.get("tableColumns")
    if not isinstance(columns, Mapping):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_COLUMNS")
    for spec in TABLE_SPECS:
        expected = columns.get(spec.name)
        if not isinstance(expected, list) or tuple(expected) != _non_generated_columns(
            connection, spec.name
        ):
            raise PublicRagSeedError("PUBLIC_RAG_SEED_COLUMNS")


def _load_manifest(path: Path) -> Mapping[str, Any]:
    _require_regular_file(path, maximum_bytes=2 * 1024 * 1024)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicRagSeedError("PUBLIC_RAG_SEED_MANIFEST") from error
    if not isinstance(payload, Mapping):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_MANIFEST")
    expected_counts = {spec.name: spec.expected_count for spec in TABLE_SPECS}
    max_part_bytes = payload.get("maxPartBytes")
    if type(max_part_bytes) is not int:
        raise PublicRagSeedError("PUBLIC_RAG_SEED_MANIFEST")
    archive_size_bytes = payload.get("archiveSizeBytes")
    if type(archive_size_bytes) is not int:
        raise PublicRagSeedError("PUBLIC_RAG_SEED_MANIFEST")
    if (
        payload.get("contractId") != "p1-public-rag-seed.v1"
        or payload.get("formatVersion") != FORMAT_VERSION
        or payload.get("sourceFlywaySchemaVersion") != SOURCE_FLYWAY_SCHEMA_VERSION
        or payload.get("targetFlywaySchemaVersion") != TARGET_FLYWAY_SCHEMA_VERSION
        or payload.get("embeddingDimension") != EMBEDDING_DIMENSION
        or payload.get("expectedSourceCount") != EXPECTED_SOURCE_COUNT
        or payload.get("expectedChunkCount") != EXPECTED_CHUNK_COUNT
        or payload.get("profileId") != PROFILE_ID
        or not 1 <= max_part_bytes <= MAX_PART_BYTES
        or payload.get("tableOrder") != [spec.name for spec in TABLE_SPECS]
        or payload.get("tableCounts") != expected_counts
        or not isinstance(payload.get("archiveSha256"), str)
        or not _is_sha256(str(payload.get("archiveSha256")))
        or archive_size_bytes < 1
    ):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_MANIFEST")
    return payload


def _verified_part_paths(*, manifest_path: Path, manifest: Mapping[str, Any]) -> tuple[Path, ...]:
    raw_parts = manifest.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        raise PublicRagSeedError("PUBLIC_RAG_SEED_PARTS")
    archive_hash = hashlib.sha256()
    archive_size = 0
    paths: list[Path] = []
    for expected_ordinal, raw_part in enumerate(raw_parts, start=1):
        if not isinstance(raw_part, Mapping):
            raise PublicRagSeedError("PUBLIC_RAG_SEED_PARTS")
        file_name = raw_part.get("file")
        size_bytes = raw_part.get("sizeBytes")
        expected_hash = raw_part.get("sha256")
        if (
            raw_part.get("ordinal") != expected_ordinal
            or not isinstance(file_name, str)
            or file_name != f"{PART_PREFIX}{expected_ordinal:04d}"
            or type(size_bytes) is not int
            or not 1 <= size_bytes <= MAX_PART_BYTES
            or not isinstance(expected_hash, str)
            or not _is_sha256(expected_hash)
        ):
            raise PublicRagSeedError("PUBLIC_RAG_SEED_PARTS")
        path = manifest_path.parent / file_name
        _require_regular_file(path, maximum_bytes=MAX_PART_BYTES)
        part_hash = hashlib.sha256()
        observed_size = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                observed_size += len(chunk)
                part_hash.update(chunk)
                archive_hash.update(chunk)
        if observed_size != size_bytes or part_hash.hexdigest() != expected_hash:
            raise PublicRagSeedError("PUBLIC_RAG_SEED_PART_HASH")
        archive_size += observed_size
        paths.append(path)
    if archive_size != manifest.get("archiveSizeBytes") or archive_hash.hexdigest() != manifest.get(
        "archiveSha256"
    ):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_ARCHIVE_HASH")
    return tuple(paths)


def _validate_header(header: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    comparable_keys = (
        "embeddingDimension",
        "expectedChunkCount",
        "expectedSourceCount",
        "formatVersion",
        "pointer",
        "profileId",
        "tableColumns",
        "tableOrder",
        "sourceFlywaySchemaVersion",
        "targetFlywaySchemaVersion",
    )
    if header.get("recordType") != "header" or any(
        header.get(key) != manifest.get(key) for key in comparable_keys
    ):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_HEADER")


def _decode_record(line: bytes) -> Mapping[str, Any]:
    try:
        record = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicRagSeedError("PUBLIC_RAG_SEED_JSONL") from error
    if not isinstance(record, Mapping):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_JSONL")
    return record


def _write_json_line(
    archive: Any,
    payload: Mapping[str, object],
    *,
    content_hash: Any | None,
) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        + b"\n"
    )
    archive.write(encoded)
    if content_hash is not None:
        content_hash.update(encoded)


def _write_manifest(path: Path, manifest: Mapping[str, object]) -> None:
    encoded = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _prepare_empty_output_dir(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        path.mkdir(parents=True, mode=0o755)
        metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_OUTPUT_DIR")
    if any(path.iterdir()):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_OUTPUT_NOT_EMPTY")


def _remove_generated_files(path: Path) -> None:
    if not path.exists() or path.is_symlink():
        return
    for child in path.iterdir():
        if child.is_file() and (child.name == MANIFEST_NAME or child.name.startswith(PART_PREFIX)):
            child.unlink()


def _require_regular_file(path: Path, *, maximum_bytes: int) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PublicRagSeedError("PUBLIC_RAG_SEED_FILE") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_size < 1
        or metadata.st_size > maximum_bytes
    ):
        raise PublicRagSeedError("PUBLIC_RAG_SEED_FILE")


def _validate_dsn(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 4096 or "\x00" in value:
        raise PublicRagSeedError("PUBLIC_RAG_SEED_DSN")


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def manifest_summary(manifest: Mapping[str, Any]) -> dict[str, object]:
    """CLI가 secret/row/pointer ID 없이 출력할 content-free receipt다."""

    parts = manifest.get("parts")
    return {
        "archiveSha256": manifest.get("archiveSha256"),
        "archiveSizeBytes": manifest.get("archiveSizeBytes"),
        "chunkCount": manifest.get("expectedChunkCount"),
        "partCount": len(parts) if isinstance(parts, list) else 0,
        "profileId": manifest.get("profileId"),
        "sourceCount": manifest.get("expectedSourceCount"),
    }


def table_names() -> Iterable[str]:
    return (spec.name for spec in TABLE_SPECS)
