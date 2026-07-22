from __future__ import annotations

import psycopg
import pytest

from tests.data.calendar.conftest import PostgresTestCluster


def test_v6_creates_all_s1_6_objects_and_replaces_v4_table_with_read_only_view(
    postgres_cluster: PostgresTestCluster,
) -> None:
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        rows = connection.execute(
            """
            SELECT relname, relkind
            FROM pg_class
            WHERE relnamespace = 'public'::regnamespace
              AND relname = ANY(%s)
            ORDER BY relname
            """,
            (
                [
                    "active_disclosure_risk_states",
                    "calendar_collection_cursors",
                    "calendar_conflicts",
                    "calendar_event_sources",
                    "calendar_events",
                    "calendar_observations",
                    "calendar_source_health",
                    "current_calendar_events",
                    "market_calendar",
                    "opendart_quota_usage",
                    "trading_sessions",
                    "disclosure_risk_state_transitions",
                ],
            ),
        ).fetchall()

        assert {name for name, _ in rows} == {
            "active_disclosure_risk_states",
            "calendar_collection_cursors",
            "calendar_conflicts",
            "calendar_event_sources",
            "calendar_events",
            "calendar_observations",
            "calendar_source_health",
            "current_calendar_events",
            "market_calendar",
            "opendart_quota_usage",
            "trading_sessions",
            "disclosure_risk_state_transitions",
        }
        assert dict(rows)["market_calendar"] == "v"
        assert connection.execute(
            "SELECT count(*) FROM trading_sessions WHERE canonical_rule_version = 'V4_COMPAT_MIGRATION'"
        ).fetchone() == (2,)
        assert connection.execute("SELECT count(*) FROM market_calendar").fetchone() == (2,)


def test_v6_database_checks_reject_invalid_quota_and_confidence(
    postgres_cluster: PostgresTestCluster,
) -> None:
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                INSERT INTO opendart_quota_usage (
                    usage_date, effective_limit, daily_budget, physical_attempts
                ) VALUES (DATE '2026-07-22', 100, 88, 0)
                """
            )
        connection.rollback()

        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                """
                INSERT INTO trading_sessions (
                    exchange_mic, session_date, is_open, timezone, degraded,
                    as_of, confidence_bps, has_conflict, canonical_hash, canonical_rule_version
                ) VALUES (
                    'XKRX', DATE '2026-07-22', true, 'Asia/Seoul', false,
                    now(), 9901, false, repeat('a', 64), 's1.6-v1'
                )
                """
            )


def test_nullable_source_revision_uses_nulls_not_distinct_uniqueness(
    postgres_cluster: PostgresTestCluster,
) -> None:
    with psycopg.connect(postgres_cluster["admin_dsn"]) as connection:
        connection.execute(
            """
            INSERT INTO calendar_events (
                event_id, event_series_key, revision_no, source_id, source_event_key,
                source_revision, event_type, symbol, event_date, detail,
                status, confidence_bps, has_conflict, canonical_hash
            ) VALUES (
                'evt-null-1', 'series-null', 1, 'opendart', 'source-null', NULL,
                'DISCLOSURE', '005930', DATE '2026-07-22', '{}'::jsonb,
                'ACTIVE', 9000, false, repeat('b', 64)
            )
            """
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute(
                """
                INSERT INTO calendar_events (
                    event_id, event_series_key, revision_no, source_id, source_event_key,
                    source_revision, event_type, symbol, event_date, detail,
                    status, confidence_bps, has_conflict, canonical_hash
                ) VALUES (
                    'evt-null-2', 'series-null-2', 1, 'opendart', 'source-null', NULL,
                    'DISCLOSURE', '005930', DATE '2026-07-23', '{}'::jsonb,
                    'ACTIVE', 9000, false, repeat('c', 64)
                )
                """
            )
