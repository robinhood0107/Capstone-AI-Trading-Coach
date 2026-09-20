-- 세션 시작의 최신 원칙을 고정하며 과거 migration은 보존한다.
SET LOCAL row_security=on;
-- V90은 직접 flyway 세션도 차단하므로 원자 migration의 backfill 동안만 owner 접근을 연다.
ALTER TABLE public.automation_runs NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runtime_claim NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_control NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runs ADD COLUMN principle_id text,
 ADD COLUMN principle_version_id text, ADD COLUMN principle_version integer,
 ADD CONSTRAINT automation_run_principle_snapshot_fk FOREIGN KEY
 (principle_version_id,principle_id,principle_version)
 REFERENCES public.principle_versions(principle_version_id,principle_id,version);
-- 진행 중인 run의 기존 control 버전만 보존하고 종료 run의 버전은 추측하지 않는다.
UPDATE public.automation_runs run SET principle_id=c.principle_id,
 principle_version_id=c.principle_version_id,principle_version=c.principle_version
FROM public.automation_runtime_claim claim JOIN public.automation_control c USING(user_id)
WHERE run.run_id=claim.run_id AND claim.claim_state='ACTIVE';
ALTER TABLE public.automation_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_runtime_claim FORCE ROW LEVEL SECURITY;
ALTER TABLE public.automation_control FORCE ROW LEVEL SECURITY;


