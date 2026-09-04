CREATE FUNCTION public.read_owner_scenario_materialization_inputs_v1(p_owner_user_id text)
RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $read_owner_scenario_materialization_inputs_v1$
DECLARE
  pointer_row public.current_p1_return_model_pointer%ROWTYPE;
  rules jsonb;
  sessions date[];
  bars jsonb;
  symbol_count integer;
  bar_count integer;
BEGIN
  IF session_user <> 'decision_worker' OR p_owner_user_id <> 'usr_demo_user' THEN
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

  SELECT array_agg(session_date ORDER BY session_date) INTO sessions
  FROM (
    SELECT DISTINCT session_date FROM public.market_data_bars
    ORDER BY session_date DESC LIMIT 104
  ) recent;
  IF coalesce(array_length(sessions,1),0) <> 104 THEN
    RAISE EXCEPTION 'owner scenario requires 104 sessions' USING ERRCODE='22023';
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
  IF symbol_count <> 31 OR bar_count <> 3224 THEN
    RAISE EXCEPTION 'owner scenario requires exact-31 by 104 bars' USING ERRCODE='22023';
  END IF;

  RETURN jsonb_build_object(
    'contractId','owner-scenario-materialization-input.v1',
    'ownerUserId',p_owner_user_id,
    'bundleSha256',pointer_row.bundle_sha256,
    'artifactId',pointer_row.artifact_id,
    'sourceRunId',pointer_row.run_id,
    'modelSha256',pointer_row.model_sha256,
    'modelQuality',pointer_row.model_quality,
    'sessions',to_jsonb(sessions),
    'rules',rules,
    'bars',bars
  );
END
$read_owner_scenario_materialization_inputs_v1$;

CREATE FUNCTION public.publish_owner_scenario_dashboard_v1(
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
  IF session_user <> 'decision_worker' OR p_owner_user_id <> 'usr_demo_user' THEN
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

  SELECT count(*) INTO existing_count FROM public.dashboard_artifact_views item
  WHERE item.run_id=p_run_id AND item.view_kind IN ('MODEL_EVALUATION','BACKTEST');
  IF existing_count=2 THEN
    IF EXISTS (
      SELECT 1 FROM public.dashboard_artifact_views item
      WHERE item.run_id=p_run_id AND item.view_kind='MODEL_EVALUATION'
        AND item.projection_hash=p_model_projection_hash
    ) AND EXISTS (
      SELECT 1 FROM public.dashboard_artifact_views item
      WHERE item.run_id=p_run_id AND item.view_kind='BACKTEST'
        AND item.projection_hash=p_backtest_projection_hash
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

ALTER FUNCTION public.read_owner_scenario_materialization_inputs_v1(text) OWNER TO flyway;
ALTER FUNCTION public.publish_owner_scenario_dashboard_v1(
  text,text,text,text,text,text,text,text,timestamptz,timestamptz
) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.read_owner_scenario_materialization_inputs_v1(text) FROM PUBLIC,decision_worker;
REVOKE ALL ON FUNCTION public.publish_owner_scenario_dashboard_v1(
  text,text,text,text,text,text,text,text,timestamptz,timestamptz
) FROM PUBLIC,decision_worker;
GRANT EXECUTE ON FUNCTION public.read_owner_scenario_materialization_inputs_v1(text) TO decision_worker;
GRANT EXECUTE ON FUNCTION public.publish_owner_scenario_dashboard_v1(
  text,text,text,text,text,text,text,text,timestamptz,timestamptz
) TO decision_worker;

ALTER TABLE public.automation_positions NO FORCE ROW LEVEL SECURITY;
UPDATE public.automation_positions
SET account_id='acct_dddddddddddddddddddddddddddddddd'
WHERE user_id='usr_demo_user' AND position_id LIKE 'auto_pos_replay%';
ALTER TABLE public.automation_positions FORCE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.p1_automation_realized_performance_v2(p_user_id text)
RETURNS TABLE(
  closed_position_count bigint,realized_pnl_krw bigint,realized_gross_krw bigint,
  winning_position_count bigint,losing_position_count bigint
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_automation_realized_performance_v2_v124$
BEGIN
  IF session_user<>'decision_app'
     OR pg_catalog.current_setting('app.actor_user_id',true)<>p_user_id
     OR NOT public.actor_rls_scope_is_open_v1() THEN
    RAISE EXCEPTION 'automation realized performance scope denied' USING ERRCODE='42501';
  END IF;
  SELECT count(*),COALESCE(sum(item.realized_pnl_krw),0),COALESCE(sum(
      (item.exit_average_fill_price_krw-item.entry_average_fill_price_krw)*item.exit_filled_quantity
    ),0),count(*) FILTER (WHERE item.realized_pnl_krw>0),
    count(*) FILTER (WHERE item.realized_pnl_krw<0)
  INTO closed_position_count,realized_pnl_krw,realized_gross_krw,
       winning_position_count,losing_position_count
  FROM public.automation_positions item
  WHERE item.user_id=p_user_id AND item.status='CLOSED' AND item.realized_pnl_krw IS NOT NULL
    AND NOT EXISTS (
      SELECT 1 FROM public.paper_accounts paper
      WHERE paper.account_id=item.account_id AND paper.user_id=item.user_id
    );
  RETURN NEXT;
END
$p1_automation_realized_performance_v2_v124$;

ALTER FUNCTION public.p1_automation_realized_performance_v2(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_automation_realized_performance_v2(text)
  FROM PUBLIC,decision_app,decision_worker,decision_replay,decision_replay_authorizer,
  decision_automation_runtime;
GRANT EXECUTE ON FUNCTION public.p1_automation_realized_performance_v2(text) TO decision_app;
