-- Voyage document 본문 batch는 110K 이하로 계획하지만 activation의 token cap은
-- provider-side input_type=document accounting을 포함하는 공식 요청 상한 120K다.
-- V55 bytes를 수정하지 않고 reservation capability만 forward repair한다.
CREATE OR REPLACE FUNCTION reserve_rag_v2_immutable_voyage_document_batch_usage(
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
     OR p_token_cap IS NULL OR p_token_cap NOT BETWEEN 1 AND 120000
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
GRANT EXECUTE ON FUNCTION reserve_rag_v2_immutable_voyage_document_batch_usage(
  text, text, text, text, text, text, timestamptz, integer, integer, bigint, bigint
) TO decision_rag_writer;
