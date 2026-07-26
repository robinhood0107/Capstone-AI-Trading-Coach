#!/usr/bin/env bash
set -Eeuo pipefail

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_APP_PASSWORD:?POSTGRES_APP_PASSWORD is required}"
: "${POSTGRES_MIGRATION_PASSWORD:?POSTGRES_MIGRATION_PASSWORD is required}"
: "${POSTGRES_COLLECTOR_PASSWORD:?POSTGRES_COLLECTOR_PASSWORD is required}"
: "${POSTGRES_DISCLOSURE_READER_PASSWORD:?POSTGRES_DISCLOSURE_READER_PASSWORD is required}"
: "${POSTGRES_MARKET_WRITER_PASSWORD:?POSTGRES_MARKET_WRITER_PASSWORD is required}"
: "${POSTGRES_PORTFOLIO_WRITER_PASSWORD:?POSTGRES_PORTFOLIO_WRITER_PASSWORD is required}"
: "${POSTGRES_RISK_WRITER_PASSWORD:?POSTGRES_RISK_WRITER_PASSWORD is required}"

# psql argv나 shell-expanded SQL에 password를 넣지 않고 process environment에서 안전하게 인용한다.
export PGPASSWORD="${POSTGRES_PASSWORD:-}"
psql -v ON_ERROR_STOP=1 --no-password --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv database_name POSTGRES_DB
\getenv app_password POSTGRES_APP_PASSWORD
\getenv migration_password POSTGRES_MIGRATION_PASSWORD
\getenv collector_password POSTGRES_COLLECTOR_PASSWORD
\getenv disclosure_reader_password POSTGRES_DISCLOSURE_READER_PASSWORD
\getenv market_writer_password POSTGRES_MARKET_WRITER_PASSWORD
\getenv portfolio_writer_password POSTGRES_PORTFOLIO_WRITER_PASSWORD
\getenv risk_writer_password POSTGRES_RISK_WRITER_PASSWORD

BEGIN;

-- role password DDL은 literalized SQL로 실행되므로 bootstrap transaction에서는 statement log를 닫는다.
SET LOCAL log_statement = 'none';
SET LOCAL log_min_error_statement = 'panic';

SELECT format(
    'CREATE ROLE decision_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app')
\gexec

SELECT format(
    'ALTER ROLE decision_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'app_password'
)
\gexec

-- DB capability bind가 statement/error log 설정과 무관하게 값으로 노출되지 않게 한다.
ALTER ROLE decision_app SET log_parameter_max_length = 0;
ALTER ROLE decision_app SET log_parameter_max_length_on_error = 0;

SELECT format(
    'CREATE ROLE flyway LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'migration_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'flyway')
\gexec

SELECT format(
    'ALTER ROLE flyway WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'migration_password'
)
\gexec

-- migration/rotation이 statement logging을 허용해도 credential bind 값은 서버 로그에 남기지 않는다.
ALTER ROLE flyway SET log_parameter_max_length = 0;
ALTER ROLE flyway SET log_parameter_max_length_on_error = 0;