CREATE OR REPLACE FUNCTION public.p1_claim_automation_session_v1(
  p_session_date date,p_claim_token_hash text
)
RETURNS TABLE(
  user_id text,run_id text,control_version integer,account_id text,principle_id text,
  strategy_id text,baseline_account_digest text,replayed boolean
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_claim_automation_session_v1$
DECLARE schedule_row public.automation_runtime_schedule%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE new_run_id text;
DECLARE pinned_version public.principle_versions%ROWTYPE;
DECLARE event_seed text;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_session_date IS NULL
     OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'automation claim input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT schedule.* INTO schedule_row
  FROM public.automation_runtime_schedule schedule
  JOIN public.automation_runtime_claim claim USING (user_id,session_date)
  WHERE schedule.session_date=p_session_date AND schedule.schedule_state='CLAIMED'
    AND claim.claim_state='ACTIVE' AND claim.claim_token_hash=p_claim_token_hash
  ORDER BY schedule.user_id LIMIT 1 FOR UPDATE OF schedule;
  IF FOUND THEN
    PERFORM set_config('app.automation_claim_scan','0',true);
    PERFORM set_config('app.automation_owner_user_id',schedule_row.user_id,true);
    SELECT * INTO claim_row FROM public.automation_runtime_claim
      WHERE automation_runtime_claim.user_id=schedule_row.user_id
        AND automation_runtime_claim.session_date=p_session_date;
    SELECT * INTO control_row FROM public.automation_control WHERE automation_control.user_id=schedule_row.user_id;
    user_id:=schedule_row.user_id;run_id:=claim_row.run_id;control_version:=control_row.version;
    account_id:=control_row.account_id;principle_id:=control_row.principle_id;
    strategy_id:=control_row.strategy_id;baseline_account_digest:=control_row.baseline_account_digest;
    replayed:=true;RETURN NEXT;RETURN;
  END IF;
  SELECT * INTO schedule_row FROM public.automation_runtime_schedule
  WHERE session_date=p_session_date AND schedule_state='ARMED'
  ORDER BY user_id LIMIT 1 FOR UPDATE SKIP LOCKED;
  IF NOT FOUND THEN
    PERFORM set_config('app.automation_claim_scan','0',true);RETURN;
  END IF;
  PERFORM set_config('app.automation_claim_scan','0',true);
  PERFORM set_config('app.automation_owner_user_id',schedule_row.user_id,true);
  SELECT * INTO control_row FROM public.automation_control
    WHERE automation_control.user_id=schedule_row.user_id FOR UPDATE;
  IF NOT FOUND OR control_row.control_state<>'ARMED' OR control_row.version<>schedule_row.control_version
     OR control_row.brokerage_mode<>'KIS_MOCK' THEN
    RAISE EXCEPTION 'automation schedule control drift' USING ERRCODE='40001';
  END IF;
  -- control 다음 원칙 row를 잠가 편집과 claim을 직렬화한다.
  PERFORM 1 FROM public.principles p WHERE p.user_id=schedule_row.user_id
    AND p.principle_id=control_row.principle_id AND p.status='ACTIVE' FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'inactive automation principle' USING ERRCODE='40001'; END IF;
  IF EXISTS(SELECT 1 FROM public.automation_runtime_claim old
    WHERE old.user_id=schedule_row.user_id AND old.claim_state='ACTIVE') THEN
    RAISE EXCEPTION 'prior automation session remains active' USING ERRCODE='40001'; END IF;
  SELECT v.* INTO STRICT pinned_version FROM public.principles p
    JOIN public.principle_versions v ON v.principle_id=p.principle_id AND v.version=p.current_version
    WHERE p.user_id=schedule_row.user_id AND p.principle_id=control_row.principle_id AND v.status='ACTIVE';
  UPDATE public.automation_control SET principle_version_id=pinned_version.principle_version_id,
    principle_version=pinned_version.version WHERE automation_control.user_id=schedule_row.user_id
      AND policy_id IS NOT NULL;
  control_row.principle_version_id:=pinned_version.principle_version_id;
  control_row.principle_version:=pinned_version.version;
  new_run_id:='auto_run_'||substr(encode(public.digest(
    convert_to(schedule_row.user_id||':'||p_session_date::text,'UTF8'),'sha256'),'hex'),1,32);
  INSERT INTO public.automation_runs(
    run_id,user_id,session_date,state,brokerage_mode,selected_symbol,selected_side,
    physical_submit_count,vertex_call_count,provider_calls,started_at,updated_at,
    principle_id,principle_version_id,principle_version
  ) VALUES (
    new_run_id,schedule_row.user_id,p_session_date,'SCHEDULED','KIS_MOCK',NULL,NULL,
    0,0,0,statement_timestamp(),statement_timestamp(),
    control_row.principle_id,control_row.principle_version_id,control_row.principle_version
  );
  INSERT INTO public.automation_runtime_claim(
    user_id,session_date,run_id,claim_token_hash,claim_state,claimed_at,released_at
  ) VALUES (schedule_row.user_id,p_session_date,new_run_id,p_claim_token_hash,'ACTIVE',statement_timestamp(),NULL);
  INSERT INTO public.automation_runtime_checkpoint(
    run_id,user_id,session_date,checkpoint_version,state,selected_symbol,selected_side,decision_id,
    vertex_call_count,provider_call_count,logical_submit_count,updated_at
  ) VALUES (new_run_id,schedule_row.user_id,p_session_date,1,'SCHEDULED',NULL,NULL,NULL,0,0,0,statement_timestamp());
  INSERT INTO public.automation_events(
    event_id,run_id,user_id,sequence,event_type,occurred_at,payload_hash,
    provider_calls,order_submits,sanitized
  ) VALUES
    (
      'auto_evt_'||substr(encode(public.digest(convert_to(new_run_id||':1:BASELINE_CAPTURED','UTF8'),'sha256'),'hex'),1,32),
      new_run_id,schedule_row.user_id,1,'BASELINE_CAPTURED',statement_timestamp(),
      encode(public.digest(convert_to(control_row.baseline_account_digest,'UTF8'),'sha256'),'hex'),0,0,true
    ),
    (
      'auto_evt_'||substr(encode(public.digest(convert_to(new_run_id||':2:RUN_TRANSITIONED','UTF8'),'sha256'),'hex'),1,32),
      new_run_id,schedule_row.user_id,2,'RUN_TRANSITIONED',statement_timestamp(),
      encode(public.digest(convert_to('SCHEDULED','UTF8'),'sha256'),'hex'),0,0,true
    );
  UPDATE public.automation_runtime_schedule SET schedule_state='CLAIMED',updated_at=statement_timestamp()
  WHERE schedule_id=schedule_row.schedule_id;
  event_seed:=new_run_id||':SESSION_CLAIMED';
  INSERT INTO public.automation_runtime_events(
    event_id,user_id,session_date,run_id,event_type,payload_hash,sanitized,occurred_at
  ) VALUES (
    'auto_rte_'||substr(encode(public.digest(convert_to(event_seed,'UTF8'),'sha256'),'hex'),1,32),
    schedule_row.user_id,p_session_date,new_run_id,'SESSION_CLAIMED',
    encode(public.digest(convert_to(p_claim_token_hash,'UTF8'),'sha256'),'hex'),true,statement_timestamp()
  );
  user_id:=schedule_row.user_id;run_id:=new_run_id;control_version:=control_row.version;
  account_id:=control_row.account_id;principle_id:=control_row.principle_id;
  strategy_id:=control_row.strategy_id;baseline_account_digest:=control_row.baseline_account_digest;
  replayed:=false;RETURN NEXT;
END
$p1_claim_automation_session_v1$;

CREATE OR REPLACE FUNCTION public.p1_automation_runtime_readiness_v1(p_user_id text, p_target_session date)
 RETURNS TABLE(control_configured boolean, certification_valid boolean, release_source_bound boolean, real_team_b_ready boolean, principle_current boolean, kill_switch_inactive boolean, account_baseline_matches boolean, unresolved_state_clear boolean, target_available boolean, current_control_version integer, all_ready boolean)
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE gate_row public.automation_activation_gate%ROWTYPE;
DECLARE observed_digest text;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_user_id!~'^usr_[A-Za-z0-9_-]{8,96}$'
     OR p_target_session IS NULL THEN
    RAISE EXCEPTION 'automation readiness scope denied' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_owner_user_id',p_user_id,true);
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=p_user_id;
  SELECT * INTO gate_row FROM public.automation_activation_gate WHERE user_id=p_user_id;
  control_configured:=control_row.user_id IS NOT NULL
    AND control_row.control_state IN ('DISARMED','ARMED')
    AND control_row.brokerage_mode='KIS_MOCK'
    AND control_row.baseline_account_digest~'^[0-9a-f]{64}$';
  certification_valid:=gate_row.user_id IS NOT NULL AND gate_row.certification_status='VALID'
    AND gate_row.certification_receipt_sha256 IS NOT NULL
    AND gate_row.strategy_eligible_from_session_date IS NOT NULL
    AND p_target_session>=gate_row.strategy_eligible_from_session_date;
  release_source_bound:=gate_row.user_id IS NOT NULL AND gate_row.clean_release_binding
    AND gate_row.release_binding_sha256 IS NOT NULL AND gate_row.source_binding_sha256 IS NOT NULL;
  real_team_b_ready:=gate_row.user_id IS NOT NULL AND gate_row.real_team_b_pointer_active
    AND gate_row.team_b_integrity_receipt_sha256 IS NOT NULL
    AND (SELECT count(*) FROM public.current_p1_return_signal_pointer)=31
    AND (SELECT count(DISTINCT bundle_sha256) FROM public.current_p1_return_signal_pointer)=1;
  principle_current:=control_row.user_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM public.principles principle
    WHERE principle.user_id=p_user_id AND principle.principle_id=control_row.principle_id
      AND principle.status='ACTIVE'
  );
  kill_switch_inactive:=COALESCE((
    SELECT NOT active FROM public.risk_kill_switch WHERE kill_switch_id='GLOBAL'
  ),false);
  kill_switch_inactive:=kill_switch_inactive AND NOT public.owner_stop_active(p_user_id);
  IF control_configured THEN
    IF control_row.expected_account_digest_v2 IS NOT NULL THEN
      observed_digest:=encode(public.digest(convert_to(
        public.p1_automation_risk_balance_projection_v2(p_user_id,control_row.account_id)::text,
        'UTF8'),'sha256'),'hex');
    ELSE
      observed_digest:=public.p1_automation_runtime_account_digest_v1(p_user_id,control_row.account_id);
    END IF;
  END IF;
  account_baseline_matches:=observed_digest IS NOT NULL
    AND observed_digest=COALESCE(control_row.expected_account_digest_v2,control_row.baseline_account_digest);
  unresolved_state_clear:=control_row.user_id IS NOT NULL
    AND public.p1_automation_open_work_clear_v3(p_user_id,control_row.account_id);
  IF control_row.control_state='ARMED' THEN
    target_available:=EXISTS (
      SELECT 1 FROM public.automation_runtime_schedule schedule
      WHERE schedule.user_id=p_user_id AND schedule.session_date=p_target_session
        AND schedule.schedule_state IN ('ARMED','CLAIMED')
        AND schedule.control_version=control_row.version
    );
  ELSE
    target_available:=NOT EXISTS (
      SELECT 1 FROM public.automation_runtime_schedule schedule
      WHERE schedule.user_id=p_user_id AND schedule.session_date=p_target_session
        AND schedule.schedule_state IN ('ARMED','CLAIMED')
        AND schedule.control_version=control_row.version
    );
  END IF;
  current_control_version:=COALESCE(control_row.version,1);
  all_ready:=control_configured AND certification_valid AND release_source_bound
    AND real_team_b_ready AND principle_current AND kill_switch_inactive
    AND account_baseline_matches AND unresolved_state_clear AND target_available;
  RETURN NEXT;
