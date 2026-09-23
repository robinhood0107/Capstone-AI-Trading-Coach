-- The public products count a conservative list-price exposure, including
-- calls covered by provider free credits. Provider billing can lag or change;
-- a free-tier balance must not silently remove this application's limit.
CREATE TABLE public.operator_ai_gross_usage_reservations (
  reservation_id text PRIMARY KEY CHECK (reservation_id ~ '^aibr_[0-9a-f]{32}$'),
  usage_date date NOT NULL,
  owner_user_id text CHECK (owner_user_id IS NULL OR owner_user_id ~ '^usr_[A-Za-z0-9_-]{4,96}$'),
  source text NOT NULL CHECK (source IN ('DEMO_AGENT','FULL_AGENT','TRADE_AI','RAG_VERTEX','RAG_VOYAGE')),
  provider text NOT NULL CHECK (provider IN ('VERTEX','VOYAGE')),
  max_gross_microusd bigint NOT NULL CHECK (max_gross_microusd > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((source = 'DEMO_AGENT') = (owner_user_id IS NULL)),
  CHECK ((source = 'RAG_VOYAGE') = (provider = 'VOYAGE'))
);
ALTER TABLE public.operator_ai_gross_usage_reservations OWNER TO flyway;
ALTER TABLE public.operator_ai_gross_usage_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.operator_ai_gross_usage_reservations FORCE ROW LEVEL SECURITY;
CREATE POLICY operator_ai_gross_usage_definer_v201 ON public.operator_ai_gross_usage_reservations
  TO PUBLIC USING (
    current_user = 'flyway' AND session_user IN ('decision_app','decision_rag_writer','decision_automation_runtime')
  ) WITH CHECK (
    current_user = 'flyway' AND session_user IN ('decision_app','decision_rag_writer','decision_automation_runtime')
  );
REVOKE ALL ON public.operator_ai_gross_usage_reservations FROM PUBLIC, decision_app,
  decision_auth, decision_identity, decision_worker, decision_replay,
  decision_rag_writer, decision_automation_runtime;
CREATE INDEX operator_ai_gross_usage_date_v201
  ON public.operator_ai_gross_usage_reservations(usage_date);

-- V200 policy row is read under the same lock by all provider paths. ADMIN
-- updates therefore serialize with reservations and lowering the cap cannot
-- admit another call after the new value commits.
CREATE POLICY operator_ai_budget_provider_definer_v201 ON public.operator_ai_budget_policy
  TO PUBLIC USING (
    current_user = 'flyway' AND session_user IN ('decision_rag_writer','decision_automation_runtime')
  ) WITH CHECK (
    current_user = 'flyway' AND session_user IN ('decision_rag_writer','decision_automation_runtime')
  );

CREATE FUNCTION public.reserve_operator_ai_gross_usage_v1(
  p_reservation_id text, p_owner_user_id text, p_source text, p_provider text,
  p_max_gross_microusd bigint, p_deployment_hard_cap_microusd bigint
) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $reserve_operator_ai_gross_usage_v1$
DECLARE
  soft_cap bigint;
  effective_cap bigint;
  used_today bigint;
  today_kst date := (statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date;
BEGIN
  IF current_user <> 'flyway'
     OR session_user NOT IN ('decision_app','decision_rag_writer','decision_automation_runtime')
     OR p_reservation_id IS NULL OR p_reservation_id !~ '^aibr_[0-9a-f]{32}$'
     OR p_source IS NULL OR p_source NOT IN ('DEMO_AGENT','FULL_AGENT','TRADE_AI','RAG_VERTEX','RAG_VOYAGE')
     OR p_provider IS NULL OR p_provider NOT IN ('VERTEX','VOYAGE')
     OR (p_source = 'RAG_VOYAGE') <> (p_provider = 'VOYAGE')
     OR (p_source = 'DEMO_AGENT') <> (p_owner_user_id IS NULL)
     OR (p_owner_user_id IS NOT NULL AND p_owner_user_id !~ '^usr_[A-Za-z0-9_-]{4,96}$')
     OR p_max_gross_microusd IS NULL OR p_max_gross_microusd <= 0
     OR p_deployment_hard_cap_microusd IS NULL OR p_deployment_hard_cap_microusd <= 0
     OR (session_user = 'decision_rag_writer' AND p_source <> 'RAG_VOYAGE')
     OR (session_user = 'decision_automation_runtime' AND p_source <> 'TRADE_AI')
     OR (session_user = 'decision_app' AND p_source = 'RAG_VOYAGE') THEN
    RAISE EXCEPTION 'operator AI reservation invalid' USING ERRCODE = '42501';
  END IF;

  SELECT policy.daily_soft_cap_microusd INTO soft_cap
  FROM public.operator_ai_budget_policy policy WHERE policy.singleton FOR UPDATE;
  IF soft_cap IS NULL OR soft_cap = 0 THEN RETURN false; END IF;
  effective_cap := LEAST(soft_cap, p_deployment_hard_cap_microusd);
  IF p_max_gross_microusd > effective_cap THEN RETURN false; END IF;
  SELECT COALESCE(SUM(item.max_gross_microusd), 0) INTO used_today
  FROM public.operator_ai_gross_usage_reservations item WHERE item.usage_date = today_kst;
  IF used_today > effective_cap - p_max_gross_microusd THEN RETURN false; END IF;
  IF EXISTS (
    SELECT 1 FROM public.operator_ai_gross_usage_reservations item
    WHERE item.reservation_id = p_reservation_id
  ) THEN RETURN false; END IF;
  INSERT INTO public.operator_ai_gross_usage_reservations(
    reservation_id, usage_date, owner_user_id, source, provider, max_gross_microusd
  ) VALUES (
    p_reservation_id, today_kst, p_owner_user_id, p_source, p_provider, p_max_gross_microusd
  );
  RETURN true;
END;
$reserve_operator_ai_gross_usage_v1$;
ALTER FUNCTION public.reserve_operator_ai_gross_usage_v1(text,text,text,text,bigint,bigint) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.reserve_operator_ai_gross_usage_v1(text,text,text,text,bigint,bigint)
  FROM PUBLIC, decision_app, decision_auth, decision_identity, decision_worker,
  decision_replay, decision_rag_writer, decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.reserve_operator_ai_gross_usage_v1(text,text,text,text,bigint,bigint)
  TO decision_app, decision_rag_writer, decision_automation_runtime;
