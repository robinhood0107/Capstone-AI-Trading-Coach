-- S4.6 Spring은 owner/session claim 발급과 동일 transaction의 active policy identity만 받고 기반 table은 읽지 못한다.
CREATE FUNCTION issue_rag_rpc_scope(
  p_owner_user_id text,
  p_session_id text,
  p_allowed_topics_json jsonb
)
RETURNS TABLE (
  scope_claim_id text,
  policy_id text,
  policy_version bigint,
  active_generation_id text,
  effective_profile_id text
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $issue_rag_rpc_scope$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR jsonb_typeof(p_allowed_topics_json) IS DISTINCT FROM 'array'
     OR EXISTS (
       SELECT 1
       FROM jsonb_array_elements(p_allowed_topics_json) AS item
       WHERE jsonb_typeof(item) IS DISTINCT FROM 'string'
     ) THEN
    RAISE EXCEPTION 'RAG RPC scope arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  RETURN QUERY
  WITH issued AS MATERIALIZED (
    SELECT claim.*
    FROM public.create_rag_retrieval_scope_claim(
      p_owner_user_id,
      p_session_id,
      ARRAY(
        SELECT jsonb_array_elements_text(p_allowed_topics_json)
      )
    ) AS claim
  )
  SELECT
    issued.scope_claim_id,
    state.policy_id,
    issued.policy_version,
    issued.active_generation_id,
    issued.effective_profile_id
  FROM issued
  JOIN public.rag_embedding_policy_state AS state
    ON state.state_id = 'default'
   AND state.version = issued.policy_version
   AND state.active_generation_id = issued.active_generation_id
   AND state.effective_profile_id = issued.effective_profile_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'RAG RPC scope policy changed during issuance'
      USING ERRCODE = '40001';
  END IF;
END
$issue_rag_rpc_scope$;

ALTER FUNCTION issue_rag_rpc_scope(text, text, jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION issue_rag_rpc_scope(text, text, jsonb)
  FROM PUBLIC;

DO $s4_6_rag_rpc_scope_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    GRANT EXECUTE
      ON FUNCTION issue_rag_rpc_scope(text, text, jsonb)
      TO decision_app;
  END IF;
END
$s4_6_rag_rpc_scope_acl$;

CREATE FUNCTION recheck_rag_rpc_citations(
  p_owner_user_id text,
  p_session_id text,
  p_scope_claim_id text,
  p_policy_id text,
  p_policy_version bigint,
  p_active_generation_id text,
  p_effective_profile_id text,
  p_citations jsonb
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $recheck_rag_rpc_citations$
DECLARE
  citation_count integer;
  valid_citation_count integer;
BEGIN
  citation_count :=
    CASE
      WHEN jsonb_typeof(p_citations) = 'array' THEN jsonb_array_length(p_citations)
      ELSE -1
    END;
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_session_id !~ '^[A-Za-z0-9._:-]{16,128}$'
     OR p_scope_claim_id !~ '^rag_scope_[0-9a-f]{32}$'
     OR p_active_generation_id !~ '^rag_gen_[0-9a-f]{32}$'
     OR p_policy_version < 1
     OR citation_count NOT BETWEEN 0 AND 5
     OR NOT EXISTS (
       SELECT 1
       FROM public.rag_retrieval_scope_claims AS claim
       JOIN public.rag_embedding_policy_state AS state
         ON state.state_id = 'default'
        AND state.policy_id = p_policy_id
        AND state.version = p_policy_version
        AND state.active_generation_id = p_active_generation_id
        AND state.effective_profile_id = p_effective_profile_id
       JOIN public.rag_corpus_generations AS generation
         ON generation.corpus_generation_id = state.active_generation_id
        AND generation.embedding_profile_id = state.effective_profile_id
        AND generation.status = 'ACTIVE'
        AND generation.evaluation_status = 'PASSED'
       WHERE claim.scope_claim_id = p_scope_claim_id
         AND claim.owner_user_id = p_owner_user_id
         AND claim.session_id = p_session_id
         AND claim.active_generation_id = p_active_generation_id
         AND claim.effective_profile_id = p_effective_profile_id
         AND claim.policy_version = p_policy_version
         AND claim.expires_at > statement_timestamp()
     ) THEN
    RAISE EXCEPTION 'RAG RPC scope recheck failed'
      USING ERRCODE = '42501';
  END IF;

  SELECT count(*)::integer
  INTO valid_citation_count
  FROM jsonb_array_elements(p_citations) AS item(value)
  JOIN public.rag_sources AS source
    ON source.source_id = item.value ->> 'sourceId'
   AND source.source_type = 'PROJECT_SOURCE_CARD'
   AND source.retired_at IS NULL
  JOIN public.rag_source_revisions AS revision
    ON revision.source_revision_id = item.value ->> 'sourceRevisionId'
   AND revision.source_id = source.source_id
   AND revision.access_level = 'PUBLIC'
   AND revision.tier = 'PROJECT'
  JOIN public.rag_source_card_verifications AS verification
    ON verification.source_revision_id = revision.source_revision_id
   AND verification.card_metadata_hash = revision.metadata_hash
   AND verification.status = 'VERIFIED'
  JOIN public.rag_chunk_revisions AS chunk
    ON chunk.chunk_revision_id = item.value ->> 'chunkRevisionId'
   AND chunk.source_revision_id = revision.source_revision_id
   AND chunk.access_level = 'PUBLIC'
   AND chunk.tier = 'PROJECT'
  JOIN public.rag_generation_chunks AS membership
    ON membership.corpus_generation_id = p_active_generation_id
   AND membership.embedding_profile_id = p_effective_profile_id
   AND membership.chunk_revision_id = chunk.chunk_revision_id
  JOIN public.rag_retrieval_scope_claims AS claim
    ON claim.scope_claim_id = p_scope_claim_id
   AND chunk.topic = ANY(claim.allowed_topics)
  WHERE jsonb_typeof(item.value) = 'object'
    AND (SELECT count(*) FROM jsonb_object_keys(item.value)) = 9
    AND item.value ?& ARRAY[
      'ordinal', 'citationId', 'sourceId', 'sourceRevisionId', 'chunkRevisionId',
      'generationId', 'title', 'sectionTitle', 'canonicalUrl'
    ]
    AND (item.value ->> 'ordinal')::integer BETWEEN 1 AND citation_count
    AND item.value ->> 'citationId' = 'cit_' || (item.value ->> 'ordinal')
    AND item.value ->> 'generationId' = p_active_generation_id
    AND item.value ->> 'title' = revision.title
    AND item.value ->> 'sectionTitle' = chunk.heading_path[cardinality(chunk.heading_path)]
    AND item.value ->> 'canonicalUrl' = revision.canonical_url;

  IF valid_citation_count <> citation_count THEN
    RAISE EXCEPTION 'RAG RPC citation access recheck failed'
      USING ERRCODE = '42501';
  END IF;
END
$recheck_rag_rpc_citations$;

ALTER FUNCTION recheck_rag_rpc_citations(
  text, text, text, text, bigint, text, text, jsonb
) OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION recheck_rag_rpc_citations(
    text, text, text, text, bigint, text, text, jsonb
  )
  FROM PUBLIC;

DO $s4_6_rag_rpc_citation_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    GRANT EXECUTE
      ON FUNCTION recheck_rag_rpc_citations(
        text, text, text, text, bigint, text, text, jsonb
      )
      TO decision_app;
  END IF;
END
$s4_6_rag_rpc_citation_acl$;
