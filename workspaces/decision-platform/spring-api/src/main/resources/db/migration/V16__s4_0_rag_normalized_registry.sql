-- S4.0은 V2의 sparse RAG skeleton을 삭제하지 않고, 비어 있음이 증명될 때만 tombstone한 뒤
-- source revision·generation·embedding identity를 분리한 새 registry를 원자적으로 만든다.
-- count와 tombstone 사이에 legacy write가 끼어들지 못하도록 다섯 table을 먼저 잠근다.
LOCK TABLE
  rag_sources,
  rag_chunks,
  rag_answers,
  rag_citations,
  rag_answer_feedback
IN ACCESS EXCLUSIVE MODE;

DO $s4_v2_precondition$
DECLARE
  sources_count bigint;
  chunks_count bigint;
  answers_count bigint;
  citations_count bigint;
  feedback_count bigint;
BEGIN
  SELECT count(*) INTO sources_count FROM rag_sources;
  SELECT count(*) INTO chunks_count FROM rag_chunks;
  SELECT count(*) INTO answers_count FROM rag_answers;
  SELECT count(*) INTO citations_count FROM rag_citations;
  SELECT count(*) INTO feedback_count FROM rag_answer_feedback;

  IF sources_count <> 0
     OR chunks_count <> 0
     OR answers_count <> 0
     OR citations_count <> 0
     OR feedback_count <> 0 THEN
    RAISE EXCEPTION
      'S4 normalized RAG precondition failed: V2 legacy RAG tables must all be empty; approved migration packet required';
  END IF;
END
$s4_v2_precondition$;

-- constraint/index 이름도 함께 tombstone해 새 canonical 이름이 legacy object와 충돌하지 않게 한다.
ALTER TABLE rag_sources RENAME CONSTRAINT rag_sources_pkey TO rag_sources_v2_legacy_pkey;

ALTER TABLE rag_chunks RENAME CONSTRAINT rag_chunks_pkey TO rag_chunks_v2_legacy_pkey;
ALTER TABLE rag_chunks
  RENAME CONSTRAINT rag_chunks_source_id_fkey TO rag_chunks_v2_legacy_source_id_fkey;
ALTER TABLE rag_chunks
  RENAME CONSTRAINT rag_chunks_seq_check TO rag_chunks_v2_legacy_seq_check;
ALTER TABLE rag_chunks
  RENAME CONSTRAINT rag_chunks_source_content_hash_embedding_model_unique
  TO rag_chunks_v2_legacy_source_content_hash_embedding_model_unique;
ALTER INDEX idx_chunks_trgm RENAME TO rag_chunks_v2_legacy_trgm_idx;
ALTER INDEX idx_chunks_source RENAME TO rag_chunks_v2_legacy_source_idx;

ALTER TABLE rag_answers RENAME CONSTRAINT rag_answers_pkey TO rag_answers_v2_legacy_pkey;
ALTER TABLE rag_answers
  RENAME CONSTRAINT rag_answers_user_id_fkey TO rag_answers_v2_legacy_user_id_fkey;
ALTER TABLE rag_answers
  RENAME CONSTRAINT rag_answers_citation_coverage_check
  TO rag_answers_v2_legacy_citation_coverage_check;
ALTER INDEX idx_answers_user RENAME TO rag_answers_v2_legacy_user_idx;

ALTER TABLE rag_citations RENAME CONSTRAINT rag_citations_pkey TO rag_citations_v2_legacy_pkey;
ALTER TABLE rag_citations
  RENAME CONSTRAINT rag_citations_answer_id_fkey TO rag_citations_v2_legacy_answer_id_fkey;
ALTER TABLE rag_citations
  RENAME CONSTRAINT rag_citations_cit_no_check TO rag_citations_v2_legacy_cit_no_check;
ALTER TABLE rag_citations
  RENAME CONSTRAINT rag_citations_chunk_id_fkey TO rag_citations_v2_legacy_chunk_id_fkey;

ALTER TABLE rag_answer_feedback
  RENAME CONSTRAINT rag_answer_feedback_pkey TO rag_answer_feedback_v2_legacy_pkey;
ALTER TABLE rag_answer_feedback
  RENAME CONSTRAINT rag_answer_feedback_answer_id_fkey
  TO rag_answer_feedback_v2_legacy_answer_id_fkey;
ALTER TABLE rag_answer_feedback
  RENAME CONSTRAINT rag_answer_feedback_user_id_fkey
  TO rag_answer_feedback_v2_legacy_user_id_fkey;

ALTER TABLE rag_sources RENAME TO rag_sources_v2_legacy;
ALTER TABLE rag_chunks RENAME TO rag_chunks_v2_legacy;
ALTER TABLE rag_answers RENAME TO rag_answers_v2_legacy;
ALTER TABLE rag_citations RENAME TO rag_citations_v2_legacy;
ALTER TABLE rag_answer_feedback RENAME TO rag_answer_feedback_v2_legacy;

REVOKE ALL PRIVILEGES ON TABLE
  rag_sources_v2_legacy,
  rag_chunks_v2_legacy,
  rag_answers_v2_legacy,
  rag_citations_v2_legacy,
  rag_answer_feedback_v2_legacy
FROM PUBLIC;

-- source identity는 URL이나 mutable metadata를 소유하지 않는다. retirement 뒤 같은 ID 재사용도 허용하지 않는다.
CREATE TABLE rag_sources (
  source_id text PRIMARY KEY,
  source_type text NOT NULL,
  institution text NOT NULL,
  topic text NOT NULL,
  owner_identity text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  retired_at timestamptz,
  CONSTRAINT rag_sources_source_id_format_check
    CHECK (source_id ~ '^src_[a-z0-9]+_[a-z0-9_]+_[0-9]{3}$'),
  CONSTRAINT rag_sources_source_type_check
    CHECK (source_type IN ('UPSTREAM_REFERENCE', 'PROJECT_SOURCE_CARD')),
  CONSTRAINT rag_sources_institution_check
    CHECK (institution ~ '^[a-z0-9][a-z0-9_]{0,63}$'),
  CONSTRAINT rag_sources_topic_check
    CHECK (topic ~ '^[a-z0-9][a-z0-9_]{0,127}$'),
  CONSTRAINT rag_sources_owner_check
    CHECK (char_length(owner_identity) BETWEEN 1 AND 128),
  CONSTRAINT rag_sources_retired_after_created_check
    CHECK (retired_at IS NULL OR retired_at >= created_at),
  CONSTRAINT rag_sources_identity_pair_unique UNIQUE (source_id, source_type)
);

