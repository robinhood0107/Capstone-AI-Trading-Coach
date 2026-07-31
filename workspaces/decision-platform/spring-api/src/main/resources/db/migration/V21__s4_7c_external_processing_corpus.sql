-- S4.7C는 기존 S4.7B bytes/generation을 수정하지 않고, 승인된 project-authored
-- sanitized card 30개의 새 revision과 generation만 append한다. 외부 provider 호출 권한은
-- 이 migration이 만들지 않으며 exact corpus/registry/approval marker 조합만 허용한다.

-- 같은 logical card의 immutable revision 이력을 허용하되 조회는 항상 generation membership의
-- source_revision_id로 결합한다. card_id 단독 최신값 추론은 허용하지 않는다.
ALTER TABLE rag_source_card_verifications
  DROP CONSTRAINT rag_source_card_verifications_card_id_key;
CREATE INDEX rag_source_card_verifications_card_revision_idx
  ON rag_source_card_verifications (card_id, verified_at DESC, source_revision_id);

CREATE OR REPLACE FUNCTION guard_rag_source_revision_locator()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $guard_rag_source_revision_locator$
DECLARE
  original_locator record;
  latest_revision_seq integer;
  parent_source_type text;
  parent_retired_at timestamptz;
BEGIN
  -- source identity row lock이 같은 source의 revision append를 직렬화해 sequence gap을 막는다.
  SELECT source.source_type, source.retired_at
  INTO parent_source_type, parent_retired_at
  FROM public.rag_sources AS source
  WHERE source.source_id = NEW.source_id
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'RAG source identity must exist before its revision'
      USING ERRCODE = '23503';
  END IF;
  IF parent_retired_at IS NOT NULL THEN
    RAISE EXCEPTION 'RAG source revision cannot append to a retired source'
      USING ERRCODE = '23514';
  END IF;
  IF parent_source_type = 'UPSTREAM_REFERENCE'
     AND (
       NEW.tier <> 'OFFICIAL'
       OR NEW.access_level <> 'PUBLIC'
       OR NEW.license_decision NOT LIKE 'REFERENCE_ONLY_%'
       OR NEW.retention_mode <> 'REFERENCE_METADATA_ONLY'
       OR NEW.external_processing_allowed
       OR NEW.initial_processing <> 'REFERENCE_ONLY'
     ) THEN
    RAISE EXCEPTION 'RAG upstream revision metadata crossed the reference-only boundary'
      USING ERRCODE = '23514';
  END IF;
  IF parent_source_type = 'PROJECT_SOURCE_CARD'
     AND (
       NEW.tier <> 'PROJECT'
       OR NEW.license_decision
          <> CASE
               WHEN NEW.access_level = 'PUBLIC' THEN 'PROJECT_AUTHORED_PUBLIC'
               ELSE 'PROJECT_AUTHORED_INTERNAL'
             END
       OR NEW.retention_mode <> 'PROJECT_CARD'
       OR (
         NEW.external_processing_allowed
         AND (
           NEW.registry_version <> 's4-7c-source-card-v2'
           OR NEW.access_level <> 'PUBLIC'
           OR position(
             'approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731' IN NEW.license_note
           ) = 0
         )
       )
       OR NEW.initial_processing <> 'PROJECT_AUTHORED_CARD'
     ) THEN
    RAISE EXCEPTION 'RAG project-card revision metadata crossed the authored-card boundary'
      USING ERRCODE = '23514';
  END IF;

  -- INSERT ... ON CONFLICT DO NOTHING 재실행은 exact immutable row일 때만 idempotent하게 통과시킨다.
  IF EXISTS (
    SELECT 1
    FROM public.rag_source_revisions AS existing
    WHERE existing.source_revision_id = NEW.source_revision_id
      AND existing.source_id = NEW.source_id
      AND existing.revision_seq = NEW.revision_seq
      AND existing.metadata_hash = NEW.metadata_hash
      AND existing.locator_sha256 = NEW.locator_sha256
  ) THEN
    RETURN NEW;
  END IF;

  SELECT
    revision.canonical_url,
    revision.allowed_origin,
    revision.allowed_path,
    revision.locator_sha256
  INTO original_locator
  FROM public.rag_source_revisions AS revision
  WHERE revision.source_id = NEW.source_id
  ORDER BY revision.revision_seq
  LIMIT 1
  FOR SHARE;

  IF NOT FOUND THEN
    IF NEW.revision_seq <> 1 THEN
      RAISE EXCEPTION 'RAG source revisions must start at sequence 1'
        USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
  END IF;

  SELECT max(revision.revision_seq)
  INTO latest_revision_seq
  FROM public.rag_source_revisions AS revision
  WHERE revision.source_id = NEW.source_id;

  IF NEW.revision_seq <> latest_revision_seq + 1 THEN
    RAISE EXCEPTION 'RAG source revisions must append the next sequence'
      USING ERRCODE = '23514';
  END IF;

  IF original_locator.canonical_url IS DISTINCT FROM NEW.canonical_url
     OR original_locator.allowed_origin IS DISTINCT FROM NEW.allowed_origin
     OR original_locator.allowed_path IS DISTINCT FROM NEW.allowed_path
     OR original_locator.locator_sha256 IS DISTINCT FROM NEW.locator_sha256 THEN
    RAISE EXCEPTION 'RAG source locator movement requires a new source sequence ID'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END
