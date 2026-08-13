-- Pre-S5 owner library는 public Voyage profile과 분리된 user-selected profile을 가진다.
-- historical V25/V28/V30 함수는 보존하고 V2 ticket/V3 writer만 현재 role에 노출한다.

ALTER TABLE public.rag_v2_immutable_import_tickets
  ADD COLUMN embedding_profile_id text;

ALTER TABLE public.rag_v2_immutable_import_tickets
  DROP CONSTRAINT rag_v2_immutable_import_ticket_policy_check,
  ADD CONSTRAINT rag_v2_immutable_import_ticket_policy_check
    CHECK (
      (policy_version = 'RAG_V2_OWNER_DOCUMENT_V1' AND embedding_profile_id IS NULL)
      OR (
        policy_version = 'RAG_V2_OWNER_DOCUMENT_V2'
        AND embedding_profile_id IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1')
      )
    );

ALTER TABLE public.rag_v2_immutable_bundles
  ADD COLUMN owner_embedding_profile_id text;

UPDATE public.rag_v2_immutable_bundles AS bundle
SET owner_embedding_profile_id = CASE
  WHEN owner_generation.actual_chunk_count > 0 THEN owner_generation.embedding_profile_id
  ELSE NULL
END
FROM public.rag_v2_immutable_component_generations AS owner_generation
WHERE owner_generation.component_generation_id = bundle.owner_private_generation_id
  AND owner_generation.owner_user_id = bundle.owner_user_id
  AND owner_generation.component_scope = 'OWNER_PRIVATE';

ALTER TABLE public.rag_v2_immutable_bundles
  DROP CONSTRAINT rag_v2_immutable_bundle_owner_profile_fkey,
  ADD CONSTRAINT rag_v2_immutable_bundle_owner_profile_check
    CHECK (
      owner_embedding_profile_id IS NULL
      OR owner_embedding_profile_id IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1')
    ),
  ADD CONSTRAINT rag_v2_immutable_bundle_owner_profile_fkey
    FOREIGN KEY (
      owner_private_generation_id,
      owner_embedding_profile_id,
      owner_private_component_scope,
      owner_partition_key
    )
    REFERENCES public.rag_v2_immutable_component_generations (
      component_generation_id,
      embedding_profile_id,
      component_scope,
      owner_partition_key
    )
    ON DELETE RESTRICT,
  ADD CONSTRAINT rag_v2_immutable_bundle_owner_scope_fkey
    FOREIGN KEY (
      owner_private_generation_id,
      owner_private_component_scope,
      owner_partition_key
    )
    REFERENCES public.rag_v2_immutable_component_generations (
      component_generation_id,
      component_scope,
      owner_partition_key
    )
    ON DELETE RESTRICT;

ALTER TABLE public.rag_v2_retrieval_scope_claims
  ADD COLUMN owner_embedding_profile_id text;

UPDATE public.rag_v2_retrieval_scope_claims AS claim
SET owner_embedding_profile_id = bundle.owner_embedding_profile_id
FROM public.rag_v2_immutable_bundles AS bundle
WHERE claim.owner_bundle_id = bundle.bundle_id
  AND claim.owner_user_id = bundle.owner_user_id;

ALTER TABLE public.rag_v2_retrieval_scope_claims
  DROP CONSTRAINT rag_v2_retrieval_scope_component_check,
  ADD CONSTRAINT rag_v2_retrieval_scope_component_check
    CHECK (
      exact30_generation_id ~ '^rgr_[0-9a-f]{32}$'
      AND oa112_generation_id ~ '^rgr_[0-9a-f]{32}$'
      AND exact30_generation_id <> oa112_generation_id
      AND (
        (
          owner_private_generation_id IS NULL
          AND owner_bundle_id IS NULL
          AND owner_pointer_version >= 0
        )
        OR (
          owner_private_generation_id ~ '^rgr_[0-9a-f]{32}$'
          AND owner_bundle_id ~ '^rgb_[0-9a-f]{32}$'
          AND owner_private_generation_id <> exact30_generation_id
          AND owner_private_generation_id <> oa112_generation_id
          AND owner_pointer_version >= 1
        )
      )
    ),
  ADD CONSTRAINT rag_v2_retrieval_scope_owner_profile_check
    CHECK (
      (owner_private_generation_id IS NULL AND owner_embedding_profile_id IS NULL)
      OR (
        owner_private_generation_id IS NOT NULL
        AND owner_embedding_profile_id IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1')
      )
    );

CREATE FUNCTION public.issue_rag_v2_immutable_import_ticket_v2(
  p_owner_user_id text,
  p_ticket_id text,
  p_operation text,
  p_policy_version text,
  p_embedding_profile_id text
)
RETURNS timestamptz
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $issue_rag_v2_immutable_import_ticket_v2$
DECLARE
  issued_at timestamptz := clock_timestamp();
  ticket_digest text;
  active_owner_profile text;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_ticket_id !~ '^rti_[0-9a-f]{32}$'
     OR p_operation <> 'OWNER_IMPORT'
     OR p_policy_version <> 'RAG_V2_OWNER_DOCUMENT_V2'
     OR p_embedding_profile_id NOT IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1')
     OR NOT EXISTS (
       SELECT 1 FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 import ticket v2 arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-owner-library|' || p_owner_user_id, 0)
  );
  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  SELECT bundle.owner_embedding_profile_id
  INTO active_owner_profile
  FROM public.rag_v2_immutable_owner_bundle_pointers AS pointer
  JOIN public.rag_v2_immutable_bundles AS bundle
    ON bundle.bundle_id = pointer.active_bundle_id
   AND bundle.owner_user_id = pointer.owner_user_id
  WHERE pointer.owner_user_id = p_owner_user_id
    AND pointer.state = 'READY'
    AND bundle.state = 'ACTIVE';
  IF active_owner_profile IS NOT NULL
     AND active_owner_profile IS DISTINCT FROM p_embedding_profile_id THEN
    RAISE EXCEPTION 'immutable RAG v2 owner library profile is locked'
      USING ERRCODE = '22023';
  END IF;

  ticket_digest := encode(digest(p_ticket_id, 'sha256'), 'hex');
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-ticket|' || ticket_digest, 0)
  );
  INSERT INTO public.rag_v2_immutable_import_tickets (
    ticket_hash, owner_user_id, operation, policy_version,
    embedding_profile_id, state, issued_at, expires_at
  ) VALUES (
    ticket_digest, p_owner_user_id, p_operation, p_policy_version,
    p_embedding_profile_id, 'ISSUED', issued_at, issued_at + interval '5 minutes'
  );
  RETURN issued_at + interval '5 minutes';
END;
$issue_rag_v2_immutable_import_ticket_v2$;
ALTER FUNCTION public.issue_rag_v2_immutable_import_ticket_v2(text, text, text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.issue_rag_v2_immutable_import_ticket_v2(text, text, text, text, text) FROM PUBLIC;

CREATE FUNCTION public.consume_rag_v2_immutable_import_ticket_v2(
  p_owner_user_id text,
  p_ticket_id text,
  p_operation text,
  p_policy_version text,
  p_embedding_profile_id text,
  p_materialization_run_id text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $consume_rag_v2_immutable_import_ticket_v2$
DECLARE
  ticket_digest text;
  consumed boolean := false;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_ticket_id !~ '^rti_[0-9a-f]{32}$'
     OR p_operation <> 'OWNER_IMPORT'
     OR p_policy_version <> 'RAG_V2_OWNER_DOCUMENT_V2'
     OR p_embedding_profile_id NOT IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1')
     OR p_materialization_run_id !~ '^rgr_run_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'immutable RAG v2 import ticket v2 consume arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  ticket_digest := encode(digest(p_ticket_id, 'sha256'), 'hex');
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-ticket|' || ticket_digest, 0)
  );
  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  IF NOT EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_materialization_runs AS run
    JOIN public.rag_v2_immutable_component_generations AS generation
      ON generation.component_generation_id = run.component_generation_id
    WHERE run.materialization_run_id = p_materialization_run_id
      AND run.owner_user_id = p_owner_user_id
      AND run.component_scope = 'OWNER_PRIVATE'
      AND run.state IN ('OPEN', 'STAGED')
      AND generation.embedding_profile_id = p_embedding_profile_id
  ) THEN
    RETURN false;
  END IF;
  UPDATE public.rag_v2_immutable_import_tickets
  SET state = 'CONSUMED', consumed_at = clock_timestamp(),
      consumer_run_id = p_materialization_run_id
  WHERE ticket_hash = ticket_digest
    AND owner_user_id = p_owner_user_id
    AND operation = p_operation
    AND policy_version = p_policy_version
    AND embedding_profile_id = p_embedding_profile_id
    AND state = 'ISSUED'
    AND consumed_at IS NULL
    AND expires_at > statement_timestamp()
  RETURNING true INTO consumed;
  RETURN coalesce(consumed, false);
