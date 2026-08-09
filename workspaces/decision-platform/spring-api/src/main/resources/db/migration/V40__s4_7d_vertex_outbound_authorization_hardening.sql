-- V40은 V39의 local Vertex boundary를 provider socket 직전 one-shot authorization으로 강화한다.
-- packet은 owner/request/scope/question HMAC/consent event에 결속되고 OAuth token과 generateContent는
-- 각각 한 번의 append-only attempt로 기록된다. raw prompt/response/token은 어느 table에도 저장하지 않는다.

DO $reject_unsafe_vertex_usage_rows$
BEGIN
  IF EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_reservations)
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_attempts)
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_outcomes) THEN
    RAISE EXCEPTION 'Pre-S5 Vertex hardening requires an empty pre-live usage ledger';
  END IF;
END
$reject_unsafe_vertex_usage_rows$;

REVOKE ALL PRIVILEGES ON FUNCTION reserve_rag_v2_immutable_vertex_usage(
  text, text, text, text, text, text, text, timestamptz, integer, integer, integer, bigint, bigint, bigint
) FROM decision_app;
REVOKE ALL PRIVILEGES ON FUNCTION claim_rag_v2_immutable_vertex_usage_attempt(text) FROM decision_app;
REVOKE ALL PRIVILEGES ON FUNCTION commit_rag_v2_immutable_vertex_usage(text, integer, integer, integer) FROM decision_app;
REVOKE ALL PRIVILEGES ON FUNCTION mark_rag_v2_immutable_vertex_usage_unknown_billing(text) FROM decision_app;
DROP FUNCTION reserve_rag_v2_immutable_vertex_usage(
  text, text, text, text, text, text, text, timestamptz, integer, integer, integer, bigint, bigint, bigint
);
DROP FUNCTION claim_rag_v2_immutable_vertex_usage_attempt(text);
DROP FUNCTION commit_rag_v2_immutable_vertex_usage(text, integer, integer, integer);
DROP FUNCTION mark_rag_v2_immutable_vertex_usage_unknown_billing(text);

ALTER TABLE public.rag_v2_immutable_vertex_usage_reservations
  ADD COLUMN scope_claim_id text NOT NULL,
  ADD COLUMN consent_event_id text NOT NULL,
  ADD COLUMN policy_sha256 text NOT NULL,
  ADD COLUMN answer_mode text NOT NULL,
  ADD COLUMN token_physical_call_cap integer NOT NULL DEFAULT 1,
  ADD COLUMN generate_content_physical_call_cap integer NOT NULL DEFAULT 1;
ALTER TABLE public.rag_v2_immutable_vertex_usage_reservations
  ADD CONSTRAINT rag_v2_immutable_vertex_usage_reservation_binding_check
  CHECK (
    scope_claim_id ~ '^rvs_[0-9a-f]{32}$'
    AND consent_event_id ~ '^rce_[A-Za-z0-9_-]{12,96}$'
    AND policy_sha256 ~ '^[0-9a-f]{64}$'
    AND answer_mode IN ('CONCISE', 'DETAILED')
    AND token_physical_call_cap = 1
    AND generate_content_physical_call_cap = 1
  );

ALTER TABLE public.rag_v2_immutable_vertex_usage_attempts
  RENAME TO rag_v2_immutable_vertex_usage_generate_content_attempts;

CREATE TABLE public.rag_v2_immutable_vertex_usage_token_attempts (
  usage_event_id text PRIMARY KEY
    REFERENCES public.rag_v2_immutable_vertex_usage_reservations (usage_event_id) ON DELETE RESTRICT,
  state text NOT NULL,
  physical_token_call_count integer NOT NULL,
  claimed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_vertex_usage_token_attempt_state_check
    CHECK (state = 'ATTEMPTED'),
  CONSTRAINT rag_v2_immutable_vertex_usage_token_attempt_physical_count_check
    CHECK (physical_token_call_count = 1)
);
ALTER TABLE public.rag_v2_immutable_vertex_usage_token_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rag_v2_immutable_vertex_usage_token_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY rag_v2_immutable_vertex_usage_token_attempt_flyway_write
  ON public.rag_v2_immutable_vertex_usage_token_attempts
  FOR ALL TO flyway USING (true) WITH CHECK (true);

