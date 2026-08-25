"""S5.1 deterministic feature-table Parquet, logical hash와 safe-read 경계."""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, ClassVar, cast

import pyarrow as pa
import pyarrow.parquet as pq

from app.data._shared.bounded_json import (
    BoundedJsonError,
    BoundedJsonLimits,
    parse_bounded_json_bytes,
)
from app.data._shared.canonical_json import canonical_json_bytes
from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError
from app.lightgbm.features import (
    CORE_FEATURE_COLUMNS,
    MarketEvidence,
    PriceEvidence,
    reject_forbidden_columns,
)
from app.lightgbm.pit_calendar import (
    ELIGIBLE_SESSION_COUNT,
    RAW_SESSION_COUNT,
    MonthlyUniverseSchedule,
    build_pit_session_window,
    derive_monthly_universe_schedule,
)
from app.lightgbm.private_root import require_private_regular_file, require_private_root
from app.lightgbm.universe import MonthlyUniverse
from app.rag.safe_io import RagSafeIoError, read_approved_regular_file

MAX_PHYSICAL_BYTES = 256 * 1024 * 1024
MAX_DECODED_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 1 * 1024 * 1024
MAX_ROWS = 250_000
MAX_COLUMNS = 128
MAX_THRIFT_STRING_BYTES = 1 * 1024 * 1024
MAX_THRIFT_CONTAINER_ITEMS = 300_000
ROW_GROUP_SIZE = 65_536
KEY_COLUMNS = ("symbol", "sessionDate")
MANIFEST_FILENAME = "manifest.json"
PARQUET_FILENAME = "features.parquet"
MANIFEST_VERSION = "s5-feature-bundle-v1"
PRODUCTION_MANIFEST_VERSION = "s5-feature-bundle-v2"
SCHEMA_VERSION = "s5-feature-table-v1"
_MANIFEST_FIELDS = frozenset(
    {
        "manifestVersion",
        "schemaVersion",
        "parquetFile",
        "parquetSha256",
        "logicalDatasetHash",
        "rowCount",
        "columnCount",
        "featureColumns",
        "provenance",
    }
)
_PROVENANCE_FIELDS = frozenset(
    {
        "producer",
        "sourceWorkspace",
        "datasetCutoff",
        "exchangeMic",
        "calendarName",
        "calendarVersion",
        "universePolicyVersion",
        "featurePolicyVersion",
        "rawSessionStart",
        "rawSessionEnd",
        "rawSessionCount",
        "eligibleSessionStart",
        "eligibleSessionEnd",
        "eligibleSessionCount",
        "universeScheduleSha256",
        "pitInputSha256",
        "optionalFeatureGroups",
    }
)
_PRODUCTION_PROVENANCE_FIELDS = _PROVENANCE_FIELDS | frozenset(
    {
        "temporalPolicyVersion",
        "temporalQuality",
        "sourceBundleSetSha256",
        "sourcePolicySetSha256",
    }
)
_MANIFEST_JSON_LIMITS = BoundedJsonLimits(
    max_bytes=MAX_MANIFEST_BYTES,
    max_depth=4,
    max_list_items=MAX_COLUMNS,
    max_object_keys=32,
    max_text_codepoints=4_096,
    max_text_bytes=16_384,
    max_number_characters=32,
)


@dataclass(frozen=True)
class FeatureArtifact:
    """검증 완료 feature table과 physical/logical digest를 분리한 receipt."""

    table: pa.Table
    parquet_sha256: str
    logical_dataset_hash: str
    physical_bytes: int
    decoded_bytes: int


