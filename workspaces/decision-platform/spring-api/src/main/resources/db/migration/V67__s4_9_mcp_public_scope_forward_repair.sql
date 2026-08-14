-- S4.9 MCP public scope는 OAuth owner 권한을 확인하기 전에 개인 overlay를 읽지 않는다.
-- 기존 claim과 V66 row는 보존하고 MCP 전용 15분 claim만 명시적으로 구분한다.

ALTER TABLE public.rag_v2_retrieval_scope_claims
  ADD COLUMN owner_scope_authorized boolean NOT NULL DEFAULT true;

ALTER TABLE public.rag_v2_retrieval_scope_claims
  DROP CONSTRAINT rag_v2_retrieval_scope_expiry_check;
ALTER TABLE public.rag_v2_retrieval_scope_claims
  ADD CONSTRAINT rag_v2_retrieval_scope_expiry_check
  CHECK (
    expires_at = created_at + interval '2 minutes'
    OR expires_at = created_at + interval '5 minutes'
    OR expires_at = created_at + interval '15 minutes'
  );

ALTER TABLE public.rag_v2_retrieval_scope_claims
  ADD CONSTRAINT rag_v2_retrieval_scope_owner_authority_check
  CHECK (
    owner_scope_authorized
    OR (
      owner_private_generation_id IS NULL
      AND owner_bundle_id IS NULL
      AND owner_embedding_profile_id IS NULL
      AND owner_pointer_version = 0
    )
  );

CREATE POLICY rag_v2_retrieval_scope_claim_mcp_ttl_update_policy
  ON public.rag_v2_retrieval_scope_claims
  FOR UPDATE
  USING (
    current_user = 'flyway'
    AND owner_user_id = current_setting('app.actor_user_id', true)
    AND expires_at = created_at + interval '2 minutes'
  )
  WITH CHECK (
    current_user = 'flyway'
    AND owner_user_id = current_setting('app.actor_user_id', true)
    AND expires_at = created_at + interval '15 minutes'
  );

