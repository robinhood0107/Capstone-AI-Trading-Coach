"""S5.6 provider projection을 fail-stop 순서로 봉인하고 feature bundle v2를 만든다."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import hashlib
import json
from io import BytesIO
import math
import os
from pathlib import Path
import stat
from typing import Protocol, TypeVar

import pyarrow as pa
import pyarrow.parquet as pq

from app.data._shared.canonical_json import canonical_json_bytes
from app.data.ecos.models import ECOSObservation
from app.data.ecos.policy import ECOS_MAX_ROWS_PER_REQUEST
from app.data.ecos.series_registry import ECOSSeries
from app.data.kis.parsers import DailyBar
from app.lightgbm.bootstrap_control import BootstrapLedger, BootstrapPhase
from app.lightgbm.bootstrap_journal import BootstrapJournal
from app.lightgbm.bootstrap_packet import BootstrapPacket
from app.lightgbm.diagnostics import (
    DIAGNOSTIC_LEDGER_FILENAME,
    DIVERGENCE_MIRROR_OUTCOME,
    record_coverage_report,
    record_diagnostic,
    record_report,
)
from app.lightgbm.outcomes import (
    BootstrapEvidenceGap,
    CollectionUnit,
    OutcomeClass,
)
from app.lightgbm.errors import (
    CalendarDivergenceSuspected,
    DatasetUnavailable,
    LightGbmContractError,
)
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
from app.lightgbm.pit_calendar import (
    S5_CALENDAR_CORRECTION_SET_SHA256,
    S5_CALENDAR_POLICY_VERSION,
    previous_xkrx_session,
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
from app.lightgbm.private_root import require_private_regular_file
from app.lightgbm.source_bundle import (
    MAX_MANIFEST_BYTES,
    SOURCE_CHUNK_BYTE_CAPS,
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


# 종목 거래 증거를 담는 두 일별 service다. 월별 base-info는 상장 목록이라 권위가 아니다.
_DAILY_UNIVERSE_SERVICES = ("stk_bydd_trd", "ksq_bydd_trd")
# chunk 달력 길이를 요청당 행 상한 이하로 두면 발행 밀도와 무관하게 한 요청이 상한 안에 든다.
# 영업일 기준이라 실제로는 더 적지만, 그 가정에 의존하지 않는 것이 요점이다.
# 증거 결손으로 제외할 수 있는 종목 비율 상한이다. 넘으면 조용한 축소이므로 사람이 본다.
MAX_EXCLUDED_SYMBOL_RATIO = 0.01
_ECOS_CHUNK_DAYS = ECOS_MAX_ROWS_PER_REQUEST
assert _ECOS_CHUNK_DAYS <= ECOS_MAX_ROWS_PER_REQUEST
DIVERGENCE_CANDIDATES_FILENAME = "calendar-divergence-candidates.json"
DIVERGENCE_BLOCK_VERSION = "s5-calendar-divergence-block-v1"
MAX_DIVERGENCE_BLOCK_BYTES = 64 * 1024
_DAILY_KRX_SERVICES = ("stk_bydd_trd", "ksq_bydd_trd", "kospi_dd_trd", "kosdaq_dd_trd")
_MONTHLY_KRX_SERVICES = ("stk_isu_base_info", "ksq_isu_base_info", "etf_bydd_trd")
_PARQUET_ROW_GROUP_SIZE = 65_536
_SOURCE_POLICY_SET_SHA256 = hashlib.sha256(
    b"s5-source-policy-set-v1\x00"
    + canonical_json_bytes(
        {
            "historicalMode": "HISTORICAL_REPLAY_RECONSTRUCTED",
            "calendarPolicyVersion": S5_CALENDAR_POLICY_VERSION,
            "calendarCorrectionSetSha256": S5_CALENDAR_CORRECTION_SET_SHA256,
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

    def require_cached_token_only(self) -> None: ...

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
    """검증된 source bundle과 feature materialization에 필요한 typed evidence.

    budgeted_calls는 cache hit 가능성이 있는 OAuth 준비까지 포함한 보수적 승인예산 소비량이며,
    실제 network physical call 수로 사용하지 않는다.
    """

    source_bundle: SourceBundle
    universes: tuple[MonthlyUniverse, ...]
    prices: tuple[ProductionPriceEvidence, ...]
    indices: tuple[IndexEvidence, ...]
    macro: tuple[MacroObservation, ...]
    listing_market_by_membership: Mapping[tuple[str, str], str]
    budgeted_calls: int
    krx_raw_prices: Mapping[tuple[str, date], tuple[float, float]] | None = None


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
    _require_absent_divergence_block(source_root)
    journal = BootstrapJournal(source_root)
    ledger = BootstrapLedger(packet.budget)
    ledger.receipts.extend(journal.consumed_receipts)
    chunks: list[SourceChunkReceipt] = []
    krx_chunks: dict[tuple[str, date], SourceChunkReceipt] = {}
    for session in packet.window.raw_sessions:
        for service in _DAILY_KRX_SERVICES:
            try:
                _rows, receipt = _fetch_and_seal_krx(
                    ledger=ledger,
                    source_root=source_root,
                    provider=krx,
                    service=service,
                    session=session,
                    clock=clock,
                    journal=journal,
                )
            except CalendarDivergenceSuspected as error:
                _publish_divergence_candidates(
                    source_root=source_root,
                    packet=packet,
                    error=error,
                    evidence="EMPTY_DAILY_PROJECTION",
                )
                raise
            except Exception as error:
                # 앞선 session들이 정상인데 이 session만 실패하면 달력 결손 후보일 수 있다.
                # provider 일시 오류와 구분할 수 없으므로 증거만 남기고 원 예외를 그대로 올린다.
                if krx_chunks:
                    _publish_divergence_candidates(
                        source_root=source_root,
                        packet=packet,
                        error=CalendarDivergenceSuspected(
                            "CALENDAR_DIVERGENCE_SUSPECTED: single session query failed",
                            operation_id=service,
                            session_date=session.isoformat(),
                        ),
                        evidence="SINGLE_SESSION_QUERY_FAILURE",
                    )
                raise error
            krx_chunks[(service, session)] = receipt
            chunks.append(receipt)
    for schedule in packet.schedules:
        for service in _MONTHLY_KRX_SERVICES:
            key = (service, schedule.selection_session)
            if key in krx_chunks:
                continue
            _rows, receipt = _fetch_and_seal_krx(
                ledger=ledger,
                source_root=source_root,
                provider=krx,
                service=service,
                session=schedule.selection_session,
                clock=clock,
                journal=journal,
            )
            krx_chunks[key] = receipt
            chunks.append(receipt)
    ledger.advance(BootstrapPhase.KRX)

    universes, listing_market_by_membership = _derive_universes(
        packet=packet,
        source_root=source_root,
        chunks=krx_chunks,
    )
    identities = validate_horizon_union(universes)
    symbol_by_identity = {
        identity: symbol
        for universe in universes
        for identity, symbol in zip(universe.instrument_ids, universe.symbols, strict=True)
    }
    if set(identities) != set(symbol_by_identity):
        raise DatasetUnavailable("DATASET_UNAVAILABLE: universe identity mapping is incomplete")

    token_query_hash = provider_query_sha256({"operation": "oauth2/tokenP", "mode": "live"})
    if journal.token_completed(token_query_hash):
        kis.require_cached_token_only()
    else:
        _journaled_call(
            ledger=ledger,
            journal=journal,
            provider="KIS",
            operation="oauth2/tokenP",
            query_hash=token_query_hash,
            call=kis.prepare_access_token,
            finalize=lambda _: None,
        )
    # 종목별로 실제 거래된 session은 이미 수집한 KRX 일별 projection이 권위다. union은 전 구간
    # 합집합이므로 cutoff 전에 상장폐지된 종목이 들어 있을 수 있고, 그 종목에 전수 커버리지를
    # 요구하면 충족될 수 없는 조건이 된다.
    traded_sessions = _derive_traded_sessions(
        packet=packet,
        source_root=source_root,
        chunks=krx_chunks,
        symbols=frozenset(symbol_by_identity.values()),
    )
    prices: list[ProductionPriceEvidence] = []
    excluded_symbols: list[str] = []
    for identity in identities:
        symbol = symbol_by_identity[identity]
        try:
            symbol_prices, symbol_chunks = _fetch_kis_symbol(
                ledger=ledger,
                source_root=source_root,
                provider=kis,
                identity=identity,
                symbol=symbol,
                raw_sessions=packet.window.raw_sessions,
                expected_sessions=traded_sessions[symbol],
                clock=clock,
                journal=journal,
            )
        except BootstrapEvidenceGap as gap:
            # 그 종목에 provider 증거가 없다. 전체를 죽이지 않고 제외하되 증거를 남긴다.
            record_diagnostic(
                source_root=source_root,
                phase="COLLECTING_KIS",
                outcome=OutcomeClass.EVIDENCE_GAP,
                unit=gap.unit,
                measured=gap.measured,
            )
            excluded_symbols.append(symbol)
            continue
        prices.extend(symbol_prices)
        chunks.extend(symbol_chunks)
    _require_bounded_exclusion(
        source_root=source_root,
        phase="COLLECTING_KIS",
        excluded=len(excluded_symbols),
        total=len(identities),
    )
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

    indices = _build_index_evidence(packet, source_root, krx_chunks)
    krx_raw_prices = _build_krx_raw_prices(
        packet,
        source_root,
        krx_chunks,
        symbols=frozenset(symbol_by_identity.values()),
    )
    # Resume 시 wall clock 변화가 이미 봉인된 manifest를 충돌시키지 않게 retrieval receipt로 결정한다.
    manifest = build_source_manifest(
        created_at=max(chunk.temporal.retrieved_at for chunk in chunks),
        dataset_cutoff=_packet_cutoff(packet),
        chunks=chunks,
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
        listing_market_by_membership=dict(sorted(listing_market_by_membership.items())),
        budgeted_calls=len(ledger.receipts),
        krx_raw_prices=krx_raw_prices,
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
    table = build_production_feature_table(packet=packet, acquisition=acquisition)
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


def build_production_feature_table(
    *,
    packet: BootstrapPacket,
    acquisition: BootstrapAcquisition,
    macro_delay_sessions: int = 0,
) -> pa.Table:
    """검증된 acquisition에서 primary 또는 +1-session macro sensitivity table을 만든다."""

    if macro_delay_sessions not in {0, 1}:
        raise LightGbmContractError("macro sensitivity delay must be zero or one session")
    memberships = {
        universe.effective_month: set(universe.instrument_ids) for universe in acquisition.universes
    }
    prices_by_identity: dict[str, list[ProductionPriceEvidence]] = defaultdict(list)
    for price in acquisition.prices:
        prices_by_identity[price.instrument_id].append(price)
    rows: list[Mapping[str, object]] = []
    eligible = set(packet.window.eligible_sessions)
    last_feature_session = packet.window.eligible_sessions[-1]
    for identity in sorted(prices_by_identity):
        identity_prices = tuple(
            price
            for price in prices_by_identity[identity]
            if price.session_date <= last_feature_session
        )
        # 상장폐지 종목은 폐지 이후 월의 base-info에 없다. 소비처는 그 종목의 가격 행 세션만
        # 조회하므로 schedule 범위를 소비 대상과 같게 맞춘다.
        market_by_session = _listing_market_schedule(
            identity=identity,
            sessions=tuple(price.session_date for price in identity_prices),
            listing_market_by_membership=acquisition.listing_market_by_membership,
        )
        feature_rows = build_production_core_feature_rows(
            identity_prices,
            acquisition.indices,
            acquisition.macro,
            listing_market_by_session=market_by_session,
            cutoff=_packet_cutoff(packet),
            macro_delay_sessions=macro_delay_sessions,
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
    return feature_table_from_rows(rows)


def build_current_inference_feature_table(
    *, packet: BootstrapPacket, acquisition: BootstrapAcquisition
) -> pa.Table:
    """label tail과 분리해 latest completed session의 exact current-31 inference rows를 만든다."""

    latest = packet.window.raw_sessions[-1]
    evidence_day = next_xkrx_evidence_clock(latest).date()
    effective_month = f"{evidence_day.year:04d}-{evidence_day.month:02d}"
    candidates = [
        universe for universe in acquisition.universes if universe.effective_month == effective_month
    ]
    if len(candidates) != 1 or len(candidates[0].symbols) != 31:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: current inference universe is not exact 31")
    universe = candidates[0]
    prices_by_identity: dict[str, list[ProductionPriceEvidence]] = defaultdict(list)
    for price in acquisition.prices:
        prices_by_identity[price.instrument_id].append(price)
    rows: list[Mapping[str, object]] = []
    for identity in universe.instrument_ids:
        identity_prices = prices_by_identity.get(identity, [])
        market_by_session = _listing_market_schedule(
            identity=identity,
            sessions=packet.window.raw_sessions,
            listing_market_by_membership=acquisition.listing_market_by_membership,
        )
        feature_rows = build_production_core_feature_rows(
            identity_prices,
            acquisition.indices,
            acquisition.macro,
            listing_market_by_session=market_by_session,
            cutoff=_packet_cutoff(packet),
        )
        current = [row for row in feature_rows if row.session_date == latest]
        if len(current) != 1:
            raise DatasetUnavailable("DATASET_UNAVAILABLE: current inference feature is missing")
        rows.append(current[0].as_mapping())
    rows.sort(key=lambda row: str(row["symbol"]))
    table = feature_table_from_rows(rows)
    if table.num_rows != 31:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: current inference row count is not 31")
    return table


def load_verified_krx_projection(
    *, source_root: Path, source_bundle: SourceBundle, service: str, session_date: date
) -> tuple[tuple[dict[str, str], ...], TemporalReceipt]:
    """검증된 bootstrap source bundle에서 exact KRX service/session projection만 복원한다."""

    matches = [
        chunk
        for chunk in source_bundle.chunks
        if chunk.source_id == "KRX"
        and chunk.operation_id == service
        and chunk.query_key == f"{service}:{session_date.isoformat()}"
    ]
    if len(matches) != 1:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: KRX source projection is missing")
    return _load_string_rows(source_root, matches[0]), matches[0].temporal


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


def _require_absent_divergence_block(source_root: Path) -> None:
    """빈 일별 projection 후보가 남은 run은 provider를 다시 열지 않는다.

    단일 session 실패 후보는 provider 일시 오류일 수 있어 계약이 허용한 resume을 막지 않는다.
    그 경우 후보 sidecar는 진단 증거로만 남는다.
    """

    block = source_root / DIVERGENCE_CANDIDATES_FILENAME
    if not block.exists():
        return
    payload = json.loads(
        read_approved_regular_file(
            approved_root=source_root,
            relative_path=DIVERGENCE_CANDIDATES_FILENAME,
            max_bytes=MAX_DIVERGENCE_BLOCK_BYTES,
        ).content.decode("utf-8")
    )
    if any(
        candidate.get("evidence") == "EMPTY_DAILY_PROJECTION"
        for candidate in payload.get("candidates", ())
    ):
        raise CalendarDivergenceSuspected(
            "CALENDAR_DIVERGENCE_SUSPECTED: unresolved calendar divergence block is present",
            operation_id="",
            session_date="",
        )


def _publish_divergence_candidates(
    *,
    source_root: Path,
    packet: BootstrapPacket,
    error: CalendarDivergenceSuspected,
    evidence: str = "EMPTY_DAILY_PROJECTION",
) -> None:
    """Provider raw 없이 후보 session만 content-free sidecar로 남긴다.

    같은 bytes면 idempotent하게 통과시켜 resume 경로에서 충돌하지 않게 한다.
    """

    payload = canonical_json_bytes(
        {
            "blockVersion": DIVERGENCE_BLOCK_VERSION,
            "packetSha256": packet.sha256,
            "calendarPolicyVersion": S5_CALENDAR_POLICY_VERSION,
            "calendarCorrectionSetSha256": S5_CALENDAR_CORRECTION_SET_SHA256,
            "candidates": [
                {
                    "provider": "KRX",
                    "operationId": error.operation_id,
                    "sessionDate": error.session_date,
                    "evidence": evidence,
                }
            ],
            "providerCallsDuringBlock": 0,
        }
    )
    target = source_root / DIVERGENCE_CANDIDATES_FILENAME
    if target.exists():
        existing = read_approved_regular_file(
            approved_root=source_root,
            relative_path=DIVERGENCE_CANDIDATES_FILENAME,
            max_bytes=MAX_DIVERGENCE_BLOCK_BYTES,
        )
        if existing.content == payload:
            return
        raise LightGbmContractError(
            "calendar divergence block conflicts with prior sealed bytes"
        )
    # 게이트 토큰은 파일 존재가 차단을 뜻하므로 삭제 가능한 별도 파일로 남긴다. 원장에는
    # 읽는 곳을 하나로 만들기 위해 같은 사건을 미러링만 한다.
    record_report(
        source_root=source_root,
        phase="COLLECTING_KRX",
        report=DIVERGENCE_MIRROR_OUTCOME,
        measured={
            "operationId": error.operation_id,
            "sessionDate": error.session_date,
            "evidence": evidence,
        },
    )
    _write_private_new_file(
        approved_root=source_root,
        relative_path=DIVERGENCE_CANDIDATES_FILENAME,
        content=payload,
        max_bytes=MAX_DIVERGENCE_BLOCK_BYTES,
    )


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
    query_hash = provider_query_sha256(query)
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
            if service in _DAILY_KRX_SERVICES:
                # 인접 session은 정상인데 이 session만 비어 있으면 달력 권위 결손 징후다.
                raise CalendarDivergenceSuspected(
                    "CALENDAR_DIVERGENCE_SUSPECTED: KRX daily projection is empty",
                    operation_id=service,
                    session_date=session.isoformat(),
                )
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
    expected_sessions: Sequence[date],
    clock: Callable[[], datetime],
    journal: BootstrapJournal,
) -> tuple[tuple[ProductionPriceEvidence, ...], tuple[SourceChunkReceipt, ...]]:
    # paging window는 packet raw 구간이다. window가 query 신원에 들어가므로 종목별로 좁히면 이미
    # 봉인된 chunk가 도달 불가가 되고 승인 호출을 다시 태워야 한다.
    start, cursor_end = raw_sessions[0], raw_sessions[-1]
    expected = frozenset(expected_sessions)
    if not expected:
        raise BootstrapEvidenceGap(
            "KIS coverage expectation is absent",
            unit=CollectionUnit(
                provider="KIS",
                operation_id=KIS_OPERATION,
                query_sha256=provider_query_sha256(
                    {"operation": KIS_OPERATION, "symbol": symbol}
                ),
                label=symbol,
            ),
            measured={"expectedSessions": 0},
        )
    seen: dict[date, ProductionPriceEvidence] = {}
    receipts: list[SourceChunkReceipt] = []
    page_number = 0
    while cursor_end >= start:
        # 증거가 말하는 session을 다 받았으면 더 요청할 것이 없다. 역사가 정확히 100의 배수로
        # 끝나는 종목은 응답 모양만으로는 "더 없음"을 구분할 수 없어 상장 전 구간을 한 번 더
        # 요청하고, 그 0행 응답이 하드 실패가 되면서 승인 호출을 태운다.
        if expected.issubset(seen):
            break
        page_number += 1
        query = {
            "operation": KIS_OPERATION,
            "symbol": symbol,
            "start": start.isoformat(),
            "end": cursor_end.isoformat(),
            "adjusted": "0",
            "page": page_number,
        }
        query_hash = provider_query_sha256(query)
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
            # 더 과거로 갈 페이지가 없다는 신호다. 잘린 역사인지는 커버리지 대조가 판정한다.
            break
        cursor_end = oldest - timedelta(days=1)
    if set(seen) != expected:
        # 어떤 종목이 얼마나 어긋났는지 예외가 직접 들고 나온다. 호출자가 원장에 남긴다.
        missing = sorted(expected - set(seen))
        extra = sorted(set(seen) - expected)
        raise BootstrapEvidenceGap(
            "KIS_HISTORY_UNAVAILABLE",
            unit=CollectionUnit(
                provider="KIS",
                operation_id=KIS_OPERATION,
                query_sha256=provider_query_sha256(
                    {"operation": KIS_OPERATION, "symbol": symbol}
                ),
                label=symbol,
            ),
            measured={
                "instrumentId": identity,
                "expectedSessions": len(expected),
                "observedSessions": len(seen),
                "missingSessions": len(missing),
                "extraSessions": len(extra),
                "firstMissingSession": missing[0].isoformat() if missing else "",
                "lastMissingSession": missing[-1].isoformat() if missing else "",
                "firstExtraSession": extra[0].isoformat() if extra else "",
                "lastExtraSession": extra[-1].isoformat() if extra else "",
            },
        )
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
    start = (
        raw_start - timedelta(days=366)
        if series.series_id == "policy-rate"
        else previous_xkrx_session(raw_start)
    )
    rows: dict[date, MacroObservation] = {}
    receipts: list[SourceChunkReceipt] = []
    chunk_start = start
    while chunk_start <= raw_end:
        chunk_end = min(
            chunk_start + timedelta(days=_ECOS_CHUNK_DAYS - 1), raw_end
        )
        query = {
            "operation": f"{series.stat_code}/{series.item_code1}/{series.cycle}",
            "start": chunk_start.isoformat(),
            "end": chunk_end.isoformat(),
        }
        query_hash = provider_query_sha256(query)
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
            try:
                macro_value = Decimal(observation.value)
            except Exception:
                raise DatasetUnavailable(
                    "DATASET_UNAVAILABLE: ECOS numeric field is invalid"
                ) from None
            if not macro_value.is_finite() or (
                series.series_id == "krw-usd-rate" and macro_value <= 0
            ):
                raise DatasetUnavailable("DATASET_UNAVAILABLE: ECOS numeric field is invalid")
            rows[day] = MacroObservation(
                series_id=series.series_id,
                observation_date=day,
                value=float(macro_value),
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
    source_root: Path,
    chunks: Mapping[tuple[str, date], SourceChunkReceipt],
) -> tuple[tuple[MonthlyUniverse, ...], dict[tuple[str, str], str]]:
    output: list[MonthlyUniverse] = []
    markets: dict[tuple[str, str], str] = {}
    for schedule in packet.schedules:
        base_rows: dict[str, tuple[dict[str, str], str, str]] = {}
        for service, market in (
                ("stk_isu_base_info", "KOSPI"),
                ("ksq_isu_base_info", "KOSDAQ"),
        ):
            for row in _load_string_rows(
                source_root, chunks[(service, schedule.selection_session)]
            ):
                symbol = row["ISU_SRT_CD"]
                if symbol in base_rows:
                    raise DatasetUnavailable(
                        "SOURCE_SNAPSHOT_CONFLICT: monthly short code is ambiguous"
                    )
                base_rows[symbol] = (row, service, market)
        for identity_row, _identity_service, market in base_rows.values():
            identity = require_standard_stock_identity(identity_row["ISU_CD"])
            key = (identity, schedule.effective_month)
            prior_market = markets.get(key)
            if prior_market is not None and prior_market != market:
                raise DatasetUnavailable(
                    "SOURCE_SNAPSHOT_CONFLICT: monthly listing market is ambiguous"
                )
            markets[key] = market
        observations: list[ProductionUniverseObservation] = []
        for day in schedule.trailing_sessions:
            for service, market in (("stk_bydd_trd", "KOSPI"), ("ksq_bydd_trd", "KOSDAQ")):
                for trading in _load_string_rows(source_root, chunks[(service, day)]):
                    base = base_rows.get(trading["ISU_CD"])
                    if base is None:
                        continue
                    identity_row, identity_service, _identity_market = base
                    identity = require_standard_stock_identity(identity_row["ISU_CD"])
                    classification = classify_krx_security(
                        security_group=identity_row["SECUGRP_NM"],
                        stock_kind=identity_row["KIND_STKCERT_TP_NM"],
                        official_name=identity_row["ISU_NM"],
                        source_service=identity_service,
                    )
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
                            trading_receipt=chunks[(service, day)].temporal,
                            identity_receipt=chunks[
                                (identity_service, schedule.selection_session)
                            ].temporal,
                        )
                    )
        etf_rows = [
            row
            for row in _load_string_rows(
                source_root, chunks[("etf_bydd_trd", schedule.selection_session)]
            )
            if row["ISU_CD"] == FIXED_ETF_SYMBOL
        ]
        if len(etf_rows) != 1:
            raise DatasetUnavailable("DATASET_UNAVAILABLE: fixed ETF evidence is missing")
        etf_receipt = chunks[("etf_bydd_trd", schedule.selection_session)].temporal
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
        universe = select_production_monthly_universe(observations, schedule=schedule)
        selection_market = {
            row.instrument_id: row.market
            for row in observations
            if row.session_date == schedule.selection_session
        }
        for identity in universe.instrument_ids:
            selected_market = selection_market.get(identity)
            if selected_market not in {"KOSPI", "KOSDAQ"}:
                raise DatasetUnavailable("DATASET_UNAVAILABLE: monthly listing market is missing")
            key = (identity, universe.effective_month)
            if key in markets and markets[key] != selected_market:
                raise DatasetUnavailable(
                    "SOURCE_SNAPSHOT_CONFLICT: selected listing market is ambiguous"
                )
            markets[key] = selected_market
        output.append(universe)
    return tuple(output), markets


def _listing_market_schedule(
    *,
    identity: str,
    sessions: Sequence[date],
    listing_market_by_membership: Mapping[tuple[str, str], str],
) -> dict[date, str]:
    """월별 base-info market을 row clock에 투영하며 warm-up 이전만 보수적으로 고정한다."""

    known = sorted(
        (month, market)
        for (instrument_id, month), market in listing_market_by_membership.items()
        if instrument_id == identity
    )
    if not known:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: listing market evidence is missing")
    earliest_month, earliest_market = known[0]
    output: dict[date, str] = {}
    for session in sessions:
        month = f"{session.year:04d}-{session.month:02d}"
        market = listing_market_by_membership.get((identity, month))
        if market is None and month < earliest_month:
            # 59-session warm-up은 첫 selection schedule보다 앞서므로 최초 base-info만 사용한다.
            market = earliest_market
        if market not in {"KOSPI", "KOSDAQ"}:
            raise DatasetUnavailable("DATASET_UNAVAILABLE: monthly listing market is missing")
        output[session] = market
    return output


def _build_index_evidence(
    packet: BootstrapPacket,
    source_root: Path,
    chunks: Mapping[tuple[str, date], SourceChunkReceipt],
) -> tuple[IndexEvidence, ...]:
    output: list[IndexEvidence] = []
    for day in packet.window.raw_sessions:
        for service, market, names in (
            ("kospi_dd_trd", "KOSPI", {"코스피", "KOSPI"}),
            ("kosdaq_dd_trd", "KOSDAQ", {"코스닥", "KOSDAQ"}),
        ):
            chunk = chunks[(service, day)]
            candidates = [
                row
                for row in _load_string_rows(source_root, chunk)
                if row["IDX_NM"].strip() in names
            ]
            if len(candidates) != 1:
                raise DatasetUnavailable("DATASET_UNAVAILABLE: exact market index is missing")
            output.append(
                IndexEvidence(
                    session_date=day,
                    market=market,
                    adjusted_close=_positive_number(candidates[0]["CLSPRC_IDX"]),
                    receipt=chunk.temporal,
                )
            )
    return tuple(output)


def _require_bounded_exclusion(
    *, source_root: Path, phase: str, excluded: int, total: int
) -> None:
    """제외가 상한을 넘으면 멈춘다. 증거 있는 제외라도 대규모면 조용한 축소다."""

    record_coverage_report(
        source_root=source_root,
        phase=phase,
        measured={"excludedUnits": excluded, "totalUnits": total},
    )
    if total <= 0:
        raise LightGbmContractError("bootstrap unit total is invalid")
    if excluded > total * MAX_EXCLUDED_SYMBOL_RATIO:
        raise LightGbmContractError(
            "bootstrap excluded unit ratio exceeds the approved bound"
        )


def _derive_traded_sessions(
    *,
    packet: BootstrapPacket,
    source_root: Path,
    chunks: Mapping[tuple[str, date], SourceChunkReceipt],
    symbols: frozenset[str],
) -> dict[str, tuple[date, ...]]:
    """KRX 일별 projection에서 종목별 실제 거래 session을 유도한다.

    일별 projection의 ISU_CD는 KIS가 쓰는 6자리 단축코드와 같다. 이 집합이 KIS 커버리지 요구의
    권위이며, provider 사이의 진짜 불일치는 정확한 일치 조건이 그대로 걸러낸다.
    """

    raw = tuple(packet.window.raw_sessions)
    # 고정 ETF는 일별 stock projection에 없고 월별 etf_bydd_trd에만 나타난다. 계약이 고정한
    # 종목이므로 전 구간 커버리지 요구를 그대로 유지하고 상장폐지 완화 대상에서 제외한다.
    output: dict[str, list[date]] = {
        symbol: [] for symbol in symbols if symbol != FIXED_ETF_SYMBOL
    }
    for day in raw:
        present: set[str] = set()
        for service in _DAILY_UNIVERSE_SERVICES:
            for row in _load_string_rows(source_root, chunks[(service, day)]):
                code = row["ISU_CD"]
                if code in output:
                    present.add(code)
        for code in present:
            output[code].append(day)
    if any(not days for days in output.values()):
        # union 소속은 거래 관측에서 나오므로 증거가 0인 종목은 있을 수 없다.
        raise DatasetUnavailable("DATASET_UNAVAILABLE: KRX trading evidence is absent")
    # rolling window는 그 종목 자기 행에 대한 위치 기반이다. 중간 결손이 있으면 60-session window가
    # 조용히 더 긴 달력 구간을 덮으므로, 상장/폐지로 끝이 잘리는 것만 허용하고 구멍은 거부한다.
    position = {day: index for index, day in enumerate(raw)}
    for days in output.values():
        if position[days[-1]] - position[days[0]] + 1 != len(days):
            raise DatasetUnavailable(
                "DATASET_UNAVAILABLE: KRX trading evidence is not contiguous"
            )
    expectations = {symbol: tuple(days) for symbol, days in output.items()}
    if FIXED_ETF_SYMBOL in symbols:
        expectations[FIXED_ETF_SYMBOL] = raw
    return expectations


def _build_krx_raw_prices(
    packet: BootstrapPacket,
    source_root: Path,
    chunks: Mapping[tuple[str, date], SourceChunkReceipt],
    *,
    symbols: frozenset[str],
) -> dict[tuple[str, date], tuple[float, float]]:
    """기업행사 sensitivity에 필요한 KRX raw open/close만 closed projection에서 보존한다.

    거래량 0인 세션은 시가가 0이고 종가는 기준가다. 그런 세션에는 raw 시가가 존재하지 않으므로
    항목을 만들지 않는다. sensitivity 소비처는 raw 증거 네 개 중 하나라도 없으면 그 key를
    건너뛰도록 이미 설계돼 있다. 수치가 아닌 필드는 여전히 거부한다.
    """

    output: dict[tuple[str, date], tuple[float, float]] = {}
    for day in packet.window.raw_sessions:
        for service in _DAILY_UNIVERSE_SERVICES:
            for row in _load_string_rows(source_root, chunks[(service, day)]):
                symbol = row["ISU_CD"]
                if symbol not in symbols:
                    continue
                open_price = _positive_number(row["TDD_OPNPRC"], allow_zero=True)
                close_price = _positive_number(row["TDD_CLSPRC"], allow_zero=True)
                if open_price <= 0 or close_price <= 0:
                    continue
                key = (symbol, day)
                value = (open_price, close_price)
                prior = output.get(key)
                if prior is not None and prior != value:
                    raise DatasetUnavailable("SOURCE_SNAPSHOT_CONFLICT: KRX raw price is ambiguous")
                output[key] = value
    if not output:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: KRX raw sensitivity prices are absent")
    return dict(sorted(output.items()))


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
        max_bytes=SOURCE_CHUNK_BYTE_CAPS[source],
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
            max_bytes=SOURCE_CHUNK_BYTE_CAPS[chunk.source_id],
        )
        if safe.content_sha256 != chunk.content_sha256:
            raise LightGbmContractError("bootstrap reused projection digest mismatch")
        require_private_regular_file(
            safe.absolute_path,
            expected_device=safe.device,
            expected_inode=safe.inode,
        )
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
            {
                "chunks",
                "progress.jsonl",
                "manifest.json",
                "recovery-lineage.json",
                DIVERGENCE_CANDIDATES_FILENAME,
                DIAGNOSTIC_LEDGER_FILENAME,
            }
            if chunks
            else {"features.parquet", "manifest.json"}
        )
        if entries and (not resume or not entries.issubset(allowed)):
            raise LightGbmContractError("production bundle root must be empty")
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            raise LightGbmContractError("production bundle root must be owner-private")
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


def provider_query_sha256(value: Mapping[str, object]) -> str:
    """Provider URL/credential 없이 closed logical query를 감사용 SHA-256으로 식별한다."""

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
    if not math.isfinite(parsed) or not (parsed >= 0 if allow_zero else parsed > 0):
        raise DatasetUnavailable("DATASET_UNAVAILABLE: provider numeric field is invalid")
    return parsed
