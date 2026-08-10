-- V52는 아직 사용되지 않은 Vertex usage ledger를 API-key-only Vertex Express mode로 supersede한다.
-- service-account/OAuth token lane은 새 outbound authority가 아니며, raw API key·prompt·response는 DB에 저장하지 않는다.

DO $reject_pre_live_vertex_usage_for_api_key_only$
BEGIN
  IF EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_reservations)
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_token_attempts)
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_generate_content_attempts)
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_outcomes) THEN
    RAISE EXCEPTION 'Pre-S5 Vertex API-key-only migration requires an empty pre-live usage ledger';
  END IF;
END
$reject_pre_live_vertex_usage_for_api_key_only$;

ALTER TABLE public.rag_v2_immutable_vertex_usage_reservations
  ADD COLUMN authentication_mode text NOT NULL DEFAULT 'SERVICE_ACCOUNT_OAUTH';
ALTER TABLE public.rag_v2_immutable_vertex_usage_reservations
  DROP CONSTRAINT rag_v2_immutable_vertex_usage_reservation_binding_check;
ALTER TABLE public.rag_v2_immutable_vertex_usage_reservations
  ADD CONSTRAINT rag_v2_immutable_vertex_usage_reservation_api_key_binding_check
  CHECK (
    authentication_mode = 'VERTEX_EXPRESS_API_KEY'
    AND token_physical_call_cap = 0
    AND generate_content_physical_call_cap = 1
  );
ALTER TABLE public.rag_v2_immutable_vertex_usage_reservations
  ALTER COLUMN authentication_mode DROP DEFAULT;

ALTER TABLE public.rag_v2_immutable_vertex_usage_outcomes
  DROP CONSTRAINT rag_v2_immutable_vertex_usage_outcome_shape_v2_check;
ALTER TABLE public.rag_v2_immutable_vertex_usage_outcomes
  ADD CONSTRAINT rag_v2_immutable_vertex_usage_outcome_api_key_shape_check
  CHECK (
    (
      state = 'COMMITTED'
      AND physical_token_call_count = 0
      AND physical_generate_content_call_count = 1
      AND prompt_token_count BETWEEN 0 AND 120000
      AND candidate_token_count BETWEEN 0 AND 32768
      AND total_token_count = prompt_token_count + candidate_token_count
      AND actual_cost_microusd BETWEEN 0 AND 1000000000
    )
    OR (
      state = 'UNKNOWN_BILLING'
      AND physical_token_call_count = 0
      AND physical_generate_content_call_count BETWEEN 0 AND 1
      AND prompt_token_count IS NULL
      AND candidate_token_count IS NULL
      AND total_token_count IS NULL
      AND actual_cost_microusd IS NULL
    )
  );

