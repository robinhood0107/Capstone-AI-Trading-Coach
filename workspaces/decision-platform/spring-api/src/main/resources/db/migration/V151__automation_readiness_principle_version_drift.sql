-- Readiness must notice that the armed principle version is no longer the current one.
--
-- 무엇이 어긋났나. `automation_control` 은 ARM 시점에 `principle_version_id` 와
-- `principle_version` 을 스냅샷으로 고정한다. 그런데 readiness 의 `principle_current` 는
-- `principles.status='ACTIVE'` 만 확인하고 버전을 보지 않았다. 그래서 ARMED 상태에서
-- 사용자가 원칙을 저장하면(그것을 막는 잠금은 프론트에도 서버에도 없다) readiness 는
-- 계속 참이고, 세션이 옛 스냅샷으로 시작한다. 사이징 한도는 고정된 버전으로 읽고
-- (V138) 위험판정은 최신 버전으로 읽으므로(V87 의 read_active_owned_principle_snapshot)
-- 주문 제출 전이에서 `V141` 의 결속 검사가 두 버전이 다르다고 거부하고, tick 이 20초 ×
-- 15회 재시도한 뒤 포기한다. 사용자에게는 "원칙을 바꿨더니 자동매매가 죽었다"로 보이고
-- 어느 화면에도 이유가 나오지 않는다.
--
-- 왜 이 방식인가. 소유자가 고른 의미는 "원칙 변경은 허용하고 다음 세션부터 적용한다"다.
-- 그러면 진행 중 세션이 옛 스냅샷을 끝까지 쓰는 것이 맞고, 드리프트가 생긴 세션은
-- **주문 직전에 죽는 대신 시작 전에 깨끗하게 건너뛰어야** 한다. 버전 일치를 readiness 에
-- 넣으면 그 지점이 앞으로 당겨진다 - `automation.py` 가 이미 `principle_active_current` 가
-- 거짓일 때 `SKIPPED_DATA_UNAVAILABLE` 로 닫는다.
--
-- 회복 경로. ARM 이 이미 같은 드리프트를 거부한다(`V133` 의 'automation principle version
-- drift'). 그래서 정지 -> 정책 재저장(그 순간의 최신 원칙을 다시 스냅샷) -> 재무장이
-- 정상 경로이고, 정지 버튼은 소유자 화면에 있다.
--
-- 무엇을 바꾸지 않는가. 원칙 편집을 막지 않는다. 사이징과 판정이 읽는 버전을 바꾸지 않는다.
-- 새 상태, 새 컬럼, 새 HTTP operation, OpenAPI 변경은 없다. 본문은 V145 와 같고
-- `principle_current` 한 절만 다르다.

SET LOCAL row_security = on;
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
  -- ARM 이 고정한 버전이 아직 현재 버전이어야 한다. DISARMED 이면 아직 고정한 것이 없으므로
  -- 활성 여부만 본다 - 그러지 않으면 첫 무장 전에 readiness 가 영구히 거짓이 된다.
  principle_current:=control_row.user_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM public.principles principle
    WHERE principle.user_id=p_user_id AND principle.principle_id=control_row.principle_id
      AND principle.status='ACTIVE'
      AND (
        control_row.control_state<>'ARMED'
        OR control_row.principle_version IS NULL
        OR principle.current_version=control_row.principle_version
      )
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
