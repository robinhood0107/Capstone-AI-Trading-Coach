-- Preserve the existing Google identity rows while making the identity store
-- explicitly provider-neutral. Google and Kakao accounts remain distinct by
-- exact issuer+subject; email is never an account-linking key.
ALTER TABLE public.google_oidc_identities RENAME TO social_login_identities;
ALTER TABLE public.social_login_identities
  RENAME CONSTRAINT google_oidc_identities_pkey TO social_login_identities_pkey;
ALTER TABLE public.social_login_identities
  RENAME CONSTRAINT google_oidc_identities_user_id_key TO social_login_identities_user_id_key;
ALTER TABLE public.social_login_identities
  DROP CONSTRAINT google_oidc_identities_issuer_check;
ALTER TABLE public.social_login_identities
  ADD CONSTRAINT social_login_identities_issuer_check
  CHECK (issuer IN ('https://accounts.google.com', 'https://kauth.kakao.com'));
ALTER POLICY google_oidc_identities_auth_v198 ON public.social_login_identities
  RENAME TO social_login_identities_auth_v211;
COMMENT ON TABLE public.social_login_identities IS
  'Verified Google OIDC and Kakao OAuth identities keyed by exact issuer and subject; never linked by email.';

DROP FUNCTION public.authenticate_google_oidc_actor_v1(text, text, boolean, integer);

CREATE FUNCTION public.authenticate_social_login_actor_v1(
  p_issuer text, p_subject text, p_google_admin_subject boolean, p_ttl_seconds integer
) RETURNS TABLE(
  session_handle text, actor_user_id text, username text, actor_role text,
  actor_security_version bigint, expires_at timestamptz
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $authenticate_social_login_actor_v1$
DECLARE
  actor public.users%ROWTYPE;
  new_suffix text;
  raw_session text;
  expected_role text;
  previous_role text;
  now_at timestamptz := statement_timestamp();
BEGIN
  IF session_user <> 'decision_auth'
     OR p_issuer IS NULL
     OR p_issuer NOT IN ('https://accounts.google.com', 'https://kauth.kakao.com')
     OR char_length(coalesce(p_subject, '')) NOT BETWEEN 1 AND 255
     OR p_subject ~ '[[:cntrl:]]'
     OR (p_issuer = 'https://kauth.kakao.com' AND p_subject !~ '^[0-9]{1,32}$')
     OR p_google_admin_subject IS NULL
     OR (p_issuer = 'https://kauth.kakao.com' AND p_google_admin_subject)
     OR p_ttl_seconds IS NULL OR p_ttl_seconds NOT BETWEEN 1 AND 86400 THEN
    RAISE EXCEPTION 'social login denied' USING ERRCODE = '42501';
  END IF;
  expected_role := CASE WHEN p_google_admin_subject THEN 'ADMIN' ELSE 'USER' END;

  -- Serialize first login by the exact provider identity, including app replicas.
  PERFORM pg_catalog.pg_advisory_xact_lock(211, pg_catalog.hashtext(p_issuer || ':' || p_subject));
  SELECT u.* INTO actor
  FROM public.social_login_identities identity
  JOIN public.users u ON u.user_id = identity.user_id
  WHERE identity.issuer = p_issuer AND identity.subject = p_subject
  FOR UPDATE OF identity, u;

  IF actor.user_id IS NULL THEN
    new_suffix := pg_catalog.encode(public.gen_random_bytes(16), 'hex');
    INSERT INTO public.users(user_id, username, role, password_hash, status)
    VALUES ('usr_' || new_suffix, 'oidc_' || new_suffix, expected_role, NULL, 'ACTIVE')
    RETURNING * INTO actor;
    INSERT INTO public.social_login_identities(issuer, subject, user_id)
    VALUES (p_issuer, p_subject, actor.user_id);
    INSERT INTO public.audit_logs(audit_log_id, user_id, actor_role, action, target_type, target_id, payload_json)
    VALUES (
      'aud_social_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex'),
      actor.user_id, actor.role, 'SOCIAL_LOGIN_ACCOUNT_CREATED', 'USER', actor.user_id,
      pg_catalog.jsonb_build_object('role', actor.role)
    );
  ELSIF actor.status <> 'ACTIVE' OR actor.password_hash IS NOT NULL
        OR actor.username !~ '^oidc_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'social login actor unavailable' USING ERRCODE = '42501';
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
        'aud_social_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex'),
        actor.user_id, actor.role, 'SOCIAL_LOGIN_ROLE_CHANGED', 'USER', actor.user_id,
        pg_catalog.jsonb_build_object('previousRole', previous_role, 'newRole', expected_role)
      );
    END IF;
    UPDATE public.social_login_identities
    SET last_login_at = now_at
    WHERE issuer = p_issuer AND subject = p_subject;
  END IF;

  IF actor.role <> expected_role OR actor.security_version < 1 THEN
    RAISE EXCEPTION 'social login actor unavailable' USING ERRCODE = '42501';
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
$authenticate_social_login_actor_v1$;
ALTER FUNCTION public.authenticate_social_login_actor_v1(text, text, boolean, integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.authenticate_social_login_actor_v1(text, text, boolean, integer)
  FROM PUBLIC, decision_app, decision_identity, decision_worker, decision_replay;
GRANT EXECUTE ON FUNCTION public.authenticate_social_login_actor_v1(text, text, boolean, integer)
  TO decision_auth;
