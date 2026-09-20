-- 스케줄은 "직전 세션이 COMPLETED 여야 다음을 ARM" 하는 연쇄다(V90 roll_schedule).
-- runtime 이 며칠 멈추면 그날 ARMED 행이 실행되지 않은 채 남고, 그 행은 영원히
-- COMPLETED 가 되지 않으므로 연쇄가 끊겨 **사람이 DB 를 만지기 전까지 자동 운용이
-- 돌아오지 않는다.** 2026-09-10 이 그 상태로 남아 09-11~09-14 가 통째로 비었다.
--
-- 지나간 세션은 이제 claim 할 수 없다(claim 은 당일만 본다). 그러니 "놓친 세션"으로
-- 명시해 마감하고 연쇄를 다시 잇는다. 주문·체결 기록은 건드리지 않는다 - 그 세션에는
-- 애초에 run 이 없었다.
SET LOCAL row_security = on;

CREATE OR REPLACE FUNCTION public.p1_settle_missed_automation_schedules_v1(
  p_user_id text, p_today date
) RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_settle_missed_automation_schedules_v1$
DECLARE settled integer:=0;
DECLARE item record;
BEGIN
  IF session_user<>'decision_automation_runtime' OR p_user_id IS NULL OR p_today IS NULL THEN
    RAISE EXCEPTION 'automation settle input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_owner_user_id',p_user_id,true);
  PERFORM pg_advisory_xact_lock(hashtextextended('automation-control:'||p_user_id,90));

  FOR item IN
    SELECT schedule_id, session_date, control_version
    FROM public.automation_runtime_schedule
    WHERE user_id=p_user_id AND session_date<p_today
      AND schedule_state IN ('ARMED','CLAIMED')
    ORDER BY session_date
    FOR UPDATE
  LOOP
    -- 그 세션에 실제 run 이 있었다면 그 결과가 상태를 정한다. 여기서 덮지 않는다.
    IF EXISTS (
      SELECT 1 FROM public.automation_runs run
      WHERE run.user_id=p_user_id AND run.session_date=item.session_date
    ) THEN
      CONTINUE;
    END IF;
    UPDATE public.automation_runtime_schedule
    SET schedule_state='COMPLETED', updated_at=statement_timestamp()
    WHERE schedule_id=item.schedule_id;
    INSERT INTO public.automation_runtime_events(
      event_id,user_id,session_date,run_id,event_type,payload_hash,sanitized,occurred_at
    ) VALUES (
      'auto_rte_'||substr(encode(public.digest(convert_to(
        p_user_id||':'||item.session_date::text||':SESSION_MISSED','UTF8'),'sha256'),'hex'),1,32),
      p_user_id,item.session_date,NULL,'SCHEDULE_ARMED',
      encode(public.digest(convert_to(
        item.schedule_id||':'||item.control_version::text||':MISSED','UTF8'),'sha256'),'hex'),
      true,statement_timestamp()
    ) ON CONFLICT (event_id) DO NOTHING;
    settled:=settled+1;
  END LOOP;
  RETURN settled;
END
$p1_settle_missed_automation_schedules_v1$;

ALTER FUNCTION public.p1_settle_missed_automation_schedules_v1(text,date) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_settle_missed_automation_schedules_v1(text,date)
  FROM PUBLIC, decision_app;
GRANT EXECUTE ON FUNCTION public.p1_settle_missed_automation_schedules_v1(text,date)
  TO decision_automation_runtime;
