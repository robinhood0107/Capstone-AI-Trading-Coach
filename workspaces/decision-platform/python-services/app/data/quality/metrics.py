from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import math
from statistics import median
from uuid import UUID, uuid5

from app.data._shared.canonical_json import canonical_json_sha256
from app.data.kis.accounting import PhysicalChannel
from app.data.quality.models import (
    AnalysisContext,
    BoundedSample,
    CalendarSummary,
    DataClassification,
    EvidenceCompleteness,
    InputProvenance,
    KISDataQualityReport,
    MetricStatus,
    QualityStatus,
    RateMetric,
    ReportCounts,
    ReportStatus,
    RetentionMetadata,
    SymbolDataset,
)
from app.data.quality.policy import (
    ABRUPT_RETURN_THRESHOLD,
    CANONICAL_DAILY_COLUMNS,
    MAX_FILES,
    MAX_ROWS,
    MAX_SAMPLES,
    MAX_SAMPLES_PER_RULE,
    METRIC_POLICY_VERSION,
    MINIMUM_OUTLIER_HISTORY,
    MODIFIED_Z_THRESHOLD,
    TRAILING_OBSERVATIONS,
    rate_ppm,
)


_REPORT_NAMESPACE = UUID("c24453a2-6a19-55d6-8aa1-6cb3e48f6c16")
_SYMBOL_DIGITS = frozenset("0123456789")


@dataclass(frozen=True)
class _ValidRow:
    symbol: str
    session_date: date
    open: int
    high: int
    low: int
    close: int
    volume: int
    turnover: int

    @property
    def signature(self) -> tuple[int, int, int, int, int, int]:
        return (self.open, self.high, self.low, self.close, self.volume, self.turnover)


@dataclass(frozen=True)
class _SampleCandidate:
    rule_code: str
    symbol: str
    session_date: date
    derived: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class _MetricCounts:
    numerator: int | None
    denominator: int | None
    status: MetricStatus


def modified_z_score(value: float, history: Sequence[float]) -> float | None:
    """현재값을 제외한 직전 60개에서 modified-z를 계산하고 증거 부족 시 대체하지 않는다."""
    trailing = tuple(history[-TRAILING_OBSERVATIONS:])
    if len(trailing) < MINIMUM_OUTLIER_HISTORY:
        return None
    center = float(median(trailing))
    absolute_deviations = tuple(abs(item - center) for item in trailing)
    mad = float(median(absolute_deviations))
    if mad == 0 or not math.isfinite(mad):
        return None
    score = 0.6745 * (value - center) / mad
    return score if math.isfinite(score) else None


