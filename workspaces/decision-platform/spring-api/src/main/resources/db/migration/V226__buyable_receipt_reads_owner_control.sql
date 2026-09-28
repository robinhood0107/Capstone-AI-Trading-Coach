-- 포트폴리오 continuation 이 FULL 에서 한 번도 매수하지 못했다(매수가능 영수증·실행 0건).
-- 원인 두 개가 매수 직전에 겹쳐 있었다.
-- 1) 매수가능 영수증 기록은 claim_scan 을 0 으로 되돌린 뒤 소유자 GUC 없이 automation_control 을
--    읽었다. control RLS 어느 정책도 맞지 않아 control 이 비고 계좌 불일치로 'drift'(40001).
-- 2) 계획 저장은 종목당 목표를 investable/5 로 다시 계산했고, 파이썬 계획은 정책의
--    max_open_positions(기본 10)로 나눈다. 값이 달라 'source receipt drift'(40001).
-- 3) 계획 검증의 `item->'exactIntent'-ARRAY[...]` 는 우선순위 때문에 'exactIntent' 를 jsonb 로
--    읽으려다 22P02 로 터졌다(V163 부터). 이 줄에 처음 도달한 것이 2026-09-28 이다.
-- 4) 같은 검증이 없는 컬럼 run.strategy_id 를 읽었다. 전략은 automation_control 에 있다.
-- 5) 주문 의도 해시를 jsonb::text(키 길이순·공백 포함)로 계산해, 파이썬 canonical JSON(키 정렬·
--    압축 구분자·끝 개행)의 해시와 절대 같을 수 없었다. 파이썬과 같은 바이트로 만든다(평평한 ASCII 객체).
-- 6) 포트폴리오 주문의 RiskEngine 재평가는 claim 이 RELEASED 된 뒤에 도는데, 원칙 스냅샷 읽기가
--    ACTIVE claim 만 받아 DecisionNotFound 로 닫혔다. 다른 포트폴리오 함수와 같은 창을 쓴다.
-- 7) 체결 대사는 orders.order_intent_json(수량·금액을 문자열로 canonicalize)과 실행의 exact intent
--    (숫자)를 그대로 비교해 늘 달랐고, 제출 응답에 없던 provider 참조를 필수로 대조했다. 값의 텍스트로
--    비교하고(persist_decision_bundle 과 같은 방식), 참조는 실행 쪽에 있을 때만 대조한다.
-- 소유자를 먼저 세우고, 목표 계산은 정책의 동시 보유 상한을 쓰고, 연산 순서를 괄호로 고정한다.

