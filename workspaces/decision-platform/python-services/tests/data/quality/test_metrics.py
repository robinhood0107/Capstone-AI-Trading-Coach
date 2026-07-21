from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
import math
from uuid import UUID

import pytest

from app.data.kis.accounting import (
    CollectionRunRecorder,
    CollectionRunStatus,
    FailureCode,
    LogicalOperation,
    PhysicalChannel,
)
from app.data.quality.metrics import analyze_quality, modified_z_score
from app.data.quality.models import (
    AnalysisContext,
    ManifestReference,
    MetricStatus,
    QualityStatus,
    SymbolDataset,
)
from app.data.quality.policy import (
    CANONICAL_DAILY_COLUMNS,
    MAX_SAMPLES_PER_RULE,
    rate_ppm,
)


def _sessions(count: int, *, start: date = date(2026, 6, 1)) -> tuple[date, ...]:
    days: list[date] = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return tuple(days)


def _context(
    *,
    sessions: tuple[date, ...],
    symbols: tuple[str, ...],
    accounting=None,
    revision: str = "7131f695293472ea16ee05322ed9b05f7b69d129",
) -> AnalysisContext:
    start = sessions[0] if sessions else date(2026, 7, 21)
    end = sessions[-1] if sessions else start
    collection_reference = (
        ManifestReference(
            identifier=(
                "collection-runs/2026/07/21/"
                "123e4567-e89b-42d3-a456-426614174000/summary.json"
            ),
            sha256="c" * 64,
        )
        if accounting is not None
        else None
    )
    return AnalysisContext(
        evaluated_at=datetime(2026, 7, 21, 7, 0, tzinfo=UTC),
        software_revision=revision,
        window_start=start,
        window_end=end,
        expected_last_completed_xkrx_session=end,
        sessions=sessions,
        universe_symbols=symbols,
        universe_manifest=ManifestReference(
            identifier="universe/universe_manifest.json",
            sha256="a" * 64,
        ),
        dataset_manifest=ManifestReference(
            identifier=(
                "datasets/2026/07/21/"
                "123e4567-e89b-42d3-a456-426614174001/manifest.json"
            ),
            sha256="b" * 64,
        ),
        collection_run=collection_reference,
        collection_summary=accounting,
        dataset_file_count=len(symbols),
    )


def _row(
    symbol: str,
    session: date,
    *,
    open_: object = 100,
    high: object = 105,
    low: object = 95,
    close: object = 101,
    volume: object = 1000,
    turnover: object = 100_000,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "date": session,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "turnover": turnover,
    }


def _dataset(symbol: str, rows: list[dict[str, object]], *, columns=None) -> SymbolDataset:
    return SymbolDataset(
        symbol=symbol,
        columns=columns or CANONICAL_DAILY_COLUMNS,
        rows=tuple(rows),
    )


def _metric(report, metric_id: str):
    return next(item for item in report.metrics if item.metric_id == metric_id)


def test_empty_input_uses_null_rate_and_not_evaluated_without_fabricated_zero() -> None:
    report = analyze_quality(_context(sessions=(), symbols=()), ())

    coverage = _metric(report, "currentUniverseHistoricalCoverage")
    logical = _metric(report, "logicalApiFailure")
    assert (coverage.numerator, coverage.denominator, coverage.rate_ppm) == (0, 0, None)
    assert coverage.status == MetricStatus.NOT_EVALUATED
    assert logical.status == MetricStatus.NOT_AVAILABLE
    assert logical.numerator is logical.denominator is logical.rate_ppm is None
    assert report.status.quality_status == QualityStatus.NOT_EVALUATED


def test_current_universe_missing_is_warn_only_and_listing_adjusted_is_unavailable() -> None:
    sessions = _sessions(2)
    symbols = ("000660", "005930")
    rows = [
        _dataset("000660", [_row("000660", session) for session in sessions]),
        _dataset("005930", [_row("005930", sessions[1])]),
    ]

    report = analyze_quality(_context(sessions=sessions, symbols=symbols), rows)

    coverage = _metric(report, "currentUniverseHistoricalCoverage")
    listing = _metric(report, "listingAdjustedCompleteness")
    assert (coverage.numerator, coverage.denominator, coverage.rate_ppm) == (1, 4, 250000)
    assert coverage.status == MetricStatus.WARN
    assert listing.status == MetricStatus.NOT_AVAILABLE
    assert report.status.quality_status == QualityStatus.WARN