def analyze_quality(
    context: AnalysisContext,
    datasets: Iterable[SymbolDataset],
) -> KISDataQualityReport:
    """manifest-pinned 일봉을 수정 없이 aggregate metric과 bounded derived sample로 투영한다.

    clock, Git, filesystem, environment, network를 읽지 않으며 orchestration이 검증해 주입한 context와
    symbol별 bounded rows만 소비한다.
    """
    dataset_items = tuple(datasets)
    if len(dataset_items) > MAX_FILES:
        raise ValueError("dataset file cap exceeded")
    total_raw_rows = sum(len(item.rows) for item in dataset_items)
    if total_raw_rows > MAX_ROWS:
        raise ValueError("dataset row cap exceeded")

    candidates: list[_SampleCandidate] = []
    integrity_numerator, integrity_denominator, valid_rows = _validate_rows(
        context,
        dataset_items,
        candidates,
    )
    canonical_numerator, canonical_denominator, unique_rows = _resolve_duplicates(
        valid_rows,
        candidates,
    )

    coverage = _coverage_metric(context, unique_rows, candidates)
    freshness, stale = _freshness_metrics(context, unique_rows, candidates)
    return_outlier, abrupt_price, volume_spike = _market_shape_metrics(
        context,
        unique_rows,
        candidates,
    )
    ingest, logical, physical = _accounting_metrics(context, total_raw_rows)

    bounded_samples = _bound_samples(candidates)
    sample_counts = Counter(_sample_metric_id(item.rule_code) for item in bounded_samples)
    metrics = (
        _rate_metric(
            "requiredSchemaIntegrity",
            _evaluated_counts(
                integrity_numerator,
                integrity_denominator,
                failure=MetricStatus.FAIL,
            ),
            sample_counts,
        ),
        _rate_metric(
            "canonicalDuplicate",
            _evaluated_counts(
                canonical_numerator,
                canonical_denominator,
                failure=MetricStatus.FAIL,
            ),
            sample_counts,
        ),
        _rate_metric("ingestDuplicate", ingest, sample_counts),
        _rate_metric("currentUniverseHistoricalCoverage", coverage, sample_counts),
        _rate_metric(
            "listingAdjustedCompleteness",
            _MetricCounts(None, None, MetricStatus.NOT_AVAILABLE),
            sample_counts,
        ),
        _rate_metric("datasetFreshness", freshness, sample_counts),
        _rate_metric("perSymbolStale", stale, sample_counts),
        _rate_metric("returnOutlier", return_outlier, sample_counts),
        _rate_metric("abruptPrice", abrupt_price, sample_counts),
        _rate_metric("shareVolumeSpike", volume_spike, sample_counts),
        _rate_metric("logicalApiFailure", logical, sample_counts),
        _rate_metric("physicalAttemptFailure", physical, sample_counts),
    )
    quality_status = _quality_status(metrics)
    fingerprint = _analysis_fingerprint(context)
    report_id = uuid5(_REPORT_NAMESPACE, fingerprint)
    return KISDataQualityReport(
        reportId=report_id,
        analysisFingerprint=fingerprint,
        evaluatedAt=context.evaluated_at,
        softwareRevision=context.software_revision,
        calendar=CalendarSummary(
            windowStart=context.window_start,
            windowEnd=context.window_end,
            expectedLastCompletedXkrxSession=(
                context.expected_last_completed_xkrx_session
            ),
        ),
        inputProvenance=InputProvenance(
            universeManifest=context.universe_manifest,
            datasetManifest=context.dataset_manifest,
            collectionRun=context.collection_run,
        ),
        counts=ReportCounts(
            symbols=len(context.universe_symbols),
            sessions=len(context.sessions),
            files=context.dataset_file_count,
            rows=total_raw_rows,
            samples=len(bounded_samples),
        ),
        status=ReportStatus(
            evidenceCompleteness=(
                EvidenceCompleteness.COMPLETE
                if context.collection_run is not None
                else EvidenceCompleteness.PARTIAL
            ),
            qualityStatus=quality_status,
        ),
        metrics=metrics,
        boundedSamples=bounded_samples,
        dataClassification=DataClassification(),
        retention=RetentionMetadata(),
    )


def _validate_rows(
    context: AnalysisContext,
    datasets: tuple[SymbolDataset, ...],
    candidates: list[_SampleCandidate],
) -> tuple[int, int, tuple[_ValidRow, ...]]:
    session_set = frozenset(context.sessions)
    valid: list[_ValidRow] = []
    invalid_units = 0
    checked_units = len(datasets) + sum(len(item.rows) for item in datasets)

    # 입력 순서가 report identity나 sample 순서를 바꾸지 않도록 file symbol 기준으로만 순회한다.
    for dataset in sorted(datasets, key=lambda item: item.symbol):
        file_symbol_valid = _is_symbol(dataset.symbol)
        if dataset.columns != CANONICAL_DAILY_COLUMNS or not file_symbol_valid:
            invalid_units += 1
            for row in dataset.rows:
                _append_integrity_sample(candidates, dataset.symbol, row)
            continue
        for row in dataset.rows:
            parsed = _parse_row(
                row,
                file_symbol=dataset.symbol,
                session_set=session_set,
                context=context,
            )
            if parsed is None:
                invalid_units += 1
                _append_integrity_sample(candidates, dataset.symbol, row)
            else:
                valid.append(parsed)
    return invalid_units, checked_units, tuple(valid)


