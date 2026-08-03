-- V35는 Python local-BGE result를 decision_app에서 다시 immutable scope로 검증하고,
-- canonical citation metadata만 v2 AES-GCM history에 저장한다. raw text/path/provider data는
-- function input·output·table 어디에도 추가하지 않는다.

-- V24 retrieval-only history는 empty answer의 AES-GCM ciphertext가 0 byte일 수 있다는
-- cryptographic fact와 충돌했다. answered rows의 non-empty answer 요구는 유지한다.
ALTER TABLE rag_v2_answer_history
  DROP CONSTRAINT rag_v2_answer_history_crypto_check;
ALTER TABLE rag_v2_answer_history
  ADD CONSTRAINT rag_v2_answer_history_crypto_check
    CHECK (
      kek_version ~ '^kek-v[1-9][0-9]{0,8}$'
      AND octet_length(wrap_nonce) = 12
      AND octet_length(wrapped_dek) = 32
      AND octet_length(wrap_tag) = 16
      AND octet_length(question_nonce) = 12
      AND octet_length(question_ciphertext) BETWEEN 1 AND 8192
      AND octet_length(question_tag) = 16
      AND octet_length(answer_nonce) = 12
      AND octet_length(answer_ciphertext) BETWEEN 0 AND 8192
      AND octet_length(answer_tag) = 16
      AND (
        generation_status = 'RETRIEVAL_ONLY'
        OR octet_length(answer_ciphertext) >= 1
      )
    );

