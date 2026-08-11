-- 최초 적용된 V54 bytes를 보존하고, 이후 승인창 원자성·evaluation batch 보강을 forward-only로 적용한다.
-- 이미 provider batch를 stage한 환경은 자동 변환하지 않고 중단해 호출·billing evidence를 보존한다.
DO $pre_s5_voyage_v54_forward_guard$
BEGIN
  IF EXISTS (SELECT 1 FROM public.rag_v2_immutable_voyage_document_batch_plans)
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_voyage_document_batches)
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_voyage_document_batch_vectors) THEN
    RAISE EXCEPTION 'V54 Voyage batch state is non-empty; forward repair requires manual evidence-preserving migration'
      USING ERRCODE = '55000';
  END IF;
END
$pre_s5_voyage_v54_forward_guard$;

DROP FUNCTION public.stage_rag_v2_immutable_voyage_document_batch(jsonb);
DROP FUNCTION public.load_rag_v2_immutable_voyage_document_batch_vectors(text);
DROP TABLE public.rag_v2_immutable_voyage_document_batch_vectors;
DROP TABLE public.rag_v2_immutable_voyage_document_batches;
DROP TABLE public.rag_v2_immutable_voyage_document_batch_plans;

CREATE TABLE rag_v2_immutable_voyage_document_batch_plans (
  batch_plan_sha256 text PRIMARY KEY,
  embedding_profile_id text NOT NULL DEFAULT 'voyage_context_4_1024_v1',
  official_tokenizer_sha256 text NOT NULL,
  expected_source_count integer NOT NULL,
  expected_chunk_count integer NOT NULL,
  expected_token_count integer NOT NULL,
  expected_batch_count integer NOT NULL,
  owner_scope_sha256 text,
  owner_private_ordered_group_count integer NOT NULL,
  state text NOT NULL DEFAULT 'STAGING',
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  completed_at timestamptz,
  CONSTRAINT rag_v2_immutable_voyage_document_batch_plan_hash_check
    CHECK (batch_plan_sha256 ~ '^[0-9a-f]{64}$' AND official_tokenizer_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_v2_immutable_voyage_document_batch_plan_profile_check
    CHECK (embedding_profile_id = 'voyage_context_4_1024_v1'),
  CONSTRAINT rag_v2_immutable_voyage_document_batch_plan_count_check
    CHECK (
      expected_source_count = 142
      AND expected_chunk_count >= 142
      AND expected_token_count BETWEEN 1 AND 100000000
      AND expected_batch_count BETWEEN 1 AND 10000
    ),
  CONSTRAINT rag_v2_immutable_voyage_document_batch_plan_owner_sentinel_check
    CHECK (owner_scope_sha256 IS NULL AND owner_private_ordered_group_count = 0),
  CONSTRAINT rag_v2_immutable_voyage_document_batch_plan_state_check
    CHECK (
      (state = 'STAGING' AND completed_at IS NULL)
      OR (state = 'COMPLETE' AND completed_at IS NOT NULL)
      OR (state = 'FAILED' AND completed_at IS NOT NULL)
    )
);

CREATE TABLE rag_v2_immutable_voyage_document_batches (
  batch_id text PRIMARY KEY,
  batch_plan_sha256 text NOT NULL REFERENCES rag_v2_immutable_voyage_document_batch_plans(batch_plan_sha256),
  batch_manifest_sha256 text NOT NULL,
  batch_ordinal integer NOT NULL,
  batch_count integer NOT NULL,
  expected_token_count integer NOT NULL,
  expected_chunk_count integer NOT NULL,
  expected_group_count integer NOT NULL,
  expected_response_bytes integer NOT NULL,
  packet_sha256 text NOT NULL,
  vector_set_sha256 text NOT NULL,
  provider_physical_call_count integer NOT NULL,
  state text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  committed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_voyage_document_batch_id_check
    CHECK (batch_id ~ '^ps5_voyage_doc_[0-9]{4}_[0-9a-f]{16}$'),
  CONSTRAINT rag_v2_immutable_voyage_document_batch_hash_check
    CHECK (
      batch_manifest_sha256 ~ '^[0-9a-f]{64}$'
      AND packet_sha256 ~ '^[0-9a-f]{64}$'
      AND vector_set_sha256 ~ '^[0-9a-f]{64}$'
    ),
  CONSTRAINT rag_v2_immutable_voyage_document_batch_count_check
    CHECK (
      batch_count BETWEEN 1 AND 10000
      AND batch_ordinal BETWEEN 1 AND batch_count
      AND expected_token_count BETWEEN 1 AND 110000
      AND expected_chunk_count BETWEEN 1 AND 672
      AND expected_group_count BETWEEN 1 AND 1000
      AND expected_response_bytes = 262144 + expected_chunk_count * 24576
      AND expected_response_bytes BETWEEN 286720 AND 16777216
      AND provider_physical_call_count = 1
      AND state = 'COMMITTED'
    ),
  CONSTRAINT rag_v2_immutable_voyage_document_batch_plan_ordinal_unique
    UNIQUE (batch_plan_sha256, batch_ordinal),
  CONSTRAINT rag_v2_immutable_voyage_document_batch_plan_manifest_unique
    UNIQUE (batch_plan_sha256, batch_manifest_sha256),
  CONSTRAINT rag_v2_immutable_voyage_document_batch_plan_id_unique
    UNIQUE (batch_plan_sha256, batch_id)
);

CREATE TABLE rag_v2_immutable_voyage_document_batch_vectors (
  batch_plan_sha256 text NOT NULL,
  batch_id text NOT NULL,
  component_scope text NOT NULL,
  source_id text NOT NULL,
  source_revision_id text NOT NULL,
  chunk_id text NOT NULL,
  embedding_input_hash text NOT NULL,
  context_set_hash text NOT NULL,
  vector_sha256 text NOT NULL,
  embedding vector(1024) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  PRIMARY KEY (batch_plan_sha256, chunk_id),
  CONSTRAINT rag_v2_immutable_voyage_document_batch_vector_plan_fkey
    FOREIGN KEY (batch_plan_sha256) REFERENCES rag_v2_immutable_voyage_document_batch_plans(batch_plan_sha256),
  CONSTRAINT rag_v2_immutable_voyage_document_batch_vector_batch_fkey
    FOREIGN KEY (batch_plan_sha256, batch_id)
    REFERENCES rag_v2_immutable_voyage_document_batches(batch_plan_sha256, batch_id),
  CONSTRAINT rag_v2_immutable_voyage_document_batch_vector_scope_check
    CHECK (component_scope IN ('EXACT30', 'OA112')),
  CONSTRAINT rag_v2_immutable_voyage_document_batch_vector_identity_check
    CHECK (
      source_id ~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'
      AND source_revision_id ~ '^srv_[a-z0-9][a-z0-9_-]{2,95}$'
      AND chunk_id ~ '^rag_v2_chk_[0-9a-f]{32}$'
      AND embedding_input_hash ~ '^[0-9a-f]{64}$'
      AND context_set_hash ~ '^[0-9a-f]{64}$'
      AND vector_sha256 ~ '^[0-9a-f]{64}$'
      AND vector_dims(embedding) = 1024
      AND vector_norm(embedding) BETWEEN 0.99999 AND 1.00001
    )
);

CREATE TABLE rag_v2_immutable_voyage_document_batch_attempts (
  batch_plan_sha256 text NOT NULL,
  batch_id text NOT NULL,
  batch_manifest_sha256 text NOT NULL,
  usage_event_id text NOT NULL UNIQUE,
  packet_sha256 text NOT NULL,
  state text NOT NULL DEFAULT 'CLAIMED',
  claimed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  terminal_at timestamptz,
  PRIMARY KEY (batch_plan_sha256, batch_id),
  CONSTRAINT rag_v2_immutable_voyage_document_batch_attempt_identity_check
    CHECK (
      batch_plan_sha256 ~ '^[0-9a-f]{64}$'
      AND batch_id ~ '^ps5_voyage_doc_[0-9]{4}_[0-9a-f]{16}$'
      AND batch_manifest_sha256 ~ '^[0-9a-f]{64}$'
      AND usage_event_id ~ '^rgr_vou_[0-9a-f]{32}$'
      AND packet_sha256 ~ '^[0-9a-f]{64}$'
    ),
  CONSTRAINT rag_v2_immutable_voyage_document_batch_attempt_state_check
    CHECK (
      (state = 'CLAIMED' AND terminal_at IS NULL)
      OR (state IN ('COMMITTED','UNKNOWN_BILLING') AND terminal_at IS NOT NULL)
    )
);

CREATE TABLE rag_v2_immutable_voyage_evaluation_batch_attempts (
  scope_claim_sha256 text NOT NULL,
  component_scope text NOT NULL,
  query_manifest_sha256 text NOT NULL,
  usage_event_id text NOT NULL UNIQUE,
  packet_sha256 text NOT NULL,
  state text NOT NULL DEFAULT 'CLAIMED',
  claimed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  terminal_at timestamptz,
  PRIMARY KEY (scope_claim_sha256, component_scope),
  CONSTRAINT rag_v2_immutable_voyage_evaluation_batch_attempt_identity_check
    CHECK (
      scope_claim_sha256 ~ '^[0-9a-f]{64}$'
      AND component_scope IN ('EXACT30','OA112')
      AND query_manifest_sha256 ~ '^[0-9a-f]{64}$'
      AND usage_event_id ~ '^rgr_vqu_[0-9a-f]{32}$'
      AND packet_sha256 ~ '^[0-9a-f]{64}$'
    ),
  CONSTRAINT rag_v2_immutable_voyage_evaluation_batch_attempt_state_check
    CHECK (
      (state = 'CLAIMED' AND terminal_at IS NULL)
      OR (state IN ('COMMITTED','UNKNOWN_BILLING') AND terminal_at IS NOT NULL)
    ),
  CONSTRAINT rag_v2_immutable_voyage_evaluation_batch_attempt_manifest_unique
    UNIQUE (scope_claim_sha256, component_scope, query_manifest_sha256)
);

CREATE TABLE rag_v2_immutable_voyage_evaluation_batch_vectors (
  scope_claim_sha256 text NOT NULL,
  component_scope text NOT NULL,
  query_manifest_sha256 text NOT NULL,
  query_sha256 text NOT NULL,
  vector_sha256 text NOT NULL,
  embedding vector(1024) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  PRIMARY KEY (scope_claim_sha256, component_scope, query_sha256),
  CONSTRAINT rag_v2_immutable_voyage_evaluation_batch_vector_attempt_fkey
    FOREIGN KEY (scope_claim_sha256, component_scope, query_manifest_sha256)
    REFERENCES rag_v2_immutable_voyage_evaluation_batch_attempts(
      scope_claim_sha256, component_scope, query_manifest_sha256
    ),
  CONSTRAINT rag_v2_immutable_voyage_evaluation_batch_vector_identity_check
    CHECK (
      scope_claim_sha256 ~ '^[0-9a-f]{64}$'
      AND component_scope IN ('EXACT30','OA112')
      AND query_manifest_sha256 ~ '^[0-9a-f]{64}$'
      AND query_sha256 ~ '^[0-9a-f]{64}$'
      AND vector_sha256 ~ '^[0-9a-f]{64}$'
      AND vector_dims(embedding) = 1024
      AND vector_norm(embedding) BETWEEN 0.99999 AND 1.00001
    )
);

ALTER TABLE rag_v2_immutable_voyage_document_batch_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_voyage_document_batch_plans FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_voyage_document_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_voyage_document_batches FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_voyage_document_batch_vectors ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_voyage_document_batch_vectors FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_voyage_document_batch_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_voyage_document_batch_attempts FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_voyage_evaluation_batch_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_voyage_evaluation_batch_attempts FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_voyage_evaluation_batch_vectors ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_voyage_evaluation_batch_vectors FORCE ROW LEVEL SECURITY;
-- V51의 기존 full-bundle usage reservation은 4 MiB 상한을 그대로 보존한다. 1024차원
-- document batch 응답만 16 MiB까지 받을 수 있도록 별도 capability를 만들고 같은 append-only
-- usage ledger와 commit/unknown transition을 재사용한다.
ALTER TABLE rag_v2_immutable_voyage_usage_reservations
  DROP CONSTRAINT rag_v2_immutable_voyage_usage_reservation_cap_check;
ALTER TABLE rag_v2_immutable_voyage_usage_reservations
  ADD CONSTRAINT rag_v2_immutable_voyage_usage_reservation_cap_check
  CHECK (
    token_cap BETWEEN 1 AND 120000
    AND byte_cap BETWEEN 1 AND 16777216
    AND cost_cap_microusd BETWEEN 1 AND 1000000000
    AND input_microusd_per_token BETWEEN 1 AND 1000000
    AND token_cap::bigint * input_microusd_per_token <= cost_cap_microusd
  );
CREATE FUNCTION reserve_rag_v2_immutable_voyage_document_batch_usage(
  p_usage_event_id text,
  p_packet_sha256 text,
  p_nonce_sha256 text,
  p_batch_manifest_sha256 text,
  p_rate_evidence_sha256 text,
  p_official_tokenizer_sha256 text,
  p_expires_at timestamptz,
  p_token_cap integer,
  p_byte_cap integer,
  p_cost_cap_microusd bigint,
  p_input_microusd_per_token bigint
)
RETURNS TABLE (
  usage_event_id text,
  expires_at timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $reserve_rag_v2_immutable_voyage_document_batch_usage$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_usage_event_id IS NULL OR p_usage_event_id !~ '^rgr_vou_[0-9a-f]{32}$'
     OR p_packet_sha256 IS NULL OR p_packet_sha256 !~ '^[0-9a-f]{64}$'
     OR p_nonce_sha256 IS NULL OR p_nonce_sha256 !~ '^[0-9a-f]{64}$'
     OR p_batch_manifest_sha256 IS NULL OR p_batch_manifest_sha256 !~ '^[0-9a-f]{64}$'
     OR p_rate_evidence_sha256 IS NULL OR p_rate_evidence_sha256 !~ '^[0-9a-f]{64}$'
     OR p_official_tokenizer_sha256 IS NULL OR p_official_tokenizer_sha256 !~ '^[0-9a-f]{64}$'
     OR p_expires_at IS NULL OR p_expires_at <= statement_timestamp()
     OR p_expires_at > statement_timestamp() + interval '2 hours'
     OR p_token_cap IS NULL OR p_token_cap NOT BETWEEN 1 AND 110000
     OR p_byte_cap IS NULL OR p_byte_cap NOT BETWEEN 1 AND 16777216
     OR p_cost_cap_microusd IS NULL OR p_cost_cap_microusd NOT BETWEEN 1 AND 1000000000
     OR p_input_microusd_per_token IS NULL OR p_input_microusd_per_token NOT BETWEEN 1 AND 1000000
     OR p_token_cap::bigint * p_input_microusd_per_token > p_cost_cap_microusd THEN
    RAISE EXCEPTION 'immutable Pre-S5 Voyage document batch usage arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'rag-v2-immutable-voyage-document-batch-usage-reservation|' || p_packet_sha256,
      0
    )
  );
  INSERT INTO public.rag_v2_immutable_voyage_usage_reservations (
    usage_event_id, packet_sha256, nonce_sha256, bundle_manifest_sha256, rate_evidence_sha256,
    official_tokenizer_sha256, expires_at, token_cap, byte_cap, cost_cap_microusd,
    input_microusd_per_token
  ) VALUES (
    p_usage_event_id, p_packet_sha256, p_nonce_sha256, p_batch_manifest_sha256,
    p_rate_evidence_sha256, p_official_tokenizer_sha256, p_expires_at, p_token_cap,
    p_byte_cap, p_cost_cap_microusd, p_input_microusd_per_token
  );
  RETURN QUERY SELECT p_usage_event_id, p_expires_at;
