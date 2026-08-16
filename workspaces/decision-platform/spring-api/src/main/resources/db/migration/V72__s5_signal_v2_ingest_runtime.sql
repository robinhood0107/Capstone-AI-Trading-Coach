-- S5.5는 V1 skeleton을 수정하지 않고 Signal v2 exact replay/ABSTAIN/provenance 경계를 forward-add한다.
ALTER TABLE public.ingested_signals
  ALTER COLUMN as_of DROP NOT NULL,
  ADD COLUMN contract_version text,
  ADD COLUMN status text,
  ADD COLUMN reason text,
  ADD COLUMN signal text,
  ADD COLUMN evaluation_id text,
  ADD COLUMN model_version text,
  ADD COLUMN model_report_id text,
  ADD COLUMN artifact_sha256 text,
  ADD COLUMN payload_sha256 text,
  ADD COLUMN provenance_sha256 text,
  ADD COLUMN logical_identity_sha256 text,
  ADD COLUMN fixture boolean,
  ADD COLUMN provenance_class text,
  ADD COLUMN payload_canonical_text text,
  ADD COLUMN artifact_verified boolean NOT NULL DEFAULT false,
  ADD COLUMN session_date date;

UPDATE public.ingested_signals
SET contract_version = 'LEGACY_UNVERIFIED',
    status = 'ABSTAIN',
    reason = 'MISSING_EVIDENCE',
    evaluation_id = 'legacy-' || signal_id,
    fixture = true,
    provenance_class = 'LEGACY_UNVERIFIED',
    payload_canonical_text = payload_json::text,
    session_date = as_of::date
WHERE contract_version IS NULL;

ALTER TABLE public.ingested_signals
  ALTER COLUMN contract_version SET NOT NULL,
  ALTER COLUMN status SET NOT NULL,
  ALTER COLUMN evaluation_id SET NOT NULL,
  ALTER COLUMN fixture SET NOT NULL,
  ALTER COLUMN provenance_class SET NOT NULL,
  ALTER COLUMN payload_canonical_text SET NOT NULL,
  ALTER COLUMN session_date SET NOT NULL,
  DROP CONSTRAINT ingested_signals_producer_symbol_as_of_timeframe_unique,
  ADD CONSTRAINT ingested_signals_s5_status_check CHECK (status IN ('AVAILABLE','ABSTAIN')),
  ADD CONSTRAINT ingested_signals_s5_reason_check CHECK (
    reason IS NULL OR reason IN (
      'ARTIFACT_DRIFT','STALE_EVIDENCE','CALIBRATION_FAILED',
      'UNIDENTIFIABLE_OUTPUT','MISSING_EVIDENCE','POSTERIOR_BELOW_THRESHOLD',
      'PRODUCER_FAILED'
    )
  ),
  ADD CONSTRAINT ingested_signals_s5_signal_check CHECK (signal IS NULL OR signal IN ('BUY','HOLD','SELL')),
  ADD CONSTRAINT ingested_signals_s5_producer_workspace_check CHECK (
    (producer IN ('RULE_BASELINE','LSTM') AND source_workspace = 'return-engine')
    OR (producer IN ('LIGHTGBM','HMM') AND source_workspace = 'decision-platform')
    OR contract_version = 'LEGACY_UNVERIFIED'
  ),
  ADD CONSTRAINT ingested_signals_s5_fixture_check CHECK (
    (fixture AND provenance_class IN ('FAKE_CONTRACT','LEGACY_UNVERIFIED'))
    OR (NOT fixture AND provenance_class = 'PRODUCTION')
  ),
  ADD CONSTRAINT ingested_signals_s5_identity_shape_check CHECK (
    contract_version = 'LEGACY_UNVERIFIED'
    OR (
      symbol ~ '^[0-9A-Z._:-]{1,20}$'
      AND evaluation_id ~ '^[A-Za-z0-9._:-]{1,128}$'
      AND model_version IS NOT NULL AND char_length(model_version) BETWEEN 1 AND 128
      AND model_report_id IS NOT NULL AND char_length(model_report_id) BETWEEN 1 AND 128
      AND octet_length(payload_canonical_text) BETWEEN 2 AND 65536
      AND artifact_verified
    )
  ),
  ADD CONSTRAINT ingested_signals_s5_numeric_check CHECK (
    contract_version = 'LEGACY_UNVERIFIED'
    OR (
      (confidence IS NULL OR (
        confidence::text NOT IN ('NaN','Infinity','-Infinity')
        AND confidence BETWEEN 0 AND 1
      ))
      AND (predicted_return IS NULL OR predicted_return::text NOT IN ('NaN','Infinity','-Infinity'))
    )
  ),
  ADD CONSTRAINT ingested_signals_s5_union_check CHECK (
    contract_version = 'LEGACY_UNVERIFIED'
    OR (
      contract_version = 'signal-v2-runtime-v1'
      AND timeframe = '1d'
      AND (
        (status = 'AVAILABLE' AND as_of IS NOT NULL AND signal IS NOT NULL
          AND confidence IS NOT NULL AND reason IS NULL)
        OR
        (status = 'ABSTAIN' AND reason IS NOT NULL AND as_of IS NULL AND signal IS NULL
          AND confidence IS NULL AND predicted_return IS NULL)
      )
    )
  ),
  ADD CONSTRAINT ingested_signals_s5_digest_check CHECK (
    contract_version = 'LEGACY_UNVERIFIED'
    OR (
      artifact_sha256 ~ '^[0-9a-f]{64}$'
      AND payload_sha256 ~ '^[0-9a-f]{64}$'
      AND provenance_sha256 ~ '^[0-9a-f]{64}$'
      AND logical_identity_sha256 ~ '^[0-9a-f]{64}$'
    )
  );

