-- Public-only MCP research context는 owner pointer와 무관하게 public generation만 재검증한다.
CREATE OR REPLACE FUNCTION public.read_rag_v2_vertex_prepared_scope_v2(
  p_owner_user_id text,
  p_session_id text,
  p_scope_claim_id text,
  p_allowed_topics text[]
)
RETURNS TABLE (
  scope_claim_id text,
  exact30_generation_id text,
  oa112_generation_id text,
  owner_private_generation_id text,
  embedding_profile_id text,
  owner_embedding_profile_id text,
  policy_version bigint,
  expires_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $read_rag_v2_vertex_prepared_scope_v2$
#variable_conflict use_column
DECLARE
  claim_row public.rag_v2_retrieval_scope_claims%ROWTYPE;
  public_pointer public.rag_v2_immutable_public_bundle_pointers%ROWTYPE;
  owner_pointer public.rag_v2_immutable_owner_bundle_pointers%ROWTYPE;
  owner_bundle public.rag_v2_immutable_bundles%ROWTYPE;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_session_id IS NULL
     OR char_length(p_session_id) NOT BETWEEN 16 AND 128
     OR p_session_id !~ '^[A-Za-z0-9._:-]+$'
     OR p_scope_claim_id !~ '^rvs_[0-9a-f]{32}$'
     OR NOT public.rag_v2_immutable_retrieval_topics_are_valid(p_allowed_topics) THEN
    RAISE EXCEPTION 'immutable RAG v2 Vertex prepared scope arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  PERFORM set_config('app.rag_v2_retrieval_scope', 'enabled', true);
  SELECT * INTO claim_row
  FROM public.rag_v2_retrieval_scope_claims AS scope
  WHERE scope.scope_claim_id = p_scope_claim_id
    AND scope.owner_user_id = p_owner_user_id
    AND scope.session_id = p_session_id
    AND scope.allowed_topics = p_allowed_topics
    AND scope.expires_at > statement_timestamp();
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 Vertex prepared scope is absent or expired'
      USING ERRCODE = '55000';
  END IF;

  SELECT * INTO public_pointer
  FROM public.rag_v2_immutable_public_bundle_pointers AS pointer
  WHERE pointer.state_id = 'default'
    AND pointer.state = 'ACTIVE'
    AND pointer.pointer_version = claim_row.public_pointer_version
    AND pointer.exact30_generation_id = claim_row.exact30_generation_id
    AND pointer.oa112_generation_id = claim_row.oa112_generation_id
    AND pointer.embedding_profile_id = claim_row.embedding_profile_id;
  IF NOT FOUND
     OR NOT EXISTS (
       SELECT 1
       FROM public.rag_v2_immutable_component_generations AS exact_generation
       JOIN public.rag_v2_immutable_component_generations AS oa_generation
         ON oa_generation.component_generation_id = claim_row.oa112_generation_id
        AND oa_generation.component_scope = 'OA112'
        AND oa_generation.owner_user_id IS NULL
        AND oa_generation.embedding_profile_id = claim_row.embedding_profile_id
        AND oa_generation.state = 'ACTIVE'
        AND oa_generation.evaluation_status = 'PASSED'
       WHERE exact_generation.component_generation_id = claim_row.exact30_generation_id
         AND exact_generation.component_scope = 'EXACT30'
         AND exact_generation.owner_user_id IS NULL
         AND exact_generation.embedding_profile_id = claim_row.embedding_profile_id
         AND exact_generation.state = 'ACTIVE'
         AND exact_generation.evaluation_status = 'PASSED'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 Vertex prepared scope public pointer changed'
      USING ERRCODE = '55000';
  END IF;

  IF NOT claim_row.owner_scope_authorized THEN
    IF claim_row.owner_private_generation_id IS NOT NULL
       OR claim_row.owner_bundle_id IS NOT NULL
       OR claim_row.owner_embedding_profile_id IS NOT NULL
       OR claim_row.owner_pointer_version <> 0 THEN
      RAISE EXCEPTION 'S4.9 MCP public prepared scope is invalid'
        USING ERRCODE = '55000';
    END IF;
  ELSE
    SELECT * INTO owner_pointer
    FROM public.rag_v2_immutable_owner_bundle_pointers AS pointer
    WHERE pointer.owner_user_id = p_owner_user_id;
    IF claim_row.owner_private_generation_id IS NULL THEN
      IF (
        FOUND
        AND owner_pointer.bundle_version = claim_row.owner_pointer_version
        AND (
          owner_pointer.state = 'ABSENT'
          OR (
            owner_pointer.state = 'READY'
            AND EXISTS (
              SELECT 1
              FROM public.rag_v2_immutable_bundles AS empty_bundle
              JOIN public.rag_v2_immutable_component_generations AS empty_generation
                ON empty_generation.component_generation_id = empty_bundle.owner_private_generation_id
               AND empty_generation.owner_user_id = empty_bundle.owner_user_id
              WHERE empty_bundle.bundle_id = owner_pointer.active_bundle_id
                AND empty_bundle.owner_user_id = p_owner_user_id
                AND empty_bundle.state = 'ACTIVE'
                AND empty_bundle.owner_embedding_profile_id IS NULL
                AND empty_generation.actual_source_count = 0
                AND empty_generation.actual_chunk_count = 0
            )
          )
        )
      ) THEN
        NULL;
      ELSIF (FOUND OR claim_row.owner_pointer_version <> 0) THEN
        RAISE EXCEPTION 'immutable RAG v2 Vertex prepared scope owner pointer changed'
          USING ERRCODE = '55000';
      END IF;
    ELSE
      IF NOT FOUND
         OR owner_pointer.state <> 'READY'
         OR owner_pointer.active_bundle_id IS DISTINCT FROM claim_row.owner_bundle_id
         OR owner_pointer.bundle_version <> claim_row.owner_pointer_version THEN
        RAISE EXCEPTION 'immutable RAG v2 Vertex prepared scope owner bundle changed'
          USING ERRCODE = '55000';
      END IF;
      SELECT * INTO owner_bundle
      FROM public.rag_v2_immutable_bundles AS bundle
      WHERE bundle.bundle_id = claim_row.owner_bundle_id
        AND bundle.owner_user_id = p_owner_user_id
        AND bundle.owner_private_generation_id = claim_row.owner_private_generation_id
        AND bundle.exact30_generation_id = claim_row.exact30_generation_id
        AND bundle.oa112_generation_id = claim_row.oa112_generation_id
        AND bundle.embedding_profile_id = claim_row.embedding_profile_id
        AND bundle.owner_embedding_profile_id = claim_row.owner_embedding_profile_id
        AND bundle.state = 'ACTIVE';
      IF NOT FOUND
         OR NOT EXISTS (
           SELECT 1
           FROM public.rag_v2_immutable_component_generations AS owner_generation
           WHERE owner_generation.component_generation_id = claim_row.owner_private_generation_id
             AND owner_generation.component_scope = 'OWNER_PRIVATE'
             AND owner_generation.owner_user_id = p_owner_user_id
             AND owner_generation.embedding_profile_id = claim_row.owner_embedding_profile_id
             AND owner_generation.state = 'ACTIVE'
             AND owner_generation.evaluation_status = 'PASSED'
         ) THEN
        RAISE EXCEPTION 'immutable RAG v2 Vertex prepared scope owner component changed'
          USING ERRCODE = '55000';
      END IF;
    END IF;
  END IF;

  RETURN QUERY
  SELECT
    claim_row.scope_claim_id,
    claim_row.exact30_generation_id,
    claim_row.oa112_generation_id,
    claim_row.owner_private_generation_id,
    claim_row.embedding_profile_id,
    claim_row.owner_embedding_profile_id,
    claim_row.policy_version,
    claim_row.expires_at;
END
$read_rag_v2_vertex_prepared_scope_v2$;

ALTER FUNCTION public.read_rag_v2_vertex_prepared_scope_v2(text,text,text,text[]) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.read_rag_v2_vertex_prepared_scope_v2(text,text,text,text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_rag_v2_vertex_prepared_scope_v2(text,text,text,text[]) TO decision_app;

REVOKE ALL PRIVILEGES ON TABLE public.rag_v2_retrieval_scope_claims FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE public.rag_v2_retrieval_scope_claims FROM decision_app;
