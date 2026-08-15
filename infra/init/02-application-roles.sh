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
: "${POSTGRES_FILL_WRITER_PASSWORD:?POSTGRES_FILL_WRITER_PASSWORD is required}"
: "${POSTGRES_RAG_WRITER_PASSWORD:?POSTGRES_RAG_WRITER_PASSWORD is required}"
: "${POSTGRES_RAG_ADMIN_PASSWORD:?POSTGRES_RAG_ADMIN_PASSWORD is required}"
: "${POSTGRES_RAG_QUERY_PASSWORD:?POSTGRES_RAG_QUERY_PASSWORD is required}"

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
\getenv fill_writer_password POSTGRES_FILL_WRITER_PASSWORD
\getenv rag_writer_password POSTGRES_RAG_WRITER_PASSWORD
\getenv rag_admin_password POSTGRES_RAG_ADMIN_PASSWORD
\getenv rag_query_password POSTGRES_RAG_QUERY_PASSWORD

-- role password DDL 전에 session 전체의 statement·duration·sampling log를 닫는다.
SET log_statement = 'none';
SET log_min_error_statement = 'panic';
SET log_duration = 'off';
SET log_min_duration_statement = -1;
SET log_min_duration_sample = -1;
SET log_statement_sample_rate = 0;
SET log_transaction_sample_rate = 0;

-- 설정이 정책이나 버전 차이로 적용되지 않으면 password 변수를 서버에 보내기 전에 중단한다.
DO $logging_guard$
BEGIN
    IF current_setting('log_statement') <> 'none'
       OR current_setting('log_min_error_statement') <> 'panic'
       OR current_setting('log_duration') <> 'off'
       OR current_setting('log_min_duration_statement') <> '-1'
       OR current_setting('log_min_duration_sample') <> '-1'
       OR current_setting('log_statement_sample_rate')::numeric <> 0
       OR current_setting('log_transaction_sample_rate')::numeric <> 0 THEN
        RAISE EXCEPTION 'secure role bootstrap logging guard is unavailable';
    END IF;
END
$logging_guard$;

BEGIN;

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
ALTER ROLE decision_app SET statement_timeout = '2s';
ALTER ROLE decision_app SET lock_timeout = '500ms';
ALTER ROLE decision_app SET idle_in_transaction_session_timeout = '5s';

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

