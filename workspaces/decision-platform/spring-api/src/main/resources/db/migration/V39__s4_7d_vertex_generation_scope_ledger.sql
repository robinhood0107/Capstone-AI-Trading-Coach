-- V39는 immutable top-5를 Vertex single-generator에 transiently 전달하기 전에 decision_app에서 다시
-- scope/owner/rights를 검증한다. provider request·response·canonical text는 ledger/history에 저장하지 않는다.
-- V35 retrieval-only history와 기존 BGE gRPC 계약은 byte-stable하게 유지한다.

CREATE FUNCTION read_rag_v2_vertex_generation_evidence(
  p_owner_user_id text,
  p_session_id text,
  p_scope_claim_id text,
  p_citations jsonb
)
RETURNS TABLE (
  ordinal integer,
  citation_id text,
  chunk_revision_id text,
  canonical_content text,
  canonical_content_sha256 text
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $read_rag_v2_vertex_generation_evidence$
#variable_conflict use_column
DECLARE
  canonical_citations jsonb;
  claim_row public.rag_v2_retrieval_scope_claims%ROWTYPE;
  citation_item jsonb;
  candidate record;
  output_ordinal integer := 0;
  total_content_bytes integer := 0;
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
    RAISE EXCEPTION 'immutable RAG v2 Vertex evidence arguments are invalid'
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
    RAISE EXCEPTION 'immutable RAG v2 Vertex evidence scope disappeared'
      USING ERRCODE = '55000';
  END IF;

  FOR citation_item IN SELECT value FROM jsonb_array_elements(canonical_citations)
  LOOP
    SELECT
      chunk.canonical_text,
      chunk.canonical_text_sha256
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
    WHERE membership.component_generation_id = citation_item ->> 'generationId'
      AND membership.chunk_id = citation_item ->> 'chunkRevisionId'
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
      AND source.external_processing_eligible
      AND (
        (source.source_scope IN ('EXACT30', 'OA112') AND source.owner_user_id IS NULL)
        OR (source.source_scope = 'OWNER_PRIVATE' AND source.owner_user_id = p_owner_user_id)
      )
      AND EXISTS (
        SELECT 1
        FROM public.rag_v2_immutable_generation_embeddings AS embedding
        WHERE embedding.component_generation_id = membership.component_generation_id
          AND embedding.chunk_id = membership.chunk_id
          AND embedding.component_scope = membership.component_scope
          AND embedding.owner_partition_key = membership.owner_partition_key
          AND embedding.embedding_profile_id = claim_row.embedding_profile_id
      );
    IF NOT FOUND
       OR candidate.canonical_text IS NULL
       OR candidate.canonical_text_sha256 !~ '^[0-9a-f]{64}$'
       OR octet_length(candidate.canonical_text) NOT BETWEEN 1 AND 16384 THEN
      RAISE EXCEPTION 'immutable RAG v2 Vertex evidence is not externally eligible'
        USING ERRCODE = '55000';
    END IF;

    total_content_bytes := total_content_bytes + octet_length(candidate.canonical_text);
    IF total_content_bytes > 60000 THEN
      RAISE EXCEPTION 'immutable RAG v2 Vertex evidence exceeds the bounded input cap'
        USING ERRCODE = '22023';
    END IF;
    output_ordinal := output_ordinal + 1;
    ordinal := output_ordinal;
    citation_id := citation_item ->> 'citationId';
    chunk_revision_id := citation_item ->> 'chunkRevisionId';
    canonical_content := candidate.canonical_text;
    canonical_content_sha256 := candidate.canonical_text_sha256;
    RETURN NEXT;
  END LOOP;
  IF output_ordinal NOT BETWEEN 1 AND 5 THEN
    RAISE EXCEPTION 'immutable RAG v2 Vertex evidence is empty'
      USING ERRCODE = '55000';
  END IF;
END
$read_rag_v2_vertex_generation_evidence$;
ALTER FUNCTION read_rag_v2_vertex_generation_evidence(text, text, text, jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION read_rag_v2_vertex_generation_evidence(text, text, text, jsonb) FROM PUBLIC;

CREATE FUNCTION persist_rag_v2_immutable_vertex_history(
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
AS $persist_rag_v2_immutable_vertex_history$
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
     OR octet_length(p_question_nonce) NOT BETWEEN 1 AND 8192
     OR octet_length(p_question_tag) <> 16
     OR octet_length(p_answer_nonce) <> 12
     OR octet_length(p_answer_ciphertext) NOT BETWEEN 1 AND 8192
     OR octet_length(p_answer_tag) <> 16
     OR p_created_at IS NULL
     OR p_created_at NOT BETWEEN transaction_timestamp() - interval '60 seconds'
         AND transaction_timestamp() + interval '60 seconds' THEN
    RAISE EXCEPTION 'immutable RAG v2 Vertex history persistence arguments are invalid'
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
    RAISE EXCEPTION 'immutable RAG v2 Vertex history scope disappeared'
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
    p_answer_id, p_owner_user_id, p_request_id, p_answer_mode, 'ANSWERED',
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
$persist_rag_v2_immutable_vertex_history$;
ALTER FUNCTION persist_rag_v2_immutable_vertex_history(
  text, text, text, text, text, text, double precision, text[], text,
  bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea, timestamptz, jsonb
) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION persist_rag_v2_immutable_vertex_history(
  text, text, text, text, text, text, double precision, text[], text,
  bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea, timestamptz, jsonb
) FROM PUBLIC;

CREATE TABLE rag_v2_immutable_vertex_usage_reservations (
  usage_event_id text PRIMARY KEY,
  owner_user_id text NOT NULL,
  request_id text NOT NULL,
  question_fingerprint_hmac text NOT NULL,
  packet_sha256 text NOT NULL,
  nonce_sha256 text NOT NULL,
  processor_set_sha256 text NOT NULL,
  provider text NOT NULL DEFAULT 'VERTEX_AI',
  operation text NOT NULL DEFAULT 'GENERATE_CONTENT',
  model_id text NOT NULL DEFAULT 'gemini-3.5-flash',
  expires_at timestamptz NOT NULL,
  input_token_cap integer NOT NULL,
  output_token_cap integer NOT NULL,
  input_byte_cap integer NOT NULL,
  cost_cap_microusd bigint NOT NULL,
  input_microusd_per_token bigint NOT NULL,
  output_microusd_per_token bigint NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_vertex_usage_reservation_id_check
    CHECK (usage_event_id ~ '^rgr_vgu_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_immutable_vertex_usage_reservation_owner_check
    CHECK (owner_user_id ~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'),
  CONSTRAINT rag_v2_immutable_vertex_usage_reservation_request_check
    CHECK (request_id ~ '^req_[A-Za-z0-9_-]{12,96}$'),
  CONSTRAINT rag_v2_immutable_vertex_usage_reservation_hash_check
    CHECK (
      question_fingerprint_hmac ~ '^[0-9a-f]{64}$'
      AND packet_sha256 ~ '^[0-9a-f]{64}$'
      AND nonce_sha256 ~ '^[0-9a-f]{64}$'
      AND processor_set_sha256 ~ '^[0-9a-f]{64}$'
    ),
  CONSTRAINT rag_v2_immutable_vertex_usage_reservation_provider_check
    CHECK (provider = 'VERTEX_AI' AND operation = 'GENERATE_CONTENT' AND model_id = 'gemini-3.5-flash'),
  CONSTRAINT rag_v2_immutable_vertex_usage_reservation_cap_check
    CHECK (
      input_token_cap BETWEEN 1 AND 120000
      AND output_token_cap BETWEEN 1 AND 32768
      AND input_byte_cap BETWEEN 1 AND 60000
      AND cost_cap_microusd BETWEEN 1 AND 1000000000
      AND input_microusd_per_token BETWEEN 1 AND 1000000
      AND output_microusd_per_token BETWEEN 1 AND 1000000
      AND input_token_cap::bigint * input_microusd_per_token
          + output_token_cap::bigint * output_microusd_per_token <= cost_cap_microusd
    ),
  CONSTRAINT rag_v2_immutable_vertex_usage_reservation_packet_unique UNIQUE (packet_sha256),
  CONSTRAINT rag_v2_immutable_vertex_usage_reservation_nonce_unique UNIQUE (nonce_sha256),
  CONSTRAINT rag_v2_immutable_vertex_usage_reservation_question_packet_unique
    UNIQUE (owner_user_id, question_fingerprint_hmac, packet_sha256)
);
ALTER TABLE rag_v2_immutable_vertex_usage_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_vertex_usage_reservations FORCE ROW LEVEL SECURITY;
CREATE POLICY rag_v2_immutable_vertex_usage_reservation_flyway_write
  ON rag_v2_immutable_vertex_usage_reservations
  FOR ALL TO flyway USING (true) WITH CHECK (true);

CREATE TABLE rag_v2_immutable_vertex_usage_attempts (
  usage_event_id text PRIMARY KEY
    REFERENCES rag_v2_immutable_vertex_usage_reservations (usage_event_id) ON DELETE RESTRICT,
  state text NOT NULL,
  physical_generate_content_call_count integer NOT NULL,
  claimed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_vertex_usage_attempt_state_check
    CHECK (state = 'ATTEMPTED'),
  CONSTRAINT rag_v2_immutable_vertex_usage_attempt_physical_count_check
    CHECK (physical_generate_content_call_count = 1)
);
ALTER TABLE rag_v2_immutable_vertex_usage_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_vertex_usage_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY rag_v2_immutable_vertex_usage_attempt_flyway_write
  ON rag_v2_immutable_vertex_usage_attempts
  FOR ALL TO flyway USING (true) WITH CHECK (true);

CREATE TABLE rag_v2_immutable_vertex_usage_outcomes (
  usage_event_id text PRIMARY KEY
    REFERENCES rag_v2_immutable_vertex_usage_reservations (usage_event_id) ON DELETE RESTRICT,
  packet_sha256 text NOT NULL,
  state text NOT NULL,
  prompt_token_count integer,
  candidate_token_count integer,
  total_token_count integer,
  actual_cost_microusd bigint,
  recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_vertex_usage_outcome_packet_check
    CHECK (packet_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_v2_immutable_vertex_usage_outcome_state_check
    CHECK (state IN ('COMMITTED', 'UNKNOWN_BILLING')),
  CONSTRAINT rag_v2_immutable_vertex_usage_outcome_shape_check
    CHECK (
      (state = 'COMMITTED'
        AND prompt_token_count BETWEEN 0 AND 120000
        AND candidate_token_count BETWEEN 0 AND 32768
        AND total_token_count = prompt_token_count + candidate_token_count
        AND actual_cost_microusd BETWEEN 0 AND 1000000000)
      OR (state = 'UNKNOWN_BILLING'
        AND prompt_token_count IS NULL
        AND candidate_token_count IS NULL
        AND total_token_count IS NULL
        AND actual_cost_microusd IS NULL)
    )
);
ALTER TABLE rag_v2_immutable_vertex_usage_outcomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_vertex_usage_outcomes FORCE ROW LEVEL SECURITY;
CREATE POLICY rag_v2_immutable_vertex_usage_outcome_flyway_write
  ON rag_v2_immutable_vertex_usage_outcomes
  FOR ALL TO flyway USING (true) WITH CHECK (true);

CREATE FUNCTION reject_rag_v2_immutable_vertex_usage_mutation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $reject_rag_v2_immutable_vertex_usage_mutation$
BEGIN
  RAISE EXCEPTION 'immutable Pre-S5 Vertex usage ledger mutation is forbidden'
    USING ERRCODE = '55000';
END
$reject_rag_v2_immutable_vertex_usage_mutation$;
ALTER FUNCTION reject_rag_v2_immutable_vertex_usage_mutation() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION reject_rag_v2_immutable_vertex_usage_mutation() FROM PUBLIC;

CREATE TRIGGER rag_v2_immutable_vertex_usage_reservations_append_only
BEFORE UPDATE OR DELETE ON rag_v2_immutable_vertex_usage_reservations
FOR EACH ROW EXECUTE FUNCTION reject_rag_v2_immutable_vertex_usage_mutation();
CREATE TRIGGER rag_v2_immutable_vertex_usage_attempts_append_only
BEFORE UPDATE OR DELETE ON rag_v2_immutable_vertex_usage_attempts
FOR EACH ROW EXECUTE FUNCTION reject_rag_v2_immutable_vertex_usage_mutation();
CREATE TRIGGER rag_v2_immutable_vertex_usage_outcomes_append_only
BEFORE UPDATE OR DELETE ON rag_v2_immutable_vertex_usage_outcomes
FOR EACH ROW EXECUTE FUNCTION reject_rag_v2_immutable_vertex_usage_mutation();

CREATE FUNCTION reserve_rag_v2_immutable_vertex_usage(
  p_usage_event_id text,
  p_owner_user_id text,
  p_request_id text,
  p_question_fingerprint_hmac text,
  p_packet_sha256 text,
  p_nonce_sha256 text,
  p_processor_set_sha256 text,
  p_expires_at timestamptz,
  p_input_token_cap integer,
  p_output_token_cap integer,
  p_input_byte_cap integer,
  p_cost_cap_microusd bigint,
  p_input_microusd_per_token bigint,
  p_output_microusd_per_token bigint
)
RETURNS TABLE (
  usage_event_id text,
  expires_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $reserve_rag_v2_immutable_vertex_usage$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_usage_event_id !~ '^rgr_vgu_[0-9a-f]{32}$'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_request_id !~ '^req_[A-Za-z0-9_-]{12,96}$'
     OR p_question_fingerprint_hmac !~ '^[0-9a-f]{64}$'
     OR p_packet_sha256 !~ '^[0-9a-f]{64}$'
     OR p_nonce_sha256 !~ '^[0-9a-f]{64}$'
     OR p_processor_set_sha256 !~ '^[0-9a-f]{64}$'
     OR p_expires_at <= statement_timestamp()
     OR p_expires_at > statement_timestamp() + interval '5 minutes'
     OR p_input_token_cap NOT BETWEEN 1 AND 120000
     OR p_output_token_cap NOT BETWEEN 1 AND 32768
     OR p_input_byte_cap NOT BETWEEN 1 AND 60000
     OR p_cost_cap_microusd NOT BETWEEN 1 AND 1000000000
     OR p_input_microusd_per_token NOT BETWEEN 1 AND 1000000
     OR p_output_microusd_per_token NOT BETWEEN 1 AND 1000000
     OR p_input_token_cap::bigint * p_input_microusd_per_token
          + p_output_token_cap::bigint * p_output_microusd_per_token > p_cost_cap_microusd THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex usage reservation arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-vertex-usage-reservation|' || p_packet_sha256, 0)
  );
  INSERT INTO public.rag_v2_immutable_vertex_usage_reservations (
    usage_event_id, owner_user_id, request_id, question_fingerprint_hmac,
    packet_sha256, nonce_sha256, processor_set_sha256, expires_at,
    input_token_cap, output_token_cap, input_byte_cap, cost_cap_microusd,
    input_microusd_per_token, output_microusd_per_token
  ) VALUES (
    p_usage_event_id, p_owner_user_id, p_request_id, p_question_fingerprint_hmac,
    p_packet_sha256, p_nonce_sha256, p_processor_set_sha256, p_expires_at,
    p_input_token_cap, p_output_token_cap, p_input_byte_cap, p_cost_cap_microusd,
    p_input_microusd_per_token, p_output_microusd_per_token
  );
  RETURN QUERY SELECT p_usage_event_id, p_expires_at;
END
$reserve_rag_v2_immutable_vertex_usage$;
ALTER FUNCTION reserve_rag_v2_immutable_vertex_usage(
  text, text, text, text, text, text, text, timestamptz, integer, integer, integer, bigint, bigint, bigint
) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION reserve_rag_v2_immutable_vertex_usage(
  text, text, text, text, text, text, text, timestamptz, integer, integer, integer, bigint, bigint, bigint
) FROM PUBLIC;

CREATE FUNCTION claim_rag_v2_immutable_vertex_usage_attempt(
  p_usage_event_id text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $claim_rag_v2_immutable_vertex_usage_attempt$
DECLARE
  reservation rag_v2_immutable_vertex_usage_reservations%ROWTYPE;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR p_usage_event_id !~ '^rgr_vgu_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex usage claim arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  SELECT * INTO reservation
  FROM public.rag_v2_immutable_vertex_usage_reservations
  WHERE usage_event_id = p_usage_event_id
  FOR UPDATE;
  IF NOT FOUND OR reservation.expires_at <= statement_timestamp() THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex usage claim is unavailable'
      USING ERRCODE = '55000';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.rag_v2_immutable_vertex_usage_attempts WHERE usage_event_id = p_usage_event_id
  ) OR EXISTS (
    SELECT 1 FROM public.rag_v2_immutable_vertex_usage_outcomes WHERE usage_event_id = p_usage_event_id
  ) THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex usage has already been claimed'
      USING ERRCODE = '55000';
  END IF;
  INSERT INTO public.rag_v2_immutable_vertex_usage_attempts (
    usage_event_id, state, physical_generate_content_call_count
  ) VALUES (
    p_usage_event_id, 'ATTEMPTED', 1
  );
END
$claim_rag_v2_immutable_vertex_usage_attempt$;
ALTER FUNCTION claim_rag_v2_immutable_vertex_usage_attempt(text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION claim_rag_v2_immutable_vertex_usage_attempt(text) FROM PUBLIC;

CREATE FUNCTION commit_rag_v2_immutable_vertex_usage(
  p_usage_event_id text,
  p_prompt_token_count integer,
  p_candidate_token_count integer,
  p_total_token_count integer
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $commit_rag_v2_immutable_vertex_usage$
DECLARE
  reservation rag_v2_immutable_vertex_usage_reservations%ROWTYPE;
  calculated_cost bigint;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR p_usage_event_id !~ '^rgr_vgu_[0-9a-f]{32}$'
     OR p_prompt_token_count NOT BETWEEN 0 AND 120000
     OR p_candidate_token_count NOT BETWEEN 0 AND 32768
     OR p_total_token_count <> p_prompt_token_count + p_candidate_token_count THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex usage commit arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  SELECT * INTO reservation
  FROM public.rag_v2_immutable_vertex_usage_reservations
  WHERE usage_event_id = p_usage_event_id
  FOR UPDATE;
  calculated_cost := p_prompt_token_count::bigint * reservation.input_microusd_per_token
    + p_candidate_token_count::bigint * reservation.output_microusd_per_token;
  IF NOT FOUND
     OR NOT EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_vertex_usage_attempts
       WHERE usage_event_id = p_usage_event_id
         AND state = 'ATTEMPTED'
         AND physical_generate_content_call_count = 1
     )
     OR EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_vertex_usage_outcomes WHERE usage_event_id = p_usage_event_id
     )
     OR p_prompt_token_count > reservation.input_token_cap
     OR p_candidate_token_count > reservation.output_token_cap
     OR calculated_cost > reservation.cost_cap_microusd THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex usage commit is unavailable'
      USING ERRCODE = '55000';
  END IF;
  INSERT INTO public.rag_v2_immutable_vertex_usage_outcomes (
    usage_event_id, packet_sha256, state, prompt_token_count,
    candidate_token_count, total_token_count, actual_cost_microusd
  ) VALUES (
    p_usage_event_id, reservation.packet_sha256, 'COMMITTED', p_prompt_token_count,
    p_candidate_token_count, p_total_token_count, calculated_cost
  );
END
$commit_rag_v2_immutable_vertex_usage$;
ALTER FUNCTION commit_rag_v2_immutable_vertex_usage(text, integer, integer, integer) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION commit_rag_v2_immutable_vertex_usage(text, integer, integer, integer) FROM PUBLIC;

CREATE FUNCTION mark_rag_v2_immutable_vertex_usage_unknown_billing(
  p_usage_event_id text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $mark_rag_v2_immutable_vertex_usage_unknown_billing$
DECLARE
  reservation rag_v2_immutable_vertex_usage_reservations%ROWTYPE;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR p_usage_event_id !~ '^rgr_vgu_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex unknown billing arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  SELECT * INTO reservation
  FROM public.rag_v2_immutable_vertex_usage_reservations
  WHERE usage_event_id = p_usage_event_id
  FOR UPDATE;
  IF NOT FOUND
     OR NOT EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_vertex_usage_attempts
       WHERE usage_event_id = p_usage_event_id
         AND state = 'ATTEMPTED'
         AND physical_generate_content_call_count = 1
     )
     OR EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_vertex_usage_outcomes WHERE usage_event_id = p_usage_event_id
     ) THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex unknown billing is unavailable'
      USING ERRCODE = '55000';
  END IF;
  INSERT INTO public.rag_v2_immutable_vertex_usage_outcomes (
    usage_event_id, packet_sha256, state, prompt_token_count,
    candidate_token_count, total_token_count, actual_cost_microusd
  ) VALUES (
    p_usage_event_id, reservation.packet_sha256, 'UNKNOWN_BILLING', NULL, NULL, NULL, NULL
  );
END
$mark_rag_v2_immutable_vertex_usage_unknown_billing$;
ALTER FUNCTION mark_rag_v2_immutable_vertex_usage_unknown_billing(text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION mark_rag_v2_immutable_vertex_usage_unknown_billing(text) FROM PUBLIC;

DO $rag_v2_immutable_vertex_generation_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      rag_v2_immutable_vertex_usage_reservations,
      rag_v2_immutable_vertex_usage_attempts,
      rag_v2_immutable_vertex_usage_outcomes
    FROM decision_app;
    GRANT EXECUTE ON FUNCTION read_rag_v2_vertex_generation_evidence(text, text, text, jsonb)
      TO decision_app;
    GRANT EXECUTE ON FUNCTION persist_rag_v2_immutable_vertex_history(
      text, text, text, text, text, text, double precision, text[], text,
      bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea, timestamptz, jsonb
    ) TO decision_app;
    GRANT EXECUTE ON FUNCTION reserve_rag_v2_immutable_vertex_usage(
      text, text, text, text, text, text, text, timestamptz, integer, integer, integer, bigint, bigint, bigint
    ) TO decision_app;
    GRANT EXECUTE ON FUNCTION claim_rag_v2_immutable_vertex_usage_attempt(text)
      TO decision_app;
    GRANT EXECUTE ON FUNCTION commit_rag_v2_immutable_vertex_usage(text, integer, integer, integer)
      TO decision_app;
    GRANT EXECUTE ON FUNCTION mark_rag_v2_immutable_vertex_usage_unknown_billing(text)
      TO decision_app;
  END IF;
END
$rag_v2_immutable_vertex_generation_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION read_rag_v2_vertex_generation_evidence(text, text, text, jsonb) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION persist_rag_v2_immutable_vertex_history(
  text, text, text, text, text, text, double precision, text[], text,
  bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea, bytea, timestamptz, jsonb
) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION reserve_rag_v2_immutable_vertex_usage(
  text, text, text, text, text, text, text, timestamptz, integer, integer, integer, bigint, bigint, bigint
) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION claim_rag_v2_immutable_vertex_usage_attempt(text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION commit_rag_v2_immutable_vertex_usage(text, integer, integer, integer) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION mark_rag_v2_immutable_vertex_usage_unknown_billing(text) FROM PUBLIC;
