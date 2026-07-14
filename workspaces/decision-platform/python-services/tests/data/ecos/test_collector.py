from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.data.ecos.collector import ECOSCollectionError, ECOSCollector
from app.data.ecos.errors import ECOSApplicationError, RegistryNotVerifiedError
from app.data.ecos.models import ECOSObservation, StatisticSearchPage
from app.data.ecos.series_registry import CANDIDATE_SERIES, ECOSSeries


def _verified_series() -> tuple[ECOSSeries, ...]:
    return tuple(entry.model_copy(update={"verified": True}) for entry in CANDIDATE_SERIES)


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


def test_collection_pages_at_two_hundred_rows_and_bounds_lookback_to_366_days() -> None:
    series = _verified_series()
    client = _FakeClient(
        {
            (series[0].series_id, 1): StatisticSearchPage(
                status="complete",
                total_count=201,
                observations=[ECOSObservation(time="20260713", value="2.5")],
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
