-- V36은 exact-30/OA112의 local BGE materializer가 raw path/provider payload 없이 public
-- immutable graph를 source 단위로 resume할 수 있게 한다. V25의 public activation CAS와 V29의
-- retrieval scope는 변경하지 않으며, writer는 두 SECURITY DEFINER capability 외 table grant를 받지 않는다.

CREATE TABLE rag_v2_immutable_public_voyage_component_evaluations (
  component_generation_id text PRIMARY KEY,
  component_scope text NOT NULL,
  embedding_profile_id text NOT NULL,
  evaluation_digest text NOT NULL,
  exact_top5_hit_rate double precision NOT NULL,
  track_recall_at5 double precision NOT NULL,
  citation_coverage double precision NOT NULL,
  direct_advice_block_rate double precision NOT NULL,
  cross_owner_leak_count integer NOT NULL,
  mixed_profile_row_count integer NOT NULL,
  owner_delete_residual_row_count integer NOT NULL,
  warm_p95_millis double precision NOT NULL,
  provider_physical_call_count integer NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_public_voyage_component_evaluation_generation_fkey
    FOREIGN KEY (component_generation_id, component_scope)
    REFERENCES rag_v2_immutable_component_generations (component_generation_id, component_scope)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_public_voyage_component_evaluation_scope_check
    CHECK (component_scope IN ('EXACT30', 'OA112')),
  CONSTRAINT rag_v2_immutable_public_voyage_component_evaluation_profile_check
    CHECK (embedding_profile_id = 'voyage_context_4_1024_v1'),
  CONSTRAINT rag_v2_immutable_public_voyage_component_evaluation_digest_check
    CHECK (evaluation_digest ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_v2_immutable_public_voyage_component_evaluation_metric_check
    CHECK (
      exact_top5_hit_rate >= 0 AND exact_top5_hit_rate <= 1
      AND track_recall_at5 >= 0 AND track_recall_at5 <= 1
      AND citation_coverage >= 0 AND citation_coverage <= 1
      AND direct_advice_block_rate >= 0 AND direct_advice_block_rate <= 1
      AND cross_owner_leak_count >= 0
      AND mixed_profile_row_count >= 0
      AND owner_delete_residual_row_count >= 0
      AND warm_p95_millis > 0
      AND provider_physical_call_count >= 0
    )
);

ALTER TABLE rag_v2_immutable_public_voyage_component_evaluations ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_public_voyage_component_evaluations FORCE ROW LEVEL SECURITY;

-- public evaluator는 writer capability의 SECURITY DEFINER owner만 읽고 쓸 수 있다. table에는
-- direct grant를 주지 않아 RLS가 writer의 broad DML capability로 바뀌지 않는다.
CREATE POLICY rag_v2_immutable_public_voyage_component_evaluations_flyway_write
  ON rag_v2_immutable_public_voyage_component_evaluations
  FOR ALL
  TO flyway
  USING (true)
  WITH CHECK (true);

-- component context가 application memory에서만 맞고 DB graph에는 bind되지 않는 우회를 막는다.
-- member digest는 source text/vector가 아닌 canonical projection hash라 public immutable evidence에
-- 저장해도 raw corpus/embedding 재배포 경계는 넓어지지 않는다.
CREATE TABLE rag_v2_immutable_public_voyage_component_manifests (
  component_generation_id text PRIMARY KEY,
  component_scope text NOT NULL,
  embedding_profile_id text NOT NULL,
  member_digests text[] NOT NULL,
  manifest_hash text NOT NULL,
  generation_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_public_voyage_component_manifest_generation_fkey
    FOREIGN KEY (component_generation_id, component_scope)
    REFERENCES rag_v2_immutable_component_generations (component_generation_id, component_scope)
    ON DELETE RESTRICT,
  CONSTRAINT rag_v2_immutable_public_voyage_component_manifest_scope_check
    CHECK (component_scope IN ('EXACT30', 'OA112')),
  CONSTRAINT rag_v2_immutable_public_voyage_component_manifest_profile_check
    CHECK (embedding_profile_id = 'voyage_context_4_1024_v1'),
  CONSTRAINT rag_v2_immutable_public_voyage_component_manifest_hash_check
    CHECK (manifest_hash ~ '^[0-9a-f]{64}$' AND generation_hash ~ '^[0-9a-f]{64}$')
);
ALTER TABLE rag_v2_immutable_public_voyage_component_manifests ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_public_voyage_component_manifests FORCE ROW LEVEL SECURITY;
CREATE POLICY rag_v2_immutable_public_voyage_component_manifests_flyway_write
  ON rag_v2_immutable_public_voyage_component_manifests
  FOR ALL
  TO flyway
  USING (true)
  WITH CHECK (true);


CREATE FUNCTION rag_v2_immutable_public_voyage_manifest_hash(
  p_component_scope text,
  p_member_digests text[]
)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog, public
AS $rag_v2_immutable_public_voyage_manifest_hash$
DECLARE
  encoded_members text;
BEGIN
  IF p_component_scope NOT IN ('EXACT30', 'OA112')
     OR cardinality(p_member_digests) <> (CASE p_component_scope WHEN 'EXACT30' THEN 30 ELSE 112 END)
     OR array_ndims(p_member_digests) <> 1
     OR array_position(p_member_digests, NULL) IS NOT NULL
     OR EXISTS (
       SELECT 1 FROM unnest(p_member_digests) AS member_digest
       WHERE member_digest !~ '^[0-9a-f]{64}$'
     )
     OR cardinality(p_member_digests) <> (
       SELECT count(DISTINCT member_digest) FROM unnest(p_member_digests) AS member_digest
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage member digest manifest is invalid'
      USING ERRCODE = '22023';
  END IF;
  SELECT string_agg(pg_catalog.to_json(member_digest)::text, ',' ORDER BY ordinal)
  INTO encoded_members
  FROM unnest(p_member_digests) WITH ORDINALITY AS digest_row(member_digest, ordinal);
  RETURN encode(public.digest(convert_to(
    '{"componentScope":' || pg_catalog.to_json(p_component_scope)::text ||
    ',"embeddingProfileId":"voyage_context_4_1024_v1","members":[' || encoded_members ||
    '],"schemaVersion":1}',
    'UTF8'
  ), 'sha256'), 'hex');
END;
$rag_v2_immutable_public_voyage_manifest_hash$;
ALTER FUNCTION rag_v2_immutable_public_voyage_manifest_hash(text, text[]) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION rag_v2_immutable_public_voyage_manifest_hash(text, text[]) FROM PUBLIC;

CREATE FUNCTION rag_v2_immutable_public_voyage_generation_hash(
  p_component_scope text,
  p_expected_source_count integer,
  p_expected_chunk_count integer,
  p_manifest_hash text
)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog, public
AS $rag_v2_immutable_public_voyage_generation_hash$
BEGIN
  IF p_component_scope NOT IN ('EXACT30', 'OA112')
     OR p_expected_source_count <> (CASE p_component_scope WHEN 'EXACT30' THEN 30 ELSE 112 END)
     OR p_expected_chunk_count < p_expected_source_count
     OR p_manifest_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage generation hash arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  RETURN encode(public.digest(convert_to(
    '{"componentScope":' || pg_catalog.to_json(p_component_scope)::text ||
    ',"embeddingProfileId":"voyage_context_4_1024_v1","expectedChunkCount":' ||
    p_expected_chunk_count::text || ',"expectedSourceCount":' || p_expected_source_count::text ||
    ',"manifestHash":' || pg_catalog.to_json(p_manifest_hash)::text || ',"schemaVersion":1}',
    'UTF8'
  ), 'sha256'), 'hex');
END;
$rag_v2_immutable_public_voyage_generation_hash$;
ALTER FUNCTION rag_v2_immutable_public_voyage_generation_hash(text, integer, integer, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION rag_v2_immutable_public_voyage_generation_hash(text, integer, integer, text) FROM PUBLIC;

CREATE FUNCTION rag_v2_immutable_public_voyage_source_member_digest(
  p_component_generation_id text,
  p_source_revision_id text
)
RETURNS text
LANGUAGE plpgsql
STABLE
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $rag_v2_immutable_public_voyage_source_member_digest$
DECLARE
  source_record public.rag_v2_immutable_source_revisions%ROWTYPE;
  oa_card_record public.rag_v2_immutable_oa_source_cards%ROWTYPE;
  encoded_chunks text;
  joined_canonical_text text;
BEGIN
  SELECT source.* INTO source_record
  FROM public.rag_v2_immutable_source_revisions AS source
  JOIN public.rag_v2_immutable_component_generations AS generation
    ON generation.component_generation_id = p_component_generation_id
  WHERE source.source_revision_id = p_source_revision_id
    AND source.source_scope = generation.component_scope
    AND source.owner_user_id IS NULL
    AND generation.owner_user_id IS NULL
    AND generation.embedding_profile_id = 'voyage_context_4_1024_v1';
  IF NOT FOUND
     OR source_record.source_scope NOT IN ('EXACT30', 'OA112')
     OR NOT source_record.external_processing_eligible
     OR (source_record.source_scope = 'EXACT30' AND (
       source_record.machine_fetch_allowed
       OR NOT source_record.local_processing_allowed
       OR NOT source_record.external_embedding_allowed
       OR NOT source_record.external_generation_allowed
       OR public.rag_v2_immutable_external_exact30_voyage_source_is_approved(
         source_record.source_id,
         source_record.canonical_https_url,
         source_record.raw_content_sha256,
         source_record.exact30_source_card_sha256
       ) IS NOT TRUE
     ))
     OR (source_record.source_scope = 'OA112' AND (
       NOT source_record.machine_fetch_allowed
       OR NOT source_record.local_processing_allowed
       OR NOT source_record.external_embedding_allowed
       OR NOT source_record.external_generation_allowed
       OR source_record.oa_track_id IS NULL
       OR source_record.reserve_source
     )) THEN
    RETURN NULL;
  END IF;

  IF source_record.source_scope = 'OA112' THEN
    SELECT * INTO oa_card_record
    FROM public.rag_v2_immutable_oa_source_cards AS card
    WHERE card.source_revision_id = p_source_revision_id
      AND card.source_scope = 'OA112'
      AND card.source_id = source_record.source_id
      AND card.active_oa112_eligible
      AND card.raw_content_sha256 = source_record.raw_content_sha256
      AND card.canonical_https_url = source_record.canonical_https_url
      AND card.license_evidence_sha256 = source_record.license_evidence_sha256
      AND card.access_evidence_sha256 = source_record.access_evidence_sha256
      AND card.machine_fetch_allowed
      AND card.local_processing_allowed
      AND card.external_embedding_allowed
      AND card.external_generation_allowed;
    IF NOT FOUND THEN
      RETURN NULL;
    END IF;
  END IF;

  SELECT
    string_agg(
      '{"canonicalTextSha256":' || pg_catalog.to_json(chunk.canonical_text_sha256)::text ||
      ',"chunkId":' || pg_catalog.to_json(chunk.chunk_id)::text ||
      ',"chunkOrdinal":' || chunk.chunk_ordinal::text ||
      ',"contextSetHash":' || pg_catalog.to_json(embedding.context_set_hash)::text ||
      ',"embeddingInputHash":' || pg_catalog.to_json(embedding.embedding_input_hash)::text || '}',
      ',' ORDER BY chunk.chunk_ordinal
    ),
    string_agg(chunk.canonical_text, E'\\n\\n' ORDER BY chunk.chunk_ordinal)
  INTO encoded_chunks, joined_canonical_text
  FROM public.rag_v2_immutable_generation_memberships AS membership
  JOIN public.rag_v2_immutable_chunks AS chunk
    ON chunk.chunk_id = membership.chunk_id
   AND chunk.source_revision_id = membership.source_revision_id
   AND chunk.source_scope = membership.component_scope
   AND chunk.owner_user_id IS NULL
  JOIN public.rag_v2_immutable_generation_embeddings AS embedding
    ON embedding.component_generation_id = membership.component_generation_id
   AND embedding.chunk_id = membership.chunk_id
   AND embedding.component_scope = membership.component_scope
   AND embedding.owner_user_id IS NULL
   AND embedding.embedding_profile_id = 'voyage_context_4_1024_v1'
  WHERE membership.component_generation_id = p_component_generation_id
    AND membership.component_scope = source_record.source_scope
    AND membership.source_revision_id = p_source_revision_id;
  IF encoded_chunks IS NULL OR joined_canonical_text IS NULL THEN
    RETURN NULL;
  END IF;
  IF source_record.source_scope = 'EXACT30' THEN
    RETURN encode(public.digest(convert_to(
      '{"canonicalTextSha256":' || pg_catalog.to_json(encode(
        public.digest(convert_to(joined_canonical_text, 'UTF8'), 'sha256'), 'hex'
      ))::text || ',"chunks":[' || encoded_chunks || '],"documentId":' ||
      pg_catalog.to_json(source_record.document_id)::text || ',"rawContentSha256":' ||
      pg_catalog.to_json(source_record.raw_content_sha256)::text || ',"sourceCardSha256":' ||
      pg_catalog.to_json(source_record.exact30_source_card_sha256)::text || ',"sourceId":' ||
      pg_catalog.to_json(source_record.source_id)::text || ',"sourceRevisionId":' ||
      pg_catalog.to_json(source_record.source_revision_id)::text || ',"sourceRevisionSha256":' ||
      pg_catalog.to_json(source_record.source_revision_sha256)::text || '}',
      'UTF8'
    ), 'sha256'), 'hex');
  END IF;
  RETURN encode(public.digest(convert_to(
    '{"accessEvidenceSha256":' || pg_catalog.to_json(source_record.access_evidence_sha256)::text ||
    ',"canonicalTextSha256":' || pg_catalog.to_json(encode(
      public.digest(convert_to(joined_canonical_text, 'UTF8'), 'sha256'), 'hex'
    ))::text || ',"chunks":[' || encoded_chunks || '],"documentId":' ||
    pg_catalog.to_json(source_record.document_id)::text || ',"licenseEvidenceSha256":' ||
    pg_catalog.to_json(source_record.license_evidence_sha256)::text || ',"oaTrackId":' ||
    pg_catalog.to_json(source_record.oa_track_id)::text || ',"rawContentSha256":' ||
    pg_catalog.to_json(source_record.raw_content_sha256)::text || ',"sourceId":' ||
    pg_catalog.to_json(source_record.source_id)::text || ',"sourceRevisionId":' ||
    pg_catalog.to_json(source_record.source_revision_id)::text || ',"sourceRevisionSha256":' ||
    pg_catalog.to_json(source_record.source_revision_sha256)::text || '}',
    'UTF8'
  ), 'sha256'), 'hex');
END;
$rag_v2_immutable_public_voyage_source_member_digest$;
ALTER FUNCTION rag_v2_immutable_public_voyage_source_member_digest(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION rag_v2_immutable_public_voyage_source_member_digest(text, text) FROM PUBLIC;


CREATE FUNCTION rag_v2_immutable_public_voyage_component_hashes_are_valid(
  p_component_generation_id text
)
RETURNS boolean
LANGUAGE plpgsql
STABLE
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $rag_v2_immutable_public_voyage_component_hashes_are_valid$
DECLARE
  generation_record public.rag_v2_immutable_component_generations%ROWTYPE;
  manifest_record public.rag_v2_immutable_public_voyage_component_manifests%ROWTYPE;
  observed_member_digests text[];
  observed_source_total integer;
  observed_chunk_total integer;
  expected_manifest_hash text;
  expected_generation_hash text;
BEGIN
  SELECT * INTO generation_record
  FROM public.rag_v2_immutable_component_generations
  WHERE component_generation_id = p_component_generation_id;
  IF NOT FOUND THEN
    RETURN false;
  END IF;
  SELECT * INTO manifest_record
  FROM public.rag_v2_immutable_public_voyage_component_manifests
  WHERE component_generation_id = p_component_generation_id;
  IF NOT FOUND
     OR generation_record.owner_user_id IS NOT NULL
     OR generation_record.component_scope NOT IN ('EXACT30', 'OA112')
     OR generation_record.embedding_profile_id <> 'voyage_context_4_1024_v1'
     OR manifest_record.component_scope <> generation_record.component_scope
     OR manifest_record.embedding_profile_id <> generation_record.embedding_profile_id
     OR manifest_record.manifest_hash <> generation_record.manifest_hash
     OR manifest_record.generation_hash <> generation_record.generation_hash THEN
    RETURN false;
  END IF;
  IF EXISTS (
    SELECT 1
    FROM (
      SELECT DISTINCT membership.source_revision_id
      FROM public.rag_v2_immutable_generation_memberships AS membership
      WHERE membership.component_generation_id = p_component_generation_id
        AND membership.component_scope = generation_record.component_scope
    ) AS selected_source
    JOIN public.rag_v2_immutable_source_revisions AS source
      ON source.source_revision_id = selected_source.source_revision_id
    WHERE source.owner_user_id IS NOT NULL
       OR source.source_scope <> generation_record.component_scope
       OR (
         generation_record.component_scope = 'EXACT30'
         AND (
           NOT source.external_processing_eligible
           OR source.machine_fetch_allowed
           OR NOT source.local_processing_allowed
           OR NOT source.external_embedding_allowed
           OR NOT source.external_generation_allowed
           OR public.rag_v2_immutable_external_exact30_voyage_source_is_approved(
             source.source_id,
             source.canonical_https_url,
             source.raw_content_sha256,
             source.exact30_source_card_sha256
           ) IS NOT TRUE
         )
       )
       OR (
         SELECT count(*)
         FROM public.rag_v2_immutable_generation_memberships AS source_membership
         WHERE source_membership.component_generation_id = p_component_generation_id
           AND source_membership.component_scope = generation_record.component_scope
           AND source_membership.source_revision_id = source.source_revision_id
       ) <> (
         SELECT count(*)
         FROM public.rag_v2_immutable_chunks AS source_chunk
         WHERE source_chunk.source_revision_id = source.source_revision_id
           AND source_chunk.source_scope = generation_record.component_scope
           AND source_chunk.owner_user_id IS NULL
       )
  ) THEN
    RETURN false;
  END IF;
  SELECT
    array_agg(
      public.rag_v2_immutable_public_voyage_source_member_digest(
        p_component_generation_id,
        selected_source.source_revision_id
      )
      ORDER BY pg_catalog.convert_to(selected_source.source_id, 'UTF8')
    ),
    count(*)::integer,
    coalesce(sum(selected_source.chunk_count), 0)::integer
  INTO observed_member_digests, observed_source_total, observed_chunk_total
  FROM (
    SELECT source.source_revision_id, source.source_id, count(membership.chunk_id)::integer AS chunk_count
    FROM public.rag_v2_immutable_generation_memberships AS membership
    JOIN public.rag_v2_immutable_source_revisions AS source
      ON source.source_revision_id = membership.source_revision_id
    WHERE membership.component_generation_id = p_component_generation_id
      AND membership.component_scope = generation_record.component_scope
    GROUP BY source.source_revision_id, source.source_id
  ) AS selected_source;
  IF observed_member_digests IS NULL
     OR array_position(observed_member_digests, NULL) IS NOT NULL
     OR observed_source_total <> generation_record.expected_source_count
     OR observed_chunk_total <> generation_record.expected_chunk_count
     OR observed_member_digests IS DISTINCT FROM manifest_record.member_digests THEN
    RETURN false;
  END IF;
  expected_manifest_hash := public.rag_v2_immutable_public_voyage_manifest_hash(
    generation_record.component_scope,
    manifest_record.member_digests
  );
  expected_generation_hash := public.rag_v2_immutable_public_voyage_generation_hash(
    generation_record.component_scope,
    generation_record.expected_source_count,
    generation_record.expected_chunk_count,
    expected_manifest_hash
  );
  RETURN expected_manifest_hash = generation_record.manifest_hash
    AND expected_generation_hash = generation_record.generation_hash
    AND p_component_generation_id = 'rgr_' || substr(expected_generation_hash, 1, 32);
END;
$rag_v2_immutable_public_voyage_component_hashes_are_valid$;
ALTER FUNCTION rag_v2_immutable_public_voyage_component_hashes_are_valid(text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION rag_v2_immutable_public_voyage_component_hashes_are_valid(text) FROM PUBLIC;

-- V36 already grants Flyway the constrained OA source-card INSERT policy; this Voyage writer reuses it.

CREATE FUNCTION stage_rag_v2_immutable_public_voyage_document(p_payload jsonb)
RETURNS TABLE (
  component_generation_id text,
  materialization_run_id text,
  state text,
  source_reused boolean,
  source_count integer,
  chunk_count integer
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $stage_rag_v2_immutable_public_voyage_document$
#variable_conflict use_column
DECLARE
  payload_scope text;
  payload_generation_id text;
  payload_run_id text;
  payload_generation_hash text;
  payload_manifest_hash text;
  payload_expected_source_count integer;
  payload_expected_chunk_count integer;
  payload_member_digests text[];
  payload_source_member_digest text;
  computed_manifest_hash text;
  computed_generation_hash text;
  payload_source jsonb;
  payload_document_id text;
  payload_source_id text;
  payload_source_revision_id text;
  payload_source_revision_sha256 text;
  payload_raw_content_sha256 text;
  payload_normalized_document_ir_sha256 text;
  payload_canonical_text_sha256 text;
  payload_canonical_text text;
  payload_mime_type text;
  payload_parser_version text;
  payload_tokenizer_version text;
  payload_document_ir jsonb;
  payload_source_locator jsonb;
  payload_canonical_https_url text;
  payload_source_card_sha256 text;
  payload_oa_track_id text;
  payload_source_card jsonb;
  payload_license_evidence_sha256 text;
  payload_access_evidence_sha256 text;
  payload_machine_fetch_allowed boolean;
  payload_local_processing_allowed boolean;
  payload_external_embedding_allowed boolean;
  payload_external_generation_allowed boolean;
  payload_external_processing_eligible boolean;
  payload_citation_title text;
  payload_topics text[];
  payload_chunk jsonb;
  payload_embedding jsonb;
  payload_chunk_id text;
  payload_chunk_ordinal integer;
  payload_chunk_text text;
  payload_chunk_sha256 text;
  payload_chunk_locator jsonb;
  payload_chunk_heading_path text[];
  payload_chunk_token_count integer;
  payload_chunk_contains_table boolean;
  payload_embedding_input_hash text;
  payload_context_set_hash text;
  payload_embedding_vector vector(1024);
  cached_embedding_vector vector(1024);
  existing_embedding_input_hash text;
  existing_context_set_hash text;
  expected_context_set_hash text;
  expected_source_record public.rag_v2_immutable_source_revisions%ROWTYPE;
  expected_generation_record public.rag_v2_immutable_component_generations%ROWTYPE;
  expected_manifest_record public.rag_v2_immutable_public_voyage_component_manifests%ROWTYPE;
  existing_run_state text;
  observed_chunk_count integer := 0;
  observed_embedding_count integer := 0;
  observed_source_text text := '';
  first_chunk_locator jsonb;
  observed_source_total integer := 0;
  observed_chunk_total integer := 0;
  existing_membership_count integer := 0;
  reused_embedding_count integer := 0;
  source_was_reused boolean := false;
  embedding_was_reused boolean := false;
  complete_state text := 'STAGING';
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_payload IS NULL
     OR jsonb_typeof(p_payload) <> 'object'
     OR octet_length(p_payload::text) NOT BETWEEN 2 AND 16777216
     OR EXISTS (
       SELECT 1 FROM jsonb_object_keys(p_payload) AS root_key
      WHERE root_key NOT IN (
         'componentGenerationId', 'componentScope', 'embeddingProfileId', 'expectedChunkCount',
         'expectedSourceCount', 'generationHash', 'manifestHash', 'materializationRunId',
         'memberDigests', 'schemaVersion', 'source'
       )
     )
     OR NOT (p_payload ?& ARRAY[
       'componentGenerationId', 'componentScope', 'embeddingProfileId', 'expectedChunkCount',
       'expectedSourceCount', 'generationHash', 'manifestHash', 'materializationRunId',
       'memberDigests', 'schemaVersion', 'source'
     ])
     OR jsonb_typeof(p_payload -> 'schemaVersion') <> 'number'
     OR p_payload ->> 'schemaVersion' <> '1'
     OR jsonb_typeof(p_payload -> 'componentGenerationId') <> 'string'
     OR jsonb_typeof(p_payload -> 'componentScope') <> 'string'
     OR jsonb_typeof(p_payload -> 'embeddingProfileId') <> 'string'
     OR jsonb_typeof(p_payload -> 'generationHash') <> 'string'
     OR jsonb_typeof(p_payload -> 'manifestHash') <> 'string'
     OR jsonb_typeof(p_payload -> 'materializationRunId') <> 'string'
     OR p_payload ->> 'embeddingProfileId' <> 'voyage_context_4_1024_v1'
     OR jsonb_typeof(p_payload -> 'expectedSourceCount') <> 'number'
     OR jsonb_typeof(p_payload -> 'expectedChunkCount') <> 'number'
     OR (p_payload -> 'expectedSourceCount')::text !~ '^(0|[1-9][0-9]*)$'
     OR (p_payload -> 'expectedChunkCount')::text !~ '^(0|[1-9][0-9]*)$'
     OR jsonb_typeof(p_payload -> 'memberDigests') <> 'array'
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements(p_payload -> 'memberDigests') AS digest_item(value)
       WHERE jsonb_typeof(digest_item.value) <> 'string'
     )
     OR jsonb_typeof(p_payload -> 'source') <> 'object' THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage staging arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  payload_scope := p_payload ->> 'componentScope';
  payload_generation_id := p_payload ->> 'componentGenerationId';
  payload_run_id := p_payload ->> 'materializationRunId';
  payload_generation_hash := p_payload ->> 'generationHash';
  payload_manifest_hash := p_payload ->> 'manifestHash';
  payload_expected_source_count := (p_payload ->> 'expectedSourceCount')::integer;
  payload_expected_chunk_count := (p_payload ->> 'expectedChunkCount')::integer;
  SELECT coalesce(array_agg(digest_item.value ORDER BY digest_item.ordinality), ARRAY[]::text[])
  INTO payload_member_digests
  FROM jsonb_array_elements_text(p_payload -> 'memberDigests') WITH ORDINALITY AS digest_item(value, ordinality);
  payload_source := p_payload -> 'source';
  IF payload_scope IS NULL
     OR payload_scope NOT IN ('EXACT30', 'OA112')
     OR payload_generation_id IS NULL
     OR payload_generation_id !~ '^rgr_[0-9a-f]{32}$'
     OR payload_run_id IS NULL
     OR payload_run_id !~ '^rgr_run_[0-9a-f]{32}$'
     OR payload_generation_hash IS NULL
     OR payload_generation_hash !~ '^[0-9a-f]{64}$'
     OR payload_manifest_hash IS NULL
     OR payload_manifest_hash !~ '^[0-9a-f]{64}$'
     OR payload_expected_source_count <> (CASE payload_scope WHEN 'EXACT30' THEN 30 ELSE 112 END)
     OR payload_expected_chunk_count < payload_expected_source_count
     OR cardinality(payload_member_digests) <> payload_expected_source_count
     OR array_position(payload_member_digests, NULL) IS NOT NULL
     OR EXISTS (
       SELECT 1 FROM unnest(payload_member_digests) AS member_digest
       WHERE member_digest !~ '^[0-9a-f]{64}$'
     )
     OR cardinality(payload_member_digests) <> (
       SELECT count(DISTINCT member_digest) FROM unnest(payload_member_digests) AS member_digest
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage component identity is invalid'
      USING ERRCODE = '22023';
  END IF;
  computed_manifest_hash := public.rag_v2_immutable_public_voyage_manifest_hash(
    payload_scope, payload_member_digests
  );
  computed_generation_hash := public.rag_v2_immutable_public_voyage_generation_hash(
    payload_scope, payload_expected_source_count, payload_expected_chunk_count, computed_manifest_hash
  );
  IF payload_manifest_hash <> computed_manifest_hash
     OR payload_generation_hash <> computed_generation_hash
     OR payload_generation_id <> 'rgr_' || substr(computed_generation_hash, 1, 32)
     OR payload_run_id <> 'rgr_run_' || substr(
       encode(
         digest(
           CASE payload_scope
             WHEN 'EXACT30' THEN
               'rag-v2-external-exact30-voyage-run|' || payload_generation_id || '|' || payload_manifest_hash
             ELSE
               'rag-v2-oa112-voyage-run|' || payload_generation_id || '|' || payload_manifest_hash
           END,
           'sha256'
         ),
         'hex'
       ),
       1,
       32
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage component hash binding is invalid'
      USING ERRCODE = '23514';
  END IF;

  IF EXISTS (
    SELECT 1 FROM jsonb_object_keys(payload_source) AS source_key
    WHERE source_key NOT IN (
      'accessEvidenceSha256', 'canonicalHttpsUrl', 'canonicalText', 'canonicalTextSha256',
      'chunks', 'citationTitle', 'documentId', 'documentIr', 'embeddings',
      'externalEmbeddingAllowed', 'externalGenerationAllowed', 'externalProcessingEligible',
      'licenseEvidenceSha256', 'localProcessingAllowed', 'machineFetchAllowed', 'mimeType',
      'oaSourceCard', 'oaTrackId', 'parserVersion', 'rawContentSha256', 'retrievalTopics',
      'sourceCardSha256', 'sourceId', 'sourceLocator', 'sourceRevisionId', 'sourceRevisionSha256', 'tokenizerVersion'
    )
  )
  OR NOT (payload_source ?& ARRAY[
    'accessEvidenceSha256', 'canonicalHttpsUrl', 'canonicalText', 'canonicalTextSha256',
    'chunks', 'citationTitle', 'documentId', 'documentIr', 'embeddings',
    'externalEmbeddingAllowed', 'externalGenerationAllowed', 'externalProcessingEligible',
    'licenseEvidenceSha256', 'localProcessingAllowed', 'machineFetchAllowed', 'mimeType',
    'oaSourceCard', 'oaTrackId', 'parserVersion', 'rawContentSha256', 'retrievalTopics',
    'sourceCardSha256', 'sourceId', 'sourceLocator', 'sourceRevisionId', 'sourceRevisionSha256', 'tokenizerVersion'
  ])
  OR jsonb_typeof(payload_source -> 'documentIr') <> 'object'
  OR jsonb_typeof(payload_source -> 'documentId') <> 'string'
  OR jsonb_typeof(payload_source -> 'sourceId') <> 'string'
  OR jsonb_typeof(payload_source -> 'sourceRevisionId') <> 'string'
  OR jsonb_typeof(payload_source -> 'sourceRevisionSha256') <> 'string'
  OR jsonb_typeof(payload_source -> 'rawContentSha256') <> 'string'
  OR jsonb_typeof(payload_source -> 'canonicalTextSha256') <> 'string'
  OR jsonb_typeof(payload_source -> 'canonicalText') <> 'string'
  OR jsonb_typeof(payload_source -> 'mimeType') <> 'string'
  OR jsonb_typeof(payload_source -> 'parserVersion') <> 'string'
  OR jsonb_typeof(payload_source -> 'tokenizerVersion') <> 'string'
  OR jsonb_typeof(payload_source -> 'canonicalHttpsUrl') <> 'string'
  OR jsonb_typeof(payload_source -> 'citationTitle') <> 'string'
  OR jsonb_typeof(payload_source -> 'chunks') <> 'array'
  OR jsonb_typeof(payload_source -> 'embeddings') <> 'array'
  OR jsonb_typeof(payload_source -> 'retrievalTopics') <> 'array'
  OR jsonb_typeof(payload_source -> 'sourceLocator') <> 'object'
  OR jsonb_typeof(payload_source -> 'machineFetchAllowed') <> 'boolean'
  OR jsonb_typeof(payload_source -> 'localProcessingAllowed') <> 'boolean'
  OR jsonb_typeof(payload_source -> 'externalEmbeddingAllowed') <> 'boolean'
  OR jsonb_typeof(payload_source -> 'externalGenerationAllowed') <> 'boolean'
  OR jsonb_typeof(payload_source -> 'externalProcessingEligible') <> 'boolean'
  OR jsonb_typeof(payload_source -> 'sourceCardSha256') NOT IN ('null', 'string')
  OR jsonb_typeof(payload_source -> 'oaTrackId') NOT IN ('null', 'string')
  OR jsonb_typeof(payload_source -> 'oaSourceCard') NOT IN ('null', 'object')
  OR jsonb_typeof(payload_source -> 'licenseEvidenceSha256') NOT IN ('null', 'string')
  OR jsonb_typeof(payload_source -> 'accessEvidenceSha256') NOT IN ('null', 'string')
  OR jsonb_array_length(payload_source -> 'chunks') NOT BETWEEN 1 AND 50000
  OR jsonb_array_length(payload_source -> 'embeddings') <> jsonb_array_length(payload_source -> 'chunks') THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage source payload is invalid'
      USING ERRCODE = '22023';
  END IF;

  payload_document_id := payload_source ->> 'documentId';
  payload_source_id := payload_source ->> 'sourceId';
  payload_source_revision_id := payload_source ->> 'sourceRevisionId';
  payload_source_revision_sha256 := payload_source ->> 'sourceRevisionSha256';
  payload_raw_content_sha256 := payload_source ->> 'rawContentSha256';
  payload_normalized_document_ir_sha256 := payload_source -> 'documentIr' ->> 'normalizedContentSha256';
  payload_canonical_text_sha256 := payload_source ->> 'canonicalTextSha256';
  payload_canonical_text := payload_source ->> 'canonicalText';
  payload_mime_type := payload_source ->> 'mimeType';
  payload_parser_version := payload_source ->> 'parserVersion';
  payload_tokenizer_version := payload_source ->> 'tokenizerVersion';
  payload_document_ir := payload_source -> 'documentIr';
  payload_source_locator := payload_source -> 'sourceLocator';
  payload_canonical_https_url := payload_source ->> 'canonicalHttpsUrl';
  payload_source_card_sha256 := payload_source ->> 'sourceCardSha256';
  payload_oa_track_id := payload_source ->> 'oaTrackId';
  payload_source_card := payload_source -> 'oaSourceCard';
  payload_license_evidence_sha256 := payload_source ->> 'licenseEvidenceSha256';
  payload_access_evidence_sha256 := payload_source ->> 'accessEvidenceSha256';
  payload_machine_fetch_allowed := (payload_source ->> 'machineFetchAllowed')::boolean;
  payload_local_processing_allowed := (payload_source ->> 'localProcessingAllowed')::boolean;
  payload_external_embedding_allowed := (payload_source ->> 'externalEmbeddingAllowed')::boolean;
  payload_external_generation_allowed := (payload_source ->> 'externalGenerationAllowed')::boolean;
  payload_external_processing_eligible := (payload_source ->> 'externalProcessingEligible')::boolean;
  payload_citation_title := payload_source ->> 'citationTitle';
  SELECT coalesce(array_agg(topic.value ORDER BY topic.ordinality), ARRAY[]::text[])
  INTO payload_topics
  FROM jsonb_array_elements_text(payload_source -> 'retrievalTopics') WITH ORDINALITY AS topic(value, ordinality);

  IF payload_document_id !~ '^doc_[a-z0-9][a-z0-9_-]{10,95}$'
     OR payload_source_id !~ '^src_[a-z0-9][a-z0-9_-]{2,95}$'
     OR payload_source_revision_id !~ '^srv_[a-z0-9][a-z0-9_-]{2,95}$'
     OR payload_source_revision_sha256 !~ '^[0-9a-f]{64}$'
     OR payload_raw_content_sha256 !~ '^[0-9a-f]{64}$'
     OR payload_normalized_document_ir_sha256 !~ '^[0-9a-f]{64}$'
     OR payload_canonical_text_sha256 !~ '^[0-9a-f]{64}$'
     OR payload_canonical_text IS NULL
     OR octet_length(payload_canonical_text) NOT BETWEEN 1 AND 16777216
     OR payload_canonical_text_sha256 <> encode(digest(payload_canonical_text, 'sha256'), 'hex')
     OR payload_mime_type IS NULL
     OR payload_parser_version IS NULL
     OR payload_tokenizer_version IS NULL
     OR char_length(payload_mime_type) NOT BETWEEN 3 AND 128
     OR char_length(payload_parser_version) NOT BETWEEN 1 AND 128
     OR char_length(payload_tokenizer_version) NOT BETWEEN 1 AND 128
     OR payload_citation_title IS NULL
     OR char_length(payload_citation_title) NOT BETWEEN 1 AND 500
     OR btrim(payload_citation_title) = ''
     OR payload_citation_title ~ '[[:cntrl:]]'
     OR public.rag_v2_immutable_retrieval_topics_are_valid(payload_topics) IS NOT TRUE
     OR public.rag_v2_immutable_locator_is_valid(payload_source_locator) IS NOT TRUE
     OR public.rag_v2_immutable_public_https_url_is_valid(payload_canonical_https_url) IS NOT TRUE
     OR payload_document_ir ->> 'sourceId' IS DISTINCT FROM payload_source_id
     OR payload_document_ir ->> 'sourceRevisionId' IS DISTINCT FROM payload_source_revision_id
     OR payload_document_ir ->> 'mimeType' IS DISTINCT FROM payload_mime_type
     OR payload_document_ir ->> 'rawContentSha256' IS DISTINCT FROM payload_raw_content_sha256
     OR payload_document_ir ->> 'normalizedContentSha256' IS DISTINCT FROM payload_normalized_document_ir_sha256
     OR payload_document_ir -> 'parserEvidence' ->> 'parserVersion' IS DISTINCT FROM payload_parser_version
     OR public.rag_v2_immutable_document_ir_structure_is_valid(payload_document_ir) IS NOT TRUE THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage source identity is invalid'
      USING ERRCODE = '22023';
  END IF;

  IF (payload_scope = 'EXACT30' AND (
        payload_oa_track_id IS NOT NULL
        OR payload_source_card <> 'null'::jsonb
        OR payload_license_evidence_sha256 IS NOT NULL
        OR payload_access_evidence_sha256 IS NOT NULL
        OR payload_machine_fetch_allowed
        OR NOT payload_local_processing_allowed
        OR NOT payload_external_embedding_allowed
        OR NOT payload_external_generation_allowed
        OR NOT payload_external_processing_eligible
        OR payload_source_card_sha256 IS NULL
        OR payload_source_card_sha256 !~ '^[0-9a-f]{64}$'
        OR public.rag_v2_immutable_external_exact30_voyage_source_is_approved(
          payload_source_id,
          payload_canonical_https_url,
          payload_raw_content_sha256,
          payload_source_card_sha256
        ) IS NOT TRUE
      ))
     OR (payload_scope = 'OA112' AND (
        payload_oa_track_id IS NULL
        OR payload_source_card IS NULL
        OR jsonb_typeof(payload_source_card) <> 'object'
        OR public.rag_v2_immutable_oa_source_card_v4_is_valid(payload_source_card) IS NOT TRUE
        OR payload_license_evidence_sha256 !~ '^[0-9a-f]{64}$'
        OR payload_access_evidence_sha256 !~ '^[0-9a-f]{64}$'
        OR NOT payload_machine_fetch_allowed
        OR NOT payload_local_processing_allowed
        OR NOT payload_external_embedding_allowed
        OR NOT payload_external_generation_allowed
        OR payload_source_card ->> 'sourceId' IS DISTINCT FROM payload_source_id
        OR payload_source_card ->> 'canonicalUrl' IS DISTINCT FROM payload_canonical_https_url
        OR payload_source_card ->> 'rawContentSha256' IS DISTINCT FROM payload_raw_content_sha256
        OR payload_source_card ->> 'mimeType' IS DISTINCT FROM payload_mime_type
        OR payload_source_card ->> 'licenseEvidenceDigest' IS DISTINCT FROM payload_license_evidence_sha256
        OR payload_source_card -> 'accessEvidence' ->> 'accessEvidenceDigest' IS DISTINCT FROM payload_access_evidence_sha256
        OR payload_source_card -> 'accessEvidence' ->> 'verificationState' <> 'VERIFIED'
        OR payload_source_card ->> 'activeOa112Eligible' <> 'true'
        OR payload_source_card_sha256 IS NOT NULL
      )) THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage scope metadata is invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-public-voyage|' || payload_generation_id, 0)
  );
  SELECT * INTO expected_generation_record
  FROM public.rag_v2_immutable_component_generations
  WHERE component_generation_id = payload_generation_id
  FOR UPDATE;
  IF NOT FOUND THEN
    INSERT INTO public.rag_v2_immutable_component_generations (
      component_generation_id, owner_user_id, component_scope, embedding_profile_id, state,
      evaluation_status, expected_source_count, expected_chunk_count, actual_source_count,
      actual_chunk_count, generation_hash, manifest_hash
    ) VALUES (
      payload_generation_id, NULL, payload_scope, 'voyage_context_4_1024_v1', 'STAGING',
      'PENDING', payload_expected_source_count, payload_expected_chunk_count, 0,
      0, payload_generation_hash, payload_manifest_hash
    );
  ELSIF expected_generation_record.owner_user_id IS NOT NULL
     OR expected_generation_record.component_scope <> payload_scope
     OR expected_generation_record.embedding_profile_id <> 'voyage_context_4_1024_v1'
     OR expected_generation_record.state <> 'STAGING'
     OR expected_generation_record.evaluation_status <> 'PENDING'
     OR expected_generation_record.expected_source_count <> payload_expected_source_count
     OR expected_generation_record.expected_chunk_count <> payload_expected_chunk_count
     OR expected_generation_record.generation_hash <> payload_generation_hash
     OR expected_generation_record.manifest_hash <> payload_manifest_hash THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage component conflicts'
      USING ERRCODE = '23505';
  END IF;

  SELECT * INTO expected_manifest_record
  FROM public.rag_v2_immutable_public_voyage_component_manifests
  WHERE component_generation_id = payload_generation_id
  FOR UPDATE;
  IF NOT FOUND THEN
    INSERT INTO public.rag_v2_immutable_public_voyage_component_manifests (
      component_generation_id, component_scope, embedding_profile_id, member_digests,
      manifest_hash, generation_hash
    ) VALUES (
      payload_generation_id, payload_scope, 'voyage_context_4_1024_v1', payload_member_digests,
      payload_manifest_hash, payload_generation_hash
    );
  ELSIF expected_manifest_record.component_scope <> payload_scope
     OR expected_manifest_record.embedding_profile_id <> 'voyage_context_4_1024_v1'
     OR expected_manifest_record.member_digests IS DISTINCT FROM payload_member_digests
     OR expected_manifest_record.manifest_hash <> payload_manifest_hash
     OR expected_manifest_record.generation_hash <> payload_generation_hash THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage member manifest conflicts'
      USING ERRCODE = '23505';
  END IF;

  SELECT run.state INTO existing_run_state
  FROM public.rag_v2_immutable_materialization_runs AS run
  WHERE run.materialization_run_id = payload_run_id
    AND run.owner_user_id IS NULL
    AND run.component_generation_id = payload_generation_id
    AND run.component_scope = payload_scope
  FOR UPDATE;
  IF NOT FOUND THEN
    INSERT INTO public.rag_v2_immutable_materialization_runs (
      materialization_run_id, owner_user_id, component_generation_id, component_scope, document_id, state
    ) VALUES (payload_run_id, NULL, payload_generation_id, payload_scope, NULL, 'OPEN');
  ELSIF existing_run_state NOT IN ('OPEN', 'STAGED') THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage run is not resumable'
      USING ERRCODE = '23505';
  END IF;

  -- generation lock alone cannot serialize two refresh generations that carry the
  -- same immutable source. Lock the scope-qualified identity before lookup so the
  -- second transaction observes the first committed source and takes the reuse path.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'rag-v2-immutable-public-voyage-source|' || payload_scope || '|' || payload_source_revision_id,
      0
    )
  );
  SELECT * INTO expected_source_record
  FROM public.rag_v2_immutable_source_revisions
  WHERE source_revision_id = payload_source_revision_id
  FOR UPDATE;
  IF FOUND THEN
    source_was_reused := true;
    IF expected_source_record.owner_user_id IS NOT NULL
       OR expected_source_record.document_id <> payload_document_id
       OR expected_source_record.source_id <> payload_source_id
       OR expected_source_record.source_scope <> payload_scope
       OR expected_source_record.source_revision_sha256 <> payload_source_revision_sha256
       OR expected_source_record.raw_content_sha256 <> payload_raw_content_sha256
       OR expected_source_record.normalized_document_ir_sha256 <> payload_normalized_document_ir_sha256
       OR expected_source_record.canonical_text_sha256 <> payload_canonical_text_sha256
       OR expected_source_record.document_ir <> payload_document_ir
       OR expected_source_record.canonical_text <> payload_canonical_text
       OR expected_source_record.source_locator <> payload_source_locator
       OR expected_source_record.canonical_https_url <> payload_canonical_https_url
       OR expected_source_record.exact30_source_card_sha256 IS DISTINCT FROM
          (CASE WHEN payload_scope = 'EXACT30' THEN payload_source_card_sha256 ELSE NULL END)
       OR expected_source_record.mime_type <> payload_mime_type
       OR expected_source_record.machine_fetch_allowed <> payload_machine_fetch_allowed
       OR expected_source_record.local_processing_allowed <> payload_local_processing_allowed
       OR expected_source_record.external_embedding_allowed <> payload_external_embedding_allowed
       OR expected_source_record.external_generation_allowed <> payload_external_generation_allowed
       OR expected_source_record.external_processing_eligible <> payload_external_processing_eligible
       OR expected_source_record.parser_version <> payload_parser_version
       OR expected_source_record.tokenizer_version <> payload_tokenizer_version
       OR expected_source_record.retrieval_topics <> payload_topics
       OR expected_source_record.citation_title <> payload_citation_title
       OR expected_source_record.oa_track_id IS DISTINCT FROM
          (CASE WHEN payload_scope = 'OA112' THEN payload_oa_track_id ELSE NULL END)
       OR expected_source_record.reserve_source THEN
      RAISE EXCEPTION 'immutable RAG v2 public Voyage source revision conflicts'
        USING ERRCODE = '23505';
    END IF;
    SELECT COUNT(*)::integer INTO existing_membership_count
    FROM public.rag_v2_immutable_generation_memberships AS membership
    WHERE membership.component_generation_id = payload_generation_id
      AND membership.source_revision_id = payload_source_revision_id
      AND membership.component_scope = payload_scope;
  END IF;

  FOR payload_chunk IN
    SELECT value
    FROM jsonb_array_elements(payload_source -> 'chunks') WITH ORDINALITY AS chunks(value, ordinality)
    ORDER BY ordinality
  LOOP
    observed_chunk_count := observed_chunk_count + 1;
    IF jsonb_typeof(payload_chunk) <> 'object'
       OR EXISTS (
         SELECT 1 FROM jsonb_object_keys(payload_chunk) AS chunk_key
         WHERE chunk_key NOT IN (
           'canonicalText', 'canonicalTextSha256', 'chunkId', 'chunkOrdinal',
           'containsTable', 'headingPath', 'locator', 'tokenCount'
         )
       )
       OR NOT (payload_chunk ?& ARRAY[
         'canonicalText', 'canonicalTextSha256', 'chunkId', 'chunkOrdinal',
         'containsTable', 'headingPath', 'locator', 'tokenCount'
       ])
       OR jsonb_typeof(payload_chunk -> 'headingPath') <> 'array'
       OR jsonb_typeof(payload_chunk -> 'locator') <> 'object'
       OR jsonb_typeof(payload_chunk -> 'chunkOrdinal') <> 'number'
       OR jsonb_typeof(payload_chunk -> 'containsTable') <> 'boolean'
       OR jsonb_typeof(payload_chunk -> 'tokenCount') <> 'number'
       OR (payload_chunk -> 'chunkOrdinal')::text !~ '^(0|[1-9][0-9]*)$'
       OR (payload_chunk -> 'tokenCount')::text !~ '^(0|[1-9][0-9]*)$'
       OR EXISTS (
         SELECT 1 FROM jsonb_array_elements(payload_chunk -> 'headingPath') AS heading(value)
         WHERE jsonb_typeof(heading.value) <> 'string'
       ) THEN
      RAISE EXCEPTION 'immutable RAG v2 public Voyage chunk is invalid'
        USING ERRCODE = '22023';
    END IF;
    payload_chunk_id := payload_chunk ->> 'chunkId';
    payload_chunk_ordinal := (payload_chunk ->> 'chunkOrdinal')::integer;
    payload_chunk_text := payload_chunk ->> 'canonicalText';
    payload_chunk_sha256 := payload_chunk ->> 'canonicalTextSha256';
    payload_chunk_locator := payload_chunk -> 'locator';
    payload_chunk_token_count := (payload_chunk ->> 'tokenCount')::integer;
    payload_chunk_contains_table := (payload_chunk ->> 'containsTable')::boolean;
    SELECT coalesce(array_agg(heading.value ORDER BY heading.ordinality), ARRAY[]::text[])
    INTO payload_chunk_heading_path
    FROM jsonb_array_elements_text(payload_chunk -> 'headingPath') WITH ORDINALITY AS heading(value, ordinality);
    IF payload_chunk_id !~ '^rag_v2_chk_[0-9a-f]{32}$'
       OR payload_chunk_ordinal <> observed_chunk_count
       OR payload_chunk_text IS NULL
       OR payload_chunk_sha256 !~ '^[0-9a-f]{64}$'
       OR payload_chunk_sha256 <> encode(digest(payload_chunk_text, 'sha256'), 'hex')
       OR payload_chunk_token_count NOT BETWEEN 1 AND 600
       OR cardinality(payload_chunk_heading_path) > 12
       OR public.rag_v2_immutable_locator_is_valid(payload_chunk_locator) IS NOT TRUE THEN
      RAISE EXCEPTION 'immutable RAG v2 public Voyage chunk contract is invalid'
        USING ERRCODE = '22023';
    END IF;
    IF first_chunk_locator IS NULL THEN
      first_chunk_locator := payload_chunk_locator;
    END IF;
    IF observed_chunk_count = 1 THEN
      observed_source_text := payload_chunk_text;
    ELSE
      observed_source_text := observed_source_text || E'\n\n' || payload_chunk_text;
    END IF;
  END LOOP;
  IF first_chunk_locator IS NULL
     OR payload_source_locator <> first_chunk_locator
     OR payload_canonical_text <> observed_source_text THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage source projection is invalid'
      USING ERRCODE = '22023';
  END IF;

  IF source_was_reused THEN
    IF EXISTS (
      SELECT 1
      FROM jsonb_array_elements(payload_source -> 'chunks') AS supplied(value)
      LEFT JOIN public.rag_v2_immutable_chunks AS stored
        ON stored.chunk_id = supplied.value ->> 'chunkId'
       AND stored.source_revision_id = payload_source_revision_id
       AND stored.owner_user_id IS NULL
       AND stored.source_scope = payload_scope
      WHERE stored.chunk_id IS NULL
         OR stored.chunk_ordinal IS DISTINCT FROM (supplied.value ->> 'chunkOrdinal')::integer
         OR to_jsonb(stored.heading_path) IS DISTINCT FROM supplied.value -> 'headingPath'
         OR stored.locator IS DISTINCT FROM supplied.value -> 'locator'
         OR stored.canonical_text IS DISTINCT FROM supplied.value ->> 'canonicalText'
         OR stored.canonical_text_sha256 IS DISTINCT FROM supplied.value ->> 'canonicalTextSha256'
         OR stored.token_count IS DISTINCT FROM (supplied.value ->> 'tokenCount')::integer
         OR stored.contains_table IS DISTINCT FROM (supplied.value ->> 'containsTable')::boolean
    )
    OR (
      SELECT COUNT(*)::integer
      FROM public.rag_v2_immutable_chunks AS stored
      WHERE stored.source_revision_id = payload_source_revision_id
        AND stored.owner_user_id IS NULL
        AND stored.source_scope = payload_scope
    ) <> observed_chunk_count
    OR (
      payload_scope = 'OA112' AND NOT EXISTS (
        SELECT 1
        FROM public.rag_v2_immutable_oa_source_cards AS card
        WHERE card.source_revision_id = payload_source_revision_id
          AND card.source_scope = 'OA112'
          AND card.source_card = payload_source_card
      )
    ) THEN
      RAISE EXCEPTION 'immutable RAG v2 public Voyage source cache conflicts'
        USING ERRCODE = '23505';
    END IF;
  ELSE
    INSERT INTO public.rag_v2_immutable_source_revisions (
      source_revision_id, document_id, source_id, owner_user_id, source_scope, oa_track_id,
      reserve_source, source_revision_sha256, raw_content_sha256, normalized_document_ir_sha256,
      canonical_text_sha256, document_ir, canonical_text, sanitized_display_name, source_locator,
      canonical_https_url, exact30_source_card_sha256, license_evidence_sha256, access_evidence_sha256, mime_type,
      machine_fetch_allowed, local_processing_allowed, external_embedding_allowed,
      external_generation_allowed, external_processing_eligible, parser_version, tokenizer_version,
      retrieval_topics, citation_title
    ) VALUES (
      payload_source_revision_id, payload_document_id, payload_source_id, NULL, payload_scope,
      CASE WHEN payload_scope = 'OA112' THEN payload_oa_track_id ELSE NULL END,
      false, payload_source_revision_sha256, payload_raw_content_sha256, payload_normalized_document_ir_sha256,
      payload_canonical_text_sha256, payload_document_ir, payload_canonical_text, NULL, payload_source_locator,
      payload_canonical_https_url,
      CASE WHEN payload_scope = 'EXACT30' THEN payload_source_card_sha256 ELSE NULL END,
      CASE WHEN payload_scope = 'OA112' THEN payload_license_evidence_sha256 ELSE NULL END,
      CASE WHEN payload_scope = 'OA112' THEN payload_access_evidence_sha256 ELSE NULL END,
      payload_mime_type, payload_machine_fetch_allowed, payload_local_processing_allowed,
      payload_external_embedding_allowed, payload_external_generation_allowed,
      payload_external_processing_eligible, payload_parser_version, payload_tokenizer_version,
      payload_topics, payload_citation_title
    );
    IF payload_scope = 'OA112' THEN
      INSERT INTO public.rag_v2_immutable_oa_source_cards (
        source_revision_id, source_scope, source_id, source_card, source_card_sha256,
        active_oa112_eligible, title, authors, canonical_https_url, canonical_https_url_sha256,
        identifier_scheme, identifier_value, revision, revision_date, raw_content_sha256, mime_type,
        license_evidence_sha256, access_evidence_sha256, access_checked_at, access_verification_state,
        machine_fetch_allowed, local_processing_allowed, external_embedding_allowed, external_generation_allowed
      ) VALUES (
        payload_source_revision_id, 'OA112', payload_source_id, payload_source_card,
        encode(digest(payload_source_card::text, 'sha256'), 'hex'),
        (payload_source_card ->> 'activeOa112Eligible')::boolean, payload_source_card ->> 'title',
        ARRAY(SELECT value FROM jsonb_array_elements_text(payload_source_card -> 'authors') AS authors(value)),
        payload_canonical_https_url, payload_source_card ->> 'canonicalUrlSha256',
        payload_source_card -> 'identifier' ->> 'scheme', payload_source_card -> 'identifier' ->> 'value',
        payload_source_card ->> 'revision', (payload_source_card ->> 'revisionDate')::date,
        payload_raw_content_sha256, payload_mime_type, payload_license_evidence_sha256,
        payload_access_evidence_sha256, payload_source_card -> 'accessEvidence' ->> 'accessCheckedAt',
        payload_source_card -> 'accessEvidence' ->> 'verificationState', payload_machine_fetch_allowed,
        payload_local_processing_allowed, payload_external_embedding_allowed, payload_external_generation_allowed
      );
    END IF;

    FOR payload_chunk IN
      SELECT value
      FROM jsonb_array_elements(payload_source -> 'chunks') WITH ORDINALITY AS chunks(value, ordinality)
      ORDER BY ordinality
    LOOP
      SELECT coalesce(array_agg(heading.value ORDER BY heading.ordinality), ARRAY[]::text[])
      INTO payload_chunk_heading_path
      FROM jsonb_array_elements_text(payload_chunk -> 'headingPath') WITH ORDINALITY AS heading(value, ordinality);
      INSERT INTO public.rag_v2_immutable_chunks (
        chunk_id, source_revision_id, owner_user_id, source_scope, chunk_ordinal, heading_path,
        locator, canonical_text, canonical_text_sha256, token_count, contains_table
      ) VALUES (
        payload_chunk ->> 'chunkId', payload_source_revision_id, NULL, payload_scope,
        (payload_chunk ->> 'chunkOrdinal')::integer, payload_chunk_heading_path,
        payload_chunk -> 'locator', payload_chunk ->> 'canonicalText', payload_chunk ->> 'canonicalTextSha256',
        (payload_chunk ->> 'tokenCount')::integer, (payload_chunk ->> 'containsTable')::boolean
      );
    END LOOP;
  END IF;

  IF source_was_reused AND existing_membership_count > 0 THEN
    IF existing_membership_count <> observed_chunk_count
       OR (
         SELECT COUNT(*)
         FROM public.rag_v2_immutable_generation_embeddings AS embedding
         WHERE embedding.component_generation_id = payload_generation_id
           AND embedding.component_scope = payload_scope
           AND embedding.chunk_id IN (
             SELECT membership.chunk_id
             FROM public.rag_v2_immutable_generation_memberships AS membership
             WHERE membership.component_generation_id = payload_generation_id
               AND membership.source_revision_id = payload_source_revision_id
               AND membership.component_scope = payload_scope
           )
       ) <> existing_membership_count THEN
      RAISE EXCEPTION 'immutable RAG v2 public Voyage source resume is incomplete'
        USING ERRCODE = '23505';
    END IF;
    IF EXISTS (
      SELECT 1
      FROM jsonb_array_elements(payload_source -> 'embeddings') AS supplied(value)
      LEFT JOIN public.rag_v2_immutable_generation_embeddings AS stored
        ON stored.component_generation_id = payload_generation_id
       AND stored.chunk_id = supplied.value ->> 'chunkId'
       AND stored.component_scope = payload_scope
       AND stored.owner_user_id IS NULL
      WHERE stored.chunk_id IS NULL
         OR stored.embedding_input_hash IS DISTINCT FROM supplied.value ->> 'embeddingInputHash'
         OR stored.context_set_hash IS DISTINCT FROM supplied.value ->> 'contextSetHash'
    ) THEN
      RAISE EXCEPTION 'immutable RAG v2 public Voyage source resume embedding conflicts'
        USING ERRCODE = '23505';
    END IF;
    payload_source_member_digest := public.rag_v2_immutable_public_voyage_source_member_digest(
      payload_generation_id,
      payload_source_revision_id
    );
    IF payload_source_member_digest IS NULL
       OR array_position(payload_member_digests, payload_source_member_digest) IS NULL THEN
      RAISE EXCEPTION 'immutable RAG v2 public Voyage resumed source is outside the bound member manifest'
        USING ERRCODE = '23514';
    END IF;
    SELECT COUNT(DISTINCT membership.source_revision_id)::integer, COUNT(*)::integer
    INTO observed_source_total, observed_chunk_total
    FROM public.rag_v2_immutable_generation_memberships AS membership
    WHERE membership.component_generation_id = payload_generation_id
      AND membership.component_scope = payload_scope;
    RETURN QUERY SELECT payload_generation_id, payload_run_id,
      CASE WHEN observed_source_total = payload_expected_source_count AND observed_chunk_total = payload_expected_chunk_count THEN 'STAGED' ELSE 'STAGING' END,
      true, observed_source_total, observed_chunk_total;
    RETURN;
  END IF;

  FOR payload_chunk IN
    SELECT value
    FROM jsonb_array_elements(payload_source -> 'chunks') WITH ORDINALITY AS chunks(value, ordinality)
    ORDER BY ordinality
  LOOP
    INSERT INTO public.rag_v2_immutable_generation_memberships (
      component_generation_id, chunk_id, source_revision_id, owner_user_id, component_scope, ordinal
    ) VALUES (
      payload_generation_id, payload_chunk ->> 'chunkId', payload_source_revision_id, NULL,
      payload_scope,
      (
        SELECT COUNT(*)::integer
        FROM public.rag_v2_immutable_generation_memberships AS previous_membership
        WHERE previous_membership.component_generation_id = payload_generation_id
      ) + 1
    );
  END LOOP;

  FOR payload_embedding IN
    SELECT value
    FROM jsonb_array_elements(payload_source -> 'embeddings') WITH ORDINALITY AS embeddings(value, ordinality)
    ORDER BY ordinality
  LOOP
    observed_embedding_count := observed_embedding_count + 1;
    IF jsonb_typeof(payload_embedding) <> 'object'
       OR EXISTS (
         SELECT 1 FROM jsonb_object_keys(payload_embedding) AS embedding_key
         WHERE embedding_key NOT IN ('chunkId', 'contextSetHash', 'embedding', 'embeddingInputHash')
       )
       OR NOT (payload_embedding ?& ARRAY['chunkId', 'contextSetHash', 'embedding', 'embeddingInputHash'])
       OR jsonb_typeof(payload_embedding -> 'chunkId') <> 'string'
       OR jsonb_typeof(payload_embedding -> 'contextSetHash') <> 'string'
       OR jsonb_typeof(payload_embedding -> 'embeddingInputHash') <> 'string'
       OR jsonb_typeof(payload_embedding -> 'embedding') <> 'array'
       OR jsonb_array_length(payload_embedding -> 'embedding') <> 1024
       OR EXISTS (
         SELECT 1 FROM jsonb_array_elements(payload_embedding -> 'embedding') AS coordinate(value)
         WHERE jsonb_typeof(coordinate.value) <> 'number'
       ) THEN
      RAISE EXCEPTION 'immutable RAG v2 public Voyage embedding is invalid'
        USING ERRCODE = '22023';
    END IF;
    payload_chunk_id := payload_embedding ->> 'chunkId';
    payload_embedding_input_hash := payload_embedding ->> 'embeddingInputHash';
    payload_context_set_hash := payload_embedding ->> 'contextSetHash';
    IF payload_chunk_id !~ '^rag_v2_chk_[0-9a-f]{32}$'
       OR payload_embedding_input_hash !~ '^[0-9a-f]{64}$'
       OR payload_context_set_hash !~ '^[0-9a-f]{64}$'
       OR NOT EXISTS (
         SELECT 1 FROM public.rag_v2_immutable_generation_memberships AS membership
         WHERE membership.component_generation_id = payload_generation_id
           AND membership.chunk_id = payload_chunk_id
           AND membership.source_revision_id = payload_source_revision_id
           AND membership.component_scope = payload_scope
       ) THEN
      RAISE EXCEPTION 'immutable RAG v2 public Voyage embedding identity is invalid'
        USING ERRCODE = '22023';
    END IF;
    IF expected_context_set_hash IS NULL THEN
      expected_context_set_hash := payload_context_set_hash;
    ELSIF payload_context_set_hash IS DISTINCT FROM expected_context_set_hash THEN
      RAISE EXCEPTION 'immutable RAG v2 public Voyage source context group drifted'
        USING ERRCODE = '23514';
    END IF;
    payload_embedding_vector := ((payload_embedding -> 'embedding')::text)::vector;
    IF vector_dims(payload_embedding_vector) <> 1024
       OR vector_norm(payload_embedding_vector)::text IN ('NaN', 'Infinity', '-Infinity')
       OR abs(vector_norm(payload_embedding_vector)::double precision - 1.0) > 0.00001 THEN
      RAISE EXCEPTION 'immutable RAG v2 public Voyage vector is invalid'
        USING ERRCODE = '22023';
    END IF;
    -- 다른 generation이 같은 immutable chunk/profile cache를 동시에 채우면 one writer만 NEW로
    -- 기록하고 나머지는 같은 cached vector를 REUSED로 참조하게 source+hash key로 직렬화한다.
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended(
        'rag-v2-immutable-public-voyage-cache|' || payload_scope || '|' ||
        payload_chunk_id || '|' || payload_embedding_input_hash || '|' || payload_context_set_hash,
        0
      )
    );
    SELECT cache.embedding
    INTO cached_embedding_vector
    FROM public.rag_v2_immutable_embedding_cache AS cache
    WHERE cache.owner_user_id IS NULL
      AND cache.chunk_id = payload_chunk_id
      AND cache.source_scope = payload_scope
      AND cache.embedding_profile_id = 'voyage_context_4_1024_v1'
      AND cache.embedding_input_hash = payload_embedding_input_hash
      AND cache.context_set_hash = payload_context_set_hash
    FOR SHARE;
    embedding_was_reused := FOUND;
    IF embedding_was_reused THEN
      payload_embedding_vector := cached_embedding_vector;
      reused_embedding_count := reused_embedding_count + 1;
    END IF;
    INSERT INTO public.rag_v2_immutable_generation_embeddings (
      component_generation_id, chunk_id, owner_user_id, component_scope, embedding_profile_id,
      embedding_input_hash, context_set_hash, embedding
    ) VALUES (
      payload_generation_id, payload_chunk_id, NULL, payload_scope, 'voyage_context_4_1024_v1',
      payload_embedding_input_hash, payload_context_set_hash, payload_embedding_vector
    );
    IF NOT embedding_was_reused THEN
      INSERT INTO public.rag_v2_immutable_embedding_cache (
        cache_id, owner_user_id, source_revision_id, chunk_id, source_scope, embedding_profile_id,
        embedding_input_hash, context_set_hash, embedding
      ) VALUES (
        'rgr_cache_' || substr(
          encode(digest('rag-v2-immutable-public-voyage-cache|' || payload_generation_id || '|' || payload_chunk_id || '|' || payload_context_set_hash, 'sha256'), 'hex'),
          1, 32
        ),
        NULL, payload_source_revision_id, payload_chunk_id, payload_scope, 'voyage_context_4_1024_v1',
        payload_embedding_input_hash, payload_context_set_hash, payload_embedding_vector
      );
    END IF;
    INSERT INTO public.rag_v2_immutable_embedding_receipts (
      receipt_id, materialization_run_id, owner_user_id, source_scope, component_generation_id,
      chunk_id, embedding_profile_id, embedding_input_hash, context_set_hash, reuse_state
    ) VALUES (
      'rgr_emb_' || substr(
        encode(digest('rag-v2-immutable-public-voyage-embedding-receipt|' || payload_run_id || '|' || payload_chunk_id, 'sha256'), 'hex'),
        1, 32
      ),
      payload_run_id, NULL, payload_scope, payload_generation_id, payload_chunk_id,
      'voyage_context_4_1024_v1', payload_embedding_input_hash, payload_context_set_hash,
      CASE WHEN embedding_was_reused THEN 'REUSED' ELSE 'NEW' END
    );
  END LOOP;
  IF observed_embedding_count <> observed_chunk_count THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage embedding count is invalid'
      USING ERRCODE = '22023';
  END IF;
  payload_source_member_digest := public.rag_v2_immutable_public_voyage_source_member_digest(
    payload_generation_id,
    payload_source_revision_id
  );
  IF payload_source_member_digest IS NULL
     OR array_position(payload_member_digests, payload_source_member_digest) IS NULL THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage source is outside the bound member manifest'
      USING ERRCODE = '23514';
  END IF;

  INSERT INTO public.rag_v2_immutable_source_receipts (
    receipt_id, materialization_run_id, owner_user_id, source_scope, source_revision_id,
    raw_content_sha256, canonical_text_sha256, reuse_state
  ) VALUES (
    'rgr_src_' || substr(
      encode(digest('rag-v2-immutable-public-voyage-source-receipt|' || payload_run_id || '|' || payload_source_revision_id, 'sha256'), 'hex'),
      1, 32
    ),
    payload_run_id, NULL, payload_scope, payload_source_revision_id,
    payload_raw_content_sha256, payload_canonical_text_sha256,
    CASE WHEN source_was_reused THEN 'REUSED' ELSE 'NEW' END
  );
  FOR payload_chunk IN
    SELECT value
    FROM jsonb_array_elements(payload_source -> 'chunks') WITH ORDINALITY AS chunks(value, ordinality)
    ORDER BY ordinality
  LOOP
    INSERT INTO public.rag_v2_immutable_chunk_receipts (
      receipt_id, materialization_run_id, owner_user_id, source_scope, source_revision_id,
      chunk_id, canonical_text_sha256, reuse_state
    ) VALUES (
      'rgr_chk_' || substr(
        encode(digest('rag-v2-immutable-public-voyage-chunk-receipt|' || payload_run_id || '|' || (payload_chunk ->> 'chunkId'), 'sha256'), 'hex'),
        1, 32
      ),
      payload_run_id, NULL, payload_scope, payload_source_revision_id,
      payload_chunk ->> 'chunkId', payload_chunk ->> 'canonicalTextSha256',
      CASE WHEN source_was_reused THEN 'REUSED' ELSE 'NEW' END
    );
  END LOOP;

  UPDATE public.rag_v2_immutable_materialization_runs AS run
  SET source_reused_count = run.source_reused_count + CASE WHEN source_was_reused THEN 1 ELSE 0 END,
      chunk_reused_count = run.chunk_reused_count + CASE WHEN source_was_reused THEN observed_chunk_count ELSE 0 END,
      embedding_reused_count = run.embedding_reused_count + reused_embedding_count
  WHERE run.materialization_run_id = payload_run_id
    AND run.owner_user_id IS NULL
    AND run.component_scope = payload_scope
    AND run.state = 'OPEN';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage reuse receipt transition failed'
      USING ERRCODE = '23514';
  END IF;

  SELECT COUNT(DISTINCT membership.source_revision_id)::integer, COUNT(*)::integer
  INTO observed_source_total, observed_chunk_total
  FROM public.rag_v2_immutable_generation_memberships AS membership
  WHERE membership.component_generation_id = payload_generation_id
    AND membership.component_scope = payload_scope;
  IF observed_source_total > payload_expected_source_count
     OR observed_chunk_total > payload_expected_chunk_count THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage component exceeded exact membership'
      USING ERRCODE = '23514';
  END IF;
  UPDATE public.rag_v2_immutable_component_generations
  SET actual_source_count = observed_source_total,
      actual_chunk_count = observed_chunk_total
  WHERE component_generation_id = payload_generation_id
    AND owner_user_id IS NULL
    AND component_scope = payload_scope
    AND state = 'STAGING'
    AND evaluation_status = 'PENDING';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage component transition failed'
      USING ERRCODE = '23514';
  END IF;
  IF observed_source_total = payload_expected_source_count
     AND observed_chunk_total = payload_expected_chunk_count THEN
    complete_state := 'STAGED';
    UPDATE public.rag_v2_immutable_materialization_runs
    SET state = 'STAGED'
    WHERE materialization_run_id = payload_run_id
      AND owner_user_id IS NULL
      AND component_scope = payload_scope
      AND state = 'OPEN';
  END IF;
  RETURN QUERY SELECT payload_generation_id, payload_run_id, complete_state,
    source_was_reused, observed_source_total, observed_chunk_total;
END;
$stage_rag_v2_immutable_public_voyage_document$;
ALTER FUNCTION stage_rag_v2_immutable_public_voyage_document(jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION stage_rag_v2_immutable_public_voyage_document(jsonb) FROM PUBLIC;

CREATE FUNCTION evaluate_rag_v2_immutable_public_voyage_component(
  p_component_generation_id text,
  p_evaluation jsonb
)
RETURNS TABLE (
  component_generation_id text,
  state text,
  source_count integer,
  chunk_count integer
)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $evaluate_rag_v2_immutable_public_voyage_component$
#variable_conflict use_column
DECLARE
  generation_record public.rag_v2_immutable_component_generations%ROWTYPE;
  submitted_evaluation_digest text;
  submitted_exact_top5_hit_rate double precision;
  submitted_track_recall_at5 double precision;
  submitted_citation_coverage double precision;
  submitted_direct_advice_block_rate double precision;
  submitted_cross_owner_leak_count integer;
  submitted_mixed_profile_row_count integer;
  submitted_owner_delete_residual_row_count integer;
  submitted_warm_p95_millis double precision;
  submitted_provider_physical_call_count integer;
  observed_source_total integer;
  observed_chunk_total integer;
  observed_embedding_total integer;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_component_generation_id !~ '^rgr_[0-9a-f]{32}$'
     OR p_evaluation IS NULL
     OR jsonb_typeof(p_evaluation) <> 'object'
     OR octet_length(p_evaluation::text) NOT BETWEEN 2 AND 8192
     OR EXISTS (
       SELECT 1 FROM jsonb_object_keys(p_evaluation) AS evaluation_key
       WHERE evaluation_key NOT IN (
         'citationCoverage', 'crossOwnerLeakCount', 'directAdviceBlockRate', 'evaluationDigest',
         'exactTop5HitRate', 'mixedProfileRowCount', 'ownerDeleteResidualRowCount',
         'providerPhysicalCallCount', 'schemaVersion', 'trackRecallAt5', 'warmP95Millis'
       )
     )
     OR NOT (p_evaluation ?& ARRAY[
       'citationCoverage', 'crossOwnerLeakCount', 'directAdviceBlockRate', 'evaluationDigest',
       'exactTop5HitRate', 'mixedProfileRowCount', 'ownerDeleteResidualRowCount',
       'providerPhysicalCallCount', 'schemaVersion', 'trackRecallAt5', 'warmP95Millis'
     ])
     OR jsonb_typeof(p_evaluation -> 'schemaVersion') <> 'number'
     OR p_evaluation ->> 'schemaVersion' <> '1'
     OR jsonb_typeof(p_evaluation -> 'evaluationDigest') <> 'string'
     OR EXISTS (
       SELECT 1
       FROM jsonb_each(p_evaluation) AS field(key, value)
       WHERE field.key NOT IN ('schemaVersion', 'evaluationDigest')
         AND jsonb_typeof(field.value) <> 'number'
     )
     OR EXISTS (
       SELECT 1
       FROM jsonb_each(p_evaluation) AS field(key, value)
       WHERE field.key IN (
         'crossOwnerLeakCount', 'mixedProfileRowCount', 'ownerDeleteResidualRowCount',
         'providerPhysicalCallCount'
       )
         AND field.value::text !~ '^(0|[1-9][0-9]*)$'
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage evaluation arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  submitted_evaluation_digest := p_evaluation ->> 'evaluationDigest';
  submitted_exact_top5_hit_rate := (p_evaluation ->> 'exactTop5HitRate')::double precision;
  submitted_track_recall_at5 := (p_evaluation ->> 'trackRecallAt5')::double precision;
  submitted_citation_coverage := (p_evaluation ->> 'citationCoverage')::double precision;
  submitted_direct_advice_block_rate := (p_evaluation ->> 'directAdviceBlockRate')::double precision;
  submitted_cross_owner_leak_count := (p_evaluation ->> 'crossOwnerLeakCount')::integer;
  submitted_mixed_profile_row_count := (p_evaluation ->> 'mixedProfileRowCount')::integer;
  submitted_owner_delete_residual_row_count := (p_evaluation ->> 'ownerDeleteResidualRowCount')::integer;
  submitted_warm_p95_millis := (p_evaluation ->> 'warmP95Millis')::double precision;
  submitted_provider_physical_call_count := (p_evaluation ->> 'providerPhysicalCallCount')::integer;
  -- Voyage vector space는 같은 profile의 query embedding으로만 평가한다. Exact-30 fixture는
  -- 10개, OA112 manifest는 112개의 packet-bound one-shot query를 요구하므로, 0을 강제하면
  -- 실제 profile evaluation을 BGE나 임의 local vector로 대체하게 된다.
  SELECT * INTO generation_record
  FROM public.rag_v2_immutable_component_generations
  WHERE component_generation_id = p_component_generation_id
  FOR UPDATE;
  IF NOT FOUND
     OR generation_record.owner_user_id IS NOT NULL
     OR generation_record.component_scope NOT IN ('EXACT30', 'OA112')
     OR generation_record.embedding_profile_id <> 'voyage_context_4_1024_v1' THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage component was not found'
      USING ERRCODE = '23514';
  END IF;
  IF submitted_evaluation_digest IS NULL
     OR submitted_evaluation_digest !~ '^[0-9a-f]{64}$'
     OR submitted_exact_top5_hit_rate <> 1.0
     OR submitted_track_recall_at5 < 0.80
     OR submitted_citation_coverage < 0.80
     OR submitted_direct_advice_block_rate <> 1.0
     OR submitted_cross_owner_leak_count <> 0
     OR submitted_mixed_profile_row_count <> 0
     OR submitted_owner_delete_residual_row_count <> 0
     OR submitted_warm_p95_millis <= 0
     OR submitted_warm_p95_millis >= 8000
     OR submitted_provider_physical_call_count <> (
       CASE generation_record.component_scope
         WHEN 'EXACT30' THEN 10
         WHEN 'OA112' THEN 112
         ELSE -1
       END
     ) THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage evaluation thresholds failed'
      USING ERRCODE = '23514';
  END IF;
  IF public.rag_v2_immutable_public_voyage_component_hashes_are_valid(
    p_component_generation_id
  ) IS NOT TRUE THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage component hash projection is invalid'
      USING ERRCODE = '23514';
  END IF;
  IF generation_record.state = 'EVALUATED'
     AND generation_record.evaluation_status = 'PASSED' THEN
    IF NOT EXISTS (
      SELECT 1
      FROM public.rag_v2_immutable_public_voyage_component_evaluations AS evaluation
      WHERE evaluation.component_generation_id = p_component_generation_id
        AND evaluation.evaluation_digest = submitted_evaluation_digest
        AND evaluation.exact_top5_hit_rate = submitted_exact_top5_hit_rate
        AND evaluation.track_recall_at5 = submitted_track_recall_at5
        AND evaluation.citation_coverage = submitted_citation_coverage
        AND evaluation.direct_advice_block_rate = submitted_direct_advice_block_rate
        AND evaluation.cross_owner_leak_count = submitted_cross_owner_leak_count
        AND evaluation.mixed_profile_row_count = submitted_mixed_profile_row_count
        AND evaluation.owner_delete_residual_row_count = submitted_owner_delete_residual_row_count
        AND evaluation.warm_p95_millis = submitted_warm_p95_millis
        AND evaluation.provider_physical_call_count = submitted_provider_physical_call_count
    ) THEN
      RAISE EXCEPTION 'immutable RAG v2 public Voyage evaluation conflicts'
        USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT p_component_generation_id, 'EVALUATED',
      generation_record.actual_source_count, generation_record.actual_chunk_count;
    RETURN;
  END IF;
  IF generation_record.state <> 'STAGING'
     OR generation_record.evaluation_status <> 'PENDING'
     OR generation_record.actual_source_count <> generation_record.expected_source_count
     OR generation_record.actual_chunk_count <> generation_record.expected_chunk_count THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage component is not complete'
      USING ERRCODE = '23514';
  END IF;
  SELECT COUNT(DISTINCT membership.source_revision_id)::integer, COUNT(*)::integer, COUNT(embedding.chunk_id)::integer
  INTO observed_source_total, observed_chunk_total, observed_embedding_total
  FROM public.rag_v2_immutable_generation_memberships AS membership
  LEFT JOIN public.rag_v2_immutable_generation_embeddings AS embedding
    ON embedding.component_generation_id = membership.component_generation_id
   AND embedding.chunk_id = membership.chunk_id
   AND embedding.component_scope = membership.component_scope
  WHERE membership.component_generation_id = p_component_generation_id
    AND membership.component_scope = generation_record.component_scope;
  IF observed_source_total <> generation_record.expected_source_count
     OR observed_chunk_total <> generation_record.expected_chunk_count
     OR observed_embedding_total <> generation_record.expected_chunk_count
     OR (generation_record.component_scope = 'OA112' AND EXISTS (
       SELECT 1
       FROM public.rag_v2_immutable_generation_memberships AS membership
       JOIN public.rag_v2_immutable_source_revisions AS source
         ON source.source_revision_id = membership.source_revision_id
       LEFT JOIN public.rag_v2_immutable_oa_source_cards AS card
         ON card.source_revision_id = source.source_revision_id
        AND card.source_scope = 'OA112'
       WHERE membership.component_generation_id = p_component_generation_id
         AND (
           source.oa_track_id IS NULL
           OR source.reserve_source
           OR NOT source.machine_fetch_allowed
           OR NOT source.local_processing_allowed
           OR NOT source.external_embedding_allowed
           OR NOT source.external_generation_allowed
           OR card.source_revision_id IS NULL
           OR NOT card.active_oa112_eligible
         )
     )) THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage persisted component is invalid'
      USING ERRCODE = '23514';
  END IF;
  INSERT INTO public.rag_v2_immutable_public_voyage_component_evaluations (
    component_generation_id, component_scope, embedding_profile_id, evaluation_digest,
    exact_top5_hit_rate, track_recall_at5, citation_coverage, direct_advice_block_rate,
    cross_owner_leak_count, mixed_profile_row_count, owner_delete_residual_row_count,
    warm_p95_millis, provider_physical_call_count
  ) VALUES (
    p_component_generation_id, generation_record.component_scope, 'voyage_context_4_1024_v1', submitted_evaluation_digest,
    submitted_exact_top5_hit_rate, submitted_track_recall_at5, submitted_citation_coverage, submitted_direct_advice_block_rate,
    submitted_cross_owner_leak_count, submitted_mixed_profile_row_count, submitted_owner_delete_residual_row_count,
    submitted_warm_p95_millis, submitted_provider_physical_call_count
  );
  UPDATE public.rag_v2_immutable_component_generations
  SET state = 'EVALUATED', evaluation_status = 'PASSED', evaluated_at = clock_timestamp()
  WHERE component_generation_id = p_component_generation_id
    AND state = 'STAGING'
    AND evaluation_status = 'PENDING';
  UPDATE public.rag_v2_immutable_materialization_runs
  SET state = 'EVALUATED', completed_at = clock_timestamp()
  WHERE component_generation_id = p_component_generation_id
    AND owner_user_id IS NULL
    AND component_scope = generation_record.component_scope
    AND state = 'STAGED';
  RETURN QUERY SELECT p_component_generation_id, 'EVALUATED', observed_source_total, observed_chunk_total;
END;
$evaluate_rag_v2_immutable_public_voyage_component$;
ALTER FUNCTION evaluate_rag_v2_immutable_public_voyage_component(text, jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION evaluate_rag_v2_immutable_public_voyage_component(text, jsonb) FROM PUBLIC;

-- V25 activation remains the only public-pointer CAS. V36 adds a narrow transition guard so an
-- immutable manifest cannot be evaluated or activated after its persisted source/chunk projection drifts.
CREATE FUNCTION guard_rag_v2_immutable_public_voyage_component_hash_transition()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $guard_rag_v2_immutable_public_voyage_component_hash_transition$
BEGIN
  IF NEW.component_scope IN ('EXACT30', 'OA112')
     AND NEW.embedding_profile_id = 'voyage_context_4_1024_v1'
     AND NEW.state IN ('EVALUATED', 'ACTIVE')
     AND EXISTS (
       SELECT 1
       FROM public.rag_v2_immutable_public_voyage_component_manifests AS manifest
       WHERE manifest.component_generation_id = NEW.component_generation_id
     )
     AND public.rag_v2_immutable_public_voyage_component_hashes_are_valid(
       NEW.component_generation_id
     ) IS NOT TRUE THEN
    RAISE EXCEPTION 'immutable RAG v2 public Voyage component hash transition is invalid'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$guard_rag_v2_immutable_public_voyage_component_hash_transition$;
ALTER FUNCTION guard_rag_v2_immutable_public_voyage_component_hash_transition() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION guard_rag_v2_immutable_public_voyage_component_hash_transition() FROM PUBLIC;

CREATE TRIGGER rag_v2_immutable_public_voyage_component_hash_transition_guard
  BEFORE UPDATE OF state
  ON rag_v2_immutable_component_generations
  FOR EACH ROW
  EXECUTE FUNCTION guard_rag_v2_immutable_public_voyage_component_hash_transition();

DO $rag_v2_public_voyage_staging_writer_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      rag_v2_immutable_oa_source_cards,
      rag_v2_immutable_source_revisions,
      rag_v2_immutable_chunks,
      rag_v2_immutable_component_generations,
      rag_v2_immutable_generation_memberships,
      rag_v2_immutable_generation_embeddings,
      rag_v2_immutable_embedding_cache,
      rag_v2_immutable_materialization_runs,
      rag_v2_immutable_source_receipts,
      rag_v2_immutable_chunk_receipts,
      rag_v2_immutable_embedding_receipts,
      rag_v2_immutable_public_voyage_component_evaluations,
      rag_v2_immutable_public_voyage_component_manifests
    FROM decision_rag_writer;
    GRANT EXECUTE ON FUNCTION stage_rag_v2_immutable_public_voyage_document(jsonb)
      TO decision_rag_writer;
    GRANT EXECUTE ON FUNCTION evaluate_rag_v2_immutable_public_voyage_component(text, jsonb)
      TO decision_rag_writer;
  END IF;
END;
$rag_v2_public_voyage_staging_writer_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION stage_rag_v2_immutable_public_voyage_document(jsonb) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION evaluate_rag_v2_immutable_public_voyage_component(text, jsonb) FROM PUBLIC;