CREATE UNIQUE INDEX ingested_signals_s5_as_of_fixture_unique
  ON public.ingested_signals (producer, symbol, as_of, timeframe, fixture)
  WHERE as_of IS NOT NULL;
CREATE UNIQUE INDEX ingested_signals_s5_identity_unique
  ON public.ingested_signals (logical_identity_sha256);

ALTER TABLE public.ingested_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ingested_signals FORCE ROW LEVEL SECURITY;
CREATE POLICY ingested_signals_s5_flyway_function_policy
  ON public.ingested_signals
  FOR ALL TO flyway USING (true) WITH CHECK (true);
REVOKE ALL PRIVILEGES ON TABLE public.ingested_signals FROM PUBLIC, decision_app;
GRANT SELECT, INSERT, UPDATE ON TABLE public.ingested_signals TO flyway;

CREATE TABLE public.signal_v2_production_pointers (
  producer text NOT NULL,
  timeframe text NOT NULL CHECK (timeframe = '1d'),
  logical_identity_sha256 text NOT NULL UNIQUE,
  activated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (producer, timeframe),
  FOREIGN KEY (logical_identity_sha256) REFERENCES public.ingested_signals(logical_identity_sha256),
  CHECK (producer IN ('RULE_BASELINE','LSTM','LIGHTGBM','HMM')),
  CHECK (logical_identity_sha256 ~ '^[0-9a-f]{64}$')
);
ALTER TABLE public.signal_v2_production_pointers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.signal_v2_production_pointers FORCE ROW LEVEL SECURITY;
CREATE POLICY signal_v2_production_pointers_flyway_function_policy
  ON public.signal_v2_production_pointers
  FOR ALL TO flyway USING (true) WITH CHECK (true);
REVOKE ALL PRIVILEGES ON TABLE public.signal_v2_production_pointers FROM PUBLIC, decision_app;
GRANT SELECT, INSERT, UPDATE ON TABLE public.signal_v2_production_pointers TO flyway;

CREATE FUNCTION public.ingest_signal_v2_exact(
  p_contract_version text,
  p_producer text,
  p_source_workspace text,
  p_symbol text,
  p_session_date date,
  p_as_of timestamptz,
  p_timeframe text,
  p_status text,
  p_reason text,
  p_signal text,
  p_confidence numeric,
  p_predicted_return numeric,
  p_evaluation_id text,
  p_model_version text,
  p_model_report_id text,
  p_artifact_sha256 text,
  p_payload_sha256 text,
  p_provenance_sha256 text,
  p_fixture boolean,
  p_provenance_class text,
  p_payload_canonical_text text
)
RETURNS TABLE(outcome text, signal_id text)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $ingest_signal_v2_exact$
DECLARE
  computed_identity text;
  computed_payload text;
  existing_payload text;
  generated_signal_id text;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app'
     OR p_contract_version <> 'signal-v2-runtime-v1'
     OR p_symbol !~ '^[0-9A-Z._:-]{1,20}$'
     OR p_evaluation_id !~ '^[A-Za-z0-9._:-]{1,128}$'
     OR octet_length(p_payload_canonical_text) NOT BETWEEN 2 AND 65536
     OR p_payload_canonical_text::jsonb IS NULL THEN
    RAISE EXCEPTION 'Signal v2 ingest arguments are invalid' USING ERRCODE = '22023';
  END IF;

  computed_identity := encode(
    digest(
      convert_to(
        concat_ws(E'\n', 'signal-v2-identity-v1', p_contract_version, p_producer,
          p_source_workspace, p_symbol, p_timeframe, p_evaluation_id),
        'UTF8'
      ),
      'sha256'
    ),
    'hex'
  );
  computed_payload := encode(digest(convert_to(p_payload_canonical_text, 'UTF8'), 'sha256'), 'hex');
  IF computed_payload <> p_payload_sha256 THEN
    RAISE EXCEPTION 'Signal v2 payload digest mismatch' USING ERRCODE = '22023';
  END IF;

  -- 같은 logical identity의 concurrent replay/conflict를 한 transaction 순서로 직렬화한다.
  PERFORM pg_advisory_xact_lock(hashtextextended(computed_identity, 0));
  SELECT stored.payload_sha256 INTO existing_payload
  FROM public.ingested_signals AS stored
  WHERE stored.logical_identity_sha256 = computed_identity
  FOR SHARE;
  IF FOUND THEN
    IF existing_payload = computed_payload THEN
      RETURN QUERY SELECT 'REPLAYED'::text, stored.signal_id
      FROM public.ingested_signals AS stored
      WHERE stored.logical_identity_sha256 = computed_identity;
      RETURN;
    END IF;
    RAISE EXCEPTION 'Signal v2 logical identity payload conflict' USING ERRCODE = '23505';
  END IF;

  generated_signal_id := 'sigv2_' || substr(computed_identity, 1, 24);
  INSERT INTO public.ingested_signals (
    signal_id, producer, source_workspace, symbol, as_of, timeframe, confidence,
    predicted_return, feature_summary_json, payload_json, contract_version, status,
    reason, signal, evaluation_id, model_version, model_report_id, artifact_sha256,
    payload_sha256, provenance_sha256, logical_identity_sha256, fixture,
    provenance_class, payload_canonical_text, artifact_verified, session_date
  ) VALUES (
    generated_signal_id, p_producer, p_source_workspace, p_symbol, p_as_of, p_timeframe,
    p_confidence, p_predicted_return, '[]'::jsonb, p_payload_canonical_text::jsonb,
    p_contract_version, p_status, p_reason, p_signal, p_evaluation_id, p_model_version,
    p_model_report_id, p_artifact_sha256, computed_payload, p_provenance_sha256,
    computed_identity, p_fixture, p_provenance_class, p_payload_canonical_text, true,
    p_session_date
  );
  RETURN QUERY SELECT 'INSERTED'::text, generated_signal_id;