END
$reserve_rag_v2_immutable_voyage_document_batch_usage$;
ALTER FUNCTION reserve_rag_v2_immutable_voyage_document_batch_usage(
  text, text, text, text, text, text, timestamptz, integer, integer, bigint, bigint
) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION reserve_rag_v2_immutable_voyage_document_batch_usage(
  text, text, text, text, text, text, timestamptz, integer, integer, bigint, bigint
) FROM PUBLIC;

CREATE POLICY rag_v2_immutable_voyage_document_batch_plans_flyway
  ON rag_v2_immutable_voyage_document_batch_plans FOR ALL TO flyway USING (true) WITH CHECK (true);
CREATE POLICY rag_v2_immutable_voyage_document_batches_flyway
  ON rag_v2_immutable_voyage_document_batches FOR ALL TO flyway USING (true) WITH CHECK (true);
CREATE POLICY rag_v2_immutable_voyage_document_batch_vectors_flyway
  ON rag_v2_immutable_voyage_document_batch_vectors FOR ALL TO flyway USING (true) WITH CHECK (true);
CREATE POLICY rag_v2_immutable_voyage_document_batch_attempts_flyway
  ON rag_v2_immutable_voyage_document_batch_attempts FOR ALL TO flyway USING (true) WITH CHECK (true);
