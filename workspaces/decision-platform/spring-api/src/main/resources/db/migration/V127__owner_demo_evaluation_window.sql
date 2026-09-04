CREATE OR REPLACE FUNCTION public.read_owner_scenario_materialization_inputs_v1(p_owner_user_id text)
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
    WHERE session_date <= DATE '2026-09-03'
    ORDER BY session_date DESC LIMIT 104
  ) recent;
  IF coalesce(array_length(sessions,1),0) <> 104 THEN
    RAISE EXCEPTION 'owner scenario requires 104 context sessions' USING ERRCODE='22023';
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
    'evaluationStart','2026-08-18',
    'evaluationEnd','2026-09-03',
    'sessions',to_jsonb(sessions),
    'rules',rules,
    'bars',bars
  );
END
$read_owner_scenario_materialization_inputs_v1$;

CREATE OR REPLACE FUNCTION public.latest_dashboard_artifact_run(
  p_actor_user_id text, p_security_version bigint, p_view_kind text
)
RETURNS TABLE(run_id text, fixture_class text, as_of timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $latest_dashboard_artifact_run_v127$
BEGIN
  IF session_user <> 'decision_app' OR p_view_kind NOT IN ('MODEL_EVALUATION','BACKTEST') THEN
    RAISE EXCEPTION 'dashboard projection read denied' USING ERRCODE='42501';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.users actor WHERE actor.user_id=p_actor_user_id
    AND actor.status='ACTIVE' AND actor.security_version=p_security_version) THEN RETURN; END IF;
  RETURN QUERY
  SELECT item.run_id, item.fixture_class, item.as_of
  FROM public.dashboard_artifact_views item
  WHERE item.view_kind=p_view_kind AND item.owner_user_id=p_actor_user_id
  ORDER BY (item.fixture_class='SYNTHETIC_FAKE_E2E'),item.published_at DESC,item.as_of DESC,item.run_id
  LIMIT 1;
END
$latest_dashboard_artifact_run_v127$;

ALTER FUNCTION public.read_owner_scenario_materialization_inputs_v1(text) OWNER TO flyway;
ALTER FUNCTION public.latest_dashboard_artifact_run(text,bigint,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.read_owner_scenario_materialization_inputs_v1(text) FROM PUBLIC,decision_worker;
REVOKE ALL ON FUNCTION public.latest_dashboard_artifact_run(text,bigint,text) FROM PUBLIC,decision_app;
GRANT EXECUTE ON FUNCTION public.read_owner_scenario_materialization_inputs_v1(text) TO decision_worker;
