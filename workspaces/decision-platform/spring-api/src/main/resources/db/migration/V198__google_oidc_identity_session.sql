-- Public Google identities use the existing users and actor_auth_session trust root.
-- The SQL function is callable only by decision_auth after Spring has validated
-- Google's authorization-code response. It never accepts a role or user ID.
-- The operator bit comes only from a server-owned exact subject-hash allowlist;
-- changing that allowlist re-evaluates the actor role and revokes older sessions.
-- Versions 185..197 belong to archived, unmerged automation experiments; this
-- forward migration keeps their names free and never edits an applied migration.

ALTER TABLE public.users ALTER COLUMN password_hash DROP NOT NULL;
-- V1 users is owned by the database bootstrap role; only the definer role
-- receives INSERT for this path. decision_auth itself keeps zero table rights.
GRANT INSERT, UPDATE(role, security_version, updated_at) ON TABLE public.users TO flyway;

CREATE TABLE public.google_oidc_identities (
  issuer text NOT NULL CHECK (issuer = 'https://accounts.google.com'),
  subject text NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 255),
  user_id text NOT NULL UNIQUE REFERENCES public.users(user_id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  last_login_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (issuer, subject)
);
ALTER TABLE public.google_oidc_identities OWNER TO flyway;
ALTER TABLE public.google_oidc_identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.google_oidc_identities FORCE ROW LEVEL SECURITY;
CREATE POLICY google_oidc_identities_auth_v198 ON public.google_oidc_identities TO PUBLIC
  USING (current_user = 'flyway' AND session_user = 'decision_auth')
  WITH CHECK (current_user = 'flyway' AND session_user = 'decision_auth');
REVOKE ALL ON public.google_oidc_identities FROM PUBLIC, decision_app, decision_auth,
  decision_identity, decision_worker, decision_replay;

CREATE FUNCTION public.authenticate_google_oidc_actor_v1(
  p_issuer text, p_subject text, p_operator_subject boolean, p_ttl_seconds integer
) RETURNS TABLE(
  session_handle text, actor_user_id text, username text, actor_role text,
  actor_security_version bigint, expires_at timestamptz
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $authenticate_google_oidc_actor_v1$
DECLARE
  actor public.users%ROWTYPE;
  new_suffix text;
  raw_session text;
  expected_role text;
  previous_role text;
  now_at timestamptz := statement_timestamp();
BEGIN
  IF session_user <> 'decision_auth'
     OR p_issuer IS DISTINCT FROM 'https://accounts.google.com'
     OR char_length(coalesce(p_subject, '')) NOT BETWEEN 1 AND 255
     OR p_subject ~ '[[:cntrl:]]'
     OR p_operator_subject IS NULL
     OR p_ttl_seconds IS NULL OR p_ttl_seconds NOT BETWEEN 1 AND 86400 THEN
    RAISE EXCEPTION 'oidc authentication denied' USING ERRCODE = '42501';
  END IF;
  expected_role := CASE WHEN p_operator_subject THEN 'ADMIN' ELSE 'USER' END;

  -- Serializes the first login for an issuer+subject, including separate app replicas.
  -- A hash collision only serializes unrelated subjects; the exact key is rechecked.
  PERFORM pg_catalog.pg_advisory_xact_lock(198, pg_catalog.hashtext(p_issuer || ':' || p_subject));
  SELECT u.* INTO actor
  FROM public.google_oidc_identities identity
  JOIN public.users u ON u.user_id = identity.user_id
  WHERE identity.issuer = p_issuer AND identity.subject = p_subject
  FOR UPDATE OF identity, u;

  IF actor.user_id IS NULL THEN
    new_suffix := pg_catalog.encode(public.gen_random_bytes(16), 'hex');
    INSERT INTO public.users(user_id, username, role, password_hash, status)
    VALUES ('usr_' || new_suffix, 'oidc_' || new_suffix, expected_role, NULL, 'ACTIVE')
    RETURNING * INTO actor;
    INSERT INTO public.google_oidc_identities(issuer, subject, user_id)
    VALUES (p_issuer, p_subject, actor.user_id);
    INSERT INTO public.audit_logs(audit_log_id, user_id, actor_role, action, target_type, target_id, payload_json)
    VALUES (
      'aud_oidc_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex'),
      actor.user_id, actor.role, 'GOOGLE_OIDC_ACCOUNT_CREATED', 'USER', actor.user_id,
      pg_catalog.jsonb_build_object('role', actor.role)
    );
  ELSIF actor.status <> 'ACTIVE' OR actor.password_hash IS NOT NULL
        OR actor.username !~ '^oidc_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'oidc actor unavailable' USING ERRCODE = '42501';
  ELSE
    IF actor.role <> expected_role THEN
      previous_role := actor.role;
      UPDATE public.users
      SET role = expected_role, security_version = security_version + 1, updated_at = now_at
      WHERE user_id = actor.user_id
      RETURNING * INTO actor;
      UPDATE public.actor_auth_session session
      SET revoked_at = now_at
      WHERE session.actor_user_id = actor.user_id AND session.revoked_at IS NULL;
      INSERT INTO public.audit_logs(audit_log_id, user_id, actor_role, action, target_type, target_id, payload_json)
      VALUES (
        'aud_oidc_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex'),
        actor.user_id, actor.role, 'GOOGLE_OIDC_ROLE_CHANGED', 'USER', actor.user_id,
        pg_catalog.jsonb_build_object('previousRole', previous_role, 'newRole', expected_role)
      );
    END IF;
    UPDATE public.google_oidc_identities
    SET last_login_at = now_at
    WHERE issuer = p_issuer AND subject = p_subject;
  END IF;

  IF actor.role <> expected_role OR actor.security_version < 1 THEN
    RAISE EXCEPTION 'oidc actor unavailable' USING ERRCODE = '42501';
  END IF;
  raw_session := 'sid1_' || pg_catalog.encode(public.gen_random_bytes(32), 'hex');
  INSERT INTO public.actor_auth_session(
    session_hash, actor_user_id, actor_role, actor_security_version, issued_at, expires_at
  ) VALUES (
    'sha256:' || pg_catalog.encode(public.digest(raw_session, 'sha256'), 'hex'),
    actor.user_id, actor.role, actor.security_version, now_at,
    now_at + pg_catalog.make_interval(secs => p_ttl_seconds)
  );
  RETURN QUERY SELECT raw_session, actor.user_id, actor.username, actor.role,
    actor.security_version, now_at + pg_catalog.make_interval(secs => p_ttl_seconds);
