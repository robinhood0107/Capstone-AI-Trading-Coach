-- FULL browser persistence: the cookie contains an opaque random handle, never an access JWT.
-- Only decision_auth can invoke the definer functions. Password rotation invalidates a handle
-- because the stored security version must still match the active user.
CREATE TABLE public.full_browser_refresh_sessions (
  token_hash text PRIMARY KEY CHECK (token_hash ~ '^sha256:[0-9a-f]{64}$'),
  actor_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
  actor_security_version bigint NOT NULL CHECK (actor_security_version > 0),
  created_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  CHECK (expires_at > created_at)
);
ALTER TABLE public.full_browser_refresh_sessions OWNER TO flyway;
CREATE INDEX full_browser_refresh_sessions_expiry_idx
  ON public.full_browser_refresh_sessions(expires_at) WHERE revoked_at IS NULL;
REVOKE ALL ON TABLE public.full_browser_refresh_sessions
  FROM PUBLIC, decision_app, decision_auth, decision_identity, decision_worker, decision_replay;

CREATE FUNCTION public.issue_full_browser_refresh_session_v1(p_access_session_handle text)
RETURNS text LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $issue_full_browser_refresh_session_v1$
DECLARE actor record;
DECLARE raw_handle text;
BEGIN
  IF session_user <> 'decision_auth' OR p_access_session_handle !~ '^sid1_[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'browser refresh issue denied' USING ERRCODE='42501';
  END IF;
  SELECT * INTO actor FROM public.read_actor_auth_session_v1(p_access_session_handle);
  IF NOT FOUND THEN RAISE EXCEPTION 'browser refresh issue denied' USING ERRCODE='42501'; END IF;
  raw_handle:='rfs1_'||pg_catalog.encode(public.gen_random_bytes(32),'hex');
  INSERT INTO public.full_browser_refresh_sessions
    (token_hash,actor_user_id,actor_security_version,created_at,expires_at)
  VALUES ('sha256:'||pg_catalog.encode(public.digest(raw_handle,'sha256'),'hex'),
          actor.actor_user_id,actor.actor_security_version,statement_timestamp(),
          statement_timestamp()+interval '30 days');
  RETURN raw_handle;
END
$issue_full_browser_refresh_session_v1$;

CREATE FUNCTION public.resume_full_browser_refresh_session_v1(p_refresh_handle text,p_access_ttl_seconds integer)
RETURNS TABLE(session_handle text,actor_user_id text,username text,actor_role text,
              actor_security_version bigint,expires_at timestamptz)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $resume_full_browser_refresh_session_v1$
DECLARE stored public.full_browser_refresh_sessions%ROWTYPE;
DECLARE actor public.users%ROWTYPE;
DECLARE new_session text;
DECLARE now_at timestamptz:=statement_timestamp();
BEGIN
  IF session_user <> 'decision_auth' OR p_refresh_handle !~ '^rfs1_[0-9a-f]{64}$'
     OR p_access_ttl_seconds NOT BETWEEN 1 AND 86400 THEN
    RAISE EXCEPTION 'browser refresh denied' USING ERRCODE='42501';
  END IF;
  SELECT * INTO stored FROM public.full_browser_refresh_sessions item
  WHERE item.token_hash='sha256:'||pg_catalog.encode(public.digest(p_refresh_handle,'sha256'),'hex')
    AND item.revoked_at IS NULL AND item.expires_at>now_at FOR UPDATE;
  IF NOT FOUND THEN RETURN; END IF;
  SELECT * INTO actor FROM public.users item WHERE item.user_id=stored.actor_user_id
    AND item.status='ACTIVE' AND item.security_version=stored.actor_security_version FOR SHARE;
  IF NOT FOUND THEN RETURN; END IF;
  UPDATE public.full_browser_refresh_sessions SET expires_at=now_at+interval '30 days'
  WHERE token_hash=stored.token_hash;
  new_session:='sid1_'||pg_catalog.encode(public.gen_random_bytes(32),'hex');
  INSERT INTO public.actor_auth_session
    (session_hash,actor_user_id,actor_role,actor_security_version,issued_at,expires_at)
  VALUES ('sha256:'||pg_catalog.encode(public.digest(new_session,'sha256'),'hex'),
          actor.user_id,actor.role,actor.security_version,now_at,
          now_at+pg_catalog.make_interval(secs=>p_access_ttl_seconds));
  RETURN QUERY SELECT new_session,actor.user_id,actor.username,actor.role,
    actor.security_version,now_at+pg_catalog.make_interval(secs=>p_access_ttl_seconds);
END
$resume_full_browser_refresh_session_v1$;

CREATE FUNCTION public.revoke_full_browser_refresh_session_v1(p_refresh_handle text)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $revoke_full_browser_refresh_session_v1$
DECLARE affected integer;
BEGIN
  IF session_user <> 'decision_auth' OR p_refresh_handle !~ '^rfs1_[0-9a-f]{64}$' THEN
    RETURN false;
  END IF;
  UPDATE public.full_browser_refresh_sessions item SET revoked_at=statement_timestamp()
  WHERE item.token_hash='sha256:'||pg_catalog.encode(public.digest(p_refresh_handle,'sha256'),'hex')
    AND item.revoked_at IS NULL;
  GET DIAGNOSTICS affected=ROW_COUNT;
  RETURN affected=1;
END
$revoke_full_browser_refresh_session_v1$;

ALTER FUNCTION public.issue_full_browser_refresh_session_v1(text) OWNER TO flyway;
ALTER FUNCTION public.resume_full_browser_refresh_session_v1(text,integer) OWNER TO flyway;
ALTER FUNCTION public.revoke_full_browser_refresh_session_v1(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.issue_full_browser_refresh_session_v1(text),
  public.resume_full_browser_refresh_session_v1(text,integer),
  public.revoke_full_browser_refresh_session_v1(text)
  FROM PUBLIC, decision_app, decision_identity, decision_worker, decision_replay;
GRANT EXECUTE ON FUNCTION public.issue_full_browser_refresh_session_v1(text),
  public.resume_full_browser_refresh_session_v1(text,integer),
  public.revoke_full_browser_refresh_session_v1(text) TO decision_auth;
