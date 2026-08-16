"""S5.6 provider projection을 fail-stop 순서로 봉인하고 feature bundle v2를 만든다."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import hashlib
from io import BytesIO
import os
from pathlib import Path
import stat
from typing import Protocol, TypeVar

import pyarrow as pa
import pyarrow.parquet as pq

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.ecos.models import ECOSObservation
from app.data.ecos.series_registry import ECOSSeries
from app.data.kis.parsers import DailyBar
from app.lightgbm.bootstrap_control import BootstrapLedger, BootstrapPhase
from app.lightgbm.bootstrap_journal import BootstrapJournal
from app.lightgbm.bootstrap_packet import BootstrapPacket
from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError
from app.lightgbm.feature_artifact import (
    FeatureArtifact,
    FeatureBundleProvenance,
    ProductionFeatureBundle,
    ProductionFeatureBundleProvenance,
    build_production_feature_manifest,
    feature_table_from_rows,
    logical_dataset_hash,
    logical_universe_schedule_sha256,
    read_production_feature_bundle,
    write_feature_parquet,
)
from app.lightgbm.features import (
    IndexEvidence,
    MacroObservation,
    ProductionPriceEvidence,
    build_production_core_feature_rows,
)
from app.lightgbm.production_policy import (
    KIS_OPERATION,
    SecurityClassification,
    classify_krx_security,
    require_standard_stock_identity,
)
from app.lightgbm.source_bundle import (
    MAX_MANIFEST_BYTES,
    SOURCE_BYTE_CAPS,
    SOURCE_MANIFEST_FILENAME,
    SourceBundle,
    SourceChunkReceipt,
    build_source_manifest,
    read_source_bundle,
)
from app.lightgbm.temporal import (
    AvailabilityBasis,
    RevisionBasis,
    TemporalQuality,
    TemporalReceipt,
    next_session_evidence_clock,
    next_xkrx_evidence_clock,
)
from app.lightgbm.universe import (
    FIXED_ETF_SYMBOL,
    MonthlyUniverse,
    ProductionUniverseObservation,
    select_production_monthly_universe,
    validate_horizon_union,
)
from app.rag.safe_io import (
    RagSafeIoError,
    read_approved_regular_file,
    write_approved_new_file,
)


_DAILY_KRX_SERVICES = ("stk_bydd_trd", "ksq_bydd_trd", "kospi_dd_trd", "kosdaq_dd_trd")
_MONTHLY_KRX_SERVICES = ("stk_isu_base_info", "ksq_isu_base_info", "etf_bydd_trd")
_PARQUET_ROW_GROUP_SIZE = 65_536
_SOURCE_POLICY_SET_SHA256 = hashlib.sha256(
    b"s5-source-policy-set-v1\x00"
    + canonical_json_bytes(
        {
            "historicalMode": "HISTORICAL_REPLAY_RECONSTRUCTED",
            "temporalPolicyVersion": "s5-temporal-policy-v2",
            "universePolicyVersion": "top30-plus-132030-v1",
            "strictProviderPITClaim": False,
        }
    )
).hexdigest()
_CallT = TypeVar("_CallT")


class KrxBootstrapProvider(Protocol):
    """한 호출이 exact KRX service/date physical GET 하나인 provider port."""

    def fetch(self, *, service: str, session_date: date) -> tuple[dict[str, str], ...]: ...


class KisBootstrapProvider(Protocol):
    """OAuth 준비와 기간별시세 한 page를 분리해 물리 예산을 셀 수 있는 port."""

    def prepare_access_token(self) -> None: ...

    def fetch_page(
        self, *, symbol: str, start: date, end: date
    ) -> tuple[DailyBar, ...]: ...


class EcosBootstrapProvider(Protocol):
    """한 호출이 exact series/date chunk StatisticSearch GET 하나인 port."""

    def fetch(
        self, *, series: ECOSSeries, start: date, end: date
    ) -> tuple[ECOSObservation, ...]: ...


@dataclass(frozen=True, slots=True)
class BootstrapAcquisition:
    """검증된 source bundle과 feature materialization에 필요한 typed evidence."""

    source_bundle: SourceBundle
    universes: tuple[MonthlyUniverse, ...]
    prices: tuple[ProductionPriceEvidence, ...]
    indices: tuple[IndexEvidence, ...]
    macro: tuple[MacroObservation, ...]
    listing_market_by_identity: Mapping[str, str]
    physical_calls: int


@dataclass(frozen=True, slots=True)
class BootstrapMaterialization:
    """source와 feature trust anchor를 함께 반환하는 S5.6A 완료 receipt."""

    acquisition: BootstrapAcquisition
    feature_bundle: ProductionFeatureBundle


def execute_bootstrap_acquisition(
    *,
    packet: BootstrapPacket,
    source_root: Path,
    krx: KrxBootstrapProvider,
    kis: KisBootstrapProvider,
    ecos: EcosBootstrapProvider,
    ecos_series: Sequence[ECOSSeries],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    resume: bool = False,
) -> BootstrapAcquisition:
    """KRX→KIS→ECOS 순서와 retry 0을 지키며 allowlisted Parquet만 manifest-last publish한다."""

    _prepare_private_bundle_root(source_root, resume=resume)
    journal = BootstrapJournal(source_root)
    ledger = BootstrapLedger(packet.budget)
    ledger.receipts.extend(journal.consumed_receipts)
    chunks: list[SourceChunkReceipt] = []
    krx_rows: dict[tuple[str, date], tuple[dict[str, str], ...]] = {}
    krx_receipts: dict[tuple[str, date], TemporalReceipt] = {}
    for session in packet.window.raw_sessions:
        for service in _DAILY_KRX_SERVICES:
            rows, receipt = _fetch_and_seal_krx(
                ledger=ledger,
                source_root=source_root,
                provider=krx,
                service=service,
                session=session,
                clock=clock,
                journal=journal,
            )
            krx_rows[(service, session)] = rows
            krx_receipts[(service, session)] = receipt.temporal
            chunks.append(receipt)
    for schedule in packet.schedules:
        for service in _MONTHLY_KRX_SERVICES:
            key = (service, schedule.selection_session)
            if key in krx_rows:
                continue
            rows, receipt = _fetch_and_seal_krx(
                ledger=ledger,
                source_root=source_root,
                provider=krx,
                service=service,
                session=schedule.selection_session,
                clock=clock,
                journal=journal,
            )
            krx_rows[key] = rows
            krx_receipts[key] = receipt.temporal
            chunks.append(receipt)
    ledger.advance(BootstrapPhase.KRX)

    universes, listing_market_by_identity = _derive_universes(
        packet=packet,
        rows=krx_rows,
        receipts=krx_receipts,
    )
    identities = validate_horizon_union(universes)
    symbol_by_identity = {
        identity: symbol
        for universe in universes
        for identity, symbol in zip(universe.instrument_ids, universe.symbols, strict=True)
    }
    if set(identities) != set(symbol_by_identity):
        raise DatasetUnavailable("DATASET_UNAVAILABLE: universe identity mapping is incomplete")

    token_query_hash = _query_sha256({"operation": "oauth2/tokenP", "mode": "live"})
    if not journal.token_completed(token_query_hash):
        _journaled_call(
            ledger=ledger,
            journal=journal,
            provider="KIS",
            operation="oauth2/tokenP",
            query_hash=token_query_hash,
            call=kis.prepare_access_token,
            finalize=lambda _: None,
        )
    prices: list[ProductionPriceEvidence] = []
    for identity in identities:
        symbol = symbol_by_identity[identity]
        symbol_prices, symbol_chunks = _fetch_kis_symbol(
            ledger=ledger,
            source_root=source_root,
            provider=kis,
            identity=identity,
            symbol=symbol,
            raw_sessions=packet.window.raw_sessions,
            clock=clock,
            journal=journal,
        )
        prices.extend(symbol_prices)
        chunks.extend(symbol_chunks)
    ledger.advance(BootstrapPhase.KIS)

    macro: list[MacroObservation] = []
    for series in _validate_ecos_series(ecos_series):
        series_rows, series_chunks = _fetch_ecos_series(
            ledger=ledger,
            source_root=source_root,
            provider=ecos,
            series=series,
            raw_start=packet.window.raw_sessions[0],
            raw_end=packet.window.raw_sessions[-1],
            clock=clock,
            journal=journal,
        )
        macro.extend(series_rows)
        chunks.extend(series_chunks)
    ledger.advance(BootstrapPhase.ECOS)

    indices = _build_index_evidence(packet, krx_rows, krx_receipts)
    manifest = build_source_manifest(
        created_at=_clock_utc(clock), dataset_cutoff=_packet_cutoff(packet), chunks=chunks
    )
    _write_private_new_file(
        approved_root=source_root,
        relative_path=SOURCE_MANIFEST_FILENAME,
        content=manifest,
        max_bytes=MAX_MANIFEST_BYTES,
    )
    bundle = read_source_bundle(
        approved_root=source_root,
        expected_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
    )
    return BootstrapAcquisition(
        source_bundle=bundle,
        universes=universes,
        prices=tuple(sorted(prices, key=lambda item: (item.session_date, item.symbol))),
        indices=indices,
        macro=tuple(sorted(macro, key=lambda item: (item.series_id, item.observation_date))),
        listing_market_by_identity=dict(sorted(listing_market_by_identity.items())),
        physical_calls=len(ledger.receipts),
    )


def materialize_production_feature_bundle(
    *,
    packet: BootstrapPacket,
    acquisition: BootstrapAcquisition,
    feature_root: Path,
    resume: bool = False,
) -> ProductionFeatureBundle:
    """검증된 source bundle만 받아 monthly membership이 적용된 feature bundle v2를 publish한다."""

    _prepare_private_bundle_root(feature_root, chunks=False, resume=resume)
    memberships = {
        universe.effective_month: set(universe.instrument_ids) for universe in acquisition.universes
    }
    prices_by_identity: dict[str, list[ProductionPriceEvidence]] = defaultdict(list)
    for price in acquisition.prices:
        prices_by_identity[price.instrument_id].append(price)
    rows: list[Mapping[str, object]] = []
    eligible = set(packet.window.eligible_sessions)
    for identity in sorted(prices_by_identity):
        market = acquisition.listing_market_by_identity.get(identity)
        if market is None:
            raise DatasetUnavailable("DATASET_UNAVAILABLE: listing market evidence is missing")
        feature_rows = build_production_core_feature_rows(
            prices_by_identity[identity],
            acquisition.indices,
            acquisition.macro,
            listing_market=market,
            cutoff=_packet_cutoff(packet),
        )
        for row in feature_rows:
            if row.session_date not in eligible:
                continue
            month = f"{row.session_date.year:04d}-{row.session_date.month:02d}"
            if identity in memberships.get(month, set()):
                rows.append(row.as_mapping())
    if not rows:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: production feature rows are absent")
    rows.sort(key=lambda item: (item["sessionDate"], item["symbol"]))
    table = feature_table_from_rows(rows)
    parquet = write_feature_parquet(table)
    artifact = FeatureArtifact(
        table=table,
        parquet_sha256=hashlib.sha256(parquet).hexdigest(),
        logical_dataset_hash=logical_dataset_hash(table),
        physical_bytes=len(parquet),
        decoded_bytes=table.nbytes,
    )
    source_set_hash = hashlib.sha256(
        b"s5-source-bundle-set-v1\x00"
        + acquisition.source_bundle.manifest_sha256.encode("ascii")
    ).hexdigest()
    pit_input_hash = _production_pit_input_sha256(acquisition)
    base = FeatureBundleProvenance(
        dataset_cutoff=_packet_cutoff(packet),
        raw_session_start=packet.window.raw_sessions[0],
        raw_session_end=packet.window.raw_sessions[-1],
        raw_session_count=len(packet.window.raw_sessions),
        eligible_session_start=packet.window.eligible_sessions[0],
        eligible_session_end=packet.window.eligible_sessions[-1],
        eligible_session_count=len(packet.window.eligible_sessions),
        universe_schedule_sha256=logical_universe_schedule_sha256(packet.schedules),
        pit_input_sha256=pit_input_hash,
    )
    provenance = ProductionFeatureBundleProvenance(
        base=base,
        source_bundle_set_sha256=source_set_hash,
        source_policy_set_sha256=_SOURCE_POLICY_SET_SHA256,
    )
    manifest = build_production_feature_manifest(artifact, provenance=provenance)
    _write_private_new_file(
        approved_root=feature_root,
        relative_path="features.parquet",
        content=parquet,
        max_bytes=256 * 1024 * 1024,
    )
    _write_private_new_file(
        approved_root=feature_root,
        relative_path="manifest.json",
        content=manifest,
        max_bytes=1024 * 1024,
    )
    return read_production_feature_bundle(
        approved_root=feature_root,
        expected_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
    )


def execute_bootstrap_materialization(
    *,
    packet: BootstrapPacket,
    source_root: Path,
    feature_root: Path,
    krx: KrxBootstrapProvider,
    kis: KisBootstrapProvider,
    ecos: EcosBootstrapProvider,
    ecos_series: Sequence[ECOSSeries],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    resume: bool = False,
) -> BootstrapMaterialization:
    """Source manifest가 검증된 뒤에만 feature manifest를 기록하는 public S5.6A entrypoint."""

    acquisition = execute_bootstrap_acquisition(
        packet=packet,
        source_root=source_root,
        krx=krx,
        kis=kis,
        ecos=ecos,
        ecos_series=ecos_series,
        clock=clock,
        resume=resume,
    )
    feature_bundle = materialize_production_feature_bundle(
        packet=packet,
        acquisition=acquisition,
        feature_root=feature_root,
        resume=resume,
    )
    return BootstrapMaterialization(acquisition=acquisition, feature_bundle=feature_bundle)


def _fetch_and_seal_krx(
    *,
    ledger: BootstrapLedger,
    source_root: Path,
    provider: KrxBootstrapProvider,
    service: str,
    session: date,
    clock: Callable[[], datetime],
    journal: BootstrapJournal,
) -> tuple[tuple[dict[str, str], ...], SourceChunkReceipt]:
    query = {"service": service, "basDd": session.strftime("%Y%m%d")}
    query_hash = _query_sha256(query)
    existing = journal.completed_chunk(query_hash)
    if existing is not None:
        _require_reused_chunk(
            existing,
            source="KRX",
            operation=service,
            query_key=f"{service}:{session.isoformat()}",
        )
        return _load_string_rows(source_root, existing), existing

    def finalize(rows: tuple[dict[str, str], ...]) -> SourceChunkReceipt:
        if not rows:
            raise DatasetUnavailable("DATASET_UNAVAILABLE: KRX projection is empty")
        payload = _string_rows_parquet(rows)
        temporal = _temporal_receipt(
            source="KRX",
            operation=service,
            observation_date=session,
            retrieved_at=_clock_utc(clock),
            request_sha256=query_hash,
            snapshot_sha256=hashlib.sha256(payload).hexdigest(),
        )
        return _seal_projection(
            source_root=source_root,
            source="KRX",
            operation=service,
            query_key=f"{service}:{session.isoformat()}",
            rows=len(rows),
            payload=payload,
            temporal=temporal,
        )

    rows, receipt = _journaled_call(
        ledger=ledger,
        journal=journal,
        provider="KRX",
        operation=service,
        query_hash=query_hash,
        call=lambda: provider.fetch(service=service, session_date=session),
        finalize=finalize,
    )
    assert receipt is not None
    return rows, receipt


def _fetch_kis_symbol(
    *,
    ledger: BootstrapLedger,
    source_root: Path,
    provider: KisBootstrapProvider,
    identity: str,
    symbol: str,
    raw_sessions: Sequence[date],
    clock: Callable[[], datetime],
    journal: BootstrapJournal,
) -> tuple[tuple[ProductionPriceEvidence, ...], tuple[SourceChunkReceipt, ...]]:
    start, cursor_end = raw_sessions[0], raw_sessions[-1]
    seen: dict[date, ProductionPriceEvidence] = {}
    receipts: list[SourceChunkReceipt] = []
    page_number = 0
    while cursor_end >= start:
        page_number += 1
        query = {
            "operation": KIS_OPERATION,
            "symbol": symbol,
            "start": start.isoformat(),
            "end": cursor_end.isoformat(),
            "adjusted": "0",
            "page": page_number,
        }
        query_hash = _query_sha256(query)
        query_key = f"{symbol}:{start.isoformat()}:{cursor_end.isoformat()}:{page_number}"
        existing = journal.completed_chunk(query_hash)
        if existing is not None:
            _require_reused_chunk(
                existing,
                source="KIS",
                operation=KIS_OPERATION,
                query_key=query_key,
            )
            page = _load_kis_rows(source_root, existing)
            chunk = existing
        else:

            def finalize(bars: tuple[DailyBar, ...]) -> SourceChunkReceipt:
                page_rows = tuple(
                    sorted(
                        (bar for bar in bars if start <= bar.date <= cursor_end),
                        key=lambda value: value.date,
                    )
                )
                if (
                    not page_rows
                    or len(page_rows) > 100
                    or len({bar.date for bar in page_rows}) != len(page_rows)
                ):
                    raise DatasetUnavailable("KIS_HISTORY_UNAVAILABLE")
                payload = _kis_rows_parquet(page_rows)
                digest = hashlib.sha256(payload).hexdigest()
                temporal = _temporal_receipt(
                    source="KIS",
                    operation=KIS_OPERATION,
                    observation_date=page_rows[-1].date,
                    retrieved_at=_clock_utc(clock),
                    request_sha256=query_hash,
                    snapshot_sha256=digest,
                )
                return _seal_projection(
                    source_root=source_root,
                    source="KIS",
                    operation=KIS_OPERATION,
                    query_key=query_key,
                    rows=len(page_rows),
                    payload=payload,
                    temporal=temporal,
                )

            bars, new_chunk = _journaled_call(
                ledger=ledger,
                journal=journal,
                provider="KIS",
                operation=KIS_OPERATION,
                query_hash=query_hash,
                call=lambda: provider.fetch_page(symbol=symbol, start=start, end=cursor_end),
                finalize=finalize,
            )
            assert new_chunk is not None
            chunk = new_chunk
            page = tuple(
                sorted(
                    (bar for bar in bars if start <= bar.date <= cursor_end),
                    key=lambda value: value.date,
                )
            )
        payload_digest = chunk.content_sha256
        chunk_temporal = chunk.temporal
        receipts.append(chunk)
        for bar in page:
            if bar.date in seen:
                raise LightGbmContractError("SOURCE_SNAPSHOT_CONFLICT")
            row_receipt = _temporal_receipt(
                source="KIS",
                operation=KIS_OPERATION,
                observation_date=bar.date,
                retrieved_at=chunk_temporal.retrieved_at,
                request_sha256=query_hash,
                snapshot_sha256=payload_digest,
            )
            seen[bar.date] = ProductionPriceEvidence(
                instrument_id=identity,
                symbol=symbol,
                session_date=bar.date,
                adjusted_open=float(bar.open),
                adjusted_close=float(bar.close),
                volume=float(bar.volume),
                flng_cls_code=bar.flng_cls_code,
                prtt_rate=float(bar.prtt_rate),
                mod_yn=bar.mod_yn,
                revl_issu_reas=bar.revl_issu_reas,
                receipt=row_receipt,
            )
        oldest = page[0].date
        if oldest <= start:
            break
        if len(page) < 100:
            raise DatasetUnavailable("KIS_HISTORY_UNAVAILABLE")
        cursor_end = oldest - timedelta(days=1)
    if set(seen) != set(raw_sessions):
        raise DatasetUnavailable("KIS_HISTORY_UNAVAILABLE")
    return tuple(seen[day] for day in sorted(seen)), tuple(receipts)


def _fetch_ecos_series(
    *,
    ledger: BootstrapLedger,
    source_root: Path,
    provider: EcosBootstrapProvider,
    series: ECOSSeries,
    raw_start: date,
    raw_end: date,
    clock: Callable[[], datetime],
    journal: BootstrapJournal,
) -> tuple[tuple[MacroObservation, ...], tuple[SourceChunkReceipt, ...]]:
    start = raw_start - timedelta(days=366) if series.series_id == "policy-rate" else raw_start
    rows: dict[date, MacroObservation] = {}
    receipts: list[SourceChunkReceipt] = []
    chunk_start = start
    while chunk_start <= raw_end:
        chunk_end = min(chunk_start + timedelta(days=365), raw_end)
        query = {
            "operation": f"{series.stat_code}/{series.item_code1}/{series.cycle}",
            "start": chunk_start.isoformat(),
            "end": chunk_end.isoformat(),
        }
        query_hash = _query_sha256(query)
        operation = str(query["operation"])
        query_key = f"{series.series_id}:{chunk_start.isoformat()}:{chunk_end.isoformat()}"
        existing = journal.completed_chunk(query_hash)
        if existing is not None:
            _require_reused_chunk(
                existing,
                source="ECOS",
                operation=operation,
                query_key=query_key,
            )
            observations = _load_ecos_rows(source_root, existing)
            chunk = existing
        else:

            def finalize(observations: tuple[ECOSObservation, ...]) -> SourceChunkReceipt:
                if not observations:
                    raise DatasetUnavailable("DATASET_UNAVAILABLE: ECOS projection is empty")
                payload = _ecos_rows_parquet(observations)
                digest = hashlib.sha256(payload).hexdigest()
                return _seal_projection(
                    source_root=source_root,
                    source="ECOS",
                    operation=operation,
                    query_key=query_key,
                    rows=len(observations),
                    payload=payload,
                    temporal=_temporal_receipt(
                        source="ECOS",
                        operation=operation,
                        observation_date=datetime.strptime(
                            observations[-1].time, "%Y%m%d"
                        ).date(),
                        retrieved_at=_clock_utc(clock),
                        request_sha256=query_hash,
                        snapshot_sha256=digest,
                    ),
                )

            observations, new_chunk = _journaled_call(
                ledger=ledger,
                journal=journal,
                provider="ECOS",
                operation=operation,
                query_hash=query_hash,
                call=lambda: provider.fetch(series=series, start=chunk_start, end=chunk_end),
                finalize=finalize,
            )
            assert new_chunk is not None
            chunk = new_chunk
        receipts.append(chunk)
        retrieved = chunk.temporal.retrieved_at
        digest = chunk.content_sha256
        for observation in observations:
            day = datetime.strptime(observation.time, "%Y%m%d").date()
            if day in rows:
                raise LightGbmContractError("SOURCE_SNAPSHOT_CONFLICT")
            rows[day] = MacroObservation(
                series_id=series.series_id,
                observation_date=day,
                value=float(Decimal(observation.value)),
                receipt=_temporal_receipt(
                    source="ECOS",
                    operation=str(query["operation"]),
                    observation_date=day,
                    retrieved_at=retrieved,
                    request_sha256=query_hash,
                    snapshot_sha256=digest,
                ),
            )
        chunk_start = chunk_end + timedelta(days=1)
    if series.series_id == "policy-rate" and not any(day <= raw_start for day in rows):
        raise DatasetUnavailable("DATASET_UNAVAILABLE: base-rate seed is missing")
    return tuple(rows[day] for day in sorted(rows)), tuple(receipts)


def _derive_universes(
    *,
    packet: BootstrapPacket,
    rows: Mapping[tuple[str, date], tuple[dict[str, str], ...]],
    receipts: Mapping[tuple[str, date], TemporalReceipt],
) -> tuple[tuple[MonthlyUniverse, ...], dict[str, str]]:
    output: list[MonthlyUniverse] = []
    markets: dict[str, str] = {}
    for schedule in packet.schedules:
        base_rows = {
            row["ISU_SRT_CD"]: (row, service)
            for service in ("stk_isu_base_info", "ksq_isu_base_info")
            for row in rows[(service, schedule.selection_session)]
        }
        observations: list[ProductionUniverseObservation] = []
        for day in schedule.trailing_sessions:
            for service, market in (("stk_bydd_trd", "KOSPI"), ("ksq_bydd_trd", "KOSDAQ")):
                for trading in rows[(service, day)]:
                    base = base_rows.get(trading["ISU_CD"])
                    if base is None:
                        continue
                    identity_row, identity_service = base
                    identity = require_standard_stock_identity(identity_row["ISU_CD"])
                    classification = classify_krx_security(
                        security_group=identity_row["SECUGRP_NM"],
                        stock_kind=identity_row["KIND_STKCERT_TP_NM"],
                        official_name=identity_row["ISU_NM"],
                        source_service=identity_service,
                    )
                    markets[identity] = market
                    observations.append(
                        ProductionUniverseObservation(
                            instrument_id=identity,
                            symbol=identity_row["ISU_SRT_CD"],
                            session_date=day,
                            trading_value=_positive_number(trading["ACC_TRDVAL"], allow_zero=True),
                            market_cap=_positive_number(trading["MKTCAP"], allow_zero=True),
                            market=market,
                            security_type=classification.value,
                            common_share=classification is SecurityClassification.COMMON_STOCK,
                            listed=True,
                            trading_receipt=receipts[(service, day)],
                            identity_receipt=receipts[(identity_service, schedule.selection_session)],
                        )
                    )
        etf_rows = [
            row
            for row in rows[("etf_bydd_trd", schedule.selection_session)]
            if row["ISU_CD"] == FIXED_ETF_SYMBOL
        ]
        if len(etf_rows) != 1:
            raise DatasetUnavailable("DATASET_UNAVAILABLE: fixed ETF evidence is missing")
        etf_receipt = receipts[("etf_bydd_trd", schedule.selection_session)]
        observations.append(
            ProductionUniverseObservation(
                instrument_id="XKRX:ETF:132030",
                symbol=FIXED_ETF_SYMBOL,
                session_date=schedule.selection_session,
                trading_value=_positive_number(etf_rows[0]["ACC_TRDVAL"], allow_zero=True),
                market_cap=_positive_number(etf_rows[0]["MKTCAP"], allow_zero=True),
                market="KOSPI",
                security_type="ETF",
                common_share=False,
                listed=True,
                trading_receipt=etf_receipt,
                identity_receipt=etf_receipt,
            )
        )
        markets["XKRX:ETF:132030"] = "KOSPI"
        output.append(select_production_monthly_universe(observations, schedule=schedule))
    return tuple(output), markets


def _build_index_evidence(
    packet: BootstrapPacket,
    rows: Mapping[tuple[str, date], tuple[dict[str, str], ...]],
    receipts: Mapping[tuple[str, date], TemporalReceipt],
) -> tuple[IndexEvidence, ...]:
    output: list[IndexEvidence] = []
    for day in packet.window.raw_sessions:
        for service, market, names in (
            ("kospi_dd_trd", "KOSPI", {"코스피", "KOSPI"}),
            ("kosdaq_dd_trd", "KOSDAQ", {"코스닥", "KOSDAQ"}),
        ):
            candidates = [row for row in rows[(service, day)] if row["IDX_NM"].strip() in names]
            if len(candidates) != 1:
                raise DatasetUnavailable("DATASET_UNAVAILABLE: exact market index is missing")
            output.append(
                IndexEvidence(
                    session_date=day,
                    market=market,
                    adjusted_close=_positive_number(candidates[0]["CLSPRC_IDX"]),
                    receipt=receipts[(service, day)],
                )
            )
    return tuple(output)


def _seal_projection(
    *,
    source_root: Path,
    source: str,
    operation: str,
    query_key: str,
    rows: int,
    payload: bytes,
    temporal: TemporalReceipt,
) -> SourceChunkReceipt:
    digest = hashlib.sha256(payload).hexdigest()
    if digest != temporal.snapshot_sha256:
        raise LightGbmContractError("source projection receipt digest mismatch")
    receipt = SourceChunkReceipt(
        source_id=source,
        operation_id=operation,
        query_key=query_key,
        content_sha256=digest,
        row_count=rows,
        byte_count=len(payload),
        temporal=temporal,
    )
    _write_private_new_file(
        approved_root=source_root,
        relative_path=receipt.relative_path,
        content=payload,
        max_bytes=SOURCE_BYTE_CAPS[source],
    )
    return receipt


def _journaled_call(
    *,
    ledger: BootstrapLedger,
    journal: BootstrapJournal,
    provider: str,
    operation: str,
    query_hash: str,
    call: Callable[[], _CallT],
    finalize: Callable[[_CallT], SourceChunkReceipt | None],
) -> tuple[_CallT, SourceChunkReceipt | None]:
    """Intent를 먼저 fsync하고 terminal+chunk가 봉인될 때만 query를 completed로 만든다."""

    ordinal = journal.begin(
        provider=provider, operation_id=operation, query_sha256=query_hash
    )
    try:
        result = ledger.physical_call(
            provider=provider,
            operation_id=operation,
            query_key_sha256=query_hash,
            call=call,
        )
        chunk = finalize(result)
    except Exception:
        journal.finish(
            ordinal=ordinal,
            provider=provider,
            operation_id=operation,
            query_sha256=query_hash,
            success=False,
            chunk=None,
        )
        raise
    journal.finish(
        ordinal=ordinal,
        provider=provider,
        operation_id=operation,
        query_sha256=query_hash,
        success=True,
        chunk=chunk,
    )
    return result, chunk


def _require_reused_chunk(
    chunk: SourceChunkReceipt,
    *,
    source: str,
    operation: str,
    query_key: str,
) -> None:
    if (
        chunk.source_id != source
        or chunk.operation_id != operation
        or chunk.query_key != query_key
    ):
        raise LightGbmContractError("bootstrap progress chunk binding mismatch")


def _load_string_rows(
    source_root: Path, chunk: SourceChunkReceipt
) -> tuple[dict[str, str], ...]:
    table = _load_projection_table(source_root, chunk)
    rows = table.to_pylist()
    if any(
        not isinstance(row, dict)
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in row.items())
        for row in rows
    ):
        raise LightGbmContractError("bootstrap reused projection values are invalid")
    return tuple(dict(row) for row in rows)


def _load_kis_rows(source_root: Path, chunk: SourceChunkReceipt) -> tuple[DailyBar, ...]:
    rows = _load_string_rows(source_root, chunk)
    try:
        return tuple(
            DailyBar(
                symbol=row["symbol"],
                date=date.fromisoformat(row["observationDate"]),
                open=int(row["adjustedOpen"]),
                high=int(row["adjustedHigh"]),
                low=int(row["adjustedLow"]),
                close=int(row["adjustedClose"]),
                volume=int(row["volume"]),
                turnover=int(row["turnover"]),
                flng_cls_code=row["flngClsCode"],
                prtt_rate=Decimal(row["prttRate"]),
                mod_yn=row["modYn"],
                revl_issu_reas=row["revlIssuReas"],
            )
            for row in rows
        )
    except (KeyError, ValueError, ArithmeticError):
        raise LightGbmContractError("bootstrap reused KIS projection is invalid") from None


def _load_ecos_rows(
    source_root: Path, chunk: SourceChunkReceipt
) -> tuple[ECOSObservation, ...]:
    rows = _load_string_rows(source_root, chunk)
    try:
        return tuple(
            ECOSObservation(time=row["observationDate"], value=row["value"])
            for row in rows
        )
    except (KeyError, ValueError):
        raise LightGbmContractError("bootstrap reused ECOS projection is invalid") from None


def _load_projection_table(source_root: Path, chunk: SourceChunkReceipt) -> pa.Table:
    try:
        safe = read_approved_regular_file(
            approved_root=source_root,
            relative_path=chunk.relative_path,
            max_bytes=SOURCE_BYTE_CAPS[chunk.source_id],
        )
        if safe.content_sha256 != chunk.content_sha256:
            raise LightGbmContractError("bootstrap reused projection digest mismatch")
        return pq.read_table(BytesIO(safe.content), use_threads=False)  # type: ignore[no-untyped-call]
    except RagSafeIoError as error:
        raise LightGbmContractError("bootstrap reused projection path is invalid") from error


def _temporal_receipt(
    *,
    source: str,
    operation: str,
    observation_date: date,
    retrieved_at: datetime,
    request_sha256: str,
    snapshot_sha256: str,
) -> TemporalReceipt:
    is_krx = source == "KRX"
    return TemporalReceipt(
        source_id=source,
        operation_id=operation,
        observation_date=observation_date,
        retrieved_at=retrieved_at,
        availability_basis=(
            AvailabilityBasis.PROVIDER_AS_OF_SCHEDULE
            if is_krx
            else AvailabilityBasis.PROJECT_FIXED_LAG
        ),
        revision_basis=RevisionBasis.CONTENT_SNAPSHOT,
        request_sha256=request_sha256,
        snapshot_sha256=snapshot_sha256,
        temporal_quality=(
            TemporalQuality.PROVIDER_AS_OF_NO_VINTAGE
            if is_krx
            else TemporalQuality.RECONSTRUCTED_FIXED_LAG
        ),
        policy_effective_at=(
            next_xkrx_evidence_clock(observation_date)
            if source == "ECOS"
            else next_session_evidence_clock(observation_date)
        ),
    )


def _string_rows_parquet(rows: Sequence[Mapping[str, str]]) -> bytes:
    fields = sorted(rows[0])
    if any(set(row) != set(fields) for row in rows):
        raise LightGbmContractError("source projection rows have inconsistent fields")
    schema = pa.schema([pa.field(field, pa.string(), nullable=False) for field in fields])
    return _table_parquet(pa.Table.from_pylist([dict(row) for row in rows], schema=schema))


def _kis_rows_parquet(rows: Sequence[DailyBar]) -> bytes:
    values = [
        {
            "symbol": row.symbol,
            "observationDate": row.date.isoformat(),
            "adjustedOpen": str(row.open),
            "adjustedHigh": str(row.high),
            "adjustedLow": str(row.low),
            "adjustedClose": str(row.close),
            "volume": str(row.volume),
            "turnover": str(row.turnover),
            "flngClsCode": row.flng_cls_code,
            "prttRate": format(row.prtt_rate, "f"),
            "modYn": row.mod_yn,
            "revlIssuReas": row.revl_issu_reas,
        }
        for row in rows
    ]
    return _string_rows_parquet(values)


def _ecos_rows_parquet(rows: Sequence[ECOSObservation]) -> bytes:
    return _string_rows_parquet(
        [{"observationDate": row.time, "value": row.value} for row in rows]
    )


def _table_parquet(table: pa.Table) -> bytes:
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
    return sink.getvalue()


def _prepare_private_bundle_root(
    root: Path, *, chunks: bool = True, resume: bool = False
) -> None:
    if not root.is_absolute():
        raise LightGbmContractError("production bundle root must be absolute")
    _require_no_symlink_components(root.parent)
    if root.exists():
        metadata = root.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
        ):
            raise LightGbmContractError("production bundle root is not a regular directory")
        entries = {entry.name for entry in root.iterdir()}
        allowed = (
            {"chunks", "progress.jsonl", "manifest.json"}
            if chunks
            else {"features.parquet", "manifest.json"}
        )
        required = "chunks" if chunks else "features.parquet"
        if entries and (not resume or not entries.issubset(allowed) or required not in entries):
            raise LightGbmContractError("production bundle root must be empty")
        root.chmod(0o700)
    else:
        parent = root.parent
        parent_metadata = parent.lstat()
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or stat.S_ISLNK(parent_metadata.st_mode)
            or parent_metadata.st_uid != os.geteuid()
            or parent_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise LightGbmContractError("production bundle parent is invalid")
        root.mkdir(mode=0o700)
    if chunks and not (root / "chunks").exists():
        (root / "chunks").mkdir(mode=0o700)


def _require_no_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise LightGbmContractError("production bundle path contains a symlink")


def _write_private_new_file(
    *, approved_root: Path, relative_path: str, content: bytes, max_bytes: int
) -> None:
    try:
        result = write_approved_new_file(
            approved_root=approved_root,
            relative_path=relative_path,
            content=content,
            max_bytes=max_bytes,
        )
    except RagSafeIoError as error:
        # Content-addressed chunks may be shared by multiple logical requests; exact bytes만 재사용한다.
        try:
            existing = read_approved_regular_file(
                approved_root=approved_root,
                relative_path=relative_path,
                max_bytes=max_bytes,
            )
        except RagSafeIoError:
            raise LightGbmContractError(
                "production projection write boundary is invalid"
            ) from error
        if existing.content != content:
            raise LightGbmContractError("production projection path conflict") from error
        return
    descriptor = os.open(result.absolute_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise LightGbmContractError("production projection is not a regular file")
        os.fchmod(descriptor, 0o600)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise LightGbmContractError("production projection inode changed")
    finally:
        os.close(descriptor)


def _validate_ecos_series(series: Sequence[ECOSSeries]) -> tuple[ECOSSeries, ...]:
    values = tuple(series)
    expected = {
        ("policy-rate", "722Y001", "0101000", "D"),
        ("krw-usd-rate", "731Y001", "0000001", "D"),
    }
    actual = {(row.series_id, row.stat_code, row.item_code1, row.cycle) for row in values}
    if actual != expected or len(values) != 2 or any(not row.verified for row in values):
        raise LightGbmContractError("ECOS S5 production series are not exact or verified")
    return tuple(sorted(values, key=lambda row: row.series_id))


def _packet_cutoff(packet: BootstrapPacket) -> datetime:
    import json

    payload = json.loads(packet.content)
    value = payload["cutoff"]
    if not isinstance(value, str) or not value.endswith("Z"):
        raise LightGbmContractError("bootstrap packet cutoff is invalid")
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _production_pit_input_sha256(acquisition: BootstrapAcquisition) -> str:
    payload = {
        "sourceManifestSha256": acquisition.source_bundle.manifest_sha256,
        "receiptSetSha256": acquisition.source_bundle.receipt_set_sha256,
        "universes": [
            {
                "effectiveMonth": row.effective_month,
                "selectionSession": row.selection_session.isoformat(),
                "instrumentIds": list(row.instrument_ids),
                "symbols": list(row.symbols),
            }
            for row in acquisition.universes
        ],
        "priceRows": len(acquisition.prices),
        "indexRows": len(acquisition.indices),
        "macroRows": len(acquisition.macro),
    }
    return hashlib.sha256(
        b"s5-pit-input-v2\x00" + canonical_json_bytes(payload)
    ).hexdigest()


def _query_sha256(value: Mapping[str, object]) -> str:
    return hashlib.sha256(b"s5-provider-query-v1\x00" + canonical_json_bytes(value)).hexdigest()


def _clock_utc(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        raise LightGbmContractError("bootstrap clock must be timezone aware")
    return value.astimezone(UTC)


def _positive_number(value: str, *, allow_zero: bool = False) -> float:
    try:
        parsed = float(value.replace(",", ""))
    except ValueError:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: provider numeric field is invalid") from None
    if not (parsed >= 0 if allow_zero else parsed > 0):
        raise DatasetUnavailable("DATASET_UNAVAILABLE: provider numeric field is invalid")
    return parsed
