-- owner BGE materializer는 direct table DML 없이 one-time import ticket에 묶인
-- SECURITY DEFINER boundary 하나로만 immutable v2 graph를 STAGED까지 만든다.
CREATE FUNCTION stage_rag_v2_immutable_owner_bge_document(
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
AS $stage_rag_v2_immutable_owner_bge_document$
DECLARE
  payload_hash text;
  expected_generation_id text;
  expected_run_id text;
  expected_manifest_hash text;
  payload_document_id text;
  payload_source_id text;
  payload_source_revision_id text;
  payload_source_revision_sha256 text;
  payload_raw_content_sha256 text;
  payload_normalized_document_ir_sha256 text;
  payload_canonical_text_sha256 text;
  payload_canonical_text text;
  payload_mime_type text;
  payload_parser_version text;
  payload_tokenizer_version text;
  payload_document_ir jsonb;
  payload_source_locator jsonb;
  payload_chunk jsonb;
  payload_embedding jsonb;
  payload_chunk_id text;
  payload_chunk_ordinal integer;
  payload_chunk_text text;
  payload_chunk_sha256 text;
  payload_chunk_locator jsonb;
  payload_chunk_heading_path text[];
  payload_chunk_token_count integer;
  payload_chunk_contains_table boolean;
  payload_embedding_input_hash text;
  payload_embedding_vector vector(1024);
  stage_expected_chunk_count integer;
  observed_embedding_count integer := 0;
  observed_source_text text := '';
  first_chunk_locator jsonb;
  previous_state text;
  previous_source_count integer;
  previous_chunk_count integer;
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
         'parserVersion', 'rawContentSha256', 'schemaVersion', 'sourceId',
         'sourceLocator', 'sourceRevisionId', 'sourceRevisionSha256', 'tokenizerVersion'
       )
     )
     OR NOT (p_payload ?& ARRAY[
       'canonicalText', 'canonicalTextSha256', 'chunks', 'documentId', 'documentIr',
       'embeddingProfileId', 'embeddings', 'mimeType', 'normalizedDocumentIrSha256',
       'parserVersion', 'rawContentSha256', 'schemaVersion', 'sourceId',
       'sourceLocator', 'sourceRevisionId', 'sourceRevisionSha256', 'tokenizerVersion'
     ])
     OR jsonb_typeof(p_payload -> 'schemaVersion') <> 'number'
     OR p_payload ->> 'schemaVersion' <> '1'
     OR jsonb_typeof(p_payload -> 'embeddingProfileId') <> 'string'
     OR p_payload ->> 'embeddingProfileId' <> 'bge_m3_local_1024_v1'
     OR jsonb_typeof(p_payload -> 'documentIr') <> 'object'
     OR jsonb_typeof(p_payload -> 'chunks') <> 'array'
     OR jsonb_typeof(p_payload -> 'embeddings') <> 'array' THEN
    RAISE EXCEPTION 'immutable RAG v2 owner BGE staging arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  stage_expected_chunk_count := jsonb_array_length(p_payload -> 'chunks');
  IF stage_expected_chunk_count NOT BETWEEN 1 AND 50000
     OR jsonb_array_length(p_payload -> 'embeddings') <> stage_expected_chunk_count THEN
    RAISE EXCEPTION 'immutable RAG v2 owner BGE staging cardinality is invalid'
      USING ERRCODE = '22023';
  END IF;

  payload_document_id := p_payload ->> 'documentId';
  payload_source_id := p_payload ->> 'sourceId';
  payload_source_revision_id := p_payload ->> 'sourceRevisionId';
  payload_source_revision_sha256 := p_payload ->> 'sourceRevisionSha256';
  payload_raw_content_sha256 := p_payload ->> 'rawContentSha256';
  payload_normalized_document_ir_sha256 := p_payload ->> 'normalizedDocumentIrSha256';
  payload_canonical_text_sha256 := p_payload ->> 'canonicalTextSha256';
  payload_canonical_text := p_payload ->> 'canonicalText';
  payload_mime_type := p_payload ->> 'mimeType';
  payload_parser_version := p_payload ->> 'parserVersion';
  payload_tokenizer_version := p_payload ->> 'tokenizerVersion';
  payload_document_ir := p_payload -> 'documentIr';
  payload_source_locator := p_payload -> 'sourceLocator';

  IF payload_document_id !~ '^doc_[a-z0-9][a-z0-9_-]{10,95}$'
     OR payload_source_id !~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'
     OR payload_source_revision_id !~ '^srv_[a-z0-9][a-z0-9_-]{2,95}$'
     OR payload_source_revision_sha256 !~ '^[0-9a-f]{64}$'
     OR payload_raw_content_sha256 !~ '^[0-9a-f]{64}$'
     OR payload_normalized_document_ir_sha256 !~ '^[0-9a-f]{64}$'
     OR payload_canonical_text_sha256 !~ '^[0-9a-f]{64}$'
     OR payload_canonical_text IS NULL
     OR octet_length(payload_canonical_text) NOT BETWEEN 1 AND 16777216
     OR payload_canonical_text_sha256 <> encode(digest(payload_canonical_text, 'sha256'), 'hex')
     OR payload_mime_type IS NULL
     OR payload_parser_version IS NULL
     OR payload_tokenizer_version IS NULL
     OR char_length(payload_mime_type) NOT BETWEEN 3 AND 128
     OR char_length(payload_parser_version) NOT BETWEEN 1 AND 128
     OR char_length(payload_tokenizer_version) NOT BETWEEN 1 AND 128
     OR NOT public.rag_v2_immutable_locator_is_valid(payload_source_locator)
     OR payload_document_ir ->> 'sourceId' IS DISTINCT FROM payload_source_id
     OR payload_document_ir ->> 'sourceRevisionId' IS DISTINCT FROM payload_source_revision_id
     OR payload_document_ir ->> 'mimeType' IS DISTINCT FROM payload_mime_type
     OR payload_document_ir ->> 'rawContentSha256' IS DISTINCT FROM payload_raw_content_sha256
     OR payload_document_ir ->> 'normalizedContentSha256' IS DISTINCT FROM payload_normalized_document_ir_sha256
     OR payload_document_ir -> 'parserEvidence' ->> 'parserVersion' IS DISTINCT FROM payload_parser_version
     OR public.rag_v2_immutable_document_ir_structure_is_valid(payload_document_ir) IS NOT TRUE THEN
    RAISE EXCEPTION 'immutable RAG v2 owner BGE staging payload is invalid'
      USING ERRCODE = '22023';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM public.users AS owner
    WHERE owner.user_id = p_owner_user_id
      AND owner.status = 'ACTIVE'
  ) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner BGE staging owner is invalid'
      USING ERRCODE = '22023';
  END IF;

  -- 모든 owner-document writer와 delete는 동일 key로 직렬화한다. tombstone trigger도 같은
  -- lock을 다시 취하므로 stale resume은 commit 이후에만 명확히 거부된다.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'rag-v2-immutable-owner-document|' || p_owner_user_id || '|' || payload_document_id,
      0
    )
  );
  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);

  payload_hash := encode(digest(p_payload::text, 'sha256'), 'hex');
  expected_generation_id := 'rgr_' || substr(
    encode(digest('rag-v2-immutable-owner-generation|' || p_owner_user_id || '|' || payload_hash, 'sha256'), 'hex'),
    1,
    32
  );
  expected_run_id := 'rgr_run_' || substr(
    encode(digest('rag-v2-immutable-owner-run|' || p_owner_user_id || '|' || payload_hash, 'sha256'), 'hex'),
    1,
    32
  );
  expected_manifest_hash := encode(
    digest(
      'rag-v2-immutable-owner-manifest|' || payload_source_revision_sha256 || '|' ||
      payload_canonical_text_sha256 || '|' || stage_expected_chunk_count::text,
      'sha256'
    ),
    'hex'
  );

  SELECT run.state, generation.actual_source_count, generation.actual_chunk_count
  INTO previous_state, previous_source_count, previous_chunk_count
  FROM public.rag_v2_immutable_materialization_runs AS run
  JOIN public.rag_v2_immutable_component_generations AS generation
    ON generation.component_generation_id = run.component_generation_id
  WHERE run.materialization_run_id = expected_run_id
    AND run.owner_user_id = p_owner_user_id
    AND run.component_generation_id = expected_generation_id
    AND generation.generation_hash = payload_hash
    AND generation.manifest_hash = expected_manifest_hash;
  IF FOUND THEN
    IF previous_state = 'STAGED'
       AND previous_source_count = 1
       AND previous_chunk_count = stage_expected_chunk_count
       AND EXISTS (
         SELECT 1
         FROM public.rag_v2_immutable_import_tickets AS ticket
         WHERE ticket.ticket_hash = encode(digest(p_ticket_id, 'sha256'), 'hex')
           AND ticket.owner_user_id = p_owner_user_id
           AND ticket.state = 'CONSUMED'
           AND ticket.consumer_run_id = expected_run_id
       ) THEN
      RETURN QUERY SELECT expected_generation_id, expected_run_id, 'STAGED', 1, stage_expected_chunk_count;
      RETURN;
    END IF;
    RAISE EXCEPTION 'immutable RAG v2 owner BGE staging identity conflicts'
      USING ERRCODE = '23505';
  END IF;

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
    expected_generation_id,
    p_owner_user_id,
    'OWNER_PRIVATE',
    'bge_m3_local_1024_v1',
    'STAGING',
    'PENDING',
    1,
    stage_expected_chunk_count,
    0,
    0,
    payload_hash,
    expected_manifest_hash
  );
  INSERT INTO public.rag_v2_immutable_materialization_runs (
    materialization_run_id,
    owner_user_id,
    component_generation_id,
    component_scope,
    document_id,
    state
  ) VALUES (
    expected_run_id,
    p_owner_user_id,
    expected_generation_id,
    'OWNER_PRIVATE',
    payload_document_id,
    'OPEN'
  );

  IF NOT public.consume_rag_v2_immutable_import_ticket(
    p_owner_user_id,
    p_ticket_id,
    'OWNER_IMPORT',
    'RAG_V2_OWNER_DOCUMENT_V1',
    expected_run_id
  ) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner BGE staging ticket was rejected'
      USING ERRCODE = '42501';
  END IF;

  FOR payload_chunk IN
    SELECT value
    FROM jsonb_array_elements(p_payload -> 'chunks') WITH ORDINALITY AS chunks(value, ordinal)
    ORDER BY ordinal
  LOOP
    IF jsonb_typeof(payload_chunk) <> 'object'
       OR EXISTS (
         SELECT 1
         FROM jsonb_object_keys(payload_chunk) AS chunk_key
         WHERE chunk_key NOT IN (
           'canonicalText', 'canonicalTextSha256', 'chunkId', 'chunkOrdinal',
           'containsTable', 'headingPath', 'locator', 'tokenCount'
         )
       )
       OR NOT (payload_chunk ?& ARRAY[
         'canonicalText', 'canonicalTextSha256', 'chunkId', 'chunkOrdinal',
         'containsTable', 'headingPath', 'locator', 'tokenCount'
       ])
       OR jsonb_typeof(payload_chunk -> 'headingPath') <> 'array'
       OR jsonb_typeof(payload_chunk -> 'locator') <> 'object'
       OR jsonb_typeof(payload_chunk -> 'containsTable') <> 'boolean'
       OR jsonb_typeof(payload_chunk -> 'tokenCount') <> 'number'
       OR EXISTS (
         SELECT 1
         FROM jsonb_array_elements(payload_chunk -> 'headingPath') AS heading(value)
         WHERE jsonb_typeof(heading.value) <> 'string'
       ) THEN
      RAISE EXCEPTION 'immutable RAG v2 owner BGE staging chunk is invalid'
        USING ERRCODE = '22023';
    END IF;

    payload_chunk_id := payload_chunk ->> 'chunkId';
    payload_chunk_ordinal := (payload_chunk ->> 'chunkOrdinal')::integer;
    payload_chunk_text := payload_chunk ->> 'canonicalText';
    payload_chunk_sha256 := payload_chunk ->> 'canonicalTextSha256';
    payload_chunk_locator := payload_chunk -> 'locator';
    payload_chunk_token_count := (payload_chunk ->> 'tokenCount')::integer;
    payload_chunk_contains_table := (payload_chunk ->> 'containsTable')::boolean;
    SELECT coalesce(array_agg(heading.value ORDER BY heading.ordinality), ARRAY[]::text[])
    INTO payload_chunk_heading_path
    FROM jsonb_array_elements_text(payload_chunk -> 'headingPath') WITH ORDINALITY AS heading(value, ordinality);

    IF payload_chunk_id !~ '^rag_v2_chk_[0-9a-f]{32}$'
       OR payload_chunk_ordinal NOT BETWEEN 1 AND stage_expected_chunk_count
       OR payload_chunk_text IS NULL
       OR payload_chunk_sha256 !~ '^[0-9a-f]{64}$'
       OR payload_chunk_sha256 <> encode(digest(payload_chunk_text, 'sha256'), 'hex')
       OR payload_chunk_token_count NOT BETWEEN 1 AND 600
       OR cardinality(payload_chunk_heading_path) > 12
       OR NOT public.rag_v2_immutable_locator_is_valid(payload_chunk_locator) THEN
      RAISE EXCEPTION 'immutable RAG v2 owner BGE staging chunk contract is invalid'
        USING ERRCODE = '22023';
    END IF;
    IF first_chunk_locator IS NULL THEN
      first_chunk_locator := payload_chunk_locator;
    END IF;
    IF payload_chunk_ordinal = 1 THEN
      observed_source_text := payload_chunk_text;
    ELSE
      observed_source_text := observed_source_text || E'\n\n' || payload_chunk_text;
    END IF;

  END LOOP;

  IF first_chunk_locator IS NULL
     OR payload_source_locator <> first_chunk_locator
     OR payload_canonical_text <> observed_source_text THEN
    RAISE EXCEPTION 'immutable RAG v2 owner BGE staging source projection is invalid'
      USING ERRCODE = '22023';
  END IF;

  INSERT INTO public.rag_v2_immutable_source_revisions (
    source_revision_id,
    document_id,
    source_id,
    owner_user_id,
    source_scope,
    oa_track_id,
    reserve_source,
    source_revision_sha256,
    raw_content_sha256,
    normalized_document_ir_sha256,
    canonical_text_sha256,
    document_ir,
    canonical_text,
    sanitized_display_name,
    source_locator,
    canonical_https_url,
    license_evidence_sha256,
    access_evidence_sha256,
    mime_type,
    machine_fetch_allowed,
    local_processing_allowed,
    external_embedding_allowed,
    external_generation_allowed,
    external_processing_eligible,
    parser_version,
    tokenizer_version
  ) VALUES (
    payload_source_revision_id,
    payload_document_id,
    payload_source_id,
    p_owner_user_id,
    'OWNER_PRIVATE',
    NULL,
    false,
    payload_source_revision_sha256,
    payload_raw_content_sha256,
    payload_normalized_document_ir_sha256,
    payload_canonical_text_sha256,
    payload_document_ir,
    payload_canonical_text,
    NULL,
    payload_source_locator,
    NULL,
    NULL,
    NULL,
    payload_mime_type,
    false,
    true,
    false,
    false,
    false,
    payload_parser_version,
    payload_tokenizer_version
  );

  FOR payload_chunk IN
    SELECT value
    FROM jsonb_array_elements(p_payload -> 'chunks') WITH ORDINALITY AS chunks(value, ordinal)
    ORDER BY ordinal
  LOOP
    SELECT coalesce(array_agg(heading.value ORDER BY heading.ordinality), ARRAY[]::text[])
    INTO payload_chunk_heading_path
    FROM jsonb_array_elements_text(payload_chunk -> 'headingPath') WITH ORDINALITY AS heading(value, ordinality);
    INSERT INTO public.rag_v2_immutable_chunks (
      chunk_id,
      source_revision_id,
      owner_user_id,
      source_scope,
      chunk_ordinal,
      heading_path,
      locator,
      canonical_text,
      canonical_text_sha256,
      token_count,
      contains_table
    ) VALUES (
      payload_chunk ->> 'chunkId',
      payload_source_revision_id,
      p_owner_user_id,
      'OWNER_PRIVATE',
      (payload_chunk ->> 'chunkOrdinal')::integer,
      payload_chunk_heading_path,
      payload_chunk -> 'locator',
      payload_chunk ->> 'canonicalText',
      payload_chunk ->> 'canonicalTextSha256',
      (payload_chunk ->> 'tokenCount')::integer,
      (payload_chunk ->> 'containsTable')::boolean
    );
  END LOOP;

  FOR payload_chunk IN
    SELECT value
    FROM jsonb_array_elements(p_payload -> 'chunks') WITH ORDINALITY AS chunks(value, ordinal)
    ORDER BY ordinal
  LOOP
    INSERT INTO public.rag_v2_immutable_generation_memberships (
      component_generation_id,
      chunk_id,
      source_revision_id,
      owner_user_id,
      component_scope,
      ordinal
    ) VALUES (
      expected_generation_id,
      payload_chunk ->> 'chunkId',
      payload_source_revision_id,
      p_owner_user_id,
      'OWNER_PRIVATE',
      (payload_chunk ->> 'chunkOrdinal')::integer
    );
  END LOOP;

  FOR payload_embedding IN
    SELECT value
    FROM jsonb_array_elements(p_payload -> 'embeddings') WITH ORDINALITY AS embeddings(value, ordinal)
    ORDER BY ordinal
  LOOP
    IF jsonb_typeof(payload_embedding) <> 'object'
       OR EXISTS (
         SELECT 1
         FROM jsonb_object_keys(payload_embedding) AS embedding_key
         WHERE embedding_key NOT IN ('chunkId', 'embedding', 'embeddingInputHash')
       )
       OR NOT (payload_embedding ?& ARRAY['chunkId', 'embedding', 'embeddingInputHash'])
       OR jsonb_typeof(payload_embedding -> 'embedding') <> 'array'
       OR jsonb_array_length(payload_embedding -> 'embedding') <> 1024
       OR EXISTS (
         SELECT 1
         FROM jsonb_array_elements(payload_embedding -> 'embedding') AS coordinate(value)
         WHERE jsonb_typeof(coordinate.value) <> 'number'
       ) THEN
      RAISE EXCEPTION 'immutable RAG v2 owner BGE staging embedding is invalid'
        USING ERRCODE = '22023';
    END IF;
    payload_chunk_id := payload_embedding ->> 'chunkId';
    payload_embedding_input_hash := payload_embedding ->> 'embeddingInputHash';
    IF payload_chunk_id !~ '^rag_v2_chk_[0-9a-f]{32}$'
       OR payload_embedding_input_hash !~ '^[0-9a-f]{64}$'
       OR NOT EXISTS (
         SELECT 1
         FROM public.rag_v2_immutable_generation_memberships AS membership
         WHERE membership.component_generation_id = expected_generation_id
           AND membership.chunk_id = payload_chunk_id
           AND membership.owner_user_id = p_owner_user_id
           AND membership.component_scope = 'OWNER_PRIVATE'
       ) THEN
      RAISE EXCEPTION 'immutable RAG v2 owner BGE staging embedding identity is invalid'
        USING ERRCODE = '22023';
    END IF;
    payload_embedding_vector := ((payload_embedding -> 'embedding')::text)::vector;
    INSERT INTO public.rag_v2_immutable_generation_embeddings (
      component_generation_id,
      chunk_id,
      owner_user_id,
      component_scope,
      embedding_profile_id,
      embedding_input_hash,
      context_set_hash,
      embedding
    ) VALUES (
      expected_generation_id,
      payload_chunk_id,
      p_owner_user_id,
      'OWNER_PRIVATE',
      'bge_m3_local_1024_v1',
      payload_embedding_input_hash,
      NULL,
      payload_embedding_vector
    );
    INSERT INTO public.rag_v2_immutable_embedding_cache (
      cache_id,
      owner_user_id,
      source_revision_id,
      chunk_id,
      source_scope,
      embedding_profile_id,
      embedding_input_hash,
      context_set_hash,
      embedding
    ) VALUES (
      'rgr_cache_' || substr(
        encode(digest('rag-v2-immutable-owner-cache|' || expected_generation_id || '|' || payload_chunk_id, 'sha256'), 'hex'),
        1,
        32
      ),
      p_owner_user_id,
      payload_source_revision_id,
      payload_chunk_id,
      'OWNER_PRIVATE',
      'bge_m3_local_1024_v1',
      payload_embedding_input_hash,
      NULL,
      payload_embedding_vector
    );
    INSERT INTO public.rag_v2_immutable_embedding_receipts (
      receipt_id,
      materialization_run_id,
      owner_user_id,
      source_scope,
      component_generation_id,
      chunk_id,
      embedding_profile_id,
      embedding_input_hash,
      context_set_hash,
      reuse_state
    ) VALUES (
      'rgr_emb_' || substr(
        encode(digest('rag-v2-immutable-owner-embedding-receipt|' || expected_run_id || '|' || payload_chunk_id, 'sha256'), 'hex'),
        1,
        32
      ),
      expected_run_id,
      p_owner_user_id,
      'OWNER_PRIVATE',
      expected_generation_id,
      payload_chunk_id,
      'bge_m3_local_1024_v1',
      payload_embedding_input_hash,
      NULL,
      'NEW'
    );
    observed_embedding_count := observed_embedding_count + 1;
  END LOOP;

  IF observed_embedding_count <> stage_expected_chunk_count THEN
    RAISE EXCEPTION 'immutable RAG v2 owner BGE staging embedding count is invalid'
      USING ERRCODE = '22023';
  END IF;

  INSERT INTO public.rag_v2_immutable_source_receipts (
    receipt_id,
    materialization_run_id,
    owner_user_id,
    source_scope,
    source_revision_id,
    raw_content_sha256,
    canonical_text_sha256,
    reuse_state
  ) VALUES (
    'rgr_src_' || substr(
      encode(digest('rag-v2-immutable-owner-source-receipt|' || expected_run_id, 'sha256'), 'hex'),
      1,
      32
    ),
    expected_run_id,
    p_owner_user_id,
    'OWNER_PRIVATE',
    payload_source_revision_id,
    payload_raw_content_sha256,
    payload_canonical_text_sha256,
    'NEW'
  );
  FOR payload_chunk IN
    SELECT value
    FROM jsonb_array_elements(p_payload -> 'chunks') WITH ORDINALITY AS chunks(value, ordinal)
    ORDER BY ordinal
  LOOP
    INSERT INTO public.rag_v2_immutable_chunk_receipts (
      receipt_id,
      materialization_run_id,
      owner_user_id,
      source_scope,
      source_revision_id,
      chunk_id,
      canonical_text_sha256,
      reuse_state
    ) VALUES (
      'rgr_chk_' || substr(
        encode(digest('rag-v2-immutable-owner-chunk-receipt|' || expected_run_id || '|' || (payload_chunk ->> 'chunkId'), 'sha256'), 'hex'),
        1,
        32
      ),
      expected_run_id,
      p_owner_user_id,
      'OWNER_PRIVATE',
      payload_source_revision_id,
      payload_chunk ->> 'chunkId',
      payload_chunk ->> 'canonicalTextSha256',
      'NEW'
    );
  END LOOP;

  UPDATE public.rag_v2_immutable_component_generations AS generation
  SET actual_source_count = 1,
      actual_chunk_count = stage_expected_chunk_count
  WHERE generation.component_generation_id = expected_generation_id
    AND generation.owner_user_id = p_owner_user_id
    AND generation.component_scope = 'OWNER_PRIVATE'
    AND generation.state = 'STAGING'
    AND generation.evaluation_status = 'PENDING';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 owner BGE staging generation transition failed'
      USING ERRCODE = '23514';
  END IF;
  UPDATE public.rag_v2_immutable_materialization_runs AS run
  SET state = 'STAGED'
  WHERE run.materialization_run_id = expected_run_id
    AND run.owner_user_id = p_owner_user_id
    AND run.component_scope = 'OWNER_PRIVATE'
    AND run.state = 'OPEN';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 owner BGE staging run transition failed'
      USING ERRCODE = '23514';
  END IF;

  RETURN QUERY SELECT expected_generation_id, expected_run_id, 'STAGED', 1, stage_expected_chunk_count;
END;
$stage_rag_v2_immutable_owner_bge_document$;
ALTER FUNCTION stage_rag_v2_immutable_owner_bge_document(text, text, jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION stage_rag_v2_immutable_owner_bge_document(text, text, jsonb) FROM PUBLIC;

DO $rag_v2_owner_bge_staging_writer_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    GRANT EXECUTE ON FUNCTION stage_rag_v2_immutable_owner_bge_document(text, text, jsonb)
      TO decision_rag_writer;
  END IF;
END;
$rag_v2_owner_bge_staging_writer_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION stage_rag_v2_immutable_owner_bge_document(text, text, jsonb) FROM PUBLIC;
