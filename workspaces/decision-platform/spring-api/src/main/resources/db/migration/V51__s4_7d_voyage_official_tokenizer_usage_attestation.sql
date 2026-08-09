-- V51은 V38/V46 historical packet/usage function signatures를 보존한다. 새 call만 local official
-- tokenizer artifact hash와 pre-call expected input token count를 append-only receipt에 결속한다.
-- canonical text, question, vectors, tokenizer bytes, provider response는 어느 table에도 저장하지 않는다.

ALTER TABLE rag_v2_immutable_voyage_usage_reservations
  ADD COLUMN official_tokenizer_sha256 text;
ALTER TABLE rag_v2_immutable_voyage_usage_outcomes
  ADD COLUMN expected_input_tokens integer;
ALTER TABLE rag_v2_immutable_voyage_query_usage_reservations
  ADD COLUMN official_tokenizer_sha256 text;
ALTER TABLE rag_v2_immutable_voyage_query_usage_outcomes
  ADD COLUMN expected_input_tokens integer;

ALTER TABLE rag_v2_immutable_voyage_usage_reservations
  ADD CONSTRAINT rag_v2_immutable_voyage_usage_reservation_tokenizer_hash_check
  CHECK (official_tokenizer_sha256 IS NULL OR official_tokenizer_sha256 ~ '^[0-9a-f]{64}$');
ALTER TABLE rag_v2_immutable_voyage_usage_outcomes
  ADD CONSTRAINT rag_v2_immutable_voyage_usage_outcome_expected_tokens_check
  CHECK (
    expected_input_tokens IS NULL
    OR (state = 'COMMITTED' AND expected_input_tokens BETWEEN 1 AND 120000)
  );
ALTER TABLE rag_v2_immutable_voyage_query_usage_reservations
  ADD CONSTRAINT rag_v2_immutable_voyage_query_usage_reservation_tokenizer_hash_check
  CHECK (official_tokenizer_sha256 IS NULL OR official_tokenizer_sha256 ~ '^[0-9a-f]{64}$');
ALTER TABLE rag_v2_immutable_voyage_query_usage_outcomes
  ADD CONSTRAINT rag_v2_immutable_voyage_query_usage_outcome_expected_tokens_check
  CHECK (
    expected_input_tokens IS NULL
    OR (state = 'COMMITTED' AND expected_input_tokens BETWEEN 1 AND 8192)
  );

CREATE FUNCTION reserve_rag_v2_immutable_voyage_usage_with_tokenizer(
  p_usage_event_id text,
  p_packet_sha256 text,
  p_nonce_sha256 text,
  p_bundle_manifest_sha256 text,
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
AS $reserve_rag_v2_immutable_voyage_usage_with_tokenizer$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_usage_event_id IS NULL OR p_usage_event_id !~ '^rgr_vou_[0-9a-f]{32}$'
     OR p_packet_sha256 IS NULL OR p_packet_sha256 !~ '^[0-9a-f]{64}$'
     OR p_nonce_sha256 IS NULL OR p_nonce_sha256 !~ '^[0-9a-f]{64}$'
     OR p_bundle_manifest_sha256 IS NULL OR p_bundle_manifest_sha256 !~ '^[0-9a-f]{64}$'
     OR p_rate_evidence_sha256 IS NULL OR p_rate_evidence_sha256 !~ '^[0-9a-f]{64}$'
     OR p_official_tokenizer_sha256 IS NULL OR p_official_tokenizer_sha256 !~ '^[0-9a-f]{64}$'
     OR p_expires_at IS NULL OR p_expires_at <= statement_timestamp()
     OR p_expires_at > statement_timestamp() + interval '5 minutes'
     OR p_token_cap IS NULL OR p_token_cap NOT BETWEEN 1 AND 120000
     OR p_byte_cap IS NULL OR p_byte_cap NOT BETWEEN 1 AND 4194304
     OR p_cost_cap_microusd IS NULL OR p_cost_cap_microusd NOT BETWEEN 1 AND 1000000000
     OR p_input_microusd_per_token IS NULL OR p_input_microusd_per_token NOT BETWEEN 1 AND 1000000
     OR p_token_cap::bigint * p_input_microusd_per_token > p_cost_cap_microusd THEN
    RAISE EXCEPTION 'immutable Pre-S5 Voyage tokenizer usage reservation arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-voyage-usage-reservation|' || p_packet_sha256, 0)
  );
  INSERT INTO public.rag_v2_immutable_voyage_usage_reservations (
    usage_event_id, packet_sha256, nonce_sha256, bundle_manifest_sha256, rate_evidence_sha256,
    official_tokenizer_sha256, expires_at, token_cap, byte_cap, cost_cap_microusd,
    input_microusd_per_token
  ) VALUES (
    p_usage_event_id, p_packet_sha256, p_nonce_sha256, p_bundle_manifest_sha256, p_rate_evidence_sha256,
    p_official_tokenizer_sha256, p_expires_at, p_token_cap, p_byte_cap, p_cost_cap_microusd,
    p_input_microusd_per_token
  );
  RETURN QUERY SELECT p_usage_event_id, p_expires_at;
