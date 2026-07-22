from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest

from app.data.calendar.errors import QuotaReservationDenied
from app.data.calendar.disclosure_state import DisclosureStateTransition
from app.data.calendar.models import (
    CalendarConflictRecord,
    CalendarEventSource,
    CalendarEventWrite,
    CalendarObservation,
    CalendarPageCommit,
    CanonicalTradingSession,
    CollectionCursor,
    RetentionRule,
    SourceHealthSnapshot,
)
from app.data.calendar.normalizer import EventCandidate, build_event_revision, canonical_hash
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
        with pytest.raises(ValueError, match="retention"):
            repository.publish_observation_and_cursor(
                observation,
                cursor,
                persistence_mode="ONLINE_PERSISTENT",
                retention=None,
            )
        with pytest.raises(RuntimeError, match="injected crash"):
            repository.publish_observation_and_cursor(
                observation,
                cursor,
                persistence_mode="OFFLINE_EPHEMERAL",
                retention=None,
                fail_before_commit=True,
            )

        assert repository.observation_exists(observation.observation_id) is False
        assert repository.load_cursor(cursor.key) is None

        repository.publish_observation_and_cursor(
            observation,
            cursor,
            persistence_mode="OFFLINE_EPHEMERAL",
            retention=None,
        )
        assert repository.observation_exists(observation.observation_id) is True
        assert repository.load_cursor(cursor.key) == cursor


