-- V31은 V30 metadata-bound STAGED owner document를 하나의 complete immutable overlay로
-- 조립한다. raw source를 재파싱하거나 provider를 호출하지 않으며, active public base와 같은
-- BGE profile에 pin될 수 있는 evaluated bundle만 반환한다.

CREATE FUNCTION prepare_rag_v2_immutable_owner_overlay(
  p_owner_user_id text,
  p_excluded_document_id text DEFAULT NULL
)
RETURNS TABLE (
  bundle_id text,
  owner_private_generation_id text,
  expected_active_bundle_id text,
  expected_bundle_version bigint,
  source_count integer,
  chunk_count integer
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $prepare_rag_v2_immutable_owner_overlay$
#variable_conflict use_column
DECLARE
  public_pointer public.rag_v2_immutable_public_bundle_pointers%ROWTYPE;
  owner_pointer public.rag_v2_immutable_owner_bundle_pointers%ROWTYPE;
  owner_pointer_exists boolean := false;
  existing_generation public.rag_v2_immutable_component_generations%ROWTYPE;
  existing_bundle public.rag_v2_immutable_bundles%ROWTYPE;
  selected_source_revision_ids text[] := ARRAY[]::text[];
  selected_source_count integer := 0;
  selected_chunk_count integer := 0;
  selected_embedding_count integer := 0;
  overlay_manifest_hash text;
  overlay_generation_hash text;
  overlay_generation_id text;
  overlay_bundle_hash text;
  overlay_bundle_id text;
  evaluation_timestamp timestamptz := clock_timestamp();
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_admin'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR (p_excluded_document_id IS NOT NULL AND p_excluded_document_id !~ '^doc_[a-z0-9][a-z0-9_-]{10,95}$')
     OR NOT EXISTS (
       SELECT 1 FROM public.users AS owner
       WHERE owner.user_id = p_owner_user_id
         AND owner.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner overlay arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  -- active document 삭제의 replacement 조립은 V25 delete와 같은 document→global lock 순서를
  -- 따라야 한다. 그렇지 않으면 구형 direct delete가 document lock을 보유한 채 global lock을
  -- 기다리는 경우와 교착될 수 있다.
  IF p_excluded_document_id IS NOT NULL THEN
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended(
        'rag-v2-immutable-owner-document|' || p_owner_user_id || '|' || p_excluded_document_id,
        0
      )
    );
  END IF;
  -- owner activation과 같은 advisory lock으로 public pointer refresh와 overlay assembly를
  -- 직렬화한다. 이 function은 pointer를 update하지 않아 direct table capability를 넓히지 않는다.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-bundle-activation', 0)
  );
  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  PERFORM set_config('app.rag_admin_maintenance', 'owner_bundle_activation', true);

  SELECT * INTO public_pointer
  FROM public.rag_v2_immutable_public_bundle_pointers
  WHERE state_id = 'default';
  IF NOT FOUND
     OR public_pointer.state <> 'ACTIVE'
     OR public_pointer.embedding_profile_id <> 'bge_m3_local_1024_v1'
     OR public_pointer.exact30_generation_id IS NULL
     OR public_pointer.oa112_generation_id IS NULL THEN
    RAISE EXCEPTION 'immutable RAG v2 BGE public base is not active'
      USING ERRCODE = '55000';
  END IF;

  SELECT * INTO owner_pointer
  FROM public.rag_v2_immutable_owner_bundle_pointers
  WHERE owner_user_id = p_owner_user_id;
  owner_pointer_exists := FOUND;

  -- V28/V30 document generation은 source 하나만 STAGED로 남긴다. 그 complete receipts만
  -- aggregate 대상으로 사용하고, metadata 없는 legacy row는 조용히 누락시키지 않고 거부한다.
  SELECT coalesce(
    array_agg(source.source_revision_id ORDER BY source.source_revision_id COLLATE "C"),
    ARRAY[]::text[]
  )
  INTO selected_source_revision_ids
  FROM public.rag_v2_immutable_source_revisions AS source
  JOIN public.rag_v2_immutable_materialization_runs AS run
    ON run.owner_user_id = source.owner_user_id
   AND run.component_scope = 'OWNER_PRIVATE'
   AND run.document_id = source.document_id
  JOIN public.rag_v2_immutable_component_generations AS staging_generation
    ON staging_generation.component_generation_id = run.component_generation_id
   AND staging_generation.owner_user_id = source.owner_user_id
   AND staging_generation.component_scope = 'OWNER_PRIVATE'
  WHERE source.owner_user_id = p_owner_user_id
    AND source.source_scope = 'OWNER_PRIVATE'
    AND (p_excluded_document_id IS NULL OR source.document_id <> p_excluded_document_id)
    AND run.state = 'STAGED'
    AND staging_generation.state = 'STAGING'
    AND staging_generation.evaluation_status = 'PENDING'
    AND staging_generation.embedding_profile_id = 'bge_m3_local_1024_v1';

  IF EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_source_revisions AS source
    WHERE source.owner_user_id = p_owner_user_id
      AND source.source_scope = 'OWNER_PRIVATE'
      AND (p_excluded_document_id IS NULL OR source.document_id <> p_excluded_document_id)
      AND NOT (source.source_revision_id = ANY(selected_source_revision_ids))
  ) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner overlay has an incomplete or historical source'
      USING ERRCODE = '55000';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_source_revisions AS source
    WHERE source.source_revision_id = ANY(selected_source_revision_ids)
      AND (
        source.sanitized_display_name IS NULL
        OR char_length(source.sanitized_display_name) NOT BETWEEN 1 AND 160
        OR source.sanitized_display_name <> btrim(source.sanitized_display_name)
        OR source.sanitized_display_name ~ '[[:cntrl:]]'
        OR position('/' IN source.sanitized_display_name) > 0
        OR position(E'\\' IN source.sanitized_display_name) > 0
        OR position(':' IN source.sanitized_display_name) > 0
        OR source.canonical_https_url IS NOT NULL
        OR source.external_processing_eligible
        OR NOT source.local_processing_allowed
        OR NOT public.rag_v2_immutable_retrieval_topics_are_valid(source.retrieval_topics)
      )
  ) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner overlay citation metadata is invalid'
      USING ERRCODE = '23514';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_source_revisions AS source
    LEFT JOIN public.rag_v2_immutable_materialization_runs AS run
      ON run.owner_user_id = source.owner_user_id
     AND run.component_scope = 'OWNER_PRIVATE'
     AND run.document_id = source.document_id
     AND run.state = 'STAGED'
    LEFT JOIN public.rag_v2_immutable_component_generations AS staging_generation
      ON staging_generation.component_generation_id = run.component_generation_id
     AND staging_generation.owner_user_id = source.owner_user_id
     AND staging_generation.component_scope = 'OWNER_PRIVATE'
    WHERE source.source_revision_id = ANY(selected_source_revision_ids)
      AND (
        run.materialization_run_id IS NULL
        OR staging_generation.component_generation_id IS NULL
        OR staging_generation.state <> 'STAGING'
        OR staging_generation.evaluation_status <> 'PENDING'
        OR staging_generation.expected_source_count <> 1
        OR staging_generation.actual_source_count <> 1
        OR staging_generation.expected_chunk_count < 1
        OR staging_generation.actual_chunk_count <> staging_generation.expected_chunk_count
        OR (
          SELECT count(*)
          FROM public.rag_v2_immutable_generation_memberships AS membership
          WHERE membership.component_generation_id = staging_generation.component_generation_id
            AND membership.source_revision_id = source.source_revision_id
            AND membership.owner_user_id = p_owner_user_id
            AND membership.component_scope = 'OWNER_PRIVATE'
        ) <> staging_generation.actual_chunk_count
        OR (
          SELECT count(*)
          FROM public.rag_v2_immutable_generation_embeddings AS embedding
          JOIN public.rag_v2_immutable_generation_memberships AS membership
            ON membership.component_generation_id = embedding.component_generation_id
           AND membership.chunk_id = embedding.chunk_id
          WHERE embedding.component_generation_id = staging_generation.component_generation_id
            AND membership.source_revision_id = source.source_revision_id
            AND embedding.owner_user_id = p_owner_user_id
            AND embedding.component_scope = 'OWNER_PRIVATE'
            AND embedding.embedding_profile_id = 'bge_m3_local_1024_v1'
        ) <> staging_generation.actual_chunk_count
      )
  ) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner overlay source receipts are incomplete'
      USING ERRCODE = '23514';
  END IF;

  SELECT count(*)::integer
  INTO selected_source_count
  FROM public.rag_v2_immutable_source_revisions AS source
  WHERE source.source_revision_id = ANY(selected_source_revision_ids);
  SELECT count(*)::integer
  INTO selected_chunk_count
  FROM public.rag_v2_immutable_generation_memberships AS membership
  JOIN public.rag_v2_immutable_source_revisions AS source
    ON source.source_revision_id = membership.source_revision_id
  WHERE source.source_revision_id = ANY(selected_source_revision_ids)
    AND membership.owner_user_id = p_owner_user_id
    AND membership.component_scope = 'OWNER_PRIVATE';
  IF (selected_source_count = 0 AND selected_chunk_count <> 0)
     OR (selected_source_count > 0 AND selected_chunk_count < selected_source_count) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner overlay cardinality is invalid'
      USING ERRCODE = '23514';
  END IF;

  SELECT encode(
    digest(
      coalesce(
        string_agg(
          source.source_revision_id || '|' || source.canonical_text_sha256 || '|' ||
          encode(digest(source.sanitized_display_name, 'sha256'), 'hex') || '|' ||
          encode(digest(array_to_string(source.retrieval_topics, E'\x1f'), 'sha256'), 'hex'),
          E'\n' ORDER BY source.source_revision_id COLLATE "C"
        ),
        ''
      ),
      'sha256'
    ),
    'hex'
  )
  INTO overlay_manifest_hash
  FROM public.rag_v2_immutable_source_revisions AS source
  WHERE source.source_revision_id = ANY(selected_source_revision_ids);
  overlay_generation_hash := encode(
    digest(
      'rag-v2-owner-overlay-generation|'
      || p_owner_user_id || '|bge_m3_local_1024_v1|' || overlay_manifest_hash,
      'sha256'
    ),
    'hex'
  );
  overlay_generation_id := 'rgr_' || substr(overlay_generation_hash, 1, 32);
  overlay_bundle_hash := encode(
    digest(
      'rag-v2-owner-overlay-bundle|'
      || p_owner_user_id || '|' || public_pointer.exact30_generation_id || '|'
      || public_pointer.oa112_generation_id || '|' || overlay_generation_id || '|'
      || public_pointer.embedding_profile_id || '|' || overlay_manifest_hash,
      'sha256'
    ),
    'hex'
  );
  overlay_bundle_id := 'rgb_' || substr(overlay_bundle_hash, 1, 32);

  SELECT * INTO existing_generation
  FROM public.rag_v2_immutable_component_generations
  WHERE component_generation_id = overlay_generation_id
  FOR UPDATE;
  IF FOUND THEN
    IF existing_generation.owner_user_id IS DISTINCT FROM p_owner_user_id
       OR existing_generation.component_scope <> 'OWNER_PRIVATE'
       OR existing_generation.embedding_profile_id <> public_pointer.embedding_profile_id
       OR existing_generation.state NOT IN ('EVALUATED', 'ACTIVE')
       OR existing_generation.evaluation_status <> 'PASSED'
       OR existing_generation.generation_hash <> overlay_generation_hash
       OR existing_generation.manifest_hash <> overlay_manifest_hash
       OR existing_generation.expected_source_count <> selected_source_count
       OR existing_generation.actual_source_count <> selected_source_count
       OR existing_generation.expected_chunk_count <> selected_chunk_count
       OR existing_generation.actual_chunk_count <> selected_chunk_count THEN
      RAISE EXCEPTION 'immutable RAG v2 owner overlay generation identity conflicted'
        USING ERRCODE = '23505';
    END IF;
  ELSE
    INSERT INTO public.rag_v2_immutable_component_generations (
      component_generation_id,
      owner_user_id,
      component_scope,
      embedding_profile_id,
      state,
      evaluation_status,
      expected_source_count,
      expected_chunk_count,
      actual_source_count,
      actual_chunk_count,
      generation_hash,
      manifest_hash
    ) VALUES (
      overlay_generation_id,
      p_owner_user_id,
      'OWNER_PRIVATE',
      public_pointer.embedding_profile_id,
      'STAGING',
      'PENDING',
      selected_source_count,
      selected_chunk_count,
      0,
      0,
      overlay_generation_hash,
      overlay_manifest_hash
    );
    INSERT INTO public.rag_v2_immutable_generation_memberships (
      component_generation_id,
      chunk_id,
      source_revision_id,
      owner_user_id,
      component_scope,
      ordinal
    )
    SELECT
      overlay_generation_id,
      membership.chunk_id,
      membership.source_revision_id,
      p_owner_user_id,
      'OWNER_PRIVATE',
      row_number() OVER (
        ORDER BY source.source_revision_id COLLATE "C", chunk.chunk_ordinal, membership.chunk_id COLLATE "C"
      )::integer
    FROM public.rag_v2_immutable_generation_memberships AS membership
    JOIN public.rag_v2_immutable_chunks AS chunk
      ON chunk.chunk_id = membership.chunk_id
     AND chunk.source_revision_id = membership.source_revision_id
    JOIN public.rag_v2_immutable_source_revisions AS source
      ON source.source_revision_id = membership.source_revision_id
    JOIN public.rag_v2_immutable_materialization_runs AS run
      ON run.component_generation_id = membership.component_generation_id
     AND run.owner_user_id = p_owner_user_id
     AND run.component_scope = 'OWNER_PRIVATE'
     AND run.document_id = source.document_id
     AND run.state = 'STAGED'
    WHERE source.source_revision_id = ANY(selected_source_revision_ids)
      AND membership.owner_user_id = p_owner_user_id
      AND membership.component_scope = 'OWNER_PRIVATE'
      AND membership.component_generation_id = run.component_generation_id;
    INSERT INTO public.rag_v2_immutable_generation_embeddings (
      component_generation_id,
      chunk_id,
      owner_user_id,
      component_scope,
      embedding_profile_id,
      embedding_input_hash,
      context_set_hash,
      embedding
    )
    SELECT
      overlay_generation_id,
      embedding.chunk_id,
      p_owner_user_id,
      'OWNER_PRIVATE',
      public_pointer.embedding_profile_id,
      embedding.embedding_input_hash,
      embedding.context_set_hash,
      embedding.embedding
    FROM public.rag_v2_immutable_generation_embeddings AS embedding
    JOIN public.rag_v2_immutable_generation_memberships AS membership
      ON membership.component_generation_id = embedding.component_generation_id
     AND membership.chunk_id = embedding.chunk_id
    JOIN public.rag_v2_immutable_source_revisions AS source
      ON source.source_revision_id = membership.source_revision_id
    JOIN public.rag_v2_immutable_materialization_runs AS run
      ON run.component_generation_id = membership.component_generation_id
     AND run.owner_user_id = p_owner_user_id
     AND run.component_scope = 'OWNER_PRIVATE'
     AND run.document_id = source.document_id
     AND run.state = 'STAGED'
    WHERE source.source_revision_id = ANY(selected_source_revision_ids)
      AND embedding.owner_user_id = p_owner_user_id
      AND embedding.component_scope = 'OWNER_PRIVATE'
      AND embedding.embedding_profile_id = public_pointer.embedding_profile_id;
    SELECT count(*)::integer
    INTO selected_embedding_count
    FROM public.rag_v2_immutable_generation_embeddings AS embedding
    WHERE embedding.component_generation_id = overlay_generation_id
      AND embedding.owner_user_id = p_owner_user_id
      AND embedding.component_scope = 'OWNER_PRIVATE'
      AND embedding.embedding_profile_id = public_pointer.embedding_profile_id;
    IF selected_embedding_count <> selected_chunk_count THEN
      RAISE EXCEPTION 'immutable RAG v2 owner overlay embedding copy is incomplete'
        USING ERRCODE = '23514';
    END IF;
    UPDATE public.rag_v2_immutable_component_generations
    SET actual_source_count = selected_source_count,
        actual_chunk_count = selected_chunk_count,
        state = 'EVALUATED',
        evaluation_status = 'PASSED',
        evaluated_at = evaluation_timestamp
    WHERE component_generation_id = overlay_generation_id
      AND owner_user_id = p_owner_user_id
      AND state = 'STAGING'
      AND evaluation_status = 'PENDING';
    IF NOT FOUND THEN
      RAISE EXCEPTION 'immutable RAG v2 owner overlay evaluation transition failed'
        USING ERRCODE = '40001';
    END IF;
  END IF;

  SELECT * INTO existing_bundle
  FROM public.rag_v2_immutable_bundles
  WHERE bundle_id = overlay_bundle_id
  FOR UPDATE;
  IF FOUND THEN
    IF existing_bundle.owner_user_id IS DISTINCT FROM p_owner_user_id
       OR existing_bundle.exact30_generation_id IS DISTINCT FROM public_pointer.exact30_generation_id
       OR existing_bundle.oa112_generation_id IS DISTINCT FROM public_pointer.oa112_generation_id
       OR existing_bundle.owner_private_generation_id IS DISTINCT FROM overlay_generation_id
       OR existing_bundle.embedding_profile_id IS DISTINCT FROM public_pointer.embedding_profile_id
       OR existing_bundle.state NOT IN ('EVALUATED', 'ACTIVE')
       OR existing_bundle.evaluation_status <> 'PASSED'
       OR existing_bundle.bundle_hash <> overlay_bundle_hash THEN
      RAISE EXCEPTION 'immutable RAG v2 owner overlay bundle identity conflicted'
        USING ERRCODE = '23505';
    END IF;
  ELSE
    INSERT INTO public.rag_v2_immutable_bundles (
      bundle_id,
      owner_user_id,
      exact30_generation_id,
      oa112_generation_id,
      owner_private_generation_id,
      embedding_profile_id,
      state,
      evaluation_status,
      bundle_hash,
      evaluated_at
    ) VALUES (
      overlay_bundle_id,
      p_owner_user_id,
      public_pointer.exact30_generation_id,
      public_pointer.oa112_generation_id,
      overlay_generation_id,
      public_pointer.embedding_profile_id,
      'EVALUATED',
      'PASSED',
      overlay_bundle_hash,
      evaluation_timestamp
    );
  END IF;

  PERFORM set_config('app.rag_admin_maintenance', '', true);
  RETURN QUERY
  SELECT
    overlay_bundle_id,
    overlay_generation_id,
    CASE WHEN owner_pointer_exists THEN owner_pointer.active_bundle_id ELSE NULL END,
    CASE WHEN owner_pointer_exists THEN owner_pointer.bundle_version ELSE 0::bigint END,
    selected_source_count,
    selected_chunk_count;
END;
$prepare_rag_v2_immutable_owner_overlay$;
ALTER FUNCTION prepare_rag_v2_immutable_owner_overlay(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION prepare_rag_v2_immutable_owner_overlay(text, text) FROM PUBLIC;

DO $rag_v2_owner_overlay_assembly_admin_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_admin') THEN
    GRANT EXECUTE ON FUNCTION prepare_rag_v2_immutable_owner_overlay(text, text)
      TO decision_rag_admin;
  END IF;
END;
$rag_v2_owner_overlay_assembly_admin_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION prepare_rag_v2_immutable_owner_overlay(text, text) FROM PUBLIC;