SELECT format(
    'CREATE ROLE decision_fill_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'fill_writer_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_fill_writer')
\gexec

SELECT format(
    'ALTER ROLE decision_fill_writer WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'fill_writer_password'
)
\gexec

SELECT format(
    'CREATE ROLE decision_rag_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'rag_writer_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer')
\gexec

SELECT format(
    'ALTER ROLE decision_rag_writer WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'rag_writer_password'
)
\gexec

-- ingest는 기본 2초로 닫고, 승인된 대량 transaction만 application이 SET LOCAL 60s를 사용한다.
ALTER ROLE decision_rag_writer SET log_parameter_max_length = 0;
ALTER ROLE decision_rag_writer SET log_parameter_max_length_on_error = 0;
ALTER ROLE decision_rag_writer SET statement_timeout = '2s';
ALTER ROLE decision_rag_writer SET lock_timeout = '500ms';
ALTER ROLE decision_rag_writer SET idle_in_transaction_session_timeout = '5s';

SELECT format(
    'CREATE ROLE decision_rag_admin LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'rag_admin_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_admin')
\gexec

SELECT format(
    'ALTER ROLE decision_rag_admin WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'rag_admin_password'
)
\gexec

-- 활성화 role은 table DML 없이 bounded definer 함수만 호출하므로 짧은 lock/transaction 상한을 둔다.
ALTER ROLE decision_rag_admin SET log_parameter_max_length = 0;
ALTER ROLE decision_rag_admin SET log_parameter_max_length_on_error = 0;
ALTER ROLE decision_rag_admin SET statement_timeout = '5s';
ALTER ROLE decision_rag_admin SET lock_timeout = '500ms';
ALTER ROLE decision_rag_admin SET idle_in_transaction_session_timeout = '5s';

SELECT format(
    'CREATE ROLE decision_rag_query LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'rag_query_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_query')
\gexec

SELECT format(
    'ALTER ROLE decision_rag_query WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'rag_query_password'
)
\gexec

-- retrieval transaction은 active bounded projection 밖으로 오래 점유하지 못하게 writer보다 더 짧게 둔다.
ALTER ROLE decision_rag_query SET log_parameter_max_length = 0;
ALTER ROLE decision_rag_query SET log_parameter_max_length_on_error = 0;
ALTER ROLE decision_rag_query SET statement_timeout = '1500ms';
ALTER ROLE decision_rag_query SET lock_timeout = '250ms';
ALTER ROLE decision_rag_query SET idle_in_transaction_session_timeout = '5s';

REVOKE ALL ON DATABASE :"database_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database_name" TO
    decision_app,
    decision_collector,
    decision_disclosure_reader,
    decision_market_writer,
    decision_portfolio_writer,
    decision_risk_writer,
    decision_fill_writer,
    decision_rag_writer,
    decision_rag_admin,
    decision_rag_query,
    flyway;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO
    decision_app,
    decision_collector,
    decision_disclosure_reader,
    decision_market_writer,
    decision_portfolio_writer,
    decision_risk_writer,
    decision_fill_writer,
    decision_rag_writer,
    decision_rag_admin,
    decision_rag_query,
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
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_fill_writer;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_fill_writer;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_rag_writer;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_rag_writer;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_rag_admin;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_rag_admin;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_rag_query;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_rag_query;
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
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM decision_fill_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM decision_fill_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM decision_rag_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM decision_rag_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM decision_rag_admin;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM decision_rag_admin;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM decision_rag_query;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM decision_rag_query;

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

DO $fill_writer_privileges$
BEGIN
    IF to_regclass('public.order_fill_observations') IS NOT NULL THEN
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public
        FROM decision_fill_writer;
        GRANT INSERT ON TABLE order_fill_observations
        TO decision_fill_writer;
        REVOKE UPDATE, DELETE, TRUNCATE ON TABLE order_fill_observations
        FROM decision_fill_writer;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public
        FROM decision_fill_writer;
        REVOKE CREATE ON SCHEMA public FROM decision_fill_writer;

        IF NOT EXISTS (
            SELECT 1
            FROM pg_policies
            WHERE schemaname = 'public'
              AND tablename = 'order_fill_observations'
              AND policyname = 'order_fill_observations_writer_insert_policy'
        ) THEN
            CREATE POLICY order_fill_observations_writer_insert_policy
              ON order_fill_observations
              FOR INSERT
              TO decision_fill_writer
              WITH CHECK (true);
        END IF;
    END IF;
END
$fill_writer_privileges$;

DO $paper_projection_privileges$
BEGIN
    IF to_regclass('public.paper_margin_owner_projection') IS NOT NULL THEN
        -- V13의 owner-scoped margin projection은 bootstrap 재실행 뒤에도 유일한 paper base read다.
        REVOKE ALL PRIVILEGES ON TABLE
            paper_accounts,
            paper_positions,
            paper_order_events,
            paper_margin_owner_projection
        FROM decision_app;
        GRANT SELECT ON TABLE paper_margin_owner_projection TO decision_app;
    END IF;
END
$paper_projection_privileges$;

DO $fill_projection_privileges$
BEGIN
    IF to_regclass('public.order_fill_observations') IS NOT NULL THEN
        -- reconciliation은 SECURITY DEFINER 함수만 호출하며 raw fill table read/write는 허용하지 않는다.
        REVOKE ALL PRIVILEGES ON TABLE
            order_fill_observations,
            order_fill_application_receipts
        FROM decision_app;
    END IF;
END
$fill_projection_privileges$;

DO $rag_source_registry_privileges$
BEGIN
    IF to_regclass('public.rag_sources') IS NOT NULL
       AND to_regclass('public.rag_source_revisions') IS NOT NULL
       AND to_regclass('public.rag_source_checks') IS NOT NULL
       AND to_regclass('public.rag_ingest_runs') IS NOT NULL
       AND to_regclass('public.rag_chunk_revisions') IS NOT NULL
       AND to_regclass('public.rag_corpus_generations') IS NOT NULL
       AND to_regclass('public.rag_generation_chunks') IS NOT NULL
       AND to_regclass('public.rag_chunk_embeddings') IS NOT NULL THEN
        -- bootstrap replay도 V16의 raw-table deny와 append-only writer allowlist를 그대로 복원한다.
        REVOKE ALL PRIVILEGES ON TABLE
            rag_sources,
            rag_source_revisions,
            rag_source_checks,
            rag_ingest_runs,
            rag_chunk_revisions,
            rag_corpus_generations,
            rag_generation_chunks,
            rag_chunk_embeddings,
            rag_embedding_policy_state,
            rag_embedding_policy_transitions
        FROM
            decision_app,
            decision_rag_writer,
            decision_rag_admin,
            decision_rag_query;
        GRANT SELECT, INSERT ON TABLE
            rag_sources,
            rag_source_revisions,
            rag_source_checks,
            rag_ingest_runs,
            rag_chunk_revisions,
            rag_corpus_generations,
            rag_generation_chunks,
            rag_chunk_embeddings
        TO decision_rag_writer;
        GRANT UPDATE (status, started_at, completed_at, actual_chunk_count, failure_class)
            ON TABLE rag_ingest_runs TO decision_rag_writer;
        GRANT UPDATE (
            status,
            actual_chunk_count,
            evaluation_status,
            evaluated_at,
            activated_at,
            failed_at,
            disabled_at,
            failure_class
        )
            ON TABLE rag_corpus_generations TO decision_rag_writer;
        REVOKE CREATE ON SCHEMA public FROM decision_rag_writer;
        REVOKE CREATE ON SCHEMA public FROM decision_rag_admin;
        REVOKE CREATE ON SCHEMA public FROM decision_rag_query;
    END IF;
END
$rag_source_registry_privileges$;

DO $rag_authorized_retrieval_privileges$
BEGIN
    IF to_regclass('public.rag_source_card_verifications') IS NOT NULL
       AND to_regclass('public.rag_source_public_topics') IS NOT NULL
       AND to_regclass('public.rag_source_exact_identifiers') IS NOT NULL
       AND to_regclass('public.rag_retrieval_scope_claims') IS NOT NULL THEN
        -- V19 sidecar와 scope claim은 모든 runtime role에 raw table 권한을 주지 않는다.
        REVOKE ALL PRIVILEGES ON TABLE
            rag_source_card_verifications,
            rag_source_public_topics,
            rag_source_exact_identifiers,
            rag_retrieval_scope_claims
        FROM
            decision_app,
            decision_rag_writer,
            decision_rag_admin,
            decision_rag_query;
    END IF;
END
$rag_authorized_retrieval_privileges$;

DO $rag_embedding_staging_privileges$
BEGIN
    IF to_regclass('public.rag_embedding_staging') IS NOT NULL THEN
        -- V17 이후 writer는 final embedding과 generation 활성화를 직접 변경하지 않는다.
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE
            ON TABLE rag_chunk_embeddings FROM decision_rag_writer;
        REVOKE SELECT ON TABLE rag_chunk_embeddings FROM decision_rag_writer;
        REVOKE UPDATE (activated_at)
            ON TABLE rag_corpus_generations FROM decision_rag_writer;
        REVOKE ALL PRIVILEGES ON TABLE rag_embedding_staging FROM
            decision_app,
            decision_rag_writer,
            decision_rag_admin,
            decision_rag_query;
        GRANT INSERT, SELECT ON TABLE rag_embedding_staging TO decision_rag_writer;
    END IF;
END
$rag_embedding_staging_privileges$;

-- extension I/O 함수는 건드리지 않고 public schema의 project-owned 함수 grant만 제거해 allowlist를 다시 만든다.
-- PUBLIC 또는 비앱 writer/reader에 남은 stale SECURITY DEFINER EXECUTE도 bootstrap replay가 보존하면 안 된다.
DO $revoke_custom_function_privileges$
DECLARE
    routine record;
BEGIN
    FOR routine IN
        SELECT proc.oid::regprocedure AS signature
        FROM pg_proc AS proc
        JOIN pg_namespace AS namespace ON namespace.oid = proc.pronamespace
        WHERE namespace.nspname = 'public'
          AND NOT EXISTS (
              SELECT 1
              FROM pg_depend AS dependency
              WHERE dependency.classid = 'pg_proc'::regclass
                AND dependency.objid = proc.oid
                AND dependency.deptype = 'e'
          )
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON FUNCTION %s FROM ' ||
            'PUBLIC, decision_app, decision_collector, decision_disclosure_reader, ' ||
            'decision_market_writer, decision_portfolio_writer, decision_risk_writer, ' ||
            'decision_fill_writer, decision_rag_writer, decision_rag_admin, decision_rag_query',
            routine.signature
        );
    END LOOP;
END
$revoke_custom_function_privileges$;

DO $decision_runtime_function_privileges$
BEGIN
    IF to_regprocedure('public.read_decision_owner_projection()') IS NOT NULL THEN
        GRANT EXECUTE ON FUNCTION
            read_decision_owner_projection(),
            read_decision_audit_projection(),
            find_decision_idempotency_result(text, text, timestamptz),
            next_decision_idempotency_generation(text, text)
        TO decision_app;
    END IF;
    IF to_regprocedure('public.read_kill_switch_gate()') IS NOT NULL THEN
        GRANT EXECUTE ON FUNCTION
            read_kill_switch_gate(),
            revalidate_kill_switch_admin(text, bigint),
            read_kill_switch_audit_projection(),
            read_decision_usability(),
            invalidate_unused_decisions_for_kill_switch(bigint, timestamptz, text)
        TO decision_app;
    END IF;
    IF to_regprocedure('public.read_mock_order_decision(text,text,text)') IS NOT NULL THEN
        GRANT EXECUTE ON FUNCTION
            read_mock_order_decision(text, text, text),
            find_mock_order_idempotency_result(text, text, timestamptz, text),
            read_mock_order_owner_projection(text, text, text),
            create_mock_order(jsonb, text),
            request_mock_order_cancel(jsonb, text)
        TO decision_app;
    END IF;
    IF to_regprocedure('public.read_paper_order_context(text,text,text)') IS NOT NULL THEN
        GRANT EXECUTE ON FUNCTION
            read_paper_order_context(text, text, text),
            find_paper_order_idempotency_result(text, text, timestamptz, text),
            read_paper_balance_projection(text, text, text),
            create_paper_order(jsonb, text)
        TO decision_app;
    END IF;
    IF to_regprocedure('public.read_order_reconciliation_state(jsonb,text)') IS NOT NULL THEN
        GRANT EXECUTE ON FUNCTION
            read_order_reconciliation_state(jsonb, text),
            acquire_order_fill_reconciliation_lock(jsonb, text),
            apply_stored_order_fills(jsonb, text),
            read_owned_order_fills(jsonb, text)
        TO decision_app;
    END IF;
    IF to_regprocedure('public.record_mock_order_provider_outcome(jsonb,text)') IS NOT NULL THEN
        GRANT EXECUTE ON FUNCTION
            record_mock_order_provider_outcome(jsonb, text)
        TO decision_app;
    END IF;
    IF to_regprocedure('public.read_rag_source_registry(text)') IS NOT NULL THEN
        GRANT EXECUTE ON FUNCTION
            read_rag_source_registry(text)
        TO decision_app;
    END IF;
    IF to_regprocedure(
        'public.record_rag_consent_event(text,text,text,text)'
    ) IS NOT NULL THEN
        -- bootstrap 재적용 뒤에도 앱은 V20의 owner-bound SECURITY DEFINER 경계만 호출한다.
        GRANT EXECUTE ON FUNCTION
            record_rag_consent_event(text, text, text, text),
            read_effective_rag_consent(text),
            claim_rag_answer(text, text, text, integer),
            mark_rag_provider_attempt(text, text, text, text, text, text, jsonb),
            complete_rag_answer(
                text, text, text, text, text, text,
                double precision, boolean, text[],
                text, bytea, bytea, bytea, bytea, bytea, bytea,
                bytea, bytea, bytea, timestamptz, integer, jsonb
            ),
            fail_rag_answer_before_provider(text, text, text),
            mark_rag_answer_unknown_after_provider(text, text, text),
            read_rag_history_metadata(text, timestamptz, text, integer),
            read_rag_history_detail(text, text),
            read_rag_history_citations(text, text),
            delete_owned_rag_history(text, text),
            upsert_owned_rag_answer_feedback(text, text, boolean),
            purge_expired_rag_history(integer)
        TO decision_app;
    END IF;
    IF to_regprocedure('public.read_active_rag_chunks(text,integer)') IS NOT NULL THEN
        GRANT EXECUTE ON FUNCTION
            read_active_rag_chunks(text, integer)
        TO decision_rag_query;
    END IF;
    IF to_regprocedure('public.retire_rag_source_for_relocation(text,text)') IS NOT NULL THEN
        -- writer는 rag_sources UPDATE 대신 exact previous→next identity 전이만 호출한다.
        GRANT EXECUTE ON FUNCTION
            retire_rag_source_for_relocation(text, text)
        TO decision_rag_writer;
    END IF;
    IF to_regprocedure('public.finalize_rag_embedding_staging(text,text,text,integer,text)') IS NOT NULL THEN
        -- staging writer는 bounded SECURITY DEFINER finalizer와 자기 run purge만 호출한다.
        GRANT EXECUTE ON FUNCTION
            finalize_rag_embedding_staging(text, text, text, integer, text),
            purge_rag_embedding_staging(text, text)
        TO decision_rag_writer;
    END IF;
    IF to_regprocedure('public.finalize_rag_embedding_staging_v2(text,text,text,integer,text)') IS NOT NULL THEN
        -- full generation bootstrap 재적용 뒤에도 writer의 유일한 final-table 경계는 bounded v2 함수다.
        GRANT EXECUTE ON FUNCTION
            finalize_rag_embedding_staging_v2(text, text, text, integer, text)
        TO decision_rag_writer;
    END IF;
    IF to_regprocedure(
        'public.register_rag_verified_source_card(text,text,text,text,timestamptz,text[])'
    ) IS NOT NULL THEN
        -- source-card status/topic/exact alias는 raw sidecar DML 없이 immutable revision 함수로만 등록한다.
        GRANT EXECUTE ON FUNCTION
            register_rag_verified_source_card(
                text, text, text, text, timestamptz, text[]
            )
        TO decision_rag_writer;
    END IF;
    IF to_regprocedure(
        'public.create_rag_retrieval_scope_claim(text,text,text[])'
    ) IS NOT NULL THEN
        -- 인증된 app만 active pointer에 묶인 짧은 owner/session claim을 발급한다.
        GRANT EXECUTE ON FUNCTION
            create_rag_retrieval_scope_claim(text, text, text[])
        TO decision_app;
    END IF;
    IF to_regprocedure(
        'public.search_authorized_rag_exact(text,text,text,text[],text[])'
    ) IS NOT NULL THEN
        -- query role은 raw table 대신 independently scoped exact/lexical/dense 함수만 호출한다.
        GRANT EXECUTE ON FUNCTION
            search_authorized_rag_exact(text, text, text, text[], text[]),
            search_authorized_rag_lexical(text, text, text, text[], text),
            search_authorized_rag_dense(text, text, text, text[], vector)
        TO decision_rag_query;
    END IF;
    IF to_regprocedure(
        'public.activate_verified_rag_generation(text,text,bigint,text,text,text,text,text,integer,integer,integer,text,text,text,text,text,text,text,text,text,numeric,text)'
    ) IS NOT NULL THEN
        -- admin은 raw table DML 없이 verification projection과 단일 CAS 전이만 호출한다.
        GRANT EXECUTE ON FUNCTION
            read_rag_activation_state(),
            read_rag_generation_embeddings_for_verification(text, text, integer),
            activate_verified_rag_generation(
                text, text, bigint, text, text, text, text, text,
                integer, integer, integer,
                text, text, text, text, text, text, text, text, text,
                numeric, text
            )
        TO decision_rag_admin;
    END IF;
    IF to_regprocedure('public.issue_rag_rpc_scope(text,text,jsonb)') IS NOT NULL THEN
        -- V22의 Spring→Python RPC 경계는 app actor scope와 citation 재검증 함수만 노출한다.
        GRANT EXECUTE ON FUNCTION
            issue_rag_rpc_scope(text, text, jsonb),
            recheck_rag_rpc_citations(
                text, text, text, text, bigint, text, text, jsonb
            )
        TO decision_app;
    END IF;
    IF to_regprocedure('public.read_rag_v2_corpus_status(text)') IS NOT NULL THEN
        -- V24 RAG v2는 direct API라도 raw table이 아니라 owner-bound definer 함수만 호출한다.
        GRANT EXECUTE ON FUNCTION
            read_rag_v2_corpus_status(text),
            read_rag_v2_history_metadata(text, timestamptz, text, integer),
            read_rag_v2_history_detail(text, text),
            delete_owned_rag_v2_history(text, text)
        TO decision_app;
        REVOKE ALL PRIVILEGES ON FUNCTION
            delete_owner_rag_v2_document(text, text, text, text)
        FROM decision_app;
    END IF;
    IF to_regprocedure(
        'public.issue_rag_v2_immutable_import_ticket(text,text,text,text)'
    ) IS NOT NULL THEN
        -- V25의 owner capability는 raw table grant 없이 app→writer→admin 세 role로 나눈다.
        GRANT EXECUTE ON FUNCTION
            record_rag_v2_immutable_consent(text, text, text, text),
            issue_rag_v2_immutable_import_ticket(text, text, text, text)
        TO decision_app;
        GRANT EXECUTE ON FUNCTION
            consume_rag_v2_immutable_import_ticket(text, text, text, text, text)
        TO decision_rag_writer;
        GRANT EXECUTE ON FUNCTION
            activate_rag_v2_immutable_public_base(text, text, bigint, text),
            activate_rag_v2_immutable_owner_bundle(text, text, text, bigint, text, text),
            delete_rag_v2_immutable_owner_document(
                text, text, text, text, bigint, text, text, text
            )
        TO decision_rag_admin;
    END IF;
    IF to_regprocedure(
        'public.issue_rag_v2_immutable_import_ticket_v2(text,text,text,text,text)'
    ) IS NOT NULL
       AND to_regprocedure(
           'public.stage_rag_v2_immutable_owner_document_v3(text,text,jsonb)'
       ) IS NOT NULL THEN
        -- V60 owner library profile 선택도 bootstrap 후 raw table 권한 없이 복구한다.
        GRANT EXECUTE ON FUNCTION
            issue_rag_v2_immutable_import_ticket_v2(text, text, text, text, text)
        TO decision_app;
        GRANT EXECUTE ON FUNCTION
            stage_rag_v2_immutable_owner_document_v3(text, text, jsonb)
        TO decision_rag_writer;
        IF to_regprocedure(
            'public.stage_rag_v2_immutable_owner_bge_document_v2(text,text,jsonb)'
        ) IS NOT NULL THEN
            REVOKE ALL PRIVILEGES ON FUNCTION
                stage_rag_v2_immutable_owner_bge_document_v2(text, text, jsonb)
            FROM decision_rag_writer;
        END IF;
    END IF;
    IF to_regprocedure(
        'public.reserve_rag_v2_owner_voyage_import(text,text,text,text,text,text,text[],integer,integer,integer)'
    ) IS NOT NULL
       AND to_regprocedure(
           'public.complete_rag_v2_owner_voyage_import(text,text,text,jsonb,integer,integer,bigint)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.fail_rag_v2_owner_voyage_import_unknown_billing(text,text,text,text)'
       ) IS NOT NULL THEN
        -- owner Voyage는 reserve/atomic completion/terminal failure 함수만 writer에 복구한다.
        GRANT EXECUTE ON FUNCTION
            reserve_rag_v2_owner_voyage_import(
                text, text, text, text, text, text, text[], integer, integer, integer
            ),
            complete_rag_v2_owner_voyage_import(
                text, text, text, jsonb, integer, integer, bigint
            ),
            fail_rag_v2_owner_voyage_import_unknown_billing(text, text, text, text)
        TO decision_rag_writer;
    END IF;
    IF to_regprocedure(
        'public.stage_rag_v2_immutable_public_bge_document(jsonb)'
    ) IS NOT NULL
       AND to_regprocedure(
           'public.evaluate_rag_v2_immutable_public_bge_component(text,jsonb)'
       ) IS NOT NULL THEN
        -- V36 public corpus writer는 raw table DML 대신 source-stage/evaluation definer pair만 재부여한다.
        GRANT EXECUTE ON FUNCTION
            stage_rag_v2_immutable_public_bge_document(jsonb),
            evaluate_rag_v2_immutable_public_bge_component(text, jsonb)
        TO decision_rag_writer;
    END IF;
    IF to_regprocedure(
        'public.stage_rag_v2_immutable_external_exact30_voyage_document(jsonb)'
    ) IS NOT NULL THEN
        -- V37은 S4.7C external-safe exact-30만 stage하며 Voyage 호출·평가·activation 권한은 주지 않는다.
        GRANT EXECUTE ON FUNCTION
            stage_rag_v2_immutable_external_exact30_voyage_document(jsonb)
        TO decision_rag_writer;
    END IF;
    IF to_regprocedure(
        'public.reserve_rag_v2_immutable_voyage_usage(text,text,text,text,text,timestamp with time zone,integer,integer,bigint,bigint)'
    ) IS NOT NULL
       AND to_regprocedure(
           'public.claim_rag_v2_immutable_voyage_usage_attempt(text)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.commit_rag_v2_immutable_voyage_usage(text,integer,bigint)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.mark_rag_v2_immutable_voyage_usage_unknown_billing(text)'
       ) IS NOT NULL THEN
        -- V38 activation ledger도 bootstrap 재적용 뒤 raw table 없이 writer capability만 회복한다.
        GRANT EXECUTE ON FUNCTION
            reserve_rag_v2_immutable_voyage_usage(
                text, text, text, text, text, timestamptz,
                integer, integer, bigint, bigint
            ),
            claim_rag_v2_immutable_voyage_usage_attempt(text),
            commit_rag_v2_immutable_voyage_usage(text, integer, bigint),
            mark_rag_v2_immutable_voyage_usage_unknown_billing(text)
        TO decision_rag_writer;
    END IF;
    IF to_regprocedure(
        'public.read_rag_v2_vertex_generation_evidence(text,text,text,jsonb)'
    ) IS NOT NULL
       AND to_regprocedure(
           'public.persist_rag_v2_immutable_vertex_history(text,text,text,text,text,text,double precision,text[],text,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,timestamp with time zone,jsonb)'
       ) IS NOT NULL THEN
        -- V39의 앱은 sanitized evidence와 암호화 history definer pair만 다시 받는다.
        GRANT EXECUTE ON FUNCTION
            read_rag_v2_vertex_generation_evidence(text, text, text, jsonb),
            persist_rag_v2_immutable_vertex_history(
                text, text, text, text, text, text, double precision, text[], text,
                bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea,
                timestamptz, jsonb
            )
        TO decision_app;
    END IF;
    IF to_regprocedure(
        'public.reserve_rag_v2_immutable_vertex_usage(text,text,text,text,text,text,text,text,text,text,text,timestamp with time zone,integer,integer,integer,bigint,bigint,bigint,integer,integer,text,jsonb)'
    ) IS NOT NULL
       AND to_regprocedure(
           'public.claim_rag_v2_immutable_vertex_token_attempt(text,text)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.claim_rag_v2_immutable_vertex_generate_content_attempt(text,text)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.commit_rag_v2_immutable_vertex_usage(text,text,integer,integer,integer)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.mark_rag_v2_immutable_vertex_usage_unknown_billing(text,text)'
       ) IS NOT NULL THEN
        -- V57 service-account OAuth ledger는 token과 generation 각각 1회 capability만 재부여한다.
        GRANT EXECUTE ON FUNCTION
            reserve_rag_v2_immutable_vertex_usage(
                text, text, text, text, text, text, text, text, text, text, text,
                timestamptz, integer, integer, integer,
                bigint, bigint, bigint, integer, integer, text, jsonb
            ),
            claim_rag_v2_immutable_vertex_token_attempt(text, text),
            claim_rag_v2_immutable_vertex_generate_content_attempt(text, text),
            commit_rag_v2_immutable_vertex_usage(text, text, integer, integer, integer),
            mark_rag_v2_immutable_vertex_usage_unknown_billing(text, text)
        TO decision_app;
    ELSIF to_regprocedure(
        'public.reserve_rag_v2_immutable_vertex_usage(text,text,text,text,text,text,text,text,text,text,text,timestamp with time zone,integer,integer,integer,bigint,bigint,bigint,integer,integer,jsonb)'
    ) IS NOT NULL
       AND to_regprocedure(
           'public.claim_rag_v2_immutable_vertex_token_attempt(text,text)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.claim_rag_v2_immutable_vertex_generate_content_attempt(text,text)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.commit_rag_v2_immutable_vertex_usage(text,text,integer,integer,integer)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.mark_rag_v2_immutable_vertex_usage_unknown_billing(text,text)'
       ) IS NOT NULL THEN
        -- V42 이전 volume은 V52/V57까지 migrate하기 전 legacy token/generate ledger만 재부여한다.
        GRANT EXECUTE ON FUNCTION
            reserve_rag_v2_immutable_vertex_usage(
                text, text, text, text, text, text, text, text, text, text, text,
                timestamptz, integer, integer, integer,
                bigint, bigint, bigint, integer, integer, jsonb
            ),
            claim_rag_v2_immutable_vertex_token_attempt(text, text),
            claim_rag_v2_immutable_vertex_generate_content_attempt(text, text),
            commit_rag_v2_immutable_vertex_usage(text, text, integer, integer, integer),
            mark_rag_v2_immutable_vertex_usage_unknown_billing(text, text)
        TO decision_app;
    END IF;
    IF to_regprocedure(
        'public.prepare_rag_v2_immutable_public_base_activation(text,text)'
    ) IS NOT NULL THEN
        -- V43 public-base pointer 준비는 activation admin에게만 재부여한다.
        GRANT EXECUTE ON FUNCTION
            prepare_rag_v2_immutable_public_base_activation(text, text)
        TO decision_rag_admin;
    END IF;
    IF to_regprocedure(
        'public.issue_rag_v2_immutable_owner_delete_ticket(text,text,text)'
    ) IS NOT NULL THEN
        -- V44 ticket 발급은 owner-bound app capability로 유지한다.
        GRANT EXECUTE ON FUNCTION
            issue_rag_v2_immutable_owner_delete_ticket(text, text, text)
        TO decision_app;
    END IF;
    IF to_regprocedure(
        'public.delete_rag_v2_immutable_owner_document_with_ticket(text,text,text,text,text,text)'
    ) IS NOT NULL THEN
        -- V44가 V25의 delete-before-replacement admin capability를 ticket boundary로 대체한다.
        REVOKE ALL PRIVILEGES ON FUNCTION
            delete_rag_v2_immutable_owner_document(
                text, text, text, text, bigint, text, text, text
            ),
            replace_and_delete_rag_v2_immutable_owner_document(
                text, text, text, text, text
            )
        FROM decision_rag_admin;
        -- 실제 hard-delete는 ticket 검증을 수행하는 admin capability에만 남긴다.
        GRANT EXECUTE ON FUNCTION
            delete_rag_v2_immutable_owner_document_with_ticket(
                text, text, text, text, text, text
            )
        TO decision_rag_admin;
    END IF;
    IF to_regprocedure(
        'public.stage_rag_v2_immutable_public_voyage_document(jsonb)'
    ) IS NOT NULL
       AND to_regprocedure(
           'public.evaluate_rag_v2_immutable_public_voyage_component(text,jsonb)'
       ) IS NOT NULL THEN
        -- V45 public Voyage staging/evaluation은 raw table DML 없이 writer pair만 재부여한다.
        GRANT EXECUTE ON FUNCTION
            stage_rag_v2_immutable_public_voyage_document(jsonb),
            evaluate_rag_v2_immutable_public_voyage_component(text, jsonb)
        TO decision_rag_writer;
    END IF;
    IF to_regprocedure(
        'public.reserve_rag_v2_immutable_voyage_query_usage(text,text,text,text,text,text,text,timestamp with time zone,integer,integer,bigint,bigint)'
    ) IS NOT NULL
       AND to_regprocedure(
           'public.claim_rag_v2_immutable_voyage_query_usage_attempt(text)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.commit_rag_v2_immutable_voyage_query_usage(text,integer,bigint)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.mark_rag_v2_immutable_voyage_query_usage_unknown_billing(text)'
       ) IS NOT NULL THEN
        -- V46 query usage는 writer가 raw table이 아니라 packet-bound one-shot ledger 함수만 재획득한다.
        GRANT EXECUTE ON FUNCTION
            reserve_rag_v2_immutable_voyage_query_usage(
                text, text, text, text, text, text, text, timestamptz,
                integer, integer, bigint, bigint
            ),
            claim_rag_v2_immutable_voyage_query_usage_attempt(text),
            commit_rag_v2_immutable_voyage_query_usage(text, integer, bigint),
            mark_rag_v2_immutable_voyage_query_usage_unknown_billing(text)
        TO decision_rag_writer;
    END IF;
    IF to_regprocedure(
        'public.reserve_rag_v2_immutable_voyage_usage_with_tokenizer(text,text,text,text,text,text,timestamp with time zone,integer,integer,bigint,bigint)'
    ) IS NOT NULL
       AND to_regprocedure(
           'public.commit_rag_v2_immutable_voyage_usage_with_tokenizer(text,integer,integer,bigint)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.reserve_rag_v2_immutable_voyage_query_usage_with_tokenizer(text,text,text,text,text,text,text,text,timestamp with time zone,integer,integer,bigint,bigint)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.commit_rag_v2_immutable_voyage_query_usage_with_tokenizer(text,integer,integer,bigint)'
       ) IS NOT NULL THEN
        -- V51은 packet-bound official tokenizer와 expected input token receipt를 갖는 새 capability만 복구한다.
        GRANT EXECUTE ON FUNCTION
            reserve_rag_v2_immutable_voyage_usage_with_tokenizer(
                text, text, text, text, text, text, timestamptz,
                integer, integer, bigint, bigint
            ),
            commit_rag_v2_immutable_voyage_usage_with_tokenizer(text, integer, integer, bigint),
            reserve_rag_v2_immutable_voyage_query_usage_with_tokenizer(
                text, text, text, text, text, text, text, text, timestamptz,
                integer, integer, bigint, bigint
            ),
            commit_rag_v2_immutable_voyage_query_usage_with_tokenizer(
                text, integer, integer, bigint
            )
        TO decision_rag_writer;
    END IF;
    IF to_regprocedure(
        'public.stage_rag_v2_immutable_owner_bge_document_v2(text,text,jsonb)'
    ) IS NOT NULL
       AND to_regprocedure(
           'public.stage_rag_v2_immutable_owner_document_v3(text,text,jsonb)'
       ) IS NULL THEN
        -- V60 전 schema에서만 BGE 전용 legacy staging capability를 복구한다.
        GRANT EXECUTE ON FUNCTION
            stage_rag_v2_immutable_owner_bge_document_v2(text, text, jsonb)
        TO decision_rag_writer;
    END IF;
    IF to_regprocedure(
        'public.reserve_rag_v2_immutable_voyage_document_batch_usage(text,text,text,text,text,text,timestamp with time zone,integer,integer,bigint,bigint)'
    ) IS NOT NULL
       AND to_regprocedure(
        'public.claim_rag_v2_immutable_voyage_document_batch_attempt(text,text,text,text)'
    ) IS NOT NULL
       AND to_regprocedure(
        'public.mark_rag_v2_immutable_voyage_document_batch_unknown_billing(text,text,text)'
    ) IS NOT NULL
       AND to_regprocedure(
        'public.commit_and_stage_rag_v2_immutable_voyage_document_batch(jsonb)'
    ) IS NOT NULL
       AND to_regprocedure(
           'public.load_rag_v2_immutable_voyage_document_batch_vectors(text)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.record_rag_v2_bge_public_execution_supersession(text,text)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.reserve_rag_v2_immutable_voyage_evaluation_batch_usage(text,text,text,text,text,text,text,text,timestamp with time zone,integer,integer,bigint,bigint)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.claim_rag_v2_immutable_voyage_evaluation_batch_attempt(text,text,text,text,text)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.mark_rag_v2_immutable_voyage_evaluation_batch_unknown_billing(text,text,text)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.commit_and_stage_rag_v2_immutable_voyage_evaluation_batch(jsonb)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.load_rag_v2_immutable_voyage_evaluation_batch_vectors(text,text,text)'
       ) IS NOT NULL THEN
        -- V54는 raw table grant 없이 document/evaluation stage-resume와 terminal BGE marker만 복구한다.
        REVOKE ALL PRIVILEGES ON FUNCTION
            stage_rag_v2_immutable_voyage_document_batch(jsonb)
        FROM decision_rag_writer;
        GRANT EXECUTE ON FUNCTION
            reserve_rag_v2_immutable_voyage_document_batch_usage(
                text, text, text, text, text, text, timestamptz,
                integer, integer, bigint, bigint
            ),
            claim_rag_v2_immutable_voyage_document_batch_attempt(text, text, text, text),
            mark_rag_v2_immutable_voyage_document_batch_unknown_billing(text, text, text),
            commit_and_stage_rag_v2_immutable_voyage_document_batch(jsonb),
            load_rag_v2_immutable_voyage_document_batch_vectors(text),
            reserve_rag_v2_immutable_voyage_evaluation_batch_usage(
                text, text, text, text, text, text, text, text, timestamptz,
                integer, integer, bigint, bigint
            ),
            claim_rag_v2_immutable_voyage_evaluation_batch_attempt(
                text, text, text, text, text
            ),
            mark_rag_v2_immutable_voyage_evaluation_batch_unknown_billing(text, text, text),
            commit_and_stage_rag_v2_immutable_voyage_evaluation_batch(jsonb),
            load_rag_v2_immutable_voyage_evaluation_batch_vectors(text, text, text),
            record_rag_v2_bge_public_execution_supersession(text, text)
        TO decision_rag_writer;
    END IF;
    IF to_regprocedure(
        'public.issue_rag_v2_retrieval_scope(text,text,text[])'
    ) IS NOT NULL
       AND to_regprocedure(
           'public.canonicalize_rag_v2_immutable_retrieval_citations(text,text,text,jsonb)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.persist_rag_v2_immutable_retrieval_history(text,text,text,text,text,text,double precision,text[],text,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,bytea,timestamp with time zone,jsonb)'
       ) IS NOT NULL THEN
        -- 앱은 claim 발급과 content-free encrypted history definer capability만 재획득한다.
        GRANT EXECUTE ON FUNCTION
            issue_rag_v2_retrieval_scope(text, text, text[]),
            canonicalize_rag_v2_immutable_retrieval_citations(text, text, text, jsonb),
            persist_rag_v2_immutable_retrieval_history(
                text, text, text, text, text, text, double precision, text[], text,
                bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea,
                timestamptz, jsonb
            )
        TO decision_app;
    END IF;
    IF to_regprocedure(
        'public.read_rag_v2_vertex_prepared_scope(text,text,text,text[])'
    ) IS NOT NULL THEN
        -- V48은 app이 raw scope table 없이 packet-bound two-minute claim만 resume하게 한다.
        GRANT EXECUTE ON FUNCTION
            read_rag_v2_vertex_prepared_scope(text, text, text, text[])
        TO decision_app;
    END IF;
    IF to_regprocedure(
        'public.issue_rag_v2_retrieval_scope_v2(text,text,text[])'
    ) IS NOT NULL
       AND to_regprocedure(
           'public.read_rag_v2_vertex_prepared_scope_v2(text,text,text,text[])'
       ) IS NOT NULL THEN
        -- V60 app projection은 owner profile이 결박된 scope와 Vertex precheck만 노출한다.
        GRANT EXECUTE ON FUNCTION
            issue_rag_v2_retrieval_scope_v2(text, text, text[]),
            read_rag_v2_vertex_prepared_scope_v2(text, text, text, text[])
        TO decision_app;
    END IF;
    IF to_regprocedure(
        'public.issue_rag_v2_retrieval_scope_v3(text,text,text[])'
    ) IS NOT NULL THEN
        -- V64 provider preparation 전용 scope는 5분 TTL issuer만 추가로 노출한다.
        GRANT EXECUTE ON FUNCTION
            issue_rag_v2_retrieval_scope_v3(text, text, text[])
        TO decision_app;
    END IF;
    IF to_regprocedure(
        'public.read_rag_v2_retrieval_scope(text,text,text)'
    ) IS NOT NULL
       AND to_regprocedure(
           'public.read_rag_v2_retrieval_scope_by_claim(text,text)'
       ) IS NOT NULL THEN
        -- query role은 owner/session-scoped projection과 각 channel top-30 함수만 다시 받는다.
        GRANT EXECUTE ON FUNCTION
            read_rag_v2_retrieval_scope(text, text, text),
            read_rag_v2_retrieval_scope_by_claim(text, text),
            search_authorized_rag_v2_exact(text, text, text, text[], text[]),
            search_authorized_rag_v2_lexical(text, text, text, text[], text),
            search_authorized_rag_v2_dense(text, text, text, text[], vector)
        TO decision_rag_query;
    END IF;
    IF to_regprocedure(
        'public.read_rag_v2_retrieval_scope_v2(text,text,text)'
    ) IS NOT NULL
       AND to_regprocedure(
           'public.read_rag_v2_retrieval_scope_by_claim_v2(text,text)'
       ) IS NOT NULL
       AND to_regprocedure(
           'public.search_authorized_rag_v2_dense_v2(text,text,text,text[],vector,vector)'
       ) IS NOT NULL THEN
        -- 서로 다른 vector space는 V60의 profile-local rank projection으로만 query role에 노출한다.
        GRANT EXECUTE ON FUNCTION
            read_rag_v2_retrieval_scope_v2(text, text, text),
            read_rag_v2_retrieval_scope_by_claim_v2(text, text),
            search_authorized_rag_v2_dense_v2(text, text, text, text[], vector, vector)
        TO decision_rag_query;
    END IF;
    IF to_regprocedure(
        'public.prepare_rag_v2_immutable_owner_overlay(text,text)'
    ) IS NOT NULL THEN
        GRANT EXECUTE ON FUNCTION
            prepare_rag_v2_immutable_owner_overlay(text, text)
        TO decision_rag_admin;
    END IF;
    IF to_regprocedure(
        'public.replace_and_delete_rag_v2_immutable_owner_document(text,text,text,text,text)'
    ) IS NOT NULL
       AND to_regprocedure(
           'public.delete_rag_v2_immutable_owner_document_with_ticket(text,text,text,text,text,text)'
       ) IS NULL THEN
        -- V44 ticket boundary 이전 schema에서만 replacement와 deletion을 묶는 legacy capability를 유지한다.
        GRANT EXECUTE ON FUNCTION
            replace_and_delete_rag_v2_immutable_owner_document(text, text, text, text, text)
        TO decision_rag_admin;
    END IF;
    IF to_regprocedure(
        'public.record_rag_v2_immutable_consent_v2(text,text,text,text,text,text,text)'
    ) IS NOT NULL
       AND to_regprocedure(
           'public.read_rag_v2_immutable_effective_consent(text)'
       ) IS NOT NULL THEN
        -- V26 control plane도 raw table 대신 owner-bound immutable consent function만 재부여한다.
        GRANT EXECUTE ON FUNCTION
            record_rag_v2_immutable_consent_v2(
                text, text, text, text, text, text, text
            ),
            read_rag_v2_immutable_effective_consent(text)
        TO decision_app;
    END IF;
    IF to_regprocedure(
        'public.record_s4_9_strong_llm_usage(text,text,text,text,text,text,text,integer,integer,integer,integer,integer,text)'
    ) IS NOT NULL THEN
        -- bootstrap 재적용 뒤에도 V66 table 직접 권한은 닫고 S4.9 definer capability만 복원한다.
        GRANT EXECUTE ON FUNCTION
            record_s4_9_strong_llm_usage(
                text, text, text, text, text, text, text,
                integer, integer, integer, integer, integer, text
            ),
            record_s4_9_web_evidence_metadata(
                text, text, text, text, text, text, text, timestamptz, text, timestamptz
            ),
            sync_s4_9_mcp_oauth_client(text, text, text, text[], text[], text),
            upsert_s4_9_mcp_oauth_code_hash(
                text, text, text, bigint, text, text, text[], text, timestamptz
            ),
            consume_s4_9_mcp_oauth_code_hash(text),
            rotate_s4_9_mcp_refresh_token_hash(
                text, text, text, bigint, text, text[], timestamptz
            ),
            revoke_s4_9_mcp_refresh_token_family(text),
            issue_s4_9_answer_validation_receipt(
                text, text, text, text, text, text, text, timestamptz
            ),
            consume_s4_9_validation_and_save_history(
                text, text, text, text, text, text,
                bytea, bytea, bytea, bytea, bytea, bytea,
                bytea, bytea, bytea, timestamptz
            ),
            persist_s4_9_strong_llm_history(
                text, text, text, text, text, text, text, double precision, text[], text,
                bytea, bytea, bytea, bytea, bytea, bytea,
                bytea, bytea, bytea, timestamptz, jsonb
            )
        TO decision_app;
    END IF;
    IF to_regprocedure(
        'public.issue_s4_9_mcp_retrieval_scope(text,text,text[],boolean)'
    ) IS NOT NULL THEN
        -- V67 public-only claim은 OAuth owner scope를 발급 전에 결박하고 raw claim table 권한은 열지 않는다.
        GRANT EXECUTE ON FUNCTION
            issue_s4_9_mcp_retrieval_scope(text, text, text[], boolean)
        TO decision_app;
    END IF;
    IF to_regprocedure(
        'public.authorize_s4_9_runtime_voyage_query(text,text,text)'
    ) IS NOT NULL THEN
        -- V68 app은 authenticated owner scope와 질문 hash만 one-shot query 권한으로 결속한다.
        GRANT EXECUTE ON FUNCTION
            authorize_s4_9_runtime_voyage_query(text, text, text)
        TO decision_app;
    END IF;
    IF to_regprocedure(
        'public.reserve_s4_9_runtime_voyage_query_usage(text,text,text)'
    ) IS NOT NULL THEN
        -- query writer는 table 권한 없이 V68 authorization을 한 번만 usage ledger로 소비한다.
        GRANT EXECUTE ON FUNCTION
            reserve_s4_9_runtime_voyage_query_usage(text, text, text)
        TO decision_rag_writer;
    END IF;
    IF to_regprocedure(
        'public.reserve_s4_9_google_grounding_budget(text,text,text,text,date,integer,integer)'
    ) IS NOT NULL THEN
        -- V70도 table 권한 없이 Google budget·provenance·history definer capability만 복원한다.
        GRANT EXECUTE ON FUNCTION
            reserve_s4_9_google_grounding_budget(text, text, text, text, date, integer, integer),
            settle_s4_9_google_grounding_budget(text, text, text, integer),
            record_s4_9_grounding_provenance(text, text, jsonb, jsonb),
            record_s4_9_read_provenance(
                text, text, text, text, text, text, text, text, text, text
            ),
            record_s4_9_search_attempt(text, text, text, text, text, integer),
            canonicalize_s4_9_strong_llm_citations_v2(text, text, text, text, jsonb),
            persist_s4_9_strong_llm_history_v2(
                text, text, text, text, text, text, text, double precision, text[], text,
                bytea, bytea, bytea, bytea, bytea, bytea,
                bytea, bytea, bytea, timestamptz, jsonb
            ),
            record_s4_9_strong_llm_usage_v2(
                text, text, text, text, text, text,
                integer, integer, integer, integer, integer, text,
                integer, integer, text, text, text
            )
        TO decision_app;
    END IF;
END
$decision_runtime_function_privileges$;

DO $cross_market_runtime_privileges$
BEGIN
    IF to_regclass('public.latest_cross_market_risk_snapshots') IS NOT NULL THEN
        -- V23 bootstrap 재적용 뒤에도 app은 bounded latest view만 읽고 raw evidence에는 접근하지 않는다.
        GRANT SELECT ON TABLE
            latest_cross_market_observations,
            latest_analyst_revision_evidence,
            latest_market_cause_evidence,
            latest_cross_market_risk_snapshots
        TO decision_app;
        -- market writer는 append-only SECURITY DEFINER 함수만 호출하며 raw table DML은 갖지 않는다.
        GRANT EXECUTE ON FUNCTION
            append_market_source_entitlement(jsonb),
            append_cross_market_exposure_catalog_entry(jsonb),
            append_cross_market_observation(jsonb),
            append_analyst_revision_evidence(jsonb),
            append_market_cause_evidence(jsonb)
        TO decision_market_writer;
    END IF;
    IF to_regprocedure('public.append_owned_foreign_news_sentiment(text,jsonb)') IS NOT NULL
       AND to_regprocedure('public.read_owned_foreign_news_sentiment(text,text)') IS NOT NULL THEN
        -- V49의 외신 runtime은 raw table DML 없이 market writer와 authenticated app을 분리한다.
        REVOKE ALL PRIVILEGES ON TABLE foreign_news_sentiment_aggregates
        FROM decision_app, decision_market_writer;
        GRANT EXECUTE ON FUNCTION
            append_owned_foreign_news_sentiment(text, jsonb)
        TO decision_market_writer;
        GRANT EXECUTE ON FUNCTION
            read_owned_foreign_news_sentiment(text, text)
        TO decision_app;
    END IF;
    IF to_regprocedure('public.append_s48_runtime_sanitized_projection(jsonb)') IS NOT NULL
       AND to_regprocedure('public.read_latest_s48_runtime_sanitized_projection(text)') IS NOT NULL THEN
        -- V50은 nine-lane typed state만 function capability로 노출하고 raw table DML은 금지한다.
        REVOKE ALL PRIVILEGES ON TABLE s48_runtime_sanitized_projections
        FROM decision_app, decision_market_writer;
        GRANT EXECUTE ON FUNCTION
            append_s48_runtime_sanitized_projection(jsonb)
        TO decision_market_writer;
        GRANT EXECUTE ON FUNCTION
            read_latest_s48_runtime_sanitized_projection(text)
        TO decision_app;
    END IF;
END
$cross_market_runtime_privileges$;

DO $block$
BEGIN
    IF to_regclass('public.flyway_schema_history') IS NOT NULL THEN
        -- 기존 volume에 role bootstrap을 재적용해도 runtime이 migration 이력을 변조하지 못한다.
        REVOKE ALL PRIVILEGES ON TABLE public.flyway_schema_history FROM decision_app;
        REVOKE ALL PRIVILEGES ON TABLE public.flyway_schema_history FROM decision_collector;
        REVOKE ALL PRIVILEGES ON TABLE public.flyway_schema_history FROM decision_disclosure_reader;
        REVOKE ALL PRIVILEGES ON TABLE public.flyway_schema_history FROM decision_fill_writer;
        REVOKE ALL PRIVILEGES ON TABLE public.flyway_schema_history FROM decision_rag_writer;
        REVOKE ALL PRIVILEGES ON TABLE public.flyway_schema_history FROM decision_rag_admin;
        REVOKE ALL PRIVILEGES ON TABLE public.flyway_schema_history FROM decision_rag_query;
    END IF;
END
$block$;
COMMIT;
SQL