CREATE OR REPLACE FUNCTION public.p1_record_automation_buyable_receipt_v1(p_run_id text, p_claim_token_hash text, p_projection jsonb)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public'
AS $function$
DECLARE claim public.automation_runtime_claim%ROWTYPE;
DECLARE control public.automation_control%ROWTYPE;
DECLARE preimage text;
DECLARE receipt_hash text;
DECLARE generated_id text;
DECLARE existing_id text;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_automation_runtime'
     OR p_run_id!~'^auto_run_[0-9a-f]{32}$' OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$'
     OR jsonb_typeof(p_projection)<>'object'
     OR p_projection-ARRAY['accountId','brokerageMode','symbol','estimatedPrice','buyableQuantity',
       'buyableAmountKrw','cashKrw','observedAt','sourceVersion']<>'{}'::jsonb
     OR NOT p_projection ?& ARRAY['accountId','brokerageMode','symbol','estimatedPrice','buyableQuantity',
       'buyableAmountKrw','cashKrw','observedAt','sourceVersion']
     OR p_projection->>'brokerageMode'<>'KIS_MOCK' OR p_projection->>'symbol'!~'^[0-9]{6}$'
     OR p_projection->>'sourceVersion'!~'^[A-Za-z0-9._-]{1,96}$' THEN
    RAISE EXCEPTION 'automation buyable receipt input invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash
    AND (claim_state='ACTIVE' OR (claim_state='RELEASED'
      AND session_date=(statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date
      AND (statement_timestamp() AT TIME ZONE 'Asia/Seoul')::time<=time '15:20')) FOR UPDATE;
  PERFORM set_config('app.automation_claim_scan','0',true);
  PERFORM set_config('app.automation_owner_user_id',COALESCE(claim.user_id,''),true);
  SELECT * INTO control FROM public.automation_control WHERE user_id=claim.user_id FOR SHARE;
  IF claim.run_id IS NULL OR control.account_id IS DISTINCT FROM p_projection->>'accountId'
     OR (p_projection->>'estimatedPrice')::bigint<=0 OR (p_projection->>'buyableQuantity')::bigint<0
     OR (p_projection->>'buyableAmountKrw')::bigint<0 OR (p_projection->>'cashKrw')::bigint<0
     OR (p_projection->>'observedAt')::timestamptz>statement_timestamp()+interval '1 minute'
     OR (p_projection->>'observedAt')::timestamptz<statement_timestamp()-interval '5 minutes' THEN
    RAISE EXCEPTION 'automation buyable receipt drift' USING ERRCODE='40001';
  END IF;
  preimage:=concat_ws(chr(31),'automation-buyable-receipt/v1',p_run_id,claim.user_id,
    p_projection->>'accountId',p_projection->>'symbol',p_projection->>'estimatedPrice',
    p_projection->>'buyableQuantity',p_projection->>'buyableAmountKrw',p_projection->>'cashKrw',
    to_char((p_projection->>'observedAt')::timestamptz AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US'),
    p_projection->>'sourceVersion');
  receipt_hash:=encode(digest(convert_to(preimage,'UTF8'),'sha256'),'hex');
  generated_id:='auto_buyable_'||substr(receipt_hash,1,32);
  SELECT receipt_id INTO existing_id FROM public.automation_broker_buyable_receipts_v1
  WHERE run_id=p_run_id AND symbol=p_projection->>'symbol'
    AND estimated_price_krw=(p_projection->>'estimatedPrice')::bigint AND receipt_sha256=receipt_hash;
  IF FOUND THEN RETURN existing_id; END IF;
  INSERT INTO public.automation_broker_buyable_receipts_v1(
    receipt_id,run_id,user_id,account_id,symbol,estimated_price_krw,buyable_quantity,
    buyable_amount_krw,cash_krw,observed_at,source_version,receipt_sha256
  ) VALUES (generated_id,p_run_id,claim.user_id,p_projection->>'accountId',p_projection->>'symbol',
    (p_projection->>'estimatedPrice')::bigint,(p_projection->>'buyableQuantity')::bigint,
    (p_projection->>'buyableAmountKrw')::bigint,(p_projection->>'cashKrw')::bigint,
    (p_projection->>'observedAt')::timestamptz,p_projection->>'sourceVersion',receipt_hash);
  RETURN generated_id;
END $function$;

CREATE OR REPLACE FUNCTION public.p1_stage_automation_portfolio_plan_v2(p_run_id text, p_claim_token_hash text, p_snapshot jsonb, p_orders jsonb)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public'
AS $function$
DECLARE claim public.automation_runtime_claim%ROWTYPE;
DECLARE run public.automation_runs%ROWTYPE;
DECLARE control public.automation_control%ROWTYPE;
DECLARE base_policy public.automation_policy_versions%ROWTYPE;
DECLARE capital_policy public.automation_capital_policy_versions_v1%ROWTYPE;
DECLARE balance public.portfolio_balance_observations%ROWTYPE;
DECLARE buyable public.automation_broker_buyable_receipts_v1%ROWTYPE;
DECLARE item jsonb;
DECLARE expected_allocation bigint;
DECLARE expected_investable bigint;
DECLARE expected_target bigint;
DECLARE expected_realized bigint:=0;
DECLARE expected_bot_value bigint:=0;
DECLARE expected_reserved bigint:=0;
DECLARE expected_ordinal integer:=1;
DECLARE expected_snapshot_hash text;
DECLARE expected_intent_hash text;
DECLARE existing_snapshot jsonb;
DECLARE supplied_snapshot jsonb;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_automation_runtime'
     OR p_run_id!~'^auto_run_[0-9a-f]{32}$' OR p_claim_token_hash!~'^sha256:[0-9a-f]{64}$'
     OR jsonb_typeof(p_snapshot)<>'object' OR jsonb_typeof(p_orders)<>'array'
     OR jsonb_array_length(p_orders) NOT BETWEEN 1 AND 3 THEN
    RAISE EXCEPTION 'automation portfolio plan input invalid' USING ERRCODE='22023';
  END IF;
  supplied_snapshot:=p_snapshot-'snapshotSha256';
  IF supplied_snapshot-ARRAY['allocationCapKrw','automationPolicyId','automationPolicyVersion',
       'balanceObservationId','botPositionMarketValueKrw','brokerBuyableCashKrw','buyableReceiptId',
       'capitalPolicyVersion','configuredCapitalKrw','investableCapKrw','principleVersion',
       'principleVersionId','realizedPnlSinceTransitionKrw','reservedBuyCashKrw',
       'targetPerPositionKrw','unusedCashReason']<>'{}'::jsonb
     OR NOT supplied_snapshot ?& ARRAY['allocationCapKrw','automationPolicyId','automationPolicyVersion',
       'balanceObservationId','botPositionMarketValueKrw','brokerBuyableCashKrw','buyableReceiptId',
       'capitalPolicyVersion','configuredCapitalKrw','investableCapKrw','principleVersion',
       'principleVersionId','realizedPnlSinceTransitionKrw','reservedBuyCashKrw',
       'targetPerPositionKrw','unusedCashReason']
     OR p_snapshot->>'snapshotSha256'!~'^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'automation portfolio snapshot shape invalid' USING ERRCODE='22023';
  END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash
    AND (claim_state='ACTIVE' OR (claim_state='RELEASED'
      AND session_date=(statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date
      AND (statement_timestamp() AT TIME ZONE 'Asia/Seoul')::time<=time '15:20')) FOR UPDATE;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN RAISE EXCEPTION 'automation portfolio claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim.user_id,true);
  SELECT * INTO run FROM public.automation_runs WHERE run_id=p_run_id FOR SHARE;
  SELECT * INTO control FROM public.automation_control WHERE user_id=claim.user_id FOR SHARE;
  SELECT * INTO base_policy FROM public.automation_policy_versions policy
  WHERE policy.user_id=claim.user_id AND policy.policy_id=p_snapshot->>'automationPolicyId'
    AND policy.version=(p_snapshot->>'automationPolicyVersion')::integer;
  SELECT * INTO capital_policy FROM public.automation_capital_policy_versions_v1 policy
  WHERE policy.user_id=claim.user_id AND policy.version=(p_snapshot->>'capitalPolicyVersion')::integer
    AND policy.effective_from_session<=claim.session_date;
  SELECT * INTO balance FROM public.portfolio_balance_observations observation
  WHERE observation.observation_id=p_snapshot->>'balanceObservationId'
    AND observation.owner_user_id=claim.user_id AND observation.source='KIS_MOCK'
    AND observation.context_status='ACTIVE' AND observation.completeness='COMPLETE';
  SELECT * INTO buyable FROM public.automation_broker_buyable_receipts_v1 receipt
  WHERE receipt.receipt_id=p_snapshot->>'buyableReceiptId' AND receipt.run_id=p_run_id
    AND receipt.user_id=claim.user_id AND receipt.account_id=control.account_id;
  IF run.run_id IS NULL OR control.control_state<>'ARMED' OR base_policy.policy_id IS NULL
     OR capital_policy.user_id IS NULL OR balance.observation_id IS NULL OR buyable.receipt_id IS NULL
     OR run.principle_version_id IS DISTINCT FROM p_snapshot->>'principleVersionId'
     OR run.principle_version IS DISTINCT FROM (p_snapshot->>'principleVersion')::integer
     OR public.owner_stop_active(claim.user_id)
     OR COALESCE((SELECT active FROM public.risk_kill_switch WHERE kill_switch_id='GLOBAL'),true) THEN
    RAISE EXCEPTION 'automation portfolio snapshot drift' USING ERRCODE='40001';
  END IF;
  SELECT COALESCE(sum(CASE WHEN adopted.position_id IS NOT NULL
      THEN COALESCE(position.realized_pnl_krw,0)-adopted.realized_pnl_baseline_krw
      WHEN position.created_at>=capital_policy.transition_started_at THEN COALESCE(position.realized_pnl_krw,0)
      ELSE 0 END),0) INTO expected_realized
  FROM public.automation_positions position
  LEFT JOIN public.automation_position_adoption_receipts_v1 adopted ON adopted.position_id=position.position_id
  WHERE position.user_id=claim.user_id AND position.account_id=control.account_id;
  SELECT COALESCE(sum(observation.market_value_krw),0) INTO expected_bot_value
  FROM public.automation_positions position
  JOIN public.portfolio_position_observations observation
    ON observation.balance_observation_id=balance.observation_id AND observation.symbol=position.symbol
  WHERE position.user_id=claim.user_id AND position.account_id=control.account_id
    AND position.status IN ('OPEN','EXIT_PENDING');
  SELECT COALESCE(sum(execution.reserved_buy_cash_krw),0) INTO expected_reserved
  FROM public.automation_portfolio_order_executions_v1 execution
  JOIN public.automation_portfolio_session_snapshots_v1 snapshot USING(run_id)
  WHERE snapshot.user_id=claim.user_id AND execution.side='BUY'
    AND execution.run_id<>p_run_id
    AND execution.state IN ('PLANNED','SUBMITTING','PENDING_RECONCILIATION');
  expected_allocation:=LEAST(GREATEST(0,base_policy.capital_limit_krw+
    CASE WHEN capital_policy.reinvest_realized_pnl THEN expected_realized ELSE LEAST(expected_realized,0) END),
    buyable.buyable_amount_krw+expected_bot_value);
  expected_investable:=expected_allocation*(10000-capital_policy.cash_buffer_bps)/10000;
  expected_target:=expected_investable/GREATEST(1,COALESCE(base_policy.max_open_positions,10));
  IF (p_snapshot->>'configuredCapitalKrw')::bigint<>base_policy.capital_limit_krw
     OR (p_snapshot->>'realizedPnlSinceTransitionKrw')::bigint<>expected_realized
     OR (p_snapshot->>'brokerBuyableCashKrw')::bigint<>buyable.buyable_amount_krw
     OR (p_snapshot->>'botPositionMarketValueKrw')::bigint<>expected_bot_value
     OR (p_snapshot->>'reservedBuyCashKrw')::bigint<>expected_reserved
     OR (p_snapshot->>'allocationCapKrw')::bigint<>expected_allocation
     OR (p_snapshot->>'investableCapKrw')::bigint<>expected_investable
     OR (p_snapshot->>'targetPerPositionKrw')::bigint<>expected_target THEN
    RAISE EXCEPTION 'automation portfolio source receipt drift' USING ERRCODE='40001';
  END IF;
  expected_snapshot_hash:=encode(digest(convert_to(supplied_snapshot::text,'UTF8'),'sha256'),'hex');
  IF expected_snapshot_hash<>p_snapshot->>'snapshotSha256' THEN
    RAISE EXCEPTION 'automation portfolio snapshot hash invalid' USING ERRCODE='22023';
  END IF;
  SELECT to_jsonb(snapshot)-ARRAY['created_at'] INTO existing_snapshot
  FROM public.automation_portfolio_session_snapshots_v1 snapshot WHERE snapshot.run_id=p_run_id;
  IF FOUND THEN
    IF (SELECT snapshot_sha256 FROM public.automation_portfolio_session_snapshots_v1 WHERE run_id=p_run_id)
       =p_snapshot->>'snapshotSha256'
       AND (SELECT jsonb_agg(to_jsonb(execution)-ARRAY['state','order_id','provider_order_ref_hash','created_at','updated_at','applied_filled_quantity','applied_fill_notional_krw'] ORDER BY ordinal)
            FROM public.automation_portfolio_order_executions_v1 execution WHERE execution.run_id=p_run_id)
         =(SELECT jsonb_agg(jsonb_build_object('run_id',p_run_id,'ordinal',(value->>'ordinal')::integer,
            'execution_id',value->>'executionId','phase',value->>'phase','symbol',value->>'symbol',
            'side',value->>'side','quantity',(value->>'quantity')::bigint,
            'limit_price_krw',(value->>'limitPriceKrw')::bigint,'current_quantity',(value->>'currentQuantity')::bigint,
            'target_quantity',(value->>'targetQuantity')::bigint,'exact_intent_json',value->'exactIntent',
            'exact_intent_sha256',value->>'exactIntentSha256','idempotency_key_hash',value->>'idempotencyKeyHash',
            'reserved_buy_cash_krw',CASE WHEN value->>'side'='BUY' THEN (value->>'quantity')::bigint*(value->>'limitPriceKrw')::bigint ELSE 0 END)
            ORDER BY (value->>'ordinal')::integer) FROM jsonb_array_elements(p_orders)) THEN RETURN 'NO_OP'; END IF;
    RAISE EXCEPTION 'automation portfolio plan identity conflict' USING ERRCODE='23505';
  END IF;
  INSERT INTO public.automation_portfolio_session_snapshots_v1(
    run_id,user_id,session_date,capital_policy_version,automation_policy_id,automation_policy_version,
    principle_id,principle_version_id,principle_version,configured_capital_krw,
    realized_pnl_since_transition_krw,broker_buyable_cash_krw,bot_position_market_value_krw,
    reserved_buy_cash_krw,allocation_cap_krw,investable_cap_krw,target_per_position_krw,
    unused_cash_reason,snapshot_sha256,balance_observation_id,buyable_receipt_id
  ) VALUES (p_run_id,claim.user_id,claim.session_date,capital_policy.version,base_policy.policy_id,
    base_policy.version,run.principle_id,run.principle_version_id,run.principle_version,
    base_policy.capital_limit_krw,expected_realized,buyable.buyable_amount_krw,expected_bot_value,
    expected_reserved,expected_allocation,expected_investable,expected_target,
    NULLIF(p_snapshot->>'unusedCashReason',''),expected_snapshot_hash,balance.observation_id,buyable.receipt_id);
  FOR item IN SELECT value FROM jsonb_array_elements(p_orders) ORDER BY (value->>'ordinal')::integer LOOP
    IF jsonb_typeof(item)<>'object' OR item-ARRAY['ordinal','executionId','phase','symbol','side','quantity',
         'limitPriceKrw','currentQuantity','targetQuantity','exactIntent','exactIntentSha256','idempotencyKeyHash']<>'{}'::jsonb
       OR NOT item ?& ARRAY['ordinal','executionId','phase','symbol','side','quantity','limitPriceKrw',
         'currentQuantity','targetQuantity','exactIntent','exactIntentSha256','idempotencyKeyHash']
       OR (item->>'ordinal')::integer<>expected_ordinal OR item->>'phase' NOT IN ('EXIT','REDUCE','INCREASE','ENTRY')
       OR item->>'side' NOT IN ('BUY','SELL') OR item->>'symbol'!~'^[0-9]{6}$'
       OR (item->>'quantity')::bigint<=0 OR (item->>'limitPriceKrw')::bigint<=0
       OR item->>'idempotencyKeyHash'!~'^sha256:[0-9a-f]{64}$'
       OR jsonb_typeof(item->'exactIntent')<>'object'
       OR (item->'exactIntent')-ARRAY['symbol','side','orderType','quantity','estimatedPrice','estimatedAmount','timeframe','strategyId']<>'{}'::jsonb
       OR item->'exactIntent'->>'strategyId' IS DISTINCT FROM control.strategy_id
       OR item->'exactIntent'->>'symbol' IS DISTINCT FROM item->>'symbol'
       OR item->'exactIntent'->>'side' IS DISTINCT FROM item->>'side'
       OR item->'exactIntent'->>'orderType'<>'LIMIT' OR item->'exactIntent'->>'timeframe'<>'1d'
       OR (item->'exactIntent'->>'quantity')::bigint<>(item->>'quantity')::bigint
       OR (item->'exactIntent'->>'estimatedPrice')::bigint<>(item->>'limitPriceKrw')::bigint
       OR (item->'exactIntent'->>'estimatedAmount')::numeric<>(item->>'quantity')::numeric*(item->>'limitPriceKrw')::numeric THEN
      RAISE EXCEPTION 'automation portfolio order invalid' USING ERRCODE='22023';
    END IF;
    expected_intent_hash:=encode(digest(convert_to('{'||(SELECT string_agg(to_jsonb(entry.key)::text||':'||entry.value::text,',' ORDER BY entry.key COLLATE "C") FROM jsonb_each(item->'exactIntent') entry)||'}'||chr(10),'UTF8'),'sha256'),'hex');
    IF expected_intent_hash<>item->>'exactIntentSha256' THEN
      RAISE EXCEPTION 'automation portfolio intent hash invalid' USING ERRCODE='22023'; END IF;
    INSERT INTO public.automation_portfolio_order_executions_v1(
      run_id,ordinal,execution_id,phase,symbol,side,quantity,limit_price_krw,current_quantity,
      target_quantity,exact_intent_json,exact_intent_sha256,idempotency_key_hash,state,reserved_buy_cash_krw
    ) VALUES (p_run_id,expected_ordinal,item->>'executionId',item->>'phase',item->>'symbol',item->>'side',
      (item->>'quantity')::bigint,(item->>'limitPriceKrw')::bigint,(item->>'currentQuantity')::bigint,
      (item->>'targetQuantity')::bigint,item->'exactIntent',expected_intent_hash,item->>'idempotencyKeyHash',
      'PLANNED',CASE WHEN item->>'side'='BUY' THEN (item->>'quantity')::bigint*(item->>'limitPriceKrw')::bigint ELSE 0 END);
    expected_ordinal:=expected_ordinal+1;
  END LOOP;
  RETURN 'INSERTED';
END $function$;

CREATE OR REPLACE FUNCTION public.read_automation_principle_snapshot_authorized(p_capability text, p_actor_user_id text, p_principle_id text, p_run_id text, p_claim_hash text)
 RETURNS TABLE(principle_id text, principle_version_id text, version integer, mode text, status text, rules_json text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
BEGIN
 IF session_user<>'decision_app' OR p_run_id IS NULL OR p_claim_hash IS NULL THEN
  RAISE EXCEPTION 'automation snapshot denied' USING ERRCODE='42501'; END IF;
 IF NOT public.consume_current_actor_capability_v2(p_capability,p_actor_user_id,
   'READ_ACTIVE_PRINCIPLE','PRINCIPLE',p_principle_id,
   'sha256:'||encode(public.digest(p_principle_id,'sha256'),'hex')) THEN RETURN; END IF;
 RETURN QUERY SELECT p.principle_id,v.principle_version_id,v.version,v.mode,v.status,v.rules_json::text
 FROM public.automation_runs run
 JOIN public.automation_runtime_claim claim ON claim.run_id=run.run_id AND claim.user_id=run.user_id
 JOIN public.principles p ON p.principle_id=run.principle_id AND p.user_id=run.user_id
 JOIN public.principle_versions v ON v.principle_version_id=run.principle_version_id
 WHERE run.run_id=p_run_id AND run.user_id=p_actor_user_id AND p.principle_id=p_principle_id
   AND p.status='ACTIVE' AND v.status='ACTIVE' AND (claim.claim_state='ACTIVE' OR (claim.claim_state='RELEASED'
     AND claim.session_date=(statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date
     AND (statement_timestamp() AT TIME ZONE 'Asia/Seoul')::time<=time '15:20'))
   AND claim.claim_token_hash=p_claim_hash;
END $function$;

CREATE OR REPLACE FUNCTION public.p1_finish_automation_portfolio_execution_v2(p_run_id text, p_claim_token_hash text, p_ordinal integer, p_state text, p_order_id text, p_provider_order_ref_hash text, p_filled_quantity bigint, p_leaves_quantity bigint, p_average_fill_price_krw bigint, p_provider_exec_ref_hash text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public'
AS $function$
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
  IF stored.order_id IS NULL
     OR (SELECT jsonb_object_agg(key,value) FROM jsonb_each_text(stored.order_intent_json::jsonb))
        IS DISTINCT FROM (SELECT jsonb_object_agg(key,value) FROM jsonb_each_text(execution.exact_intent_json::jsonb))
     OR (COALESCE(execution.provider_order_ref_hash,p_provider_order_ref_hash) IS NOT NULL
         AND stored.provider_order_ref_hash IS DISTINCT FROM COALESCE(execution.provider_order_ref_hash,p_provider_order_ref_hash)) THEN
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
    IF delta_notional<=0 THEN
      RAISE EXCEPTION 'automation portfolio fill delta invalid' USING ERRCODE='40001'; END IF;
    -- KIS reports a rounded cumulative average; successive totals need not divide
    -- evenly by the newly filled quantity. Keep the exact delta for P&L below.
    delta_price:=round(delta_notional/delta_quantity)::bigint;
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
      realized_delta:=delta_notional-position.entry_average_fill_price_krw*delta_quantity
        -floor(((delta_notional+position.entry_average_fill_price_krw*delta_quantity)*35+19999)/20000);
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
END $function$;