REVOKE ALL PRIVILEGES ON FUNCTION public.claim_rag_v2_immutable_vertex_token_attempt(text, text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION public.claim_rag_v2_immutable_vertex_token_attempt(text, text) FROM decision_app;
DROP FUNCTION public.claim_rag_v2_immutable_vertex_token_attempt(text, text);

REVOKE ALL PRIVILEGES ON FUNCTION public.reserve_rag_v2_immutable_vertex_usage(
  text, text, text, text, text, text, text, text, text, text, text, timestamptz,
  integer, integer, integer, bigint, bigint, bigint, integer, integer, jsonb
) FROM decision_app;
DROP FUNCTION public.reserve_rag_v2_immutable_vertex_usage(
  text, text, text, text, text, text, text, text, text, text, text, timestamptz,
  integer, integer, integer, bigint, bigint, bigint, integer, integer, jsonb
);

CREATE FUNCTION public.reserve_rag_v2_immutable_vertex_usage(
  p_usage_event_id text,
  p_owner_user_id text,
  p_request_id text,
  p_scope_claim_id text,
  p_question_fingerprint_hmac text,
  p_answer_mode text,
  p_consent_event_id text,
  p_packet_sha256 text,
  p_nonce_sha256 text,
  p_policy_sha256 text,
  p_processor_set_sha256 text,
  p_expires_at timestamptz,
  p_input_token_cap integer,
  p_output_token_cap integer,
  p_input_byte_cap integer,
  p_cost_cap_microusd bigint,
  p_input_microusd_per_token bigint,
  p_output_microusd_per_token bigint,
  p_token_physical_call_cap integer,
  p_generate_content_physical_call_cap integer,
  p_authentication_mode text,
  p_evidence_manifest jsonb
)
RETURNS TABLE (
  usage_event_id text,
  expires_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $reserve_rag_v2_immutable_vertex_usage$
DECLARE
  reservation public.rag_v2_immutable_vertex_usage_reservations%ROWTYPE;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_usage_event_id !~ '^rgr_vgu_[0-9a-f]{32}$'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_request_id !~ '^req_[A-Za-z0-9_-]{12,96}$'
     OR p_scope_claim_id !~ '^rvs_[0-9a-f]{32}$'
     OR p_question_fingerprint_hmac !~ '^[0-9a-f]{64}$'
     OR p_answer_mode NOT IN ('CONCISE', 'DETAILED')
     OR p_consent_event_id !~ '^rce_[A-Za-z0-9_-]{12,96}$'
     OR p_packet_sha256 !~ '^[0-9a-f]{64}$'
     OR p_nonce_sha256 !~ '^[0-9a-f]{64}$'
     OR p_policy_sha256 !~ '^[0-9a-f]{64}$'
     OR p_processor_set_sha256 !~ '^[0-9a-f]{64}$'
     OR p_expires_at <= statement_timestamp()
     OR p_expires_at > statement_timestamp() + interval '5 minutes'
     OR p_input_token_cap NOT BETWEEN 1 AND 120000
     OR p_output_token_cap NOT BETWEEN 1 AND 32768
     OR p_input_byte_cap NOT BETWEEN 1 AND 60000
     OR p_input_byte_cap + 512 > p_input_token_cap
     OR p_cost_cap_microusd NOT BETWEEN 1 AND 1000000000
     OR p_input_microusd_per_token NOT BETWEEN 1 AND 1000000
     OR p_output_microusd_per_token NOT BETWEEN 1 AND 1000000
     OR p_token_physical_call_cap <> 0
     OR p_generate_content_physical_call_cap <> 1
     OR p_authentication_mode <> 'VERTEX_EXPRESS_API_KEY'
     OR NOT public.rag_v2_immutable_vertex_evidence_manifest_is_valid(p_evidence_manifest)
     OR p_input_token_cap::bigint * p_input_microusd_per_token
          + p_output_token_cap::bigint * p_output_microusd_per_token > p_cost_cap_microusd THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex API-key usage reservation arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-vertex-usage-reservation|' || p_packet_sha256, 0)
  );
  INSERT INTO public.rag_v2_immutable_vertex_usage_reservations (
    usage_event_id, owner_user_id, request_id, scope_claim_id, question_fingerprint_hmac,
    answer_mode, consent_event_id, packet_sha256, nonce_sha256, policy_sha256, processor_set_sha256, expires_at,
    input_token_cap, output_token_cap, input_byte_cap, cost_cap_microusd,
    input_microusd_per_token, output_microusd_per_token,
    token_physical_call_cap, generate_content_physical_call_cap, authentication_mode, evidence_manifest
  ) VALUES (
    p_usage_event_id, p_owner_user_id, p_request_id, p_scope_claim_id, p_question_fingerprint_hmac,
    p_answer_mode, p_consent_event_id, p_packet_sha256, p_nonce_sha256, p_policy_sha256, p_processor_set_sha256, p_expires_at,
    p_input_token_cap, p_output_token_cap, p_input_byte_cap, p_cost_cap_microusd,
    p_input_microusd_per_token, p_output_microusd_per_token,
    p_token_physical_call_cap, p_generate_content_physical_call_cap, p_authentication_mode, p_evidence_manifest
  ) RETURNING * INTO reservation;
  PERFORM public.assert_rag_v2_immutable_vertex_reservation_is_current(reservation);
  RETURN QUERY SELECT p_usage_event_id, p_expires_at;
END
$reserve_rag_v2_immutable_vertex_usage$;
ALTER FUNCTION public.reserve_rag_v2_immutable_vertex_usage(
  text, text, text, text, text, text, text, text, text, text, text, timestamptz,
  integer, integer, integer, bigint, bigint, bigint, integer, integer, text, jsonb
) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.reserve_rag_v2_immutable_vertex_usage(
  text, text, text, text, text, text, text, text, text, text, text, timestamptz,
  integer, integer, integer, bigint, bigint, bigint, integer, integer, text, jsonb
) FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.claim_rag_v2_immutable_vertex_generate_content_attempt(
  p_usage_event_id text,
  p_owner_user_id text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $claim_rag_v2_immutable_vertex_generate_content_attempt$
DECLARE
  reservation public.rag_v2_immutable_vertex_usage_reservations%ROWTYPE;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_usage_event_id !~ '^rgr_vgu_[0-9a-f]{32}$'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$' THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex generate claim arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  SELECT * INTO reservation
  FROM public.rag_v2_immutable_vertex_usage_reservations
  WHERE usage_event_id = p_usage_event_id
    AND owner_user_id = p_owner_user_id
  FOR UPDATE;
  IF NOT FOUND
     OR reservation.expires_at <= statement_timestamp()
     OR reservation.authentication_mode <> 'VERTEX_EXPRESS_API_KEY'
     OR reservation.token_physical_call_cap <> 0
     OR reservation.generate_content_physical_call_cap <> 1
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_generate_content_attempts WHERE usage_event_id = p_usage_event_id)
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_outcomes WHERE usage_event_id = p_usage_event_id) THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex generate claim is unavailable'
      USING ERRCODE = '55000';
  END IF;
  PERFORM public.assert_rag_v2_immutable_vertex_reservation_is_current(reservation);
  INSERT INTO public.rag_v2_immutable_vertex_usage_generate_content_attempts (
    usage_event_id, state, physical_generate_content_call_count
  ) VALUES (p_usage_event_id, 'ATTEMPTED', 1);