@dataclass(frozen=True)
class FeatureBundleProvenance:
    """S5-authoritative PIT input만 묶는 closed feature bundle provenance."""

    dataset_cutoff: datetime
    raw_session_start: date
    raw_session_end: date
    raw_session_count: int
    eligible_session_start: date
    eligible_session_end: date
    eligible_session_count: int
    universe_schedule_sha256: str
    pit_input_sha256: str

    producer: ClassVar[str] = "decision-platform"
    source_workspace: ClassVar[str] = "decision-platform"
    exchange_mic: ClassVar[str] = "XKRX"
    calendar_name: ClassVar[str] = "XKRX"
    calendar_version: ClassVar[str] = "4.13.2"
    universe_policy_version: ClassVar[str] = "s5-pit-universe-v1"
    feature_policy_version: ClassVar[str] = "s5-core-features-v1"
    optional_feature_groups: ClassVar[tuple[str, ...]] = ()

    def __post_init__(self) -> None:
        if self.dataset_cutoff.tzinfo is None:
            raise LightGbmContractError("feature provenance cutoff must be timezone aware")
        expected_window = build_pit_session_window(self.dataset_cutoff)
        if (
            self.raw_session_count != RAW_SESSION_COUNT
            or self.eligible_session_count != ELIGIBLE_SESSION_COUNT
            or self.raw_session_start != expected_window.raw_sessions[0]
            or self.raw_session_end != expected_window.raw_sessions[-1]
            or self.eligible_session_start != expected_window.eligible_sessions[0]
            or self.eligible_session_end != expected_window.eligible_sessions[-1]
        ):
            raise LightGbmContractError("feature provenance session window is invalid")
        _require_sha256(self.universe_schedule_sha256, "universe schedule")
        _require_sha256(self.pit_input_sha256, "PIT input")

    def as_mapping(self) -> dict[str, object]:
        """manifest exact camelCase provenance projection을 반환한다."""

        return {
            "producer": self.producer,
            "sourceWorkspace": self.source_workspace,
            "datasetCutoff": _canonical_utc(self.dataset_cutoff),
            "exchangeMic": self.exchange_mic,
            "calendarName": self.calendar_name,
            "calendarVersion": self.calendar_version,
            "universePolicyVersion": self.universe_policy_version,
            "featurePolicyVersion": self.feature_policy_version,
            "rawSessionStart": self.raw_session_start.isoformat(),
            "rawSessionEnd": self.raw_session_end.isoformat(),
            "rawSessionCount": self.raw_session_count,
            "eligibleSessionStart": self.eligible_session_start.isoformat(),
            "eligibleSessionEnd": self.eligible_session_end.isoformat(),
            "eligibleSessionCount": self.eligible_session_count,
            "universeScheduleSha256": self.universe_schedule_sha256,
            "pitInputSha256": self.pit_input_sha256,
            "optionalFeatureGroups": [],
        }


@dataclass(frozen=True)
class FeatureBundle:
    """외부 trust anchor와 manifest/Parquet 검증을 모두 통과한 immutable receipt."""

    artifact: FeatureArtifact
    manifest_sha256: str
    manifest_bytes: bytes
    provenance: FeatureBundleProvenance


@dataclass(frozen=True)
class ProductionFeatureBundleProvenance:
    """Reconstructed source bundle까지 결속하는 production-only provenance v2."""

    base: FeatureBundleProvenance
    source_bundle_set_sha256: str
    source_policy_set_sha256: str

    temporal_policy_version: ClassVar[str] = "s5-temporal-policy-v2"
    temporal_quality: ClassVar[str] = "RECONSTRUCTED_FIXED_LAG"
    universe_policy_version: ClassVar[str] = "top30-plus-132030-v1"

    def __post_init__(self) -> None:
        _require_sha256(self.source_bundle_set_sha256, "source bundle set")
        _require_sha256(self.source_policy_set_sha256, "source policy set")

    def as_mapping(self) -> dict[str, object]:
        """v1 PIT fields를 보존하면서 production authority fields를 추가한다."""

        value = self.base.as_mapping()
        value["universePolicyVersion"] = self.universe_policy_version
        value.update(
            {
                "temporalPolicyVersion": self.temporal_policy_version,
                "temporalQuality": self.temporal_quality,
                "sourceBundleSetSha256": self.source_bundle_set_sha256,
                "sourcePolicySetSha256": self.source_policy_set_sha256,
            }
        )
        return value