END;
$consume_rag_v2_immutable_import_ticket_v2$;
ALTER FUNCTION public.consume_rag_v2_immutable_import_ticket_v2(text, text, text, text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.consume_rag_v2_immutable_import_ticket_v2(text, text, text, text, text, text) FROM PUBLIC;

-- V3 reuses all V28/V30 structural/hash/vector validation. The compatibility ticket projection
-- exists only inside this transaction; the committed ticket remains V2 and profile-bound.
CREATE FUNCTION public.stage_rag_v2_immutable_owner_document_v3(
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
AS $stage_rag_v2_immutable_owner_document_v3$
#variable_conflict use_column
DECLARE
  selected_profile text;
  ticket_digest text;
  legacy_payload jsonb;
  legacy_embeddings jsonb;
  staged_generation_id text;
  staged_run_id text;
  staged_state text;
  staged_source_count integer;
  staged_chunk_count integer;
  voyage_generation_hash text;
  voyage_generation_id text;
  voyage_run_id text;
  voyage_manifest_hash text;
  payload_source_revision_id text;
  payload_document_id text;
  payload_chunk jsonb;
  voyage_embedding jsonb;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_ticket_id !~ '^rti_[0-9a-f]{32}$'
     OR p_payload IS NULL
     OR jsonb_typeof(p_payload) <> 'object'
     OR p_payload ->> 'schemaVersion' <> '3'
     OR p_payload ->> 'embeddingProfileId' NOT IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1') THEN
    RAISE EXCEPTION 'immutable RAG v2 owner v3 staging arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  selected_profile := p_payload ->> 'embeddingProfileId';
  IF selected_profile = 'voyage_context_4_1024_v1'
     AND current_setting('app.rag_v2_owner_voyage_completion', true) <> 'enabled' THEN
    RAISE EXCEPTION 'immutable RAG v2 owner Voyage requires atomic completion'
      USING ERRCODE = '42501';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-owner-library|' || p_owner_user_id, 0)
  );
  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  IF selected_profile = 'bge_m3_local_1024_v1'
     AND EXISTS (
       SELECT 1
       FROM public.rag_v2_owner_voyage_import_attempts AS attempt
       WHERE attempt.owner_user_id = p_owner_user_id
         AND attempt.state = 'ATTEMPTED'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner library has an active Voyage attempt'
      USING ERRCODE = '55000';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_source_revisions AS source
    JOIN public.rag_v2_immutable_materialization_runs AS run
      ON run.owner_user_id = source.owner_user_id
     AND run.document_id = source.document_id
     AND run.component_scope = 'OWNER_PRIVATE'
    JOIN public.rag_v2_immutable_component_generations AS generation
      ON generation.component_generation_id = run.component_generation_id
    WHERE source.owner_user_id = p_owner_user_id
      AND source.source_scope = 'OWNER_PRIVATE'
      AND generation.embedding_profile_id <> selected_profile
  ) OR EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_owner_bundle_pointers AS pointer
    JOIN public.rag_v2_immutable_bundles AS bundle
      ON bundle.bundle_id = pointer.active_bundle_id
     AND bundle.owner_user_id = pointer.owner_user_id
    WHERE pointer.owner_user_id = p_owner_user_id
      AND pointer.state = 'READY'
      AND bundle.owner_embedding_profile_id IS NOT NULL
      AND bundle.owner_embedding_profile_id <> selected_profile
  ) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner library profile is locked'
      USING ERRCODE = '22023';
  END IF;

  ticket_digest := encode(digest(p_ticket_id, 'sha256'), 'hex');
  PERFORM 1
  FROM public.rag_v2_immutable_import_tickets AS ticket
  WHERE ticket.ticket_hash = ticket_digest
    AND ticket.owner_user_id = p_owner_user_id
    AND ticket.operation = 'OWNER_IMPORT'
    AND ticket.policy_version = 'RAG_V2_OWNER_DOCUMENT_V2'
    AND ticket.embedding_profile_id = selected_profile
    AND ticket.state = 'ISSUED'
    AND ticket.consumed_at IS NULL
    AND ticket.expires_at > statement_timestamp()
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 owner v3 ticket was rejected'
      USING ERRCODE = '42501';
  END IF;

  SELECT jsonb_agg(value - 'contextSetHash' ORDER BY ordinal)
  INTO legacy_embeddings
  FROM jsonb_array_elements(p_payload -> 'embeddings') WITH ORDINALITY AS values(value, ordinal);
  legacy_payload := jsonb_set(p_payload, '{schemaVersion}', '2'::jsonb, true);
  legacy_payload := jsonb_set(legacy_payload, '{embeddingProfileId}', '"bge_m3_local_1024_v1"'::jsonb, true);
  legacy_payload := jsonb_set(legacy_payload, '{embeddings}', coalesce(legacy_embeddings, '[]'::jsonb), true);

  UPDATE public.rag_v2_immutable_import_tickets
  SET policy_version = 'RAG_V2_OWNER_DOCUMENT_V1', embedding_profile_id = NULL
  WHERE ticket_hash = ticket_digest;
  SELECT staged.component_generation_id, staged.materialization_run_id,
         staged.state, staged.source_count, staged.chunk_count
  INTO staged_generation_id, staged_run_id, staged_state,
       staged_source_count, staged_chunk_count
  FROM public.stage_rag_v2_immutable_owner_bge_document_v2(
    p_owner_user_id, p_ticket_id, legacy_payload
  ) AS staged;

  IF selected_profile = 'bge_m3_local_1024_v1' THEN
    UPDATE public.rag_v2_immutable_import_tickets
    SET policy_version = 'RAG_V2_OWNER_DOCUMENT_V2', embedding_profile_id = selected_profile
    WHERE ticket_hash = ticket_digest AND consumer_run_id = staged_run_id;
    RETURN QUERY
    SELECT staged_generation_id, staged_run_id, staged_state,
           staged_source_count, staged_chunk_count;
    RETURN;
  END IF;

  payload_source_revision_id := p_payload ->> 'sourceRevisionId';
  payload_document_id := p_payload ->> 'documentId';
  voyage_generation_hash := encode(
    digest('rag-v2-owner-voyage-generation|' || p_owner_user_id || '|' || p_payload::text, 'sha256'),
    'hex'
  );
  voyage_generation_id := 'rgr_' || substr(voyage_generation_hash, 1, 32);
  voyage_run_id := 'rgr_run_' || substr(
    encode(digest('rag-v2-owner-voyage-run|' || p_owner_user_id || '|' || p_payload::text, 'sha256'), 'hex'),
    1, 32
  );
  voyage_manifest_hash := encode(
    digest('rag-v2-owner-voyage-manifest|' || payload_source_revision_id || '|' || staged_chunk_count::text, 'sha256'),
    'hex'
  );

  INSERT INTO public.rag_v2_immutable_component_generations (
    component_generation_id, owner_user_id, component_scope, embedding_profile_id,
    state, evaluation_status, expected_source_count, expected_chunk_count,
    actual_source_count, actual_chunk_count, generation_hash, manifest_hash
  ) VALUES (
    voyage_generation_id, p_owner_user_id, 'OWNER_PRIVATE', selected_profile,
    'STAGING', 'PENDING', 1, staged_chunk_count, 1, staged_chunk_count,
    voyage_generation_hash, voyage_manifest_hash
  );
  INSERT INTO public.rag_v2_immutable_materialization_runs (
    materialization_run_id, owner_user_id, component_generation_id,
    component_scope, document_id, state
  ) VALUES (
    voyage_run_id, p_owner_user_id, voyage_generation_id,
    'OWNER_PRIVATE', payload_document_id, 'STAGED'
  );
  INSERT INTO public.rag_v2_immutable_generation_memberships (
    component_generation_id, chunk_id, source_revision_id,
    owner_user_id, component_scope, ordinal
  )
  SELECT voyage_generation_id, membership.chunk_id, membership.source_revision_id,
         p_owner_user_id, 'OWNER_PRIVATE', membership.ordinal
  FROM public.rag_v2_immutable_generation_memberships AS membership
  WHERE membership.component_generation_id = staged_generation_id
  ORDER BY membership.ordinal;

  FOR voyage_embedding IN
    SELECT value
    FROM jsonb_array_elements(p_payload -> 'embeddings') WITH ORDINALITY AS values(value, ordinal)
    ORDER BY ordinal
  LOOP
    IF voyage_embedding ->> 'contextSetHash' !~ '^[0-9a-f]{64}$' THEN
      RAISE EXCEPTION 'immutable RAG v2 owner Voyage context identity is invalid'
        USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.rag_v2_immutable_generation_embeddings (
      component_generation_id, chunk_id, owner_user_id, component_scope,
      embedding_profile_id, embedding_input_hash, context_set_hash, embedding
    )
    SELECT voyage_generation_id, old.chunk_id, p_owner_user_id, 'OWNER_PRIVATE',
           selected_profile, voyage_embedding ->> 'embeddingInputHash',
           voyage_embedding ->> 'contextSetHash', old.embedding
    FROM public.rag_v2_immutable_generation_embeddings AS old
    WHERE old.component_generation_id = staged_generation_id
      AND old.chunk_id = voyage_embedding ->> 'chunkId';
    IF NOT FOUND THEN
      RAISE EXCEPTION 'immutable RAG v2 owner Voyage embedding identity is invalid'
        USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.rag_v2_immutable_embedding_cache (
      cache_id, owner_user_id, source_revision_id, chunk_id, source_scope,
      embedding_profile_id, embedding_input_hash, context_set_hash, embedding
    )
    SELECT
      'rgr_cache_' || substr(encode(digest('rag-v2-owner-voyage-cache|' || voyage_generation_id || '|' || old.chunk_id, 'sha256'), 'hex'), 1, 32),
      p_owner_user_id, payload_source_revision_id, old.chunk_id, 'OWNER_PRIVATE',
      selected_profile, voyage_embedding ->> 'embeddingInputHash',
      voyage_embedding ->> 'contextSetHash', old.embedding
    FROM public.rag_v2_immutable_generation_embeddings AS old
    WHERE old.component_generation_id = staged_generation_id
      AND old.chunk_id = voyage_embedding ->> 'chunkId';
    INSERT INTO public.rag_v2_immutable_embedding_receipts (
      receipt_id, materialization_run_id, owner_user_id, source_scope,
      component_generation_id, chunk_id, embedding_profile_id,
      embedding_input_hash, context_set_hash, reuse_state
    ) VALUES (
      'rgr_emb_' || substr(encode(digest('rag-v2-owner-voyage-embedding-receipt|' || voyage_run_id || '|' || (voyage_embedding ->> 'chunkId'), 'sha256'), 'hex'), 1, 32),
      voyage_run_id, p_owner_user_id, 'OWNER_PRIVATE', voyage_generation_id,
      voyage_embedding ->> 'chunkId', selected_profile,
      voyage_embedding ->> 'embeddingInputHash', voyage_embedding ->> 'contextSetHash', 'NEW'
    );
  END LOOP;

  INSERT INTO public.rag_v2_immutable_source_receipts (
    receipt_id, materialization_run_id, owner_user_id, source_scope,
    source_revision_id, raw_content_sha256, canonical_text_sha256, reuse_state
  )
  SELECT
    'rgr_src_' || substr(encode(digest('rag-v2-owner-voyage-source-receipt|' || voyage_run_id, 'sha256'), 'hex'), 1, 32),
    voyage_run_id, p_owner_user_id, 'OWNER_PRIVATE', source.source_revision_id,
    source.raw_content_sha256, source.canonical_text_sha256, 'NEW'
  FROM public.rag_v2_immutable_source_revisions AS source
  WHERE source.source_revision_id = payload_source_revision_id
    AND source.owner_user_id = p_owner_user_id;
  FOR payload_chunk IN SELECT value FROM jsonb_array_elements(p_payload -> 'chunks') AS values(value)
  LOOP
    INSERT INTO public.rag_v2_immutable_chunk_receipts (
      receipt_id, materialization_run_id, owner_user_id, source_scope,
      source_revision_id, chunk_id, canonical_text_sha256, reuse_state
    ) VALUES (
      'rgr_chk_' || substr(encode(digest('rag-v2-owner-voyage-chunk-receipt|' || voyage_run_id || '|' || (payload_chunk ->> 'chunkId'), 'sha256'), 'hex'), 1, 32),
      voyage_run_id, p_owner_user_id, 'OWNER_PRIVATE', payload_source_revision_id,
      payload_chunk ->> 'chunkId', payload_chunk ->> 'canonicalTextSha256', 'NEW'
    );
  END LOOP;

  UPDATE public.rag_v2_immutable_source_revisions
  SET external_embedding_allowed = true,
      external_generation_allowed = true,
      external_processing_eligible = true
  WHERE source_revision_id = payload_source_revision_id
    AND owner_user_id = p_owner_user_id
    AND source_scope = 'OWNER_PRIVATE';
  UPDATE public.rag_v2_immutable_import_tickets
  SET policy_version = 'RAG_V2_OWNER_DOCUMENT_V2',
      embedding_profile_id = selected_profile,
      consumer_run_id = voyage_run_id
  WHERE ticket_hash = ticket_digest AND consumer_run_id = staged_run_id;

  DELETE FROM public.rag_v2_immutable_embedding_receipts WHERE materialization_run_id = staged_run_id;
  DELETE FROM public.rag_v2_immutable_chunk_receipts WHERE materialization_run_id = staged_run_id;
  DELETE FROM public.rag_v2_immutable_source_receipts WHERE materialization_run_id = staged_run_id;
  DELETE FROM public.rag_v2_immutable_embedding_cache
  WHERE owner_user_id = p_owner_user_id
    AND source_revision_id = payload_source_revision_id
    AND embedding_profile_id = 'bge_m3_local_1024_v1';
  DELETE FROM public.rag_v2_immutable_generation_embeddings WHERE component_generation_id = staged_generation_id;
  DELETE FROM public.rag_v2_immutable_generation_memberships WHERE component_generation_id = staged_generation_id;
  DELETE FROM public.rag_v2_immutable_materialization_runs WHERE materialization_run_id = staged_run_id;
  DELETE FROM public.rag_v2_immutable_component_generations WHERE component_generation_id = staged_generation_id;

  RETURN QUERY SELECT voyage_generation_id, voyage_run_id, 'STAGED', 1, staged_chunk_count;
