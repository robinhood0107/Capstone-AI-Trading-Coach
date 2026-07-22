-- S2.1 owner authorization 전에 JWT subject와 DB users identity를 하나의 trust root로 맞춘다.
ALTER TABLE users
    ADD COLUMN security_version bigint NOT NULL DEFAULT 1,
    ADD CONSTRAINT users_security_version_positive_check CHECK (security_version > 0);

DO $s21_actor_trust$
DECLARE
    demo_user_hash text := convert_from(decode('${demoUserPasswordHashBase64}', 'base64'), 'UTF8');
    demo_admin_hash text := convert_from(decode('${demoAdminPasswordHashBase64}', 'base64'), 'UTF8');
BEGIN
    IF length(demo_user_hash) <> 60
       OR substring(demo_user_hash FROM 1 FOR 7) NOT IN ('$2a$12$', '$2b$12$', '$2y$12$')
       OR substring(demo_user_hash FROM 8) !~ '^[./A-Za-z0-9]{53}$' THEN
        RAISE EXCEPTION 'DEMO_USER_PASSWORD_HASH must be a BCrypt strength-12 value';
    END IF;
    IF length(demo_admin_hash) <> 60
       OR substring(demo_admin_hash FROM 1 FOR 7) NOT IN ('$2a$12$', '$2b$12$', '$2y$12$')
       OR substring(demo_admin_hash FROM 8) !~ '^[./A-Za-z0-9]{53}$' THEN
        RAISE EXCEPTION 'DEMO_ADMIN_PASSWORD_HASH must be a BCrypt strength-12 value';
    END IF;
    IF demo_user_hash = demo_admin_hash THEN
        RAISE EXCEPTION 'demo user and admin password hashes must differ';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM users
        WHERE (user_id = 'usr_demo_user' OR username = 'demo-user')
          AND NOT (
              user_id = 'usr_demo_user'
              AND username = 'demo-user'
              AND role = 'USER'
              AND status = 'ACTIVE'
              AND security_version = 1
              AND password_hash = demo_user_hash
          )
    ) THEN
        RAISE EXCEPTION 'demo user identity conflicts with the approved S2.1 trust root';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM users
        WHERE (user_id = 'usr_demo_admin' OR username = 'demo-admin')
          AND NOT (
              user_id = 'usr_demo_admin'
              AND username = 'demo-admin'
              AND role = 'ADMIN'
              AND status = 'ACTIVE'
              AND security_version = 1
              AND password_hash = demo_admin_hash
          )
    ) THEN
        RAISE EXCEPTION 'demo admin identity conflicts with the approved S2.1 trust root';
    END IF;

    INSERT INTO users (user_id, username, role, password_hash, status, security_version)
    SELECT 'usr_demo_user', 'demo-user', 'USER', demo_user_hash, 'ACTIVE', 1
    WHERE NOT EXISTS (
        SELECT 1 FROM users WHERE user_id = 'usr_demo_user' OR username = 'demo-user'
    );

    INSERT INTO users (user_id, username, role, password_hash, status, security_version)
    SELECT 'usr_demo_admin', 'demo-admin', 'ADMIN', demo_admin_hash, 'ACTIVE', 1
    WHERE NOT EXISTS (
        SELECT 1 FROM users WHERE user_id = 'usr_demo_admin' OR username = 'demo-admin'
    );
END
$s21_actor_trust$;

DO $s21_actor_privileges$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
        -- runtime은 인증 재검증용 SELECT만 가지며 credential/status/version 변경은 operator role에 남긴다.
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE users FROM decision_app;
        GRANT SELECT ON TABLE users TO decision_app;
    END IF;
END
$s21_actor_privileges$;
