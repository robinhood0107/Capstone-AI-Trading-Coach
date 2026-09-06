-- baseline 복원 후 남은 row_security=off를 상속하지 않는다.
SET LOCAL row_security = on;
-- 기존 봉인 seed와 일일 v1 bytes를 보존하고 Ridge 모델을 동반 artifact로 저장한다.
CREATE TABLE public.p1_ridge_daily_forecasts (
 batch_sha256 text NOT NULL REFERENCES public.p1_return_daily_signal_batch(batch_sha256),
 symbol text NOT NULL CHECK(symbol~'^[0-9]{6}$'),
 model_sha256 text NOT NULL CHECK(model_sha256~'^[0-9a-f]{64}$'),
 model_json jsonb NOT NULL, forecasts jsonb NOT NULL,
 PRIMARY KEY(batch_sha256,symbol)
);
ALTER TABLE public.p1_ridge_daily_forecasts OWNER TO flyway;
REVOKE ALL ON public.p1_ridge_daily_forecasts FROM PUBLIC,decision_app,decision_automation_runtime;

CREATE FUNCTION public.p1_commit_daily_signal_batch_v2(p_packet_text text,p_packet_sha256 text)
RETURNS TABLE(outcome text,batch_sha256 text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE packet jsonb; legacy jsonb; item jsonb; forecast jsonb; model jsonb; result record;
DECLARE legacy_text text; legacy_sha text; source date; target date;
BEGIN
 IF session_user<>'decision_automation_runtime' OR p_packet_text IS NULL
  OR p_packet_sha256 IS NULL OR p_packet_sha256!~'^[0-9a-f]{64}$'
  OR octet_length(p_packet_text) NOT BETWEEN 2 AND 524288
  OR encode(public.digest(p_packet_text,'sha256'),'hex')<>p_packet_sha256 THEN
  RAISE EXCEPTION 'ridge batch denied' USING ERRCODE='42501'; END IF;
 -- 크기 상한 뒤 C JSON parser와 아래 exact shape 검증을 쓴다.
 -- 문자열 전체를 PL/pgSQL substr로 스캔하면 모델 JSON에서 제곱 비용이 발생한다.
 packet:=p_packet_text::jsonb;
 IF packet->>'contractId'<>'p1-return-daily-signal-batch.v2'
  OR (SELECT count(*) FROM jsonb_object_keys(packet))<>3
  OR NOT packet ?& ARRAY['contractId','legacyPacket','ridge']
  OR jsonb_typeof(packet->'legacyPacket')<>'string'
  OR jsonb_typeof(packet->'ridge')<>'array' OR jsonb_array_length(packet->'ridge')<>31 THEN
  RAISE EXCEPTION 'ridge batch shape invalid' USING ERRCODE='22023'; END IF;
 legacy_text:=packet->>'legacyPacket'; legacy:=legacy_text::jsonb;
 legacy_sha:=encode(public.digest(legacy_text,'sha256'),'hex');
 source:=(legacy->>'sourceSession')::date; target:=(legacy->>'targetSession')::date;
 IF (SELECT count(DISTINCT r->>'symbol') FROM jsonb_array_elements(packet->'ridge') r)<>31
 OR EXISTS(SELECT r->>'symbol' FROM jsonb_array_elements(packet->'ridge') r
    EXCEPT SELECT s->>'symbol' FROM jsonb_array_elements(legacy->'signals') s) THEN
  RAISE EXCEPTION 'ridge universe invalid' USING ERRCODE='22023'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(packet->'ridge') LOOP
  IF jsonb_typeof(item)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(item))<>4
    OR NOT item ?& ARRAY['symbol','modelJson','modelSha256','forecasts']
    OR encode(public.digest(item->>'modelJson','sha256'),'hex')<>item->>'modelSha256' THEN
   RAISE EXCEPTION 'ridge model hash invalid' USING ERRCODE='22023'; END IF;
  model:=(item->>'modelJson')::jsonb;
  IF jsonb_typeof(model)<>'object' OR (SELECT count(*) FROM jsonb_object_keys(model))<>10
    OR NOT model ?& ARRAY['contractId','estimator','alpha','symbol','featureOrder','sourceSession','firstSession','inputSha256','qualityStatus','models']
    OR model->>'estimator'<>'STANDARD_SCALER_RIDGE'
    OR model->>'inputSha256'!~'^[0-9a-f]{64}$'
    OR model->'featureOrder'<>'["open","high","low","raw_close","volume","return_1d","ma5","ma20","rsi14"]'::jsonb
    OR model->>'contractId'<>'p1-ridge-return-model.v1' OR model->>'symbol'<>item->>'symbol'
    OR (model->>'sourceSession')::date<>source OR model->>'qualityStatus'<>'COMPARISON_PENDING'
    OR (model->>'alpha')::numeric<>1 OR jsonb_array_length(model->'models')<>3
    OR jsonb_array_length(item->'forecasts')<>3
    OR (SELECT array_agg((f->>'horizonSessions')::integer ORDER BY (f->>'horizonSessions')::integer)
        FROM jsonb_array_elements(item->'forecasts') f)<>ARRAY[1,5,20] THEN
   RAISE EXCEPTION 'ridge model binding invalid' USING ERRCODE='22023'; END IF;
  FOR forecast IN SELECT value FROM jsonb_array_elements(item->'forecasts') LOOP
   IF NOT forecast ?& ARRAY['horizonSessions','targetSession','expectedReturn','forecastClose','trainSamples','trainedThrough']
     OR jsonb_typeof(forecast->'expectedReturn')<>'number'
     OR jsonb_typeof(forecast->'forecastClose')<>'number'
     OR (forecast->>'trainedThrough')::date<>source OR (forecast->>'targetSession')::date<=source
     OR (forecast->>'expectedReturn')::numeric<=-1 OR (forecast->>'expectedReturn')::numeric>1000
     OR (forecast->>'forecastClose')::numeric<=0 OR (forecast->>'trainSamples')::integer<40 THEN
    RAISE EXCEPTION 'ridge forecast invalid' USING ERRCODE='22023'; END IF;
   IF (forecast->>'horizonSessions')::integer=1 AND (
    (forecast->>'targetSession')::date<>target OR NOT EXISTS(
     SELECT 1 FROM jsonb_array_elements(legacy->'signals') s WHERE s->>'producer'='RULE_BASELINE'
      AND s->>'symbol'=item->>'symbol' AND (s->>'expectedReturn')::numeric=(forecast->>'expectedReturn')::numeric
    )) THEN RAISE EXCEPTION 'ridge signal mismatch' USING ERRCODE='22023'; END IF;
  END LOOP;
 END LOOP;
 -- 같은 모델·거래일의 동시 생성을 직렬화해 재시도를 동일 게시로 처리한다.
 PERFORM pg_advisory_xact_lock(hashtextextended('daily-ridge:'||(legacy->>'bundleSha256')||':'||target::text,134));
 SELECT * INTO result FROM public.p1_commit_daily_signal_batch_v1(legacy_text,legacy_sha);
 IF result.outcome='REPLAYED' AND EXISTS(SELECT 1 FROM public.p1_ridge_daily_forecasts WHERE p1_ridge_daily_forecasts.batch_sha256=legacy_sha) THEN
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(packet->'ridge') r JOIN public.p1_ridge_daily_forecasts saved
   ON saved.batch_sha256=legacy_sha AND saved.symbol=r->>'symbol'
   WHERE saved.model_sha256<>r->>'modelSha256' OR saved.forecasts<>r->'forecasts') THEN
   RAISE EXCEPTION 'ridge replay conflict' USING ERRCODE='23505'; END IF;
 ELSE
  INSERT INTO public.p1_ridge_daily_forecasts
   SELECT legacy_sha,r->>'symbol',r->>'modelSha256',(r->>'modelJson')::jsonb,r->'forecasts'
   FROM jsonb_array_elements(packet->'ridge') r;
 END IF;
 outcome:=result.outcome;batch_sha256:=legacy_sha;RETURN NEXT;
