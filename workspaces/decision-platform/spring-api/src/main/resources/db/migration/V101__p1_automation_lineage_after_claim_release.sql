-- V94가 만든 계보 전진은 실제 PostgreSQL에서 한 번도 성공한 적이 없다.
--
-- PersistentAutomationRunner.run_tick은 체크포인트를 먼저 durable하게 밀고(`advance`) 그
-- 뒤에 계보를 전진시킨다. 그런데 체크포인트 전진 함수는 종결 상태(COMPLETED 포함)에서
-- automation_runtime_claim을 곧바로 RELEASED로 반납한다. 그래서 계보 함수가 요구하는
-- claim_state='ACTIVE'는 체결이 확정된 바로 그 순간에 항상 거짓이고, 호출은 늘 42501
-- 'automation claim unavailable'로 닫혔다.
--
-- 결과는 V94가 막으려던 바로 그 고장이다. 기대 계좌 투영이 전진하지 않으므로 다음 세션이
-- 자기 체결을 외부 드리프트로 보고 ACCOUNT_DRIFT로 HALT하고, HALT는 stop으로 풀리지 않는다.
--
-- 호출 순서를 뒤집지 않는다. 계보는 체결이 durable해진 뒤에 남는 것이 맞다. 대신 반납된
-- claim을 좁게 허용한다. 조건은 시계가 아니라 기록이다 - run이 COMPLETED이고, 그 run의
-- 예약이 같은 주문 번호로 같은 수량과 같은 평균가를 이미 기록하고 있어야 한다. 토큰 해시
-- 소지는 그대로 요구하므로 다른 런타임이 끼어들 수 있는 창은 열리지 않는다.

CREATE OR REPLACE FUNCTION public.p1_advance_automation_account_lineage_v3(
  p_run_id text,p_claim_token_hash text,p_reason text,p_next_projection jsonb,
  p_next_digest text,p_order_id text,p_filled_quantity bigint,p_average_fill_price_krw bigint
)
RETURNS integer
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $p1_advance_automation_account_lineage_v3$
DECLARE claim_row public.automation_runtime_claim%ROWTYPE;
DECLARE control_row public.automation_control%ROWTYPE;
DECLARE existing_row public.automation_account_lineage%ROWTYPE;
DECLARE run_row public.automation_runs%ROWTYPE;
DECLARE prior_digest_value text;
DECLARE sequence_value integer;
BEGIN
  IF session_user<>'decision_automation_runtime'
     OR p_run_id!~'^auto_run_[0-9a-f]{32}$'
     OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$'
     OR p_reason NOT IN ('BUY_FILL','SELL_FILL')
     OR p_next_digest!~'^[0-9a-f]{64}$'
     OR p_order_id!~'^ord_mock_[0-9a-f]{32}$'
     OR p_filled_quantity<=0 OR p_average_fill_price_krw<=0
     OR NOT public.p1_automation_structural_projection_valid_v2(p_next_projection) THEN
    RAISE EXCEPTION 'automation account lineage input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim_row FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM set_config('app.automation_claim_scan','0',true);
    RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.automation_claim_scan','0',true);
  PERFORM set_config('app.automation_owner_user_id',claim_row.user_id,true);
  -- 반납된 claim은 자기 run의 종결 직후에만, 그리고 durable state가 그 체결을 증언할 때만
  -- 받아들인다. 시계나 유예창이 아니라 기록으로 판정한다.
  IF claim_row.claim_state<>'ACTIVE' THEN
    SELECT * INTO run_row FROM public.automation_runs WHERE run_id=p_run_id;
    IF NOT FOUND OR run_row.user_id<>claim_row.user_id OR run_row.state<>'COMPLETED' THEN
      RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501';
    END IF;
    PERFORM 1 FROM public.automation_order_reservations
    WHERE run_id=p_run_id AND user_id=claim_row.user_id AND order_id=p_order_id
      AND filled_quantity=p_filled_quantity AND average_fill_price_krw=p_average_fill_price_krw;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'automation claim unavailable' USING ERRCODE='42501';
    END IF;
  END IF;
  SELECT * INTO control_row FROM public.automation_control
  WHERE user_id=claim_row.user_id FOR UPDATE;
  IF NOT FOUND OR control_row.account_id<>p_next_projection->>'accountId' THEN
    RAISE EXCEPTION 'automation account lineage scope denied' USING ERRCODE='42501';
  END IF;
  -- tick 재시도가 lineage를 두 번 밀지 않게 한다. 같은 run과 주문이면 순수 no-op이다.
  SELECT * INTO existing_row FROM public.automation_account_lineage
  WHERE user_id=claim_row.user_id AND run_id=p_run_id AND order_id=p_order_id AND reason=p_reason;
  IF FOUND THEN
    IF existing_row.next_digest<>p_next_digest THEN
      RAISE EXCEPTION 'automation account lineage conflict' USING ERRCODE='23505';
    END IF;
    RETURN existing_row.sequence;
  END IF;
  SELECT next_digest INTO prior_digest_value FROM public.automation_account_lineage
  WHERE user_id=claim_row.user_id ORDER BY sequence DESC LIMIT 1;
  IF prior_digest_value IS NULL THEN
    RAISE EXCEPTION 'automation account lineage baseline missing' USING ERRCODE='40001';
  END IF;
  SELECT COALESCE(max(sequence),0)+1 INTO sequence_value
  FROM public.automation_account_lineage WHERE user_id=claim_row.user_id;
  INSERT INTO public.automation_account_lineage(
    lineage_id,user_id,run_id,sequence,reason,prior_digest,next_digest,order_id,
    filled_quantity,average_fill_price_krw,occurred_at
  ) VALUES (
    'auto_acl_'||substr(encode(public.digest(convert_to(
      claim_row.user_id||':'||sequence_value::text||':'||p_reason,'UTF8'),'sha256'),'hex'),1,32),
    claim_row.user_id,p_run_id,sequence_value,p_reason,prior_digest_value,p_next_digest,p_order_id,
    p_filled_quantity,p_average_fill_price_krw,statement_timestamp()
  );
  -- 기대 투영만 전진시킨다. expected_account_digest_v2는 portfolio_balance_observations를
  -- 따라가는 별개 축이므로 여기서 건드리면 SQL 쪽 accountDigestMatches가 도리어 깨진다.
  UPDATE public.automation_control
  SET expected_account_projection_v2=p_next_projection,updated_at=statement_timestamp()
  WHERE user_id=claim_row.user_id;
  RETURN sequence_value;
END
$p1_advance_automation_account_lineage_v3$;

ALTER FUNCTION public.p1_advance_automation_account_lineage_v3(
  text,text,text,jsonb,text,text,bigint,bigint
) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_advance_automation_account_lineage_v3(
  text,text,text,jsonb,text,text,bigint,bigint
) FROM PUBLIC,decision_app,decision_worker,decision_replay,decision_replay_authorizer;
GRANT EXECUTE ON FUNCTION public.p1_advance_automation_account_lineage_v3(
  text,text,text,jsonb,text,text,bigint,bigint
) TO decision_automation_runtime;
