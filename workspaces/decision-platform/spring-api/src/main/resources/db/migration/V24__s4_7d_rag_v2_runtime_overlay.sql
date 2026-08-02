-- S4.7D RAG v2는 v1 exact-30 API/history를 건드리지 않고 public OA와 owner-private
-- overlay를 별도 generation pointer로 pin한다. 원본 로컬 경로와 추출 text는 DB 경계에 두지 않는다.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE rag_v2_public_corpus_state (
  state_id text PRIMARY KEY,
  state text NOT NULL,
  public_corpus_version text NOT NULL,
  exact30_generation_id text,
  oa_generation_id text,
  progress_percent integer NOT NULL,
  failure_code text,
  version bigint NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_public_corpus_state_id_check
    CHECK (state_id = 'default'),
  CONSTRAINT rag_v2_public_corpus_state_state_check
    CHECK (state IN ('CORE_READY', 'BUILDING', 'FULL_READY', 'FAILED')),
  CONSTRAINT rag_v2_public_corpus_state_version_check
    CHECK (char_length(public_corpus_version) BETWEEN 1 AND 128),
  CONSTRAINT rag_v2_public_corpus_state_generation_check
    CHECK (
      (exact30_generation_id IS NULL OR exact30_generation_id ~ '^rag_gen_[0-9a-f]{32}$')
      AND
      (oa_generation_id IS NULL OR oa_generation_id ~ '^rag_gen_[0-9a-f]{32}$')
    ),
  CONSTRAINT rag_v2_public_corpus_state_progress_check
    CHECK (progress_percent BETWEEN 0 AND 100),
  CONSTRAINT rag_v2_public_corpus_state_failure_check
    CHECK (
      (
        state = 'FAILED'
        AND failure_code IN (
          'DOWNLOAD_UNAVAILABLE',
          'SOURCE_DRIFT',
          'PARSER_FAILED',
          'OCR_QUALITY_FAILED',
          'EMBEDDING_FAILED',
          'DISK_FULL',
          'ACTIVATION_CONFLICT'
        )
      )
      OR
      (state <> 'FAILED' AND failure_code IS NULL)
    ),
  CONSTRAINT rag_v2_public_corpus_state_counter_check CHECK (version >= 1)
);

INSERT INTO rag_v2_public_corpus_state (
  state_id,
  state,
  public_corpus_version,
  exact30_generation_id,
  oa_generation_id,
  progress_percent,
  failure_code,
  version
)
VALUES (
  'default',
  'CORE_READY',
  'exact30-v1+oa140-draft-v1',
  NULL,
  NULL,
  0,
  NULL,
  1
)
ON CONFLICT (state_id) DO NOTHING;

CREATE TABLE rag_v2_owner_private_generation_pointers (
  owner_user_id text PRIMARY KEY REFERENCES users(user_id) ON DELETE RESTRICT,
  private_overlay_state text NOT NULL,
  active_private_generation_id text,
  external_llm_opt_in boolean NOT NULL DEFAULT false,
  progress_percent integer NOT NULL DEFAULT 0,
  failure_code text,
  generation_version bigint NOT NULL DEFAULT 1,
  updated_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_private_pointer_state_check
    CHECK (private_overlay_state IN ('ABSENT', 'BUILDING', 'READY', 'FAILED')),
  CONSTRAINT rag_v2_private_pointer_generation_check
    CHECK (
      active_private_generation_id IS NULL
      OR active_private_generation_id ~ '^rag_owner_gen_[0-9a-f]{32}$'
    ),
  CONSTRAINT rag_v2_private_pointer_state_generation_check
    CHECK (
      (private_overlay_state = 'READY' AND active_private_generation_id IS NOT NULL)
      OR
      (private_overlay_state <> 'READY')
    ),
  CONSTRAINT rag_v2_private_pointer_progress_check
    CHECK (progress_percent BETWEEN 0 AND 100),
  CONSTRAINT rag_v2_private_pointer_failure_check
    CHECK (
      (
        private_overlay_state = 'FAILED'
        AND failure_code IN (
          'PARSER_FAILED',
          'OCR_QUALITY_FAILED',
          'EMBEDDING_FAILED',
          'DISK_FULL',
          'ACTIVATION_CONFLICT'
        )
      )
      OR
      (private_overlay_state <> 'FAILED' AND failure_code IS NULL)
    ),
  CONSTRAINT rag_v2_private_pointer_counter_check CHECK (generation_version >= 1)
);