ALTER TABLE public.rag_v2_immutable_vertex_usage_outcomes
  ADD COLUMN physical_token_call_count integer NOT NULL DEFAULT 0,
  ADD COLUMN physical_generate_content_call_count integer NOT NULL DEFAULT 0;
ALTER TABLE public.rag_v2_immutable_vertex_usage_outcomes
  DROP CONSTRAINT rag_v2_immutable_vertex_usage_outcome_shape_check;
ALTER TABLE public.rag_v2_immutable_vertex_usage_outcomes
  ADD CONSTRAINT rag_v2_immutable_vertex_usage_outcome_shape_v2_check
  CHECK (
    (
      state = 'COMMITTED'
      AND physical_token_call_count = 1
      AND physical_generate_content_call_count = 1
      AND prompt_token_count BETWEEN 0 AND 120000
      AND candidate_token_count BETWEEN 0 AND 32768
      AND total_token_count = prompt_token_count + candidate_token_count
      AND actual_cost_microusd BETWEEN 0 AND 1000000000
    )
    OR (
      state = 'UNKNOWN_BILLING'
      AND physical_token_call_count BETWEEN 0 AND 1
      AND physical_generate_content_call_count BETWEEN 0 AND 1
      AND physical_generate_content_call_count <= physical_token_call_count
      AND prompt_token_count IS NULL
      AND candidate_token_count IS NULL
      AND total_token_count IS NULL
      AND actual_cost_microusd IS NULL
    )
  );

CREATE TRIGGER rag_v2_immutable_vertex_usage_token_attempts_append_only
BEFORE UPDATE OR DELETE ON public.rag_v2_immutable_vertex_usage_token_attempts
FOR EACH ROW EXECUTE FUNCTION public.reject_rag_v2_immutable_vertex_usage_mutation();

REVOKE ALL PRIVILEGES ON TABLE
  public.rag_v2_immutable_vertex_usage_reservations,
  public.rag_v2_immutable_vertex_usage_generate_content_attempts,
  public.rag_v2_immutable_vertex_usage_token_attempts,
  public.rag_v2_immutable_vertex_usage_outcomes
FROM decision_app;

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
  p_generate_content_physical_call_cap integer
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
  effective_consent public.rag_v2_immutable_consent_events%ROWTYPE;
  scope_row public.rag_v2_retrieval_scope_claims%ROWTYPE;
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
     OR p_token_physical_call_cap <> 1
     OR p_generate_content_physical_call_cap <> 1
     OR p_input_token_cap::bigint * p_input_microusd_per_token
          + p_output_token_cap::bigint * p_output_microusd_per_token > p_cost_cap_microusd THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex usage reservation arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  -- GRANT/revoke writer와 같은 owner lock으로 linearize한다. 이 reservation commit이 outbound authorization point다.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-consent|' || p_owner_user_id, 0)
  );
  SELECT * INTO effective_consent
  FROM public.rag_v2_immutable_consent_events AS event
  WHERE event.owner_user_id = p_owner_user_id
    AND event.public_consent_event_id IS NOT NULL
    AND event.policy_digest IS NOT NULL
    AND event.processor_set_digest IS NOT NULL
  ORDER BY event.created_at DESC, event.consent_event_id DESC
  LIMIT 1;
  IF NOT FOUND
     OR effective_consent.action <> 'GRANT'
     OR effective_consent.public_consent_event_id IS DISTINCT FROM p_consent_event_id
     OR effective_consent.policy_digest IS DISTINCT FROM p_policy_sha256
     OR effective_consent.processor_set_digest IS DISTINCT FROM p_processor_set_sha256 THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex consent is not currently granted'
      USING ERRCODE = '55000';
  END IF;

  SELECT * INTO scope_row
  FROM public.rag_v2_retrieval_scope_claims AS scope
  WHERE scope.scope_claim_id = p_scope_claim_id
    AND scope.owner_user_id = p_owner_user_id
    AND scope.session_id = p_request_id
    AND scope.expires_at > statement_timestamp();
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex retrieval scope is unavailable'
      USING ERRCODE = '55000';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-vertex-usage-reservation|' || p_packet_sha256, 0)
  );
  INSERT INTO public.rag_v2_immutable_vertex_usage_reservations (
    usage_event_id, owner_user_id, request_id, scope_claim_id, question_fingerprint_hmac,
    answer_mode, consent_event_id, packet_sha256, nonce_sha256, policy_sha256, processor_set_sha256, expires_at,
    input_token_cap, output_token_cap, input_byte_cap, cost_cap_microusd,
    input_microusd_per_token, output_microusd_per_token,
    token_physical_call_cap, generate_content_physical_call_cap
  ) VALUES (
    p_usage_event_id, p_owner_user_id, p_request_id, p_scope_claim_id, p_question_fingerprint_hmac,
    p_answer_mode, p_consent_event_id, p_packet_sha256, p_nonce_sha256, p_policy_sha256, p_processor_set_sha256, p_expires_at,
    p_input_token_cap, p_output_token_cap, p_input_byte_cap, p_cost_cap_microusd,
    p_input_microusd_per_token, p_output_microusd_per_token,
    p_token_physical_call_cap, p_generate_content_physical_call_cap
  );
  RETURN QUERY SELECT p_usage_event_id, p_expires_at;
