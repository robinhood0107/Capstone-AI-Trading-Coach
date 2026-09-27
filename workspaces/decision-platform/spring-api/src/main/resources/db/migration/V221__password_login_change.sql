-- 이메일 비밀번호 계정이 현재 비밀번호를 확인한 뒤 새 비밀번호로 바꾼다.
-- demo-user/demo-admin 은 운영자가 서명한 자격증명 번들로만 회전하므로 여기서 다루지 않는다.
-- 바꾸면 이 계정의 기존 세션을 모두 닫고, 요청한 브라우저에만 새 세션을 준다.
CREATE FUNCTION public.change_password_login_actor_v1(
  p_user_id text, p_current_password text, p_new_password_hash text, p_ttl_seconds integer
) RETURNS TABLE(
  session_handle text, actor_user_id text, username text, actor_role text,
  actor_security_version bigint, expires_at timestamptz
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $change_password_login_actor_v1$
DECLARE
  actor public.users%ROWTYPE;
  stored_hash text;
  raw_session text;
  now_at timestamptz := statement_timestamp();
BEGIN
  IF session_user <> 'decision_auth'
     OR p_user_id IS NULL OR p_user_id !~ '^usr_[A-Za-z0-9_-]{4,96}$'
     OR octet_length(coalesce(p_current_password, '')) NOT BETWEEN 1 AND 72
     OR p_new_password_hash !~ '^\$2[aby]\$12\$[./A-Za-z0-9]{53}$'
     OR p_ttl_seconds IS NULL OR p_ttl_seconds NOT BETWEEN 1 AND 86400 THEN
    RAISE EXCEPTION 'password change denied' USING ERRCODE = '42501';
  END IF;

  SELECT * INTO actor FROM public.users WHERE user_id = p_user_id AND status = 'ACTIVE' FOR UPDATE;
  IF actor.user_id IS NULL THEN
    RETURN;
  END IF;
  SELECT identity.password_hash INTO stored_hash
  FROM public.password_login_identities identity
  WHERE identity.user_id = actor.user_id
  FOR UPDATE;
  IF stored_hash IS NULL THEN
    RAISE EXCEPTION 'password login not configured' USING ERRCODE = 'P0002';
  END IF;
  IF public.crypt(p_current_password, stored_hash) <> stored_hash THEN
    RETURN;
  END IF;

  UPDATE public.password_login_identities
  SET password_hash = p_new_password_hash, updated_at = now_at
  WHERE user_id = actor.user_id;
  UPDATE public.actor_auth_session session
  SET revoked_at = now_at
  WHERE session.actor_user_id = actor.user_id AND session.revoked_at IS NULL;
  INSERT INTO public.audit_logs(audit_log_id, user_id, actor_role, action, target_type, target_id, payload_json)
  VALUES (
    'aud_password_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex'),
    actor.user_id, actor.role, 'PASSWORD_LOGIN_CHANGED', 'USER', actor.user_id,
    pg_catalog.jsonb_build_object('credential', 'password', 'sessionsRevoked', true)
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
$change_password_login_actor_v1$;
ALTER FUNCTION public.change_password_login_actor_v1(text, text, text, integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.change_password_login_actor_v1(text, text, text, integer)
  FROM PUBLIC, decision_app, decision_identity, decision_worker, decision_replay;
GRANT EXECUTE ON FUNCTION public.change_password_login_actor_v1(text, text, text, integer)
  TO decision_auth;
