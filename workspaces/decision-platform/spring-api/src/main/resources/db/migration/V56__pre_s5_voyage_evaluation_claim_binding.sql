-- 이미 적용된 V55 bytes는 보존하고 evaluation attempt를 원래 reservation identity에 결속한다.
-- writer가 drift된 packet/manifest/scope를 전달하면 provider ledger를 소비하기 전에 fail-closed한다.
CREATE OR REPLACE FUNCTION claim_rag_v2_immutable_voyage_evaluation_batch_attempt(
  p_usage_event_id text,
  p_scope_claim_sha256 text,
  p_component_scope text,
  p_query_manifest_sha256 text,
  p_packet_sha256 text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $claim_rag_v2_immutable_voyage_evaluation_batch_attempt$
DECLARE
  reservation public.rag_v2_immutable_voyage_query_usage_reservations%ROWTYPE;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_rag_writer' THEN
    RAISE EXCEPTION 'Pre-S5 Voyage evaluation claim is forbidden' USING ERRCODE = '42501';
  END IF;
  SELECT * INTO reservation
  FROM public.rag_v2_immutable_voyage_query_usage_reservations
  WHERE usage_event_id = p_usage_event_id
  FOR UPDATE;
  IF NOT FOUND
     OR reservation.scope_claim_sha256 <> p_scope_claim_sha256
     OR reservation.evaluation_component_scope <> p_component_scope
     OR reservation.query_sha256 <> p_query_manifest_sha256
     OR reservation.packet_sha256 <> p_packet_sha256 THEN
    RAISE EXCEPTION 'Pre-S5 Voyage evaluation claim conflicts with its reservation'
      USING ERRCODE = '55000';
  END IF;
  PERFORM public.claim_rag_v2_immutable_voyage_query_usage_attempt(p_usage_event_id);
  INSERT INTO public.rag_v2_immutable_voyage_evaluation_batch_attempts (
    scope_claim_sha256, component_scope, query_manifest_sha256, usage_event_id, packet_sha256
  ) VALUES (
    reservation.scope_claim_sha256, reservation.evaluation_component_scope,
    reservation.query_sha256, p_usage_event_id, reservation.packet_sha256
  );
END
$claim_rag_v2_immutable_voyage_evaluation_batch_attempt$;
ALTER FUNCTION claim_rag_v2_immutable_voyage_evaluation_batch_attempt(text,text,text,text,text)
  OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION
  claim_rag_v2_immutable_voyage_evaluation_batch_attempt(text,text,text,text,text) FROM PUBLIC;

DO $pre_s5_voyage_evaluation_claim_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    GRANT EXECUTE ON FUNCTION
      claim_rag_v2_immutable_voyage_evaluation_batch_attempt(text,text,text,text,text)
    TO decision_rag_writer;
  END IF;
END
$pre_s5_voyage_evaluation_claim_acl$;