@dataclass(frozen=True)
class ProductionFeatureBundle:
    """Feature bundle v2 검증 완료 receipt이며 production trainer만 소비한다."""

    artifact: FeatureArtifact
    manifest_sha256: str
    manifest_bytes: bytes
    provenance: ProductionFeatureBundleProvenance


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


def read_feature_bundle(
    *,
    approved_root: Path,
    expected_manifest_sha256: str,
) -> FeatureBundle:
    """고정 manifest와 Parquet을 외부 digest부터 순서대로 검증한다."""

    _require_sha256(expected_manifest_sha256, "feature manifest")
    try:
        safe_manifest = read_approved_regular_file(
            approved_root=approved_root,
            relative_path=MANIFEST_FILENAME,
            max_bytes=MAX_MANIFEST_BYTES,
        )
    except RagSafeIoError as error:
        raise LightGbmContractError("feature manifest path or file boundary is invalid") from error
    if safe_manifest.content_sha256 != expected_manifest_sha256:
        raise LightGbmContractError("feature manifest SHA-256 does not match trust anchor")
    manifest = _parse_feature_manifest(safe_manifest.content)
    row_count = _require_integer(manifest["rowCount"], "rowCount")
    if row_count == 0:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: source feature rows are absent")
    column_count = _require_integer(manifest["columnCount"], "columnCount")
    if (
        row_count < 0
        or row_count > MAX_ROWS
        or column_count != len(KEY_COLUMNS) + len(CORE_FEATURE_COLUMNS)
    ):
        raise LightGbmContractError("feature manifest row or column count is invalid")
    provenance = _parse_feature_provenance(manifest["provenance"])
    artifact = _read_feature_artifact(
        approved_root=approved_root,
        expected_sha256=_require_text(manifest["parquetSha256"], "parquetSha256"),
    )
    if (
        artifact.table.num_rows != row_count
        or artifact.table.num_columns != column_count
        or artifact.logical_dataset_hash
        != _require_text(manifest["logicalDatasetHash"], "logicalDatasetHash")
    ):
        raise LightGbmContractError("feature bundle manifest does not match decoded Parquet")
    return FeatureBundle(
        artifact=artifact,
        manifest_sha256=safe_manifest.content_sha256,
        manifest_bytes=safe_manifest.content,
        provenance=provenance,
    )


def read_production_feature_bundle(
    *, approved_root: Path, expected_manifest_sha256: str
) -> ProductionFeatureBundle:
    """v1 fixture reader와 분리해 feature bundle v2만 production input으로 연다."""

    _require_sha256(expected_manifest_sha256, "feature manifest")
    require_private_root(approved_root)
    try:
        safe_manifest = read_approved_regular_file(
            approved_root=approved_root,
            relative_path=MANIFEST_FILENAME,
            max_bytes=MAX_MANIFEST_BYTES,
        )
    except RagSafeIoError as error:
        raise LightGbmContractError("feature manifest path or file boundary is invalid") from error
    if safe_manifest.content_sha256 != expected_manifest_sha256:
        raise LightGbmContractError("feature manifest SHA-256 does not match trust anchor")
    require_private_regular_file(
        safe_manifest.absolute_path,
        expected_device=safe_manifest.device,
        expected_inode=safe_manifest.inode,
    )
    manifest = _parse_feature_manifest_v2(safe_manifest.content)
    row_count = _require_integer(manifest["rowCount"], "rowCount")
    require_source_rows(row_count)
    artifact = _read_feature_artifact(
        approved_root=approved_root,
        expected_sha256=_require_text(manifest["parquetSha256"], "parquetSha256"),
        require_private=True,
    )
    if (
        artifact.table.num_rows != row_count
        or artifact.table.num_columns != _require_integer(manifest["columnCount"], "columnCount")
        or artifact.logical_dataset_hash != manifest["logicalDatasetHash"]
    ):
        raise LightGbmContractError("production feature manifest does not match Parquet")
    provenance = _parse_production_feature_provenance(manifest["provenance"])
    return ProductionFeatureBundle(
        artifact=artifact,
        manifest_sha256=safe_manifest.content_sha256,
        manifest_bytes=safe_manifest.content,
        provenance=provenance,
    )


