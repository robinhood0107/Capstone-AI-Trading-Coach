-- Gemini 3 계열 totalTokenCount에는 hidden thought token이 포함될 수 있으므로 provider projection과 DB 원장을 정렬한다.
ALTER TABLE public.rag_v2_immutable_vertex_usage_outcomes
  DROP CONSTRAINT rag_v2_immutable_vertex_usage_outcome_service_account_shape_che;

ALTER TABLE public.rag_v2_immutable_vertex_usage_outcomes
  ADD CONSTRAINT rag_v2_vertex_usage_outcome_thought_shape_check
  CHECK (
    (
      state = 'COMMITTED'
      AND physical_token_call_count = 1
      AND physical_generate_content_call_count = 1
      AND prompt_token_count BETWEEN 0 AND 120000
      AND candidate_token_count BETWEEN 0 AND 32768
      AND total_token_count BETWEEN prompt_token_count + candidate_token_count AND 152768
      AND total_token_count - prompt_token_count - candidate_token_count BETWEEN 0 AND 32768
      AND actual_cost_microusd BETWEEN 0 AND 1000000000
    )
    OR
    (
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
     OR p_total_token_count NOT BETWEEN p_prompt_token_count + p_candidate_token_count AND 152768
     OR p_total_token_count - p_prompt_token_count - p_candidate_token_count NOT BETWEEN 0 AND 32768 THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex usage commit arguments are invalid' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO reservation
  FROM public.rag_v2_immutable_vertex_usage_reservations
  WHERE usage_event_id = p_usage_event_id AND owner_user_id = p_owner_user_id
  FOR UPDATE;
  IF NOT FOUND
     OR reservation.authentication_mode <> 'SERVICE_ACCOUNT_OAUTH'
     OR reservation.token_physical_call_cap <> 1
     OR reservation.generate_content_physical_call_cap <> 1
     OR NOT EXISTS (
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
     OR p_total_token_count - p_prompt_token_count > reservation.output_token_cap THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex usage commit is unavailable' USING ERRCODE = '55000';
  END IF;
  -- hidden thought token도 output 단가로 보수적으로 과금해 승인 cost cap을 우회하지 못하게 한다.
  calculated_cost := p_prompt_token_count::bigint * reservation.input_microusd_per_token
    + (p_total_token_count - p_prompt_token_count)::bigint * reservation.output_microusd_per_token;
  IF calculated_cost > reservation.cost_cap_microusd THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex usage commit exceeds the approved cost cap' USING ERRCODE = '55000';
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
GRANT EXECUTE ON FUNCTION public.commit_rag_v2_immutable_vertex_usage(text, text, integer, integer, integer) TO decision_app;
