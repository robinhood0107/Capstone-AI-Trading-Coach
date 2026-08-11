-- V54는 one full-bundle Voyage call을 exact manifest-bound document batch set으로 대체한다.
-- canonical text/raw/provider response는 저장하지 않고 성공 batch의 vectors와 content-free identity만
-- durable stage해 재시작 시 이미 소비한 packet/provider call을 반복하지 않게 한다.

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
      AND expected_chunk_count BETWEEN 1 AND 16000
      AND expected_group_count BETWEEN 1 AND 1000
      AND provider_physical_call_count = 1
      AND state = 'COMMITTED'
    ),
  CONSTRAINT rag_v2_immutable_voyage_document_batch_plan_ordinal_unique
    UNIQUE (batch_plan_sha256, batch_ordinal),
  CONSTRAINT rag_v2_immutable_voyage_document_batch_plan_manifest_unique
    UNIQUE (batch_plan_sha256, batch_manifest_sha256)
);

CREATE TABLE rag_v2_immutable_voyage_document_batch_vectors (
  batch_plan_sha256 text NOT NULL,
  batch_id text NOT NULL REFERENCES rag_v2_immutable_voyage_document_batches(batch_id),
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

CREATE TABLE rag_v2_immutable_bge_public_execution_supersessions (
  marker text PRIMARY KEY,
  exact30_component_generation_id text NOT NULL,
  oa112_component_generation_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_bge_public_execution_supersession_marker_check
    CHECK (marker = 'TERMINALLY_SUPERSEDED_NO_FURTHER_BGE_RUN'),
  CONSTRAINT rag_v2_immutable_bge_public_execution_supersession_generation_check
    CHECK (
      exact30_component_generation_id ~ '^rgr_[0-9a-f]{32}$'
      AND oa112_component_generation_id ~ '^rgr_[0-9a-f]{32}$'
      AND exact30_component_generation_id <> oa112_component_generation_id
    )
);

ALTER TABLE rag_v2_immutable_voyage_document_batch_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_voyage_document_batch_plans FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_voyage_document_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_voyage_document_batches FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_voyage_document_batch_vectors ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_voyage_document_batch_vectors FORCE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_bge_public_execution_supersessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_bge_public_execution_supersessions FORCE ROW LEVEL SECURITY;

CREATE POLICY rag_v2_immutable_voyage_document_batch_plans_flyway
  ON rag_v2_immutable_voyage_document_batch_plans FOR ALL TO flyway USING (true) WITH CHECK (true);
CREATE POLICY rag_v2_immutable_voyage_document_batches_flyway
  ON rag_v2_immutable_voyage_document_batches FOR ALL TO flyway USING (true) WITH CHECK (true);
CREATE POLICY rag_v2_immutable_voyage_document_batch_vectors_flyway
  ON rag_v2_immutable_voyage_document_batch_vectors FOR ALL TO flyway USING (true) WITH CHECK (true);
CREATE POLICY rag_v2_immutable_bge_public_execution_supersessions_flyway
  ON rag_v2_immutable_bge_public_execution_supersessions FOR ALL TO flyway USING (true) WITH CHECK (true);

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
  IF (SELECT count(*) FROM jsonb_object_keys(payload_batch)) <> 8
     OR NOT (payload_batch ?& ARRAY[
       'batchId','batchManifestSha256','batchOrdinal','batchCount','tokenCount','chunkCount',
       'groupCount','vectorSetSha256'
     ])
     OR payload_batch ->> 'batchId' !~ '^ps5_voyage_doc_[0-9]{4}_[0-9a-f]{16}$'
     OR payload_batch ->> 'batchManifestSha256' !~ '^[0-9a-f]{64}$'
     OR payload_batch ->> 'vectorSetSha256' !~ '^[0-9a-f]{64}$'
     OR (payload_batch ->> 'batchCount')::integer <> (payload_plan ->> 'batchCount')::integer
     OR (payload_batch ->> 'batchOrdinal')::integer NOT BETWEEN 1 AND (payload_plan ->> 'batchCount')::integer
     OR (payload_batch ->> 'tokenCount')::integer NOT BETWEEN 1 AND 110000
     OR (payload_batch ->> 'chunkCount')::integer NOT BETWEEN 1 AND 16000
     OR (payload_batch ->> 'groupCount')::integer NOT BETWEEN 1 AND 1000
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
       OR existing_batch.expected_token_count <> (payload_batch ->> 'tokenCount')::integer
       OR existing_batch.expected_chunk_count <> observed_vector_count
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
      expected_token_count, expected_chunk_count, expected_group_count, packet_sha256,
      vector_set_sha256, provider_physical_call_count, state
    ) VALUES (
      payload_batch ->> 'batchId', payload_plan ->> 'batchPlanSha256',
      payload_batch ->> 'batchManifestSha256', (payload_batch ->> 'batchOrdinal')::integer,
      (payload_batch ->> 'batchCount')::integer, (payload_batch ->> 'tokenCount')::integer,
      observed_vector_count, (payload_batch ->> 'groupCount')::integer,
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
  IF complete_count = (payload_plan ->> 'batchCount')::integer
     AND vector_count = (payload_plan ->> 'chunkCount')::integer THEN
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
  -- 첫 batch 전에는 plan row가 아직 없다. 이 경우 empty resume는 정상이며, 첫 stage가
  -- complete plan identity를 원자적으로 생성한다. 존재하는 다른 state는 fail-closed 한다.
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

-- 평가 질문의 논리 개수(10+112)는 유지하지만 component별 singleton-group 요청 한 번만 사용한다.
-- historical V45/V47 migration bytes는 수정하지 않고, expected physical ledger cardinality만 fail-closed
-- replacement로 좁힌다. exact source text가 drift하면 migration 자체가 중단된다.
DO $pre_s5_voyage_evaluation_batch_function_rewrite$
DECLARE
  function_definition text;
  patched_definition text;
BEGIN
  SELECT pg_get_functiondef(
    'public.evaluate_rag_v2_immutable_public_voyage_component_v45_unlinked(text,jsonb)'::regprocedure
  ) INTO function_definition;
  patched_definition := replace(
    function_definition,
    $old_v45$WHEN 'EXACT30' THEN 10
         WHEN 'OA112' THEN 112$old_v45$,
    $new_v45$WHEN 'EXACT30' THEN 1
         WHEN 'OA112' THEN 1$new_v45$
  );
  IF patched_definition = function_definition
     OR position($old_v45$WHEN 'EXACT30' THEN 10
         WHEN 'OA112' THEN 112$old_v45$ IN patched_definition) <> 0 THEN
    RAISE EXCEPTION 'V45 Voyage evaluation physical-count contract drifted'
      USING ERRCODE = '55000';
  END IF;
  EXECUTE patched_definition;

  SELECT pg_get_functiondef(
    'public.evaluate_rag_v2_immutable_public_voyage_component(text,jsonb)'::regprocedure
  ) INTO function_definition;
  patched_definition := replace(
    function_definition,
    $old_v47$expected_query_count := CASE generation_scope WHEN 'EXACT30' THEN 10 WHEN 'OA112' THEN 112 ELSE -1 END;$old_v47$,
    $new_v47$expected_query_count := CASE generation_scope WHEN 'EXACT30' THEN 1 WHEN 'OA112' THEN 1 ELSE -1 END;$new_v47$
  );
  IF patched_definition = function_definition
     OR position($old_v47$expected_query_count := CASE generation_scope WHEN 'EXACT30' THEN 10 WHEN 'OA112' THEN 112 ELSE -1 END;$old_v47$ IN patched_definition) <> 0 THEN
    RAISE EXCEPTION 'V47 Voyage query ledger cardinality contract drifted'
      USING ERRCODE = '55000';
  END IF;
  EXECUTE patched_definition;
END
$pre_s5_voyage_evaluation_batch_function_rewrite$;

CREATE FUNCTION record_rag_v2_bge_public_execution_supersession(
  p_exact30_component_generation_id text,
  p_oa112_component_generation_id text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $record_rag_v2_bge_public_execution_supersession$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR NOT EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_component_generations
       WHERE component_generation_id = p_exact30_component_generation_id
         AND component_scope = 'EXACT30'
         AND embedding_profile_id = 'bge_m3_local_1024_v1'
         AND state <> 'ACTIVE'
     )
     OR NOT EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_component_generations
       WHERE component_generation_id = p_oa112_component_generation_id
         AND component_scope = 'OA112'
         AND embedding_profile_id = 'bge_m3_local_1024_v1'
         AND state <> 'ACTIVE'
     ) THEN
    RAISE EXCEPTION 'BGE public execution supersession arguments are invalid' USING ERRCODE = '22023';
  END IF;
  INSERT INTO public.rag_v2_immutable_bge_public_execution_supersessions (
    marker, exact30_component_generation_id, oa112_component_generation_id
  ) VALUES (
    'TERMINALLY_SUPERSEDED_NO_FURTHER_BGE_RUN',
    p_exact30_component_generation_id,
    p_oa112_component_generation_id
  ) ON CONFLICT (marker) DO NOTHING;
  IF NOT EXISTS (
    SELECT 1 FROM public.rag_v2_immutable_bge_public_execution_supersessions
    WHERE marker = 'TERMINALLY_SUPERSEDED_NO_FURTHER_BGE_RUN'
      AND exact30_component_generation_id = p_exact30_component_generation_id
      AND oa112_component_generation_id = p_oa112_component_generation_id
  ) THEN
    RAISE EXCEPTION 'BGE public execution supersession conflicts' USING ERRCODE = '23505';
  END IF;
END
$record_rag_v2_bge_public_execution_supersession$;
ALTER FUNCTION record_rag_v2_bge_public_execution_supersession(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION record_rag_v2_bge_public_execution_supersession(text, text) FROM PUBLIC;

DO $pre_s5_voyage_batch_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    GRANT EXECUTE ON FUNCTION stage_rag_v2_immutable_voyage_document_batch(jsonb) TO decision_rag_writer;
    GRANT EXECUTE ON FUNCTION load_rag_v2_immutable_voyage_document_batch_vectors(text)
      TO decision_rag_writer;
    GRANT EXECUTE ON FUNCTION record_rag_v2_bge_public_execution_supersession(text, text)
      TO decision_rag_writer;
  END IF;
END
$pre_s5_voyage_batch_acl$;

REVOKE ALL PRIVILEGES ON TABLE rag_v2_immutable_voyage_document_batch_plans FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE rag_v2_immutable_voyage_document_batches FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE rag_v2_immutable_voyage_document_batch_vectors FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE rag_v2_immutable_bge_public_execution_supersessions FROM PUBLIC;
