-- Resolve the owner from the same unexpired, single-use S4.9 authorization
-- that created the Voyage lease, then atomically reserve its gross price cap.
CREATE FUNCTION public.reserve_s4_9_operator_voyage_gross_usage_v1(
  p_reservation_id text, p_scope_claim_id text, p_question_sha256 text,
  p_max_gross_microusd bigint, p_deployment_hard_cap_microusd bigint
) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $reserve_s4_9_operator_voyage_gross_usage_v1$
DECLARE authorized_owner text;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_rag_writer'
     OR p_reservation_id IS NULL OR p_reservation_id !~ '^aibr_[0-9a-f]{32}$'
     OR p_scope_claim_id IS NULL OR p_scope_claim_id !~ '^rvs_[0-9a-f]{32}$'
     OR p_question_sha256 IS NULL OR p_question_sha256 !~ '^[0-9a-f]{64}$'
     OR p_max_gross_microusd IS NULL OR p_max_gross_microusd <= 0
     OR p_deployment_hard_cap_microusd IS NULL OR p_deployment_hard_cap_microusd <= 0 THEN
    RAISE EXCEPTION 'S4.9 operator budget reservation invalid' USING ERRCODE = '42501';
  END IF;
  SELECT authorized_query.owner_user_id INTO authorized_owner
  FROM public.s4_9_voyage_query_authorizations authorized_query
  WHERE authorized_query.scope_claim_id = p_scope_claim_id
    AND authorized_query.question_sha256 = p_question_sha256
    AND authorized_query.expires_at > statement_timestamp();
  IF authorized_owner IS NULL THEN
    RAISE EXCEPTION 'S4.9 operator budget authorization unavailable' USING ERRCODE = '55000';
  END IF;
  RETURN public.reserve_operator_ai_gross_usage_v1(
    p_reservation_id, authorized_owner, 'RAG_VOYAGE', 'VOYAGE',
    p_max_gross_microusd, p_deployment_hard_cap_microusd
  );
END;
$reserve_s4_9_operator_voyage_gross_usage_v1$;
ALTER FUNCTION public.reserve_s4_9_operator_voyage_gross_usage_v1(text,text,text,bigint,bigint) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.reserve_s4_9_operator_voyage_gross_usage_v1(text,text,text,bigint,bigint)
  FROM PUBLIC, decision_app, decision_worker, decision_auth, decision_identity, decision_replay,
  decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.reserve_s4_9_operator_voyage_gross_usage_v1(text,text,text,bigint,bigint)
  TO decision_rag_writer;
