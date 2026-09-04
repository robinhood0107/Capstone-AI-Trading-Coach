-- Indexed generation이 없으면 등록된 공개 source card를 사용한다.

CREATE OR REPLACE FUNCTION public.read_rag_source_registry(p_actor_user_id text)
RETURNS TABLE(source_id text, title text, institution text, topic text,
              attribution text, canonical_url text, last_checked_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path TO 'public', 'pg_catalog', 'pg_temp'
AS $read_rag_source_registry_v122$
BEGIN
  IF p_actor_user_id IS NULL
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_actor_user_id
     OR NOT EXISTS (
       SELECT 1
       FROM public.users AS authenticated_user
       WHERE authenticated_user.user_id = p_actor_user_id
         AND authenticated_user.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'RAG source projection actor mismatch' USING ERRCODE = '42501';
  END IF;

  RETURN QUERY
  WITH indexed AS (
    SELECT DISTINCT ON (source.source_id)
      source.source_id AS source_id,
      revision.title AS title,
      source.institution AS institution,
      source.topic AS topic,
      revision.attribution AS attribution,
      revision.canonical_url AS canonical_url,
      latest_check.checked_at AS last_checked_at
    FROM public.rag_embedding_policy_state AS policy_state
    JOIN public.rag_corpus_generations AS generation
      ON generation.corpus_generation_id = policy_state.active_generation_id
     AND generation.embedding_profile_id = policy_state.effective_profile_id
     AND generation.status = 'ACTIVE'
     AND generation.evaluation_status = 'PASSED'
    JOIN public.rag_generation_chunks AS membership
      ON membership.corpus_generation_id = generation.corpus_generation_id
     AND membership.embedding_profile_id = generation.embedding_profile_id
    JOIN public.rag_chunk_revisions AS chunk
      ON chunk.chunk_revision_id = membership.chunk_revision_id
     AND chunk.access_level = 'PUBLIC'
    JOIN public.rag_source_revisions AS revision
      ON revision.source_revision_id = chunk.source_revision_id
     AND revision.access_level = 'PUBLIC'
    JOIN public.rag_sources AS source
      ON source.source_id = revision.source_id
     AND source.source_type = 'PROJECT_SOURCE_CARD'
     AND source.retired_at IS NULL
    LEFT JOIN LATERAL (
      SELECT source_check.checked_at
      FROM public.rag_source_checks AS source_check
      WHERE source_check.source_revision_id = revision.source_revision_id
      ORDER BY source_check.checked_at DESC, source_check.source_check_id DESC
      LIMIT 1
    ) AS latest_check ON true
    ORDER BY source.source_id, revision.revision_seq DESC
  ),
  registered AS (
    SELECT DISTINCT ON (source.source_id)
      source.source_id AS source_id,
      revision.title AS title,
      source.institution AS institution,
      source.topic AS topic,
      revision.attribution AS attribution,
      revision.canonical_url AS canonical_url,
      latest_check.checked_at AS last_checked_at
    FROM public.rag_sources AS source
    JOIN public.rag_source_revisions AS revision
      ON revision.source_id = source.source_id
     AND revision.access_level = 'PUBLIC'
    LEFT JOIN LATERAL (
      SELECT source_check.checked_at
      FROM public.rag_source_checks AS source_check
      WHERE source_check.source_revision_id = revision.source_revision_id
      ORDER BY source_check.checked_at DESC, source_check.source_check_id DESC
      LIMIT 1
    ) AS latest_check ON true
    WHERE source.source_type = 'PROJECT_SOURCE_CARD'
      AND source.retired_at IS NULL
    ORDER BY source.source_id, revision.revision_seq DESC
  ),
  resolved AS (
    SELECT * FROM indexed
    UNION ALL
    SELECT * FROM registered WHERE NOT EXISTS (SELECT 1 FROM indexed)
  )
  SELECT
    resolved.source_id,
    resolved.title,
    resolved.institution,
    resolved.topic,
    resolved.attribution,
    resolved.canonical_url,
    resolved.last_checked_at
  FROM resolved
  ORDER BY resolved.source_id
  LIMIT 30;
END
$read_rag_source_registry_v122$;