CREATE FUNCTION canonicalize_rag_v2_immutable_retrieval_citations(
  p_owner_user_id text,
  p_session_id text,
  p_scope_claim_id text,
  p_citations jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $canonicalize_rag_v2_immutable_retrieval_citations$
#variable_conflict use_column
DECLARE
  claim_row public.rag_v2_retrieval_scope_claims%ROWTYPE;
  public_pointer public.rag_v2_immutable_public_bundle_pointers%ROWTYPE;
  owner_pointer public.rag_v2_immutable_owner_bundle_pointers%ROWTYPE;
  owner_bundle public.rag_v2_immutable_bundles%ROWTYPE;
  citation_item jsonb;
  candidate record;
  expected_ordinal integer := 1;
  seen_chunk_ids text[] := ARRAY[]::text[];
  output jsonb := '[]'::jsonb;
  requested_generation_id text;
  requested_kind text;
  requested_chunk_id text;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_session_id IS NULL
     OR char_length(p_session_id) NOT BETWEEN 16 AND 128
     OR p_session_id !~ '^[A-Za-z0-9._:-]+$'
     OR p_scope_claim_id !~ '^rvs_[0-9a-f]{32}$'
     OR jsonb_typeof(p_citations) <> 'array'
     OR jsonb_array_length(p_citations) NOT BETWEEN 1 AND 5
     OR octet_length(p_citations::text) > 16384 THEN
    RAISE EXCEPTION 'immutable RAG v2 citation recheck arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM set_config('app.actor_user_id', p_owner_user_id, true);
  PERFORM set_config('app.rag_v2_retrieval_scope', 'enabled', true);
  SELECT * INTO claim_row
  FROM public.rag_v2_retrieval_scope_claims AS scope
  WHERE scope.scope_claim_id = p_scope_claim_id
    AND scope.owner_user_id = p_owner_user_id
    AND scope.session_id = p_session_id
    AND scope.expires_at > statement_timestamp();
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 retrieval scope is absent or expired'
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
    RAISE EXCEPTION 'immutable RAG v2 public retrieval scope changed'
      USING ERRCODE = '55000';
  END IF;

  SELECT * INTO owner_pointer
  FROM public.rag_v2_immutable_owner_bundle_pointers AS pointer
  WHERE pointer.owner_user_id = p_owner_user_id;
  IF claim_row.owner_private_generation_id IS NULL THEN
    IF (FOUND AND (owner_pointer.state <> 'ABSENT' OR owner_pointer.bundle_version <> claim_row.owner_pointer_version))
       OR (NOT FOUND AND claim_row.owner_pointer_version <> 0) THEN
      RAISE EXCEPTION 'immutable RAG v2 owner retrieval scope changed'
        USING ERRCODE = '55000';
    END IF;
  ELSE
    IF NOT FOUND
       OR owner_pointer.state <> 'READY'
       OR owner_pointer.active_bundle_id IS DISTINCT FROM claim_row.owner_bundle_id
       OR owner_pointer.bundle_version <> claim_row.owner_pointer_version THEN
      RAISE EXCEPTION 'immutable RAG v2 owner bundle scope changed'
        USING ERRCODE = '55000';
    END IF;
    SELECT * INTO owner_bundle
    FROM public.rag_v2_immutable_bundles AS bundle
    WHERE bundle.bundle_id = claim_row.owner_bundle_id
      AND bundle.owner_user_id = p_owner_user_id
      AND bundle.state = 'ACTIVE'
      AND bundle.evaluation_status = 'PASSED'
      AND bundle.owner_private_generation_id = claim_row.owner_private_generation_id
      AND bundle.exact30_generation_id = claim_row.exact30_generation_id
      AND bundle.oa112_generation_id = claim_row.oa112_generation_id
      AND bundle.embedding_profile_id = claim_row.embedding_profile_id;
    IF NOT FOUND
       OR NOT EXISTS (
         SELECT 1
         FROM public.rag_v2_immutable_component_generations AS generation
         WHERE generation.component_generation_id = claim_row.owner_private_generation_id
           AND generation.component_scope = 'OWNER_PRIVATE'
           AND generation.owner_user_id = p_owner_user_id
           AND generation.embedding_profile_id = claim_row.embedding_profile_id
           AND generation.state = 'ACTIVE'
           AND generation.evaluation_status = 'PASSED'
       ) THEN
      RAISE EXCEPTION 'immutable RAG v2 owner component scope changed'
        USING ERRCODE = '55000';
    END IF;
  END IF;

  FOR citation_item IN SELECT value FROM jsonb_array_elements(p_citations)
  LOOP
    IF jsonb_typeof(citation_item) <> 'object'
       OR NOT (citation_item ?& ARRAY[
         'ordinal', 'citationId', 'sourceId', 'sourceRevisionId',
         'chunkRevisionId', 'generationId', 'citationKind'
       ])
       OR EXISTS (
         SELECT 1 FROM jsonb_object_keys(citation_item) AS key_name
         WHERE key_name NOT IN (
           'ordinal', 'citationId', 'sourceId', 'sourceRevisionId',
           'chunkRevisionId', 'generationId', 'citationKind'
         )
       )
       OR jsonb_typeof(citation_item -> 'ordinal') <> 'number'
       OR citation_item ->> 'ordinal' <> expected_ordinal::text
       OR jsonb_typeof(citation_item -> 'citationId') <> 'string'
       OR citation_item ->> 'citationId' <> ('cit_' || expected_ordinal::text)
       OR jsonb_typeof(citation_item -> 'sourceId') <> 'string'
       OR citation_item ->> 'sourceId' !~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'
       OR jsonb_typeof(citation_item -> 'sourceRevisionId') <> 'string'
       OR citation_item ->> 'sourceRevisionId' !~ '^srv_[a-z0-9][a-z0-9_-]{2,95}$'
       OR jsonb_typeof(citation_item -> 'chunkRevisionId') <> 'string'
       OR citation_item ->> 'chunkRevisionId' !~ '^rag_v2_chk_[0-9a-f]{32}$'
       OR jsonb_typeof(citation_item -> 'generationId') <> 'string'
       OR citation_item ->> 'generationId' !~ '^rgr_[0-9a-f]{32}$'
       OR jsonb_typeof(citation_item -> 'citationKind') <> 'string'
       OR citation_item ->> 'citationKind' NOT IN ('PUBLIC_WEB', 'LOCAL_DOCUMENT') THEN
      RAISE EXCEPTION 'immutable RAG v2 citation receipt is invalid'
        USING ERRCODE = '22023';
    END IF;

    requested_generation_id := citation_item ->> 'generationId';
    requested_kind := citation_item ->> 'citationKind';
    requested_chunk_id := citation_item ->> 'chunkRevisionId';
    IF requested_chunk_id = ANY(seen_chunk_ids) THEN
      RAISE EXCEPTION 'immutable RAG v2 citation chunk is duplicated'
        USING ERRCODE = '22023';
    END IF;
    seen_chunk_ids := array_append(seen_chunk_ids, requested_chunk_id);

    SELECT
      source.source_id,
      source.source_revision_id,
      source.source_scope,
      source.owner_user_id,
      source.document_id,
      source.sanitized_display_name,
      source.citation_title,
      source.canonical_https_url,
      source.retrieval_topics,
      chunk.chunk_id,
      chunk.locator,
      membership.component_generation_id
    INTO candidate
    FROM public.rag_v2_immutable_generation_memberships AS membership
    JOIN public.rag_v2_immutable_chunks AS chunk
      ON chunk.chunk_id = membership.chunk_id
     AND chunk.source_revision_id = membership.source_revision_id
     AND chunk.source_scope = membership.component_scope
     AND chunk.owner_partition_key = membership.owner_partition_key
    JOIN public.rag_v2_immutable_source_revisions AS source
      ON source.source_revision_id = membership.source_revision_id
     AND source.source_scope = membership.component_scope
     AND source.owner_partition_key = membership.owner_partition_key
    WHERE membership.component_generation_id = requested_generation_id
      AND membership.chunk_id = requested_chunk_id
      AND membership.source_revision_id = citation_item ->> 'sourceRevisionId'
      AND source.source_id = citation_item ->> 'sourceId'
      AND membership.component_generation_id = ANY(
        ARRAY[
          claim_row.exact30_generation_id,
          claim_row.oa112_generation_id,
          claim_row.owner_private_generation_id
        ]::text[]
      )
      AND source.retrieval_topics && claim_row.allowed_topics
      AND EXISTS (
        SELECT 1
        FROM public.rag_v2_immutable_generation_embeddings AS embedding
        WHERE embedding.component_generation_id = membership.component_generation_id
          AND embedding.chunk_id = membership.chunk_id
          AND embedding.component_scope = membership.component_scope
          AND embedding.owner_partition_key = membership.owner_partition_key
          AND embedding.embedding_profile_id = claim_row.embedding_profile_id
      );
    IF NOT FOUND THEN
      RAISE EXCEPTION 'immutable RAG v2 citation is outside the active scope'
        USING ERRCODE = '55000';
    END IF;

    IF candidate.source_scope IN ('EXACT30', 'OA112') THEN
      IF requested_kind <> 'PUBLIC_WEB'
         OR candidate.owner_user_id IS NOT NULL
         OR candidate.sanitized_display_name IS NOT NULL
         OR candidate.citation_title IS NULL
         OR char_length(candidate.citation_title) NOT BETWEEN 1 AND 500
         OR NOT public.rag_v2_immutable_public_https_url_is_valid(candidate.canonical_https_url)
         OR (
           candidate.source_scope = 'EXACT30'
           AND candidate.component_generation_id <> claim_row.exact30_generation_id
         )
         OR (
           candidate.source_scope = 'OA112'
           AND (
             candidate.component_generation_id <> claim_row.oa112_generation_id
             OR NOT EXISTS (
               SELECT 1
               FROM public.rag_v2_immutable_oa_source_cards AS card
               WHERE card.source_revision_id = candidate.source_revision_id
                 AND card.source_id = candidate.source_id
                 AND card.active_oa112_eligible
                 AND card.canonical_https_url = candidate.canonical_https_url
             )
           )
         ) THEN
        RAISE EXCEPTION 'immutable RAG v2 public citation is invalid'
          USING ERRCODE = '55000';
      END IF;
      output := output || jsonb_build_array(
        jsonb_build_object(
          'citationKind', 'PUBLIC_WEB',
          'citationId', citation_item ->> 'citationId',
          'sourceId', candidate.source_id,
          'sourceRevisionId', candidate.source_revision_id,
          'chunkRevisionId', candidate.chunk_id,
          'generationId', candidate.component_generation_id,
          'title', candidate.citation_title,
          'canonicalUrl', candidate.canonical_https_url,
          'locator', candidate.locator
        )
      );
    ELSE
      IF requested_kind <> 'LOCAL_DOCUMENT'
         OR candidate.source_scope <> 'OWNER_PRIVATE'
         OR candidate.owner_user_id <> p_owner_user_id
         OR candidate.component_generation_id IS DISTINCT FROM claim_row.owner_private_generation_id
         OR candidate.citation_title IS NOT NULL
         OR candidate.canonical_https_url IS NOT NULL
         OR candidate.document_id !~ '^doc_[a-z0-9][a-z0-9_-]{10,95}$'
         OR candidate.sanitized_display_name IS NULL
         OR char_length(candidate.sanitized_display_name) NOT BETWEEN 1 AND 160
         OR candidate.sanitized_display_name ~ '[/\\:]' THEN
        RAISE EXCEPTION 'immutable RAG v2 owner citation is invalid'
          USING ERRCODE = '55000';
      END IF;
      output := output || jsonb_build_array(
        jsonb_build_object(
          'citationKind', 'LOCAL_DOCUMENT',
          'citationId', citation_item ->> 'citationId',
          'sourceId', candidate.source_id,
          'sourceRevisionId', candidate.source_revision_id,
          'chunkRevisionId', candidate.chunk_id,
          'generationId', candidate.component_generation_id,
          'documentId', candidate.document_id,
          'displayName', candidate.sanitized_display_name,
          'locator', candidate.locator
        )
      );
    END IF;
    expected_ordinal := expected_ordinal + 1;
  END LOOP;
  RETURN output;
END
$canonicalize_rag_v2_immutable_retrieval_citations$;
ALTER FUNCTION canonicalize_rag_v2_immutable_retrieval_citations(text, text, text, jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION canonicalize_rag_v2_immutable_retrieval_citations(text, text, text, jsonb) FROM PUBLIC;

CREATE FUNCTION persist_rag_v2_immutable_retrieval_history(
  p_owner_user_id text,
  p_answer_id text,
  p_request_id text,
  p_answer_mode text,
  p_session_id text,
  p_scope_claim_id text,
  p_citation_coverage double precision,
  p_guardrail_flags text[],
  p_kek_version text,
  p_wrap_nonce bytea,
  p_wrapped_dek bytea,
  p_wrap_tag bytea,
  p_question_nonce bytea,
  p_question_ciphertext bytea,
  p_question_tag bytea,
  p_answer_nonce bytea,
  p_answer_ciphertext bytea,
  p_answer_tag bytea,
  p_created_at timestamptz,
  p_citations jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $persist_rag_v2_immutable_retrieval_history$
DECLARE
  canonical_citations jsonb;
  claim_row public.rag_v2_retrieval_scope_claims%ROWTYPE;
  private_state text;
  public_version text;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_answer_id !~ '^rag_[A-Za-z0-9_-]{12,96}$'
     OR p_request_id !~ '^req_[A-Za-z0-9_-]{12,96}$'
     OR p_answer_mode NOT IN ('CONCISE', 'DETAILED')
     OR p_citation_coverage <> 1.0
     OR coalesce(cardinality(p_guardrail_flags), 0) <> 0
     OR p_kek_version !~ '^kek-v[1-9][0-9]{0,8}$'
     OR octet_length(p_wrap_nonce) <> 12
     OR octet_length(p_wrapped_dek) <> 32
     OR octet_length(p_wrap_tag) <> 16
     OR octet_length(p_question_nonce) <> 12
     OR octet_length(p_question_ciphertext) NOT BETWEEN 1 AND 8192
     OR octet_length(p_question_tag) <> 16
     OR octet_length(p_answer_nonce) <> 12
     OR octet_length(p_answer_ciphertext) NOT BETWEEN 0 AND 8192
     OR octet_length(p_answer_tag) <> 16
     OR p_created_at IS NULL
     OR p_created_at NOT BETWEEN transaction_timestamp() - interval '60 seconds'
         AND transaction_timestamp() + interval '60 seconds' THEN
    RAISE EXCEPTION 'immutable RAG v2 history persistence arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  canonical_citations := public.canonicalize_rag_v2_immutable_retrieval_citations(
    p_owner_user_id,
    p_session_id,
    p_scope_claim_id,
    p_citations
  );
  SELECT * INTO claim_row
  FROM public.rag_v2_retrieval_scope_claims AS scope
  WHERE scope.scope_claim_id = p_scope_claim_id
    AND scope.owner_user_id = p_owner_user_id
    AND scope.session_id = p_session_id
    AND scope.expires_at > statement_timestamp();
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 history scope disappeared'
      USING ERRCODE = '55000';
  END IF;
  private_state := CASE WHEN claim_row.owner_private_generation_id IS NULL THEN 'ABSENT' ELSE 'READY' END;
  public_version := 'immutable-v2-' || claim_row.public_pointer_version::text;

  INSERT INTO public.rag_v2_answer_history (
    answer_id, owner_user_id, request_id, answer_mode, generation_status,
    citation_coverage, retrieval_failure, guardrail_flags, public_corpus_version,
    private_overlay_state, kek_version, wrap_nonce, wrapped_dek, wrap_tag,
    question_nonce, question_ciphertext, question_tag,
    answer_nonce, answer_ciphertext, answer_tag,
    citation_count, created_at, expires_at
  ) VALUES (
    p_answer_id, p_owner_user_id, p_request_id, p_answer_mode, 'RETRIEVAL_ONLY',
    p_citation_coverage, false, p_guardrail_flags, public_version,
    private_state, p_kek_version, p_wrap_nonce, p_wrapped_dek, p_wrap_tag,
    p_question_nonce, p_question_ciphertext, p_question_tag,
    p_answer_nonce, p_answer_ciphertext, p_answer_tag,
    jsonb_array_length(canonical_citations), p_created_at, p_created_at + interval '30 days'
  );

  INSERT INTO public.rag_v2_answer_citations (
    answer_id, owner_user_id, ordinal, citation_kind, source_id, title,
    canonical_url, document_id, sanitized_display_name, locator
  )
  SELECT
    p_answer_id,
    p_owner_user_id,
    ordinal::integer,
    citation.value ->> 'citationKind',
    CASE WHEN citation.value ->> 'citationKind' = 'PUBLIC_WEB' THEN citation.value ->> 'sourceId' ELSE NULL END,
    CASE WHEN citation.value ->> 'citationKind' = 'PUBLIC_WEB' THEN citation.value ->> 'title' ELSE NULL END,
    CASE WHEN citation.value ->> 'citationKind' = 'PUBLIC_WEB' THEN citation.value ->> 'canonicalUrl' ELSE NULL END,
    CASE WHEN citation.value ->> 'citationKind' = 'LOCAL_DOCUMENT' THEN citation.value ->> 'documentId' ELSE NULL END,
    CASE WHEN citation.value ->> 'citationKind' = 'LOCAL_DOCUMENT' THEN citation.value ->> 'displayName' ELSE NULL END,
    citation.value -> 'locator'
  FROM jsonb_array_elements(canonical_citations) WITH ORDINALITY AS citation(value, ordinal)
  ORDER BY ordinal;

  RETURN canonical_citations;
END
$persist_rag_v2_immutable_retrieval_history$;
ALTER FUNCTION persist_rag_v2_immutable_retrieval_history(
  text, text, text, text, text, text, double precision, text[], text,
  bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea, timestamptz, jsonb
) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION persist_rag_v2_immutable_retrieval_history(
  text, text, text, text, text, text, double precision, text[], text,
  bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea, timestamptz, jsonb
) FROM PUBLIC;

DO $rag_v2_immutable_recheck_history_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    GRANT EXECUTE ON FUNCTION canonicalize_rag_v2_immutable_retrieval_citations(text, text, text, jsonb)
      TO decision_app;
    GRANT EXECUTE ON FUNCTION persist_rag_v2_immutable_retrieval_history(
      text, text, text, text, text, text, double precision, text[], text,
      bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea, timestamptz, jsonb
    ) TO decision_app;
  END IF;
END
$rag_v2_immutable_recheck_history_acl$;