def _read_feature_artifact(
    *,
    approved_root: Path,
    expected_sha256: str,
    require_private: bool = False,
) -> FeatureArtifact:
    """manifest 검증 뒤에만 호출하는 고정-path Parquet reader."""

    try:
        safe = read_approved_regular_file(
            approved_root=approved_root,
            relative_path=PARQUET_FILENAME,
            max_bytes=MAX_PHYSICAL_BYTES,
        )
    except RagSafeIoError as error:
        raise LightGbmContractError("feature artifact path or file boundary is invalid") from error
    if safe.content_sha256 != expected_sha256:
        raise LightGbmContractError("feature artifact SHA-256 does not match manifest")
    if require_private:
        require_private_regular_file(
            safe.absolute_path,
            expected_device=safe.device,
            expected_inode=safe.inode,
        )
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
    expected_columns = [*KEY_COLUMNS, *CORE_FEATURE_COLUMNS]
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
    validate_feature_table(table, approved_feature_columns=CORE_FEATURE_COLUMNS)
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


def logical_universe_schedule_sha256(
    schedules: Sequence[MonthlyUniverseSchedule],
) -> str:
    """calendar-derived schedule만 정렬해 feature provenance digest로 묶는다."""

    ordered = sorted(schedules, key=lambda item: item.effective_month)
    if not ordered or len({item.effective_month for item in ordered}) != len(ordered):
        raise LightGbmContractError("feature universe schedules must be non-empty and unique")
    receipts: list[dict[str, object]] = []
    for schedule in ordered:
        expected = derive_monthly_universe_schedule(
            schedule.effective_month,
            dataset_cutoff=schedule.evidence_cutoff,
        )
        if schedule != expected:
            raise LightGbmContractError("feature universe schedule is not XKRX-derived")
        receipts.append(
            {
                "effectiveMonth": schedule.effective_month,
                "firstEffectiveSession": schedule.first_effective_session.isoformat(),
                "evidenceCutoff": _canonical_utc(schedule.evidence_cutoff),
                "selectionSession": schedule.selection_session.isoformat(),
                "trailingSessions": [value.isoformat() for value in schedule.trailing_sessions],
            }
        )
    return _canonical_domain_sha256(b"s5-universe-schedule-v1\x00", receipts)


def logical_pit_input_sha256(
    universes: Sequence[MonthlyUniverse],
    prices: Sequence[PriceEvidence],
    markets: Sequence[MarketEvidence],
) -> str:
    """S5 price/market/macro/universe PIT value와 provenance만 content hash로 묶는다."""

    if not universes or not prices or not markets:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: PIT input evidence is absent")
    if len({item.effective_month for item in universes}) != len(universes) or any(
        not item.instrument_ids
        or len(item.instrument_ids) != len(item.symbols)
        or len(set(item.instrument_ids)) != len(item.instrument_ids)
        for item in universes
    ):
        raise LightGbmContractError("PIT universe input is invalid")
    for price in prices:
        _validate_pit_receipt(price.available_at, price.source_revision, price.source_sha256)
    for market in markets:
        _validate_pit_receipt(market.available_at, market.source_revision, market.source_sha256)
    universe_receipts = [
        {
            "effectiveMonth": item.effective_month,
            "selectionSession": item.selection_session.isoformat(),
            "instrumentIds": list(item.instrument_ids),
            "symbols": list(item.symbols),
        }
        for item in sorted(universes, key=lambda value: (value.effective_month, value.symbols))
    ]
    price_receipts = [
        {
            "instrumentId": item.instrument_id,
            "symbol": item.symbol,
            "sessionDate": item.session_date.isoformat(),
            "adjustedOpen": item.adjusted_open,
            "adjustedClose": item.adjusted_close,
            "volume": item.volume,
            "availableAt": _canonical_evidence_time(item.available_at),
            "sourceRevision": item.source_revision,
            "sourceSha256": item.source_sha256,
        }
        for item in sorted(
            prices,
            key=lambda value: (
                value.instrument_id,
                value.session_date,
                _canonical_evidence_time(value.available_at),
                value.source_revision,
                value.source_sha256,
            ),
        )
    ]
    market_receipts = [
        {
            "market": item.market,
            "sessionDate": item.session_date.isoformat(),
            "marketAdjustedClose": item.market_adjusted_close,
            "baseRate": item.base_rate,
            "usdkrw": item.usdkrw,
            "availableAt": _canonical_evidence_time(item.available_at),
            "sourceRevision": item.source_revision,
            "sourceSha256": item.source_sha256,
        }
        for item in sorted(
            markets,
            key=lambda value: (
                value.market,
                value.session_date,
                _canonical_evidence_time(value.available_at),
                value.source_revision,
                value.source_sha256,
            ),
        )
    ]
    payload = {
        "universes": universe_receipts,
        "prices": price_receipts,
        "markets": market_receipts,
    }
    return _canonical_domain_sha256(b"s5-pit-input-v1\x00", payload)


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


