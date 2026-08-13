-- V63은 V61이 빈 라이브러리에 만드는 0-row OWNER_PRIVATE generation을 base-only scope로 인정한다.
-- V62의 NULL generation 경계도 보존하며, 실제 owner source·membership·embedding이 하나라도 있으면 거부한다.

CREATE OR REPLACE FUNCTION public.rag_v2_immutable_empty_owner_scope_is_current(
  p_owner_user_id text,
  p_owner_pointer_version bigint,
  p_exact30_generation_id text,
  p_oa112_generation_id text,
  p_embedding_profile_id text
)
RETURNS boolean
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $rag_v2_immutable_empty_owner_scope_is_current$
DECLARE
  pointer public.rag_v2_immutable_owner_bundle_pointers%ROWTYPE;
  bundle public.rag_v2_immutable_bundles%ROWTYPE;
BEGIN
  IF p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_owner_pointer_version < 0
     OR p_exact30_generation_id !~ '^rgr_[0-9a-f]{32}$'
     OR p_oa112_generation_id !~ '^rgr_[0-9a-f]{32}$'
     OR p_exact30_generation_id = p_oa112_generation_id
     OR p_embedding_profile_id NOT IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1') THEN
    RETURN false;
  END IF;

  SELECT * INTO pointer
  FROM public.rag_v2_immutable_owner_bundle_pointers AS current_pointer
  WHERE current_pointer.owner_user_id = p_owner_user_id;
  IF NOT FOUND THEN
    RETURN p_owner_pointer_version = 0;
  END IF;
  IF pointer.bundle_version <> p_owner_pointer_version THEN
    RETURN false;
  END IF;
  IF pointer.state = 'ABSENT' THEN
    RETURN pointer.active_bundle_id IS NULL;
  END IF;
  IF pointer.state <> 'READY' OR pointer.active_bundle_id IS NULL THEN
    RETURN false;
  END IF;

  SELECT * INTO bundle
  FROM public.rag_v2_immutable_bundles AS current_bundle
  WHERE current_bundle.bundle_id = pointer.active_bundle_id
    AND current_bundle.owner_user_id = p_owner_user_id
    AND current_bundle.state = 'ACTIVE'
    AND current_bundle.evaluation_status = 'PASSED'
    AND current_bundle.owner_embedding_profile_id IS NULL
    AND current_bundle.exact30_generation_id = p_exact30_generation_id
    AND current_bundle.oa112_generation_id = p_oa112_generation_id
    AND current_bundle.embedding_profile_id = p_embedding_profile_id;
  IF NOT FOUND THEN
    RETURN false;
  END IF;
  IF bundle.owner_private_generation_id IS NULL THEN
    RETURN true;
  END IF;

  RETURN NOT EXISTS (
           SELECT 1
           FROM public.rag_v2_immutable_source_revisions AS source
           WHERE source.owner_user_id = p_owner_user_id
             AND source.source_scope = 'OWNER_PRIVATE'
         )
    AND EXISTS (
      SELECT 1
      FROM public.rag_v2_immutable_component_generations AS generation
      WHERE generation.component_generation_id = bundle.owner_private_generation_id
        AND generation.owner_user_id = p_owner_user_id
        AND generation.component_scope = 'OWNER_PRIVATE'
        AND generation.embedding_profile_id = p_embedding_profile_id
        AND generation.state = 'ACTIVE'
        AND generation.evaluation_status = 'PASSED'
        AND generation.expected_source_count = 0
        AND generation.actual_source_count = 0
        AND generation.expected_chunk_count = 0
        AND generation.actual_chunk_count = 0
    )
    AND NOT EXISTS (
      SELECT 1
      FROM public.rag_v2_immutable_generation_memberships AS membership
      WHERE membership.component_generation_id = bundle.owner_private_generation_id
    )
    AND NOT EXISTS (
      SELECT 1
      FROM public.rag_v2_immutable_generation_embeddings AS embedding
      WHERE embedding.component_generation_id = bundle.owner_private_generation_id
    );
END
$rag_v2_immutable_empty_owner_scope_is_current$;
ALTER FUNCTION public.rag_v2_immutable_empty_owner_scope_is_current(text,bigint,text,text,text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.rag_v2_immutable_empty_owner_scope_is_current(text,bigint,text,text,text) FROM PUBLIC;
