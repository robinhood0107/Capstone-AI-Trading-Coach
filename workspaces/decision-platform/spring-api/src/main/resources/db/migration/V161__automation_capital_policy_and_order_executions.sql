-- 세션 원칙 snapshot과 별개로 자본정책을 다음 XKRX 세션에 고정하고 주문별 실행 단위를 보존한다.
CREATE TABLE public.automation_capital_policy_versions_v1 (
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  version integer NOT NULL CHECK (version>=1),
  reinvest_realized_pnl boolean NOT NULL DEFAULT true,
  cash_buffer_bps integer NOT NULL CHECK (cash_buffer_bps=100),
  rebalance_deviation_bps integer NOT NULL CHECK (rebalance_deviation_bps=200),
  minimum_adjustment_krw bigint NOT NULL CHECK (minimum_adjustment_krw=10000),
  max_orders_per_session integer NOT NULL CHECK (max_orders_per_session BETWEEN 1 AND 3),
  effective_from_session date NOT NULL,
  transition_started_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY(user_id,version)
);
CREATE INDEX automation_capital_policy_current_v1
  ON public.automation_capital_policy_versions_v1(user_id,version DESC);

CREATE TABLE public.automation_capital_policy_idempotency_v1 (
  scope_hash text PRIMARY KEY CHECK (scope_hash~'^sha256:[0-9a-f]{64}$'),
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  request_hash text NOT NULL CHECK (request_hash~'^sha256:[0-9a-f]{64}$'),
  policy_version integer NOT NULL,
  result_json jsonb NOT NULL CHECK (jsonb_typeof(result_json)='object'),
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  FOREIGN KEY(user_id,policy_version)
    REFERENCES public.automation_capital_policy_versions_v1(user_id,version) ON DELETE RESTRICT
);