def build_feature_manifest(
    artifact: FeatureArtifact,
    *,
    provenance: FeatureBundleProvenance,
) -> bytes:
    """외부 trust anchor가 승인할 closed feature bundle manifest를 만든다."""

    require_source_rows(artifact.table.num_rows)
    validate_feature_table(artifact.table, approved_feature_columns=CORE_FEATURE_COLUMNS)
    _require_sha256(artifact.parquet_sha256, "Parquet")
    _require_sha256(artifact.logical_dataset_hash, "logical dataset")
    if artifact.logical_dataset_hash != logical_dataset_hash(artifact.table):
        raise LightGbmContractError("feature artifact logical hash does not match its table")
    if (
        artifact.physical_bytes <= 0
        or artifact.physical_bytes > MAX_PHYSICAL_BYTES
        or artifact.decoded_bytes < 0
        or artifact.decoded_bytes > MAX_DECODED_BYTES
    ):
        raise LightGbmContractError("feature artifact size receipt is invalid")
    return canonical_json_bytes(
        {
            "manifestVersion": MANIFEST_VERSION,
            "schemaVersion": SCHEMA_VERSION,
            "parquetFile": PARQUET_FILENAME,
            "logicalDatasetHash": artifact.logical_dataset_hash,
            "parquetSha256": artifact.parquet_sha256,
            "rowCount": artifact.table.num_rows,
            "columnCount": artifact.table.num_columns,
            "featureColumns": list(CORE_FEATURE_COLUMNS),
            "provenance": provenance.as_mapping(),
        }
    )


def build_production_feature_manifest(
    artifact: FeatureArtifact, *, provenance: ProductionFeatureBundleProvenance
) -> bytes:
    """동일 feature table logical hash를 유지하며 authority만 v2로 결속한다."""

    require_source_rows(artifact.table.num_rows)
    validate_feature_table(artifact.table, approved_feature_columns=CORE_FEATURE_COLUMNS)
    _require_sha256(artifact.parquet_sha256, "Parquet")
    _require_sha256(artifact.logical_dataset_hash, "logical dataset")
    if artifact.logical_dataset_hash != logical_dataset_hash(artifact.table):
        raise LightGbmContractError("production feature logical hash is inconsistent")
    return canonical_json_bytes(
        {
            "manifestVersion": PRODUCTION_MANIFEST_VERSION,
            "schemaVersion": SCHEMA_VERSION,
            "parquetFile": PARQUET_FILENAME,
            "logicalDatasetHash": artifact.logical_dataset_hash,
            "parquetSha256": artifact.parquet_sha256,
            "rowCount": artifact.table.num_rows,
            "columnCount": artifact.table.num_columns,
            "featureColumns": list(CORE_FEATURE_COLUMNS),
            "provenance": provenance.as_mapping(),
        }
    )


