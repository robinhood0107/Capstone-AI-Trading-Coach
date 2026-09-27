-- FULL 에서는 백테스트·모델 평가·성과 리포트를 무장한 사용자마다 만든다.
--
-- V124·V154·V158 의 materializer 함수는 owner 를 'usr_demo_user' 하나로 못박았다. FULL 의
-- DashboardController 는 호출자 본인의 행만 읽으므로, 다른 사용자는 리포트가 영원히 비어 있었다.
-- 여기서는 그 리터럴만 "활성 사용자" 조건으로 바꾼다. session_user(decision_worker)와
-- current_user(flyway) 경계, 입력 검증, 행 모양은 그대로다. LOCAL 은 여전히 demo-user 하나만
-- 넘기므로 동작이 같다.
--
-- 바꾸지 않는 것: 새 표, 새 컬럼, 새 권한은 없다. CREATE OR REPLACE 는 소유자와 GRANT 를 유지한다.

SET LOCAL row_security = on;
CREATE OR REPLACE FUNCTION public.read_owner_scenario_materialization_inputs_v1(p_owner_user_id text)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $read_owner_scenario_materialization_inputs_v1$
DECLARE
  pointer_row public.current_p1_return_model_pointer%ROWTYPE;
  rules jsonb;
  sessions date[];
  session_count integer;
  evaluation_start date := DATE '2026-08-18';
  evaluation_end date;
  evaluation_count integer;
  bars jsonb;
  symbol_count integer;
  bar_count integer;
BEGIN
  IF session_user <> 'decision_worker' OR p_owner_user_id !~ '^usr_[A-Za-z0-9_-]{8,96}$'
     OR NOT EXISTS (
       SELECT 1 FROM public.users owner_row
       WHERE owner_row.user_id=p_owner_user_id AND owner_row.status='ACTIVE'
     ) THEN
    RAISE EXCEPTION 'owner scenario input denied' USING ERRCODE='42501';
  END IF;
  SELECT * INTO pointer_row FROM public.current_p1_return_model_pointer LIMIT 1;
  IF NOT FOUND THEN RAISE EXCEPTION 'owner scenario model pointer unavailable' USING ERRCODE='P0002'; END IF;

  SELECT version.rules_json INTO rules
  FROM public.principles principle
  JOIN public.principle_versions version
    ON version.principle_id=principle.principle_id AND version.version=principle.current_version
  WHERE principle.user_id=p_owner_user_id AND principle.status='ACTIVE'
  ORDER BY principle.updated_at DESC,principle.principle_id
  LIMIT 1;
  IF rules IS NULL THEN RAISE EXCEPTION 'owner scenario principle unavailable' USING ERRCODE='P0002'; END IF;

  SELECT array_agg(complete.session_date ORDER BY complete.session_date) INTO sessions
  FROM (
    SELECT bar.session_date
    FROM public.market_data_bars bar
    GROUP BY bar.session_date
    HAVING count(DISTINCT bar.symbol)=31
  ) complete;
  session_count:=coalesce(array_length(sessions,1),0);
  IF session_count < 104 THEN
    RAISE EXCEPTION 'owner scenario requires at least 104 context sessions' USING ERRCODE='22023';
  END IF;
  evaluation_end:=sessions[session_count];
  SELECT count(*) INTO evaluation_count
  FROM unnest(sessions) AS session_date
  WHERE session_date BETWEEN evaluation_start AND evaluation_end;
  IF evaluation_count < 13 THEN
    RAISE EXCEPTION 'owner scenario requires at least 13 evaluation sessions' USING ERRCODE='22023';
  END IF;

  WITH latest AS (
    SELECT DISTINCT ON (symbol,session_date)
      symbol,session_date,open_price,high_price,low_price,close_price,volume,
      manifest_sha256,source_receipt_sha256
    FROM public.market_data_bars
    WHERE session_date=ANY(sessions)
    ORDER BY symbol,session_date,generation DESC
  )
  SELECT count(DISTINCT symbol),count(*),jsonb_agg(
    jsonb_build_object(
      'symbol',symbol,'sessionDate',session_date,'open',open_price,'high',high_price,
      'low',low_price,'close',close_price,'volume',volume,
      'manifestSha256',manifest_sha256,'sourceReceiptSha256',source_receipt_sha256
    ) ORDER BY session_date,symbol
  ) INTO symbol_count,bar_count,bars FROM latest;
  IF symbol_count <> 31 OR bar_count <> 31*session_count THEN
    RAISE EXCEPTION 'owner scenario requires exact-31 by every context session' USING ERRCODE='22023';
  END IF;

  RETURN jsonb_build_object(
    'contractId','owner-scenario-materialization-input.v1',
    'ownerUserId',p_owner_user_id,
    'bundleSha256',pointer_row.bundle_sha256,
    'artifactId',pointer_row.artifact_id,
    'sourceRunId',pointer_row.run_id,
    'modelSha256',pointer_row.model_sha256,
    'modelQuality',pointer_row.model_quality,
    'evaluationStart',to_char(evaluation_start,'YYYY-MM-DD'),
    'evaluationEnd',to_char(evaluation_end,'YYYY-MM-DD'),
    'sessions',to_jsonb(sessions),
    'rules',rules,
    'bars',bars
  );
