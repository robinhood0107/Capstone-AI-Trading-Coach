-- S6.5 append-only financial-engineering snapshots and complete report manifests.

CREATE TABLE public.financial_engineering_snapshots (
  snapshot_id text PRIMARY KEY CHECK (snapshot_id ~ '^fes_[0-9a-f]{24}$'),
  schema_version integer NOT NULL CHECK (schema_version = 1),
  symbol text NOT NULL CHECK (symbol ~ '^[0-9A-Z./-]{1,32}$'),
  session_date date NOT NULL,
  as_of timestamptz NOT NULL,
  available_at timestamptz NOT NULL CHECK (available_at >= as_of),
  source_manifest_hash text NOT NULL CHECK (source_manifest_hash ~ '^[0-9a-f]{64}$'),
  config_hash text NOT NULL CHECK (config_hash ~ '^[0-9a-f]{64}$'),
  numeric_payload_hash text NOT NULL CHECK (numeric_payload_hash ~ '^[0-9a-f]{64}$'),
  artifact_hash text NOT NULL UNIQUE CHECK (artifact_hash ~ '^[0-9a-f]{64}$'),
  availability text NOT NULL CHECK (availability IN ('AVAILABLE','ABSTAIN','NOT_AVAILABLE')),
  quality text NOT NULL CHECK (quality IN ('PASS','WARN','EVIDENCE_GAP')),
  staleness text NOT NULL CHECK (staleness IN ('FRESH','STALE','NOT_ESTIMABLE')),
  numeric_payload jsonb NOT NULL CHECK (
    jsonb_typeof(numeric_payload) = 'object'
    AND octet_length(numeric_payload::text) BETWEEN 2 AND 262144
  ),
  explanatory_payload jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (
    jsonb_typeof(explanatory_payload) = 'object'
    AND octet_length(explanatory_payload::text) <= 65536
  ),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT financial_engineering_snapshot_identity UNIQUE (
    schema_version, symbol, session_date, as_of, available_at
  )
);

