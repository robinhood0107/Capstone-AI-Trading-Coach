-- Operator-controlled capacity. Limits reject only the request that would exceed them:
-- existing users and already armed automations keep running.

CREATE TABLE public.service_limits (
  limits_id smallint PRIMARY KEY CHECK (limits_id = 1),
  signup_cap integer CHECK (signup_cap IS NULL OR signup_cap BETWEEN 1 AND 1000000),
  automation_active_cap integer NOT NULL DEFAULT 20 CHECK (automation_active_cap BETWEEN 1 AND 1000),
  updated_by text REFERENCES public.users(user_id),
  updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO public.service_limits(limits_id) VALUES (1);
ALTER TABLE public.service_limits OWNER TO flyway;
REVOKE ALL ON public.service_limits FROM PUBLIC, decision_app, decision_auth, decision_identity, decision_worker, decision_replay;

-- Capacity checks and the admin console must count every owner's row. These SELECT policies apply
-- only while a V214 definer function runs (current_user = flyway) and has set its transaction flag.
CREATE POLICY automation_control_capacity_reader_v214 ON public.automation_control FOR SELECT TO PUBLIC
  USING (current_user = 'flyway' AND pg_catalog.current_setting('app.v214_capacity_read', true) = 'on');
CREATE POLICY automation_runtime_claim_capacity_reader_v214 ON public.automation_runtime_claim FOR SELECT TO PUBLIC
  USING (current_user = 'flyway' AND pg_catalog.current_setting('app.v214_capacity_read', true) = 'on');
-- The console shows only whether a key is registered. Envelope columns stay unreadable.
GRANT SELECT (owner_user_id, brokerage_mode, credential_state) ON public.user_broker_credentials TO flyway;
CREATE POLICY user_broker_credentials_capacity_reader_v214 ON public.user_broker_credentials FOR SELECT TO PUBLIC
  USING (current_user = 'flyway' AND pg_catalog.current_setting('app.v214_capacity_read', true) = 'on');

-- 1. Signup cap: every new account (password signup, first provider login) inserts into users.
CREATE FUNCTION public.enforce_signup_cap_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $enforce_signup_cap_v1$
DECLARE cap integer;
DECLARE current_count bigint;
BEGIN
  SELECT signup_cap INTO cap FROM public.service_limits WHERE limits_id = 1;
  IF cap IS NULL THEN
    RETURN NEW;
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(214, 1);
  SELECT count(*) INTO current_count FROM public.users WHERE status <> 'DISABLED';
  IF current_count >= cap THEN
    RAISE EXCEPTION 'signup capacity reached' USING ERRCODE = '53400';
  END IF;
  RETURN NEW;
END
$enforce_signup_cap_v1$;
ALTER FUNCTION public.enforce_signup_cap_v1() OWNER TO flyway;
CREATE TRIGGER users_signup_cap_v214 BEFORE INSERT ON public.users
  FOR EACH ROW EXECUTE FUNCTION public.enforce_signup_cap_v1();

-- 2. Automation cap: only the transition into ARMED is checked, on every arm path.
CREATE FUNCTION public.enforce_automation_active_cap_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog
AS $enforce_automation_active_cap_v1$
DECLARE cap integer;
DECLARE active_count bigint;
BEGIN
  IF NEW.control_state <> 'ARMED' OR (TG_OP = 'UPDATE' AND OLD.control_state = 'ARMED') THEN
    RETURN NEW;
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(214, 2);
  SELECT automation_active_cap INTO cap FROM public.service_limits WHERE limits_id = 1;
  PERFORM pg_catalog.set_config('app.v214_capacity_read', 'on', true);
  SELECT count(*) INTO active_count
  FROM public.automation_control control
  JOIN public.users app_user ON app_user.user_id = control.user_id
  WHERE app_user.status = 'ACTIVE' AND control.control_state = 'ARMED' AND control.user_id <> NEW.user_id;
  PERFORM pg_catalog.set_config('app.v214_capacity_read', 'off', true);
  IF active_count >= cap THEN
    RAISE EXCEPTION 'automation capacity reached' USING ERRCODE = '53400';
  END IF;
  RETURN NEW;
END
$enforce_automation_active_cap_v1$;
ALTER FUNCTION public.enforce_automation_active_cap_v1() OWNER TO flyway;
CREATE TRIGGER automation_control_active_cap_v214 BEFORE INSERT OR UPDATE OF control_state ON public.automation_control
  FOR EACH ROW EXECUTE FUNCTION public.enforce_automation_active_cap_v1();

-- 3. The runtime listing never fails for everyone: capacity is enforced when arming.
CREATE OR REPLACE FUNCTION public.p1_list_armed_automation_users_v1()
RETURNS TABLE(user_id text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_list_armed_automation_users_v1$
BEGIN
  IF session_user <> 'decision_automation_runtime' THEN
    RAISE EXCEPTION 'automation owner listing denied' USING ERRCODE='42501';
  END IF;
  RETURN QUERY
  SELECT control.user_id
  FROM public.automation_control control
  JOIN public.users app_user ON app_user.user_id=control.user_id
  WHERE app_user.status='ACTIVE'
    AND (control.control_state='ARMED' OR EXISTS (
      SELECT 1 FROM public.automation_runtime_claim claim
      WHERE claim.user_id=control.user_id AND claim.claim_state='ACTIVE'
    ))
  ORDER BY control.user_id;
END
$p1_list_armed_automation_users_v1$;

CREATE FUNCTION public.p1_read_automation_active_cap_v1()
RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_read_automation_active_cap_v1$
  SELECT automation_active_cap FROM public.service_limits
  WHERE limits_id = 1 AND session_user = 'decision_automation_runtime'
$p1_read_automation_active_cap_v1$;
ALTER FUNCTION public.p1_read_automation_active_cap_v1() OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_read_automation_active_cap_v1() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_read_automation_active_cap_v1() TO decision_automation_runtime;

-- 4. Admin console. Spring passes the Bearer actor; every function re-checks it is an ACTIVE ADMIN.
CREATE FUNCTION public.admin_require_actor_v1(p_actor_user_id text)
RETURNS public.users
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $admin_require_actor_v1$
DECLARE actor public.users%ROWTYPE;
BEGIN
  IF session_user <> 'decision_auth' OR p_actor_user_id IS NULL OR p_actor_user_id !~ '^usr_[A-Za-z0-9_-]{4,96}$' THEN
    RAISE EXCEPTION 'admin console denied' USING ERRCODE = '42501';
  END IF;
  SELECT * INTO actor FROM public.users WHERE user_id = p_actor_user_id;
  IF actor.user_id IS NULL OR actor.role <> 'ADMIN' OR actor.status <> 'ACTIVE' THEN
    RAISE EXCEPTION 'admin console denied' USING ERRCODE = '42501';
  END IF;
  RETURN actor;
END
$admin_require_actor_v1$;
ALTER FUNCTION public.admin_require_actor_v1(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.admin_require_actor_v1(text) FROM PUBLIC;

CREATE FUNCTION public.admin_list_users_v1(p_actor_user_id text, p_search text, p_limit integer, p_offset integer)
RETURNS TABLE(
  user_id text, username text, role text, status text, created_at timestamptz,
  email text, providers text[], broker_state text, automation_state text, total_count bigint
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $admin_list_users_v1$
BEGIN
  PERFORM public.admin_require_actor_v1(p_actor_user_id);
  PERFORM pg_catalog.set_config('app.v214_capacity_read', 'on', true);
  IF p_limit NOT BETWEEN 1 AND 200 OR p_offset < 0 OR char_length(coalesce(p_search, '')) > 254 THEN
    RAISE EXCEPTION 'admin list denied' USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
  WITH matched AS (
    SELECT u.user_id, u.username, u.role, u.status, u.created_at, pw.email_normalized AS email
    FROM public.users u
    LEFT JOIN public.password_login_identities pw ON pw.user_id = u.user_id
    WHERE coalesce(p_search, '') = ''
       OR u.user_id ILIKE '%' || p_search || '%'
       OR u.username ILIKE '%' || p_search || '%'
       OR pw.email_normalized ILIKE '%' || lower(p_search) || '%'
  )
  SELECT m.user_id, m.username, m.role, m.status, m.created_at, m.email,
         coalesce((SELECT array_agg(CASE s.issuer WHEN 'https://accounts.google.com' THEN 'google' ELSE 'kakao' END ORDER BY s.issuer)
                   FROM public.social_login_identities s WHERE s.user_id = m.user_id), ARRAY[]::text[]),
         (SELECT c.credential_state FROM public.user_broker_credentials c WHERE c.owner_user_id = m.user_id AND c.brokerage_mode = 'KIS_MOCK'),
         (SELECT a.control_state FROM public.automation_control a WHERE a.user_id = m.user_id LIMIT 1),
         count(*) OVER ()
  FROM matched m
  ORDER BY m.created_at DESC, m.user_id
  LIMIT p_limit OFFSET p_offset;
  PERFORM pg_catalog.set_config('app.v214_capacity_read', 'off', true);
END
$admin_list_users_v1$;

CREATE FUNCTION public.admin_set_user_access_v1(
  p_actor_user_id text, p_target_user_id text, p_role text, p_status text
) RETURNS TABLE(user_id text, role text, status text, security_version bigint)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $admin_set_user_access_v1$
DECLARE actor public.users%ROWTYPE;
DECLARE target public.users%ROWTYPE;
DECLARE now_at timestamptz := statement_timestamp();
DECLARE remaining_admins bigint;
BEGIN
  actor := public.admin_require_actor_v1(p_actor_user_id);
  IF p_target_user_id IS NULL OR p_target_user_id !~ '^usr_[A-Za-z0-9_-]{4,96}$'
     OR p_role NOT IN ('USER', 'ADMIN') OR p_status NOT IN ('ACTIVE', 'DISABLED') THEN
    RAISE EXCEPTION 'admin access change denied' USING ERRCODE = '22023';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(214, 3);
  SELECT * INTO target FROM public.users WHERE users.user_id = p_target_user_id FOR UPDATE;
  IF target.user_id IS NULL THEN
    RAISE EXCEPTION 'admin access target missing' USING ERRCODE = 'P0002';
  END IF;
  IF target.user_id = 'usr_demo_admin' AND p_status = 'ACTIVE' THEN
    RAISE EXCEPTION 'retired account cannot be reactivated' USING ERRCODE = '23514';
  END IF;
  IF target.role = 'ADMIN' AND target.status = 'ACTIVE' AND (p_role <> 'ADMIN' OR p_status <> 'ACTIVE') THEN
    SELECT count(*) INTO remaining_admins FROM public.users
    WHERE users.role = 'ADMIN' AND users.status = 'ACTIVE' AND users.user_id <> target.user_id;
    IF remaining_admins = 0 THEN
      RAISE EXCEPTION 'the last active admin cannot be removed' USING ERRCODE = '23514';
    END IF;
  END IF;
  IF target.role = p_role AND target.status = p_status THEN
    RETURN QUERY SELECT target.user_id, target.role, target.status, target.security_version;
    RETURN;
  END IF;
  UPDATE public.users
     SET role = p_role, status = p_status, security_version = users.security_version + 1, updated_at = now_at
   WHERE users.user_id = target.user_id
  RETURNING * INTO target;
  UPDATE public.actor_auth_session SET revoked_at = now_at
   WHERE actor_user_id = target.user_id AND revoked_at IS NULL;
  INSERT INTO public.audit_logs(audit_log_id, user_id, actor_role, action, target_type, target_id, payload_json)
  VALUES ('aud_admin_' || encode(public.gen_random_bytes(16), 'hex'), actor.user_id, 'ADMIN',
          'ADMIN_USER_ACCESS_CHANGED', 'USER', target.user_id,
          jsonb_build_object('role', target.role, 'status', target.status));
  RETURN QUERY SELECT target.user_id, target.role, target.status, target.security_version;
END
$admin_set_user_access_v1$;

CREATE FUNCTION public.admin_list_automation_v1(p_actor_user_id text)
RETURNS TABLE(
  user_id text, username text, control_state text, account_id text,
  control_updated_at timestamptz, today_claim_state text, today_run_id text
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $admin_list_automation_v1$
BEGIN
  PERFORM public.admin_require_actor_v1(p_actor_user_id);
  PERFORM pg_catalog.set_config('app.v214_capacity_read', 'on', true);
  RETURN QUERY
  SELECT a.user_id, u.username, a.control_state, a.account_id, a.updated_at,
         (SELECT c.claim_state FROM public.automation_runtime_claim c
           WHERE c.user_id = a.user_id ORDER BY c.session_date DESC LIMIT 1),
         (SELECT c.run_id FROM public.automation_runtime_claim c
           WHERE c.user_id = a.user_id ORDER BY c.session_date DESC LIMIT 1)
  FROM public.automation_control a
  JOIN public.users u ON u.user_id = a.user_id
  WHERE a.control_state <> 'DISARMED' OR EXISTS (
    SELECT 1 FROM public.automation_runtime_claim c WHERE c.user_id = a.user_id AND c.claim_state = 'ACTIVE'
  )
  ORDER BY a.updated_at DESC;
  PERFORM pg_catalog.set_config('app.v214_capacity_read', 'off', true);
END
$admin_list_automation_v1$;

CREATE FUNCTION public.admin_read_limits_v1(p_actor_user_id text)
RETURNS TABLE(
  signup_cap integer, automation_active_cap integer, updated_by text, updated_at timestamptz,
  user_count bigint, active_user_count bigint, armed_count bigint
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $admin_read_limits_v1$
BEGIN
  PERFORM public.admin_require_actor_v1(p_actor_user_id);
  PERFORM pg_catalog.set_config('app.v214_capacity_read', 'on', true);
  RETURN QUERY
  SELECT l.signup_cap, l.automation_active_cap, l.updated_by, l.updated_at,
         (SELECT count(*) FROM public.users),
         (SELECT count(*) FROM public.users WHERE status <> 'DISABLED'),
         (SELECT count(*) FROM public.automation_control a JOIN public.users u ON u.user_id = a.user_id
           WHERE u.status = 'ACTIVE' AND a.control_state = 'ARMED')
  FROM public.service_limits l WHERE l.limits_id = 1;
  PERFORM pg_catalog.set_config('app.v214_capacity_read', 'off', true);
END
$admin_read_limits_v1$;

CREATE FUNCTION public.admin_update_limits_v1(
  p_actor_user_id text, p_signup_cap integer, p_automation_active_cap integer
) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $admin_update_limits_v1$
DECLARE actor public.users%ROWTYPE;
BEGIN
  actor := public.admin_require_actor_v1(p_actor_user_id);
  UPDATE public.service_limits
     SET signup_cap = p_signup_cap, automation_active_cap = p_automation_active_cap,
         updated_by = actor.user_id, updated_at = statement_timestamp()
   WHERE limits_id = 1;
  INSERT INTO public.audit_logs(audit_log_id, user_id, actor_role, action, target_type, target_id, payload_json)
  VALUES ('aud_admin_' || encode(public.gen_random_bytes(16), 'hex'), actor.user_id, 'ADMIN',
          'ADMIN_LIMITS_CHANGED', 'SYSTEM', 'service_limits',
          jsonb_build_object('signupCap', p_signup_cap, 'automationActiveCap', p_automation_active_cap));
END
$admin_update_limits_v1$;

DO $admin_grants$
DECLARE fn text;
BEGIN
  FOREACH fn IN ARRAY ARRAY[
    'admin_list_users_v1(text,text,integer,integer)',
    'admin_set_user_access_v1(text,text,text,text)',
    'admin_list_automation_v1(text)',
    'admin_read_limits_v1(text)',
    'admin_update_limits_v1(text,integer,integer)'
  ] LOOP
    EXECUTE format('ALTER FUNCTION public.%s OWNER TO flyway', fn);
    EXECUTE format('REVOKE ALL ON FUNCTION public.%s FROM PUBLIC, decision_app, decision_identity, decision_worker, decision_replay', fn);
    EXECUTE format('GRANT EXECUTE ON FUNCTION public.%s TO decision_auth', fn);
  END LOOP;
END
$admin_grants$;