END
$read_owner_scenario_materialization_inputs_v1$;

CREATE OR REPLACE FUNCTION public.publish_owner_scenario_dashboard_v1(
  p_owner_user_id text,p_source_bundle_sha256 text,p_artifact_id text,p_run_id text,
  p_model_projection_text text,p_model_projection_hash text,
  p_backtest_projection_text text,p_backtest_projection_hash text,
  p_as_of timestamptz,p_fresh_until timestamptz
)
RETURNS text
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $publish_owner_scenario_dashboard_v1$
DECLARE existing_count integer;
BEGIN
  IF session_user <> 'decision_worker' OR p_owner_user_id !~ '^usr_[A-Za-z0-9_-]{8,96}$'
     OR NOT EXISTS (
       SELECT 1 FROM public.users owner_row
       WHERE owner_row.user_id=p_owner_user_id AND owner_row.status='ACTIVE'
     ) THEN
    RAISE EXCEPTION 'owner scenario publish denied' USING ERRCODE='42501';
  END IF;
  IF p_artifact_id !~ '^artifact_owner_[0-9a-f]{24}$'
     OR p_run_id !~ '^run_owner_[0-9a-f]{24}$'
     OR p_source_bundle_sha256 !~ '^[0-9a-f]{64}$'
     OR p_model_projection_hash !~ '^sha256:[0-9a-f]{64}$'
     OR p_backtest_projection_hash !~ '^sha256:[0-9a-f]{64}$'
     OR octet_length(p_model_projection_text) NOT BETWEEN 2 AND 524288
     OR octet_length(p_backtest_projection_text) NOT BETWEEN 2 AND 524288
     OR jsonb_typeof(p_model_projection_text::jsonb) <> 'object'
     OR jsonb_typeof(p_backtest_projection_text::jsonb) <> 'object'
     OR p_model_projection_hash <> 'sha256:' || encode(public.digest(p_model_projection_text,'sha256'),'hex')
     OR p_backtest_projection_hash <> 'sha256:' || encode(public.digest(p_backtest_projection_text,'sha256'),'hex')
     OR p_fresh_until < p_as_of THEN
    RAISE EXCEPTION 'owner scenario projection invalid' USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.current_p1_return_model_pointer pointer
    WHERE pointer.bundle_sha256=p_source_bundle_sha256
  ) THEN RAISE EXCEPTION 'owner scenario source pointer changed' USING ERRCODE='40001'; END IF;

  -- run_id 는 전역 PK 다. 다른 owner 의 같은 run_id 는 재생이 아니라 충돌이다.
  SELECT count(*) INTO existing_count FROM public.dashboard_artifact_views item
  WHERE item.run_id=p_run_id AND item.view_kind IN ('MODEL_EVALUATION','BACKTEST');
  IF existing_count=2 THEN
    IF EXISTS (
      SELECT 1 FROM public.dashboard_artifact_views item
      WHERE item.run_id=p_run_id AND item.view_kind='MODEL_EVALUATION'
        AND item.owner_user_id=p_owner_user_id AND item.projection_hash=p_model_projection_hash
    ) AND EXISTS (
      SELECT 1 FROM public.dashboard_artifact_views item
      WHERE item.run_id=p_run_id AND item.view_kind='BACKTEST'
        AND item.owner_user_id=p_owner_user_id AND item.projection_hash=p_backtest_projection_hash
    ) THEN RETURN 'REPLAYED'; END IF;
    RAISE EXCEPTION 'owner scenario identity conflict' USING ERRCODE='23505';
  ELSIF existing_count<>0 THEN
    RAISE EXCEPTION 'owner scenario partial projection' USING ERRCODE='23505';
  END IF;

  INSERT INTO public.dashboard_artifact_views(
    artifact_id,view_kind,owner_user_id,run_id,fixture_class,evidence_mode,
    projection_json,projection_hash,as_of,fresh_until
  ) VALUES
    (p_artifact_id,'MODEL_EVALUATION',p_owner_user_id,p_run_id,'REAL_ARTIFACT','REAL_ARTIFACT',
      p_model_projection_text::jsonb,p_model_projection_hash,p_as_of,p_fresh_until),
    (p_artifact_id,'BACKTEST',p_owner_user_id,p_run_id,'REAL_ARTIFACT','REAL_ARTIFACT',
      p_backtest_projection_text::jsonb,p_backtest_projection_hash,p_as_of,p_fresh_until);
  RETURN 'INSERTED';