END
$authenticate_google_oidc_actor_v1$;
ALTER FUNCTION public.authenticate_google_oidc_actor_v1(text, text, boolean, integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.authenticate_google_oidc_actor_v1(text, text, boolean, integer)
  FROM PUBLIC, decision_app, decision_identity, decision_worker, decision_replay;
GRANT EXECUTE ON FUNCTION public.authenticate_google_oidc_actor_v1(text, text, boolean, integer)
  TO decision_auth;

-- The API authenticates the Bearer JWT first, then passes its verified sid,
-- user ID, and security version. Logging out revokes the DB session immediately.
CREATE FUNCTION public.revoke_actor_auth_session_v1(
  p_session_handle text, p_actor_user_id text, p_security_version bigint
) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $revoke_actor_auth_session_v1$
DECLARE affected integer;
BEGIN
  IF session_user <> 'decision_auth'
     OR p_session_handle IS NULL OR p_session_handle !~ '^sid1_[0-9a-f]{64}$'
     OR p_actor_user_id IS NULL OR p_actor_user_id !~ '^usr_[A-Za-z0-9_-]{4,96}$'
     OR p_security_version IS NULL OR p_security_version < 1 THEN
    RAISE EXCEPTION 'session revocation denied' USING ERRCODE = '42501';
  END IF;
  UPDATE public.actor_auth_session session
  SET revoked_at = statement_timestamp()
  WHERE session.session_hash = 'sha256:' || pg_catalog.encode(public.digest(p_session_handle, 'sha256'), 'hex')
    AND session.actor_user_id = p_actor_user_id
    AND session.actor_security_version = p_security_version
    AND session.revoked_at IS NULL;
  GET DIAGNOSTICS affected = ROW_COUNT;
  RETURN affected = 1;
END
$revoke_actor_auth_session_v1$;
ALTER FUNCTION public.revoke_actor_auth_session_v1(text, text, bigint) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.revoke_actor_auth_session_v1(text, text, bigint)
  FROM PUBLIC, decision_app, decision_identity, decision_worker, decision_replay;
GRANT EXECUTE ON FUNCTION public.revoke_actor_auth_session_v1(text, text, bigint)
  TO decision_auth;
