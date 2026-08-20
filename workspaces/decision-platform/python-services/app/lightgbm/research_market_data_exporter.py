"""Provider-free S5 source adoption into the neutral S5.7 market-data archive.

This module is deliberately kept inside the historical LightGBM research boundary.  It may
understand the S5 source bundle, while every operational consumer imports only
``app.data.market_data``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from typing import Any, Sequence, cast

import pyarrow as pa
import pyarrow.parquet as pq

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.market_data.archive import (
    HISTORICAL_PROVIDER_INTENT_COUNT,
    HISTORICAL_UNIVERSE_UNION_COUNT,
    MAX_ARTIFACT_BYTES,
    MAX_MANIFEST_BYTES,
    OPERATIONAL_HISTORY_MAX,
    RESEARCH_HISTORY_MAX,
    SEED_CONTRACT_ID,
    SOURCE_CHUNK_COUNT,
    SOURCE_SESSION_COUNT,
    MarketDataArtifact,
    archive_digest,
    read_market_data_archive,
)
from app.lightgbm.bootstrap_executor import (
    _build_index_evidence,
    _derive_universes,
    _load_ecos_rows,
    _load_kis_rows,
)
from app.lightgbm.errors import LightGbmContractError
from app.lightgbm.pit_calendar import KST, MonthlyUniverseSchedule
from app.lightgbm.source_bundle import SourceBundle, SourceChunkReceipt, read_source_bundle
from app.lightgbm.temporal import receipt_set_sha256
from app.lightgbm.universe import MonthlyUniverse, validate_horizon_union
from app.rag.safe_io import RagSafeIoError, write_approved_new_file


_PROGRESS_FILENAME = "progress.jsonl"
_PARQUET_ROW_GROUP_SIZE = 8_192
_ARTIFACT_PATHS = {
    "BARS": "bars/bars-v1.parquet",
    "INDICES": "indices/indices-v1.parquet",
    "MACRO": "macro/macro-v1.parquet",
    "UNIVERSES": "universes/universes-v1.parquet",
}


@dataclass(frozen=True, slots=True)
class SeedExportResult:
    output_root: Path
    manifest_sha256: str
    archive_sha256: str
    source_manifest_sha256: str
    source_chunk_count: int
    provider_intent_count_before: int
    provider_intent_count_after: int
    row_counts: dict[str, int]
    no_op: bool


def export_research_source_to_market_data(
    *, source_root: Path, expected_source_manifest_sha256: str, output_root: Path
) -> SeedExportResult:
    """Verify source-only evidence and publish four normalized artifacts manifest-last."""

    intents_before = _provider_intent_count(source_root)
    if intents_before != HISTORICAL_PROVIDER_INTENT_COUNT:
        raise LightGbmContractError("historical provider INTENT count drifted before adoption")
    bundle = read_source_bundle(
        approved_root=source_root,
        expected_manifest_sha256=expected_source_manifest_sha256,
    )
    if len(bundle.chunks) != SOURCE_CHUNK_COUNT:
        raise LightGbmContractError("S5.7 adoption requires the sealed exact-7,218 source")

    chunks = _chunk_index(bundle)
    schedules, raw_sessions = _derive_schedules(chunks)
    packet_view = cast(
        Any,
        SimpleNamespace(
            schedules=schedules,
            window=SimpleNamespace(raw_sessions=raw_sessions),
        ),
    )
    universes, listing_market = _derive_universes(
        packet=packet_view,
        source_root=source_root,
        chunks=chunks,
    )
    identities = validate_horizon_union(universes)
    if len(identities) != HISTORICAL_UNIVERSE_UNION_COUNT:
        raise LightGbmContractError("historical monthly universe union is not exact 270")

    tables = {
        "BARS": _bars_table(
            source_root=source_root,
            bundle=bundle,
            universes=universes,
        ),
        "INDICES": _indices_table(
            packet_view=packet_view,
            source_root=source_root,
            chunks=chunks,
        ),
        "MACRO": _macro_table(source_root=source_root, bundle=bundle),
        "UNIVERSES": _universes_table(
            universes=universes,
            schedules=schedules,
            chunks=chunks,
            listing_market=listing_market,
        ),
    }
    payloads = {kind: _parquet_bytes(table) for kind, table in tables.items()}
    artifacts = tuple(
        _artifact_receipt(kind=kind, table=tables[kind], payload=payloads[kind])
        for kind in sorted(tables)
    )
    archive_sha256 = archive_digest(artifacts)
    source_manifest = cast(dict[str, object], json.loads(bundle.manifest_bytes))
    manifest_bytes = canonical_json_bytes(
        {
            "archiveSha256": archive_sha256,
            "artifacts": [_artifact_dict(artifact) for artifact in artifacts],
            "contractId": SEED_CONTRACT_ID,
            "createdAt": source_manifest["createdAt"],
            "hardlinkUsed": False,
            "historicalProviderIntentCount": HISTORICAL_PROVIDER_INTENT_COUNT,
            "historicalUniverseUnionCount": HISTORICAL_UNIVERSE_UNION_COUNT,
            "operationalHistoryMaxSessions": OPERATIONAL_HISTORY_MAX,
            "providerCallsDuringAdoption": 0,
            "rawChunkCopied": False,
            "researchHistoryMaxSessions": RESEARCH_HISTORY_MAX,
            "sourceChunkCount": SOURCE_CHUNK_COUNT,
            "sourceManifestSha256": bundle.manifest_sha256,
            "sourcePathPersisted": False,
            "sourceSessionCount": SOURCE_SESSION_COUNT,
            "strictPitPerformanceClaimAllowed": False,
            "temporalQuality": "RECONSTRUCTED_FIXED_LAG",
        }
    )
    no_op = _publish_archive(
        output_root=output_root,
        manifest_bytes=manifest_bytes,
        artifacts=artifacts,
        payloads=payloads,
        expected_source_manifest_sha256=bundle.manifest_sha256,
    )
    verified = read_market_data_archive(output_root)
    intents_after = _provider_intent_count(source_root)
    if intents_after != intents_before:
        raise LightGbmContractError("provider INTENT count changed during provider-free adoption")
    return SeedExportResult(
        output_root=output_root,
        manifest_sha256=verified.manifest_sha256,
        archive_sha256=verified.archive_sha256,
        source_manifest_sha256=verified.source_manifest_sha256,
        source_chunk_count=len(bundle.chunks),
        provider_intent_count_before=intents_before,
        provider_intent_count_after=intents_after,
        row_counts={kind: table.num_rows for kind, table in tables.items()},
        no_op=no_op,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="provider-free S5.7B market-data seed exporter")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-manifest-sha256", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    result = export_research_source_to_market_data(
        source_root=args.source_root,
        expected_source_manifest_sha256=args.source_manifest_sha256,
        output_root=args.output_root,
    )
    print(
        canonical_json_bytes(
            {
                "archiveSha256": result.archive_sha256,
                "manifestSha256": result.manifest_sha256,
                "noOp": result.no_op,
                "providerCalls": 0,
                "providerIntentCountAfter": result.provider_intent_count_after,
                "providerIntentCountBefore": result.provider_intent_count_before,
                "rowCounts": result.row_counts,
                "sourceChunkCount": result.source_chunk_count,
                "sourceManifestSha256": result.source_manifest_sha256,
            }
        ).decode("utf-8")
    )
    return 0


def _chunk_index(
    bundle: SourceBundle,
) -> dict[tuple[str, date], SourceChunkReceipt]:
    indexed: dict[tuple[str, date], SourceChunkReceipt] = {}
    for chunk in bundle.chunks:
        if chunk.source_id != "KRX":
            continue
        key = (chunk.operation_id, chunk.temporal.observation_date)
        if key in indexed:
            raise LightGbmContractError("KRX source operation/session is duplicated")
        indexed[key] = chunk
    return indexed


def _derive_schedules(
    chunks: dict[tuple[str, date], SourceChunkReceipt],
) -> tuple[tuple[MonthlyUniverseSchedule, ...], tuple[date, ...]]:
    daily_services = ("stk_bydd_trd", "ksq_bydd_trd", "kospi_dd_trd", "kosdaq_dd_trd")
    service_dates = [
        {day for operation, day in chunks if operation == service} for service in daily_services
    ]
    if any(dates != service_dates[0] for dates in service_dates[1:]):
        raise LightGbmContractError("KRX daily source session sets disagree")
    raw_sessions = tuple(sorted(service_dates[0]))
    if len(raw_sessions) != SOURCE_SESSION_COUNT:
        raise LightGbmContractError("neutral seed requires exact 1,072 KRX sessions")
    selection_services = ("stk_isu_base_info", "ksq_isu_base_info", "etf_bydd_trd")
    selection_sets = [
        {day for operation, day in chunks if operation == service} for service in selection_services
    ]
    if any(dates != selection_sets[0] for dates in selection_sets[1:]):
        raise LightGbmContractError("monthly universe source session sets disagree")
    schedules: list[MonthlyUniverseSchedule] = []
    for selection in sorted(selection_sets[0]):
        month = _next_month(selection)
        effective = [day for day in raw_sessions if day.strftime("%Y-%m") == month]
        trailing = tuple(day for day in raw_sessions if day <= selection)[-20:]
        if not effective or len(trailing) != 20 or trailing[-1] != selection:
            raise LightGbmContractError("monthly universe schedule is incomplete")
        first = effective[0]
        schedules.append(
            MonthlyUniverseSchedule(
                effective_month=month,
                first_effective_session=first,
                evidence_cutoff=datetime.combine(first, time(8, 10), tzinfo=KST),
                selection_session=selection,
                trailing_sessions=trailing,
            )
        )
    if len(schedules) != 51:
        raise LightGbmContractError("neutral seed requires exact 51 monthly universes")
    return tuple(schedules), raw_sessions


def _bars_table(
    *, source_root: Path, bundle: SourceBundle, universes: Sequence[MonthlyUniverse]
) -> pa.Table:
    allowed_symbols = {symbol for universe in universes for symbol in universe.symbols}
    if len(allowed_symbols) != HISTORICAL_UNIVERSE_UNION_COUNT:
        raise LightGbmContractError("KIS export scope must equal the historical union 270")
    rows: dict[tuple[str, date], dict[str, object]] = {}
    query_symbols: set[str] = set()
    for chunk in bundle.chunks:
        if chunk.source_id != "KIS":
            continue
        query_symbol = chunk.query_key.split(":", 1)[0]
        query_symbols.add(query_symbol)
        if query_symbol not in allowed_symbols:
            raise LightGbmContractError("KIS source escaped the historical universe union")
        for bar in _load_kis_rows(source_root, chunk):
            if bar.symbol != query_symbol:
                raise LightGbmContractError("KIS row symbol disagrees with sealed query")
            value = {
                "symbol": bar.symbol,
                "sessionDate": bar.date,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "currency": "KRW",
                "temporalQuality": chunk.temporal.temporal_quality.value,
                "sourceReceiptSha256": chunk.content_sha256,
            }
            key = (bar.symbol, bar.date)
            prior = rows.get(key)
            if prior is not None and _bar_value_tuple(prior) != _bar_value_tuple(value):
                raise LightGbmContractError("conflicting KIS bar vintages cannot be normalized")
            if prior is None:
                rows[key] = value
    if query_symbols != allowed_symbols:
        raise LightGbmContractError("KIS source does not cover the exact historical union")
    ordered = [rows[key] for key in sorted(rows)]
    return pa.Table.from_pylist(
        ordered,
        schema=pa.schema(
            [
                pa.field("symbol", pa.string(), nullable=False),
                pa.field("sessionDate", pa.date32(), nullable=False),
                pa.field("open", pa.int64(), nullable=False),
                pa.field("high", pa.int64(), nullable=False),
                pa.field("low", pa.int64(), nullable=False),
                pa.field("close", pa.int64(), nullable=False),
                pa.field("volume", pa.int64(), nullable=False),
                pa.field("currency", pa.string(), nullable=False),
                pa.field("temporalQuality", pa.string(), nullable=False),
                pa.field("sourceReceiptSha256", pa.string(), nullable=False),
            ]
        ),
    )


def _indices_table(
    *,
    packet_view: Any,
    source_root: Path,
    chunks: dict[tuple[str, date], SourceChunkReceipt],
) -> pa.Table:
    evidence = _build_index_evidence(packet_view, source_root, chunks)
    rows = [
        {
            "indexId": item.market,
            "sessionDate": item.session_date,
            "close": item.adjusted_close,
            "temporalQuality": item.receipt.temporal_quality.value,
            "sourceReceiptSha256": item.receipt.snapshot_sha256,
        }
        for item in evidence
    ]
    rows.sort(key=lambda row: (cast(date, row["sessionDate"]), cast(str, row["indexId"])))
    if len(rows) != SOURCE_SESSION_COUNT * 2:
        raise LightGbmContractError("index history must contain two exact 1,072-session series")
    return pa.Table.from_pylist(
        rows,
        schema=pa.schema(
            [
                pa.field("indexId", pa.string(), nullable=False),
                pa.field("sessionDate", pa.date32(), nullable=False),
                pa.field("close", pa.float64(), nullable=False),
                pa.field("temporalQuality", pa.string(), nullable=False),
                pa.field("sourceReceiptSha256", pa.string(), nullable=False),
            ]
        ),
    )


def _macro_table(*, source_root: Path, bundle: SourceBundle) -> pa.Table:
    rows: dict[tuple[str, date], dict[str, object]] = {}
    for chunk in bundle.chunks:
        if chunk.source_id != "ECOS":
            continue
        for observation in _load_ecos_rows(source_root, chunk):
            day = date.fromisoformat(observation.time)
            value = {
                "seriesId": chunk.operation_id,
                "observationDate": day,
                "availableAt": chunk.temporal.effective_at,
                "value": str(Decimal(observation.value)),
                "temporalQuality": chunk.temporal.temporal_quality.value,
                "sourceReceiptSha256": chunk.content_sha256,
            }
            key = (chunk.operation_id, day)
            prior = rows.get(key)
            if prior is not None and prior["value"] != value["value"]:
                raise LightGbmContractError("conflicting ECOS values cannot be normalized")
            if prior is None:
                rows[key] = value
    ordered = [rows[key] for key in sorted(rows)]
    return pa.Table.from_pylist(
        ordered,
        schema=pa.schema(
            [
                pa.field("seriesId", pa.string(), nullable=False),
                pa.field("observationDate", pa.date32(), nullable=False),
                pa.field("availableAt", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("value", pa.string(), nullable=False),
                pa.field("temporalQuality", pa.string(), nullable=False),
                pa.field("sourceReceiptSha256", pa.string(), nullable=False),
            ]
        ),
    )


def _universes_table(
    *,
    universes: Sequence[MonthlyUniverse],
    schedules: Sequence[MonthlyUniverseSchedule],
    chunks: dict[tuple[str, date], SourceChunkReceipt],
    listing_market: dict[tuple[str, str], str],
) -> pa.Table:
    schedule_by_month = {schedule.effective_month: schedule for schedule in schedules}
    rows: list[dict[str, object]] = []
    for universe in universes:
        schedule = schedule_by_month[universe.effective_month]
        receipts = [
            chunks[(operation, day)].temporal
            for day in schedule.trailing_sessions
            for operation in ("stk_bydd_trd", "ksq_bydd_trd")
        ]
        receipts.extend(
            chunks[(operation, schedule.selection_session)].temporal
            for operation in ("stk_isu_base_info", "ksq_isu_base_info", "etf_bydd_trd")
        )
        provenance = receipt_set_sha256(receipts)
        for rank, (identity, symbol) in enumerate(
            zip(universe.instrument_ids, universe.symbols, strict=True), start=1
        ):
            rows.append(
                {
                    "membershipMonth": universe.effective_month,
                    "selectionSession": universe.selection_session,
                    "effectiveFromSession": schedule.first_effective_session,
                    "instrumentId": identity,
                    "symbol": symbol,
                    "market": listing_market[(identity, universe.effective_month)],
                    "rank": rank,
                    "isFixedMember": symbol == "132030",
                    "temporalQuality": "PROVIDER_AS_OF_NO_VINTAGE",
                    "sourceReceiptSha256": provenance,
                }
            )
    rows.sort(key=lambda row: (cast(str, row["membershipMonth"]), cast(int, row["rank"])))
    if len(rows) != len(universes) * 31:
        raise LightGbmContractError("universe archive must contain exact 31 rows per month")
    return pa.Table.from_pylist(
        rows,
        schema=pa.schema(
            [
                pa.field("membershipMonth", pa.string(), nullable=False),
                pa.field("selectionSession", pa.date32(), nullable=False),
                pa.field("effectiveFromSession", pa.date32(), nullable=False),
                pa.field("instrumentId", pa.string(), nullable=False),
                pa.field("symbol", pa.string(), nullable=False),
                pa.field("market", pa.string(), nullable=False),
                pa.field("rank", pa.int16(), nullable=False),
                pa.field("isFixedMember", pa.bool_(), nullable=False),
                pa.field("temporalQuality", pa.string(), nullable=False),
                pa.field("sourceReceiptSha256", pa.string(), nullable=False),
            ]
        ),
    )


def _artifact_receipt(*, kind: str, table: pa.Table, payload: bytes) -> MarketDataArtifact:
    date_column = {
        "BARS": "sessionDate",
        "INDICES": "sessionDate",
        "MACRO": "observationDate",
        "UNIVERSES": "effectiveFromSession",
    }[kind]
    dates = cast(list[date], table[date_column].to_pylist())
    quality = {
        "BARS": "RECONSTRUCTED_FIXED_LAG",
        "INDICES": "PROVIDER_AS_OF_NO_VINTAGE",
        "MACRO": "RECONSTRUCTED_FIXED_LAG",
        "UNIVERSES": "PROVIDER_AS_OF_NO_VINTAGE",
    }[kind]
    return MarketDataArtifact(
        kind=kind,
        relative_path=_ARTIFACT_PATHS[kind],
        sha256=hashlib.sha256(payload).hexdigest(),
        row_count=table.num_rows,
        first_session_date=min(dates),
        last_session_date=max(dates),
        temporal_quality=quality,
    )


def _artifact_dict(artifact: MarketDataArtifact) -> dict[str, object]:
    return {
        "firstSessionDate": artifact.first_session_date.isoformat(),
        "kind": artifact.kind,
        "lastSessionDate": artifact.last_session_date.isoformat(),
        "relativePath": artifact.relative_path,
        "rowCount": artifact.row_count,
        "sha256": artifact.sha256,
        "temporalQuality": artifact.temporal_quality,
    }


def _publish_archive(
    *,
    output_root: Path,
    manifest_bytes: bytes,
    artifacts: Sequence[MarketDataArtifact],
    payloads: dict[str, bytes],
    expected_source_manifest_sha256: str,
) -> bool:
    if output_root.exists():
        existing = read_market_data_archive(output_root)
        if existing.source_manifest_sha256 != expected_source_manifest_sha256:
            raise LightGbmContractError("existing neutral archive binds another source manifest")
        if existing.manifest_sha256 != hashlib.sha256(manifest_bytes).hexdigest():
            raise LightGbmContractError(
                "existing neutral archive conflicts with deterministic export"
            )
        return True
    _make_private_tree(output_root)
    try:
        for artifact in artifacts:
            write_approved_new_file(
                approved_root=output_root,
                relative_path=artifact.relative_path,
                content=payloads[artifact.kind],
                max_bytes=MAX_ARTIFACT_BYTES,
            )
        write_approved_new_file(
            approved_root=output_root,
            relative_path="manifest.json",
            content=manifest_bytes,
            max_bytes=MAX_MANIFEST_BYTES,
        )
    except RagSafeIoError as error:
        raise LightGbmContractError("neutral archive publication failed closed") from error
    return False


def _make_private_tree(output_root: Path) -> None:
    if not output_root.is_absolute() or ".." in output_root.parts or output_root.anchor != "/":
        raise LightGbmContractError("neutral archive root must be an absolute normalized path")
    parent = output_root.parent
    current = Path("/")
    for component in parent.parts[1:]:
        current /= component
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise LightGbmContractError("neutral archive parent contains a symlink")
    output_root.mkdir(mode=0o700)
    os.chmod(output_root, 0o700)
    for directory in sorted({Path(path).parts[0] for path in _ARTIFACT_PATHS.values()}):
        (output_root / directory).mkdir(mode=0o700)


def _provider_intent_count(source_root: Path) -> int:
    path = source_root / _PROGRESS_FILENAME
    count = 0
    with path.open("rb") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise LightGbmContractError("historical progress journal is invalid") from error
            if event.get("state") == "INTENT":
                count += 1
    return count


def _next_month(day: date) -> str:
    if day.month == 12:
        return f"{day.year + 1:04d}-01"
    return f"{day.year:04d}-{day.month + 1:02d}"


def _bar_value_tuple(row: dict[str, object]) -> tuple[object, ...]:
    return tuple(row[field] for field in ("open", "high", "low", "close", "volume"))


def _parquet_bytes(table: pa.Table) -> bytes:
    sink = BytesIO()
    pq.write_table(  # type: ignore[no-untyped-call]
        table.replace_schema_metadata(None),
        sink,
        version="2.6",
        data_page_version="1.0",
        compression="zstd",
        compression_level=3,
        use_dictionary=False,
        row_group_size=_PARQUET_ROW_GROUP_SIZE,
        write_statistics=True,
    )
    payload = sink.getvalue()
    if not payload or len(payload) > MAX_ARTIFACT_BYTES:
        raise LightGbmContractError("neutral Parquet artifact exceeds its physical bound")
    return payload
