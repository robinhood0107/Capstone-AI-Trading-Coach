-- V62는 V61이 만드는 READY base-only owner overlay를 public-only retrieval scope로 안전하게 인정한다.
-- owner generation/profile은 계속 NULL이어야 하며 public base와 pointer version이 모두 current일 때만 허용한다.

CREATE FUNCTION public.rag_v2_immutable_empty_owner_scope_is_current(
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
  IF NOT (pointer.bundle_version = p_owner_pointer_version) THEN
    RETURN false;
  END IF;
  IF pointer.state = 'ABSENT' THEN
    RETURN pointer.active_bundle_id IS NULL;
  END IF;
  IF pointer.state = 'READY' AND pointer.active_bundle_id IS NOT NULL THEN
    RETURN EXISTS (
      SELECT 1
      FROM public.rag_v2_immutable_bundles AS bundle
      WHERE bundle.bundle_id = pointer.active_bundle_id
        AND bundle.owner_user_id = p_owner_user_id
        AND bundle.state = 'ACTIVE'
        AND bundle.evaluation_status = 'PASSED'
        AND bundle.owner_private_generation_id IS NULL
        AND bundle.owner_embedding_profile_id IS NULL
        AND bundle.exact30_generation_id = p_exact30_generation_id
        AND bundle.oa112_generation_id = p_oa112_generation_id
        AND bundle.embedding_profile_id = p_embedding_profile_id
    );
  END IF;
  RETURN false;
END
$rag_v2_immutable_empty_owner_scope_is_current$;
ALTER FUNCTION public.rag_v2_immutable_empty_owner_scope_is_current(text,bigint,text,text,text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.rag_v2_immutable_empty_owner_scope_is_current(text,bigint,text,text,text) FROM PUBLIC;

DO $repair_base_only_owner_scope_guards$
DECLARE
  definition text;
  repaired text;
  retrieval_old constant text := $old$
  IF claim_row.owner_private_generation_id IS NULL THEN
    IF (FOUND AND (owner_pointer.state <> 'ABSENT' OR owner_pointer.bundle_version <> claim_row.owner_pointer_version))
       OR (NOT FOUND AND claim_row.owner_pointer_version <> 0) THEN
      RAISE EXCEPTION 'immutable RAG v2 owner retrieval scope changed'
        USING ERRCODE = '55000';
    END IF;
  ELSE
$old$;
  retrieval_new constant text := $new$
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
  vertex_old constant text := $old$
  IF scope_row.owner_private_generation_id IS NULL THEN
    IF (FOUND AND (owner_pointer.state <> 'ABSENT' OR owner_pointer.bundle_version <> scope_row.owner_pointer_version))
       OR (NOT FOUND AND scope_row.owner_pointer_version <> 0) THEN
      RAISE EXCEPTION 'immutable Pre-S5 Vertex owner retrieval scope changed'
        USING ERRCODE = '55000';
    END IF;
  ELSE
$old$;
  vertex_new constant text := $new$
  IF scope_row.owner_private_generation_id IS NULL THEN
    IF NOT public.rag_v2_immutable_empty_owner_scope_is_current(
      p_reservation.owner_user_id,
      scope_row.owner_pointer_version,
      scope_row.exact30_generation_id,
      scope_row.oa112_generation_id,
      scope_row.embedding_profile_id
    ) THEN
      RAISE EXCEPTION 'immutable Pre-S5 Vertex owner retrieval scope changed'
        USING ERRCODE = '55000';
    END IF;
  ELSE
$new$;
BEGIN
  SELECT pg_get_functiondef(
    'public.canonicalize_rag_v2_immutable_retrieval_citations(text,text,text,jsonb)'::regprocedure
  ) INTO definition;
  repaired := replace(definition, retrieval_old, retrieval_new);
  IF repaired = definition THEN
    RAISE EXCEPTION 'V62 retrieval guard source is not the expected V35 definition';
  END IF;
  EXECUTE repaired;

  SELECT pg_get_functiondef(
    'public.assert_rag_v2_immutable_vertex_reservation_is_current(public.rag_v2_immutable_vertex_usage_reservations)'::regprocedure
  ) INTO definition;
  repaired := replace(definition, vertex_old, vertex_new);
  IF repaired = definition THEN
    RAISE EXCEPTION 'V62 Vertex guard source is not the expected V42 definition';
  END IF;
  EXECUTE repaired;
END
$repair_base_only_owner_scope_guards$;

ALTER FUNCTION public.canonicalize_rag_v2_immutable_retrieval_citations(text,text,text,jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.canonicalize_rag_v2_immutable_retrieval_citations(text,text,text,jsonb) FROM PUBLIC;
ALTER FUNCTION public.assert_rag_v2_immutable_vertex_reservation_is_current(
  public.rag_v2_immutable_vertex_usage_reservations
) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.assert_rag_v2_immutable_vertex_reservation_is_current(
  public.rag_v2_immutable_vertex_usage_reservations
) FROM PUBLIC;