def test_page_commit_is_atomic_idempotent_and_keeps_every_audit_relation(
    postgres_cluster: PostgresTestCluster,
) -> None:
    observed_at = datetime(2026, 7, 22, 0, 0, tzinfo=UTC)
    observation = CalendarObservation(
        observation_id="obs-page-atomic",
        source_id="opendart-structured-events",
        origin_group="opendart",
        capability="DISCLOSURE_EVENT",
        effective_from=date(2026, 7, 22),
        effective_to=date(2026, 7, 22),
        observed_at=observed_at,
        ingested_at=observed_at + timedelta(seconds=1),
        sanitized_payload={"corp_code": "00126380", "transition": "OPEN"},
        sanitized_payload_hash=canonical_hash({"corp_code": "00126380", "transition": "OPEN"}),
        adapter_version="s1.6-opendart-v1",
        mapping_version="s1.6-disclosure-state-v1",
        registry_version="s1.6-registry-v1",
    )
    session = CanonicalTradingSession(
        exchange_mic="XKRX",
        session_date=date(2026, 8, 3),
        is_open=False,
        open_at=None,
        close_at=None,
        timezone="Asia/Seoul",
        reason="FIXTURE_CLOSED",
        chosen_source_id="kis-holiday-ctca0903r",
        degraded=False,
        fallback_reason=None,
        as_of=observed_at,
        confidence_bps=7000,
        has_conflict=True,
        conflicts=(),
        source_refs=("a" * 64,),
        canonical_hash="b" * 64,
    )
    candidate = EventCandidate(
        source_id="opendart-structured-events",
        source_event_key="20260722000001",
        stable_identity="00126380:bnkMngtPcbg:20260722000001",
        source_revision=None,
        event_type="DISCLOSURE",
        symbol="005930",
        exchange_mic="XKRX",
        event_date=date(2026, 7, 22),
        detail={"corp_code": "00126380", "state_type": "BANK_MANAGEMENT", "transition": "OPEN"},
    )
    event = build_event_revision(candidate)
    transition = DisclosureStateTransition(
        transition_id="c" * 64,
        corp_code="00126380",
        state_type="BANK_MANAGEMENT",
        state_key="00126380:BANK_MANAGEMENT",
        transition="OPEN",
        revision_no=1,
        revised_from_transition_id=None,
        source_id="opendart-structured-events",
        source_event_key="20260722000001",
        source_revision=None,
        effective_on=date(2026, 7, 22),
        observed_at=observed_at,
        canonical_event_id=event.event_id,
        mapping_version="s1.6-disclosure-state-v1",
    )
    cursor = CollectionCursor(
        source_id="opendart-structured-events",
        operation="bnkMngtPcbg",
        subject="00126380",
        window_from=date(2026, 7, 1),
        window_to=date(2026, 7, 22),
        mapping_version="s1.6-disclosure-state-v1",
        next_page=2,
        continuation="page-2",
        completed=False,
    )
    commit = CalendarPageCommit(
        observation=observation,
        cursor=cursor,
        source_health=SourceHealthSnapshot(
            source_id="opendart-structured-events",
            last_success_at=observed_at,
            last_failure_at=None,
            failure_count=0,
            stale_after=timedelta(days=1),
            network_ready=True,
            status_code="HEALTHY",
            error_code=None,
        ),
        persistence_mode="OFFLINE_EPHEMERAL",
        retention=None,
        trading_session=session,
        event_writes=(CalendarEventWrite(event, confidence_bps=9000, status="ACTUAL"),),
        source_links=(
            CalendarEventSource(
                event_source_id="link-event",
                event_id=event.event_id,
                exchange_mic=None,
                session_date=None,
                observation_id=observation.observation_id,
                source_choice="CHOSEN",
                resolution_reason="STRUCTURED_ENDPOINT_IDENTITY",
                opaque_source_ref="e" * 64,
            ),
        ),
        conflicts=(
            CalendarConflictRecord(
                conflict_id="conflict-session",
                canonical_key="XKRX:2026-08-03",
                field_name="is_open",
                competing_values=(
                    {"source_id": "kis-holiday-ctca0903r", "tier": 1, "origin_group": "kis", "value": False},
                    {"source_id": "xkrx-4.13.2", "tier": 2, "origin_group": "exchange-calendars", "value": True},
                ),
                chosen_value=False,
                chosen_source_id="kis-holiday-ctca0903r",
                resolution_rule="KIS_OPND_YN_OVER_XKRX_BASE",
                resolution_reason="FIELD_AUTHORITY",
                unresolved=True,
                conflict_hash="f" * 64,
            ),
        ),
        disclosure_transitions=(transition,),
    )

    with psycopg.connect(postgres_cluster["collector_dsn"]) as connection:
        repository = CalendarRepository(connection)
        with pytest.raises(ValueError, match="retention"):
            repository.publish_page(
                replace(
                    commit,
                    persistence_mode="ONLINE_PERSISTENT",
                    retention=None,
                )
            )

        persistent_commit = replace(
            commit,
            persistence_mode="ONLINE_PERSISTENT",
            retention=RetentionRule(days=30, owner="market-data-operator"),
        )
        before = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM calendar_observations),
              (SELECT count(*) FROM trading_sessions),
              (SELECT count(*) FROM trading_session_revisions),
              (SELECT count(*) FROM calendar_events),
              (SELECT count(*) FROM calendar_event_sources),
              (SELECT count(*) FROM calendar_conflicts),
              (SELECT count(*) FROM disclosure_risk_state_transitions),
              (SELECT count(*) FROM calendar_source_health)
            """
        ).fetchone()
        assert before is not None
        with pytest.raises(RuntimeError, match="injected crash"):
            repository.publish_page(persistent_commit, fail_before_commit=True)
        assert repository.load_cursor(cursor.key) is None

        repository.publish_page(persistent_commit)
        repository.publish_page(persistent_commit)
        assert repository.load_cursor(cursor.key) == cursor

        after = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM calendar_observations),
              (SELECT count(*) FROM trading_sessions),
              (SELECT count(*) FROM trading_session_revisions),
              (SELECT count(*) FROM calendar_events),
              (SELECT count(*) FROM calendar_event_sources),
              (SELECT count(*) FROM calendar_conflicts),
              (SELECT count(*) FROM disclosure_risk_state_transitions),
              (SELECT count(*) FROM calendar_source_health)
            """
        ).fetchone()
        assert after is not None
        assert tuple(current - prior for current, prior in zip(after, before, strict=True)) == (
            1,
            1,
            1,
            1,
            1,
            1,
            1,
            1,
        )
        assert connection.execute(
            "SELECT count(*) FROM active_disclosure_risk_states WHERE corp_code = '00126380'"
        ).fetchone() == (1,)


def _quota(limit: int, budget: int) -> OpenDARTQuotaConfig:
    return OpenDARTQuotaConfig(
        daily_call_limit=limit,
        daily_call_budget=budget,
        max_calls_per_run=min(8_000, budget),
        max_symbols_per_run=10,
    )
