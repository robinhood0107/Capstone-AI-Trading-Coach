-- 운영에서는 infra/init/02-application-roles.sh가 만드는 role을 Flyway보다 먼저 재현한다.
-- 실제 non-superuser flyway로 V2를 재생할 수 있도록 extension만 database owner가 선설치한다.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE ROLE decision_app
  LOGIN PASSWORD 'app-test' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_worker
  LOGIN PASSWORD 'worker-test-secret-0001' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_replay
  LOGIN PASSWORD 'replay-test-secret-0001' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_identity
  LOGIN PASSWORD 'identity-test-secret-0001' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_auth
  LOGIN PASSWORD 'auth-test-secret-0001' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_replay_authorizer
  LOGIN PASSWORD 'replay-authorizer-test-0001' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_demo
  LOGIN PASSWORD 'demo-test-secret-0001' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_collector
    LOGIN PASSWORD 'collector-test' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_disclosure_reader
    LOGIN PASSWORD 'disclosure-reader-test'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_market_writer
    LOGIN PASSWORD 'market-writer-test'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_market_operational_reader
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_market_research_reader
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_market_retention_admin
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_portfolio_writer
    LOGIN PASSWORD 'portfolio-writer-test'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_risk_writer
    LOGIN PASSWORD 'risk-writer-test'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_fill_writer
    LOGIN PASSWORD 'fill-writer-test'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_rag_writer
    LOGIN PASSWORD 'rag-writer-test'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_rag_admin
    LOGIN PASSWORD 'rag-admin-test'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_rag_query
    LOGIN PASSWORD 'rag-query-test'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_signal_writer
    LOGIN PASSWORD 'signal-writer-test'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_signal_scheduler
    LOGIN PASSWORD 'signal-scheduler-test'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_signal_admin
    LOGIN PASSWORD 'signal-admin-test'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE flyway
    LOGIN PASSWORD 'flyway-test'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;

-- Spring 통합 migration user와 실제 flyway role 모두 production의 bind-log 기본값을 재현한다.
ALTER ROLE decision SET log_parameter_max_length = 0;
ALTER ROLE decision SET log_parameter_max_length_on_error = 0;
ALTER ROLE decision_app SET log_parameter_max_length = 0;
ALTER ROLE decision_app SET log_parameter_max_length_on_error = 0;
ALTER ROLE decision_app SET statement_timeout = '2s';
ALTER ROLE decision_app SET lock_timeout = '500ms';
ALTER ROLE decision_app SET idle_in_transaction_session_timeout = '5s';
ALTER ROLE decision_worker SET log_parameter_max_length = 0;
ALTER ROLE decision_worker SET log_parameter_max_length_on_error = 0;
ALTER ROLE decision_worker SET statement_timeout = '60s';
ALTER ROLE decision_worker SET lock_timeout = '500ms';
ALTER ROLE decision_worker SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE decision_rag_writer SET log_parameter_max_length = 0;
ALTER ROLE decision_rag_writer SET log_parameter_max_length_on_error = 0;
ALTER ROLE decision_rag_writer SET statement_timeout = '2s';
ALTER ROLE decision_rag_writer SET lock_timeout = '500ms';
ALTER ROLE decision_rag_writer SET idle_in_transaction_session_timeout = '5s';
ALTER ROLE decision_rag_admin SET log_parameter_max_length = 0;
ALTER ROLE decision_rag_admin SET log_parameter_max_length_on_error = 0;
ALTER ROLE decision_rag_admin SET statement_timeout = '5s';
ALTER ROLE decision_rag_admin SET lock_timeout = '500ms';
ALTER ROLE decision_rag_admin SET idle_in_transaction_session_timeout = '5s';
ALTER ROLE decision_rag_query SET log_parameter_max_length = 0;
ALTER ROLE decision_rag_query SET log_parameter_max_length_on_error = 0;
ALTER ROLE decision_rag_query SET statement_timeout = '1500ms';
ALTER ROLE decision_rag_query SET lock_timeout = '250ms';
ALTER ROLE decision_rag_query SET idle_in_transaction_session_timeout = '5s';
ALTER ROLE decision_signal_writer SET statement_timeout = '60s';
ALTER ROLE decision_signal_writer SET lock_timeout = '500ms';
ALTER ROLE decision_signal_writer SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE decision_signal_scheduler SET statement_timeout = '5s';
ALTER ROLE decision_signal_scheduler SET lock_timeout = '500ms';
ALTER ROLE decision_signal_scheduler SET idle_in_transaction_session_timeout = '5s';
ALTER ROLE decision_signal_admin SET statement_timeout = '5s';
ALTER ROLE decision_signal_admin SET lock_timeout = '500ms';
ALTER ROLE decision_signal_admin SET idle_in_transaction_session_timeout = '5s';
ALTER ROLE flyway SET log_parameter_max_length = 0;
ALTER ROLE flyway SET log_parameter_max_length_on_error = 0;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO
    decision_app,
    decision_worker,
    decision_replay,
    decision_identity,
    decision_auth,
    decision_replay_authorizer,
    decision_demo,
    decision_collector,
    decision_disclosure_reader,
    decision_market_writer,
    decision_market_operational_reader,
    decision_market_research_reader,
    decision_market_retention_admin,
    decision_portfolio_writer,
    decision_risk_writer,
    decision_fill_writer,
    decision_rag_writer,
    decision_rag_admin,
    decision_rag_query,
    decision_signal_writer,
    decision_signal_scheduler,
    decision_signal_admin,
    flyway;
GRANT CREATE ON SCHEMA public TO flyway;
