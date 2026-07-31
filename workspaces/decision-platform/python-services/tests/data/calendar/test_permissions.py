from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest

from tests.data.calendar.conftest import PostgresTestCluster


REPO_ROOT = Path(__file__).resolve().parents[6]


def test_repo_hygiene_supplies_required_collector_and_disclosure_reader_passwords() -> None:
    """Compose 검증도 collector/reader 필수 비밀번호를 주입해 실제 CI 경계를 재현한다."""

    compose = (REPO_ROOT / "infra/docker-compose.infra.yml").read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github/workflows/repo-hygiene.yml").read_text(
        encoding="utf-8"
    )

    assert (
        "${POSTGRES_COLLECTOR_PASSWORD:?POSTGRES_COLLECTOR_PASSWORD is required}"
        in compose
    )
    assert (
        "${POSTGRES_DISCLOSURE_READER_PASSWORD:?POSTGRES_DISCLOSURE_READER_PASSWORD is required}"
        in compose
    )
    assert (
        "${POSTGRES_RAG_WRITER_PASSWORD:?POSTGRES_RAG_WRITER_PASSWORD is required}"
        in compose
    )
    assert (
        "${POSTGRES_RAG_ADMIN_PASSWORD:?POSTGRES_RAG_ADMIN_PASSWORD is required}"
        in compose
    )
    assert (
        "${POSTGRES_RAG_QUERY_PASSWORD:?POSTGRES_RAG_QUERY_PASSWORD is required}"
        in compose
    )
    assert "POSTGRES_COLLECTOR_PASSWORD: validation-dummy-collector" in workflow
    assert "POSTGRES_DISCLOSURE_READER_PASSWORD: validation-dummy-disclosure-reader" in workflow
    assert "POSTGRES_RAG_WRITER_PASSWORD: validation-dummy-rag-writer" in workflow
    assert "POSTGRES_RAG_ADMIN_PASSWORD: validation-dummy-rag-admin" in workflow
    assert "POSTGRES_RAG_QUERY_PASSWORD: validation-dummy-rag-query" in workflow


def test_role_bootstrap_disables_all_duration_and_statement_logging_before_password_ddl() -> None:
    """password literal DDL 전에 session logging을 닫고 effective 값까지 fail-closed 검증한다."""

    script = (REPO_ROOT / "infra/init/02-application-roles.sh").read_text(
        encoding="utf-8"
    )
    begin = script.index("BEGIN;")
    first_password_ddl = script.index("PASSWORD %L")
    settings = {
        "log_statement": "'none'",
        "log_min_error_statement": "'panic'",
        "log_duration": "'off'",
        "log_min_duration_statement": "-1",
        "log_min_duration_sample": "-1",
        "log_statement_sample_rate": "0",
        "log_transaction_sample_rate": "0",
    }

    for setting, value in settings.items():
        assert script.index(f"SET {setting} = {value};") < begin
        assert script.index(f"current_setting('{setting}')") < first_password_ddl
    assert "psql -v ON_ERROR_STOP=1" in script


def test_role_bootstrap_sets_bounded_application_query_and_transaction_timeouts() -> None:
    script = (REPO_ROOT / "infra/init/02-application-roles.sh").read_text(
        encoding="utf-8"
    )

    assert "ALTER ROLE decision_app SET statement_timeout = '2s'" in script
    assert "ALTER ROLE decision_app SET lock_timeout = '500ms'" in script
    assert (
        "ALTER ROLE decision_app SET idle_in_transaction_session_timeout = '5s'"
        in script
    )


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
                exchange_mic, session_date, is_open, open_at, close_at, timezone, degraded,
                as_of, confidence_bps, has_conflict, canonical_hash, canonical_rule_version
            ) VALUES (
                'XKRX', DATE '2026-07-22', true,
                TIMESTAMPTZ '2026-07-22 09:00:00+09', TIMESTAMPTZ '2026-07-22 15:30:00+09',
                'Asia/Seoul', false,
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
        assert connection.execute(
            "SELECT count(*) FROM current_calendar_events WHERE event_id = 'permission-read-probe'"
        ).fetchone() == (0,)


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
        lambda connection: connection.execute(
            "UPDATE trading_session_revisions SET reason = 'rewritten'"
        ),
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
        "SELECT * FROM trading_session_revisions",
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
