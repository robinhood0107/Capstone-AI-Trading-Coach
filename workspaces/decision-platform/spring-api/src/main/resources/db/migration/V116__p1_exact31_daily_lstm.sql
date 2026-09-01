-- Current P1 Return path: confidence-free Git Model Seed plus exact-31 daily Rule+LSTM.
-- V88 and its historical bytes remain unchanged; this migration adds the current boundary.

ALTER TABLE public.p1_return_artifact_bundle
  ADD COLUMN model_sha256 text;

ALTER TABLE public.p1_return_artifact_bundle
  ADD CONSTRAINT p1_return_artifact_model_sha_v116
    CHECK (model_sha256 IS NULL OR model_sha256 ~ '^[0-9a-f]{64}$');

DO $drop_v88_quality_check$
DECLARE constraint_name text;
BEGIN
  SELECT conname INTO constraint_name
  FROM pg_constraint
  WHERE conrelid='public.p1_return_artifact_bundle'::regclass
    AND contype='c'
    AND pg_get_constraintdef(oid) LIKE '%model_quality%mock_runtime_eligible%';
  IF constraint_name IS NOT NULL THEN
    EXECUTE format(
      'ALTER TABLE public.p1_return_artifact_bundle DROP CONSTRAINT %I',
      constraint_name
    );
  END IF;
END
$drop_v88_quality_check$;

ALTER TABLE public.p1_return_artifact_bundle
  ADD CONSTRAINT p1_return_artifact_truth_v116 CHECK (
    (evidence_mode='SYNTHETIC_GOLDEN' AND NOT real_team_b
      AND model_quality='NOT_EVALUATED_SYNTHETIC' AND mock_runtime_eligible)
    OR
    (evidence_mode='REAL_TEAM_B' AND real_team_b
      AND model_quality IN ('PASS','BELOW_BASELINE') AND mock_runtime_eligible)
  );

CREATE TABLE public.p1_return_model_seed_signal (
  bundle_sha256 text NOT NULL
    REFERENCES public.p1_return_artifact_bundle(bundle_sha256) ON DELETE RESTRICT,
  producer text NOT NULL CHECK (producer IN ('LSTM','RULE_BASELINE')),
  symbol text NOT NULL CHECK (symbol ~ '^[0-9]{6}$'),
  session_date date NOT NULL,
  as_of timestamptz NOT NULL,
  signal text NOT NULL CHECK (signal IN ('BUY','HOLD','SELL')),
  predicted_return numeric NOT NULL CHECK (abs(predicted_return)<=1000),
  model_version text NOT NULL CHECK (char_length(model_version) BETWEEN 1 AND 128),
  model_report_id text NOT NULL CHECK (model_report_id ~ '^mrp_[A-Za-z0-9_-]{8,96}$'),
  payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  fixture boolean NOT NULL,
  PRIMARY KEY (bundle_sha256,producer,symbol)
);

CREATE TABLE public.p1_return_daily_signal_batch (
  batch_sha256 text PRIMARY KEY CHECK (batch_sha256 ~ '^[0-9a-f]{64}$'),
  bundle_sha256 text NOT NULL
    REFERENCES public.p1_return_artifact_bundle(bundle_sha256) ON DELETE RESTRICT,
  artifact_id text NOT NULL,
  model_sha256 text NOT NULL CHECK (model_sha256 ~ '^[0-9a-f]{64}$'),
  market_manifest_sha256 text NOT NULL CHECK (market_manifest_sha256 ~ '^[0-9a-f]{64}$'),
  inference_request_sha256 text NOT NULL CHECK (inference_request_sha256 ~ '^[0-9a-f]{64}$'),
  inference_response_sha256 text NOT NULL CHECK (inference_response_sha256 ~ '^[0-9a-f]{64}$'),
  source_session date NOT NULL,
  target_session date NOT NULL,
  status text NOT NULL CHECK (status='COMPLETE'),
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  CHECK (source_session<target_session),
  UNIQUE (bundle_sha256,target_session)
);

