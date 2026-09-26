-- The fixed operator account is demo-user alone. It becomes ADMIN, demo-admin is retired,
-- and a provider login never re-evaluates a role downward: roles are changed by an ADMIN.

-- 1. Only demo-user is a fixed password account now.
CREATE OR REPLACE FUNCTION public.read_demo_credentials()
RETURNS TABLE(user_id text, username text, password_hash text, role text, status text, security_version bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $read_demo_credentials$
BEGIN
  IF session_user <> 'decision_auth' AND NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname=session_user AND rolsuper
  ) THEN RAISE EXCEPTION 'credential reader role denied' USING ERRCODE='42501'; END IF;
  RETURN QUERY SELECT item.user_id,item.username,item.password_hash,item.role,item.status,item.security_version
  FROM public.users item WHERE item.user_id = 'usr_demo_user';
END
$read_demo_credentials$;

CREATE OR REPLACE FUNCTION public.authenticate_demo_actor_session_v1(
  p_username text,p_password text,p_ttl_seconds integer
) RETURNS TABLE(
  session_handle text,actor_user_id text,username text,actor_role text,
  actor_security_version bigint,expires_at timestamptz
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $authenticate_demo_actor_session_v1$
DECLARE demo_user public.users%ROWTYPE;
DECLARE user_matches boolean;
DECLARE decoy_hash text;
DECLARE raw_session text;
DECLARE now_at timestamptz:=statement_timestamp();
DECLARE password_bytes integer:=octet_length(coalesce(p_password,''));
BEGIN
  IF session_user<>'decision_auth'
     OR char_length(coalesce(p_username,'')) NOT BETWEEN 1 AND 128
     OR p_ttl_seconds NOT BETWEEN 1 AND 86400 THEN
    RAISE EXCEPTION 'demo authentication denied' USING ERRCODE='42501';
  END IF;

  SELECT * INTO demo_user FROM public.users WHERE user_id='usr_demo_user' FOR SHARE;
  IF demo_user.user_id IS NULL
     OR demo_user.username<>'demo-user' OR demo_user.role NOT IN ('USER','ADMIN')
     OR demo_user.status<>'ACTIVE' OR demo_user.security_version<=0
     OR demo_user.password_hash!~'^\$2[aby]\$12\$[./A-Za-z0-9]{53}$' THEN
    RAISE EXCEPTION 'demo authentication denied' USING ERRCODE='42501';
  END IF;

  -- Every outcome pays the same two BCrypt costs, so unknown names and overlong
  -- input do not become a cheaper enumeration path.
  user_matches:=public.crypt(coalesce(p_password,''),demo_user.password_hash)=demo_user.password_hash;
  -- Pay a second strength-12 cost against a fresh salt; its value is never compared.
  decoy_hash:=public.crypt(coalesce(p_password,''),public.gen_salt('bf',12));
  IF password_bytes NOT BETWEEN 1 AND 72 OR p_username<>'demo-user' OR NOT user_matches THEN
    RETURN;
  END IF;

  DELETE FROM public.actor_auth_session expired
  WHERE expired.expires_at<=now_at OR expired.revoked_at IS NOT NULL;
  raw_session:='sid1_'||encode(public.gen_random_bytes(32),'hex');
  INSERT INTO public.actor_auth_session(
    session_hash,actor_user_id,actor_role,actor_security_version,issued_at,expires_at
  ) VALUES (
    'sha256:'||encode(public.digest(raw_session,'sha256'),'hex'),
    demo_user.user_id,demo_user.role,demo_user.security_version,
    now_at,now_at+make_interval(secs=>p_ttl_seconds)
  );
  RETURN QUERY SELECT raw_session,demo_user.user_id,demo_user.username,
    demo_user.role,demo_user.security_version,now_at+make_interval(secs=>p_ttl_seconds);
END
$authenticate_demo_actor_session_v1$;

-- 2. demo-user becomes the operator ADMIN. The role change revokes its open sessions.
DO $v213_roles$
DECLARE now_at timestamptz := statement_timestamp();
BEGIN
  UPDATE public.users
     SET role='ADMIN', security_version=security_version+1, updated_at=now_at
   WHERE user_id='usr_demo_user' AND role<>'ADMIN';
  IF FOUND THEN
    UPDATE public.actor_auth_session SET revoked_at=now_at
     WHERE actor_user_id='usr_demo_user' AND revoked_at IS NULL;
    INSERT INTO public.audit_logs(audit_log_id,user_id,actor_role,action,target_type,target_id,payload_json)
    VALUES ('aud_v213_'||encode(public.gen_random_bytes(16),'hex'),'usr_demo_user','ADMIN',
            'ACCOUNT_ROLE_CHANGED','USER','usr_demo_user',
            jsonb_build_object('previousRole','USER','newRole','ADMIN','reason','single operator account'));
  END IF;

END
$v213_roles$;

-- The second fixed account is consolidated into the operator account. Every row it owned or
-- touched is reassigned to demo-user; a per-owner row that demo-user already has its own copy of
-- (unique per user) is dropped. The account row and its textual references are then removed.
DO $v213_consolidate$
DECLARE ref record;
DECLARE forced boolean;
DECLARE pass integer;
DECLARE removed bigint;
DECLARE has_rows boolean;
DECLARE paused name[];
DECLARE trigger_name name;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM public.users WHERE user_id='usr_demo_admin') THEN
    RETURN;
  END IF;
  DELETE FROM public.actor_auth_session WHERE actor_user_id='usr_demo_admin';
  FOR pass IN 1..3 LOOP
    FOR ref IN
      SELECT c.conrelid::regclass AS tbl, a.attname AS col
      FROM pg_constraint c
      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
      WHERE c.contype = 'f' AND c.confrelid = 'public.users'::regclass AND array_length(c.conkey, 1) = 1
    LOOP
      SELECT relforcerowsecurity INTO forced FROM pg_class WHERE oid = ref.tbl;
      IF forced THEN EXECUTE format('ALTER TABLE %s NO FORCE ROW LEVEL SECURITY', ref.tbl); END IF;
      EXECUTE format('SELECT EXISTS (SELECT 1 FROM %s WHERE %I = %L)', ref.tbl, ref.col, 'usr_demo_admin') INTO has_rows;
      IF NOT has_rows THEN
        IF forced THEN EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', ref.tbl); END IF;
        CONTINUE;
      END IF;
      -- Append-only evidence tables reject updates by trigger. This one-time consolidation pauses
      -- only the user triggers that are currently enabled, then restores exactly those.
      SELECT coalesce(array_agg(tgname), ARRAY[]::name[]) INTO paused
        FROM pg_trigger WHERE tgrelid = ref.tbl AND NOT tgisinternal AND tgenabled <> 'D';
      FOREACH trigger_name IN ARRAY paused LOOP
        EXECUTE format('ALTER TABLE %s DISABLE TRIGGER %I', ref.tbl, trigger_name);
      END LOOP;
      BEGIN
        EXECUTE format('UPDATE %s SET %I = %L WHERE %I = %L',
                       ref.tbl, ref.col, 'usr_demo_user', ref.col, 'usr_demo_admin');
      EXCEPTION WHEN unique_violation THEN
        BEGIN
          EXECUTE format('DELETE FROM %s WHERE %I = %L', ref.tbl, ref.col, 'usr_demo_admin');
        EXCEPTION WHEN foreign_key_violation THEN
          NULL; -- a child row still points at it; a later pass handles the child first
        END;
      END;
      FOREACH trigger_name IN ARRAY paused LOOP
        EXECUTE format('ALTER TABLE %s ENABLE TRIGGER %I', ref.tbl, trigger_name);
      END LOOP;
      IF forced THEN EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', ref.tbl); END IF;
    END LOOP;
  END LOOP;

  UPDATE public.audit_logs SET target_id='usr_demo_user' WHERE target_id='usr_demo_admin';
  UPDATE public.audit_logs
     SET payload_json = replace(replace(payload_json::text, 'usr_demo_admin', 'usr_demo_user'), 'demo-admin', 'demo-user')::jsonb
   WHERE payload_json::text LIKE '%usr_demo_admin%' OR payload_json::text LIKE '%demo-admin%';

  DELETE FROM public.users WHERE user_id='usr_demo_admin';
  GET DIAGNOSTICS removed = ROW_COUNT;
  IF removed <> 1 THEN
    RAISE EXCEPTION 'fixed account consolidation did not complete' USING ERRCODE = '55000';
  END IF;
  INSERT INTO public.audit_logs(audit_log_id,user_id,actor_role,action,target_type,target_id,payload_json)
  VALUES ('aud_v213_'||encode(public.gen_random_bytes(16),'hex'),'usr_demo_user','ADMIN',
          'OPERATOR_ACCOUNT_CONSOLIDATED','USER','usr_demo_user',
          jsonb_build_object('reason','single operator account'));