END
$reserve_rag_v2_immutable_voyage_usage_with_tokenizer$;
ALTER FUNCTION reserve_rag_v2_immutable_voyage_usage_with_tokenizer(
  text, text, text, text, text, text, timestamptz, integer, integer, bigint, bigint
) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION reserve_rag_v2_immutable_voyage_usage_with_tokenizer(
  text, text, text, text, text, text, timestamptz, integer, integer, bigint, bigint
) FROM PUBLIC;

CREATE FUNCTION commit_rag_v2_immutable_voyage_usage_with_tokenizer(
  p_usage_event_id text,
  p_expected_input_tokens integer,
  p_provider_total_tokens integer,
  p_actual_cost_microusd bigint
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $commit_rag_v2_immutable_voyage_usage_with_tokenizer$
DECLARE
  reservation rag_v2_immutable_voyage_usage_reservations%ROWTYPE;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_usage_event_id IS NULL OR p_usage_event_id !~ '^rgr_vou_[0-9a-f]{32}$'
     OR p_expected_input_tokens IS NULL OR p_expected_input_tokens NOT BETWEEN 1 AND 120000
     OR p_provider_total_tokens IS NULL OR p_provider_total_tokens NOT BETWEEN 0 AND 120000
     OR p_actual_cost_microusd IS NULL OR p_actual_cost_microusd NOT BETWEEN 0 AND 1000000000 THEN
    RAISE EXCEPTION 'immutable Pre-S5 Voyage tokenizer usage commit arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  SELECT * INTO reservation
  FROM public.rag_v2_immutable_voyage_usage_reservations
  WHERE usage_event_id = p_usage_event_id
  FOR UPDATE;
  IF NOT FOUND
     OR reservation.official_tokenizer_sha256 IS NULL
     OR NOT EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_voyage_usage_attempts
       WHERE usage_event_id = p_usage_event_id AND state = 'ATTEMPTED' AND physical_call_count = 1
     )
     OR EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_voyage_usage_outcomes
       WHERE usage_event_id = p_usage_event_id
     )
     OR p_expected_input_tokens > reservation.token_cap
     OR p_provider_total_tokens > reservation.token_cap
     OR p_actual_cost_microusd <> p_provider_total_tokens::bigint * reservation.input_microusd_per_token
     OR p_actual_cost_microusd > reservation.cost_cap_microusd THEN
    RAISE EXCEPTION 'immutable Pre-S5 Voyage tokenizer usage commit is unavailable'
      USING ERRCODE = '55000';
  END IF;
  INSERT INTO public.rag_v2_immutable_voyage_usage_outcomes (
    usage_event_id, packet_sha256, state, expected_input_tokens, provider_total_tokens, actual_cost_microusd
  ) VALUES (
    p_usage_event_id, reservation.packet_sha256, 'COMMITTED',
    p_expected_input_tokens, p_provider_total_tokens, p_actual_cost_microusd
  );
END
$commit_rag_v2_immutable_voyage_usage_with_tokenizer$;
ALTER FUNCTION commit_rag_v2_immutable_voyage_usage_with_tokenizer(text, integer, integer, bigint)
  OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION commit_rag_v2_immutable_voyage_usage_with_tokenizer(
  text, integer, integer, bigint
) FROM PUBLIC;