CREATE TABLE public.p1_return_daily_signal_projection (
  batch_sha256 text NOT NULL
    REFERENCES public.p1_return_daily_signal_batch(batch_sha256) ON DELETE RESTRICT,
  producer text NOT NULL CHECK (producer IN ('LSTM','RULE_BASELINE')),
  symbol text NOT NULL CHECK (symbol ~ '^[0-9]{6}$'),
  signal text NOT NULL CHECK (signal IN ('BUY','HOLD','SELL')),
  expected_return numeric NOT NULL CHECK (abs(expected_return)<=1000),
  payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  PRIMARY KEY (batch_sha256,producer,symbol)
);

CREATE TRIGGER p1_return_model_seed_signal_append_only
BEFORE UPDATE OR DELETE ON public.p1_return_model_seed_signal
FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

CREATE TRIGGER p1_return_daily_signal_batch_append_only
BEFORE UPDATE OR DELETE ON public.p1_return_daily_signal_batch
FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

CREATE TRIGGER p1_return_daily_signal_projection_append_only
BEFORE UPDATE OR DELETE ON public.p1_return_daily_signal_projection
FOR EACH ROW EXECUTE FUNCTION public.reject_stream_metric_mutation();

CREATE VIEW public.current_p1_return_model_pointer
WITH (security_barrier=true)
AS
SELECT bundle.bundle_sha256,bundle.artifact_id,bundle.run_id,bundle.model_sha256,
       bundle.model_quality,bundle.imported_at
FROM public.p1_return_artifact_bundle bundle
WHERE bundle.real_team_b
  AND bundle.model_quality IN ('PASS','BELOW_BASELINE')
  AND bundle.mock_runtime_eligible
  AND bundle.model_sha256 IS NOT NULL
ORDER BY bundle.imported_at DESC,bundle.bundle_sha256 DESC
LIMIT 1;

DROP VIEW public.current_p1_return_signal_pointer;
CREATE VIEW public.current_p1_return_signal_pointer
WITH (security_barrier=true)
AS
SELECT
  seed.symbol,model.bundle_sha256,model.artifact_id,model.run_id,
  max(seed.session_date) AS session_date,max(seed.as_of) AS as_of
FROM public.current_p1_return_model_pointer model
JOIN public.p1_return_model_seed_signal seed USING (bundle_sha256)
GROUP BY seed.symbol,model.bundle_sha256,model.artifact_id,model.run_id
UNION ALL
SELECT
  signal.symbol,bundle.bundle_sha256,bundle.artifact_id,bundle.run_id,
  max(signal.session_date) AS session_date,max(signal.as_of) AS as_of
FROM public.p1_return_artifact_bundle bundle
JOIN public.p1_return_signal_projection signal USING (bundle_sha256)
WHERE bundle.real_team_b AND bundle.model_quality='PASS'
  AND bundle.mock_runtime_eligible AND bundle.model_sha256 IS NULL
GROUP BY signal.symbol,bundle.bundle_sha256,bundle.artifact_id,bundle.run_id;