END
$function$;

CREATE OR REPLACE FUNCTION public.p1_read_automation_runtime_state_v4(p_run_id text, p_claim_token_hash text)
 RETURNS text
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE base jsonb;
DECLARE target date;
DECLARE signals_json jsonb;
DECLARE principle_version_current boolean;
BEGIN
  IF session_user<>'decision_automation_runtime' THEN
    RAISE EXCEPTION 'automation state v4 denied' USING ERRCODE='42501';
  END IF;
  base:=public.p1_read_automation_runtime_state_v3(p_run_id,p_claim_token_hash)::jsonb;
  target:=(base->>'sessionDate')::date;
  -- ARM 이 고정한 원칙 버전이 아직 현재 버전인가. DISARMED 이거나 아직 고정한 것이 없으면
  -- 활성 여부만 본다 - 그러지 않으면 첫 무장 전에 영구히 거짓이 된다.
  SELECT EXISTS (
    SELECT 1 FROM public.automation_runs run
    JOIN public.principles p ON p.principle_id=run.principle_id AND p.user_id=run.user_id
    JOIN public.automation_control c ON c.user_id=run.user_id
    WHERE run.run_id=p_run_id AND p.status='ACTIVE'
      AND (c.policy_id IS NULL OR c.principle_version_id=run.principle_version_id)
  ) INTO principle_version_current;
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
    'signals',signals_json,
    -- v1 이 만든 값을 덮는다. 런타임이 보는 것은 이 값이다.
    'principleActiveCurrent',COALESCE(principle_version_current,false)
  ))::text;
END
$function$;