def _parse_row(
    row: Mapping[str, object],
    *,
    file_symbol: str,
    session_set: frozenset[date],
    context: AnalysisContext,
) -> _ValidRow | None:
    if set(row) != set(CANONICAL_DAILY_COLUMNS):
        return None
    symbol = row.get("symbol")
    session_date = row.get("date")
    if (
        not isinstance(symbol, str)
        or not _is_symbol(symbol)
        or symbol != file_symbol
        or type(session_date) is not date
        or session_date not in session_set
        or session_date < context.window_start
        or session_date > context.window_end
        or session_date > context.evaluated_at.date()
    ):
        return None
    names = ("open", "high", "low", "close", "volume", "turnover")
    raw_values = tuple(row.get(name) for name in names)
    if any(type(value) is not int for value in raw_values):
        return None
    open_, high, low, close, volume, turnover = raw_values
    assert isinstance(open_, int)
    assert isinstance(high, int)
    assert isinstance(low, int)
    assert isinstance(close, int)
    assert isinstance(volume, int)
    assert isinstance(turnover, int)
    if (
        min(open_, high, low, close) <= 0
        or volume < 0
        or turnover < 0
        or low > min(open_, close)
        or high < max(open_, close)
        or low > high
    ):
        return None
    return _ValidRow(
        symbol=symbol,
        session_date=session_date,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        turnover=turnover,
    )


def _append_integrity_sample(
    candidates: list[_SampleCandidate],
    file_symbol: str,
    row: Mapping[str, object],
) -> None:
    raw_date = row.get("date")
    if not _is_symbol(file_symbol) or type(raw_date) is not date:
        return
    candidates.append(
        _SampleCandidate(
            rule_code="SCHEMA_INTEGRITY",
            symbol=file_symbol,
            session_date=raw_date,
            derived=(("occurrenceCount", 1),),
        )
    )


def _resolve_duplicates(
    rows: tuple[_ValidRow, ...],
    candidates: list[_SampleCandidate],
) -> tuple[int, int, tuple[_ValidRow, ...]]:
    groups: dict[tuple[str, date], list[_ValidRow]] = defaultdict(list)
    for row in rows:
        groups[(row.symbol, row.session_date)].append(row)

    excess_rows = 0
    selected: list[_ValidRow] = []
    for (symbol, session_date), group in sorted(groups.items()):
        if len(group) == 1:
            selected.append(group[0])
            continue
        excess_rows += len(group) - 1
        candidates.append(
            _SampleCandidate(
                rule_code="CANONICAL_DUPLICATE",
                symbol=symbol,
                session_date=session_date,
                derived=(("occurrenceCount", len(group)),),
            )
        )
        signatures = {item.signature for item in group}
        if len(signatures) == 1:
            selected.append(group[0])
        # conflicting group은 임의 행을 downstream 계산에 쓰지 않는다.
    return excess_rows, len(rows), tuple(selected)


def _coverage_metric(
    context: AnalysisContext,
    rows: tuple[_ValidRow, ...],
    candidates: list[_SampleCandidate],
) -> _MetricCounts:
    symbols = tuple(sorted(context.universe_symbols))
    expected = len(symbols) * len(context.sessions)
    if expected == 0:
        return _MetricCounts(0, 0, MetricStatus.NOT_EVALUATED)
    present = {(row.symbol, row.session_date) for row in rows}
    missing = 0
    for symbol in symbols:
        for session_date in context.sessions:
            if (symbol, session_date) in present:
                continue
            missing += 1
            candidates.append(
                _SampleCandidate(
                    rule_code="CURRENT_UNIVERSE_MISSING",
                    symbol=symbol,
                    session_date=session_date,
                    derived=(("occurrenceCount", 1),),
                )
            )
    return _evaluated_counts(missing, expected, failure=MetricStatus.WARN)