CREATE FUNCTION public.import_p1_return_bundle_v2(p_packet_text text,p_packet_sha256 text)
RETURNS TABLE(outcome text,artifact_id text,run_id text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $import_p1_return_bundle_v2$
DECLARE
  packet jsonb;
  existing public.p1_return_artifact_bundle%ROWTYPE;
  v_artifact_id text;
  v_bundle_sha256 text;
  v_run_id text;
  v_session_date date;
  v_as_of timestamptz;
  v_fresh_until timestamptz;
  model_projection jsonb;
  backtest_projection jsonb;
  expected_evidence text;
  expected_fixture text;
BEGIN
  IF session_user<>'decision_worker'
     OR p_packet_sha256!~'^[0-9a-f]{64}$'
     OR p_packet_text IS NULL
     OR octet_length(p_packet_text) NOT BETWEEN 2 AND 524288
     OR NOT public.json_text_depth_within(p_packet_text,12)
     OR encode(public.digest(p_packet_text,'sha256'),'hex')<>p_packet_sha256 THEN
    RAISE EXCEPTION 'P1 v3 artifact import packet denied' USING ERRCODE='42501';
  END IF;
  packet:=p_packet_text::jsonb;
  IF jsonb_typeof(packet)<>'object'
     OR (SELECT count(*) FROM jsonb_object_keys(packet))<>22
     OR NOT packet ?& ARRAY[
       'artifactId','asOf','backtestProjectionSha256','backtestProjectionText','bundleSha256',
       'contractId','evidenceMode','fixtureClass','freshUntil','inputPackSha256',
       'manifestFileName','manifestSha256','mockRuntimeEligible','modelProjectionSha256',
       'modelProjectionText','modelQuality','modelSha256','realTeamB','runId','sessionDate',
       'signals','sourceWorkspace'
     ]
     OR packet->>'contractId'<>'p1-return-artifact-import.v1'
     OR packet->>'sourceWorkspace'<>'return-engine'
     OR packet->>'manifestFileName'<>'p1-return-engine-manifest.v3.json' THEN
    RAISE EXCEPTION 'P1 v3 artifact import shape invalid' USING ERRCODE='22023';
  END IF;
  v_bundle_sha256:=packet->>'bundleSha256';
  v_artifact_id:=packet->>'artifactId';
  v_run_id:=packet->>'runId';
  IF v_bundle_sha256!~'^[0-9a-f]{64}$'
     OR packet->>'manifestSha256'<>v_bundle_sha256
     OR packet->>'modelSha256'!~'^[0-9a-f]{64}$'
     OR v_artifact_id<>'artifact_p1_'||substr(v_bundle_sha256,1,24)
     OR v_run_id!~'^run_[A-Za-z0-9_-]{8,96}$'
     OR packet->>'inputPackSha256'!~'^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'P1 v3 artifact identity invalid' USING ERRCODE='22023';
  END IF;
  v_session_date:=(packet->>'sessionDate')::date;
  v_as_of:=(packet->>'asOf')::timestamptz;
  v_fresh_until:=(packet->>'freshUntil')::timestamptz;
  IF v_fresh_until<=v_as_of THEN
    RAISE EXCEPTION 'P1 v3 artifact clock invalid' USING ERRCODE='22023';
  END IF;
  IF packet->>'evidenceMode'='SYNTHETIC_GOLDEN' THEN
    expected_evidence:='SYNTHETIC_DEMO';expected_fixture:='SYNTHETIC_FAKE_E2E';
    IF packet->'realTeamB'<>'false'::jsonb
       OR packet->>'modelQuality'<>'NOT_EVALUATED_SYNTHETIC'
       OR packet->'mockRuntimeEligible'<>'true'::jsonb THEN
      RAISE EXCEPTION 'P1 v3 synthetic truth invalid' USING ERRCODE='22023';
    END IF;
  ELSIF packet->>'evidenceMode'='REAL_TEAM_B' THEN
    expected_evidence:='REAL_ARTIFACT';expected_fixture:='REAL_ARTIFACT';
    IF packet->'realTeamB'<>'true'::jsonb
       OR packet->>'modelQuality' NOT IN ('PASS','BELOW_BASELINE')
       OR packet->'mockRuntimeEligible'<>'true'::jsonb THEN
      RAISE EXCEPTION 'P1 v3 real artifact truth invalid' USING ERRCODE='22023';
    END IF;
  ELSE
    RAISE EXCEPTION 'P1 v3 evidence mode invalid' USING ERRCODE='22023';
  END IF;
  IF packet->>'fixtureClass'<>expected_fixture THEN
    RAISE EXCEPTION 'P1 v3 fixture class invalid' USING ERRCODE='22023';
  END IF;
  IF packet->>'modelProjectionSha256'!~'^[0-9a-f]{64}$'
     OR packet->>'backtestProjectionSha256'!~'^[0-9a-f]{64}$'
     OR encode(public.digest(packet->>'modelProjectionText','sha256'),'hex')
        <>packet->>'modelProjectionSha256'
     OR encode(public.digest(packet->>'backtestProjectionText','sha256'),'hex')
        <>packet->>'backtestProjectionSha256' THEN
    RAISE EXCEPTION 'P1 v3 projection hash invalid' USING ERRCODE='22023';
  END IF;
  model_projection:=(packet->>'modelProjectionText')::jsonb;
  backtest_projection:=(packet->>'backtestProjectionText')::jsonb;
  IF model_projection->'success'<>'true'::jsonb
     OR model_projection#>>'{data,evidenceMode}'<>expected_evidence
     OR model_projection#>'{data,performanceClaimAllowed}'<>'false'::jsonb
     OR backtest_projection->'success'<>'true'::jsonb
     OR backtest_projection#>>'{data,evidenceMode}'<>expected_evidence
     OR backtest_projection#>'{data,performanceClaimAllowed}'<>'false'::jsonb THEN
    RAISE EXCEPTION 'P1 v3 projection semantics invalid' USING ERRCODE='22023';
  END IF;
  IF jsonb_typeof(packet->'signals')<>'array'
     OR jsonb_array_length(packet->'signals')<>62
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements(packet->'signals') signal
       WHERE jsonb_typeof(signal)<>'object'
         OR (SELECT count(*) FROM jsonb_object_keys(signal))<>9
         OR NOT signal ?& ARRAY['asOf','modelReportId','modelVersion','payloadSha256',
           'predictedReturn','producer','sessionDate','signal','symbol']
         OR signal ? 'confidence'
         OR signal->>'producer' NOT IN ('LSTM','RULE_BASELINE')
         OR signal->>'symbol'!~'^[0-9]{6}$'
         OR signal->>'sessionDate'<>v_session_date::text
         OR (signal->>'asOf')::timestamptz<>v_as_of
         OR signal->>'signal' NOT IN ('BUY','HOLD','SELL')
         OR abs((signal->>'predictedReturn')::numeric)>1000
         OR signal->>'payloadSha256'!~'^[0-9a-f]{64}$'
     )
     OR (SELECT count(DISTINCT (signal->>'producer',signal->>'symbol'))
         FROM jsonb_array_elements(packet->'signals') signal)<>62
     OR (SELECT count(DISTINCT signal->>'symbol')
         FROM jsonb_array_elements(packet->'signals') signal)<>31
     OR (SELECT count(*) FROM jsonb_array_elements(packet->'signals') signal
         WHERE signal->>'symbol'='132030')<>2 THEN
    RAISE EXCEPTION 'P1 v3 signal seed invalid' USING ERRCODE='22023';
  END IF;
  SELECT * INTO existing FROM public.p1_return_artifact_bundle
  WHERE bundle_sha256=v_bundle_sha256 FOR SHARE;
  IF FOUND THEN
    IF existing.packet_sha256=p_packet_sha256
       AND existing.artifact_id=v_artifact_id AND existing.run_id=v_run_id THEN
      outcome:='REPLAYED';artifact_id:=v_artifact_id;run_id:=v_run_id;RETURN NEXT;RETURN;
    END IF;
    RAISE EXCEPTION 'P1 v3 artifact identity conflict' USING ERRCODE='23505';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.users WHERE user_id='usr_demo_user' AND status='ACTIVE') THEN
    RAISE EXCEPTION 'P1 artifact owner unavailable' USING ERRCODE='23503';
  END IF;
  INSERT INTO public.p1_return_artifact_bundle(
    bundle_sha256,artifact_id,run_id,input_pack_sha256,manifest_sha256,packet_sha256,
    evidence_mode,real_team_b,model_quality,mock_runtime_eligible,session_date,as_of,fresh_until,
    model_projection_sha256,backtest_projection_sha256,model_sha256
  ) VALUES (
    v_bundle_sha256,v_artifact_id,v_run_id,packet->>'inputPackSha256',v_bundle_sha256,
    p_packet_sha256,packet->>'evidenceMode',(packet->>'realTeamB')::boolean,
    packet->>'modelQuality',(packet->>'mockRuntimeEligible')::boolean,v_session_date,
    v_as_of,v_fresh_until,packet->>'modelProjectionSha256',
    packet->>'backtestProjectionSha256',packet->>'modelSha256'
  );
  INSERT INTO public.p1_return_model_seed_signal(
    bundle_sha256,producer,symbol,session_date,as_of,signal,predicted_return,
    model_version,model_report_id,payload_sha256,fixture
  )
  SELECT v_bundle_sha256,signal->>'producer',signal->>'symbol',v_session_date,v_as_of,
    signal->>'signal',(signal->>'predictedReturn')::numeric,signal->>'modelVersion',
    signal->>'modelReportId',signal->>'payloadSha256',
    packet->>'evidenceMode'='SYNTHETIC_GOLDEN'
  FROM jsonb_array_elements(packet->'signals') signal;
  INSERT INTO public.dashboard_artifact_views(
    artifact_id,view_kind,owner_user_id,run_id,fixture_class,evidence_mode,projection_json,
    projection_hash,as_of,fresh_until
  ) VALUES
    (v_artifact_id,'MODEL_EVALUATION','usr_demo_user',v_run_id,expected_fixture,
      expected_evidence,model_projection,'sha256:'||(packet->>'modelProjectionSha256'),
      v_as_of,v_fresh_until),
    (v_artifact_id,'BACKTEST','usr_demo_user',v_run_id,expected_fixture,
      expected_evidence,backtest_projection,'sha256:'||(packet->>'backtestProjectionSha256'),
      v_as_of,v_fresh_until);
  INSERT INTO public.artifact_ingest_projection(
    artifact_id,owner_user_id,file_name,producer,run_id,file_hash,schema_version,
    status,last_ingested_at,duplicate
  ) VALUES (
    v_artifact_id,'usr_demo_user','p1-return-engine-manifest.v3.json','return-engine',
    v_run_id,'sha256:'||v_bundle_sha256,'3.0.0','INGESTED',statement_timestamp(),false
  );
  outcome:='IMPORTED';artifact_id:=v_artifact_id;run_id:=v_run_id;RETURN NEXT;
