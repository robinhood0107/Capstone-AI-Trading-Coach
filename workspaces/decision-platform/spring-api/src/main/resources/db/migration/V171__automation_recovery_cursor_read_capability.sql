-- V169 의 복구는 프로덕션에서 첫 단계 직후 멈추게 돼 있었다.
--
-- `_settle_and_arm` 은 놓친 세션을 마감한 뒤 `last_completed_session` 과
-- `control_version` 을 읽어 한 칸씩 굴린다. 그런데 그 두 메서드는 테이블을 **직접
-- SELECT** 하고, `decision_automation_runtime` 역할에는 그 권한이 없다(최소권한 원칙대로
-- 모든 읽기가 함수를 거치게 돼 있다). 실측:
--
--   has_table_privilege('decision_automation_runtime','automation_runtime_schedule','SELECT') = false
--   has_table_privilege('decision_automation_runtime','automation_control','SELECT')          = false
--
-- 그래서 2026-09-15 09:30 에 복구는 `settled=1` 을 찍고 곧바로 InsufficientPrivilege 로
-- 끊겼을 것이다. 회귀 테스트가 못 잡은 이유는 대역이 그 두 메서드를 파이썬으로 구현해
-- 권한 경계를 통과해 버렸기 때문이다 - V170 절의 교훈과 같은 부류다.
--
-- 다른 runtime 읽기와 같은 모양으로 맞춘다: SECURITY DEFINER 함수 하나가 두 값을 함께
-- 돌려주고, 실행 권한은 자동운용 runtime 에만 준다. 쓰기는 없다.
SET LOCAL row_security = on;

CREATE OR REPLACE FUNCTION public.p1_read_automation_recovery_cursor_v1(
  p_user_id text
) RETURNS TABLE(last_completed_session date, control_version integer)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_read_automation_recovery_cursor_v1$
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_user_id IS NULL THEN
    RAISE EXCEPTION 'automation recovery cursor input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_owner_user_id',p_user_id,true);
  RETURN QUERY
  SELECT
    (SELECT max(schedule.session_date)
     FROM public.automation_runtime_schedule schedule
     WHERE schedule.user_id=p_user_id AND schedule.schedule_state='COMPLETED'),
    (SELECT control.version
     FROM public.automation_control control
     WHERE control.user_id=p_user_id);
END
$p1_read_automation_recovery_cursor_v1$;

ALTER FUNCTION public.p1_read_automation_recovery_cursor_v1(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_read_automation_recovery_cursor_v1(text)
  FROM PUBLIC, decision_app;
GRANT EXECUTE ON FUNCTION public.p1_read_automation_recovery_cursor_v1(text)
  TO decision_automation_runtime;
