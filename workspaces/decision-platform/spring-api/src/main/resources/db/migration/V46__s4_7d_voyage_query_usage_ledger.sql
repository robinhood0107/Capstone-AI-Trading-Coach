-- V46 binds one Voyage contextual query embedding attempt to an exact local approval packet.
-- Question text, opaque scope claim text, provider response, and vectors are never persisted:
-- only their SHA-256 projections and sanitized numeric billing outcome are append-only ledger data.
-- V38 document full-bundle usage remains byte-stable and purpose-separated.

CREATE TABLE rag_v2_immutable_voyage_query_usage_reservations (
  usage_event_id text PRIMARY KEY,
  packet_sha256 text NOT NULL,
  nonce_sha256 text NOT NULL,
  query_sha256 text NOT NULL,
  scope_claim_sha256 text NOT NULL,
  rate_evidence_sha256 text NOT NULL,
  evaluation_component_scope text NOT NULL DEFAULT 'RUNTIME',
  provider text NOT NULL DEFAULT 'VOYAGE',
  operation text NOT NULL DEFAULT 'CONTEXTUALIZED_QUERY_EMBEDDING',
  expires_at timestamptz NOT NULL,
  token_cap integer NOT NULL,
  byte_cap integer NOT NULL,
  cost_cap_microusd bigint NOT NULL,
  input_microusd_per_token bigint NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_voyage_query_usage_reservation_id_check
    CHECK (usage_event_id ~ '^rgr_vqu_[0-9a-f]{32}$'),
  CONSTRAINT rag_v2_immutable_voyage_query_usage_reservation_hash_check
    CHECK (
      packet_sha256 ~ '^[0-9a-f]{64}$'
      AND nonce_sha256 ~ '^[0-9a-f]{64}$'
      AND query_sha256 ~ '^[0-9a-f]{64}$'
      AND scope_claim_sha256 ~ '^[0-9a-f]{64}$'
      AND rate_evidence_sha256 ~ '^[0-9a-f]{64}$'
    ),
  CONSTRAINT rag_v2_immutable_voyage_query_usage_reservation_provider_check
    CHECK (provider = 'VOYAGE' AND operation = 'CONTEXTUALIZED_QUERY_EMBEDDING'),
  CONSTRAINT rag_v2_immutable_voyage_query_usage_reservation_evaluation_scope_check
    CHECK (evaluation_component_scope IN ('RUNTIME', 'EXACT30', 'OA112')),
  CONSTRAINT rag_v2_immutable_voyage_query_usage_reservation_cap_check
    CHECK (
      token_cap BETWEEN 1 AND 8192
      AND byte_cap BETWEEN 1 AND 4194304
      AND cost_cap_microusd BETWEEN 1 AND 1000000000
      AND input_microusd_per_token BETWEEN 1 AND 1000000
      AND token_cap::bigint * input_microusd_per_token <= cost_cap_microusd
    ),
  CONSTRAINT rag_v2_immutable_voyage_query_usage_reservation_packet_unique UNIQUE (packet_sha256),
  CONSTRAINT rag_v2_immutable_voyage_query_usage_reservation_nonce_unique UNIQUE (nonce_sha256)
);
CREATE INDEX rag_v2_immutable_voyage_query_usage_reservation_evaluation_lookup
  ON rag_v2_immutable_voyage_query_usage_reservations (
    scope_claim_sha256,
    evaluation_component_scope
  );
ALTER TABLE rag_v2_immutable_voyage_query_usage_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_voyage_query_usage_reservations FORCE ROW LEVEL SECURITY;
CREATE POLICY rag_v2_immutable_voyage_query_usage_reservation_flyway_write
  ON rag_v2_immutable_voyage_query_usage_reservations
  FOR ALL TO flyway USING (true) WITH CHECK (true);

CREATE TABLE rag_v2_immutable_voyage_query_usage_attempts (
  usage_event_id text PRIMARY KEY
    REFERENCES rag_v2_immutable_voyage_query_usage_reservations (usage_event_id) ON DELETE RESTRICT,
  state text NOT NULL,
  physical_call_count integer NOT NULL,
  claimed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_voyage_query_usage_attempt_state_check
    CHECK (state = 'ATTEMPTED'),
  CONSTRAINT rag_v2_immutable_voyage_query_usage_attempt_physical_count_check
    CHECK (physical_call_count = 1)
);
ALTER TABLE rag_v2_immutable_voyage_query_usage_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_voyage_query_usage_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY rag_v2_immutable_voyage_query_usage_attempt_flyway_write
  ON rag_v2_immutable_voyage_query_usage_attempts
  FOR ALL TO flyway USING (true) WITH CHECK (true);

