-- 부분체결이 포트폴리오 경로를 통째로 중단시킨다.
--
-- `_terminal_state`(automation_portfolio_runtime.py:324-333) 가 `cumulative == quantity` 가
-- 아니면 예외를 던져 `continue_session` 전체가 중단된다. **이미 체결된 주식이 장부에 들어가지
-- 않는다.** 그런데 이 함수는 처음부터 증분식이다 -
-- `delta_quantity := p_filled_quantity - execution.applied_filled_quantity`, 포지션 생성·갱신도
-- 델타 기반이고 `entry_unfilled_quantity` 슬롯까지 있다. 막고 있던 것은 Python 과 아래 한 줄뿐이다.
--
-- 새 실행 상태(PARTIALLY_FILLED)를 만들지 않는다. 그러면 테이블 CHECK(V161:80)와
-- "한 run 에 열린 주문 하나"를 지키는 partial unique index(V161:94-96)를 재생성해야 하고,
-- 그 사이 두 주문이 동시에 열리는 창이 생긴다. `PENDING_RECONCILIATION` 은 이미
-- "브로커에 살아 있고 결과 미확정"을 뜻하며 부분체결이 정확히 그것이다.
--
-- 본문은 V163:471-627 을 **프로그램으로 떠서** 네 줄만 바꾼 것이다. 줄을 넣거나 빼지 않았고
-- 나머지 가드는 글자 그대로 보존된다. `test_automation_sql_alignment.py` 가 이를 고정한다.
--
--   1) V163:513  early-return 을 **삭제하지 않고 완화**한다. 이 경로가 반복 폴링을 공짜로
--                만드는 멱등 no-op 이다 - 같은 누적을 다시 보고하면 여기서 끝난다.
--   2) V163:556  비terminal 적재에도 orders 의 보존 등식
--                filled+leaves+unfilled_terminated=quantity (V14:73) 를 강제한다.
--                주식을 흘린 영수증을 거절한다. 순수한 강화다.
--   3) V163:576  **가장 중요.** automation_positions 에
--                entry_ordered_quantity=entry_filled_quantity+entry_unfilled_quantity
--                CHECK(V111:96) 가 있어, 현행 `ordered+delta` 는 delta<quantity 인 순간
--                CHECK 위반으로 트랜잭션 전체를 중단시킨다. 회계 흠집이 아니라 차단 결함이다.
--                주문 전체 크기를 실행당 한 번만 계상하고 unfilled 를 유도한다.
--                전체 체결에서는 현행과 수식이 동치라 기존 동작이 바뀌지 않는다.
--   4) V163:617  부분체결을 orders projection 에 드러낸다. 'PARTIALLY_FILLED' 는 V15:26-37
--                CHECK 에 이미 있다.
--
-- search_path 함정: CREATE OR REPLACE 는 owner 와 ACL 은 보존하지만 **SET 절은 새 헤더로
-- 교체한다.** 헤더를 V163 과 바이트 동일하게 두었으므로 V165 가 고친 pgcrypto(digest()) 경로를
-- 파일 끝에서 다시 발행한다. 빠뜨리면 조용히 다시 깨진다.
--
-- 시그니처가 같으므로 V163:709 OWNER, :717 REVOKE, :725 GRANT 가 그대로 살아남는다.
-- DROP FUNCTION 을 쓰지 않는다.
SET LOCAL row_security = on;