def _parse_feature_manifest(content: bytes) -> dict[str, object]:
    try:
        payload = parse_bounded_json_bytes(content, limits=_MANIFEST_JSON_LIMITS)
    except BoundedJsonError as error:
        raise LightGbmContractError("feature manifest JSON is invalid") from error
    if not isinstance(payload, dict):
        raise LightGbmContractError("feature manifest root must be an object")
    manifest = cast(dict[str, object], payload)
    if set(manifest) != _MANIFEST_FIELDS:
        raise LightGbmContractError("feature manifest contains an unknown or missing field")
    if canonical_json_bytes(manifest) != content:
        raise LightGbmContractError("feature manifest must use canonical JSON bytes")
    if (
        manifest["manifestVersion"] != MANIFEST_VERSION
        or manifest["schemaVersion"] != SCHEMA_VERSION
        or manifest["parquetFile"] != PARQUET_FILENAME
        or manifest["featureColumns"] != list(CORE_FEATURE_COLUMNS)
    ):
        raise LightGbmContractError("feature manifest version or schema is invalid")
    _require_sha256(_require_text(manifest["parquetSha256"], "parquetSha256"), "Parquet")
    _require_sha256(
        _require_text(manifest["logicalDatasetHash"], "logicalDatasetHash"),
        "logical dataset",
    )
    return manifest


def _parse_feature_manifest_v2(content: bytes) -> dict[str, object]:
    try:
        payload = parse_bounded_json_bytes(content, limits=_MANIFEST_JSON_LIMITS)
    except BoundedJsonError as error:
        raise LightGbmContractError("production feature manifest JSON is invalid") from error
    if not isinstance(payload, dict):
        raise LightGbmContractError("production feature manifest root must be an object")
    manifest = cast(dict[str, object], payload)
    if set(manifest) != _MANIFEST_FIELDS or canonical_json_bytes(manifest) != content:
        raise LightGbmContractError("production feature manifest is not closed canonical JSON")
    if (
        manifest["manifestVersion"] != PRODUCTION_MANIFEST_VERSION
        or manifest["schemaVersion"] != SCHEMA_VERSION
        or manifest["parquetFile"] != PARQUET_FILENAME
        or manifest["featureColumns"] != list(CORE_FEATURE_COLUMNS)
    ):
        raise LightGbmContractError("production feature manifest version or schema is invalid")
    _require_sha256(_require_text(manifest["parquetSha256"], "parquetSha256"), "Parquet")
    _require_sha256(
        _require_text(manifest["logicalDatasetHash"], "logicalDatasetHash"), "logical dataset"
    )
    return manifest


def _parse_feature_provenance(value: object) -> FeatureBundleProvenance:
    if not isinstance(value, dict):
        raise LightGbmContractError("feature provenance must be an object")
    provenance = cast(dict[str, object], value)
    if set(provenance) != _PROVENANCE_FIELDS:
        raise LightGbmContractError("feature provenance contains an unknown or missing field")
    constants = {
        "producer": FeatureBundleProvenance.producer,
        "sourceWorkspace": FeatureBundleProvenance.source_workspace,
        "exchangeMic": FeatureBundleProvenance.exchange_mic,
        "calendarName": FeatureBundleProvenance.calendar_name,
        "calendarVersion": FeatureBundleProvenance.calendar_version,
        "universePolicyVersion": FeatureBundleProvenance.universe_policy_version,
        "featurePolicyVersion": FeatureBundleProvenance.feature_policy_version,
    }
    if any(provenance[key] != expected for key, expected in constants.items()):
        raise LightGbmContractError("feature provenance authority is invalid")
    if provenance["optionalFeatureGroups"] != []:
        raise LightGbmContractError("feature bundle v1 optional feature groups must be empty")
    return FeatureBundleProvenance(
        dataset_cutoff=_parse_canonical_utc(provenance["datasetCutoff"]),
        raw_session_start=_parse_iso_date(provenance["rawSessionStart"], "rawSessionStart"),
        raw_session_end=_parse_iso_date(provenance["rawSessionEnd"], "rawSessionEnd"),
        raw_session_count=_require_integer(provenance["rawSessionCount"], "rawSessionCount"),
        eligible_session_start=_parse_iso_date(
            provenance["eligibleSessionStart"], "eligibleSessionStart"
        ),
        eligible_session_end=_parse_iso_date(
            provenance["eligibleSessionEnd"], "eligibleSessionEnd"
        ),
        eligible_session_count=_require_integer(
            provenance["eligibleSessionCount"], "eligibleSessionCount"
        ),
        universe_schedule_sha256=_require_text(
            provenance["universeScheduleSha256"], "universeScheduleSha256"
        ),
        pit_input_sha256=_require_text(provenance["pitInputSha256"], "pitInputSha256"),
    )