def _freshness_metrics(
    context: AnalysisContext,
    rows: tuple[_ValidRow, ...],
    candidates: list[_SampleCandidate],
) -> tuple[_MetricCounts, _MetricCounts]:
    if not context.sessions:
        not_evaluated = _MetricCounts(0, 0, MetricStatus.NOT_EVALUATED)
        return not_evaluated, not_evaluated
    dates = tuple(row.session_date for row in rows)
    is_stale = not dates or max(dates) < context.expected_last_completed_xkrx_session
    freshness = _evaluated_counts(
        int(is_stale),
        1,
        failure=MetricStatus.FAIL,
    )
    if not context.universe_symbols:
        return freshness, _MetricCounts(0, 0, MetricStatus.NOT_EVALUATED)

    session_index = {day: index for index, day in enumerate(context.sessions)}
    expected_index = session_index.get(context.expected_last_completed_xkrx_session)
    if expected_index is None:
        raise ValueError("expected last session must belong to sessions")
    by_symbol: dict[str, list[date]] = defaultdict(list)
    for row in rows:
        by_symbol[row.symbol].append(row.session_date)
    stale_symbols = 0
    for symbol in sorted(context.universe_symbols):
        symbol_dates = by_symbol.get(symbol, [])
        if symbol_dates:
            last_index = session_index[max(symbol_dates)]
            lag = max(0, expected_index - last_index)
        else:
            lag = expected_index + 1
        if lag == 0:
            continue
        stale_symbols += 1
        candidates.append(
            _SampleCandidate(
                rule_code="PER_SYMBOL_STALE",
                symbol=symbol,
                session_date=context.expected_last_completed_xkrx_session,
                derived=(("lagSessions", lag),),
            )
        )
    stale = _evaluated_counts(
        stale_symbols,
        len(context.universe_symbols),
        failure=MetricStatus.WARN,
    )
    return freshness, stale


def _market_shape_metrics(
    context: AnalysisContext,
    rows: tuple[_ValidRow, ...],
    candidates: list[_SampleCandidate],
) -> tuple[_MetricCounts, _MetricCounts, _MetricCounts]:
    session_index = {day: index for index, day in enumerate(context.sessions)}
    by_symbol: dict[str, list[_ValidRow]] = defaultdict(list)
    for row in rows:
        if row.symbol in context.universe_symbols:
            by_symbol[row.symbol].append(row)

    outlier_numerator = 0
    outlier_denominator = 0
    abrupt_numerator = 0
    abrupt_denominator = 0
    volume_numerator = 0
    volume_denominator = 0
    for symbol in sorted(context.universe_symbols):
        ordered = sorted(by_symbol.get(symbol, ()), key=lambda item: item.session_date)
        for segment in _continuous_segments(ordered, session_index):
            returns: list[tuple[date, float, float]] = []
            for previous, current in zip(segment, segment[1:], strict=False):
                simple_return = (current.close - previous.close) / previous.close
                log_return = math.log(current.close / previous.close)
                returns.append((current.session_date, log_return, simple_return))
                abrupt_denominator += 1
                if abs(simple_return) >= ABRUPT_RETURN_THRESHOLD:
                    abrupt_numerator += 1
                    candidates.append(
                        _SampleCandidate(
                            rule_code="ABRUPT_PRICE",
                            symbol=symbol,
                            session_date=current.session_date,
                            derived=(("returnPpm", _bounded_scaled(simple_return, 1_000_000)),),
                        )
                    )
            for index, (session_date, value, _) in enumerate(returns):
                history = tuple(item[1] for item in returns[max(0, index - 60) : index])
                score = modified_z_score(value, history)
                if score is None:
                    continue
                outlier_denominator += 1
                if abs(score) > MODIFIED_Z_THRESHOLD:
                    outlier_numerator += 1
                    candidates.append(
                        _SampleCandidate(
                            rule_code="RETURN_OUTLIER",
                            symbol=symbol,
                            session_date=session_date,
                            derived=(("modifiedZMilli", _bounded_scaled(score, 1_000)),),
                        )
                    )

            volume_values = tuple(
                (row.session_date, math.log1p(row.volume)) for row in segment
            )
            for index, (session_date, value) in enumerate(volume_values):
                history = tuple(
                    item[1] for item in volume_values[max(0, index - 60) : index]
                )
                score = modified_z_score(value, history)
                if score is None:
                    continue
                volume_denominator += 1
                if abs(score) > MODIFIED_Z_THRESHOLD:
                    volume_numerator += 1
                    candidates.append(
                        _SampleCandidate(
                            rule_code="SHARE_VOLUME_SPIKE",
                            symbol=symbol,
                            session_date=session_date,
                            derived=(("modifiedZMilli", _bounded_scaled(score, 1_000)),),
                        )
                    )

    return (
        _evaluated_counts(
            outlier_numerator,
            outlier_denominator,
            failure=MetricStatus.WARN,
        ),
        _evaluated_counts(
            abrupt_numerator,
            abrupt_denominator,
            failure=MetricStatus.WARN,
        ),
        _evaluated_counts(
            volume_numerator,
            volume_denominator,
            failure=MetricStatus.WARN,
        ),
    )


