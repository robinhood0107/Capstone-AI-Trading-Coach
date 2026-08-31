-- V91 arm은 날짜만 비교해 항상 "오늘보다 뒤"의 세션을 골랐다. 비거래일에는 맞지만 거래일
-- 09:30 전에 arm해도 오늘 세션을 건너뛰므로, 스케줄은 ARMED인데 당일 runtime claim은 없는
-- 상태가 됐다. 이미 적용된 V91 bytes는 바꾸지 않고 현재 KST 시각이 09:30 전이면 열린 당일
-- 세션을, 그 뒤에는 다음 열린 세션을 고르도록 함수를 forward-repair한다.

CREATE OR REPLACE FUNCTION public.p1_arm_automation_v2(
  p_user_id text,p_account_id text,p_policy_id text,p_expected_policy_version integer,
  p_expected_control_version integer,p_scope_hash text,p_request_hash text
)
RETURNS TABLE(result_json text,replayed boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_arm_automation_v2$
DECLARE prior public.automation_control_idempotency%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE policy_row public.automation_policy_versions%ROWTYPE;
DECLARE gate_row public.automation_activation_gate%ROWTYPE;
DECLARE risk_projection jsonb;
DECLARE risk_digest text;
DECLARE legacy_digest text;
DECLARE current_version integer;
DECLARE next_version integer;
DECLARE local_now timestamp;
DECLARE target_session date;
DECLARE new_schedule_id text;
DECLARE projection jsonb;
DECLARE active_receipt text;
BEGIN
  PERFORM public.assert_actor_rls_scope_exact_v1(p_user_id,'ARM_AUTOMATION','AUTOMATION',p_user_id,p_request_hash);
  IF p_scope_hash!~'^sha256:[0-9a-f]{64}$' OR p_request_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_account_id!~'^acct_[0-9a-f]{32}$' OR p_policy_id!~'^auto_pol_[0-9a-f]{32}$'
     OR p_expected_policy_version<1 OR p_expected_control_version<1 THEN
    RAISE EXCEPTION 'automation v2 arm input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('automation-control:'||p_user_id,91));
  PERFORM pg_advisory_xact_lock(hashtextextended(p_scope_hash,91));
  SELECT * INTO prior FROM public.automation_control_idempotency WHERE scope_hash=p_scope_hash;
  IF FOUND THEN
    IF prior.user_id<>p_user_id OR prior.operation<>'ARM' OR prior.request_hash<>p_request_hash THEN
      RAISE EXCEPTION 'automation idempotency conflict' USING ERRCODE='23505';
    END IF;
    result_json:=prior.result_json::text;replayed:=true;RETURN NEXT;RETURN;
  END IF;
  SELECT * INTO policy_row FROM public.automation_policy_versions
  WHERE user_id=p_user_id AND policy_id=p_policy_id AND version=p_expected_policy_version;
  IF NOT FOUND OR EXISTS (
    SELECT 1 FROM public.automation_policy_versions newer
    WHERE newer.user_id=p_user_id AND newer.version>p_expected_policy_version
  ) THEN RAISE EXCEPTION 'automation policy version conflict' USING ERRCODE='40001'; END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.principles item
    WHERE item.user_id=p_user_id AND item.principle_id=policy_row.principle_id
      AND item.status='ACTIVE' AND item.current_version=policy_row.principle_version
  ) THEN RAISE EXCEPTION 'automation principle version drift' USING ERRCODE='40001'; END IF;
  SELECT * INTO control_row FROM public.automation_control WHERE user_id=p_user_id FOR UPDATE;
  current_version:=CASE WHEN FOUND THEN control_row.version ELSE 1 END;
  IF current_version<>p_expected_control_version OR (control_row.user_id IS NOT NULL AND control_row.control_state<>'DISARMED')
     OR current_version=2147483647 THEN
    RAISE EXCEPTION 'automation control version conflict' USING ERRCODE='40001';
  END IF;
  IF COALESCE((SELECT active FROM public.risk_kill_switch WHERE kill_switch_id='GLOBAL'),true) THEN
    RAISE EXCEPTION 'automation kill switch active' USING ERRCODE='40001';
  END IF;
  SELECT * INTO gate_row FROM public.automation_activation_gate WHERE user_id=p_user_id;
  IF NOT FOUND OR gate_row.certification_status<>'VALID' OR NOT gate_row.clean_release_binding
     OR NOT gate_row.real_team_b_pointer_active OR gate_row.team_b_integrity_receipt_sha256 IS NULL THEN
    RAISE EXCEPTION 'automation activation gate closed' USING ERRCODE='40001';
  END IF;
  IF (SELECT count(*) FROM public.current_p1_return_signal_pointer)<>31
     OR (SELECT count(DISTINCT bundle_sha256) FROM public.current_p1_return_signal_pointer)<>1 THEN
    RAISE EXCEPTION 'automation real Team B pointer unavailable' USING ERRCODE='40001';
  END IF;
  SELECT bundle.packet_sha256 INTO active_receipt FROM public.p1_return_artifact_bundle bundle
  WHERE bundle.bundle_sha256=(SELECT min(bundle_sha256) FROM public.current_p1_return_signal_pointer)
    AND bundle.real_team_b AND bundle.mock_runtime_eligible;
  IF active_receipt IS DISTINCT FROM gate_row.team_b_integrity_receipt_sha256 THEN
    RAISE EXCEPTION 'automation Team B receipt drift' USING ERRCODE='40001';
  END IF;
  risk_projection:=public.p1_automation_risk_balance_projection_v2(p_user_id,p_account_id);
  IF risk_projection IS NULL THEN
    RAISE EXCEPTION 'BLOCKED_INCOMPLETE_RISK_BALANCE' USING ERRCODE='P1B01';
  END IF;
  risk_digest:=encode(public.digest(convert_to(risk_projection::text,'UTF8'),'sha256'),'hex');
  legacy_digest:=public.p1_automation_account_digest_v1(p_user_id,'KIS_MOCK',p_account_id);
  local_now:=statement_timestamp() AT TIME ZONE 'Asia/Seoul';
  SELECT session_date INTO target_session FROM public.trading_sessions
  WHERE exchange_mic='XKRX' AND is_open
    AND (
      session_date>local_now::date
      OR (session_date=local_now::date AND local_now::time<time '09:30')
    )
  ORDER BY session_date LIMIT 1;
  IF target_session IS NULL THEN RAISE EXCEPTION 'automation next XKRX session unavailable' USING ERRCODE='40001'; END IF;
  next_version:=current_version+1;
  INSERT INTO public.automation_control(
    user_id,control_state,version,brokerage_mode,account_id,principle_id,strategy_id,
    baseline_account_digest,certification_status,kill_switch_active,created_at,updated_at,
    policy_id,policy_version,principle_version_id,principle_version,
    team_b_integrity_receipt_sha256_v2,initial_account_digest_v2,expected_account_digest_v2,
    expected_account_projection_v2
  ) VALUES (
    p_user_id,'ARMED',next_version,'KIS_MOCK',p_account_id,policy_row.principle_id,'strategy_rule_lstm_v1',
    legacy_digest,'VALID',false,statement_timestamp(),statement_timestamp(),p_policy_id,
    policy_row.version,policy_row.principle_version_id,policy_row.principle_version,active_receipt,
    risk_digest,risk_digest,risk_projection
  ) ON CONFLICT (user_id) DO UPDATE SET
    control_state='ARMED',version=excluded.version,brokerage_mode='KIS_MOCK',account_id=excluded.account_id,
    principle_id=excluded.principle_id,strategy_id=excluded.strategy_id,
    baseline_account_digest=excluded.baseline_account_digest,certification_status='VALID',
    kill_switch_active=false,updated_at=excluded.updated_at,policy_id=excluded.policy_id,
    policy_version=excluded.policy_version,principle_version_id=excluded.principle_version_id,
    principle_version=excluded.principle_version,
    team_b_integrity_receipt_sha256_v2=excluded.team_b_integrity_receipt_sha256_v2,
    initial_account_digest_v2=excluded.initial_account_digest_v2,
    expected_account_digest_v2=excluded.expected_account_digest_v2,
    expected_account_projection_v2=excluded.expected_account_projection_v2;
  new_schedule_id:='auto_sched_'||substr(encode(public.digest(
    convert_to(p_user_id||':'||target_session::text,'UTF8'),'sha256'),'hex'),1,32);
  INSERT INTO public.automation_runtime_schedule(
    schedule_id,user_id,session_date,control_version,schedule_state,run_at,created_at,updated_at
  ) VALUES (
    new_schedule_id,p_user_id,target_session,next_version,'ARMED',
    (target_session+time '09:30') AT TIME ZONE 'Asia/Seoul',statement_timestamp(),statement_timestamp()
  ) ON CONFLICT (user_id,session_date) DO UPDATE SET
    control_version=excluded.control_version,schedule_state='ARMED',run_at=excluded.run_at,updated_at=excluded.updated_at;
  INSERT INTO public.automation_account_lineage(
    lineage_id,user_id,run_id,sequence,reason,prior_digest,next_digest,order_id,
    filled_quantity,average_fill_price_krw,occurred_at
  ) VALUES (
    'auto_acl_'||substr(encode(public.digest(convert_to(p_user_id||':'||next_version::text||':ARM_BASELINE','UTF8'),'sha256'),'hex'),1,32),
    p_user_id,NULL,COALESCE((SELECT max(sequence)+1 FROM public.automation_account_lineage WHERE user_id=p_user_id),1),
    'ARM_BASELINE',NULL,risk_digest,NULL,NULL,NULL,statement_timestamp()
  );
  projection:=jsonb_build_object(
    'blocker',NULL,'brokerageMode','KIS_MOCK','certificationStatus','VALID',
    'contractId','automation-status.v2','controlState','ARMED','controlVersion',next_version,
    'killSwitchActive',false,'nextSessionDate',target_session,'openPositionCount',0,
    'policyId',p_policy_id,'policyVersion',policy_row.version,'projectionState','ARMED',
    'riskBalanceStatus','COMPLETE'
  );
  INSERT INTO public.automation_control_idempotency(
    scope_hash,user_id,operation,request_hash,control_version,result_json
  ) VALUES (p_scope_hash,p_user_id,'ARM',p_request_hash,next_version,projection);
  result_json:=projection::text;replayed:=false;RETURN NEXT;
END
$p1_arm_automation_v2$;
