from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.data.ecos.collector import ECOSCollectionError, ECOSCollector
from app.data.ecos.errors import ECOSApplicationError, RegistryNotVerifiedError
from app.data.ecos.models import ECOSObservation, StatisticSearchPage
from app.data.ecos.series_registry import CANDIDATE_SERIES, ECOSSeries

_REGISTRY_VERIFIED_AT = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


def _verified_series() -> tuple[ECOSSeries, ...]:
    return tuple(
        entry.model_copy(
            update={
                "verified": True,
                "registry_verified_at": _REGISTRY_VERIFIED_AT,
                "name": f"synthetic-{entry.series_id}",
                "unit": "synthetic-unit",
            }
        )
        for entry in CANDIDATE_SERIES
    )


class _FakeClient:
    def __init__(
        self,
        responses: dict[str | tuple[str, int], StatisticSearchPage | Exception],
    ) -> None:
        self.responses = responses
        self.calls: list[tuple[str, int, int]] = []

    def statistic_search(
        self,
        *,
        series: ECOSSeries,
        start: date,
        end: date,
        page_start: int,
        page_end: int,
    ) -> StatisticSearchPage:
        assert start <= end
        self.calls.append((series.series_id, page_start, page_end))
        response = self.responses.get((series.series_id, page_start))
        if response is None:
            response = self.responses[series.series_id]
        if isinstance(response, Exception):
            raise response
        return response


class _Publisher:
    def __init__(self) -> None:
        self.snapshots: list[object] = []

    def __call__(self, snapshot: object) -> None:
        self.snapshots.append(snapshot)


def _page(value: str = "2.5") -> StatisticSearchPage:
    return StatisticSearchPage(
        status="complete",
        total_count=1,
        observations=[ECOSObservation(time="20260714", value=value)],
    )


def _page_with_raw_rows(
    *,
    total_count: int,
    raw_row_count: int,
    time: str = "20260714",
    value: str = "2.5",
) -> StatisticSearchPage:
    """parser가 exact duplicate를 접은 뒤에도 원래 page row 수를 재현한다."""
    if raw_row_count == 0:
        return StatisticSearchPage(
            status="empty",
            total_count=total_count,
            observations=[],
        )
    return StatisticSearchPage(
        status="complete",
        total_count=total_count,
        observations=[ECOSObservation(time=time, value=value)],
        duplicate_count=raw_row_count - 1,
    )


def test_provisional_registry_is_rejected_before_client_or_publisher() -> None:
    client = _FakeClient({})
    publisher = _Publisher()
    collector = ECOSCollector(client=client, publisher=publisher)

    with pytest.raises(RegistryNotVerifiedError):
        collector.collect(
            series=CANDIDATE_SERIES,
            start=date(2026, 7, 1),
            end=date(2026, 7, 14),
            retrieved_at=datetime(2026, 7, 14, tzinfo=UTC),
            persist=True,
        )

    assert client.calls == []
    assert publisher.snapshots == []


def test_one_series_failure_publishes_a_partial_snapshot() -> None:
    series = _verified_series()
    client = _FakeClient(
        {
            series[0].series_id: _page(),
            series[1].series_id: ECOSApplicationError("ERROR-500", retryable=True),
        }
    )
    publisher = _Publisher()
    collector = ECOSCollector(client=client, publisher=publisher)

    result = collector.collect(
        series=series,
        start=date(2026, 7, 1),
        end=date(2026, 7, 14),
        retrieved_at=datetime(2026, 7, 14, tzinfo=UTC),
        persist=True,
    )

    assert result.partial is True
    assert result.coverage == "partial"
    assert [entry.status for entry in result.series_results] == ["complete", "failed"]
    assert client.calls == [
        (series[0].series_id, 1, 200),
        (series[1].series_id, 1, 200),
    ]
    assert len(publisher.snapshots) == 1
    assert result.snapshot.registry_verified_at == _REGISTRY_VERIFIED_AT
    assert result.snapshot.registry_verified_at != result.snapshot.retrieved_at


def test_both_series_failure_never_publishes() -> None:
    series = _verified_series()
    failure = ECOSApplicationError("ERROR-500", retryable=True)
    client = _FakeClient({entry.series_id: failure for entry in series})
    publisher = _Publisher()
    collector = ECOSCollector(client=client, publisher=publisher)

    with pytest.raises(ECOSCollectionError, match="all_series_failed"):
        collector.collect(
            series=series,
            start=date(2026, 7, 1),
            end=date(2026, 7, 14),
            retrieved_at=datetime(2026, 7, 14, tzinfo=UTC),
            persist=True,
        )

    assert client.calls == [(entry.series_id, 1, 200) for entry in series]
    assert publisher.snapshots == []


@pytest.mark.parametrize(
    "first_outcome",
    [
        StatisticSearchPage(status="empty", total_count=0, observations=[]),
        ECOSApplicationError("ERROR-500", retryable=True),
    ],
    ids=["empty", "failed"],
)
def test_strict_mode_stops_after_first_incomplete_series_without_publishing(
    first_outcome: StatisticSearchPage | Exception,
) -> None:
    series = _verified_series()
    client = _FakeClient(
        {
            series[0].series_id: first_outcome,
            series[1].series_id: _page(),
        }
    )
    publisher = _Publisher()
    collector = ECOSCollector(client=client, publisher=publisher)

    with pytest.raises(ECOSCollectionError, match="complete|incomplete"):
        collector.collect(
            series=series,
            start=date(2026, 7, 1),
            end=date(2026, 7, 14),
            retrieved_at=datetime(2026, 7, 14, tzinfo=UTC),
            persist=True,
            require_complete=True,
        )

    assert client.calls == [(series[0].series_id, 1, 200)]
    assert publisher.snapshots == []


