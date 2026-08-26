-- Owner-validated Return Engine v2 bundles cross one function-only atomic import boundary.
-- Raw files remain in the owner-private content-addressed archive; PostgreSQL stores projections only.

CREATE TABLE public.p1_return_artifact_bundle (
  bundle_sha256 text PRIMARY KEY,
  artifact_id text NOT NULL UNIQUE,
  run_id text NOT NULL UNIQUE,
  input_pack_sha256 text NOT NULL,
  manifest_sha256 text NOT NULL,
  packet_sha256 text NOT NULL,
  evidence_mode text NOT NULL CHECK (evidence_mode IN ('REAL_TEAM_B','SYNTHETIC_GOLDEN')),
  real_team_b boolean NOT NULL,
  model_quality text NOT NULL CHECK (model_quality IN ('PASS','BELOW_BASELINE','NOT_EVALUATED_SYNTHETIC')),
  mock_runtime_eligible boolean NOT NULL,
  session_date date NOT NULL,
  as_of timestamptz NOT NULL,
  fresh_until timestamptz NOT NULL,
  model_projection_sha256 text NOT NULL,
  backtest_projection_sha256 text NOT NULL,
  imported_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  CHECK (bundle_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (artifact_id = 'artifact_p1_' || substr(bundle_sha256,1,24)),
  CHECK (run_id ~ '^run_[A-Za-z0-9_-]{8,96}$'),
  CHECK (input_pack_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (manifest_sha256 = bundle_sha256),
  CHECK (packet_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (model_projection_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (backtest_projection_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (fresh_until > as_of),
  CHECK (
    (evidence_mode='SYNTHETIC_GOLDEN' AND NOT real_team_b
      AND model_quality='NOT_EVALUATED_SYNTHETIC' AND mock_runtime_eligible)
    OR
    (evidence_mode='REAL_TEAM_B' AND real_team_b
      AND model_quality IN ('PASS','BELOW_BASELINE')
      AND (model_quality='PASS' OR NOT mock_runtime_eligible))
  )
);

CREATE TABLE public.p1_return_signal_projection (
  bundle_sha256 text NOT NULL REFERENCES public.p1_return_artifact_bundle(bundle_sha256) ON DELETE RESTRICT,
  producer text NOT NULL CHECK (producer IN ('LSTM','RULE_BASELINE')),
  symbol text NOT NULL CHECK (symbol ~ '^[0-9]{6}$'),
  session_date date NOT NULL,
  as_of timestamptz NOT NULL,
  signal text NOT NULL CHECK (signal IN ('BUY','HOLD','SELL')),
  confidence numeric NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  predicted_return numeric NOT NULL CHECK (abs(predicted_return) <= 1000),
  model_version text NOT NULL CHECK (char_length(model_version) BETWEEN 1 AND 128),
  model_report_id text NOT NULL CHECK (model_report_id ~ '^mrp_[A-Za-z0-9_-]{8,96}$'),
  payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  fixture boolean NOT NULL,
  PRIMARY KEY (bundle_sha256,producer,symbol)
);

CREATE TRIGGER p1_return_artifact_bundle_append_only
BEFORE UPDATE OR DELETE ON public.p1_return_artifact_bundle
FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

CREATE TRIGGER p1_return_signal_projection_append_only
BEFORE UPDATE OR DELETE ON public.p1_return_signal_projection
FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

CREATE VIEW public.current_p1_return_signal_pointer
WITH (security_barrier=true)
AS
SELECT DISTINCT ON (signal.symbol)
  signal.symbol,bundle.bundle_sha256,bundle.artifact_id,bundle.run_id,bundle.session_date,bundle.as_of
FROM public.p1_return_signal_projection signal
JOIN public.p1_return_artifact_bundle bundle USING (bundle_sha256)
WHERE bundle.real_team_b AND bundle.model_quality='PASS' AND bundle.mock_runtime_eligible
ORDER BY signal.symbol,bundle.imported_at DESC,bundle.bundle_sha256 DESC;

CREATE FUNCTION public.import_p1_return_bundle_v1(p_packet_text text,p_packet_sha256 text)
RETURNS TABLE(outcome text,artifact_id text,run_id text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $import_p1_return_bundle_v1$
DECLARE
  packet jsonb;
  existing public.p1_return_artifact_bundle%ROWTYPE;
  model_projection_text text;
  backtest_projection_text text;
  model_projection jsonb;
  backtest_projection jsonb;
  expected_evidence text;
  expected_fixture text;
  v_artifact_id text;
  v_bundle_sha256 text;
  v_run_id text;
  v_session_date date;
  v_as_of timestamptz;
  v_fresh_until timestamptz;
BEGIN
  IF session_user<>'decision_worker'
     OR p_packet_sha256!~'^[0-9a-f]{64}$'
     OR p_packet_text IS NULL
     OR octet_length(p_packet_text) NOT BETWEEN 2 AND 524288
     OR NOT public.json_text_depth_within(p_packet_text,12)
     OR encode(public.digest(p_packet_text,'sha256'),'hex')<>p_packet_sha256 THEN
    RAISE EXCEPTION 'P1 artifact import packet denied' USING ERRCODE='42501';
  END IF;
  packet:=p_packet_text::jsonb;
  IF jsonb_typeof(packet)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(packet))<>21
     OR NOT packet ?& ARRAY[
       'artifactId','asOf','backtestProjectionSha256','backtestProjectionText','bundleSha256',
       'contractId','evidenceMode','fixtureClass','freshUntil','inputPackSha256',
       'manifestFileName','manifestSha256','mockRuntimeEligible','modelProjectionSha256',
       'modelProjectionText','modelQuality','realTeamB','runId','sessionDate','signals','sourceWorkspace'
     ]
     OR packet->>'contractId'<>'p1-return-artifact-import.v1'
     OR packet->>'sourceWorkspace'<>'return-engine'
     OR packet->>'manifestFileName'<>'p1-return-engine-manifest.v2.json' THEN
    RAISE EXCEPTION 'P1 artifact import packet shape invalid' USING ERRCODE='22023';
  END IF;

  v_bundle_sha256:=packet->>'bundleSha256';
  v_artifact_id:=packet->>'artifactId';
  v_run_id:=packet->>'runId';
  IF v_bundle_sha256!~'^[0-9a-f]{64}$'
     OR packet->>'manifestSha256'<>v_bundle_sha256
     OR v_artifact_id<>'artifact_p1_'||substr(v_bundle_sha256,1,24)
     OR v_run_id!~'^run_[A-Za-z0-9_-]{8,96}$'
     OR packet->>'inputPackSha256'!~'^[0-9a-f]{64}$'
     OR packet->>'modelProjectionSha256'!~'^[0-9a-f]{64}$'
     OR packet->>'backtestProjectionSha256'!~'^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'P1 artifact import identity invalid' USING ERRCODE='22023';
  END IF;
  v_session_date:=(packet->>'sessionDate')::date;
  v_as_of:=(packet->>'asOf')::timestamptz;
  v_fresh_until:=(packet->>'freshUntil')::timestamptz;
  IF v_fresh_until<=v_as_of THEN
    RAISE EXCEPTION 'P1 artifact import clock invalid' USING ERRCODE='22023';
  END IF;

  IF packet->>'evidenceMode'='SYNTHETIC_GOLDEN' THEN
    expected_evidence:='SYNTHETIC_DEMO';expected_fixture:='SYNTHETIC_FAKE_E2E';
    IF packet->'realTeamB'<>'false'::jsonb
       OR packet->>'modelQuality'<>'NOT_EVALUATED_SYNTHETIC'
       OR packet->'mockRuntimeEligible'<>'true'::jsonb THEN
      RAISE EXCEPTION 'P1 synthetic artifact truth invalid' USING ERRCODE='22023';
    END IF;
  ELSIF packet->>'evidenceMode'='REAL_TEAM_B' THEN
    expected_evidence:='REAL_ARTIFACT';expected_fixture:='REAL_ARTIFACT';
    IF packet->'realTeamB'<>'true'::jsonb
       OR packet->>'modelQuality' NOT IN ('PASS','BELOW_BASELINE')
       OR (packet->>'modelQuality'<>'PASS' AND packet->'mockRuntimeEligible'<>'false'::jsonb) THEN
      RAISE EXCEPTION 'P1 real artifact truth invalid' USING ERRCODE='22023';
    END IF;
  ELSE
    RAISE EXCEPTION 'P1 artifact evidence mode invalid' USING ERRCODE='22023';
  END IF;
  IF packet->>'fixtureClass'<>expected_fixture THEN
    RAISE EXCEPTION 'P1 artifact fixture class invalid' USING ERRCODE='22023';
  END IF;

  model_projection_text:=packet->>'modelProjectionText';
  backtest_projection_text:=packet->>'backtestProjectionText';
  IF model_projection_text IS NULL OR backtest_projection_text IS NULL
     OR octet_length(model_projection_text) NOT BETWEEN 2 AND 524288
     OR octet_length(backtest_projection_text) NOT BETWEEN 2 AND 524288
     OR NOT public.json_text_depth_within(model_projection_text,10)
     OR NOT public.json_text_depth_within(backtest_projection_text,10)
     OR encode(public.digest(model_projection_text,'sha256'),'hex')<>packet->>'modelProjectionSha256'
     OR encode(public.digest(backtest_projection_text,'sha256'),'hex')<>packet->>'backtestProjectionSha256' THEN
    RAISE EXCEPTION 'P1 artifact projection hash invalid' USING ERRCODE='22023';
  END IF;
  model_projection:=model_projection_text::jsonb;
  backtest_projection:=backtest_projection_text::jsonb;
  IF model_projection->'success'<>'true'::jsonb OR model_projection->'error'<>'null'::jsonb
     OR model_projection#>>'{data,evidenceMode}'<>expected_evidence
     OR model_projection#>'{data,performanceClaimAllowed}'<>'false'::jsonb
     OR model_projection#>>'{data,viewState}'<>'READY'
     OR model_projection#>>'{data,view,runId}'<>v_run_id
     OR (model_projection#>>'{data,asOf}')::timestamptz<>v_as_of
     OR (model_projection#>>'{data,freshUntil}')::timestamptz<>v_fresh_until
     OR backtest_projection->'success'<>'true'::jsonb OR backtest_projection->'error'<>'null'::jsonb
     OR backtest_projection#>>'{data,evidenceMode}'<>expected_evidence
     OR backtest_projection#>'{data,performanceClaimAllowed}'<>'false'::jsonb
     OR backtest_projection#>>'{data,viewState}'<>'READY'
     OR backtest_projection#>>'{data,view,runId}'<>v_run_id
     OR backtest_projection#>>'{data,view,fixtureClass}'<>expected_fixture
     OR (backtest_projection#>>'{data,asOf}')::timestamptz<>v_as_of
     OR (backtest_projection#>>'{data,freshUntil}')::timestamptz<>v_fresh_until THEN
    RAISE EXCEPTION 'P1 artifact projection semantics invalid' USING ERRCODE='22023';
  END IF;

  IF jsonb_typeof(packet->'signals')<>'array' OR jsonb_array_length(packet->'signals')<>62
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements(packet->'signals') signal
       WHERE jsonb_typeof(signal)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(signal))<>10
         OR NOT signal ?& ARRAY['asOf','confidence','modelReportId','modelVersion','payloadSha256',
           'predictedReturn','producer','sessionDate','signal','symbol']
         OR signal->>'producer' NOT IN ('LSTM','RULE_BASELINE')
         OR signal->>'symbol'!~'^[0-9]{6}$'
         OR signal->>'sessionDate'<>v_session_date::text
         OR (signal->>'asOf')::timestamptz<>v_as_of
         OR signal->>'signal' NOT IN ('BUY','HOLD','SELL')
         OR (signal->>'confidence')::numeric NOT BETWEEN 0 AND 1
         OR abs((signal->>'predictedReturn')::numeric)>1000
         OR char_length(signal->>'modelVersion') NOT BETWEEN 1 AND 128
         OR signal->>'modelReportId'!~'^mrp_[A-Za-z0-9_-]{8,96}$'
         OR signal->>'payloadSha256'!~'^[0-9a-f]{64}$'
     )
     OR (SELECT count(DISTINCT (signal->>'producer',signal->>'symbol'))
         FROM jsonb_array_elements(packet->'signals') signal)<>62
     OR (SELECT count(DISTINCT signal->>'symbol') FROM jsonb_array_elements(packet->'signals') signal)<>31
     OR (SELECT count(*) FROM jsonb_array_elements(packet->'signals') signal WHERE signal->>'symbol'='132030')<>2
     OR (SELECT count(DISTINCT signal->>'producer') FROM jsonb_array_elements(packet->'signals') signal)<>2 THEN
    RAISE EXCEPTION 'P1 artifact signal projection invalid' USING ERRCODE='22023';
  END IF;

  SELECT * INTO existing FROM public.p1_return_artifact_bundle
  WHERE bundle_sha256=v_bundle_sha256 FOR SHARE;
  IF FOUND THEN
    IF existing.packet_sha256=p_packet_sha256 AND existing.artifact_id=v_artifact_id
       AND existing.run_id=v_run_id THEN
      outcome:='REPLAYED';artifact_id:=v_artifact_id;run_id:=v_run_id;RETURN NEXT;RETURN;
    END IF;
    RAISE EXCEPTION 'P1 artifact bundle identity conflict' USING ERRCODE='23505';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.users WHERE user_id='usr_demo_user' AND status='ACTIVE') THEN
    RAISE EXCEPTION 'P1 artifact owner unavailable' USING ERRCODE='23503';
  END IF;

  INSERT INTO public.p1_return_artifact_bundle(
    bundle_sha256,artifact_id,run_id,input_pack_sha256,manifest_sha256,packet_sha256,
    evidence_mode,real_team_b,model_quality,mock_runtime_eligible,session_date,as_of,fresh_until,
    model_projection_sha256,backtest_projection_sha256
  ) VALUES (
    v_bundle_sha256,v_artifact_id,v_run_id,packet->>'inputPackSha256',packet->>'manifestSha256',
    p_packet_sha256,packet->>'evidenceMode',(packet->>'realTeamB')::boolean,packet->>'modelQuality',
    (packet->>'mockRuntimeEligible')::boolean,v_session_date,v_as_of,v_fresh_until,
    packet->>'modelProjectionSha256',packet->>'backtestProjectionSha256'
  );
  INSERT INTO public.p1_return_signal_projection(
    bundle_sha256,producer,symbol,session_date,as_of,signal,confidence,predicted_return,
    model_version,model_report_id,payload_sha256,fixture
  )
  SELECT v_bundle_sha256,signal->>'producer',signal->>'symbol',v_session_date,v_as_of,
    signal->>'signal',(signal->>'confidence')::numeric,(signal->>'predictedReturn')::numeric,
    signal->>'modelVersion',signal->>'modelReportId',signal->>'payloadSha256',
    packet->>'evidenceMode'='SYNTHETIC_GOLDEN'
  FROM jsonb_array_elements(packet->'signals') signal;
  INSERT INTO public.dashboard_artifact_views(
    artifact_id,view_kind,owner_user_id,run_id,fixture_class,evidence_mode,projection_json,
    projection_hash,as_of,fresh_until
  ) VALUES
    (v_artifact_id,'MODEL_EVALUATION','usr_demo_user',v_run_id,expected_fixture,expected_evidence,
      model_projection,'sha256:'||(packet->>'modelProjectionSha256'),v_as_of,v_fresh_until),
    (v_artifact_id,'BACKTEST','usr_demo_user',v_run_id,expected_fixture,expected_evidence,
      backtest_projection,'sha256:'||(packet->>'backtestProjectionSha256'),v_as_of,v_fresh_until);
  INSERT INTO public.artifact_ingest_projection(
    artifact_id,owner_user_id,file_name,producer,run_id,file_hash,schema_version,status,last_ingested_at,duplicate
  ) VALUES (
    v_artifact_id,'usr_demo_user','p1-return-engine-manifest.v2.json','return-engine',v_run_id,
    'sha256:'||v_bundle_sha256,'2.0.0','INGESTED',statement_timestamp(),false
  );
  outcome:='IMPORTED';artifact_id:=v_artifact_id;run_id:=v_run_id;RETURN NEXT;
END
$import_p1_return_bundle_v1$;

CREATE FUNCTION public.read_p1_return_signal_v2(p_symbol text,p_allow_synthetic boolean)
RETURNS TABLE(
  producer text,source_workspace text,session_date date,as_of timestamptz,status text,reason text,
  signal text,confidence numeric,predicted_return numeric,model_version text,model_report_id text,
  latest_completed_session date
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $read_p1_return_signal_v2$
BEGIN
  IF session_user<>'decision_app' OR p_symbol!~'^[0-9]{6}$' OR p_allow_synthetic IS NULL THEN
    RAISE EXCEPTION 'P1 return signal read denied' USING ERRCODE='42501';
  END IF;
  RETURN QUERY
  WITH selected_bundle AS (
    SELECT bundle.*
    FROM public.p1_return_artifact_bundle bundle
    WHERE (bundle.real_team_b AND bundle.model_quality='PASS' AND bundle.mock_runtime_eligible)
       OR (p_allow_synthetic AND NOT bundle.real_team_b AND bundle.evidence_mode='SYNTHETIC_GOLDEN')
    ORDER BY bundle.real_team_b DESC,bundle.imported_at DESC,bundle.bundle_sha256 DESC
    LIMIT 1
  )
  SELECT component.producer,'return-engine'::text,component.session_date,component.as_of,
    'AVAILABLE'::text,NULL::text,component.signal,component.confidence,component.predicted_return,
    component.model_version,component.model_report_id,component.session_date
  FROM selected_bundle bundle
  JOIN public.p1_return_signal_projection component USING (bundle_sha256)
  WHERE component.symbol=p_symbol
  ORDER BY component.producer;
END
$read_p1_return_signal_v2$;

ALTER TABLE public.p1_return_artifact_bundle OWNER TO flyway;
ALTER TABLE public.p1_return_signal_projection OWNER TO flyway;
ALTER VIEW public.current_p1_return_signal_pointer OWNER TO flyway;
ALTER FUNCTION public.import_p1_return_bundle_v1(text,text) OWNER TO flyway;
ALTER FUNCTION public.read_p1_return_signal_v2(text,boolean) OWNER TO flyway;

REVOKE ALL ON TABLE public.p1_return_artifact_bundle,public.p1_return_signal_projection,
  public.current_p1_return_signal_pointer FROM PUBLIC,decision_app,decision_worker,decision_replay,
  decision_identity,decision_auth,decision_demo;
GRANT SELECT,INSERT ON TABLE public.p1_return_artifact_bundle,public.p1_return_signal_projection TO flyway;
GRANT SELECT ON TABLE public.current_p1_return_signal_pointer TO flyway;
REVOKE ALL ON FUNCTION public.import_p1_return_bundle_v1(text,text),
  public.read_p1_return_signal_v2(text,boolean) FROM PUBLIC,decision_app,decision_worker,
  decision_replay,decision_identity,decision_auth,decision_demo;
GRANT EXECUTE ON FUNCTION public.import_p1_return_bundle_v1(text,text) TO decision_worker;
GRANT EXECUTE ON FUNCTION public.read_p1_return_signal_v2(text,boolean) TO decision_app;
REVOKE CREATE ON SCHEMA public FROM decision_worker;