def test_dataset_freshness_fails_while_per_symbol_stale_only_warns() -> None:
    sessions = _sessions(3)
    report = analyze_quality(
        _context(sessions=sessions, symbols=("005930",)),
        [_dataset("005930", [_row("005930", sessions[0])])],
    )

    freshness = _metric(report, "datasetFreshness")
    stale = _metric(report, "perSymbolStale")
    assert freshness.status == MetricStatus.FAIL
    assert freshness.numerator == freshness.denominator == 1
    assert stale.status == MetricStatus.WARN
    assert stale.numerator == stale.denominator == 1
    assert report.status.quality_status == QualityStatus.FAIL


@pytest.mark.parametrize(
    ("row_change", "columns"),
    [
        ({"close": None}, None),
        ({"close": True}, None),
        ({"close": "101"}, None),
        ({"close": math.inf}, None),
        ({"close": 0}, None),
        ({"volume": -1}, None),
        ({"low": 110}, None),
        ({"symbol": "000660"}, None),
        ({"date": date(2026, 5, 29)}, None),
        ({}, CANONICAL_DAILY_COLUMNS + ("extra",)),
    ],
)
def test_required_schema_integrity_rejects_invalid_rows_and_columns(
    row_change: dict[str, object],
    columns: tuple[str, ...] | None,
) -> None:
    sessions = _sessions(1)
    row = _row("005930", sessions[0])
    row.update(row_change)

    report = analyze_quality(
        _context(sessions=sessions, symbols=("005930",)),
        [_dataset("005930", [row], columns=columns)],
    )

    assert _metric(report, "requiredSchemaIntegrity").status == MetricStatus.FAIL


def test_canonical_exact_and_conflicting_duplicates_both_fail() -> None:
    sessions = _sessions(1)
    exact = _row("005930", sessions[0])
    conflicting = _row("005930", sessions[0], close=102)

    exact_report = analyze_quality(
        _context(sessions=sessions, symbols=("005930",)),
        [_dataset("005930", [exact, dict(exact)])],
    )
    conflict_report = analyze_quality(
        _context(sessions=sessions, symbols=("005930",)),
        [_dataset("005930", [exact, conflicting])],
    )

    assert _metric(exact_report, "canonicalDuplicate").status == MetricStatus.FAIL
    assert _metric(exact_report, "canonicalDuplicate").numerator == 1
    assert _metric(conflict_report, "canonicalDuplicate").status == MetricStatus.FAIL


def test_collection_accounting_projects_logical_physical_and_ingest_duplicates() -> None:
    recorder = CollectionRunRecorder(
        run_id=UUID("123e4567-e89b-42d3-a456-426614174000"),
        started_at=datetime(2026, 7, 21, 1, 0, tzinfo=UTC),
    )
    operation = recorder.start_logical(LogicalOperation.DAILY_BARS)
    recorder.record_physical_attempt(PhysicalChannel.MARKET_DATA)
    recorder.record_physical_failure(PhysicalChannel.MARKET_DATA, FailureCode.HTTP_RETRYABLE)
    recorder.record_physical_attempt(PhysicalChannel.MARKET_DATA)
    recorder.record_physical_success(PhysicalChannel.MARKET_DATA)
    recorder.succeed_logical(operation)
    recorder.record_ingest_duplicates(exact_rows=1, conflicting_groups=0)
    accounting = recorder.snapshot(
        completed_at=datetime(2026, 7, 21, 1, 1, tzinfo=UTC),
        status=CollectionRunStatus.SUCCESS,
    )
    sessions = _sessions(1)

    report = analyze_quality(
        _context(sessions=sessions, symbols=("005930",), accounting=accounting),
        [_dataset("005930", [_row("005930", sessions[0])])],
    )

    logical = _metric(report, "logicalApiFailure")
    physical = _metric(report, "physicalAttemptFailure")
    ingest = _metric(report, "ingestDuplicate")
    assert (logical.numerator, logical.denominator, logical.status) == (0, 1, MetricStatus.PASS)
    assert (physical.numerator, physical.denominator, physical.rate_ppm) == (1, 2, 500000)
    assert physical.status == MetricStatus.WARN
    assert ingest.status == MetricStatus.WARN
    assert report.status.evidence_completeness == "COMPLETE"


