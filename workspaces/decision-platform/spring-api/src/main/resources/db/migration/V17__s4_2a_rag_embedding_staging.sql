-- S4.2A는 RLS table에 COPY를 우회하지 않고 narrow staging + bounded finalizer를 사용한다.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 동일 canonical chunk가 서로 다른 immutable generation에 포함될 수 있도록 generation을 identity에 포함한다.
ALTER TABLE rag_chunk_embeddings
  DROP CONSTRAINT rag_chunk_embeddings_identity_unique;
ALTER TABLE rag_chunk_embeddings
  ADD CONSTRAINT rag_chunk_embeddings_identity_unique
  UNIQUE NULLS NOT DISTINCT (
    corpus_generation_id,
    chunk_revision_id,
    embedding_profile_id,
    embedding_input_hash,
    context_set_hash
  );

CREATE TABLE rag_embedding_staging (
  generation_id text NOT NULL,
  materialization_run_id text NOT NULL,
  chunk_revision_id text NOT NULL,
  embedding_profile_id text NOT NULL,
  embedding_input_hash text NOT NULL,
  context_set_hash text,
  embedding vector(1024) NOT NULL,
  staging_row_hash text NOT NULL,
  writer_role name NOT NULL DEFAULT current_user,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_embedding_staging_generation_membership_fkey
    FOREIGN KEY (generation_id, chunk_revision_id, embedding_profile_id)
    REFERENCES rag_generation_chunks (
      corpus_generation_id,
      chunk_revision_id,
      embedding_profile_id
    )
    ON DELETE RESTRICT,
  CONSTRAINT rag_embedding_staging_generation_id_check
    CHECK (generation_id ~ '^rag_gen_[0-9a-f]{32}$'),
  CONSTRAINT rag_embedding_staging_run_id_check
    CHECK (octet_length(materialization_run_id) = 40
      AND materialization_run_id ~ '^rag_mat_[0-9a-f]{32}$'
    ),
  CONSTRAINT rag_embedding_staging_profile_check
    CHECK (embedding_profile_id IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1')),
  CONSTRAINT rag_embedding_staging_hash_check
    CHECK (
      embedding_input_hash ~ '^[0-9a-f]{64}$'
      AND staging_row_hash ~ '^[0-9a-f]{64}$'
      AND (context_set_hash IS NULL OR context_set_hash ~ '^[0-9a-f]{64}$')
    ),
  CONSTRAINT rag_embedding_staging_context_policy_check
    CHECK (
      (embedding_profile_id = 'bge_m3_local_1024_v1' AND context_set_hash IS NULL)
      OR
      (embedding_profile_id = 'voyage_context_4_1024_v1' AND context_set_hash IS NOT NULL)
    ),
  CONSTRAINT rag_embedding_staging_writer_check
    CHECK (writer_role = 'decision_rag_writer'::name),
  CONSTRAINT rag_embedding_staging_dimension_check
    CHECK (vector_dims(embedding) = 1024),
  CONSTRAINT rag_embedding_staging_finite_check
    CHECK (
      vector_norm(embedding)::text NOT IN ('NaN', 'Infinity', '-Infinity')
    ),
  CONSTRAINT rag_embedding_staging_normalized_check
    CHECK (abs(vector_norm(embedding)::double precision - 1.0) <= 0.00001),
  CONSTRAINT rag_embedding_staging_pkey
    PRIMARY KEY (generation_id, materialization_run_id, chunk_revision_id)
);
CREATE INDEX rag_embedding_staging_run_idx
  ON rag_embedding_staging (generation_id, materialization_run_id, writer_role);

-- COPY row마다 caller와 immutable generation membership을 고정해 다른 run/profile 주입을 조기에 막는다.
CREATE FUNCTION guard_rag_embedding_staging()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $guard_rag_embedding_staging$
DECLARE
  generation_status text;
  generation_profile text;
BEGIN
  IF session_user <> 'decision_rag_writer' THEN
    RAISE EXCEPTION 'RAG embedding staging requires the dedicated writer role'
      USING ERRCODE = '42501';
  END IF;
  NEW.writer_role := session_user;

  SELECT generation.status, generation.embedding_profile_id
  INTO generation_status, generation_profile
  FROM public.rag_corpus_generations AS generation
  WHERE generation.corpus_generation_id = NEW.generation_id
  FOR UPDATE;
  IF NOT FOUND
     OR generation_status <> 'MATERIALIZING'
     OR generation_profile IS DISTINCT FROM NEW.embedding_profile_id THEN
    RAISE EXCEPTION 'RAG embedding staging generation is not open'
      USING ERRCODE = '23514';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.rag_generation_chunks AS membership
    WHERE membership.corpus_generation_id = NEW.generation_id
      AND membership.chunk_revision_id = NEW.chunk_revision_id
      AND membership.embedding_profile_id = NEW.embedding_profile_id
      AND membership.embedding_input_hash = NEW.embedding_input_hash
      AND membership.context_set_hash IS NOT DISTINCT FROM NEW.context_set_hash
  ) THEN
    RAISE EXCEPTION 'RAG embedding staging membership or input hash drifted'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END