END
$v213_consolidate$;

-- 3. Provider identities: only demo-user keeps the fixed password method.
CREATE OR REPLACE FUNCTION public.read_account_auth_methods_v1(p_user_id text)
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
    FROM public.users users WHERE users.user_id = p_user_id AND users.user_id = 'usr_demo_user'
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

CREATE OR REPLACE FUNCTION public.unlink_social_login_actor_v1(p_user_id text, p_issuer text)
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
  IF p_user_id <> 'usr_demo_user'
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

-- 4. A provider login keeps the stored role. The operator Google subject can only grant ADMIN.
CREATE OR REPLACE FUNCTION public.authenticate_social_login_actor_v2(
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

  PERFORM pg_catalog.pg_advisory_xact_lock(212, pg_catalog.hashtext(p_issuer || ':' || p_subject));
  SELECT u.* INTO actor
  FROM public.social_login_identities identity
  JOIN public.users u ON u.user_id = identity.user_id
  WHERE identity.issuer = p_issuer AND identity.subject = p_subject
  FOR UPDATE OF identity, u;

  IF actor.user_id IS NULL THEN
    new_suffix := pg_catalog.encode(public.gen_random_bytes(16), 'hex');
    INSERT INTO public.users(user_id, username, role, password_hash, status)
    VALUES ('usr_' || new_suffix, 'oidc_' || new_suffix,
            CASE WHEN p_google_admin_subject THEN 'ADMIN' ELSE 'USER' END, NULL, 'ACTIVE')
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
    IF p_google_admin_subject AND actor.role <> 'ADMIN' THEN
      UPDATE public.users
      SET role = 'ADMIN', security_version = security_version + 1, updated_at = now_at
      WHERE user_id = actor.user_id
      RETURNING * INTO actor;
      UPDATE public.actor_auth_session session
      SET revoked_at = now_at
      WHERE session.actor_user_id = actor.user_id AND session.revoked_at IS NULL;
      INSERT INTO public.audit_logs(audit_log_id, user_id, actor_role, action, target_type, target_id, payload_json)
      VALUES (
        'aud_social_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex'),
        actor.user_id, actor.role, 'SOCIAL_LOGIN_ROLE_CHANGED', 'USER', actor.user_id,
        pg_catalog.jsonb_build_object('previousRole', 'USER', 'newRole', 'ADMIN')
      );
    END IF;
    UPDATE public.social_login_identities
    SET last_login_at = now_at
    WHERE issuer = p_issuer AND subject = p_subject;
  END IF;

  IF actor.role NOT IN ('USER', 'ADMIN') OR actor.security_version < 1 THEN
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

-- 5. Linking no longer needs a role guard: a login can no longer demote the account.
CREATE OR REPLACE FUNCTION public.link_social_login_actor_v1(
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
  IF actor.user_id IS NULL THEN
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
