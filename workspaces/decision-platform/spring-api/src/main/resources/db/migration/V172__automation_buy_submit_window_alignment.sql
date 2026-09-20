-- 포트폴리오 경로의 매수가 구조적으로 불가능했다.
--
-- V163:427-429 의 `p1_begin_automation_portfolio_execution_v2` 는 BUY 제출을 **09:40 이후
-- 전부 거부**한다(ERRCODE 40001). 그런데 Python 은 `_DECISION_TIMES = (09:45, 11:00, 14:00)`
-- (`automation_runtime.py:72`) 에 진입한다 - **세 시점 전부 그 마감 이후**다. 즉 세 결정
-- 시점 중 무엇도 주문을 낼 수 없었다. 2026-09-15 에 자동운용이 066570 을 BUY 로 고르고도
-- 무주문으로 끝난 여러 원인 중 하나다.
--
-- 09:40 단일 진입 모델에서 3시점 모델로 옮기다 만 자국이다. `_SUBMIT_DEADLINE = time(9,40)`
-- (`automation_runtime.py:61`) 은 Python 에서 참조 0 으로 죽었지만 SQL 에서는 살아 있었다.
-- 이제 Python 의 `_BUY_SUBMIT_DEADLINE` 하나가 진실이고 이 값이 그것과 같아야 한다
-- (`test_automation_sql_alignment.py` 가 고정한다).
--
-- 14:30 인 이유: 마지막 결정 시점(14:00)보다 뒤여야 run 이 제출까지 갈 여유가 있고,
-- 취소 경계(15:20)보다는 충분히 앞이어야 한다 - 15:19 에 낸 매수는 60초 뒤 취소돼 지금과
-- 같은 미체결 사망을 반대쪽 끝에서 재현한다. 매도 마감 15:20 은 그대로다.
--
-- 본문은 V163:398-437 을 **프로그램으로 떠서** 시간 리터럴 한 줄만 바꾼 것이다. 나머지
-- 일곱 개 가드(역할/ordinal/멱등키 정규식, claim FOR UPDATE 창, control ARMED,
-- owner_stop_active, risk_kill_switch, 직전 ordinal 미terminal, 23505 멱등 drift 와 NO_OP
-- 재진입 단락)는 글자 그대로 보존된다.
--
-- CREATE OR REPLACE 를 쓴다. DROP+CREATE 는 V163:707 의 OWNER TO flyway 와 :711-723 의
-- GRANT EXECUTE ... TO decision_automation_runtime 를 조용히 버려 그날 첫 제출부터 42501 이
-- 난다.
SET LOCAL row_security = on;

CREATE OR REPLACE FUNCTION public.p1_begin_automation_portfolio_execution_v2(
  p_run_id text,p_claim_token_hash text,p_ordinal integer,p_idempotency_key_hash text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $begin$
DECLARE claim public.automation_runtime_claim%ROWTYPE;
DECLARE execution public.automation_portfolio_order_executions_v1%ROWTYPE;
DECLARE control public.automation_control%ROWTYPE;
DECLARE local_now timestamp;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_automation_runtime'
     OR p_ordinal NOT BETWEEN 1 AND 3 OR p_idempotency_key_hash!~'^sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'automation portfolio execution input invalid' USING ERRCODE='22023'; END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash
    AND (claim_state='ACTIVE' OR (claim_state='RELEASED'
      AND session_date=(statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date
      AND (statement_timestamp() AT TIME ZONE 'Asia/Seoul')::time<=time '15:20')) FOR UPDATE;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN RAISE EXCEPTION 'automation portfolio claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim.user_id,true);
  SELECT * INTO control FROM public.automation_control WHERE user_id=claim.user_id FOR SHARE;
  SELECT * INTO execution FROM public.automation_portfolio_order_executions_v1
  WHERE run_id=p_run_id AND ordinal=p_ordinal FOR UPDATE;
  local_now:=statement_timestamp() AT TIME ZONE 'Asia/Seoul';
  IF execution.run_id IS NULL OR control.control_state<>'ARMED'
     OR public.owner_stop_active(claim.user_id)
     OR COALESCE((SELECT active FROM public.risk_kill_switch WHERE kill_switch_id='GLOBAL'),true)
     OR EXISTS(SELECT 1 FROM public.automation_portfolio_order_executions_v1 prior
       WHERE prior.run_id=p_run_id AND prior.ordinal<p_ordinal AND prior.state NOT IN ('FILLED','CANCELLED','REJECTED'))
     OR (claim.session_date=local_now::date AND (local_now::time<time '09:30'
       OR local_now::time>CASE WHEN execution.side='BUY' THEN time '14:30' ELSE time '15:20' END)) THEN
    RAISE EXCEPTION 'automation portfolio execution not currently eligible' USING ERRCODE='40001'; END IF;
  IF execution.idempotency_key_hash<>p_idempotency_key_hash THEN
    RAISE EXCEPTION 'automation execution idempotency drift' USING ERRCODE='23505'; END IF;
  IF execution.state IN ('SUBMITTING','PENDING_RECONCILIATION','FILLED','CANCELLED','REJECTED') THEN
    RETURN 'NO_OP'; END IF;
  UPDATE public.automation_portfolio_order_executions_v1 SET state='SUBMITTING',updated_at=statement_timestamp()
  WHERE run_id=p_run_id AND ordinal=p_ordinal;
  RETURN 'SUBMIT';
END $begin$;