$guard_rag_embedding_staging$;
ALTER FUNCTION guard_rag_embedding_staging() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_embedding_staging() FROM PUBLIC;

CREATE TRIGGER rag_embedding_staging_guard
BEFORE INSERT ON rag_embedding_staging
FOR EACH ROW
EXECUTE FUNCTION guard_rag_embedding_staging();

CREATE FUNCTION finalize_rag_embedding_staging(
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
AS $finalize_rag_embedding_staging$
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
  -- SECURITY DEFINER의 current_user는 flyway이고 실제 caller는 session_user로 별도 검증한다.
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_expected_writer_role <> session_user
     OR p_generation_id !~ '^rag_gen_[0-9a-f]{32}$'
     OR octet_length(p_materialization_run_id) <> 40
     OR p_materialization_run_id !~ '^rag_mat_[0-9a-f]{32}$'
     OR p_expected_row_count NOT BETWEEN 1 AND 10000
     OR p_expected_staging_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'RAG embedding staging finalize caller or argument is invalid'
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
     OR generation_expected_row_count <> p_expected_row_count THEN
    RAISE EXCEPTION 'RAG embedding staging finalize generation is not bounded'
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
    RAISE EXCEPTION 'RAG embedding staging finalize validation failed'
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
    embedding
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
    staging.embedding
  FROM public.rag_embedding_staging AS staging
  WHERE staging.generation_id = p_generation_id
    AND staging.materialization_run_id = p_materialization_run_id
    AND staging.writer_role::text = p_expected_writer_role
  ORDER BY convert_to(staging.chunk_revision_id, 'UTF8')
  ON CONFLICT ON CONSTRAINT rag_chunk_embeddings_identity_unique DO NOTHING;
  GET DIAGNOSTICS inserted_row_count = ROW_COUNT;
  IF inserted_row_count <> p_expected_row_count THEN
    RAISE EXCEPTION 'RAG embedding staging finalize encountered an existing final identity'
      USING ERRCODE = '23505';
  END IF;

  UPDATE public.rag_corpus_generations AS generation
  SET actual_chunk_count = p_expected_row_count
  WHERE generation.corpus_generation_id = p_generation_id
    AND generation.status = 'MATERIALIZING'
    AND generation.actual_chunk_count = 0;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'RAG embedding staging finalize generation counter drifted'
      USING ERRCODE = '23514';
  END IF;

  DELETE FROM public.rag_embedding_staging AS staging
  WHERE staging.generation_id = p_generation_id
    AND staging.materialization_run_id = p_materialization_run_id
    AND staging.writer_role::text = p_expected_writer_role;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'RAG embedding staging finalize cleanup failed'
      USING ERRCODE = '23514';
  END IF;
  RETURN inserted_row_count;
END
$finalize_rag_embedding_staging$;
ALTER FUNCTION finalize_rag_embedding_staging(text, text, text, integer, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION finalize_rag_embedding_staging(text, text, text, integer, text) FROM PUBLIC;

CREATE FUNCTION purge_rag_embedding_staging(
  p_generation_id text,
  p_materialization_run_id text
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $purge_rag_embedding_staging$
DECLARE
  deleted_row_count integer;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_generation_id !~ '^rag_gen_[0-9a-f]{32}$'
     OR octet_length(p_materialization_run_id) <> 40
     OR p_materialization_run_id !~ '^rag_mat_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'RAG embedding staging purge caller or argument is invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM 1
  FROM public.rag_corpus_generations AS generation
  WHERE generation.corpus_generation_id = p_generation_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'RAG embedding staging purge generation is missing'
      USING ERRCODE = '23503';
  END IF;

  DELETE FROM public.rag_embedding_staging AS staging
  WHERE staging.generation_id = p_generation_id
    AND staging.materialization_run_id = p_materialization_run_id
    AND staging.writer_role = session_user::name;
  GET DIAGNOSTICS deleted_row_count = ROW_COUNT;
  RETURN deleted_row_count;
END
$purge_rag_embedding_staging$;
ALTER FUNCTION purge_rag_embedding_staging(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION purge_rag_embedding_staging(text, text) FROM PUBLIC;

-- V16의 activation join에 generation identity를 추가해 다른 generation의 vector 재사용을 금지한다.
CREATE OR REPLACE FUNCTION guard_rag_generation_activation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $guard_rag_generation_activation$
DECLARE
  membership_count bigint;
  embedding_count bigint;
  embedded_chunk_count bigint;
BEGIN
  IF NEW.status IN ('MATERIALIZED', 'ACTIVE') THEN
    IF TG_OP = 'INSERT' THEN
      RAISE EXCEPTION 'RAG generation must be materialized before activation'
        USING ERRCODE = '23514';
    END IF;
    IF OLD.status IS NOT DISTINCT FROM NEW.status THEN
      RETURN NEW;
    END IF;

    SELECT
      count(*),
      count(embedding.chunk_embedding_id),
      count(DISTINCT embedding.chunk_revision_id)
    INTO membership_count, embedding_count, embedded_chunk_count
    FROM public.rag_generation_chunks AS membership
    LEFT JOIN public.rag_chunk_embeddings AS embedding
      ON embedding.corpus_generation_id = membership.corpus_generation_id
     AND embedding.chunk_revision_id = membership.chunk_revision_id
     AND embedding.embedding_profile_id = membership.embedding_profile_id
     AND embedding.embedding_input_hash = membership.embedding_input_hash
     AND embedding.context_set_hash IS NOT DISTINCT FROM membership.context_set_hash
    WHERE membership.corpus_generation_id = NEW.corpus_generation_id
      AND membership.embedding_profile_id = NEW.embedding_profile_id;

    IF NEW.actual_chunk_count <> NEW.expected_chunk_count
       OR membership_count <> NEW.expected_chunk_count
       OR embedding_count <> NEW.expected_chunk_count
       OR embedded_chunk_count <> NEW.expected_chunk_count THEN
      RAISE EXCEPTION 'RAG generation materialization graph is incomplete'
        USING ERRCODE = '23514';
    END IF;
    IF NEW.status = 'ACTIVE'
       AND (
         NEW.evaluation_status <> 'PASSED'
         OR NEW.evaluated_at IS NULL
         OR NEW.activated_at IS NULL
       ) THEN
      RAISE EXCEPTION 'RAG generation activation gate is incomplete'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END
$guard_rag_generation_activation$;
ALTER FUNCTION guard_rag_generation_activation() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_generation_activation() FROM PUBLIC;

REVOKE ALL PRIVILEGES ON TABLE rag_embedding_staging FROM PUBLIC;

DO $s4_2a_embedding_staging_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE rag_chunk_embeddings FROM decision_rag_writer;
    REVOKE SELECT ON TABLE rag_chunk_embeddings FROM decision_rag_writer;
    REVOKE UPDATE (activated_at) ON TABLE rag_corpus_generations FROM decision_rag_writer;
    REVOKE ALL PRIVILEGES ON TABLE rag_embedding_staging FROM decision_rag_writer;
    GRANT INSERT, SELECT ON TABLE rag_embedding_staging TO decision_rag_writer;
    GRANT EXECUTE
      ON FUNCTION finalize_rag_embedding_staging(text, text, text, integer, text)
      TO decision_rag_writer;
    GRANT EXECUTE
      ON FUNCTION purge_rag_embedding_staging(text, text)
      TO decision_rag_writer;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    REVOKE ALL PRIVILEGES ON TABLE rag_embedding_staging FROM decision_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_query') THEN
    REVOKE ALL PRIVILEGES ON TABLE rag_embedding_staging FROM decision_rag_query;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_worker') THEN
    REVOKE ALL PRIVILEGES ON TABLE rag_embedding_staging FROM decision_worker;
  END IF;
END
$s4_2a_embedding_staging_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_embedding_staging() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_generation_activation() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION finalize_rag_embedding_staging(text, text, text, integer, text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION purge_rag_embedding_staging(text, text) FROM PUBLIC;
GRANT EXECUTE
  ON FUNCTION finalize_rag_embedding_staging(text, text, text, integer, text)
  TO decision_rag_writer;
GRANT EXECUTE ON FUNCTION purge_rag_embedding_staging(text, text) TO decision_rag_writer;