-- locator 이동은 기존 revision UPDATE가 아니라 source retirement와 새 source ID 발급으로 처리한다.
CREATE TABLE rag_source_revisions (
  source_revision_id text PRIMARY KEY,
  source_id text NOT NULL REFERENCES rag_sources(source_id) ON DELETE RESTRICT,
  revision_seq integer NOT NULL,
  registry_version text NOT NULL,
  title text NOT NULL,
  tier text NOT NULL,
  access_level text NOT NULL,
  license_decision text NOT NULL,
  license_note text NOT NULL,
  attribution text NOT NULL,
  retention_mode text NOT NULL,
  retention_days integer NOT NULL,
  retention_owner text NOT NULL,
  external_processing_allowed boolean NOT NULL,
  initial_processing text NOT NULL,
  canonical_url text NOT NULL,
  allowed_origin text NOT NULL,
  allowed_path text NOT NULL,
  locator_sha256 text NOT NULL,
  metadata_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_source_revisions_id_format_check
    CHECK (source_revision_id ~ '^src_rev_[0-9a-f]{32}$'),
  CONSTRAINT rag_source_revisions_sequence_check CHECK (revision_seq BETWEEN 1 AND 999999),
  CONSTRAINT rag_source_revisions_registry_version_check
    CHECK (char_length(registry_version) BETWEEN 1 AND 128),
  CONSTRAINT rag_source_revisions_title_check CHECK (char_length(title) BETWEEN 1 AND 300),
  CONSTRAINT rag_source_revisions_tier_check
    CHECK (tier IN ('OFFICIAL', 'PROJECT')),
  CONSTRAINT rag_source_revisions_access_check
    CHECK (access_level IN ('PUBLIC', 'INTERNAL')),
  CONSTRAINT rag_source_revisions_license_check
    CHECK (
      license_decision IN (
        'REFERENCE_ONLY_NO_EXTERNAL_PROCESSING',
        'REFERENCE_ONLY_TERMS_RESTRICTED',
        'REFERENCE_ONLY_LICENSE_UNSPECIFIED',
        'PROJECT_AUTHORED_PUBLIC',
        'PROJECT_AUTHORED_INTERNAL'
      )
    ),
  CONSTRAINT rag_source_revisions_license_note_check
    CHECK (char_length(license_note) BETWEEN 1 AND 1000),
  CONSTRAINT rag_source_revisions_attribution_check
    CHECK (char_length(attribution) BETWEEN 1 AND 500),
  CONSTRAINT rag_source_revisions_retention_check
    CHECK (
      retention_mode IN ('REFERENCE_METADATA_ONLY', 'PROJECT_CARD')
      AND retention_days BETWEEN 1 AND 3650
      AND char_length(retention_owner) BETWEEN 1 AND 128
    ),
  CONSTRAINT rag_source_revisions_processing_check
    CHECK (initial_processing IN ('REFERENCE_ONLY', 'PROJECT_AUTHORED_CARD')),
  CONSTRAINT rag_source_revisions_locator_check
    CHECK (
      canonical_url ~ '^https://'
      AND allowed_origin ~ '^https://[A-Za-z0-9.-]+(:443)?$'
      AND allowed_path LIKE '/%'
      AND position('#' IN canonical_url) = 0
      AND position('@' IN split_part(canonical_url, '/', 3)) = 0
    ),
  CONSTRAINT rag_source_revisions_hash_check
    CHECK (
      locator_sha256 ~ '^[0-9a-f]{64}$'
      AND metadata_hash ~ '^[0-9a-f]{64}$'
    ),
  CONSTRAINT rag_source_revisions_source_sequence_unique UNIQUE (source_id, revision_seq),
  CONSTRAINT rag_source_revisions_source_metadata_unique UNIQUE (source_id, metadata_hash),
  CONSTRAINT rag_source_revisions_identity_pair_unique UNIQUE (source_revision_id, source_id)
);
CREATE INDEX rag_source_revisions_source_latest_idx
  ON rag_source_revisions (source_id, revision_seq DESC);

-- locator 이동은 같은 source ID의 revision append로 숨기지 않고 새 sequence source ID 승인으로만 처리한다.
CREATE FUNCTION guard_rag_source_revision_locator()
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
       OR NEW.external_processing_allowed
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

CREATE TRIGGER rag_source_revisions_locator_guard
BEFORE INSERT ON rag_source_revisions
FOR EACH ROW
EXECUTE FUNCTION guard_rag_source_revision_locator();

-- relocation은 direct UPDATE 권한 없이 기존 identity를 닫고 바로 다음 sequence identity만 활성화한다.
CREATE FUNCTION retire_rag_source_for_relocation(
  previous_source_id text,
  replacement_source_id text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $retire_rag_source_for_relocation$
DECLARE
  previous_source record;
  replacement_source record;
  previous_sequence integer;
  replacement_sequence integer;
  retired boolean;
BEGIN
  IF previous_source_id IS NULL
     OR replacement_source_id IS NULL
     OR previous_source_id !~ '^src_[a-z0-9]+_[a-z0-9_]+_[0-9]{3}$'
     OR replacement_source_id !~ '^src_[a-z0-9]+_[a-z0-9_]+_[0-9]{3}$'
     OR previous_source_id = replacement_source_id THEN
    RAISE EXCEPTION 'RAG source relocation identity is invalid'
      USING ERRCODE = '22023';
  END IF;

  -- 반대 순서의 동시 relocation도 같은 lock order를 사용해 교착을 피한다.
  PERFORM 1
  FROM public.rag_sources AS source
  WHERE source.source_id IN (previous_source_id, replacement_source_id)
  ORDER BY source.source_id
  FOR UPDATE;

  SELECT
    source.source_type,
    source.institution,
    source.topic,
    source.owner_identity,
    source.retired_at
  INTO previous_source
  FROM public.rag_sources AS source
  WHERE source.source_id = previous_source_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'RAG relocation previous source is missing'
      USING ERRCODE = '23503';
  END IF;

  SELECT
    source.source_type,
    source.institution,
    source.topic,
    source.owner_identity,
    source.retired_at
  INTO replacement_source
  FROM public.rag_sources AS source
  WHERE source.source_id = replacement_source_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'RAG relocation replacement source is missing'
      USING ERRCODE = '23503';
  END IF;

  previous_sequence := right(previous_source_id, 3)::integer;
  replacement_sequence := right(replacement_source_id, 3)::integer;
  IF left(previous_source_id, char_length(previous_source_id) - 3)
       IS DISTINCT FROM
       left(replacement_source_id, char_length(replacement_source_id) - 3)
     OR replacement_sequence <> previous_sequence + 1
     OR previous_source.source_type IS DISTINCT FROM replacement_source.source_type
     OR previous_source.institution IS DISTINCT FROM replacement_source.institution
     OR previous_source.topic IS DISTINCT FROM replacement_source.topic
     OR previous_source.owner_identity IS DISTINCT FROM replacement_source.owner_identity
     OR replacement_source.retired_at IS NOT NULL THEN
    RAISE EXCEPTION 'RAG relocation requires the next matching active source identity'
      USING ERRCODE = '23514';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.rag_source_revisions AS revision
    WHERE revision.source_id = replacement_source_id
      AND revision.revision_seq = 1
  ) THEN
    RAISE EXCEPTION 'RAG relocation replacement revision is missing'
      USING ERRCODE = '23503';
  END IF;

  IF previous_source.retired_at IS NOT NULL THEN
    RETURN false;
  END IF;

  UPDATE public.rag_sources AS source
  SET retired_at = transaction_timestamp()
  WHERE source.source_id = previous_source_id
    AND source.retired_at IS NULL;
  retired := FOUND;
  RETURN retired;
