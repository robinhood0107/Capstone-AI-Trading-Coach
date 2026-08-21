-- S6.7 P1 cross-market snapshots: append-only stored projection and WARN_ONLY reader authority.

CREATE TABLE public.cross_market_risk_snapshots_v2 (
  snapshot_id uuid PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  owner_scope_hash text NOT NULL CHECK (owner_scope_hash ~ '^[0-9a-f]{64}$'),
  symbol text NOT NULL CHECK (symbol ~ '^[0-9A-Z./-]{1,32}$'),
  available_at timestamptz NOT NULL,
  stale_at timestamptz NOT NULL CHECK (stale_at >= available_at),
  evidence_mode text NOT NULL CHECK (evidence_mode IN (
    'SYNTHETIC_FIXTURE','HISTORICAL_REPLAY','PROSPECTIVE_SHADOW','MANUAL_EOD'
  )),
  storage_mode text NOT NULL CHECK (storage_mode IN ('ARTIFACT_ONLY','STORED_SNAPSHOT')),
  runtime_mode text NOT NULL CHECK (runtime_mode IN ('OFF','SHADOW','WARN_ONLY','ENFORCED')),
  availability text NOT NULL CHECK (availability IN ('AVAILABLE','UNAVAILABLE','STALE')),
  quality text NOT NULL CHECK (quality IN ('PASS','WARN','EVIDENCE_GAP')),
  score numeric,
  threshold_percentile numeric,
  threshold_artifact_hash text,
  config_hash text NOT NULL CHECK (config_hash ~ '^[0-9a-f]{64}$'),
  exposure_classification text NOT NULL CHECK (exposure_classification IN (
    'NEW_BUY','INCREASE_BUY','SELL','REDUCE','LIQUIDATION','EXISTING_POSITION','UNCLASSIFIED'
  )),
  exposure_available_at timestamptz NOT NULL,
  exposure_catalog_hash text NOT NULL CHECK (exposure_catalog_hash ~ '^[0-9a-f]{64}$'),
  semantic_input_hash text NOT NULL CHECK (semantic_input_hash ~ '^[0-9a-f]{64}$'),
  artifact_hash text NOT NULL CHECK (artifact_hash ~ '^[0-9a-f]{64}$'),
  payload_json jsonb NOT NULL CHECK (
    jsonb_typeof(payload_json) = 'object' AND octet_length(payload_json::text) BETWEEN 2 AND 262144
  ),
  explanation_json jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (
    jsonb_typeof(explanation_json) = 'object' AND octet_length(explanation_json::text) <= 65536
  ),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT cross_market_v2_score_check CHECK (score IS NULL OR score BETWEEN 0 AND 100),
  CONSTRAINT cross_market_v2_threshold_check CHECK (
    threshold_percentile IS NULL OR threshold_percentile IN (95, 97.5, 99)
  ),
  CONSTRAINT cross_market_v2_exposure_time_check CHECK (exposure_available_at = available_at),
  CONSTRAINT cross_market_v2_available_check CHECK (
    (availability = 'AVAILABLE' AND score IS NOT NULL AND threshold_percentile IS NOT NULL
      AND threshold_artifact_hash ~ '^[0-9a-f]{64}$' AND config_hash <> repeat('0', 64))
    OR (availability IN ('UNAVAILABLE','STALE'))
  ),
  CONSTRAINT cross_market_v2_identity UNIQUE (owner_scope_hash, symbol, available_at)
);

CREATE INDEX cross_market_risk_snapshots_v2_latest_idx
ON public.cross_market_risk_snapshots_v2(owner_user_id, owner_scope_hash, symbol, available_at DESC, created_at DESC);

CREATE OR REPLACE FUNCTION public.reject_cross_market_v2_mutation()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
AS $function$
BEGIN
  RAISE EXCEPTION 'cross-market v2 snapshots are append-only' USING ERRCODE = '55000';
END
$function$;

CREATE TRIGGER cross_market_risk_snapshots_v2_immutable
BEFORE UPDATE OR DELETE ON public.cross_market_risk_snapshots_v2
FOR EACH ROW EXECUTE FUNCTION public.reject_cross_market_v2_mutation();

