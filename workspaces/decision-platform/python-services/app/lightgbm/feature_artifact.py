"""S5.1 deterministic feature-table Parquet, logical hash와 safe-read 경계."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
from io import BytesIO
from pathlib import Path
import struct
from typing import Any, Iterable, Mapping, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError
from app.lightgbm.features import CORE_FEATURE_COLUMNS, reject_forbidden_columns
from app.rag.safe_io import RagSafeIoError, read_approved_regular_file


MAX_PHYSICAL_BYTES = 256 * 1024 * 1024
MAX_DECODED_BYTES = 256 * 1024 * 1024
MAX_ROWS = 250_000
MAX_COLUMNS = 128
MAX_THRIFT_STRING_BYTES = 1 * 1024 * 1024
MAX_THRIFT_CONTAINER_ITEMS = 300_000
ROW_GROUP_SIZE = 65_536
KEY_COLUMNS = ("symbol", "sessionDate")


@dataclass(frozen=True)
class FeatureArtifact:
    """검증 완료 feature table과 physical/logical digest를 분리한 receipt."""

    table: pa.Table
    parquet_sha256: str
    logical_dataset_hash: str
    physical_bytes: int
    decoded_bytes: int


def feature_table_from_rows(rows: Sequence[Mapping[str, object]]) -> pa.Table:
    """mapping rows를 key + nullable float32의 exact ordered schema로 변환한다."""

    schema = pa.schema(
        [
            pa.field("symbol", pa.string(), nullable=False),
            pa.field("sessionDate", pa.date32(), nullable=False),
        ]
        + [pa.field(name, pa.float32(), nullable=True) for name in CORE_FEATURE_COLUMNS]
    )
    normalized = [
        {
            "symbol": row["symbol"],
            "sessionDate": row["sessionDate"],
            **{name: row.get(name) for name in CORE_FEATURE_COLUMNS},
        }
        for row in rows
    ]
    return pa.Table.from_pylist(normalized, schema=schema)


def write_feature_parquet(table: pa.Table) -> bytes:
    """고정 writer profile로 schema metadata 없는 deterministic Parquet bytes를 만든다."""

    validate_feature_table(table, approved_feature_columns=table.column_names[2:])
    sink = BytesIO()
    pq.write_table(  # type: ignore[no-untyped-call]
        table.replace_schema_metadata(None),
        sink,
        version="2.6",
        data_page_version="1.0",
        compression="zstd",
        compression_level=3,
        use_dictionary=False,
        row_group_size=ROW_GROUP_SIZE,
        write_statistics=True,
    )
    payload = sink.getvalue()
    if not payload or len(payload) > MAX_PHYSICAL_BYTES:
        raise LightGbmContractError("feature Parquet physical size exceeds 256 MiB")
    return payload


def read_feature_artifact(
    *,
    approved_root: Path,
    relative_path: str,
    expected_sha256: str,
    approved_feature_columns: Sequence[str],
) -> FeatureArtifact:
    """approved root의 regular Parquet을 projection 전에 bounded metadata/schema부터 검증한다."""

    try:
        safe = read_approved_regular_file(
            approved_root=approved_root,
            relative_path=relative_path,
            max_bytes=MAX_PHYSICAL_BYTES,
        )
    except RagSafeIoError as error:
        raise LightGbmContractError("feature artifact path or file boundary is invalid") from error
    if safe.content_sha256 != expected_sha256:
        raise LightGbmContractError("feature artifact SHA-256 does not match manifest")
    parquet = pq.ParquetFile(  # type: ignore[no-untyped-call]
        BytesIO(safe.content),
        thrift_string_size_limit=MAX_THRIFT_STRING_BYTES,
        thrift_container_size_limit=MAX_THRIFT_CONTAINER_ITEMS,
        page_checksum_verification=True,
    )
    metadata = parquet.metadata
    if metadata.num_rows > MAX_ROWS or metadata.num_columns > MAX_COLUMNS:
        raise LightGbmContractError("feature artifact row or column cap exceeded")
    uncompressed = sum(
        metadata.row_group(group).column(column).total_uncompressed_size
        for group in range(metadata.num_row_groups)
        for column in range(metadata.num_columns)
    )
    if uncompressed > MAX_DECODED_BYTES:
        raise LightGbmContractError("feature artifact declared decoded size exceeds 256 MiB")
    schema_names = parquet.schema_arrow.names
    reject_forbidden_columns(schema_names)
    expected_columns = [*KEY_COLUMNS, *approved_feature_columns]
    if schema_names != expected_columns:
        raise LightGbmContractError("feature artifact contains an unknown or reordered column")
    batches: list[pa.RecordBatch] = []
    rows = 0
    decoded = 0
    for batch in parquet.iter_batches(  # type: ignore[no-untyped-call]
        batch_size=8_192,
        use_threads=False,
    ):
        rows += batch.num_rows
        decoded += batch.nbytes
        if rows > MAX_ROWS or decoded > MAX_DECODED_BYTES:
            raise LightGbmContractError("feature artifact actual decoded bound exceeded")
        batches.append(batch)
    table = pa.Table.from_batches(batches, schema=parquet.schema_arrow)
    validate_feature_table(table, approved_feature_columns=approved_feature_columns)
    return FeatureArtifact(
        table=table,
        parquet_sha256=safe.content_sha256,
        logical_dataset_hash=logical_dataset_hash(table),
        physical_bytes=len(safe.content),
        decoded_bytes=decoded,
    )


def validate_feature_table(table: pa.Table, *, approved_feature_columns: Sequence[str]) -> None:
    """fit 전에 exact schema, float32, finite, sort와 unique key를 전수 검사한다."""

    reject_forbidden_columns(table.column_names)
    if table.num_rows > MAX_ROWS or table.num_columns > MAX_COLUMNS:
        raise LightGbmContractError("feature table row or column cap exceeded")
    expected = [*KEY_COLUMNS, *approved_feature_columns]
    if table.column_names != expected or len(expected) != len(set(expected)):
        raise LightGbmContractError(
            "feature table schema has unknown, duplicate, or reordered columns"
        )
    if table.schema.metadata:
        raise LightGbmContractError("feature table arbitrary schema metadata is forbidden")
    if table.schema.field("symbol").type != pa.string() or table.schema.field("symbol").nullable:
        raise LightGbmContractError("feature symbol must be non-null UTF-8")
    if (
        table.schema.field("sessionDate").type != pa.date32()
        or table.schema.field("sessionDate").nullable
    ):
        raise LightGbmContractError("feature sessionDate must be non-null date32")
    for name in approved_feature_columns:
        field = table.schema.field(name)
        if field.type != pa.float32():
            raise LightGbmContractError("all feature columns must be nullable float32")
        values = table[name].combine_chunks()
        for value in values.to_pylist():
            if value is not None and not (-float("inf") < float(value) < float("inf")):
                raise LightGbmContractError("feature table contains a non-finite value")
    keys = list(zip(table["sessionDate"].to_pylist(), table["symbol"].to_pylist(), strict=True))
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise LightGbmContractError(
            "feature table key must be unique and sorted by sessionDate, symbol"
        )


def logical_dataset_hash(table: pa.Table) -> str:
    """label join 전 feature table의 packed-validity logical hash를 계산한다."""

    digest = hashlib.sha256()
    digest.update(b"s5-logical-feature-table-v1\x00")
    _update_logical_schema_and_values(digest, table)
    return digest.hexdigest()


def logical_training_dataset_hash(table: pa.Table, labels: Sequence[int]) -> str:
    """ordered feature bytes와 exact class label byte를 함께 묶은 model-input hash를 계산한다."""

    label_values = tuple(labels)
    if len(label_values) != table.num_rows or any(
        isinstance(value, bool) or value not in (0, 1, 2) for value in label_values
    ):
        raise LightGbmContractError("training dataset labels must use exact class bytes 0, 1, 2")
    digest = hashlib.sha256()
    digest.update(b"s5-logical-training-dataset-v1\x00")
    _update_logical_schema_and_values(digest, table)
    digest.update(bytes(label_values))
    return digest.hexdigest()


def _update_logical_schema_and_values(digest: Any, table: pa.Table) -> None:
    """schema, key, packed validity, little-endian float32 순서를 두 hash profile이 공유한다."""

    for field in table.schema:
        digest.update(field.name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(field.type).encode("ascii"))
        digest.update(b"\x01" if field.nullable else b"\x00")
    rows = table.to_pylist()
    epoch = date(1970, 1, 1)
    for row in rows:
        symbol = row["symbol"].encode("utf-8")
        digest.update(struct.pack("<I", len(symbol)))
        digest.update(symbol)
        digest.update(struct.pack("<i", (row["sessionDate"] - epoch).days))

    feature_names = table.column_names[2:]
    validity = bytearray((len(rows) * len(feature_names) + 7) // 8)
    for row_index, row in enumerate(rows):
        for feature_index, field in enumerate(feature_names):
            if row[field] is not None:
                bit_index = row_index * len(feature_names) + feature_index
                validity[bit_index // 8] |= 1 << (bit_index % 8)
    digest.update(struct.pack("<I", len(validity)))
    digest.update(validity)

    for row in rows:
        for field in feature_names:
            value = row[field]
            if value is not None:
                digest.update(struct.pack("<f", float(value)))


def build_feature_manifest(artifact: FeatureArtifact, *, provenance: Mapping[str, object]) -> bytes:
    """기존 canonical JSON helper로 closed consumer manifest bytes를 만든다."""

    return canonical_json_bytes(
        {
            "schemaVersion": "s5-feature-table-v1",
            "logicalDatasetHash": artifact.logical_dataset_hash,
            "parquetSha256": artifact.parquet_sha256,
            "rowCount": artifact.table.num_rows,
            "columnCount": artifact.table.num_columns,
            "provenance": dict(provenance),
        }
    )


def optional_group_is_eligible(
    fold_evidence: Iterable[tuple[int, int, bool]],
) -> bool:
    """모든 primary fold가 exact coverage 98% 이상이고 complete evidence일 때만 포함한다."""

    evidence = tuple(fold_evidence)
    if not evidence:
        return False
    for covered, denominator, complete in evidence:
        if denominator <= 0 or covered < 0 or covered > denominator or not complete:
            return False
        if covered / denominator < 0.98:
            return False
    return True


def require_source_rows(row_count: int) -> None:
    """calendar arithmetic만 있고 source rows가 없으면 성공 대신 DATASET_UNAVAILABLE을 낸다."""

    if row_count <= 0:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: source feature rows are absent")