END;
$stage_rag_v2_immutable_owner_document_v3$;
ALTER FUNCTION public.stage_rag_v2_immutable_owner_document_v3(text, text, jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.stage_rag_v2_immutable_owner_document_v3(text, text, jsonb) FROM PUBLIC;

DO $pre_s5_owner_profile_initial_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    GRANT EXECUTE ON FUNCTION public.issue_rag_v2_immutable_import_ticket_v2(text, text, text, text, text)
      TO decision_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    GRANT EXECUTE ON FUNCTION public.stage_rag_v2_immutable_owner_document_v3(text, text, jsonb)
      TO decision_rag_writer;
    REVOKE ALL PRIVILEGES ON FUNCTION public.stage_rag_v2_immutable_owner_bge_document_v2(text, text, jsonb)
      FROM decision_rag_writer;
    REVOKE ALL PRIVILEGES ON FUNCTION public.stage_rag_v2_immutable_owner_bge_document(text, text, jsonb)
      FROM decision_rag_writer;
  END IF;
END;
$pre_s5_owner_profile_initial_acl$;

REVOKE ALL PRIVILEGES ON TABLE rag_v2_immutable_import_tickets FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE rag_v2_retrieval_scope_claims FROM PUBLIC;

CREATE TABLE public.rag_v2_owner_voyage_import_attempts (
  plan_sha256 text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  packet_sha256 text NOT NULL,
  approval_manifest_sha256 text NOT NULL,
  nonce_sha256 text NOT NULL,
  ticket_set_sha256 text NOT NULL,
  document_count integer NOT NULL,
  chunk_count integer NOT NULL,
  expected_input_tokens integer NOT NULL,
  provider_total_tokens integer,
  actual_cost_microusd bigint,
  state text NOT NULL,
  response_validation_leaf text,
  attempted_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  completed_at timestamptz,
  CONSTRAINT rag_v2_owner_voyage_attempt_hash_check CHECK (
    plan_sha256 ~ '^[0-9a-f]{64}$'
    AND packet_sha256 ~ '^[0-9a-f]{64}$'
    AND approval_manifest_sha256 ~ '^[0-9a-f]{64}$'
    AND nonce_sha256 ~ '^[0-9a-f]{64}$'
    AND ticket_set_sha256 ~ '^[0-9a-f]{64}$'
  ),
  CONSTRAINT rag_v2_owner_voyage_attempt_count_check CHECK (
    document_count BETWEEN 1 AND 1000
    AND chunk_count BETWEEN document_count AND 16000
    AND expected_input_tokens BETWEEN 1 AND 55000
  ),
  CONSTRAINT rag_v2_owner_voyage_attempt_state_check CHECK (
    state IN ('ATTEMPTED', 'UNKNOWN_BILLING', 'COMMITTED')
  ),
  CONSTRAINT rag_v2_owner_voyage_attempt_usage_check CHECK (
    (
      state = 'ATTEMPTED'
      AND provider_total_tokens IS NULL
      AND actual_cost_microusd IS NULL
      AND completed_at IS NULL
    )
    OR (
      state = 'UNKNOWN_BILLING'
      AND provider_total_tokens IS NULL
      AND actual_cost_microusd IS NULL
      AND completed_at IS NOT NULL
    )
    OR (
      state = 'COMMITTED'
      AND provider_total_tokens BETWEEN 1 AND 120000
      AND actual_cost_microusd >= 0
      AND completed_at IS NOT NULL
      AND response_validation_leaf IS NULL
    )
  )
);

ALTER TABLE public.rag_v2_owner_voyage_import_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rag_v2_owner_voyage_import_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY rag_v2_owner_voyage_import_attempts_flyway
  ON public.rag_v2_owner_voyage_import_attempts
  FOR ALL TO flyway USING (true) WITH CHECK (true);

CREATE FUNCTION public.reserve_rag_v2_owner_voyage_import(
  p_owner_user_id text,
  p_plan_sha256 text,
  p_packet_sha256 text,
  p_approval_manifest_sha256 text,
  p_nonce_sha256 text,
  p_ticket_set_sha256 text,
  p_ticket_ids text[],
  p_document_count integer,
  p_chunk_count integer,
  p_expected_input_tokens integer
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $reserve_rag_v2_owner_voyage_import$
DECLARE
  provided_ticket_set_sha256 text;
  authorized_ticket_count integer;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_plan_sha256 !~ '^[0-9a-f]{64}$'
     OR p_packet_sha256 !~ '^[0-9a-f]{64}$'
     OR p_approval_manifest_sha256 !~ '^[0-9a-f]{64}$'
     OR p_nonce_sha256 !~ '^[0-9a-f]{64}$'
     OR p_ticket_set_sha256 !~ '^[0-9a-f]{64}$'
     OR p_ticket_ids IS NULL
     OR cardinality(p_ticket_ids) IS DISTINCT FROM p_document_count
     OR EXISTS (
       SELECT 1 FROM unnest(p_ticket_ids) AS ticket_id
       WHERE ticket_id !~ '^rti_[0-9a-f]{32}$'
     )
     OR (
       SELECT count(DISTINCT ticket_id)
       FROM unnest(p_ticket_ids) AS ticket_id
     ) <> p_document_count
     OR p_document_count NOT BETWEEN 1 AND 1000
     OR p_chunk_count NOT BETWEEN p_document_count AND 16000
     OR p_expected_input_tokens NOT BETWEEN 1 AND 55000 THEN
    RAISE EXCEPTION 'OWNER_VOYAGE_IMPORT_TOO_LARGE'
      USING ERRCODE = '22023';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-owner-library|' || p_owner_user_id, 0)
  );
  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  provided_ticket_set_sha256 := encode(
    digest(array_to_string(p_ticket_ids, E'\n') || E'\n', 'sha256'),
    'hex'
  );
  IF provided_ticket_set_sha256 IS DISTINCT FROM p_ticket_set_sha256 THEN
    RAISE EXCEPTION 'immutable RAG v2 owner Voyage ticket set is invalid'
      USING ERRCODE = '22023';
  END IF;
  SELECT count(*)::integer
  INTO authorized_ticket_count
  FROM public.rag_v2_immutable_import_tickets AS ticket
  WHERE ticket.owner_user_id = p_owner_user_id
    AND ticket.operation = 'OWNER_IMPORT'
    AND ticket.policy_version = 'RAG_V2_OWNER_DOCUMENT_V2'
    AND ticket.embedding_profile_id = 'voyage_context_4_1024_v1'
    AND ticket.state = 'ISSUED'
    AND ticket.consumed_at IS NULL
    AND ticket.expires_at > statement_timestamp()
    AND ticket.ticket_hash = ANY (
      ARRAY(
        SELECT encode(digest(ticket_id, 'sha256'), 'hex')
        FROM unnest(p_ticket_ids) AS ticket_id
      )
    );
  IF authorized_ticket_count <> p_document_count THEN
    RAISE EXCEPTION 'immutable RAG v2 owner Voyage ticket authorization is absent'
      USING ERRCODE = '42501';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_source_revisions AS source
    JOIN public.rag_v2_immutable_materialization_runs AS run
      ON run.owner_user_id = source.owner_user_id
     AND run.document_id = source.document_id
     AND run.component_scope = 'OWNER_PRIVATE'
    JOIN public.rag_v2_immutable_component_generations AS generation
      ON generation.component_generation_id = run.component_generation_id
    WHERE source.owner_user_id = p_owner_user_id
      AND source.source_scope = 'OWNER_PRIVATE'
      AND generation.embedding_profile_id <> 'voyage_context_4_1024_v1'
  ) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner library profile is locked'
      USING ERRCODE = '22023';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_consent_events AS consent
    WHERE consent.owner_user_id = p_owner_user_id
      AND consent.public_consent_event_id IS NOT NULL
      AND consent.policy_digest IS NOT NULL
      AND consent.processor_set_digest IS NOT NULL
      AND consent.action = 'GRANT'
      AND NOT EXISTS (
        SELECT 1
        FROM public.rag_v2_immutable_consent_events AS newer
        WHERE newer.owner_user_id = consent.owner_user_id
          AND newer.public_consent_event_id IS NOT NULL
          AND (newer.created_at, newer.consent_event_id) > (consent.created_at, consent.consent_event_id)
      )
  ) OR EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_owner_bundle_pointers AS pointer
    JOIN public.rag_v2_immutable_bundles AS bundle
      ON bundle.bundle_id = pointer.active_bundle_id
     AND bundle.owner_user_id = pointer.owner_user_id
    WHERE pointer.owner_user_id = p_owner_user_id
      AND pointer.state = 'READY'
      AND bundle.owner_embedding_profile_id IS NOT NULL
      AND bundle.owner_embedding_profile_id IS DISTINCT FROM 'voyage_context_4_1024_v1'
  ) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner Voyage authorization is absent'
      USING ERRCODE = '42501';
  END IF;
  INSERT INTO public.rag_v2_owner_voyage_import_attempts (
    plan_sha256, owner_user_id, packet_sha256, approval_manifest_sha256,
    nonce_sha256, ticket_set_sha256, document_count, chunk_count,
    expected_input_tokens, state
  ) VALUES (
    p_plan_sha256, p_owner_user_id, p_packet_sha256, p_approval_manifest_sha256,
    p_nonce_sha256, p_ticket_set_sha256, p_document_count, p_chunk_count,
    p_expected_input_tokens, 'ATTEMPTED'
  );
  RETURN true;
END;
$reserve_rag_v2_owner_voyage_import$;
ALTER FUNCTION public.reserve_rag_v2_owner_voyage_import(text, text, text, text, text, text, text[], integer, integer, integer) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.reserve_rag_v2_owner_voyage_import(text, text, text, text, text, text, text[], integer, integer, integer) FROM PUBLIC;

