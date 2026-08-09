-- V42는 reservation 이후 revoke·scope expiry·bundle pointer 전환·owner hard-delete가 발생해도
-- OAuth/generateContent attempt를 만들지 않는다. ledger에는 citation identity와 chunk SHA만 남기며
-- canonical text·prompt·provider payload는 저장하지 않는다.

DO $reject_pre_live_vertex_usage_for_claim_recheck$
BEGIN
  IF EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_reservations)
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_token_attempts)
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_generate_content_attempts)
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_outcomes) THEN
    RAISE EXCEPTION 'Pre-S5 Vertex claim-time recheck requires an empty pre-live usage ledger';
  END IF;
END
$reject_pre_live_vertex_usage_for_claim_recheck$;

CREATE FUNCTION public.rag_v2_immutable_vertex_evidence_manifest_is_valid(
  p_evidence_manifest jsonb
)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog, public
AS $rag_v2_immutable_vertex_evidence_manifest_is_valid$
DECLARE
  evidence_item jsonb;
  expected_ordinal integer := 1;
  seen_chunk_ids text[] := ARRAY[]::text[];
BEGIN
  IF jsonb_typeof(p_evidence_manifest) <> 'array'
     OR jsonb_array_length(p_evidence_manifest) NOT BETWEEN 1 AND 5
     OR octet_length(p_evidence_manifest::text) > 2048 THEN
    RETURN false;
  END IF;

  FOR evidence_item IN SELECT value FROM jsonb_array_elements(p_evidence_manifest)
  LOOP
    IF jsonb_typeof(evidence_item) <> 'object'
       OR NOT (evidence_item ?& ARRAY['ordinal', 'citationId', 'chunkRevisionId', 'canonicalTextSha256'])
       OR EXISTS (
         SELECT 1
         FROM jsonb_object_keys(evidence_item) AS key_name
         WHERE key_name NOT IN ('ordinal', 'citationId', 'chunkRevisionId', 'canonicalTextSha256')
       )
       OR jsonb_typeof(evidence_item -> 'ordinal') <> 'number'
       OR evidence_item ->> 'ordinal' <> expected_ordinal::text
       OR jsonb_typeof(evidence_item -> 'citationId') <> 'string'
       OR evidence_item ->> 'citationId' <> ('cit_' || expected_ordinal::text)
       OR jsonb_typeof(evidence_item -> 'chunkRevisionId') <> 'string'
       OR evidence_item ->> 'chunkRevisionId' !~ '^rag_v2_chk_[0-9a-f]{32}$'
       OR jsonb_typeof(evidence_item -> 'canonicalTextSha256') <> 'string'
       OR evidence_item ->> 'canonicalTextSha256' !~ '^[0-9a-f]{64}$'
       OR evidence_item ->> 'chunkRevisionId' = ANY(seen_chunk_ids) THEN
      RETURN false;
    END IF;
    seen_chunk_ids := array_append(seen_chunk_ids, evidence_item ->> 'chunkRevisionId');
    expected_ordinal := expected_ordinal + 1;
  END LOOP;
  RETURN true;
END
$rag_v2_immutable_vertex_evidence_manifest_is_valid$;
ALTER FUNCTION public.rag_v2_immutable_vertex_evidence_manifest_is_valid(jsonb) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.rag_v2_immutable_vertex_evidence_manifest_is_valid(jsonb) FROM PUBLIC;

ALTER TABLE public.rag_v2_immutable_vertex_usage_reservations
  ADD COLUMN evidence_manifest jsonb NOT NULL;
ALTER TABLE public.rag_v2_immutable_vertex_usage_reservations
  ADD CONSTRAINT rag_v2_immutable_vertex_usage_reservation_evidence_manifest_check
  CHECK (public.rag_v2_immutable_vertex_evidence_manifest_is_valid(evidence_manifest));