$guard_rag_source_revision_locator$;
ALTER FUNCTION guard_rag_source_revision_locator() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_source_revision_locator() FROM PUBLIC;

CREATE OR REPLACE FUNCTION register_rag_verified_source_card(
  p_source_revision_id text,
  p_source_id text,
  p_card_id text,
  p_card_metadata_hash text,
  p_verified_at timestamptz,
  p_public_topics text[]
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $register_rag_verified_source_card$
DECLARE
  inserted_count integer;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_source_revision_id !~ '^src_rev_[0-9a-f]{32}$'
     OR p_source_id !~ '^src_project_[a-z0-9_]+_[0-9]{3}$'
     OR p_card_id !~ '^card_[a-z0-9][a-z0-9_]{1,127}_[0-9]{3}$'
     OR p_card_id IS DISTINCT FROM regexp_replace(p_source_id, '^src_project_', 'card_')
     OR p_card_metadata_hash !~ '^[0-9a-f]{64}$'
     OR p_verified_at IS NULL
     OR p_verified_at > transaction_timestamp() + interval '1 minute'
     OR p_public_topics IS NULL
     OR cardinality(p_public_topics) NOT BETWEEN 1 AND 5
     OR cardinality(ARRAY(SELECT DISTINCT unnest(p_public_topics)))
        <> cardinality(p_public_topics)
     OR NOT (
       p_public_topics <@ ARRAY[
         'API',
         'DATA',
         'FINANCIAL_ENGINEERING',
         'METHODOLOGY',
         'PRODUCT_RISK',
         'RISK'
       ]::text[]
     )
     OR NOT EXISTS (
       SELECT 1
       FROM public.rag_source_revisions AS revision
       JOIN public.rag_sources AS source
         ON source.source_id = revision.source_id
        AND source.source_type = 'PROJECT_SOURCE_CARD'
        AND source.retired_at IS NULL
       WHERE revision.source_revision_id = p_source_revision_id
         AND revision.source_id = p_source_id
         AND revision.tier = 'PROJECT'
         AND revision.access_level = 'PUBLIC'
         AND revision.initial_processing = 'PROJECT_AUTHORED_CARD'
         AND (
           (
             revision.registry_version = 's4-7b-source-card-v2'
             AND NOT revision.external_processing_allowed
           )
           OR
           (
             revision.registry_version = 's4-7c-source-card-v2'
             AND revision.external_processing_allowed
             AND position(
               'approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731' IN revision.license_note
             ) > 0
           )
         )
         AND revision.metadata_hash = p_card_metadata_hash
     ) THEN
    RAISE EXCEPTION 'RAG verified source-card registration is invalid'
      USING ERRCODE = '22023';
  END IF;

  INSERT INTO public.rag_source_card_verifications (
    source_revision_id,
    card_id,
    status,
    card_metadata_hash,
    verified_at
  )
  VALUES (
    p_source_revision_id,
    p_card_id,
    'VERIFIED',
    p_card_metadata_hash,
    p_verified_at
  )
  ON CONFLICT (source_revision_id) DO NOTHING;
  GET DIAGNOSTICS inserted_count = ROW_COUNT;

  IF inserted_count = 0
     AND NOT EXISTS (
       SELECT 1
       FROM public.rag_source_card_verifications AS verification
       WHERE verification.source_revision_id = p_source_revision_id
         AND verification.card_id = p_card_id
         AND verification.status = 'VERIFIED'
         AND verification.card_metadata_hash = p_card_metadata_hash
         AND verification.verified_at = p_verified_at
     ) THEN
    RAISE EXCEPTION 'RAG verified source-card identity drifted'
      USING ERRCODE = '23514';
  END IF;

  INSERT INTO public.rag_source_public_topics (source_id, public_topic)
  SELECT p_source_id, topic
  FROM unnest(p_public_topics) AS topic
  ORDER BY convert_to(topic, 'UTF8')
  ON CONFLICT DO NOTHING;

  IF (
    SELECT array_agg(topic.public_topic ORDER BY convert_to(topic.public_topic, 'UTF8'))
    FROM public.rag_source_public_topics AS topic
    WHERE topic.source_id = p_source_id
  ) IS DISTINCT FROM (
    SELECT array_agg(topic ORDER BY convert_to(topic, 'UTF8'))
    FROM unnest(p_public_topics) AS topic
  ) THEN
    RAISE EXCEPTION 'RAG verified source-card public topic identity drifted'
      USING ERRCODE = '23514';
  END IF;

  INSERT INTO public.rag_source_exact_identifiers (
    source_id,
    identifier,
    identifier_kind
  )
  SELECT approved.source_id, approved.identifier, approved.identifier_kind
  FROM (
    VALUES
      ('src_project_kis_current_price_snapshot_001', 'FHKST01010100', 'KIS_TR_ID'),
      ('src_project_kis_adjusted_price_001', 'FHKST03010100', 'KIS_TR_ID'),
      ('src_project_gold_futures_etf_132030_001', '132030', 'SYMBOL')
  ) AS approved(source_id, identifier, identifier_kind)
  WHERE approved.source_id = p_source_id
  ON CONFLICT DO NOTHING;

  IF EXISTS (
    SELECT 1
    FROM (
      VALUES
        ('src_project_kis_current_price_snapshot_001', 'FHKST01010100', 'KIS_TR_ID'),
        ('src_project_kis_adjusted_price_001', 'FHKST03010100', 'KIS_TR_ID'),
        ('src_project_gold_futures_etf_132030_001', '132030', 'SYMBOL')
    ) AS approved(source_id, identifier, identifier_kind)
    WHERE approved.source_id = p_source_id
      AND NOT EXISTS (
        SELECT 1
        FROM public.rag_source_exact_identifiers AS exact_identifier
        WHERE exact_identifier.source_id = approved.source_id
          AND exact_identifier.identifier = approved.identifier
          AND exact_identifier.identifier_kind = approved.identifier_kind
      )
  ) THEN
    RAISE EXCEPTION 'RAG exact identifier identity drifted'
      USING ERRCODE = '23514';
  END IF;
  RETURN inserted_count;
END
$register_rag_verified_source_card$;
ALTER FUNCTION register_rag_verified_source_card(text, text, text, text, timestamptz, text[])
  OWNER TO flyway;
REVOKE ALL PRIVILEGES
  ON FUNCTION register_rag_verified_source_card(text, text, text, text, timestamptz, text[])
  FROM PUBLIC;

CREATE OR REPLACE FUNCTION activate_verified_rag_generation(
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
     OR p_expected_corpus_hash NOT IN (
       '7f2b4d72dcbaccf57cbe49a980973b17b4a9bfd85bec4694fd66fd7fd2a9decd',
       'bdc42bfb735b411156ec2f79626d6fd2cf56662c57d83e2cdb960fb74e7b0e04'
     )
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
   AND (
     (
       p_expected_corpus_hash = '7f2b4d72dcbaccf57cbe49a980973b17b4a9bfd85bec4694fd66fd7fd2a9decd'
       AND revision.registry_version = 's4-7b-source-card-v2'
       AND NOT revision.external_processing_allowed
     )
     OR
     (
       p_expected_corpus_hash = 'bdc42bfb735b411156ec2f79626d6fd2cf56662c57d83e2cdb960fb74e7b0e04'
       AND revision.registry_version = 's4-7c-source-card-v2'
       AND revision.external_processing_allowed
       AND position(
         'approvalId=AUTH_EXTERNAL_PROCESSING_30_PROJECT_CARDS_20260731' IN revision.license_note
       ) > 0
     )
   )
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
-- CREATE OR REPLACE 이후에도 least-privilege caller만 각 경계를 실행하도록 명시적으로 재고정한다.
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_source_revision_locator() FROM PUBLIC;
REVOKE ALL PRIVILEGES
  ON FUNCTION register_rag_verified_source_card(text, text, text, text, timestamptz, text[])
  FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION activate_verified_rag_generation(
  text, text, bigint, text, text, text, text, text,
  integer, integer, integer,
  text, text, text, text, text, text, text, text, text,
  numeric, text
) FROM PUBLIC;
GRANT EXECUTE
  ON FUNCTION register_rag_verified_source_card(text, text, text, text, timestamptz, text[])
  TO decision_rag_writer;
GRANT EXECUTE ON FUNCTION activate_verified_rag_generation(
  text, text, bigint, text, text, text, text, text,
  integer, integer, integer,
  text, text, text, text, text, text, text, text, text,
  numeric, text
) TO decision_rag_admin;