END
$claim_rag_v2_immutable_vertex_generate_content_attempt$;
ALTER FUNCTION public.claim_rag_v2_immutable_vertex_generate_content_attempt(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.claim_rag_v2_immutable_vertex_generate_content_attempt(text, text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.commit_rag_v2_immutable_vertex_usage(
  p_usage_event_id text,
  p_owner_user_id text,
  p_prompt_token_count integer,
  p_candidate_token_count integer,
  p_total_token_count integer
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $commit_rag_v2_immutable_vertex_usage$
DECLARE
  reservation public.rag_v2_immutable_vertex_usage_reservations%ROWTYPE;
  calculated_cost bigint;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_usage_event_id !~ '^rgr_vgu_[0-9a-f]{32}$'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_prompt_token_count NOT BETWEEN 0 AND 120000
     OR p_candidate_token_count NOT BETWEEN 0 AND 32768
     OR p_total_token_count <> p_prompt_token_count + p_candidate_token_count THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex usage commit arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  SELECT * INTO reservation
  FROM public.rag_v2_immutable_vertex_usage_reservations
  WHERE usage_event_id = p_usage_event_id
    AND owner_user_id = p_owner_user_id
  FOR UPDATE;
  IF NOT FOUND
     OR reservation.authentication_mode <> 'VERTEX_EXPRESS_API_KEY'
     OR reservation.token_physical_call_cap <> 0
     OR reservation.generate_content_physical_call_cap <> 1
     OR NOT EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_vertex_usage_generate_content_attempts
       WHERE usage_event_id = p_usage_event_id AND state = 'ATTEMPTED' AND physical_generate_content_call_count = 1
     )
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_outcomes WHERE usage_event_id = p_usage_event_id)
     OR p_prompt_token_count > reservation.input_token_cap
     OR p_candidate_token_count > reservation.output_token_cap THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex usage commit is unavailable'
      USING ERRCODE = '55000';
  END IF;
  calculated_cost := p_prompt_token_count::bigint * reservation.input_microusd_per_token
    + p_candidate_token_count::bigint * reservation.output_microusd_per_token;
  IF calculated_cost > reservation.cost_cap_microusd THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex usage commit exceeds the approved cost cap'
      USING ERRCODE = '55000';
  END IF;
  INSERT INTO public.rag_v2_immutable_vertex_usage_outcomes (
    usage_event_id, packet_sha256, state, physical_token_call_count, physical_generate_content_call_count,
    prompt_token_count, candidate_token_count, total_token_count, actual_cost_microusd
  ) VALUES (
    p_usage_event_id, reservation.packet_sha256, 'COMMITTED', 0, 1,
    p_prompt_token_count, p_candidate_token_count, p_total_token_count, calculated_cost
  );
END
$commit_rag_v2_immutable_vertex_usage$;
ALTER FUNCTION public.commit_rag_v2_immutable_vertex_usage(text, text, integer, integer, integer) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.commit_rag_v2_immutable_vertex_usage(text, text, integer, integer, integer) FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.mark_rag_v2_immutable_vertex_usage_unknown_billing(
  p_usage_event_id text,
  p_owner_user_id text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $mark_rag_v2_immutable_vertex_usage_unknown_billing$
DECLARE
  reservation public.rag_v2_immutable_vertex_usage_reservations%ROWTYPE;
  generate_count integer := 0;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_usage_event_id !~ '^rgr_vgu_[0-9a-f]{32}$'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$' THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex unknown billing arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  SELECT * INTO reservation
  FROM public.rag_v2_immutable_vertex_usage_reservations
  WHERE usage_event_id = p_usage_event_id
    AND owner_user_id = p_owner_user_id
  FOR UPDATE;
  IF NOT FOUND
     OR reservation.authentication_mode <> 'VERTEX_EXPRESS_API_KEY'
     OR reservation.token_physical_call_cap <> 0
     OR reservation.generate_content_physical_call_cap <> 1
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_outcomes WHERE usage_event_id = p_usage_event_id) THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex unknown billing is unavailable'
      USING ERRCODE = '55000';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.rag_v2_immutable_vertex_usage_generate_content_attempts
    WHERE usage_event_id = p_usage_event_id AND state = 'ATTEMPTED' AND physical_generate_content_call_count = 1
  ) THEN
    generate_count := 1;
  END IF;
  INSERT INTO public.rag_v2_immutable_vertex_usage_outcomes (
    usage_event_id, packet_sha256, state, physical_token_call_count, physical_generate_content_call_count,
    prompt_token_count, candidate_token_count, total_token_count, actual_cost_microusd
  ) VALUES (
    p_usage_event_id, reservation.packet_sha256, 'UNKNOWN_BILLING', 0, generate_count,
    NULL, NULL, NULL, NULL
  );
