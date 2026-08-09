-- V47은 V45의 aggregate metric claim을 V46의 append-only query-attempt ledger에 결속한다.
-- raw question, scope claim, provider response, vector는 계속 저장하지 않고 SHA-256 projection과
-- EXACT30/OA112 closed evaluation label만 사용한다. 일반 runtime query는 RUNTIME label로 분리된다.

ALTER TABLE public.rag_v2_immutable_public_voyage_component_evaluations
  ADD COLUMN evaluation_scope_claim_sha256 text;

ALTER TABLE public.rag_v2_immutable_public_voyage_component_evaluations
  ADD CONSTRAINT rag_v2_immutable_public_voyage_component_evaluation_scope_hash_check
  CHECK (
    evaluation_scope_claim_sha256 IS NULL
    OR evaluation_scope_claim_sha256 ~ '^[0-9a-f]{64}$'
  );

-- V45's original function remains an internal evaluator so its byte-stable acceptance calculation can be
-- reused.  The public writer capability is replaced by the wrapper below, which first proves the exact
-- successful one-shot query set in V46 and then delegates the component-state transition.
ALTER FUNCTION public.evaluate_rag_v2_immutable_public_voyage_component(text, jsonb)
  RENAME TO evaluate_rag_v2_immutable_public_voyage_component_v45_unlinked;

REVOKE ALL PRIVILEGES ON FUNCTION public.evaluate_rag_v2_immutable_public_voyage_component_v45_unlinked(text, jsonb)
  FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION public.evaluate_rag_v2_immutable_public_voyage_component_v45_unlinked(text, jsonb)
  FROM decision_rag_writer;

CREATE FUNCTION public.evaluate_rag_v2_immutable_public_voyage_component(
  p_component_generation_id text,
  p_evaluation jsonb
)
RETURNS TABLE (
  component_generation_id text,
  state text,
  source_count integer,
  chunk_count integer
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $evaluate_rag_v2_immutable_public_voyage_component$
DECLARE
  generation_scope text;
  expected_query_count integer;
  submitted_scope_claim_sha256 text;
  observed_committed_query_count integer;
  observed_distinct_query_count integer;
  observed_unresolved_attempt_count integer;
  evaluated_row record;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_component_generation_id !~ '^rgr_[0-9a-f]{32}$'
     OR p_evaluation IS NULL
     OR jsonb_typeof(p_evaluation) <> 'object'
     OR NOT (p_evaluation ? 'evaluationScopeClaimSha256')
     OR jsonb_typeof(p_evaluation -> 'evaluationScopeClaimSha256') <> 'string'
     OR p_evaluation ->> 'evaluationScopeClaimSha256' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage evaluation usage binding is invalid'
      USING ERRCODE = '22023';
  END IF;

  submitted_scope_claim_sha256 := p_evaluation ->> 'evaluationScopeClaimSha256';
  SELECT generation.component_scope INTO generation_scope
  FROM public.rag_v2_immutable_component_generations AS generation
  WHERE generation.component_generation_id = p_component_generation_id
    AND generation.owner_user_id IS NULL
    AND generation.embedding_profile_id = 'voyage_context_4_1024_v1'
  FOR UPDATE;
  IF generation_scope NOT IN ('EXACT30', 'OA112') THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage evaluation component is unavailable'
      USING ERRCODE = '23514';
  END IF;
  expected_query_count := CASE generation_scope WHEN 'EXACT30' THEN 10 WHEN 'OA112' THEN 112 ELSE -1 END;

  SELECT
    COUNT(*) FILTER (WHERE outcome.state = 'COMMITTED')::integer,
    COUNT(DISTINCT reservation.query_sha256) FILTER (WHERE outcome.state = 'COMMITTED')::integer,
    COUNT(*) FILTER (
      WHERE outcome.usage_event_id IS NULL OR outcome.state <> 'COMMITTED'
    )::integer
  INTO
    observed_committed_query_count,
    observed_distinct_query_count,
    observed_unresolved_attempt_count
  FROM public.rag_v2_immutable_voyage_query_usage_reservations AS reservation
  JOIN public.rag_v2_immutable_voyage_query_usage_attempts AS attempt
    ON attempt.usage_event_id = reservation.usage_event_id
   AND attempt.state = 'ATTEMPTED'
   AND attempt.physical_call_count = 1
  LEFT JOIN public.rag_v2_immutable_voyage_query_usage_outcomes AS outcome
    ON outcome.usage_event_id = reservation.usage_event_id
  WHERE reservation.scope_claim_sha256 = submitted_scope_claim_sha256
    AND reservation.evaluation_component_scope = generation_scope;

  IF observed_committed_query_count <> expected_query_count
     OR observed_distinct_query_count <> expected_query_count
     OR observed_unresolved_attempt_count <> 0 THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage evaluation query ledger is incomplete'
      USING ERRCODE = '23514';
  END IF;

  SELECT * INTO evaluated_row
  FROM public.evaluate_rag_v2_immutable_public_voyage_component_v45_unlinked(
    p_component_generation_id,
    p_evaluation - 'evaluationScopeClaimSha256'
  );
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage evaluation transition returned no receipt'
      USING ERRCODE = '23514';
  END IF;

  UPDATE public.rag_v2_immutable_public_voyage_component_evaluations AS evaluation
  SET evaluation_scope_claim_sha256 = submitted_scope_claim_sha256
  WHERE evaluation.component_generation_id = p_component_generation_id
    AND (
      evaluation.evaluation_scope_claim_sha256 IS NULL
      OR evaluation.evaluation_scope_claim_sha256 = submitted_scope_claim_sha256
    );
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage evaluation scope conflicts'
      USING ERRCODE = '23505';
  END IF;

  RETURN QUERY
  SELECT
    evaluated_row.component_generation_id,
    evaluated_row.state,
    evaluated_row.source_count,
    evaluated_row.chunk_count;
END;
$evaluate_rag_v2_immutable_public_voyage_component$;
ALTER FUNCTION public.evaluate_rag_v2_immutable_public_voyage_component(text, jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.evaluate_rag_v2_immutable_public_voyage_component(text, jsonb) FROM PUBLIC;

DO $rag_v2_immutable_public_voyage_evaluation_usage_link_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    GRANT EXECUTE ON FUNCTION public.evaluate_rag_v2_immutable_public_voyage_component(text, jsonb)
      TO decision_rag_writer;
  END IF;
END
$rag_v2_immutable_public_voyage_evaluation_usage_link_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION public.evaluate_rag_v2_immutable_public_voyage_component(text, jsonb) FROM PUBLIC;