CREATE TABLE public.automation_portfolio_session_snapshots_v1 (
  run_id text PRIMARY KEY REFERENCES public.automation_runs(run_id) ON DELETE RESTRICT,
  user_id text NOT NULL REFERENCES public.users(user_id) ON DELETE RESTRICT,
  session_date date NOT NULL,
  capital_policy_version integer NOT NULL,
  automation_policy_id text NOT NULL,
  automation_policy_version integer NOT NULL,
  principle_id text NOT NULL,
  principle_version_id text NOT NULL,
  principle_version integer NOT NULL,
  configured_capital_krw bigint NOT NULL CHECK (configured_capital_krw>=0),
  realized_pnl_since_transition_krw bigint NOT NULL,
  broker_buyable_cash_krw bigint NOT NULL CHECK (broker_buyable_cash_krw>=0),
  bot_position_market_value_krw bigint NOT NULL CHECK (bot_position_market_value_krw>=0),
  reserved_buy_cash_krw bigint NOT NULL CHECK (reserved_buy_cash_krw>=0),
  allocation_cap_krw bigint NOT NULL CHECK (allocation_cap_krw>=0),
  investable_cap_krw bigint NOT NULL CHECK (investable_cap_krw>=0),
  target_per_position_krw bigint NOT NULL CHECK (target_per_position_krw>=0),
  unused_cash_reason text,
  snapshot_sha256 text NOT NULL CHECK (snapshot_sha256~'^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  UNIQUE(user_id,session_date),
  FOREIGN KEY(user_id,capital_policy_version)
    REFERENCES public.automation_capital_policy_versions_v1(user_id,version) ON DELETE RESTRICT,
  FOREIGN KEY(automation_policy_id,automation_policy_version)
    REFERENCES public.automation_policy_versions(policy_id,version) ON DELETE RESTRICT,
  FOREIGN KEY(principle_version_id,principle_id,principle_version)
    REFERENCES public.principle_versions(principle_version_id,principle_id,version) ON DELETE RESTRICT,
  CHECK (investable_cap_krw<=allocation_cap_krw),
  CHECK (target_per_position_krw<=investable_cap_krw),
  CHECK (unused_cash_reason IS NULL OR unused_cash_reason~'^[A-Z0-9_]{1,96}$')
);

CREATE TABLE public.automation_portfolio_order_executions_v1 (
  run_id text NOT NULL REFERENCES public.automation_portfolio_session_snapshots_v1(run_id) ON DELETE RESTRICT,
  ordinal integer NOT NULL CHECK (ordinal BETWEEN 1 AND 3),
  execution_id text NOT NULL UNIQUE CHECK (execution_id~'^auto_exec_[0-9a-f]{32}$'),
  phase text NOT NULL CHECK (phase IN ('EXIT','REDUCE','INCREASE','ENTRY')),
  symbol text NOT NULL CHECK (symbol~'^[0-9]{6}$'),
  side text NOT NULL CHECK (side IN ('BUY','SELL')),
  quantity bigint NOT NULL CHECK (quantity>0),
  limit_price_krw bigint NOT NULL CHECK (limit_price_krw>0),
  current_quantity bigint NOT NULL CHECK (current_quantity>=0),
  target_quantity bigint NOT NULL CHECK (target_quantity>=0),
  exact_intent_json jsonb NOT NULL CHECK (
    jsonb_typeof(exact_intent_json)='object'
    AND exact_intent_json ?& ARRAY['symbol','side','orderType','quantity','estimatedPrice','estimatedAmount','timeframe','strategyId']
    AND exact_intent_json-ARRAY['symbol','side','orderType','quantity','estimatedPrice','estimatedAmount','timeframe','strategyId']='{}'::jsonb
  ),
  exact_intent_sha256 text NOT NULL CHECK (exact_intent_sha256~'^[0-9a-f]{64}$'),
  idempotency_key_hash text NOT NULL CHECK (idempotency_key_hash~'^sha256:[0-9a-f]{64}$'),
  state text NOT NULL CHECK (state IN ('PLANNED','SUBMITTING','PENDING_RECONCILIATION','FILLED','CANCELLED','REJECTED')),
  order_id text REFERENCES public.orders(order_id) ON DELETE RESTRICT,
  provider_order_ref_hash text CHECK (provider_order_ref_hash IS NULL OR provider_order_ref_hash~'^[0-9a-f]{64}$'),
  reserved_buy_cash_krw bigint NOT NULL CHECK (reserved_buy_cash_krw>=0),
  created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
  PRIMARY KEY(run_id,ordinal),
  CHECK ((side='BUY')=(reserved_buy_cash_krw>0)),
  CHECK ((phase IN ('EXIT','REDUCE'))=(side='SELL')),
  CHECK ((exact_intent_json->>'symbol')=symbol AND (exact_intent_json->>'side')=side),
  CHECK ((exact_intent_json->>'quantity')::bigint=quantity),
  CHECK ((exact_intent_json->>'estimatedPrice')::bigint=limit_price_krw),
  CHECK ((exact_intent_json->>'estimatedAmount')::numeric=quantity::numeric*limit_price_krw::numeric)
);
CREATE UNIQUE INDEX automation_portfolio_one_open_execution_v1
  ON public.automation_portfolio_order_executions_v1(run_id)
  WHERE state IN ('SUBMITTING','PENDING_RECONCILIATION');

ALTER TABLE public.automation_capital_policy_versions_v1 OWNER TO flyway;
ALTER TABLE public.automation_capital_policy_idempotency_v1 OWNER TO flyway;
ALTER TABLE public.automation_portfolio_session_snapshots_v1 OWNER TO flyway;
ALTER TABLE public.automation_portfolio_order_executions_v1 OWNER TO flyway;

ALTER TABLE public.automation_policy_versions NO FORCE ROW LEVEL SECURITY;
WITH next_session AS (
  SELECT min(session_date) session_date FROM public.trading_sessions
  WHERE exchange_mic='XKRX' AND is_open AND session_date>=(statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date
), owners AS (
  SELECT DISTINCT user_id FROM public.automation_policy_versions
)
INSERT INTO public.automation_capital_policy_versions_v1(
  user_id,version,reinvest_realized_pnl,cash_buffer_bps,rebalance_deviation_bps,
  minimum_adjustment_krw,max_orders_per_session,effective_from_session,transition_started_at
)
SELECT owner.user_id,1,true,100,200,10000,3,next_session.session_date,statement_timestamp()
FROM owners owner CROSS JOIN next_session WHERE next_session.session_date IS NOT NULL;
ALTER TABLE public.automation_policy_versions FORCE ROW LEVEL SECURITY;

CREATE FUNCTION public.p1_put_automation_capital_policy_v1(
  p_user_id text,p_reinvest_realized_pnl boolean,p_expected_version integer,
  p_scope_hash text,p_request_hash text
) RETURNS TABLE(result_json text,replayed boolean)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $put$
DECLARE current_version integer;
DECLARE next_version integer;
DECLARE next_session date;
DECLARE prior public.automation_capital_policy_idempotency_v1%ROWTYPE;
DECLARE projection jsonb;
DECLARE transition_time timestamptz;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR nullif(current_setting('app.actor_user_id',true),'') IS DISTINCT FROM p_user_id
     OR p_reinvest_realized_pnl IS NULL OR p_expected_version<0
     OR p_scope_hash!~'^sha256:[0-9a-f]{64}$' OR p_request_hash!~'^sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'automation capital policy input invalid' USING ERRCODE='22023';
  END IF;
  SELECT * INTO prior FROM public.automation_capital_policy_idempotency_v1 WHERE scope_hash=p_scope_hash;
  IF FOUND THEN
    IF prior.user_id<>p_user_id OR prior.request_hash<>p_request_hash THEN
      RAISE EXCEPTION 'automation capital policy idempotency conflict' USING ERRCODE='23505';
    END IF;
    result_json:=prior.result_json::text;replayed:=true;RETURN NEXT;RETURN;
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('automation-capital:'||p_user_id,0));
  SELECT version INTO current_version FROM public.automation_capital_policy_versions_v1
  WHERE user_id=p_user_id ORDER BY version DESC LIMIT 1;
  current_version:=COALESCE(current_version,0);
  IF current_version<>p_expected_version THEN
    RAISE EXCEPTION 'automation capital policy version conflict' USING ERRCODE='40001';
  END IF;
  SELECT session_date INTO next_session FROM public.trading_sessions
  WHERE exchange_mic='XKRX' AND is_open AND (
    session_date>(statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date
    OR (session_date=(statement_timestamp() AT TIME ZONE 'Asia/Seoul')::date
      AND (statement_timestamp() AT TIME ZONE 'Asia/Seoul')::time<time '09:30')
  ) ORDER BY session_date LIMIT 1;
  IF next_session IS NULL THEN
    RAISE EXCEPTION 'automation capital next XKRX session unavailable' USING ERRCODE='40001';
  END IF;
  next_version:=current_version+1;
  transition_time:=CASE WHEN current_version=0 THEN statement_timestamp() ELSE (
    SELECT transition_started_at FROM public.automation_capital_policy_versions_v1
    WHERE user_id=p_user_id AND version=current_version
  ) END;
  INSERT INTO public.automation_capital_policy_versions_v1(
    user_id,version,reinvest_realized_pnl,cash_buffer_bps,rebalance_deviation_bps,
    minimum_adjustment_krw,max_orders_per_session,effective_from_session,transition_started_at
  ) VALUES (p_user_id,next_version,p_reinvest_realized_pnl,100,200,10000,3,next_session,transition_time);
  projection:=jsonb_build_object(
    'contractId','automation-capital-policy.v1','version',next_version,
    'reinvestRealizedPnl',p_reinvest_realized_pnl,'cashBufferBps',100,
    'rebalanceDeviationBps',200,'minimumAdjustmentKrw',10000,
    'maxOrdersPerSession',3,'effectiveFromSession',next_session,
    'transitionStartedAt',transition_time
  );
  INSERT INTO public.automation_capital_policy_idempotency_v1(
    scope_hash,user_id,request_hash,policy_version,result_json
  ) VALUES (p_scope_hash,p_user_id,p_request_hash,next_version,projection);
  result_json:=projection::text;replayed:=false;RETURN NEXT;
END $put$;

CREATE FUNCTION public.p1_read_automation_capital_policy_v1(p_user_id text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER STABLE SET search_path=pg_catalog AS $read$
DECLARE policy public.automation_capital_policy_versions_v1%ROWTYPE;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR nullif(current_setting('app.actor_user_id',true),'') IS DISTINCT FROM p_user_id THEN
    RAISE EXCEPTION 'automation capital policy access denied' USING ERRCODE='42501';
  END IF;
  SELECT * INTO policy FROM public.automation_capital_policy_versions_v1
  WHERE user_id=p_user_id ORDER BY version DESC LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  RETURN jsonb_build_object(
    'contractId','automation-capital-policy.v1','version',policy.version,
    'reinvestRealizedPnl',policy.reinvest_realized_pnl,'cashBufferBps',policy.cash_buffer_bps,
    'rebalanceDeviationBps',policy.rebalance_deviation_bps,
    'minimumAdjustmentKrw',policy.minimum_adjustment_krw,
    'maxOrdersPerSession',policy.max_orders_per_session,
    'effectiveFromSession',policy.effective_from_session,
    'transitionStartedAt',policy.transition_started_at
  )::text;
END $read$;

CREATE FUNCTION public.p1_read_automation_capital_status_v1(p_user_id text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER STABLE SET search_path=pg_catalog AS $status$
DECLARE capital public.automation_capital_policy_versions_v1%ROWTYPE;
DECLARE base public.automation_policy_versions%ROWTYPE;
DECLARE control public.automation_control%ROWTYPE;
DECLARE balance record;
DECLARE realized bigint:=0;
DECLARE bot_value bigint:=0;
DECLARE reserved bigint:=0;
DECLARE allocation bigint:=0;
DECLARE investable bigint:=0;
DECLARE available bigint:=0;
DECLARE target bigint:=0;
DECLARE position_items jsonb:='[]'::jsonb;
DECLARE existing_count integer:=0;
DECLARE valuation_missing integer:=0;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_app'
     OR nullif(current_setting('app.actor_user_id',true),'') IS DISTINCT FROM p_user_id THEN
    RAISE EXCEPTION 'automation capital status access denied' USING ERRCODE='42501'; END IF;
  SELECT * INTO capital FROM public.automation_capital_policy_versions_v1
  WHERE user_id=p_user_id ORDER BY version DESC LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT * INTO control FROM public.automation_control WHERE user_id=p_user_id;
  SELECT * INTO base FROM public.automation_policy_versions policy
  WHERE policy.user_id=p_user_id ORDER BY version DESC LIMIT 1;
  IF control.user_id IS NULL OR base.policy_id IS NULL THEN RETURN NULL; END IF;
  SELECT item.observation_id,item.cash_krw INTO balance
  FROM public.portfolio_balance_observations item
  WHERE item.owner_user_id=p_user_id AND item.source='KIS_MOCK'
    AND item.context_status='ACTIVE' AND item.completeness='COMPLETE'
    AND item.account_scope_hash LIKE substr(control.account_id,6)||'%'
  ORDER BY item.observed_at DESC,item.received_at DESC,item.observation_id LIMIT 1;
  SELECT COALESCE(sum(position.realized_pnl_krw),0) INTO realized
  FROM public.automation_positions position
  WHERE position.user_id=p_user_id AND position.account_id=control.account_id
    AND position.status='CLOSED' AND position.closed_at>=capital.transition_started_at
    AND position.realized_pnl_krw IS NOT NULL;
  IF balance.observation_id IS NOT NULL THEN
    SELECT COALESCE(sum(observation.market_value_krw),0),
      count(*) FILTER(WHERE observation.symbol IS NULL)
    INTO bot_value,valuation_missing
    FROM public.automation_positions position
    LEFT JOIN public.portfolio_position_observations observation
      ON observation.balance_observation_id=balance.observation_id
      AND observation.symbol=position.symbol
    WHERE position.user_id=p_user_id AND position.account_id=control.account_id
      AND position.status IN ('OPEN','EXIT_PENDING');
  END IF;
  SELECT COALESCE(sum(item.reserved_buy_cash_krw),0) INTO reserved
  FROM public.automation_portfolio_order_executions_v1 item
  JOIN public.automation_portfolio_session_snapshots_v1 snapshot USING(run_id)
  WHERE snapshot.user_id=p_user_id AND item.side='BUY'
    AND item.state IN ('PLANNED','SUBMITTING','PENDING_RECONCILIATION');
  allocation:=LEAST(
    GREATEST(0,base.capital_limit_krw+CASE WHEN capital.reinvest_realized_pnl THEN realized ELSE 0 END),
    COALESCE(balance.cash_krw,0)+bot_value
  );
  investable:=allocation*(10000-capital.cash_buffer_bps)/10000;
  target:=investable/5;
  available:=GREATEST(0,LEAST(COALESCE(balance.cash_krw,0),investable-bot_value)-reserved);
  SELECT count(*) INTO existing_count FROM public.automation_positions position
  WHERE position.user_id=p_user_id AND position.account_id=control.account_id
    AND position.status IN ('OPEN','EXIT_PENDING') AND position.created_at<=capital.transition_started_at;
  IF balance.observation_id IS NOT NULL THEN
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
      'symbol',position.symbol,'currentQuantity',position.quantity,
      'targetQuantity',CASE WHEN observation.market_value_krw>0 AND position.quantity>0
        THEN target/(observation.market_value_krw/position.quantity) ELSE NULL END,
      'currentMarketValueKrw',observation.market_value_krw,'targetMarketValueKrw',target,
      'currentWeightBps',CASE WHEN allocation>0 AND observation.market_value_krw IS NOT NULL
        THEN observation.market_value_krw*10000/allocation ELSE NULL END,
      'targetWeightBps',CASE WHEN allocation>0 THEN target*10000/allocation ELSE 0 END,
      'valuationStatus',CASE WHEN observation.symbol IS NULL THEN 'MISSING' ELSE 'COMPLETE' END
    ) ORDER BY position.symbol),'[]'::jsonb) INTO position_items
    FROM public.automation_positions position
    LEFT JOIN public.portfolio_position_observations observation
      ON observation.balance_observation_id=balance.observation_id AND observation.symbol=position.symbol
    WHERE position.user_id=p_user_id AND position.account_id=control.account_id
      AND position.status IN ('OPEN','EXIT_PENDING');
  END IF;
  RETURN jsonb_build_object(
    'contractId','automation-capital-status.v1','policyVersion',capital.version,
    'reinvestRealizedPnl',capital.reinvest_realized_pnl,'configuredCapitalKrw',base.capital_limit_krw,
    'realizedPnlSinceTransitionKrw',realized,'brokerBuyableCashKrw',COALESCE(balance.cash_krw,0),
    'botPositionMarketValueKrw',bot_value,'reservedBuyCashKrw',reserved,
    'allocationCapKrw',allocation,'investableCapKrw',investable,
    'availableBuyCashKrw',available,'targetPerPositionKrw',target,
    'existingBotPositionsAdopted',existing_count,'valuationMissingCount',valuation_missing,
    'unusedCashReason',CASE WHEN valuation_missing>0 THEN 'POSITION_VALUATION_MISSING'
      WHEN available>0 THEN 'BUFFER_OR_NO_MORE_ELIGIBLE_CANDIDATES' ELSE NULL END,
    'positions',position_items,'asOf',statement_timestamp()
  )::text;
END $status$;

ALTER FUNCTION public.p1_put_automation_capital_policy_v1(text,boolean,integer,text,text) OWNER TO flyway;
ALTER FUNCTION public.p1_read_automation_capital_policy_v1(text) OWNER TO flyway;
ALTER FUNCTION public.p1_read_automation_capital_status_v1(text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_put_automation_capital_policy_v1(text,boolean,integer,text,text),
  public.p1_read_automation_capital_policy_v1(text),
  public.p1_read_automation_capital_status_v1(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.p1_put_automation_capital_policy_v1(text,boolean,integer,text,text),
  public.p1_read_automation_capital_policy_v1(text),
  public.p1_read_automation_capital_status_v1(text) TO decision_app;

REVOKE ALL ON TABLE public.automation_capital_policy_versions_v1,
  public.automation_capital_policy_idempotency_v1,
  public.automation_portfolio_session_snapshots_v1,
  public.automation_portfolio_order_executions_v1
FROM PUBLIC,decision_app,decision_automation_runtime;

CREATE FUNCTION public.p1_stage_automation_portfolio_plan_v1(
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
     OR jsonb_array_length(p_orders) NOT BETWEEN 1 AND 3 THEN
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

CREATE FUNCTION public.p1_begin_automation_portfolio_execution_v1(
  p_run_id text,p_claim_token_hash text,p_ordinal integer,p_idempotency_key_hash text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $begin$
DECLARE claim public.automation_runtime_claim%ROWTYPE;
DECLARE execution public.automation_portfolio_order_executions_v1%ROWTYPE;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_automation_runtime'
     OR p_ordinal NOT BETWEEN 1 AND 3 OR p_idempotency_key_hash!~'^sha256:[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'automation portfolio execution input invalid' USING ERRCODE='22023'; END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash AND claim_state='ACTIVE' FOR UPDATE;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN RAISE EXCEPTION 'automation portfolio claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim.user_id,true);
  SELECT * INTO execution FROM public.automation_portfolio_order_executions_v1
  WHERE run_id=p_run_id AND ordinal=p_ordinal FOR UPDATE;
  IF NOT FOUND OR EXISTS(
    SELECT 1 FROM public.automation_portfolio_order_executions_v1 prior
    WHERE prior.run_id=p_run_id AND prior.ordinal<p_ordinal
      AND prior.state NOT IN ('FILLED','CANCELLED','REJECTED')
  ) THEN RAISE EXCEPTION 'automation prior execution unresolved' USING ERRCODE='40001'; END IF;
  IF execution.idempotency_key_hash<>p_idempotency_key_hash THEN
    RAISE EXCEPTION 'automation execution idempotency drift' USING ERRCODE='23505'; END IF;
  IF execution.state IN ('SUBMITTING','PENDING_RECONCILIATION','FILLED','CANCELLED','REJECTED') THEN
    RETURN 'NO_OP'; END IF;
  UPDATE public.automation_portfolio_order_executions_v1 SET state='SUBMITTING',updated_at=statement_timestamp()
  WHERE run_id=p_run_id AND ordinal=p_ordinal;
  RETURN 'SUBMIT';
END $begin$;

CREATE FUNCTION public.p1_finish_automation_portfolio_execution_v1(
  p_run_id text,p_claim_token_hash text,p_ordinal integer,p_state text,
  p_order_id text,p_provider_order_ref_hash text
) RETURNS text LANGUAGE plpgsql SECURITY DEFINER VOLATILE SET search_path=pg_catalog AS $finish$
DECLARE claim public.automation_runtime_claim%ROWTYPE;
DECLARE current_state text;
BEGIN
  IF current_user<>'flyway' OR session_user<>'decision_automation_runtime'
     OR p_ordinal NOT BETWEEN 1 AND 3
     OR p_state NOT IN ('PENDING_RECONCILIATION','FILLED','CANCELLED','REJECTED')
     OR (p_provider_order_ref_hash IS NOT NULL AND p_provider_order_ref_hash!~'^[0-9a-f]{64}$') THEN
    RAISE EXCEPTION 'automation portfolio outcome invalid' USING ERRCODE='22023'; END IF;
  PERFORM set_config('app.automation_claim_scan','1',true);
  SELECT * INTO claim FROM public.automation_runtime_claim
  WHERE run_id=p_run_id AND claim_token_hash=p_claim_token_hash AND claim_state='ACTIVE' FOR UPDATE;
  PERFORM set_config('app.automation_claim_scan','0',true);
  IF NOT FOUND THEN RAISE EXCEPTION 'automation portfolio claim unavailable' USING ERRCODE='42501'; END IF;
  PERFORM set_config('app.automation_owner_user_id',claim.user_id,true);
  SELECT state INTO current_state FROM public.automation_portfolio_order_executions_v1
  WHERE run_id=p_run_id AND ordinal=p_ordinal FOR UPDATE;
  IF current_state=p_state THEN RETURN 'NO_OP'; END IF;
  IF current_state NOT IN ('SUBMITTING','PENDING_RECONCILIATION')
     OR (current_state='PENDING_RECONCILIATION' AND p_state='PENDING_RECONCILIATION') THEN
    RAISE EXCEPTION 'automation portfolio outcome transition invalid' USING ERRCODE='40001'; END IF;
  UPDATE public.automation_portfolio_order_executions_v1 SET state=p_state,
    order_id=COALESCE(automation_portfolio_order_executions_v1.order_id,p_order_id),
    provider_order_ref_hash=COALESCE(
      automation_portfolio_order_executions_v1.provider_order_ref_hash,p_provider_order_ref_hash
    ),updated_at=statement_timestamp()
  WHERE run_id=p_run_id AND ordinal=p_ordinal;
  RETURN 'UPDATED';
END $finish$;

ALTER FUNCTION public.p1_stage_automation_portfolio_plan_v1(text,text,jsonb,jsonb) OWNER TO flyway;
ALTER FUNCTION public.p1_begin_automation_portfolio_execution_v1(text,text,integer,text) OWNER TO flyway;
ALTER FUNCTION public.p1_finish_automation_portfolio_execution_v1(text,text,integer,text,text,text) OWNER TO flyway;
REVOKE ALL ON FUNCTION public.p1_stage_automation_portfolio_plan_v1(text,text,jsonb,jsonb),
  public.p1_begin_automation_portfolio_execution_v1(text,text,integer,text),
  public.p1_finish_automation_portfolio_execution_v1(text,text,integer,text,text,text)
FROM PUBLIC,decision_app;
GRANT EXECUTE ON FUNCTION public.p1_stage_automation_portfolio_plan_v1(text,text,jsonb,jsonb),
  public.p1_begin_automation_portfolio_execution_v1(text,text,integer,text),
  public.p1_finish_automation_portfolio_execution_v1(text,text,integer,text,text,text)
TO decision_automation_runtime;