CREATE OR REPLACE FUNCTION public.p1_finish_automation_portfolio_execution_v2(
  p_run_id text,p_claim_token_hash text,p_ordinal integer,p_state text,p_order_id text,
  p_provider_order_ref_hash text,p_filled_quantity bigint,p_leaves_quantity bigint,
  p_average_fill_price_krw bigint,p_provider_exec_ref_hash text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $finish$
DECLARE claim public.automation_runtime_claim%ROWTYPE;
DECLARE execution public.automation_portfolio_order_executions_v1%ROWTYPE;
DECLARE stored public.orders%ROWTYPE;
DECLARE fill_count integer;
DECLARE delta_quantity bigint;
DECLARE delta_notional numeric(30,0);
DECLARE delta_price bigint;
DECLARE position public.automation_positions%ROWTYPE;
DECLARE snapshot public.automation_portfolio_session_snapshots_v1%ROWTYPE;
DECLARE policy public.automation_policy_versions%ROWTYPE;
DECLARE new_quantity bigint;
DECLARE new_entry_filled bigint;
DECLARE new_exit_filled bigint;
DECLARE new_entry_average bigint;
DECLARE new_exit_average bigint;
DECLARE realized_delta bigint;
DECLARE expiry date;
DECLARE observation_id text;
DECLARE observation_payload jsonb;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_automation_runtime'
     OR p_ordinal NOT BETWEEN 1 AND 3 OR p_state NOT IN ('PENDING_RECONCILIATION','FILLED','CANCELLED','REJECTED')
     OR p_filled_quantity<0 OR p_leaves_quantity<0
     OR (p_average_fill_price_krw IS NULL)<>(p_filled_quantity=0)
     OR (p_provider_order_ref_hash IS NOT NULL AND p_provider_order_ref_hash!~'^[0-9a-f]{64}$')
     OR (p_provider_exec_ref_hash IS NOT NULL AND p_provider_exec_ref_hash!~'^[0-9a-f]{64}$') THEN
    RAISE EXCEPTION 'automation portfolio outcome invalid' USING ERRCODE='22023'; END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash FOR UPDATE;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN RAISE EXCEPTION 'automation portfolio claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim.user_id,true);
  SELECT * INTO execution FROM public.automation_portfolio_order_executions_v1
  WHERE run_id=p_run_id AND ordinal=p_ordinal FOR UPDATE;
  IF execution.run_id IS NULL OR execution.state NOT IN ('SUBMITTING','PENDING_RECONCILIATION')
     OR p_filled_quantity+p_leaves_quantity>execution.quantity
     OR p_filled_quantity<execution.applied_filled_quantity THEN
    RAISE EXCEPTION 'automation portfolio outcome transition invalid' USING ERRCODE='40001'; END IF;
  IF p_state='PENDING_RECONCILIATION' AND p_filled_quantity<=execution.applied_filled_quantity THEN
    IF p_order_id IS NULL THEN RAISE EXCEPTION 'automation pending order missing' USING ERRCODE='22023'; END IF;
    UPDATE public.automation_portfolio_order_executions_v1 SET state=p_state,
      order_id=COALESCE(order_id,p_order_id),provider_order_ref_hash=COALESCE(provider_order_ref_hash,p_provider_order_ref_hash),
      updated_at=statement_timestamp() WHERE run_id=p_run_id AND ordinal=p_ordinal;
    RETURN 'UPDATED';
  END IF;
  IF p_state='REJECTED' AND p_order_id IS NULL AND p_filled_quantity=0 THEN
    UPDATE public.automation_portfolio_order_executions_v1 SET state='REJECTED',updated_at=statement_timestamp()
    WHERE run_id=p_run_id AND ordinal=p_ordinal;
    RETURN 'UPDATED';
  END IF;
  SELECT * INTO stored FROM public.orders WHERE order_id=p_order_id AND user_id=claim.user_id
    AND account_id=(SELECT account_id FROM public.automation_control WHERE user_id=claim.user_id)
    AND symbol=execution.symbol AND side=execution.side AND quantity=execution.quantity FOR SHARE;
  IF stored.order_id IS NULL OR stored.order_intent_json IS DISTINCT FROM execution.exact_intent_json
     OR stored.provider_order_ref_hash IS DISTINCT FROM COALESCE(execution.provider_order_ref_hash,p_provider_order_ref_hash) THEN
    RAISE EXCEPTION 'automation portfolio order receipt mismatch' USING ERRCODE='40001'; END IF;
  IF p_filled_quantity>0 THEN
    IF p_provider_exec_ref_hash IS NULL THEN
      RAISE EXCEPTION 'automation portfolio fill receipt missing' USING ERRCODE='40001'; END IF;
    observation_payload:=jsonb_build_object('providerExecRefHash',p_provider_exec_ref_hash,
      'fillQuantity',p_filled_quantity-execution.applied_filled_quantity,
      'fillPriceKrw',p_average_fill_price_krw,'cumulativeQuantity',p_filled_quantity,
      'leavesQuantity',p_leaves_quantity,'sourceVersion','kis-mock-automation-portfolio-v1');
    observation_id:='ofo_'||substr(encode(digest(convert_to(stored.order_id||chr(31)||p_provider_exec_ref_hash,'UTF8'),'sha256'),'hex'),1,32);
    INSERT INTO public.order_fill_observations(observation_id,order_id,provider_exec_ref_hash,exec_type,
      fill_quantity,fill_price_krw,cumulative_quantity,leaves_quantity,average_fill_price_krw,
      observed_at,received_at,schema_version,source_version,source_ref,completeness,artifact_hash)
    VALUES(observation_id,stored.order_id,p_provider_exec_ref_hash,
      CASE WHEN p_leaves_quantity=0 THEN 'FILL' ELSE 'PARTIAL_FILL' END,
      p_filled_quantity-execution.applied_filled_quantity,p_average_fill_price_krw,p_filled_quantity,
      p_leaves_quantity,p_average_fill_price_krw,statement_timestamp(),statement_timestamp(),'1',
      'kis-mock-automation-portfolio-v1','automation-portfolio',
      CASE WHEN p_leaves_quantity=0 THEN 'COMPLETE' ELSE 'PARTIAL' END,
      encode(digest(convert_to(observation_payload::text,'UTF8'),'sha256'),'hex'))
    ON CONFLICT(order_id,provider_exec_ref_hash) DO NOTHING;
    SELECT count(*) INTO fill_count FROM public.order_fill_observations observation
    WHERE observation.order_id=stored.order_id AND observation.provider_exec_ref_hash=p_provider_exec_ref_hash
      AND observation.cumulative_quantity=p_filled_quantity AND observation.leaves_quantity=p_leaves_quantity
      AND observation.average_fill_price_krw=p_average_fill_price_krw;
    IF fill_count<>1 THEN RAISE EXCEPTION 'automation portfolio fill receipt mismatch' USING ERRCODE='40001'; END IF;
  END IF;
  IF (p_state='FILLED' AND (p_filled_quantity<>execution.quantity OR p_leaves_quantity<>0)) OR (p_state='PENDING_RECONCILIATION' AND p_filled_quantity+p_leaves_quantity<>execution.quantity) THEN
    RAISE EXCEPTION 'automation portfolio filled quantity mismatch' USING ERRCODE='40001'; END IF;
  delta_quantity:=p_filled_quantity-execution.applied_filled_quantity;
  delta_notional:=p_filled_quantity::numeric*p_average_fill_price_krw::numeric-execution.applied_fill_notional_krw;
  IF delta_quantity>0 THEN
    IF delta_notional<=0 OR delta_notional%delta_quantity<>0 THEN
      RAISE EXCEPTION 'automation portfolio fill delta invalid' USING ERRCODE='40001'; END IF;
    delta_price:=(delta_notional/delta_quantity)::bigint;
    SELECT * INTO snapshot FROM public.automation_portfolio_session_snapshots_v1 WHERE run_id=p_run_id;
    SELECT * INTO policy FROM public.automation_policy_versions item
      WHERE item.policy_id=snapshot.automation_policy_id AND item.version=snapshot.automation_policy_version;
    SELECT * INTO position FROM public.automation_positions item
      WHERE item.user_id=claim.user_id AND item.account_id=stored.account_id
        AND item.symbol=execution.symbol AND item.status IN ('OPEN','EXIT_PENDING') FOR UPDATE;
    IF execution.side='BUY' THEN
      IF FOUND THEN
        new_entry_filled:=position.entry_filled_quantity+delta_quantity;
        new_entry_average:=((position.entry_filled_quantity::numeric*position.entry_average_fill_price_krw
          +delta_notional)/new_entry_filled)::bigint;
        UPDATE public.automation_positions SET quantity=quantity+delta_quantity,
          entry_ordered_quantity=entry_ordered_quantity+CASE WHEN execution.applied_filled_quantity=0 THEN execution.quantity ELSE 0 END,entry_unfilled_quantity=entry_ordered_quantity+CASE WHEN execution.applied_filled_quantity=0 THEN execution.quantity ELSE 0 END-new_entry_filled,
          entry_filled_quantity=new_entry_filled,entry_average_fill_price_krw=new_entry_average,
          peak_price_krw=GREATEST(COALESCE(peak_price_krw,delta_price),delta_price)
        WHERE position_id=position.position_id;
      ELSE
        IF policy.max_holding_sessions>0 THEN
          SELECT session_date INTO expiry FROM public.trading_sessions
          WHERE exchange_mic='XKRX' AND is_open AND session_date>claim.session_date
          ORDER BY session_date OFFSET policy.max_holding_sessions-1 LIMIT 1;
          IF expiry IS NULL THEN RAISE EXCEPTION 'automation position expiry unavailable' USING ERRCODE='40001'; END IF;
        END IF;
        INSERT INTO public.automation_positions(position_id,user_id,account_id,symbol,quantity,entry_session,
          expiry_session,status,bot_owned,short_allowed,created_at,entry_order_id,entry_ordered_quantity,
          entry_filled_quantity,entry_unfilled_quantity,entry_average_fill_price_krw,policy_id,policy_version,
          stop_loss_bps,take_profit_bps,exit_filled_quantity,max_holding_sessions,atr_period,
          atr_multiplier_milli,model_sell_enabled,peak_price_krw,atr_status)
        VALUES('auto_pos_'||substr(encode(digest(convert_to(stored.order_id,'UTF8'),'sha256'),'hex'),1,32),
          claim.user_id,stored.account_id,execution.symbol,delta_quantity,claim.session_date,expiry,'OPEN',true,false,
          statement_timestamp(),stored.order_id,execution.quantity,delta_quantity,
          execution.quantity-delta_quantity,delta_price,policy.policy_id,policy.version,policy.stop_loss_bps,
          policy.take_profit_bps,0,policy.max_holding_sessions,policy.atr_period,policy.atr_multiplier_milli,
          policy.model_sell_enabled,delta_price,'UNAVAILABLE');
      END IF;
    ELSE
      IF NOT FOUND OR position.quantity<delta_quantity THEN
        RAISE EXCEPTION 'automation sell position drift' USING ERRCODE='40001'; END IF;
      new_quantity:=position.quantity-delta_quantity;
      new_exit_filled:=position.exit_filled_quantity+delta_quantity;
      new_exit_average:=((position.exit_filled_quantity::numeric*COALESCE(position.exit_average_fill_price_krw,0)
        +delta_notional)/new_exit_filled)::bigint;
      realized_delta:=(delta_price-position.entry_average_fill_price_krw)*delta_quantity
        -((delta_price+position.entry_average_fill_price_krw)*delta_quantity*35+19999)/20000;
      UPDATE public.automation_positions SET quantity=new_quantity,exit_filled_quantity=new_exit_filled,
        exit_average_fill_price_krw=new_exit_average,
        realized_pnl_krw=COALESCE(realized_pnl_krw,0)+realized_delta,
        status=CASE WHEN new_quantity=0 THEN 'CLOSED' ELSE 'OPEN' END,
        closed_at=CASE WHEN new_quantity=0 THEN statement_timestamp() ELSE NULL END
      WHERE position_id=position.position_id;
    END IF;
  END IF;
  UPDATE public.orders SET status=CASE WHEN p_state='FILLED' THEN 'FILLED' WHEN p_state='PENDING_RECONCILIATION' THEN 'PARTIALLY_FILLED' ELSE p_state END,
    filled_quantity=p_filled_quantity,leaves_quantity=CASE WHEN p_state IN ('FILLED','CANCELLED','REJECTED') THEN 0 ELSE p_leaves_quantity END,
    unfilled_terminated_quantity=CASE WHEN p_state IN ('CANCELLED','REJECTED') THEN quantity-p_filled_quantity ELSE 0 END,
    average_fill_price_krw=p_average_fill_price_krw,reconciliation_status='MATCHED',
    reconciled_at=statement_timestamp(),updated_at=statement_timestamp() WHERE order_id=stored.order_id;
  UPDATE public.automation_portfolio_order_executions_v1 SET state=p_state,order_id=stored.order_id,
    provider_order_ref_hash=stored.provider_order_ref_hash,applied_filled_quantity=p_filled_quantity,
    applied_fill_notional_krw=COALESCE(p_filled_quantity::numeric*p_average_fill_price_krw::numeric,0),
    updated_at=statement_timestamp()
  WHERE run_id=p_run_id AND ordinal=p_ordinal;
  RETURN 'UPDATED';
END $finish$;

-- V165 가 pgcrypto(digest()) 때문에 넣은 search_path 를 다시 발행한다. CREATE OR REPLACE 가
-- 헤더의 `SET search_path=pg_catalog` 로 되돌려 놓기 때문이다.
ALTER FUNCTION public.p1_finish_automation_portfolio_execution_v2(
  text,text,integer,text,text,text,bigint,bigint,bigint,text
) SET search_path TO pg_catalog,public;