def _continuous_segments(
    rows: Sequence[_ValidRow],
    session_index: Mapping[date, int],
) -> tuple[tuple[_ValidRow, ...], ...]:
    segments: list[tuple[_ValidRow, ...]] = []
    current: list[_ValidRow] = []
    previous_index: int | None = None
    for row in rows:
        index = session_index[row.session_date]
        if previous_index is not None and index != previous_index + 1:
            segments.append(tuple(current))
            current = []
        current.append(row)
        previous_index = index
    if current:
        segments.append(tuple(current))
    return tuple(segments)


def _accounting_metrics(
    context: AnalysisContext,
    total_raw_rows: int,
) -> tuple[_MetricCounts, _MetricCounts, _MetricCounts]:
    summary = context.collection_summary
    if summary is None:
        unavailable = _MetricCounts(None, None, MetricStatus.NOT_AVAILABLE)
        return unavailable, unavailable, unavailable

    duplicates = summary.ingest_duplicates
    duplicate_numerator = duplicates.exact_rows + duplicates.conflicting_groups
    duplicate_denominator = total_raw_rows + duplicate_numerator
    if duplicate_denominator == 0:
        ingest = _MetricCounts(0, 0, MetricStatus.NOT_EVALUATED)
    elif duplicates.conflicting_groups:
        ingest = _MetricCounts(
            duplicate_numerator,
            duplicate_denominator,
            MetricStatus.FAIL,
        )
    elif duplicates.exact_rows:
        ingest = _MetricCounts(
            duplicate_numerator,
            duplicate_denominator,
            MetricStatus.WARN,
        )
    else:
        ingest = _MetricCounts(0, duplicate_denominator, MetricStatus.PASS)

    logical_denominator = sum(item.started for item in summary.logical_operations)
    logical_numerator = sum(item.terminal_failures for item in summary.logical_operations)
    logical = _evaluated_counts(
        logical_numerator,
        logical_denominator,
        failure=MetricStatus.FAIL,
    )
    market_data = next(
        (
            item
            for item in summary.physical_attempts
            if item.channel == PhysicalChannel.MARKET_DATA
        ),
        None,
    )
    physical = _evaluated_counts(
        market_data.failures if market_data is not None else 0,
        market_data.attempts if market_data is not None else 0,
        failure=MetricStatus.WARN,
    )
    return ingest, logical, physical


def _evaluated_counts(
    numerator: int,
    denominator: int,
    *,
    failure: MetricStatus,
) -> _MetricCounts:
    if denominator == 0:
        return _MetricCounts(0, 0, MetricStatus.NOT_EVALUATED)
    return _MetricCounts(
        numerator,
        denominator,
        failure if numerator else MetricStatus.PASS,
    )


