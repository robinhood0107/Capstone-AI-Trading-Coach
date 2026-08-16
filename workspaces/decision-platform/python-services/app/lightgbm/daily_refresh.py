"""S5.6B one-session daily refresh의 immutable state, packet, provider 실행 경계."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import hashlib
import math
from pathlib import Path
from typing import cast

import pyarrow as pa

from app.data._shared.bounded_json import (
    BoundedJsonError,
    BoundedJsonLimits,
    parse_bounded_json_bytes,
)
from app.data._shared.canonical_json import canonical_json_bytes
from app.data.ecos.models import ECOSObservation
from app.data.ecos.series_registry import ECOSSeries
from app.data.kis.parsers import DailyBar
from app.lightgbm.bootstrap_executor import (
    BootstrapAcquisition,
    EcosBootstrapProvider,
    KisBootstrapProvider,
    KrxBootstrapProvider,
    load_verified_krx_projection,
)
from app.lightgbm.bootstrap_packet import BootstrapPacket
from app.lightgbm.errors import DatasetUnavailable, LightGbmContractError
from app.lightgbm.feature_artifact import feature_table_from_rows
from app.lightgbm.features import (
    IndexEvidence,
    MacroObservation,
    ProductionPriceEvidence,
    build_production_core_feature_rows,
)
from app.lightgbm.pit_calendar import _calendar, derive_monthly_universe_schedule
from app.lightgbm.production_policy import (
    ECOS_OPERATIONS,
    KIS_OPERATION,
    KRX_OPERATIONS,
    SecurityClassification,
    classify_krx_security,
    require_standard_stock_identity,
)
from app.lightgbm.production_release import (
    ValidatedSignalBatch,
    load_qualified_production_release,
    write_production_signal_batch,
)
from app.lightgbm.temporal import (
    AvailabilityBasis,
    KST,
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
)
from app.rag.safe_io import RagSafeIoError, read_approved_regular_file, write_approved_new_file


DAILY_STATE_VERSION = "s5-daily-inference-state-v1"
DAILY_PACKET_VERSION = "s5-daily-refresh-packet-v1"
DAILY_KRX_MAX = 7
DAILY_KIS_MAX = 31
DAILY_KIS_TOKEN_MAX = 1
DAILY_ECOS_MAX = 2
DAILY_TOTAL_MAX = 41
DAILY_STATE_MAX_BYTES = 64 * 1024 * 1024
DAILY_PACKET_MAX_BYTES = 256 * 1024
_STATE_FIELDS = frozenset(
    {
        "stateVersion",
        "bootstrapPacketSha256",
        "sourceManifestSha256",
        "featureManifestSha256",
        "releaseManifestSha256",
        "previousStateSha256",
        "sessionDate",
        "asOf",
        "universe",
        "listingMarkets",
        "prices",
        "indices",
        "macro",
        "krxHistory",
    }
)
_PACKET_FIELDS = frozenset(
    {
        "packetVersion",
        "previousStateSha256",
        "bootstrapPacketSha256",
        "releaseManifestSha256",
        "sessionDate",
        "asOf",
        "operations",
        "limits",
    }
)
_JSON_LIMITS = BoundedJsonLimits(
    max_bytes=DAILY_STATE_MAX_BYTES,
    max_depth=10,
    max_list_items=100_000,
    max_object_keys=64,
    max_text_codepoints=16_384,
    max_text_bytes=65_536,
    max_number_characters=64,
)


@dataclass(frozen=True, slots=True)
class DailyKrxProjection:
    """한 session/service의 allowlisted KRX projection과 동일-content receipt."""

    session_date: date
    service: str
    rows: tuple[dict[str, str], ...]
    receipt: TemporalReceipt


@dataclass(frozen=True, slots=True)
class DailyInferenceState:
    """다음 한 session 추론에 필요한 bounded 60/20-session evidence snapshot."""

    content: bytes
    sha256: str
    bootstrap_packet_sha256: str
    source_manifest_sha256: str
    feature_manifest_sha256: str
    release_manifest_sha256: str
    previous_state_sha256: str | None
    session_date: date
    as_of: datetime
    universe: MonthlyUniverse
    listing_markets: Mapping[str, str]
    prices: tuple[ProductionPriceEvidence, ...]
    indices: tuple[IndexEvidence, ...]
    macro: tuple[MacroObservation, ...]
    krx_history: tuple[DailyKrxProjection, ...]


@dataclass(frozen=True, slots=True)
class DailyRefreshPacket:
    """정확히 다음 한 XKRX session과 41-call 상한만 승인하는 content-free packet."""

    content: bytes
    sha256: str
    previous_state_sha256: str
    bootstrap_packet_sha256: str
    release_manifest_sha256: str
    session_date: date
    as_of: datetime


@dataclass(frozen=True, slots=True)
class DailyRefreshResult:
    """새 state와 exact-31 batch 및 승인 예산을 소비한 provider operation 수를 결속한다.

    OAuth cache hit은 실제 token endpoint 호출을 만들지 않을 수 있으므로 이 값은 물리 호출을
    과장하지 않고 성공 경로가 예약·소비한 최대 operation 수만 나타낸다.
    """

    state: DailyInferenceState
    batch: ValidatedSignalBatch
    budgeted_calls: int


def write_initial_daily_state(
    *,
    packet: BootstrapPacket,
    acquisition: BootstrapAcquisition,
    source_root: Path,
    feature_manifest_sha256: str,
    release_manifest_sha256: str,
    state_root: Path,
) -> DailyInferenceState:
    """bootstrap 검증 결과에서 첫 60/20-session daily state를 content-addressed로 만든다."""

    latest = packet.window.latest_completed
    evidence_day = next_xkrx_evidence_clock(latest).date()
    effective_month = f"{evidence_day.year:04d}-{evidence_day.month:02d}"
    candidates = [row for row in acquisition.universes if row.effective_month == effective_month]
    if len(candidates) != 1:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: initial daily universe is missing")
    universe = candidates[0]
    sessions_60 = tuple(packet.window.raw_sessions[-60:])
    session_set = set(sessions_60)
    identities = set(universe.instrument_ids)
    prices = tuple(
        row
        for row in acquisition.prices
        if row.instrument_id in identities and row.session_date in session_set
    )
    _require_exact_price_window(prices, universe, sessions_60)
    indices = tuple(row for row in acquisition.indices if row.session_date in session_set)
    _require_index_window(indices, sessions_60)
    macro = _trim_macro(acquisition.macro, sessions_60)
    listing_markets = {
        identity: acquisition.listing_market_by_membership[(identity, effective_month)]
        for identity in universe.instrument_ids
    }
    history_sessions = tuple(packet.window.raw_sessions[-20:])
    history: list[DailyKrxProjection] = []
    for session in history_sessions:
        for service in ("stk_bydd_trd", "ksq_bydd_trd"):
            rows, receipt = load_verified_krx_projection(
                source_root=source_root,
                source_bundle=acquisition.source_bundle,
                service=service,
                session_date=session,
            )
            history.append(
                DailyKrxProjection(
                    session_date=session,
                    service=service,
                    rows=rows,
                    receipt=receipt,
                )
            )
    return _write_state(
        state_root=state_root,
        bootstrap_packet_sha256=packet.sha256,
        source_manifest_sha256=acquisition.source_bundle.manifest_sha256,
        feature_manifest_sha256=feature_manifest_sha256,
        release_manifest_sha256=release_manifest_sha256,
        previous_state_sha256=None,
        session_date=latest,
        as_of=next_xkrx_evidence_clock(latest),
        universe=universe,
        listing_markets=listing_markets,
        prices=prices,
        indices=indices,
        macro=macro,
        krx_history=tuple(history),
    )


def read_daily_state(*, state_root: Path, expected_sha256: str) -> DailyInferenceState:
    """외부 trust-anchor digest와 derived filename으로만 immutable daily state를 연다."""

    _require_sha(expected_sha256, "daily state")
    try:
        safe = read_approved_regular_file(
            approved_root=state_root,
            relative_path=f"state-{expected_sha256}.json",
            max_bytes=DAILY_STATE_MAX_BYTES,
        )
    except RagSafeIoError as error:
        raise LightGbmContractError("daily state path boundary is invalid") from error
    if safe.content_sha256 != expected_sha256:
        raise LightGbmContractError("daily state trust anchor mismatch")
    payload = _parse_mapping(safe.content, _STATE_FIELDS, "daily state")
    if canonical_json_bytes(payload) != safe.content or payload["stateVersion"] != DAILY_STATE_VERSION:
        raise LightGbmContractError("daily state is noncanonical or version invalid")
    universe = _parse_universe(payload["universe"])
    listing_markets = _parse_listing_markets(payload["listingMarkets"], universe)
    prices = tuple(_parse_price(value) for value in _list(payload["prices"], "prices"))
    indices = tuple(_parse_index(value) for value in _list(payload["indices"], "indices"))
    macro = tuple(_parse_macro(value) for value in _list(payload["macro"], "macro"))
    history = tuple(
        _parse_krx_projection(value) for value in _list(payload["krxHistory"], "krxHistory")
    )
    session_date = _date(payload["sessionDate"], "sessionDate")
    as_of = _datetime(payload["asOf"], "asOf")
    sessions_60 = _last_sessions(session_date, 60)
    _require_exact_price_window(prices, universe, sessions_60)
    _require_index_window(indices, sessions_60)
    _trim_macro(macro, sessions_60)
    _require_history(history, _last_sessions(session_date, 20))
    return DailyInferenceState(
        content=safe.content,
        sha256=expected_sha256,
        bootstrap_packet_sha256=_sha(payload["bootstrapPacketSha256"], "bootstrap packet"),
        source_manifest_sha256=_sha(payload["sourceManifestSha256"], "source manifest"),
        feature_manifest_sha256=_sha(payload["featureManifestSha256"], "feature manifest"),
        release_manifest_sha256=_sha(payload["releaseManifestSha256"], "release manifest"),
        previous_state_sha256=(
            _sha(payload["previousStateSha256"], "previous state")
            if payload["previousStateSha256"] is not None
            else None
        ),
        session_date=session_date,
        as_of=as_of,
        universe=universe,
        listing_markets=listing_markets,
        prices=prices,
        indices=indices,
        macro=macro,
        krx_history=history,
    )


def author_daily_refresh_packet(
    *,
    state: DailyInferenceState,
    cutoff: datetime,
    requested_session: date | None = None,
) -> DailyRefreshPacket:
    """바로 다음 한 XKRX session만 author한다.

    기본 실행은 최신 완료 session과 정확히 같아야 한다. 놓친 session은 operator가 그 바로 다음
    session을 명시한 별도 bounded packet으로만 한 칸씩 복구하며 다중 자동 catch-up은 만들지 않는다.
    """

    if cutoff.tzinfo is None:
        raise LightGbmContractError("daily packet cutoff must be timezone aware")
    calendar = _calendar()
    prior = calendar.date_to_session(state.session_date.isoformat(), direction="none")
    expected = cast(date, calendar.next_session(prior).date())
    from app.lightgbm.pit_calendar import latest_completed_session

    latest = latest_completed_session(cutoff)
    target = requested_session or latest
    if target != expected or target > latest:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: daily refresh is not exactly one session")
    if requested_session is None and latest != expected:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: daily refresh is not exactly one session")
    as_of = next_xkrx_evidence_clock(target)
    if as_of > cutoff:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: daily evidence clock has not matured")
    payload = {
        "packetVersion": DAILY_PACKET_VERSION,
        "previousStateSha256": state.sha256,
        "bootstrapPacketSha256": state.bootstrap_packet_sha256,
        "releaseManifestSha256": state.release_manifest_sha256,
        "sessionDate": target.isoformat(),
        "asOf": _utc(as_of),
        "operations": {
            "KRX": list(KRX_OPERATIONS),
            "KIS": [KIS_OPERATION],
            "ECOS": list(ECOS_OPERATIONS),
        },
        "limits": {
            "krxMaxGet": DAILY_KRX_MAX,
            "kisMaxGet": DAILY_KIS_MAX,
            "kisTokenMax": DAILY_KIS_TOKEN_MAX,
            "ecosMaxGet": DAILY_ECOS_MAX,
            "totalMaxPhysicalCalls": DAILY_TOTAL_MAX,
            "retry": 0,
            "costMax": 0,
            "accountBalanceOrderCalls": 0,
        },
    }
    content = canonical_json_bytes(payload)
    return DailyRefreshPacket(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        previous_state_sha256=state.sha256,
        bootstrap_packet_sha256=state.bootstrap_packet_sha256,
        release_manifest_sha256=state.release_manifest_sha256,
        session_date=target,
        as_of=as_of,
    )


def validate_daily_refresh_packet(
    content: bytes,
    *,
    expected_sha256: str,
    state: DailyInferenceState,
) -> DailyRefreshPacket:
    """packet을 state에서 재생성해 path나 provider 범위 변경을 전부 거부한다."""

    _require_sha(expected_sha256, "daily packet")
    payload = _parse_mapping(content, _PACKET_FIELDS, "daily packet")
    if canonical_json_bytes(payload) != content or payload["packetVersion"] != DAILY_PACKET_VERSION:
        raise LightGbmContractError("daily packet is noncanonical or version invalid")
    session_date = _date(payload["sessionDate"], "sessionDate")
    as_of = _datetime(payload["asOf"], "asOf")
    if (
        hashlib.sha256(content).hexdigest() != expected_sha256
        or payload["previousStateSha256"] != state.sha256
        or payload["bootstrapPacketSha256"] != state.bootstrap_packet_sha256
        or payload["releaseManifestSha256"] != state.release_manifest_sha256
        or payload["operations"]
        != {"KRX": list(KRX_OPERATIONS), "KIS": [KIS_OPERATION], "ECOS": list(ECOS_OPERATIONS)}
        or payload["limits"]
        != {
            "krxMaxGet": 7,
            "kisMaxGet": 31,
            "kisTokenMax": 1,
            "ecosMaxGet": 2,
            "totalMaxPhysicalCalls": 41,
            "retry": 0,
            "costMax": 0,
            "accountBalanceOrderCalls": 0,
        }
    ):
        raise LightGbmContractError("daily packet trust anchor or authority is invalid")
    expected_session = cast(
        date,
        _calendar()
        .next_session(
            _calendar().date_to_session(state.session_date.isoformat(), direction="none")
        )
        .date(),
    )
    if session_date != expected_session or as_of != next_xkrx_evidence_clock(session_date):
        raise LightGbmContractError("daily packet session clock is invalid")
    return DailyRefreshPacket(
        content=content,
        sha256=expected_sha256,
        previous_state_sha256=state.sha256,
        bootstrap_packet_sha256=state.bootstrap_packet_sha256,
        release_manifest_sha256=state.release_manifest_sha256,
        session_date=session_date,
        as_of=as_of,
    )


def execute_daily_refresh(
    *,
    packet: DailyRefreshPacket,
    state: DailyInferenceState,
    state_root: Path,
    run_root: Path,
    feature_root: Path,
    release_root: Path,
    krx: KrxBootstrapProvider,
    kis: KisBootstrapProvider,
    ecos: EcosBootstrapProvider,
    ecos_series: Sequence[ECOSSeries],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> DailyRefreshResult:
    """KRX→universe→KIS→ECOS 순서로 실패 즉시 중단하고 exact-31 batch를 만든다."""

    if packet.previous_state_sha256 != state.sha256:
        raise LightGbmContractError("daily packet does not bind the previous state")
    budgeted_calls = 0
    target = packet.session_date
    krx_operations = _daily_krx_operations(state=state, packet=packet)
    krx_rows: dict[str, tuple[dict[str, str], ...]] = {}
    krx_receipts: dict[str, TemporalReceipt] = {}
    for service in krx_operations:
        rows = krx.fetch(service=service, session_date=target)
        budgeted_calls += 1
        if not rows:
            raise DatasetUnavailable("DATASET_UNAVAILABLE: daily KRX projection is empty")
        receipt = _receipt_for_rows(
            source="KRX",
            operation=service,
            observation_date=target,
            retrieved_at=_clock(clock),
            query={"service": service, "basDd": target.strftime("%Y%m%d")},
            rows=rows,
        )
        krx_rows[service] = tuple(dict(row) for row in rows)
        krx_receipts[service] = receipt
    history = _advance_krx_history(state.krx_history, target, krx_rows, krx_receipts)
    universe, listing_markets = _resolve_daily_universe(
        state=state,
        packet=packet,
        history=history,
        krx_rows=krx_rows,
        krx_receipts=krx_receipts,
    )
    kis.prepare_access_token()
    budgeted_calls += 1
    kis.require_cached_token_only()
    sessions_60 = _last_sessions(target, 60)
    prices: list[ProductionPriceEvidence] = []
    identity_by_symbol = dict(zip(universe.symbols, universe.instrument_ids, strict=True))
    start = target - timedelta(days=150)
    for symbol in sorted(universe.symbols):
        bars = kis.fetch_page(symbol=symbol, start=start, end=target)
        budgeted_calls += 1
        prices.extend(
            _daily_price_rows(
                identity=identity_by_symbol[symbol],
                symbol=symbol,
                bars=bars,
                required_sessions=sessions_60,
                retrieved_at=_clock(clock),
                start=start,
                end=target,
            )
        )
    prices_tuple = tuple(sorted(prices, key=lambda row: (row.session_date, row.symbol)))
    _require_exact_price_window(prices_tuple, universe, sessions_60)
    indices = _advance_indices(state.indices, target, krx_rows, krx_receipts, sessions_60)
    macro = list(state.macro)
    for series in _validated_series(ecos_series):
        observations = ecos.fetch(series=series, start=target, end=target)
        budgeted_calls += 1
        macro = _advance_macro(
            macro,
            series=series,
            observations=observations,
            target=target,
            retrieved_at=_clock(clock),
        )
    expected_budget = len(krx_operations) + DAILY_KIS_TOKEN_MAX + len(universe.symbols) + len(
        _validated_series(ecos_series)
    )
    if budgeted_calls != expected_budget or budgeted_calls > DAILY_TOTAL_MAX:
        raise LightGbmContractError("daily provider operation budget is invalid")
    macro_tuple = _trim_macro(tuple(macro), sessions_60)
    inference_table = _build_inference_table(
        packet=packet,
        universe=universe,
        listing_markets=listing_markets,
        prices=prices_tuple,
        indices=indices,
        macro=macro_tuple,
    )
    release = load_qualified_production_release(
        release_root=release_root,
        expected_release_manifest_sha256=state.release_manifest_sha256,
        feature_root=feature_root,
        expected_feature_manifest_sha256=state.feature_manifest_sha256,
    )
    batch = write_production_signal_batch(
        release=release,
        inference_universe=universe,
        inference_table=inference_table,
        session_date=target,
        as_of=packet.as_of,
        batch_root=run_root / "batch",
    )
    new_state = _write_state(
        state_root=state_root,
        bootstrap_packet_sha256=state.bootstrap_packet_sha256,
        source_manifest_sha256=state.source_manifest_sha256,
        feature_manifest_sha256=state.feature_manifest_sha256,
        release_manifest_sha256=state.release_manifest_sha256,
        previous_state_sha256=state.sha256,
        session_date=target,
        as_of=packet.as_of,
        universe=universe,
        listing_markets=listing_markets,
        prices=prices_tuple,
        indices=indices,
        macro=macro_tuple,
        krx_history=history,
    )
    return DailyRefreshResult(state=new_state, batch=batch, budgeted_calls=budgeted_calls)


def _daily_krx_operations(
    *, state: DailyInferenceState, packet: DailyRefreshPacket
) -> tuple[str, ...]:
    """월 경계에서만 identity/ETF evidence를 추가해 KRX 7 GET 상한을 소비한다."""

    evidence_day = packet.as_of.astimezone(KST).date()
    effective_month = f"{evidence_day.year:04d}-{evidence_day.month:02d}"
    daily = ("stk_bydd_trd", "ksq_bydd_trd", "kospi_dd_trd", "kosdaq_dd_trd")
    if effective_month == state.universe.effective_month:
        return daily
    return tuple(KRX_OPERATIONS)


def _write_state(
    *,
    state_root: Path,
    bootstrap_packet_sha256: str,
    source_manifest_sha256: str,
    feature_manifest_sha256: str,
    release_manifest_sha256: str,
    previous_state_sha256: str | None,
    session_date: date,
    as_of: datetime,
    universe: MonthlyUniverse,
    listing_markets: Mapping[str, str],
    prices: Sequence[ProductionPriceEvidence],
    indices: Sequence[IndexEvidence],
    macro: Sequence[MacroObservation],
    krx_history: Sequence[DailyKrxProjection],
) -> DailyInferenceState:
    payload = {
        "stateVersion": DAILY_STATE_VERSION,
        "bootstrapPacketSha256": _require_sha(bootstrap_packet_sha256, "bootstrap packet"),
        "sourceManifestSha256": _require_sha(source_manifest_sha256, "source manifest"),
        "featureManifestSha256": _require_sha(feature_manifest_sha256, "feature manifest"),
        "releaseManifestSha256": _require_sha(release_manifest_sha256, "release manifest"),
        "previousStateSha256": previous_state_sha256,
        "sessionDate": session_date.isoformat(),
        "asOf": _utc(as_of),
        "universe": {
            "selectionSession": universe.selection_session.isoformat(),
            "effectiveMonth": universe.effective_month,
            "instrumentIds": list(universe.instrument_ids),
            "symbols": list(universe.symbols),
        },
        "listingMarkets": dict(sorted(listing_markets.items())),
        "prices": [_price_mapping(row) for row in prices],
        "indices": [_index_mapping(row) for row in indices],
        "macro": [_macro_mapping(row) for row in macro],
        "krxHistory": [_krx_projection_mapping(row) for row in krx_history],
    }
    content = canonical_json_bytes(payload)
    if len(content) > DAILY_STATE_MAX_BYTES:
        raise LightGbmContractError("daily state exceeds bounded size")
    digest = hashlib.sha256(content).hexdigest()
    try:
        write_approved_new_file(
            approved_root=state_root,
            relative_path=f"state-{digest}.json",
            content=content,
            max_bytes=DAILY_STATE_MAX_BYTES,
        )
    except RagSafeIoError as error:
        # content-addressed state의 exact replay만 허용하고 기존 inode를 덮어쓰지 않는다.
        try:
            existing = read_approved_regular_file(
                approved_root=state_root,
                relative_path=f"state-{digest}.json",
                max_bytes=DAILY_STATE_MAX_BYTES,
            )
        except RagSafeIoError:
            raise LightGbmContractError("daily state publish boundary is invalid") from error
        if existing.content != content or existing.content_sha256 != digest:
            raise LightGbmContractError("daily state resume content conflict") from error
    return read_daily_state(state_root=state_root, expected_sha256=digest)


def _resolve_daily_universe(
    *,
    state: DailyInferenceState,
    packet: DailyRefreshPacket,
    history: tuple[DailyKrxProjection, ...],
    krx_rows: Mapping[str, tuple[dict[str, str], ...]],
    krx_receipts: Mapping[str, TemporalReceipt],
) -> tuple[MonthlyUniverse, dict[str, str]]:
    evidence_day = packet.as_of.astimezone(KST).date()
    effective_month = f"{evidence_day.year:04d}-{evidence_day.month:02d}"
    if effective_month == state.universe.effective_month:
        return state.universe, dict(state.listing_markets)
    schedule = derive_monthly_universe_schedule(effective_month, dataset_cutoff=packet.as_of)
    if schedule.selection_session != packet.session_date:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: monthly universe rollover evidence is missing")
    by_service_day = {(row.service, row.session_date): row for row in history}
    base_rows: dict[str, tuple[dict[str, str], str, str]] = {}
    for service, market in (("stk_isu_base_info", "KOSPI"), ("ksq_isu_base_info", "KOSDAQ")):
        for row in krx_rows[service]:
            symbol = row["ISU_SRT_CD"]
            if symbol in base_rows:
                raise DatasetUnavailable("SOURCE_SNAPSHOT_CONFLICT: daily identity is ambiguous")
            base_rows[symbol] = (row, service, market)
    observations: list[ProductionUniverseObservation] = []
    for session in schedule.trailing_sessions:
        for service, market in (("stk_bydd_trd", "KOSPI"), ("ksq_bydd_trd", "KOSDAQ")):
            projection = by_service_day.get((service, session))
            if projection is None:
                raise DatasetUnavailable("DATASET_UNAVAILABLE: trailing universe row is missing")
            for trading in projection.rows:
                base = base_rows.get(trading["ISU_CD"])
                if base is None:
                    continue
                identity_row, identity_service, _ = base
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
                        session_date=session,
                        trading_value=_number(trading["ACC_TRDVAL"], allow_zero=True),
                        market_cap=_number(trading["MKTCAP"], allow_zero=True),
                        market=market,
                        security_type=classification.value,
                        common_share=classification is SecurityClassification.COMMON_STOCK,
                        listed=True,
                        trading_receipt=projection.receipt,
                        identity_receipt=krx_receipts[identity_service],
                    )
                )
    etf = [row for row in krx_rows["etf_bydd_trd"] if row["ISU_CD"] == FIXED_ETF_SYMBOL]
    if len(etf) != 1:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: fixed ETF evidence is missing")
    observations.append(
        ProductionUniverseObservation(
            instrument_id="XKRX:ETF:132030",
            symbol=FIXED_ETF_SYMBOL,
            session_date=schedule.selection_session,
            trading_value=_number(etf[0]["ACC_TRDVAL"], allow_zero=True),
            market_cap=_number(etf[0]["MKTCAP"], allow_zero=True),
            market="KOSPI",
            security_type="ETF",
            common_share=False,
            listed=True,
            trading_receipt=krx_receipts["etf_bydd_trd"],
            identity_receipt=krx_receipts["etf_bydd_trd"],
        )
    )
    universe = select_production_monthly_universe(observations, schedule=schedule)
    markets = {
        identity: next(
            row.market
            for row in observations
            if row.instrument_id == identity and row.session_date == schedule.selection_session
        )
        for identity in universe.instrument_ids
    }
    return universe, markets


def _build_inference_table(
    *,
    packet: DailyRefreshPacket,
    universe: MonthlyUniverse,
    listing_markets: Mapping[str, str],
    prices: Sequence[ProductionPriceEvidence],
    indices: Sequence[IndexEvidence],
    macro: Sequence[MacroObservation],
) -> pa.Table:
    rows: list[dict[str, object]] = []
    sessions = _last_sessions(packet.session_date, 60)
    for identity in universe.instrument_ids:
        symbol_prices = [row for row in prices if row.instrument_id == identity]
        feature_rows = build_production_core_feature_rows(
            symbol_prices,
            indices,
            macro,
            listing_market_by_session={day: listing_markets[identity] for day in sessions},
            cutoff=packet.as_of,
        )
        current = [row for row in feature_rows if row.session_date == packet.session_date]
        if len(current) != 1:
            raise DatasetUnavailable("DATASET_UNAVAILABLE: daily feature row is missing")
        rows.append(current[0].as_mapping())
    rows.sort(key=lambda row: str(row["symbol"]))
    return feature_table_from_rows(rows)


def _daily_price_rows(
    *,
    identity: str,
    symbol: str,
    bars: Sequence[DailyBar],
    required_sessions: Sequence[date],
    retrieved_at: datetime,
    start: date,
    end: date,
) -> tuple[ProductionPriceEvidence, ...]:
    by_date = {bar.date: bar for bar in bars}
    if len(by_date) != len(bars) or not set(required_sessions).issubset(by_date):
        raise DatasetUnavailable("KIS_HISTORY_UNAVAILABLE")
    projection = [
        {
            "symbol": bar.symbol,
            "date": bar.date.isoformat(),
            "open": bar.open,
            "close": bar.close,
            "volume": bar.volume,
            "flngClsCode": bar.flng_cls_code,
            "prttRate": format(bar.prtt_rate, "f"),
            "modYn": bar.mod_yn,
            "revlIssuReas": bar.revl_issu_reas,
        }
        for bar in sorted(bars, key=lambda row: row.date)
    ]
    query = {
        "operation": KIS_OPERATION,
        "symbol": symbol,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "adjusted": "0",
    }
    snapshot_sha = hashlib.sha256(canonical_json_bytes(projection)).hexdigest()
    request_sha = hashlib.sha256(
        b"s5-provider-query-v1\x00" + canonical_json_bytes(query)
    ).hexdigest()
    output = []
    for session in required_sessions:
        bar = by_date[session]
        receipt = _receipt(
            source="KIS",
            operation=KIS_OPERATION,
            observation_date=session,
            retrieved_at=retrieved_at,
            request_sha256=request_sha,
            snapshot_sha256=snapshot_sha,
        )
        output.append(
            ProductionPriceEvidence(
                instrument_id=identity,
                symbol=symbol,
                session_date=session,
                adjusted_open=float(bar.open),
                adjusted_close=float(bar.close),
                volume=float(bar.volume),
                flng_cls_code=bar.flng_cls_code,
                prtt_rate=float(bar.prtt_rate),
                mod_yn=bar.mod_yn,
                revl_issu_reas=bar.revl_issu_reas,
                receipt=receipt,
            )
        )
    return tuple(output)


def _advance_indices(
    prior: Sequence[IndexEvidence],
    target: date,
    rows: Mapping[str, tuple[dict[str, str], ...]],
    receipts: Mapping[str, TemporalReceipt],
    sessions: Sequence[date],
) -> tuple[IndexEvidence, ...]:
    output = [row for row in prior if row.session_date in set(sessions[:-1])]
    for service, market, names in (
        ("kospi_dd_trd", "KOSPI", {"코스피", "KOSPI"}),
        ("kosdaq_dd_trd", "KOSDAQ", {"코스닥", "KOSDAQ"}),
    ):
        matches = [row for row in rows[service] if row["IDX_NM"].strip() in names]
        if len(matches) != 1:
            raise DatasetUnavailable("DATASET_UNAVAILABLE: daily market index is missing")
        output.append(
            IndexEvidence(
                session_date=target,
                market=market,
                adjusted_close=_number(matches[0]["CLSPRC_IDX"]),
                receipt=receipts[service],
            )
        )
    result = tuple(sorted(output, key=lambda row: (row.session_date, row.market)))
    _require_index_window(result, sessions)
    return result


def _advance_macro(
    prior: Sequence[MacroObservation],
    *,
    series: ECOSSeries,
    observations: Sequence[ECOSObservation],
    target: date,
    retrieved_at: datetime,
) -> list[MacroObservation]:
    projection = [{"time": row.time, "value": row.value} for row in observations]
    request = {
        "operation": f"{series.stat_code}/{series.item_code1}/{series.cycle}",
        "start": target.isoformat(),
        "end": target.isoformat(),
    }
    snapshot_sha = hashlib.sha256(canonical_json_bytes(projection)).hexdigest()
    request_sha = hashlib.sha256(
        b"s5-provider-query-v1\x00" + canonical_json_bytes(request)
    ).hexdigest()
    output = [row for row in prior if row.series_id != series.series_id or row.observation_date != target]
    for observation in observations:
        day = datetime.strptime(observation.time, "%Y%m%d").date()
        value = Decimal(observation.value)
        if not value.is_finite() or (series.series_id == "krw-usd-rate" and value <= 0):
            raise DatasetUnavailable("DATASET_UNAVAILABLE: daily ECOS value is invalid")
        output.append(
            MacroObservation(
                series_id=series.series_id,
                observation_date=day,
                value=float(value),
                receipt=_receipt(
                    source="ECOS",
                    operation=str(request["operation"]),
                    observation_date=day,
                    retrieved_at=retrieved_at,
                    request_sha256=request_sha,
                    snapshot_sha256=snapshot_sha,
                ),
            )
        )
    if series.series_id == "krw-usd-rate" and not any(
        row.series_id == series.series_id and row.observation_date == target for row in output
    ):
        raise DatasetUnavailable("DATASET_UNAVAILABLE: daily USDKRW observation is missing")
    return output


def _advance_krx_history(
    prior: Sequence[DailyKrxProjection],
    target: date,
    rows: Mapping[str, tuple[dict[str, str], ...]],
    receipts: Mapping[str, TemporalReceipt],
) -> tuple[DailyKrxProjection, ...]:
    sessions = set(_last_sessions(target, 20))
    output = [row for row in prior if row.session_date in sessions]
    for service in ("stk_bydd_trd", "ksq_bydd_trd"):
        output.append(
            DailyKrxProjection(
                session_date=target,
                service=service,
                rows=rows[service],
                receipt=receipts[service],
            )
        )
    result = tuple(sorted(output, key=lambda row: (row.session_date, row.service)))
    _require_history(result, tuple(sorted(sessions)))
    return result


def _trim_macro(
    rows: Sequence[MacroObservation], sessions: Sequence[date]
) -> tuple[MacroObservation, ...]:
    session_set = set(sessions)
    fx = [row for row in rows if row.series_id == "krw-usd-rate" and row.observation_date in session_set]
    if {row.observation_date for row in fx} != session_set:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: daily USDKRW window is incomplete")
    rate_rows = sorted(
        (row for row in rows if row.series_id == "policy-rate" and row.observation_date <= sessions[-1]),
        key=lambda row: row.observation_date,
    )
    seed = [row for row in rate_rows if row.observation_date <= sessions[0]]
    if not seed:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: daily base-rate seed is missing")
    retained_rate = [seed[-1], *[row for row in rate_rows if row.observation_date > sessions[0]]]
    deduped: dict[tuple[str, date], MacroObservation] = {}
    for row in (*retained_rate, *fx):
        key = (row.series_id, row.observation_date)
        prior = deduped.get(key)
        if prior is not None and prior != row:
            raise DatasetUnavailable("SOURCE_SNAPSHOT_CONFLICT")
        deduped[key] = row
    return tuple(sorted(deduped.values(), key=lambda row: (row.observation_date, row.series_id)))


def _require_exact_price_window(
    rows: Sequence[ProductionPriceEvidence],
    universe: MonthlyUniverse,
    sessions: Sequence[date],
) -> None:
    expected = {(identity, session) for identity in universe.instrument_ids for session in sessions}
    actual = {(row.instrument_id, row.session_date) for row in rows}
    if actual != expected or len(rows) != len(expected):
        raise DatasetUnavailable("DATASET_UNAVAILABLE: daily price window is incomplete")


def _require_index_window(rows: Sequence[IndexEvidence], sessions: Sequence[date]) -> None:
    expected = {(market, session) for market in ("KOSPI", "KOSDAQ") for session in sessions}
    actual = {(row.market, row.session_date) for row in rows}
    if actual != expected or len(rows) != len(expected):
        raise DatasetUnavailable("DATASET_UNAVAILABLE: daily index window is incomplete")


def _require_history(rows: Sequence[DailyKrxProjection], sessions: Sequence[date]) -> None:
    expected = {(service, session) for service in ("stk_bydd_trd", "ksq_bydd_trd") for session in sessions}
    actual = {(row.service, row.session_date) for row in rows}
    if actual != expected or len(rows) != len(expected):
        raise DatasetUnavailable("DATASET_UNAVAILABLE: daily KRX history is incomplete")


def _last_sessions(session_date: date, count: int) -> tuple[date, ...]:
    calendar = _calendar()
    last = calendar.date_to_session(session_date.isoformat(), direction="none")
    first = calendar.session_offset(last, -(count - 1))
    return tuple(cast(date, value.date()) for value in calendar.sessions_in_range(first, last))


def _validated_series(series: Sequence[ECOSSeries]) -> tuple[ECOSSeries, ...]:
    values = tuple(sorted(series, key=lambda row: row.series_id))
    expected = {
        ("policy-rate", "722Y001", "0101000", "D"),
        ("krw-usd-rate", "731Y001", "0000001", "D"),
    }
    if len(values) != 2 or any(not row.verified for row in values) or {
        (row.series_id, row.stat_code, row.item_code1, row.cycle) for row in values
    } != expected:
        raise LightGbmContractError("daily ECOS series are not exact")
    return values


def _receipt_for_rows(
    *,
    source: str,
    operation: str,
    observation_date: date,
    retrieved_at: datetime,
    query: Mapping[str, object],
    rows: Sequence[Mapping[str, str]],
) -> TemporalReceipt:
    request_sha = hashlib.sha256(
        b"s5-provider-query-v1\x00" + canonical_json_bytes(dict(query))
    ).hexdigest()
    snapshot_sha = hashlib.sha256(
        canonical_json_bytes([dict(row) for row in rows])
    ).hexdigest()
    return _receipt(
        source=source,
        operation=operation,
        observation_date=observation_date,
        retrieved_at=retrieved_at,
        request_sha256=request_sha,
        snapshot_sha256=snapshot_sha,
    )


def _receipt(
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
        retrieved_at=retrieved_at.astimezone(UTC),
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


def _clock(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None:
        raise LightGbmContractError("daily provider clock must be timezone aware")
    return value.astimezone(UTC)


def _number(value: str, *, allow_zero: bool = False) -> float:
    try:
        parsed = float(value.replace(",", ""))
    except ValueError:
        raise DatasetUnavailable("DATASET_UNAVAILABLE: daily provider numeric is invalid") from None
    if not math.isfinite(parsed) or not (parsed >= 0 if allow_zero else parsed > 0):
        raise DatasetUnavailable("DATASET_UNAVAILABLE: daily provider numeric is invalid")
    return parsed


def _price_mapping(row: ProductionPriceEvidence) -> dict[str, object]:
    return {
        "instrumentId": row.instrument_id,
        "symbol": row.symbol,
        "sessionDate": row.session_date.isoformat(),
        "adjustedOpen": row.adjusted_open,
        "adjustedClose": row.adjusted_close,
        "volume": row.volume,
        "flngClsCode": row.flng_cls_code,
        "prttRate": row.prtt_rate,
        "modYn": row.mod_yn,
        "revlIssuReas": row.revl_issu_reas,
        "receipt": row.receipt.as_dict(),
    }


def _index_mapping(row: IndexEvidence) -> dict[str, object]:
    return {
        "sessionDate": row.session_date.isoformat(),
        "market": row.market,
        "adjustedClose": row.adjusted_close,
        "receipt": row.receipt.as_dict(),
    }


def _macro_mapping(row: MacroObservation) -> dict[str, object]:
    return {
        "seriesId": row.series_id,
        "observationDate": row.observation_date.isoformat(),
        "value": row.value,
        "receipt": row.receipt.as_dict(),
    }


def _krx_projection_mapping(row: DailyKrxProjection) -> dict[str, object]:
    return {
        "sessionDate": row.session_date.isoformat(),
        "service": row.service,
        "rows": list(row.rows),
        "receipt": row.receipt.as_dict(),
    }


def _parse_universe(value: object) -> MonthlyUniverse:
    row = _closed(value, {"selectionSession", "effectiveMonth", "instrumentIds", "symbols"}, "universe")
    identities = tuple(_text(item, "instrumentId") for item in _list(row["instrumentIds"], "instrumentIds"))
    symbols = tuple(_text(item, "symbol") for item in _list(row["symbols"], "symbols"))
    if len(identities) != 31 or len(symbols) != 31 or len(set(identities)) != 31 or len(set(symbols)) != 31:
        raise LightGbmContractError("daily universe must be exact 31")
    return MonthlyUniverse(
        selection_session=_date(row["selectionSession"], "selectionSession"),
        effective_month=_text(row["effectiveMonth"], "effectiveMonth"),
        instrument_ids=identities,
        symbols=symbols,
    )


def _parse_listing_markets(value: object, universe: MonthlyUniverse) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != set(universe.instrument_ids):
        raise LightGbmContractError("daily listing market mapping is invalid")
    output = {str(key): _text(item, "listingMarket") for key, item in value.items()}
    if any(market not in {"KOSPI", "KOSDAQ"} for market in output.values()):
        raise LightGbmContractError("daily listing market is invalid")
    return output


def _parse_price(value: object) -> ProductionPriceEvidence:
    row = _closed(
        value,
        {
            "instrumentId", "symbol", "sessionDate", "adjustedOpen", "adjustedClose",
            "volume", "flngClsCode", "prttRate", "modYn", "revlIssuReas", "receipt",
        },
        "price",
    )
    adjusted_open = row["adjustedOpen"]
    return ProductionPriceEvidence(
        instrument_id=_text(row["instrumentId"], "instrumentId"),
        symbol=_text(row["symbol"], "symbol"),
        session_date=_date(row["sessionDate"], "sessionDate"),
        adjusted_open=(None if adjusted_open is None else _float(adjusted_open, "adjustedOpen")),
        adjusted_close=_float(row["adjustedClose"], "adjustedClose"),
        volume=_float(row["volume"], "volume"),
        flng_cls_code=_text(row["flngClsCode"], "flngClsCode", allow_empty=True),
        prtt_rate=_float(row["prttRate"], "prttRate"),
        mod_yn=_text(row["modYn"], "modYn"),
        revl_issu_reas=_text(row["revlIssuReas"], "revlIssuReas", allow_empty=True),
        receipt=_parse_receipt(row["receipt"]),
    )


def _parse_index(value: object) -> IndexEvidence:
    row = _closed(value, {"sessionDate", "market", "adjustedClose", "receipt"}, "index")
    return IndexEvidence(
        session_date=_date(row["sessionDate"], "sessionDate"),
        market=_text(row["market"], "market"),
        adjusted_close=_float(row["adjustedClose"], "adjustedClose"),
        receipt=_parse_receipt(row["receipt"]),
    )


def _parse_macro(value: object) -> MacroObservation:
    row = _closed(value, {"seriesId", "observationDate", "value", "receipt"}, "macro")
    return MacroObservation(
        series_id=_text(row["seriesId"], "seriesId"),
        observation_date=_date(row["observationDate"], "observationDate"),
        value=_float(row["value"], "value"),
        receipt=_parse_receipt(row["receipt"]),
    )


def _parse_krx_projection(value: object) -> DailyKrxProjection:
    row = _closed(value, {"sessionDate", "service", "rows", "receipt"}, "krxHistory")
    service = _text(row["service"], "service")
    if service not in {"stk_bydd_trd", "ksq_bydd_trd"}:
        raise LightGbmContractError("daily KRX history service is invalid")
    parsed_rows = []
    for item in _list(row["rows"], "rows"):
        if not isinstance(item, dict) or not item or any(
            not isinstance(key, str) or not isinstance(child, str) for key, child in item.items()
        ):
            raise LightGbmContractError("daily KRX history row is invalid")
        parsed_rows.append(dict(cast(dict[str, str], item)))
    return DailyKrxProjection(
        session_date=_date(row["sessionDate"], "sessionDate"),
        service=service,
        rows=tuple(parsed_rows),
        receipt=_parse_receipt(row["receipt"]),
    )


def _parse_receipt(value: object) -> TemporalReceipt:
    required = {
        "sourceId", "operationId", "observationDate", "retrievedAt", "availabilityBasis",
        "revisionBasis", "requestSha256", "snapshotSha256", "temporalPolicyVersion",
        "temporalQuality",
    }
    optional = {"providerAvailableAt", "policyEffectiveAt", "providerRevision"}
    if not isinstance(value, dict) or not required.issubset(value) or not set(value).issubset(required | optional):
        raise LightGbmContractError("daily temporal receipt is not closed")
    row = cast(dict[str, object], value)
    try:
        return TemporalReceipt(
            source_id=_text(row["sourceId"], "sourceId"),
            operation_id=_text(row["operationId"], "operationId"),
            observation_date=_date(row["observationDate"], "observationDate"),
            retrieved_at=_datetime(row["retrievedAt"], "retrievedAt"),
            availability_basis=AvailabilityBasis(_text(row["availabilityBasis"], "availabilityBasis")),
            revision_basis=RevisionBasis(_text(row["revisionBasis"], "revisionBasis")),
            request_sha256=_sha(row["requestSha256"], "requestSha256"),
            snapshot_sha256=_sha(row["snapshotSha256"], "snapshotSha256"),
            temporal_quality=TemporalQuality(_text(row["temporalQuality"], "temporalQuality")),
            provider_available_at=(
                _datetime(row["providerAvailableAt"], "providerAvailableAt")
                if "providerAvailableAt" in row else None
            ),
            policy_effective_at=(
                _datetime(row["policyEffectiveAt"], "policyEffectiveAt")
                if "policyEffectiveAt" in row else None
            ),
            provider_revision=(
                _text(row["providerRevision"], "providerRevision")
                if "providerRevision" in row else None
            ),
            temporal_policy_version=_text(row["temporalPolicyVersion"], "temporalPolicyVersion"),
        )
    except ValueError:
        raise LightGbmContractError("daily temporal receipt enum is invalid") from None


def _parse_mapping(content: bytes, fields: frozenset[str], label: str) -> dict[str, object]:
    try:
        value = parse_bounded_json_bytes(content, limits=_JSON_LIMITS)
    except BoundedJsonError as error:
        raise LightGbmContractError(f"{label} JSON is invalid") from error
    return _closed(value, fields, label)


def _closed(value: object, fields: set[str] | frozenset[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise LightGbmContractError(f"{label} field set is invalid")
    return cast(dict[str, object], value)


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise LightGbmContractError(f"{label} must be a list")
    return value


def _text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value and not allow_empty):
        raise LightGbmContractError(f"{label} must be text")
    return value


def _date(value: object, label: str) -> date:
    try:
        parsed = date.fromisoformat(_text(value, label))
    except ValueError:
        raise LightGbmContractError(f"{label} date is invalid") from None
    return parsed


def _datetime(value: object, label: str) -> datetime:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00") if text.endswith("Z") else datetime.fromisoformat(text)
    except ValueError:
        raise LightGbmContractError(f"{label} datetime is invalid") from None
    if parsed.tzinfo is None:
        raise LightGbmContractError(f"{label} datetime must be timezone aware")
    return parsed


def _float(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LightGbmContractError(f"{label} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise LightGbmContractError(f"{label} must be finite")
    return parsed


def _sha(value: object, label: str) -> str:
    text = _text(value, label)
    return _require_sha(text, label)


def _require_sha(value: str, label: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise LightGbmContractError(f"{label} SHA-256 is invalid")
    return value


def _utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise LightGbmContractError("daily datetime must be timezone aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