END
$ingest_signal_v2_exact$;
ALTER FUNCTION public.ingest_signal_v2_exact(text,text,text,text,date,timestamptz,text,text,text,text,numeric,numeric,text,text,text,text,text,text,boolean,text,text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.ingest_signal_v2_exact(text,text,text,text,date,timestamptz,text,text,text,text,numeric,numeric,text,text,text,text,text,text,boolean,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.ingest_signal_v2_exact(text,text,text,text,date,timestamptz,text,text,text,text,numeric,numeric,text,text,text,text,text,text,boolean,text,text) TO decision_app;

CREATE FUNCTION public.activate_signal_v2_production_pointer(p_logical_identity_sha256 text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $activate_signal_v2_production_pointer$
DECLARE
  candidate public.ingested_signals%ROWTYPE;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_app' THEN
    RAISE EXCEPTION 'Signal v2 pointer actor is invalid' USING ERRCODE = '42501';
  END IF;
  SELECT * INTO candidate FROM public.ingested_signals
  WHERE logical_identity_sha256 = p_logical_identity_sha256 FOR SHARE;
  IF NOT FOUND OR candidate.fixture OR candidate.provenance_class <> 'PRODUCTION'
     OR NOT candidate.artifact_verified OR candidate.status <> 'AVAILABLE' THEN
    RAISE EXCEPTION 'Signal v2 production pointer candidate is invalid' USING ERRCODE = '22023';
  END IF;
  INSERT INTO public.signal_v2_production_pointers(producer, timeframe, logical_identity_sha256)
  VALUES (candidate.producer, candidate.timeframe, candidate.logical_identity_sha256)
  ON CONFLICT (producer, timeframe) DO UPDATE
  SET logical_identity_sha256 = EXCLUDED.logical_identity_sha256,
      activated_at = clock_timestamp();
END
$activate_signal_v2_production_pointer$;
ALTER FUNCTION public.activate_signal_v2_production_pointer(text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.activate_signal_v2_production_pointer(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.activate_signal_v2_production_pointer(text) TO decision_app;

CREATE FUNCTION public.read_production_signal_v2(p_symbol text)
RETURNS TABLE(
  producer text, source_workspace text, symbol text, session_date date, as_of timestamptz,
  timeframe text, status text, reason text, signal text, confidence numeric,
  predicted_return numeric, model_version text, model_report_id text
)
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $read_production_signal_v2$
  SELECT stored.producer, stored.source_workspace, stored.symbol, stored.session_date,
    stored.as_of, stored.timeframe, stored.status, stored.reason, stored.signal,
    stored.confidence, stored.predicted_return, stored.model_version, stored.model_report_id
  FROM public.signal_v2_production_pointers AS pointer
  JOIN public.ingested_signals AS stored
    ON stored.logical_identity_sha256 = pointer.logical_identity_sha256
  WHERE current_user = 'flyway' AND session_user = 'decision_app'
    AND p_symbol ~ '^[0-9A-Z._:-]{1,20}$'
    AND stored.symbol = p_symbol AND stored.fixture = false
    AND stored.provenance_class = 'PRODUCTION' AND stored.artifact_verified
  ORDER BY stored.producer
$read_production_signal_v2$;
ALTER FUNCTION public.read_production_signal_v2(text) OWNER TO flyway;
REVOKE ALL PRIVILEGES ON FUNCTION public.read_production_signal_v2(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_production_signal_v2(text) TO decision_app;