CREATE POLICY rag_v2_immutable_voyage_evaluation_batch_attempts_flyway
  ON rag_v2_immutable_voyage_evaluation_batch_attempts FOR ALL TO flyway USING (true) WITH CHECK (true);
CREATE POLICY rag_v2_immutable_voyage_evaluation_batch_vectors_flyway
  ON rag_v2_immutable_voyage_evaluation_batch_vectors FOR ALL TO flyway USING (true) WITH CHECK (true);
CREATE FUNCTION claim_rag_v2_immutable_voyage_document_batch_attempt(
  p_usage_event_id text,
  p_batch_plan_sha256 text,
  p_batch_id text,
  p_batch_manifest_sha256 text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $claim_rag_v2_immutable_voyage_document_batch_attempt$
DECLARE
  reserved_packet_sha256 text;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_usage_event_id !~ '^rgr_vou_[0-9a-f]{32}$'
     OR p_batch_plan_sha256 !~ '^[0-9a-f]{64}$'
     OR p_batch_id !~ '^ps5_voyage_doc_[0-9]{4}_[0-9a-f]{16}$'
     OR p_batch_manifest_sha256 !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch claim is invalid' USING ERRCODE = '22023';
  END IF;
  SELECT reservation.packet_sha256 INTO reserved_packet_sha256
  FROM public.rag_v2_immutable_voyage_usage_reservations AS reservation
  WHERE reservation.usage_event_id = p_usage_event_id
    AND reservation.bundle_manifest_sha256 = p_batch_manifest_sha256
    AND reservation.expires_at > statement_timestamp();
  IF reserved_packet_sha256 IS NULL THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch reservation is absent' USING ERRCODE = '55000';
  END IF;
  PERFORM public.claim_rag_v2_immutable_voyage_usage_attempt(p_usage_event_id);
  INSERT INTO public.rag_v2_immutable_voyage_document_batch_attempts (
    batch_plan_sha256, batch_id, batch_manifest_sha256, usage_event_id, packet_sha256
  ) VALUES (
    p_batch_plan_sha256, p_batch_id, p_batch_manifest_sha256, p_usage_event_id,
    reserved_packet_sha256
  );
END
$claim_rag_v2_immutable_voyage_document_batch_attempt$;
ALTER FUNCTION claim_rag_v2_immutable_voyage_document_batch_attempt(text, text, text, text)
  OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION
  claim_rag_v2_immutable_voyage_document_batch_attempt(text, text, text, text) FROM PUBLIC;

CREATE FUNCTION mark_rag_v2_immutable_voyage_document_batch_unknown_billing(
  p_usage_event_id text,
  p_batch_plan_sha256 text,
  p_batch_id text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $mark_rag_v2_immutable_voyage_document_batch_unknown_billing$
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_rag_writer' THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch unknown outcome is unavailable'
      USING ERRCODE = '55000';
  END IF;
  PERFORM public.mark_rag_v2_immutable_voyage_usage_unknown_billing(p_usage_event_id);
  UPDATE public.rag_v2_immutable_voyage_document_batch_attempts
  SET state = 'UNKNOWN_BILLING', terminal_at = transaction_timestamp()
  WHERE usage_event_id = p_usage_event_id
    AND batch_plan_sha256 = p_batch_plan_sha256
    AND batch_id = p_batch_id
    AND state = 'CLAIMED';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch unknown outcome conflicts'
      USING ERRCODE = '55000';
  END IF;
END
$mark_rag_v2_immutable_voyage_document_batch_unknown_billing$;
ALTER FUNCTION mark_rag_v2_immutable_voyage_document_batch_unknown_billing(text, text, text)
  OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION
  mark_rag_v2_immutable_voyage_document_batch_unknown_billing(text, text, text) FROM PUBLIC;

CREATE FUNCTION stage_rag_v2_immutable_voyage_document_batch(p_payload jsonb)
RETURNS TABLE (
  batch_plan_sha256 text,
  batch_id text,
  state text,
  batch_reused boolean,
  completed_batch_count integer,
  staged_vector_count integer
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $stage_rag_v2_immutable_voyage_document_batch$
DECLARE
  payload_plan jsonb;
  payload_batch jsonb;
  payload_vectors jsonb;
  item jsonb;
  observed_vector_count integer;
  payload_vector_set_sha256 text;
  existing_batch public.rag_v2_immutable_voyage_document_batches%ROWTYPE;
  reused boolean := false;
  complete_count integer;
  vector_count integer;
  aggregate_token_count bigint;
  aggregate_chunk_count bigint;
  aggregate_group_count bigint;
  observed_group_count bigint;
  observed_source_count integer;
  distinct_ordinal_count integer;
  minimum_ordinal integer;
  maximum_ordinal integer;
  minimum_batch_count integer;
  maximum_batch_count integer;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_payload IS NULL
     OR jsonb_typeof(p_payload) <> 'object'
     OR (SELECT count(*) FROM jsonb_object_keys(p_payload)) <> 5
     OR NOT (p_payload ?& ARRAY['schemaVersion','plan','batch','packetSha256','vectors'])
     OR p_payload ->> 'schemaVersion' <> 'pre-s5-voyage-document-batch-stage/v1'
     OR jsonb_typeof(p_payload -> 'plan') <> 'object'
     OR jsonb_typeof(p_payload -> 'batch') <> 'object'
     OR jsonb_typeof(p_payload -> 'vectors') <> 'array' THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch stage payload is invalid' USING ERRCODE = '22023';
  END IF;
  payload_plan := p_payload -> 'plan';
  payload_batch := p_payload -> 'batch';
  payload_vectors := p_payload -> 'vectors';
  IF (SELECT count(*) FROM jsonb_object_keys(payload_plan)) <> 8
     OR NOT (payload_plan ?& ARRAY[
       'batchPlanSha256','officialTokenizerSha256','sourceCount','chunkCount','tokenCount',
       'batchCount','ownerScopeSha256','ownerPrivateOrderedGroupCount'
     ])
     OR payload_plan ->> 'batchPlanSha256' !~ '^[0-9a-f]{64}$'
     OR payload_plan ->> 'officialTokenizerSha256' !~ '^[0-9a-f]{64}$'
     OR (payload_plan ->> 'sourceCount')::integer <> 142
     OR (payload_plan ->> 'chunkCount')::integer < 142
     OR (payload_plan ->> 'tokenCount')::integer NOT BETWEEN 1 AND 100000000
     OR (payload_plan ->> 'batchCount')::integer NOT BETWEEN 1 AND 10000
     OR payload_plan -> 'ownerScopeSha256' <> 'null'::jsonb
     OR (payload_plan ->> 'ownerPrivateOrderedGroupCount')::integer <> 0 THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch plan is invalid' USING ERRCODE = '22023';
  END IF;
  IF (SELECT count(*) FROM jsonb_object_keys(payload_batch)) <> 9
     OR NOT (payload_batch ?& ARRAY[
       'batchId','batchManifestSha256','batchOrdinal','batchCount','tokenCount','chunkCount',
       'groupCount','estimatedResponseBytes','vectorSetSha256'
     ])
     OR payload_batch ->> 'batchId' !~ '^ps5_voyage_doc_[0-9]{4}_[0-9a-f]{16}$'
     OR payload_batch ->> 'batchManifestSha256' !~ '^[0-9a-f]{64}$'
     OR payload_batch ->> 'vectorSetSha256' !~ '^[0-9a-f]{64}$'
     OR (payload_batch ->> 'batchCount')::integer <> (payload_plan ->> 'batchCount')::integer
     OR (payload_batch ->> 'batchOrdinal')::integer NOT BETWEEN 1 AND (payload_plan ->> 'batchCount')::integer
     OR (payload_batch ->> 'tokenCount')::integer NOT BETWEEN 1 AND 110000
     OR (payload_batch ->> 'chunkCount')::integer NOT BETWEEN 1 AND 672
     OR (payload_batch ->> 'groupCount')::integer NOT BETWEEN 1 AND 1000
     OR (payload_batch ->> 'estimatedResponseBytes')::integer
        <> 262144 + (payload_batch ->> 'chunkCount')::integer * 24576
     OR (payload_batch ->> 'estimatedResponseBytes')::integer NOT BETWEEN 286720 AND 16777216
     OR p_payload ->> 'packetSha256' !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch identity is invalid' USING ERRCODE = '22023';
  END IF;
  observed_vector_count := jsonb_array_length(payload_vectors);
  IF observed_vector_count <> (payload_batch ->> 'chunkCount')::integer
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements(payload_vectors) AS vector_row(value)
       WHERE jsonb_typeof(vector_row.value) <> 'object'
          OR (SELECT count(*) FROM jsonb_object_keys(vector_row.value)) <> 9
          OR NOT (vector_row.value ?& ARRAY[
            'componentScope','sourceId','sourceRevisionId','chunkId','embeddingInputHash',
            'contextSetHash','vectorSha256','vector','chunkOrdinal'
          ])
          OR vector_row.value ->> 'componentScope' NOT IN ('EXACT30','OA112')
          OR vector_row.value ->> 'sourceId' !~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'
          OR vector_row.value ->> 'sourceRevisionId' !~ '^srv_[a-z0-9][a-z0-9_-]{2,95}$'
          OR vector_row.value ->> 'chunkId' !~ '^rag_v2_chk_[0-9a-f]{32}$'
          OR vector_row.value ->> 'embeddingInputHash' !~ '^[0-9a-f]{64}$'
          OR vector_row.value ->> 'contextSetHash' !~ '^[0-9a-f]{64}$'
          OR vector_row.value ->> 'vectorSha256' !~ '^[0-9a-f]{64}$'
          OR jsonb_typeof(vector_row.value -> 'vector') <> 'array'
          OR jsonb_array_length(vector_row.value -> 'vector') <> 1024
          OR (vector_row.value ->> 'chunkOrdinal')::integer < 1
     )
     OR observed_vector_count <> (
       SELECT count(DISTINCT vector_row.value ->> 'chunkId')
       FROM jsonb_array_elements(payload_vectors) AS vector_row(value)
     ) THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch vectors are invalid' USING ERRCODE = '22023';
  END IF;
  payload_vector_set_sha256 := payload_batch ->> 'vectorSetSha256';
  IF NOT EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_voyage_usage_reservations AS reservation
    JOIN public.rag_v2_immutable_voyage_usage_outcomes AS outcome
      ON outcome.usage_event_id = reservation.usage_event_id
     AND outcome.packet_sha256 = reservation.packet_sha256
     AND outcome.state = 'COMMITTED'
    WHERE reservation.packet_sha256 = p_payload ->> 'packetSha256'
      AND reservation.bundle_manifest_sha256 = payload_batch ->> 'batchManifestSha256'
      AND reservation.official_tokenizer_sha256 = payload_plan ->> 'officialTokenizerSha256'
      AND reservation.byte_cap >= (payload_batch ->> 'estimatedResponseBytes')::integer
      AND outcome.expected_input_tokens = (payload_batch ->> 'tokenCount')::integer
  ) THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch usage outcome is absent' USING ERRCODE = '55000';
  END IF;

  INSERT INTO public.rag_v2_immutable_voyage_document_batch_plans (
    batch_plan_sha256, official_tokenizer_sha256, expected_source_count, expected_chunk_count,
    expected_token_count, expected_batch_count, owner_scope_sha256, owner_private_ordered_group_count
  ) VALUES (
    payload_plan ->> 'batchPlanSha256', payload_plan ->> 'officialTokenizerSha256',
    (payload_plan ->> 'sourceCount')::integer, (payload_plan ->> 'chunkCount')::integer,
    (payload_plan ->> 'tokenCount')::integer, (payload_plan ->> 'batchCount')::integer,
    NULL, (payload_plan ->> 'ownerPrivateOrderedGroupCount')::integer
  ) ON CONFLICT ON CONSTRAINT rag_v2_immutable_voyage_document_batch_plans_pkey DO NOTHING;
  IF NOT EXISTS (
    SELECT 1 FROM public.rag_v2_immutable_voyage_document_batch_plans AS plan
    WHERE plan.batch_plan_sha256 = payload_plan ->> 'batchPlanSha256'
      AND plan.official_tokenizer_sha256 = payload_plan ->> 'officialTokenizerSha256'
      AND plan.expected_source_count = (payload_plan ->> 'sourceCount')::integer
      AND plan.expected_chunk_count = (payload_plan ->> 'chunkCount')::integer
      AND plan.expected_token_count = (payload_plan ->> 'tokenCount')::integer
      AND plan.expected_batch_count = (payload_plan ->> 'batchCount')::integer
      AND plan.owner_scope_sha256 IS NULL
      AND plan.owner_private_ordered_group_count = 0
      AND plan.state IN ('STAGING','COMPLETE')
  ) THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch plan conflicts' USING ERRCODE = '23505';
  END IF;
  SELECT * INTO existing_batch
  FROM public.rag_v2_immutable_voyage_document_batches AS selected_batch
  WHERE selected_batch.batch_id = payload_batch ->> 'batchId';
  IF FOUND THEN
    IF existing_batch.batch_plan_sha256 <> payload_plan ->> 'batchPlanSha256'
       OR existing_batch.batch_manifest_sha256 <> payload_batch ->> 'batchManifestSha256'
       OR existing_batch.batch_ordinal <> (payload_batch ->> 'batchOrdinal')::integer
       OR existing_batch.batch_count <> (payload_batch ->> 'batchCount')::integer
       OR existing_batch.expected_token_count <> (payload_batch ->> 'tokenCount')::integer
       OR existing_batch.expected_chunk_count <> observed_vector_count
       OR existing_batch.expected_group_count <> (payload_batch ->> 'groupCount')::integer
       OR existing_batch.expected_response_bytes
          <> (payload_batch ->> 'estimatedResponseBytes')::integer
       OR existing_batch.packet_sha256 <> p_payload ->> 'packetSha256'
       OR existing_batch.vector_set_sha256 <> payload_vector_set_sha256
       OR existing_batch.state <> 'COMMITTED'
       OR (SELECT count(*) FROM public.rag_v2_immutable_voyage_document_batch_vectors AS vector_row
           WHERE vector_row.batch_id = existing_batch.batch_id) <> observed_vector_count THEN
      RAISE EXCEPTION 'Pre-S5 Voyage document batch conflicts' USING ERRCODE = '23505';
    END IF;
    reused := true;
  ELSE
    INSERT INTO public.rag_v2_immutable_voyage_document_batches (
      batch_id, batch_plan_sha256, batch_manifest_sha256, batch_ordinal, batch_count,
      expected_token_count, expected_chunk_count, expected_group_count, expected_response_bytes, packet_sha256,
      vector_set_sha256, provider_physical_call_count, state
    ) VALUES (
      payload_batch ->> 'batchId', payload_plan ->> 'batchPlanSha256',
      payload_batch ->> 'batchManifestSha256', (payload_batch ->> 'batchOrdinal')::integer,
      (payload_batch ->> 'batchCount')::integer, (payload_batch ->> 'tokenCount')::integer,
      observed_vector_count, (payload_batch ->> 'groupCount')::integer,
      (payload_batch ->> 'estimatedResponseBytes')::integer,
      p_payload ->> 'packetSha256', payload_vector_set_sha256, 1, 'COMMITTED'
    );
    FOR item IN SELECT value FROM jsonb_array_elements(payload_vectors) AS vector_row(value) LOOP
      INSERT INTO public.rag_v2_immutable_voyage_document_batch_vectors (
        batch_plan_sha256, batch_id, component_scope, source_id, source_revision_id, chunk_id,
        embedding_input_hash, context_set_hash, vector_sha256, embedding
      ) VALUES (
        payload_plan ->> 'batchPlanSha256', payload_batch ->> 'batchId', item ->> 'componentScope',
        item ->> 'sourceId', item ->> 'sourceRevisionId', item ->> 'chunkId',
        item ->> 'embeddingInputHash', item ->> 'contextSetHash', item ->> 'vectorSha256',
        (item -> 'vector')::text::vector(1024)
      );
    END LOOP;
  END IF;
  SELECT count(*) INTO complete_count
  FROM public.rag_v2_immutable_voyage_document_batches AS batch
  WHERE batch.batch_plan_sha256 = payload_plan ->> 'batchPlanSha256' AND batch.state = 'COMMITTED';
  SELECT count(*) INTO vector_count
  FROM public.rag_v2_immutable_voyage_document_batch_vectors AS vector_row
  WHERE vector_row.batch_plan_sha256 = payload_plan ->> 'batchPlanSha256';
  SELECT
    COALESCE(sum(batch.expected_token_count), 0),
    COALESCE(sum(batch.expected_chunk_count), 0),
    COALESCE(sum(batch.expected_group_count), 0),
    count(DISTINCT batch.batch_ordinal),
    min(batch.batch_ordinal),
    max(batch.batch_ordinal),
    min(batch.batch_count),
    max(batch.batch_count)
  INTO
    aggregate_token_count,
    aggregate_chunk_count,
    aggregate_group_count,
    distinct_ordinal_count,
    minimum_ordinal,
    maximum_ordinal,
    minimum_batch_count,
    maximum_batch_count
  FROM public.rag_v2_immutable_voyage_document_batches AS batch
  WHERE batch.batch_plan_sha256 = payload_plan ->> 'batchPlanSha256'
    AND batch.state = 'COMMITTED';
  SELECT
    count(DISTINCT (vector_row.batch_id, vector_row.source_id)),
    count(DISTINCT vector_row.source_id)
  INTO observed_group_count, observed_source_count
  FROM public.rag_v2_immutable_voyage_document_batch_vectors AS vector_row
  WHERE vector_row.batch_plan_sha256 = payload_plan ->> 'batchPlanSha256';
  IF complete_count = (payload_plan ->> 'batchCount')::integer THEN
    IF vector_count <> (payload_plan ->> 'chunkCount')::integer
       OR aggregate_token_count <> (payload_plan ->> 'tokenCount')::integer
       OR aggregate_chunk_count <> (payload_plan ->> 'chunkCount')::integer
       OR aggregate_group_count <> observed_group_count
       OR observed_source_count <> (payload_plan ->> 'sourceCount')::integer
       OR distinct_ordinal_count <> complete_count
       OR minimum_ordinal <> 1
       OR maximum_ordinal <> complete_count
       OR minimum_batch_count <> complete_count
       OR maximum_batch_count <> complete_count THEN
      RAISE EXCEPTION 'Pre-S5 Voyage document batch completion conflicts' USING ERRCODE = '23505';
    END IF;
    UPDATE public.rag_v2_immutable_voyage_document_batch_plans
    SET state = 'COMPLETE', completed_at = COALESCE(completed_at, transaction_timestamp())
    WHERE rag_v2_immutable_voyage_document_batch_plans.batch_plan_sha256 = payload_plan ->> 'batchPlanSha256'
      AND rag_v2_immutable_voyage_document_batch_plans.state = 'STAGING';
  END IF;
  RETURN QUERY SELECT
    payload_plan ->> 'batchPlanSha256', payload_batch ->> 'batchId',
    (SELECT plan.state FROM public.rag_v2_immutable_voyage_document_batch_plans AS plan
     WHERE plan.batch_plan_sha256 = payload_plan ->> 'batchPlanSha256'),
    reused, complete_count, vector_count;
END
$stage_rag_v2_immutable_voyage_document_batch$;
ALTER FUNCTION stage_rag_v2_immutable_voyage_document_batch(jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION stage_rag_v2_immutable_voyage_document_batch(jsonb) FROM PUBLIC;

CREATE FUNCTION commit_and_stage_rag_v2_immutable_voyage_document_batch(p_payload jsonb)
RETURNS TABLE (
  batch_plan_sha256 text,
  batch_id text,
  state text,
  batch_reused boolean,
  completed_batch_count integer,
  staged_vector_count integer
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $commit_and_stage_rag_v2_immutable_voyage_document_batch$
DECLARE
  usage_payload jsonb;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_payload IS NULL
     OR jsonb_typeof(p_payload) <> 'object'
     OR (SELECT count(*) FROM jsonb_object_keys(p_payload)) <> 6
     OR NOT (p_payload ?& ARRAY['schemaVersion','plan','batch','packetSha256','vectors','usage'])
     OR jsonb_typeof(p_payload -> 'usage') <> 'object' THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch atomic payload is invalid'
      USING ERRCODE = '22023';
  END IF;
  usage_payload := p_payload -> 'usage';
  IF (SELECT count(*) FROM jsonb_object_keys(usage_payload)) <> 4
     OR NOT (usage_payload ?& ARRAY[
       'usageEventId','expectedInputTokens','providerTotalTokens','actualCostMicrousd'
     ])
     OR usage_payload ->> 'usageEventId' !~ '^rgr_vou_[0-9a-f]{32}$'
     OR (usage_payload ->> 'expectedInputTokens')::integer
        <> (p_payload -> 'batch' ->> 'tokenCount')::integer
     OR (usage_payload ->> 'providerTotalTokens')::integer NOT BETWEEN 0 AND 110000
     OR (usage_payload ->> 'actualCostMicrousd')::bigint NOT BETWEEN 0 AND 1000000000 THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch usage payload is invalid'
      USING ERRCODE = '22023';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_voyage_document_batch_attempts AS attempt
    WHERE attempt.usage_event_id = usage_payload ->> 'usageEventId'
      AND attempt.batch_plan_sha256 = p_payload -> 'plan' ->> 'batchPlanSha256'
      AND attempt.batch_id = p_payload -> 'batch' ->> 'batchId'
      AND attempt.batch_manifest_sha256 = p_payload -> 'batch' ->> 'batchManifestSha256'
      AND attempt.packet_sha256 = p_payload ->> 'packetSha256'
      AND attempt.state = 'CLAIMED'
  ) THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch attempt is absent' USING ERRCODE = '55000';
  END IF;
  PERFORM public.commit_rag_v2_immutable_voyage_usage_with_tokenizer(
    usage_payload ->> 'usageEventId',
    (usage_payload ->> 'expectedInputTokens')::integer,
    (usage_payload ->> 'providerTotalTokens')::integer,
    (usage_payload ->> 'actualCostMicrousd')::bigint
  );
  RETURN QUERY
  SELECT staged.batch_plan_sha256, staged.batch_id, staged.state, staged.batch_reused,
         staged.completed_batch_count, staged.staged_vector_count
  FROM public.stage_rag_v2_immutable_voyage_document_batch(p_payload - 'usage') AS staged;
  UPDATE public.rag_v2_immutable_voyage_document_batch_attempts AS attempt
  SET state = 'COMMITTED', terminal_at = transaction_timestamp()
  WHERE attempt.usage_event_id = usage_payload ->> 'usageEventId'
    AND attempt.state = 'CLAIMED';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch attempt commit conflicts'
      USING ERRCODE = '55000';
  END IF;
END
$commit_and_stage_rag_v2_immutable_voyage_document_batch$;
ALTER FUNCTION commit_and_stage_rag_v2_immutable_voyage_document_batch(jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION
  commit_and_stage_rag_v2_immutable_voyage_document_batch(jsonb) FROM PUBLIC;

CREATE FUNCTION load_rag_v2_immutable_voyage_document_batch_vectors(p_batch_plan_sha256 text)
RETURNS TABLE (
  batch_id text,
  chunk_id text,
  embedding vector(1024)
)
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $load_rag_v2_immutable_voyage_document_batch_vectors$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_batch_plan_sha256 IS NULL
     OR p_batch_plan_sha256 !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch resume is unavailable' USING ERRCODE = '55000';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_voyage_document_batch_attempts AS attempt
    WHERE attempt.batch_plan_sha256 = p_batch_plan_sha256
      AND attempt.state <> 'COMMITTED'
  ) THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch has a terminal ambiguous attempt'
      USING ERRCODE = '55000';
  END IF;
  -- 첫 batch 전에는 plan row가 아직 없다. 이 경우 attempt도 없을 때만 empty resume가 정상이며,
  -- 첫 stage가 complete plan identity를 원자적으로 생성한다.
  IF NOT EXISTS (
    SELECT 1 FROM public.rag_v2_immutable_voyage_document_batch_plans AS plan
    WHERE plan.batch_plan_sha256 = p_batch_plan_sha256
  ) THEN
    RETURN;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.rag_v2_immutable_voyage_document_batch_plans AS plan
    WHERE plan.batch_plan_sha256 = p_batch_plan_sha256 AND plan.state IN ('STAGING','COMPLETE')
  ) THEN
    RAISE EXCEPTION 'Pre-S5 Voyage document batch resume state is invalid' USING ERRCODE = '55000';
  END IF;
  RETURN QUERY
  SELECT vector_row.batch_id, vector_row.chunk_id, vector_row.embedding
  FROM public.rag_v2_immutable_voyage_document_batch_vectors AS vector_row
  JOIN public.rag_v2_immutable_voyage_document_batches AS batch
    ON batch.batch_id = vector_row.batch_id
   AND batch.batch_plan_sha256 = vector_row.batch_plan_sha256
   AND batch.state = 'COMMITTED'
  WHERE vector_row.batch_plan_sha256 = p_batch_plan_sha256
  ORDER BY batch.batch_ordinal, vector_row.chunk_id;
END
$load_rag_v2_immutable_voyage_document_batch_vectors$;
ALTER FUNCTION load_rag_v2_immutable_voyage_document_batch_vectors(text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION load_rag_v2_immutable_voyage_document_batch_vectors(text) FROM PUBLIC;

-- Window A는 document와 두 evaluation packet을 한 번에 승인하므로 evaluation 전용 reservation만
-- 최대 2시간을 허용한다. 일반 runtime query의 V51 5분 TTL은 그대로 유지한다.
CREATE FUNCTION reserve_rag_v2_immutable_voyage_evaluation_batch_usage(
  p_usage_event_id text,
  p_packet_sha256 text,
  p_nonce_sha256 text,
  p_query_manifest_sha256 text,
  p_scope_claim_sha256 text,
  p_rate_evidence_sha256 text,
  p_official_tokenizer_sha256 text,
  p_component_scope text,
  p_expires_at timestamptz,
  p_token_cap integer,
  p_byte_cap integer,
  p_cost_cap_microusd bigint,
  p_input_microusd_per_token bigint
)
RETURNS TABLE (usage_event_id text, expires_at timestamptz)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $reserve_rag_v2_immutable_voyage_evaluation_batch_usage$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_usage_event_id !~ '^rgr_vqu_[0-9a-f]{32}$'
     OR p_packet_sha256 !~ '^[0-9a-f]{64}$'
     OR p_nonce_sha256 !~ '^[0-9a-f]{64}$'
     OR p_query_manifest_sha256 !~ '^[0-9a-f]{64}$'
     OR p_scope_claim_sha256 !~ '^[0-9a-f]{64}$'
     OR p_rate_evidence_sha256 !~ '^[0-9a-f]{64}$'
     OR p_official_tokenizer_sha256 !~ '^[0-9a-f]{64}$'
     OR p_component_scope NOT IN ('EXACT30','OA112')
     OR p_expires_at <= statement_timestamp()
     OR p_expires_at > statement_timestamp() + interval '2 hours'
     OR p_token_cap NOT BETWEEN 1 AND 8192
     OR p_byte_cap NOT BETWEEN 1 AND 4194304
     OR p_cost_cap_microusd NOT BETWEEN 1 AND 1000000000
     OR p_input_microusd_per_token NOT BETWEEN 1 AND 1000000
     OR p_token_cap::bigint * p_input_microusd_per_token > p_cost_cap_microusd THEN
    RAISE EXCEPTION 'immutable Pre-S5 Voyage evaluation batch reservation arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'rag-v2-immutable-voyage-evaluation-batch|' || p_scope_claim_sha256 || '|' || p_component_scope,
      0
    )
  );
  IF EXISTS (
    SELECT 1 FROM public.rag_v2_immutable_voyage_evaluation_batch_attempts
    WHERE scope_claim_sha256 = p_scope_claim_sha256 AND component_scope = p_component_scope
  ) THEN
    RAISE EXCEPTION 'immutable Pre-S5 Voyage evaluation batch is already terminal'
      USING ERRCODE = '55000';
  END IF;
  INSERT INTO public.rag_v2_immutable_voyage_query_usage_reservations (
    usage_event_id, packet_sha256, nonce_sha256, query_sha256, scope_claim_sha256,
    rate_evidence_sha256, official_tokenizer_sha256, evaluation_component_scope, expires_at,
    token_cap, byte_cap, cost_cap_microusd, input_microusd_per_token
  ) VALUES (
    p_usage_event_id, p_packet_sha256, p_nonce_sha256, p_query_manifest_sha256,
    p_scope_claim_sha256, p_rate_evidence_sha256, p_official_tokenizer_sha256,
    p_component_scope, p_expires_at, p_token_cap, p_byte_cap, p_cost_cap_microusd,
    p_input_microusd_per_token
  );
  RETURN QUERY SELECT p_usage_event_id, p_expires_at;