END
$import_p1_return_bundle_v2$;

CREATE FUNCTION public.p1_read_daily_inference_context_v1(p_target_session date)
RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_read_daily_inference_context_v1$
DECLARE model_row record;
DECLARE source_row record;
DECLARE symbols_json jsonb;
DECLARE existing_batch public.p1_return_daily_signal_batch%ROWTYPE;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_target_session IS NULL THEN
    RAISE EXCEPTION 'daily inference context denied' USING ERRCODE='42501';
  END IF;
  SELECT * INTO model_row FROM public.current_p1_return_model_pointer LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT * INTO existing_batch FROM public.p1_return_daily_signal_batch
  WHERE bundle_sha256=model_row.bundle_sha256 AND target_session=p_target_session
    AND status='COMPLETE';
  IF FOUND THEN
    RETURN jsonb_build_object(
      'batchSha256',existing_batch.batch_sha256,'outcome','REPLAYED'
    )::text;
  END IF;
  SELECT manifest.manifest_sha256,manifest.session_date INTO source_row
  FROM public.market_data_manifests manifest
  WHERE manifest.status='ACCEPTED' AND manifest.session_date<p_target_session
  ORDER BY manifest.session_date DESC,manifest.generation DESC LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT jsonb_agg(seed.symbol ORDER BY seed.symbol) INTO symbols_json
  FROM public.p1_return_model_seed_signal seed
  WHERE seed.bundle_sha256=model_row.bundle_sha256 AND seed.producer='LSTM';
  IF jsonb_array_length(COALESCE(symbols_json,'[]'::jsonb))<>31 THEN RETURN NULL; END IF;
  RETURN jsonb_build_object(
    'artifactId',model_row.artifact_id,'bundleSha256',model_row.bundle_sha256,
    'marketManifestSha256',source_row.manifest_sha256,'modelSha256',model_row.model_sha256,
    'outcome','MATERIALIZE','sourceSession',source_row.session_date,
    'symbols',symbols_json,'targetSession',p_target_session
  )::text;