END
$publish_owner_scenario_dashboard_v1$;

CREATE OR REPLACE FUNCTION public.read_owner_performance_report_inputs_v1(p_owner_user_id text)
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
  IF current_user<>'flyway' OR session_user<>'decision_worker' OR p_owner_user_id!~'^usr_[A-Za-z0-9_-]{8,96}$'
     OR NOT EXISTS(SELECT 1 FROM public.users owner_row
       WHERE owner_row.user_id=p_owner_user_id AND owner_row.status='ACTIVE') THEN
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

CREATE OR REPLACE FUNCTION public.publish_owner_performance_report_v1(
  p_owner_user_id text,p_report_id text,p_source_generation_sha256 text,p_source_start date,p_source_end date,
  p_model_sha256 text,p_principle_version_id text,p_principle_version integer,p_cost_bps integer,
  p_report_text text,p_report_sha256 text,p_generated_at timestamptz
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $publish$
DECLARE existing public.owner_performance_report_generations%ROWTYPE;
DECLARE prior public.owner_performance_report_generations%ROWTYPE;
DECLARE next_version integer;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_worker' OR p_owner_user_id!~'^usr_[A-Za-z0-9_-]{8,96}$'
     OR NOT EXISTS(SELECT 1 FROM public.users owner_row
       WHERE owner_row.user_id=p_owner_user_id AND owner_row.status='ACTIVE')
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

CREATE OR REPLACE FUNCTION public.record_owner_performance_report_failure_v1(
  p_owner_user_id text,p_report_id text,p_source_generation_sha256 text,p_source_start date,p_source_end date,
  p_model_sha256 text,p_principle_version_id text,p_principle_version integer,p_cost_bps integer,
  p_failure_code text,p_generated_at timestamptz
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $failure$
DECLARE inserted integer;
DECLARE next_version integer;
DECLARE prior_id text;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_worker' OR p_owner_user_id!~'^usr_[A-Za-z0-9_-]{8,96}$'
     OR NOT EXISTS(SELECT 1 FROM public.users owner_row
       WHERE owner_row.user_id=p_owner_user_id AND owner_row.status='ACTIVE')
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
