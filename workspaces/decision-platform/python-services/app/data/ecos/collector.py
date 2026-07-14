from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Protocol

from app.data.ecos.errors import ECOSError
from app.data.ecos.models import (
    ECOSCollectionResult,
    ECOSCoverage,
    ECOSMacroSnapshot,
    ECOSObservation,
    ECOSSeriesSnapshot,
    ECOSSeriesStatus,
    StatisticSearchPage,
)
from app.data.ecos.series_registry import CANDIDATE_SERIES, ECOSSeries, verified_series

_PAGE_SIZE = 200
_MAX_ROWS_PER_SERIES = 400
_MAX_LOOKBACK_DAYS = 366
_MAX_PHYSICAL_ATTEMPTS = 8
_REGISTRY_VERSION = "ecos-v1"


class ECOSCollectionError(ECOSError):
    """series 원문 오류를 노출하지 않고 stable collection failure만 전달한다."""


class _SearchClient(Protocol):
    def statistic_search(
        self,
        *,
        series: ECOSSeries,
        start: date,
        end: date,
        page_start: int,
        page_end: int,
    ) -> StatisticSearchPage: ...


class ECOSCollector:
    """두 approved series를 200행 page로 수집하고 partial snapshot 규칙을 적용한다."""

    def __init__(
        self,
        *,
        client: _SearchClient,
        publisher: Callable[[object], None] | None,
    ) -> None:
        self._client = client
        self._publisher = publisher

    def collect(
        self,
        *,
        series: Sequence[ECOSSeries],
        start: date,
        end: date,
        retrieved_at: datetime,
        persist: bool,
    ) -> ECOSCollectionResult:
        """provisional registry를 outbound 전에 차단하고 두 series의 sanitized 결과만 반환한다."""
        approved = verified_series(series)
        if _series_identities(approved) != _series_identities(CANDIDATE_SERIES):
            raise ECOSCollectionError("series_registry_mismatch")
        _validate_collection_window(start=start, end=end, retrieved_at=retrieved_at)
        if persist and self._publisher is None:
            raise ECOSCollectionError("publisher_unavailable")

        series_results: list[ECOSSeriesSnapshot] = []
        logical_attempts = 0
        duplicate_count = 0
        for entry in approved:
            try:
                page, attempts = self._collect_series(entry, start=start, end=end)
                logical_attempts += attempts
                if logical_attempts > _MAX_PHYSICAL_ATTEMPTS:
                    raise ECOSCollectionError("run_attempt_limit_exceeded")
                series_result = _series_snapshot(
                    entry,
                    start=start,
                    end=end,
                    status="complete" if page.observations else "empty",
                    observations=tuple(page.observations),
                )
                duplicate_count += page.duplicate_count
            except Exception:
                # parser/provider exception의 message·cause는 series snapshot 경계 밖으로 내보내지 않는다.
                series_result = _series_snapshot(
                    entry,
                    start=start,
                    end=end,
                    status="failed",
                    observations=(),
                )
            series_results.append(series_result)

        if all(result.status == "failed" for result in series_results):
            raise ECOSCollectionError("all_series_failed")

        coverage: ECOSCoverage
        if any(result.status == "failed" for result in series_results):
            coverage = "partial"
        elif all(result.status == "empty" for result in series_results):
            coverage = "empty"
        else:
            coverage = "complete"
        partial = coverage == "partial"
        snapshot = ECOSMacroSnapshot(
            schemaVersion=1,
            source="ecos",
            asOf=end,
            retrievedAt=retrieved_at,
            registryVersion=_REGISTRY_VERSION,
            registryVerifiedAt=_registry_verified_at(approved),
            series=tuple(series_results),
            partial=partial,
            coverage=coverage,
        )
        physical_attempt_count = _physical_attempt_count(self._client, logical_attempts)
        collection_result = ECOSCollectionResult(
            snapshot=snapshot,
            series_results=tuple(series_results),
            partial=partial,
            coverage=coverage,
            physical_attempt_count=physical_attempt_count,
            duplicate_count=duplicate_count,
        )
        if persist:
            publisher = self._publisher
            if publisher is None:
                raise ECOSCollectionError("publisher_unavailable")
            publisher(collection_result)
        return collection_result

    def close(self) -> None:
        """CLI 종료 시 client connection pool을 명시적으로 닫는다."""
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def _collect_series(
        self,
        series: ECOSSeries,
        *,
        start: date,
        end: date,
    ) -> tuple[StatisticSearchPage, int]:
        observations: dict[str, ECOSObservation] = {}
        duplicate_count = 0
        page_start = 1
        expected_total: int | None = None
        raw_row_count = 0
        attempts = 0
        while True:
            page_end = page_start + _PAGE_SIZE - 1
            page = self._client.statistic_search(
                series=series,
                start=start,
                end=end,
                page_start=page_start,
                page_end=page_end,
            )
            attempts += 1
            if page.total_count > _MAX_ROWS_PER_SERIES:
                raise ECOSCollectionError("series_row_limit_exceeded")
            if expected_total is None:
                expected_total = page.total_count
            elif page.total_count != expected_total:
                raise ECOSCollectionError("series_page_count_mismatch")

            # parser가 exact duplicate를 접으므로 provider 원문 row 수는 둘의 합으로 복원한다.
            page_raw_row_count = len(page.observations) + page.duplicate_count
            expected_page_row_count = min(_PAGE_SIZE, expected_total - raw_row_count)
            if page_raw_row_count != expected_page_row_count:
                raise ECOSCollectionError("series_page_row_count_mismatch")
            raw_row_count += page_raw_row_count
            duplicate_count += page.duplicate_count
            for observation in page.observations:
                existing = observations.get(observation.time)
                if existing is None:
                    observations[observation.time] = observation
                elif existing.value == observation.value:
                    duplicate_count += 1
                else:
                    raise ECOSCollectionError("conflicting_duplicate_observation")
            if page_end >= page.total_count:
                break
            page_start += _PAGE_SIZE
            if page_start > _MAX_ROWS_PER_SERIES:
                raise ECOSCollectionError("series_row_limit_exceeded")

        if raw_row_count != expected_total:
            raise ECOSCollectionError("series_total_row_count_mismatch")

        ordered = [observations[key] for key in sorted(observations)]
        return (
            StatisticSearchPage(
                status="complete" if ordered else "empty",
                total_count=expected_total or 0,
                observations=ordered,
                duplicate_count=duplicate_count,
                retryable=False,
            ),
            attempts,
        )


