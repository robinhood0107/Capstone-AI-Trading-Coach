-- 운영에서는 infra/init/02-application-roles.sh가 만드는 role을 Flyway보다 먼저 재현한다.
CREATE ROLE decision_app
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE decision_collector
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE flyway
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;

-- Spring 통합 migration user와 실제 flyway role 모두 production의 bind-log 기본값을 재현한다.
ALTER ROLE decision SET log_parameter_max_length = 0;
ALTER ROLE decision SET log_parameter_max_length_on_error = 0;
ALTER ROLE flyway SET log_parameter_max_length = 0;
ALTER ROLE flyway SET log_parameter_max_length_on_error = 0;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO decision_app, decision_collector, flyway;