ALTER TABLE public.cross_market_risk_snapshots_v2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cross_market_risk_snapshots_v2 FORCE ROW LEVEL SECURITY;
CREATE POLICY cross_market_risk_snapshots_v2_flyway_function
ON public.cross_market_risk_snapshots_v2 FOR ALL TO flyway USING (true) WITH CHECK (true);
GRANT SELECT, INSERT ON TABLE public.cross_market_risk_snapshots_v2 TO flyway;

CREATE OR REPLACE FUNCTION public.append_cross_market_risk_snapshot_v2(
  p_snapshot_id uuid,
  p_owner_user_id text,
  p_owner_scope_hash text,
  p_symbol text,
  p_available_at timestamptz,
  p_stale_at timestamptz,
  p_evidence_mode text,
  p_storage_mode text,
  p_runtime_mode text,
  p_availability text,
  p_quality text,
  p_score numeric,
  p_threshold_percentile numeric,
  p_threshold_artifact_hash text,
  p_config_hash text,
  p_exposure_classification text,
  p_exposure_available_at timestamptz,
  p_exposure_catalog_hash text,
  p_semantic_input_hash text,
  p_artifact_hash text,
  p_payload_text text,
  p_explanation_text text
)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $append_cross_market_risk_snapshot_v2$
DECLARE
  expected_semantic_hash text;
  existing public.cross_market_risk_snapshots_v2%ROWTYPE;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_market_writer'
     OR p_runtime_mode = 'ENFORCED'
     OR p_owner_scope_hash !~ '^[0-9a-f]{64}$'
     OR p_config_hash !~ '^[0-9a-f]{64}$'
     OR p_exposure_catalog_hash !~ '^[0-9a-f]{64}$'
     OR p_semantic_input_hash !~ '^[0-9a-f]{64}$'
     OR p_artifact_hash !~ '^[0-9a-f]{64}$'
     OR octet_length(p_payload_text) NOT BETWEEN 2 AND 262144
     OR octet_length(p_explanation_text) NOT BETWEEN 2 AND 65536 THEN
    RAISE EXCEPTION 'cross-market v2 append arguments are invalid' USING ERRCODE = '22023';
  END IF;
  expected_semantic_hash := encode(digest(convert_to(concat_ws(E'\n',
    's6-cross-market-semantic-v2', p_symbol,
    to_char(p_available_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    to_char(p_stale_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    coalesce(p_score::text, ''), coalesce(p_threshold_percentile::text, ''),
    coalesce(p_threshold_artifact_hash, ''), p_config_hash,
    p_exposure_classification, p_exposure_catalog_hash), 'UTF8'), 'sha256'), 'hex');
  IF expected_semantic_hash <> p_semantic_input_hash THEN
    RAISE EXCEPTION 'cross-market semantic input hash mismatch' USING ERRCODE = '22023';
  END IF;
  IF p_exposure_available_at <> p_available_at THEN
    RAISE EXCEPTION 'cross-market exposure chronology mismatch' USING ERRCODE = '22023';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(concat_ws(E'\n',
    p_owner_scope_hash, p_symbol,
    to_char(p_available_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')), 0));
  SELECT * INTO existing FROM public.cross_market_risk_snapshots_v2 s
  WHERE s.owner_scope_hash = p_owner_scope_hash AND s.symbol = p_symbol
    AND s.available_at = p_available_at;
  IF FOUND THEN
    IF existing.snapshot_id = p_snapshot_id
       AND existing.semantic_input_hash = p_semantic_input_hash
       AND existing.artifact_hash = p_artifact_hash THEN
      RETURN 'NO_OP';
    END IF;
    RAISE EXCEPTION 'cross-market v2 identity hash conflict' USING ERRCODE = '23505';
  END IF;

  INSERT INTO public.cross_market_risk_snapshots_v2 (
    snapshot_id, owner_user_id, owner_scope_hash, symbol, available_at, stale_at,
    evidence_mode, storage_mode, runtime_mode, availability, quality, score,
    threshold_percentile, threshold_artifact_hash, config_hash,
    exposure_classification, exposure_available_at, exposure_catalog_hash,
    semantic_input_hash, artifact_hash, payload_json, explanation_json
  ) VALUES (
    p_snapshot_id, p_owner_user_id, p_owner_scope_hash, p_symbol, p_available_at, p_stale_at,
    p_evidence_mode, p_storage_mode, p_runtime_mode, p_availability, p_quality, p_score,
    p_threshold_percentile, p_threshold_artifact_hash, p_config_hash,
    p_exposure_classification, p_exposure_available_at, p_exposure_catalog_hash,
    p_semantic_input_hash, p_artifact_hash, p_payload_text::jsonb, p_explanation_text::jsonb
  );
  RETURN 'INSERTED';
END
$append_cross_market_risk_snapshot_v2$;

CREATE OR REPLACE FUNCTION public.read_cross_market_decision_input_v2(
  p_owner_scope_hash text,
  p_symbol text,
  p_evaluation_as_of timestamptz
)
RETURNS TABLE(
  snapshot_id uuid, owner_scope_hash text, symbol text, available_at timestamptz,
  stale_at timestamptz, evidence_mode text, storage_mode text, runtime_mode text,
  availability text, quality text, score numeric, threshold_percentile numeric,
  threshold_artifact_hash text, config_hash text, exposure_classification text,
  exposure_available_at timestamptz, exposure_catalog_hash text,
  semantic_input_hash text, artifact_hash text, payload_json jsonb
)
LANGUAGE sql SECURITY DEFINER STABLE
SET search_path = pg_catalog, public, pg_temp
AS $read_cross_market_decision_input_v2$
  SELECT s.snapshot_id, s.owner_scope_hash, s.symbol, s.available_at, s.stale_at,
    s.evidence_mode, s.storage_mode, s.runtime_mode, s.availability, s.quality,
    s.score, s.threshold_percentile, s.threshold_artifact_hash, s.config_hash,
    s.exposure_classification, s.exposure_available_at, s.exposure_catalog_hash,
    s.semantic_input_hash, s.artifact_hash, s.payload_json
  FROM public.cross_market_risk_snapshots_v2 s
  WHERE session_user = 'decision_app'
    AND s.owner_user_id = nullif(current_setting('app.actor_user_id', true), '')
    AND s.owner_scope_hash = p_owner_scope_hash
    AND s.symbol = p_symbol
    AND s.available_at <= p_evaluation_as_of
  ORDER BY s.available_at DESC, s.created_at DESC
  LIMIT 1
$read_cross_market_decision_input_v2$;

ALTER TABLE public.cross_market_risk_snapshots_v2 OWNER TO flyway;
ALTER FUNCTION public.reject_cross_market_v2_mutation() OWNER TO flyway;
ALTER FUNCTION public.append_cross_market_risk_snapshot_v2(uuid, text, text, text,
  timestamptz, timestamptz, text, text, text, text, text, numeric, numeric, text,
  text, text, timestamptz, text, text, text, text, text) OWNER TO flyway;
ALTER FUNCTION public.read_cross_market_decision_input_v2(text, text, timestamptz) OWNER TO flyway;

REVOKE ALL PRIVILEGES ON TABLE public.cross_market_risk_snapshots_v2
FROM PUBLIC, decision_app, decision_market_writer;
REVOKE ALL PRIVILEGES ON FUNCTION public.reject_cross_market_v2_mutation(),
  public.append_cross_market_risk_snapshot_v2(uuid, text, text, text, timestamptz,
    timestamptz, text, text, text, text, text, numeric, numeric, text, text, text,
    timestamptz, text, text, text, text, text),
  public.read_cross_market_decision_input_v2(text, text, timestamptz)
FROM PUBLIC, decision_app, decision_market_writer;
GRANT EXECUTE ON FUNCTION public.append_cross_market_risk_snapshot_v2(uuid, text,
  text, text, timestamptz, timestamptz, text, text, text, text, text, numeric,
  numeric, text, text, text, timestamptz, text, text, text, text, text)
TO decision_market_writer;
GRANT EXECUTE ON FUNCTION public.read_cross_market_decision_input_v2(text, text, timestamptz)
TO decision_app;
REVOKE CREATE ON SCHEMA public FROM decision_market_writer, decision_app;
