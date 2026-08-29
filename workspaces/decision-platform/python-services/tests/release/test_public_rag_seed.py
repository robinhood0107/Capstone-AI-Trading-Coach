from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path

import pytest

from app.release.public_rag_seed import (
    FORWARD_COMPATIBLE_TARGET_SCHEMA_VERSIONS,
    MAX_PART_BYTES,
    PART_PREFIX,
    TABLE_SPECS,
    PublicRagSeedError,
    _PartReader,
    _SplitHashWriter,
    _require_empty_target,
    verify_seed_parts,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
SEED_ROOT = REPOSITORY_ROOT / "deploy" / "p1" / "seed" / "public-rag"
MANIFEST = SEED_ROOT / "public-rag-seed.v1.manifest.json"


def test_sealed_v87_seed_allows_only_declared_additive_targets() -> None:
    # 봉인된 Seed는 V87에 바이트로 묶여 있고, 그 뒤 additive migration만 명시적으로 호환을
    # 선언한다. 목록이 저절로 넓어지지 않는지 확인한다.
    assert FORWARD_COMPATIBLE_TARGET_SCHEMA_VERSIONS == frozenset(
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
        }
    )


def test_committed_public_seed_parts_are_hash_bound_and_bounded() -> None:
    manifest = verify_seed_parts(manifest_path=MANIFEST)

    assert manifest["sourceFlywaySchemaVersion"] == "73"
    assert manifest["targetFlywaySchemaVersion"] == "87"
    assert manifest["expectedSourceCount"] == 142
    assert manifest["expectedChunkCount"] == 7_871
    assert manifest["embeddingDimension"] == 1024
    assert manifest["profileId"] == "voyage_context_4_1024_v1"
    parts = manifest["parts"]
    assert isinstance(parts, list)
    assert len(parts) == 2
    assert all(isinstance(part, dict) and part["sizeBytes"] <= MAX_PART_BYTES for part in parts)


def test_committed_public_seed_archive_has_closed_table_order_and_counts() -> None:
    manifest = verify_seed_parts(manifest_path=MANIFEST)
    parts = manifest["parts"]
    assert isinstance(parts, list)
    assert all(isinstance(part, dict) and isinstance(part.get("file"), str) for part in parts)
    part_paths = tuple(SEED_ROOT / str(part["file"]) for part in parts)
    counts = {spec.name: 0 for spec in TABLE_SPECS}
    content_hash = hashlib.sha256()

    with gzip.GzipFile(fileobj=io.BufferedReader(_PartReader(part_paths)), mode="rb") as archive:
        header_line = archive.readline()
        header = json.loads(header_line)
        content_hash.update(header_line)
        assert header["recordType"] == "header"
        assert header["tableOrder"] == [spec.name for spec in TABLE_SPECS]
        footer = None
        for line in archive:
            record = json.loads(line)
            if record["recordType"] == "footer":
                footer = record
                break
            assert record["recordType"] == "row"
            assert record["table"] in counts
            assert isinstance(record["value"], dict)
            counts[record["table"]] += 1
            content_hash.update(line)

        assert footer is not None
        assert archive.read(1) == b""
        assert footer["tableCounts"] == counts == manifest["tableCounts"]
        assert footer["contentSha256"] == content_hash.hexdigest()


def test_split_writer_is_deterministic_and_never_exceeds_bound(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    payload = (b"seed-boundary" * 1000) + b"!"

    first_writer = _SplitHashWriter(first, max_part_bytes=4096)
    first_writer.write(payload)
    first_writer.close()
    second_writer = _SplitHashWriter(second, max_part_bytes=4096)
    second_writer.write(payload)
    second_writer.close()

    assert first_writer.archive_sha256 == second_writer.archive_sha256
    assert first_writer.archive_size == len(payload)
    assert [part.size_bytes for part in first_writer.parts] == [4096, 4096, 4096, 713]
    assert [part.sha256 for part in first_writer.parts] == [
        part.sha256 for part in second_writer.parts
    ]
    assert [part.file for part in first_writer.parts] == [
        f"{PART_PREFIX}{ordinal:04d}" for ordinal in range(1, 5)
    ]


def test_seed_verifier_rejects_symlinked_manifest(tmp_path: Path) -> None:
    link = tmp_path / "manifest.json"
    link.symlink_to(MANIFEST)

    with pytest.raises(PublicRagSeedError, match="PUBLIC_RAG_SEED_FILE"):
        verify_seed_parts(manifest_path=link)


def test_part_reader_hashes_the_exact_consumed_bytes_and_rejects_symlinks(
    tmp_path: Path,
) -> None:
    first = tmp_path / "part-0001"
    second = tmp_path / "part-0002"
    first.write_bytes(b"first-part")
    second.write_bytes(b"second-part")
    payload = first.read_bytes() + second.read_bytes()
    reader = _PartReader((first, second))

    with io.BufferedReader(reader, buffer_size=4) as buffered:
        assert buffered.read() == payload

    assert reader.archive_size == len(payload)
    assert reader.archive_sha256 == hashlib.sha256(payload).hexdigest()

    link = tmp_path / "part-link"
    link.symlink_to(first)
    linked_reader = _PartReader((link,))
    with pytest.raises(PublicRagSeedError, match="PUBLIC_RAG_SEED_PART_OPEN"):
        io.BufferedReader(linked_reader).read()


def test_export_queries_are_public_pointer_reachable_only() -> None:
    by_name = {spec.name: spec.select_sql for spec in TABLE_SPECS}

    for table_name in (
        "rag_v2_immutable_source_revisions",
        "rag_v2_immutable_chunks",
        "rag_v2_immutable_component_generations",
        "rag_v2_immutable_generation_memberships",
        "rag_v2_immutable_generation_embeddings",
    ):
        query = by_name[table_name]
        assert "seed_pointer" in query
        assert "owner_user_id IS NULL" in query
    assert not any("users" in spec.select_sql for spec in TABLE_SPECS)
    assert not any("usage_reservations" in spec.select_sql for spec in TABLE_SPECS)


def test_empty_squashed_baseline_gets_only_the_not_materialized_pointer() -> None:
    class Result:
        def __init__(self, row: tuple[int, ...]):
            self._row = row

        def fetchone(self) -> tuple[int, ...]:
            return self._row

    class Connection:
        def __init__(self) -> None:
            self.statements: list[str] = []
            self.pointer_checked = False

        def execute(self, statement: object) -> Result:
            rendered = str(statement)
            self.statements.append(rendered)
            if "FROM public.rag_v2_immutable_public_bundle_pointers" in rendered:
                self.pointer_checked = True
                return Result((0, 0))
            return Result((0,))

    connection = Connection()
    _require_empty_target(connection)  # type: ignore[arg-type]

    assert connection.pointer_checked
    inserts = [statement for statement in connection.statements if "INSERT INTO" in statement]
    assert len(inserts) == 1
    assert "rag_v2_immutable_public_bundle_pointers" in inserts[0]
    assert "NOT_MATERIALIZED" in inserts[0]