def test_collection_pages_at_two_hundred_rows_and_bounds_lookback_to_366_days() -> None:
    series = _verified_series()
    client = _FakeClient(
        {
            (series[0].series_id, 1): StatisticSearchPage(
                status="complete",
                total_count=201,
                observations=[ECOSObservation(time="20260713", value="2.5")],
                duplicate_count=199,
            ),
            (series[0].series_id, 201): StatisticSearchPage(
                status="complete",
                total_count=201,
                observations=[ECOSObservation(time="20260714", value="2.5")],
            ),
            series[1].series_id: StatisticSearchPage(
                status="empty",
                total_count=0,
                observations=[],
            ),
        }
    )
    collector = ECOSCollector(client=client, publisher=_Publisher())

    result = collector.collect(
        series=series,
        start=date(2025, 7, 14),
        end=date(2026, 7, 14),
        retrieved_at=datetime(2026, 7, 14, tzinfo=UTC),
        persist=False,
    )

    assert client.calls == [
        (series[0].series_id, 1, 200),
        (series[0].series_id, 201, 400),
        (series[1].series_id, 1, 200),
    ]
    assert [item.time for item in result.series_results[0].observations] == [
        "20260713",
        "20260714",
    ]

    with pytest.raises(ECOSCollectionError, match="lookback"):
        collector.collect(
            series=series,
            start=date(2025, 7, 13),
            end=date(2026, 7, 14),
            retrieved_at=datetime(2026, 7, 14, tzinfo=UTC),
            persist=False,
        )


@pytest.mark.parametrize(
    ("first_page_rows", "second_page_rows"),
    [
        (200, 99),
        (0, 100),
    ],
    ids=["final-row-shortage", "early-empty-page"],
)
def test_provider_total_count_mismatch_fails_the_series_closed(
    first_page_rows: int,
    second_page_rows: int,
) -> None:
    series = _verified_series()
    client = _FakeClient(
        {
            (series[0].series_id, 1): _page_with_raw_rows(
                total_count=300,
                raw_row_count=first_page_rows,
                time="20260713",
            ),
            (series[0].series_id, 201): _page_with_raw_rows(
                total_count=300,
                raw_row_count=second_page_rows,
            ),
            series[1].series_id: _page_with_raw_rows(total_count=0, raw_row_count=0),
        }
    )
    collector = ECOSCollector(client=client, publisher=_Publisher())

    result = collector.collect(
        series=series,
        start=date(2026, 7, 1),
        end=date(2026, 7, 14),
        retrieved_at=datetime(2026, 7, 14, tzinfo=UTC),
        persist=False,
    )

    assert [entry.status for entry in result.series_results] == ["failed", "empty"]
    assert result.partial is True
    assert result.coverage == "partial"
    if first_page_rows == 0:
        assert (series[0].series_id, 201, 400) not in client.calls


@pytest.mark.parametrize(
    ("total_count", "raw_row_count"),
    [(3, 2), (1, 2)],
    ids=["single-page-shortage", "single-page-overflow"],
)
def test_single_page_row_count_mismatch_fails_the_series_closed(
    total_count: int,
    raw_row_count: int,
) -> None:
    series = _verified_series()
    client = _FakeClient(
        {
            series[0].series_id: _page_with_raw_rows(
                total_count=total_count,
                raw_row_count=raw_row_count,
            ),
            series[1].series_id: _page_with_raw_rows(total_count=0, raw_row_count=0),
        }
    )
    collector = ECOSCollector(client=client, publisher=_Publisher())

    result = collector.collect(
        series=series,
        start=date(2026, 7, 1),
        end=date(2026, 7, 14),
        retrieved_at=datetime(2026, 7, 14, tzinfo=UTC),
        persist=False,
    )

    assert [entry.status for entry in result.series_results] == ["failed", "empty"]
    assert result.coverage == "partial"


def test_exact_duplicate_rows_count_toward_total_but_final_observations_are_deduped() -> None:
    series = _verified_series()
    client = _FakeClient(
        {
            (series[0].series_id, 1): _page_with_raw_rows(
                total_count=300,
                raw_row_count=200,
            ),
            (series[0].series_id, 201): _page_with_raw_rows(
                total_count=300,
                raw_row_count=100,
            ),
            series[1].series_id: _page_with_raw_rows(total_count=0, raw_row_count=0),
        }
    )
    collector = ECOSCollector(client=client, publisher=_Publisher())

    result = collector.collect(
        series=series,
        start=date(2026, 7, 1),
        end=date(2026, 7, 14),
        retrieved_at=datetime(2026, 7, 14, tzinfo=UTC),
        persist=False,
    )

    assert [entry.status for entry in result.series_results] == ["complete", "empty"]
    assert result.series_results[0].observations == (ECOSObservation(time="20260714", value="2.5"),)
    assert result.duplicate_count == 299


def test_total_count_mismatch_across_all_series_raises_all_series_failed() -> None:
    series = _verified_series()
    responses: dict[str | tuple[str, int], StatisticSearchPage | Exception] = {}
    for entry in series:
        responses[(entry.series_id, 1)] = _page_with_raw_rows(
            total_count=300,
            raw_row_count=200,
        )
        responses[(entry.series_id, 201)] = _page_with_raw_rows(
            total_count=300,
            raw_row_count=99,
        )
    collector = ECOSCollector(client=_FakeClient(responses), publisher=_Publisher())

    with pytest.raises(ECOSCollectionError, match="all_series_failed"):
        collector.collect(
            series=series,
            start=date(2026, 7, 1),
            end=date(2026, 7, 14),
            retrieved_at=datetime(2026, 7, 14, tzinfo=UTC),
            persist=False,
        )