SELECT format(
    'CREATE ROLE decision_collector LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'collector_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_collector')
\gexec

SELECT format(
    'ALTER ROLE decision_collector WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'collector_password'
)
\gexec

SELECT format(
    'CREATE ROLE decision_disclosure_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'disclosure_reader_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_disclosure_reader')
\gexec

SELECT format(
    'ALTER ROLE decision_disclosure_reader WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'disclosure_reader_password'
)
\gexec

SELECT format(
    'CREATE ROLE decision_market_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'market_writer_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_market_writer')
\gexec

SELECT format(
    'ALTER ROLE decision_market_writer WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'market_writer_password'
)
\gexec

SELECT format(
    'CREATE ROLE decision_portfolio_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'portfolio_writer_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_portfolio_writer')
\gexec

SELECT format(
    'ALTER ROLE decision_portfolio_writer WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'portfolio_writer_password'
)
\gexec

SELECT format(
    'CREATE ROLE decision_risk_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'risk_writer_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_risk_writer')
\gexec

SELECT format(
    'ALTER ROLE decision_risk_writer WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'risk_writer_password'
)
\gexec

REVOKE ALL ON DATABASE :"database_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database_name" TO
    decision_app,
    decision_collector,
    decision_disclosure_reader,
    decision_market_writer,
    decision_portfolio_writer,
    decision_risk_writer,
    flyway;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO
    decision_app,
    decision_collector,
    decision_disclosure_reader,
    decision_market_writer,
    decision_portfolio_writer,
    decision_risk_writer,
    flyway;
GRANT CREATE ON SCHEMA public TO flyway;

-- S1.2c의 Spring runtime은 DB write controller가 없다. 쓰기 권한은 해당 기능 migration이
-- 필요한 application table에만 명시적으로 추가하고, bootstrap에서 미리 전체 DML을 주지 않는다.
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_app;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_app;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_collector;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_collector;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_disclosure_reader;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_disclosure_reader;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_market_writer;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_market_writer;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_portfolio_writer;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_portfolio_writer;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_risk_writer;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_risk_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM decision_app;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    REVOKE SELECT ON TABLES FROM decision_app;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM decision_app;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM decision_collector;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM decision_collector;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM decision_disclosure_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM decision_disclosure_reader;

DO $calendar_privileges$
BEGIN
    IF to_regclass('public.opendart_quota_usage') IS NOT NULL THEN
        -- 기존 volume에서 bootstrap을 재실행해도 V6의 exact collector grant와 app deny를 복원한다.
        REVOKE ALL PRIVILEGES ON TABLE
            opendart_quota_usage,
            calendar_source_health,
            calendar_observations,
            trading_sessions,
            trading_session_revisions,
            calendar_events,
            calendar_event_sources,
            calendar_conflicts,
            calendar_collection_cursors,
            disclosure_risk_state_transitions,
            current_calendar_events,
            active_disclosure_risk_states,
            market_calendar
        FROM decision_collector;
        GRANT SELECT, INSERT, UPDATE ON TABLE
            opendart_quota_usage,
            calendar_source_health,
            trading_sessions,
            calendar_collection_cursors
        TO decision_collector;
        GRANT SELECT, INSERT ON TABLE
            calendar_observations,
            trading_session_revisions,
            calendar_events,
            calendar_event_sources,
            calendar_conflicts,
            disclosure_risk_state_transitions
        TO decision_collector;
        GRANT SELECT ON TABLE
            current_calendar_events,
            active_disclosure_risk_states,
            market_calendar
        TO decision_collector;

        REVOKE ALL PRIVILEGES ON TABLE
            opendart_quota_usage,
            calendar_source_health,
            calendar_observations,
            trading_session_revisions,
            calendar_events,
            calendar_event_sources,
            calendar_conflicts,
            calendar_collection_cursors,
            disclosure_risk_state_transitions
        FROM decision_app;
        REVOKE ALL PRIVILEGES ON TABLE
            trading_sessions,
            current_calendar_events,
            active_disclosure_risk_states,
            market_calendar
        FROM decision_app;
        GRANT SELECT ON TABLE
            trading_sessions,
            current_calendar_events,
            active_disclosure_risk_states,
            market_calendar
        TO decision_app;
    END IF;
END
$calendar_privileges$;

DO $principle_privileges$
BEGIN
    IF to_regclass('public.principle_presets') IS NOT NULL
       AND EXISTS (
           SELECT 1
           FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'principles'
             AND column_name = 'title'
       ) THEN
        -- 기존 volume에서 bootstrap을 재실행해도 V8 target table의 exact grant를 broad SELECT로 덮지 않는다.
        REVOKE ALL PRIVILEGES ON TABLE
            users,
            principle_presets,
            principles,
            principle_versions,
            audit_logs
        FROM decision_app;
        GRANT SELECT ON TABLE users, principle_presets TO decision_app;
        GRANT SELECT, INSERT ON TABLE principles, principle_versions TO decision_app;
        GRANT UPDATE (title, mode, status, current_version, updated_at)
            ON TABLE principles TO decision_app;
        GRANT INSERT ON TABLE audit_logs TO decision_app;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_app;
        REVOKE CREATE ON SCHEMA public FROM decision_app;
    END IF;
END
$principle_privileges$;

DO $decision_runtime_privileges$
BEGIN
    IF to_regclass('public.decision_idempotency_results') IS NOT NULL THEN
        -- 기존 volume에서도 V9의 source read-only 및 append-only history 경계를 그대로 복원한다.
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_app;
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_disclosure_reader;
        GRANT SELECT ON TABLE
            users,
            principle_presets,
            principles,
            principle_versions,
            trading_sessions,
            current_calendar_events,
            active_disclosure_risk_states,
            market_calendar,
            decision_owner_projection,
            decision_audit_projection,
            latest_market_quote_observations,
            latest_instrument_catalog_observations,
            latest_portfolio_balance_observations,
            latest_deterministic_risk_observations,
            latest_daily_order_count_observations,
            active_paper_portfolio_projection
        TO decision_app;
        GRANT INSERT ON TABLE
            principles,
            principle_versions,
            decisions,
            decision_violations,
            decision_artifacts,
            decision_traces,
            audit_logs,
            event_outbox,
            decision_idempotency_results
        TO decision_app;
        GRANT EXECUTE ON FUNCTION
            read_decision_owner_projection(),
            read_decision_audit_projection(),
            find_decision_idempotency_result(text, text, timestamptz),
            next_decision_idempotency_generation(text, text)
        TO decision_app;
        GRANT UPDATE (title, mode, status, current_version, updated_at)
            ON TABLE principles TO decision_app;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_app;
        GRANT SELECT ON TABLE
            current_corporation_registry_projection,
            disclosure_event_observation_projection,
            disclosure_collection_status_projection
        TO decision_disclosure_reader;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_disclosure_reader;
        REVOKE CREATE ON SCHEMA public FROM decision_app;
        REVOKE CREATE ON SCHEMA public FROM decision_disclosure_reader;
    END IF;
END
$decision_runtime_privileges$;

DO $risk_kill_switch_privileges$
BEGIN
    IF to_regclass('public.risk_kill_switch') IS NOT NULL THEN
        -- 기존 volume bootstrap 재적용 뒤에도 V10의 singleton 및 append-only 최소권한을 복원한다.
        REVOKE ALL PRIVILEGES ON TABLE
            risk_kill_switch,
            risk_kill_switch_transitions,
            decision_invalidations,
            kill_switch_user_projection
        FROM decision_app;
        GRANT SELECT ON TABLE risk_kill_switch TO decision_app;
        GRANT UPDATE (
            active,
            reason_class,
            generation,
            changed_by,
            changed_by_role,
            changed_at,
            request_id
        ) ON TABLE risk_kill_switch TO decision_app;
        GRANT INSERT ON TABLE risk_kill_switch_transitions TO decision_app;
        GRANT SELECT ON TABLE kill_switch_user_projection TO decision_app;
        GRANT EXECUTE ON FUNCTION
            read_kill_switch_gate(),
            revalidate_kill_switch_admin(text, bigint),
            read_kill_switch_audit_projection(),
            read_decision_usability(),
            invalidate_unused_decisions_for_kill_switch(bigint, timestamptz, text)
        TO decision_app;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_app;
        REVOKE CREATE ON SCHEMA public FROM decision_app;
    END IF;
END
$risk_kill_switch_privileges$;

DO $brokerage_mock_order_privileges$
BEGIN
    IF to_regclass('public.mock_order_owner_projection') IS NOT NULL THEN
        -- 기존 volume bootstrap 재적용 뒤에도 S3.1 mock order ledger 최소권한을 복원한다.
        REVOKE ALL PRIVILEGES ON TABLE
            orders,
            order_events,
            mock_order_owner_projection
        FROM decision_app;
        IF to_regclass('public.brokerage_db_capability_keys') IS NOT NULL THEN
            REVOKE ALL PRIVILEGES ON TABLE brokerage_db_capability_keys
            FROM decision_app;
            REVOKE ALL ON FUNCTION assert_brokerage_database_capability(text)
            FROM decision_app;
            GRANT EXECUTE ON FUNCTION
                read_mock_order_decision(text, text, text),
                find_mock_order_idempotency_result(text, text, timestamptz, text),
                read_mock_order_owner_projection(text, text, text),
                create_mock_order(jsonb, text),
                request_mock_order_cancel(jsonb, text)
            TO decision_app;
        ELSE
            GRANT INSERT, SELECT ON TABLE orders TO decision_app;
            GRANT INSERT, SELECT ON TABLE order_events TO decision_app;
            GRANT SELECT ON TABLE mock_order_owner_projection TO decision_app;
            GRANT EXECUTE ON FUNCTION
                read_mock_order_decision(),
                find_mock_order_idempotency_result(text, text, timestamptz),
                read_mock_order_owner_projection()
            TO decision_app;
        END IF;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_app;
        REVOKE CREATE ON SCHEMA public FROM decision_app;
    END IF;
END
$brokerage_mock_order_privileges$;

DO $decision_source_writer_privileges$
BEGIN
    IF to_regclass('public.instrument_catalog_observations') IS NOT NULL THEN
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_market_writer;
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_portfolio_writer;
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_risk_writer;
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_disclosure_reader;

        GRANT INSERT ON TABLE
            market_quote_observations,
            instrument_catalog_observations
        TO decision_market_writer;
        GRANT INSERT ON TABLE
            portfolio_balance_observations,
            portfolio_position_observations
        TO decision_portfolio_writer;
        GRANT INSERT ON TABLE
            deterministic_risk_observations,
            daily_order_count_observations
        TO decision_risk_writer;
        GRANT INSERT ON TABLE corporation_registry_observations TO decision_collector;

        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_market_writer;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_portfolio_writer;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_risk_writer;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_disclosure_reader;
        GRANT SELECT ON TABLE
            current_corporation_registry_projection,
            disclosure_event_observation_projection,
            disclosure_collection_status_projection
        TO decision_disclosure_reader;
        REVOKE CREATE ON SCHEMA public FROM
            decision_market_writer,
            decision_portfolio_writer,
            decision_risk_writer,
            decision_disclosure_reader;
    END IF;
END
$decision_source_writer_privileges$;

DO $block$
BEGIN
    IF to_regclass('public.flyway_schema_history') IS NOT NULL THEN
        -- 기존 volume에 role bootstrap을 재적용해도 runtime이 migration 이력을 변조하지 못한다.
        REVOKE ALL PRIVILEGES ON TABLE public.flyway_schema_history FROM decision_app;
        REVOKE ALL PRIVILEGES ON TABLE public.flyway_schema_history FROM decision_collector;
        REVOKE ALL PRIVILEGES ON TABLE public.flyway_schema_history FROM decision_disclosure_reader;
    END IF;
END
$block$;
COMMIT;
SQL