CREATE FUNCTION public.issue_s4_9_mcp_retrieval_scope(
  p_owner_user_id text,
  p_session_id text,
  p_allowed_topics text[],
  p_include_owner boolean
)
RETURNS TABLE (
  scope_claim_id text,
  owner_user_id text,
  session_id text,
  exact30_generation_id text,
  oa112_generation_id text,
  owner_private_generation_id text,
  embedding_profile_id text,
  owner_embedding_profile_id text,
  policy_version bigint,
  allowed_topics text[],
  expires_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $issue_s4_9_mcp_retrieval_scope$
#variable_conflict use_column
DECLARE
  issued record;
  public_pointer public.rag_v2_immutable_public_bundle_pointers%ROWTYPE;
  generated_scope_claim_id text;
  claim_created_at timestamptz := transaction_timestamp();
  claim_expires_at timestamptz := transaction_timestamp() + interval '15 minutes';
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_session_id IS NULL
     OR char_length(p_session_id) NOT BETWEEN 16 AND 128
     OR p_session_id !~ '^[A-Za-z0-9._:-]+$'
     OR NOT public.rag_v2_immutable_retrieval_topics_are_valid(p_allowed_topics)
     OR NOT EXISTS (
       SELECT 1 FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'S4.9 MCP retrieval scope arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  IF p_include_owner THEN
    SELECT * INTO STRICT issued
    FROM public.issue_rag_v2_retrieval_scope_v2(
      p_owner_user_id,
      p_session_id,
      p_allowed_topics
    );

    RETURN QUERY
    UPDATE public.rag_v2_retrieval_scope_claims AS claim
    SET expires_at = claim.created_at + interval '15 minutes',
        owner_scope_authorized = true
    WHERE claim.scope_claim_id = issued.scope_claim_id
      AND claim.owner_user_id = p_owner_user_id
      AND claim.session_id = p_session_id
      AND claim.expires_at = claim.created_at + interval '2 minutes'
    RETURNING
      claim.scope_claim_id,
      claim.owner_user_id,
      claim.session_id,
      claim.exact30_generation_id,
      claim.oa112_generation_id,
      claim.owner_private_generation_id,
      claim.embedding_profile_id,
      claim.owner_embedding_profile_id,
      claim.policy_version,
      claim.allowed_topics,
      claim.expires_at;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'S4.9 MCP owner retrieval scope extension failed'
        USING ERRCODE = '55000';
    END IF;
    RETURN;
  END IF;

  -- 공개 전용 분기는 owner pointer/bundle/component table을 전혀 읽지 않는다.
  PERFORM set_config('app.rag_v2_retrieval_scope', 'enabled', true);
  SELECT * INTO public_pointer
  FROM public.rag_v2_immutable_public_bundle_pointers AS pointer
  WHERE pointer.state_id = 'default'
    AND pointer.state = 'ACTIVE';
  IF NOT FOUND
     OR NOT EXISTS (
       SELECT 1
       FROM public.rag_v2_immutable_component_generations AS exact_generation
       JOIN public.rag_v2_immutable_component_generations AS oa_generation
         ON oa_generation.component_generation_id = public_pointer.oa112_generation_id
        AND oa_generation.component_scope = 'OA112'
        AND oa_generation.owner_user_id IS NULL
        AND oa_generation.embedding_profile_id = public_pointer.embedding_profile_id
        AND oa_generation.state = 'ACTIVE'
        AND oa_generation.evaluation_status = 'PASSED'
       WHERE exact_generation.component_generation_id = public_pointer.exact30_generation_id
         AND exact_generation.component_scope = 'EXACT30'
         AND exact_generation.owner_user_id IS NULL
         AND exact_generation.embedding_profile_id = public_pointer.embedding_profile_id
         AND exact_generation.state = 'ACTIVE'
         AND exact_generation.evaluation_status = 'PASSED'
     ) THEN
    RAISE EXCEPTION 'S4.9 MCP public bundle is not active'
      USING ERRCODE = '55000';
  END IF;

  generated_scope_claim_id :=
    'rvs_' || substr(
      encode(
        digest(
          gen_random_bytes(32) || convert_to(
            concat_ws(
              E'\n',
              p_owner_user_id,
              p_session_id,
              public_pointer.exact30_generation_id,
              public_pointer.oa112_generation_id,
              public_pointer.embedding_profile_id,
              claim_created_at::text
            ),
            'UTF8'
          ),
          'sha256'
        ),
        'hex'
      ),
      1,
      32
    );

  INSERT INTO public.rag_v2_retrieval_scope_claims (
    scope_claim_id, owner_user_id, session_id, allowed_topics,
    exact30_generation_id, oa112_generation_id, owner_private_generation_id,
    owner_bundle_id, embedding_profile_id, owner_embedding_profile_id,
    public_pointer_version, owner_pointer_version, owner_scope_authorized,
    policy_version, created_at, expires_at
  ) VALUES (
    generated_scope_claim_id, p_owner_user_id, p_session_id, p_allowed_topics,
    public_pointer.exact30_generation_id, public_pointer.oa112_generation_id, NULL,
    NULL, public_pointer.embedding_profile_id, NULL,
    public_pointer.pointer_version, 0, false,
    1, claim_created_at, claim_expires_at
  );

  RETURN QUERY SELECT
    generated_scope_claim_id,
    p_owner_user_id,
    p_session_id,
    public_pointer.exact30_generation_id,
    public_pointer.oa112_generation_id,
    NULL::text,
    public_pointer.embedding_profile_id,
    NULL::text,
    1::bigint,
    p_allowed_topics,
    claim_expires_at;
END;
$issue_s4_9_mcp_retrieval_scope$;
ALTER FUNCTION public.issue_s4_9_mcp_retrieval_scope(text,text,text[],boolean) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.issue_s4_9_mcp_retrieval_scope(text,text,text[],boolean) FROM PUBLIC;

-- Public-only claim은 개인 pointer drift와 무관하게 public generation만 계속 검증한다.
DO $repair_s4_9_mcp_public_scope_guards$
DECLARE
  definition text;
  repaired text;
  canonical_old constant text := $old$
  SELECT * INTO owner_pointer
  FROM public.rag_v2_immutable_owner_bundle_pointers AS pointer
  WHERE pointer.owner_user_id = p_owner_user_id;
  IF claim_row.owner_private_generation_id IS NULL THEN
    IF NOT public.rag_v2_immutable_empty_owner_scope_is_current(
      p_owner_user_id,
      claim_row.owner_pointer_version,
      claim_row.exact30_generation_id,
      claim_row.oa112_generation_id,
      claim_row.embedding_profile_id
    ) THEN
      RAISE EXCEPTION 'immutable RAG v2 owner retrieval scope changed'
        USING ERRCODE = '55000';
    END IF;
  ELSE
$old$;
  canonical_new constant text := $new$
  IF NOT claim_row.owner_scope_authorized THEN
    IF claim_row.owner_private_generation_id IS NOT NULL
       OR claim_row.owner_bundle_id IS NOT NULL
       OR claim_row.owner_embedding_profile_id IS NOT NULL
       OR claim_row.owner_pointer_version <> 0 THEN
      RAISE EXCEPTION 'S4.9 MCP public retrieval scope is invalid'
        USING ERRCODE = '55000';
    END IF;
  ELSE
    SELECT * INTO owner_pointer
    FROM public.rag_v2_immutable_owner_bundle_pointers AS pointer
    WHERE pointer.owner_user_id = p_owner_user_id;
    IF claim_row.owner_private_generation_id IS NULL THEN
      IF NOT public.rag_v2_immutable_empty_owner_scope_is_current(
        p_owner_user_id,
        claim_row.owner_pointer_version,
        claim_row.exact30_generation_id,
        claim_row.oa112_generation_id,
        claim_row.embedding_profile_id
      ) THEN
        RAISE EXCEPTION 'immutable RAG v2 owner retrieval scope changed'
          USING ERRCODE = '55000';
      END IF;
    ELSE
$new$;
  canonical_end_old constant text := $old$
    END IF;
  END IF;

  FOR citation_item IN SELECT value FROM jsonb_array_elements(p_citations)
$old$;
  canonical_end_new constant text := $new$
      END IF;
    END IF;
  END IF;

  FOR citation_item IN SELECT value FROM jsonb_array_elements(p_citations)
$new$;
  resolve_old constant text := $old$
  SELECT * INTO owner_pointer
  FROM public.rag_v2_immutable_owner_bundle_pointers
  WHERE owner_user_id = p_owner_user_id;
  IF claim_row.owner_private_generation_id IS NULL THEN
$old$;
  resolve_new constant text := $new$
  IF NOT claim_row.owner_scope_authorized THEN
    IF claim_row.owner_private_generation_id IS NOT NULL
       OR claim_row.owner_bundle_id IS NOT NULL
       OR claim_row.owner_embedding_profile_id IS NOT NULL
       OR claim_row.owner_pointer_version <> 0 THEN
      RAISE EXCEPTION 'S4.9 MCP public retrieval scope is invalid'
        USING ERRCODE = '55000';
    END IF;
  ELSE
    SELECT * INTO owner_pointer
    FROM public.rag_v2_immutable_owner_bundle_pointers
    WHERE owner_user_id = p_owner_user_id;
    IF claim_row.owner_private_generation_id IS NULL THEN
$new$;
  resolve_end_old constant text := $old$
    END IF;
  END IF;

  RETURN QUERY
$old$;
  resolve_end_new constant text := $new$
      END IF;
    END IF;
  END IF;

  RETURN QUERY
$new$;
BEGIN
  SELECT pg_get_functiondef(
    'public.canonicalize_rag_v2_immutable_retrieval_citations(text,text,text,jsonb)'::regprocedure
  ) INTO definition;
  repaired := replace(definition, canonical_old, canonical_new);
  repaired := replace(repaired, canonical_end_old, canonical_end_new);
  repaired := replace(
    repaired,
    'generation.embedding_profile_id = claim_row.embedding_profile_id',
    'generation.embedding_profile_id = claim_row.owner_embedding_profile_id'
  );
  -- 부분 문자열 치환은 exact_generation/oa_generation도 함께 잡으므로 공개 component 검증은
  -- 반드시 공개 profile로 복원한다. OWNER_PRIVATE generation만 owner profile을 사용한다.
  repaired := replace(
    repaired,
    'exact_generation.embedding_profile_id = claim_row.owner_embedding_profile_id',
    'exact_generation.embedding_profile_id = claim_row.embedding_profile_id'
  );
  repaired := replace(
    repaired,
    'oa_generation.embedding_profile_id = claim_row.owner_embedding_profile_id',
    'oa_generation.embedding_profile_id = claim_row.embedding_profile_id'
  );
  repaired := replace(
    repaired,
    'embedding.embedding_profile_id = claim_row.embedding_profile_id',
    $$embedding.embedding_profile_id = CASE
            WHEN membership.component_scope = 'OWNER_PRIVATE' THEN claim_row.owner_embedding_profile_id
            ELSE claim_row.embedding_profile_id
          END$$
  );
  IF repaired = definition THEN
    RAISE EXCEPTION 'V67 canonical citation guard source is not the expected V62 definition';
  END IF;
  EXECUTE repaired;

  SELECT pg_get_functiondef(
    'public.resolve_rag_v2_retrieval_scope_v2(text,text,text)'::regprocedure
  ) INTO definition;
  repaired := replace(definition, resolve_old, resolve_new);
  repaired := replace(repaired, resolve_end_old, resolve_end_new);
  IF repaired = definition THEN
    RAISE EXCEPTION 'V67 retrieval resolver source is not the expected V60 definition';
  END IF;
  EXECUTE repaired;

  SELECT pg_get_functiondef(
    'public.read_rag_v2_vertex_generation_evidence(text,text,text,jsonb)'::regprocedure
  ) INTO definition;
  repaired := replace(
    definition,
    'embedding.embedding_profile_id = claim_row.embedding_profile_id',
    $$embedding.embedding_profile_id = CASE
            WHEN membership.component_scope = 'OWNER_PRIVATE' THEN claim_row.owner_embedding_profile_id
            ELSE claim_row.embedding_profile_id
          END$$
  );
  IF repaired = definition THEN
    RAISE EXCEPTION 'V67 Vertex evidence profile source is not the expected V39 definition';
  END IF;
  EXECUTE repaired;
END
$repair_s4_9_mcp_public_scope_guards$;

ALTER FUNCTION public.canonicalize_rag_v2_immutable_retrieval_citations(text,text,text,jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.canonicalize_rag_v2_immutable_retrieval_citations(text,text,text,jsonb) FROM PUBLIC;
ALTER FUNCTION public.resolve_rag_v2_retrieval_scope_v2(text,text,text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.resolve_rag_v2_retrieval_scope_v2(text,text,text) FROM PUBLIC;
ALTER FUNCTION public.read_rag_v2_vertex_generation_evidence(text,text,text,jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.read_rag_v2_vertex_generation_evidence(text,text,text,jsonb) FROM PUBLIC;

DO $s4_9_mcp_scope_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    GRANT EXECUTE ON FUNCTION public.issue_s4_9_mcp_retrieval_scope(text,text,text[],boolean)
      TO decision_app;
  END IF;
END
$s4_9_mcp_scope_acl$;

REVOKE ALL PRIVILEGES ON TABLE public.rag_v2_retrieval_scope_claims FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE public.rag_v2_retrieval_scope_claims FROM decision_app;
