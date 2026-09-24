-- V201/V206 usage estimates remain useful for visibility, but they are no longer
-- an application-side call gate. Provider quotas and per-request contracts own limits.
CREATE FUNCTION public.record_operator_ai_gross_usage_v1(
  p_reservation_id text, p_owner_user_id text, p_source text, p_provider text,
  p_max_gross_microusd bigint
) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $record_operator_ai_gross_usage_v1$
DECLARE
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
     OR (session_user = 'decision_rag_writer' AND p_source <> 'RAG_VOYAGE')
     OR (session_user = 'decision_automation_runtime' AND p_source <> 'TRADE_AI')
     OR (session_user = 'decision_app' AND p_source = 'RAG_VOYAGE') THEN
    RAISE EXCEPTION 'operator AI usage measurement invalid' USING ERRCODE = '42501';
  END IF;

  INSERT INTO public.operator_ai_gross_usage_reservations(
    reservation_id, usage_date, owner_user_id, source, provider, max_gross_microusd
  ) VALUES (
    p_reservation_id, today_kst, p_owner_user_id, p_source, p_provider, p_max_gross_microusd
  ) ON CONFLICT (reservation_id) DO NOTHING;
  RETURN true;
END;
$record_operator_ai_gross_usage_v1$;
ALTER FUNCTION public.record_operator_ai_gross_usage_v1(text,text,text,text,bigint) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.record_operator_ai_gross_usage_v1(text,text,text,text,bigint)
  FROM PUBLIC, decision_auth, decision_identity, decision_worker, decision_replay;
GRANT EXECUTE ON FUNCTION public.record_operator_ai_gross_usage_v1(text,text,text,text,bigint)
  TO decision_app, decision_rag_writer, decision_automation_runtime;

CREATE FUNCTION public.record_s4_9_operator_voyage_gross_usage_v1(
  p_reservation_id text, p_scope_claim_id text, p_question_sha256 text,
  p_max_gross_microusd bigint
) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $record_s4_9_operator_voyage_gross_usage_v1$
DECLARE authorized_owner text;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_rag_writer'
     OR p_reservation_id IS NULL OR p_reservation_id !~ '^aibr_[0-9a-f]{32}$'
     OR p_scope_claim_id IS NULL OR p_scope_claim_id !~ '^rvs_[0-9a-f]{32}$'
     OR p_question_sha256 IS NULL OR p_question_sha256 !~ '^[0-9a-f]{64}$'
     OR p_max_gross_microusd IS NULL OR p_max_gross_microusd <= 0 THEN
    RAISE EXCEPTION 'S4.9 operator Voyage usage measurement invalid' USING ERRCODE = '42501';
  END IF;
  SELECT authorized_query.owner_user_id INTO authorized_owner
  FROM public.s4_9_voyage_query_authorizations authorized_query
  WHERE authorized_query.scope_claim_id = p_scope_claim_id
    AND authorized_query.question_sha256 = p_question_sha256
    AND authorized_query.expires_at > statement_timestamp();
  IF authorized_owner IS NULL THEN
    RAISE EXCEPTION 'S4.9 operator Voyage usage authorization unavailable' USING ERRCODE = '55000';
  END IF;
  RETURN public.record_operator_ai_gross_usage_v1(
    p_reservation_id, authorized_owner, 'RAG_VOYAGE', 'VOYAGE', p_max_gross_microusd
  );
END;
$record_s4_9_operator_voyage_gross_usage_v1$;
ALTER FUNCTION public.record_s4_9_operator_voyage_gross_usage_v1(text,text,text,bigint) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.record_s4_9_operator_voyage_gross_usage_v1(text,text,text,bigint)
  FROM PUBLIC, decision_app, decision_auth, decision_identity, decision_worker,
  decision_replay, decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.record_s4_9_operator_voyage_gross_usage_v1(text,text,text,bigint)
  TO decision_rag_writer;

-- Remove the ADMIN/day-dollar policy surface so configuration cannot imply an
-- application stop that no longer exists.
DROP FUNCTION public.reserve_s4_9_operator_voyage_gross_usage_v1(text,text,text,bigint,bigint);
DROP FUNCTION public.reserve_operator_ai_gross_usage_v1(text,text,text,text,bigint,bigint);
DROP FUNCTION public.read_operator_ai_budget_policy_v1(text,bigint);
DROP FUNCTION public.set_operator_ai_budget_policy_v1(text,bigint,bigint,bigint);
DROP TABLE public.operator_ai_budget_policy;
DROP POLICY IF EXISTS google_oidc_identities_budget_definer_v200 ON public.google_oidc_identities;