def test_unrecovered_physical_failure_is_fail_not_warn() -> None:
    recorder = CollectionRunRecorder(
        run_id=UUID("123e4567-e89b-42d3-a456-426614174000"),
        started_at=datetime(2026, 7, 21, 1, 0, tzinfo=UTC),
    )
    operation = recorder.start_logical(LogicalOperation.DAILY_BARS)
    recorder.record_physical_attempt(PhysicalChannel.MARKET_DATA)
    recorder.record_physical_failure(PhysicalChannel.MARKET_DATA, FailureCode.HTTP_RETRYABLE)
    recorder.fail_logical(operation, FailureCode.HTTP_RETRYABLE)
    accounting = recorder.snapshot(
        completed_at=datetime(2026, 7, 21, 1, 1, tzinfo=UTC),
        status=CollectionRunStatus.FAILED,
    )
    sessions = _sessions(1)

    report = analyze_quality(
        _context(sessions=sessions, symbols=("005930",), accounting=accounting),
        [_dataset("005930", [_row("005930", sessions[0])])],
    )

    assert _metric(report, "logicalApiFailure").status == MetricStatus.FAIL
    assert _metric(report, "physicalAttemptFailure").status == MetricStatus.FAIL


def test_no_collection_accounting_is_not_available_instead_of_zero_percent() -> None:
    sessions = _sessions(1)
    report = analyze_quality(
        _context(sessions=sessions, symbols=("005930",)),
        [_dataset("005930", [_row("005930", sessions[0])])],
    )

    for metric_id in ("logicalApiFailure", "physicalAttemptFailure"):
        metric = _metric(report, metric_id)
        assert metric.status == MetricStatus.NOT_AVAILABLE
        assert metric.rate_ppm is None


def test_rate_ppm_uses_decimal_half_up_and_zero_denominator_returns_none() -> None:
    assert rate_ppm(1, 6) == 166667
    assert rate_ppm(1, 8) == 125000
    assert rate_ppm(0, 0) is None


def test_modified_z_uses_trailing_history_and_has_no_mad_fallback() -> None:
    history = tuple(0.001 * value for value in range(1, 21))

    assert abs(modified_z_score(0.20, history) or 0) > 3.5
    assert modified_z_score(0.1, (0.1,) * 20) is None
    assert modified_z_score(0.1, history[:19]) is None


def test_return_outlier_and_share_volume_spike_flag_known_current_observation() -> None:
    sessions = _sessions(22)
    returns = [0.001 * ((index % 10) + 1) for index in range(20)] + [0.20]
    closes = [1_000_000]
    for value in returns:
        closes.append(round(closes[-1] * (1 + value)))
    volumes = [100 + index for index in range(21)] + [100_000]
    rows = [
        _row(
            "005930",
            session,
            open_=close,
            high=close,
            low=close,
            close=close,
            volume=volume,
        )
        for session, close, volume in zip(sessions, closes, volumes, strict=True)
    ]

    report = analyze_quality(
        _context(sessions=sessions, symbols=("005930",)),
        [_dataset("005930", rows)],
    )

    assert _metric(report, "returnOutlier").numerator == 1
    assert _metric(report, "returnOutlier").status == MetricStatus.WARN
    assert (_metric(report, "shareVolumeSpike").numerator or 0) >= 1
    assert _metric(report, "shareVolumeSpike").status == MetricStatus.WARN


