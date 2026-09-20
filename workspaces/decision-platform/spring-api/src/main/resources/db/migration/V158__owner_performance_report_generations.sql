-- source generation마다 재계산 백테스트, 고정 예측 실현, 실제 운용 손익을 분리해 append-only 보존한다.
CREATE TABLE public.owner_performance_report_generations (
  report_id text PRIMARY KEY,
  owner_user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  report_version integer NOT NULL,
  source_generation_sha256 text NOT NULL,
  source_start date NOT NULL,
  source_end date NOT NULL,
  model_sha256 text NOT NULL,
  principle_version_id text NOT NULL,
  principle_version integer NOT NULL,
  cost_bps integer NOT NULL,
  status text NOT NULL,
  report_json jsonb,
  report_sha256 text,
  failure_code text,
  supersedes_report_id text REFERENCES public.owner_performance_report_generations(report_id) ON DELETE RESTRICT,
  correction_of_report_id text REFERENCES public.owner_performance_report_generations(report_id) ON DELETE RESTRICT,
  generated_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
  CONSTRAINT owner_performance_report_id_check CHECK (report_id ~ '^perf_(report|fail)_[0-9a-f]{24}$'),
  CONSTRAINT owner_performance_report_version_check CHECK (report_version>=1),
  CONSTRAINT owner_performance_report_source_check CHECK (
    source_generation_sha256~'^[0-9a-f]{64}$' AND source_start=DATE '2026-08-18' AND source_end>=source_start
  ),
  CONSTRAINT owner_performance_report_model_check CHECK (model_sha256~'^[0-9a-f]{64}$'),
  CONSTRAINT owner_performance_report_principle_check CHECK (
    principle_version_id~'^pvr_[A-Za-z0-9_-]{8,96}$' AND principle_version>=1
  ),
  CONSTRAINT owner_performance_report_cost_check CHECK (cost_bps BETWEEN 0 AND 10000),
  CONSTRAINT owner_performance_report_status_check CHECK (
    (status='SUCCESS' AND report_json IS NOT NULL AND report_sha256~'^[0-9a-f]{64}$' AND failure_code IS NULL)
    OR (status='FAILED' AND report_json IS NULL AND report_sha256 IS NULL AND failure_code~'^[A-Z0-9_]{1,96}$')
  ),
  CONSTRAINT owner_performance_report_json_check CHECK (
    report_json IS NULL OR (jsonb_typeof(report_json)='object' AND octet_length(report_json::text)<=262144)
  ),
  CONSTRAINT owner_performance_report_version_unique UNIQUE(owner_user_id,report_version)
);
CREATE UNIQUE INDEX owner_performance_report_success_source_unique
  ON public.owner_performance_report_generations(owner_user_id,source_generation_sha256)
  WHERE status='SUCCESS';
ALTER TABLE public.owner_performance_report_generations OWNER TO flyway;

CREATE TRIGGER owner_performance_reports_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
  ON public.owner_performance_report_generations FOR EACH STATEMENT
  EXECUTE FUNCTION public.reject_world_news_v2_mutation();