-- 이 helper는 public API가 아니다. claim transaction은 activation lock 다음 consent lock을 같은 순서로
-- 잡고 pointer/rights가 reservation 당시와 동일하게 current인지 확인한 뒤에만 attempt receipt를 append한다.
CREATE FUNCTION public.assert_rag_v2_immutable_vertex_reservation_is_current(
  p_reservation public.rag_v2_immutable_vertex_usage_reservations
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $assert_rag_v2_immutable_vertex_reservation_is_current$
#variable_conflict use_column
DECLARE
  effective_consent public.rag_v2_immutable_consent_events%ROWTYPE;
  scope_row public.rag_v2_retrieval_scope_claims%ROWTYPE;
  public_pointer public.rag_v2_immutable_public_bundle_pointers%ROWTYPE;
  owner_pointer public.rag_v2_immutable_owner_bundle_pointers%ROWTYPE;
  owner_bundle public.rag_v2_immutable_bundles%ROWTYPE;
  evidence_item jsonb;
  evidence_row record;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_reservation.owner_user_id
     OR p_reservation.expires_at <= statement_timestamp()
     OR NOT public.rag_v2_immutable_vertex_evidence_manifest_is_valid(p_reservation.evidence_manifest) THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex reservation is not current'
      USING ERRCODE = '55000';
  END IF;

  -- public/owner activation과 owner consent writer가 동일 lock을 쓰므로 claim commit이 새 outbound authorization point다.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-bundle-activation', 0)
  );
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-consent|' || p_reservation.owner_user_id, 0)
  );
  PERFORM set_config('app.actor_user_id', p_reservation.owner_user_id, true);
  PERFORM set_config('app.rag_v2_retrieval_scope', 'enabled', true);

  SELECT * INTO effective_consent
  FROM public.rag_v2_immutable_consent_events AS event
  WHERE event.owner_user_id = p_reservation.owner_user_id
    AND event.public_consent_event_id IS NOT NULL
    AND event.policy_digest IS NOT NULL
    AND event.processor_set_digest IS NOT NULL
  ORDER BY event.created_at DESC, event.consent_event_id DESC
  LIMIT 1;
  IF NOT FOUND
     OR effective_consent.action <> 'GRANT'
     OR effective_consent.public_consent_event_id IS DISTINCT FROM p_reservation.consent_event_id
     OR effective_consent.policy_digest IS DISTINCT FROM p_reservation.policy_sha256
     OR effective_consent.processor_set_digest IS DISTINCT FROM p_reservation.processor_set_sha256 THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex consent is not currently granted'
      USING ERRCODE = '55000';
  END IF;

  SELECT * INTO scope_row
  FROM public.rag_v2_retrieval_scope_claims AS scope
  WHERE scope.scope_claim_id = p_reservation.scope_claim_id
    AND scope.owner_user_id = p_reservation.owner_user_id
    AND scope.session_id = p_reservation.request_id
    AND scope.expires_at > statement_timestamp();
  IF NOT FOUND THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex retrieval scope is unavailable'
      USING ERRCODE = '55000';
  END IF;

  SELECT * INTO public_pointer
  FROM public.rag_v2_immutable_public_bundle_pointers AS pointer
  WHERE pointer.state_id = 'default'
    AND pointer.state = 'ACTIVE'
    AND pointer.pointer_version = scope_row.public_pointer_version
    AND pointer.exact30_generation_id = scope_row.exact30_generation_id
    AND pointer.oa112_generation_id = scope_row.oa112_generation_id
    AND pointer.embedding_profile_id = scope_row.embedding_profile_id;
  IF NOT FOUND
     OR NOT EXISTS (
       SELECT 1
       FROM public.rag_v2_immutable_component_generations AS exact_generation
       JOIN public.rag_v2_immutable_component_generations AS oa_generation
         ON oa_generation.component_generation_id = scope_row.oa112_generation_id
        AND oa_generation.component_scope = 'OA112'
        AND oa_generation.owner_user_id IS NULL
        AND oa_generation.embedding_profile_id = scope_row.embedding_profile_id
        AND oa_generation.state = 'ACTIVE'
        AND oa_generation.evaluation_status = 'PASSED'
       WHERE exact_generation.component_generation_id = scope_row.exact30_generation_id
         AND exact_generation.component_scope = 'EXACT30'
         AND exact_generation.owner_user_id IS NULL
         AND exact_generation.embedding_profile_id = scope_row.embedding_profile_id
         AND exact_generation.state = 'ACTIVE'
         AND exact_generation.evaluation_status = 'PASSED'
     ) THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex public retrieval scope changed'
      USING ERRCODE = '55000';
  END IF;

  SELECT * INTO owner_pointer
  FROM public.rag_v2_immutable_owner_bundle_pointers AS pointer
  WHERE pointer.owner_user_id = p_reservation.owner_user_id;
  IF scope_row.owner_private_generation_id IS NULL THEN
    IF (FOUND AND (owner_pointer.state <> 'ABSENT' OR owner_pointer.bundle_version <> scope_row.owner_pointer_version))
       OR (NOT FOUND AND scope_row.owner_pointer_version <> 0) THEN
      RAISE EXCEPTION 'immutable Pre-S5 Vertex owner retrieval scope changed'
        USING ERRCODE = '55000';
    END IF;
  ELSE
    IF NOT FOUND
       OR owner_pointer.state <> 'READY'
       OR owner_pointer.active_bundle_id IS DISTINCT FROM scope_row.owner_bundle_id
       OR owner_pointer.bundle_version <> scope_row.owner_pointer_version THEN
      RAISE EXCEPTION 'immutable Pre-S5 Vertex owner bundle scope changed'
        USING ERRCODE = '55000';
    END IF;
    SELECT * INTO owner_bundle
    FROM public.rag_v2_immutable_bundles AS bundle
    WHERE bundle.bundle_id = scope_row.owner_bundle_id
      AND bundle.owner_user_id = p_reservation.owner_user_id
      AND bundle.state = 'ACTIVE'
      AND bundle.evaluation_status = 'PASSED'
      AND bundle.owner_private_generation_id = scope_row.owner_private_generation_id
      AND bundle.exact30_generation_id = scope_row.exact30_generation_id
      AND bundle.oa112_generation_id = scope_row.oa112_generation_id
      AND bundle.embedding_profile_id = scope_row.embedding_profile_id;
    IF NOT FOUND
       OR NOT EXISTS (
         SELECT 1
         FROM public.rag_v2_immutable_component_generations AS generation
         WHERE generation.component_generation_id = scope_row.owner_private_generation_id
           AND generation.component_scope = 'OWNER_PRIVATE'
           AND generation.owner_user_id = p_reservation.owner_user_id
           AND generation.embedding_profile_id = scope_row.embedding_profile_id
           AND generation.state = 'ACTIVE'
           AND generation.evaluation_status = 'PASSED'
       ) THEN
      RAISE EXCEPTION 'immutable Pre-S5 Vertex owner component scope changed'
        USING ERRCODE = '55000';
    END IF;
  END IF;

  FOR evidence_item IN SELECT value FROM jsonb_array_elements(p_reservation.evidence_manifest)
  LOOP
    SELECT
      source.source_scope,
      source.owner_user_id,
      membership.component_generation_id,
      chunk.canonical_text_sha256
    INTO evidence_row
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
    WHERE membership.chunk_id = evidence_item ->> 'chunkRevisionId'
      AND chunk.canonical_text_sha256 = evidence_item ->> 'canonicalTextSha256'
      AND source.retrieval_topics && scope_row.allowed_topics
      AND source.external_processing_eligible
      AND source.external_embedding_allowed
      AND source.external_generation_allowed
      AND EXISTS (
        SELECT 1
        FROM public.rag_v2_immutable_generation_embeddings AS embedding
        WHERE embedding.component_generation_id = membership.component_generation_id
          AND embedding.chunk_id = membership.chunk_id
          AND embedding.component_scope = membership.component_scope
          AND embedding.owner_partition_key = membership.owner_partition_key
          AND embedding.embedding_profile_id = scope_row.embedding_profile_id
      )
      AND (
        (source.source_scope = 'EXACT30'
          AND source.owner_user_id IS NULL
          AND membership.component_generation_id = scope_row.exact30_generation_id)
        OR (source.source_scope = 'OA112'
          AND source.owner_user_id IS NULL
          AND membership.component_generation_id = scope_row.oa112_generation_id)
        OR (source.source_scope = 'OWNER_PRIVATE'
          AND source.owner_user_id = p_reservation.owner_user_id
          AND scope_row.owner_private_generation_id IS NOT NULL
          AND membership.component_generation_id = scope_row.owner_private_generation_id)
      );
    IF NOT FOUND THEN
      RAISE EXCEPTION 'immutable Pre-S5 Vertex evidence is no longer externally eligible'
        USING ERRCODE = '55000';
    END IF;
  END LOOP;
