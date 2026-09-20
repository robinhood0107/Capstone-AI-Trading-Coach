-- 세션당 4번째 주문이 22023 으로 죽는다.
--
-- V167:104-114 가 상한을 5 로 올렸다 - `max_orders_per_session` 정책 CHECK 도,
-- `automation_portfolio_order_executions_v1.ordinal` 테이블 CHECK 도 1..5 다. Python 도
-- `_MAX_ORDERS_PER_SESSION = 5`(automation_portfolio.py:27) 이고 계획 개수는
-- `min(정책값, 5)`(automation_portfolio_runtime.py:72-79) 로 정해진다.
--
-- 그런데 SQL 세 함수는 여전히 3 에서 거절한다:
--
--   * p1_stage_automation_portfolio_plan_v1  (V161:331)  jsonb_array_length(p_orders) 1..3
--   * p1_begin_automation_portfolio_execution_v2  (V172:37)   p_ordinal 1..3
--   * p1_finish_automation_portfolio_execution_v2 (V173:65)   p_ordinal 1..3
--
-- 정책을 4 나 5 로 올리는 것은 CHECK 가 **명시적으로 허용**하는데, 올리는 순간 그 세션의
-- 계획 단계가 통째로 22023 으로 죽는다. 오늘 유효 정책이 3 이라 아직 발화하지 않았을 뿐인
-- 지뢰다. V167 이 올리다 만 자국이고, V172 의 09:40 과 같은 종류의 결함이다.
--
-- 고치는 것은 상한 셋뿐이다. **거래 동작은 바뀌지 않는다** - 정책값은 그대로 3 이고 이
-- 마이그레이션은 정책을 읽지도 쓰지도 않는다. 4·5 를 쓰게 되는 날 죽지 않게 할 뿐이다.
--
-- 본문은 각 함수의 **최신 정의에서 프로그램으로 떠서** 상한 한 줄만 바꾼 것이다. 줄을
-- 넣거나 빼지 않았고 나머지 가드는 글자 그대로 보존된다 - stage 의 자본·배분 검증,
-- begin 의 일곱 가드, finish 의 부분체결 적재 전부. `test_automation_sql_alignment.py` 가
-- 이를 고정한다.
--
-- v1 함수(V161:419,450)는 건드리지 않는다. 호출자가 0 이고 v2 로 대체됐다. 상한을 3 으로
-- 남겨 두는 것이 더 안전하다 - 되살아나면 즉시 거절된다.
--
-- CREATE OR REPLACE 를 쓴다. DROP+CREATE 는 OWNER TO flyway 와
-- GRANT EXECUTE ... TO decision_automation_runtime 를 조용히 버려 첫 호출부터 42501 이 난다.
SET LOCAL row_security = on;