CREATE TABLE rag_v2_owner_documents (
  document_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  owner_generation_id text NOT NULL,
  sanitized_display_name text NOT NULL,
  mime_type text NOT NULL,
  raw_sha256 text NOT NULL,
  normalized_sha256 text NOT NULL,
  source_revision_id text NOT NULL,
  processing_mode text NOT NULL,
  local_processing_allowed boolean NOT NULL,
  external_llm_allowed boolean NOT NULL,
  parser_backend text NOT NULL,
  parser_version text NOT NULL,
  ocr_backend text,
  ocr_model_hash text,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_owner_documents_id_check
    CHECK (document_id ~ '^doc_[a-z0-9][a-z0-9_-]{10,95}$'),
  CONSTRAINT rag_v2_owner_documents_generation_check
    CHECK (owner_generation_id ~ '^rag_owner_gen_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_owner_documents_display_check
    CHECK (
      char_length(sanitized_display_name) BETWEEN 1 AND 160
      AND sanitized_display_name !~ '[/\\:]'
    ),
  CONSTRAINT rag_v2_owner_documents_mime_check
    CHECK (
      mime_type IN (
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'text/html',
        'text/markdown',
        'text/plain',
        'image/png',
        'image/jpeg',
        'image/tiff'
      )
    ),
  CONSTRAINT rag_v2_owner_documents_hash_check
    CHECK (
      raw_sha256 ~ '^[0-9a-f]{64}$'
      AND normalized_sha256 ~ '^[0-9a-f]{64}$'
      AND source_revision_id ~ '^src_rev_[0-9a-f]{32}$'
      AND (ocr_model_hash IS NULL OR ocr_model_hash ~ '^[0-9a-f]{64}$')
    ),
  CONSTRAINT rag_v2_owner_documents_processing_check
    CHECK (
      processing_mode = 'LOCAL_EPHEMERAL_PARSE'
      AND local_processing_allowed
    ),
  CONSTRAINT rag_v2_owner_documents_backend_check
    CHECK (
      char_length(parser_backend) BETWEEN 1 AND 64
      AND char_length(parser_version) BETWEEN 1 AND 128
      AND (ocr_backend IS NULL OR char_length(ocr_backend) BETWEEN 1 AND 64)
    ),
  CONSTRAINT rag_v2_owner_documents_owner_identity_unique
    UNIQUE (owner_user_id, document_id)
);
CREATE INDEX rag_v2_owner_documents_owner_generation_idx
  ON rag_v2_owner_documents (owner_user_id, owner_generation_id, document_id);

CREATE TABLE rag_v2_owner_document_chunks (
  chunk_id text PRIMARY KEY,
  document_id text NOT NULL REFERENCES rag_v2_owner_documents(document_id) ON DELETE RESTRICT,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  owner_generation_id text NOT NULL,
  chunk_ordinal integer NOT NULL,
  block_kind text NOT NULL,
  locator jsonb NOT NULL,
  content_sha256 text NOT NULL,
  embedding_input_hash text NOT NULL,
  token_count integer NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_owner_chunks_id_check
    CHECK (chunk_id ~ '^rag_v2_chk_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_owner_chunks_generation_check
    CHECK (owner_generation_id ~ '^rag_owner_gen_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_owner_chunks_block_check
    CHECK (block_kind IN ('heading', 'paragraph', 'list', 'table', 'formula', 'caption')),
  CONSTRAINT rag_v2_owner_chunks_locator_check
    CHECK (
      jsonb_typeof(locator) = 'object'
      AND (
        locator ? 'page'
        OR locator ? 'slide'
        OR locator ? 'sheet'
        OR locator ? 'section'
      )
      AND NOT (locator ? 'path')
      AND NOT (locator ? 'url')
    ),
  CONSTRAINT rag_v2_owner_chunks_hash_check
    CHECK (
      content_sha256 ~ '^[0-9a-f]{64}$'
      AND embedding_input_hash ~ '^[0-9a-f]{64}$'
    ),
  CONSTRAINT rag_v2_owner_chunks_count_check
    CHECK (chunk_ordinal BETWEEN 1 AND 100000 AND token_count BETWEEN 1 AND 2048),
  CONSTRAINT rag_v2_owner_chunks_owner_document_fkey
    FOREIGN KEY (owner_user_id, document_id)
    REFERENCES rag_v2_owner_documents(owner_user_id, document_id)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_owner_chunks_document_ordinal_unique
    UNIQUE (owner_user_id, document_id, chunk_ordinal)
);
CREATE INDEX rag_v2_owner_chunks_owner_generation_idx
  ON rag_v2_owner_document_chunks (owner_user_id, owner_generation_id, chunk_id);

CREATE TABLE rag_v2_owner_document_embeddings (
  chunk_id text PRIMARY KEY
    REFERENCES rag_v2_owner_document_chunks(chunk_id) ON DELETE RESTRICT,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  owner_generation_id text NOT NULL,
  embedding_profile_id text NOT NULL,
  embedding_input_hash text NOT NULL,
  embedding vector(1024) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_owner_embeddings_generation_check
    CHECK (owner_generation_id ~ '^rag_owner_gen_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_owner_embeddings_profile_check
    CHECK (embedding_profile_id = 'bge_m3_local_1024_v1'),
  CONSTRAINT rag_v2_owner_embeddings_hash_check
    CHECK (embedding_input_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_v2_owner_embeddings_dimension_check
    CHECK (vector_dims(embedding) = 1024),
  CONSTRAINT rag_v2_owner_embeddings_finite_check
    CHECK (vector_norm(embedding)::text NOT IN ('NaN', 'Infinity', '-Infinity')),
  CONSTRAINT rag_v2_owner_embeddings_normalized_check
    CHECK (abs(vector_norm(embedding)::double precision - 1.0) <= 0.00001)
);
CREATE INDEX rag_v2_owner_embeddings_owner_generation_idx
  ON rag_v2_owner_document_embeddings (owner_user_id, owner_generation_id, chunk_id);

CREATE TABLE rag_v2_document_deletion_receipts (
  deletion_receipt_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  document_id text NOT NULL,
  deleted_generation_id text,
  reason_hash text NOT NULL,
  content_hash_was_removed boolean NOT NULL,
  deleted_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_document_deletion_receipts_id_check
    CHECK (deletion_receipt_id ~ '^rag_v2_del_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_document_deletion_receipts_document_check
    CHECK (document_id ~ '^doc_[a-z0-9][a-z0-9_-]{10,95}$'),
  CONSTRAINT rag_v2_document_deletion_receipts_generation_check
    CHECK (deleted_generation_id IS NULL OR deleted_generation_id ~ '^rag_owner_gen_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_document_deletion_receipts_hash_check
    CHECK (reason_hash ~ '^[0-9a-f]{64}$' AND content_hash_was_removed),
  CONSTRAINT rag_v2_document_deletion_receipts_unique
    UNIQUE (owner_user_id, document_id, deletion_receipt_id)
);
CREATE INDEX rag_v2_document_deletion_receipts_owner_idx
  ON rag_v2_document_deletion_receipts (owner_user_id, deleted_at DESC);

CREATE TABLE rag_v2_answer_history (
  answer_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  request_id text NOT NULL,
  answer_mode text NOT NULL,
  generation_status text NOT NULL,
  citation_coverage double precision NOT NULL,
  retrieval_failure boolean NOT NULL,
  guardrail_flags text[] NOT NULL,
  public_corpus_version text NOT NULL,
  private_overlay_state text NOT NULL,
  kek_version text NOT NULL,
  wrap_nonce bytea NOT NULL,
  wrapped_dek bytea NOT NULL,
  wrap_tag bytea NOT NULL,
  question_nonce bytea NOT NULL,
  question_ciphertext bytea NOT NULL,
  question_tag bytea NOT NULL,
  answer_nonce bytea NOT NULL,
  answer_ciphertext bytea NOT NULL,
  answer_tag bytea NOT NULL,
  citation_count integer NOT NULL,
  created_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  CONSTRAINT rag_v2_answer_history_id_check
    CHECK (answer_id ~ '^rag_[A-Za-z0-9_-]{12,96}$'),
  CONSTRAINT rag_v2_answer_history_request_check
    CHECK (request_id ~ '^req_[A-Za-z0-9_-]{12,96}$'),
  CONSTRAINT rag_v2_answer_history_mode_check
    CHECK (answer_mode IN ('CONCISE', 'DETAILED')),
  CONSTRAINT rag_v2_answer_history_status_check
    CHECK (generation_status IN ('ANSWERED', 'RETRIEVAL_ONLY', 'RETRIEVAL_FAILURE')),
  CONSTRAINT rag_v2_answer_history_coverage_check
    CHECK (citation_coverage BETWEEN 0.0 AND 1.0),
  CONSTRAINT rag_v2_answer_history_bundle_check
    CHECK (
      char_length(public_corpus_version) BETWEEN 1 AND 128
      AND private_overlay_state IN ('ABSENT', 'READY')
    ),
  CONSTRAINT rag_v2_answer_history_status_result_check
    CHECK (
      (
        generation_status = 'ANSWERED'
        AND citation_count BETWEEN 1 AND 5
        AND citation_coverage >= 0.8
        AND NOT retrieval_failure
      )
      OR
      (
        generation_status = 'RETRIEVAL_ONLY'
        AND citation_count BETWEEN 0 AND 5
        AND NOT retrieval_failure
      )
      OR
      (
        generation_status = 'RETRIEVAL_FAILURE'
        AND citation_count = 0
        AND citation_coverage = 0.0
        AND retrieval_failure
      )
    ),
  CONSTRAINT rag_v2_answer_history_flags_check
    CHECK (
      cardinality(guardrail_flags) BETWEEN 0 AND 20
      AND array_position(guardrail_flags, '') IS NULL
      AND octet_length(array_to_string(guardrail_flags, '')) <= 1024
      AND array_to_string(guardrail_flags, '') ~ '^[A-Z0-9_]*$'
    ),
  CONSTRAINT rag_v2_answer_history_crypto_check
    CHECK (
      kek_version ~ '^kek-v[1-9][0-9]{0,8}$'
      AND octet_length(wrap_nonce) = 12
      AND octet_length(wrapped_dek) = 32
      AND octet_length(wrap_tag) = 16
      AND octet_length(question_nonce) = 12
      AND octet_length(question_ciphertext) BETWEEN 1 AND 8192
      AND octet_length(question_tag) = 16
      AND octet_length(answer_nonce) = 12
      AND octet_length(answer_ciphertext) BETWEEN 1 AND 8192
      AND octet_length(answer_tag) = 16
    ),
  CONSTRAINT rag_v2_answer_history_expiry_check
    CHECK (expires_at = created_at + interval '30 days'),
  CONSTRAINT rag_v2_answer_history_owner_answer_unique
    UNIQUE (owner_user_id, answer_id)
);
CREATE INDEX rag_v2_answer_history_owner_created_idx
  ON rag_v2_answer_history (owner_user_id, created_at DESC, answer_id DESC);

CREATE TABLE rag_v2_answer_citations (
  answer_id text NOT NULL REFERENCES rag_v2_answer_history(answer_id) ON DELETE CASCADE,
  owner_user_id text NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
  ordinal integer NOT NULL,
  citation_kind text NOT NULL,
  source_id text,
  title text,
  canonical_url text,
  document_id text,
  sanitized_display_name text,
  locator jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_answer_citations_ordinal_check CHECK (ordinal BETWEEN 1 AND 5),
  CONSTRAINT rag_v2_answer_citations_kind_check CHECK (citation_kind IN ('PUBLIC_WEB', 'LOCAL_DOCUMENT')),
  CONSTRAINT rag_v2_answer_citations_locator_check
    CHECK (
      jsonb_typeof(locator) = 'object'
      AND (
        locator ? 'page'
        OR locator ? 'slide'
        OR locator ? 'sheet'
        OR locator ? 'section'
      )
      AND NOT (locator ? 'path')
      AND NOT (locator ? 'url')
    ),
  CONSTRAINT rag_v2_answer_citations_public_check
    CHECK (
      (
        citation_kind = 'PUBLIC_WEB'
        AND source_id ~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'
        AND char_length(title) BETWEEN 1 AND 500
        AND canonical_url ~ '^https://'
        AND octet_length(canonical_url) BETWEEN 9 AND 2048
        AND document_id IS NULL
        AND sanitized_display_name IS NULL
      )
      OR
      (
        citation_kind = 'LOCAL_DOCUMENT'
        AND source_id IS NULL
        AND title IS NULL
        AND canonical_url IS NULL
        AND document_id ~ '^doc_[a-z0-9][a-z0-9_-]{10,95}$'
        AND char_length(sanitized_display_name) BETWEEN 1 AND 160
        AND sanitized_display_name !~ '[/\\:]'
      )
    ),
  CONSTRAINT rag_v2_answer_citations_pkey PRIMARY KEY (answer_id, ordinal),
  CONSTRAINT rag_v2_answer_citations_owner_history_fkey
    FOREIGN KEY (owner_user_id, answer_id)
    REFERENCES rag_v2_answer_history(owner_user_id, answer_id)
    ON DELETE CASCADE
);

ALTER TABLE rag_v2_owner_private_generation_pointers ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_owner_private_generation_pointers FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_owner_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_owner_documents FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_owner_document_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_owner_document_chunks FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_owner_document_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_owner_document_embeddings FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_document_deletion_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_document_deletion_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_answer_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_answer_history FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_answer_citations ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_answer_citations FORCE ROW LEVEL SECURITY;

CREATE POLICY rag_v2_private_pointer_owner_policy
  ON rag_v2_owner_private_generation_pointers
  USING (owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_owner_documents_owner_policy
  ON rag_v2_owner_documents
  USING (owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_owner_chunks_owner_policy
  ON rag_v2_owner_document_chunks
  USING (owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_owner_embeddings_owner_policy
  ON rag_v2_owner_document_embeddings
  USING (owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_deletion_receipts_owner_policy
  ON rag_v2_document_deletion_receipts
  USING (owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_answer_history_owner_policy
  ON rag_v2_answer_history
  USING (owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id = current_setting('app.actor_user_id', true));
CREATE POLICY rag_v2_answer_citations_owner_policy
  ON rag_v2_answer_citations
  USING (owner_user_id = current_setting('app.actor_user_id', true))
  WITH CHECK (owner_user_id = current_setting('app.actor_user_id', true));

CREATE FUNCTION read_rag_v2_corpus_status(p_owner_user_id text)
RETURNS TABLE (
  state text,
  public_corpus_version text,
  private_overlay_state text,
  progress_percent integer,
  failure_code text
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $read_rag_v2_corpus_status$
DECLARE
  public_state record;
  private_state record;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR NOT EXISTS (
       SELECT 1 FROM public.users AS actor
       WHERE actor.user_id = p_owner_user_id
         AND actor.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'RAG v2 corpus status arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  SELECT *
  INTO public_state
  FROM public.rag_v2_public_corpus_state AS corpus
  WHERE corpus.state_id = 'default';

  SELECT *
  INTO private_state
  FROM public.rag_v2_owner_private_generation_pointers AS private
  WHERE private.owner_user_id = p_owner_user_id;

  RETURN QUERY
  SELECT
    CASE
      WHEN public_state.state = 'FAILED'
        OR coalesce(private_state.private_overlay_state, 'ABSENT') = 'FAILED'
        THEN 'FAILED'
      WHEN public_state.state = 'FULL_READY'
        AND coalesce(private_state.private_overlay_state, 'ABSENT') IN ('ABSENT', 'READY')
        THEN 'FULL_READY'
      WHEN public_state.state = 'BUILDING'
        OR coalesce(private_state.private_overlay_state, 'ABSENT') = 'BUILDING'
        THEN 'BUILDING'
      ELSE 'CORE_READY'
    END,
    public_state.public_corpus_version,
    coalesce(private_state.private_overlay_state, 'ABSENT'),
    greatest(public_state.progress_percent, coalesce(private_state.progress_percent, 0)),
    coalesce(private_state.failure_code, public_state.failure_code);
END
$read_rag_v2_corpus_status$;

CREATE FUNCTION read_rag_v2_history_metadata(
  p_owner_user_id text,
  p_cursor_created_at timestamptz,
  p_cursor_answer_id text,
  p_limit integer
)
RETURNS TABLE (
  answer_id text,
  created_at timestamptz,
  expires_at timestamptz,
  generation_status text
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $read_rag_v2_history_metadata$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_limit NOT BETWEEN 1 AND 51
     OR (
       p_cursor_answer_id IS NOT NULL
       AND p_cursor_answer_id !~ '^rag_[A-Za-z0-9_-]{12,96}$'
     ) THEN
    RAISE EXCEPTION 'RAG v2 history metadata arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  RETURN QUERY
  SELECT
    history.answer_id,
    history.created_at,
    history.expires_at,
    history.generation_status
  FROM public.rag_v2_answer_history AS history
  WHERE history.owner_user_id = p_owner_user_id
    AND history.expires_at > statement_timestamp()
    AND (
      p_cursor_answer_id IS NULL
      OR
      (history.created_at, history.answer_id) < (p_cursor_created_at, p_cursor_answer_id)
    )
  ORDER BY history.created_at DESC, history.answer_id DESC
  LIMIT p_limit;
END
$read_rag_v2_history_metadata$;

CREATE FUNCTION read_rag_v2_history_detail(
  p_owner_user_id text,
  p_answer_id text
)
RETURNS TABLE (
  answer_id text,
  created_at timestamptz,
  expires_at timestamptz,
  answer_mode text,
  generation_status text,
  kek_version text,
  wrap_nonce bytea,
  wrapped_dek bytea,
  wrap_tag bytea,
  question_nonce bytea,
  question_ciphertext bytea,
  question_tag bytea,
  answer_nonce bytea,
  answer_ciphertext bytea,
  answer_tag bytea,
  citations jsonb
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $read_rag_v2_history_detail$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_answer_id !~ '^rag_[A-Za-z0-9_-]{12,96}$' THEN
    RAISE EXCEPTION 'RAG v2 history detail arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  RETURN QUERY
  SELECT
    history.answer_id,
    history.created_at,
    history.expires_at,
    history.answer_mode,
    history.generation_status,
    history.kek_version,
    history.wrap_nonce,
    history.wrapped_dek,
    history.wrap_tag,
    history.question_nonce,
    history.question_ciphertext,
    history.question_tag,
    history.answer_nonce,
    history.answer_ciphertext,
    history.answer_tag,
    coalesce(
      jsonb_agg(
        CASE
          WHEN citation.citation_kind = 'PUBLIC_WEB' THEN
            jsonb_build_object(
              'citationKind', 'PUBLIC_WEB',
              'sourceId', citation.source_id,
              'title', citation.title,
              'canonicalUrl', citation.canonical_url,
              'locator', citation.locator
            )
          ELSE
            jsonb_build_object(
              'citationKind', 'LOCAL_DOCUMENT',
              'documentId', citation.document_id,
              'displayName', citation.sanitized_display_name,
              'locator', citation.locator
            )
        END
        ORDER BY citation.ordinal
      ) FILTER (WHERE citation.answer_id IS NOT NULL),
      '[]'::jsonb
    ) AS citations
  FROM public.rag_v2_answer_history AS history
  LEFT JOIN public.rag_v2_answer_citations AS citation
    ON citation.answer_id = history.answer_id
   AND citation.owner_user_id = history.owner_user_id
  WHERE history.owner_user_id = p_owner_user_id
    AND history.answer_id = p_answer_id
    AND history.expires_at > statement_timestamp()
  GROUP BY history.answer_id;
END
$read_rag_v2_history_detail$;

CREATE FUNCTION delete_owned_rag_v2_history(
  p_owner_user_id text,
  p_answer_id text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $delete_owned_rag_v2_history$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_answer_id !~ '^rag_[A-Za-z0-9_-]{12,96}$' THEN
    RAISE EXCEPTION 'RAG v2 history delete arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  DELETE FROM public.rag_v2_answer_history AS history
  WHERE history.owner_user_id = p_owner_user_id
    AND history.answer_id = p_answer_id;
END
$delete_owned_rag_v2_history$;

CREATE FUNCTION delete_owner_rag_v2_document(
  p_owner_user_id text,
  p_document_id text,
  p_deletion_receipt_id text,
  p_reason_hash text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $delete_owner_rag_v2_document$
DECLARE
  target_generation text;
  deleted_document_count integer;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_document_id !~ '^doc_[a-z0-9][a-z0-9_-]{10,95}$'
     OR p_deletion_receipt_id !~ '^rag_v2_del_[0-9a-f]{32}$'
     OR p_reason_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'RAG v2 document delete arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  SELECT document.owner_generation_id
  INTO target_generation
  FROM public.rag_v2_owner_documents AS document
  WHERE document.owner_user_id = p_owner_user_id
    AND document.document_id = p_document_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RETURN false;
  END IF;

  PERFORM 1
  FROM public.rag_v2_owner_private_generation_pointers AS pointer
  WHERE pointer.owner_user_id = p_owner_user_id
  FOR UPDATE;

  DELETE FROM public.rag_v2_owner_document_embeddings AS embedding
  WHERE embedding.owner_user_id = p_owner_user_id
    AND embedding.chunk_id IN (
      SELECT chunk.chunk_id
      FROM public.rag_v2_owner_document_chunks AS chunk
      WHERE chunk.owner_user_id = p_owner_user_id
        AND chunk.document_id = p_document_id
    );

  DELETE FROM public.rag_v2_owner_document_chunks AS chunk
  WHERE chunk.owner_user_id = p_owner_user_id
    AND chunk.document_id = p_document_id;

  DELETE FROM public.rag_v2_owner_documents AS document
  WHERE document.owner_user_id = p_owner_user_id
    AND document.document_id = p_document_id;
  GET DIAGNOSTICS deleted_document_count = ROW_COUNT;

  INSERT INTO public.rag_v2_document_deletion_receipts (
    deletion_receipt_id,
    owner_user_id,
    document_id,
    deleted_generation_id,
    reason_hash,
    content_hash_was_removed
  )
  VALUES (
    p_deletion_receipt_id,
    p_owner_user_id,
    p_document_id,
    target_generation,
    p_reason_hash,
    true
  );

  UPDATE public.rag_v2_owner_private_generation_pointers AS pointer
  SET private_overlay_state = 'ABSENT',
      active_private_generation_id = NULL,
      progress_percent = 0,
      failure_code = NULL,
      generation_version = pointer.generation_version + 1,
      updated_at = transaction_timestamp()
  WHERE pointer.owner_user_id = p_owner_user_id
    AND pointer.active_private_generation_id IS NOT DISTINCT FROM target_generation;

  RETURN deleted_document_count = 1;
END
$delete_owner_rag_v2_document$;

ALTER FUNCTION read_rag_v2_corpus_status(text) OWNER TO flyway;
ALTER FUNCTION read_rag_v2_history_metadata(text, timestamptz, text, integer) OWNER TO flyway;
ALTER FUNCTION read_rag_v2_history_detail(text, text) OWNER TO flyway;
ALTER FUNCTION delete_owned_rag_v2_history(text, text) OWNER TO flyway;
ALTER FUNCTION delete_owner_rag_v2_document(text, text, text, text) OWNER TO flyway;

REVOKE ALL PRIVILEGES ON FUNCTION read_rag_v2_corpus_status(text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION read_rag_v2_history_metadata(text, timestamptz, text, integer) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION read_rag_v2_history_detail(text, text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION delete_owned_rag_v2_history(text, text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION delete_owner_rag_v2_document(text, text, text, text) FROM PUBLIC;

DO $s4_7d_rag_v2_runtime_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    GRANT EXECUTE ON FUNCTION read_rag_v2_corpus_status(text) TO decision_app;
    GRANT EXECUTE
      ON FUNCTION read_rag_v2_history_metadata(text, timestamptz, text, integer)
      TO decision_app;
    GRANT EXECUTE ON FUNCTION read_rag_v2_history_detail(text, text) TO decision_app;
    GRANT EXECUTE ON FUNCTION delete_owned_rag_v2_history(text, text) TO decision_app;
    GRANT EXECUTE ON FUNCTION delete_owner_rag_v2_document(text, text, text, text) TO decision_app;
  END IF;
END
$s4_7d_rag_v2_runtime_acl$;

REVOKE ALL PRIVILEGES ON TABLE
  rag_v2_public_corpus_state,
  rag_v2_owner_private_generation_pointers,
  rag_v2_owner_documents,
  rag_v2_owner_document_chunks,
  rag_v2_owner_document_embeddings,
  rag_v2_document_deletion_receipts,
  rag_v2_answer_history,
  rag_v2_answer_citations
FROM PUBLIC;