CREATE FUNCTION reserve_rag_v2_immutable_voyage_query_usage_with_tokenizer(
  p_usage_event_id text,
  p_packet_sha256 text,
  p_nonce_sha256 text,
  p_query_sha256 text,
  p_scope_claim_sha256 text,
  p_rate_evidence_sha256 text,
  p_official_tokenizer_sha256 text,
  p_evaluation_component_scope text,
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
AS $reserve_rag_v2_immutable_voyage_query_usage_with_tokenizer$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_usage_event_id IS NULL OR p_usage_event_id !~ '^rgr_vqu_[0-9a-f]{32}$'
     OR p_packet_sha256 IS NULL OR p_packet_sha256 !~ '^[0-9a-f]{64}$'
     OR p_nonce_sha256 IS NULL OR p_nonce_sha256 !~ '^[0-9a-f]{64}$'
     OR p_query_sha256 IS NULL OR p_query_sha256 !~ '^[0-9a-f]{64}$'
     OR p_scope_claim_sha256 IS NULL OR p_scope_claim_sha256 !~ '^[0-9a-f]{64}$'
     OR p_rate_evidence_sha256 IS NULL OR p_rate_evidence_sha256 !~ '^[0-9a-f]{64}$'
     OR p_official_tokenizer_sha256 IS NULL OR p_official_tokenizer_sha256 !~ '^[0-9a-f]{64}$'
     OR p_evaluation_component_scope IS NULL OR p_evaluation_component_scope NOT IN ('RUNTIME', 'EXACT30', 'OA112')
     OR p_expires_at IS NULL OR p_expires_at <= statement_timestamp()
     OR p_expires_at > statement_timestamp() + interval '5 minutes'
     OR p_token_cap IS NULL OR p_token_cap NOT BETWEEN 1 AND 8192
     OR p_byte_cap IS NULL OR p_byte_cap NOT BETWEEN 1 AND 4194304
     OR p_cost_cap_microusd IS NULL OR p_cost_cap_microusd NOT BETWEEN 1 AND 1000000000
     OR p_input_microusd_per_token IS NULL OR p_input_microusd_per_token NOT BETWEEN 1 AND 1000000
     OR p_token_cap::bigint * p_input_microusd_per_token > p_cost_cap_microusd THEN
    RAISE EXCEPTION 'immutable Pre-S5 Voyage query tokenizer usage reservation arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-voyage-query-usage-reservation|' || p_packet_sha256, 0)
  );
  INSERT INTO public.rag_v2_immutable_voyage_query_usage_reservations (
    usage_event_id, packet_sha256, nonce_sha256, query_sha256, scope_claim_sha256,
    rate_evidence_sha256, official_tokenizer_sha256, evaluation_component_scope, expires_at, token_cap,
    byte_cap, cost_cap_microusd, input_microusd_per_token
  ) VALUES (
    p_usage_event_id, p_packet_sha256, p_nonce_sha256, p_query_sha256, p_scope_claim_sha256,
    p_rate_evidence_sha256, p_official_tokenizer_sha256, p_evaluation_component_scope, p_expires_at,
    p_token_cap, p_byte_cap, p_cost_cap_microusd, p_input_microusd_per_token
  );
  RETURN QUERY SELECT p_usage_event_id, p_expires_at;
END
$reserve_rag_v2_immutable_voyage_query_usage_with_tokenizer$;
ALTER FUNCTION reserve_rag_v2_immutable_voyage_query_usage_with_tokenizer(
  text, text, text, text, text, text, text, text, timestamptz, integer, integer, bigint, bigint
) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION reserve_rag_v2_immutable_voyage_query_usage_with_tokenizer(
  text, text, text, text, text, text, text, text, timestamptz, integer, integer, bigint, bigint
) FROM PUBLIC;