CREATE OR REPLACE FUNCTION public.p1_stage_automation_portfolio_plan_v1(
  p_run_id text,p_claim_token_hash text,p_snapshot jsonb,p_orders jsonb
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $stage$
DECLARE claim public.automation_runtime_claim%ROWTYPE;
DECLARE run public.automation_runs%ROWTYPE;
DECLARE base_policy public.automation_policy_versions%ROWTYPE;
DECLARE capital_policy public.automation_capital_policy_versions_v1%ROWTYPE;
DECLARE item jsonb;
DECLARE expected_investable bigint;
DECLARE expected_target bigint;
DECLARE expected_allocation bigint;
DECLARE existing_hash text;
DECLARE expected_ordinal integer:=1;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_automation_runtime'
     OR p_run_id!~'^auto_run_[0-9a-f]{32}$' OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$'
     OR jsonb_typeof(p_snapshot)<>'object' OR jsonb_typeof(p_orders)<>'array'
     OR jsonb_array_length(p_orders) NOT BETWEEN 1 AND 5 THEN
    RAISE EXCEPTION 'automation portfolio plan input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash AND claim_state='ACTIVE' FOR UPDATE;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN RAISE EXCEPTION 'automation portfolio claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim.user_id,true);
  SELECT * INTO run FROM public.automation_runs WHERE run_id=p_run_id;
  SELECT * INTO base_policy FROM public.automation_policy_versions policy
  WHERE policy.user_id=claim.user_id AND policy.policy_id=p_snapshot->>'automationPolicyId'
    AND policy.version=(p_snapshot->>'automationPolicyVersion')::integer;
  SELECT * INTO capital_policy FROM public.automation_capital_policy_versions_v1 policy
  WHERE policy.user_id=claim.user_id AND policy.version=(p_snapshot->>'capitalPolicyVersion')::integer
    AND policy.effective_from_session<=claim.session_date;
  IF run.run_id IS NULL OR base_policy.policy_id IS NULL OR capital_policy.user_id IS NULL
     OR run.principle_version_id IS DISTINCT FROM p_snapshot->>'principleVersionId'
     OR run.principle_version IS DISTINCT FROM (p_snapshot->>'principleVersion')::integer
     OR p_snapshot->>'snapshotSha256'!~'^[0-9a-f]{64}$'
     OR (p_snapshot->>'brokerBuyableCashKrw')::bigint<0
     OR (p_snapshot->>'botPositionMarketValueKrw')::bigint<0
     OR (p_snapshot->>'reservedBuyCashKrw')::bigint<0 THEN
    RAISE EXCEPTION 'automation portfolio snapshot drift' USING ERRCODE='40001';
  END IF;
  expected_allocation:=LEAST(
    GREATEST(0,base_policy.capital_limit_krw+CASE WHEN capital_policy.reinvest_realized_pnl
      THEN (p_snapshot->>'realizedPnlSinceTransitionKrw')::bigint ELSE 0 END),
    (p_snapshot->>'brokerBuyableCashKrw')::bigint+(p_snapshot->>'botPositionMarketValueKrw')::bigint
  );
  expected_investable:=expected_allocation*(10000-capital_policy.cash_buffer_bps)/10000;
  expected_target:=expected_investable/5;
  IF (p_snapshot->>'configuredCapitalKrw')::bigint<>base_policy.capital_limit_krw
     OR (p_snapshot->>'allocationCapKrw')::bigint<>expected_allocation
     OR (p_snapshot->>'investableCapKrw')::bigint<>expected_investable
     OR (p_snapshot->>'targetPerPositionKrw')::bigint<>expected_target THEN
    RAISE EXCEPTION 'automation portfolio derived capital drift' USING ERRCODE='40001';
  END IF;
  SELECT snapshot_sha256 INTO existing_hash FROM public.automation_portfolio_session_snapshots_v1
  WHERE run_id=p_run_id;
  IF FOUND THEN
    IF existing_hash=p_snapshot->>'snapshotSha256' AND (
      SELECT array_agg(exact_intent_sha256 ORDER BY ordinal)
      FROM public.automation_portfolio_order_executions_v1 WHERE run_id=p_run_id
    )=(
      SELECT array_agg(value->>'exactIntentSha256' ORDER BY (value->>'ordinal')::integer)
      FROM jsonb_array_elements(p_orders)
    ) THEN RETURN 'NO_OP'; END IF;
    RAISE EXCEPTION 'automation portfolio plan identity conflict' USING ERRCODE='23505';
  END IF;
  INSERT INTO public.automation_portfolio_session_snapshots_v1(
    run_id,user_id,session_date,capital_policy_version,automation_policy_id,
    automation_policy_version,principle_id,principle_version_id,principle_version,
    configured_capital_krw,realized_pnl_since_transition_krw,broker_buyable_cash_krw,
    bot_position_market_value_krw,reserved_buy_cash_krw,allocation_cap_krw,
    investable_cap_krw,target_per_position_krw,unused_cash_reason,snapshot_sha256
  ) VALUES (
    p_run_id,claim.user_id,claim.session_date,capital_policy.version,base_policy.policy_id,
    base_policy.version,run.principle_id,run.principle_version_id,run.principle_version,
    base_policy.capital_limit_krw,(p_snapshot->>'realizedPnlSinceTransitionKrw')::bigint,
    (p_snapshot->>'brokerBuyableCashKrw')::bigint,(p_snapshot->>'botPositionMarketValueKrw')::bigint,
    (p_snapshot->>'reservedBuyCashKrw')::bigint,expected_allocation,expected_investable,expected_target,
    NULLIF(p_snapshot->>'unusedCashReason',''),p_snapshot->>'snapshotSha256'
  );
  FOR item IN SELECT value FROM jsonb_array_elements(p_orders) ORDER BY (value->>'ordinal')::integer LOOP
    IF (item->>'ordinal')::integer<>expected_ordinal OR item->>'phase' NOT IN ('EXIT','REDUCE','INCREASE','ENTRY')
       OR item->>'side' NOT IN ('BUY','SELL') OR item->>'symbol'!~'^[0-9]{6}$'
       OR (item->>'quantity')::bigint<=0 OR (item->>'limitPriceKrw')::bigint<=0
       OR item->>'exactIntentSha256'!~'^[0-9a-f]{64}$'
       OR item->>'idempotencyKeyHash'!~'^sha256:[0-9a-f]{64}$' THEN
      RAISE EXCEPTION 'automation portfolio order invalid' USING ERRCODE='22023';
    END IF;
    INSERT INTO public.automation_portfolio_order_executions_v1(
      run_id,ordinal,execution_id,phase,symbol,side,quantity,limit_price_krw,current_quantity,
      target_quantity,exact_intent_json,exact_intent_sha256,idempotency_key_hash,state,
      reserved_buy_cash_krw
    ) VALUES (
      p_run_id,expected_ordinal,item->>'executionId',item->>'phase',item->>'symbol',item->>'side',
      (item->>'quantity')::bigint,(item->>'limitPriceKrw')::bigint,
      (item->>'currentQuantity')::bigint,(item->>'targetQuantity')::bigint,item->'exactIntent',
      item->>'exactIntentSha256',item->>'idempotencyKeyHash','PLANNED',
      CASE WHEN item->>'side'='BUY' THEN (item->>'quantity')::bigint*(item->>'limitPriceKrw')::bigint ELSE 0 END
    );
    expected_ordinal:=expected_ordinal+1;
  END LOOP;
  RETURN 'INSERTED';
END $stage$;

CREATE OR REPLACE FUNCTION public.p1_begin_automation_portfolio_execution_v2(
  p_run_id text,p_claim_token_hash text,p_ordinal integer,p_idempotency_key_hash text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $begin$
DECLARE claim public.automation_runtime_claim%ROWTYPE;
DECLARE execution public.automation_portfolio_order_executions_v1%ROWTYPE;
DECLARE control public.automation_control%ROWTYPE;
DECLARE local_now timestamp;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_automation_runtime'
     OR p_ordinal NOT BETWEEN 1 AND 5 OR p_idempotency_key_hash!~'^sha256:[0-9a-f]{64}$' THEN
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
     OR p_ordinal NOT BETWEEN 1 AND 5 OR p_state NOT IN ('PENDING_RECONCILIATION','FILLED','CANCELLED','REJECTED')
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
-- 헤더의 `SET search_path=pg_catalog` 로 되돌려 놓기 때문이다. finish 만 digest() 를 쓴다.
ALTER FUNCTION public.p1_finish_automation_portfolio_execution_v2(
  text,text,integer,text,text,text,bigint,bigint,bigint,text
) SET search_path TO pg_catalog,public;
