-- S1.2c runtime은 read-only DB role로 시작한다. 후속 write 기능은 필요한 table·operation만
-- 별도 migration으로 부여해 audit/version/event 불변식과 Flyway history를 보호한다.
DO $block$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_app;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_app;
        GRANT SELECT ON ALL TABLES IN SCHEMA public TO decision_app;
        ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
            REVOKE ALL PRIVILEGES ON TABLES FROM decision_app;
        ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
            GRANT SELECT ON TABLES TO decision_app;
        ALTER DEFAULT PRIVILEGES FOR ROLE flyway IN SCHEMA public
            REVOKE ALL PRIVILEGES ON SEQUENCES FROM decision_app;
        REVOKE ALL PRIVILEGES ON TABLE public.flyway_schema_history FROM decision_app;
    END IF;
END
$block$;