END
$retire_rag_source_for_relocation$;
ALTER FUNCTION retire_rag_source_for_relocation(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION retire_rag_source_for_relocation(text, text) FROM PUBLIC;

-- source check는 raw body/header/IP를 보관하지 않고 bounded 상태와 digest만 append한다.
CREATE TABLE rag_source_checks (
  source_check_id text PRIMARY KEY,
  source_id text NOT NULL,
  source_revision_id text NOT NULL,
  checked_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  check_result text NOT NULL,
  response_status integer,
  bytes_read integer,
  content_hash text,
  peer_ip_fingerprint text,
  rejection_class text,
  CONSTRAINT rag_source_checks_id_format_check
    CHECK (source_check_id ~ '^src_chk_[0-9a-f]{32}$'),
  CONSTRAINT rag_source_checks_revision_fkey
    FOREIGN KEY (source_revision_id, source_id)
    REFERENCES rag_source_revisions(source_revision_id, source_id)
    ON DELETE RESTRICT,
  CONSTRAINT rag_source_checks_result_check
    CHECK (
      check_result IN (
        'UNCHANGED',
        'CHANGED',
        'UNAVAILABLE',
        'REJECTED_URL',
        'REJECTED_DNS',
        'REJECTED_PEER',
        'REJECTED_REDIRECT',
        'REJECTED_RESPONSE',
        'TIMEOUT'
      )
    ),
  CONSTRAINT rag_source_checks_response_bounds_check
    CHECK (
      (response_status IS NULL OR response_status BETWEEN 100 AND 599)
      AND (bytes_read IS NULL OR bytes_read BETWEEN 0 AND 8388608)
    ),
  CONSTRAINT rag_source_checks_hash_check
    CHECK (
      (content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$')
      AND (peer_ip_fingerprint IS NULL OR peer_ip_fingerprint ~ '^[0-9a-f]{64}$')
    ),
  CONSTRAINT rag_source_checks_rejection_check
    CHECK (
      rejection_class IS NULL
      OR rejection_class IN (
        'URL_POLICY',
        'DNS_POLICY',
        'REBINDING',
        'PEER_MISMATCH',
        'REDIRECT',
        'MIME_MISMATCH',
        'OVERSIZED',
        'COMPRESSED',
        'TIMEOUT',
        'TRANSPORT_DISABLED'
      )
    )
);
CREATE INDEX rag_source_checks_revision_checked_idx
  ON rag_source_checks (source_revision_id, checked_at DESC, source_check_id);

-- ingest run은 source 상태와 분리하며 terminal 결과를 덮어쓰지 않는 bounded orchestration ledger다.
CREATE TABLE rag_ingest_runs (
  ingest_run_id text PRIMARY KEY,
  source_revision_id text NOT NULL REFERENCES rag_source_revisions(source_revision_id) ON DELETE RESTRICT,
  parser_version text NOT NULL,
  canonicalizer_version text NOT NULL,
  card_schema_version text NOT NULL,
  input_content_hash text NOT NULL,
  status text NOT NULL,
  expected_chunk_count integer NOT NULL,
  actual_chunk_count integer,
  planned_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  started_at timestamptz,
  completed_at timestamptz,
  failure_class text,
  CONSTRAINT rag_ingest_runs_id_format_check
    CHECK (ingest_run_id ~ '^rag_ing_[0-9a-f]{32}$'),
  CONSTRAINT rag_ingest_runs_version_check
    CHECK (
      char_length(parser_version) BETWEEN 1 AND 128
      AND char_length(canonicalizer_version) BETWEEN 1 AND 128
      AND char_length(card_schema_version) BETWEEN 1 AND 128
    ),
  CONSTRAINT rag_ingest_runs_hash_check
    CHECK (input_content_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_ingest_runs_status_check
    CHECK (status IN ('PLANNED', 'RUNNING', 'SUCCEEDED', 'FAILED')),
  CONSTRAINT rag_ingest_runs_count_check
    CHECK (
      expected_chunk_count BETWEEN 1 AND 10000
      AND (actual_chunk_count IS NULL OR actual_chunk_count BETWEEN 0 AND expected_chunk_count)
    ),
  CONSTRAINT rag_ingest_runs_time_check
    CHECK (
      (started_at IS NULL OR started_at >= planned_at)
      AND (completed_at IS NULL OR (started_at IS NOT NULL AND completed_at >= started_at))
    ),
  CONSTRAINT rag_ingest_runs_terminal_check
    CHECK (
      (status IN ('PLANNED', 'RUNNING') AND completed_at IS NULL AND failure_class IS NULL)
      OR
      (status = 'SUCCEEDED' AND completed_at IS NOT NULL AND failure_class IS NULL
       AND actual_chunk_count = expected_chunk_count)
      OR
      (status = 'FAILED' AND completed_at IS NOT NULL AND failure_class IS NOT NULL
       AND char_length(failure_class) BETWEEN 1 AND 64)
    ),
  CONSTRAINT rag_ingest_runs_source_input_unique
    UNIQUE (source_revision_id, parser_version, canonicalizer_version, card_schema_version, input_content_hash),
  CONSTRAINT rag_ingest_runs_identity_source_unique
    UNIQUE (ingest_run_id, source_revision_id)
);

-- PLANNED→RUNNING→terminal 단방향만 허용해 성공·실패 receipt를 다시 열거나 덮어쓰지 못하게 한다.
CREATE FUNCTION guard_rag_ingest_run_transition()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $guard_rag_ingest_run_transition$
DECLARE
  ingest_source_type text;
  ingest_source_tier text;
  persisted_chunk_count integer;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'PLANNED'
       OR NEW.started_at IS NOT NULL
       OR NEW.completed_at IS NOT NULL
       OR NEW.actual_chunk_count IS NOT NULL
       OR NEW.failure_class IS NOT NULL THEN
      RAISE EXCEPTION 'RAG ingest run must start in a clean PLANNED state'
        USING ERRCODE = '23514';
    END IF;
    SELECT source.source_type, revision.tier
    INTO ingest_source_type, ingest_source_tier
    FROM public.rag_source_revisions AS revision
    JOIN public.rag_sources AS source
      ON source.source_id = revision.source_id
    WHERE revision.source_revision_id = NEW.source_revision_id
    FOR KEY SHARE OF source, revision;
    IF NOT FOUND
       OR ingest_source_type <> 'PROJECT_SOURCE_CARD'
       OR ingest_source_tier <> 'PROJECT' THEN
      RAISE EXCEPTION 'RAG ingest accepts verified project-card lineage only'
        USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
  END IF;

  IF OLD.status IN ('SUCCEEDED', 'FAILED') THEN
    RAISE EXCEPTION 'RAG ingest terminal state is immutable'
      USING ERRCODE = '23514';
  END IF;
  IF OLD.status IS DISTINCT FROM NEW.status
     AND NOT (
       (OLD.status = 'PLANNED' AND NEW.status = 'RUNNING')
       OR
       (OLD.status = 'RUNNING' AND NEW.status IN ('SUCCEEDED', 'FAILED'))
     ) THEN
    RAISE EXCEPTION 'RAG ingest status transition is not allowed'
      USING ERRCODE = '23514';
  END IF;
  IF NEW.status = 'PLANNED'
     AND (
       NEW.started_at IS NOT NULL
       OR NEW.completed_at IS NOT NULL
       OR NEW.actual_chunk_count IS NOT NULL
       OR NEW.failure_class IS NOT NULL
     ) THEN
    RAISE EXCEPTION 'RAG PLANNED ingest state cannot carry execution receipt'
      USING ERRCODE = '23514';
  END IF;
  IF NEW.status = 'RUNNING'
     AND (
       NEW.started_at IS NULL
       OR NEW.completed_at IS NOT NULL
       OR NEW.actual_chunk_count IS NOT NULL
       OR NEW.failure_class IS NOT NULL
     ) THEN
    RAISE EXCEPTION 'RAG RUNNING ingest state requires only a start receipt'
      USING ERRCODE = '23514';
  END IF;
  IF NEW.status IN ('SUCCEEDED', 'FAILED') THEN
    SELECT count(*)::integer
    INTO persisted_chunk_count
    FROM public.rag_chunk_revisions AS chunk
    WHERE chunk.ingest_run_id = NEW.ingest_run_id;
    IF NEW.actual_chunk_count IS DISTINCT FROM persisted_chunk_count THEN
      RAISE EXCEPTION 'RAG ingest terminal count must match persisted chunks'
        USING ERRCODE = '23514';
    END IF;
    IF NEW.status = 'SUCCEEDED'
       AND persisted_chunk_count <> NEW.expected_chunk_count THEN
      RAISE EXCEPTION 'RAG ingest success requires every expected chunk'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END
$guard_rag_ingest_run_transition$;
ALTER FUNCTION guard_rag_ingest_run_transition() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_ingest_run_transition() FROM PUBLIC;

CREATE TRIGGER rag_ingest_runs_transition_guard
BEFORE INSERT OR UPDATE ON rag_ingest_runs
FOR EACH ROW
EXECUTE FUNCTION guard_rag_ingest_run_transition();

-- canonical text만 저장한다. BGE transient adjacent context와 provider payload는 이 table에 들어오지 않는다.
CREATE TABLE rag_chunk_revisions (
  chunk_revision_id text PRIMARY KEY,
  ingest_run_id text NOT NULL,
  source_revision_id text NOT NULL REFERENCES rag_source_revisions(source_revision_id) ON DELETE RESTRICT,
  chunk_seq integer NOT NULL,
  heading_path text[] NOT NULL,
  canonical_content text NOT NULL,
  canonical_content_hash text NOT NULL,
  token_count integer NOT NULL,
  topic text NOT NULL,
  access_level text NOT NULL,
  tier text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_chunk_revisions_id_format_check
    CHECK (chunk_revision_id ~ '^rag_chk_[0-9a-f]{32}$'),
  CONSTRAINT rag_chunk_revisions_sequence_check CHECK (chunk_seq BETWEEN 1 AND 10000),
  CONSTRAINT rag_chunk_revisions_heading_check
    CHECK (cardinality(heading_path) BETWEEN 1 AND 12),
  CONSTRAINT rag_chunk_revisions_content_check
    CHECK (octet_length(canonical_content) BETWEEN 1 AND 65536),
  CONSTRAINT rag_chunk_revisions_hash_check
    CHECK (canonical_content_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_chunk_revisions_token_check CHECK (token_count BETWEEN 1 AND 10000),
  CONSTRAINT rag_chunk_revisions_topic_check CHECK (char_length(topic) BETWEEN 1 AND 128),
  CONSTRAINT rag_chunk_revisions_access_check CHECK (access_level IN ('PUBLIC', 'INTERNAL')),
  CONSTRAINT rag_chunk_revisions_tier_check CHECK (tier IN ('OFFICIAL', 'PROJECT')),
  CONSTRAINT rag_chunk_revisions_ingest_source_fkey
    FOREIGN KEY (ingest_run_id, source_revision_id)
    REFERENCES rag_ingest_runs(ingest_run_id, source_revision_id)
    ON DELETE RESTRICT,
  CONSTRAINT rag_chunk_revisions_run_sequence_unique UNIQUE (ingest_run_id, chunk_seq),
  CONSTRAINT rag_chunk_revisions_source_sequence_hash_unique
    UNIQUE (source_revision_id, chunk_seq, canonical_content_hash),
  CONSTRAINT rag_chunk_revisions_identity_pair_unique
    UNIQUE (chunk_revision_id, source_revision_id)
);

-- retrieval scope는 writer가 복제 입력한 label이 아니라 immutable source/revision authority와 일치해야 한다.
CREATE FUNCTION guard_rag_chunk_scope()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $guard_rag_chunk_scope$
DECLARE
  authoritative_topic text;
  authoritative_access_level text;
  authoritative_tier text;
  authoritative_source_type text;
  authoritative_processing text;
  ingest_status text;
  ingest_expected_chunk_count integer;
BEGIN
  SELECT
    source.topic,
    revision.access_level,
    revision.tier,
    source.source_type,
    revision.initial_processing,
    ingest.status,
    ingest.expected_chunk_count
  INTO
    authoritative_topic,
    authoritative_access_level,
    authoritative_tier,
    authoritative_source_type,
    authoritative_processing,
    ingest_status,
    ingest_expected_chunk_count
  FROM public.rag_source_revisions AS revision
  JOIN public.rag_sources AS source
    ON source.source_id = revision.source_id
  JOIN public.rag_ingest_runs AS ingest
    ON ingest.ingest_run_id = NEW.ingest_run_id
   AND ingest.source_revision_id = revision.source_revision_id
  WHERE revision.source_revision_id = NEW.source_revision_id
  FOR UPDATE OF ingest;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'RAG chunk source revision authority is missing'
      USING ERRCODE = '23503';
  END IF;
  IF ingest_status <> 'RUNNING'
     OR NEW.chunk_seq > ingest_expected_chunk_count THEN
    RAISE EXCEPTION 'RAG chunks can append only to the bounded RUNNING ingest'
      USING ERRCODE = '23514';
  END IF;
  IF authoritative_source_type <> 'PROJECT_SOURCE_CARD'
     OR authoritative_tier <> 'PROJECT'
     OR authoritative_processing <> 'PROJECT_AUTHORED_CARD' THEN
    RAISE EXCEPTION 'RAG chunks require project-authored card lineage'
      USING ERRCODE = '23514';
  END IF;
  IF NEW.topic IS DISTINCT FROM authoritative_topic
     OR NEW.access_level IS DISTINCT FROM authoritative_access_level
     OR NEW.tier IS DISTINCT FROM authoritative_tier THEN
    RAISE EXCEPTION 'RAG chunk scope must match its source revision authority'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END
$guard_rag_chunk_scope$;
ALTER FUNCTION guard_rag_chunk_scope() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_chunk_scope() FROM PUBLIC;

CREATE TRIGGER rag_chunk_revisions_scope_guard
BEFORE INSERT ON rag_chunk_revisions
FOR EACH ROW
EXECUTE FUNCTION guard_rag_chunk_scope();

CREATE INDEX rag_chunk_revisions_source_idx
  ON rag_chunk_revisions (source_revision_id, chunk_seq);
CREATE INDEX rag_chunk_revisions_trgm_idx
  ON rag_chunk_revisions USING gin (canonical_content gin_trgm_ops);

CREATE TABLE rag_corpus_generations (
  corpus_generation_id text PRIMARY KEY,
  corpus_hash text NOT NULL,
  embedding_profile_id text NOT NULL,
  vector_space text NOT NULL,
  status text NOT NULL,
  expected_chunk_count integer NOT NULL,
  actual_chunk_count integer NOT NULL DEFAULT 0,
  evaluation_status text NOT NULL DEFAULT 'PENDING',
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  evaluated_at timestamptz,
  activated_at timestamptz,
  failed_at timestamptz,
  disabled_at timestamptz,
  failure_class text,
  CONSTRAINT rag_corpus_generations_id_format_check
    CHECK (corpus_generation_id ~ '^rag_gen_[0-9a-f]{32}$'),
  CONSTRAINT rag_corpus_generations_hash_check CHECK (corpus_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_corpus_generations_profile_check
    CHECK (embedding_profile_id IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1')),
  CONSTRAINT rag_corpus_generations_vector_space_check
    CHECK (vector_space = embedding_profile_id),
  CONSTRAINT rag_corpus_generations_status_check
    CHECK (
      status IN (
        'REGISTERED',
        'PLANNED',
        'MATERIALIZING',
        'MATERIALIZED',
        'EVAL_PASSED',
        'ACTIVE',
        'FAILED_FINAL',
        'DISABLED'
      )
    ),
  CONSTRAINT rag_corpus_generations_count_check
    CHECK (
      expected_chunk_count BETWEEN 1 AND 10000
      AND actual_chunk_count BETWEEN 0 AND expected_chunk_count
      AND (
        status NOT IN ('MATERIALIZED', 'EVAL_PASSED', 'ACTIVE')
        OR actual_chunk_count = expected_chunk_count
      )
    ),
  CONSTRAINT rag_corpus_generations_evaluation_check
    CHECK (
      evaluation_status IN ('PENDING', 'PASSED', 'FAILED')
      AND (
        status NOT IN ('EVAL_PASSED', 'ACTIVE')
        OR (evaluation_status = 'PASSED' AND evaluated_at IS NOT NULL)
      )
      AND (status <> 'FAILED_FINAL' OR evaluation_status = 'FAILED')
    ),
  CONSTRAINT rag_corpus_generations_time_check
    CHECK (
      (evaluated_at IS NULL OR evaluated_at >= created_at)
      AND (activated_at IS NULL OR (evaluated_at IS NOT NULL AND activated_at >= evaluated_at))
      AND (failed_at IS NULL OR failed_at >= coalesce(evaluated_at, created_at))
      AND (
        disabled_at IS NULL
        OR disabled_at >= coalesce(activated_at, evaluated_at, created_at)
      )
      AND (status <> 'ACTIVE' OR activated_at IS NOT NULL)
      AND (status <> 'FAILED_FINAL' OR failed_at IS NOT NULL)
      AND (status <> 'DISABLED' OR disabled_at IS NOT NULL)
    ),
  CONSTRAINT rag_corpus_generations_terminal_check
    CHECK (
      (
        status = 'FAILED_FINAL'
        AND failure_class IS NOT NULL
        AND char_length(failure_class) BETWEEN 1 AND 64
        AND activated_at IS NULL
        AND disabled_at IS NULL
      )
      OR
      (
        status = 'DISABLED'
        AND failure_class IS NULL
        AND failed_at IS NULL
      )
      OR
      (
        status NOT IN ('FAILED_FINAL', 'DISABLED')
        AND failure_class IS NULL
        AND failed_at IS NULL
        AND disabled_at IS NULL
      )
    ),
  CONSTRAINT rag_corpus_generations_profile_identity_unique
    UNIQUE (corpus_generation_id, embedding_profile_id),
  CONSTRAINT rag_corpus_generations_corpus_profile_unique
    UNIQUE (corpus_hash, embedding_profile_id)
);

CREATE TABLE rag_generation_chunks (
  corpus_generation_id text NOT NULL,
  chunk_revision_id text NOT NULL REFERENCES rag_chunk_revisions(chunk_revision_id) ON DELETE RESTRICT,
  embedding_profile_id text NOT NULL,
  embedding_input_hash text NOT NULL,
  context_set_hash text,
  ordinal integer NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_generation_chunks_generation_profile_fkey
    FOREIGN KEY (corpus_generation_id, embedding_profile_id)
    REFERENCES rag_corpus_generations(corpus_generation_id, embedding_profile_id)
    ON DELETE RESTRICT,
  CONSTRAINT rag_generation_chunks_ordinal_check CHECK (ordinal BETWEEN 1 AND 10000),
  CONSTRAINT rag_generation_chunks_hash_check
    CHECK (
      embedding_input_hash ~ '^[0-9a-f]{64}$'
      AND (context_set_hash IS NULL OR context_set_hash ~ '^[0-9a-f]{64}$')
    ),
  CONSTRAINT rag_generation_chunks_context_policy_check
    CHECK (
      (embedding_profile_id = 'bge_m3_local_1024_v1' AND context_set_hash IS NULL)
      OR
      (embedding_profile_id = 'voyage_context_4_1024_v1' AND context_set_hash IS NOT NULL)
    ),
  CONSTRAINT rag_generation_chunks_pkey PRIMARY KEY (corpus_generation_id, chunk_revision_id),
  CONSTRAINT rag_generation_chunks_ordinal_unique UNIQUE (corpus_generation_id, ordinal),
  CONSTRAINT rag_generation_chunks_embedding_identity_unique
    UNIQUE (corpus_generation_id, chunk_revision_id, embedding_profile_id)
);

-- NULLS NOT DISTINCT는 BGE의 context_set_hash=NULL도 같은 canonical identity로 중복되지 않게 한다.
CREATE TABLE rag_chunk_embeddings (
  chunk_embedding_id text PRIMARY KEY,
  corpus_generation_id text NOT NULL,
  chunk_revision_id text NOT NULL,
  embedding_profile_id text NOT NULL,
  vector_space text NOT NULL,
  embedding_input_hash text NOT NULL,
  context_set_hash text,
  embedding vector(1024) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_chunk_embeddings_id_format_check
    CHECK (chunk_embedding_id ~ '^rag_emb_[0-9a-f]{32}$'),
  CONSTRAINT rag_chunk_embeddings_membership_fkey
    FOREIGN KEY (corpus_generation_id, chunk_revision_id, embedding_profile_id)
    REFERENCES rag_generation_chunks(corpus_generation_id, chunk_revision_id, embedding_profile_id)
    ON DELETE RESTRICT,
  CONSTRAINT rag_chunk_embeddings_vector_space_check
    CHECK (vector_space = embedding_profile_id),
  CONSTRAINT rag_chunk_embeddings_hash_check
    CHECK (
      embedding_input_hash ~ '^[0-9a-f]{64}$'
      AND (context_set_hash IS NULL OR context_set_hash ~ '^[0-9a-f]{64}$')
    ),
  CONSTRAINT rag_chunk_embeddings_context_policy_check
    CHECK (
      (embedding_profile_id = 'bge_m3_local_1024_v1' AND context_set_hash IS NULL)
      OR
      (embedding_profile_id = 'voyage_context_4_1024_v1' AND context_set_hash IS NOT NULL)
    ),
  CONSTRAINT rag_chunk_embeddings_normalized_check
    CHECK (abs(vector_norm(embedding)::double precision - 1.0) <= 0.00001),
  CONSTRAINT rag_chunk_embeddings_identity_unique
    UNIQUE NULLS NOT DISTINCT (
      chunk_revision_id,
      embedding_profile_id,
      embedding_input_hash,
      context_set_hash
    )
);
CREATE INDEX rag_chunk_embeddings_generation_idx
  ON rag_chunk_embeddings (corpus_generation_id, embedding_profile_id, chunk_revision_id);

-- materialization row는 parent generation을 잠근 뒤에만 추가해 ACTIVE 전환과 동시 삽입이 엇갈리지 않게 한다.
CREATE FUNCTION guard_rag_generation_materialization()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $guard_rag_generation_materialization$
DECLARE
  generation_status text;
  membership_ingest_status text;
  membership_source_type text;
  membership_tier text;
BEGIN
  IF TG_TABLE_NAME = 'rag_generation_chunks' THEN
    SELECT ingest.status, source.source_type, revision.tier
    INTO membership_ingest_status, membership_source_type, membership_tier
    FROM public.rag_chunk_revisions AS chunk
    JOIN public.rag_ingest_runs AS ingest
      ON ingest.ingest_run_id = chunk.ingest_run_id
     AND ingest.source_revision_id = chunk.source_revision_id
    JOIN public.rag_source_revisions AS revision
      ON revision.source_revision_id = chunk.source_revision_id
    JOIN public.rag_sources AS source
      ON source.source_id = revision.source_id
    WHERE chunk.chunk_revision_id = NEW.chunk_revision_id
    FOR UPDATE OF ingest;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'RAG generation chunk lineage is missing'
        USING ERRCODE = '23503';
    END IF;
    IF membership_ingest_status <> 'SUCCEEDED'
       OR membership_source_type <> 'PROJECT_SOURCE_CARD'
       OR membership_tier <> 'PROJECT' THEN
      RAISE EXCEPTION 'RAG generation membership requires succeeded project-card ingest'
        USING ERRCODE = '23514';
    END IF;
  END IF;

  SELECT generation.status
  INTO generation_status
  FROM public.rag_corpus_generations AS generation
  WHERE generation.corpus_generation_id = NEW.corpus_generation_id
    AND generation.embedding_profile_id = NEW.embedding_profile_id
  FOR UPDATE;

  IF generation_status IS DISTINCT FROM 'MATERIALIZING' THEN
    RAISE EXCEPTION 'RAG generation is not open for materialization'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END
$guard_rag_generation_materialization$;
ALTER FUNCTION guard_rag_generation_materialization() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_generation_materialization() FROM PUBLIC;

CREATE TRIGGER rag_generation_chunks_materialization_guard
BEFORE INSERT ON rag_generation_chunks
FOR EACH ROW
EXECUTE FUNCTION guard_rag_generation_materialization();

CREATE TRIGGER rag_chunk_embeddings_materialization_guard
BEFORE INSERT ON rag_chunk_embeddings
FOR EACH ROW
EXECUTE FUNCTION guard_rag_generation_materialization();

-- canonical catalog의 8단계 lifecycle을 단방향으로 고정해 frozen generation 재개방을 막는다.
CREATE FUNCTION guard_rag_generation_transition()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $guard_rag_generation_transition$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'REGISTERED'
       OR NEW.actual_chunk_count <> 0
       OR NEW.evaluation_status <> 'PENDING'
       OR NEW.evaluated_at IS NOT NULL
       OR NEW.activated_at IS NOT NULL
       OR NEW.failed_at IS NOT NULL
       OR NEW.disabled_at IS NOT NULL
       OR NEW.failure_class IS NOT NULL THEN
      RAISE EXCEPTION 'RAG generation must start in a clean REGISTERED state'
        USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
  END IF;

  IF OLD.status IN ('FAILED_FINAL', 'DISABLED') THEN
    RAISE EXCEPTION 'RAG generation terminal state is immutable'
      USING ERRCODE = '23514';
  END IF;
  IF OLD.status IS NOT DISTINCT FROM NEW.status
     AND OLD.status <> 'MATERIALIZING' THEN
    RAISE EXCEPTION 'RAG generation state receipt cannot be rewritten in place'
      USING ERRCODE = '23514';
  END IF;
  IF OLD.status IS DISTINCT FROM NEW.status
     AND NOT (
       (OLD.status = 'REGISTERED' AND NEW.status IN ('PLANNED', 'DISABLED'))
       OR
       (OLD.status = 'PLANNED' AND NEW.status IN ('MATERIALIZING', 'FAILED_FINAL', 'DISABLED'))
       OR
       (OLD.status = 'MATERIALIZING' AND NEW.status IN ('MATERIALIZED', 'FAILED_FINAL', 'DISABLED'))
       OR
       (OLD.status = 'MATERIALIZED' AND NEW.status IN ('EVAL_PASSED', 'FAILED_FINAL', 'DISABLED'))
       OR
       (OLD.status = 'EVAL_PASSED' AND NEW.status IN ('ACTIVE', 'DISABLED'))
       OR
       (OLD.status = 'ACTIVE' AND NEW.status = 'DISABLED')
     ) THEN
    RAISE EXCEPTION 'RAG generation status transition is not allowed'
      USING ERRCODE = '23514';
  END IF;
  IF NEW.status IN ('REGISTERED', 'PLANNED', 'MATERIALIZING', 'MATERIALIZED')
     AND (
       NEW.evaluation_status <> 'PENDING'
       OR NEW.evaluated_at IS NOT NULL
       OR NEW.activated_at IS NOT NULL
       OR NEW.failed_at IS NOT NULL
       OR NEW.disabled_at IS NOT NULL
       OR NEW.failure_class IS NOT NULL
     ) THEN
    RAISE EXCEPTION 'RAG pre-evaluation generation cannot carry terminal receipt'
      USING ERRCODE = '23514';
  END IF;
  IF NEW.status = 'EVAL_PASSED'
     AND (
       NEW.evaluation_status <> 'PASSED'
       OR NEW.evaluated_at IS NULL
       OR NEW.activated_at IS NOT NULL
       OR NEW.failed_at IS NOT NULL
       OR NEW.disabled_at IS NOT NULL
       OR NEW.failure_class IS NOT NULL
     ) THEN
    RAISE EXCEPTION 'RAG EVAL_PASSED generation receipt is incomplete'
      USING ERRCODE = '23514';
  END IF;
  IF NEW.status = 'ACTIVE'
     AND (
       NEW.evaluation_status <> 'PASSED'
       OR NEW.evaluated_at IS NULL
       OR NEW.activated_at IS NULL
       OR NEW.failed_at IS NOT NULL
       OR NEW.disabled_at IS NOT NULL
       OR NEW.failure_class IS NOT NULL
     ) THEN
    RAISE EXCEPTION 'RAG ACTIVE generation receipt is incomplete'
      USING ERRCODE = '23514';
  END IF;
  IF NEW.status = 'FAILED_FINAL'
     AND (
       NEW.evaluation_status <> 'FAILED'
       OR NEW.failed_at IS NULL
       OR NEW.failure_class IS NULL
       OR NEW.activated_at IS NOT NULL
       OR NEW.disabled_at IS NOT NULL
     ) THEN
    RAISE EXCEPTION 'RAG FAILED_FINAL generation receipt is incomplete'
      USING ERRCODE = '23514';
  END IF;
  IF NEW.status = 'DISABLED'
     AND (
       NEW.disabled_at IS NULL
       OR NEW.failed_at IS NOT NULL
       OR NEW.failure_class IS NOT NULL
       OR NEW.actual_chunk_count IS DISTINCT FROM OLD.actual_chunk_count
       OR NEW.evaluation_status IS DISTINCT FROM OLD.evaluation_status
       OR NEW.evaluated_at IS DISTINCT FROM OLD.evaluated_at
       OR NEW.activated_at IS DISTINCT FROM OLD.activated_at
     ) THEN
    RAISE EXCEPTION 'RAG DISABLED generation must preserve prior materialization and exposure receipts'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END
$guard_rag_generation_transition$;
ALTER FUNCTION guard_rag_generation_transition() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_generation_transition() FROM PUBLIC;

CREATE TRIGGER rag_corpus_generations_transition_guard
BEFORE INSERT OR UPDATE ON rag_corpus_generations
FOR EACH ROW
EXECUTE FUNCTION guard_rag_generation_transition();

-- MATERIALIZED/ACTIVE 전환은 membership과 embedding이 expected count만큼 일대일로 완성돼야 한다.
CREATE FUNCTION guard_rag_generation_activation()
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
      ON embedding.chunk_revision_id = membership.chunk_revision_id
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

CREATE TRIGGER rag_corpus_generations_activation_guard
BEFORE INSERT OR UPDATE OF status, actual_chunk_count, evaluation_status, evaluated_at, activated_at
ON rag_corpus_generations
FOR EACH ROW
EXECUTE FUNCTION guard_rag_generation_activation();

-- static profile/policy catalog는 JSON SSOT에 남기고 DB에는 승인된 동적 pointer와 append-only 전이만 둔다.
CREATE TABLE rag_embedding_policy_state (
  state_id text PRIMARY KEY,
  policy_id text NOT NULL,
  effective_profile_id text NOT NULL,
  active_generation_id text,
  version bigint NOT NULL,
  changed_at timestamptz NOT NULL,
  changed_by_audit_ref text NOT NULL,
  CONSTRAINT rag_embedding_policy_state_singleton_check CHECK (state_id = 'default'),
  CONSTRAINT rag_embedding_policy_state_policy_check
    CHECK (policy_id IN ('bge_only_v1', 'voyage_only_v1', 'bge_then_voyage_on_sla_v1')),
  CONSTRAINT rag_embedding_policy_state_profile_check
    CHECK (
      (policy_id = 'bge_only_v1' AND effective_profile_id = 'bge_m3_local_1024_v1')
      OR
      (policy_id = 'voyage_only_v1' AND effective_profile_id = 'voyage_context_4_1024_v1')
      OR
      (
        policy_id = 'bge_then_voyage_on_sla_v1'
        AND effective_profile_id IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1')
      )
    ),
  CONSTRAINT rag_embedding_policy_state_version_check CHECK (version > 0),
  CONSTRAINT rag_embedding_policy_state_audit_check
    CHECK (char_length(changed_by_audit_ref) BETWEEN 16 AND 128),
  CONSTRAINT rag_embedding_policy_state_generation_profile_fkey
    FOREIGN KEY (active_generation_id, effective_profile_id)
    REFERENCES rag_corpus_generations(corpus_generation_id, embedding_profile_id)
    ON DELETE RESTRICT
);

CREATE TABLE rag_embedding_policy_transitions (
  transition_id text PRIMARY KEY,
  state_id text NOT NULL REFERENCES rag_embedding_policy_state(state_id) ON DELETE RESTRICT,
  from_version bigint NOT NULL,
  to_version bigint NOT NULL,
  from_policy_id text NOT NULL,
  to_policy_id text NOT NULL,
  from_profile_id text NOT NULL,
  to_profile_id text NOT NULL,
  target_generation_id text,
  trigger_code text NOT NULL,
  approved_by_audit_ref text NOT NULL,
  approved_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_embedding_policy_transitions_id_format_check
    CHECK (transition_id ~ '^rag_pol_[0-9a-f]{32}$'),
  CONSTRAINT rag_embedding_policy_transitions_version_check
    CHECK (from_version > 0 AND to_version = from_version + 1),
  CONSTRAINT rag_embedding_policy_transitions_policy_check
    CHECK (
      from_policy_id IN ('bge_only_v1', 'voyage_only_v1', 'bge_then_voyage_on_sla_v1')
      AND to_policy_id IN ('bge_only_v1', 'voyage_only_v1', 'bge_then_voyage_on_sla_v1')
    ),
  CONSTRAINT rag_embedding_policy_transitions_profile_check
    CHECK (
      from_profile_id IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1')
      AND to_profile_id IN ('bge_m3_local_1024_v1', 'voyage_context_4_1024_v1')
    ),
  CONSTRAINT rag_embedding_policy_transitions_trigger_check
    CHECK (
      trigger_code IN (
        'INITIAL_ADMIN_SELECTION',
        'BGE_WARM_P95_SLA_FAILED_AND_VOYAGE_EVAL_PASSED',
        'ADMIN_ROLLBACK'
      )
    ),
  CONSTRAINT rag_embedding_policy_transitions_audit_check
    CHECK (char_length(approved_by_audit_ref) BETWEEN 16 AND 128),
  CONSTRAINT rag_embedding_policy_transitions_time_check CHECK (created_at >= approved_at),
  CONSTRAINT rag_embedding_policy_transitions_version_unique UNIQUE (state_id, to_version),
  CONSTRAINT rag_embedding_policy_transitions_target_generation_fkey
    FOREIGN KEY (target_generation_id, to_profile_id)
    REFERENCES rag_corpus_generations(corpus_generation_id, embedding_profile_id)
    ON DELETE RESTRICT
);

-- 인증된 public API에는 active generation이 실제로 참조하는 PUBLIC project card의 7개 필드만 반환한다.
CREATE FUNCTION read_rag_source_registry(p_actor_user_id text)
RETURNS TABLE (
  source_id text,
  title text,
  institution text,
  topic text,
  attribution text,
  canonical_url text,
  last_checked_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $read_rag_source_registry$
BEGIN
  IF p_actor_user_id IS NULL
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_actor_user_id
     OR NOT EXISTS (
       SELECT 1
       FROM public.users AS authenticated_user
       WHERE authenticated_user.user_id = p_actor_user_id
         AND authenticated_user.status = 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'RAG source projection actor mismatch' USING ERRCODE = '42501';
  END IF;

  RETURN QUERY
  WITH active_sources AS (
    SELECT DISTINCT ON (source.source_id)
      source.source_id,
      revision.title,
      source.institution,
      source.topic,
      revision.attribution,
      revision.canonical_url,
      latest_check.checked_at AS last_checked_at
    FROM public.rag_embedding_policy_state AS policy_state
    JOIN public.rag_corpus_generations AS generation
      ON generation.corpus_generation_id = policy_state.active_generation_id
     AND generation.embedding_profile_id = policy_state.effective_profile_id
     AND generation.status = 'ACTIVE'
     AND generation.evaluation_status = 'PASSED'
    JOIN public.rag_generation_chunks AS membership
      ON membership.corpus_generation_id = generation.corpus_generation_id
     AND membership.embedding_profile_id = generation.embedding_profile_id
    JOIN public.rag_chunk_revisions AS chunk
      ON chunk.chunk_revision_id = membership.chunk_revision_id
     AND chunk.access_level = 'PUBLIC'
    JOIN public.rag_source_revisions AS revision
      ON revision.source_revision_id = chunk.source_revision_id
     AND revision.access_level = 'PUBLIC'
    JOIN public.rag_sources AS source
      ON source.source_id = revision.source_id
     AND source.source_type = 'PROJECT_SOURCE_CARD'
     AND source.retired_at IS NULL
    LEFT JOIN LATERAL (
      SELECT source_check.checked_at
      FROM public.rag_source_checks AS source_check
      WHERE source_check.source_revision_id = revision.source_revision_id
      ORDER BY source_check.checked_at DESC, source_check.source_check_id DESC
      LIMIT 1
    ) AS latest_check ON true
    ORDER BY source.source_id, revision.revision_seq DESC
  )
  SELECT
    active_sources.source_id,
    active_sources.title,
    active_sources.institution,
    active_sources.topic,
    active_sources.attribution,
    active_sources.canonical_url,
    active_sources.last_checked_at
  FROM active_sources
  ORDER BY active_sources.source_id
  LIMIT 30;
END
$read_rag_source_registry$;
ALTER FUNCTION read_rag_source_registry(text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION read_rag_source_registry(text) FROM PUBLIC;

-- retrieval role도 raw table을 받지 않고 active/public generation의 bounded chunk projection만 실행한다.
CREATE FUNCTION read_active_rag_chunks(p_topic text, p_limit integer)
RETURNS TABLE (
  chunk_revision_id text,
  source_id text,
  title text,
  heading_path text[],
  canonical_content text,
  canonical_content_hash text
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $read_active_rag_chunks$
BEGIN
  IF p_limit IS NULL OR p_limit < 1 OR p_limit > 30
     OR p_topic IS NULL OR char_length(p_topic) < 1 OR char_length(p_topic) > 128 THEN
    RAISE EXCEPTION 'RAG retrieval projection bounds invalid' USING ERRCODE = '22023';
  END IF;

  RETURN QUERY
  SELECT
    chunk.chunk_revision_id,
    source.source_id,
    revision.title,
    chunk.heading_path,
    chunk.canonical_content,
    chunk.canonical_content_hash
  FROM public.rag_embedding_policy_state AS policy_state
  JOIN public.rag_corpus_generations AS generation
    ON generation.corpus_generation_id = policy_state.active_generation_id
   AND generation.embedding_profile_id = policy_state.effective_profile_id
   AND generation.status = 'ACTIVE'
   AND generation.evaluation_status = 'PASSED'
  JOIN public.rag_generation_chunks AS membership
    ON membership.corpus_generation_id = generation.corpus_generation_id
   AND membership.embedding_profile_id = generation.embedding_profile_id
  JOIN public.rag_chunk_revisions AS chunk
    ON chunk.chunk_revision_id = membership.chunk_revision_id
   AND chunk.access_level = 'PUBLIC'
   AND chunk.topic = p_topic
  JOIN public.rag_source_revisions AS revision
    ON revision.source_revision_id = chunk.source_revision_id
   AND revision.access_level = 'PUBLIC'
  JOIN public.rag_sources AS source
    ON source.source_id = revision.source_id
   AND source.source_type = 'PROJECT_SOURCE_CARD'
   AND source.retired_at IS NULL
  ORDER BY membership.ordinal
  LIMIT p_limit;
END
$read_active_rag_chunks$;
ALTER FUNCTION read_active_rag_chunks(text, integer) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION read_active_rag_chunks(text, integer) FROM PUBLIC;

REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;

DO $s4_rag_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      rag_sources,
      rag_source_revisions,
      rag_source_checks,
      rag_ingest_runs,
      rag_chunk_revisions,
      rag_corpus_generations,
      rag_generation_chunks,
      rag_chunk_embeddings,
      rag_embedding_policy_state,
      rag_embedding_policy_transitions,
      rag_sources_v2_legacy,
      rag_chunks_v2_legacy,
      rag_answers_v2_legacy,
      rag_citations_v2_legacy,
      rag_answer_feedback_v2_legacy
    FROM decision_app;
    REVOKE CREATE ON SCHEMA public FROM decision_app;
    GRANT EXECUTE ON FUNCTION read_rag_source_registry(text) TO decision_app;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_rag_writer;
    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_rag_writer;
    REVOKE CREATE ON SCHEMA public FROM decision_rag_writer;

    GRANT SELECT, INSERT ON TABLE
      rag_sources,
      rag_source_revisions,
      rag_source_checks,
      rag_ingest_runs,
      rag_chunk_revisions,
      rag_corpus_generations,
      rag_generation_chunks,
      rag_chunk_embeddings
    TO decision_rag_writer;
    GRANT UPDATE (status, started_at, completed_at, actual_chunk_count, failure_class)
      ON TABLE rag_ingest_runs TO decision_rag_writer;
    GRANT UPDATE (
      status,
      actual_chunk_count,
      evaluation_status,
      evaluated_at,
      activated_at,
      failed_at,
      disabled_at,
      failure_class
    )
      ON TABLE rag_corpus_generations TO decision_rag_writer;
    GRANT EXECUTE
      ON FUNCTION retire_rag_source_for_relocation(text, text)
      TO decision_rag_writer;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_query') THEN
    REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM decision_rag_query;
    REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM decision_rag_query;
    REVOKE CREATE ON SCHEMA public FROM decision_rag_query;
    GRANT EXECUTE ON FUNCTION read_active_rag_chunks(text, integer) TO decision_rag_query;
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_worker') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      rag_sources,
      rag_source_revisions,
      rag_source_checks,
      rag_ingest_runs,
      rag_chunk_revisions,
      rag_corpus_generations,
      rag_generation_chunks,
      rag_chunk_embeddings,
      rag_embedding_policy_state,
      rag_embedding_policy_transitions
    FROM decision_worker;
  END IF;
END
$s4_rag_acl$;

ALTER FUNCTION read_rag_source_registry(text) OWNER TO flyway;
ALTER FUNCTION read_active_rag_chunks(text, integer) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_generation_materialization() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_generation_transition() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_generation_activation() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_source_revision_locator() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION retire_rag_source_for_relocation(text, text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_ingest_run_transition() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_chunk_scope() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION read_rag_source_registry(text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION read_active_rag_chunks(text, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION read_rag_source_registry(text) TO decision_app;
GRANT EXECUTE ON FUNCTION read_active_rag_chunks(text, integer) TO decision_rag_query;
GRANT EXECUTE
  ON FUNCTION retire_rag_source_for_relocation(text, text)
  TO decision_rag_writer;
