-- One operator policy for the public products. The private deployment hard cap
-- is never stored here; the application must reject a soft cap above it.
CREATE TABLE public.operator_ai_budget_policy (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  daily_soft_cap_microusd bigint NOT NULL DEFAULT 0 CHECK (daily_soft_cap_microusd >= 0),
  revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
  changed_by text REFERENCES public.users(user_id),
  changed_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.operator_ai_budget_policy OWNER TO flyway;
-- Flyway applies this seed with its migration login, before forced RLS limits
-- the table to later decision_app SECURITY DEFINER calls.
INSERT INTO public.operator_ai_budget_policy(singleton) VALUES (true);
ALTER TABLE public.operator_ai_budget_policy ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.operator_ai_budget_policy FORCE ROW LEVEL SECURITY;
CREATE POLICY operator_ai_budget_policy_definer_v200 ON public.operator_ai_budget_policy
  TO PUBLIC USING (current_user = 'flyway' AND session_user = 'decision_app')
  WITH CHECK (current_user = 'flyway' AND session_user = 'decision_app');
REVOKE ALL ON public.operator_ai_budget_policy FROM PUBLIC, decision_app, decision_auth,
  decision_identity, decision_worker, decision_replay;

-- V198 permits only decision_auth to inspect identities. This definer-only
-- policy lets the budget function prove ADMIN came from Google OIDC, while
-- decision_app still has no direct SELECT grant on the identity table.
CREATE POLICY google_oidc_identities_budget_definer_v200 ON public.google_oidc_identities
  FOR SELECT TO PUBLIC USING (current_user = 'flyway' AND session_user = 'decision_app');

CREATE FUNCTION public.read_operator_ai_budget_policy_v1(
  p_actor_user_id text, p_security_version bigint
) RETURNS TABLE(daily_soft_cap_microusd bigint, revision bigint, changed_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $read_operator_ai_budget_policy_v1$
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR NOT EXISTS (
       SELECT 1 FROM public.users actor JOIN public.google_oidc_identities identity
         ON identity.user_id = actor.user_id
       WHERE actor.user_id = p_actor_user_id AND actor.security_version = p_security_version
         AND actor.role = 'ADMIN' AND actor.status = 'ACTIVE'
         AND actor.password_hash IS NULL
     ) THEN
    RAISE EXCEPTION 'operator budget actor denied' USING ERRCODE = '42501';
  END IF;
  RETURN QUERY SELECT policy.daily_soft_cap_microusd, policy.revision, policy.changed_at
  FROM public.operator_ai_budget_policy policy WHERE policy.singleton;
END;
$read_operator_ai_budget_policy_v1$;
ALTER FUNCTION public.read_operator_ai_budget_policy_v1(text, bigint) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.read_operator_ai_budget_policy_v1(text, bigint)
  FROM PUBLIC, decision_auth, decision_identity, decision_worker, decision_replay;
GRANT EXECUTE ON FUNCTION public.read_operator_ai_budget_policy_v1(text, bigint) TO decision_app;

CREATE FUNCTION public.set_operator_ai_budget_policy_v1(
  p_actor_user_id text, p_security_version bigint, p_daily_soft_cap_microusd bigint,
  p_expected_revision bigint
) RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $set_operator_ai_budget_policy_v1$
DECLARE next_revision bigint;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR p_daily_soft_cap_microusd IS NULL OR p_daily_soft_cap_microusd < 0
     OR p_expected_revision IS NULL OR p_expected_revision < 1
     OR NOT EXISTS (
       SELECT 1 FROM public.users actor JOIN public.google_oidc_identities identity
         ON identity.user_id = actor.user_id
       WHERE actor.user_id = p_actor_user_id AND actor.security_version = p_security_version
         AND actor.role = 'ADMIN' AND actor.status = 'ACTIVE'
         AND actor.password_hash IS NULL
     ) THEN
    RAISE EXCEPTION 'operator budget actor denied' USING ERRCODE = '42501';
  END IF;
  UPDATE public.operator_ai_budget_policy policy
  SET daily_soft_cap_microusd = p_daily_soft_cap_microusd,
      revision = policy.revision + 1,
      changed_by = p_actor_user_id,
      changed_at = statement_timestamp()
  WHERE policy.singleton AND policy.revision = p_expected_revision
  RETURNING policy.revision INTO next_revision;
  IF next_revision IS NULL THEN
    RAISE EXCEPTION 'operator budget revision conflict' USING ERRCODE = '40001';
  END IF;
  INSERT INTO public.audit_logs(audit_log_id, user_id, actor_role, action, target_type, target_id, payload_json)
  VALUES (
    'aud_ai_budget_' || pg_catalog.encode(public.gen_random_bytes(16), 'hex'),
    p_actor_user_id, 'ADMIN', 'OPERATOR_AI_BUDGET_CHANGED', 'OPERATOR_AI_BUDGET', 'global',
    pg_catalog.jsonb_build_object('dailySoftCapMicrousd', p_daily_soft_cap_microusd, 'revision', next_revision)
  );
  RETURN next_revision;
END;
$set_operator_ai_budget_policy_v1$;
ALTER FUNCTION public.set_operator_ai_budget_policy_v1(text, bigint, bigint, bigint) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.set_operator_ai_budget_policy_v1(text, bigint, bigint, bigint)
  FROM PUBLIC, decision_auth, decision_identity, decision_worker, decision_replay;
GRANT EXECUTE ON FUNCTION public.set_operator_ai_budget_policy_v1(text, bigint, bigint, bigint) TO decision_app;

GRANT SELECT ON TABLE public.users, public.google_oidc_identities TO flyway;
GRANT INSERT ON TABLE public.audit_logs TO flyway;