CREATE FUNCTION public.read_automation_principle_snapshot_authorized(
 p_capability text,p_actor_user_id text,p_principle_id text,p_run_id text,p_claim_hash text
) RETURNS TABLE(principle_id text,principle_version_id text,version integer,mode text,status text,rules_json text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog AS $pin$
BEGIN
 IF session_user<>'decision_app' OR p_run_id IS NULL OR p_claim_hash IS NULL THEN
  RAISE EXCEPTION 'automation snapshot denied' USING ERRCODE='42501'; END IF;
 IF NOT public.consume_current_actor_capability_v2(p_capability,p_actor_user_id,
   'READ_ACTIVE_PRINCIPLE','PRINCIPLE',p_principle_id,
   'sha256:'||encode(public.digest(p_principle_id,'sha256'),'hex')) THEN RETURN; END IF;
 RETURN QUERY SELECT p.principle_id,v.principle_version_id,v.version,v.mode,v.status,v.rules_json::text
 FROM public.automation_runs run
 JOIN public.automation_runtime_claim claim ON claim.run_id=run.run_id AND claim.user_id=run.user_id
 JOIN public.principles p ON p.principle_id=run.principle_id AND p.user_id=run.user_id
 JOIN public.principle_versions v ON v.principle_version_id=run.principle_version_id
 WHERE run.run_id=p_run_id AND run.user_id=p_actor_user_id AND p.principle_id=p_principle_id
   AND p.status='ACTIVE' AND v.status='ACTIVE' AND claim.claim_state='ACTIVE'
   AND claim.claim_token_hash=p_claim_hash;
END $pin$;
REVOKE ALL ON FUNCTION public.read_automation_principle_snapshot_authorized(text,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_automation_principle_snapshot_authorized(text,text,text,text,text) TO decision_app;


CREATE OR REPLACE FUNCTION public.persist_decision_bundle_authorized(p_capability text, p_bundle jsonb)
 RETURNS TABLE(outcome text, result_canonical_json text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
#variable_conflict use_column
DECLARE
  actor_user_id text;
  actor_role text;
  existing record;
  generation integer;
  violation jsonb;
  ordinal integer := 0;
  trace_type text;
  created_at timestamptz;
  database_now timestamptz := statement_timestamp();
  result_text text;
  snapshot_text text;
  reference_payload jsonb;
  gate_active boolean;
  persisted record;
BEGIN
  IF session_user <> 'decision_app'
     OR jsonb_typeof(p_bundle) <> 'object'
     OR EXISTS (SELECT 1 FROM jsonb_each(p_bundle) AS field WHERE field.value = 'null'::jsonb)
     OR p_bundle - ARRAY[
       'decisionId','evaluationId','actorUserId','actorRole','requestId',
       'scopeHash','requestHash','ownerScopeHash','portfolioSource','symbol','side','outcome','mode',
       'canSubmitOrder','enforcementAction','evaluationAsOf','createdAt','validUntil',
       'resultSchemaVersion','snapshotSchemaVersion','catalogVersion','readinessPolicyVersion',
       'mappingVersions','semanticInputHash','snapshotArtifactHash','resultCanonicalJson',
       'snapshotCanonicalJson','principleId','principleVersion','principleVersionId','violations'
     ] <> '{}'::jsonb
     OR NOT p_bundle ?& ARRAY[
       'decisionId','evaluationId','actorUserId','actorRole','requestId',
       'scopeHash','requestHash','ownerScopeHash','portfolioSource','symbol','side','outcome','mode',
       'canSubmitOrder','enforcementAction','evaluationAsOf','createdAt','validUntil',
       'resultSchemaVersion','snapshotSchemaVersion','catalogVersion','readinessPolicyVersion',
       'mappingVersions','semanticInputHash','snapshotArtifactHash','resultCanonicalJson',
       'snapshotCanonicalJson','principleId','principleVersion','principleVersionId','violations'
     ] THEN
    RAISE EXCEPTION 'decision bundle shape denied' USING ERRCODE = '22023';
  END IF;

  BEGIN
    actor_user_id := p_bundle->>'actorUserId';
    actor_role := p_bundle->>'actorRole';
    created_at := (p_bundle->>'createdAt')::timestamptz;
    result_text := p_bundle->>'resultCanonicalJson';
    snapshot_text := p_bundle->>'snapshotCanonicalJson';
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'decision bundle types denied' USING ERRCODE = '22023';
  END;

  IF actor_role NOT IN ('USER','ADMIN')
     OR p_bundle->>'requestId' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
     OR p_bundle->>'scopeHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'requestHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'ownerScopeHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'semanticInputHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'snapshotArtifactHash' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(p_bundle->'mappingVersions') <> 'object'
     OR jsonb_typeof(p_bundle->'violations') <> 'array'
     OR jsonb_array_length(p_bundle->'violations') > 14
     OR octet_length(result_text) NOT BETWEEN 2 AND 1048576
     OR octet_length(snapshot_text) NOT BETWEEN 2 AND 1048576
     OR jsonb_typeof(result_text::jsonb) <> 'object'
     OR jsonb_typeof(snapshot_text::jsonb) <> 'object' THEN
    RAISE EXCEPTION 'decision bundle values denied' USING ERRCODE = '22023';
  END IF;

  IF NOT public.consume_current_actor_capability(p_capability,actor_user_id) THEN
    RAISE EXCEPTION 'decision actor capability denied' USING ERRCODE = '42501';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.users actor
    WHERE actor.user_id=actor_user_id AND actor.status='ACTIVE' AND actor.role=actor_role
  ) THEN
    RAISE EXCEPTION 'decision current actor denied' USING ERRCODE = '42501';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(p_bundle->>'scopeHash',2303));
  SELECT item.request_hash,item.result_canonical_json INTO existing
  FROM public.decision_idempotency_results item
  WHERE item.scope_hash=p_bundle->>'scopeHash'
    AND item.owner_scope_hash=p_bundle->>'ownerScopeHash'
    AND item.expires_at>database_now
  ORDER BY item.generation DESC LIMIT 1;
  IF FOUND THEN
    IF existing.request_hash=p_bundle->>'requestHash' THEN
      RETURN QUERY SELECT 'REPLAY'::text,existing.result_canonical_json;
    ELSE
      RETURN QUERY SELECT 'CONFLICT'::text,NULL::text;
    END IF;
    RETURN;
  END IF;

  SELECT gate.active INTO gate_active
  FROM public.risk_kill_switch gate
  WHERE gate.kill_switch_id='GLOBAL'
  FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'kill switch authority unavailable' USING ERRCODE = 'P5501';
  END IF;
  IF gate_active OR public.owner_stop_active(actor_user_id) THEN
    RAISE EXCEPTION 'kill switch blocks decision persistence' USING ERRCODE = '55000';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM public.principles item
    JOIN public.principle_versions version_item
      ON version_item.principle_id=item.principle_id AND version_item.version=(p_bundle->>'principleVersion')::integer
    WHERE item.principle_id=p_bundle->>'principleId'
      AND item.user_id=actor_user_id
      AND item.status='ACTIVE'
      AND (item.current_version=(p_bundle->>'principleVersion')::integer OR EXISTS (
        SELECT 1 FROM public.automation_runs run
        JOIN public.automation_runtime_claim claim ON claim.run_id=run.run_id AND claim.user_id=run.user_id
        JOIN public.automation_order_reservations reservation ON reservation.run_id=run.run_id AND reservation.user_id=run.user_id
        WHERE run.user_id=actor_user_id AND run.principle_id=item.principle_id
          AND run.principle_version_id=p_bundle->>'principleVersionId'
          AND claim.claim_state='ACTIVE' AND run.state='RISK_CHECKING'
          AND reservation.principle_version_id=run.principle_version_id
          AND p_bundle->>'portfolioSource'='KIS_MOCK'
          -- snapshot은 금액/수량을 문자열로 canonicalize한다. field 집합과 값은 그대로 대조한다.
          AND (SELECT jsonb_object_agg(key,value) FROM jsonb_each_text(reservation.exact_intent_json::jsonb))
            =(SELECT jsonb_object_agg(key,value) FROM jsonb_each_text(snapshot_text::jsonb->'orderIntent'))
      ))
      AND version_item.principle_version_id=p_bundle->>'principleVersionId'
      AND version_item.status='ACTIVE'
      AND version_item.mode=p_bundle->>'mode'
    FOR SHARE OF item
  ) THEN
    RAISE EXCEPTION 'pinned principle conflict' USING ERRCODE = '40001';
  END IF;

  INSERT INTO public.decisions(
    decision_id,evaluation_id,user_id,principle_id,principle_version_id,principle_version,
    portfolio_source,symbol,side,outcome,mode,can_submit_order,enforcement_action,
    evaluation_as_of,created_at,valid_until,result_schema_version,snapshot_schema_version,
    catalog_version,readiness_policy_version,mapping_versions_json,semantic_input_hash,
    snapshot_artifact_hash,result_json
  ) VALUES (
    p_bundle->>'decisionId',p_bundle->>'evaluationId',actor_user_id,p_bundle->>'principleId',
    p_bundle->>'principleVersionId',(p_bundle->>'principleVersion')::integer,
    p_bundle->>'portfolioSource',p_bundle->>'symbol',p_bundle->>'side',p_bundle->>'outcome',
    p_bundle->>'mode',(p_bundle->>'canSubmitOrder')::boolean,p_bundle->>'enforcementAction',
    (p_bundle->>'evaluationAsOf')::timestamptz,created_at,(p_bundle->>'validUntil')::timestamptz,
    p_bundle->>'resultSchemaVersion',p_bundle->>'snapshotSchemaVersion',
    (p_bundle->>'catalogVersion')::integer,p_bundle->>'readinessPolicyVersion',
    p_bundle->'mappingVersions',p_bundle->>'semanticInputHash',p_bundle->>'snapshotArtifactHash',
    result_text::jsonb
  ) RETURNING decision_id,evaluation_id,outcome,principle_version_id,
      semantic_input_hash,snapshot_artifact_hash
    INTO persisted;

  FOR violation IN SELECT value FROM jsonb_array_elements(p_bundle->'violations') LOOP
    ordinal := ordinal + 1;
    IF jsonb_typeof(violation)<>'object'
       OR violation - ARRAY['ruleId','severity','observedValue','thresholdValue','message'] <> '{}'::jsonb
       OR NOT violation ?& ARRAY['ruleId','severity','observedValue','thresholdValue','message'] THEN
      RAISE EXCEPTION 'decision violation shape denied' USING ERRCODE='22023';
    END IF;
    INSERT INTO public.decision_violations(
      violation_id,decision_id,evaluation_id,ordinal,rule_id,severity,metric,public_code,
      observed_value,threshold_value,message,created_at
    ) VALUES (
      'vio_'||replace(gen_random_uuid()::text,'-',''),p_bundle->>'decisionId',p_bundle->>'evaluationId',
      ordinal,violation->>'ruleId',violation->>'severity',NULL,NULL,
      CASE WHEN violation->'observedValue'='null'::jsonb THEN NULL ELSE (violation->>'observedValue')::numeric END,
      CASE WHEN violation->'thresholdValue'='null'::jsonb THEN NULL ELSE (violation->>'thresholdValue')::numeric END,
      violation->>'message',created_at
    );
  END LOOP;

  FOREACH trace_type IN ARRAY ARRAY[
    'ORDER_VALIDATED','PRINCIPLE_PINNED','FRESHNESS_EVALUATED','RULES_EVALUATED',
    'FINDINGS_COMPOSED','POLICY_APPLIED','PERSISTED'
  ] LOOP
    ordinal := array_position(ARRAY[
      'ORDER_VALIDATED','PRINCIPLE_PINNED','FRESHNESS_EVALUATED','RULES_EVALUATED',
      'FINDINGS_COMPOSED','POLICY_APPLIED','PERSISTED'
    ],trace_type);
    INSERT INTO public.decision_traces(
      trace_id,decision_id,evaluation_id,step,trace_type,trace_json,created_at
    ) VALUES (
      'trc_'||replace(gen_random_uuid()::text,'-',''),p_bundle->>'decisionId',p_bundle->>'evaluationId',
      ordinal,trace_type,jsonb_build_object('decisionId',p_bundle->>'decisionId',
        'evaluationId',p_bundle->>'evaluationId','traceType',trace_type),created_at
    );
  END LOOP;

  INSERT INTO public.decision_artifacts(
    decision_id,evaluation_id,result_canonical_json,snapshot_artifact_canonical_json,
    semantic_input_hash,snapshot_artifact_hash,created_at
  ) VALUES (
    p_bundle->>'decisionId',p_bundle->>'evaluationId',result_text,snapshot_text,
    p_bundle->>'semanticInputHash',p_bundle->>'snapshotArtifactHash',created_at
  );

  reference_payload := jsonb_build_object(
    'evaluationId',persisted.evaluation_id,'decisionId',persisted.decision_id,
    'outcome',persisted.outcome,'principleVersionId',persisted.principle_version_id,
    'semanticInputHash',persisted.semantic_input_hash,'snapshotArtifactHash',persisted.snapshot_artifact_hash
  );
  INSERT INTO public.audit_logs(
    audit_log_id,user_id,actor_role,action,target_type,target_id,request_id,payload_json,created_at
  ) VALUES (
    'aud_'||replace(gen_random_uuid()::text,'-',''),actor_user_id,actor_role,'DECISION_EVALUATED',
    'DECISION',persisted.decision_id,p_bundle->>'requestId',reference_payload,created_at
  );
  INSERT INTO public.event_outbox(
    event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,
    schema_version,status,retry_count,created_at,updated_at
  ) VALUES (
    'evt_'||replace(gen_random_uuid()::text,'-',''),'risk.decision-created.v1','DECISION',
    persisted.decision_id,persisted.decision_id,reference_payload,'1.0.0','PENDING',0,created_at,created_at
  );

  SELECT coalesce(max(item.generation),0)+1 INTO generation
  FROM public.decision_idempotency_results item
  WHERE item.scope_hash=p_bundle->>'scopeHash' AND item.owner_scope_hash=p_bundle->>'ownerScopeHash';
  INSERT INTO public.decision_idempotency_results(
    idempotency_result_id,scope_hash,generation,request_hash,owner_scope_hash,purpose_version,
    decision_id,evaluation_id,http_status,content_type,result_canonical_json,created_at,expires_at
  ) VALUES (
    'idr_'||replace(gen_random_uuid()::text,'-',''),p_bundle->>'scopeHash',generation,
    p_bundle->>'requestHash',p_bundle->>'ownerScopeHash','decision-evaluate-order/v1',
    persisted.decision_id,persisted.evaluation_id,200,'application/json',result_text,
    database_now,database_now+interval '24 hours'
  );
  RETURN QUERY SELECT 'INSERTED'::text,result_text;