CREATE FUNCTION commit_rag_v2_immutable_voyage_query_usage_with_tokenizer(
  p_usage_event_id text,
  p_expected_input_tokens integer,
  p_provider_total_tokens integer,
  p_actual_cost_microusd bigint
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $commit_rag_v2_immutable_voyage_query_usage_with_tokenizer$
DECLARE
  reservation rag_v2_immutable_voyage_query_usage_reservations%ROWTYPE;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_usage_event_id IS NULL OR p_usage_event_id !~ '^rgr_vqu_[0-9a-f]{32}$'
     OR p_expected_input_tokens IS NULL OR p_expected_input_tokens NOT BETWEEN 1 AND 8192
     OR p_provider_total_tokens IS NULL OR p_provider_total_tokens NOT BETWEEN 0 AND 8192
     OR p_actual_cost_microusd IS NULL OR p_actual_cost_microusd NOT BETWEEN 0 AND 1000000000 THEN
    RAISE EXCEPTION 'immutable Pre-S5 Voyage query tokenizer usage commit arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  SELECT * INTO reservation
  FROM public.rag_v2_immutable_voyage_query_usage_reservations
  WHERE usage_event_id = p_usage_event_id
  FOR UPDATE;
  IF NOT FOUND
     OR reservation.official_tokenizer_sha256 IS NULL
     OR NOT EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_voyage_query_usage_attempts
       WHERE usage_event_id = p_usage_event_id AND state = 'ATTEMPTED' AND physical_call_count = 1
     )
     OR EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_voyage_query_usage_outcomes
       WHERE usage_event_id = p_usage_event_id
     )
     OR p_expected_input_tokens > reservation.token_cap
     OR p_provider_total_tokens > reservation.token_cap
     OR p_actual_cost_microusd <> p_provider_total_tokens::bigint * reservation.input_microusd_per_token
     OR p_actual_cost_microusd > reservation.cost_cap_microusd THEN
    RAISE EXCEPTION 'immutable Pre-S5 Voyage query tokenizer usage commit is unavailable'
      USING ERRCODE = '55000';
  END IF;
  INSERT INTO public.rag_v2_immutable_voyage_query_usage_outcomes (
    usage_event_id, packet_sha256, state, expected_input_tokens, provider_total_tokens, actual_cost_microusd
  ) VALUES (
    p_usage_event_id, reservation.packet_sha256, 'COMMITTED',
    p_expected_input_tokens, p_provider_total_tokens, p_actual_cost_microusd
  );
END
$commit_rag_v2_immutable_voyage_query_usage_with_tokenizer$;
ALTER FUNCTION commit_rag_v2_immutable_voyage_query_usage_with_tokenizer(text, integer, integer, bigint)
  OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION commit_rag_v2_immutable_voyage_query_usage_with_tokenizer(
  text, integer, integer, bigint
) FROM PUBLIC;

DO $rag_v2_immutable_voyage_tokenizer_usage_ledger_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    GRANT EXECUTE ON FUNCTION reserve_rag_v2_immutable_voyage_usage_with_tokenizer(
      text, text, text, text, text, text, timestamptz, integer, integer, bigint, bigint
    ) TO decision_rag_writer;
    GRANT EXECUTE ON FUNCTION commit_rag_v2_immutable_voyage_usage_with_tokenizer(
      text, integer, integer, bigint
    ) TO decision_rag_writer;
    GRANT EXECUTE ON FUNCTION reserve_rag_v2_immutable_voyage_query_usage_with_tokenizer(
      text, text, text, text, text, text, text, text, timestamptz, integer, integer, bigint, bigint
    ) TO decision_rag_writer;
    GRANT EXECUTE ON FUNCTION commit_rag_v2_immutable_voyage_query_usage_with_tokenizer(
      text, integer, integer, bigint
    ) TO decision_rag_writer;
  END IF;
END
$rag_v2_immutable_voyage_tokenizer_usage_ledger_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION reserve_rag_v2_immutable_voyage_usage_with_tokenizer(
  text, text, text, text, text, text, timestamptz, integer, integer, bigint, bigint
) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION commit_rag_v2_immutable_voyage_usage_with_tokenizer(
  text, integer, integer, bigint
) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION reserve_rag_v2_immutable_voyage_query_usage_with_tokenizer(
  text, text, text, text, text, text, text, text, timestamptz, integer, integer, bigint, bigint
) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION commit_rag_v2_immutable_voyage_query_usage_with_tokenizer(
  text, integer, integer, bigint
) FROM PUBLIC;