CREATE TABLE rag_v2_immutable_voyage_query_usage_outcomes (
  usage_event_id text PRIMARY KEY
    REFERENCES rag_v2_immutable_voyage_query_usage_reservations (usage_event_id) ON DELETE RESTRICT,
  packet_sha256 text NOT NULL,
  state text NOT NULL,
  provider_total_tokens integer,
  actual_cost_microusd bigint,
  recorded_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT rag_v2_immutable_voyage_query_usage_outcome_packet_check
    CHECK (packet_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT rag_v2_immutable_voyage_query_usage_outcome_state_check
    CHECK (state IN ('COMMITTED', 'UNKNOWN_BILLING')),
  CONSTRAINT rag_v2_immutable_voyage_query_usage_outcome_shape_check
    CHECK (
      (state = 'COMMITTED'
        AND provider_total_tokens BETWEEN 0 AND 8192
        AND actual_cost_microusd BETWEEN 0 AND 1000000000)
      OR (state = 'UNKNOWN_BILLING'
        AND provider_total_tokens IS NULL
        AND actual_cost_microusd IS NULL)
    )
);
ALTER TABLE rag_v2_immutable_voyage_query_usage_outcomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE rag_v2_immutable_voyage_query_usage_outcomes FORCE ROW LEVEL SECURITY;
CREATE POLICY rag_v2_immutable_voyage_query_usage_outcome_flyway_write
  ON rag_v2_immutable_voyage_query_usage_outcomes
  FOR ALL TO flyway USING (true) WITH CHECK (true);

CREATE FUNCTION reject_rag_v2_immutable_voyage_query_usage_mutation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $reject_rag_v2_immutable_voyage_query_usage_mutation$
BEGIN
  RAISE EXCEPTION 'immutable Pre-S5 Voyage query usage ledger mutation is forbidden'
    USING ERRCODE = '55000';
END
$reject_rag_v2_immutable_voyage_query_usage_mutation$;
ALTER FUNCTION reject_rag_v2_immutable_voyage_query_usage_mutation() OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION reject_rag_v2_immutable_voyage_query_usage_mutation() FROM PUBLIC;

CREATE TRIGGER rag_v2_immutable_voyage_query_usage_reservations_append_only
BEFORE UPDATE OR DELETE ON rag_v2_immutable_voyage_query_usage_reservations
FOR EACH ROW EXECUTE FUNCTION reject_rag_v2_immutable_voyage_query_usage_mutation();
CREATE TRIGGER rag_v2_immutable_voyage_query_usage_attempts_append_only
BEFORE UPDATE OR DELETE ON rag_v2_immutable_voyage_query_usage_attempts
FOR EACH ROW EXECUTE FUNCTION reject_rag_v2_immutable_voyage_query_usage_mutation();
CREATE TRIGGER rag_v2_immutable_voyage_query_usage_outcomes_append_only
BEFORE UPDATE OR DELETE ON rag_v2_immutable_voyage_query_usage_outcomes
FOR EACH ROW EXECUTE FUNCTION reject_rag_v2_immutable_voyage_query_usage_mutation();

CREATE FUNCTION reserve_rag_v2_immutable_voyage_query_usage(
  p_usage_event_id text,
  p_packet_sha256 text,
  p_nonce_sha256 text,
  p_query_sha256 text,
  p_scope_claim_sha256 text,
  p_rate_evidence_sha256 text,
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
AS $reserve_rag_v2_immutable_voyage_query_usage$
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_usage_event_id !~ '^rgr_vqu_[0-9a-f]{32}$'
     OR p_packet_sha256 !~ '^[0-9a-f]{64}$'
     OR p_nonce_sha256 !~ '^[0-9a-f]{64}$'
     OR p_query_sha256 !~ '^[0-9a-f]{64}$'
     OR p_scope_claim_sha256 !~ '^[0-9a-f]{64}$'
     OR p_rate_evidence_sha256 !~ '^[0-9a-f]{64}$'
     OR p_evaluation_component_scope NOT IN ('RUNTIME', 'EXACT30', 'OA112')
     OR p_expires_at <= statement_timestamp()
     OR p_expires_at > statement_timestamp() + interval '5 minutes'
     OR p_token_cap NOT BETWEEN 1 AND 8192
     OR p_byte_cap NOT BETWEEN 1 AND 4194304
     OR p_cost_cap_microusd NOT BETWEEN 1 AND 1000000000
     OR p_input_microusd_per_token NOT BETWEEN 1 AND 1000000
     OR p_token_cap::bigint * p_input_microusd_per_token > p_cost_cap_microusd THEN
    RAISE EXCEPTION 'immutable Pre-S5 Voyage query usage reservation arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('rag-v2-immutable-voyage-query-usage-reservation|' || p_packet_sha256, 0)
  );
  INSERT INTO public.rag_v2_immutable_voyage_query_usage_reservations (
    usage_event_id, packet_sha256, nonce_sha256, query_sha256, scope_claim_sha256,
    rate_evidence_sha256, evaluation_component_scope, expires_at, token_cap, byte_cap, cost_cap_microusd,
    input_microusd_per_token
  ) VALUES (
    p_usage_event_id, p_packet_sha256, p_nonce_sha256, p_query_sha256, p_scope_claim_sha256,
    p_rate_evidence_sha256, p_evaluation_component_scope, p_expires_at, p_token_cap, p_byte_cap, p_cost_cap_microusd,
    p_input_microusd_per_token
  );
  RETURN QUERY SELECT p_usage_event_id, p_expires_at;
END
$reserve_rag_v2_immutable_voyage_query_usage$;
ALTER FUNCTION reserve_rag_v2_immutable_voyage_query_usage(
  text, text, text, text, text, text, text, timestamptz, integer, integer, bigint, bigint
) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION reserve_rag_v2_immutable_voyage_query_usage(
  text, text, text, text, text, text, text, timestamptz, integer, integer, bigint, bigint
) FROM PUBLIC;

CREATE FUNCTION claim_rag_v2_immutable_voyage_query_usage_attempt(
  p_usage_event_id text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $claim_rag_v2_immutable_voyage_query_usage_attempt$
DECLARE
  reservation rag_v2_immutable_voyage_query_usage_reservations%ROWTYPE;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_usage_event_id !~ '^rgr_vqu_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'immutable Pre-S5 Voyage query usage claim arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  SELECT * INTO reservation
  FROM public.rag_v2_immutable_voyage_query_usage_reservations
  WHERE usage_event_id = p_usage_event_id
  FOR UPDATE;
  IF NOT FOUND OR reservation.expires_at <= statement_timestamp() THEN
    RAISE EXCEPTION 'immutable Pre-S5 Voyage query usage claim is unavailable'
      USING ERRCODE = '55000';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_voyage_query_usage_attempts
    WHERE usage_event_id = p_usage_event_id
  ) OR EXISTS (
    SELECT 1
    FROM public.rag_v2_immutable_voyage_query_usage_outcomes
    WHERE usage_event_id = p_usage_event_id
  ) THEN
    RAISE EXCEPTION 'immutable Pre-S5 Voyage query usage has already been claimed'
      USING ERRCODE = '55000';
  END IF;
  INSERT INTO public.rag_v2_immutable_voyage_query_usage_attempts (
    usage_event_id, state, physical_call_count
  ) VALUES (
    p_usage_event_id, 'ATTEMPTED', 1
  );
END
$claim_rag_v2_immutable_voyage_query_usage_attempt$;
ALTER FUNCTION claim_rag_v2_immutable_voyage_query_usage_attempt(text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION claim_rag_v2_immutable_voyage_query_usage_attempt(text) FROM PUBLIC;

CREATE FUNCTION commit_rag_v2_immutable_voyage_query_usage(
  p_usage_event_id text,
  p_provider_total_tokens integer,
  p_actual_cost_microusd bigint
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $commit_rag_v2_immutable_voyage_query_usage$
DECLARE
  reservation rag_v2_immutable_voyage_query_usage_reservations%ROWTYPE;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_usage_event_id !~ '^rgr_vqu_[0-9a-f]{32}$'
     OR p_provider_total_tokens NOT BETWEEN 0 AND 8192
     OR p_actual_cost_microusd NOT BETWEEN 0 AND 1000000000 THEN
    RAISE EXCEPTION 'immutable Pre-S5 Voyage query usage commit arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  SELECT * INTO reservation
  FROM public.rag_v2_immutable_voyage_query_usage_reservations
  WHERE usage_event_id = p_usage_event_id
  FOR UPDATE;
  IF NOT FOUND
     OR NOT EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_voyage_query_usage_attempts
       WHERE usage_event_id = p_usage_event_id AND state = 'ATTEMPTED' AND physical_call_count = 1
     )
     OR EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_voyage_query_usage_outcomes
       WHERE usage_event_id = p_usage_event_id
     )
     OR p_provider_total_tokens > reservation.token_cap
     OR p_actual_cost_microusd <> p_provider_total_tokens::bigint * reservation.input_microusd_per_token
     OR p_actual_cost_microusd > reservation.cost_cap_microusd THEN
    RAISE EXCEPTION 'immutable Pre-S5 Voyage query usage commit is unavailable'
      USING ERRCODE = '55000';
  END IF;
  INSERT INTO public.rag_v2_immutable_voyage_query_usage_outcomes (
    usage_event_id, packet_sha256, state, provider_total_tokens, actual_cost_microusd
  ) VALUES (
    p_usage_event_id, reservation.packet_sha256, 'COMMITTED',
    p_provider_total_tokens, p_actual_cost_microusd
  );
END
$commit_rag_v2_immutable_voyage_query_usage$;
ALTER FUNCTION commit_rag_v2_immutable_voyage_query_usage(text, integer, bigint) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION commit_rag_v2_immutable_voyage_query_usage(text, integer, bigint) FROM PUBLIC;

CREATE FUNCTION mark_rag_v2_immutable_voyage_query_usage_unknown_billing(
  p_usage_event_id text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $mark_rag_v2_immutable_voyage_query_usage_unknown_billing$
DECLARE
  reservation rag_v2_immutable_voyage_query_usage_reservations%ROWTYPE;
BEGIN
  IF current_user <> 'flyway'
     OR session_user <> 'decision_rag_writer'
     OR p_usage_event_id !~ '^rgr_vqu_[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'immutable Pre-S5 Voyage query unknown billing arguments are invalid'
      USING ERRCODE = '22023';
  END IF;
  SELECT * INTO reservation
  FROM public.rag_v2_immutable_voyage_query_usage_reservations
  WHERE usage_event_id = p_usage_event_id
  FOR UPDATE;
  IF NOT FOUND
     OR NOT EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_voyage_query_usage_attempts
       WHERE usage_event_id = p_usage_event_id AND state = 'ATTEMPTED' AND physical_call_count = 1
     )
     OR EXISTS (
       SELECT 1 FROM public.rag_v2_immutable_voyage_query_usage_outcomes
       WHERE usage_event_id = p_usage_event_id
     ) THEN
    RAISE EXCEPTION 'immutable Pre-S5 Voyage query unknown billing is unavailable'
      USING ERRCODE = '55000';
  END IF;
  INSERT INTO public.rag_v2_immutable_voyage_query_usage_outcomes (
    usage_event_id, packet_sha256, state, provider_total_tokens, actual_cost_microusd
  ) VALUES (
    p_usage_event_id, reservation.packet_sha256, 'UNKNOWN_BILLING', NULL, NULL
  );
END
$mark_rag_v2_immutable_voyage_query_usage_unknown_billing$;
ALTER FUNCTION mark_rag_v2_immutable_voyage_query_usage_unknown_billing(text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION mark_rag_v2_immutable_voyage_query_usage_unknown_billing(text) FROM PUBLIC;

DO $rag_v2_immutable_voyage_query_usage_ledger_acl$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_rag_writer') THEN
    REVOKE ALL PRIVILEGES ON TABLE
      rag_v2_immutable_voyage_query_usage_reservations,
      rag_v2_immutable_voyage_query_usage_attempts,
      rag_v2_immutable_voyage_query_usage_outcomes
    FROM decision_rag_writer;
    GRANT EXECUTE ON FUNCTION reserve_rag_v2_immutable_voyage_query_usage(
      text, text, text, text, text, text, text, timestamptz, integer, integer, bigint, bigint
    ) TO decision_rag_writer;
    GRANT EXECUTE ON FUNCTION claim_rag_v2_immutable_voyage_query_usage_attempt(text)
      TO decision_rag_writer;
    GRANT EXECUTE ON FUNCTION commit_rag_v2_immutable_voyage_query_usage(text, integer, bigint)
      TO decision_rag_writer;
    GRANT EXECUTE ON FUNCTION mark_rag_v2_immutable_voyage_query_usage_unknown_billing(text)
      TO decision_rag_writer;
  END IF;
END
$rag_v2_immutable_voyage_query_usage_ledger_acl$;

REVOKE ALL PRIVILEGES ON FUNCTION reserve_rag_v2_immutable_voyage_query_usage(
  text, text, text, text, text, text, text, timestamptz, integer, integer, bigint, bigint
) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION claim_rag_v2_immutable_voyage_query_usage_attempt(text) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION commit_rag_v2_immutable_voyage_query_usage(text, integer, bigint) FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION mark_rag_v2_immutable_voyage_query_usage_unknown_billing(text) FROM PUBLIC;