def _validate_collection_window(*, start: date, end: date, retrieved_at: datetime) -> None:
    if start > end:
        raise ECOSCollectionError("collection_date_range_invalid")
    if (end - start).days + 1 > _MAX_LOOKBACK_DAYS:
        raise ECOSCollectionError("lookback_exceeds_366_days")
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() != UTC.utcoffset(retrieved_at):
        raise ECOSCollectionError("retrieved_at_must_be_utc")


def _series_snapshot(
    series: ECOSSeries,
    *,
    start: date,
    end: date,
    status: ECOSSeriesStatus,
    observations: tuple[ECOSObservation, ...],
) -> ECOSSeriesSnapshot:
    return ECOSSeriesSnapshot(
        seriesId=series.series_id,
        statCode=series.stat_code,
        itemCode1=series.item_code1,
        cycle="D",
        # activation commit 전 synthetic verified copy는 안전한 식별자로만 offline 검증한다.
        name=series.name or series.series_id,
        unit=series.unit or "unknown",
        requestedFrom=start.strftime("%Y%m%d"),
        requestedTo=end.strftime("%Y%m%d"),
        status=status,
        observations=observations,
    )


def _physical_attempt_count(client: object, logical_attempts: int) -> int:
    value = getattr(client, "physical_attempt_count", logical_attempts)
    calls = getattr(client, "calls", None)
    if not hasattr(client, "physical_attempt_count") and isinstance(calls, list):
        value = len(calls)
    if not isinstance(value, int) or isinstance(value, bool) or value < logical_attempts:
        value = logical_attempts
    if value > _MAX_PHYSICAL_ATTEMPTS:
        raise ECOSCollectionError("run_attempt_limit_exceeded")
    return value


def _registry_verified_at(
    series: Sequence[ECOSSeries],
) -> datetime:
    evidence = [entry.registry_verified_at for entry in series if entry.registry_verified_at]
    if len(evidence) != len(series):
        raise ECOSCollectionError("registry_evidence_missing")
    return max(evidence)


def _series_identities(series: Sequence[ECOSSeries]) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        (entry.series_id, entry.stat_code, entry.item_code1, entry.cycle) for entry in series
    )
