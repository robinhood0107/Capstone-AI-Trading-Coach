-- V161/V162의 source bytes를 보존하고, 실제 소비 경계만 additive v2 함수로 강화한다.

CREATE TABLE public.automation_broker_buyable_receipts_v1 (
  receipt_id text PRIMARY KEY CHECK (receipt_id~'^auto_buyable_[0-9a-f]{32}$'),
  run_id text NOT NULL REFERENCES public.automation_runs(run_id) ON DELETE RESTRICT,
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  account_id text NOT NULL CHECK (account_id~'^acct_[A-Za-z0-9_-]{8,96}$'),
  symbol text NOT NULL CHECK (symbol~'^[0-9]{6}$'),
  estimated_price_krw bigint NOT NULL CHECK (estimated_price_krw>0),
  buyable_quantity bigint NOT NULL CHECK (buyable_quantity>=0),
  buyable_amount_krw bigint NOT NULL CHECK (buyable_amount_krw>=0),
  cash_krw bigint NOT NULL CHECK (cash_krw>=0),
  observed_at timestamptz NOT NULL,
  source_version text NOT NULL CHECK (source_version~'^[A-Za-z0-9._-]{1,96}$'),
  receipt_sha256 text NOT NULL CHECK (receipt_sha256~'^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  UNIQUE(run_id,symbol,estimated_price_krw,receipt_sha256)
);
CREATE INDEX automation_broker_buyable_owner_latest_v1
  ON public.automation_broker_buyable_receipts_v1(user_id,account_id,observed_at DESC);

ALTER TABLE public.automation_portfolio_session_snapshots_v1
  ADD COLUMN balance_observation_id text REFERENCES public.portfolio_balance_observations(observation_id) ON DELETE RESTRICT,
  ADD COLUMN buyable_receipt_id text REFERENCES public.automation_broker_buyable_receipts_v1(receipt_id) ON DELETE RESTRICT;
ALTER TABLE public.automation_portfolio_order_executions_v1
  ADD COLUMN applied_filled_quantity bigint NOT NULL DEFAULT 0 CHECK (applied_filled_quantity>=0 AND applied_filled_quantity<=quantity),
  ADD COLUMN applied_fill_notional_krw numeric(30,0) NOT NULL DEFAULT 0 CHECK (applied_fill_notional_krw>=0);

CREATE TABLE public.automation_position_adoption_receipts_v1 (
  transition_id text PRIMARY KEY CHECK (transition_id~'^auto_adopt_[0-9a-f]{32}$'),
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  account_id text NOT NULL CHECK (account_id~'^acct_[A-Za-z0-9_-]{8,96}$'),
  position_id text NOT NULL UNIQUE REFERENCES public.automation_positions(position_id) ON DELETE RESTRICT,
  previous_policy_id text,
  previous_policy_version integer,
  applied_policy_id text NOT NULL,
  applied_policy_version integer NOT NULL,
  effective_session date NOT NULL,
  capital_baseline_krw bigint NOT NULL CHECK (capital_baseline_krw>=0),
  quantity_at_transition bigint NOT NULL CHECK (quantity_at_transition>0),
  average_price_at_transition_krw bigint NOT NULL CHECK (average_price_at_transition_krw>0),
  realized_pnl_baseline_krw bigint NOT NULL,
  immediate_exit_reason text CHECK (immediate_exit_reason IS NULL OR immediate_exit_reason~'^[A-Z0-9_]{1,96}$'),
  receipt_sha256 text NOT NULL CHECK (receipt_sha256~'^[0-9a-f]{64}$'),
  applied_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  FOREIGN KEY(applied_policy_id,applied_policy_version)
    REFERENCES public.automation_policy_versions(policy_id,version) ON DELETE RESTRICT
);
CREATE INDEX automation_position_adoption_owner_v1
  ON public.automation_position_adoption_receipts_v1(user_id,account_id,applied_at DESC);

ALTER TABLE public.automation_broker_buyable_receipts_v1 OWNER TO flyway;
ALTER TABLE public.automation_position_adoption_receipts_v1 OWNER TO flyway;
REVOKE ALL ON TABLE public.automation_broker_buyable_receipts_v1,
  public.automation_position_adoption_receipts_v1 FROM PUBLIC,decision_app,decision_automation_runtime;

CREATE FUNCTION public.append_world_news_file_batch_v1(p_documents jsonb,p_collection jsonb)
RETURNS TABLE(inserted_count bigint,observed_count bigint,no_op_count bigint,identity_conflict_count bigint)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $batch$
DECLARE item jsonb;
DECLARE disposition text;
DECLARE collection_disposition text;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_market_writer'
     OR jsonb_typeof(p_documents)<>'array' OR jsonb_array_length(p_documents)>100000
     OR jsonb_typeof(p_collection)<>'object' THEN
    RAISE EXCEPTION 'world news file batch input invalid' USING ERRCODE='22023';
  END IF;
  inserted_count:=0;observed_count:=0;no_op_count:=0;identity_conflict_count:=0;
  FOR item IN SELECT value FROM jsonb_array_elements(p_documents) LOOP
    disposition:=public.append_world_news_document_v2(item);
    CASE disposition
      WHEN 'INSERTED' THEN inserted_count:=inserted_count+1;
      WHEN 'OBSERVED' THEN observed_count:=observed_count+1;
      WHEN 'NO_OP' THEN no_op_count:=no_op_count+1;
      WHEN 'IDENTITY_CONFLICT' THEN identity_conflict_count:=identity_conflict_count+1;
      ELSE RAISE EXCEPTION 'world news document disposition invalid' USING ERRCODE='40001';
    END CASE;
  END LOOP;
  collection_disposition:=public.append_world_news_collection_v2(p_collection);
  IF collection_disposition NOT IN ('INSERTED','NO_OP') THEN
    RAISE EXCEPTION 'world news collection disposition invalid' USING ERRCODE='40001';
  END IF;
  RETURN NEXT;
END $batch$;

CREATE FUNCTION public.p1_record_automation_buyable_receipt_v1(
  p_run_id text,p_claim_token_hash text,p_projection jsonb
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $record$
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
END $record$;

CREATE FUNCTION public.p1_read_automation_portfolio_sources_v1(p_run_id text,p_claim_token_hash text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER STABLE SET search_path=pg_catalog AS $sources$
DECLARE claim public.automation_runtime_claim%ROWTYPE;
DECLARE run public.automation_runs%ROWTYPE;
DECLARE control public.automation_control%ROWTYPE;
DECLARE base public.automation_policy_versions%ROWTYPE;
DECLARE capital public.automation_capital_policy_versions_v1%ROWTYPE;
DECLARE balance public.portfolio_balance_observations%ROWTYPE;
DECLARE realized bigint:=0;
DECLARE bot_value bigint:=0;
DECLARE reserved bigint:=0;
DECLARE positions jsonb:='[]'::jsonb;
DECLARE order_count integer:=0;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_automation_runtime' THEN
    RAISE EXCEPTION 'automation portfolio source access denied' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash
    AND (claim_state='ACTIVE' OR (claim_state='RELEASED'
      AND session_date=(statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date
      AND (statement_timestamp() AT TIME ZONE 'Asia/Seoul')::time<=time '15:20'));
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN RAISE EXCEPTION 'automation portfolio claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim.user_id,true);
  SELECT * INTO run FROM public.automation_runs WHERE run_id=p_run_id;
  SELECT * INTO control FROM public.automation_control WHERE user_id=claim.user_id;
  SELECT * INTO base FROM public.automation_policy_versions item
  WHERE item.policy_id=run.policy_id AND item.version=run.policy_version AND item.user_id=claim.user_id;
  SELECT * INTO capital FROM public.automation_capital_policy_versions_v1 item
  WHERE item.user_id=claim.user_id AND item.effective_from_session<=claim.session_date
  ORDER BY item.version DESC LIMIT 1;
  SELECT * INTO balance FROM public.portfolio_balance_observations item
  WHERE item.owner_user_id=claim.user_id AND item.source='KIS_MOCK'
    AND item.context_status='ACTIVE' AND item.completeness='COMPLETE'
    AND item.account_scope_hash LIKE substr(control.account_id,6)||'%'
  ORDER BY item.observed_at DESC,item.received_at DESC,item.observation_id LIMIT 1;
  IF run.run_id IS NULL OR control.account_id IS NULL OR base.policy_id IS NULL
     OR capital.user_id IS NULL OR balance.observation_id IS NULL THEN
    RAISE EXCEPTION 'automation portfolio sources unavailable' USING ERRCODE='40001'; END IF;
  SELECT COALESCE(sum(CASE WHEN adopted.position_id IS NOT NULL
      THEN COALESCE(position.realized_pnl_krw,0)-adopted.realized_pnl_baseline_krw
      WHEN position.created_at>=capital.transition_started_at THEN COALESCE(position.realized_pnl_krw,0)
      ELSE 0 END),0) INTO realized
  FROM public.automation_positions position
  LEFT JOIN public.automation_position_adoption_receipts_v1 adopted ON adopted.position_id=position.position_id
  WHERE position.user_id=claim.user_id AND position.account_id=control.account_id;
  SELECT COALESCE(sum(observation.market_value_krw),0),
    COALESCE(jsonb_agg(jsonb_build_object('positionId',position.position_id,'symbol',position.symbol,
      'quantity',position.quantity,'marketValueKrw',observation.market_value_krw,
      'priceKrw',CASE WHEN position.quantity>0 THEN observation.market_value_krw/position.quantity ELSE NULL END,
      'status',position.status) ORDER BY position.symbol),'[]'::jsonb)
  INTO bot_value,positions
  FROM public.automation_positions position
  JOIN public.portfolio_position_observations observation
    ON observation.balance_observation_id=balance.observation_id AND observation.symbol=position.symbol
  WHERE position.user_id=claim.user_id AND position.account_id=control.account_id
    AND position.status IN ('OPEN','EXIT_PENDING');
  SELECT COALESCE(sum(execution.reserved_buy_cash_krw),0) INTO reserved
  FROM public.automation_portfolio_order_executions_v1 execution
  JOIN public.automation_portfolio_session_snapshots_v1 snapshot USING(run_id)
  WHERE snapshot.user_id=claim.user_id AND execution.side='BUY'
    AND execution.run_id<>p_run_id
    AND execution.state IN ('PLANNED','SUBMITTING','PENDING_RECONCILIATION');
  SELECT count(*) INTO order_count FROM public.orders item
  WHERE item.user_id=claim.user_id AND item.account_id=control.account_id
    AND (item.submitted_at AT TIME ZONE 'Asia/Seoul')::date=claim.session_date;
  RETURN jsonb_build_object('automationPolicyId',base.policy_id,'automationPolicyVersion',base.version,
    'capitalPolicyVersion',capital.version,'reinvestRealizedPnl',capital.reinvest_realized_pnl,
    'cashBufferBps',capital.cash_buffer_bps,'rebalanceDeviationBps',capital.rebalance_deviation_bps,
    'minimumAdjustmentKrw',capital.minimum_adjustment_krw,
    'maxOrdersPerSession',capital.max_orders_per_session,'principleVersionId',run.principle_version_id,
    'principleVersion',run.principle_version,'configuredCapitalKrw',base.capital_limit_krw,
    'realizedPnlSinceTransitionKrw',realized,'balanceObservationId',balance.observation_id,
    'brokerCashKrw',balance.cash_krw,'botPositionMarketValueKrw',bot_value,
    'reservedBuyCashKrw',reserved,'ordersAlreadySubmitted',order_count,'positions',positions)::text;
END $sources$;

CREATE FUNCTION public.p1_stage_automation_portfolio_plan_v2(
  p_run_id text,p_claim_token_hash text,p_snapshot jsonb,p_orders jsonb
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $stage$
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
  expected_target:=expected_investable/5;
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
       OR item->'exactIntent'-ARRAY['symbol','side','orderType','quantity','estimatedPrice','estimatedAmount','timeframe','strategyId']<>'{}'::jsonb
       OR item->'exactIntent'->>'strategyId' IS DISTINCT FROM run.strategy_id
       OR item->'exactIntent'->>'symbol' IS DISTINCT FROM item->>'symbol'
       OR item->'exactIntent'->>'side' IS DISTINCT FROM item->>'side'
       OR item->'exactIntent'->>'orderType'<>'LIMIT' OR item->'exactIntent'->>'timeframe'<>'1d'
       OR (item->'exactIntent'->>'quantity')::bigint<>(item->>'quantity')::bigint
       OR (item->'exactIntent'->>'estimatedPrice')::bigint<>(item->>'limitPriceKrw')::bigint
       OR (item->'exactIntent'->>'estimatedAmount')::numeric<>(item->>'quantity')::numeric*(item->>'limitPriceKrw')::numeric THEN
      RAISE EXCEPTION 'automation portfolio order invalid' USING ERRCODE='22023';
    END IF;
    expected_intent_hash:=encode(digest(convert_to((item->'exactIntent')::text,'UTF8'),'sha256'),'hex');
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
END $stage$;

CREATE FUNCTION public.p1_begin_automation_portfolio_execution_v2(
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
       OR local_now::time>CASE WHEN execution.side='BUY' THEN time '09:40' ELSE time '15:20' END)) THEN
    RAISE EXCEPTION 'automation portfolio execution not currently eligible' USING ERRCODE='40001'; END IF;
  IF execution.idempotency_key_hash<>p_idempotency_key_hash THEN
    RAISE EXCEPTION 'automation execution idempotency drift' USING ERRCODE='23505'; END IF;
  IF execution.state IN ('SUBMITTING','PENDING_RECONCILIATION','FILLED','CANCELLED','REJECTED') THEN
    RETURN 'NO_OP'; END IF;
  UPDATE public.automation_portfolio_order_executions_v1 SET state='SUBMITTING',updated_at=statement_timestamp()
  WHERE run_id=p_run_id AND ordinal=p_ordinal;
  RETURN 'SUBMIT';
END $begin$;

CREATE FUNCTION public.p1_read_automation_portfolio_execution_v1(p_run_id text,p_claim_token_hash text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER STABLE SET search_path=pg_catalog AS $read_execution$
DECLARE claim public.automation_runtime_claim%ROWTYPE;
DECLARE execution public.automation_portfolio_order_executions_v1%ROWTYPE;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_automation_runtime' THEN
    RAISE EXCEPTION 'automation portfolio execution access denied' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash
    AND (claim_state='ACTIVE' OR (claim_state='RELEASED'
      AND session_date=(statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date
      AND (statement_timestamp() AT TIME ZONE 'Asia/Seoul')::time<=time '15:20'));
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN RAISE EXCEPTION 'automation portfolio claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim.user_id,true);
  SELECT * INTO execution FROM public.automation_portfolio_order_executions_v1 item
  WHERE item.run_id=p_run_id AND item.state NOT IN ('FILLED','CANCELLED','REJECTED')
  ORDER BY item.ordinal LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  RETURN jsonb_build_object('ordinal',execution.ordinal,'state',execution.state,
    'executionId',execution.execution_id,'phase',execution.phase,'symbol',execution.symbol,
    'side',execution.side,'quantity',execution.quantity,'limitPriceKrw',execution.limit_price_krw,
    'currentQuantity',execution.current_quantity,'targetQuantity',execution.target_quantity,
    'exactIntent',execution.exact_intent_json,'exactIntentSha256',execution.exact_intent_sha256,
    'idempotencyKeyHash',execution.idempotency_key_hash,'orderId',execution.order_id,
    'providerOrderRefHash',execution.provider_order_ref_hash,
    'appliedFilledQuantity',execution.applied_filled_quantity)::text;
END $read_execution$;

CREATE FUNCTION public.p1_finish_automation_portfolio_execution_v2(
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
  IF p_state='PENDING_RECONCILIATION' THEN
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
  IF p_state='FILLED' AND (p_filled_quantity<>execution.quantity OR p_leaves_quantity<>0) THEN
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
          entry_ordered_quantity=entry_ordered_quantity+delta_quantity,
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
  UPDATE public.orders SET status=CASE WHEN p_state='FILLED' THEN 'FILLED' ELSE p_state END,
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

CREATE FUNCTION public.p1_adopt_automation_position_v1(
  p_transition_id text,p_user_id text,p_account_id text,p_position_id text,p_effective_session date,p_apply boolean
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $adopt$
DECLARE control public.automation_control%ROWTYPE;
DECLARE position public.automation_positions%ROWTYPE;
DECLARE policy public.automation_policy_versions%ROWTYPE;
DECLARE capital public.automation_capital_policy_versions_v1%ROWTYPE;
DECLARE prior public.automation_position_adoption_receipts_v1%ROWTYPE;
DECLARE immediate_reason text;
DECLARE receipt_hash text;
DECLARE projection jsonb;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_automation_runtime'
     OR p_transition_id!~'^auto_adopt_[0-9a-f]{32}$' OR p_user_id!~'^usr_[A-Za-z0-9_-]{8,96}$'
     OR p_account_id!~'^acct_[A-Za-z0-9_-]{8,96}$' OR p_position_id!~'^auto_pos_[A-Za-z0-9_-]{8,96}$'
     OR p_effective_session IS NULL OR p_apply IS NULL THEN
    RAISE EXCEPTION 'automation position adoption input invalid' USING ERRCODE='22023'; END IF;
  SELECT * INTO prior FROM public.automation_position_adoption_receipts_v1 WHERE transition_id=p_transition_id;
  IF FOUND THEN
    IF prior.user_id<>p_user_id OR prior.account_id<>p_account_id OR prior.position_id<>p_position_id
       OR prior.effective_session<>p_effective_session THEN
      RAISE EXCEPTION 'automation position adoption idempotency conflict' USING ERRCODE='23505'; END IF;
    RETURN jsonb_build_object('status','NO_OP','transitionId',prior.transition_id,
      'positionId',prior.position_id,'receiptSha256',prior.receipt_sha256)::text;
  END IF;
  SELECT * INTO control FROM public.automation_control WHERE user_id=p_user_id FOR UPDATE;
  SELECT * INTO position FROM public.automation_positions
  WHERE position_id=p_position_id AND user_id=p_user_id AND account_id=p_account_id
    AND bot_owned AND status IN ('OPEN','EXIT_PENDING') FOR UPDATE;
  SELECT * INTO policy FROM public.automation_policy_versions item
  WHERE item.policy_id=control.policy_id AND item.version=control.policy_version AND item.user_id=p_user_id FOR SHARE;
  SELECT * INTO capital FROM public.automation_capital_policy_versions_v1 item
  WHERE item.user_id=p_user_id AND item.effective_from_session<=p_effective_session
  ORDER BY item.version DESC LIMIT 1 FOR SHARE;
  IF control.account_id IS DISTINCT FROM p_account_id OR position.position_id IS NULL
     OR policy.policy_id IS NULL OR capital.user_id IS NULL OR position.entry_average_fill_price_krw IS NULL
     OR (SELECT count(*) FROM public.automation_positions item WHERE item.user_id=p_user_id
       AND item.account_id=p_account_id AND item.bot_owned AND item.status IN ('OPEN','EXIT_PENDING'))<>1 THEN
    RAISE EXCEPTION 'automation position adoption target drift' USING ERRCODE='40001'; END IF;
  immediate_reason:=CASE
    WHEN position.status='EXIT_PENDING' THEN COALESCE(position.exit_reason,'EXISTING_EXIT_PENDING')
    ELSE NULL END;
  receipt_hash:=encode(digest(convert_to(concat_ws(chr(31),'automation-position-adoption/v1',
    p_transition_id,p_user_id,p_account_id,p_position_id,COALESCE(position.policy_id,'NULL'),
    COALESCE(position.policy_version::text,'NULL'),policy.policy_id,policy.version,p_effective_session,
    policy.capital_limit_krw,position.quantity,position.entry_average_fill_price_krw,
    COALESCE(position.realized_pnl_krw,0),COALESCE(immediate_reason,'NULL')),'UTF8'),'sha256'),'hex');
  projection:=jsonb_build_object('status',CASE WHEN p_apply THEN 'APPLIED' ELSE 'DRY_RUN' END,
    'transitionId',p_transition_id,'positionId',p_position_id,'previousPolicyId',position.policy_id,
    'previousPolicyVersion',position.policy_version,'appliedPolicyId',policy.policy_id,
    'appliedPolicyVersion',policy.version,'effectiveSession',p_effective_session,
    'capitalBaselineKrw',policy.capital_limit_krw,'quantityPreserved',position.quantity,
    'averagePricePreservedKrw',position.entry_average_fill_price_krw,
    'realizedPnlBaselineKrw',COALESCE(position.realized_pnl_krw,0),
    'entrySessionPreserved',position.entry_session,'peakPricePreservedKrw',position.peak_price_krw,
    'immediateExitReason',immediate_reason,'receiptSha256',receipt_hash);
  IF NOT p_apply THEN RETURN projection::text; END IF;
  UPDATE public.automation_positions SET policy_id=policy.policy_id,policy_version=policy.version,
    stop_loss_bps=policy.stop_loss_bps,take_profit_bps=policy.take_profit_bps,
    max_holding_sessions=policy.max_holding_sessions,atr_period=policy.atr_period,
    atr_multiplier_milli=policy.atr_multiplier_milli,model_sell_enabled=policy.model_sell_enabled
  WHERE position_id=p_position_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'automation position adoption rowcount drift' USING ERRCODE='40001'; END IF;
  INSERT INTO public.automation_position_adoption_receipts_v1(
    transition_id,user_id,account_id,position_id,previous_policy_id,previous_policy_version,
    applied_policy_id,applied_policy_version,effective_session,capital_baseline_krw,
    quantity_at_transition,average_price_at_transition_krw,realized_pnl_baseline_krw,
    immediate_exit_reason,receipt_sha256
  ) VALUES (p_transition_id,p_user_id,p_account_id,p_position_id,position.policy_id,position.policy_version,
    policy.policy_id,policy.version,p_effective_session,policy.capital_limit_krw,position.quantity,
    position.entry_average_fill_price_krw,COALESCE(position.realized_pnl_krw,0),immediate_reason,receipt_hash);
  RETURN projection::text;
END $adopt$;

ALTER FUNCTION public.append_world_news_file_batch_v1(jsonb,jsonb) OWNER TO flyway;
ALTER FUNCTION public.p1_record_automation_buyable_receipt_v1(text,text,jsonb) OWNER TO flyway;
ALTER FUNCTION public.p1_read_automation_portfolio_sources_v1(text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_stage_automation_portfolio_plan_v2(text,text,jsonb,jsonb) OWNER TO flyway;
ALTER FUNCTION public.p1_begin_automation_portfolio_execution_v2(text,text,integer,text) OWNER TO flyway;
ALTER FUNCTION public.p1_read_automation_portfolio_execution_v1(text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_finish_automation_portfolio_execution_v2(text,text,integer,text,text,text,bigint,bigint,bigint,text) OWNER TO flyway;
ALTER FUNCTION public.p1_adopt_automation_position_v1(text,text,text,text,date,boolean) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.append_world_news_file_batch_v1(jsonb,jsonb),
  public.p1_record_automation_buyable_receipt_v1(text,text,jsonb),
  public.p1_read_automation_portfolio_sources_v1(text,text),
  public.p1_stage_automation_portfolio_plan_v2(text,text,jsonb,jsonb),
  public.p1_begin_automation_portfolio_execution_v2(text,text,integer,text),
  public.p1_read_automation_portfolio_execution_v1(text,text),
  public.p1_finish_automation_portfolio_execution_v2(text,text,integer,text,text,text,bigint,bigint,bigint,text),
  public.p1_adopt_automation_position_v1(text,text,text,text,date,boolean) FROM PUBLIC,decision_app;
GRANT EXECUTE ON FUNCTION public.append_world_news_file_batch_v1(jsonb,jsonb) TO decision_market_writer;
GRANT EXECUTE ON FUNCTION public.p1_record_automation_buyable_receipt_v1(text,text,jsonb),
  public.p1_read_automation_portfolio_sources_v1(text,text),
  public.p1_stage_automation_portfolio_plan_v2(text,text,jsonb,jsonb),
  public.p1_begin_automation_portfolio_execution_v2(text,text,integer,text),
  public.p1_read_automation_portfolio_execution_v1(text,text),
  public.p1_finish_automation_portfolio_execution_v2(text,text,integer,text,text,text,bigint,bigint,bigint,text),
  public.p1_adopt_automation_position_v1(text,text,text,text,date,boolean) TO decision_automation_runtime;
