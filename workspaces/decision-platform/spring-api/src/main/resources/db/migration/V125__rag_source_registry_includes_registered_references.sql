CREATE OR REPLACE FUNCTION public.read_rag_source_registry(p_actor_user_id text)
RETURNS TABLE(source_id text, title text, institution text, topic text,
              attribution text, canonical_url text, last_checked_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path TO 'public', 'pg_catalog', 'pg_temp'
AS $read_rag_source_registry_v125$
BEGIN
  IF p_actor_user_id IS NULL
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_actor_user_id
     OR NOT EXISTS (
       SELECT 1 FROM public.users actor
       WHERE actor.user_id=p_actor_user_id AND actor.status='ACTIVE'
     ) THEN
    RAISE EXCEPTION 'RAG source projection actor mismatch' USING ERRCODE='42501';
  END IF;

  RETURN QUERY
  WITH indexed AS (
    SELECT DISTINCT ON (source.source_id)
      source.source_id,revision.title,source.institution,source.topic,
      revision.attribution,revision.canonical_url,latest_check.checked_at
    FROM public.rag_embedding_policy_state policy_state
    JOIN public.rag_corpus_generations generation
      ON generation.corpus_generation_id=policy_state.active_generation_id
     AND generation.embedding_profile_id=policy_state.effective_profile_id
     AND generation.status='ACTIVE' AND generation.evaluation_status='PASSED'
    JOIN public.rag_generation_chunks membership
      ON membership.corpus_generation_id=generation.corpus_generation_id
     AND membership.embedding_profile_id=generation.embedding_profile_id
    JOIN public.rag_chunk_revisions chunk
      ON chunk.chunk_revision_id=membership.chunk_revision_id AND chunk.access_level='PUBLIC'
    JOIN public.rag_source_revisions revision
      ON revision.source_revision_id=chunk.source_revision_id AND revision.access_level='PUBLIC'
    JOIN public.rag_sources source
      ON source.source_id=revision.source_id AND source.retired_at IS NULL
    LEFT JOIN LATERAL (
      SELECT source_check.checked_at FROM public.rag_source_checks source_check
      WHERE source_check.source_revision_id=revision.source_revision_id
      ORDER BY source_check.checked_at DESC,source_check.source_check_id DESC LIMIT 1
    ) latest_check ON true
    ORDER BY source.source_id,revision.revision_seq DESC
  ), registered AS (
    SELECT DISTINCT ON (source.source_id)
      source.source_id,revision.title,source.institution,source.topic,
      revision.attribution,revision.canonical_url,latest_check.checked_at
    FROM public.rag_sources source
    JOIN public.rag_source_revisions revision
      ON revision.source_id=source.source_id AND revision.access_level='PUBLIC'
    LEFT JOIN LATERAL (
      SELECT source_check.checked_at FROM public.rag_source_checks source_check
      WHERE source_check.source_revision_id=revision.source_revision_id
      ORDER BY source_check.checked_at DESC,source_check.source_check_id DESC LIMIT 1
    ) latest_check ON true
    WHERE source.source_type IN ('PROJECT_SOURCE_CARD','UPSTREAM_REFERENCE')
      AND source.retired_at IS NULL
    ORDER BY source.source_id,revision.revision_seq DESC
  ), resolved AS (
    SELECT * FROM indexed
    UNION ALL SELECT * FROM registered WHERE NOT EXISTS (SELECT 1 FROM indexed)
  )
  SELECT * FROM resolved ORDER BY source_id LIMIT 30;
END
$read_rag_source_registry_v125$;