END
$reserve_rag_v2_immutable_voyage_evaluation_batch_usage$;
ALTER FUNCTION reserve_rag_v2_immutable_voyage_evaluation_batch_usage(
  text,text,text,text,text,text,text,text,timestamptz,integer,integer,bigint,bigint
) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION reserve_rag_v2_immutable_voyage_evaluation_batch_usage(
  text,text,text,text,text,text,text,text,timestamptz,integer,integer,bigint,bigint
) FROM PUBLIC;

CREATE FUNCTION claim_rag_v2_immutable_voyage_evaluation_batch_attempt(
  p_usage_event_id text,
  p_scope_claim_sha256 text,
  p_component_scope text,
  p_query_manifest_sha256 text,
  p_packet_sha256 text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $claim_rag_v2_immutable_voyage_evaluation_batch_attempt$
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_rag_writer' THEN
    RAISE EXCEPTION 'Pre-S5 Voyage evaluation claim is forbidden' USING ERRCODE = '42501';
  END IF;
  PERFORM public.claim_rag_v2_immutable_voyage_query_usage_attempt(p_usage_event_id);
  INSERT INTO public.rag_v2_immutable_voyage_evaluation_batch_attempts (
    scope_claim_sha256, component_scope, query_manifest_sha256, usage_event_id, packet_sha256
  ) VALUES (
    p_scope_claim_sha256, p_component_scope, p_query_manifest_sha256, p_usage_event_id, p_packet_sha256
  );
END
$claim_rag_v2_immutable_voyage_evaluation_batch_attempt$;
ALTER FUNCTION claim_rag_v2_immutable_voyage_evaluation_batch_attempt(text,text,text,text,text)
  OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION
  claim_rag_v2_immutable_voyage_evaluation_batch_attempt(text,text,text,text,text) FROM PUBLIC;

CREATE FUNCTION mark_rag_v2_immutable_voyage_evaluation_batch_unknown_billing(
  p_usage_event_id text,
  p_scope_claim_sha256 text,
  p_component_scope text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $mark_rag_v2_immutable_voyage_evaluation_batch_unknown_billing$
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_rag_writer' THEN
    RAISE EXCEPTION 'Pre-S5 Voyage evaluation unknown outcome is forbidden' USING ERRCODE = '42501';
  END IF;
  PERFORM public.mark_rag_v2_immutable_voyage_query_usage_unknown_billing(p_usage_event_id);
  UPDATE public.rag_v2_immutable_voyage_evaluation_batch_attempts
  SET state = 'UNKNOWN_BILLING', terminal_at = transaction_timestamp()
  WHERE usage_event_id = p_usage_event_id
    AND scope_claim_sha256 = p_scope_claim_sha256
    AND component_scope = p_component_scope
    AND state = 'CLAIMED';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Pre-S5 Voyage evaluation unknown outcome conflicts' USING ERRCODE = '55000';
  END IF;
END
$mark_rag_v2_immutable_voyage_evaluation_batch_unknown_billing$;
ALTER FUNCTION mark_rag_v2_immutable_voyage_evaluation_batch_unknown_billing(text,text,text)
  OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION
  mark_rag_v2_immutable_voyage_evaluation_batch_unknown_billing(text,text,text) FROM PUBLIC;

CREATE FUNCTION commit_and_stage_rag_v2_immutable_voyage_evaluation_batch(p_payload jsonb)
RETURNS TABLE (component_scope text, staged_vector_count integer, batch_reused boolean)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $commit_and_stage_rag_v2_immutable_voyage_evaluation_batch$
DECLARE
  expected_count integer;
  vector_item jsonb;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_payload IS NULL OR jsonb_typeof(p_payload) <> 'object'
     OR (SELECT count(*) FROM jsonb_object_keys(p_payload)) <> 10
     OR NOT (p_payload ?& ARRAY[
       'schemaVersion','scopeClaimSha256','componentScope','queryManifestSha256','usageEventId',
       'packetSha256','expectedInputTokens','providerTotalTokens','actualCostMicrousd','vectors'
     ])
     OR p_payload ->> 'schemaVersion' <> 'pre-s5-voyage-evaluation-batch-stage/v1'
     OR p_payload ->> 'scopeClaimSha256' !~ '^[0-9a-f]{64}$'
     OR p_payload ->> 'componentScope' NOT IN ('EXACT30','OA112')
     OR p_payload ->> 'queryManifestSha256' !~ '^[0-9a-f]{64}$'
     OR p_payload ->> 'usageEventId' !~ '^rgr_vqu_[0-9a-f]{32}$'
     OR p_payload ->> 'packetSha256' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(p_payload -> 'vectors') <> 'array' THEN
    RAISE EXCEPTION 'Pre-S5 Voyage evaluation stage payload is invalid' USING ERRCODE = '22023';
  END IF;
  expected_count := CASE p_payload ->> 'componentScope' WHEN 'EXACT30' THEN 10 ELSE 112 END;
  IF jsonb_array_length(p_payload -> 'vectors') <> expected_count
     OR (p_payload ->> 'expectedInputTokens')::integer NOT BETWEEN 1 AND 8192
     OR (p_payload ->> 'providerTotalTokens')::integer NOT BETWEEN 0 AND 8192
     OR (p_payload ->> 'actualCostMicrousd')::bigint NOT BETWEEN 0 AND 1000000000
     OR NOT EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_voyage_evaluation_batch_attempts AS attempt
       WHERE attempt.scope_claim_sha256 = p_payload ->> 'scopeClaimSha256'
         AND attempt.component_scope = p_payload ->> 'componentScope'
         AND attempt.query_manifest_sha256 = p_payload ->> 'queryManifestSha256'
         AND attempt.usage_event_id = p_payload ->> 'usageEventId'
         AND attempt.packet_sha256 = p_payload ->> 'packetSha256'
         AND attempt.state = 'CLAIMED'
     ) THEN
    RAISE EXCEPTION 'Pre-S5 Voyage evaluation stage is unavailable' USING ERRCODE = '55000';
  END IF;
  PERFORM public.commit_rag_v2_immutable_voyage_query_usage_with_tokenizer(
    p_payload ->> 'usageEventId',
    (p_payload ->> 'expectedInputTokens')::integer,
    (p_payload ->> 'providerTotalTokens')::integer,
    (p_payload ->> 'actualCostMicrousd')::bigint
  );
  FOR vector_item IN SELECT value FROM jsonb_array_elements(p_payload -> 'vectors') LOOP
    IF (SELECT count(*) FROM jsonb_object_keys(vector_item)) <> 3
       OR NOT (vector_item ?& ARRAY['querySha256','vectorSha256','embedding'])
       OR vector_item ->> 'querySha256' !~ '^[0-9a-f]{64}$'
       OR vector_item ->> 'vectorSha256' !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(vector_item -> 'embedding') <> 'array'
       OR jsonb_array_length(vector_item -> 'embedding') <> 1024 THEN
      RAISE EXCEPTION 'Pre-S5 Voyage evaluation vector payload is invalid' USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.rag_v2_immutable_voyage_evaluation_batch_vectors (
      scope_claim_sha256, component_scope, query_manifest_sha256, query_sha256,
      vector_sha256, embedding
    ) VALUES (
      p_payload ->> 'scopeClaimSha256', p_payload ->> 'componentScope',
      p_payload ->> 'queryManifestSha256', vector_item ->> 'querySha256',
      vector_item ->> 'vectorSha256', (vector_item -> 'embedding')::text::vector
    );
  END LOOP;
  UPDATE public.rag_v2_immutable_voyage_evaluation_batch_attempts
  SET state = 'COMMITTED', terminal_at = transaction_timestamp()
  WHERE usage_event_id = p_payload ->> 'usageEventId' AND state = 'CLAIMED';
  IF NOT FOUND OR (
    SELECT count(*) FROM public.rag_v2_immutable_voyage_evaluation_batch_vectors AS staged_vector
    WHERE staged_vector.scope_claim_sha256 = p_payload ->> 'scopeClaimSha256'
      AND staged_vector.component_scope = p_payload ->> 'componentScope'
  ) <> expected_count THEN
    RAISE EXCEPTION 'Pre-S5 Voyage evaluation stage cardinality conflicts' USING ERRCODE = '55000';
  END IF;
  RETURN QUERY SELECT p_payload ->> 'componentScope', expected_count, false;
END
$commit_and_stage_rag_v2_immutable_voyage_evaluation_batch$;
ALTER FUNCTION commit_and_stage_rag_v2_immutable_voyage_evaluation_batch(jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION
  commit_and_stage_rag_v2_immutable_voyage_evaluation_batch(jsonb) FROM PUBLIC;

CREATE FUNCTION load_rag_v2_immutable_voyage_evaluation_batch_vectors(
  p_scope_claim_sha256 text,
  p_component_scope text,
  p_query_manifest_sha256 text
)
RETURNS TABLE (query_sha256 text, embedding vector(1024))
LANGUAGE plpgsql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $load_rag_v2_immutable_voyage_evaluation_batch_vectors$
DECLARE
  attempt_state text;
  expected_count integer;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_scope_claim_sha256 !~ '^[0-9a-f]{64}$'
     OR p_component_scope NOT IN ('EXACT30','OA112')
     OR p_query_manifest_sha256 !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'Pre-S5 Voyage evaluation resume arguments are invalid' USING ERRCODE = '22023';
  END IF;
  SELECT state INTO attempt_state
  FROM public.rag_v2_immutable_voyage_evaluation_batch_attempts
  WHERE scope_claim_sha256 = p_scope_claim_sha256 AND component_scope = p_component_scope;
  IF NOT FOUND THEN
    RETURN;
  END IF;
  IF attempt_state <> 'COMMITTED' OR NOT EXISTS (
    SELECT 1 FROM public.rag_v2_immutable_voyage_evaluation_batch_attempts
    WHERE scope_claim_sha256 = p_scope_claim_sha256
      AND component_scope = p_component_scope
      AND query_manifest_sha256 = p_query_manifest_sha256
  ) THEN
    RAISE EXCEPTION 'Pre-S5 Voyage evaluation batch is terminal or drifted' USING ERRCODE = '55000';
  END IF;
  expected_count := CASE p_component_scope WHEN 'EXACT30' THEN 10 ELSE 112 END;
  IF (SELECT count(*) FROM public.rag_v2_immutable_voyage_evaluation_batch_vectors
      WHERE scope_claim_sha256 = p_scope_claim_sha256 AND component_scope = p_component_scope)
     <> expected_count THEN
    RAISE EXCEPTION 'Pre-S5 Voyage evaluation resume cardinality conflicts' USING ERRCODE = '55000';
  END IF;
  RETURN QUERY
  SELECT vector_row.query_sha256, vector_row.embedding
  FROM public.rag_v2_immutable_voyage_evaluation_batch_vectors AS vector_row
  WHERE vector_row.scope_claim_sha256 = p_scope_claim_sha256
    AND vector_row.component_scope = p_component_scope
  ORDER BY vector_row.query_sha256;
END
$load_rag_v2_immutable_voyage_evaluation_batch_vectors$;
ALTER FUNCTION load_rag_v2_immutable_voyage_evaluation_batch_vectors(text,text,text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION
  load_rag_v2_immutable_voyage_evaluation_batch_vectors(text,text,text) FROM PUBLIC;
DO $pre_s5_voyage_batch_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    REVOKE ALL PRIVILEGES ON FUNCTION stage_rag_v2_immutable_voyage_document_batch(jsonb)
      FROM decision_rag_writer;
    GRANT EXECUTE ON FUNCTION reserve_rag_v2_immutable_voyage_document_batch_usage(
      text, text, text, text, text, text, timestamptz, integer, integer, bigint, bigint
    ) TO decision_rag_writer;
    GRANT EXECUTE ON FUNCTION
      claim_rag_v2_immutable_voyage_document_batch_attempt(text, text, text, text),
      mark_rag_v2_immutable_voyage_document_batch_unknown_billing(text, text, text),
      commit_and_stage_rag_v2_immutable_voyage_document_batch(jsonb)
    TO decision_rag_writer;
    GRANT EXECUTE ON FUNCTION load_rag_v2_immutable_voyage_document_batch_vectors(text)
      TO decision_rag_writer;
    GRANT EXECUTE ON FUNCTION reserve_rag_v2_immutable_voyage_evaluation_batch_usage(
      text,text,text,text,text,text,text,text,timestamptz,integer,integer,bigint,bigint
    ) TO decision_rag_writer;
    GRANT EXECUTE ON FUNCTION
      claim_rag_v2_immutable_voyage_evaluation_batch_attempt(text,text,text,text,text),
      mark_rag_v2_immutable_voyage_evaluation_batch_unknown_billing(text,text,text),
      commit_and_stage_rag_v2_immutable_voyage_evaluation_batch(jsonb),
      load_rag_v2_immutable_voyage_evaluation_batch_vectors(text,text,text)
    TO decision_rag_writer;
    GRANT EXECUTE ON FUNCTION record_rag_v2_bge_public_execution_supersession(text, text)
      TO decision_rag_writer;
  END IF;
END
$pre_s5_voyage_batch_acl$;

REVOKE ALL PRIVILEGES ON TABLE rag_v2_immutable_voyage_document_batch_plans FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE rag_v2_immutable_voyage_document_batches FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE rag_v2_immutable_voyage_document_batch_vectors FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE rag_v2_immutable_voyage_document_batch_attempts FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE rag_v2_immutable_voyage_evaluation_batch_attempts FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE rag_v2_immutable_voyage_evaluation_batch_vectors FROM PUBLIC;