CREATE FUNCTION public.complete_rag_v2_owner_voyage_import(
  p_owner_user_id text,
  p_plan_sha256 text,
  p_packet_sha256 text,
  p_items jsonb,
  p_expected_input_tokens integer,
  p_provider_total_tokens integer,
  p_actual_cost_microusd bigint
)
RETURNS TABLE (
  component_generation_id text,
  document_count integer,
  chunk_count integer,
  state text
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $complete_rag_v2_owner_voyage_import$
#variable_conflict use_column
DECLARE
  attempt public.rag_v2_owner_voyage_import_attempts%ROWTYPE;
  item jsonb;
  staged record;
  completed_documents integer := 0;
  completed_chunks integer := 0;
  last_generation_id text := NULL;
  provided_ticket_set_sha256 text;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR jsonb_typeof(p_items) <> 'array'
     OR p_expected_input_tokens NOT BETWEEN 1 AND 55000
     OR p_provider_total_tokens NOT BETWEEN 1 AND 120000
     OR p_actual_cost_microusd < 0 THEN
    RAISE EXCEPTION 'immutable RAG v2 owner Voyage completion arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  SELECT * INTO attempt
  FROM public.rag_v2_owner_voyage_import_attempts
  WHERE plan_sha256 = p_plan_sha256
    AND owner_user_id = p_owner_user_id
    AND packet_sha256 = p_packet_sha256
    AND state = 'ATTEMPTED'
  FOR UPDATE;
  IF NOT FOUND
     OR attempt.expected_input_tokens <> p_expected_input_tokens
     OR jsonb_array_length(p_items) <> attempt.document_count THEN
    RAISE EXCEPTION 'immutable RAG v2 owner Voyage attempt is not completable'
      USING ERRCODE = '55000';
  END IF;
  SELECT encode(
    digest(
      coalesce(
        string_agg((value ->> 'importTicketId') || E'\n', '' ORDER BY ordinal),
        ''
      ),
      'sha256'
    ),
    'hex'
  )
  INTO provided_ticket_set_sha256
  FROM jsonb_array_elements(p_items) WITH ORDINALITY AS values(value, ordinal);
  IF provided_ticket_set_sha256 IS DISTINCT FROM attempt.ticket_set_sha256 THEN
    RAISE EXCEPTION 'immutable RAG v2 owner Voyage ticket set drifted'
      USING ERRCODE = '55000';
  END IF;

  PERFORM set_config('app.rag_v2_owner_voyage_completion', 'enabled', true);
  FOR item IN SELECT value FROM jsonb_array_elements(p_items) WITH ORDINALITY AS values(value, ordinal) ORDER BY ordinal
  LOOP
    IF jsonb_typeof(item) <> 'object'
       OR item ->> 'importTicketId' !~ '^rti_[0-9a-f]{32}$'
       OR jsonb_typeof(item -> 'stagingPayload') <> 'object' THEN
      RAISE EXCEPTION 'immutable RAG v2 owner Voyage completion item is invalid'
        USING ERRCODE = '22023';
    END IF;
    SELECT * INTO staged
    FROM public.stage_rag_v2_immutable_owner_document_v3(
      p_owner_user_id,
      item ->> 'importTicketId',
      item -> 'stagingPayload'
    );
    IF staged.state <> 'STAGED' OR staged.source_count <> 1 OR staged.chunk_count < 1 THEN
      RAISE EXCEPTION 'immutable RAG v2 owner Voyage staging was incomplete'
        USING ERRCODE = '23514';
    END IF;
    last_generation_id := staged.component_generation_id;
    completed_documents := completed_documents + 1;
    completed_chunks := completed_chunks + staged.chunk_count;
  END LOOP;
  IF completed_documents <> attempt.document_count OR completed_chunks <> attempt.chunk_count THEN
    RAISE EXCEPTION 'immutable RAG v2 owner Voyage completion cardinality drifted'
      USING ERRCODE = '23514';
  END IF;
  UPDATE public.rag_v2_owner_voyage_import_attempts
  SET state = 'COMMITTED', provider_total_tokens = p_provider_total_tokens,
      actual_cost_microusd = p_actual_cost_microusd, completed_at = clock_timestamp()
  WHERE plan_sha256 = p_plan_sha256 AND state = 'ATTEMPTED';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 owner Voyage usage commit conflicted'
      USING ERRCODE = '40001';
  END IF;
  RETURN QUERY SELECT last_generation_id, completed_documents, completed_chunks, 'STAGED';
END;
$complete_rag_v2_owner_voyage_import$;
ALTER FUNCTION public.complete_rag_v2_owner_voyage_import(text, text, text, jsonb, integer, integer, bigint) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.complete_rag_v2_owner_voyage_import(text, text, text, jsonb, integer, integer, bigint) FROM PUBLIC;

CREATE FUNCTION public.fail_rag_v2_owner_voyage_import_unknown_billing(
  p_owner_user_id text,
  p_plan_sha256 text,
  p_packet_sha256 text,
  p_response_validation_leaf text DEFAULT NULL
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $fail_rag_v2_owner_voyage_import_unknown_billing$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR (p_response_validation_leaf IS NOT NULL AND p_response_validation_leaf NOT IN (
       'STATUS', 'BODY_SIZE_OR_TYPE', 'BODY_UTF8_OR_JSON', 'ENVELOPE_REQUIRED_FIELDS',
       'MODEL', 'USAGE', 'GROUP_COUNT', 'GROUP_FIELDS_OR_INDEX', 'CHUNK_COUNT',
       'CHUNK_FIELDS_OR_INDEX', 'CHUNK_TEXT', 'VECTOR_DIMENSION', 'VECTOR_NUMBER',
       'VECTOR_FINITE', 'VECTOR_NORM', 'COST_CAP'
     )) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner Voyage failure receipt is invalid'
      USING ERRCODE = '22023';
  END IF;
  UPDATE public.rag_v2_owner_voyage_import_attempts
  SET state = 'UNKNOWN_BILLING', response_validation_leaf = p_response_validation_leaf,
      completed_at = clock_timestamp()
  WHERE owner_user_id = p_owner_user_id
    AND plan_sha256 = p_plan_sha256
    AND packet_sha256 = p_packet_sha256
    AND (
      state = 'ATTEMPTED'
      OR (
        state = 'UNKNOWN_BILLING'
        AND response_validation_leaf IS NULL
        AND p_response_validation_leaf IS NOT NULL
      )
    );
  RETURN FOUND;
END;
$fail_rag_v2_owner_voyage_import_unknown_billing$;
ALTER FUNCTION public.fail_rag_v2_owner_voyage_import_unknown_billing(text, text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.fail_rag_v2_owner_voyage_import_unknown_billing(text, text, text, text) FROM PUBLIC;

DO $pre_s5_owner_voyage_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    GRANT EXECUTE ON FUNCTION public.reserve_rag_v2_owner_voyage_import(text, text, text, text, text, text, text[], integer, integer, integer)
      TO decision_rag_writer;
    GRANT EXECUTE ON FUNCTION public.complete_rag_v2_owner_voyage_import(text, text, text, jsonb, integer, integer, bigint)
      TO decision_rag_writer;
    GRANT EXECUTE ON FUNCTION public.fail_rag_v2_owner_voyage_import_unknown_billing(text, text, text, text)
      TO decision_rag_writer;
  END IF;
END;
$pre_s5_owner_voyage_acl$;

REVOKE ALL PRIVILEGES ON TABLE public.rag_v2_owner_voyage_import_attempts FROM PUBLIC;
CREATE OR REPLACE FUNCTION activate_rag_v2_immutable_owner_bundle(
  p_owner_user_id text,
  p_bundle_id text,
  p_expected_active_bundle_id text,
  p_expected_bundle_version bigint,
  p_activation_receipt_id text,
  p_activation_kind text DEFAULT 'OWNER_BUNDLE'
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $activate_rag_v2_immutable_owner_bundle$
DECLARE
  pointer_record public.rag_v2_immutable_owner_bundle_pointers%ROWTYPE;
  public_pointer public.rag_v2_immutable_public_bundle_pointers%ROWTYPE;
  bundle_record public.rag_v2_immutable_bundles%ROWTYPE;
  owner_generation public.rag_v2_immutable_component_generations%ROWTYPE;
  owner_source_count bigint;
  owner_chunk_count bigint;
  owner_embedding_count bigint;
  activation_timestamp timestamptz := clock_timestamp();
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_admin'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_bundle_id !~ '^rgb_[0-9a-f]{32}$'
     OR (p_expected_active_bundle_id IS NOT NULL AND p_expected_active_bundle_id !~ '^rgb_[0-9a-f]{32}$')
     OR p_expected_bundle_version < 0
     OR p_activation_receipt_id !~ '^rgr_act_[0-9a-f]{32}$'
     OR p_activation_kind NOT IN ('OWNER_BUNDLE', 'OWNER_DELETE_REPLACEMENT')
     OR NOT EXISTS (
       SELECT 1 FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner bundle activation arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  -- owner bundle activation도 public pointer를 읽기만 하므로 별도 transaction-local capability로 제한한다.
  PERFORM set_config('app.rag_admin_maintenance', 'owner_bundle_activation', true);
  -- public base replacement과 같은 lock을 사용해 owner→public pointer lock 순서를 직렬화한다.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-bundle-activation', 0)
  );
  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  INSERT INTO public.rag_v2_immutable_owner_bundle_pointers (
    owner_user_id, state, active_bundle_id, bundle_version
  )
  VALUES (p_owner_user_id, 'ABSENT', NULL, 0)
  ON CONFLICT (owner_user_id) DO NOTHING;
  SELECT * INTO pointer_record
  FROM public.rag_v2_immutable_owner_bundle_pointers
  WHERE owner_user_id = p_owner_user_id
  FOR UPDATE;
  IF pointer_record.bundle_version IS DISTINCT FROM p_expected_bundle_version
     OR pointer_record.active_bundle_id IS DISTINCT FROM p_expected_active_bundle_id THEN
    RAISE EXCEPTION 'immutable RAG v2 owner pointer CAS failed'
      USING ERRCODE = '40001';
  END IF;

  SELECT * INTO public_pointer
  FROM public.rag_v2_immutable_public_bundle_pointers
  WHERE state_id = 'default';
  -- 동일 advisory lock 아래에서 읽으므로 owner activation이 public pointer row lock까지 획득할 필요는 없다.
  -- 이로써 owner marker에는 public pointer UPDATE RLS capability를 부여하지 않는다.
  IF NOT FOUND OR public_pointer.state <> 'ACTIVE' THEN
    RAISE EXCEPTION 'immutable RAG v2 public base is not active'
      USING ERRCODE = '23514';
  END IF;

  SELECT * INTO bundle_record
  FROM public.rag_v2_immutable_bundles
  WHERE bundle_id = p_bundle_id
    AND owner_user_id = p_owner_user_id
  FOR UPDATE;
  IF NOT FOUND
     OR bundle_record.state <> 'EVALUATED'
     OR bundle_record.evaluation_status <> 'PASSED'
     OR bundle_record.exact30_generation_id IS DISTINCT FROM public_pointer.exact30_generation_id
     OR bundle_record.oa112_generation_id IS DISTINCT FROM public_pointer.oa112_generation_id
     OR bundle_record.embedding_profile_id IS DISTINCT FROM public_pointer.embedding_profile_id THEN
    RAISE EXCEPTION 'immutable RAG v2 bundle is not eligible for the active public base'
      USING ERRCODE = '23514';
  END IF;
  SELECT * INTO owner_generation
  FROM public.rag_v2_immutable_component_generations
  WHERE component_generation_id = bundle_record.owner_private_generation_id
  FOR UPDATE;
  SELECT
    COUNT(DISTINCT membership.source_revision_id),
    COUNT(membership.chunk_id),
    COUNT(embedding.chunk_id)
  INTO owner_source_count, owner_chunk_count, owner_embedding_count
  FROM public.rag_v2_immutable_generation_memberships AS membership
  LEFT JOIN public.rag_v2_immutable_generation_embeddings AS embedding
    ON embedding.component_generation_id = membership.component_generation_id
   AND embedding.chunk_id = membership.chunk_id
  WHERE membership.component_generation_id = bundle_record.owner_private_generation_id;
  -- V59에서 생성된 non-empty owner bundle은 generation identity로 profile을 전방 보정한다.
  -- 빈 replacement bundle은 NULL을 유지해 마지막 문서 삭제 뒤 library profile 잠금을 해제한다.
  IF bundle_record.owner_embedding_profile_id IS NULL AND owner_chunk_count > 0 THEN
    UPDATE public.rag_v2_immutable_bundles
    SET owner_embedding_profile_id = owner_generation.embedding_profile_id
    WHERE bundle_id = p_bundle_id
    RETURNING * INTO bundle_record;
  END IF;
  IF NOT FOUND
     OR owner_generation.component_scope <> 'OWNER_PRIVATE'
     OR owner_generation.owner_user_id IS DISTINCT FROM p_owner_user_id
     OR (
       owner_chunk_count > 0
       AND owner_generation.embedding_profile_id IS DISTINCT FROM bundle_record.owner_embedding_profile_id
     )
     OR owner_generation.state <> 'EVALUATED'
     OR owner_generation.evaluation_status <> 'PASSED'
     OR owner_generation.expected_source_count <> owner_source_count
     OR owner_generation.expected_chunk_count <> owner_chunk_count
     OR owner_generation.actual_source_count <> owner_source_count
     OR owner_generation.actual_chunk_count <> owner_chunk_count
     OR owner_embedding_count <> owner_chunk_count
     OR EXISTS (
       SELECT 1
       FROM public.rag_v2_immutable_generation_memberships AS membership
       JOIN public.rag_v2_immutable_source_revisions AS source
         ON source.source_revision_id = membership.source_revision_id
       WHERE membership.component_generation_id = owner_generation.component_generation_id
         AND (
           membership.component_scope <> 'OWNER_PRIVATE'
           OR membership.owner_user_id IS DISTINCT FROM p_owner_user_id
           OR source.source_scope <> 'OWNER_PRIVATE'
           OR source.owner_user_id IS DISTINCT FROM p_owner_user_id
     )
     OR EXISTS (
       SELECT 1
       FROM public.rag_v2_immutable_generation_embeddings AS embedding
       WHERE embedding.component_generation_id = owner_generation.component_generation_id
         AND (
           embedding.component_scope <> 'OWNER_PRIVATE'
           OR embedding.owner_user_id IS DISTINCT FROM p_owner_user_id
           OR embedding.embedding_profile_id IS DISTINCT FROM owner_generation.embedding_profile_id
         )
     )
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 owner component scope is invalid'
      USING ERRCODE = '23514';
  END IF;
  IF bundle_record.owner_embedding_profile_id = 'voyage_context_4_1024_v1'
     AND (
       EXISTS (
         SELECT 1
         FROM public.rag_v2_immutable_generation_memberships AS membership
         JOIN public.rag_v2_immutable_source_revisions AS source
           ON source.source_revision_id = membership.source_revision_id
         WHERE membership.component_generation_id = owner_generation.component_generation_id
           AND NOT source.external_processing_eligible
       )
       OR coalesce((
         SELECT consent.action = 'GRANT'
         FROM public.rag_v2_immutable_consent_events AS consent
         WHERE consent.owner_user_id = p_owner_user_id
           AND consent.policy_version = 'EXTERNAL_AI_RAG_V2'
         ORDER BY consent.created_at DESC, consent.consent_event_id DESC
         LIMIT 1
       ), false) = false
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 Voyage owner consent or safety gate is invalid'
      USING ERRCODE = '23514';
  END IF;

  UPDATE public.rag_v2_immutable_bundles
  SET state = 'SUPERSEDED'
  WHERE bundle_id = pointer_record.active_bundle_id
    AND state = 'ACTIVE';
  UPDATE public.rag_v2_immutable_bundles
  SET state = 'ACTIVE', activated_at = activation_timestamp
  WHERE bundle_id = p_bundle_id;
  UPDATE public.rag_v2_immutable_owner_bundle_pointers
  SET state = 'READY',
      active_bundle_id = p_bundle_id,
      bundle_version = pointer_record.bundle_version + 1,
      updated_at = activation_timestamp
  WHERE owner_user_id = p_owner_user_id;
  INSERT INTO public.rag_v2_immutable_activation_receipts (
    activation_receipt_id,
    owner_user_id,
    activation_kind,
    activated_bundle_id,
    exact30_generation_id,
    oa112_generation_id,
    owner_private_generation_id,
    embedding_profile_id,
    previous_pointer_version,
    new_pointer_version,
    created_at
  )
  VALUES (
    p_activation_receipt_id,
    p_owner_user_id,
    p_activation_kind,
    p_bundle_id,
    NULL,
    NULL,
    NULL,
    bundle_record.embedding_profile_id,
    pointer_record.bundle_version,
    pointer_record.bundle_version + 1,
    activation_timestamp
  );
  PERFORM set_config('app.rag_admin_maintenance', '', true);
  RETURN pointer_record.bundle_version + 1;
END;
$activate_rag_v2_immutable_owner_bundle$;
ALTER FUNCTION activate_rag_v2_immutable_owner_bundle(text, text, text, bigint, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION activate_rag_v2_immutable_owner_bundle(text, text, text, bigint, text, text) FROM PUBLIC;
-- V60 owner overlay는 public profile과 owner library profile을 별도로 pin한다.
-- 서로 다른 vector space의 score를 비교하지 않으며 이 함수는 provider를 호출하지 않는다.

CREATE OR REPLACE FUNCTION prepare_rag_v2_immutable_owner_overlay(
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
  selected_profile_count integer := 0;
  selected_owner_profile text := NULL;
  effective_owner_profile text;
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
     OR public_pointer.embedding_profile_id NOT IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1')
     OR public_pointer.exact30_generation_id IS NULL
     OR public_pointer.oa112_generation_id IS NULL THEN
    RAISE EXCEPTION 'immutable RAG v2 public base is not active'
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
    AND staging_generation.evaluation_status = 'PENDING';

  SELECT count(DISTINCT staging_generation.embedding_profile_id)::integer,
         min(staging_generation.embedding_profile_id)
  INTO selected_profile_count, selected_owner_profile
  FROM public.rag_v2_immutable_source_revisions AS source
  JOIN public.rag_v2_immutable_materialization_runs AS run
    ON run.owner_user_id = source.owner_user_id
   AND run.component_scope = 'OWNER_PRIVATE'
   AND run.document_id = source.document_id
   AND run.state = 'STAGED'
  JOIN public.rag_v2_immutable_component_generations AS staging_generation
    ON staging_generation.component_generation_id = run.component_generation_id
   AND staging_generation.owner_user_id = source.owner_user_id
   AND staging_generation.component_scope = 'OWNER_PRIVATE'
   AND staging_generation.state = 'STAGING'
   AND staging_generation.evaluation_status = 'PENDING'
  WHERE source.source_revision_id = ANY(selected_source_revision_ids);
  IF selected_profile_count > 1 THEN
    RAISE EXCEPTION 'immutable RAG v2 owner library contains mixed profiles'
      USING ERRCODE = '23514';
  END IF;
  effective_owner_profile := coalesce(selected_owner_profile, public_pointer.embedding_profile_id);

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
        OR NOT source.local_processing_allowed
        OR (
          selected_owner_profile = 'bge_m3_local_1024_v1'
          AND (
            source.external_processing_eligible
            OR source.external_embedding_allowed
            OR source.external_generation_allowed
          )
        )
        OR (
          selected_owner_profile = 'voyage_context_4_1024_v1'
          AND (
            NOT source.external_processing_eligible
            OR NOT source.external_embedding_allowed
            OR NOT source.external_generation_allowed
          )
        )
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
            AND embedding.embedding_profile_id = selected_owner_profile
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
      || p_owner_user_id || '|' || effective_owner_profile || '|' || overlay_manifest_hash,
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
      || public_pointer.embedding_profile_id || '|' || coalesce(selected_owner_profile, '') || '|' || overlay_manifest_hash,
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
       OR existing_generation.embedding_profile_id <> effective_owner_profile
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
      effective_owner_profile,
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
      effective_owner_profile,
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
      AND embedding.embedding_profile_id = effective_owner_profile;
    SELECT count(*)::integer
    INTO selected_embedding_count
    FROM public.rag_v2_immutable_generation_embeddings AS embedding
    WHERE embedding.component_generation_id = overlay_generation_id
      AND embedding.owner_user_id = p_owner_user_id
      AND embedding.component_scope = 'OWNER_PRIVATE'
      AND embedding.embedding_profile_id = effective_owner_profile;
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
       OR existing_bundle.owner_embedding_profile_id IS DISTINCT FROM selected_owner_profile
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
      owner_embedding_profile_id,
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
      selected_owner_profile,
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

-- Python runtime은 complete Core 6 receipt set과 일부 receipt만 확보된 상태를 이미 구분한다.
-- V60은 provider 권한을 넓히지 않고 V50의 content-free row constraint만 같은 terminal enum에 맞춘다.
CREATE OR REPLACE FUNCTION s48_runtime_state_is_safe(
  p_source_family text,
  p_ingestion_mode text,
  p_status text,
  p_reason text,
  p_projection_hash text
)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog, public, pg_temp
AS $s48_runtime_state_is_safe_v60$
  SELECT COALESCE(
    (
      p_source_family IN ('OPENDART', 'ECOS')
      AND p_ingestion_mode = 'REUSE_AUTHORIZED_PROJECTION'
      AND (
        (
          p_status = 'AVAILABLE'
          AND p_reason = 'AUTHORIZED_PROJECTION_AVAILABLE'
          AND p_projection_hash ~ '^[0-9a-f]{64}$'
        )
        OR (
          p_status = 'ABSTAIN'
          AND p_reason = 'REUSE_AUTHORIZED_PROJECTION_NOT_AVAILABLE'
          AND p_projection_hash IS NULL
        )
      )
    )
    OR (
      p_source_family IN ('KIS', 'SEC_EDGAR', 'KRX')
      AND p_ingestion_mode = 'DIRECT_READ_PROBE'
      AND (
        (
          p_status = 'AVAILABLE'
          AND p_reason = 'COMPLETE_DIRECT_PROBE_SET_AVAILABLE'
          AND p_projection_hash ~ '^[0-9a-f]{64}$'
        )
        OR (
          p_status = 'ABSTAIN'
          AND p_reason IN ('APPROVAL_PACKET_REQUIRED', 'DIRECT_PROBE_RECEIPT_SET_INCOMPLETE')
          AND p_projection_hash IS NULL
        )
      )
    )
    OR (
      p_source_family = 'KOFIA'
      AND p_ingestion_mode = 'DIRECT_READ_PROBE'
      AND p_status = 'BLOCKED'
      AND p_reason = 'BLOCKED_NO_CREDENTIAL_OR_APPROVAL'
      AND p_projection_hash IS NULL
    )
    OR (
      p_source_family IN ('FINNHUB_OPTIONAL3', 'TWELVE_DATA', 'MASSIVE')
      AND p_ingestion_mode = 'DIRECT_READ_PROBE'
      AND p_status = 'BLOCKED'
      AND p_reason = 'BLOCKED_NO_CREDENTIAL_OR_ENTITLEMENT'
      AND p_projection_hash IS NULL
    ),
    false
  )
$s48_runtime_state_is_safe_v60$;
ALTER FUNCTION public.s48_runtime_state_is_safe(text, text, text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.s48_runtime_state_is_safe(text, text, text, text, text) FROM PUBLIC;

CREATE FUNCTION issue_rag_v2_retrieval_scope_v2(
  p_owner_user_id text,
  p_session_id text,
  p_allowed_topics text[]
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
AS $issue_rag_v2_retrieval_scope_v2$
#variable_conflict use_column
DECLARE
  public_pointer public.rag_v2_immutable_public_bundle_pointers%ROWTYPE;
  owner_pointer public.rag_v2_immutable_owner_bundle_pointers%ROWTYPE;
  owner_bundle public.rag_v2_immutable_bundles%ROWTYPE;
  exact_generation public.rag_v2_immutable_component_generations%ROWTYPE;
  oa_generation public.rag_v2_immutable_component_generations%ROWTYPE;
  owner_generation public.rag_v2_immutable_component_generations%ROWTYPE;
  generated_scope_claim_id text;
  claim_created_at timestamptz := transaction_timestamp();
  claim_expires_at timestamptz := transaction_timestamp() + interval '2 minutes';
  selected_owner_generation_id text := NULL;
  selected_owner_bundle_id text := NULL;
  selected_owner_profile text := NULL;
  selected_owner_pointer_version bigint := 0;
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
       SELECT 1
       FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id
         AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 retrieval scope arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  PERFORM set_config('app.rag_v2_retrieval_scope', 'enabled', true);
  SELECT * INTO public_pointer
  FROM public.rag_v2_immutable_public_bundle_pointers
  WHERE state_id = 'default';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 public bundle is not active'
      USING ERRCODE = '55000';
  END IF;

  SELECT * INTO exact_generation
  FROM public.rag_v2_immutable_component_generations
  WHERE component_generation_id = public_pointer.exact30_generation_id
    AND component_scope = 'EXACT30'
    AND owner_user_id IS NULL
    AND embedding_profile_id = public_pointer.embedding_profile_id
    AND state = 'ACTIVE'
    AND evaluation_status = 'PASSED';
  SELECT * INTO oa_generation
  FROM public.rag_v2_immutable_component_generations
  WHERE component_generation_id = public_pointer.oa112_generation_id
    AND component_scope = 'OA112'
    AND owner_user_id IS NULL
    AND embedding_profile_id = public_pointer.embedding_profile_id
    AND state = 'ACTIVE'
    AND evaluation_status = 'PASSED';
  IF NOT FOUND OR exact_generation.component_generation_id IS NULL OR oa_generation.component_generation_id IS NULL THEN
    RAISE EXCEPTION 'immutable RAG v2 active public components are invalid'
      USING ERRCODE = '55000';
  END IF;

  SELECT * INTO owner_pointer
  FROM public.rag_v2_immutable_owner_bundle_pointers
  WHERE owner_user_id = p_owner_user_id;
  IF FOUND AND owner_pointer.state IN ('BUILDING', 'FAILED') THEN
    RAISE EXCEPTION 'immutable RAG v2 owner overlay is not ready'
      USING ERRCODE = '55000';
  END IF;
  IF FOUND AND owner_pointer.state = 'READY' THEN
    SELECT * INTO owner_bundle
    FROM public.rag_v2_immutable_bundles
    WHERE bundle_id = owner_pointer.active_bundle_id
      AND owner_user_id = p_owner_user_id
      AND state = 'ACTIVE';
    IF NOT FOUND
       OR owner_bundle.exact30_generation_id IS DISTINCT FROM public_pointer.exact30_generation_id
       OR owner_bundle.oa112_generation_id IS DISTINCT FROM public_pointer.oa112_generation_id
       OR owner_bundle.embedding_profile_id IS DISTINCT FROM public_pointer.embedding_profile_id THEN
      RAISE EXCEPTION 'immutable RAG v2 owner bundle is not pinned to public base'
        USING ERRCODE = '55000';
    END IF;
    selected_owner_profile := owner_bundle.owner_embedding_profile_id;
    IF selected_owner_profile IS NOT NULL THEN
      SELECT * INTO owner_generation
      FROM public.rag_v2_immutable_component_generations
      WHERE component_generation_id = owner_bundle.owner_private_generation_id
        AND component_scope = 'OWNER_PRIVATE'
        AND owner_user_id = p_owner_user_id
        AND embedding_profile_id = selected_owner_profile
        AND state = 'ACTIVE'
        AND evaluation_status = 'PASSED';
      IF NOT FOUND OR owner_generation.actual_chunk_count < 1 THEN
        RAISE EXCEPTION 'immutable RAG v2 owner component is invalid'
          USING ERRCODE = '55000';
      END IF;
      selected_owner_generation_id := owner_generation.component_generation_id;
      selected_owner_bundle_id := owner_bundle.bundle_id;
    END IF;
    selected_owner_pointer_version := owner_pointer.bundle_version;
  ELSIF FOUND AND owner_pointer.state <> 'ABSENT' THEN
    RAISE EXCEPTION 'immutable RAG v2 owner pointer state is invalid'
      USING ERRCODE = '55000';
  ELSIF FOUND THEN
    selected_owner_pointer_version := owner_pointer.bundle_version;
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
              coalesce(selected_owner_generation_id, ''),
              public_pointer.embedding_profile_id,
              coalesce(selected_owner_profile, ''),
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
    scope_claim_id,
    owner_user_id,
    session_id,
    allowed_topics,
    exact30_generation_id,
    oa112_generation_id,
    owner_private_generation_id,
    owner_bundle_id,
    embedding_profile_id,
    owner_embedding_profile_id,
    public_pointer_version,
    owner_pointer_version,
    policy_version,
    created_at,
    expires_at
  ) VALUES (
    generated_scope_claim_id,
    p_owner_user_id,
    p_session_id,
    p_allowed_topics,
    public_pointer.exact30_generation_id,
    public_pointer.oa112_generation_id,
    selected_owner_generation_id,
    selected_owner_bundle_id,
    public_pointer.embedding_profile_id,
    selected_owner_profile,
    public_pointer.pointer_version,
    selected_owner_pointer_version,
    1,
    claim_created_at,
    claim_expires_at
  );
  RETURN QUERY
  SELECT
    generated_scope_claim_id,
    p_owner_user_id,
    p_session_id,
    public_pointer.exact30_generation_id,
    public_pointer.oa112_generation_id,
    selected_owner_generation_id,
    public_pointer.embedding_profile_id,
    selected_owner_profile,
    1::bigint,
    p_allowed_topics,
    claim_expires_at;
END;
$issue_rag_v2_retrieval_scope_v2$;
ALTER FUNCTION issue_rag_v2_retrieval_scope_v2(text, text, text[]) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION issue_rag_v2_retrieval_scope_v2(text, text, text[]) FROM PUBLIC;

-- 공개 ask 응답 bytes는 유지하되 legacy capability가 만든 claim도 owner profile을 명시적으로 pin한다.
CREATE OR REPLACE FUNCTION public.issue_rag_v2_retrieval_scope(
  p_owner_user_id text,
  p_session_id text,
  p_allowed_topics text[]
)
RETURNS TABLE (
  scope_claim_id text,
  owner_user_id text,
  session_id text,
  exact30_generation_id text,
  oa112_generation_id text,
  owner_private_generation_id text,
  embedding_profile_id text,
  policy_version bigint,
  allowed_topics text[],
  expires_at timestamptz
)
LANGUAGE sql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $issue_rag_v2_retrieval_scope_compat$
  SELECT
    issued.scope_claim_id,
    issued.owner_user_id,
    issued.session_id,
    issued.exact30_generation_id,
    issued.oa112_generation_id,
    issued.owner_private_generation_id,
    issued.embedding_profile_id,
    issued.policy_version,
    issued.allowed_topics,
    issued.expires_at
  FROM public.issue_rag_v2_retrieval_scope_v2(
    p_owner_user_id,
    p_session_id,
    p_allowed_topics
  ) AS issued;
$issue_rag_v2_retrieval_scope_compat$;
ALTER FUNCTION public.issue_rag_v2_retrieval_scope(text, text, text[]) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.issue_rag_v2_retrieval_scope(text, text, text[]) FROM PUBLIC;

CREATE FUNCTION resolve_rag_v2_retrieval_scope_v2(
  p_scope_claim_id text,
  p_owner_user_id text,
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
  owner_embedding_profile_id text,
  policy_version bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $resolve_rag_v2_retrieval_scope_v2$
#variable_conflict use_column
DECLARE
  claim_row public.rag_v2_retrieval_scope_claims%ROWTYPE;
  public_pointer public.rag_v2_immutable_public_bundle_pointers%ROWTYPE;
  owner_pointer public.rag_v2_immutable_owner_bundle_pointers%ROWTYPE;
  owner_bundle public.rag_v2_immutable_bundles%ROWTYPE;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_query'
     OR p_scope_claim_id !~ '^rvs_[0-9a-f]{32}$'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_session_id IS NULL
     OR char_length(p_session_id) NOT BETWEEN 16 AND 128
     OR p_session_id !~ '^[A-Za-z0-9._:-]+$' THEN
    RAISE EXCEPTION 'immutable RAG v2 retrieval scope resolution arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  PERFORM set_config('app.rag_v2_retrieval_scope', 'enabled', true);

  SELECT * INTO claim_row
  FROM public.rag_v2_retrieval_scope_claims
  WHERE scope_claim_id = p_scope_claim_id
    AND owner_user_id = p_owner_user_id
    AND session_id = p_session_id
    AND expires_at > statement_timestamp();
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 retrieval scope is absent or expired'
      USING ERRCODE = '55000';
  END IF;

  SELECT * INTO public_pointer
  FROM public.rag_v2_immutable_public_bundle_pointers
  WHERE state_id = 'default'
    AND state = 'ACTIVE'
    AND pointer_version = claim_row.public_pointer_version
    AND exact30_generation_id = claim_row.exact30_generation_id
    AND oa112_generation_id = claim_row.oa112_generation_id
    AND embedding_profile_id = claim_row.embedding_profile_id;
  IF NOT FOUND
     OR NOT EXISTS (
       SELECT 1
       FROM public.rag_v2_immutable_component_generations AS exact_generation
       JOIN public.rag_v2_immutable_component_generations AS oa_generation
         ON oa_generation.component_generation_id = claim_row.oa112_generation_id
        AND oa_generation.component_scope = 'OA112'
        AND oa_generation.owner_user_id IS NULL
        AND oa_generation.embedding_profile_id = claim_row.embedding_profile_id
        AND oa_generation.state = 'ACTIVE'
        AND oa_generation.evaluation_status = 'PASSED'
       WHERE exact_generation.component_generation_id = claim_row.exact30_generation_id
         AND exact_generation.component_scope = 'EXACT30'
         AND exact_generation.owner_user_id IS NULL
         AND exact_generation.embedding_profile_id = claim_row.embedding_profile_id
         AND exact_generation.state = 'ACTIVE'
         AND exact_generation.evaluation_status = 'PASSED'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 retrieval scope public pointer changed'
      USING ERRCODE = '55000';
  END IF;

  SELECT * INTO owner_pointer
  FROM public.rag_v2_immutable_owner_bundle_pointers
  WHERE owner_user_id = p_owner_user_id;
  IF claim_row.owner_private_generation_id IS NULL THEN
    IF (
      FOUND
      AND owner_pointer.bundle_version = claim_row.owner_pointer_version
      AND (
        owner_pointer.state = 'ABSENT'
        OR (
          owner_pointer.state = 'READY'
          AND EXISTS (
            SELECT 1
            FROM public.rag_v2_immutable_bundles AS empty_bundle
            JOIN public.rag_v2_immutable_component_generations AS empty_generation
              ON empty_generation.component_generation_id = empty_bundle.owner_private_generation_id
             AND empty_generation.owner_user_id = empty_bundle.owner_user_id
            WHERE empty_bundle.bundle_id = owner_pointer.active_bundle_id
              AND empty_bundle.owner_user_id = p_owner_user_id
              AND empty_bundle.state = 'ACTIVE'
              AND empty_bundle.owner_embedding_profile_id IS NULL
              AND empty_generation.actual_source_count = 0
              AND empty_generation.actual_chunk_count = 0
          )
        )
      )
    ) THEN
      NULL;
    ELSIF (FOUND OR claim_row.owner_pointer_version <> 0) THEN
      RAISE EXCEPTION 'immutable RAG v2 retrieval scope owner pointer changed'
        USING ERRCODE = '55000';
    END IF;
  ELSE
    IF NOT FOUND
       OR owner_pointer.state <> 'READY'
       OR owner_pointer.active_bundle_id IS DISTINCT FROM claim_row.owner_bundle_id
       OR owner_pointer.bundle_version <> claim_row.owner_pointer_version THEN
      RAISE EXCEPTION 'immutable RAG v2 retrieval scope owner bundle changed'
        USING ERRCODE = '55000';
    END IF;
    SELECT * INTO owner_bundle
    FROM public.rag_v2_immutable_bundles
    WHERE bundle_id = claim_row.owner_bundle_id
      AND owner_user_id = p_owner_user_id
      AND owner_private_generation_id = claim_row.owner_private_generation_id
      AND exact30_generation_id = claim_row.exact30_generation_id
      AND oa112_generation_id = claim_row.oa112_generation_id
      AND embedding_profile_id = claim_row.embedding_profile_id
      AND owner_embedding_profile_id IS NOT DISTINCT FROM claim_row.owner_embedding_profile_id
      AND state = 'ACTIVE';
    IF NOT FOUND
       OR NOT EXISTS (
         SELECT 1
         FROM public.rag_v2_immutable_component_generations AS owner_generation
         WHERE owner_generation.component_generation_id = claim_row.owner_private_generation_id
           AND owner_generation.component_scope = 'OWNER_PRIVATE'
           AND owner_generation.owner_user_id = p_owner_user_id
           AND owner_generation.embedding_profile_id = claim_row.owner_embedding_profile_id
           AND owner_generation.state = 'ACTIVE'
           AND owner_generation.evaluation_status = 'PASSED'
       ) THEN
      RAISE EXCEPTION 'immutable RAG v2 retrieval scope owner component changed'
        USING ERRCODE = '55000';
    END IF;
  END IF;

  RETURN QUERY
  SELECT
    claim_row.scope_claim_id,
    claim_row.owner_user_id,
    claim_row.session_id,
    claim_row.allowed_topics,
    claim_row.exact30_generation_id,
    claim_row.oa112_generation_id,
    claim_row.owner_private_generation_id,
    claim_row.embedding_profile_id,
    claim_row.owner_embedding_profile_id,
    claim_row.policy_version;
END;
$resolve_rag_v2_retrieval_scope_v2$;
ALTER FUNCTION resolve_rag_v2_retrieval_scope_v2(text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION resolve_rag_v2_retrieval_scope_v2(text, text, text) FROM PUBLIC;
CREATE FUNCTION public.read_rag_v2_retrieval_scope_v2(
  p_scope_claim_id text,
  p_owner_user_id text,
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
  owner_embedding_profile_id text,
  policy_version bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $read_rag_v2_retrieval_scope_v2$
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_rag_query' THEN
    RAISE EXCEPTION 'immutable RAG v2 retrieval scope v2 read is not authorized'
      USING ERRCODE = '42501';
  END IF;
  RETURN QUERY
  SELECT *
  FROM public.resolve_rag_v2_retrieval_scope_v2(
    p_scope_claim_id, p_owner_user_id, p_session_id
  );
END;
$read_rag_v2_retrieval_scope_v2$;
ALTER FUNCTION public.read_rag_v2_retrieval_scope_v2(text, text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.read_rag_v2_retrieval_scope_v2(text, text, text) FROM PUBLIC;

CREATE FUNCTION public.read_rag_v2_retrieval_scope_by_claim_v2(
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
  owner_embedding_profile_id text,
  policy_version bigint
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $read_rag_v2_retrieval_scope_by_claim_v2$
DECLARE
  bound_owner_user_id text;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_query'
     OR p_scope_claim_id !~ '^rvs_[0-9a-f]{32}$'
     OR p_session_id IS NULL
     OR char_length(p_session_id) NOT BETWEEN 16 AND 128
     OR p_session_id !~ '^[A-Za-z0-9._:-]+$' THEN
    RAISE EXCEPTION 'immutable RAG v2 opaque scope v2 arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  PERFORM set_config('app.rag_v2_retrieval_scope', 'enabled', true);
  SELECT scope.owner_user_id INTO bound_owner_user_id
  FROM public.rag_v2_retrieval_scope_claims AS scope
  WHERE scope.scope_claim_id = p_scope_claim_id
    AND scope.session_id = p_session_id
    AND scope.expires_at > statement_timestamp();
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 opaque scope v2 is absent or expired'
      USING ERRCODE = '55000';
  END IF;
  RETURN QUERY
  SELECT *
  FROM public.resolve_rag_v2_retrieval_scope_v2(
    p_scope_claim_id, bound_owner_user_id, p_session_id
  );
END;
$read_rag_v2_retrieval_scope_by_claim_v2$;
ALTER FUNCTION public.read_rag_v2_retrieval_scope_by_claim_v2(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.read_rag_v2_retrieval_scope_by_claim_v2(text, text) FROM PUBLIC;
CREATE OR REPLACE FUNCTION authorized_rag_v2_retrieval_rows(
  p_scope_claim_id text,
  p_owner_user_id text,
  p_session_id text,
  p_topics text[]
)
RETURNS TABLE (
  canonical_content text,
  canonical_content_sha256 text,
  canonical_https_url text,
  chunk_id text,
  document_id text,
  embedding_profile_id text,
  external_processing_eligible boolean,
  generation_id text,
  heading_path text[],
  locator jsonb,
  candidate_owner_user_id text,
  policy_version bigint,
  sanitized_display_name text,
  scope_claim_id text,
  session_id text,
  source_id text,
  source_revision_id text,
  source_scope text,
  citation_title text,
  retrieval_topics text[]
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $authorized_rag_v2_retrieval_rows$
#variable_conflict use_column
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_query'
     OR NOT public.rag_v2_immutable_retrieval_topics_are_valid(p_topics) THEN
    RAISE EXCEPTION 'immutable RAG v2 retrieval row arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
  WITH scope AS (
    SELECT *
    FROM public.resolve_rag_v2_retrieval_scope_v2(p_scope_claim_id, p_owner_user_id, p_session_id)
  )
  SELECT
    chunk.canonical_text,
    chunk.canonical_text_sha256,
    source.canonical_https_url,
    chunk.chunk_id,
    source.document_id,
    CASE
      WHEN source.source_scope = 'OWNER_PRIVATE' THEN scope.owner_embedding_profile_id
      ELSE scope.embedding_profile_id
    END,
    source.external_processing_eligible,
    membership.component_generation_id,
    chunk.heading_path,
    chunk.locator,
    source.owner_user_id,
    scope.policy_version,
    source.sanitized_display_name,
    scope.scope_claim_id,
    scope.session_id,
    source.source_id,
    source.source_revision_id,
    source.source_scope,
    source.citation_title,
    source.retrieval_topics
  FROM scope
  JOIN public.rag_v2_immutable_generation_memberships AS membership
    ON membership.component_generation_id = ANY(
      ARRAY[
        scope.exact30_generation_id,
        scope.oa112_generation_id,
        scope.owner_private_generation_id
      ]::text[]
    )
  JOIN public.rag_v2_immutable_chunks AS chunk
    ON chunk.chunk_id = membership.chunk_id
   AND chunk.source_revision_id = membership.source_revision_id
   AND chunk.source_scope = membership.component_scope
   AND chunk.owner_partition_key = membership.owner_partition_key
  JOIN public.rag_v2_immutable_source_revisions AS source
    ON source.source_revision_id = membership.source_revision_id
   AND source.source_scope = membership.component_scope
   AND source.owner_partition_key = membership.owner_partition_key
  JOIN public.rag_v2_immutable_generation_embeddings AS embedding
    ON embedding.component_generation_id = membership.component_generation_id
   AND embedding.chunk_id = membership.chunk_id
   AND embedding.component_scope = membership.component_scope
   AND embedding.owner_partition_key = membership.owner_partition_key
   AND embedding.embedding_profile_id = CASE
     WHEN membership.component_scope = 'OWNER_PRIVATE' THEN scope.owner_embedding_profile_id
     ELSE scope.embedding_profile_id
   END
  WHERE p_topics <@ scope.allowed_topics
    AND source.retrieval_topics && p_topics
    AND (
      (
        source.source_scope IN ('EXACT30', 'OA112')
        AND source.owner_user_id IS NULL
        AND source.citation_title IS NOT NULL
        AND source.sanitized_display_name IS NULL
        AND public.rag_v2_immutable_public_https_url_is_valid(source.canonical_https_url)
      )
      OR (
        source.source_scope = 'OWNER_PRIVATE'
        AND source.owner_user_id = scope.owner_user_id
        AND source.citation_title IS NULL
        AND source.sanitized_display_name IS NOT NULL
        AND source.canonical_https_url IS NULL
      )
    )
    AND (
      source.source_scope <> 'OA112'
      OR EXISTS (
        SELECT 1
        FROM public.rag_v2_immutable_oa_source_cards AS card
        WHERE card.source_revision_id = source.source_revision_id
          AND card.source_id = source.source_id
          AND card.active_oa112_eligible
          AND card.canonical_https_url = source.canonical_https_url
      )
    );
END;
$authorized_rag_v2_retrieval_rows$;
ALTER FUNCTION authorized_rag_v2_retrieval_rows(text, text, text, text[]) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION authorized_rag_v2_retrieval_rows(text, text, text, text[]) FROM PUBLIC;
CREATE FUNCTION search_authorized_rag_v2_dense_v2(
  p_scope_claim_id text,
  p_owner_user_id text,
  p_session_id text,
  p_topics text[],
  p_query_embedding vector(1024),
  p_owner_query_embedding vector(1024)
)
RETURNS TABLE (
  rank_no integer,
  canonical_content text,
  canonical_content_sha256 text,
  canonical_https_url text,
  chunk_id text,
  document_id text,
  embedding_profile_id text,
  external_processing_eligible boolean,
  generation_id text,
  heading_path text[],
  locator jsonb,
  candidate_owner_user_id text,
  policy_version bigint,
  sanitized_display_name text,
  scope_claim_id text,
  session_id text,
  source_id text,
  source_revision_id text,
  source_scope text,
  citation_title text,
  retrieval_topics text[]
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $search_authorized_rag_v2_dense_v2$
#variable_conflict use_column
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_query'
     OR p_query_embedding IS NULL
     OR vector_dims(p_query_embedding) <> 1024
     OR vector_norm(p_query_embedding)::text IN ('NaN', 'Infinity', '-Infinity')
     OR abs(vector_norm(p_query_embedding)::double precision - 1.0) > 0.00001
     OR (
       p_owner_query_embedding IS NOT NULL
       AND (
         vector_dims(p_owner_query_embedding) <> 1024
         OR vector_norm(p_owner_query_embedding)::text IN ('NaN', 'Infinity', '-Infinity')
         OR abs(vector_norm(p_owner_query_embedding)::double precision - 1.0) > 0.00001
       )
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 dense retrieval arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  RETURN QUERY
  WITH scope AS (
    SELECT *
    FROM public.resolve_rag_v2_retrieval_scope_v2(p_scope_claim_id, p_owner_user_id, p_session_id)
  ), candidates AS (
    SELECT
      rows.*,
      embedding.embedding <=> CASE
        WHEN membership.component_scope = 'OWNER_PRIVATE' THEN p_owner_query_embedding
        ELSE p_query_embedding
      END AS dense_distance
    FROM scope
    JOIN public.rag_v2_immutable_generation_memberships AS membership
      ON membership.component_generation_id = ANY(
        ARRAY[
          scope.exact30_generation_id,
          scope.oa112_generation_id,
          scope.owner_private_generation_id
        ]::text[]
      )
    JOIN public.rag_v2_immutable_chunks AS chunk
      ON chunk.chunk_id = membership.chunk_id
     AND chunk.source_revision_id = membership.source_revision_id
     AND chunk.source_scope = membership.component_scope
     AND chunk.owner_partition_key = membership.owner_partition_key
    JOIN public.rag_v2_immutable_source_revisions AS source
      ON source.source_revision_id = membership.source_revision_id
     AND source.source_scope = membership.component_scope
     AND source.owner_partition_key = membership.owner_partition_key
    JOIN public.rag_v2_immutable_generation_embeddings AS embedding
      ON embedding.component_generation_id = membership.component_generation_id
     AND embedding.chunk_id = membership.chunk_id
     AND embedding.component_scope = membership.component_scope
     AND embedding.owner_partition_key = membership.owner_partition_key
     AND embedding.embedding_profile_id = CASE
       WHEN membership.component_scope = 'OWNER_PRIVATE' THEN scope.owner_embedding_profile_id
       ELSE scope.embedding_profile_id
     END
    CROSS JOIN LATERAL (
      SELECT
        chunk.canonical_text AS canonical_content,
        chunk.canonical_text_sha256 AS canonical_content_sha256,
        source.canonical_https_url,
        chunk.chunk_id,
        source.document_id,
        CASE
          WHEN source.source_scope = 'OWNER_PRIVATE' THEN scope.owner_embedding_profile_id
          ELSE scope.embedding_profile_id
        END AS embedding_profile_id,
        source.external_processing_eligible,
        membership.component_generation_id AS generation_id,
        chunk.heading_path,
        chunk.locator,
        source.owner_user_id AS candidate_owner_user_id,
        scope.policy_version,
        source.sanitized_display_name,
        scope.scope_claim_id,
        scope.session_id,
        source.source_id,
        source.source_revision_id,
        source.source_scope,
        source.citation_title,
        source.retrieval_topics
    ) AS rows
    WHERE p_topics <@ scope.allowed_topics
      AND source.retrieval_topics && p_topics
      AND (source.source_scope <> 'OWNER_PRIVATE' OR p_owner_query_embedding IS NOT NULL)
      AND (
        (
          source.source_scope IN ('EXACT30', 'OA112')
          AND source.owner_user_id IS NULL
          AND source.citation_title IS NOT NULL
          AND source.sanitized_display_name IS NULL
          AND public.rag_v2_immutable_public_https_url_is_valid(source.canonical_https_url)
        )
        OR (
          source.source_scope = 'OWNER_PRIVATE'
          AND source.owner_user_id = scope.owner_user_id
          AND source.citation_title IS NULL
          AND source.sanitized_display_name IS NOT NULL
          AND source.canonical_https_url IS NULL
        )
      )
      AND (
        source.source_scope <> 'OA112'
        OR EXISTS (
          SELECT 1
          FROM public.rag_v2_immutable_oa_source_cards AS card
          WHERE card.source_revision_id = source.source_revision_id
            AND card.source_id = source.source_id
            AND card.active_oa112_eligible
            AND card.canonical_https_url = source.canonical_https_url
        )
      )
  ), ranked AS (
    SELECT
      candidates.*,
      rank() OVER (
        PARTITION BY embedding_profile_id
        ORDER BY dense_distance
      )::integer AS profile_rank
    FROM candidates
    WHERE dense_distance <= 0.55
  )
  SELECT
    row_number() OVER (
      ORDER BY profile_rank, (source_scope = 'OWNER_PRIVATE') DESC,
               source_id COLLATE "C", chunk_id COLLATE "C"
    )::integer,
    canonical_content,
    canonical_content_sha256,
    canonical_https_url,
    chunk_id,
    document_id,
    embedding_profile_id,
    external_processing_eligible,
    generation_id,
    heading_path,
    locator,
    candidate_owner_user_id,
    policy_version,
    sanitized_display_name,
    scope_claim_id,
    session_id,
    source_id,
    source_revision_id,
    source_scope,
    citation_title,
    retrieval_topics
  FROM ranked
  ORDER BY profile_rank, (source_scope = 'OWNER_PRIVATE') DESC,
           source_id COLLATE "C", chunk_id COLLATE "C"
  LIMIT 30;
END;
$search_authorized_rag_v2_dense_v2$;
ALTER FUNCTION search_authorized_rag_v2_dense_v2(text, text, text, text[], vector, vector) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION search_authorized_rag_v2_dense_v2(text, text, text, text[], vector, vector) FROM PUBLIC;
CREATE FUNCTION public.read_rag_v2_vertex_prepared_scope_v2(
  p_owner_user_id text,
  p_session_id text,
  p_scope_claim_id text,
  p_allowed_topics text[]
)
RETURNS TABLE (
  scope_claim_id text,
  exact30_generation_id text,
  oa112_generation_id text,
  owner_private_generation_id text,
  embedding_profile_id text,
  owner_embedding_profile_id text,
  policy_version bigint,
  expires_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $read_rag_v2_vertex_prepared_scope_v2$
#variable_conflict use_column
DECLARE
  claim_row public.rag_v2_retrieval_scope_claims%ROWTYPE;
  public_pointer public.rag_v2_immutable_public_bundle_pointers%ROWTYPE;
  owner_pointer public.rag_v2_immutable_owner_bundle_pointers%ROWTYPE;
  owner_bundle public.rag_v2_immutable_bundles%ROWTYPE;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_session_id IS NULL
     OR char_length(p_session_id) NOT BETWEEN 16 AND 128
     OR p_session_id !~ '^[A-Za-z0-9._:-]+$'
     OR p_scope_claim_id !~ '^rvs_[0-9a-f]{32}$'
     OR NOT public.rag_v2_immutable_retrieval_topics_are_valid(p_allowed_topics) THEN
    RAISE EXCEPTION 'immutable RAG v2 Vertex prepared scope arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  PERFORM set_config('app.rag_v2_retrieval_scope', 'enabled', true);
  SELECT * INTO claim_row
  FROM public.rag_v2_retrieval_scope_claims AS scope
  WHERE scope.scope_claim_id = p_scope_claim_id
    AND scope.owner_user_id = p_owner_user_id
    AND scope.session_id = p_session_id
    AND scope.allowed_topics = p_allowed_topics
    AND scope.expires_at > statement_timestamp();
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 Vertex prepared scope is absent or expired'
      USING ERRCODE = '55000';
  END IF;
  SELECT * INTO public_pointer
  FROM public.rag_v2_immutable_public_bundle_pointers AS pointer
  WHERE pointer.state_id = 'default'
    AND pointer.state = 'ACTIVE'
    AND pointer.pointer_version = claim_row.public_pointer_version
    AND pointer.exact30_generation_id = claim_row.exact30_generation_id
    AND pointer.oa112_generation_id = claim_row.oa112_generation_id
    AND pointer.embedding_profile_id = claim_row.embedding_profile_id;
  IF NOT FOUND
     OR NOT EXISTS (
       SELECT 1
       FROM public.rag_v2_immutable_component_generations AS exact_generation
       JOIN public.rag_v2_immutable_component_generations AS oa_generation
         ON oa_generation.component_generation_id = claim_row.oa112_generation_id
        AND oa_generation.component_scope = 'OA112'
        AND oa_generation.owner_user_id IS NULL
        AND oa_generation.embedding_profile_id = claim_row.embedding_profile_id
        AND oa_generation.state = 'ACTIVE'
        AND oa_generation.evaluation_status = 'PASSED'
       WHERE exact_generation.component_generation_id = claim_row.exact30_generation_id
         AND exact_generation.component_scope = 'EXACT30'
         AND exact_generation.owner_user_id IS NULL
         AND exact_generation.embedding_profile_id = claim_row.embedding_profile_id
         AND exact_generation.state = 'ACTIVE'
         AND exact_generation.evaluation_status = 'PASSED'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 Vertex prepared scope public pointer changed'
      USING ERRCODE = '55000';
  END IF;

  SELECT * INTO owner_pointer
  FROM public.rag_v2_immutable_owner_bundle_pointers AS pointer
  WHERE pointer.owner_user_id = p_owner_user_id;
  IF claim_row.owner_private_generation_id IS NULL THEN
    IF (
      FOUND
      AND owner_pointer.bundle_version = claim_row.owner_pointer_version
      AND (
        owner_pointer.state = 'ABSENT'
        OR (
          owner_pointer.state = 'READY'
          AND EXISTS (
            SELECT 1
            FROM public.rag_v2_immutable_bundles AS empty_bundle
            JOIN public.rag_v2_immutable_component_generations AS empty_generation
              ON empty_generation.component_generation_id = empty_bundle.owner_private_generation_id
             AND empty_generation.owner_user_id = empty_bundle.owner_user_id
            WHERE empty_bundle.bundle_id = owner_pointer.active_bundle_id
              AND empty_bundle.owner_user_id = p_owner_user_id
              AND empty_bundle.state = 'ACTIVE'
              AND empty_bundle.owner_embedding_profile_id IS NULL
              AND empty_generation.actual_source_count = 0
              AND empty_generation.actual_chunk_count = 0
          )
        )
      )
    ) THEN
      NULL;
    ELSIF (FOUND OR claim_row.owner_pointer_version <> 0) THEN
      RAISE EXCEPTION 'immutable RAG v2 Vertex prepared scope owner pointer changed'
        USING ERRCODE = '55000';
    END IF;
  ELSE
    IF NOT FOUND
       OR owner_pointer.state <> 'READY'
       OR owner_pointer.active_bundle_id IS DISTINCT FROM claim_row.owner_bundle_id
       OR owner_pointer.bundle_version <> claim_row.owner_pointer_version THEN
      RAISE EXCEPTION 'immutable RAG v2 Vertex prepared scope owner bundle changed'
        USING ERRCODE = '55000';
    END IF;
    SELECT * INTO owner_bundle
    FROM public.rag_v2_immutable_bundles AS bundle
    WHERE bundle.bundle_id = claim_row.owner_bundle_id
      AND bundle.owner_user_id = p_owner_user_id
      AND bundle.owner_private_generation_id = claim_row.owner_private_generation_id
      AND bundle.exact30_generation_id = claim_row.exact30_generation_id
      AND bundle.oa112_generation_id = claim_row.oa112_generation_id
      AND bundle.embedding_profile_id = claim_row.embedding_profile_id
      AND bundle.owner_embedding_profile_id = claim_row.owner_embedding_profile_id
      AND bundle.state = 'ACTIVE';
    IF NOT FOUND
       OR NOT EXISTS (
         SELECT 1
         FROM public.rag_v2_immutable_component_generations AS owner_generation
         WHERE owner_generation.component_generation_id = claim_row.owner_private_generation_id
           AND owner_generation.component_scope = 'OWNER_PRIVATE'
           AND owner_generation.owner_user_id = p_owner_user_id
           AND owner_generation.embedding_profile_id = claim_row.owner_embedding_profile_id
           AND owner_generation.state = 'ACTIVE'
           AND owner_generation.evaluation_status = 'PASSED'
       ) THEN
      RAISE EXCEPTION 'immutable RAG v2 Vertex prepared scope owner component changed'
        USING ERRCODE = '55000';
    END IF;
  END IF;

  RETURN QUERY
  SELECT
    claim_row.scope_claim_id,
    claim_row.exact30_generation_id,
    claim_row.oa112_generation_id,
    claim_row.owner_private_generation_id,
    claim_row.embedding_profile_id,
    claim_row.owner_embedding_profile_id,
    claim_row.policy_version,
    claim_row.expires_at;
END
$read_rag_v2_vertex_prepared_scope_v2$;
ALTER FUNCTION public.read_rag_v2_vertex_prepared_scope_v2(text, text, text, text[]) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.read_rag_v2_vertex_prepared_scope_v2(text, text, text, text[]) FROM PUBLIC;

DO $pre_s5_owner_dual_profile_runtime_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    GRANT EXECUTE ON FUNCTION public.issue_rag_v2_retrieval_scope_v2(text, text, text[])
      TO decision_app;
    GRANT EXECUTE ON FUNCTION public.read_rag_v2_vertex_prepared_scope_v2(text, text, text, text[])
      TO decision_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_query') THEN
    GRANT EXECUTE ON FUNCTION public.read_rag_v2_retrieval_scope_v2(text, text, text)
      TO decision_rag_query;
    GRANT EXECUTE ON FUNCTION public.read_rag_v2_retrieval_scope_by_claim_v2(text, text)
      TO decision_rag_query;
    GRANT EXECUTE ON FUNCTION public.search_authorized_rag_v2_dense_v2(text, text, text, text[], vector, vector)
      TO decision_rag_query;
  END IF;
END;
$pre_s5_owner_dual_profile_runtime_acl$;

REVOKE ALL PRIVILEGES ON TABLE public.rag_v2_immutable_import_tickets FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE public.rag_v2_retrieval_scope_claims FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE public.rag_v2_owner_voyage_import_attempts FROM PUBLIC;

DO $rag_v2_owner_overlay_assembly_admin_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_admin') THEN
    GRANT EXECUTE ON FUNCTION prepare_rag_v2_immutable_owner_overlay(text, text)
      TO decision_rag_admin;
  END IF;
END;
$rag_v2_owner_overlay_assembly_admin_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION prepare_rag_v2_immutable_owner_overlay(text, text) FROM PUBLIC;