END
$assert_rag_v2_immutable_vertex_reservation_is_current$;
ALTER FUNCTION public.assert_rag_v2_immutable_vertex_reservation_is_current(
  public.rag_v2_immutable_vertex_usage_reservations
) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.assert_rag_v2_immutable_vertex_reservation_is_current(
  public.rag_v2_immutable_vertex_usage_reservations
) FROM PUBLIC;

REVOKE ALL PRIVILEGES ON FUNCTION public.reserve_rag_v2_immutable_vertex_usage(
  text, text, text, text, text, text, text, text, text, text, text, timestamptz,
  integer, integer, integer, bigint, bigint, bigint, integer, integer
) FROM decision_app;
DROP FUNCTION public.reserve_rag_v2_immutable_vertex_usage(
  text, text, text, text, text, text, text, text, text, text, text, timestamptz,
  integer, integer, integer, bigint, bigint, bigint, integer, integer
);

CREATE FUNCTION public.reserve_rag_v2_immutable_vertex_usage(
  p_usage_event_id text,
  p_owner_user_id text,
  p_request_id text,
  p_scope_claim_id text,
  p_question_fingerprint_hmac text,
  p_answer_mode text,
  p_consent_event_id text,
  p_packet_sha256 text,
  p_nonce_sha256 text,
  p_policy_sha256 text,
  p_processor_set_sha256 text,
  p_expires_at timestamptz,
  p_input_token_cap integer,
  p_output_token_cap integer,
  p_input_byte_cap integer,
  p_cost_cap_microusd bigint,
  p_input_microusd_per_token bigint,
  p_output_microusd_per_token bigint,
  p_token_physical_call_cap integer,
  p_generate_content_physical_call_cap integer,
  p_evidence_manifest jsonb
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
DECLARE
  reservation public.rag_v2_immutable_vertex_usage_reservations%ROWTYPE;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_usage_event_id !~ '^rgr_vgu_[0-9a-f]{32}$'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$'
     OR p_request_id !~ '^req_[A-Za-z0-9_-]{12,96}$'
     OR p_scope_claim_id !~ '^rvs_[0-9a-f]{32}$'
     OR p_question_fingerprint_hmac !~ '^[0-9a-f]{64}$'
     OR p_answer_mode NOT IN ('CONCISE', 'DETAILED')
     OR p_consent_event_id !~ '^rce_[A-Za-z0-9_-]{12,96}$'
     OR p_packet_sha256 !~ '^[0-9a-f]{64}$'
     OR p_nonce_sha256 !~ '^[0-9a-f]{64}$'
     OR p_policy_sha256 !~ '^[0-9a-f]{64}$'
     OR p_processor_set_sha256 !~ '^[0-9a-f]{64}$'
     OR p_expires_at <= statement_timestamp()
     OR p_expires_at > statement_timestamp() + interval '5 minutes'
     OR p_input_token_cap NOT BETWEEN 1 AND 120000
     OR p_output_token_cap NOT BETWEEN 1 AND 32768
     OR p_input_byte_cap NOT BETWEEN 1 AND 60000
     OR p_input_byte_cap + 512 > p_input_token_cap
     OR p_cost_cap_microusd NOT BETWEEN 1 AND 1000000000
     OR p_input_microusd_per_token NOT BETWEEN 1 AND 1000000
     OR p_output_microusd_per_token NOT BETWEEN 1 AND 1000000
     OR p_token_physical_call_cap <> 1
     OR p_generate_content_physical_call_cap <> 1
     OR NOT public.rag_v2_immutable_vertex_evidence_manifest_is_valid(p_evidence_manifest)
     OR p_input_token_cap::bigint * p_input_microusd_per_token
          + p_output_token_cap::bigint * p_output_microusd_per_token > p_cost_cap_microusd THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex usage reservation arguments are invalid'
      USING ERRCODE = '22023';
  END IF;

  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-vertex-usage-reservation|' || p_packet_sha256, 0)
  );
  INSERT INTO public.rag_v2_immutable_vertex_usage_reservations (
    usage_event_id, owner_user_id, request_id, scope_claim_id, question_fingerprint_hmac,
    answer_mode, consent_event_id, packet_sha256, nonce_sha256, policy_sha256, processor_set_sha256, expires_at,
    input_token_cap, output_token_cap, input_byte_cap, cost_cap_microusd,
    input_microusd_per_token, output_microusd_per_token,
    token_physical_call_cap, generate_content_physical_call_cap, evidence_manifest
  ) VALUES (
    p_usage_event_id, p_owner_user_id, p_request_id, p_scope_claim_id, p_question_fingerprint_hmac,
    p_answer_mode, p_consent_event_id, p_packet_sha256, p_nonce_sha256, p_policy_sha256, p_processor_set_sha256, p_expires_at,
    p_input_token_cap, p_output_token_cap, p_input_byte_cap, p_cost_cap_microusd,
    p_input_microusd_per_token, p_output_microusd_per_token,
    p_token_physical_call_cap, p_generate_content_physical_call_cap, p_evidence_manifest
  ) RETURNING * INTO reservation;
  PERFORM public.assert_rag_v2_immutable_vertex_reservation_is_current(reservation);
  RETURN QUERY SELECT p_usage_event_id, p_expires_at;
