-- Internal owner-scoped scheduling; the runtime receives no direct table SELECT.
-- The full product runs one bounded scheduler for all active owners. This database boundary
-- is the only way it discovers owners and acquires sessions; every lookup and replay is scoped
-- by the verified user_id argument, and admission is capped at the measured-work ceiling of 100.

CREATE FUNCTION public.p1_list_armed_automation_users_v1()
RETURNS TABLE(user_id text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_list_armed_automation_users_v1$
DECLARE active_count integer;
BEGIN
  IF session_user <> 'decision_automation_runtime' THEN
    RAISE EXCEPTION 'automation owner listing denied' USING ERRCODE='42501';
  END IF;
  SELECT count(*) INTO active_count
  FROM public.automation_control control
  JOIN public.users app_user ON app_user.user_id=control.user_id
  WHERE app_user.status='ACTIVE'
    AND (control.control_state='ARMED' OR EXISTS (
      SELECT 1 FROM public.automation_runtime_claim claim
      WHERE claim.user_id=control.user_id AND claim.claim_state='ACTIVE'
    ));
  IF active_count > 100 THEN
    RAISE EXCEPTION 'automation owner admission cap exceeded' USING ERRCODE='54000';
  END IF;
  RETURN QUERY
  SELECT control.user_id
  FROM public.automation_control control
  JOIN public.users app_user ON app_user.user_id=control.user_id
  WHERE app_user.status='ACTIVE'
    AND (control.control_state='ARMED' OR EXISTS (
      SELECT 1 FROM public.automation_runtime_claim claim
      WHERE claim.user_id=control.user_id AND claim.claim_state='ACTIVE'
    ))
  ORDER BY control.user_id;
END
$p1_list_armed_automation_users_v1$;

CREATE FUNCTION public.p1_claim_automation_session_for_owner_v1(
  p_owner_user_id text,p_session_date date,p_claim_token_hash text
)
RETURNS TABLE(
  user_id text,run_id text,control_version integer,account_id text,principle_id text,
  strategy_id text,baseline_account_digest text,replayed boolean
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog
AS $p1_claim_automation_session_for_owner_v1$
DECLARE schedule_row public.automation_runtime_schedule%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE new_run_id text;
DECLARE pinned_version public.principle_versions%ROWTYPE;
DECLARE event_seed text;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_owner_user_id!~'^usr_[A-Za-z0-9_-]{8,96}$'
     OR p_session_date IS NULL OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'automation claim input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT schedule.* INTO schedule_row
  FROM public.automation_runtime_schedule schedule
  JOIN public.automation_runtime_claim claim USING (user_id,session_date)
  WHERE schedule.user_id=p_owner_user_id AND schedule.session_date=p_session_date
    AND schedule.schedule_state='CLAIMED' AND claim.claim_state='ACTIVE'
  ORDER BY schedule.user_id LIMIT 1 FOR UPDATE OF schedule,claim;
  IF FOUND THEN
    PERFORM set_config('app.automation_claim_scan','0',true);
    PERFORM set_config('app.automation_owner_user_id',schedule_row.user_id,true);
    -- A rolling upgrade can encounter an active claim created by the former singleton runtime.
    -- Rebind only that exact owner's already-claimed session to the owner-scoped token; Python
    -- does not keep a permanent fallback to the old global claim format.
    UPDATE public.automation_runtime_claim SET claim_token_hash=p_claim_token_hash
      WHERE user_id=schedule_row.user_id AND session_date=p_session_date
        AND claim_state='ACTIVE' AND claim_token_hash<>p_claim_token_hash;
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
  WHERE user_id=p_owner_user_id AND session_date=p_session_date AND schedule_state='ARMED'
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
$p1_claim_automation_session_for_owner_v1$;

ALTER FUNCTION public.p1_list_armed_automation_users_v1() OWNER TO flyway;
ALTER FUNCTION public.p1_claim_automation_session_for_owner_v1(text,date,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_list_armed_automation_users_v1(),
  public.p1_claim_automation_session_for_owner_v1(text,date,text)
  FROM PUBLIC, decision_app, decision_worker, decision_auth, decision_identity;
GRANT EXECUTE ON FUNCTION public.p1_list_armed_automation_users_v1(),
  public.p1_claim_automation_session_for_owner_v1(text,date,text)
  TO decision_automation_runtime;