def test_mad_zero_short_history_and_session_gap_are_not_evaluated() -> None:
    sessions = _sessions(23)
    rows = [
        _row(
            "005930",
            session,
            open_=100,
            high=100,
            low=100,
            close=100,
            volume=0,
        )
        for session in sessions
        if session != sessions[10]
    ]

    report = analyze_quality(
        _context(sessions=sessions, symbols=("005930",)),
        [_dataset("005930", rows)],
    )

    assert _metric(report, "returnOutlier").status == MetricStatus.NOT_EVALUATED
    assert _metric(report, "shareVolumeSpike").status == MetricStatus.NOT_EVALUATED


def test_abrupt_price_includes_exact_thirty_percent_threshold() -> None:
    sessions = _sessions(2)
    rows = [
        _row("005930", sessions[0], open_=100, high=100, low=100, close=100),
        _row("005930", sessions[1], open_=130, high=130, low=130, close=130),
    ]

    report = analyze_quality(
        _context(sessions=sessions, symbols=("005930",)),
        [_dataset("005930", rows)],
    )

    abrupt = _metric(report, "abruptPrice")
    assert (abrupt.numerator, abrupt.denominator, abrupt.rate_ppm) == (1, 1, 1000000)
    assert abrupt.status == MetricStatus.WARN
    assert any(sample.rule_code == "ABRUPT_PRICE" for sample in report.bounded_samples)


def test_analysis_is_permutation_invariant_deterministic_and_does_not_mutate_input() -> None:
    sessions = _sessions(2)
    first_rows = [_row("005930", sessions[1]), _row("005930", sessions[0])]
    second_rows = [_row("000660", sessions[1]), _row("000660", sessions[0])]
    source = [first_rows, second_rows]
    before = deepcopy(source)
    context = _context(sessions=sessions, symbols=("000660", "005930"))

    first = analyze_quality(
        context,
        [_dataset("005930", first_rows), _dataset("000660", second_rows)],
    )
    second = analyze_quality(
        context,
        [
            _dataset("000660", list(reversed(second_rows))),
            _dataset("005930", list(reversed(first_rows))),
        ],
    )
    changed_revision = analyze_quality(
        _context(
            sessions=sessions,
            symbols=("000660", "005930"),
            revision="8131f695293472ea16ee05322ed9b05f7b69d129",
        ),
        [_dataset("005930", first_rows), _dataset("000660", second_rows)],
    )

    assert first.model_dump(mode="json", by_alias=True) == second.model_dump(
        mode="json",
        by_alias=True,
    )
    assert first.analysis_fingerprint == second.analysis_fingerprint
    assert first.report_id == second.report_id
    assert changed_revision.analysis_fingerprint != first.analysis_fingerprint
    assert changed_revision.report_id != first.report_id
    assert source == before


def test_samples_are_stably_sorted_with_rule_and_total_caps() -> None:
    symbols = tuple(f"{index:06d}" for index in range(1, 31))
    sessions = _sessions(1)

    report = analyze_quality(_context(sessions=sessions, symbols=symbols), ())

    assert len(report.bounded_samples) <= 100
    by_rule: dict[str, int] = {}
    for sample in report.bounded_samples:
        by_rule[sample.rule_code] = by_rule.get(sample.rule_code, 0) + 1
    assert all(count <= 20 for count in by_rule.values())
    assert list(report.bounded_samples) == sorted(
        report.bounded_samples,
        key=lambda item: (item.rule_code, item.symbol, item.session_date),
    )


def test_sample_candidates_are_bounded_while_missing_matrix_is_scanned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.data.quality import metrics

    symbols = tuple(f"{index:06d}" for index in range(1, 201))
    sessions = _sessions(2)
    observed_retained: list[int] = []
    original = metrics._bound_samples

    def assert_bounded(candidates):
        observed_retained.append(len(candidates))
        # v1에는 sample을 만드는 rule이 7개이고 각 rule은 scan 중에도 20개만 보존한다.
        assert len(candidates) <= 7 * MAX_SAMPLES_PER_RULE
        return original(candidates)

    monkeypatch.setattr(metrics, "_bound_samples", assert_bounded)

    report = analyze_quality(_context(sessions=sessions, symbols=symbols), ())

    assert observed_retained
    assert len(report.bounded_samples) == MAX_SAMPLES_PER_RULE
