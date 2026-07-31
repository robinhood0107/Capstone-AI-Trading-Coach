-- S4.2B exact 30-card BGE generation의 per-row receipt, independent verification과 CAS activation 경계다.
ALTER TABLE rag_chunk_embeddings
  ADD COLUMN materialization_row_hash text;
ALTER TABLE rag_chunk_embeddings
  ADD CONSTRAINT rag_chunk_embeddings_materialization_row_hash_check
  CHECK (
    materialization_row_hash IS NULL
    OR materialization_row_hash ~ '^[0-9a-f]{64}$'
  );

-- S4.2A PoC finalizer는 그대로 두고 full generation만 row receipt를 보존하는 v2 경계를 사용한다.
CREATE FUNCTION finalize_rag_embedding_staging_v2(
  p_generation_id text,
  p_materialization_run_id text,
  p_expected_writer_role text,
  p_expected_row_count integer,
  p_expected_staging_hash text
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $finalize_rag_embedding_staging_v2$
DECLARE
  generation_status text;
  generation_profile text;
  generation_expected_row_count integer;
  staged_row_count bigint;
  distinct_chunk_count bigint;
  invalid_membership_count bigint;
  invalid_vector_count bigint;
  computed_staging_hash text;
  inserted_row_count integer;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_expected_writer_role <> session_user
     OR p_generation_id !~ '^rag_gen_[0-9a-f]{32}$'
     OR octet_length(p_materialization_run_id) <> 40
     OR p_materialization_run_id !~ '^rag_mat_[0-9a-f]{32}$'
     OR p_expected_row_count NOT BETWEEN 1 AND 10000
     OR p_expected_staging_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'RAG full generation finalize caller or argument is invalid'
      USING ERRCODE = '22023';
  END IF;

  SELECT
    generation.status,
    generation.embedding_profile_id,
    generation.expected_chunk_count
  INTO
    generation_status,
    generation_profile,
    generation_expected_row_count
  FROM public.rag_corpus_generations AS generation
  WHERE generation.corpus_generation_id = p_generation_id
  FOR UPDATE;
  IF NOT FOUND
     OR generation_status <> 'MATERIALIZING'
     OR generation_profile <> 'bge_m3_local_1024_v1'
     OR generation_expected_row_count <> p_expected_row_count THEN
    RAISE EXCEPTION 'RAG full generation finalize target is not bounded'
      USING ERRCODE = '23514';
  END IF;

  SELECT
    count(*),
    count(DISTINCT staging.chunk_revision_id),
    count(*) FILTER (
      WHERE membership.chunk_revision_id IS NULL
         OR staging.embedding_profile_id IS DISTINCT FROM generation_profile
         OR staging.writer_role::text IS DISTINCT FROM p_expected_writer_role
         OR staging.embedding_input_hash IS DISTINCT FROM membership.embedding_input_hash
         OR staging.context_set_hash IS DISTINCT FROM membership.context_set_hash
    ),
    count(*) FILTER (
      WHERE vector_dims(staging.embedding) <> 1024
         OR vector_norm(staging.embedding)::text IN ('NaN', 'Infinity', '-Infinity')
         OR abs(vector_norm(staging.embedding)::double precision - 1.0) > 0.00001
    ),
    encode(
      digest(
        coalesce(
          string_agg(
            staging.staging_row_hash,
            ''
            ORDER BY convert_to(staging.chunk_revision_id, 'UTF8')
          ),
          ''
        ),
        'sha256'
      ),
      'hex'
    )
  INTO
    staged_row_count,
    distinct_chunk_count,
    invalid_membership_count,
    invalid_vector_count,
    computed_staging_hash
  FROM public.rag_embedding_staging AS staging
  LEFT JOIN public.rag_generation_chunks AS membership
    ON membership.corpus_generation_id = staging.generation_id
   AND membership.chunk_revision_id = staging.chunk_revision_id
   AND membership.embedding_profile_id = staging.embedding_profile_id
  WHERE staging.generation_id = p_generation_id
    AND staging.materialization_run_id = p_materialization_run_id;

  IF staged_row_count <> p_expected_row_count
     OR distinct_chunk_count <> p_expected_row_count
     OR invalid_membership_count <> 0
     OR invalid_vector_count <> 0
     OR computed_staging_hash IS DISTINCT FROM p_expected_staging_hash THEN
    RAISE EXCEPTION 'RAG full generation finalize validation failed'
      USING ERRCODE = '23514';
  END IF;

  INSERT INTO public.rag_chunk_embeddings (
    chunk_embedding_id,
    corpus_generation_id,
    chunk_revision_id,
    embedding_profile_id,
    vector_space,
    embedding_input_hash,
    context_set_hash,
    embedding,
    materialization_row_hash
  )
  SELECT
    'rag_emb_' ||
      substr(
        encode(
          digest(
            concat_ws(
              E'\n',
              staging.generation_id,
              staging.chunk_revision_id,
              staging.embedding_profile_id,
              staging.embedding_input_hash,
              coalesce(staging.context_set_hash, '')
            ),
            'sha256'
          ),
          'hex'
        ),
        1,
        32
      ),
    staging.generation_id,
    staging.chunk_revision_id,
    staging.embedding_profile_id,
    staging.embedding_profile_id,
    staging.embedding_input_hash,
    staging.context_set_hash,
    staging.embedding,
    staging.staging_row_hash
  FROM public.rag_embedding_staging AS staging
  WHERE staging.generation_id = p_generation_id
    AND staging.materialization_run_id = p_materialization_run_id
    AND staging.writer_role::text = p_expected_writer_role
  ORDER BY convert_to(staging.chunk_revision_id, 'UTF8')
  ON CONFLICT ON CONSTRAINT rag_chunk_embeddings_identity_unique DO NOTHING;
  GET DIAGNOSTICS inserted_row_count = ROW_COUNT;
  IF inserted_row_count <> p_expected_row_count THEN
    RAISE EXCEPTION 'RAG full generation finalize encountered an existing final identity'
      USING ERRCODE = '23505';
  END IF;

  UPDATE public.rag_corpus_generations AS generation
  SET actual_chunk_count = p_expected_row_count
  WHERE generation.corpus_generation_id = p_generation_id
    AND generation.status = 'MATERIALIZING'
    AND generation.actual_chunk_count = 0;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'RAG full generation finalize counter drifted'
      USING ERRCODE = '23514';
  END IF;

  DELETE FROM public.rag_embedding_staging AS staging
  WHERE staging.generation_id = p_generation_id
    AND staging.materialization_run_id = p_materialization_run_id
    AND staging.writer_role::text = p_expected_writer_role;
  GET DIAGNOSTICS staged_row_count = ROW_COUNT;
  IF staged_row_count <> p_expected_row_count THEN
    RAISE EXCEPTION 'RAG full generation finalize cleanup failed'
      USING ERRCODE = '23514';
  END IF;
  RETURN inserted_row_count;
END
$finalize_rag_embedding_staging_v2$;
ALTER FUNCTION finalize_rag_embedding_staging_v2(text, text, text, integer, text)
  OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION finalize_rag_embedding_staging_v2(text, text, text, integer, text)
  FROM PUBLIC;

-- model/runtime evidence는 pointer와 함께 한 transaction에서만 append되고 이후 table DML 권한을 주지 않는다.
CREATE TABLE rag_generation_attestations (
  corpus_generation_id text PRIMARY KEY
    REFERENCES rag_corpus_generations(corpus_generation_id) ON DELETE RESTRICT,
  corpus_hash text NOT NULL,
  generation_hash text NOT NULL,
  membership_hash text NOT NULL,
  aggregate_row_hash text NOT NULL,
  generation_vector_hash text NOT NULL,
  db_vector_hash text NOT NULL,
  source_revision_count integer NOT NULL,
  chunk_count integer NOT NULL,
  batch_size integer NOT NULL,
  model_revision text NOT NULL,
  model_file_manifest_hash text NOT NULL,
  tokenizer_sha256 text NOT NULL,
  parser_version text NOT NULL,
  chunker_version text NOT NULL,
  input_strategy_version text NOT NULL,
  batch_benchmark_sha256 text NOT NULL,
  environment_fingerprint_sha256 text NOT NULL,
  benchmark_report_sha256 text NOT NULL,
  warm_p95_ms numeric(12, 6) NOT NULL,
  approved_by_audit_ref text NOT NULL,
  attested_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_generation_attestations_hash_check
    CHECK (
      corpus_hash ~ '^[0-9a-f]{64}$'
      AND generation_hash ~ '^[0-9a-f]{64}$'
      AND membership_hash ~ '^[0-9a-f]{64}$'
      AND aggregate_row_hash ~ '^[0-9a-f]{64}$'
      AND generation_vector_hash ~ '^[0-9a-f]{64}$'
      AND db_vector_hash ~ '^[0-9a-f]{64}$'
      AND model_file_manifest_hash ~ '^[0-9a-f]{64}$'
      AND tokenizer_sha256 ~ '^[0-9a-f]{64}$'
      AND batch_benchmark_sha256 ~ '^[0-9a-f]{64}$'
      AND environment_fingerprint_sha256 ~ '^[0-9a-f]{64}$'
      AND benchmark_report_sha256 ~ '^[0-9a-f]{64}$'
    ),
  CONSTRAINT rag_generation_attestations_count_check
    CHECK (
      source_revision_count = 30
      AND chunk_count = 30
      AND batch_size BETWEEN 16 AND 64
    ),
  CONSTRAINT rag_generation_attestations_version_check
    CHECK (
      model_revision = '5617a9f61b028005a4858fdac845db406aefb181'
      AND parser_version = 'rag-source-card-v2-markdown-v1'
      AND chunker_version = 'bge-tokenizer-heading-400-600-v1'
      AND input_strategy_version = 'adjacent-7.5pct-per-side-no-reallocation-v1'
    ),
  CONSTRAINT rag_generation_attestations_sla_check
    CHECK (warm_p95_ms > 0 AND warm_p95_ms <= 1500.0),
  CONSTRAINT rag_generation_attestations_audit_check
    CHECK (char_length(approved_by_audit_ref) BETWEEN 16 AND 128)
);

-- active pointer가 없을 때도 version CAS가 명확하도록 승인된 BGE-only 초기 state를 한 번만 만든다.
INSERT INTO rag_embedding_policy_state (
  state_id,
  policy_id,
  effective_profile_id,
  active_generation_id,
  version,
  changed_at,
  changed_by_audit_ref
)
VALUES (
  'default',
  'bge_only_v1',
  'bge_m3_local_1024_v1',
  NULL,
  1,
  transaction_timestamp(),
  's4-2b-policy-bootstrap-v1'
)
ON CONFLICT (state_id) DO NOTHING;

-- writer는 materialization/evaluation까지만 진행하고 ACTIVE/DISABLED 전이는 admin 함수만 만든다.
CREATE FUNCTION guard_rag_writer_generation_terminal_transition()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $guard_rag_writer_generation_terminal_transition$
BEGIN
  IF session_user = 'decision_rag_writer'
     AND (
       NEW.status = 'ACTIVE'
       OR (OLD.status = 'ACTIVE' AND NEW.status = 'DISABLED')
     ) THEN
    RAISE EXCEPTION 'RAG writer cannot activate or supersede a generation'
      USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END
$guard_rag_writer_generation_terminal_transition$;
ALTER FUNCTION guard_rag_writer_generation_terminal_transition() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_writer_generation_terminal_transition() FROM PUBLIC;

CREATE TRIGGER a_rag_writer_generation_terminal_guard
BEFORE UPDATE OF status ON rag_corpus_generations
FOR EACH ROW
EXECUTE FUNCTION guard_rag_writer_generation_terminal_transition();

CREATE FUNCTION read_rag_activation_state()
RETURNS TABLE (
  active_generation_id text,
  policy_version bigint,
  policy_id text,
  effective_profile_id text
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $read_rag_activation_state$
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_rag_admin' THEN
    RAISE EXCEPTION 'RAG activation state requires the dedicated admin role'
      USING ERRCODE = '42501';
  END IF;
  RETURN QUERY
  SELECT
    state.active_generation_id,
    state.version,
    state.policy_id,
    state.effective_profile_id
  FROM public.rag_embedding_policy_state AS state
  WHERE state.state_id = 'default';
END
$read_rag_activation_state$;
ALTER FUNCTION read_rag_activation_state() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION read_rag_activation_state() FROM PUBLIC;

-- independent reader는 지정 generation의 exact bounded vector projection만 읽고 raw tables는 볼 수 없다.
CREATE FUNCTION read_rag_generation_embeddings_for_verification(
  p_generation_id text,
  p_expected_corpus_hash text,
  p_expected_row_count integer
)
RETURNS TABLE (
  chunk_revision_id text,
  embedding_text text,
  materialization_row_hash text
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $read_rag_generation_embeddings_for_verification$
DECLARE
  generation_row_count integer;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_admin'
     OR p_generation_id !~ '^rag_gen_[0-9a-f]{32}$'
     OR p_expected_corpus_hash !~ '^[0-9a-f]{64}$'
     OR p_expected_row_count NOT BETWEEN 1 AND 10000 THEN
    RAISE EXCEPTION 'RAG generation verification caller or argument is invalid'
      USING ERRCODE = '22023';
  END IF;

  SELECT generation.actual_chunk_count
  INTO generation_row_count
  FROM public.rag_corpus_generations AS generation
  WHERE generation.corpus_generation_id = p_generation_id
    AND generation.corpus_hash = p_expected_corpus_hash
    AND generation.embedding_profile_id = 'bge_m3_local_1024_v1'
    AND generation.status IN ('MATERIALIZED', 'EVAL_PASSED', 'ACTIVE')
    AND generation.actual_chunk_count = p_expected_row_count;
  IF NOT FOUND OR generation_row_count <> p_expected_row_count THEN
    RAISE EXCEPTION 'RAG generation verification target is not complete'
      USING ERRCODE = '23514';
  END IF;

  RETURN QUERY
  SELECT
    embedding.chunk_revision_id,
    embedding.embedding::text,
    embedding.materialization_row_hash
  FROM public.rag_chunk_embeddings AS embedding
  WHERE embedding.corpus_generation_id = p_generation_id
    AND embedding.embedding_profile_id = 'bge_m3_local_1024_v1'
    AND embedding.materialization_row_hash IS NOT NULL
  ORDER BY convert_to(embedding.chunk_revision_id, 'UTF8')
  LIMIT p_expected_row_count + 1;
END
$read_rag_generation_embeddings_for_verification$;
ALTER FUNCTION read_rag_generation_embeddings_for_verification(text, text, integer)
  OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION read_rag_generation_embeddings_for_verification(text, text, integer)
  FROM PUBLIC;

CREATE FUNCTION activate_verified_rag_generation(
  p_generation_id text,
  p_expected_current_generation_id text,
  p_expected_policy_version bigint,
  p_expected_corpus_hash text,
  p_generation_hash text,
  p_membership_hash text,
  p_aggregate_row_hash text,
  p_db_vector_hash text,
  p_expected_source_revision_count integer,
  p_expected_chunk_count integer,
  p_batch_size integer,
  p_model_revision text,
  p_model_file_manifest_hash text,
  p_tokenizer_sha256 text,
  p_parser_version text,
  p_chunker_version text,
  p_input_strategy_version text,
  p_batch_benchmark_sha256 text,
  p_environment_fingerprint_sha256 text,
  p_benchmark_report_sha256 text,
  p_warm_p95_ms numeric,
  p_approved_by_audit_ref text
)
RETURNS TABLE (
  previous_generation_id text,
  active_generation_id text,
  policy_version bigint,
  generation_status text
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $activate_verified_rag_generation$
DECLARE
  state_record record;
  generation_record record;
  source_revision_count bigint;
  membership_count bigint;
  embedding_count bigint;
  invalid_vector_count bigint;
  invalid_row_hash_count bigint;
  computed_membership_hash text;
  computed_aggregate_row_hash text;
  transition_id text;
  transition_trigger text;
  transition_time timestamptz := transaction_timestamp();
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_admin'
     OR p_generation_id !~ '^rag_gen_[0-9a-f]{32}$'
     OR (
       p_expected_current_generation_id IS NOT NULL
       AND p_expected_current_generation_id !~ '^rag_gen_[0-9a-f]{32}$'
     )
     OR p_expected_policy_version < 1
     OR p_expected_corpus_hash <> '7f2b4d72dcbaccf57cbe49a980973b17b4a9bfd85bec4694fd66fd7fd2a9decd'
     OR p_generation_hash !~ '^[0-9a-f]{64}$'
     OR p_generation_id <> 'rag_gen_' || substr(p_generation_hash, 1, 32)
     OR p_membership_hash !~ '^[0-9a-f]{64}$'
     OR p_aggregate_row_hash !~ '^[0-9a-f]{64}$'
     OR p_db_vector_hash !~ '^[0-9a-f]{64}$'
     OR p_expected_source_revision_count <> 30
     OR p_expected_chunk_count <> 30
     OR p_batch_size NOT BETWEEN 16 AND 64
     OR p_model_revision <> '5617a9f61b028005a4858fdac845db406aefb181'
     OR p_model_file_manifest_hash
        <> 'a0ae6372b2d735b593d806d24c1155cb48dd7188adebe7d6b7619a1622fb71aa'
     OR p_tokenizer_sha256
        <> '6710678b12670bc442b99edc952c4d996ae309a7020c1fa0096dd245c2faf790'
     OR p_parser_version <> 'rag-source-card-v2-markdown-v1'
     OR p_chunker_version <> 'bge-tokenizer-heading-400-600-v1'
     OR p_input_strategy_version <> 'adjacent-7.5pct-per-side-no-reallocation-v1'
     OR p_batch_benchmark_sha256 !~ '^[0-9a-f]{64}$'
     OR p_environment_fingerprint_sha256 !~ '^[0-9a-f]{64}$'
     OR p_benchmark_report_sha256 !~ '^[0-9a-f]{64}$'
     OR p_warm_p95_ms <= 0
     OR p_warm_p95_ms > 1500.0
     OR char_length(p_approved_by_audit_ref) NOT BETWEEN 16 AND 128 THEN
    RAISE EXCEPTION 'RAG verified activation argument is invalid'
      USING ERRCODE = '22023';
  END IF;

  SELECT state.*
  INTO state_record
  FROM public.rag_embedding_policy_state AS state
  WHERE state.state_id = 'default'
  FOR UPDATE;
  IF NOT FOUND
     OR state_record.version IS DISTINCT FROM p_expected_policy_version
     OR state_record.active_generation_id IS DISTINCT FROM p_expected_current_generation_id
     OR state_record.policy_id <> 'bge_only_v1'
     OR state_record.effective_profile_id <> 'bge_m3_local_1024_v1'
     OR state_record.active_generation_id IS NOT DISTINCT FROM p_generation_id THEN
    RAISE EXCEPTION 'RAG activation pointer CAS failed'
      USING ERRCODE = '40001';
  END IF;

  -- target과 이전 active row를 byte-order로 잠가 concurrent replacement의 deadlock surface를 줄인다.
  PERFORM generation.corpus_generation_id
  FROM public.rag_corpus_generations AS generation
  WHERE generation.corpus_generation_id IN (
    p_generation_id,
    coalesce(p_expected_current_generation_id, p_generation_id)
  )
  ORDER BY convert_to(generation.corpus_generation_id, 'UTF8')
  FOR UPDATE;

  SELECT generation.*
  INTO generation_record
  FROM public.rag_corpus_generations AS generation
  WHERE generation.corpus_generation_id = p_generation_id;
  IF NOT FOUND
     OR generation_record.status <> 'MATERIALIZED'
     OR generation_record.evaluation_status <> 'PENDING'
     OR generation_record.corpus_hash <> p_expected_corpus_hash
     OR generation_record.embedding_profile_id <> 'bge_m3_local_1024_v1'
     OR generation_record.vector_space <> 'bge_m3_local_1024_v1'
     OR generation_record.expected_chunk_count <> p_expected_chunk_count
     OR generation_record.actual_chunk_count <> p_expected_chunk_count THEN
    RAISE EXCEPTION 'RAG activation target generation is not materialized'
      USING ERRCODE = '23514';
  END IF;

  IF p_expected_current_generation_id IS NOT NULL
     AND NOT EXISTS (
       SELECT 1
       FROM public.rag_corpus_generations AS previous
       WHERE previous.corpus_generation_id = p_expected_current_generation_id
         AND previous.status = 'ACTIVE'
         AND previous.embedding_profile_id = 'bge_m3_local_1024_v1'
     ) THEN
    RAISE EXCEPTION 'RAG activation previous generation is not active'
      USING ERRCODE = '23514';
  END IF;

  SELECT
    count(DISTINCT chunk.source_revision_id),
    count(*),
    count(embedding.chunk_embedding_id),
    count(*) FILTER (
      WHERE embedding.chunk_embedding_id IS NULL
         OR vector_dims(embedding.embedding) <> 1024
         OR vector_norm(embedding.embedding)::text IN ('NaN', 'Infinity', '-Infinity')
         OR abs(vector_norm(embedding.embedding)::double precision - 1.0) > 0.00001
    ),
    count(*) FILTER (
      WHERE embedding.materialization_row_hash IS NULL
         OR embedding.materialization_row_hash !~ '^[0-9a-f]{64}$'
    ),
    encode(
      digest(
        string_agg(
          encode(
            digest(
              concat_ws(
                E'\n',
                source.source_id,
                chunk.source_revision_id,
                membership.chunk_revision_id,
                chunk.canonical_content_hash,
                membership.embedding_input_hash,
                membership.ordinal::text
              ),
              'sha256'
            ),
            'hex'
          ),
          ''
          ORDER BY membership.ordinal
        ),
        'sha256'
      ),
      'hex'
    ),
    encode(
      digest(
        string_agg(
          embedding.materialization_row_hash,
          ''
          ORDER BY convert_to(membership.chunk_revision_id, 'UTF8')
        ),
        'sha256'
      ),
      'hex'
    )
  INTO
    source_revision_count,
    membership_count,
    embedding_count,
    invalid_vector_count,
    invalid_row_hash_count,
    computed_membership_hash,
    computed_aggregate_row_hash
  FROM public.rag_generation_chunks AS membership
  JOIN public.rag_chunk_revisions AS chunk
    ON chunk.chunk_revision_id = membership.chunk_revision_id
  JOIN public.rag_source_revisions AS revision
    ON revision.source_revision_id = chunk.source_revision_id
   AND revision.tier = 'PROJECT'
   AND revision.access_level = 'PUBLIC'
   AND NOT revision.external_processing_allowed
  JOIN public.rag_sources AS source
    ON source.source_id = revision.source_id
   AND source.source_type = 'PROJECT_SOURCE_CARD'
   AND source.retired_at IS NULL
  LEFT JOIN public.rag_chunk_embeddings AS embedding
    ON embedding.corpus_generation_id = membership.corpus_generation_id
   AND embedding.chunk_revision_id = membership.chunk_revision_id
   AND embedding.embedding_profile_id = membership.embedding_profile_id
   AND embedding.embedding_input_hash = membership.embedding_input_hash
   AND embedding.context_set_hash IS NOT DISTINCT FROM membership.context_set_hash
  WHERE membership.corpus_generation_id = p_generation_id
    AND membership.embedding_profile_id = 'bge_m3_local_1024_v1';

  IF source_revision_count <> p_expected_source_revision_count
     OR membership_count <> p_expected_chunk_count
     OR embedding_count <> p_expected_chunk_count
     OR invalid_vector_count <> 0
     OR invalid_row_hash_count <> 0
     OR computed_membership_hash IS DISTINCT FROM p_membership_hash
     OR computed_aggregate_row_hash IS DISTINCT FROM p_aggregate_row_hash THEN
    RAISE EXCEPTION 'RAG activation graph attestation failed'
      USING ERRCODE = '23514';
  END IF;

  INSERT INTO public.rag_generation_attestations (
    corpus_generation_id,
    corpus_hash,
    generation_hash,
    membership_hash,
    aggregate_row_hash,
    generation_vector_hash,
    db_vector_hash,
    source_revision_count,
    chunk_count,
    batch_size,
    model_revision,
    model_file_manifest_hash,
    tokenizer_sha256,
    parser_version,
    chunker_version,
    input_strategy_version,
    batch_benchmark_sha256,
    environment_fingerprint_sha256,
    benchmark_report_sha256,
    warm_p95_ms,
    approved_by_audit_ref
  )
  VALUES (
    p_generation_id,
    p_expected_corpus_hash,
    p_generation_hash,
    p_membership_hash,
    p_aggregate_row_hash,
    p_db_vector_hash,
    p_db_vector_hash,
    p_expected_source_revision_count,
    p_expected_chunk_count,
    p_batch_size,
    p_model_revision,
    p_model_file_manifest_hash,
    p_tokenizer_sha256,
    p_parser_version,
    p_chunker_version,
    p_input_strategy_version,
    p_batch_benchmark_sha256,
    p_environment_fingerprint_sha256,
    p_benchmark_report_sha256,
    p_warm_p95_ms,
    p_approved_by_audit_ref
  );

  UPDATE public.rag_corpus_generations AS generation
  SET
    status = 'EVAL_PASSED',
    evaluation_status = 'PASSED',
    evaluated_at = transition_time
  WHERE generation.corpus_generation_id = p_generation_id
    AND generation.status = 'MATERIALIZED';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'RAG generation verification transition failed'
      USING ERRCODE = '23514';
  END IF;

  IF p_expected_current_generation_id IS NOT NULL THEN
    UPDATE public.rag_corpus_generations AS previous
    SET
      status = 'DISABLED',
      disabled_at = transition_time
    WHERE previous.corpus_generation_id = p_expected_current_generation_id
      AND previous.status = 'ACTIVE';
    IF NOT FOUND THEN
      RAISE EXCEPTION 'RAG previous generation supersede transition failed'
        USING ERRCODE = '23514';
    END IF;
  END IF;

  UPDATE public.rag_corpus_generations AS generation
  SET
    status = 'ACTIVE',
    activated_at = transition_time
  WHERE generation.corpus_generation_id = p_generation_id
    AND generation.status = 'EVAL_PASSED';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'RAG generation activation transition failed'
      USING ERRCODE = '23514';
  END IF;

  UPDATE public.rag_embedding_policy_state AS state
  SET
    policy_id = 'bge_only_v1',
    effective_profile_id = 'bge_m3_local_1024_v1',
    active_generation_id = p_generation_id,
    version = state.version + 1,
    changed_at = transition_time,
    changed_by_audit_ref = p_approved_by_audit_ref
  WHERE state.state_id = 'default'
    AND state.version = p_expected_policy_version
    AND state.active_generation_id IS NOT DISTINCT FROM p_expected_current_generation_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'RAG activation pointer update lost CAS'
      USING ERRCODE = '40001';
  END IF;

  transition_trigger := CASE
    WHEN p_expected_current_generation_id IS NULL THEN 'INITIAL_ADMIN_SELECTION'
    ELSE 'ADMIN_ROLLBACK'
  END;
  transition_id :=
    'rag_pol_' ||
    substr(
      encode(
        digest(
          concat_ws(
            E'\n',
            'default',
            p_expected_policy_version::text,
            (p_expected_policy_version + 1)::text,
            coalesce(p_expected_current_generation_id, ''),
            p_generation_id,
            p_approved_by_audit_ref
          ),
          'sha256'
        ),
        'hex'
      ),
      1,
      32
    );
  INSERT INTO public.rag_embedding_policy_transitions (
    transition_id,
    state_id,
    from_version,
    to_version,
    from_policy_id,
    to_policy_id,
    from_profile_id,
    to_profile_id,
    target_generation_id,
    trigger_code,
    approved_by_audit_ref,
    approved_at
  )
  VALUES (
    transition_id,
    'default',
    p_expected_policy_version,
    p_expected_policy_version + 1,
    'bge_only_v1',
    'bge_only_v1',
    'bge_m3_local_1024_v1',
    'bge_m3_local_1024_v1',
    p_generation_id,
    transition_trigger,
    p_approved_by_audit_ref,
    transition_time
  );

  RETURN QUERY
  SELECT
    p_expected_current_generation_id,
    p_generation_id,
    p_expected_policy_version + 1,
    'ACTIVE'::text;
END
$activate_verified_rag_generation$;
ALTER FUNCTION activate_verified_rag_generation(
  text, text, bigint, text, text, text, text, text,
  integer, integer, integer,
  text, text, text, text, text, text, text, text, text,
  numeric, text
) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION activate_verified_rag_generation(
  text, text, bigint, text, text, text, text, text,
  integer, integer, integer,
  text, text, text, text, text, text, text, text, text,
  numeric, text
) FROM PUBLIC;

REVOKE ALL PRIVILEGES ON TABLE rag_generation_attestations FROM PUBLIC;

DO $s4_2b_full_generation_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    REVOKE ALL PRIVILEGES ON TABLE rag_generation_attestations FROM decision_rag_writer;
    GRANT EXECUTE
      ON FUNCTION finalize_rag_embedding_staging_v2(text, text, text, integer, text)
      TO decision_rag_writer;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_admin') THEN
    REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_rag_admin;
    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_rag_admin;
    REVOKE CREATE ON SCHEMA public FROM decision_rag_admin;
    GRANT EXECUTE ON FUNCTION read_rag_activation_state() TO decision_rag_admin;
    GRANT EXECUTE
      ON FUNCTION read_rag_generation_embeddings_for_verification(text, text, integer)
      TO decision_rag_admin;
    GRANT EXECUTE ON FUNCTION activate_verified_rag_generation(
      text, text, bigint, text, text, text, text, text,
      integer, integer, integer,
      text, text, text, text, text, text, text, text, text,
      numeric, text
    ) TO decision_rag_admin;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    REVOKE ALL PRIVILEGES ON TABLE rag_generation_attestations FROM decision_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_query') THEN
    REVOKE ALL PRIVILEGES ON TABLE rag_generation_attestations FROM decision_rag_query;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_worker') THEN
    REVOKE ALL PRIVILEGES ON TABLE rag_generation_attestations FROM decision_worker;
  END IF;
END
$s4_2b_full_generation_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_writer_generation_terminal_transition() FROM PUBLIC;
REVOKE ALL PRIVILEGES
  ON FUNCTION finalize_rag_embedding_staging_v2(text, text, text, integer, text)
  FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION read_rag_activation_state() FROM PUBLIC;
REVOKE ALL PRIVILEGES
  ON FUNCTION read_rag_generation_embeddings_for_verification(text, text, integer)
  FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION activate_verified_rag_generation(
  text, text, bigint, text, text, text, text, text,
  integer, integer, integer,
  text, text, text, text, text, text, text, text, text,
  numeric, text
) FROM PUBLIC;
GRANT EXECUTE
  ON FUNCTION finalize_rag_embedding_staging_v2(text, text, text, integer, text)
  TO decision_rag_writer;
GRANT EXECUTE ON FUNCTION read_rag_activation_state() TO decision_rag_admin;
GRANT EXECUTE
  ON FUNCTION read_rag_generation_embeddings_for_verification(text, text, integer)
  TO decision_rag_admin;
GRANT EXECUTE ON FUNCTION activate_verified_rag_generation(
  text, text, bigint, text, text, text, text, text,
  integer, integer, integer,
  text, text, text, text, text, text, text, text, text,
  numeric, text
) TO decision_rag_admin;
