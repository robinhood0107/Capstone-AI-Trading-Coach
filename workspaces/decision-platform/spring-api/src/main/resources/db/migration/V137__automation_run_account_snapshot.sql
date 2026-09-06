SET LOCAL row_security = on;
-- 운용 실행도 생성 시점 계좌를 소유한다. 현재 계좌를 과거 실행에 임의로 대입하지 않는다.
ALTER TABLE public.automation_runs ADD COLUMN account_id text
 CHECK(account_id IS NULL OR account_id~'^acct_[A-Za-z0-9_-]{8,96}$');

-- 이미 있는 체결 lineage와 포지션이 한 계좌로 일치하는 실행만 복원한다.
-- migration 로그인에만 이 transaction의 근거 복원 권한을 준다. runtime session에는 적용되지 않는다.
CREATE POLICY v137_run_recovery ON public.automation_runs TO flyway
 USING(session_user='flyway') WITH CHECK(session_user='flyway');
CREATE POLICY v137_position_recovery ON public.automation_positions FOR SELECT TO flyway USING(session_user='flyway');
CREATE POLICY v137_lineage_recovery ON public.automation_account_lineage FOR SELECT TO flyway USING(session_user='flyway');
WITH anchors AS (
 SELECT lineage.run_id,min(position.account_id) AS account_id
 FROM public.automation_account_lineage lineage
 JOIN public.automation_positions position ON position.entry_order_id=lineage.order_id
  AND position.user_id=lineage.user_id
  AND position.entry_filled_quantity=lineage.filled_quantity
  AND position.entry_average_fill_price_krw=lineage.average_fill_price_krw
 WHERE lineage.run_id IS NOT NULL
 GROUP BY lineage.run_id HAVING count(DISTINCT position.account_id)=1
)
UPDATE public.automation_runs run SET account_id=anchors.account_id
FROM anchors WHERE run.run_id=anchors.run_id AND run.account_id IS NULL;
DROP POLICY v137_run_recovery ON public.automation_runs;
DROP POLICY v137_position_recovery ON public.automation_positions;
DROP POLICY v137_lineage_recovery ON public.automation_account_lineage;


CREATE FUNCTION public.p1_snapshot_run_account() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $f$
DECLARE selected_account text;
BEGIN
 IF NEW.brokerage_mode='KIS_MOCK' THEN
  SELECT control.account_id INTO selected_account FROM public.automation_control control
  WHERE control.user_id=NEW.user_id AND control.brokerage_mode=NEW.brokerage_mode;
  IF NEW.account_id IS NOT NULL AND NEW.account_id IS DISTINCT FROM selected_account THEN
   RAISE EXCEPTION 'automation run account mismatch' USING ERRCODE='42501';
  END IF;
  NEW.account_id:=selected_account;
 END IF;
 RETURN NEW;
END $f$;
ALTER FUNCTION public.p1_snapshot_run_account() OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_snapshot_run_account() FROM PUBLIC;
CREATE TRIGGER p1_run_account_snapshot BEFORE INSERT ON public.automation_runs
 FOR EACH ROW EXECUTE FUNCTION public.p1_snapshot_run_account();
CREATE INDEX automation_run_owner_account_time ON public.automation_runs(user_id,account_id,updated_at DESC,run_id DESC);

-- UI에는 일회성 준비 타이머가 아니라 상주 runtime의 실제 다음 평가 경계를 표시한다.
CREATE OR REPLACE FUNCTION public.p1_read_automation_schedule_time_v1(p_user_id text) RETURNS timestamptz
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $f$
BEGIN
 IF session_user<>'decision_app' OR current_setting('app.actor_user_id',true) IS DISTINCT FROM p_user_id THEN
  RAISE EXCEPTION 'schedule owner scope denied' USING ERRCODE='42501'; END IF;
 RETURN (SELECT min((schedule.session_date+time '09:30') AT TIME ZONE 'Asia/Seoul')
   FROM public.automation_runtime_schedule schedule JOIN public.automation_control control USING(user_id)
   WHERE schedule.user_id=p_user_id AND schedule.schedule_state='ARMED' AND control.control_state='ARMED'
     AND schedule.control_version=control.version
     AND (schedule.session_date+time '09:30') AT TIME ZONE 'Asia/Seoul'>statement_timestamp());
END $f$;