END
$mark_rag_v2_immutable_vertex_usage_unknown_billing$;
ALTER FUNCTION public.mark_rag_v2_immutable_vertex_usage_unknown_billing(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.mark_rag_v2_immutable_vertex_usage_unknown_billing(text, text) FROM PUBLIC;

DO $rag_v2_vertex_api_key_only_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    GRANT EXECUTE ON FUNCTION public.reserve_rag_v2_immutable_vertex_usage(
      text, text, text, text, text, text, text, text, text, text, text, timestamptz,
      integer, integer, integer, bigint, bigint, bigint, integer, integer, text, jsonb
    ) TO decision_app;
    GRANT EXECUTE ON FUNCTION public.claim_rag_v2_immutable_vertex_generate_content_attempt(text, text) TO decision_app;
    GRANT EXECUTE ON FUNCTION public.commit_rag_v2_immutable_vertex_usage(text, text, integer, integer, integer) TO decision_app;
    GRANT EXECUTE ON FUNCTION public.mark_rag_v2_immutable_vertex_usage_unknown_billing(text, text) TO decision_app;
  END IF;
END
$rag_v2_vertex_api_key_only_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION public.reserve_rag_v2_immutable_vertex_usage(
  text, text, text, text, text, text, text, text, text, text, text, timestamptz,
  integer, integer, integer, bigint, bigint, bigint, integer, integer, text, jsonb
) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION public.claim_rag_v2_immutable_vertex_generate_content_attempt(text, text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION public.commit_rag_v2_immutable_vertex_usage(text, text, integer, integer, integer) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION public.mark_rag_v2_immutable_vertex_usage_unknown_billing(text, text) FROM PUBLIC;