END
$function$
;

CREATE OR REPLACE FUNCTION public.persist_decision_bundle_authorized_v2(p_capability text, p_bundle_text text)
 RETURNS TABLE(outcome text, result_canonical_json text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
#variable_conflict use_column
DECLARE
  p_bundle jsonb;
  actor_user_id text;
  actor_role text;
  actor_security_version bigint;
  existing record;
  generation integer;
  violation jsonb;
  ordinal integer := 0;
  trace_type text;
  created_at timestamptz;
  database_now timestamptz := statement_timestamp();
  result_text text;
  snapshot_text text;
  reference_payload jsonb;
  gate_active boolean;
  persisted record;
BEGIN
  IF session_user<>'decision_app' OR octet_length(p_bundle_text) NOT BETWEEN 2 AND 4194304 THEN
    RAISE EXCEPTION 'decision bundle request denied' USING ERRCODE='42501';
  END IF;
  BEGIN
    p_bundle:=p_bundle_text::jsonb;
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'decision bundle types denied' USING ERRCODE='22023';
  END;
  IF jsonb_typeof(p_bundle)<>'object'
     OR EXISTS (SELECT 1 FROM jsonb_each(p_bundle) AS field WHERE field.value='null'::jsonb)
     OR p_bundle-ARRAY[
       'decisionId','evaluationId','actorUserId','actorRole','requestId',
       'scopeHash','requestHash','ownerScopeHash','portfolioSource','symbol','side','outcome','mode',
       'canSubmitOrder','enforcementAction','evaluationAsOf','createdAt','validUntil',
       'resultSchemaVersion','snapshotSchemaVersion','catalogVersion','readinessPolicyVersion',
       'mappingVersions','semanticInputHash','snapshotArtifactHash','resultCanonicalJson',
       'snapshotCanonicalJson','principleId','principleVersion','principleVersionId','violations'
     ]<>'{}'::jsonb
     OR NOT p_bundle?&ARRAY[
       'decisionId','evaluationId','actorUserId','actorRole','requestId',
       'scopeHash','requestHash','ownerScopeHash','portfolioSource','symbol','side','outcome','mode',
       'canSubmitOrder','enforcementAction','evaluationAsOf','createdAt','validUntil',
       'resultSchemaVersion','snapshotSchemaVersion','catalogVersion','readinessPolicyVersion',
       'mappingVersions','semanticInputHash','snapshotArtifactHash','resultCanonicalJson',
       'snapshotCanonicalJson','principleId','principleVersion','principleVersionId','violations'
     ] THEN
    RAISE EXCEPTION 'decision bundle shape denied' USING ERRCODE='22023';
  END IF;
  BEGIN
    actor_user_id:=p_bundle->>'actorUserId';
    actor_role:=p_bundle->>'actorRole';
    created_at:=(p_bundle->>'createdAt')::timestamptz;
    result_text:=p_bundle->>'resultCanonicalJson';
    snapshot_text:=p_bundle->>'snapshotCanonicalJson';
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'decision bundle types denied' USING ERRCODE='22023';
  END;
  IF actor_role NOT IN ('USER','ADMIN')
     OR p_bundle->>'decisionId' !~ '^dec_[0-9a-f]{32}$'
     OR p_bundle->>'requestId' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'
     OR p_bundle->>'scopeHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'requestHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'ownerScopeHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'semanticInputHash' !~ '^[0-9a-f]{64}$'
     OR p_bundle->>'snapshotArtifactHash' !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(p_bundle->'mappingVersions')<>'object'
     OR jsonb_typeof(p_bundle->'violations')<>'array'
     OR jsonb_array_length(p_bundle->'violations')>14
     OR octet_length(result_text) NOT BETWEEN 2 AND 1048576
     OR octet_length(snapshot_text) NOT BETWEEN 2 AND 1048576
     OR jsonb_typeof(result_text::jsonb)<>'object'
     OR jsonb_typeof(snapshot_text::jsonb)<>'object' THEN
    RAISE EXCEPTION 'decision bundle values denied' USING ERRCODE='22023';
  END IF;
  SELECT actor.security_version INTO actor_security_version
  FROM public.users actor
  WHERE actor.user_id=actor_user_id AND actor.status='ACTIVE' AND actor.role=actor_role
  FOR SHARE;
  IF NOT FOUND OR NOT public.consume_actor_request_capability_v2(
    p_capability,actor_user_id,actor_security_version,actor_role,
    'PERSIST_DECISION_BUNDLE','DECISION',p_bundle->>'decisionId',
    public.actor_capability_payload_hash(p_bundle_text)
  ) THEN
    RAISE EXCEPTION 'decision actor capability denied' USING ERRCODE='42501';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(p_bundle->>'scopeHash',2303));
  SELECT item.request_hash,item.result_canonical_json INTO existing
  FROM public.decision_idempotency_results item
  WHERE item.scope_hash=p_bundle->>'scopeHash'
    AND item.owner_scope_hash=p_bundle->>'ownerScopeHash'
    AND item.expires_at>database_now
  ORDER BY item.generation DESC LIMIT 1;
  IF FOUND THEN
    IF existing.request_hash=p_bundle->>'requestHash' THEN
      RETURN QUERY SELECT 'REPLAY'::text,existing.result_canonical_json;
    ELSE
      RETURN QUERY SELECT 'CONFLICT'::text,NULL::text;
    END IF;
    RETURN;
  END IF;

  SELECT gate.active INTO gate_active
  FROM public.risk_kill_switch gate
  WHERE gate.kill_switch_id='GLOBAL'
  FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'kill switch authority unavailable' USING ERRCODE='P5501';
  END IF;
  IF gate_active OR public.owner_stop_active(actor_user_id) THEN
    RAISE EXCEPTION 'kill switch blocks decision persistence' USING ERRCODE='55000';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM public.principles item
    JOIN public.principle_versions version_item
      ON version_item.principle_id=item.principle_id AND version_item.version=(p_bundle->>'principleVersion')::integer
    WHERE item.principle_id=p_bundle->>'principleId'
      AND item.user_id=actor_user_id
      AND item.status='ACTIVE'
      AND (item.current_version=(p_bundle->>'principleVersion')::integer OR EXISTS (
        SELECT 1 FROM public.automation_runs run
        JOIN public.automation_runtime_claim claim ON claim.run_id=run.run_id AND claim.user_id=run.user_id
        JOIN public.automation_order_reservations reservation ON reservation.run_id=run.run_id AND reservation.user_id=run.user_id
        WHERE run.user_id=actor_user_id AND run.principle_id=item.principle_id
          AND run.principle_version_id=p_bundle->>'principleVersionId'
          AND claim.claim_state='ACTIVE' AND run.state='RISK_CHECKING'
          AND reservation.principle_version_id=run.principle_version_id
          AND p_bundle->>'portfolioSource'='KIS_MOCK'
          -- snapshot은 금액/수량을 문자열로 canonicalize한다. field 집합과 값은 그대로 대조한다.
          AND (SELECT jsonb_object_agg(key,value) FROM jsonb_each_text(reservation.exact_intent_json::jsonb))
            =(SELECT jsonb_object_agg(key,value) FROM jsonb_each_text(snapshot_text::jsonb->'orderIntent'))
      ))
      AND version_item.principle_version_id=p_bundle->>'principleVersionId'
      AND version_item.status='ACTIVE'
      AND version_item.mode=p_bundle->>'mode'
    FOR SHARE OF item
  ) THEN
    RAISE EXCEPTION 'pinned principle conflict' USING ERRCODE='40001';
  END IF;

  INSERT INTO public.decisions(
    decision_id,evaluation_id,user_id,principle_id,principle_version_id,principle_version,
    portfolio_source,symbol,side,outcome,mode,can_submit_order,enforcement_action,
    evaluation_as_of,created_at,valid_until,result_schema_version,snapshot_schema_version,
    catalog_version,readiness_policy_version,mapping_versions_json,semantic_input_hash,
    snapshot_artifact_hash,result_json
  ) VALUES (
    p_bundle->>'decisionId',p_bundle->>'evaluationId',actor_user_id,p_bundle->>'principleId',
    p_bundle->>'principleVersionId',(p_bundle->>'principleVersion')::integer,
    p_bundle->>'portfolioSource',p_bundle->>'symbol',p_bundle->>'side',p_bundle->>'outcome',
    p_bundle->>'mode',(p_bundle->>'canSubmitOrder')::boolean,p_bundle->>'enforcementAction',
    (p_bundle->>'evaluationAsOf')::timestamptz,created_at,(p_bundle->>'validUntil')::timestamptz,
    p_bundle->>'resultSchemaVersion',p_bundle->>'snapshotSchemaVersion',
    (p_bundle->>'catalogVersion')::integer,p_bundle->>'readinessPolicyVersion',
    p_bundle->'mappingVersions',p_bundle->>'semanticInputHash',p_bundle->>'snapshotArtifactHash',
    result_text::jsonb
  ) RETURNING decision_id,evaluation_id,outcome,principle_version_id,
      semantic_input_hash,snapshot_artifact_hash
    INTO persisted;

  FOR violation IN SELECT value FROM jsonb_array_elements(p_bundle->'violations') LOOP
    ordinal:=ordinal+1;
    IF jsonb_typeof(violation)<>'object'
       OR violation-ARRAY['ruleId','severity','observedValue','thresholdValue','message']<>'{}'::jsonb
       OR NOT violation?&ARRAY['ruleId','severity','observedValue','thresholdValue','message'] THEN
      RAISE EXCEPTION 'decision violation shape denied' USING ERRCODE='22023';
    END IF;
    INSERT INTO public.decision_violations(
      violation_id,decision_id,evaluation_id,ordinal,rule_id,severity,metric,public_code,
      observed_value,threshold_value,message,created_at
    ) VALUES (
      'vio_'||replace(gen_random_uuid()::text,'-',''),p_bundle->>'decisionId',p_bundle->>'evaluationId',
      ordinal,violation->>'ruleId',violation->>'severity',NULL,NULL,
      CASE WHEN violation->'observedValue'='null'::jsonb THEN NULL ELSE (violation->>'observedValue')::numeric END,
      CASE WHEN violation->'thresholdValue'='null'::jsonb THEN NULL ELSE (violation->>'thresholdValue')::numeric END,
      violation->>'message',created_at
    );
  END LOOP;

  FOREACH trace_type IN ARRAY ARRAY[
    'ORDER_VALIDATED','PRINCIPLE_PINNED','FRESHNESS_EVALUATED','RULES_EVALUATED',
    'FINDINGS_COMPOSED','POLICY_APPLIED','PERSISTED'
  ] LOOP
    ordinal:=array_position(ARRAY[
      'ORDER_VALIDATED','PRINCIPLE_PINNED','FRESHNESS_EVALUATED','RULES_EVALUATED',
      'FINDINGS_COMPOSED','POLICY_APPLIED','PERSISTED'
    ],trace_type);
    INSERT INTO public.decision_traces(
      trace_id,decision_id,evaluation_id,step,trace_type,trace_json,created_at
    ) VALUES (
      'trc_'||replace(gen_random_uuid()::text,'-',''),p_bundle->>'decisionId',p_bundle->>'evaluationId',
      ordinal,trace_type,jsonb_build_object('decisionId',p_bundle->>'decisionId',
        'evaluationId',p_bundle->>'evaluationId','traceType',trace_type),created_at
    );
  END LOOP;

  INSERT INTO public.decision_artifacts(
    decision_id,evaluation_id,result_canonical_json,snapshot_artifact_canonical_json,
    semantic_input_hash,snapshot_artifact_hash,created_at
  ) VALUES (
    p_bundle->>'decisionId',p_bundle->>'evaluationId',result_text,snapshot_text,
    p_bundle->>'semanticInputHash',p_bundle->>'snapshotArtifactHash',created_at
  );
  reference_payload:=jsonb_build_object(
    'evaluationId',persisted.evaluation_id,'decisionId',persisted.decision_id,
    'outcome',persisted.outcome,'principleVersionId',persisted.principle_version_id,
    'semanticInputHash',persisted.semantic_input_hash,'snapshotArtifactHash',persisted.snapshot_artifact_hash
  );
  INSERT INTO public.audit_logs(
    audit_log_id,user_id,actor_role,action,target_type,target_id,request_id,payload_json,created_at
  ) VALUES (
    'aud_'||replace(gen_random_uuid()::text,'-',''),actor_user_id,actor_role,'DECISION_EVALUATED',
    'DECISION',persisted.decision_id,p_bundle->>'requestId',reference_payload,created_at
  );
  INSERT INTO public.event_outbox(
    event_id,event_type,aggregate_type,aggregate_id,partition_key,payload_json,
    schema_version,status,retry_count,created_at,updated_at
  ) VALUES (
    'evt_'||replace(gen_random_uuid()::text,'-',''),'risk.decision-created.v1','DECISION',
    persisted.decision_id,persisted.decision_id,reference_payload,'1.0.0','PENDING',0,created_at,created_at
  );

  SELECT coalesce(max(item.generation),0)+1 INTO generation
  FROM public.decision_idempotency_results item
  WHERE item.scope_hash=p_bundle->>'scopeHash' AND item.owner_scope_hash=p_bundle->>'ownerScopeHash';
  INSERT INTO public.decision_idempotency_results(
    idempotency_result_id,scope_hash,generation,request_hash,owner_scope_hash,purpose_version,
    decision_id,evaluation_id,http_status,content_type,result_canonical_json,created_at,expires_at
  ) VALUES (
    'idr_'||replace(gen_random_uuid()::text,'-',''),p_bundle->>'scopeHash',generation,
    p_bundle->>'requestHash',p_bundle->>'ownerScopeHash','decision-evaluate-order/v1',
    persisted.decision_id,persisted.evaluation_id,200,'application/json',result_text,
    database_now,database_now+interval '24 hours'
  );
  RETURN QUERY SELECT 'INSERTED'::text,result_text;
