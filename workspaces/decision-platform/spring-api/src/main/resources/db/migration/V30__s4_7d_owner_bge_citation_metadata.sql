-- V28은 historical path-free local BGE staging record로 보존한다. V30은 display name/topic을
-- immutable source revision에 transaction-atomically bind해 owner citation과 scoped retrieval을
-- 가능하게 하며, 기존 V28 row를 backfill하거나 rewrite하지 않는다.

CREATE FUNCTION stage_rag_v2_immutable_owner_bge_document_v2(
  p_owner_user_id text,
  p_ticket_id text,
  p_payload jsonb
)
RETURNS TABLE (
  component_generation_id text,
  materialization_run_id text,
  state text,
  source_count integer,
  chunk_count integer
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $stage_rag_v2_immutable_owner_bge_document_v2$
#variable_conflict use_column
DECLARE
  v1_payload jsonb;
  payload_display_name text;
  payload_topics text[];
  staged_generation_id text;
  staged_run_id text;
  staged_state text;
  staged_source_count integer;
  staged_chunk_count integer;
  existing_display_name text;
  existing_topics text[];
  source_created_at timestamptz;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_ticket_id !~ '^rti_[0-9a-f]{32}$'
     OR p_payload IS NULL
     OR jsonb_typeof(p_payload) <> 'object'
     OR octet_length(p_payload::text) NOT BETWEEN 2 AND 16777216
     OR EXISTS (
       SELECT 1
       FROM jsonb_object_keys(p_payload) AS root_key
       WHERE root_key NOT IN (
         'canonicalText', 'canonicalTextSha256', 'chunks', 'documentId', 'documentIr',
         'embeddingProfileId', 'embeddings', 'mimeType', 'normalizedDocumentIrSha256',
         'parserVersion', 'rawContentSha256', 'retrievalTopics', 'sanitizedDisplayName',
         'schemaVersion', 'sourceId', 'sourceLocator', 'sourceRevisionId',
         'sourceRevisionSha256', 'tokenizerVersion'
       )
     )
     OR NOT (p_payload ?& ARRAY[
       'canonicalText', 'canonicalTextSha256', 'chunks', 'documentId', 'documentIr',
       'embeddingProfileId', 'embeddings', 'mimeType', 'normalizedDocumentIrSha256',
       'parserVersion', 'rawContentSha256', 'retrievalTopics', 'sanitizedDisplayName',
       'schemaVersion', 'sourceId', 'sourceLocator', 'sourceRevisionId',
       'sourceRevisionSha256', 'tokenizerVersion'
     ])
     OR jsonb_typeof(p_payload -> 'schemaVersion') <> 'number'
     OR p_payload ->> 'schemaVersion' <> '2'
     OR jsonb_typeof(p_payload -> 'sanitizedDisplayName') <> 'string'
     OR jsonb_typeof(p_payload -> 'retrievalTopics') <> 'array'
     OR EXISTS (
       SELECT 1
       FROM jsonb_array_elements(p_payload -> 'retrievalTopics') AS topic(value)
       WHERE jsonb_typeof(topic.value) <> 'string'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner BGE v2 staging arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  payload_display_name := p_payload ->> 'sanitizedDisplayName';
  SELECT coalesce(
    array_agg(topic.value ORDER BY topic.value COLLATE "C"),
    ARRAY[]::text[]
  )
  INTO payload_topics
  FROM jsonb_array_elements_text(p_payload -> 'retrievalTopics') AS topic(value);
  IF payload_display_name IS NULL
     OR char_length(payload_display_name) NOT BETWEEN 1 AND 160
     OR payload_display_name <> btrim(payload_display_name)
     OR left(payload_display_name, 1) IN ('.', '~')
     OR position('/' IN payload_display_name) > 0
     OR position(E'\\' IN payload_display_name) > 0
     OR position(':' IN payload_display_name) > 0
     OR payload_display_name ~ '[[:cntrl:]]'
     OR NOT public.rag_v2_immutable_retrieval_topics_are_valid(payload_topics) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner BGE v2 citation metadata is invalid'
      USING ERRCODE = '22023';
  END IF;

  -- V28 identity/data-path 검증을 그대로 재사용하되, v2-only citation metadata는 v1 closed
  -- payload에 섞지 않는다. V30 아래의 guarded write가 metadata를 같은 transaction 안에서만 bind한다.
  v1_payload := jsonb_set(
    p_payload - ARRAY['sanitizedDisplayName', 'retrievalTopics'],
    '{schemaVersion}',
    '1'::jsonb,
    true
  );
  SELECT
    staged.component_generation_id,
    staged.materialization_run_id,
    staged.state,
    staged.source_count,
    staged.chunk_count
  INTO
    staged_generation_id,
    staged_run_id,
    staged_state,
    staged_source_count,
    staged_chunk_count
  FROM public.stage_rag_v2_immutable_owner_bge_document(
    p_owner_user_id,
    p_ticket_id,
    v1_payload
  ) AS staged;
  IF staged_generation_id IS NULL
     OR staged_run_id IS NULL
     OR staged_state <> 'STAGED'
     OR staged_source_count <> 1
     OR staged_chunk_count < 1 THEN
    RAISE EXCEPTION 'immutable RAG v2 owner BGE v2 staging receipt is invalid'
      USING ERRCODE = '23514';
  END IF;

  SELECT
    source.sanitized_display_name,
    source.retrieval_topics,
    source.created_at
  INTO existing_display_name, existing_topics, source_created_at
  FROM public.rag_v2_immutable_source_revisions AS source
  WHERE source.source_revision_id = v1_payload ->> 'sourceRevisionId'
    AND source.source_id = v1_payload ->> 'sourceId'
    AND source.owner_user_id = p_owner_user_id
    AND source.source_scope = 'OWNER_PRIVATE'
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 owner BGE v2 source receipt is absent'
      USING ERRCODE = '23514';
  END IF;

  IF existing_display_name IS NULL AND existing_topics IS NULL THEN
    -- 이 function의 current transaction에서 방금 생성된 source만 metadata를 받을 수 있다.
    -- 과거 V28 STAGED row는 immutable historical record로 남겨 fail-closed한다.
    IF source_created_at IS DISTINCT FROM transaction_timestamp() THEN
      RAISE EXCEPTION 'immutable RAG v2 owner BGE v2 cannot backfill historical staging metadata'
        USING ERRCODE = '55000';
    END IF;
    UPDATE public.rag_v2_immutable_source_revisions
    SET sanitized_display_name = payload_display_name,
        retrieval_topics = payload_topics
    WHERE source_revision_id = v1_payload ->> 'sourceRevisionId'
      AND source_id = v1_payload ->> 'sourceId'
      AND owner_user_id = p_owner_user_id
      AND source_scope = 'OWNER_PRIVATE'
      AND sanitized_display_name IS NULL
      AND retrieval_topics IS NULL;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'immutable RAG v2 owner BGE v2 metadata binding conflicted'
        USING ERRCODE = '40001';
    END IF;
  ELSIF existing_display_name IS DISTINCT FROM payload_display_name
     OR existing_topics IS DISTINCT FROM payload_topics THEN
    RAISE EXCEPTION 'immutable RAG v2 owner BGE v2 metadata identity conflicted'
      USING ERRCODE = '23505';
  END IF;

  RETURN QUERY
  SELECT staged_generation_id, staged_run_id, staged_state, staged_source_count, staged_chunk_count;
END;
$stage_rag_v2_immutable_owner_bge_document_v2$;
ALTER FUNCTION stage_rag_v2_immutable_owner_bge_document_v2(text, text, jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION stage_rag_v2_immutable_owner_bge_document_v2(text, text, jsonb) FROM PUBLIC;

DO $rag_v2_owner_bge_v2_staging_writer_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    REVOKE ALL PRIVILEGES ON FUNCTION stage_rag_v2_immutable_owner_bge_document(text, text, jsonb)
      FROM decision_rag_writer;
    GRANT EXECUTE ON FUNCTION stage_rag_v2_immutable_owner_bge_document_v2(text, text, jsonb)
      TO decision_rag_writer;
  END IF;
END;
$rag_v2_owner_bge_v2_staging_writer_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION stage_rag_v2_immutable_owner_bge_document(text, text, jsonb) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION stage_rag_v2_immutable_owner_bge_document_v2(text, text, jsonb) FROM PUBLIC;
