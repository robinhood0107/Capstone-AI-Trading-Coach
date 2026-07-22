#!/usr/bin/env bash
set -Eeuo pipefail

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_APP_PASSWORD:?POSTGRES_APP_PASSWORD is required}"
: "${POSTGRES_MIGRATION_PASSWORD:?POSTGRES_MIGRATION_PASSWORD is required}"
: "${POSTGRES_COLLECTOR_PASSWORD:?POSTGRES_COLLECTOR_PASSWORD is required}"

# psql argv나 shell-expanded SQL에 password를 넣지 않고 process environment에서 안전하게 인용한다.
export PGPASSWORD="${POSTGRES_PASSWORD:-}"
psql -v ON_ERROR_STOP=1 --no-password --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv database_name POSTGRES_DB
\getenv app_password POSTGRES_APP_PASSWORD
\getenv migration_password POSTGRES_MIGRATION_PASSWORD
\getenv collector_password POSTGRES_COLLECTOR_PASSWORD

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

REVOKE ALL ON DATABASE :"database_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database_name" TO decision_app, decision_collector, flyway;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO decision_app, decision_collector, flyway;
GRANT CREATE ON SCHEMA public TO flyway;

-- S1.2c의 Spring runtime은 DB write controller가 없다. 쓰기 권한은 해당 기능 migration이
-- 필요한 application table에만 명시적으로 추가하고, bootstrap에서 미리 전체 DML을 주지 않는다.
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_app;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_app;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_collector;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_collector;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO decision_app;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM decision_app;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    GRANT SELECT ON TABLES TO decision_app;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM decision_app;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    REVOKE ALL PRIVILEGES ON TABLES FROM decision_collector;
ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
    REVOKE ALL PRIVILEGES ON SEQUENCES FROM decision_collector;

DO $block$
BEGIN
    IF to_regclass('public.flyway_schema_history') IS NOT NULL THEN
        -- 기존 volume에 role bootstrap을 재적용해도 runtime이 migration 이력을 변조하지 못한다.
        REVOKE ALL PRIVILEGES ON TABLE public.flyway_schema_history FROM decision_app;
        REVOKE ALL PRIVILEGES ON TABLE public.flyway_schema_history FROM decision_collector;
    END IF;
END
$block$;
SQL