END
$reserve_rag_v2_immutable_vertex_usage$;
ALTER FUNCTION public.reserve_rag_v2_immutable_vertex_usage(
  text, text, text, text, text, text, text, text, text, text, text, timestamptz,
  integer, integer, integer, bigint, bigint, bigint, integer, integer, jsonb
) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.reserve_rag_v2_immutable_vertex_usage(
  text, text, text, text, text, text, text, text, text, text, text, timestamptz,
  integer, integer, integer, bigint, bigint, bigint, integer, integer, jsonb
) FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.claim_rag_v2_immutable_vertex_token_attempt(
  p_usage_event_id text,
  p_owner_user_id text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $claim_rag_v2_immutable_vertex_token_attempt$
DECLARE
  reservation public.rag_v2_immutable_vertex_usage_reservations%ROWTYPE;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_usage_event_id !~ '^rgr_vgu_[0-9a-f]{32}$'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$' THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex token claim arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  SELECT * INTO reservation
  FROM public.rag_v2_immutable_vertex_usage_reservations
  WHERE usage_event_id = p_usage_event_id
    AND owner_user_id = p_owner_user_id
  FOR UPDATE;
  IF NOT FOUND
     OR reservation.expires_at <= statement_timestamp()
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_token_attempts WHERE usage_event_id = p_usage_event_id)
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_generate_content_attempts WHERE usage_event_id = p_usage_event_id)
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_outcomes WHERE usage_event_id = p_usage_event_id) THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex token claim is unavailable'
      USING ERRCODE = '55000';
  END IF;
  PERFORM public.assert_rag_v2_immutable_vertex_reservation_is_current(reservation);
  INSERT INTO public.rag_v2_immutable_vertex_usage_token_attempts (
    usage_event_id, state, physical_token_call_count
  ) VALUES (p_usage_event_id, 'ATTEMPTED', 1);