END
$p1_read_daily_inference_context_v1$;

CREATE FUNCTION public.p1_commit_daily_signal_batch_v1(
  p_packet_text text,p_packet_sha256 text
) RETURNS TABLE(outcome text,batch_sha256 text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_commit_daily_signal_batch_v1$
DECLARE packet jsonb;
DECLARE model_row record;
DECLARE existing public.p1_return_daily_signal_batch%ROWTYPE;
DECLARE target date;
DECLARE source date;
BEGIN
  IF session_user<>'decision_automation_runtime'
     OR p_packet_sha256!~'^[0-9a-f]{64}$'
     OR p_packet_text IS NULL OR octet_length(p_packet_text) NOT BETWEEN 2 AND 524288
     OR NOT public.json_text_depth_within(p_packet_text,12)
     OR encode(public.digest(p_packet_text,'sha256'),'hex')<>p_packet_sha256 THEN
    RAISE EXCEPTION 'daily inference batch denied' USING ERRCODE='42501';
  END IF;
  packet:=p_packet_text::jsonb;
  IF jsonb_typeof(packet)<>'object'
     OR (SELECT count(*) FROM jsonb_object_keys(packet))<>10
     OR NOT packet ?& ARRAY['artifactId','bundleSha256','contractId',
       'inferenceRequestSha256','inferenceResponseSha256','marketManifestSha256',
       'modelSha256','signals','sourceSession','targetSession']
     OR packet->>'contractId'<>'p1-return-daily-signal-batch.v1'
     OR packet ? 'confidence' THEN
    RAISE EXCEPTION 'daily inference batch shape invalid' USING ERRCODE='22023';
  END IF;
  target:=(packet->>'targetSession')::date;
  source:=(packet->>'sourceSession')::date;
  SELECT * INTO model_row FROM public.current_p1_return_model_pointer LIMIT 1;
  IF NOT FOUND OR packet->>'bundleSha256'<>model_row.bundle_sha256
     OR packet->>'artifactId'<>model_row.artifact_id
     OR packet->>'modelSha256'<>model_row.model_sha256
     OR source>=target
     OR packet->>'marketManifestSha256'!~'^[0-9a-f]{64}$'
     OR packet->>'inferenceRequestSha256'!~'^[0-9a-f]{64}$'
     OR packet->>'inferenceResponseSha256'!~'^[0-9a-f]{64}$'
     OR NOT EXISTS (
       SELECT 1 FROM public.market_data_manifests manifest
       WHERE manifest.manifest_sha256=packet->>'marketManifestSha256'
         AND manifest.session_date=source AND manifest.status='ACCEPTED'
     ) THEN
    RAISE EXCEPTION 'daily inference batch binding invalid' USING ERRCODE='22023';
  END IF;
  IF jsonb_typeof(packet->'signals')<>'array'
     OR jsonb_array_length(packet->'signals')<>62
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements(packet->'signals') signal
       WHERE jsonb_typeof(signal)<>'object'
         OR (SELECT count(*) FROM jsonb_object_keys(signal))<>4
         OR NOT signal ?& ARRAY['expectedReturn','producer','signal','symbol']
         OR signal ? 'confidence'
         OR signal->>'producer' NOT IN ('LSTM','RULE_BASELINE')
         OR signal->>'symbol'!~'^[0-9]{6}$'
         OR signal->>'signal' NOT IN ('BUY','HOLD','SELL')
         OR abs((signal->>'expectedReturn')::numeric)>1000
     )
     OR (SELECT count(DISTINCT (signal->>'producer',signal->>'symbol'))
         FROM jsonb_array_elements(packet->'signals') signal)<>62
     OR (SELECT count(DISTINCT signal->>'symbol')
         FROM jsonb_array_elements(packet->'signals') signal)<>31
     OR (SELECT count(*) FROM jsonb_array_elements(packet->'signals') signal
         WHERE signal->>'symbol'='132030')<>2 THEN
    RAISE EXCEPTION 'daily inference exact-31 signal invalid' USING ERRCODE='22023';
  END IF;
  SELECT * INTO existing FROM public.p1_return_daily_signal_batch
  WHERE bundle_sha256=model_row.bundle_sha256 AND target_session=target FOR SHARE;
  IF FOUND THEN
    IF existing.batch_sha256=p_packet_sha256 THEN
      outcome:='REPLAYED';batch_sha256:=p_packet_sha256;RETURN NEXT;RETURN;
    END IF;
    RAISE EXCEPTION 'daily inference identity conflict' USING ERRCODE='23505';
  END IF;
  INSERT INTO public.p1_return_daily_signal_batch(
    batch_sha256,bundle_sha256,artifact_id,model_sha256,market_manifest_sha256,
    inference_request_sha256,inference_response_sha256,source_session,target_session,status
  ) VALUES (
    p_packet_sha256,model_row.bundle_sha256,model_row.artifact_id,model_row.model_sha256,
    packet->>'marketManifestSha256',packet->>'inferenceRequestSha256',
    packet->>'inferenceResponseSha256',source,target,'COMPLETE'
  );
  INSERT INTO public.p1_return_daily_signal_projection(
    batch_sha256,producer,symbol,signal,expected_return,payload_sha256
  )
  SELECT p_packet_sha256,signal->>'producer',signal->>'symbol',signal->>'signal',
    (signal->>'expectedReturn')::numeric,
    encode(public.digest(signal::text,'sha256'),'hex')
  FROM jsonb_array_elements(packet->'signals') signal;
  outcome:='IMPORTED';batch_sha256:=p_packet_sha256;RETURN NEXT;