END
$function$
;

-- 앱에 테이블 접근 권한을 추가하지 않고 definer의 owner-scoped 읽기만 허용한다.
CREATE POLICY automation_claim_snapshot_reader_v155 ON public.automation_runtime_claim
FOR SELECT TO PUBLIC USING(current_user='flyway' AND session_user='decision_app'
 AND public.actor_rls_scope_is_open_v1() AND user_id=current_setting('app.actor_user_id',true));
CREATE POLICY automation_reservation_snapshot_reader_v155 ON public.automation_order_reservations
FOR SELECT TO PUBLIC USING(current_user='flyway' AND session_user='decision_app'
 AND public.actor_rls_scope_is_open_v1() AND user_id=current_setting('app.actor_user_id',true));
CREATE FUNCTION public.guard_automation_principle_snapshot_v155() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $guard$
BEGIN
 IF ROW(NEW.principle_id,NEW.principle_version_id,NEW.principle_version)
    IS DISTINCT FROM ROW(OLD.principle_id,OLD.principle_version_id,OLD.principle_version) THEN
  RAISE EXCEPTION 'immutable automation principle snapshot' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $guard$;
CREATE TRIGGER immutable_automation_principle_snapshot_v155
BEFORE UPDATE ON public.automation_runs FOR EACH ROW
EXECUTE FUNCTION public.guard_automation_principle_snapshot_v155();
REVOKE ALL ON FUNCTION public.guard_automation_principle_snapshot_v155() FROM PUBLIC;