END
$claim_rag_v2_immutable_vertex_token_attempt$;
ALTER FUNCTION public.claim_rag_v2_immutable_vertex_token_attempt(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.claim_rag_v2_immutable_vertex_token_attempt(text, text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION public.claim_rag_v2_immutable_vertex_generate_content_attempt(
  p_usage_event_id text,
  p_owner_user_id text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $claim_rag_v2_immutable_vertex_generate_content_attempt$
DECLARE
  reservation public.rag_v2_immutable_vertex_usage_reservations%ROWTYPE;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_app'
     OR nullif(current_setting('app.actor_user_id', true), '') IS DISTINCT FROM p_owner_user_id
     OR p_usage_event_id !~ '^rgr_vgu_[0-9a-f]{32}$'
     OR p_owner_user_id !~ '^usr_[a-z0-9][a-z0-9_-]{2,95}$' THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex generate claim arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  SELECT * INTO reservation
  FROM public.rag_v2_immutable_vertex_usage_reservations
  WHERE usage_event_id = p_usage_event_id
    AND owner_user_id = p_owner_user_id
  FOR UPDATE;
  IF NOT FOUND
     OR reservation.expires_at <= statement_timestamp()
     OR NOT EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_vertex_usage_token_attempts
       WHERE usage_event_id = p_usage_event_id AND state = 'ATTEMPTED' AND physical_token_call_count = 1
     )
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_generate_content_attempts WHERE usage_event_id = p_usage_event_id)
     OR EXISTS (SELECT 1 FROM public.rag_v2_immutable_vertex_usage_outcomes WHERE usage_event_id = p_usage_event_id) THEN
    RAISE EXCEPTION 'immutable Pre-S5 Vertex generate claim is unavailable'
      USING ERRCODE = '55000';
  END IF;
  PERFORM public.assert_rag_v2_immutable_vertex_reservation_is_current(reservation);
  INSERT INTO public.rag_v2_immutable_vertex_usage_generate_content_attempts (
    usage_event_id, state, physical_generate_content_call_count
  ) VALUES (p_usage_event_id, 'ATTEMPTED', 1);
END
$claim_rag_v2_immutable_vertex_generate_content_attempt$;
ALTER FUNCTION public.claim_rag_v2_immutable_vertex_generate_content_attempt(text, text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.claim_rag_v2_immutable_vertex_generate_content_attempt(text, text) FROM PUBLIC;

DO $rag_v2_vertex_claim_time_evidence_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_app') THEN
    GRANT EXECUTE ON FUNCTION public.reserve_rag_v2_immutable_vertex_usage(
      text, text, text, text, text, text, text, text, text, text, text, timestamptz,
      integer, integer, integer, bigint, bigint, bigint, integer, integer, jsonb
    ) TO decision_app;
    GRANT EXECUTE ON FUNCTION public.claim_rag_v2_immutable_vertex_token_attempt(text, text) TO decision_app;
    GRANT EXECUTE ON FUNCTION public.claim_rag_v2_immutable_vertex_generate_content_attempt(text, text) TO decision_app;
  END IF;
END
$rag_v2_vertex_claim_time_evidence_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION public.reserve_rag_v2_immutable_vertex_usage(
  text, text, text, text, text, text, text, text, text, text, text, timestamptz,
  integer, integer, integer, bigint, bigint, bigint, integer, integer, jsonb
) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION public.claim_rag_v2_immutable_vertex_token_attempt(text, text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION public.claim_rag_v2_immutable_vertex_generate_content_attempt(text, text) FROM PUBLIC;
