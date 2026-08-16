"""S5.6 content-addressed source chunks와 manifest-last 검증 경계."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from typing import Mapping, Sequence, cast

import pyarrow as pa
import pyarrow.parquet as pq

from app.data._shared.bounded_json import (
    BoundedJsonError,
    BoundedJsonLimits,
    parse_bounded_json_bytes,
)
from app.data._shared.canonical_json import canonical_json_bytes
from app.data.krx.production_parsers import S5_PRODUCTION_PROJECTION_FIELDS
from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError
from app.lightgbm.temporal import (
    AvailabilityBasis,
    RevisionBasis,
    TemporalQuality,
    TemporalReceipt,
    receipt_set_sha256,
)
from app.rag.safe_io import RagSafeIoError, read_approved_regular_file


SOURCE_MANIFEST_FILENAME = "manifest.json"
SOURCE_MANIFEST_VERSION = "s5-pit-source-bundle-v1"
# Exact 6,446 call receipts를 raw-free로 모두 결속하므로 1 MiB로는 정상 bootstrap도 표현할 수 없다.
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_CHUNKS = 6_446
SOURCE_BYTE_CAPS = {"KRX": 16 * 1024**3, "KIS": 2 * 1024**3, "ECOS": 64 * 1024**2}
SOURCE_ROW_CAPS = {"KRX": 10_000_000, "KIS": 192_960, "ECOS": 10_000}
_SOURCE_OPERATIONS = {
    "KRX": frozenset(S5_PRODUCTION_PROJECTION_FIELDS),
    "KIS": frozenset({"FHKST03010100"}),
    "ECOS": frozenset({"722Y001/0101000/D", "731Y001/0000001/D"}),
}
_KIS_FIELDS = frozenset(
    {
        "symbol",
        "observationDate",
        "adjustedOpen",
        "adjustedHigh",
        "adjustedLow",
        "adjustedClose",
        "volume",
        "turnover",
        "flngClsCode",
        "prttRate",
        "modYn",
        "revlIssuReas",
    }
)
_ECOS_FIELDS = frozenset({"observationDate", "value"})
_MANIFEST_FIELDS = frozenset(
    {
        "manifestVersion",
        "historicalMode",
        "futureCollectionMode",
        "strictProviderPITClaim",
        "temporalPolicyVersion",
        "createdAt",
        "datasetCutoff",
        "chunks",
    }
)
_CHUNK_FIELDS = frozenset(
    {"sourceId", "operationId", "queryKey", "contentSha256", "rowCount", "bytes", "receipt"}
)
_RECEIPT_FIELDS = frozenset(
    {
        "sourceId",
        "operationId",
        "observationDate",
        "retrievedAt",
        "providerAvailableAt",
        "policyEffectiveAt",
        "availabilityBasis",
        "providerRevision",
        "revisionBasis",
        "requestSha256",
        "snapshotSha256",
        "temporalPolicyVersion",
        "temporalQuality",
    }
)
_JSON_LIMITS = BoundedJsonLimits(
    max_bytes=MAX_MANIFEST_BYTES,
    max_depth=6,
    max_list_items=MAX_CHUNKS,
    max_object_keys=32,
    max_text_codepoints=8_192,
    max_text_bytes=32_768,
    max_number_characters=32,
)


@dataclass(frozen=True, slots=True)
class SourceChunkReceipt:
    """Raw provider response가 아닌 allowlisted sealed projection receipt."""

    source_id: str
    operation_id: str
    query_key: str
    content_sha256: str
    row_count: int
    byte_count: int
    temporal: TemporalReceipt

    @property
    def relative_path(self) -> str:
        """Manifest path input 없이 digest에서만 safe filename을 파생한다."""

        return f"chunks/{self.source_id.lower()}-{self.content_sha256}.parquet"

    def as_dict(self) -> dict[str, object]:
        return {
            "sourceId": self.source_id,
            "operationId": self.operation_id,
            "queryKey": self.query_key,
            "contentSha256": self.content_sha256,
            "rowCount": self.row_count,
            "bytes": self.byte_count,
            "receipt": self.temporal.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class SourceBundle:
    """Manifest와 모든 derived-path chunk 검증이 완료된 immutable bundle."""

    manifest_sha256: str
    manifest_bytes: bytes
    dataset_cutoff: datetime
    chunks: tuple[SourceChunkReceipt, ...]
    receipt_set_sha256: str


def build_source_manifest(
    *, created_at: datetime, dataset_cutoff: datetime, chunks: Sequence[SourceChunkReceipt]
) -> bytes:
    """모든 chunk 검증 뒤 마지막에 기록할 canonical source manifest를 만든다."""

    _require_utc(created_at, "createdAt")
    if dataset_cutoff.tzinfo is None:
        raise LightGbmContractError("datasetCutoff must be timezone aware")
    ordered = tuple(sorted(chunks, key=lambda item: (item.source_id, item.operation_id, item.query_key)))
    _validate_chunks(ordered)
    return canonical_json_bytes(
        {
            "manifestVersion": SOURCE_MANIFEST_VERSION,
            "historicalMode": "HISTORICAL_REPLAY_RECONSTRUCTED",
            "futureCollectionMode": "AS_COLLECTED",
            "strictProviderPITClaim": False,
            "temporalPolicyVersion": "s5-temporal-policy-v2",
            "createdAt": _canonical_utc(created_at),
            "datasetCutoff": _canonical_utc(dataset_cutoff),
            "chunks": [chunk.as_dict() for chunk in ordered],
        }
    )


def read_source_bundle(
    *, approved_root: Path, expected_manifest_sha256: str
) -> SourceBundle:
    """External manifest digest부터 각 content-addressed projection까지 no-follow 검증한다."""

    _require_sha256(expected_manifest_sha256, "source manifest")
    try:
        manifest_file = read_approved_regular_file(
            approved_root=approved_root,
            relative_path=SOURCE_MANIFEST_FILENAME,
            max_bytes=MAX_MANIFEST_BYTES,
        )
    except RagSafeIoError as error:
        raise LightGbmContractError("source manifest file boundary is invalid") from error
    if manifest_file.content_sha256 != expected_manifest_sha256:
        raise LightGbmContractError("source manifest trust anchor mismatch")
    payload = _parse_manifest(manifest_file.content)
    chunks_value = payload["chunks"]
    if not isinstance(chunks_value, list) or not chunks_value:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: source bundle has no chunks")
    chunks = tuple(_parse_chunk(value) for value in chunks_value)
    _validate_chunks(chunks)
    source_bytes = {source: 0 for source in SOURCE_BYTE_CAPS}
    source_decoded = {source: 0 for source in SOURCE_BYTE_CAPS}
    source_rows = {source: 0 for source in SOURCE_ROW_CAPS}
    for chunk in chunks:
        try:
            safe = read_approved_regular_file(
                approved_root=approved_root,
                relative_path=chunk.relative_path,
                max_bytes=min(chunk.byte_count, SOURCE_BYTE_CAPS[chunk.source_id]),
            )
        except RagSafeIoError as error:
            raise LightGbmContractError("source chunk file boundary is invalid") from error
        if safe.content_sha256 != chunk.content_sha256 or len(safe.content) != chunk.byte_count:
            raise LightGbmContractError("source chunk digest or size mismatch")
        decoded_rows, decoded_bytes = _verify_source_parquet(chunk, safe.content)
        if decoded_rows != chunk.row_count:
            raise LightGbmContractError("source chunk declared row count does not match Parquet")
        source_bytes[chunk.source_id] += chunk.byte_count
        source_decoded[chunk.source_id] += decoded_bytes
        source_rows[chunk.source_id] += decoded_rows
        if (
            source_bytes[chunk.source_id] > SOURCE_BYTE_CAPS[chunk.source_id]
            or source_decoded[chunk.source_id] > SOURCE_BYTE_CAPS[chunk.source_id]
            or source_rows[chunk.source_id] > SOURCE_ROW_CAPS[chunk.source_id]
        ):
            raise LightGbmContractError("source bundle decoded row or byte cap exceeded")
    return SourceBundle(
        manifest_sha256=manifest_file.content_sha256,
        manifest_bytes=manifest_file.content,
        dataset_cutoff=_parse_datetime(payload["datasetCutoff"], "datasetCutoff"),
        chunks=chunks,
        receipt_set_sha256=receipt_set_sha256(chunk.temporal for chunk in chunks),
    )


def _parse_manifest(content: bytes) -> dict[str, object]:
    try:
        value = parse_bounded_json_bytes(content, limits=_JSON_LIMITS)
    except BoundedJsonError as error:
        raise LightGbmContractError("source manifest JSON is invalid") from error
    if not isinstance(value, dict):
        raise LightGbmContractError("source manifest root must be an object")
    payload = cast(dict[str, object], value)
    if set(payload) != _MANIFEST_FIELDS or canonical_json_bytes(payload) != content:
        raise LightGbmContractError("source manifest is not closed canonical JSON")
    if (
        payload["manifestVersion"] != SOURCE_MANIFEST_VERSION
        or payload["historicalMode"] != "HISTORICAL_REPLAY_RECONSTRUCTED"
        or payload["futureCollectionMode"] != "AS_COLLECTED"
        or payload["strictProviderPITClaim"] is not False
        or payload["temporalPolicyVersion"] != "s5-temporal-policy-v2"
    ):
        raise LightGbmContractError("source manifest authority is invalid")
    _parse_datetime(payload["createdAt"], "createdAt")
    _parse_datetime(payload["datasetCutoff"], "datasetCutoff")
    return payload


def _parse_chunk(value: object) -> SourceChunkReceipt:
    if not isinstance(value, Mapping) or set(value) != _CHUNK_FIELDS:
        raise LightGbmContractError("source chunk receipt is not closed")
    receipt_value = value["receipt"]
    if not isinstance(receipt_value, Mapping) or not set(receipt_value).issubset(_RECEIPT_FIELDS):
        raise LightGbmContractError("temporal receipt contains an unknown field")
    required = _RECEIPT_FIELDS - {"providerAvailableAt", "policyEffectiveAt", "providerRevision"}
    if not required.issubset(receipt_value):
        raise LightGbmContractError("temporal receipt is incomplete")
    receipt = TemporalReceipt(
        source_id=_text(receipt_value["sourceId"], "sourceId"),
        operation_id=_text(receipt_value["operationId"], "operationId"),
        observation_date=_parse_date(receipt_value["observationDate"], "observationDate"),
        retrieved_at=_parse_datetime(receipt_value["retrievedAt"], "retrievedAt"),
        provider_available_at=_optional_datetime(receipt_value.get("providerAvailableAt")),
        policy_effective_at=_optional_datetime(receipt_value.get("policyEffectiveAt")),
        availability_basis=AvailabilityBasis(_text(receipt_value["availabilityBasis"], "availabilityBasis")),
        provider_revision=_optional_text(receipt_value.get("providerRevision")),
        revision_basis=RevisionBasis(_text(receipt_value["revisionBasis"], "revisionBasis")),
        request_sha256=_text(receipt_value["requestSha256"], "requestSha256"),
        snapshot_sha256=_text(receipt_value["snapshotSha256"], "snapshotSha256"),
        temporal_policy_version=_text(receipt_value["temporalPolicyVersion"], "temporalPolicyVersion"),
        temporal_quality=TemporalQuality(_text(receipt_value["temporalQuality"], "temporalQuality")),
    )
    chunk = SourceChunkReceipt(
        source_id=_text(value["sourceId"], "sourceId"),
        operation_id=_text(value["operationId"], "operationId"),
        query_key=_text(value["queryKey"], "queryKey"),
        content_sha256=_text(value["contentSha256"], "contentSha256"),
        row_count=_integer(value["rowCount"], "rowCount"),
        byte_count=_integer(value["bytes"], "bytes"),
        temporal=receipt,
    )
    if len(chunk.operation_id) > 80 or len(chunk.query_key) > 256:
        raise LightGbmContractError("source chunk operation or query key is too long")
    if (
        chunk.source_id != receipt.source_id
        or chunk.operation_id != receipt.operation_id
        or chunk.content_sha256 != receipt.snapshot_sha256
    ):
        raise LightGbmContractError("source chunk and temporal receipt binding mismatch")
    return chunk


def _validate_chunks(chunks: Sequence[SourceChunkReceipt]) -> None:
    if not chunks or len(chunks) > MAX_CHUNKS:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: source chunk count is invalid")
    keys: set[tuple[str, str, str]] = set()
    for chunk in chunks:
        if chunk.source_id not in SOURCE_BYTE_CAPS:
            raise LightGbmContractError("source chunk provider is not allowlisted")
        if chunk.operation_id not in _SOURCE_OPERATIONS[chunk.source_id]:
            raise LightGbmContractError("source chunk operation is not allowlisted")
        _require_sha256(chunk.content_sha256, "source chunk")
        if chunk.row_count <= 0 or chunk.byte_count <= 0:
            raise DatasetUnavailable("DATASET_UNAVAILABLE: source chunk is empty")
        key = (chunk.source_id, chunk.operation_id, chunk.query_key)
        if key in keys:
            raise LightGbmContractError("source chunk logical key is duplicated")
        keys.add(key)


def _verify_source_parquet(chunk: SourceChunkReceipt, content: bytes) -> tuple[int, int]:
    """Footer 선언과 실제 batch를 모두 읽어 nested/unknown/schema/decoded lie를 거부한다."""

    try:
        parquet = pq.ParquetFile(  # type: ignore[no-untyped-call]
            BytesIO(content),
            thrift_string_size_limit=1 * 1024 * 1024,
            thrift_container_size_limit=10_000_000,
            page_checksum_verification=True,
        )
        metadata = parquet.metadata
        expected_fields = _expected_projection_fields(chunk)
        if (
            metadata.num_rows > SOURCE_ROW_CAPS[chunk.source_id]
            or metadata.num_columns != len(expected_fields)
            or set(parquet.schema_arrow.names) != expected_fields
            or parquet.schema_arrow.metadata
        ):
            raise LightGbmContractError("source Parquet footer or schema is invalid")
        for field in parquet.schema_arrow:
            if field.type != pa.string() or field.nullable:
                raise LightGbmContractError("source Parquet fields must be non-null strings")
        declared_decoded = sum(
            metadata.row_group(group).column(column).total_uncompressed_size
            for group in range(metadata.num_row_groups)
            for column in range(metadata.num_columns)
        )
        if declared_decoded > SOURCE_BYTE_CAPS[chunk.source_id]:
            raise LightGbmContractError("source Parquet declared decoded cap exceeded")
        rows = 0
        decoded = 0
        for batch in parquet.iter_batches(batch_size=8_192, use_threads=False):  # type: ignore[no-untyped-call]
            rows += batch.num_rows
            decoded += batch.nbytes
            if (
                rows > SOURCE_ROW_CAPS[chunk.source_id]
                or decoded > SOURCE_BYTE_CAPS[chunk.source_id]
            ):
                raise LightGbmContractError("source Parquet actual decoded cap exceeded")
        return rows, decoded
    except LightGbmContractError:
        raise
    except Exception as error:
        raise LightGbmContractError("source chunk is not valid bounded Parquet") from error


def _expected_projection_fields(chunk: SourceChunkReceipt) -> frozenset[str]:
    if chunk.source_id == "KRX":
        return S5_PRODUCTION_PROJECTION_FIELDS[chunk.operation_id]
    if chunk.source_id == "KIS":
        return _KIS_FIELDS
    return _ECOS_FIELDS


def _parse_datetime(value: object, field: str) -> datetime:
    text = _text(value, field)
    if not text.endswith("Z"):
        raise LightGbmContractError(f"{field} must use canonical UTC")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        raise LightGbmContractError(f"{field} is invalid") from None
    if _canonical_utc(parsed) != text:
        raise LightGbmContractError(f"{field} must use canonical UTC")
    return parsed


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _parse_datetime(value, "temporal timestamp")


def _parse_date(value: object, field: str) -> date:
    text = _text(value, field)
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise LightGbmContractError(f"{field} is invalid") from None
    if parsed.isoformat() != text:
        raise LightGbmContractError(f"{field} is invalid")
    return parsed


def _canonical_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise LightGbmContractError("timestamp must be timezone aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise LightGbmContractError(f"{field} must be UTC")


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise LightGbmContractError(f"{field} must be bounded text")
    return value


def _optional_text(value: object) -> str | None:
    return None if value is None else _text(value, "optional text")


def _integer(value: object, field: str) -> int:
    if type(value) is not int:
        raise LightGbmContractError(f"{field} must be an integer")
    return value


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise LightGbmContractError(f"{field} SHA-256 is invalid")
