from __future__ import annotations

from collections.abc import Callable

import psycopg
import pytest

from tests.data.calendar.conftest import PostgresTestCluster


def test_collector_can_only_perform_allowlisted_calendar_operations(
    postgres_cluster: PostgresTestCluster,
) -> None:
    with psycopg.connect(postgres_cluster["collector_dsn"]) as connection:
        connection.execute(
            """
            INSERT INTO calendar_source_health (
                source_id, failure_count, stale_after, network_ready, status_code
            ) VALUES ('xkrx', 0, interval '1 day', false, 'OFFLINE_READY')
            """
        )
        connection.execute(
            """
            UPDATE calendar_source_health
            SET last_success_at = now(), status_code = 'HEALTHY'
            WHERE source_id = 'xkrx'
            """
        )
        connection.execute(
            """
            INSERT INTO calendar_observations (
                observation_id, source_id, origin_group, capability,
                effective_from, observed_at, ingested_at, sanitized_payload,
                sanitized_payload_hash, adapter_version, mapping_version, registry_version
            ) VALUES (
                'obs-permission', 'xkrx', 'exchange-calendars', 'MARKET_SESSION',
                DATE '2026-07-22', now(), now(), '{}'::jsonb,
                repeat('d', 64), '1', '1', '1'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO trading_sessions (
                exchange_mic, session_date, is_open, timezone, degraded,
                as_of, confidence_bps, has_conflict, canonical_hash, canonical_rule_version
            ) VALUES (
                'XKRX', DATE '2026-07-22', true, 'Asia/Seoul', false,
                now(), 9000, false, repeat('e', 64), 's1.6-v1'
            )
            """
        )
        connection.execute(
            """
            UPDATE trading_sessions SET reason = 'regular' 
            WHERE exchange_mic = 'XKRX' AND session_date = DATE '2026-07-22'
            """
        )
        assert connection.execute("SELECT count(*) FROM current_calendar_events").fetchone() == (0,)


@pytest.mark.parametrize(
    "operation",
    [
        lambda connection: connection.execute("CREATE TABLE collector_escape(id integer)"),
        lambda connection: connection.execute("SELECT * FROM flyway_schema_history"),
        lambda connection: connection.execute("UPDATE flyway_schema_history SET success = false"),
        lambda connection: connection.execute("SELECT * FROM users"),
        lambda connection: connection.execute("DELETE FROM calendar_observations"),
        lambda connection: connection.execute("TRUNCATE calendar_events"),
        lambda connection: connection.execute("UPDATE calendar_events SET status = 'CANCELLED'"),
        lambda connection: connection.execute("CREATE ROLE collector_escape_role"),
        lambda connection: connection.execute("ALTER ROLE decision_collector SUPERUSER"),
    ],
)
def test_collector_forbidden_operations_fail(
    postgres_cluster: PostgresTestCluster,
    operation: Callable[[psycopg.Connection[tuple[object, ...]]], object],
) -> None:
    with psycopg.connect(postgres_cluster["collector_dsn"]) as connection:
        with pytest.raises(psycopg.Error):
            operation(connection)


def test_application_can_read_current_canonical_but_not_internal_or_write_tables(
    postgres_cluster: PostgresTestCluster,
) -> None:
    with psycopg.connect(postgres_cluster["app_dsn"]) as connection:
        connection.execute("SELECT * FROM market_calendar LIMIT 1")
        connection.execute("SELECT * FROM current_calendar_events LIMIT 1")
        connection.execute("SELECT * FROM active_disclosure_risk_states LIMIT 1")
        connection.execute("SELECT * FROM trading_sessions LIMIT 1")

    forbidden = [
        "SELECT * FROM calendar_observations",
        "SELECT * FROM opendart_quota_usage",
        "SELECT * FROM calendar_conflicts",
        "SELECT * FROM flyway_schema_history",
        "INSERT INTO trading_sessions (exchange_mic, session_date, is_open, timezone, "
        "degraded, as_of, confidence_bps, has_conflict, canonical_hash, canonical_rule_version) "
        "VALUES ('XKRX', DATE '2026-07-23', true, 'Asia/Seoul', false, now(), 9000, "
        "false, repeat('f', 64), 's1.6-v1')",
        "CREATE TABLE app_escape(id integer)",
    ]
    for statement in forbidden:
        with psycopg.connect(postgres_cluster["app_dsn"]) as connection:
            with pytest.raises(psycopg.Error):
                connection.execute(statement)