END
$reserve_rag_v2_immutable_vertex_usage$;
ALTER FUNCTION public.reserve_rag_v2_immutable_vertex_usage(
  text, text, text, text, text, text, text, text, text, text, text, timestamptz,
  integer, integer, integer, bigint, bigint, bigint, integer, integer
) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.reserve_rag_v2_immutable_vertex_usage(
  text, text, text, text, text, text, text, text, text, text, text, timestamptz,
  integer, integer, integer, bigint, bigint, bigint, integer, integer
) FROM PUBLIC;

CREATE FUNCTION public.claim_rag_v2_immutable_vertex_token_attempt(
  p_usage_event_id text,
  p_owner_user_id text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $claim_rag_v2_immutable_vertex_token_attempt$
DECLARE
  reservation public.rag_v2_immutable_vertex_usage_reservations%ROWTYPE;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_usage_event_id !~ '^rgr_vgu_[0-9a-f]{32}$'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$' THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex token claim arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  SELECT * INTO reservation
  FROM public.rag_v2_immutable_vertex_usage_reservations
  WHERE usage_event_id = p_usage_event_id
    AND owner_user_id = p_owner_user_id
  FOR UPDATE;
  IF NOT FOUND
     OR reservation.expires_at <= statement_timestamp()
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_token_attempts WHERE usage_event_id = p_usage_event_id)
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_generate_content_attempts WHERE usage_event_id = p_usage_event_id)
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_outcomes WHERE usage_event_id = p_usage_event_id) THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex token claim is unavailable'
      USING ERRCODE = '55000';
  END IF;
  INSERT INTO public.rag_v2_immutable_vertex_usage_token_attempts (
    usage_event_id, state, physical_token_call_count
  ) VALUES (p_usage_event_id, 'ATTEMPTED', 1);
END
$claim_rag_v2_immutable_vertex_token_attempt$;
ALTER FUNCTION public.claim_rag_v2_immutable_vertex_token_attempt(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.claim_rag_v2_immutable_vertex_token_attempt(text, text) FROM PUBLIC;

CREATE FUNCTION public.claim_rag_v2_immutable_vertex_generate_content_attempt(
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
     OR NOT EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_vertex_usage_token_attempts
       WHERE usage_event_id = p_usage_event_id AND state = 'ATTEMPTED' AND physical_token_call_count = 1
     )
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_generate_content_attempts WHERE usage_event_id = p_usage_event_id)
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_outcomes WHERE usage_event_id = p_usage_event_id) THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex generate claim is unavailable'
      USING ERRCODE = '55000';
  END IF;
  INSERT INTO public.rag_v2_immutable_vertex_usage_generate_content_attempts (
    usage_event_id, state, physical_generate_content_call_count
  ) VALUES (p_usage_event_id, 'ATTEMPTED', 1);
