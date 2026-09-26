-- Password login is a separate credential from the built-in legacy demo hashes.
-- Provider identities can attach to one user per provider; the issuer+subject remains globally unique.

ALTER TABLE public.social_login_identities
  DROP CONSTRAINT social_login_identities_user_id_key;
ALTER TABLE public.social_login_identities
  ADD CONSTRAINT social_login_identities_user_provider_key UNIQUE (user_id, issuer);

CREATE TABLE public.password_login_identities (
  email_normalized text PRIMARY KEY,
  user_id text NOT NULL UNIQUE REFERENCES public.users(user_id) ON DELETE CASCADE,
  password_hash text NOT NULL CHECK (password_hash ~ '^\$2[aby]\$12\$[./A-Za-z0-9]{53}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (email_normalized = lower(email_normalized)),
  CHECK (email_normalized ~ '^[a-z0-9.!#$%&''*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}$')
);
ALTER TABLE public.password_login_identities OWNER TO flyway;
ALTER TABLE public.password_login_identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.password_login_identities FORCE ROW LEVEL SECURITY;
CREATE POLICY password_login_identities_auth_v212 ON public.password_login_identities TO PUBLIC
  USING (current_user = 'flyway' AND session_user = 'decision_auth')
  WITH CHECK (current_user = 'flyway' AND session_user = 'decision_auth');
REVOKE ALL ON public.password_login_identities
  FROM PUBLIC, decision_app, decision_auth, decision_identity, decision_worker, decision_replay;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.password_login_identities TO flyway;

-- A missing email still pays one BCrypt cost. The application creates this dummy hash at startup.
CREATE FUNCTION public.authenticate_password_login_actor_v1(
  p_email text, p_password text, p_dummy_hash text, p_ttl_seconds integer
) RETURNS TABLE(
  session_handle text, actor_user_id text, username text, actor_role text,
  actor_security_version bigint, expires_at timestamptz
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $authenticate_password_login_actor_v1$
DECLARE
  actor public.users%ROWTYPE;
  stored_hash text;
  candidate_hash text;
  matched boolean;
  raw_session text;
  now_at timestamptz := statement_timestamp();
BEGIN
  IF session_user <> 'decision_auth'
     OR p_email IS NULL OR p_email <> pg_catalog.lower(pg_catalog.btrim(p_email))
     OR p_email !~ '^[a-z0-9.!#$%&''*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}$'
     OR p_dummy_hash !~ '^\$2[aby]\$12\$[./A-Za-z0-9]{53}$'
     OR p_ttl_seconds IS NULL OR p_ttl_seconds NOT BETWEEN 1 AND 86400 THEN
    RAISE EXCEPTION 'password authentication denied' USING ERRCODE = '42501';
  END IF;

  SELECT users.* INTO actor
  FROM public.password_login_identities identity
  JOIN public.users users ON users.user_id = identity.user_id
  WHERE identity.email_normalized = p_email AND users.status = 'ACTIVE'
  FOR SHARE OF identity, users;
  IF actor.user_id IS NOT NULL THEN
    SELECT identity.password_hash INTO stored_hash
    FROM public.password_login_identities identity
    WHERE identity.user_id = actor.user_id;
  END IF;

  candidate_hash := coalesce(stored_hash, p_dummy_hash);
  matched := public.crypt(coalesce(p_password, ''), candidate_hash) = candidate_hash;
  IF actor.user_id IS NULL OR octet_length(coalesce(p_password, '')) NOT BETWEEN 1 AND 72 OR NOT matched THEN
    RETURN;
  END IF;

  DELETE FROM public.actor_auth_session expired
  WHERE expired.expires_at <= now_at OR expired.revoked_at IS NOT NULL;
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
$authenticate_password_login_actor_v1$;
ALTER FUNCTION public.authenticate_password_login_actor_v1(text, text, text, integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.authenticate_password_login_actor_v1(text, text, text, integer)
  FROM PUBLIC, decision_app, decision_identity, decision_worker, decision_replay;
GRANT EXECUTE ON FUNCTION public.authenticate_password_login_actor_v1(text, text, text, integer)
  TO decision_auth;

CREATE FUNCTION public.register_password_login_actor_v1(
  p_email text, p_password_hash text, p_ttl_seconds integer
) RETURNS TABLE(
  session_handle text, actor_user_id text, username text, actor_role text,
  actor_security_version bigint, expires_at timestamptz
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $register_password_login_actor_v1$
DECLARE
  actor public.users%ROWTYPE;
  new_suffix text;
  raw_session text;
  now_at timestamptz := statement_timestamp();
BEGIN
  IF session_user <> 'decision_auth'
     OR p_email IS NULL OR p_email <> pg_catalog.lower(pg_catalog.btrim(p_email))
     OR p_email !~ '^[a-z0-9.!#$%&''*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}$'
     OR p_password_hash !~ '^\$2[aby]\$12\$[./A-Za-z0-9]{53}$'
     OR p_ttl_seconds IS NULL OR p_ttl_seconds NOT BETWEEN 1 AND 86400 THEN
    RAISE EXCEPTION 'password account registration denied' USING ERRCODE = '42501';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(212, pg_catalog.hashtext(p_email));
  IF EXISTS (SELECT 1 FROM public.password_login_identities WHERE email_normalized = p_email) THEN
    RAISE EXCEPTION 'password account already exists' USING ERRCODE = '23505';
  END IF;

  new_suffix := pg_catalog.encode(public.gen_random_bytes(16), 'hex');
  INSERT INTO public.users(user_id, username, role, password_hash, status)
  VALUES ('usr_' || new_suffix, 'member_' || new_suffix, 'USER', NULL, 'ACTIVE')
  RETURNING * INTO actor;
  INSERT INTO public.password_login_identities(email_normalized, user_id, password_hash)
  VALUES (p_email, actor.user_id, p_password_hash);
  INSERT INTO public.audit_logs(audit_log_id, user_id, actor_role, action, target_type, target_id, payload_json)
  VALUES (
    'aud_password_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex'),
    actor.user_id, actor.role, 'PASSWORD_ACCOUNT_CREATED', 'USER', actor.user_id,
    pg_catalog.jsonb_build_object('credential', 'password')
  );

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
$register_password_login_actor_v1$;
ALTER FUNCTION public.register_password_login_actor_v1(text, text, integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.register_password_login_actor_v1(text, text, integer)
  FROM PUBLIC, decision_app, decision_identity, decision_worker, decision_replay;
GRANT EXECUTE ON FUNCTION public.register_password_login_actor_v1(text, text, integer)
  TO decision_auth;

CREATE FUNCTION public.add_password_login_actor_v1(
  p_user_id text, p_email text, p_password_hash text, p_ttl_seconds integer
) RETURNS TABLE(
  session_handle text, actor_user_id text, username text, actor_role text,
  actor_security_version bigint, expires_at timestamptz
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $add_password_login_actor_v1$
DECLARE
  actor public.users%ROWTYPE;
  raw_session text;
  now_at timestamptz := statement_timestamp();
BEGIN
  IF session_user <> 'decision_auth'
     OR p_user_id IS NULL OR p_user_id !~ '^usr_[A-Za-z0-9_-]{4,96}$'
     OR p_email IS NULL OR p_email <> pg_catalog.lower(pg_catalog.btrim(p_email))
     OR p_email !~ '^[a-z0-9.!#$%&''*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}$'
     OR p_password_hash !~ '^\$2[aby]\$12\$[./A-Za-z0-9]{53}$'
     OR p_ttl_seconds IS NULL OR p_ttl_seconds NOT BETWEEN 1 AND 86400 THEN
    RAISE EXCEPTION 'password account update denied' USING ERRCODE = '42501';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(212, pg_catalog.hashtext(p_email));
  SELECT * INTO actor FROM public.users WHERE user_id = p_user_id AND status = 'ACTIVE' FOR UPDATE;
  IF actor.user_id IS NULL OR EXISTS (
    SELECT 1 FROM public.password_login_identities WHERE user_id = p_user_id OR email_normalized = p_email
  ) THEN
    RAISE EXCEPTION 'password account update unavailable' USING ERRCODE = '23505';
  END IF;
  INSERT INTO public.password_login_identities(email_normalized, user_id, password_hash)
  VALUES (p_email, actor.user_id, p_password_hash);
  INSERT INTO public.audit_logs(audit_log_id, user_id, actor_role, action, target_type, target_id, payload_json)
  VALUES (
    'aud_password_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex'),
    actor.user_id, actor.role, 'PASSWORD_LOGIN_ADDED', 'USER', actor.user_id,
    pg_catalog.jsonb_build_object('credential', 'password')
  );
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
$add_password_login_actor_v1$;
ALTER FUNCTION public.add_password_login_actor_v1(text, text, text, integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.add_password_login_actor_v1(text, text, text, integer)
  FROM PUBLIC, decision_app, decision_identity, decision_worker, decision_replay;
GRANT EXECUTE ON FUNCTION public.add_password_login_actor_v1(text, text, text, integer)
  TO decision_auth;

CREATE FUNCTION public.read_account_auth_methods_v1(p_user_id text)
RETURNS TABLE(provider text, email_normalized text, linked_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $read_account_auth_methods_v1$
BEGIN
  IF session_user <> 'decision_auth' OR p_user_id IS NULL OR p_user_id !~ '^usr_[A-Za-z0-9_-]{4,96}$' THEN
    RAISE EXCEPTION 'account methods read denied' USING ERRCODE = '42501';
  END IF;
  RETURN QUERY
  SELECT 'password'::text, identity.email_normalized, identity.created_at
    FROM public.password_login_identities identity WHERE identity.user_id = p_user_id
  UNION ALL
  SELECT 'demo-password'::text, NULL::text, users.created_at
    FROM public.users users
   WHERE users.user_id = p_user_id AND users.user_id IN ('usr_demo_user', 'usr_demo_admin')
  UNION ALL
  SELECT CASE identity.issuer
           WHEN 'https://accounts.google.com' THEN 'google'
           WHEN 'https://kauth.kakao.com' THEN 'kakao'
         END::text,
         NULL::text, identity.created_at
    FROM public.social_login_identities identity WHERE identity.user_id = p_user_id
  ORDER BY 1;
END
$read_account_auth_methods_v1$;
ALTER FUNCTION public.read_account_auth_methods_v1(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.read_account_auth_methods_v1(text)
  FROM PUBLIC, decision_app, decision_identity, decision_worker, decision_replay;
GRANT EXECUTE ON FUNCTION public.read_account_auth_methods_v1(text) TO decision_auth;

CREATE FUNCTION public.unlink_social_login_actor_v1(p_user_id text, p_issuer text)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $unlink_social_login_actor_v1$
DECLARE
  actor public.users%ROWTYPE;
  removed integer;
BEGIN
  IF session_user <> 'decision_auth'
     OR p_user_id IS NULL OR p_user_id !~ '^usr_[A-Za-z0-9_-]{4,96}$'
     OR p_issuer NOT IN ('https://accounts.google.com', 'https://kauth.kakao.com') THEN
    RAISE EXCEPTION 'social identity unlink denied' USING ERRCODE = '42501';
  END IF;
  SELECT * INTO actor FROM public.users WHERE user_id = p_user_id AND status = 'ACTIVE' FOR UPDATE;
  IF actor.user_id IS NULL THEN
    RETURN false;
  END IF;
  IF p_user_id NOT IN ('usr_demo_user', 'usr_demo_admin')
     AND NOT EXISTS (SELECT 1 FROM public.password_login_identities WHERE user_id = p_user_id)
     AND (SELECT count(*) FROM public.social_login_identities WHERE user_id = p_user_id) <= 1 THEN
    RAISE EXCEPTION 'cannot unlink the last login method' USING ERRCODE = '23514';
  END IF;
  DELETE FROM public.social_login_identities WHERE user_id = p_user_id AND issuer = p_issuer;
  GET DIAGNOSTICS removed = ROW_COUNT;
  IF removed = 1 THEN
    INSERT INTO public.audit_logs(audit_log_id, user_id, actor_role, action, target_type, target_id, payload_json)
    VALUES (
      'aud_social_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex'),
      actor.user_id, actor.role, 'SOCIAL_LOGIN_IDENTITY_UNLINKED', 'USER', actor.user_id,
      pg_catalog.jsonb_build_object(
        'provider', CASE p_issuer WHEN 'https://accounts.google.com' THEN 'google' ELSE 'kakao' END
      )
    );
  END IF;
  RETURN removed = 1;
END
$unlink_social_login_actor_v1$;
ALTER FUNCTION public.unlink_social_login_actor_v1(text, text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.unlink_social_login_actor_v1(text, text)
  FROM PUBLIC, decision_app, decision_identity, decision_worker, decision_replay;
GRANT EXECUTE ON FUNCTION public.unlink_social_login_actor_v1(text, text) TO decision_auth;

DROP FUNCTION public.authenticate_social_login_actor_v1(text, text, boolean, integer);

CREATE FUNCTION public.authenticate_social_login_actor_v2(
  p_issuer text,
  p_subject text,
  p_google_admin_subject boolean,
  p_ttl_seconds integer
) RETURNS TABLE(
  session_handle text, actor_user_id text, username text, actor_role text,
  actor_security_version bigint, expires_at timestamptz
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $authenticate_social_login_actor_v2$
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
     OR p_ttl_seconds IS NULL OR p_ttl_seconds NOT BETWEEN 1 AND 86400
     THEN
    RAISE EXCEPTION 'social login denied' USING ERRCODE = '42501';
  END IF;
  expected_role := CASE WHEN p_google_admin_subject THEN 'ADMIN' ELSE 'USER' END;

  PERFORM pg_catalog.pg_advisory_xact_lock(212, pg_catalog.hashtext(p_issuer || ':' || p_subject));
  SELECT u.* INTO actor
  FROM public.social_login_identities identity
  JOIN public.users u ON u.user_id = identity.user_id
  WHERE identity.issuer = p_issuer AND identity.subject = p_subject
  FOR UPDATE OF identity, u;

  IF actor.user_id IS NOT NULL AND p_issuer = 'https://kauth.kakao.com' THEN
    expected_role := actor.role;
  END IF;

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
  ELSIF actor.status <> 'ACTIVE'
        OR NOT (
          actor.username ~ '^oidc_[0-9a-f]{32}$'
          OR EXISTS (SELECT 1 FROM public.password_login_identities credential WHERE credential.user_id = actor.user_id)
          OR (actor.user_id = 'usr_demo_user' AND actor.username = 'demo-user')
        ) THEN
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
$authenticate_social_login_actor_v2$;
ALTER FUNCTION public.authenticate_social_login_actor_v2(text, text, boolean, integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.authenticate_social_login_actor_v2(text, text, boolean, integer)
  FROM PUBLIC, decision_app, decision_identity, decision_worker, decision_replay;
GRANT EXECUTE ON FUNCTION public.authenticate_social_login_actor_v2(text, text, boolean, integer)
  TO decision_auth;

CREATE FUNCTION public.link_social_login_actor_v1(
  p_user_id text, p_issuer text, p_subject text, p_google_admin_subject boolean, p_ttl_seconds integer
) RETURNS TABLE(
  session_handle text, actor_user_id text, username text, actor_role text,
  actor_security_version bigint, expires_at timestamptz
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $link_social_login_actor_v1$
DECLARE
  actor public.users%ROWTYPE;
  existing_user_id text;
  existing_issuer text;
BEGIN
  IF session_user <> 'decision_auth'
     OR p_user_id IS NULL OR p_user_id !~ '^usr_[A-Za-z0-9_-]{4,96}$'
     OR p_issuer NOT IN ('https://accounts.google.com', 'https://kauth.kakao.com')
     OR p_subject IS NULL OR p_subject !~ '^[^[:cntrl:]]{1,255}$'
     OR (p_issuer = 'https://kauth.kakao.com' AND p_subject !~ '^[0-9]{1,32}$')
     OR p_google_admin_subject IS NULL
     OR (p_issuer = 'https://kauth.kakao.com' AND p_google_admin_subject)
     OR p_ttl_seconds IS NULL OR p_ttl_seconds NOT BETWEEN 1 AND 86400 THEN
    RAISE EXCEPTION 'social identity link denied' USING ERRCODE = '42501';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(212, pg_catalog.hashtext(p_issuer || ':' || p_subject));
  SELECT * INTO actor FROM public.users WHERE user_id = p_user_id AND status = 'ACTIVE' FOR UPDATE;
  -- A USER-level provider identity must never re-evaluate an ADMIN account down to USER.
  IF actor.user_id IS NULL OR (actor.role = 'ADMIN' AND NOT p_google_admin_subject) THEN
    RAISE EXCEPTION 'social identity link actor unavailable' USING ERRCODE = '42501';
  END IF;
  SELECT identity.user_id INTO existing_user_id
  FROM public.social_login_identities identity
  WHERE identity.issuer = p_issuer AND identity.subject = p_subject
  FOR UPDATE;
  IF existing_user_id IS NOT NULL AND existing_user_id <> p_user_id THEN
    RAISE EXCEPTION 'provider account belongs to another user' USING ERRCODE = '23505';
  END IF;
  SELECT identity.issuer INTO existing_issuer
  FROM public.social_login_identities identity
  WHERE identity.user_id = p_user_id AND identity.issuer = p_issuer
  FOR UPDATE;
  IF existing_issuer IS NOT NULL AND existing_user_id IS NULL THEN
    RAISE EXCEPTION 'provider already linked to this user' USING ERRCODE = '23505';
  END IF;
  IF existing_user_id IS NULL THEN
    INSERT INTO public.social_login_identities(issuer, subject, user_id)
    VALUES (p_issuer, p_subject, p_user_id);
    INSERT INTO public.audit_logs(audit_log_id, user_id, actor_role, action, target_type, target_id, payload_json)
    VALUES (
      'aud_social_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex'),
      actor.user_id, actor.role, 'SOCIAL_LOGIN_IDENTITY_LINKED', 'USER', actor.user_id,
      pg_catalog.jsonb_build_object(
        'provider', CASE p_issuer WHEN 'https://accounts.google.com' THEN 'google' ELSE 'kakao' END
      )
    );
  END IF;
  RETURN QUERY SELECT * FROM public.authenticate_social_login_actor_v2(
    p_issuer, p_subject, p_google_admin_subject, p_ttl_seconds
  );
END
$link_social_login_actor_v1$;
ALTER FUNCTION public.link_social_login_actor_v1(text, text, text, boolean, integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.link_social_login_actor_v1(text, text, text, boolean, integer)
  FROM PUBLIC, decision_app, decision_identity, decision_worker, decision_replay;
GRANT EXECUTE ON FUNCTION public.link_social_login_actor_v1(text, text, text, boolean, integer)
  TO decision_auth;

-- Keep the prior signature for an older API process during an in-place image rollout.
CREATE FUNCTION public.authenticate_social_login_actor_v1(
  p_issuer text, p_subject text, p_google_admin_subject boolean, p_ttl_seconds integer
) RETURNS TABLE(
  session_handle text, actor_user_id text, username text, actor_role text,
  actor_security_version bigint, expires_at timestamptz
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $authenticate_social_login_actor_v1$
BEGIN
  RETURN QUERY SELECT * FROM public.authenticate_social_login_actor_v2(
    p_issuer, p_subject, p_google_admin_subject, p_ttl_seconds
  );
END
$authenticate_social_login_actor_v1$;
ALTER FUNCTION public.authenticate_social_login_actor_v1(text, text, boolean, integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.authenticate_social_login_actor_v1(text, text, boolean, integer)
  FROM PUBLIC, decision_app, decision_identity, decision_worker, decision_replay;
GRANT EXECUTE ON FUNCTION public.authenticate_social_login_actor_v1(text, text, boolean, integer)
  TO decision_auth;