CREATE TABLE public.financial_engineering_report_manifests (
  run_id uuid PRIMARY KEY,
  snapshot_id text NOT NULL UNIQUE REFERENCES public.financial_engineering_snapshots(snapshot_id),
  snapshot_artifact_hash text NOT NULL UNIQUE CHECK (snapshot_artifact_hash ~ '^[0-9a-f]{64}$'),
  report_artifact_hash text NOT NULL UNIQUE CHECK (report_artifact_hash ~ '^[0-9a-f]{64}$'),
  report_bytes integer NOT NULL CHECK (report_bytes BETWEEN 1 AND 1048576),
  steps jsonb NOT NULL CHECK (
    jsonb_typeof(steps) = 'array' AND jsonb_array_length(steps) = 5
    AND octet_length(steps::text) <= 16384
  ),
  complete boolean NOT NULL CHECK (complete),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE OR REPLACE FUNCTION public.reject_financial_engineering_mutation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $function$
BEGIN
  RAISE EXCEPTION 'financial engineering records are append-only' USING ERRCODE = '55000';
END
$function$;

CREATE TRIGGER financial_engineering_snapshot_immutable
BEFORE UPDATE OR DELETE ON public.financial_engineering_snapshots
FOR EACH ROW EXECUTE FUNCTION public.reject_financial_engineering_mutation();

CREATE TRIGGER financial_engineering_report_immutable
BEFORE UPDATE OR DELETE ON public.financial_engineering_report_manifests
FOR EACH ROW EXECUTE FUNCTION public.reject_financial_engineering_mutation();

ALTER TABLE public.financial_engineering_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.financial_engineering_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE public.financial_engineering_report_manifests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.financial_engineering_report_manifests FORCE ROW LEVEL SECURITY;

CREATE POLICY financial_engineering_snapshots_flyway_function
ON public.financial_engineering_snapshots FOR ALL TO flyway USING (true) WITH CHECK (true);
CREATE POLICY financial_engineering_report_manifests_flyway_function
ON public.financial_engineering_report_manifests FOR ALL TO flyway USING (true) WITH CHECK (true);

GRANT SELECT, INSERT ON TABLE public.financial_engineering_snapshots TO flyway;
GRANT SELECT, INSERT ON TABLE public.financial_engineering_report_manifests TO flyway;

CREATE OR REPLACE FUNCTION public.append_financial_engineering_result(
  p_run_id uuid,
  p_schema_version integer,
  p_symbol text,
  p_session_date date,
  p_as_of timestamptz,
  p_available_at timestamptz,
  p_source_manifest_hash text,
  p_config_hash text,
  p_numeric_payload_hash text,
  p_artifact_hash text,
  p_availability text,
  p_quality text,
  p_staleness text,
  p_numeric_payload_text text,
  p_explanatory_payload_text text,
  p_report_artifact_hash text,
  p_report_bytes integer,
  p_steps_text text
)
RETURNS TABLE(outcome text, snapshot_id text)
LANGUAGE plpgsql
SECURITY DEFINER
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $append_financial_engineering_result$
DECLARE
  computed_snapshot_id text;
  existing public.financial_engineering_snapshots%ROWTYPE;
  existing_report public.financial_engineering_report_manifests%ROWTYPE;
BEGIN
  IF current_user <> 'flyway' OR session_user <> 'decision_market_writer'
     OR p_schema_version <> 1
     OR p_symbol !~ '^[0-9A-Z./-]{1,32}$'
     OR p_source_manifest_hash !~ '^[0-9a-f]{64}$'
     OR p_config_hash !~ '^[0-9a-f]{64}$'
     OR p_numeric_payload_hash !~ '^[0-9a-f]{64}$'
     OR p_artifact_hash !~ '^[0-9a-f]{64}$'
     OR p_report_artifact_hash !~ '^[0-9a-f]{64}$'
     OR octet_length(p_numeric_payload_text) NOT BETWEEN 2 AND 262144
     OR octet_length(p_explanatory_payload_text) NOT BETWEEN 2 AND 65536
     OR octet_length(p_steps_text) NOT BETWEEN 2 AND 16384 THEN
    RAISE EXCEPTION 'financial engineering append arguments are invalid' USING ERRCODE = '22023';
  END IF;
  IF encode(digest(convert_to(p_numeric_payload_text, 'UTF8'), 'sha256'), 'hex') <> p_numeric_payload_hash THEN
    RAISE EXCEPTION 'numeric payload hash mismatch' USING ERRCODE = '22023';
  END IF;
  computed_snapshot_id := 'fes_' || substr(encode(digest(convert_to(concat_ws(E'\n',
    'financial-engineering-snapshot-identity-v1', p_schema_version::text, p_symbol,
    p_session_date::text, p_as_of::text, p_available_at::text), 'UTF8'), 'sha256'), 'hex'), 1, 24);

  PERFORM pg_advisory_xact_lock(hashtextextended(computed_snapshot_id, 0));
  SELECT * INTO existing FROM public.financial_engineering_snapshots
  WHERE financial_engineering_snapshots.snapshot_id = computed_snapshot_id;
  IF FOUND THEN
    SELECT * INTO existing_report FROM public.financial_engineering_report_manifests
    WHERE financial_engineering_report_manifests.snapshot_id = computed_snapshot_id;
    IF existing.artifact_hash = p_artifact_hash
       AND existing.numeric_payload_hash = p_numeric_payload_hash
       AND existing_report.report_artifact_hash = p_report_artifact_hash
       AND existing_report.complete THEN
      RETURN QUERY SELECT 'NO_OP'::text, computed_snapshot_id;
      RETURN;
    END IF;
    RAISE EXCEPTION 'financial engineering identity hash conflict' USING ERRCODE = '23505';
  END IF;

  INSERT INTO public.financial_engineering_snapshots (
    snapshot_id, schema_version, symbol, session_date, as_of, available_at,
    source_manifest_hash, config_hash, numeric_payload_hash, artifact_hash,
    availability, quality, staleness, numeric_payload, explanatory_payload
  ) VALUES (
    computed_snapshot_id, p_schema_version, p_symbol, p_session_date, p_as_of, p_available_at,
    p_source_manifest_hash, p_config_hash, p_numeric_payload_hash, p_artifact_hash,
    p_availability, p_quality, p_staleness, p_numeric_payload_text::jsonb,
    p_explanatory_payload_text::jsonb
  );
  INSERT INTO public.financial_engineering_report_manifests (
    run_id, snapshot_id, snapshot_artifact_hash, report_artifact_hash,
    report_bytes, steps, complete
  ) VALUES (
    p_run_id, computed_snapshot_id, p_artifact_hash, p_report_artifact_hash,
    p_report_bytes, p_steps_text::jsonb, true
  );
  RETURN QUERY SELECT 'INSERTED'::text, computed_snapshot_id;
END
$append_financial_engineering_result$;

ALTER FUNCTION public.append_financial_engineering_result(uuid, integer, text,
  date, timestamptz, timestamptz, text, text, text, text, text, text, text,
  text, text, text, integer, text) OWNER TO flyway;

CREATE OR REPLACE FUNCTION public.read_financial_engineering_snapshot(
  p_symbol text,
  p_evaluation_as_of timestamptz
)
RETURNS TABLE(
  snapshot_id text, schema_version integer, symbol text, session_date date,
  as_of timestamptz, available_at timestamptz, source_manifest_hash text,
  config_hash text, numeric_payload_hash text, artifact_hash text,
  availability text, quality text, staleness text, numeric_payload jsonb,
  explanatory_payload jsonb, report_artifact_hash text, created_at timestamptz
)
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog, public, pg_temp
AS $read_financial_engineering_snapshot$
  SELECT s.snapshot_id, s.schema_version, s.symbol, s.session_date, s.as_of,
         s.available_at, s.source_manifest_hash, s.config_hash,
         s.numeric_payload_hash, s.artifact_hash, s.availability, s.quality,
         s.staleness, s.numeric_payload, s.explanatory_payload,
         r.report_artifact_hash, s.created_at
  FROM public.financial_engineering_snapshots s
  JOIN public.financial_engineering_report_manifests r ON r.snapshot_id = s.snapshot_id
  WHERE session_user = 'decision_app'
    AND s.symbol = p_symbol
    AND s.available_at <= p_evaluation_as_of
    AND r.complete
  ORDER BY s.available_at DESC, s.created_at DESC
  LIMIT 1
$read_financial_engineering_snapshot$;

ALTER FUNCTION public.reject_financial_engineering_mutation() OWNER TO flyway;
ALTER FUNCTION public.read_financial_engineering_snapshot(text, timestamptz) OWNER TO flyway;

REVOKE ALL PRIVILEGES ON TABLE public.financial_engineering_snapshots,
  public.financial_engineering_report_manifests
FROM PUBLIC, decision_app, decision_market_writer;
REVOKE ALL PRIVILEGES ON FUNCTION public.reject_financial_engineering_mutation(),
  public.append_financial_engineering_result(uuid, integer, text, date, timestamptz,
    timestamptz, text, text, text, text, text, text, text, text, text, text,
    integer, text),
  public.read_financial_engineering_snapshot(text, timestamptz)
FROM PUBLIC, decision_app, decision_market_writer;

GRANT EXECUTE ON FUNCTION public.append_financial_engineering_result(uuid, integer,
  text, date, timestamptz, timestamptz, text, text, text, text, text, text, text,
  text, text, text, integer, text) TO decision_market_writer;
GRANT EXECUTE ON FUNCTION public.read_financial_engineering_snapshot(text, timestamptz)
TO decision_app;
REVOKE CREATE ON SCHEMA public FROM decision_market_writer, decision_app;