def _rate_metric(
    metric_id: str,
    counts: _MetricCounts,
    sample_counts: Counter[str],
) -> RateMetric:
    calculated_rate = (
        rate_ppm(counts.numerator, counts.denominator)
        if counts.numerator is not None and counts.denominator is not None
        else None
    )
    return RateMetric(
        metricId=metric_id,
        status=counts.status,
        numerator=counts.numerator,
        denominator=counts.denominator,
        ratePpm=calculated_rate,
        sampleCount=sample_counts[metric_id],
    )


def _bound_samples(candidates: list[_SampleCandidate]) -> tuple[BoundedSample, ...]:
    per_rule: Counter[str] = Counter()
    bounded: list[BoundedSample] = []
    for item in sorted(
        candidates,
        key=lambda value: (
            value.rule_code,
            value.symbol,
            value.session_date,
            value.derived,
        ),
    ):
        if len(bounded) >= MAX_SAMPLES:
            break
        if per_rule[item.rule_code] >= MAX_SAMPLES_PER_RULE:
            continue
        per_rule[item.rule_code] += 1
        bounded.append(
            BoundedSample(
                ruleCode=item.rule_code,
                symbol=item.symbol,
                sessionDate=item.session_date,
                derived=dict(item.derived),
            )
        )
    return tuple(bounded)


def _sample_metric_id(rule_code: str) -> str:
    return {
        "SCHEMA_INTEGRITY": "requiredSchemaIntegrity",
        "CANONICAL_DUPLICATE": "canonicalDuplicate",
        "CURRENT_UNIVERSE_MISSING": "currentUniverseHistoricalCoverage",
        "PER_SYMBOL_STALE": "perSymbolStale",
        "RETURN_OUTLIER": "returnOutlier",
        "ABRUPT_PRICE": "abruptPrice",
        "SHARE_VOLUME_SPIKE": "shareVolumeSpike",
    }[rule_code]


def _quality_status(metrics: tuple[RateMetric, ...]) -> QualityStatus:
    statuses = {item.status for item in metrics}
    if MetricStatus.FAIL in statuses:
        return QualityStatus.FAIL
    if MetricStatus.WARN in statuses:
        return QualityStatus.WARN
    if MetricStatus.PASS in statuses:
        return QualityStatus.PASS
    return QualityStatus.NOT_EVALUATED


def _analysis_fingerprint(context: AnalysisContext) -> str:
    collection = (
        context.collection_run.model_dump(mode="json", by_alias=True)
        if context.collection_run is not None
        else None
    )
    identity = {
        "metricPolicyVersion": METRIC_POLICY_VERSION,
        "evaluatedAt": context.evaluated_at.isoformat(),
        "softwareRevision": context.software_revision,
        "calendar": {
            "name": "XKRX",
            "timezone": "Asia/Seoul",
            "windowStart": context.window_start.isoformat(),
            "windowEnd": context.window_end.isoformat(),
            "expectedLastCompletedXkrxSession": (
                context.expected_last_completed_xkrx_session.isoformat()
            ),
            "sessions": [day.isoformat() for day in context.sessions],
        },
        "inputProvenance": {
            "universeManifest": context.universe_manifest.model_dump(
                mode="json", by_alias=True
            ),
            "datasetManifest": context.dataset_manifest.model_dump(
                mode="json", by_alias=True
            ),
            "collectionRun": collection,
        },
    }
    return canonical_json_sha256(identity)


def _bounded_scaled(value: float, scale: int) -> int:
    rounded = int(
        (Decimal(str(value)) * Decimal(scale)).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )
    return max(-1_000_000, min(1_000_000, rounded))


def _is_symbol(value: str) -> bool:
    return len(value) == 6 and set(value) <= _SYMBOL_DIGITS
