-- 운영에서는 infra/init/02-application-roles.sh가 만드는 role을 Flyway보다 먼저 재현한다.
-- 실제 non-superuser flyway로 V2를 재생할 수 있도록 extension만 database owner가 선설치한다.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE ROLE decision_app
    LOGIN PASSWORD 'app-test' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_collector
    LOGIN PASSWORD 'collector-test' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_disclosure_reader
    LOGIN PASSWORD 'disclosure-reader-test'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_market_writer
    LOGIN PASSWORD 'market-writer-test'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_portfolio_writer
    LOGIN PASSWORD 'portfolio-writer-test'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_risk_writer
    LOGIN PASSWORD 'risk-writer-test'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_fill_writer
    LOGIN PASSWORD 'fill-writer-test'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE flyway
    LOGIN PASSWORD 'flyway-test'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;

-- Spring 통합 migration user와 실제 flyway role 모두 production의 bind-log 기본값을 재현한다.
ALTER ROLE decision SET log_parameter_max_length = 0;
ALTER ROLE decision SET log_parameter_max_length_on_error = 0;
ALTER ROLE decision_app SET log_parameter_max_length = 0;
ALTER ROLE decision_app SET log_parameter_max_length_on_error = 0;
ALTER ROLE flyway SET log_parameter_max_length = 0;
ALTER ROLE flyway SET log_parameter_max_length_on_error = 0;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO
    decision_app,
    decision_collector,
    decision_disclosure_reader,
    decision_market_writer,
    decision_portfolio_writer,
    decision_risk_writer,
    decision_fill_writer,
    flyway;
GRANT CREATE ON SCHEMA public TO flyway;