END $f$;
ALTER FUNCTION public.p1_commit_daily_signal_batch_v2(text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_commit_daily_signal_batch_v2(text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_commit_daily_signal_batch_v2(text,text) TO decision_automation_runtime;

CREATE FUNCTION public.p1_read_ridge_forecasts_v1(p_symbol text) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE result jsonb;
BEGIN
 IF session_user<>'decision_app' OR p_symbol!~'^[0-9]{6}$' THEN RAISE EXCEPTION 'ridge read denied' USING ERRCODE='42501'; END IF;
 SELECT jsonb_build_object('sourceSession',b.source_session,'modelVersion',r.model_sha256,
   'estimator','RIDGE','qualityStatus','COMPARISON_PENDING','forecasts',r.forecasts)
 INTO result FROM public.p1_return_daily_signal_batch b JOIN public.current_p1_return_model_pointer current USING(bundle_sha256)
 JOIN public.p1_ridge_daily_forecasts r USING(batch_sha256)
 WHERE r.symbol=p_symbol AND b.status='COMPLETE' ORDER BY b.target_session DESC,b.created_at DESC LIMIT 1;
 RETURN result;
END $f$;
ALTER FUNCTION public.p1_read_ridge_forecasts_v1(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_read_ridge_forecasts_v1(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_read_ridge_forecasts_v1(text) TO decision_app;

CREATE OR REPLACE FUNCTION public.p1_read_automation_runtime_state_v4(p_run_id text, p_claim_token_hash text)
 RETURNS text
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
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
    'baselineSignal',candidate.baseline_signal,'expectedReturn',candidate.expected_return,
    'lstmExpectedReturn',candidate.lstm_return,'ridgeExpectedReturn',candidate.ridge_return,
    'forecastClose',candidate.forecast_close,'ridgeModelSha256',candidate.ridge_model_sha,
    'combinationMethod','EQUAL_WEIGHT_50_50'
  ) ORDER BY candidate.expected_return DESC,candidate.symbol),'[]'::jsonb)
  INTO signals_json
  FROM (
    SELECT signal.symbol,
      max(signal.signal) FILTER (WHERE signal.producer='LSTM') AS lstm_signal,
      max(signal.signal) FILTER (WHERE signal.producer='RULE_BASELINE') AS baseline_signal,
      avg(signal.expected_return) AS expected_return,
      max(signal.expected_return) FILTER(WHERE signal.producer='LSTM') AS lstm_return,
      max(signal.expected_return) FILTER(WHERE signal.producer='RULE_BASELINE') AS ridge_return,
      max(ridge.model_sha256) AS ridge_model_sha,
      max((ridge.forecasts->0->>'forecastClose')::numeric / (1+(ridge.forecasts->0->>'expectedReturn')::numeric)) * (1+avg(signal.expected_return)) AS forecast_close
    FROM public.p1_return_daily_signal_batch batch
    JOIN public.p1_return_daily_signal_projection signal USING (batch_sha256)
    JOIN public.p1_ridge_daily_forecasts ridge ON ridge.batch_sha256=batch.batch_sha256 AND ridge.symbol=signal.symbol
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
$function$

;
CREATE OR REPLACE FUNCTION public.p1_read_return_signal_v3(p_symbol text)
 RETURNS TABLE(producer text, source_workspace text, session_date date, as_of timestamp with time zone, status text, reason text, signal text, predicted_return numeric, model_version text, model_report_id text, latest_completed_session date)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
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
    'mrp_p1_'||substr(batch.bundle_sha256,1,24),
    (SELECT min(calendar.session_date) FROM public.trading_sessions calendar WHERE calendar.exchange_mic='XKRX' AND calendar.is_open
      AND calendar.close_at>statement_timestamp())
  FROM selected batch
  JOIN public.p1_return_daily_signal_projection component USING (batch_sha256)
  JOIN public.p1_ridge_daily_forecasts ridge ON ridge.batch_sha256=batch.batch_sha256 AND ridge.symbol=component.symbol
  WHERE component.symbol=p_symbol
  ORDER BY component.producer;
END
$function$

;