def _parse_production_feature_provenance(
    value: object,
) -> ProductionFeatureBundleProvenance:
    if not isinstance(value, dict):
        raise LightGbmContractError("production feature provenance must be an object")
    provenance = cast(dict[str, object], value)
    if set(provenance) != _PRODUCTION_PROVENANCE_FIELDS:
        raise LightGbmContractError("production feature provenance is not closed")
    if (
        provenance["temporalPolicyVersion"] != "s5-temporal-policy-v2"
        or provenance["temporalQuality"] != "RECONSTRUCTED_FIXED_LAG"
        or provenance["universePolicyVersion"] != "top30-plus-132030-v1"
        or provenance["optionalFeatureGroups"] != []
    ):
        raise LightGbmContractError("production temporal authority is invalid")
    base_value = dict(provenance)
    for key in (
        "temporalPolicyVersion",
        "temporalQuality",
        "sourceBundleSetSha256",
        "sourcePolicySetSha256",
    ):
        del base_value[key]
    base_value["universePolicyVersion"] = FeatureBundleProvenance.universe_policy_version
    return ProductionFeatureBundleProvenance(
        base=_parse_feature_provenance(base_value),
        source_bundle_set_sha256=_require_text(
            provenance["sourceBundleSetSha256"], "sourceBundleSetSha256"
        ),
        source_policy_set_sha256=_require_text(
            provenance["sourcePolicySetSha256"], "sourcePolicySetSha256"
        ),
    )


def _canonical_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _canonical_evidence_time(value: datetime) -> str:
    if value.tzinfo is None:
        raise LightGbmContractError("PIT input evidence timestamp must be timezone aware")
    return _canonical_utc(value)


def _validate_pit_receipt(available_at: datetime, revision: str, digest: str) -> None:
    if not revision:
        raise LightGbmContractError("PIT input source revision is invalid")
    _require_sha256(digest, "PIT input source")
    _canonical_evidence_time(available_at)


def _canonical_domain_sha256(domain: bytes, value: object) -> str:
    try:
        payload = canonical_json_bytes(value)
    except (TypeError, ValueError) as error:
        raise LightGbmContractError("PIT provenance contains an unsupported value") from error
    return hashlib.sha256(domain + payload).hexdigest()


def _parse_canonical_utc(value: object) -> datetime:
    text = _require_text(value, "datasetCutoff")
    if not text.endswith("Z"):
        raise LightGbmContractError("feature provenance cutoff must use canonical UTC")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError:
        raise LightGbmContractError("feature provenance cutoff is invalid") from None
    if _canonical_utc(parsed) != text:
        raise LightGbmContractError("feature provenance cutoff must use canonical UTC")
    return parsed


def _parse_iso_date(value: object, field: str) -> date:
    text = _require_text(value, field)
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise LightGbmContractError(f"feature provenance {field} is invalid") from None
    if parsed.isoformat() != text:
        raise LightGbmContractError(f"feature provenance {field} is invalid")
    return parsed


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise LightGbmContractError(f"feature manifest {field} must be text")
    return value


def _require_integer(value: object, field: str) -> int:
    if type(value) is not int:
        raise LightGbmContractError(f"feature manifest {field} must be an integer")
    return value


def _require_sha256(value: str, field: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise LightGbmContractError(f"feature {field} SHA-256 is invalid")


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
