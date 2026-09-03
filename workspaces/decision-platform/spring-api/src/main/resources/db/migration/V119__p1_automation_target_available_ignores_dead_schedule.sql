-- P1 자동운용: target_available 이 claim 불가능한 예약 행을 점유로 세지 않는다.
--
-- 무엇이 막혔나. control 이 DISARMED 인데 그 세션의 예약 행이 ARMED 로 남으면 그 세션은
-- 재arm 이 구조적으로 불가능해진다.
--
--   readiness  target_available := NOT EXISTS (ARMED/CLAIMED 행)        -> false
--   start      readiness.all_ready 가 false 이므로 'readiness gate closed'
--   start 의   ARMED/CLAIMED 행 replay 지름길은 control 이 ARMED 일 때만 탄다
--
-- 즉 어느 경로로도 그 세션을 다시 켤 수 없다. 실측으로 이 상태에 도달했다 - 예약 행이
-- control_version 22 / ARMED 인데 control 은 version 23 / DISARMED 였고, `mock start` 가
-- TARGET_SESSION_AVAILABLE=FAIL 로 닫혔다. roll_schedule 은 control 이 ARMED 여야 도므로
-- (V90) 그 상태로는 다음 거래일이 조용히 사라진다.
--
-- 왜 과제한인가. 그 행은 claim 이 이미 거부하도록 되어 있다 -
-- p1_claim_automation_session_v1 이 control_state='ARMED' 와
-- control_row.version = schedule_row.control_version 을 함께 요구한다. 버전이 어긋난 행은
-- 발화할 수 없는 죽은 행이다. 그런데 readiness 는 그 죽은 행을 "세션이 이미 점유됨"으로 세어
-- 정상 경로를 닫는다. 막는 근거가 claim 경로의 사실과 어긋난다.
--
-- 무엇을 좁혔나. target_available 절에 control_version 일치 조건 한 줄만 더한다. 이 게이트의
-- 안전 목적 - 살아 있는 세션을 두 번 예약하지 않는다 - 은 그대로 남는다. 현재 control
-- 버전의 ARMED/CLAIMED 행은 여전히 점유로 센다. 달라지는 것은 발화할 수 없는 행을 더 이상
-- 점유로 세지 않는 것뿐이다.
--
-- start 가 그 뒤를 이미 처리한다. control 을 다음 버전으로 올리고
-- ON CONFLICT (user_id,session_date) DO UPDATE SET control_version=excluded.control_version,
-- schedule_state='ARMED' 로 낡은 행을 덮어쓴다. 고아 행이 남지 않는다.
--
-- 이 파일은 기존 함수를 그대로 복사해 target_available 절만 바꾼 것이다. CREATE OR REPLACE
-- 이므로 권한·STABLE·SECURITY DEFINER·search_path 는 그대로 유지된다.

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
  control_configured:=control_row.user_id IS NOT NULL AND control_row.control_state='DISARMED'
    AND control_row.brokerage_mode='KIS_MOCK' AND control_row.baseline_account_digest~'^[0-9a-f]{64}$';
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
  -- 발화할 수 있는 행만 점유로 센다. claim 이 control_version 일치를 요구하므로
  -- 버전이 어긋난 ARMED/CLAIMED 행은 죽은 행이고, 그것으로 재arm 을 막으면 그 세션이
  -- 영구히 잠긴다. 현재 버전의 행은 그대로 점유로 센다.
  target_available:=NOT EXISTS (
    SELECT 1 FROM public.automation_runtime_schedule schedule
    WHERE schedule.user_id=p_user_id AND schedule.session_date=p_target_session
      AND schedule.schedule_state IN ('ARMED','CLAIMED')
      AND schedule.control_version=control_row.version
  );
  current_control_version:=COALESCE(control_row.version,1);
  all_ready:=control_configured AND certification_valid AND release_source_bound
    AND real_team_b_ready AND principle_current AND kill_switch_inactive
    AND account_baseline_matches AND unresolved_state_clear AND target_available;
  RETURN NEXT;
END
$function$;