END
$claim_rag_v2_immutable_vertex_generate_content_attempt$;
ALTER FUNCTION public.claim_rag_v2_immutable_vertex_generate_content_attempt(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.claim_rag_v2_immutable_vertex_generate_content_attempt(text, text) FROM PUBLIC;

CREATE FUNCTION public.commit_rag_v2_immutable_vertex_usage(
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
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex usage commit is unavailable'
      USING ERRCODE = '55000';
  END IF;
  calculated_cost := p_prompt_token_count::bigint * reservation.input_microusd_per_token
    + p_candidate_token_count::bigint * reservation.output_microusd_per_token;
  IF NOT EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_vertex_usage_token_attempts
       WHERE usage_event_id = p_usage_event_id AND state = 'ATTEMPTED' AND physical_token_call_count = 1
     )
     OR NOT EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_vertex_usage_generate_content_attempts
       WHERE usage_event_id = p_usage_event_id AND state = 'ATTEMPTED' AND physical_generate_content_call_count = 1
     )
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_outcomes WHERE usage_event_id = p_usage_event_id)
     OR p_prompt_token_count > reservation.input_token_cap
     OR p_candidate_token_count > reservation.output_token_cap
     OR calculated_cost > reservation.cost_cap_microusd THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex usage commit is unavailable'
      USING ERRCODE = '55000';
  END IF;
  INSERT INTO public.rag_v2_immutable_vertex_usage_outcomes (
    usage_event_id, packet_sha256, state, physical_token_call_count, physical_generate_content_call_count,
    prompt_token_count, candidate_token_count, total_token_count, actual_cost_microusd
  ) VALUES (
    p_usage_event_id, reservation.packet_sha256, 'COMMITTED', 1, 1,
    p_prompt_token_count, p_candidate_token_count, p_total_token_count, calculated_cost
  );
END
$commit_rag_v2_immutable_vertex_usage$;
ALTER FUNCTION public.commit_rag_v2_immutable_vertex_usage(text, text, integer, integer, integer) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.commit_rag_v2_immutable_vertex_usage(text, text, integer, integer, integer) FROM PUBLIC;

CREATE FUNCTION public.mark_rag_v2_immutable_vertex_usage_unknown_billing(
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
  token_count integer := 0;
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
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_outcomes WHERE usage_event_id = p_usage_event_id) THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex unknown billing is unavailable'
      USING ERRCODE = '55000';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.rag_v2_immutable_vertex_usage_token_attempts
    WHERE usage_event_id = p_usage_event_id AND state = 'ATTEMPTED' AND physical_token_call_count = 1
  ) THEN
    token_count := 1;
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
    p_usage_event_id, reservation.packet_sha256, 'UNKNOWN_BILLING', token_count, generate_count,
    NULL, NULL, NULL, NULL
  );
END
$mark_rag_v2_immutable_vertex_usage_unknown_billing$;
ALTER FUNCTION public.mark_rag_v2_immutable_vertex_usage_unknown_billing(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.mark_rag_v2_immutable_vertex_usage_unknown_billing(text, text) FROM PUBLIC;

DO $rag_v2_vertex_outbound_authorization_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    GRANT EXECUTE ON FUNCTION public.reserve_rag_v2_immutable_vertex_usage(
      text, text, text, text, text, text, text, text, text, text, text, timestamptz,
      integer, integer, integer, bigint, bigint, bigint, integer, integer
    ) TO decision_app;
    GRANT EXECUTE ON FUNCTION public.claim_rag_v2_immutable_vertex_token_attempt(text, text) TO decision_app;
    GRANT EXECUTE ON FUNCTION public.claim_rag_v2_immutable_vertex_generate_content_attempt(text, text) TO decision_app;
    GRANT EXECUTE ON FUNCTION public.commit_rag_v2_immutable_vertex_usage(text, text, integer, integer, integer) TO decision_app;
    GRANT EXECUTE ON FUNCTION public.mark_rag_v2_immutable_vertex_usage_unknown_billing(text, text) TO decision_app;
  END IF;
END
$rag_v2_vertex_outbound_authorization_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION public.reserve_rag_v2_immutable_vertex_usage(
  text, text, text, text, text, text, text, text, text, text, text, timestamptz,
  integer, integer, integer, bigint, bigint, bigint, integer, integer
) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION public.claim_rag_v2_immutable_vertex_token_attempt(text, text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION public.claim_rag_v2_immutable_vertex_generate_content_attempt(text, text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION public.commit_rag_v2_immutable_vertex_usage(text, text, integer, integer, integer) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION public.mark_rag_v2_immutable_vertex_usage_unknown_billing(text, text) FROM PUBLIC;
