from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime

import psycopg
import pytest

from app.data.calendar.errors import QuotaReservationDenied
from app.data.calendar.models import CalendarObservation, CollectionCursor
from app.data.calendar.repository import CalendarRepository, OpenDARTQuotaRepository
from app.data.calendar.settings import OpenDARTQuotaConfig
from tests.data.calendar.conftest import PostgresTestCluster


def test_two_connections_competing_for_last_quota_slot_grant_exactly_one(
    postgres_cluster: PostgresTestCluster,
) -> None:
    usage_date = date(2026, 7, 23)
    config = OpenDARTQuotaConfig(
        daily_call_limit=2,
        daily_call_budget=1,
        max_calls_per_run=1,
        max_symbols_per_run=1,
    )

    def reserve(token: str) -> bool:
        with psycopg.connect(postgres_cluster["collector_dsn"]) as connection:
            repository = OpenDARTQuotaRepository(connection)
            try:
                repository.reserve(usage_date, config, token)
            except QuotaReservationDenied:
                return False
            return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, ["last-slot-a", "last-slot-b"]))

    assert sorted(results) == [False, True]


def test_same_day_limits_only_decrease_and_next_day_can_increase(
    postgres_cluster: PostgresTestCluster,
) -> None:
    first_day = date(2026, 7, 24)
    with psycopg.connect(postgres_cluster["collector_dsn"]) as connection:
        repository = OpenDARTQuotaRepository(connection)
        repository.reserve(first_day, _quota(100, 80), "decrease-1")
        repository.reserve(first_day, _quota(200, 170), "decrease-2")
        repository.reserve(first_day, _quota(50, 40), "decrease-3")

        usage = repository.get_usage(first_day)
        assert usage.effective_limit == 50
        assert usage.daily_budget == 40
        assert usage.physical_attempts == 3

        second_day = date(2026, 7, 25)
        repository.reserve(second_day, _quota(200, 170), "increase-next-day")
        next_usage = repository.get_usage(second_day)
        assert next_usage.effective_limit == 200
        assert next_usage.daily_budget == 170


def test_reservation_is_never_refunded_and_020_blocks_the_rest_of_kst_day(
    postgres_cluster: PostgresTestCluster,
) -> None:
    usage_date = date(2026, 7, 26)
    with psycopg.connect(postgres_cluster["collector_dsn"]) as connection:
        repository = OpenDARTQuotaRepository(connection)
        repository.reserve(usage_date, _quota(100, 80), "crash-after-commit")
        assert repository.get_usage(usage_date).physical_attempts == 1

        repository.mark_exhausted(usage_date, "PROVIDER_STATUS_020")
        with pytest.raises(QuotaReservationDenied, match="exhausted"):
            repository.reserve(usage_date, _quota(100, 80), "after-020")
        assert repository.get_usage(usage_date).physical_attempts == 1


def test_observation_and_cursor_publish_commit_or_rollback_together(
    postgres_cluster: PostgresTestCluster,
) -> None:
    observation = CalendarObservation(
        observation_id="obs-cursor-atomic",
        source_id="opendart",
        origin_group="opendart",
        capability="DISCLOSURE_EVENT",
        effective_from=date(2026, 7, 22),
        effective_to=None,
        observed_at=datetime(2026, 7, 22, 0, 0, tzinfo=UTC),
        ingested_at=datetime(2026, 7, 22, 0, 1, tzinfo=UTC),
        sanitized_payload={"event": "BANK_MANAGEMENT"},
        sanitized_payload_hash="1" * 64,
        adapter_version="1",
        mapping_version="1",
        registry_version="1",
    )
    cursor = CollectionCursor(
        source_id="opendart",
        operation="bnkMngtPcbg",
        subject="00126380",
        window_from=date(2026, 7, 1),
        window_to=date(2026, 7, 22),
        mapping_version="1",
        next_page=2,
        continuation=None,
        completed=False,
    )

    with psycopg.connect(postgres_cluster["collector_dsn"]) as connection:
        repository = CalendarRepository(connection)
        with pytest.raises(RuntimeError, match="injected crash"):
            repository.publish_observation_and_cursor(observation, cursor, fail_before_commit=True)

        assert repository.observation_exists(observation.observation_id) is False
        assert repository.load_cursor(cursor.key) is None

        repository.publish_observation_and_cursor(observation, cursor)
        assert repository.observation_exists(observation.observation_id) is True
        assert repository.load_cursor(cursor.key) == cursor


def _quota(limit: int, budget: int) -> OpenDARTQuotaConfig:
    return OpenDARTQuotaConfig(
        daily_call_limit=limit,
        daily_call_budget=budget,
        max_calls_per_run=min(8_000, budget),
        max_symbols_per_run=10,
    )
