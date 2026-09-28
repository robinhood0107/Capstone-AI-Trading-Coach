-- FULL 자동운용 bridge 는 소유자 비밀번호 없이(OIDC 사용자) 같은 container loopback + 공유 비밀로만
-- 들어온다. 그 뒤의 Decision·Brokerage·자격증명 서비스는 actor capability 를 발급할 때 DB 세션이
-- 있는 인증 컨텍스트를 요구해, FULL 세션은 첫 bridge 호출에서 항상 닫혔다. bridge 가 활성 소유자에게
-- 짧은 세션을 직접 연다. 로그인 함수와 같은 세션 표·해시 규칙을 쓰고, decision_auth 만 부른다.
CREATE OR REPLACE FUNCTION public.issue_automation_runtime_session_v1(p_user_id text, p_ttl_seconds integer)
RETURNS TABLE(
  session_handle text, actor_user_id text, username text, actor_role text,
  actor_security_version bigint, expires_at timestamptz
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $issue_automation_runtime_session_v1$
DECLARE
  actor public.users%ROWTYPE;
  raw_session text;
  now_at timestamptz := statement_timestamp();
BEGIN
  IF session_user <> 'decision_auth'
     OR p_user_id IS NULL OR p_user_id !~ '^usr_[A-Za-z0-9_-]{4,96}$'
     OR p_ttl_seconds IS NULL OR p_ttl_seconds NOT BETWEEN 1 AND 300 THEN
    RAISE EXCEPTION 'automation runtime session denied' USING ERRCODE = '42501';
  END IF;
  SELECT * INTO actor FROM public.users WHERE user_id = p_user_id AND status = 'ACTIVE' FOR SHARE;
  IF actor.user_id IS NULL OR actor.security_version < 1 THEN
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
$issue_automation_runtime_session_v1$;
ALTER FUNCTION public.issue_automation_runtime_session_v1(text, integer) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.issue_automation_runtime_session_v1(text, integer)
  FROM PUBLIC, decision_app, decision_identity, decision_worker, decision_replay;
GRANT EXECUTE ON FUNCTION public.issue_automation_runtime_session_v1(text, integer) TO decision_auth;