END
$p1_commit_daily_signal_batch_v1$;

CREATE FUNCTION public.p1_read_automation_runtime_state_v4(
  p_run_id text,p_claim_token_hash text
) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_read_automation_runtime_state_v4$
DECLARE base jsonb;
DECLARE target date;
DECLARE signals_json jsonb;
BEGIN
  IF session_user<>'decision_automation_runtime' THEN
    RAISE EXCEPTION 'automation state v4 denied' USING ERRCODE='42501';
  END IF;
  base:=public.p1_read_automation_runtime_state_v3(p_run_id,p_claim_token_hash)::jsonb;
  target:=(base->>'sessionDate')::date;
  SELECT COALESCE(jsonb_agg(jsonb_build_object(
    'symbol',candidate.symbol,'lstmSignal',candidate.lstm_signal,
    'baselineSignal',candidate.baseline_signal,'expectedReturn',candidate.expected_return
  ) ORDER BY candidate.expected_return DESC,candidate.symbol),'[]'::jsonb)
  INTO signals_json
  FROM (
    SELECT signal.symbol,
      max(signal.signal) FILTER (WHERE signal.producer='LSTM') AS lstm_signal,
      max(signal.signal) FILTER (WHERE signal.producer='RULE_BASELINE') AS baseline_signal,
      max(signal.expected_return) FILTER (WHERE signal.producer='LSTM') AS expected_return
    FROM public.p1_return_daily_signal_batch batch
    JOIN public.p1_return_daily_signal_projection signal USING (batch_sha256)
    JOIN public.current_p1_return_model_pointer model USING (bundle_sha256)
    WHERE batch.target_session=target AND batch.status='COMPLETE'
    GROUP BY signal.symbol
    HAVING count(DISTINCT signal.producer)=2
  ) candidate;
  RETURN (base || jsonb_build_object(
    'releaseActive',jsonb_array_length(signals_json)=31,
    'signals',signals_json
  ))::text;
