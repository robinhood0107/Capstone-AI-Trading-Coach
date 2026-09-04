CREATE OR REPLACE FUNCTION public.p1_automation_runtime_readiness_v1(
  p_user_id text, p_target_session date
)
RETURNS TABLE(
  control_configured boolean, certification_valid boolean, release_source_bound boolean,
  real_team_b_ready boolean, principle_current boolean, kill_switch_inactive boolean,
  account_baseline_matches boolean, unresolved_state_clear boolean, target_available boolean,
  current_control_version integer, all_ready boolean
)
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
  IF control_configured THEN
    observed_digest:=public.p1_automation_runtime_account_digest_v1(p_user_id,control_row.account_id);
  END IF;
  account_baseline_matches:=observed_digest IS NOT NULL
    AND observed_digest=control_row.baseline_account_digest;
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

ALTER FUNCTION public.p1_automation_runtime_readiness_v1(text,date) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_automation_runtime_readiness_v1(text,date)
  FROM PUBLIC,decision_app,decision_worker,decision_replay,decision_replay_authorizer;
GRANT EXECUTE ON FUNCTION public.p1_automation_runtime_readiness_v1(text,date)
  TO decision_automation_runtime;