CREATE FUNCTION public.read_owner_performance_report_inputs_v1(p_owner_user_id text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER STABLE SET search_path=pg_catalog AS $inputs$
DECLARE principle_row record;
DECLARE forecast_total integer:=0;
DECLARE forecast_realized integer:=0;
DECLARE sum_absolute_error numeric:=0;
DECLARE sum_squared_error numeric:=0;
DECLARE sum_error numeric:=0;
DECLARE closed_count integer:=0;
DECLARE realized_pnl bigint:=0;
DECLARE open_count integer:=0;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_worker' OR p_owner_user_id<>'usr_demo_user' THEN
    RAISE EXCEPTION 'owner performance input denied' USING ERRCODE='42501'; END IF;
  SELECT version.principle_version_id,version.version INTO principle_row
  FROM public.principles principle JOIN public.principle_versions version
    ON version.principle_id=principle.principle_id AND version.version=principle.current_version
  WHERE principle.user_id=p_owner_user_id AND principle.status='ACTIVE'
  ORDER BY principle.updated_at DESC,principle.principle_id LIMIT 1;
  IF NOT FOUND THEN RAISE EXCEPTION 'owner performance principle unavailable' USING ERRCODE='P0002'; END IF;

  WITH forecasts AS (
    SELECT batch.source_session,batch.target_session,projection.symbol,avg(projection.expected_return) predicted_return
    FROM public.p1_return_daily_signal_batch batch
    JOIN public.p1_return_daily_signal_projection projection USING(batch_sha256)
    WHERE batch.status='COMPLETE' AND batch.target_session>=DATE '2026-08-18'
    GROUP BY batch.batch_sha256,batch.source_session,batch.target_session,projection.symbol
    HAVING count(DISTINCT projection.producer)=2
  ), latest_bars AS (
    SELECT DISTINCT ON (bar.symbol,bar.session_date) bar.symbol,bar.session_date,bar.close_price
    FROM public.market_data_bars bar
    ORDER BY bar.symbol,bar.session_date,bar.generation DESC
  ), evaluated AS (
    SELECT forecast.predicted_return,
      CASE WHEN source.close_price>0 AND target.close_price>0
        THEN target.close_price::numeric/source.close_price::numeric-1 END actual_return
    FROM forecasts forecast
    LEFT JOIN latest_bars source ON source.symbol=forecast.symbol AND source.session_date=forecast.source_session
    LEFT JOIN latest_bars target ON target.symbol=forecast.symbol AND target.session_date=forecast.target_session
  )
  SELECT count(*),count(actual_return),COALESCE(sum(abs(predicted_return-actual_return)),0),
    COALESCE(sum(power(predicted_return-actual_return,2)),0),COALESCE(sum(predicted_return-actual_return),0)
  INTO forecast_total,forecast_realized,sum_absolute_error,sum_squared_error,sum_error FROM evaluated;

  SELECT count(*) FILTER(WHERE status='CLOSED' AND realized_pnl_krw IS NOT NULL),
    COALESCE(sum(realized_pnl_krw) FILTER(WHERE status='CLOSED' AND realized_pnl_krw IS NOT NULL),0),
    count(*) FILTER(WHERE status IN ('OPEN','EXIT_PENDING'))
  INTO closed_count,realized_pnl,open_count
  FROM public.automation_positions position
  WHERE position.user_id=p_owner_user_id
    AND NOT EXISTS(SELECT 1 FROM public.paper_accounts paper
      WHERE paper.user_id=position.user_id AND paper.account_id=position.account_id);

  RETURN jsonb_build_object(
    'contractId','owner-performance-report-input.v1',
    'principleVersionId',principle_row.principle_version_id,'principleVersion',principle_row.version,
    'fixedForecast',jsonb_build_object(
      'totalCount',forecast_total,'realizedCount',forecast_realized,'pendingCount',forecast_total-forecast_realized,
      'sumAbsoluteError',sum_absolute_error,'sumSquaredError',sum_squared_error,'sumError',sum_error
    ),
    'actualTrading',jsonb_build_object(
      'closedPositionCount',closed_count,'openPositionCount',open_count,'realizedPnlKrw',realized_pnl
    )
  );
END $inputs$;
ALTER FUNCTION public.read_owner_performance_report_inputs_v1(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.read_owner_performance_report_inputs_v1(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_owner_performance_report_inputs_v1(text) TO decision_worker;

CREATE FUNCTION public.publish_owner_performance_report_v1(
  p_owner_user_id text,p_report_id text,p_source_generation_sha256 text,p_source_start date,p_source_end date,
  p_model_sha256 text,p_principle_version_id text,p_principle_version integer,p_cost_bps integer,
  p_report_text text,p_report_sha256 text,p_generated_at timestamptz
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $publish$
DECLARE existing public.owner_performance_report_generations%ROWTYPE;
DECLARE prior public.owner_performance_report_generations%ROWTYPE;
DECLARE next_version integer;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_worker' OR p_owner_user_id<>'usr_demo_user'
     OR p_report_id!~'^perf_report_[0-9a-f]{24}$' OR p_source_generation_sha256!~'^[0-9a-f]{64}$'
     OR p_source_start<>DATE '2026-08-18' OR p_source_end<p_source_start OR p_model_sha256!~'^[0-9a-f]{64}$'
     OR p_principle_version_id!~'^pvr_[A-Za-z0-9_-]{8,96}$' OR p_principle_version<1
     OR p_cost_bps NOT BETWEEN 0 AND 10000 OR jsonb_typeof(p_report_text::jsonb)<>'object'
     OR octet_length(p_report_text)>262144 OR p_report_text::jsonb->>'contractId'<>'owner-performance-report.v1'
     OR NOT p_report_text::jsonb ?& ARRAY['contractId','sourceStart','sourceEnd','sourceGenerationSha256',
       'modelSha256','principleVersionId','principleVersion','costBps','sections']
     OR p_report_sha256!~'^[0-9a-f]{64}$'
     OR p_report_sha256<>encode(public.digest(convert_to(p_report_text,'UTF8'),'sha256'),'hex') THEN
    RAISE EXCEPTION 'owner performance report invalid' USING ERRCODE='22023'; END IF;
  SELECT * INTO existing FROM public.owner_performance_report_generations report
  WHERE report.owner_user_id=p_owner_user_id AND report.source_generation_sha256=p_source_generation_sha256
    AND report.status='SUCCESS';
  IF FOUND THEN
    IF existing.report_id=p_report_id AND existing.report_sha256=p_report_sha256 THEN RETURN 'NO_OP'; END IF;
    RAISE EXCEPTION 'owner performance source identity conflict' USING ERRCODE='23505'; END IF;
  SELECT * INTO prior FROM public.owner_performance_report_generations report
  WHERE report.owner_user_id=p_owner_user_id AND report.status='SUCCESS'
  ORDER BY report.report_version DESC LIMIT 1;
  SELECT COALESCE(max(report_version),0)+1 INTO next_version FROM public.owner_performance_report_generations
  WHERE owner_user_id=p_owner_user_id;
  INSERT INTO public.owner_performance_report_generations(
    report_id,owner_user_id,report_version,source_generation_sha256,source_start,source_end,model_sha256,
    principle_version_id,principle_version,cost_bps,status,report_json,report_sha256,failure_code,
    supersedes_report_id,correction_of_report_id,generated_at
  ) VALUES (
    p_report_id,p_owner_user_id,next_version,p_source_generation_sha256,p_source_start,p_source_end,p_model_sha256,
    p_principle_version_id,p_principle_version,p_cost_bps,'SUCCESS',p_report_text::jsonb,p_report_sha256,NULL,
    prior.report_id,CASE WHEN prior.report_id IS NOT NULL AND prior.source_end=p_source_end THEN prior.report_id END,p_generated_at
  );
  RETURN 'INSERTED';
END $publish$;
ALTER FUNCTION public.publish_owner_performance_report_v1(text,text,text,date,date,text,text,integer,integer,text,text,timestamptz) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.publish_owner_performance_report_v1(text,text,text,date,date,text,text,integer,integer,text,text,timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.publish_owner_performance_report_v1(text,text,text,date,date,text,text,integer,integer,text,text,timestamptz) TO decision_worker;

CREATE FUNCTION public.record_owner_performance_report_failure_v1(
  p_owner_user_id text,p_report_id text,p_source_generation_sha256 text,p_source_start date,p_source_end date,
  p_model_sha256 text,p_principle_version_id text,p_principle_version integer,p_cost_bps integer,
  p_failure_code text,p_generated_at timestamptz
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $failure$
DECLARE inserted integer;
DECLARE next_version integer;
DECLARE prior_id text;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_worker' OR p_owner_user_id<>'usr_demo_user'
     OR p_report_id!~'^perf_fail_[0-9a-f]{24}$' OR p_source_generation_sha256!~'^[0-9a-f]{64}$'
     OR p_source_start<>DATE '2026-08-18' OR p_source_end<p_source_start OR p_model_sha256!~'^[0-9a-f]{64}$'
     OR p_principle_version_id!~'^pvr_[A-Za-z0-9_-]{8,96}$' OR p_principle_version<1
     OR p_cost_bps NOT BETWEEN 0 AND 10000 OR p_failure_code!~'^[A-Z0-9_]{1,96}$' THEN
    RAISE EXCEPTION 'owner performance failure invalid' USING ERRCODE='22023'; END IF;
  IF EXISTS(SELECT 1 FROM public.owner_performance_report_generations WHERE report_id=p_report_id) THEN RETURN 'NO_OP'; END IF;
  SELECT COALESCE(max(report_version),0)+1 INTO next_version FROM public.owner_performance_report_generations
  WHERE owner_user_id=p_owner_user_id;
  SELECT report_id INTO prior_id FROM public.owner_performance_report_generations
  WHERE owner_user_id=p_owner_user_id AND status='SUCCESS' ORDER BY report_version DESC LIMIT 1;
  INSERT INTO public.owner_performance_report_generations(
    report_id,owner_user_id,report_version,source_generation_sha256,source_start,source_end,model_sha256,
    principle_version_id,principle_version,cost_bps,status,report_json,report_sha256,failure_code,
    supersedes_report_id,correction_of_report_id,generated_at
  ) VALUES (
    p_report_id,p_owner_user_id,next_version,p_source_generation_sha256,p_source_start,p_source_end,p_model_sha256,
    p_principle_version_id,p_principle_version,p_cost_bps,'FAILED',NULL,NULL,p_failure_code,prior_id,NULL,p_generated_at
  );
  GET DIAGNOSTICS inserted=ROW_COUNT;
  RETURN CASE WHEN inserted=1 THEN 'INSERTED' ELSE 'NO_OP' END;
END $failure$;
ALTER FUNCTION public.record_owner_performance_report_failure_v1(text,text,text,date,date,text,text,integer,integer,text,timestamptz) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.record_owner_performance_report_failure_v1(text,text,text,date,date,text,text,integer,integer,text,timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.record_owner_performance_report_failure_v1(text,text,text,date,date,text,text,integer,integer,text,timestamptz) TO decision_worker;

CREATE FUNCTION public.read_latest_owner_performance_report_authorized_v1(
  p_capability text,p_actor_user_id text,p_security_version bigint
) RETURNS TABLE(report_json jsonb,report_version integer,last_failure_code text,last_failure_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $read$
DECLARE actor_role text;
DECLARE success_row public.owner_performance_report_generations%ROWTYPE;
BEGIN
  SELECT actor.role INTO actor_role FROM public.users actor
  WHERE actor.user_id=p_actor_user_id AND actor.status='ACTIVE' AND actor.security_version=p_security_version;
  IF NOT FOUND OR NOT public.consume_actor_request_capability_v2(
    p_capability,p_actor_user_id,p_security_version,actor_role,
    'READ_DASHBOARD_ARTIFACT','DASHBOARD_ARTIFACT','performance-latest',
    public.actor_capability_payload_hash('PERFORMANCE_REPORT','latest')
  ) THEN RETURN; END IF;
  SELECT * INTO success_row FROM public.owner_performance_report_generations report
  WHERE report.owner_user_id=p_actor_user_id AND report.status='SUCCESS'
  ORDER BY report.report_version DESC LIMIT 1;
  IF NOT FOUND THEN RETURN; END IF;
  RETURN QUERY SELECT success_row.report_json||jsonb_build_object(
      'generatedAt',success_row.generated_at,'reportId',success_row.report_id,
      'reportVersion',success_row.report_version,'supersedesReportId',success_row.supersedes_report_id,
      'correctionOfReportId',success_row.correction_of_report_id
    ),success_row.report_version,
    failure.failure_code,failure.generated_at
  FROM (SELECT 1) one LEFT JOIN LATERAL (
    SELECT report.failure_code,report.generated_at FROM public.owner_performance_report_generations report
    WHERE report.owner_user_id=p_actor_user_id AND report.status='FAILED'
      AND report.report_version>success_row.report_version ORDER BY report.report_version DESC LIMIT 1
  ) failure ON true;
END $read$;
ALTER FUNCTION public.read_latest_owner_performance_report_authorized_v1(text,text,bigint) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.read_latest_owner_performance_report_authorized_v1(text,text,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_latest_owner_performance_report_authorized_v1(text,text,bigint) TO decision_app;

REVOKE ALL ON TABLE public.owner_performance_report_generations FROM PUBLIC,decision_worker,decision_app;