END
$p1_read_automation_runtime_state_v4$;

CREATE FUNCTION public.p1_read_return_signal_v3(p_symbol text)
RETURNS TABLE(
  producer text,source_workspace text,session_date date,as_of timestamptz,status text,
  reason text,signal text,predicted_return numeric,model_version text,model_report_id text,
  latest_completed_session date
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_read_return_signal_v3$
BEGIN
  IF session_user<>'decision_app' OR p_symbol!~'^[0-9A-Z._:-]{1,20}$' THEN
    RAISE EXCEPTION 'P1 return signal v3 read denied' USING ERRCODE='42501';
  END IF;
  RETURN QUERY
  WITH selected AS (
    SELECT batch.* FROM public.p1_return_daily_signal_batch batch
    JOIN public.current_p1_return_model_pointer model USING (bundle_sha256)
    WHERE batch.status='COMPLETE'
    ORDER BY batch.target_session DESC,batch.created_at DESC LIMIT 1
  )
  SELECT component.producer,'return-engine'::text,batch.target_session,batch.created_at,
    'AVAILABLE'::text,NULL::text,component.signal,component.expected_return,
    substr(batch.model_sha256,1,32),
    'mrp_p1_'||substr(batch.bundle_sha256,1,24),batch.target_session
  FROM selected batch
  JOIN public.p1_return_daily_signal_projection component USING (batch_sha256)
  WHERE component.symbol=p_symbol
  ORDER BY component.producer;
END
$p1_read_return_signal_v3$;

CREATE FUNCTION public.p1_record_automation_ai_judgement_v3(
  p_run_id text,p_claim_token_hash text,p_checkpoint_version integer,p_participation text,
  p_provider_id text,p_prompt_version text,p_baseline_symbol text,p_selected_symbol text,
  p_vetoed_symbol_count integer,p_judge_call_count integer,p_candidate_count integer,
  p_verdicts_json text,p_ai_settings_sha256 text,p_evidence_set_sha256 text,
  p_grounding_call_count integer,p_grounding_query_count integer,p_evidence_count integer
) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_record_automation_ai_judgement_v3$
  SELECT public.p1_record_automation_ai_judgement_v2(
    p_run_id,p_claim_token_hash,p_checkpoint_version,p_participation,p_provider_id,
    p_prompt_version,NULL,p_baseline_symbol,p_selected_symbol,p_vetoed_symbol_count,
    p_judge_call_count,p_candidate_count,NULL,NULL,p_verdicts_json,p_ai_settings_sha256,
    p_evidence_set_sha256,p_grounding_call_count,p_grounding_query_count,p_evidence_count
  )
$p1_record_automation_ai_judgement_v3$;

ALTER TABLE public.p1_return_model_seed_signal OWNER TO flyway;
ALTER TABLE public.p1_return_daily_signal_batch OWNER TO flyway;
ALTER TABLE public.p1_return_daily_signal_projection OWNER TO flyway;
ALTER VIEW public.current_p1_return_model_pointer OWNER TO flyway;
ALTER VIEW public.current_p1_return_signal_pointer OWNER TO flyway;
ALTER FUNCTION public.import_p1_return_bundle_v2(text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_read_daily_inference_context_v1(date) OWNER TO flyway;
ALTER FUNCTION public.p1_commit_daily_signal_batch_v1(text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_read_automation_runtime_state_v4(text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_read_return_signal_v3(text) OWNER TO flyway;
ALTER FUNCTION public.p1_record_automation_ai_judgement_v3(
  text,text,integer,text,text,text,text,text,integer,integer,integer,text,text,text,
  integer,integer,integer
) OWNER TO flyway;

REVOKE ALL ON TABLE public.p1_return_model_seed_signal,
  public.p1_return_daily_signal_batch,public.p1_return_daily_signal_projection,
  public.current_p1_return_model_pointer,public.current_p1_return_signal_pointer
  FROM PUBLIC,decision_app,decision_worker,decision_automation_runtime;
GRANT SELECT,INSERT ON TABLE public.p1_return_model_seed_signal,
  public.p1_return_daily_signal_batch,public.p1_return_daily_signal_projection TO flyway;
GRANT SELECT ON TABLE public.current_p1_return_model_pointer,
  public.current_p1_return_signal_pointer TO flyway;

REVOKE ALL ON FUNCTION public.import_p1_return_bundle_v2(text,text),
  public.p1_read_daily_inference_context_v1(date),
  public.p1_commit_daily_signal_batch_v1(text,text),
  public.p1_read_automation_runtime_state_v4(text,text),
  public.p1_read_return_signal_v3(text),
  public.p1_record_automation_ai_judgement_v3(
    text,text,integer,text,text,text,text,text,integer,integer,integer,text,text,text,
    integer,integer,integer
  ) FROM PUBLIC,decision_app,decision_worker,decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.import_p1_return_bundle_v2(text,text) TO decision_worker;
GRANT EXECUTE ON FUNCTION public.p1_read_daily_inference_context_v1(date),
  public.p1_commit_daily_signal_batch_v1(text,text),
  public.p1_read_automation_runtime_state_v4(text,text),
  public.p1_record_automation_ai_judgement_v3(
    text,text,integer,text,text,text,text,text,integer,integer,integer,text,text,text,
    integer,integer,integer
  ) TO decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.p1_read_return_signal_v3(text) TO decision_app;
REVOKE CREATE ON SCHEMA public FROM decision_worker,decision_automation_runtime;
