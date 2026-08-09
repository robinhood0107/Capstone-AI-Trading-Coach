-- V34는 historical V24 status projection을 immutable public/owner pointer로 supersede한다.
-- provider transport, raw corpus 저장, 기존 V24~V33 migration byte 변경은 만들지 않는다.

CREATE OR REPLACE FUNCTION read_rag_v2_corpus_status(p_owner_user_id text)
RETURNS TABLE (
  state text,
  public_corpus_version text,
  private_overlay_state text,
  progress_percent integer,
  failure_code text
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $read_rag_v2_corpus_status$
DECLARE
  public_pointer public.rag_v2_immutable_public_bundle_pointers%ROWTYPE;
  owner_pointer public.rag_v2_immutable_owner_bundle_pointers%ROWTYPE;
  public_ready boolean := false;
  public_pointer_found boolean := false;
  owner_state text := 'ABSENT';
  owner_ready boolean := true;
  public_version text := 'immutable-v2-0';
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR NOT EXISTS (
       SELECT 1 FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id
         AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 corpus status arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  -- FORCE RLS public pointer policy allows this bounded status read only through the same
  -- transaction-local marker used by immutable retrieval capability functions.
  PERFORM set_config('app.rag_v2_retrieval_scope', 'enabled', true);
  SELECT * INTO public_pointer
  FROM public.rag_v2_immutable_public_bundle_pointers AS public_pointer_row
  WHERE public_pointer_row.state_id = 'default'
    AND public_pointer_row.state = 'ACTIVE';
  public_pointer_found := FOUND;
  IF FOUND THEN
    public_version := 'immutable-v2-' || public_pointer.pointer_version::text;
    -- V25 activation atomically validates exact/OA cardinality, rights, profile and evaluation
    -- before it can set the pointer ACTIVE. V29 resolver repeats the component-level check when
    -- issuing a capability; status itself only follows this immutable activation receipt.
    public_ready := true;
  END IF;

  SELECT * INTO owner_pointer
  FROM public.rag_v2_immutable_owner_bundle_pointers AS owner_pointer_row
  WHERE owner_pointer_row.owner_user_id = p_owner_user_id;
  IF FOUND THEN
    owner_state := owner_pointer.state;
    IF owner_pointer.state = 'READY' THEN
      SELECT EXISTS (
        SELECT 1
        FROM public.rag_v2_immutable_bundles AS bundle
        JOIN public.rag_v2_immutable_component_generations AS owner_generation
          ON owner_generation.component_generation_id = bundle.owner_private_generation_id
         AND owner_generation.component_scope = 'OWNER_PRIVATE'
         AND owner_generation.owner_user_id = p_owner_user_id
         AND owner_generation.embedding_profile_id = bundle.embedding_profile_id
         AND owner_generation.state = 'ACTIVE'
         AND owner_generation.evaluation_status = 'PASSED'
        WHERE bundle.bundle_id = owner_pointer.active_bundle_id
          AND bundle.owner_user_id = p_owner_user_id
          AND bundle.state = 'ACTIVE'
          AND bundle.evaluation_status = 'PASSED'
          AND bundle.exact30_generation_id IS NOT DISTINCT FROM public_pointer.exact30_generation_id
          AND bundle.oa112_generation_id IS NOT DISTINCT FROM public_pointer.oa112_generation_id
          AND bundle.embedding_profile_id IS NOT DISTINCT FROM public_pointer.embedding_profile_id
      ) INTO owner_ready;
      IF NOT owner_ready THEN
        owner_state := 'FAILED';
      END IF;
    ELSIF owner_pointer.state NOT IN ('ABSENT', 'BUILDING', 'FAILED') THEN
      owner_state := 'FAILED';
    END IF;
  END IF;

  RETURN QUERY
  SELECT
    CASE
      WHEN owner_state = 'FAILED' THEN 'FAILED'
      WHEN public_ready AND owner_state IN ('ABSENT', 'READY') THEN 'FULL_READY'
      WHEN owner_state = 'BUILDING' THEN 'BUILDING'
      ELSE 'CORE_READY'
    END,
    public_version,
    owner_state,
    CASE
      WHEN public_ready AND owner_state IN ('ABSENT', 'READY') THEN 100
      WHEN owner_state = 'BUILDING' THEN 50
      ELSE 0
    END,
    CASE
      WHEN owner_state = 'FAILED' THEN 'OWNER_OVERLAY_FAILED'
      WHEN public_pointer_found AND NOT public_ready THEN 'IMMUTABLE_PUBLIC_BUNDLE_INVALID'
      ELSE NULL
    END;
END
$read_rag_v2_corpus_status$;
ALTER FUNCTION read_rag_v2_corpus_status(text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION read_rag_v2_corpus_status(text) FROM PUBLIC;

-- Python query process에는 owner ID를 wire/argv로 주지 않는다. claim table direct SELECT 없이
-- opaque claim과 request session만으로 V29 resolver를 호출하므로 claim expiry/pointer drift 규칙은 같다.
CREATE POLICY rag_v2_retrieval_scope_claim_opaque_query_policy
  ON rag_v2_retrieval_scope_claims
  FOR SELECT
  USING (
    current_user = 'flyway'
    AND session_user = 'decision_rag_query'
    AND current_setting('app.rag_v2_retrieval_scope', true) = 'enabled'
  );

CREATE FUNCTION read_rag_v2_retrieval_scope_by_claim(
  p_scope_claim_id text,
  p_session_id text
)
RETURNS TABLE (
  scope_claim_id text,
  owner_user_id text,
  session_id text,
  allowed_topics text[],
  exact30_generation_id text,
  oa112_generation_id text,
  owner_private_generation_id text,
  embedding_profile_id text,
  policy_version bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $read_rag_v2_retrieval_scope_by_claim$
DECLARE
  bound_owner_user_id text;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_query'
     OR p_scope_claim_id !~ '^rvs_[0-9a-f]{32}$'
     OR p_session_id IS NULL
     OR char_length(p_session_id) NOT BETWEEN 16 AND 128
     OR p_session_id !~ '^[A-Za-z0-9._:-]+$' THEN
    RAISE EXCEPTION 'immutable RAG v2 opaque scope arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM set_config('app.rag_v2_retrieval_scope', 'enabled', true);
  SELECT scope.owner_user_id
  INTO bound_owner_user_id
  FROM public.rag_v2_retrieval_scope_claims AS scope
  WHERE scope.scope_claim_id = p_scope_claim_id
    AND scope.session_id = p_session_id
    AND scope.expires_at > statement_timestamp();
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 opaque scope is absent or expired'
      USING ERRCODE = '55000';
  END IF;

  RETURN QUERY
  SELECT *
  FROM public.resolve_rag_v2_retrieval_scope(
    p_scope_claim_id,
    bound_owner_user_id,
    p_session_id
  );
END
$read_rag_v2_retrieval_scope_by_claim$;
ALTER FUNCTION read_rag_v2_retrieval_scope_by_claim(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION read_rag_v2_retrieval_scope_by_claim(text, text) FROM PUBLIC;

DO $rag_v2_immutable_runtime_status_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    GRANT EXECUTE ON FUNCTION read_rag_v2_corpus_status(text) TO decision_app;
    REVOKE ALL PRIVILEGES ON FUNCTION read_rag_v2_retrieval_scope_by_claim(text, text) FROM decision_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_query') THEN
    GRANT EXECUTE ON FUNCTION read_rag_v2_retrieval_scope_by_claim(text, text) TO decision_rag_query;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    REVOKE ALL PRIVILEGES ON FUNCTION read_rag_v2_retrieval_scope_by_claim(text, text) FROM decision_rag_writer;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_admin') THEN
    REVOKE ALL PRIVILEGES ON FUNCTION read_rag_v2_retrieval_scope_by_claim(text, text) FROM decision_rag_admin;
  END IF;
END
$rag_v2_immutable_runtime_status_acl$;
